"""
Prefect flows for scheduled ingestion.

Cadence (from ingestion-and-temporal-design.md):
  geo_spine  — monthly  (reference)
  oparl      — daily    (event_stream)
  overpass   — weekly   (reference)

Run locally:
  prefect worker start --pool default
  python -m ingestion.flows   # deploys all flows

Or trigger one-off:
  from ingestion.flows import run_geo_spine
  asyncio.run(run_geo_spine())
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from prefect import flow, get_run_logger, task
from prefect.schedules import CronSchedule

from connectors.autobahn.connector import AutobahnConnector
from connectors.baustellen.connector import BaustellenConnector
from connectors.brightsky.connector import BrightSkyConnector
from connectors.geo_spine.connector import GeoSpineConnector
from connectors.gremienniederschriften.connector import GremienNiederschriftenConnector
from connectors.gremientermine.connector import GremienTermineConnector
from connectors.gtfs_realtime.connector import GtfsRealtimeConnector
from connectors.gtfs_static.connector import GtfsStaticConnector
from connectors.lanuv_air.connector import LanuvAirConnector
from connectors.ods_pois.connector import OdsPoisConnector
from connectors.ods_stats.connector import OdsStatsConnector
from connectors.oparl.connector import OParlConnector
from connectors.overpass.connector import OverpassConnector
from connectors.polizei_rss.connector import PolizeiRssConnector
from connectors.strassen.connector import StrassenConnector
from connectors.strassenabschnitte.connector import StrassenabschnitteConnector
from connectors.vergabe_nrw.connector import VergabeNrwConnector
from connectors.wahlergebnisse.connector import WahlergebnisseConnector
from connectors.wahlergebnisse_stimmbezirk.connector import (
    WahlergebnisseStimmbezirkConnector,
)
from db.session import get_conn
from ingestion.writer import upsert_edge, upsert_node
from ontology.nodes import GeoArea

logger = logging.getLogger(__name__)


# ── checkpoint helpers ─────────────────────────────────────────────────────────

async def _load_checkpoint(connector: str) -> dict[str, Any] | None:
    async with get_conn() as conn:
        row = await conn.fetchrow(
            """
            SELECT checkpoint FROM ingestion_runs
            WHERE connector = $1 AND status = 'ok'
            ORDER BY finished_at DESC LIMIT 1
            """,
            connector,
        )
        if row and row["checkpoint"]:
            return row["checkpoint"]
    return None


async def _start_run(connector: str) -> str:
    async with get_conn() as conn:
        row = await conn.fetchrow(
            "INSERT INTO ingestion_runs (connector) VALUES ($1) RETURNING id",
            connector,
        )
        return str(row["id"])


async def _finish_run(
    run_id: str,
    connector: str,
    nodes_written: int,
    edges_written: int,
    checkpoint: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    status = "error" if error else "ok"
    async with get_conn() as conn:
        await conn.execute(
            """
            UPDATE ingestion_runs
            SET finished_at = NOW(), status = $2,
                nodes_written = $3, edges_written = $4,
                error_message = $5, checkpoint = $6
            WHERE id = $1
            """,
            run_id, status, nodes_written, edges_written,
            error, json.dumps(checkpoint) if checkpoint else None,
        )


# ── geo spine ─────────────────────────────────────────────────────────────────

@flow(name="geo-spine", log_prints=True)
async def run_geo_spine() -> None:
    log = get_run_logger()
    connector = GeoSpineConnector()
    run_id = await _start_run(connector.source_name)
    nodes_written = edges_written = 0
    stadtbezirk_map: dict[str, GeoArea] = {}
    stat_bezirk_nodes: list[GeoArea] = []

    try:
        async with connector:
            async for raw in connector.fetch():
                normalized = connector.normalize(raw)
                nodes = await connector.emit_entities(normalized)
                for node in nodes:
                    await upsert_node(node)
                    nodes_written += 1
                    if isinstance(node, GeoArea):
                        area_type = node.properties.get("area_type")
                        if area_type == "stadtbezirk" and node.source_id:
                            stadtbezirk_map[node.source_id] = node
                        elif area_type == "statistischer_bezirk":
                            stat_bezirk_nodes.append(node)

        # Resolve PART_OF edges now both layers are loaded
        async with connector:
            for stat_node in stat_bezirk_nodes:
                for edge in await connector.link_stat_to_stadt(stat_node, stadtbezirk_map):
                    await upsert_edge(edge)
                    edges_written += 1

        log.info("geo-spine done: %d nodes, %d edges", nodes_written, edges_written)
        await _finish_run(run_id, connector.source_name, nodes_written, edges_written)
    except Exception as exc:
        log.error("geo-spine failed: %s", exc, exc_info=True)
        await _finish_run(run_id, connector.source_name, nodes_written, edges_written, error=str(exc))
        raise


# ── OParl ─────────────────────────────────────────────────────────────────────

@flow(name="oparl-council", log_prints=True)
async def run_oparl() -> None:
    from config import settings
    log = get_run_logger()
    if not settings.oparl_endpoint_url:
        log.warning(
            "OPARL_ENDPOINT_URL not set — Dortmund disabled OParl in production "
            "(FragDenStaat FOI Oct 2024). Skipping flow."
        )
        return
    connector = OParlConnector()
    run_id = await _start_run(connector.source_name)
    checkpoint = await _load_checkpoint(connector.source_name)
    nodes_written = edges_written = 0
    new_checkpoint: dict[str, Any] = {"modified_since": datetime.utcnow().isoformat() + "Z"}

    try:
        async with connector:
            async for raw in connector.fetch(checkpoint):
                normalized = connector.normalize(raw)
                nodes = await connector.emit_entities(normalized)
                for node in nodes:
                    await upsert_node(node)
                    nodes_written += 1
                edges = await connector.emit_edges(normalized, nodes)
                for edge in edges:
                    await upsert_edge(edge)
                    edges_written += 1

        log.info("oparl done: %d nodes, %d edges", nodes_written, edges_written)
        await _finish_run(run_id, connector.source_name, nodes_written, edges_written, new_checkpoint)
    except Exception as exc:
        log.error("oparl failed: %s", exc, exc_info=True)
        await _finish_run(run_id, connector.source_name, nodes_written, edges_written, error=str(exc))
        raise


# ── Gremientermine ────────────────────────────────────────────────────────────

@flow(name="gremientermine", log_prints=True)
async def run_gremientermine() -> None:
    log = get_run_logger()
    connector = GremienTermineConnector()
    run_id = await _start_run(connector.source_name)
    nodes_written = edges_written = 0

    try:
        async with connector:
            async for raw in connector.fetch():
                normalized = connector.normalize(raw)
                nodes = await connector.emit_entities(normalized)
                for node in nodes:
                    await upsert_node(node)
                    nodes_written += 1

        log.info("gremientermine done: %d nodes", nodes_written)
        await _finish_run(run_id, connector.source_name, nodes_written, edges_written)
    except Exception as exc:
        log.error("gremientermine failed: %s", exc, exc_info=True)
        await _finish_run(run_id, connector.source_name, nodes_written, edges_written, error=str(exc))
        raise


# ── Gremienniederschriften (council minutes → meetings/agenda/resolutions) ────

@flow(name="gremienniederschriften", log_prints=True)
async def run_gremienniederschriften() -> None:
    log = get_run_logger()
    connector = GremienNiederschriftenConnector()
    run_id = await _start_run(connector.source_name)
    checkpoint = await _load_checkpoint(connector.source_name)
    seen: set[str] = set((checkpoint or {}).get("seen_document_ids", []))
    nodes_written = edges_written = 0
    try:
        async with connector:
            async for raw in connector.fetch(checkpoint):
                normalized = connector.normalize(raw)
                nodes = await connector.emit_entities(normalized)
                for node in nodes:
                    await upsert_node(node)
                    nodes_written += 1
                for edge in await connector.emit_edges(normalized, nodes):
                    await upsert_edge(edge)
                    edges_written += 1
                seen.add(normalized["document_id"])
        log.info(
            "gremienniederschriften done: %d nodes, %d edges (%d docs seen)",
            nodes_written, edges_written, len(seen),
        )
        await _finish_run(
            run_id, connector.source_name, nodes_written, edges_written,
            checkpoint={"seen_document_ids": sorted(seen)},
        )
    except Exception as exc:
        log.error("gremienniederschriften failed: %s", exc, exc_info=True)
        await _finish_run(
            run_id, connector.source_name, nodes_written, edges_written,
            checkpoint={"seen_document_ids": sorted(seen)}, error=str(exc),
        )
        raise


# ── Overpass POIs ──────────────────────────────────────────────────────────────

@flow(name="overpass-pois", log_prints=True)
async def run_overpass() -> None:
    log = get_run_logger()
    connector = OverpassConnector()
    run_id = await _start_run(connector.source_name)
    nodes_written = edges_written = 0
    poi_nodes = []

    try:
        async with connector:
            async for raw in connector.fetch():
                normalized = connector.normalize(raw)
                nodes = await connector.emit_entities(normalized)
                for node in nodes:
                    await upsert_node(node)
                    nodes_written += 1
                    poi_nodes.append(node)

        # Spatial join: link each POI to its statistischer Bezirk via PostGIS
        edges_written += await _locate_pois_in_geo_areas(poi_nodes)

        log.info("overpass done: %d nodes, %d edges", nodes_written, edges_written)
        await _finish_run(run_id, connector.source_name, nodes_written, edges_written)
    except Exception as exc:
        log.error("overpass failed: %s", exc, exc_info=True)
        await _finish_run(run_id, connector.source_name, nodes_written, edges_written, error=str(exc))
        raise


async def _locate_pois_in_geo_areas(poi_nodes: list) -> int:
    """
    ST_Within join: for each POI with geometry, find the statistischer Bezirk
    that contains it and emit a LOCATED_IN edge.
    """
    from ingestion.writer import upsert_edge
    from ontology.edges import located_in as located_in_edge
    from uuid import UUID

    written = 0
    async with get_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT p.id AS poi_id, g.id AS geo_id
            FROM nodes p
            JOIN nodes g ON g.node_type = 'GeoArea'
                AND g.properties->>'area_type' = 'statistischer_bezirk'
                AND ST_Within(p.geom, g.geom)
            WHERE p.node_type = 'POI'
              AND p.geom IS NOT NULL
              AND p.valid_to IS NULL
              AND g.valid_to IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM edges e
                  WHERE e.edge_type = 'LOCATED_IN'
                    AND e.from_node_id = p.id
                    AND e.to_node_id = g.id
                    AND e.valid_to IS NULL
              )
            """
        )
        for row in rows:
            edge = located_in_edge(
                from_node_id=UUID(row["poi_id"]),
                to_node_id=UUID(row["geo_id"]),
                source="osm_overpass",
                observed_at=datetime.utcnow(),
            )
            await upsert_edge(edge)
            written += 1
    return written


