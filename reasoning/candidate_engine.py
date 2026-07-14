"""
All-pairs synergy candidate engine.

"Match everyone with everyone" — but only the cheap part. Four signals are
computed across the full actor set in-database (no LLM):

  sem_sim    — pgvector KNN over the actor embeddings (k nearest per actor)
  comp_score — need↔offer tag matches, IDF-weighted so ubiquitous tags
               (parkraum, publikum) count little and rare fits count a lot;
               tags whose need×offer cross product explodes are skipped
  dist_m     — physically close (≤150 m), cross-type, not yet connected
  shared_n   — ≥2 shared specific neighbours (same articles/venues/tenders),
               hubs excluded (GeoArea, degree > 60)

Scores land in synergy_candidates; the LLM (deep finder / scanner) validates
from the top of the `new` frontier, and verdicts flip status via
synergy_feedback — so coverage of the pair space accumulates run over run.

Actor set: extracted actors (news/venues), registered companies, resource-tagged
named POIs, upcoming non-news events (one representative per series label).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import asyncpg

from db.session import get_conn

logger = logging.getLogger(__name__)

KNN_PER_ACTOR = 12
SEM_FLOOR = 0.40
PROX_M = 150.0
# a tag whose need-side × offer-side cross product exceeds this is too generic
# to signal anything (publikum, parkraum) — skip it entirely
MAX_TAG_PAIRS = 300_000

# NB: no embedding column — copying the vectors once cost 210 MB and tipped
# the database over its disk quota. The semantic pass joins node_embeddings.
_FILL_ACTOR_SET = """
    INSERT INTO synergy_actor_set (node_id, node_type, label, geom)
    SELECT o.id, o.node_type, o.label, o.geom
    FROM nodes o
    WHERE o.valid_to IS NULL AND (
        (o.node_type = 'Organization'
         AND o.source IN ('news_extraction', 'event_venue', 'vergabe_nrw_metropole_ruhr'))
        OR (o.node_type = 'Organization' AND o.source = 'offeneregister'
            AND coalesce(o.properties->>'status', '') = 'currently registered')
        OR (o.node_type = 'POI' AND o.label NOT LIKE 'OSM %'
            AND EXISTS (SELECT 1 FROM node_resources r WHERE r.node_id = o.id))
        -- journalists are reach/connector actors: no geom/tags, but their
        -- WROTE/MENTIONS neighbourhood ties them to the beats they cover
        OR o.node_type = 'Journalist'
    )
    UNION ALL
    SELECT ev.id, ev.node_type, ev.label, ev.geom
    FROM (
        SELECT DISTINCT ON (n.label) n.id, n.node_type, n.label, n.geom
        FROM nodes n
        WHERE n.node_type = 'Event' AND n.valid_to IS NULL
          AND coalesce(n.properties->>'event_type', '') <> 'news'
          AND n.valid_from >= CURRENT_DATE
        ORDER BY n.label, n.valid_from ASC
    ) ev
"""

_PAIR_COLS = """
    least(a_id::text, b_id::text) || '|' || greatest(a_id::text, b_id::text),
    least(a_id::text, b_id::text)::uuid,
    greatest(a_id::text, b_id::text)::uuid
"""

# chunked by actor id ($3): one statement over the full set runs for ~10+
# minutes and the connection pooler resets it — short statements survive
_SEM_PAIRS_BATCH = f"""
    INSERT INTO synergy_candidates (pair_key, node_a, node_b, sem_sim)
    SELECT {_PAIR_COLS}, max(sim)
    FROM (
        SELECT a.node_id AS a_id, b.node_id AS b_id, b.sim
        FROM synergy_actor_set a
        JOIN node_embeddings ea ON ea.node_id = a.node_id
        CROSS JOIN LATERAL (
            SELECT e.node_id, 1 - (e.embedding <=> ea.embedding) AS sim
            FROM node_embeddings e
            JOIN synergy_actor_set s ON s.node_id = e.node_id
            WHERE e.node_id <> a.node_id
            ORDER BY e.embedding <=> ea.embedding
            LIMIT $1
        ) b
        WHERE a.node_id = ANY($3::uuid[]) AND b.sim >= $2
    ) pairs
    GROUP BY 1, 2, 3
    ON CONFLICT (pair_key) DO UPDATE SET sem_sim = EXCLUDED.sem_sim
