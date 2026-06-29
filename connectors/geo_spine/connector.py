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

# Dataset IDs on the OpenDataSoft platform
DATASETS = {
    "stadtbezirke": "stadtbezirke-dortmund",
    "stat_bezirke": "statistische-bezirke-dortmund",
    "strassen": "fb62-strassen",
}


class GeoSpineConnector(BaseConnector):
    shape = ConnectorShape.REFERENCE
    source_name = "opendata_dortmund_geo"

    async def fetch(self, checkpoint: dict[str, Any] | None = None) -> AsyncGenerator[Any, None]:
        for dataset_key, dataset_id in DATASETS.items():
            offset = 0
            limit = 100
            while True:
                resp = await self._get(
                    f"{BASE}/catalog/datasets/{dataset_id}/records",
                    params={
                        "limit": limit,
                        "offset": offset,
                        "select": "*",
                    },
                )
                data = resp.json()
                records = data.get("results", [])
                if not records:
                    break
                for rec in records:
                    yield {"dataset": dataset_key, "record": rec}
                if len(records) < limit:
                    break
                offset += limit

    def normalize(self, raw: Any) -> dict[str, Any]:
        dataset = raw["dataset"]
        rec = raw["record"]
        fields = rec.get("fields", rec)

        if dataset == "stadtbezirke":
            return {
                "dataset": dataset,
                "area_type": "stadtbezirk",
                "source_id": str(fields.get("stadtbezirk_nr", fields.get("nr", ""))),
                "label": fields.get("stadtbezirk_name", fields.get("name", "Unknown")),
                "ags": fields.get("ags"),
                "geom": rec.get("geo_shape") or fields.get("geo_shape"),
                "source_url": f"https://open-data.dortmund.de/explore/dataset/{DATASETS['stadtbezirke']}/",
                "parent_id": None,
            }

        if dataset == "stat_bezirke":
            return {
                "dataset": dataset,
                "area_type": "statistischer_bezirk",
                "source_id": str(fields.get("stat_bezirk_nr", fields.get("nr", ""))),
                "label": fields.get("stat_bezirk_name", fields.get("name", "Unknown")),
                "parent_source_id": str(fields.get("stadtbezirk_nr", "")),
                "geom": rec.get("geo_shape") or fields.get("geo_shape"),
                "source_url": f"https://open-data.dortmund.de/explore/dataset/{DATASETS['stat_bezirke']}/",
            }

        if dataset == "strassen":
            return {
                "dataset": dataset,
                "source_id": str(fields.get("strassenid", fields.get("id", ""))),
                "label": fields.get("strassenname", fields.get("name", "Unknown")),
                "road_type": fields.get("strassenklasse", fields.get("klasse")),
                "length_m": fields.get("laenge_m"),
                "geom": rec.get("geo_shape") or fields.get("geo_shape"),
                "source_url": f"https://open-data.dortmund.de/explore/dataset/{DATASETS['strassen']}/",
            }

        return {"dataset": dataset, "raw": raw}

    async def emit_entities(self, normalized: dict[str, Any]) -> list[NodeBase]:
        dataset = normalized["dataset"]

        if dataset in ("stadtbezirke", "stat_bezirke"):
            return [
                GeoArea(
                    label=normalized["label"],
                    properties={
                        "area_type": normalized.get("area_type"),
                        "ags": normalized.get("ags"),
                    },
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
        edges: list[EdgeBase] = []

        if normalized["dataset"] == "stat_bezirke" and nodes:
            stat_bezirk = nodes[0]
            parent_source_id = normalized.get("parent_source_id", "")
            if parent_source_id:
                # Edge will be finalized in the flow once parent nodes are written.
                # Store the parent source_id in properties so the flow can resolve it.
                stat_bezirk.properties["_parent_source_id"] = parent_source_id

        return edges

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
