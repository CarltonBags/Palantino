"""
Entity resolution — cross-source deduplication.

Strategy (from CLAUDE.md):
  1. Deterministic keys where they exist (geo proximity, register IDs).
  2. Fuzzy name matching (pg_trgm) with confidence scores.
  3. High-confidence (≥0.95) → auto-merge via SAME_AS edge (inferred=True).
  4. Low-confidence (0.7–0.95) → write to resolution_candidates for human review.
  5. Below 0.7 → discard.

Test with precision/recall on a labeled sample in tests/resolution/.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from db.session import get_conn
from ingestion.writer import upsert_edge
from ontology.edges import same_as

logger = logging.getLogger(__name__)

AUTO_MERGE_THRESHOLD = 0.95
CANDIDATE_THRESHOLD = 0.70

# Rechtsform suffixes stripped before name comparison, so the legal name
# ("Rewe Markt GmbH") matches the trade/news name ("Rewe").
_LEGAL_FORM_RE = (
    r"\s+(gGmbH|GmbH & Co\. KG|GmbH|UG.*|Aktiengesellschaft|AG"
    r"|mbH|e\.?\s?V\.?|KGaA|KG|OHG|GbR|SE|Stiftung|Verein).*$"
)

def norm_name_sql(expr: str) -> str:
    """SQL for the normalised comparison name of `expr`: legal form stripped,
    lower-cased, whitespace collapsed, edge punctuation trimmed."""
    return (
        "lower(trim(both ' .,-–„“\"' FROM regexp_replace("
        "regexp_replace({expr}, '{legal}', '', 'i'), '\\s+', ' ', 'g')))"
    ).format(expr=expr, legal=_LEGAL_FORM_RE.replace("'", "''"))


_NORM_LABEL_SQL = norm_name_sql("label")


@dataclass
class ResolutionCandidate:
    node_a_id: UUID
    node_b_id: UUID
    method: str
    confidence: float


class EntityResolver:
    """
    Run resolution passes against the current graph state in Postgres.
    Each pass emits SAME_AS edges (auto) or resolution_candidates (manual review).
    """

    async def run_all(self) -> None:
        await self.resolve_by_geo_proximity()
        await self.resolve_by_name_fuzzy()
        await self.resolve_company_to_storefront()
        await self.resolve_actors()

    async def resolve_actors(
        self, fuzzy_threshold: float = 0.75, limit: int = 3000,
    ) -> dict[str, int]:
        """
        Link LLM-extracted actors (source news_extraction / event_venue) to their
        canonical counterparts: Handelsregister companies, named OSM POIs, and
        each other (near-duplicate extractions the exact-key dedupe missed).
        Comparison is on the legal-form-stripped, normalised name.

        Confidence policy:
          - exact normalised name, Organization↔Organization → 0.96 (auto-merge);
          - any POI target is capped below auto-merge (chains: one name, many
            storefronts) → review queue;
          - fuzzy (pg_trgm on normalised names) → similarity as confidence,
            review queue via the standard thresholds.
        """
        counts = {"exact": 0, "fuzzy": 0}
        actors_cte = f"""
            actors AS (
                SELECT id, source, {_NORM_LABEL_SQL} AS nname
                FROM nodes
                WHERE node_type = 'Organization' AND valid_to IS NULL
                  AND source IN ('news_extraction', 'event_venue',
                                 'vergabe_nrw_metropole_ruhr')
            ),
            targets AS (
                SELECT id, source, node_type, {_NORM_LABEL_SQL} AS nname
                FROM nodes
                WHERE valid_to IS NULL AND (
                    (node_type = 'Organization'
                     AND source IN ('offeneregister', 'news_extraction', 'event_venue'))
                    OR (node_type = 'POI' AND label NOT LIKE 'OSM %'))
            )
        """
        not_seen = """
            NOT EXISTS (
                SELECT 1 FROM resolution_candidates rc
                WHERE (rc.node_a_id = a.id AND rc.node_b_id = t.id)
                   OR (rc.node_a_id = t.id AND rc.node_b_id = a.id))
            AND NOT EXISTS (
                SELECT 1 FROM edges e
                WHERE e.edge_type = 'SAME_AS' AND e.valid_to IS NULL
                  AND ((e.from_node_id = a.id AND e.to_node_id = t.id)
                    OR (e.from_node_id = t.id AND e.to_node_id = a.id)))
        """
        # Actor↔actor pairs appear on both sides — a.id < t.id dedupes them;
        # register/POI targets only ever appear on the target side.
        no_mirror = """
            (t.node_type = 'POI' OR t.source = 'offeneregister' OR a.id < t.id)
        """
        async with get_conn() as conn:
            exact = await conn.fetch(
                f"""
                WITH {actors_cte}
                SELECT a.id AS a_id, t.id AS b_id, t.node_type AS t_type
                FROM actors a
                JOIN targets t ON t.nname = a.nname AND t.id <> a.id
                WHERE length(a.nname) >= 4 AND {no_mirror} AND {not_seen}
                LIMIT $1
                """,
                limit,
            )
            fuzzy = await conn.fetch(
                f"""
                WITH {actors_cte}
                SELECT a.id AS a_id, t.id AS b_id, t.node_type AS t_type,
                       similarity(a.nname, t.nname) AS sim
                FROM actors a
                JOIN targets t ON t.id <> a.id
                    AND left(t.nname, 1) = left(a.nname, 1)
                    AND t.nname <> a.nname
                    AND similarity(a.nname, t.nname) >= $1
                WHERE length(a.nname) >= 5 AND {no_mirror} AND {not_seen}
                ORDER BY sim DESC
                LIMIT $2
                """,
                fuzzy_threshold, limit,
            )
        for row in exact:
            confidence = 0.96
            if row["t_type"] == "POI":
                confidence = AUTO_MERGE_THRESHOLD - 0.01
            counts["exact"] += 1
            await self._handle_candidate(
                ResolutionCandidate(
                    node_a_id=UUID(str(row["a_id"])),
                    node_b_id=UUID(str(row["b_id"])),
                    method="actor_name_exact",
                    confidence=round(confidence, 3),
                )
            )
        for row in fuzzy:
            confidence = float(row["sim"])
            if row["t_type"] == "POI":
                confidence = min(confidence, AUTO_MERGE_THRESHOLD - 0.01)
            counts["fuzzy"] += 1
            await self._handle_candidate(
                ResolutionCandidate(
                    node_a_id=UUID(str(row["a_id"])),
                    node_b_id=UUID(str(row["b_id"])),
                    method="actor_name_fuzzy",
                    confidence=round(confidence, 3),
                )
            )
        logger.info("actor resolution: %s", counts)
        return counts

    async def resolve_company_to_storefront(
        self, name_threshold: float = 0.4, limit: int = 4000,
    ) -> None:
        """
        Link a Handelsregister company (Organization) to its physical storefront
        (OSM POI) — different node types, so the same-type fuzzy pass misses them.
        Block by shared postcode; strip the Rechtsform suffix so the legal name
        ("Rewe Markt GmbH") matches the trade name ("Rewe"); confidence from name
        similarity, boosted when the street also matches. This is the cross-source
        link that ties a legal entity to its shop.
        """
        async with get_conn() as conn:
            rows = await conn.fetch(
                """
                WITH comp AS (
                    SELECT id, properties->>'addr_postcode' AS plz,
                           properties->>'addr_street' AS street,
                           trim(regexp_replace(
                               label,
                               '\\s+(gGmbH|GmbH & Co\\. KG|GmbH|UG.*|Aktiengesellschaft|AG'
                               '|mbH|e\\.?\\s?V\\.?|KGaA|KG|OHG|GbR|SE|Stiftung|Verein).*$',
                               '', 'i')) AS cname
                    FROM nodes
                    WHERE node_type = 'Organization' AND source = 'offeneregister'
                      AND valid_to IS NULL AND properties->>'addr_postcode' IS NOT NULL
                )
                SELECT c.id AS a_id, p.id AS b_id,
                       similarity(c.cname, p.label) AS name_sim,
                       similarity(lower(coalesce(c.street, '')),
                                  lower(coalesce(p.properties->>'addr_street', ''))) AS street_sim
                FROM comp c
                JOIN nodes p ON p.node_type = 'POI' AND p.valid_to IS NULL
                    AND p.properties->>'addr_postcode' = c.plz
                    AND length(c.cname) >= 3
                    AND similarity(c.cname, p.label) >= $1
                WHERE NOT EXISTS (
                    SELECT 1 FROM resolution_candidates rc
                    WHERE rc.node_a_id = c.id AND rc.node_b_id = p.id
                )
                ORDER BY name_sim DESC
                LIMIT $2
                """,
                name_threshold, limit,
            )
            for row in rows:
                name_sim = float(row["name_sim"])
                street_sim = float(row["street_sim"] or 0.0)
                confidence = name_sim + 0.15 if street_sim >= 0.6 else name_sim
                # Cap below AUTO_MERGE: a company can be a chain (one legal entity,
                # many stores), so a company↔POI SAME_AS is not safe to auto-merge —
                # always route to the human review queue.
                confidence = min(confidence, AUTO_MERGE_THRESHOLD - 0.01)
                await self._handle_candidate(
                    ResolutionCandidate(
                        node_a_id=UUID(str(row["a_id"])),
                        node_b_id=UUID(str(row["b_id"])),
                        method="company_storefront",
                        confidence=round(confidence, 3),
                    )
                )

    async def resolve_by_geo_proximity(
        self,
        distance_m: float = 50.0,
    ) -> None:
        """
        Two nodes of different sources within distance_m metres with the same
        label (case-insensitive) are likely the same entity.
        """
        async with get_conn() as conn:
            rows = await conn.fetch(
                """
                SELECT a.id AS a_id, b.id AS b_id,
                       ST_Distance(a.geom::geography, b.geom::geography) AS dist_m
                FROM nodes a
                JOIN nodes b ON a.id < b.id
                    AND a.source != b.source
                    AND a.node_type = b.node_type
                    AND lower(a.label) = lower(b.label)
                    AND a.geom IS NOT NULL AND b.geom IS NOT NULL
                    AND ST_DWithin(a.geom::geography, b.geom::geography, $1)
                    AND a.valid_to IS NULL AND b.valid_to IS NULL
                WHERE NOT EXISTS (
                    SELECT 1 FROM resolution_candidates rc
                    WHERE rc.node_a_id = a.id AND rc.node_b_id = b.id
                )
                """,
                distance_m,
            )
            for row in rows:
                dist = row["dist_m"]
                confidence = max(0.0, 1.0 - dist / distance_m) * 0.9 + 0.1
                await self._handle_candidate(
                    ResolutionCandidate(
                        node_a_id=UUID(str(row["a_id"])),
                        node_b_id=UUID(str(row["b_id"])),
                        method="geo_name",
                        confidence=round(confidence, 3),
                    )
                )

    async def resolve_by_name_fuzzy(
        self,
        node_type: str = "Organization",
        similarity_threshold: float = 0.80,
    ) -> None:
        """
        Fuzzy name match across sources using pg_trgm similarity.
        Only runs on Organization nodes (where register_id is missing).
        """
        async with get_conn() as conn:
            rows = await conn.fetch(
                """
                SELECT a.id AS a_id, b.id AS b_id,
                       similarity(a.label, b.label) AS sim
                FROM nodes a
                JOIN nodes b ON a.id < b.id
                    AND a.source != b.source
                    AND a.node_type = $1
                    AND a.node_type = b.node_type
                    AND similarity(a.label, b.label) >= $2
                    AND a.valid_to IS NULL AND b.valid_to IS NULL
                WHERE NOT EXISTS (
                    SELECT 1 FROM resolution_candidates rc
                    WHERE rc.node_a_id = a.id AND rc.node_b_id = b.id
                )
                ORDER BY sim DESC
                LIMIT 1000
                """,
                node_type,
                similarity_threshold,
            )
            for row in rows:
                await self._handle_candidate(
                    ResolutionCandidate(
                        node_a_id=UUID(str(row["a_id"])),
                        node_b_id=UUID(str(row["b_id"])),
                        method="name_fuzzy_trgm",
                        confidence=round(float(row["sim"]), 3),
                    )
                )

    async def _handle_candidate(self, candidate: ResolutionCandidate) -> None:
        if candidate.confidence >= AUTO_MERGE_THRESHOLD:
            edge = same_as(
                node_a_id=candidate.node_a_id,
                node_b_id=candidate.node_b_id,
                method=candidate.method,
                source="resolution",
                confidence=candidate.confidence,
                reasoning_trace=f"auto-merged via {candidate.method} @ {candidate.confidence:.3f}",
            )
            await upsert_edge(edge)
            logger.info(
                "Auto-merged %s ↔ %s via %s (%.3f)",
                candidate.node_a_id, candidate.node_b_id,
                candidate.method, candidate.confidence,
            )
        elif candidate.confidence >= CANDIDATE_THRESHOLD:
            await self._write_candidate(candidate)

    async def _write_candidate(self, candidate: ResolutionCandidate) -> None:
        async with get_conn() as conn:
            await conn.execute(
                """
                INSERT INTO resolution_candidates
                    (node_a_id, node_b_id, method, confidence)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT DO NOTHING
                """,
                str(candidate.node_a_id),
                str(candidate.node_b_id),
                candidate.method,
                candidate.confidence,
            )