# ── Generic node-only runner ─────────────────────────────────────────────────

async def _run_node_connector(connector: Any, flow_log: Any) -> None:
    """
    Drive a connector that emits nodes (and optional edges) with no cross-item
    join. Used by the snapshot / event_stream / reference sources that don't
    need a post-pass (weather, air, police, tenders, demographics, gtfs-rt).
    """
    run_id = await _start_run(connector.source_name)
    checkpoint = await _load_checkpoint(connector.source_name)
    nodes_written = edges_written = 0
    try:
        async with connector:
            async for raw in connector.fetch(checkpoint):
                normalized = connector.normalize(raw)
                nodes = await connector.emit_entities(normalized)
                for node in nodes:
                    await upsert_node(node)
                    nodes_written += 1
                for edge in await connector.emit_edges(normalized, nodes):
                    await upsert_edge(edge)
                    edges_written += 1
        flow_log.info(
            "%s done: %d nodes, %d edges", connector.source_name, nodes_written, edges_written
        )
        await _finish_run(run_id, connector.source_name, nodes_written, edges_written)
    except Exception as exc:
        flow_log.error("%s failed: %s", connector.source_name, exc, exc_info=True)
        await _finish_run(
            run_id, connector.source_name, nodes_written, edges_written, error=str(exc)
        )
        raise


