"""
Tellerrand ("über den Tellerrand schauen") — horizon-broadening discovery.

Given an interest or an organisation the person is part of, propose ADJACENT-but-
different interests that stretch them out of their comfort zone, then ground each
in real Dortmund offerings (events, clubs, organisations, places).

Stage 1: the LLM proposes n stretch interests + a bridge (why it connects + widens).
Stage 2: each is matched to actual graph entities by semantic retrieval.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from db.session import get_conn
from embeddings.embedder import embed_texts, to_pgvector
from reasoning.llm import complete
from reasoning.prompts import TELLERRAND_PROMPT, TELLERRAND_SYSTEM

logger = logging.getLogger(__name__)

_MATCH_TYPES = ("Event", "POI", "Organization")


def _parse_list(raw: str) -> list[dict[str, Any]]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1].lstrip("json").strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    return [d for d in data if isinstance(d, dict)]


async def extend_horizon(interest: str, n: int = 5) -> list[dict[str, Any]]:
    interest = (interest or "").strip()
    if not interest:
        return []

    raw = await complete(
        TELLERRAND_SYSTEM,
        TELLERRAND_PROMPT.format(interest=interest, n=n),
        max_tokens=2000,
    )
    ideas = _parse_list(raw)[:n]
    if not ideas:
        return []

    searches = [(i.get("search") or i.get("interest") or "").strip() for i in ideas]
    vecs = await embed_texts(searches)

    out: list[dict[str, Any]] = []
    async with get_conn() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL hnsw.iterative_scan = 'relaxed_order'")
            await conn.execute("SET LOCAL hnsw.ef_search = 200")
            for idea, vec in zip(ideas, vecs):
                rows = await conn.fetch(
                    """
                    SELECT n.id::text AS id, n.node_type, n.label, n.source_url
                    FROM node_embeddings e
                    JOIN nodes n ON n.id = e.node_id
                    WHERE n.valid_to IS NULL AND n.node_type = ANY($2::text[])
                    ORDER BY e.embedding <=> $1::vector
                    LIMIT 3
                    """,
                    to_pgvector(vec), list(_MATCH_TYPES),
                )
                out.append({
                    "interest": idea.get("interest"),
                    "bridge": idea.get("bridge"),
                    "options": [
                        {"id": r["id"], "node_type": r["node_type"],
                         "label": r["label"], "source_url": r["source_url"]}
                        for r in rows
                    ],
                })
    logger.info("tellerrand: %d stretch interests for %r", len(out), interest[:40])
    return out
