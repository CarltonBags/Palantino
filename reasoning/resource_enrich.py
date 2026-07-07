"""
Populate node_resources: deterministic offers/needs for POIs (OSM tags) and
LLM-tagged needs/offers for upcoming events. Idempotent (ON CONFLICT DO NOTHING).
"""
from __future__ import annotations

import json
import logging

from db.session import get_conn
from reasoning.llm import complete, fast_model
from reasoning.prompts import RESOURCE_TAG_PROMPT, RESOURCE_TAG_SYSTEM
from reasoning.resources import RESOURCES, poi_resources

logger = logging.getLogger(__name__)

_INSERT = (
    "INSERT INTO node_resources (node_id, kind, tag) VALUES ($1, $2, $3) "
    "ON CONFLICT DO NOTHING"
)


def _parse_tags(raw: str) -> tuple[list[str], list[str]] | None:
    """None = unparseable (leave pending, retry later); ([], []) = parsed fine,
    node genuinely has no tags from the vocabulary."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1].lstrip("json").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    needs = [t for t in data.get("needs", []) if t in RESOURCES]
    offers = [t for t in data.get("offers", []) if t in RESOURCES]
    return needs, offers


# Operational done-marker: a parsed-but-tagless node would otherwise never leave
# the pending set (NOT EXISTS node_resources) and get re-sent to the LLM on every
# sweep. The flag is bookkeeping, not a fact — the bitemporal rule is untouched.
_MARK_TAGGED = (
    "UPDATE nodes SET properties = properties || '{\"resource_tagged\": true}'::jsonb "
    "WHERE id = $1"
)


async def enrich_pois() -> int:
    """Deterministic POI offers/needs from OSM tags."""
    async with get_conn() as c:
        rows = await c.fetch(
            "SELECT id, properties FROM nodes WHERE node_type = 'POI' AND valid_to IS NULL"
        )
        recs: list[tuple] = []
        for r in rows:
            props = r["properties"]
            if isinstance(props, str):
                try:
                    props = json.loads(props)
                except json.JSONDecodeError:
                    props = {}
            needs, offers = poi_resources(props if isinstance(props, dict) else {})
            recs.extend((r["id"], "need", t) for t in needs)
            recs.extend((r["id"], "offer", t) for t in offers)
        for i in range(0, len(recs), 1000):
            await c.executemany(_INSERT, recs[i : i + 1000])
    return len(recs)


# news beat (category) → domain capability tag a journalist can amplify
_BEAT_DOMAIN: dict[str, str] = {
    "kultur": "kultur", "kunst": "kultur", "kreativ": "kultur",
    "sport": "sport", "fußball": "sport", "fussball": "sport", "handball": "sport",
    "eishockey": "sport", "bvb": "sport", "borussia": "sport",
    "soziales": "begegnung", "bildung": "bildung",
    "umwelt": "umwelt", "klima": "umwelt", "gesundheit": "gesundheit",
    "integration": "integration", "migration": "integration", "flucht": "integration",
    "wirtschaft": "finanzierung",
}


async def enrich_journalists() -> int:
    """Deterministic journalist resources. A journalist's core offer is REACH —
    they make actors/causes visible — so every one offers `sichtbarkeit`; their
    beats map to the domains they can amplify. This is what lets a Verein that
    NEEDS sichtbarkeit form a complementary synergy with a local journalist."""
    async with get_conn() as c:
        rows = await c.fetch(
            "SELECT id, properties FROM nodes WHERE node_type = 'Journalist' AND valid_to IS NULL"
        )
        recs: list[tuple] = []
        for r in rows:
            props = r["properties"]
            if isinstance(props, str):
                try:
                    props = json.loads(props)
                except json.JSONDecodeError:
                    props = {}
            offers = {"sichtbarkeit"}
            for beat in (props.get("beats") or []) if isinstance(props, dict) else []:
                low = str(beat).lower()
                for key, dom in _BEAT_DOMAIN.items():
                    if key in low:
                        offers.add(dom)
            recs.extend((r["id"], "offer", t) for t in offers if t in RESOURCES)
        for i in range(0, len(recs), 1000):
            await c.executemany(_INSERT, recs[i : i + 1000])
    return len(recs)


async def enrich_events(limit: int = 200) -> int:
    """LLM-tag upcoming, not-yet-tagged events with needs/offers."""
    async with get_conn() as c:
        evs = await c.fetch(
            """
            SELECT id, label, properties FROM nodes n
            WHERE node_type = 'Event' AND valid_to IS NULL AND valid_from >= CURRENT_DATE
              AND NOT (properties ? 'resource_tagged')
              AND NOT EXISTS (SELECT 1 FROM node_resources nr WHERE nr.node_id = n.id)
            LIMIT $1
            """,
            limit,
        )
    tagged = 0
    for e in evs:
        props = e["properties"]
        if isinstance(props, str):
            try:
                props = json.loads(props)
            except json.JSONDecodeError:
                props = {}
        if not isinstance(props, dict):
            props = {}
        prompt = RESOURCE_TAG_PROMPT.format(
            label=e["label"],
            category=props.get("category") or "—",
            description=(props.get("description") or "")[:600],
        )
        try:
            raw = await complete(RESOURCE_TAG_SYSTEM, prompt, max_tokens=1500, model=fast_model())
        except Exception as exc:
            logger.warning("event tag failed for %s: %s", e["id"], exc)
            continue
        parsed = _parse_tags(raw)
        if parsed is None:
            continue
        needs, offers = parsed
        recs = [(e["id"], "need", t) for t in needs] + [(e["id"], "offer", t) for t in offers]
        async with get_conn() as c:
            if recs:
                await c.executemany(_INSERT, recs)
                tagged += 1
            await c.execute(_MARK_TAGGED, e["id"])
    return tagged


async def enrich_actors(limit: int = 400) -> int:
    """LLM-tag extracted actors (news civic actors + calendar venues) with
    needs/offers, so they can take part in complementary synergies."""
    async with get_conn() as c:
        actors = await c.fetch(
            """
            SELECT id, label, properties FROM nodes n
            WHERE source IN ('news_extraction', 'event_venue') AND valid_to IS NULL
              AND NOT (properties ? 'resource_tagged')
              AND NOT EXISTS (SELECT 1 FROM node_resources nr WHERE nr.node_id = n.id)
            LIMIT $1
            """,
            limit,
        )
    tagged = 0
    for a in actors:
        props = a["properties"] if isinstance(a["properties"], dict) else {}
        prompt = RESOURCE_TAG_PROMPT.format(
            label=a["label"],
            category=props.get("org_type") or "Organisation",
            description=(props.get("role") or "")[:400],
        )
        try:
            raw = await complete(RESOURCE_TAG_SYSTEM, prompt, max_tokens=1500, model=fast_model())
        except Exception as exc:
            logger.warning("actor tag failed for %s: %s", a["id"], exc)
            continue
        parsed = _parse_tags(raw)
        if parsed is None:
            continue
        needs, offers = parsed
        recs = [(a["id"], "need", t) for t in needs] + [(a["id"], "offer", t) for t in offers]
        async with get_conn() as c:
            if recs:
                await c.executemany(_INSERT, recs)
                tagged += 1
            await c.execute(_MARK_TAGGED, a["id"])
    return tagged