"""
SEM_BATCH = 300

_COMP_PAIRS = f"""
    WITH freq AS (
        SELECT r.tag, r.kind, count(*) AS n
        FROM node_resources r
        JOIN synergy_actor_set s ON s.node_id = r.node_id
        GROUP BY 1, 2
    ),
    usable AS (
        SELECT fn.tag,
               ln((SELECT count(*) FROM synergy_actor_set)::float
                  / greatest(fn.n + fo.n, 2)) AS w
        FROM freq fn
        JOIN freq fo ON fo.tag = fn.tag AND fo.kind = 'offer'
        WHERE fn.kind = 'need' AND fn.n::bigint * fo.n <= $1
    )
    INSERT INTO synergy_candidates (pair_key, node_a, node_b, comp_score)
    SELECT {_PAIR_COLS}, sum(w)
    FROM (
        SELECT DISTINCT an.node_id AS a_id, ao.node_id AS b_id, u.tag, u.w
        FROM node_resources rn
        JOIN synergy_actor_set an ON an.node_id = rn.node_id AND rn.kind = 'need'
        JOIN usable u ON u.tag = rn.tag
        JOIN node_resources ro ON ro.tag = rn.tag AND ro.kind = 'offer'
        JOIN synergy_actor_set ao ON ao.node_id = ro.node_id
            AND ao.node_id <> an.node_id
    ) pairs
    GROUP BY 1, 2, 3
    ON CONFLICT (pair_key) DO UPDATE SET comp_score = EXCLUDED.comp_score
"""

_PROX_PAIRS = f"""
    INSERT INTO synergy_candidates (pair_key, node_a, node_b, dist_m)
    SELECT {_PAIR_COLS}, min(d)
    FROM (
        SELECT a.node_id AS a_id, b.node_id AS b_id,
               ST_Distance(a.geom::geography, b.geom::geography) AS d
        FROM synergy_actor_set a
        JOIN synergy_actor_set b
          ON a.node_id < b.node_id
         AND a.node_type <> b.node_type
         AND ST_DWithin(a.geom, b.geom, 0.0025)
         AND ST_DWithin(a.geom::geography, b.geom::geography, $1)
        WHERE NOT EXISTS (
            SELECT 1 FROM edges g WHERE g.valid_to IS NULL
              AND ((g.from_node_id = a.node_id AND g.to_node_id = b.node_id)
                OR (g.from_node_id = b.node_id AND g.to_node_id = a.node_id)))
    ) pairs
    GROUP BY 1, 2, 3
    ON CONFLICT (pair_key) DO UPDATE SET dist_m = EXCLUDED.dist_m
"""

_LINK_PAIRS = f"""
    WITH hubs AS (
        SELECT id FROM nodes WHERE node_type = 'GeoArea'
        UNION
        SELECT nid FROM (
            SELECT from_node_id AS nid FROM edges WHERE valid_to IS NULL
            UNION ALL
            SELECT to_node_id FROM edges WHERE valid_to IS NULL
        ) d GROUP BY nid HAVING count(*) > 60
    ),
    nb AS (  -- actor ↔ specific neighbour, direction-agnostic
        SELECT s.node_id AS actor, e.to_node_id AS neighbour
        FROM edges e JOIN synergy_actor_set s ON s.node_id = e.from_node_id
        WHERE e.valid_to IS NULL AND e.to_node_id NOT IN (SELECT id FROM hubs)
        UNION
        SELECT s.node_id, e.from_node_id
        FROM edges e JOIN synergy_actor_set s ON s.node_id = e.to_node_id
        WHERE e.valid_to IS NULL AND e.from_node_id NOT IN (SELECT id FROM hubs)
    )
    INSERT INTO synergy_candidates (pair_key, node_a, node_b, shared_n)
    SELECT {_PAIR_COLS}, max(n)
    FROM (
        SELECT x.actor AS a_id, y.actor AS b_id, count(DISTINCT x.neighbour) AS n
        FROM nb x JOIN nb y ON y.neighbour = x.neighbour AND x.actor < y.actor
        WHERE NOT EXISTS (
            SELECT 1 FROM edges g WHERE g.valid_to IS NULL
              AND ((g.from_node_id = x.actor AND g.to_node_id = y.actor)
                OR (g.from_node_id = y.actor AND g.to_node_id = x.actor)))
        GROUP BY 1, 2
        HAVING count(DISTINCT x.neighbour) >= 2
    ) pairs
    GROUP BY 1, 2, 3
    ON CONFLICT (pair_key) DO UPDATE SET shared_n = EXCLUDED.shared_n
