"""
Geographic spine connector — Dortmund Open Data Portal (OpenDataSoft).

Pulls:
  - Stadtbezirke (city districts, 12 in total)
  - Statistische Bezirke (statistical sub-districts, ~170)
  - Straßen (road network geometry, dataset fb62-strassen)

Shape: reference (monthly full refresh + diff)
License: DL-DE-Zero (attribution appreciated, no other restriction)
robots.txt: open-data.dortmund.de allows crawling
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncGenerator

from config import settings
from connectors.base import BaseConnector, ConnectorShape
from ontology.edges import EdgeBase, part_of
from ontology.nodes import GeoArea, NodeBase, Road

logger = logging.getLogger(__name__)

BASE = settings.opendata_dortmund_base_url

# Dataset IDs on the OpenDataSoft platform (v2.1, flat records).
# Roads have dedicated connectors (strassen / strassenabschnitte); the spine is
# just the two area layers.
DATASETS = {
    "stadtbezirke": "fb62-stadtbezirke",
    "stat_bezirke": "fb62-statistischebezirke",
}
_PAGE_SIZE = 100


def _geometry(feature: dict[str, Any] | None) -> dict[str, Any] | None:
    """ODS `geografische_polygone` is a GeoJSON Feature; pull the inner geometry."""
    if not feature:
        return None
    geom = feature.get("geometry", feature)
    if geom.get("type") and geom.get("coordinates"):
        return {"type": geom["type"], "coordinates": geom["coordinates"]}
    return None


class GeoSpineConnector(BaseConnector):
    shape = ConnectorShape.REFERENCE
    source_name = "opendata_dortmund_geo"

    async def fetch(self, checkpoint: dict[str, Any] | None = None) -> AsyncGenerator[Any, None]:
        for dataset_key, dataset_id in DATASETS.items():
            offset = 0
            while True:
                resp = await self._get(
                    f"{BASE}/catalog/datasets/{dataset_id}/records",
                    params={"limit": _PAGE_SIZE, "offset": offset},
                )
                data = resp.json()
                records = data.get("results", [])
                if not records:
                    break
                for rec in records:
                    yield {"dataset": dataset_key, "record": rec}
                if offset + _PAGE_SIZE >= data.get("total_count", 0):
                    break
                offset += _PAGE_SIZE

    def normalize(self, raw: Any) -> dict[str, Any]:
        dataset = raw["dataset"]
        rec = raw["record"]
        geom = _geometry(rec.get("geografische_polygone"))

        if dataset == "stadtbezirke":
            return {
                "dataset": dataset,
                "area_type": "stadtbezirk",
                "source_id": str(rec.get("stadtbezirk_nr", "")),
                "label": rec.get("stadtbezirk_bezeichnung", "Unknown"),
                "ags": rec.get("ags"),
                "geom": geom,
                "source_url": f"https://open-data.dortmund.de/explore/dataset/{DATASETS['stadtbezirke']}/",
                "parent_id": None,
            }

        if dataset == "stat_bezirke":
            return {
                "dataset": dataset,
                "area_type": "statistischer_bezirk",
                "source_id": str(rec.get("statistischer_bezirk_nr", "")),
                "label": rec.get("statistischer_bezirk", "Unknown"),
                "parent_source_id": str(rec.get("stadtbezirk_nr", "")),
                "geom": geom,
                "source_url": f"https://open-data.dortmund.de/explore/dataset/{DATASETS['stat_bezirke']}/",
            }

        return {"dataset": dataset, "raw": raw}

    async def emit_entities(self, normalized: dict[str, Any]) -> list[NodeBase]:
        dataset = normalized["dataset"]

        if dataset in ("stadtbezirke", "stat_bezirke"):
            props: dict[str, Any] = {
                "area_type": normalized.get("area_type"),
                "ags": normalized.get("ags"),
            }
            # Carry the parent Stadtbezirk key so the flow can resolve PART_OF
            # once both layers are loaded (link_stat_to_stadt reads this).
            parent_source_id = normalized.get("parent_source_id")
            if parent_source_id:
                props["_parent_source_id"] = parent_source_id
            return [
                GeoArea(
                    label=normalized["label"],
                    properties=props,
                    geom=normalized.get("geom"),
                    **self._provenance(normalized["source_id"], normalized.get("source_url")),
                )
            ]

        if dataset == "strassen":
            return [
                Road(
                    label=normalized["label"],
                    properties={
                        "road_type": normalized.get("road_type"),
                        "length_m": normalized.get("length_m"),
                    },
                    geom=normalized.get("geom"),
                    **self._provenance(normalized["source_id"], normalized.get("source_url")),
                )
            ]

        return []

    async def emit_edges(self, normalized: dict[str, Any], nodes: list[NodeBase]) -> list[EdgeBase]:
        # PART_OF (statistischer Bezirk → Stadtbezirk) is resolved by the flow via
        # link_stat_to_stadt once both layers are loaded; the parent key now rides
        # on the node's properties (set in emit_entities).
        return []

    async def link_stat_to_stadt(
        self,
        stat_node: GeoArea,
        stadtbezirk_map: dict[str, GeoArea],
    ) -> list[EdgeBase]:
        """Resolve PART_OF edges after both layers are loaded."""
        parent_source_id = stat_node.properties.get("_parent_source_id", "")
        parent = stadtbezirk_map.get(parent_source_id)
        if parent is None:
            logger.warning(
                "stat_bezirk %s: no parent stadtbezirk %s found",
                stat_node.source_id,
                parent_source_id,
            )
            return []
        return [
            part_of(
                child_id=stat_node.id,
                parent_id=parent.id,
                source=self.source_name,
                source_url=stat_node.source_url,
                observed_at=self._now(),
            )
        ]
