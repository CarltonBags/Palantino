"""
Extract real ACTORS from news articles.

News enters the graph as Event nodes that are actually articles — not entities you
can partner with. This pass reads each recent article and pulls out the named
orgs/initiatives/Vereine/offices it describes, materialising them as Organization
nodes (source='news_extraction', inferred=True, article as provenance) linked
MENTIONS. Synergy discovery can then anchor on the ACTORS, not the articles.

GDPR (rule 5): organisations/initiatives only — never private individuals.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from db.session import get_conn
from ingestion.writer import upsert_edge, upsert_node
from ontology.edges import mentions
from ontology.nodes import Organization
from reasoning.llm import complete, fast_model
from reasoning.prompts import NEWS_ACTOR_PROMPT, NEWS_ACTOR_SYSTEM

logger = logging.getLogger(__name__)

_NEWS_SOURCES = ("nordstadtblogger", "wirindortmund")


def _actor_key(name: str) -> str:
    norm = re.sub(r"\s+", " ", name.lower()).strip(" .,-–")
    return hashlib.sha256(norm.encode()).hexdigest()[:20]


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


async def extract_news_actors(limit: int = 200, months: int = 60) -> dict[str, int]:
    """Extract actors from not-yet-processed news articles (newest first). The
    window is wide because the ORGANISATIONS a 2022 article describes (a Verein, a
    Stiftung) are usually still active today — the article age isn't the actor age.
    Full backlog coverage needs repeated runs (thousands of articles)."""
    async with get_conn() as conn:
        arts = await conn.fetch(
            f"""
            SELECT id, label, properties, source_url, source
            FROM nodes n
            WHERE node_type = 'Event' AND valid_to IS NULL
              AND coalesce(properties->>'event_type', '') = 'news'
              AND source = ANY($2::text[])
              AND valid_from >= CURRENT_DATE - make_interval(months => $3)
              AND NOT EXISTS (
                  SELECT 1 FROM edges e WHERE e.from_node_id = n.id
                    AND e.edge_type = 'MENTIONS' AND e.source = 'news_extraction'
                    AND e.valid_to IS NULL)
            ORDER BY valid_from DESC
            LIMIT $1
            """,
            limit, list(_NEWS_SOURCES), months,
        )

    counts = {"articles": 0, "actors": 0, "mentions": 0}
    for a in arts:
        counts["articles"] += 1
        props = a["properties"] if isinstance(a["properties"], dict) else {}
        text = (props.get("description") or props.get("subtitle") or "")[:1600]
        try:
            raw = await complete(
                NEWS_ACTOR_SYSTEM,
                NEWS_ACTOR_PROMPT.format(title=a["label"], text=text or a["label"]),
                max_tokens=1200, model=fast_model(),
            )
        except Exception as exc:
            logger.warning("actor extraction failed for %s: %s", a["id"], exc)
            continue
        for act in _parse_list(raw):
            name = (act.get("name") or "").strip()
            if len(name) < 3:
                continue
            node = Organization(
                label=name,
                properties={
                    "org_type": act.get("type") or "sonstige",
                    "role": act.get("role"),
                    "from_news": True,
                },
                source="news_extraction",
                source_id=_actor_key(name),
                source_url=a["source_url"],
                inferred=True,
                confidence=0.7,
                reasoning_trace=f"aus Nachrichtenartikel „{a['label'][:80]}“ extrahiert",
            )
            org_id, was_new = await upsert_node(node)
            if was_new:
                counts["actors"] += 1
            _, edge_new = await upsert_edge(
                mentions(
                    a["id"], org_id, source="news_extraction", inferred=True,
                    source_id=f"{a['id']}->{org_id}",
                )
            )
            if edge_new:
                counts["mentions"] += 1
    logger.info("actor extraction: %s", counts)
    return counts
