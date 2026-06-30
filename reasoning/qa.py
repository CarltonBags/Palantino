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
import random
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

_LENSES = {"factual", "synergy", "inefficiency", "scandal", "crime", "leads"}

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
    sql = f"""
        SELECT n.id, n.node_type, n.label, n.properties, n.source, n.source_url, n.valid_from
        FROM node_embeddings e
        JOIN nodes n ON n.id = e.node_id
        WHERE {' AND '.join(filters)}
        ORDER BY {order}
        LIMIT {limit_ph}
        """
    if list_mode:
        return await conn.fetch(sql, *params)
    # filtered ANN → iterative scan so a type/date filter doesn't starve results
    async with conn.transaction():
        await conn.execute("SET LOCAL hnsw.iterative_scan = 'relaxed_order'")
        await conn.execute("SET LOCAL hnsw.ef_search = 200")
        return await conn.fetch(sql, *params)


# Don't fan out THROUGH these hub types — a district links to ~all co-located
# nodes, which would explode the subgraph with noise. We still keep a hub if a
# seed links to it (one hop in), we just don't expand its other members.
_HUB_TYPES = ("GeoArea",)
_NODE_COLS = "id, node_type, label, properties, source, source_url, valid_from"


async def _expand(conn: Any, seed_ids: list[str], max_total: int = 40, hops: int = 2) -> list[str]:
    """
    Multi-hop graph expansion: walk edges out from the vector seeds (up to `hops`),
    adding connected nodes — so the LLM sees relationally-linked facts (a tender
    and the resolution behind it, a meeting and its agenda items) that pure vector
    recall misses. Bounded: skips fan-out through hub types, caps the total.
    """
    seen: set[str] = set(seed_ids)
    frontier = list(seed_ids)
    for _ in range(hops):
        if len(seen) >= max_total or not frontier:
            break
        expandable = await conn.fetch(
            "SELECT id FROM nodes WHERE id = ANY($1::uuid[]) "
            "AND node_type <> ALL($2::text[])",
            frontier, list(_HUB_TYPES),
        )
        ex_ids = [str(r["id"]) for r in expandable]
        if not ex_ids:
            break
        rows = await conn.fetch(
            """
            SELECT DISTINCT
                CASE WHEN from_node_id = ANY($1::uuid[]) THEN to_node_id ELSE from_node_id END AS nid
            FROM edges
            WHERE (from_node_id = ANY($1::uuid[]) OR to_node_id = ANY($1::uuid[]))
              AND valid_to IS NULL
            LIMIT 400
            """,
            ex_ids,
        )
        new = [str(r["nid"]) for r in rows if str(r["nid"]) not in seen]
        new = new[: max(0, max_total - len(seen))]
        if not new:
            break
        seen.update(new)
        frontier = new
    return list(seen)


