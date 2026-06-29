"""
Connector: Autobahn GmbH live traffic (roadworks / warnings / closures).

Source:  https://verkehr.autobahn.de/o/autobahn/<road>/services/<service>
API:     public REST/JSON, no auth
License: Datenlizenz Deutschland – Zero (Autobahn GmbH des Bundes)
Shape:   event_stream — poll, dedupe by item identifier

What this covers (the live motorway road-state layer for Dortmund):
  - roadworks  → ConstructionSite nodes
  - warning    → Event (event_type="traffic_disruption")
  - closure    → Event (event_type="road_closure")
Scope:
  - The API is per-Autobahn nationwide. We query only the motorways through
    Dortmund (A1, A2, A40, A42, A44, A45) and keep items whose coordinate falls
    in a Dortmund bbox, so we don't ingest the whole network.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, AsyncGenerator

from connectors.base import BaseConnector, ConnectorShape
from ontology.edges import EdgeBase
from ontology.nodes import ConstructionSite, Event, NodeBase

_API = "https://verkehr.autobahn.de/o/autobahn"
_ROADS = ["A1", "A2", "A40", "A42", "A44", "A45"]
# service path -> (json key, node kind)
_SERVICES = {
    "roadworks": ("roadworks", "roadwork"),
    "warning": ("warning", "warning"),
    "closure": ("closure", "closure"),
}
_BBOX = {"min_lat": 51.41, "max_lat": 51.60, "min_lon": 7.30, "max_lon": 7.64}


def _in_bbox(lat: float, lon: float) -> bool:
    return (
        _BBOX["min_lat"] <= lat <= _BBOX["max_lat"]
        and _BBOX["min_lon"] <= lon <= _BBOX["max_lon"]
    )


def _coord(item: dict[str, Any]) -> tuple[float, float] | None:
    c = item.get("coordinate") or {}
    try:
        return float(c["lat"]), float(c["long"])
    except (KeyError, ValueError, TypeError):
        return None


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _join(value: Any) -> str | None:
    """Autobahn title/subtitle/description come as str or list[str]."""
    if isinstance(value, list):
        text = " ".join(str(v) for v in value if v).strip()
        return text or None
    return value or None


class AutobahnConnector(BaseConnector):
    shape = ConnectorShape.EVENT_STREAM
    source_name = "autobahn_gmbh"

    async def fetch(self, checkpoint: dict[str, Any] | None = None) -> AsyncGenerator[Any, None]:
        seen: set[str] = set((checkpoint or {}).get("seen_ids", []))
        for road in _ROADS:
            for service, (key, kind) in _SERVICES.items():
                try:
                    resp = await self._get(f"{_API}/{road}/services/{service}")
                except Exception:
                    continue  # one road/service outage shouldn't kill the run
                items = resp.json().get(key, [])
                for item in items:
                    coord = _coord(item)
                    if coord is None or not _in_bbox(*coord):
                        continue
                    ident = item.get("identifier")
                    if not ident or ident in seen:
                        continue
                    yield {"_road": road, "_kind": kind, "_lat": coord[0], "_lon": coord[1], **item}

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        kind = raw["_kind"]
        title = _join(raw.get("title")) or f"{raw['_road']} {kind}"
        return {
            "source_id": raw["identifier"],
            "label": title[:200],
            "road": raw["_road"],
            "kind": kind,
            "subtitle": _join(raw.get("subtitle")),
            "description": _join(raw.get("description")),
            "is_blocked": bool(raw.get("isBlocked")),
            "valid_from": _parse_ts(raw.get("startTimestamp")),
            "lat": raw["_lat"],
            "lon": raw["_lon"],
        }

    async def emit_entities(self, normalized: dict[str, Any]) -> list[NodeBase]:
        source_url = f"{_API}/{normalized['road']}/services"
        prov = self._provenance(normalized["source_id"], source_url)
        geom = {"type": "Point", "coordinates": [normalized["lon"], normalized["lat"]]}

        if normalized["kind"] == "roadwork":
            node: NodeBase = ConstructionSite(
                label=normalized["label"],
                valid_from=normalized["valid_from"],
                geom=geom,
                properties={
                    "reason": normalized["subtitle"],
                    "operator": "Autobahn GmbH",
                    "road": normalized["road"],
                    "description": normalized["description"],
                    "is_blocked": normalized["is_blocked"],
                },
                **prov,
            )
        else:
            event_type = "road_closure" if normalized["kind"] == "closure" else "traffic_disruption"
            node = Event(
                label=normalized["label"],
                valid_from=normalized["valid_from"],
                geom=geom,
                properties={
                    "event_type": event_type,
                    "road": normalized["road"],
                    "subtitle": normalized["subtitle"],
                    "description": normalized["description"],
                    "is_blocked": normalized["is_blocked"],
                    "tags": ["verkehr", "autobahn", normalized["road"]],
                },
                **prov,
            )
        return [node]

    async def emit_edges(self, normalized: dict[str, Any], nodes: list[NodeBase]) -> list[EdgeBase]:
        # LOCATED_IN (point → statistischer Bezirk) resolved by the PostGIS join
        # in the flow, per node type.
        return []