@flow(name="brightsky-weather", log_prints=True)
async def run_brightsky() -> None:
    await _run_node_connector(BrightSkyConnector(), get_run_logger())


@flow(name="polizei-rss", log_prints=True)
async def run_polizei_rss() -> None:
    await _run_node_connector(PolizeiRssConnector(), get_run_logger())


@flow(name="lanuv-air", log_prints=True)
async def run_lanuv_air() -> None:
    await _run_node_connector(LanuvAirConnector(), get_run_logger())


@flow(name="vergabe-nrw", log_prints=True)
async def run_vergabe_nrw() -> None:
    await _run_node_connector(VergabeNrwConnector(), get_run_logger())


@flow(name="ods-demographics", log_prints=True)
async def run_ods_stats() -> None:
    await _run_node_connector(OdsStatsConnector(), get_run_logger())


@flow(name="gtfs-realtime", log_prints=True)
async def run_gtfs_realtime() -> None:
    await _run_node_connector(GtfsRealtimeConnector(), get_run_logger())


@flow(name="wahlergebnisse", log_prints=True)
async def run_wahlergebnisse() -> None:
    await _run_node_connector(WahlergebnisseConnector(), get_run_logger())


# ── Spatially-located node connectors (POI + construction sites) ──────────────

@flow(name="baustellen", log_prints=True)
async def run_baustellen() -> None:
    log = get_run_logger()
    connector = BaustellenConnector()
    run_id = await _start_run(connector.source_name)
    nodes_written = edges_written = 0
    try:
        async with connector:
            async for raw in connector.fetch():
                normalized = connector.normalize(raw)
                for node in await connector.emit_entities(normalized):
                    await upsert_node(node)
                    nodes_written += 1
        edges_written += await _locate_nodes_in_geo_areas(
            "ConstructionSite", connector.source_name
        )
        log.info("baustellen done: %d nodes, %d edges", nodes_written, edges_written)
        await _finish_run(run_id, connector.source_name, nodes_written, edges_written)
    except Exception as exc:
        log.error("baustellen failed: %s", exc, exc_info=True)
        await _finish_run(run_id, connector.source_name, nodes_written, edges_written, error=str(exc))
        raise


