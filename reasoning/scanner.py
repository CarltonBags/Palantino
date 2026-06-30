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
from reasoning.llm import active_model, complete
from reasoning.prompts import (
    INEFFICIENCY_PROMPT,
    SYNERGY_PROMPT,
    SYSTEM_PROMPT,
    format_subgraph,
)

logger = logging.getLogger(__name__)

CONFIDENCE_FLOOR = 0.7
# News-context synergies are creative/contextual and human-reviewed, so persist
# them at a lower bar — surface more, let a person confirm or dismiss.
NEWS_CONFIDENCE_FLOOR = 0.6


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


def dedup_candidates(candidates: list[Candidate]) -> list[Candidate]:
    """
    Drop candidates whose node set is identical to one already kept (different
    anchors often pull the same cluster). Keeps the first occurrence, so the
    earlier generator wins. Saves redundant Claude calls.
    """
    seen: set[frozenset[str]] = set()
    out: list[Candidate] = []
    for c in candidates:
        key = frozenset(c.node_ids)
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


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


# Anchor types for spatial-temporal clustering: things that *happen* at a place
# and time (not static infrastructure like POIs/stops). Construction + every
# kind of Event (police incident, road closure, transit disruption, precinct).
_ANCHOR_TYPES = ("ConstructionSite", "Event")


async def spatial_temporal_candidates(
    radius_m: float = 150.0,
    window_days: int = 30,
    limit: int = 50,
    anchor_types: tuple[str, ...] = _ANCHOR_TYPES,
) -> list[Candidate]:
    """Anchor on event-like geo nodes + others nearby and overlapping in time."""
    out: list[Candidate] = []
    async with _get_conn() as conn:
        anchors = await conn.fetch(
            """
            SELECT id, valid_from, valid_to FROM nodes
            WHERE node_type = ANY($2::text[]) AND geom IS NOT NULL
              AND valid_to IS NULL
            ORDER BY observed_at DESC LIMIT $1
            """,
            limit, list(anchor_types),
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


async def news_context_candidates(
    news_limit: int = 15, event_sample: int = 20
) -> list[Candidate]:
    """
    News-driven context. Anchor on a recent local-news article and pull the
    SEMANTICALLY NEAREST upcoming events (by embedding cosine distance) into the
    same subgraph, so the model can connect what the news SIGNALS (a need, mood,
    theme, audience) to an event that could address or amplify it — e.g. a piece
    on a refugee centre + a participatory exhibition for the same audience.

    Retrieval is semantic, NOT spatial: the relevant event may be across town.
    Requires embeddings (run_embed_nodes); without them this yields nothing and
    the scan falls back to the other generators.
    """
    out: list[Candidate] = []
    async with _get_conn() as conn:
        anchors = await conn.fetch(
            """
            SELECT n.id FROM nodes n
            JOIN node_embeddings e ON e.node_id = n.id
            WHERE n.node_type = 'Event' AND n.properties->>'event_type' = 'news'
              AND n.valid_to IS NULL
            ORDER BY n.valid_from DESC NULLS LAST
            LIMIT $1
            """,
            news_limit,
        )
        for a in anchors:
            # k nearest upcoming public events to this article's embedding.
            rows = await conn.fetch(
                """
                SELECT ev.id
                FROM nodes ev
                JOIN node_embeddings ee ON ee.node_id = ev.id
                WHERE ev.node_type = 'Event'
                  AND ev.source = 'dortmund_veranstaltungskalender'
                  AND ev.valid_to IS NULL AND ev.geom IS NOT NULL
                  AND (ev.valid_from IS NULL OR ev.valid_from >= NOW() - INTERVAL '7 days')
                ORDER BY ee.embedding <=> (SELECT embedding FROM node_embeddings WHERE node_id = $1)
                LIMIT $2
                """,
                a["id"], event_sample,
            )
            neighbour_ids = [str(r["id"]) for r in rows]
            if not neighbour_ids:
                continue
            node_ids = [str(a["id"]), *neighbour_ids]
            nodes, edges = await _gather_subgraph(conn, node_ids)
            if len(nodes) < 2:
                continue
            out.append(Candidate("news_context", node_ids, str(a["id"]), nodes, edges))
    return out


# ── reasoning + persistence ─────────────────────────────────────────────────────

async def _reason(candidate: Candidate, insight_type: str) -> list[dict[str, Any]]:
    from datetime import date

    template = INEFFICIENCY_PROMPT if insight_type == "inefficiency" else SYNERGY_PROMPT
    prompt = template.format(
        subgraph_json=format_subgraph(candidate.nodes, candidate.edges),
        today=date.today().isoformat(),
    )
    # Headroom for reasoning models (v4-pro spends budget on hidden reasoning).
    text = await complete(SYSTEM_PROMPT, prompt, max_tokens=8000)
    return parse_insights(text)


async def _persist(candidate: Candidate, insight_type: str, insight: dict[str, Any]) -> bool:
    confidence = float(insight.get("confidence", 0) or 0)
    floor = NEWS_CONFIDENCE_FLOOR if candidate.generator == "news_context" else CONFIDENCE_FLOOR
    if confidence < floor:
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
            insight.get("evidence", []),
            insight.get("reasoning_trace"),
            active_model(),
            candidate.generator,
            key,
        )
    return result.endswith("1")


async def scan(
    insight_types: tuple[str, ...] = ("inefficiency", "synergy"), limit: int = 50
) -> dict[str, int]:
    """Generate candidates, reason over each, persist high-confidence insights.
    `limit` caps candidates per generator — keep it small for on-demand scans."""
    key = settings.deepseek_api_key if settings.llm_provider == "deepseek" else settings.anthropic_api_key
    if not key:
        raise RuntimeError(
            f"No API key for llm_provider={settings.llm_provider!r} — cannot run insight scan"
        )

    candidates = dedup_candidates(
        await spatial_temporal_candidates(limit=limit)
        + await area_bridge_candidates(limit=limit)
        + await ego_network_candidates(limit=limit)
    )
    # News-context candidates run through synergy only (they surface creative
    # news→event/place connections, not inefficiencies).
    news_candidates = dedup_candidates(await news_context_candidates(news_limit=limit))
    logger.info(
        "insight scan: %d candidate subgraphs + %d news-context (deduped)",
        len(candidates), len(news_candidates),
    )

    counts = {"candidates": len(candidates) + len(news_candidates), "written": 0}
    for candidate in candidates:
        for insight_type in insight_types:
            try:
                for insight in await _reason(candidate, insight_type):
                    if await _persist(candidate, insight_type, insight):
                        counts["written"] += 1
            except Exception as exc:  # one bad candidate shouldn't abort the scan
                logger.warning("scan failed on %s/%s: %s", candidate.anchor_id, insight_type, exc)
    for candidate in news_candidates:
        try:
            for insight in await _reason(candidate, "synergy"):
                if await _persist(candidate, "synergy", insight):
                    counts["written"] += 1
        except Exception as exc:
            logger.warning("news scan failed on %s: %s", candidate.anchor_id, exc)
    return counts