"""

# suppress non-pairs: same entity (SAME_AS / near-identical label / same venue)
# and pairs already negatively judged; mark confirmed/validated ones
_APPLY_STATUS = [
    """
    UPDATE synergy_candidates sc SET status = 'suppressed'
    FROM nodes na, nodes nb
    WHERE na.id = sc.node_a AND nb.id = sc.node_b AND sc.status <> 'suppressed'
      AND (lower(na.label) = lower(nb.label)
           OR position(lower(na.label) IN lower(nb.label)) > 0
           OR position(lower(nb.label) IN lower(na.label)) > 0
           OR similarity(lower(na.label), lower(nb.label)) >= 0.6
           OR (coalesce(na.properties->>'venue', '') <> ''
               AND lower(na.properties->>'venue')
                   = lower(coalesce(nb.properties->>'venue', ''))))
    """,
    """
    UPDATE synergy_candidates sc SET status = 'suppressed'
    FROM edges e
    WHERE e.edge_type = 'SAME_AS' AND e.valid_to IS NULL AND sc.status <> 'suppressed'
      AND ((e.from_node_id = sc.node_a AND e.to_node_id = sc.node_b)
        OR (e.from_node_id = sc.node_b AND e.to_node_id = sc.node_a))
    """,
    """
    UPDATE synergy_candidates sc
    SET status = CASE WHEN f.verdict IN ('reject', 'dismissed')
                      THEN 'suppressed' ELSE 'validated' END
    FROM synergy_feedback f WHERE f.pair_key = sc.pair_key
    """,
]

_RESCORE = """
    UPDATE synergy_candidates SET
        score = 0.45 * coalesce(sem_sim, 0)
              + 0.30 * least(coalesce(comp_score, 0) / 6.0, 1.0)
              + 0.15 * greatest(0.0, 1.0 - coalesce(dist_m, 1e9) / $1)
              + 0.25 * least(coalesce(shared_n, 0) / 4.0, 1.0),
        computed_at = now()
"""

# drop pairs whose actors left the current actor set (past events, dissolved
# companies) unless a verdict already exists on them
_PRUNE = """
    DELETE FROM synergy_candidates sc
    WHERE sc.status = 'new'
      AND (NOT EXISTS (SELECT 1 FROM synergy_actor_set s WHERE s.node_id = sc.node_a)
           OR NOT EXISTS (SELECT 1 FROM synergy_actor_set s WHERE s.node_id = sc.node_b))
