"""
One-off backfill: re-tag LLM-tagged actors/events with the new capability/domain
resource vocabulary + actor-aware prompt.

Clears resource_tagged + node_resources for exactly the nodes enrich_actors /
enrich_events will re-select (news_extraction/event_venue actors + upcoming
events), then drains both enrichers on the fast model. POIs and journalists are
left alone — they are tagged deterministically, not by the LLM.

Run under caffeinate so an idle machine can't kill the connection mid-run:
    caffeinate -is python scripts/retag_resources.py
"""
from __future__ import annotations

import asyncio
import logging

from db.session import get_conn
from reasoning.resource_enrich import enrich_actors, enrich_events

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("retag")

# scope predicates (kept identical between the DELETE, the flag-clear and the
# enricher's own selection, so nothing is orphaned)
_SCOPE_ACTORS = (
    "valid_to IS NULL AND source IN ('news_extraction','event_venue') "
    "AND node_type = 'Organization' AND (properties ? 'resource_tagged')"
)
_SCOPE_EVENTS = (
    "valid_to IS NULL AND node_type = 'Event' AND valid_from >= CURRENT_DATE "
    "AND (properties ? 'resource_tagged')"
)


async def _clear(scope: str) -> None:
    async with get_conn() as c:
        await c.execute(
            f"DELETE FROM node_resources WHERE node_id IN "
            f"(SELECT id FROM nodes WHERE {scope})"
        )
        await c.execute(
            f"UPDATE nodes SET properties = properties - 'resource_tagged' WHERE {scope}"
        )

_PENDING_ACTORS = """
    SELECT count(*) FROM nodes n WHERE source IN ('news_extraction','event_venue')
      AND valid_to IS NULL AND NOT (properties ? 'resource_tagged')
      AND NOT EXISTS (SELECT 1 FROM node_resources nr WHERE nr.node_id = n.id)
"""
_PENDING_EVENTS = """
    SELECT count(*) FROM nodes n WHERE node_type = 'Event' AND valid_to IS NULL
      AND valid_from >= CURRENT_DATE AND NOT (properties ? 'resource_tagged')
      AND NOT EXISTS (SELECT 1 FROM node_resources nr WHERE nr.node_id = n.id)
"""


async def _drain(name: str, enrich, pending_sql: str, batch: int) -> None:
    """Run enrich in batches until pending hits 0 or stops shrinking (persistent
    parse failures never get flagged, so guard against an infinite loop)."""
    prev = None
    while True:
        async with get_conn() as c:
            pending = await c.fetchval(pending_sql)
        log.info("%s: %d pending", name, pending)
        if pending == 0 or pending == prev:
            break
        prev = pending
        await enrich(limit=batch)


async def main() -> None:
    await _clear(_SCOPE_ACTORS)
    await _clear(_SCOPE_EVENTS)
    log.info("cleared old tags in scope; starting re-tag on fast model")
    await _drain("actors", enrich_actors, _PENDING_ACTORS, batch=300)
    await _drain("events", enrich_events, _PENDING_EVENTS, batch=200)
    log.info("retag backfill done")


if __name__ == "__main__":
    asyncio.run(main())
