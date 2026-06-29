"""
OSM Overpass POI connector — businesses and points-of-interest in Dortmund.

Queries Dortmund admin boundary (relation 62571) for all nodes/ways with
shop, amenity, office, leisure, or tourism tags.

Shape: reference (weekly full refresh)
License: ODbL — attribution required + share-alike
robots.txt: overpass-api.de allows crawling; rate-limit respected
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, AsyncGenerator

from config import settings
from connectors.base import BaseConnector, ConnectorShape
from ontology.edges import EdgeBase, located_in
from ontology.nodes import NodeBase, POI

logger = logging.getLogger(__name__)

OVERPASS_URL = settings.overpass_api_url

# Dortmund admin boundary relation ID (OSM relation 1829065, admin_level 6,
# wikidata Q1295). NB: 62571 is Landkreis Oberspreewald-Lausitz — wrong city.
DORTMUND_RELATION = 1829065
# Overpass area id for a relation = 3600000000 + relation_id. NOT a "3600"
# string prefix — that drops the zero-padding (→ 360062571), a nonexistent area
# that returns 0 elements with no error.
DORTMUND_AREA_ID = 3600000000 + DORTMUND_RELATION

# Overpass QL query — nodes and ways with business/POI tags inside Dortmund
OVERPASS_QUERY = f"""
[out:json][timeout:120];
area({DORTMUND_AREA_ID})->.dortmund;
(
  node["shop"](area.dortmund);
  node["amenity"](area.dortmund);
  node["office"](area.dortmund);
  node["leisure"](area.dortmund);
  node["tourism"](area.dortmund);
  way["shop"](area.dortmund);
  way["amenity"](area.dortmund);
  way["office"](area.dortmund);
);
out center tags;
"""


class OverpassConnector(BaseConnector):
    shape = ConnectorShape.REFERENCE
    source_name = "osm_overpass"

    async def fetch(self, checkpoint: dict[str, Any] | None = None) -> AsyncGenerator[Any, None]:
        resp = await self._post(
            OVERPASS_URL,
            data={"data": OVERPASS_QUERY},
            timeout=180.0,
        )
        data = resp.json()
        for element in data.get("elements", []):
            yield element

    def normalize(self, raw: Any) -> dict[str, Any]:
        tags = raw.get("tags", {})
        osm_type = raw.get("type", "node")  # node or way
        osm_id = raw.get("id", 0)

        # Ways return a center point; nodes have lat/lon directly
        if osm_type == "way":
            center = raw.get("center", {})
            lat = center.get("lat")
            lon = center.get("lon")
        else:
            lat = raw.get("lat")
            lon = raw.get("lon")

        label = (
            tags.get("name")
            or tags.get("brand")
            or tags.get("operator")
            or f"OSM {osm_type} {osm_id}"
        )

        return {
            "source_id": f"{osm_type}/{osm_id}",
            "label": label,
            "lat": lat,
            "lon": lon,
            "amenity": tags.get("amenity"),
            "shop": tags.get("shop"),
            "office": tags.get("office"),
            "leisure": tags.get("leisure"),
            "tourism": tags.get("tourism"),
            "opening_hours": tags.get("opening_hours"),
            "phone": tags.get("phone") or tags.get("contact:phone"),
            "website": tags.get("website") or tags.get("contact:website"),
            "addr_street": tags.get("addr:street"),
            "addr_housenumber": tags.get("addr:housenumber"),
            "addr_postcode": tags.get("addr:postcode"),
            "source_url": f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
        }

    async def emit_entities(self, normalized: dict[str, Any]) -> list[NodeBase]:
        lat = normalized.get("lat")
        lon = normalized.get("lon")
        geom: dict[str, Any] | None = None
        if lat is not None and lon is not None:
            geom = {"type": "Point", "coordinates": [lon, lat]}

        return [
            POI(
                label=normalized["label"],
                geom=geom,
                properties={
                    "amenity": normalized.get("amenity"),
                    "shop": normalized.get("shop"),
                    "office": normalized.get("office"),
                    "leisure": normalized.get("leisure"),
                    "tourism": normalized.get("tourism"),
                    "opening_hours": normalized.get("opening_hours"),
                    "phone": normalized.get("phone"),
                    "website": normalized.get("website"),
                    "addr_street": normalized.get("addr_street"),
                    "addr_housenumber": normalized.get("addr_housenumber"),
                    "addr_postcode": normalized.get("addr_postcode"),
                },
                **self._provenance(normalized["source_id"], normalized["source_url"]),
            )
        ]

    async def emit_edges(self, normalized: dict[str, Any], nodes: list[NodeBase]) -> list[EdgeBase]:
        # LOCATED_IN edges are resolved in the flow by spatially joining POI geometry
        # to loaded GeoArea nodes via PostGIS ST_Within. No edges emitted here.
        return []