"""


_RETRYABLE = (
    asyncpg.exceptions.ConnectionDoesNotExistError,
    asyncpg.exceptions.InterfaceError,
    ConnectionResetError,
    OSError,
    TimeoutError,  # client-side statement timeout (half-open pooler connection)
)


async def _run(sql: str, *params: Any, ef_search: int | None = None, attempts: int = 3) -> str:
    """One statement, own pooled connection, own transaction, retried on
    connection loss. A refresh spans hours across dozens of statements — held
    on a single pooled connection, one reset kills the whole run; acquired
    per statement, a reset costs one retry."""
    async def _once() -> str:
        async with get_conn() as conn:
            async with conn.transaction():
                await conn.execute("SET LOCAL statement_timeout = '1800s'")
                if ef_search is not None:
                    await conn.execute(f"SET LOCAL hnsw.ef_search = {ef_search}")
                    # the KNN filters node_embeddings down to the actor set
                    # (~18% selectivity) — iterative scan keeps probing until
                    # the LIMIT is satisfied instead of starving on the filter
                    await conn.execute("SET LOCAL hnsw.iterative_scan = 'relaxed_order'")
                return await conn.execute(sql, *params, timeout=600)

    for attempt in range(attempts):
        try:
            # outer watchdog: sockets that die while the machine sleeps have
            # left awaits hanging PAST every per-command timeout — wait_for is
            # the belt-and-braces that always fires on wall clock after wake
            return await asyncio.wait_for(_once(), timeout=1200)
        except (*_RETRYABLE, asyncio.TimeoutError) as exc:
            if attempt == attempts - 1:
                raise
            logger.warning("statement lost its connection (%s) — retrying", type(exc).__name__)
            await asyncio.sleep(5 * (attempt + 1))
    raise RuntimeError("unreachable")


async def _fetchval(sql: str) -> Any:
    async with get_conn() as conn:
        return await conn.fetchval(sql)


async def refresh_candidates() -> dict[str, Any]:
    """Rebuild the actor set and recompute all four signals across it."""
    counts: dict[str, Any] = {}
    t0 = time.monotonic()
    # TRUNCATE, not DELETE: after repeated wipe-and-refill cycles the row
    # deletes crawl on dead-tuple bloat (a 17k-row DELETE exceeded a 20-min
    # watchdog), and an abandoned server-side DELETE then blocks its own
    # retry. TRUNCATE is instant; its ACCESS EXCLUSIVE lock is safe now that
    # zombie transactions are prevented by the timeout stack.
    await _run("TRUNCATE synergy_actor_set")
    await _run(_FILL_ACTOR_SET)
    await _run("ANALYZE synergy_actor_set")
    counts["actors"] = await _fetchval("SELECT count(*) FROM synergy_actor_set")

    async with get_conn() as conn:
        actor_ids = [
            str(r["node_id"]) for r in await conn.fetch(
                "SELECT s.node_id FROM synergy_actor_set s "
                "JOIN node_embeddings e ON e.node_id = s.node_id ORDER BY s.node_id"
            )
        ]
    counts["sem"] = 0
    for i in range(0, len(actor_ids), SEM_BATCH):
        chunk = actor_ids[i : i + SEM_BATCH]
        res = await _run(_SEM_PAIRS_BATCH, KNN_PER_ACTOR, SEM_FLOOR, chunk, ef_search=80)
        counts["sem"] += int(res.split()[-1])
        # log EVERY chunk: silence in the log must mean "stuck", not "between
        # log intervals" — that ambiguity hid three zombie runs
        logger.info(
            "sem pass: %d/%d actors, %d pairs so far",
            min(i + SEM_BATCH, len(actor_ids)), len(actor_ids), counts["sem"],
        )
    counts["comp"] = int((await _run(_COMP_PAIRS, MAX_TAG_PAIRS)).split()[-1])
    counts["prox"] = int((await _run(_PROX_PAIRS, PROX_M)).split()[-1])
    counts["link"] = int((await _run(_LINK_PAIRS)).split()[-1])

    for stmt in _APPLY_STATUS:
        await _run(stmt)
    await _run(_PRUNE)
    await _run(_RESCORE, PROX_M)
    counts["frontier"] = await _fetchval(
        "SELECT count(*) FROM synergy_candidates WHERE status = 'new'"
    )
    counts["seconds"] = round(time.monotonic() - t0, 1)
    logger.info("candidate refresh: %s", counts)
    return counts


async def frontier_pairs(
    limit: int = 40, anchor_ids: list[str] | None = None
) -> list[tuple[dict, dict, str]]:
    """Top unvalidated pairs, ready for the deep finder: (node_a, node_b, note).
    With anchor_ids, only pairs touching one of those nodes (chat Tiefensuche);
    without, the global frontier (scanner)."""
    where = "sc.status = 'new'"
    params: list[Any] = [limit]
    if anchor_ids:
        params.append(anchor_ids)
        where += " AND (sc.node_a = ANY($2::uuid[]) OR sc.node_b = ANY($2::uuid[]))"
    async with get_conn() as conn:
        rows = await conn.fetch(
            f"""
            SELECT sc.pair_key, sc.sem_sim, sc.comp_score, sc.dist_m, sc.shared_n, sc.score,
                   a.id AS a_id, a.node_type AS a_type, a.label AS a_label,
                   a.properties AS a_props, a.source AS a_source, a.source_url AS a_url,
                   b.id AS b_id, b.node_type AS b_type, b.label AS b_label,
                   b.properties AS b_props, b.source AS b_source, b.source_url AS b_url
            FROM synergy_candidates sc
            JOIN nodes a ON a.id = sc.node_a AND a.valid_to IS NULL
            JOIN nodes b ON b.id = sc.node_b AND b.valid_to IS NULL
            WHERE {where}
            ORDER BY sc.score DESC
            LIMIT $1
            """,
            *params,
        )
    pairs: list[tuple[dict, dict, str]] = []
    for r in rows:
        signals = []
        if r["sem_sim"]:
            signals.append(f"thematisch verwandt ({r['sem_sim']:.2f})")
        if r["comp_score"]:
            signals.append("Bedarf trifft Angebot")
        if r["dist_m"] is not None:
            signals.append(f"{int(r['dist_m'])} m entfernt")
        if r["shared_n"]:
            signals.append(f"{r['shared_n']} gemeinsame Nachbarn im Graphen")
        note = (
            f"„{r['a_label']}“ und „{r['b_label']}“ — bislang unverbunden; "
            f"Signale: {', '.join(signals)}."
        )
        a = {"id": r["a_id"], "node_type": r["a_type"], "label": r["a_label"],
             "properties": r["a_props"], "source": r["a_source"], "source_url": r["a_url"]}
        b = {"id": r["b_id"], "node_type": r["b_type"], "label": r["b_label"],
             "properties": r["b_props"], "source": r["b_source"], "source_url": r["b_url"]}
        pairs.append((a, b, note))
    return pairs