async def _diverse_seeds(
    conn: Any, qvec: list[float], k: int, intent: dict[str, Any] | None = None,
    pool_size: int = 80, lam: float = 0.55,
) -> list[dict[str, Any]]:
    """
    MMR seed selection for BROAD analytical queries. Pure KNN on a generic query
    ("Synergien für die Stadt") deterministically returns the same densest cluster
    every time → repetitive answers. Instead pull a larger relevance pool (scoped
    by the same filters, e.g. node_types=[POI,Organization] for leads), pick a
    RANDOM first seed from the top (rotation across asks), then greedily add seeds
    that are relevant but dissimilar to those already chosen (MMR → breadth).
    """
    filters = ["n.valid_to IS NULL"]
    params: list[Any] = [to_pgvector(qvec)]

    def add(value: Any) -> str:
        params.append(value)
        return f"${len(params)}"

    if intent:
        if intent.get("node_types"):
            filters.append(f"n.node_type = ANY({add(intent['node_types'])}::text[])")
        if intent.get("category"):
            filters.append(f"n.properties->>'category' ILIKE {add('%' + intent['category'] + '%')}")
        if intent.get("date_from"):
            filters.append(f"n.valid_from >= {add(intent['date_from'])}")
        if intent.get("date_to"):
            filters.append(f"n.valid_from < ({add(intent['date_to'])}::date + INTERVAL '1 day')")
    params.append(pool_size)
    # Filtered ANN: without iterative scan, HNSW returns the globally-nearest
    # vectors THEN applies the filter — so a type filter (e.g. POI) can yield ~0
    # rows when the nearest are another type. pgvector 0.8 iterative scan keeps
    # searching until LIMIT rows pass the filter.
    async with conn.transaction():
        await conn.execute("SET LOCAL hnsw.iterative_scan = 'relaxed_order'")
        await conn.execute("SET LOCAL hnsw.ef_search = 200")
        rows = await conn.fetch(
            f"""
            SELECT {_NODE_COLS}, e.embedding::text AS emb
            FROM node_embeddings e
            JOIN nodes n ON n.id = e.node_id
            WHERE {' AND '.join(filters)}
            ORDER BY e.embedding <=> $1::vector
            LIMIT ${len(params)}
            """,
            *params,
        )
    if not rows:
        return []
    import numpy as np

    def _vec(s: str) -> Any:
        v = np.array([float(x) for x in s.strip("[]").split(",")])
        n = np.linalg.norm(v)
        return v / n if n else v

    vecs = [_vec(r["emb"]) for r in rows]
    q = np.array(qvec, dtype=float)
    q = q / (np.linalg.norm(q) or 1.0)
    sim_q = [float(q @ v) for v in vecs]
    n = len(rows)
    k = min(k, n)
    selected = [random.randrange(min(12, n))]  # random anchor → rotates per ask
    while len(selected) < k:
        best_i, best = -1, -1e9
        for i in range(n):
            if i in selected:
                continue
            div = max(float(vecs[i] @ vecs[j]) for j in selected)
            score = lam * sim_q[i] - (1.0 - lam) * div
            if score > best:
                best, best_i = score, i
        selected.append(best_i)
    out = []
    for i in selected:
        d = dict(rows[i])
        d.pop("emb", None)
        out.append(d)
    return out


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


async def answer_question(question: str, k: int = 24, lens_override: str | None = None) -> dict[str, Any]:
    """Answer a question from the most relevant graph facts. lens_override forces
    a lens (e.g. the dedicated leads window) instead of auto-detecting it."""
    question = (question or "").strip()
    if not question:
        return {"answer": "Bitte eine Frage eingeben.", "citations": [], "intent": {}}

    intent = await extract_intent(question)
    if lens_override in _LENSES:
        intent["lens"] = lens_override
    # Enumeration only makes sense for factual "list all X" queries; analytical
    # lenses always want the focused + graph-expanded subgraph, never a dump.
    list_mode = bool(intent["list"]) and intent["lens"] == "factual"
    # "List all events" without an explicit date → default to upcoming (today on).
    if list_mode and not intent["date_from"] and (intent["category"] or "Event" in intent["node_types"]):
        intent["date_from"] = date.today()

    # Leads = business development: scope to real business actors, not news, so the
    # retrieval surfaces actual prospects (POIs/Orgs) instead of editorial roundups.
    if intent["lens"] == "leads" and not intent["node_types"]:
        intent["node_types"] = ["POI", "Organization"]

    qvec = (await embed_texts([intent["search_text"]]))[0]
    lit = to_pgvector(qvec)

    has_filters = bool(
        intent["node_types"] or intent["category"] or intent["date_from"] or intent["date_to"]
    )
    # Diversify (MMR + random anchor) for exploratory analytical asks so they don't
    # keep returning the same dense cluster: any broad lens, and always leads
    # (inherently exploratory, but scoped to its business node_types via intent).
    diversify = (
        intent["lens"] != "factual" and not list_mode
        and (not has_filters or intent["lens"] == "leads")
    )
    k_eff = 60 if list_mode else k
    async with get_conn() as conn:
        if diversify:
            nodes = await _diverse_seeds(conn, qvec, k_eff, intent)
        else:
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
        # Multi-hop graph expansion (analytical/factual only — not enumeration):
        # follow edges out from the vector seeds to pull in connected facts.
        if not list_mode:
            ids = await _expand(conn, ids, max_total=40)
            nodes = await conn.fetch(
                f"SELECT {_NODE_COLS} FROM nodes WHERE id = ANY($1::uuid[]) AND valid_to IS NULL",
                ids,
            )
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
    # reasoning before the answer, so give ample headroom or `content` truncates
    # to empty. 8000 makes that rare; the guard below keeps the UI non-blank.
    answer = await complete(system, prompt, max_tokens=8000)
    if not answer.strip():
        answer = "_Die Antwort konnte nicht erzeugt werden — bitte erneut versuchen._"

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