@flow(name="ods-pois", log_prints=True)
async def run_ods_pois() -> None:
    log = get_run_logger()
    connector = OdsPoisConnector()
    run_id = await _start_run(connector.source_name)
    nodes_written = edges_written = 0
    try:
        async with connector:
            async for raw in connector.fetch():
                normalized = connector.normalize(raw)
                for node in await connector.emit_entities(normalized):
                    await upsert_node(node)
                    nodes_written += 1
        edges_written += await _locate_nodes_in_geo_areas("POI", connector.source_name)
        log.info("ods-pois done: %d nodes, %d edges", nodes_written, edges_written)
        await _finish_run(run_id, connector.source_name, nodes_written, edges_written)
    except Exception as exc:
        log.error("ods-pois failed: %s", exc, exc_info=True)
        await _finish_run(run_id, connector.source_name, nodes_written, edges_written, error=str(exc))
        raise


# ── Ratswahl per Stimmbezirk (precinct results, geo-located) ──────────────────

@flow(name="wahlergebnisse-stimmbezirk", log_prints=True)
async def run_wahlergebnisse_stimmbezirk() -> None:
    log = get_run_logger()
    connector = WahlergebnisseStimmbezirkConnector()
    run_id = await _start_run(connector.source_name)
    nodes_written = edges_written = 0
    try:
        async with connector:
            async for raw in connector.fetch():
                normalized = connector.normalize(raw)
                for node in await connector.emit_entities(normalized):
                    await upsert_node(node)
                    nodes_written += 1
        # Locate precinct events that carry a polling-station point.
        edges_written += await _locate_nodes_in_geo_areas(
            "Event", connector.source_name, source_filter=connector.source_name
        )
        log.info(
            "wahlergebnisse-stimmbezirk done: %d nodes, %d edges",
            nodes_written, edges_written,
        )
        await _finish_run(run_id, connector.source_name, nodes_written, edges_written)
    except Exception as exc:
        log.error("wahlergebnisse-stimmbezirk failed: %s", exc, exc_info=True)
        await _finish_run(run_id, connector.source_name, nodes_written, edges_written, error=str(exc))
        raise


