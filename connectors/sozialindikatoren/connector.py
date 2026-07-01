"""
Connector: Dortmund social indicators by Stadtbezirk (welfare / social-benefit /
old-age basic-security rates).

Source:  Dortmund Open Data Portal (ODS v2.1) · License: DL-DE-Zero
Shape:   reference — yearly datasets, full refresh + diff

Why it matters: district-level social need. Lets the reasoner WEIGHT synergies and
inefficiencies by context — e.g. an untapped job-training synergy in a high-welfare
district carries more impact; a service cut in a high-need Bezirk is a stronger
inefficiency signal.

Design (mirrors ods_stats): no Statistic node type in the ontology, so emit a
GeoArea snapshot per Stadtbezirk+year carrying the rates as properties, keyed by
Bezirk-Nr + year, valid_from = Jan 1. The resolution layer SAME_AS-links these to
the geographic-spine Stadtbezirk.
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

# dataset_id -> reference year (one dataset per year).
_DATASETS = {
    "sozialindikatoren-in-den-stadtbezirken-2023": 2023,
    "sozialindikatoren-in-den-stadtbezirken": 2022,
}
_PAGE_SIZE = 100


def _make_source_id(dataset: str, stadtbezirk_nr: str) -> str:
    return hashlib.sha256(f"{dataset}|{stadtbezirk_nr}".encode()).hexdigest()[:20]


def _source_url(dataset: str) -> str:
    return f"https://open-data.dortmund.de/explore/dataset/{dataset}/"


class SozialindikatorenConnector(BaseConnector):
    shape = ConnectorShape.REFERENCE
    source_name = "opendata_dortmund_sozialindikatoren"

    async def fetch(self, checkpoint: dict[str, Any] | None = None) -> AsyncGenerator[Any, None]:
        for dataset, year in _DATASETS.items():
            offset = 0
            url = f"{_BASE}/catalog/datasets/{dataset}/records"
            while True:
                resp = await self._get(url, params={"limit": _PAGE_SIZE, "offset": offset})
                data = resp.json()
                results = data.get("results", [])
                for record in results:
                    record["_dataset"] = dataset
                    record["_year"] = year
                    yield record
                if offset + _PAGE_SIZE >= data.get("total_count", 0) or not results:
                    break
                offset += _PAGE_SIZE

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        dataset = raw.get("_dataset", "")
        year = raw.get("_year")
        stadtbezirk = raw.get("stadtbezirk") or "Unbekannt"
        nr = str(raw.get("stadtbezirk_nr") or "")
        return {
            "source_id": _make_source_id(dataset, nr),
            "label": f"{stadtbezirk} Sozialindikatoren {year}",
            "valid_from": datetime(year, 1, 1, tzinfo=timezone.utc) if year else None,
            "dataset": dataset,
            "year": year,
            "stadtbezirk": stadtbezirk,
            "stadtbezirk_nr": nr,
            "leistungen_lebensunterhalt_quote": raw.get(
                "quote_leistungen_zur_sicherung_des_lebensunterhalts"
            ),
            "sozialgeldquote": raw.get("sozialgeldquote"),
            "grundsicherung_im_alter_quote": raw.get("grundsicherung_im_alter_quote"),
        }

    async def emit_entities(self, normalized: dict[str, Any]) -> list[NodeBase]:
        prov = self._provenance(normalized["source_id"], _source_url(normalized["dataset"]))
        return [
            GeoArea(
                label=normalized["label"],
                valid_from=normalized["valid_from"],
                properties={
                    "area_type": "stadtbezirk_sozialindikatoren",
                    "stadtbezirk": normalized["stadtbezirk"],
                    "stadtbezirk_nr": normalized["stadtbezirk_nr"],
                    "year": normalized["year"],
                    "leistungen_lebensunterhalt_quote": normalized["leistungen_lebensunterhalt_quote"],
                    "sozialgeldquote": normalized["sozialgeldquote"],
                    "grundsicherung_im_alter_quote": normalized["grundsicherung_im_alter_quote"],
                },
                **prov,
            )
        ]

    async def emit_edges(self, normalized: dict[str, Any], nodes: list[NodeBase]) -> list[EdgeBase]:
        # SAME_AS to the spine Stadtbezirk handled by the resolution layer.
        return []
