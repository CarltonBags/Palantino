"""
Connector: Dortmund streets (Straßenverzeichnis with districts).

Source:  Dortmund Open Data Portal — dataset fb62-strassen-statistische-bezirke
API:     ODS v2.1
License: DL-DE-Zero
Shape:   reference — full street register (~4700 rows), slow refresh

What this covers:
  - Road nodes: street name + Straßenschlüssel (stable city key) + the
    statistischer Bezirk and Stadtbezirk the street sits in.
  - No geometry in this dataset (name + admin assignment only). It is primarily
    the street GAZETTEER that lets the text linker match street mentions in
    council minutes / tenders / police reports, and the per-street district
    assignment lets the flow attach each Road to its statistischer Bezirk.
"""

from __future__ import annotations

from typing import Any, AsyncGenerator

from config import settings
from connectors.base import BaseConnector, ConnectorShape
from ontology.edges import EdgeBase
from ontology.nodes import NodeBase, Road

_BASE = settings.opendata_dortmund_base_url
_DATASET = "fb62-strassen-statistische-bezirke"
_SOURCE_URL = f"https://open-data.dortmund.de/explore/dataset/{_DATASET}/"
_RECORDS_URL = f"{_BASE}/catalog/datasets/{_DATASET}/records"
_PAGE_SIZE = 100


class StrassenConnector(BaseConnector):
    shape = ConnectorShape.REFERENCE
    source_name = "opendata_dortmund_strassen"

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
        name = raw.get("strassenname") or "Unbenannte Straße"
        schluessel = str(raw.get("strassenschlussel") or "")
        return {
            "source_id": schluessel or name,
            "label": name,
            "name_de": name,
            "strassenschlussel": schluessel,
            "stat_bezirk_nr": raw.get("statistischer_bezirk_nr"),
            "stat_bezirk": raw.get("statistischer_bezirk_bezeichnung"),
            "stadtbezirk_nr": raw.get("stadtbezirk_nr"),
            "stadtbezirk": raw.get("stadtbezirk_bezeichnung"),
        }

    async def emit_entities(self, normalized: dict[str, Any]) -> list[NodeBase]:
        prov = self._provenance(normalized["source_id"], _SOURCE_URL)
        node = Road(
            label=normalized["label"],
            properties={
                "road_type": "street",
                "name_de": normalized["name_de"],
                "strassenschlussel": normalized["strassenschlussel"],
                "stat_bezirk_nr": normalized["stat_bezirk_nr"],
                "stat_bezirk": normalized["stat_bezirk"],
                "stadtbezirk_nr": normalized["stadtbezirk_nr"],
                "stadtbezirk": normalized["stadtbezirk"],
            },
            **prov,
        )
        return [node]

    async def emit_edges(self, normalized: dict[str, Any], nodes: list[NodeBase]) -> list[EdgeBase]:
        # Road → statistischer Bezirk PART_OF resolved by a name join in the flow
        # (the district name lives in properties; the GeoArea must already exist).
        return []