# ── Autobahn live traffic (roadworks + warnings + closures) ───────────────────

@flow(name="autobahn-traffic", log_prints=True)
async def run_autobahn() -> None:
    log = get_run_logger()
    connector = AutobahnConnector()
    run_id = await _start_run(connector.source_name)
    checkpoint = await _load_checkpoint(connector.source_name)
    seen: set[str] = set((checkpoint or {}).get("seen_ids", []))
    nodes_written = edges_written = 0
    try:
        async with connector:
            async for raw in connector.fetch(checkpoint):
                normalized = connector.normalize(raw)
                for node in await connector.emit_entities(normalized):
                    await upsert_node(node)
                    nodes_written += 1
                seen.add(normalized["source_id"])
        # Locate both node types this connector emits.
        for node_type in ("ConstructionSite", "Event"):
            edges_written += await _locate_nodes_in_geo_areas(
                node_type, connector.source_name, source_filter=connector.source_name
            )
        log.info("autobahn done: %d nodes, %d edges", nodes_written, edges_written)
        await _finish_run(
            run_id, connector.source_name, nodes_written, edges_written,
            checkpoint={"seen_ids": sorted(seen)},
        )
    except Exception as exc:
        log.error("autobahn failed: %s", exc, exc_info=True)
        await _finish_run(
            run_id, connector.source_name, nodes_written, edges_written,
            checkpoint={"seen_ids": sorted(seen)}, error=str(exc),
        )
        raise


# ── GTFS static (stops + routes) ──────────────────────────────────────────────

@flow(name="gtfs-static", log_prints=True)
async def run_gtfs_static() -> None:
    log = get_run_logger()
    connector = GtfsStaticConnector()
    run_id = await _start_run(connector.source_name)
    nodes_written = edges_written = 0
    try:
        async with connector:
            async for raw in connector.fetch():
                normalized = connector.normalize(raw)
                for node in await connector.emit_entities(normalized):
                    await upsert_node(node)
                    nodes_written += 1
        # Locate transit stops in their statistischer Bezirk.
        edges_written += await _locate_nodes_in_geo_areas("TransitStop", connector.source_name)
        log.info("gtfs-static done: %d nodes, %d edges", nodes_written, edges_written)
        await _finish_run(run_id, connector.source_name, nodes_written, edges_written)
    except Exception as exc:
        log.error("gtfs-static failed: %s", exc, exc_info=True)
        await _finish_run(run_id, connector.source_name, nodes_written, edges_written, error=str(exc))
        raise