_ANCHOR_LABEL = {
    "synergy": "Synergien", "inefficiency": "Ineffizienzen",
    "scandal": "Auffälligkeiten", "crime": "Vorfallsmuster",
}


async def analyze_node(node_id: str, lens: str, k: int = 20) -> dict[str, Any]:
    """
    Anchored analysis: run a lens (synergy/inefficiency/…) centred on ONE node
    (e.g. a chosen event), over that node + its semantically-nearest neighbours +
    their edges. Powers the 'add to chat' event picker.
    """
    lens = lens if lens in _ANCHOR_LABEL else "synergy"
    async with get_conn() as conn:
        anchor = await conn.fetchrow(
            "SELECT id, node_type, label, properties, source, source_url, valid_from "
            "FROM nodes WHERE id = $1 AND valid_to IS NULL",
            node_id,
        )
        if anchor is None:
            return {"answer": "Knoten nicht gefunden.", "citations": [], "intent": {"lens": lens}}
        neighbours = await conn.fetch(
            """
            SELECT n.id, n.node_type, n.label, n.properties, n.source, n.source_url, n.valid_from
            FROM node_embeddings e
            JOIN nodes n ON n.id = e.node_id
            WHERE n.valid_to IS NULL AND n.id <> $1
            ORDER BY e.embedding <=> (SELECT embedding FROM node_embeddings WHERE node_id = $1)
            LIMIT $2
            """,
            node_id, k,
        )
        seed_ids = [str(anchor["id"])] + [str(n["id"]) for n in neighbours]
        # graph-expand from the anchor + its semantic neighbours
        ids = await _expand(conn, seed_ids, max_total=40)
        nodes = await conn.fetch(
            f"SELECT {_NODE_COLS} FROM nodes WHERE id = ANY($1::uuid[]) AND valid_to IS NULL",
            ids,
        )
        nodes = [dict(n) for n in nodes]
        edges = await conn.fetch(
            """
            SELECT id, edge_type, from_node_id, to_node_id, properties, source
            FROM edges
            WHERE from_node_id = ANY($1::uuid[]) AND to_node_id = ANY($1::uuid[])
              AND valid_to IS NULL
            """,
            ids,
        )

    question = f"Welche {_ANCHOR_LABEL[lens]} gibt es rund um: {anchor['label']}?"
    subgraph = format_subgraph(nodes, edges)
    prompt = ANALYSIS_PROMPT.format(
        question=question, subgraph_json=subgraph, today=date.today().isoformat()
    )
    answer = await complete(ANALYSIS_SYSTEM_PROMPTS[lens], prompt, max_tokens=8000)
    if not answer.strip():
        answer = "_Die Analyse konnte nicht erzeugt werden — bitte erneut versuchen._"
    citations = [
        {
            "id": str(n["id"]), "label": n["label"], "node_type": n["node_type"],
            "source": n["source"], "source_url": n["source_url"],
        }
        for n in nodes
    ]
    return {
        "answer": answer,
        "citations": citations,
        "question": question,
        "intent": {"lens": lens, "anchor": anchor["label"]},
    }
