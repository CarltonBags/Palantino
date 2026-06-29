"""
Connector: Dortmund civic POIs (XErleben) — schools first.

Source:  Dortmund Open Data Portal — XErleben "Orte von Interesse" datasets
API:     ODS v2.1
License: DL-DE-Zero
Shape:   reference — slow full refresh + diff

What this covers:
  - Civic facilities (schools, libraries, pools, kitas) with coords, address,
    Stadtbezirk + statistischer Bezirk codes.
Each dataset shares the XErleben schema (objektname / objektart / strasse /
geo_point_2d / stadtbezbe). Add more dataset IDs to _DATASETS to extend.
"""

from __future__ import annotations

import hashlib
from typing import Any, AsyncGenerator

from config import settings
from connectors.base import BaseConnector, ConnectorShape
from ontology.edges import EdgeBase
from ontology.nodes import POI, NodeBase

_BASE = settings.opendata_dortmund_base_url

# XErleben POI datasets sharing the same schema. Extend as more are scoped in.
_DATASETS = {
    "schulen": "school",
}
_PAGE_SIZE = 100


def _make_source_id(dataset: str, name: str, lon: float | None, lat: float | None) -> str:
    key = f"{dataset}|{name}|{lon}|{lat}"
    return hashlib.sha256(key.encode()).hexdigest()[:20]


def _source_url(dataset: str) -> str:
    return f"https://open-data.dortmund.de/explore/dataset/{dataset}/"


class OdsPoisConnector(BaseConnector):
    shape = ConnectorShape.REFERENCE
    source_name = "opendata_dortmund_xerleben"

    async def fetch(self, checkpoint: dict[str, Any] | None = None) -> AsyncGenerator[Any, None]:
        for dataset, poi_type in _DATASETS.items():
            offset = 0
            records_url = f"{_BASE}/catalog/datasets/{dataset}/records"
            while True:
                resp = await self._get(records_url, params={"limit": _PAGE_SIZE, "offset": offset})
                data = resp.json()
                results = data.get("results", [])
                for record in results:
                    record["_dataset"] = dataset
                    record["_poi_type"] = poi_type
                    yield record
                if offset + _PAGE_SIZE >= data.get("total_count", 0):
                    break
                offset += _PAGE_SIZE

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        dataset = raw.get("_dataset", "")
        geo = raw.get("geo_point_2d") or {}
        lon = geo.get("lon")
        lat = geo.get("lat")
        name = raw.get("objektname") or "Unbenannt"

        return {
            "source_id": _make_source_id(dataset, name, lon, lat),
            "label": name,
            "dataset": dataset,
            "poi_type": raw.get("_poi_type"),
            "objektart": raw.get("objektart"),
            "addr_street": raw.get("strasse"),
            "addr_housenumber": raw.get("hausnummer"),
            "stadtbezirk": raw.get("stadtbezbe"),
            "stat_bezirk": raw.get("statbezibe"),
            "website": raw.get("link"),
            "lon": lon,
            "lat": lat,
        }

    async def emit_entities(self, normalized: dict[str, Any]) -> list[NodeBase]:
        prov = self._provenance(normalized["source_id"], _source_url(normalized["dataset"]))
        geom = None
        if normalized["lon"] is not None and normalized["lat"] is not None:
            geom = {"type": "Point", "coordinates": [normalized["lon"], normalized["lat"]]}
        node = POI(
            label=normalized["label"],
            geom=geom,
            properties={
                "poi_type": normalized["poi_type"],
                "objektart": normalized["objektart"],
                "addr_street": normalized["addr_street"],
                "addr_housenumber": normalized["addr_housenumber"],
                "stadtbezirk": normalized["stadtbezirk"],
                "stat_bezirk": normalized["stat_bezirk"],
                "website": normalized["website"],
            },
            **prov,
        )
        return [node]

    async def emit_edges(self, normalized: dict[str, Any], nodes: list[NodeBase]) -> list[EdgeBase]:
        # LOCATED_IN resolved by PostGIS spatial join in the flow.
        return []