async def _locate_nodes_in_geo_areas(
    node_type: str, source: str, source_filter: str | None = None
) -> int:
    """
    Generic ST_Within join: link every node of `node_type` with geometry to the
    statistischer Bezirk that contains it via a LOCATED_IN edge. Mirrors the POI
    join but parameterized by node type. `source_filter` narrows to one source's
    nodes (needed for Event, which several connectors emit).
    """
    from uuid import UUID

    from ontology.edges import located_in as located_in_edge

    written = 0
    async with get_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT p.id AS child_id, g.id AS geo_id
            FROM nodes p
            JOIN nodes g ON g.node_type = 'GeoArea'
                AND g.properties->>'area_type' = 'statistischer_bezirk'
                AND ST_Within(p.geom, g.geom)
            WHERE p.node_type = $1
              AND ($2::text IS NULL OR p.source = $2)
              AND p.geom IS NOT NULL
              AND p.valid_to IS NULL
              AND g.valid_to IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM edges e
                  WHERE e.edge_type = 'LOCATED_IN'
                    AND e.from_node_id = p.id
                    AND e.to_node_id = g.id
                    AND e.valid_to IS NULL
              )
            """,
            node_type,
            source_filter,
        )
        for row in rows:
            edge = located_in_edge(
                from_node_id=UUID(row["child_id"]),
                to_node_id=UUID(row["geo_id"]),
                source=source,
                observed_at=datetime.now(timezone.utc),
            )
            await upsert_edge(edge)
            written += 1
    return written


# ── Streets (Road gazetteer + Road→Bezirk) ────────────────────────────────────

@flow(name="strassen", log_prints=True)
async def run_strassen() -> None:
    log = get_run_logger()
    connector = StrassenConnector()
    run_id = await _start_run(connector.source_name)
    nodes_written = edges_written = 0
    try:
        async with connector:
            async for raw in connector.fetch():
                normalized = connector.normalize(raw)
                for node in await connector.emit_entities(normalized):
                    await upsert_node(node)
                    nodes_written += 1
        edges_written += await _link_roads_to_bezirke(connector.source_name)
        log.info("strassen done: %d nodes, %d edges", nodes_written, edges_written)
        await _finish_run(run_id, connector.source_name, nodes_written, edges_written)
    except Exception as exc:
        log.error("strassen failed: %s", exc, exc_info=True)
        await _finish_run(run_id, connector.source_name, nodes_written, edges_written, error=str(exc))
        raise


async def _link_roads_to_bezirke(source: str) -> int:
    """PART_OF: each Road → the statistischer Bezirk named in its properties."""
    from uuid import UUID

    from ontology.edges import part_of

    written = 0
    async with get_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT r.id AS road_id, g.id AS geo_id
            FROM nodes r
            JOIN nodes g ON g.node_type = 'GeoArea'
                AND g.properties->>'area_type' = 'statistischer_bezirk'
                AND lower(g.label) = lower(r.properties->>'stat_bezirk')
            WHERE r.node_type = 'Road' AND r.valid_to IS NULL AND g.valid_to IS NULL
              AND r.properties->>'stat_bezirk' IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM edges e
                  WHERE e.edge_type = 'PART_OF' AND e.from_node_id = r.id
                    AND e.to_node_id = g.id AND e.valid_to IS NULL
              )
            """
        )
        for row in rows:
            edge = part_of(
                child_id=UUID(row["road_id"]),
                parent_id=UUID(row["geo_id"]),
                source=source,
                observed_at=datetime.now(timezone.utc),
            )
            await upsert_edge(edge)
            written += 1
    return written


# ── Street segments (geometry) ────────────────────────────────────────────────

@flow(name="strassenabschnitte", log_prints=True)
async def run_strassenabschnitte() -> None:
    log = get_run_logger()
    connector = StrassenabschnitteConnector()
    run_id = await _start_run(connector.source_name)
    nodes_written = edges_written = 0
    try:
        async with connector:
            async for raw in connector.fetch():
                normalized = connector.normalize(raw)
                for node in await connector.emit_entities(normalized):
                    await upsert_node(node)
                    nodes_written += 1
        edges_written += await _link_segments_to_streets(connector.source_name)
        edges_written += await _locate_nodes_in_geo_areas(
            "Road", connector.source_name, source_filter=connector.source_name
        )
        log.info("strassenabschnitte done: %d nodes, %d edges", nodes_written, edges_written)
        await _finish_run(run_id, connector.source_name, nodes_written, edges_written)
    except Exception as exc:
        log.error("strassenabschnitte failed: %s", exc, exc_info=True)
        await _finish_run(run_id, connector.source_name, nodes_written, edges_written, error=str(exc))
        raise


