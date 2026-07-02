"""
Extract venue ACTORS from calendar events.

A calendar event names its `venue` (Musiktheater Piano, Museum für Kunst und
Kulturgeschichte …) plus contact + location. Those venues are 100%-active,
contactable, geolocated actors that host many events — ideal synergy partners.
This pass materialises each distinct venue as an Organization actor
(source='event_venue', geom from its events, contacts carried over) and links every
event LOCATED_IN it. Unlike news actors these come with coordinates → they can take
part in proximity synergies too.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from db.session import get_conn
from ingestion.writer import upsert_edge, upsert_node
from ontology.edges import located_in
from ontology.nodes import Organization

logger = logging.getLogger(__name__)


def _key(name: str) -> str:
    norm = re.sub(r"\s+", " ", name.lower()).strip(" .,-–")
    return "venue_" + hashlib.sha256(norm.encode()).hexdigest()[:18]


async def extract_event_venues(limit: int = 300) -> dict[str, int]:
    """Materialise venue actors + LOCATED_IN edges for not-yet-linked events."""
    async with get_conn() as conn:
        venues = await conn.fetch(
            """
            SELECT properties->>'venue' AS venue,
                   max(properties->>'contact_website') AS website,
                   max(properties->>'contact_email')   AS email,
                   max(properties->>'contact_phone')   AS phone,
                   max(properties->>'street')          AS street,
                   max(properties->>'stadtbezirk')     AS bezirk,
                   max(source_url)                     AS source_url,
                   (array_agg(ST_AsGeoJSON(geom)) FILTER (WHERE geom IS NOT NULL))[1] AS geojson,
                   array_agg(id) AS event_ids
            FROM nodes n
            WHERE source = 'dortmund_veranstaltungskalender' AND node_type = 'Event'
              AND coalesce(properties->>'venue', '') <> '' AND valid_to IS NULL
              AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.from_node_id = n.id
                  AND e.source = 'event_venue' AND e.valid_to IS NULL)
            GROUP BY properties->>'venue'
            ORDER BY count(*) DESC
            LIMIT $1
            """,
            limit,
        )

    counts = {"venues": 0, "links": 0}
    for v in venues:
        name = (v["venue"] or "").strip()
        if len(name) < 3:
            continue
        geom = json.loads(v["geojson"]) if v["geojson"] else None
        node = Organization(
            label=name,
            properties={
                "org_type": "veranstaltungsort",
                "contact_website": v["website"],
                "contact_email": v["email"],
                "contact_phone": v["phone"],
                "street": v["street"],
                "stadtbezirk": v["bezirk"],
                "from_events": True,
            },
            source="event_venue",
            source_id=_key(name),
            source_url=v["website"] or v["source_url"],
            geom=geom,
            inferred=False,
            confidence=0.9,
        )
        vid, was_new = await upsert_node(node)
        if was_new:
            counts["venues"] += 1
        for eid in v["event_ids"]:
            _, edge_new = await upsert_edge(
                located_in(eid, vid, source="event_venue", inferred=False,
                           source_id=f"{eid}->{vid}")
            )
            if edge_new:
                counts["links"] += 1
    logger.info("venue extraction: %s", counts)
    return counts
