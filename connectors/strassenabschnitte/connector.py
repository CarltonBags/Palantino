"""
Connector: Dortmund street segments with geometry (Straßenabschnitte).

Source:  Dortmund Open Data Portal — dataset fb62-strassenabschnitte
API:     ODS v2.1
License: DL-DE-Zero
Shape:   reference — ~19.5k segments, slow full refresh

What this covers:
  - Road nodes at SEGMENT granularity WITH LineString geometry (the drawable
    road layer + the basis for spatial joins). Carries street name, key, segment
    number, length, and road class.
Relationship to the `strassen` connector:
  - `strassen` = one Road per street name (the text-linker gazetteer, no geom).
  - this = many geometric segments per street. Different source, so the two never
    collide on (source, source_id). The flow links each segment to its parent
    street (by Straßenschlüssel) and to its statistischer Bezirk (by ST_Within).
"""

from __future__ import annotations

from typing import Any, AsyncGenerator

from config import settings
from connectors.base import BaseConnector, ConnectorShape
from ontology.edges import EdgeBase
from ontology.nodes import NodeBase, Road

_BASE = settings.opendata_dortmund_base_url
_DATASET = "fb62-strassenabschnitte"
_SOURCE_URL = f"https://open-data.dortmund.de/explore/dataset/{_DATASET}/"
_RECORDS_URL = f"{_BASE}/catalog/datasets/{_DATASET}/records"
_PAGE_SIZE = 100


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _geometry(geo_shape: dict[str, Any] | None) -> dict[str, Any] | None:
    """ODS geo_shape is a GeoJSON Feature; pull the inner geometry."""
    if not geo_shape:
        return None
    geom = geo_shape.get("geometry", geo_shape)
    if geom.get("type") and geom.get("coordinates"):
        return {"type": geom["type"], "coordinates": geom["coordinates"]}
    return None


class StrassenabschnitteConnector(BaseConnector):
    shape = ConnectorShape.REFERENCE
    source_name = "opendata_dortmund_strassenabschnitte"

    async def fetch(self, checkpoint: dict[str, Any] | None = None) -> AsyncGenerator[Any, None]:
        offset = 0
        while True:
            resp = await self._get(_RECORDS_URL, params={"limit": _PAGE_SIZE, "offset": offset})
            data = resp.json()
            for record in data.get("results", []):
                yield record
            if offset + _PAGE_SIZE >= data.get("total_count", 0):
                break
            offset += _PAGE_SIZE

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        name = raw.get("strassenname") or "Unbenannter Abschnitt"
        schluessel = str(raw.get("strassenschlussel") or "")
        abschnitt = str(raw.get("strassenabschnittsnummer") or "")
        return {
            "source_id": f"{schluessel}-{abschnitt}",
            "label": name,
            "name_de": name,
            "strassenschlussel": schluessel,
            "abschnitt": abschnitt,
            "length_m": _to_int(raw.get("lange_strassenabschnitt_m")),
            "strassenklasse": raw.get("strassenklasse"),
            "strassengruppe": raw.get("strassengruppe"),
            "geom": _geometry(raw.get("geo_shape")),
        }

    async def emit_entities(self, normalized: dict[str, Any]) -> list[NodeBase]:
        prov = self._provenance(normalized["source_id"], _SOURCE_URL)
        node = Road(
            label=normalized["label"],
            geom=normalized["geom"],
            properties={
                "road_type": normalized["strassenklasse"] or "segment",
                "name_de": normalized["name_de"],
                "strassenschlussel": normalized["strassenschlussel"],
                "abschnitt": normalized["abschnitt"],
                "length_m": normalized["length_m"],
                "strassengruppe": normalized["strassengruppe"],
            },
            **prov,
        )
        return [node]

    async def emit_edges(self, normalized: dict[str, Any], nodes: list[NodeBase]) -> list[EdgeBase]:
        # PART_OF (segment → parent street) and LOCATED_IN (segment →
        # statistischer Bezirk) are resolved by joins in the flow.
        return []
