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
from reasoning.llm import complete
from reasoning.prompts import (
    ANALYSIS_PROMPT,
    ANALYSIS_SYSTEM_PROMPTS,
    QA_PROMPT,
    QA_SYSTEM_PROMPT,
    QUERY_INTENT_PROMPT,
    QUERY_INTENT_SYSTEM,
    format_subgraph,
)

_LENSES = {"factual", "synergy", "inefficiency", "scandal"}

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
    fallback = {
        "lens": "factual", "search_text": question, "node_types": [],
        "category": None, "list": False, "date_from": None, "date_to": None,
    }
    try:
        # Use the main model (not the fast one): reliable lens classification +
        # node-type extraction matters more here than the tiny cost of one short
        # call. Reasoning models need headroom (hidden reasoning_content).
        raw = await complete(
            QUERY_INTENT_SYSTEM,
            QUERY_INTENT_PROMPT.format(question=question, today=date.today().isoformat()),
            max_tokens=1500,
        )
    except Exception as exc:  # intent is best-effort — never block the answer
        logger.warning("intent extraction failed: %s", exc)
        return fallback
    data = _loads(raw)
    search_text = str(data.get("search_text") or "").strip() or question
    node_types = [t for t in (data.get("node_types") or []) if t in _VALID_NODE_TYPES]
    category = str(data.get("category")).strip() if data.get("category") else None
    lens = str(data.get("lens") or "factual").strip().lower()
    return {
        "lens": lens if lens in _LENSES else "factual",
        "search_text": search_text,
        "node_types": node_types,
        "category": category,
        "list": bool(data.get("list")),
        "date_from": _valid_date(data.get("date_from")),
        "date_to": _valid_date(data.get("date_to")),
    }


async def _retrieve(
    conn: Any, lit: str, intent: dict[str, Any], k: int, use_filters: bool, list_mode: bool
) -> list:
    """
    list_mode=False: semantic KNN (rank by similarity) — for analytical questions.
    list_mode=True: structured enumeration (filter + chronological) — for "list all
    X" questions, where similarity top-k would drop most matches.
    """
    filters = ["n.valid_to IS NULL"]
    params: list[Any] = [] if list_mode else [lit]  # $1 = query vector (semantic only)

    def add(value: Any) -> str:
        params.append(value)
        return f"${len(params)}"

    if use_filters and intent["node_types"]:
        filters.append(f"n.node_type = ANY({add(intent['node_types'])}::text[])")
    if use_filters and intent.get("category"):
        filters.append(f"n.properties->>'category' ILIKE {add('%' + intent['category'] + '%')}")
    if use_filters and intent["date_from"]:
        filters.append(f"n.valid_from >= {add(intent['date_from'])}")
    if use_filters and intent["date_to"]:
        # day-inclusive: a date_to of 2026-07-05 must include events all day on the 5th
        filters.append(f"n.valid_from < ({add(intent['date_to'])}::date + INTERVAL '1 day')")
    limit_ph = add(k)
    order = "n.valid_from ASC NULLS LAST" if list_mode else "e.embedding <=> $1::vector"
    return await conn.fetch(
        f"""
        SELECT n.id, n.node_type, n.label, n.properties, n.source, n.source_url, n.valid_from
        FROM node_embeddings e
        JOIN nodes n ON n.id = e.node_id
        WHERE {' AND '.join(filters)}
        ORDER BY {order}
        LIMIT {limit_ph}
        """,
        *params,
    )


def _intent_out(intent: dict[str, Any]) -> dict[str, Any]:
    return {
        "lens": intent.get("lens", "factual"),
        "search_text": intent["search_text"],
        "node_types": intent["node_types"],
        "category": intent.get("category"),
        "list": bool(intent.get("list")),
        "date_from": intent["date_from"].isoformat() if intent["date_from"] else None,
        "date_to": intent["date_to"].isoformat() if intent["date_to"] else None,
    }


async def answer_question(question: str, k: int = 24) -> dict[str, Any]:
    """Answer a natural-language question from the most relevant graph facts."""
    question = (question or "").strip()
    if not question:
        return {"answer": "Bitte eine Frage eingeben.", "citations": [], "intent": {}}

    intent = await extract_intent(question)
    list_mode = bool(intent["list"])
    # "List all events" without an explicit date → default to upcoming (today on).
    if list_mode and not intent["date_from"] and (intent["category"] or "Event" in intent["node_types"]):
        intent["date_from"] = date.today()

    qvec = (await embed_texts([intent["search_text"]]))[0]
    lit = to_pgvector(qvec)

    has_filters = bool(
        intent["node_types"] or intent["category"] or intent["date_from"] or intent["date_to"]
    )
    k_eff = 60 if list_mode else k  # enumeration needs room; analysis stays tight
    async with get_conn() as conn:
        nodes = await _retrieve(conn, lit, intent, k_eff, use_filters=has_filters, list_mode=list_mode)
        # If filters were too narrow and found nothing, retry unfiltered + semantic.
        if not nodes and has_filters:
            nodes = await _retrieve(conn, lit, intent, k_eff, use_filters=False, list_mode=False)
        if not nodes:
            return {
                "answer": "Dazu liegen im Wissensgraphen keine passenden Fakten vor.",
                "citations": [],
                "intent": _intent_out(intent),
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
    today = date.today().isoformat()
    lens = intent["lens"]
    if lens == "factual":
        system = QA_SYSTEM_PROMPT
        prompt = QA_PROMPT.format(question=question, subgraph_json=subgraph, today=today)
    else:
        # analytical lens (synergy / inefficiency / scandal) over the same subgraph
        system = ANALYSIS_SYSTEM_PROMPTS[lens]
        prompt = ANALYSIS_PROMPT.format(question=question, subgraph_json=subgraph, today=today)
    # Reasoning models (e.g. deepseek-v4-pro) spend the budget on hidden
    # reasoning before the answer, so give ample headroom or `content` truncates.
    answer = await complete(system, prompt, max_tokens=4000)

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
    return {"answer": answer, "citations": citations, "intent": _intent_out(intent)}
