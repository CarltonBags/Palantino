"""
Populate node_resources: deterministic offers/needs for POIs (OSM tags) and
LLM-tagged needs/offers for upcoming events. Idempotent (ON CONFLICT DO NOTHING).
"""
from __future__ import annotations

import json
import logging

from db.session import get_conn
from reasoning.llm import complete
from reasoning.prompts import RESOURCE_TAG_PROMPT, RESOURCE_TAG_SYSTEM
from reasoning.resources import RESOURCES, poi_resources

logger = logging.getLogger(__name__)

_INSERT = (
    "INSERT INTO node_resources (node_id, kind, tag) VALUES ($1, $2, $3) "
    "ON CONFLICT DO NOTHING"
)


def _parse_tags(raw: str) -> tuple[list[str], list[str]]:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1].lstrip("json").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return [], []
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return [], []
    needs = [t for t in data.get("needs", []) if t in RESOURCES]
    offers = [t for t in data.get("offers", []) if t in RESOURCES]
    return needs, offers


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


async def enrich_events(limit: int = 200) -> int:
    """LLM-tag upcoming, not-yet-tagged events with needs/offers."""
    async with get_conn() as c:
        evs = await c.fetch(
            """
            SELECT id, label, properties FROM nodes n
            WHERE node_type = 'Event' AND valid_to IS NULL AND valid_from >= CURRENT_DATE
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
            raw = await complete(RESOURCE_TAG_SYSTEM, prompt, max_tokens=4000)  # main model
        except Exception as exc:
            logger.warning("event tag failed for %s: %s", e["id"], exc)
            continue
        needs, offers = _parse_tags(raw)
        recs = [(e["id"], "need", t) for t in needs] + [(e["id"], "offer", t) for t in offers]
        if recs:
            async with get_conn() as c:
                await c.executemany(_INSERT, recs)
            tagged += 1
    return tagged
