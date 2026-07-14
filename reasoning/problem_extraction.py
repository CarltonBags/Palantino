"""
Extract current CIVIC PROBLEMS from news articles.

News describes not only actors but deficits: vacancy, unsafe crossings,
missing offers, loneliness, understaffing. This pass distils them into Problem
nodes (source='problem_extraction', inferred=True, evidence articles linked
MENTIONS, needs tagged from the closed resource vocabulary) — the solvable
units the problem→solver retrieval matches actors against.

GDPR (rule 5): problems are described institutionally; never private persons.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from db.session import get_conn
from ingestion.writer import upsert_edge, upsert_node
from ontology.edges import located_in, mentions
from ontology.nodes import Problem
from reasoning.llm import complete, fast_model
from reasoning.prompts import PROBLEM_EXTRACT_PROMPT, PROBLEM_EXTRACT_SYSTEM
from reasoning.resources import RESOURCES

logger = logging.getLogger(__name__)

_NEWS_SOURCES = ("nordstadtblogger", "wirindortmund")

_INSERT_NEED = (
    "INSERT INTO node_resources (node_id, kind, tag) VALUES ($1, 'need', $2) "
    "ON CONFLICT DO NOTHING"
)

# Operational done-marker (same pattern as actor extraction): an article that
# names no problem would otherwise be re-sent to the LLM on every sweep.
_MARK = (
    "UPDATE nodes SET properties = properties || '{\"problems_extracted\": true}'::jsonb "
    "WHERE id = $1"
)

# Bump the problem's freshness when a newer article evidences it — expiry
# closes problems whose coverage stopped. Operational field, not a fact.
_TOUCH_EVIDENCE = """
    UPDATE nodes SET properties = properties ||
        jsonb_build_object('last_evidence', greatest(
            coalesce(properties->>'last_evidence', '1970-01-01'), $2::text))
    WHERE id = $1
"""


def _problem_key(name: str) -> str:
    norm = re.sub(r"\s+", " ", name.lower()).strip(" .,-–")
    return hashlib.sha256(norm.encode()).hexdigest()[:20]


def _parse_list(raw: str) -> list[dict[str, Any]] | None:
    """None = unparseable (retry later); [] = parsed, article names no problem."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1].lstrip("json").strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return [d for d in data if isinstance(d, dict)]


async def _district_edge(conn: Any, problem_id: str, district: str, prov: dict) -> None:
    row = await conn.fetchrow(
        """
        SELECT id FROM nodes WHERE node_type = 'GeoArea' AND valid_to IS NULL
          AND lower(label) = lower($1) LIMIT 1
        """,
        district.strip(),
    )
    if row:
        await upsert_edge(
            located_in(
                problem_id, row["id"], inferred=True,
                source_id=f"{problem_id}->geo", **prov,
            )
        )


async def extract_problems(limit: int = 200, months: int = 24) -> dict[str, int]:
    """Extract problems from not-yet-processed recent news (newest first). The
    window is narrower than actor extraction: a problem from 2020 coverage is
    not evidence of a CURRENT problem."""
    async with get_conn() as conn:
        arts = await conn.fetch(
            """
            SELECT id, label, properties, source_url, valid_from
            FROM nodes n
            WHERE node_type = 'Event' AND valid_to IS NULL
              AND coalesce(properties->>'event_type', '') = 'news'
              AND source = ANY($2::text[])
              AND valid_from >= CURRENT_DATE - make_interval(months => $3)
              AND NOT (properties ? 'problems_extracted')
            ORDER BY valid_from DESC
            LIMIT $1
            """,
            limit, list(_NEWS_SOURCES), months,
        )

    from embeddings.backfill import embed_nodes

    counts = {"articles": 0, "problems": 0, "mentions": 0}
    for a in arts:
        if counts["articles"] and counts["articles"] % 100 == 0:
            await embed_nodes()
        counts["articles"] += 1
        props = a["properties"] if isinstance(a["properties"], dict) else {}
        text = (props.get("description") or props.get("subtitle") or "")[:1600]
        try:
            raw = await complete(
                PROBLEM_EXTRACT_SYSTEM,
                PROBLEM_EXTRACT_PROMPT.format(title=a["label"], text=text or a["label"]),
                max_tokens=1500, model=fast_model(),
            )
        except Exception as exc:
            logger.warning("problem extraction failed for %s: %s", a["id"], exc)
            continue
        items = _parse_list(raw)
        if items is None:
            continue
        article_date = (a["valid_from"].date().isoformat() if a["valid_from"] else "")
        prov = dict(source="problem_extraction", source_url=a["source_url"])
        for item in items:
            name = (item.get("name") or "").strip()
            if len(name) < 5:
                continue
            node = Problem(
                label=name,
                properties={
                    "theme": item.get("theme"),
                    "district": item.get("district"),
                    "affected": item.get("affected"),
                    "last_evidence": article_date,
                },
                source_id=_problem_key(name),
                inferred=True,
                confidence=0.6,
                reasoning_trace=f"aus Nachrichtenartikel „{a['label'][:80]}“ destilliert",
                **prov,
            )
            problem_id, was_new = await upsert_node(node)
            if was_new:
                counts["problems"] += 1
            async with get_conn() as conn:
                if article_date:
                    await conn.execute(_TOUCH_EVIDENCE, problem_id, article_date)
                for need in {n for n in (item.get("needs") or []) if n in RESOURCES}:
                    await conn.execute(_INSERT_NEED, problem_id, need)
                if item.get("district"):
                    await _district_edge(conn, problem_id, item["district"], prov)
            _, edge_new = await upsert_edge(
                mentions(
                    a["id"], problem_id, source="problem_extraction", inferred=True,
                    source_id=f"{a['id']}->{problem_id}",
                )
            )
            if edge_new:
                counts["mentions"] += 1
        async with get_conn() as conn:
            await conn.execute(_MARK, a["id"])
    await embed_nodes()
    logger.info("problem extraction: %s", counts)
    return counts


async def expire_stale_problems(keep_months: int = 12) -> int:
    """Close problems whose newest evidence is older than keep_months — no
    fresh coverage means we can no longer claim it is a CURRENT problem."""
    async with get_conn() as conn:
        res = await conn.execute(
            """
            UPDATE nodes SET valid_to = now()
            WHERE node_type = 'Problem' AND valid_to IS NULL
              AND coalesce(properties->>'last_evidence', '1970-01-01')::date
                  < CURRENT_DATE - make_interval(months => $1)
            """,
            keep_months,
        )
    n = int(res.split()[-1])
    if n:
        logger.info("expired %d stale problems", n)
    return n