async def _link_segments_to_streets(source: str) -> int:
    """PART_OF: each geometric segment → its parent street (by Straßenschlüssel)."""
    from uuid import UUID

    from ontology.edges import part_of

    written = 0
    async with get_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT seg.id AS seg_id, st.id AS street_id
            FROM nodes seg
            JOIN nodes st ON st.node_type = 'Road'
                AND st.source = 'opendata_dortmund_strassen'
                AND st.properties->>'strassenschlussel' = seg.properties->>'strassenschlussel'
                AND st.valid_to IS NULL
            WHERE seg.node_type = 'Road' AND seg.source = $1 AND seg.valid_to IS NULL
              AND seg.properties->>'strassenschlussel' IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM edges e
                  WHERE e.edge_type = 'PART_OF' AND e.from_node_id = seg.id
                    AND e.to_node_id = st.id AND e.valid_to IS NULL
              )
            """,
            source,
        )
        for row in rows:
            edge = part_of(
                child_id=UUID(row["seg_id"]),
                parent_id=UUID(row["street_id"]),
                source=source,
                observed_at=datetime.now(timezone.utc),
            )
            await upsert_edge(edge)
            written += 1
    return written


# ── Text → geo linking (entity resolution) ────────────────────────────────────

@flow(name="text-linking", log_prints=True)
async def run_text_linking() -> None:
    """Link text nodes (meetings, resolutions, …) to GeoArea districts they mention."""
    from resolution.text_linker import TextLinker

    log = get_run_logger()
    run_id = await _start_run("text_linker")
    try:
        counts = await TextLinker().run()
        log.info("text-linking done: %s", counts)
        await _finish_run(run_id, "text_linker", 0, counts["edges"])
    except Exception as exc:
        log.error("text-linking failed: %s", exc, exc_info=True)
        await _finish_run(run_id, "text_linker", 0, 0, error=str(exc))
        raise


# ── Insight scan (reasoning layer) ────────────────────────────────────────────

@flow(name="insight-scan", log_prints=True)
async def run_insight_scan() -> None:
    """Generate candidate subgraphs and reason over them (inefficiency/synergy)."""
    from reasoning.scanner import scan

    log = get_run_logger()
    run_id = await _start_run("insight_scan")
    try:
        counts = await scan()
        log.info("insight-scan done: %s", counts)
        await _finish_run(run_id, "insight_scan", counts["written"], 0)
    except Exception as exc:
        log.error("insight-scan failed: %s", exc, exc_info=True)
        await _finish_run(run_id, "insight_scan", 0, 0, error=str(exc))
        raise


# ── Deployment ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from prefect import serve

    # One blocking serve() deploys every flow on its own cadence (per
    # ingestion-and-temporal-design.md). Separate .serve() calls would block on
    # the first and never reach the rest.
    serve(
        # reference — slow refresh
        run_geo_spine.to_deployment(name="geo-spine-monthly", cron="0 3 1 * *"),
        run_overpass.to_deployment(name="overpass-weekly", cron="0 4 * * 3"),
        run_ods_pois.to_deployment(name="ods-pois-monthly", cron="0 4 2 * *"),
        run_strassen.to_deployment(name="strassen-monthly", cron="0 3 2 * *"),
        run_strassenabschnitte.to_deployment(name="strassenabschnitte-monthly", cron="30 3 2 * *"),
        run_ods_stats.to_deployment(name="ods-demographics-monthly", cron="0 4 3 * *"),
        run_wahlergebnisse.to_deployment(name="wahlergebnisse-monthly", cron="0 4 4 * *"),
        run_wahlergebnisse_stimmbezirk.to_deployment(
            name="wahlergebnisse-stimmbezirk-monthly", cron="30 4 4 * *"
        ),
        run_gtfs_static.to_deployment(name="gtfs-static-weekly", cron="0 4 * * 3"),
        # event_stream / snapshot — daily
        run_oparl.to_deployment(name="oparl-daily", cron="0 6 * * *"),
        run_gremientermine.to_deployment(name="gremientermine-daily", cron="30 6 * * *"),
        run_gremienniederschriften.to_deployment(
            name="gremienniederschriften-daily", cron="45 6 * * *"
        ),
        run_baustellen.to_deployment(name="baustellen-daily", cron="0 5 * * *"),
        run_autobahn.to_deployment(name="autobahn-traffic-30min", cron="*/30 * * * *"),
        run_vergabe_nrw.to_deployment(name="vergabe-nrw-daily", cron="0 7 * * *"),
        # snapshot / event_stream — hourly+
        run_brightsky.to_deployment(name="brightsky-hourly", cron="5 * * * *"),
        run_lanuv_air.to_deployment(name="lanuv-air-hourly", cron="15 * * * *"),
        run_polizei_rss.to_deployment(name="polizei-rss-hourly", cron="25 * * * *"),
        run_gtfs_realtime.to_deployment(name="gtfs-realtime-10min", cron="*/10 * * * *"),
        # resolution + reasoning — after the daily ingests settle
        run_text_linking.to_deployment(name="text-linking-daily", cron="30 7 * * *"),
        run_insight_scan.to_deployment(name="insight-scan-daily", cron="0 8 * * *"),
    )
