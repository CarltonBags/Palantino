"""
Connector: GTFS static schedules (transit spine).

Source:  gtfs.de free Germany-wide local-transit feed (download.gtfs.de)
Access:  download GTFS zip — stable "latest.zip", no auth
License: CC-BY (gtfs.de / DELFI)
Shape:   reference — full refresh weekly, diff by GTFS id

Why gtfs.de and not opendata-oepnv.de:
  - The official VRR/NRW feed on opendata-oepnv.de gates downloads behind a free
    account (anonymous requests 404). gtfs.de serves the same trips openly AND is
    the same provider as our GTFS-Realtime feed, so static IDs line up with the
    realtime stream.

What this covers:
  - TransitStop nodes (stops in a Dortmund bbox) and TransitRoute nodes serving
    those stops, plus SERVES edges via stop_times (resolved in the flow).
Scope:
  - Germany-wide feed is huge. We bound stops to a Dortmund bbox and keep only
    routes that touch those stops, so we don't ingest the whole country.
"""

from __future__ import annotations

import csv
import io
import zipfile
from typing import Any, AsyncGenerator

from connectors.base import BaseConnector, ConnectorShape
from ontology.edges import EdgeBase, serves
from ontology.nodes import NodeBase, TransitRoute, TransitStop

_ZIP_URL = "https://download.gtfs.de/germany/nv_free/latest.zip"
# Dortmund bounding box (rough city extent) to bound the country-wide stop list.
_BBOX = {"min_lat": 51.41, "max_lat": 51.60, "min_lon": 7.30, "max_lon": 7.64}


def _in_bbox(lat: float, lon: float) -> bool:
    return (
        _BBOX["min_lat"] <= lat <= _BBOX["max_lat"]
        and _BBOX["min_lon"] <= lon <= _BBOX["max_lon"]
    )


class GtfsStaticConnector(BaseConnector):
    shape = ConnectorShape.REFERENCE
    source_name = "gtfs_nrw_static"

    async def fetch(self, checkpoint: dict[str, Any] | None = None) -> AsyncGenerator[Any, None]:
        resp = await self._get(_ZIP_URL)
        url = _ZIP_URL
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            # 1. Stops inside the Dortmund bbox.
            stop_ids: set[str] = set()
            stops = self._read_csv(zf, "stops.txt")
            for stop in stops:
                try:
                    lat = float(stop.get("stop_lat", ""))
                    lon = float(stop.get("stop_lon", ""))
                except (ValueError, TypeError):
                    continue
                if _in_bbox(lat, lon):
                    stop_ids.add(stop.get("stop_id", ""))
                    yield {"_kind": "stop", "_url": url, **stop}

            # 2. Stream stop_times → trips touching our stops (memory-bounded).
            trip_ids = self._trip_ids_for_stops(zf, stop_ids)
            # 3. trips → route_ids for those trips.
            route_ids = self._route_ids_for_trips(zf, trip_ids)
            # 4. Emit only routes in that set.
            for route in self._read_csv(zf, "routes.txt"):
                if route.get("route_id") in route_ids:
                    yield {"_kind": "route", "_url": url, **route}

    @staticmethod
    def _trip_ids_for_stops(zf: zipfile.ZipFile, stop_ids: set[str]) -> set[str]:
        if "stop_times.txt" not in zf.namelist() or not stop_ids:
            return set()
        trip_ids: set[str] = set()
        with zf.open("stop_times.txt") as raw:
            stream = io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace")
            reader = csv.DictReader(stream)
            for row in reader:
                if row.get("stop_id") in stop_ids:
                    trip_ids.add(row.get("trip_id", ""))
        return trip_ids

    @staticmethod
    def _route_ids_for_trips(zf: zipfile.ZipFile, trip_ids: set[str]) -> set[str]:
        if "trips.txt" not in zf.namelist() or not trip_ids:
            return set()
        route_ids: set[str] = set()
        with zf.open("trips.txt") as raw:
            stream = io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace")
            reader = csv.DictReader(stream)
            for row in reader:
                if row.get("trip_id") in trip_ids:
                    route_ids.add(row.get("route_id", ""))
        return route_ids

    @staticmethod
    def _read_csv(zf: zipfile.ZipFile, name: str) -> list[dict[str, str]]:
        if name not in zf.namelist():
            return []
        raw = zf.read(name).decode("utf-8-sig", errors="replace")
        return list(csv.DictReader(io.StringIO(raw)))

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        kind = raw.get("_kind")
        if kind == "stop":
            return {
                "kind": "stop",
                "source_id": f"stop:{raw.get('stop_id')}",
                "label": raw.get("stop_name") or "Haltestelle",
                "stop_code": raw.get("stop_code"),
                "platform_code": raw.get("platform_code"),
                "wheelchair_boarding": raw.get("wheelchair_boarding"),
                "lat": float(raw["stop_lat"]),
                "lon": float(raw["stop_lon"]),
                "source_url": raw.get("_url"),
            }
        return {
            "kind": "route",
            "source_id": f"route:{raw.get('route_id')}",
            "label": raw.get("route_long_name") or raw.get("route_short_name") or "Linie",
            "route_short_name": raw.get("route_short_name"),
            "route_type": raw.get("route_type"),
            "agency_id": raw.get("agency_id"),
            "source_url": raw.get("_url"),
        }

    async def emit_entities(self, normalized: dict[str, Any]) -> list[NodeBase]:
        prov = self._provenance(normalized["source_id"], normalized.get("source_url"))
        if normalized["kind"] == "stop":
            node: NodeBase = TransitStop(
                label=normalized["label"],
                geom={"type": "Point", "coordinates": [normalized["lon"], normalized["lat"]]},
                properties={
                    "stop_code": normalized["stop_code"],
                    "platform_code": normalized["platform_code"],
                    "wheelchair_boarding": normalized["wheelchair_boarding"],
                },
                **prov,
            )
        else:
            node = TransitRoute(
                label=normalized["label"],
                properties={
                    "route_short_name": normalized["route_short_name"],
                    "route_type": normalized["route_type"],
                    "agency_id": normalized["agency_id"],
                },
                **prov,
            )
        return [node]

    async def emit_edges(self, normalized: dict[str, Any], nodes: list[NodeBase]) -> list[EdgeBase]:
        # SERVES (route → stop) needs the stop_times join across both node sets,
        # resolved in the flow once stops+routes are loaded. None at item level.
        return []
