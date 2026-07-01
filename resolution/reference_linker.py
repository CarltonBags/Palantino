"""
Reference linker — deterministic cross-source joins on shared register keys
(not fuzzy). Complements the fuzzy EntityResolver and the text_linker.

Pass 1: planned road measures ↔ the council decision that authorised them, via
the Drucksachen-Nr (Tiefbau ConstructionSite.drucksachennummer ==
Resolution.resolution_number). Emits RELATES_TO (relation=authorized_by). This is
the inefficiency backbone: it puts a planned repaving next to the resolution behind
it, so "works vs decision timing" becomes queryable.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

logger = logging.getLogger(__name__)

_DS_MATCH_SQL = """
SELECT cs.id AS cs_id, r.id AS res_id,
       cs.properties->>'drucksachennummer' AS dsnr
FROM nodes cs
JOIN nodes r ON r.node_type = 'Resolution' AND r.valid_to IS NULL
    AND r.properties->>'resolution_number' = cs.properties->>'drucksachennummer'
WHERE cs.source = 'opendata_dortmund_tiefbau_programm' AND cs.valid_to IS NULL
  AND cs.properties->>'drucksachennummer' ~ '[0-9]{3,}-[0-9]{2}'
LIMIT $1
"""


async def link_drucksachen(limit: int = 20000) -> dict[str, int]:
    """Link planned works to their authorising council decision by Drucksachen-Nr."""
    from db.session import get_conn
    from ingestion.writer import upsert_edge
    from ontology.edges import relates_to

    counts = {"matches": 0, "edges": 0}
    async with get_conn() as conn:
        rows = await conn.fetch(_DS_MATCH_SQL, limit)
        for row in rows:
            counts["matches"] += 1
            edge = relates_to(
                from_id=UUID(str(row["cs_id"])),
                to_id=UUID(str(row["res_id"])),
                source="reference_linker",
                source_id=f"{row['cs_id']}->{row['res_id']}:authorized_by",
                inferred=True,
                confidence=1.0,  # exact register-key match
                reasoning_trace=(
                    f"Drucksachen-Nr {row['dsnr']} = Resolution.resolution_number"
                ),
                observed_at=datetime.now(timezone.utc),
                properties={"relation": "authorized_by", "drucksachennummer": row["dsnr"]},
            )
            _, was_new = await upsert_edge(edge)
            if was_new:
                counts["edges"] += 1
    logger.info("reference linker (drucksachen): %d matches, %d new edges",
                counts["matches"], counts["edges"])
    return counts


_WORK_DISTRICT_SQL = """
SELECT cs.id AS cs_id, g.id AS geo_id
FROM nodes cs
JOIN nodes g ON g.node_type = 'GeoArea' AND g.properties->>'area_type' = 'stadtbezirk'
    AND g.valid_to IS NULL
    AND lower(g.label) = lower(cs.properties->>'stadtbezirk')
WHERE cs.source = 'opendata_dortmund_tiefbau_programm' AND cs.valid_to IS NULL
  AND cs.properties->>'stadtbezirk' IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM edges e WHERE e.edge_type = 'LOCATED_IN' AND e.valid_to IS NULL
        AND e.from_node_id = cs.id AND e.to_node_id = g.id)
LIMIT $1
"""


async def link_works_to_districts(limit: int = 20000) -> dict[str, int]:
    """Snap planned works (no geometry) to their Stadtbezirk GeoArea by name."""
    from db.session import get_conn
    from ingestion.writer import upsert_edge
    from ontology.edges import located_in

    counts = {"matches": 0, "edges": 0}
    async with get_conn() as conn:
        rows = await conn.fetch(_WORK_DISTRICT_SQL, limit)
        for row in rows:
            counts["matches"] += 1
            edge = located_in(
                from_node_id=UUID(str(row["cs_id"])),
                to_node_id=UUID(str(row["geo_id"])),
                source="reference_linker",
                observed_at=datetime.now(timezone.utc),
            )
            _, was_new = await upsert_edge(edge)
            if was_new:
                counts["edges"] += 1
    logger.info("reference linker (works→district): %d matches, %d new edges",
                counts["matches"], counts["edges"])
    return counts
