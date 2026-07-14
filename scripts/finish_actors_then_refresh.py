"""
Resume: finish the ACTOR resource re-tag, then run a full frontier refresh — in
ONE process so there's a single thing to keep alive (the split
retag-process + waiter kept dying independently when the machine slept).

Events are intentionally skipped (deferred to a later batch). Run under
caffeinate:
    caffeinate -is python -m scripts.finish_actors_then_refresh
"""
from __future__ import annotations

import asyncio
import logging

from db.session import get_conn
from reasoning.candidate_engine import refresh_candidates
from reasoning.resource_enrich import enrich_actors

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("resume")

_PENDING_ACTORS = """
    SELECT count(*) FROM nodes n WHERE source IN ('news_extraction','event_venue')
      AND valid_to IS NULL AND NOT (properties ? 'resource_tagged')
      AND NOT EXISTS (SELECT 1 FROM node_resources nr WHERE nr.node_id = n.id)
"""


async def main() -> None:
    prev = None
    while True:
        async with get_conn() as c:
            pending = await c.fetchval(_PENDING_ACTORS)
        log.info("actors pending: %d", pending)
        # residual are usually unparseable nodes that never flag; stop near zero
        if pending <= 10 or pending == prev:
            break
        prev = pending
        await enrich_actors(limit=300)

    log.info("actor retag done — starting full frontier refresh")
    result = await refresh_candidates()
    log.info("REFRESH RESULT: %s", result)


if __name__ == "__main__":
    asyncio.run(main())
