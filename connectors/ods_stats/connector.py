"""
Connector: Dortmund demographics (population / age structure by Stadtbezirk).

Source:  Dortmund Open Data Portal — statistics datasets
API:     ODS v2.1
License: DL-DE-Zero
Shape:   reference — yearly datasets, full refresh + diff

What this covers:
  - Per-Stadtbezirk age structure (average age, minor/elderly share) for a year.
Design note:
  - The ontology has no Demographic node (adding one is a separate ontology PR —
    see CLAUDE.md). We emit a GeoArea snapshot carrying the stats as properties,
    keyed by Stadtbezirk + year, with valid_from = Jan 1 of that year. Entity
    resolution links these to the geographic-spine Stadtbezirk via SAME_AS.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from config import settings
from connectors.base import BaseConnector, ConnectorShape
from ontology.edges import EdgeBase
from ontology.nodes import GeoArea, NodeBase

_BASE = settings.opendata_dortmund_base_url

# dataset_id -> reference year. Age-structure series, one dataset per year.
_DATASETS = {
    "hauptwohnbevolkerung-der-stadtbezirke-masse-der-altersstruktur-2024": 2024,
}
_PAGE_SIZE = 100


def _make_source_id(dataset: str, stadtbezirk_nr: str) -> str:
    key = f"{dataset}|{stadtbezirk_nr}"
    return hashlib.sha256(key.encode()).hexdigest()[:20]


def _source_url(dataset: str) -> str:
    return f"https://open-data.dortmund.de/explore/dataset/{dataset}/"


class OdsStatsConnector(BaseConnector):
    shape = ConnectorShape.REFERENCE
    source_name = "opendata_dortmund_demographics"

    async def fetch(self, checkpoint: dict[str, Any] | None = None) -> AsyncGenerator[Any, None]:
        for dataset, year in _DATASETS.items():
            offset = 0
            records_url = f"{_BASE}/catalog/datasets/{dataset}/records"
            while True:
                resp = await self._get(records_url, params={"limit": _PAGE_SIZE, "offset": offset})
                data = resp.json()
                results = data.get("results", [])
                for record in results:
                    record["_dataset"] = dataset
                    record["_year"] = year
                    yield record
                if offset + _PAGE_SIZE >= data.get("total_count", 0):
                    break
                offset += _PAGE_SIZE

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        dataset = raw.get("_dataset", "")
        year = raw.get("_year")
        stadtbezirk = raw.get("stadtbezirk") or "Unbekannt"
        nr = str(raw.get("stadtbezirk_nr") or "")

        return {
            "source_id": _make_source_id(dataset, nr),
            "label": f"{stadtbezirk} Demografie {year}",
            "valid_from": datetime(year, 1, 1, tzinfo=timezone.utc) if year else None,
            "dataset": dataset,
            "year": year,
            "stadtbezirk": stadtbezirk,
            "stadtbezirk_nr": nr,
            "durchschnittsalter": raw.get("durchschnittsalter"),
            "minderjahrigenanteil": raw.get("minderjahrigenanteil"),
            "altenanteil": raw.get("altenanteil"),
            "hochbetagtenanteil": raw.get("hochbetagtenanteil"),
        }

    async def emit_entities(self, normalized: dict[str, Any]) -> list[NodeBase]:
        prov = self._provenance(normalized["source_id"], _source_url(normalized["dataset"]))
        node = GeoArea(
            label=normalized["label"],
            valid_from=normalized["valid_from"],
            properties={
                "area_type": "stadtbezirk_demographics",
                "stadtbezirk": normalized["stadtbezirk"],
                "stadtbezirk_nr": normalized["stadtbezirk_nr"],
                "year": normalized["year"],
                "durchschnittsalter": normalized["durchschnittsalter"],
                "minderjahrigenanteil": normalized["minderjahrigenanteil"],
                "altenanteil": normalized["altenanteil"],
                "hochbetagtenanteil": normalized["hochbetagtenanteil"],
            },
            **prov,
        )
        return [node]

    async def emit_edges(self, normalized: dict[str, Any], nodes: list[NodeBase]) -> list[EdgeBase]:
        # SAME_AS to spine Stadtbezirk handled by the resolution layer.
        return []
