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
                        node_a_id=UUID(row["a_id"]),
                        node_b_id=UUID(row["b_id"]),
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
                        node_a_id=UUID(row["a_id"]),
                        node_b_id=UUID(row["b_id"]),
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
