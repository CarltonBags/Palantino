"""
Bitemporal writer — upserts nodes and edges into Postgres.

Rules enforced here:
  - Append-only: never overwrite or delete existing facts.
  - Dedup on (source, source_id): if unchanged, extend valid_to + touch observed_at.
  - Content hash: detect changes; if changed, close old version, open new.
  - Idempotent: running twice must not create duplicates.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Any

from db.session import get_conn
from ontology.edges import EdgeBase
from ontology.nodes import NodeBase

logger = logging.getLogger(__name__)


def _content_hash(properties: dict[str, Any], label: str) -> str:
    payload = json.dumps({"label": label, "properties": properties}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


async def upsert_node(node: NodeBase) -> tuple[str, bool]:
    """
    Insert or update a node.

    Returns (node_id, was_new).
    If (source, source_id) already exists and content is unchanged → touch observed_at.
    If content changed → close old (set valid_to=now), insert new version.
    If no source_id → always insert (snapshot observation).
    """
    now = datetime.utcnow()
    async with get_conn() as conn:
        if node.source_id:
            existing = await conn.fetchrow(
                "SELECT id, properties, label FROM nodes WHERE source = $1 AND source_id = $2 AND valid_to IS NULL",
                node.source,
                node.source_id,
            )
            if existing:
                new_hash = _content_hash(node.properties, node.label)
                old_hash = _content_hash(existing["properties"], existing["label"])
                if new_hash == old_hash:
                    # unchanged — just touch observed_at. Sync the in-memory id to
                    # the stored one so edges built from this node object resolve.
                    await conn.execute(
                        "UPDATE nodes SET observed_at = $1 WHERE id = $2",
                        now, existing["id"],
                    )
                    node.id = existing["id"]
                    return str(existing["id"]), False
                else:
                    # changed — close old version
                    await conn.execute(
                        "UPDATE nodes SET valid_to = $1 WHERE id = $2",
                        now, existing["id"],
                    )

        geom_wkt: str | None = None
        if node.geom:
            geom_wkt = f"SRID=4326;{_geojson_to_ewkt(node.geom)}"

        # Insert with the node's own id so edges built from the in-memory node
        # object (which reference node.id) resolve against the stored row.
        row = await conn.fetchrow(
            """
            INSERT INTO nodes
                (id, node_type, label, properties, source, source_id, source_url,
                 observed_at, valid_from, valid_to, inferred, confidence, reasoning_trace, geom)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,
                ST_GeomFromEWKT($14))
            RETURNING id
            """,
            node.id,
            node.node_type,
            node.label,
            node.properties,
            node.source,
            node.source_id,
            node.source_url,
            node.observed_at or now,
            node.valid_from,
            node.valid_to,
            node.inferred,
            node.confidence,
            node.reasoning_trace,
            geom_wkt,
        )
        return str(row["id"]), True


async def upsert_edge(edge: EdgeBase) -> tuple[str, bool]:
    """Insert or dedup an edge on (source, source_id) if source_id given."""
    now = datetime.utcnow()
    async with get_conn() as conn:
        if edge.source_id:
            existing = await conn.fetchrow(
                "SELECT id FROM edges WHERE source = $1 AND source_id = $2 AND valid_to IS NULL",
                edge.source, edge.source_id,
            )
            if existing:
                await conn.execute(
                    "UPDATE edges SET observed_at = $1 WHERE id = $2",
                    now, existing["id"],
                )
                return str(existing["id"]), False

        row = await conn.fetchrow(
            """
            INSERT INTO edges
                (edge_type, from_node_id, to_node_id, properties,
                 source, source_id, source_url,
                 observed_at, valid_from, valid_to,
                 inferred, confidence, reasoning_trace)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
            RETURNING id
            """,
            edge.edge_type,
            str(edge.from_node_id),
            str(edge.to_node_id),
            edge.properties,
            edge.source,
            edge.source_id,
            edge.source_url,
            edge.observed_at or now,
            edge.valid_from,
            edge.valid_to,
            edge.inferred,
            edge.confidence,
            edge.reasoning_trace,
        )
        return str(row["id"]), True


def _geojson_to_ewkt(geom: dict[str, Any]) -> str:
    """Minimal GeoJSON → WKT conversion for common types."""
    gtype = geom.get("type", "")
    coords = geom.get("coordinates", [])

    if gtype == "Point":
        return f"POINT({coords[0]} {coords[1]})"

    if gtype == "LineString":
        pts = ", ".join(f"{c[0]} {c[1]}" for c in coords)
        return f"LINESTRING({pts})"

    if gtype == "Polygon":
        rings = []
        for ring in coords:
            pts = ", ".join(f"{c[0]} {c[1]}" for c in ring)
            rings.append(f"({pts})")
        return f"POLYGON({', '.join(rings)})"

    if gtype == "MultiPolygon":
        polys = []
        for poly in coords:
            rings = []
            for ring in poly:
                pts = ", ".join(f"{c[0]} {c[1]}" for c in ring)
                rings.append(f"({pts})")
            polys.append(f"({', '.join(rings)})")
        return f"MULTIPOLYGON({', '.join(polys)})"

    # Fall back to GeoJSON text for complex types
    return f"GEOMETRYCOLLECTION EMPTY"
