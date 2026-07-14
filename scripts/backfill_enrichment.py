"""One-off overnight drain of the LLM enrichment backlogs (DeepSeek flash).

Runs the three jobs in sequence, each until its pending set is empty or stops
shrinking (hard LLM failures stay pending for the regular sweeps):

  1. extract_news_actors — news articles → Organization actors + MENTIONS
  2. enrich_events       — upcoming events → need/offer resource tags
  3. enrich_actors       — extracted actors/venues → need/offer resource tags
  4. extract_problems    — recent news (24 months) → Problem nodes + needs

Safe to kill and re-run: every job is incremental and idempotent, and
extraction embeds in flushes so a partial run still leaves actors searchable.

Usage:  python -m scripts.backfill_enrichment
"""
from __future__ import annotations

import asyncio
import logging
import time

from db.session import close_pool, get_conn
from reasoning.actor_extraction import extract_news_actors
from reasoning.problem_extraction import extract_problems
from reasoning.resource_enrich import enrich_actors, enrich_events

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill")

BATCH = 500

# Pending counts — must mirror the fetch filters in the respective job.
_PENDING = {
    "actor_extraction": """
        SELECT count(*) FROM nodes n
        WHERE node_type = 'Event' AND valid_to IS NULL
          AND coalesce(properties->>'event_type', '') = 'news'
          AND source = ANY(ARRAY['nordstadtblogger', 'wirindortmund'])
          AND valid_from >= CURRENT_DATE - interval '60 months'
          AND NOT (properties ? 'actors_extracted')
          AND NOT EXISTS (
              SELECT 1 FROM edges e WHERE e.from_node_id = n.id
                AND e.edge_type = 'MENTIONS' AND e.source = 'news_extraction'
                AND e.valid_to IS NULL)
    """,
    "event_tags": """
        SELECT count(*) FROM nodes n
        WHERE node_type = 'Event' AND valid_to IS NULL AND valid_from >= CURRENT_DATE
          AND NOT (properties ? 'resource_tagged')
          AND NOT EXISTS (SELECT 1 FROM node_resources nr WHERE nr.node_id = n.id)
    """,
    "actor_tags": """
        SELECT count(*) FROM nodes n
        WHERE source IN ('news_extraction', 'event_venue') AND valid_to IS NULL
          AND NOT (properties ? 'resource_tagged')
          AND NOT EXISTS (SELECT 1 FROM node_resources nr WHERE nr.node_id = n.id)
    """,
    "problem_extraction": """
        SELECT count(*) FROM nodes n
        WHERE node_type = 'Event' AND valid_to IS NULL
          AND coalesce(properties->>'event_type', '') = 'news'
          AND source = ANY(ARRAY['nordstadtblogger', 'wirindortmund'])
          AND valid_from >= CURRENT_DATE - interval '24 months'
          AND NOT (properties ? 'problems_extracted')
    """,
}


async def _pending(job: str) -> int:
    async with get_conn() as conn:
        return await conn.fetchval(_PENDING[job])


async def _drain(job: str, run_batch) -> None:
    last: int | None = None
    while True:
        pending = await _pending(job)
        if pending == 0:
            logger.info("%s: drained.", job)
            return
        if last is not None and pending >= last:
            logger.warning(
                "%s: stalled at %d pending (no progress last batch) — leaving "
                "the rest to the regular sweeps.", job, pending,
            )
            return
        logger.info("%s: %d pending, running batch of %d …", job, pending, BATCH)
        t0 = time.monotonic()
        result = await run_batch()
        logger.info("%s: batch done in %.0fs (%s)", job, time.monotonic() - t0, result)
        last = pending


async def main() -> None:
    try:
        await _drain("actor_extraction", lambda: extract_news_actors(limit=BATCH))
        await _drain("event_tags", lambda: enrich_events(limit=BATCH))
        await _drain("actor_tags", lambda: enrich_actors(limit=BATCH))
        await _drain("problem_extraction", lambda: extract_problems(limit=BATCH))
    finally:
        await close_pool()
    logger.info("backfill complete.")


if __name__ == "__main__":
    asyncio.run(main())
