"""
Ask-the-city Q&A — grounded retrieval-augmented generation over the graph.

Pipeline:
  1. intent pre-pass (cheap/fast model): turn the NL question into a focused
     search phrase + structured filters (node types, date range) — the things
     vector similarity is blind to.
  2. hybrid retrieval: embed the search phrase, pgvector KNN, AND apply the
     filters as SQL WHERE. Falls back to unfiltered KNN if the filters yield
     nothing.
  3. grounded answer: hand the retrieved subgraph to the main model, which
     answers in German using only those facts and names its sources.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from db.session import get_conn
from embeddings.embedder import embed_texts, to_pgvector
from reasoning.llm import complete, fast_model
from reasoning.prompts import (
    QA_PROMPT,
    QA_SYSTEM_PROMPT,
    QUERY_INTENT_PROMPT,
    QUERY_INTENT_SYSTEM,
    format_subgraph,
)

logger = logging.getLogger(__name__)

_VALID_NODE_TYPES = {
    "AgendaItem", "Resolution", "Meeting", "Event", "Tender",
    "POI", "Organization", "Road", "GeoArea",
}


def _loads(raw: str) -> dict[str, Any]:
    """Tolerant JSON parse (strip ```json fences)."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return {}
    return {}


def _valid_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


async def extract_intent(question: str) -> dict[str, Any]:
    """Cheap pre-pass: focused search phrase + structured filters."""
    fallback = {"search_text": question, "node_types": [], "date_from": None, "date_to": None}
    try:
        raw = await complete(
            QUERY_INTENT_SYSTEM,
            QUERY_INTENT_PROMPT.format(question=question, today=date.today().isoformat()),
            max_tokens=400,
            model=fast_model(),
        )
    except Exception as exc:  # intent is best-effort — never block the answer
        logger.warning("intent extraction failed: %s", exc)
        return fallback
    data = _loads(raw)
    search_text = str(data.get("search_text") or "").strip() or question
    node_types = [t for t in (data.get("node_types") or []) if t in _VALID_NODE_TYPES]
    return {
        "search_text": search_text,
        "node_types": node_types,
        "date_from": _valid_date(data.get("date_from")),
        "date_to": _valid_date(data.get("date_to")),
    }


async def _knn(conn: Any, lit: str, intent: dict[str, Any], k: int, use_filters: bool) -> list:
    filters = ["n.valid_to IS NULL"]
    params: list[Any] = [lit]
    if use_filters and intent["node_types"]:
        params.append(intent["node_types"])
        filters.append(f"n.node_type = ANY(${len(params)}::text[])")
    if use_filters and intent["date_from"]:
        params.append(intent["date_from"])
        filters.append(f"n.valid_from >= ${len(params)}")
    if use_filters and intent["date_to"]:
        params.append(intent["date_to"])
        filters.append(f"n.valid_from <= ${len(params)}")
    params.append(k)
    return await conn.fetch(
        f"""
        SELECT n.id, n.node_type, n.label, n.properties, n.source, n.source_url, n.valid_from
        FROM node_embeddings e
        JOIN nodes n ON n.id = e.node_id
        WHERE {' AND '.join(filters)}
        ORDER BY e.embedding <=> $1::vector
        LIMIT ${len(params)}
        """,
        *params,
    )


async def answer_question(question: str, k: int = 24) -> dict[str, Any]:
    """Answer a natural-language question from the k most relevant graph facts."""
    question = (question or "").strip()
    if not question:
        return {"answer": "Bitte eine Frage eingeben.", "citations": [], "intent": {}}

    intent = await extract_intent(question)
    qvec = (await embed_texts([intent["search_text"]]))[0]
    lit = to_pgvector(qvec)

    has_filters = bool(intent["node_types"] or intent["date_from"] or intent["date_to"])
    async with get_conn() as conn:
        nodes = await _knn(conn, lit, intent, k, use_filters=has_filters)
        # If filters were too narrow and found nothing, retry unfiltered.
        if not nodes and has_filters:
            nodes = await _knn(conn, lit, intent, k, use_filters=False)
        if not nodes:
            return {
                "answer": "Dazu liegen im Wissensgraphen keine passenden Fakten vor.",
                "citations": [],
                "intent": intent,
            }
        ids = [str(n["id"]) for n in nodes]
        edges = await conn.fetch(
            """
            SELECT id, edge_type, from_node_id, to_node_id, properties, source
            FROM edges
            WHERE from_node_id = ANY($1::uuid[]) AND to_node_id = ANY($1::uuid[])
              AND valid_to IS NULL
            """,
            ids,
        )

    subgraph = format_subgraph([dict(n) for n in nodes], [dict(e) for e in edges])
    prompt = QA_PROMPT.format(
        question=question, subgraph_json=subgraph, today=date.today().isoformat()
    )
    # Reasoning models (e.g. deepseek-v4-pro) spend the budget on hidden
    # reasoning before the answer, so give ample headroom or `content` truncates.
    answer = await complete(QA_SYSTEM_PROMPT, prompt, max_tokens=4000)

    citations = [
        {
            "id": str(n["id"]),
            "label": n["label"],
            "node_type": n["node_type"],
            "source": n["source"],
            "source_url": n["source_url"],
        }
        for n in nodes
    ]
    return {"answer": answer, "citations": citations, "intent": {
        "search_text": intent["search_text"],
        "node_types": intent["node_types"],
        "date_from": intent["date_from"].isoformat() if intent["date_from"] else None,
        "date_to": intent["date_to"].isoformat() if intent["date_to"] else None,
    }}
