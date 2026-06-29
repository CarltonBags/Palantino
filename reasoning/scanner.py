"""
Insight scanner — the layer that *finds* candidate subgraphs to reason over,
instead of waiting for a human to hand-pick node IDs.

Two candidate generators (both work with current data):

  spatial_temporal — anchor on a ConstructionSite; gather geo-located nodes
      within `radius_m` metres whose validity window overlaps the anchor's
      (± `window_days`). Surfaces "X happening at the same place and time as Y"
      (overlapping roadworks, construction during an event, …).

  ego_network — anchor on high-degree nodes; gather their immediate neighbours
      via edges. Surfaces clusters the graph itself already links.

Each candidate's subgraph (nodes + edges) is sent to Claude with the existing
inefficiency / synergy prompts. Results are persisted to the `insights` table as
inferred findings (rule 3: separate from source facts, with confidence + trace).
A deterministic candidate_key dedupes across re-scans.

DB- and Claude-dependent; the pure helpers (candidate_key, parse_insights) are
unit-tested without either.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from config import settings
from reasoning.prompts import (
    INEFFICIENCY_PROMPT,
    SYNERGY_PROMPT,
    SYSTEM_PROMPT,
    format_subgraph,
)

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"
CONFIDENCE_FLOOR = 0.7


def _get_conn() -> Any:
    # Lazy import so the pure helpers (and their tests) don't require psycopg.
    from db.session import get_conn

    return get_conn()


@dataclass
class Candidate:
    generator: str
    node_ids: list[str]
    anchor_id: str
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)


# ── pure helpers (unit-tested) ──────────────────────────────────────────────────

def candidate_key(insight_type: str, node_ids: list[str]) -> str:
    """Stable dedup key: insight type + the set of subgraph node IDs."""
    payload = insight_type + "|" + "|".join(sorted(node_ids))
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def parse_insights(raw_text: str) -> list[dict[str, Any]]:
    """
    Parse the model's JSON response into a list of insight dicts. Tolerates a
    bare list, an {"insights": [...]} object, or text with surrounding noise.
    Returns [] on anything unparseable.
    """
    text = raw_text.strip()
    # Strip ```json fences if present.
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return []
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return []
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        items = data.get("insights", [])
        return [d for d in items if isinstance(d, dict)]
    return []


def derive_title(insight: dict[str, Any]) -> str:
    """Use an explicit title if the model gave one, else the first sentence."""
    if insight.get("title"):
        return str(insight["title"])[:200]
    desc = str(insight.get("description", "")).strip()
    return (desc.split(". ")[0] or "Insight")[:200]


# ── candidate generators (DB) ───────────────────────────────────────────────────

async def _gather_subgraph(conn: Any, node_ids: list[str]) -> tuple[list[dict], list[dict]]:
    nodes = await conn.fetch(
        "SELECT id, node_type, label, properties, source, source_url, valid_from "
        "FROM nodes WHERE id = ANY($1::uuid[]) AND valid_to IS NULL",
        node_ids,
    )
    edges = await conn.fetch(
        "SELECT id, edge_type, from_node_id, to_node_id, properties, source, source_url "
        "FROM edges WHERE from_node_id = ANY($1::uuid[]) AND to_node_id = ANY($1::uuid[]) "
        "AND valid_to IS NULL AND inferred = FALSE",
        node_ids,
    )
    return [dict(n) for n in nodes], [dict(e) for e in edges]


async def spatial_temporal_candidates(
    radius_m: float = 150.0,
    window_days: int = 30,
    limit: int = 50,
) -> list[Candidate]:
    """ConstructionSite anchors + geo nodes nearby and overlapping in time."""
    out: list[Candidate] = []
    async with _get_conn() as conn:
        anchors = await conn.fetch(
            """
            SELECT id, valid_from, valid_to FROM nodes
            WHERE node_type = 'ConstructionSite' AND geom IS NOT NULL
              AND valid_to IS NULL
            ORDER BY observed_at DESC LIMIT $1
            """,
            limit,
        )
        for a in anchors:
            rows = await conn.fetch(
                """
                SELECT n.id
                FROM nodes anchor
                JOIN nodes n ON n.id <> anchor.id
                    AND n.geom IS NOT NULL
                    AND n.valid_to IS NULL
                    AND ST_DWithin(anchor.geom::geography, n.geom::geography, $2)
                    AND (
                        n.valid_from IS NULL OR anchor.valid_from IS NULL
                        OR n.valid_from BETWEEN anchor.valid_from - ($3 || ' days')::interval
                                            AND COALESCE(anchor.valid_to, anchor.valid_from)
                                                + ($3 || ' days')::interval
                    )
                WHERE anchor.id = $1
                LIMIT 25
                """,
                a["id"], radius_m, str(window_days),
            )
            neighbour_ids = [str(r["id"]) for r in rows]
            if not neighbour_ids:
                continue
            node_ids = [str(a["id"]), *neighbour_ids]
            nodes, edges = await _gather_subgraph(conn, node_ids)
            # Need at least two distinct node types to be worth reasoning over.
            if len({n["node_type"] for n in nodes}) < 2:
                continue
            out.append(
                Candidate("spatial_temporal", node_ids, str(a["id"]), nodes, edges)
            )
    return out


async def area_bridge_candidates(limit: int = 50) -> list[Candidate]:
    """
    District-centric cross-domain subgraphs. Anchor on a GeoArea that has BOTH a
    physical node located in it (LOCATED_IN, source fact) AND a text node that
    mentions it (mentions_area, inferred by the text linker). This is exactly the
    bridge that lets "council meeting about district X" meet "roadworks in
    district X" — the headline inefficiency pattern.
    """
    out: list[Candidate] = []
    async with _get_conn() as conn:
        areas = await conn.fetch(
            """
            SELECT g.id
            FROM nodes g
            WHERE g.node_type = 'GeoArea' AND g.valid_to IS NULL
              AND EXISTS (
                  SELECT 1 FROM edges e WHERE e.to_node_id = g.id
                    AND e.edge_type = 'LOCATED_IN' AND e.valid_to IS NULL)
              AND (
                  EXISTS (
                      SELECT 1 FROM edges e WHERE e.to_node_id = g.id
                        AND e.edge_type = 'RELATES_TO'
                        AND e.properties->>'relation' = 'mentions_area'
                        AND e.valid_to IS NULL)
                  OR EXISTS (
                      -- a street in this area is mentioned by some text node
                      SELECT 1 FROM edges p
                      JOIN edges m ON m.to_node_id = p.from_node_id
                        AND m.edge_type = 'RELATES_TO'
                        AND m.properties->>'relation' = 'mentions_street'
                        AND m.valid_to IS NULL
                      WHERE p.edge_type = 'PART_OF' AND p.to_node_id = g.id
                        AND p.valid_to IS NULL)
              )
            LIMIT $1
            """,
            limit,
        )
        for area in areas:
            nbr = await conn.fetch(
                """
                -- direct: physical nodes in the area + text nodes mentioning it
                SELECT from_node_id AS other FROM edges
                WHERE to_node_id = $1 AND valid_to IS NULL
                  AND (edge_type = 'LOCATED_IN'
                       OR (edge_type = 'RELATES_TO'
                           AND properties->>'relation' = 'mentions_area'))
                UNION
                -- 2-hop: text nodes that mention a street belonging to the area
                SELECT m.from_node_id AS other
                FROM edges p
                JOIN edges m ON m.to_node_id = p.from_node_id
                    AND m.edge_type = 'RELATES_TO'
                    AND m.properties->>'relation' = 'mentions_street'
                    AND m.valid_to IS NULL
                WHERE p.edge_type = 'PART_OF' AND p.to_node_id = $1
                  AND p.valid_to IS NULL
                LIMIT 40
                """,
                area["id"],
            )
            node_ids = [str(area["id"]), *[str(r["other"]) for r in nbr]]
            nodes, edges = await _gather_subgraph(conn, node_ids)
            if len({n["node_type"] for n in nodes}) < 2:
                continue
            out.append(Candidate("area_bridge", node_ids, str(area["id"]), nodes, edges))
    return out


async def ego_network_candidates(min_degree: int = 3, limit: int = 50) -> list[Candidate]:
    """High-degree nodes + their immediate neighbours (source-fact edges only)."""
    out: list[Candidate] = []
    async with _get_conn() as conn:
        hubs = await conn.fetch(
            """
            SELECT n.id, COUNT(e.id) AS deg
            FROM nodes n
            JOIN edges e ON (e.from_node_id = n.id OR e.to_node_id = n.id)
                AND e.valid_to IS NULL AND e.inferred = FALSE
            WHERE n.valid_to IS NULL
            GROUP BY n.id
            HAVING COUNT(e.id) >= $1
            ORDER BY deg DESC LIMIT $2
            """,
            min_degree, limit,
        )
        for h in hubs:
            nbr = await conn.fetch(
                """
                SELECT DISTINCT CASE WHEN from_node_id = $1 THEN to_node_id
                                     ELSE from_node_id END AS other
                FROM edges
                WHERE (from_node_id = $1 OR to_node_id = $1)
                  AND valid_to IS NULL AND inferred = FALSE
                LIMIT 25
                """,
                h["id"],
            )
            node_ids = [str(h["id"]), *[str(r["other"]) for r in nbr]]
            nodes, edges = await _gather_subgraph(conn, node_ids)
            if len(nodes) < 2:
                continue
            out.append(Candidate("ego_network", node_ids, str(h["id"]), nodes, edges))
    return out


# ── reasoning + persistence ─────────────────────────────────────────────────────

def _build_client() -> Any:
    import anthropic

    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


async def _reason(client: Any, candidate: Candidate, insight_type: str) -> list[dict[str, Any]]:
    template = INEFFICIENCY_PROMPT if insight_type == "inefficiency" else SYNERGY_PROMPT
    prompt = template.format(subgraph_json=format_subgraph(candidate.nodes, candidate.edges))
    message = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    return parse_insights(message.content[0].text)


async def _persist(candidate: Candidate, insight_type: str, insight: dict[str, Any]) -> bool:
    confidence = float(insight.get("confidence", 0) or 0)
    if confidence < CONFIDENCE_FLOOR:
        return False
    key = candidate_key(insight_type, candidate.node_ids)
    async with _get_conn() as conn:
        result = await conn.execute(
            """
            INSERT INTO insights
                (insight_type, title, description, confidence, evidence_node_ids,
                 evidence, reasoning_trace, model, generator, candidate_key)
            VALUES ($1,$2,$3,$4,$5::uuid[],$6,$7,$8,$9,$10)
            ON CONFLICT (candidate_key) DO NOTHING
            """,
            insight_type,
            derive_title(insight),
            str(insight.get("description", "")),
            confidence,
            candidate.node_ids,
            json.dumps(insight.get("evidence", [])),
            insight.get("reasoning_trace"),
            MODEL,
            candidate.generator,
            key,
        )
    return result.endswith("1")


async def scan(insight_types: tuple[str, ...] = ("inefficiency", "synergy")) -> dict[str, int]:
    """Generate candidates, reason over each, persist high-confidence insights."""
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not configured — cannot run insight scan")

    candidates = (
        await spatial_temporal_candidates()
        + await area_bridge_candidates()
        + await ego_network_candidates()
    )
    logger.info("insight scan: %d candidate subgraphs", len(candidates))

    client = _build_client()
    counts = {"candidates": len(candidates), "written": 0}
    for candidate in candidates:
        for insight_type in insight_types:
            try:
                for insight in await _reason(client, candidate, insight_type):
                    if await _persist(candidate, insight_type, insight):
                        counts["written"] += 1
            except Exception as exc:  # one bad candidate shouldn't abort the scan
                logger.warning("scan failed on %s/%s: %s", candidate.anchor_id, insight_type, exc)
    return counts
