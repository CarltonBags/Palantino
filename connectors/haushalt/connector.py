"""
Connector: Dortmund municipal budget (Haushaltsplan) by Produktbereich.

Source:  Dortmund Open Data Portal (ODS v2.1) · License: DL-DE-Zero
Shape:   reference — yearly budget, full refresh

The money layer: per functional area (Produktbereich, e.g. "Verkehrsflächen und
-anlagen") the income, expense, surplus/deficit and investment amounts. Lets the
reasoner put spend next to the works and tenders in that domain — the missing side
of the decision → tender → spend triangle. Structured on ODS, so no PDF parsing.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from config import settings
from connectors.base import BaseConnector, ConnectorShape
from ontology.edges import EdgeBase
from ontology.nodes import BudgetItem, NodeBase

_BASE = settings.opendata_dortmund_base_url

# dataset_id -> budget year (one dataset per year).
_DATASETS = {
    "fb20-haushaltsplan-uebersicht-produktbereiche-2021": 2021,
}
_PAGE_SIZE = 100


def _to_num(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _make_source_id(dataset: str, produkt_bereich: str) -> str:
    return hashlib.sha256(f"{dataset}|{produkt_bereich}".encode()).hexdigest()[:20]


def _source_url(dataset: str) -> str:
    return f"https://open-data.dortmund.de/explore/dataset/{dataset}/"


class HaushaltConnector(BaseConnector):
    shape = ConnectorShape.REFERENCE
    source_name = "opendata_dortmund_haushalt"

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
        pb = str(raw.get("produkt_bereich") or "")
        bez = raw.get("bezeichnung") or "Unbenannt"
        return {
            "source_id": _make_source_id(dataset, pb),
            "label": f"{bez} (Haushalt {year})",
            "valid_from": datetime(year, 1, 1, tzinfo=timezone.utc) if year else None,
            "dataset": dataset,
            "produkt_bereich": pb,
            "bezeichnung": bez,
            "year": year,
            "ertraege_eur": _to_num(raw.get("ordentliche_ertrage_inkl_finanzertrage")),
            "aufwendungen_eur": _to_num(
                raw.get("ordentliche_aufwendungen_inkl_zinsen_u_sonst_finanzaufw")
            ),
            "saldo_eur": _to_num(raw.get("uberschuss_fehlbedarf")),
            "investive_einzahlungen_eur": _to_num(raw.get("investive_einzahlungen")),
            "investive_auszahlungen_eur": _to_num(raw.get("investive_auszahlungen")),
            "saldo_investition_eur": _to_num(raw.get("saldo_investitionstatigkeit")),
        }

    async def emit_entities(self, normalized: dict[str, Any]) -> list[NodeBase]:
        prov = self._provenance(normalized["source_id"], _source_url(normalized["dataset"]))
        return [
            BudgetItem(
                label=normalized["label"],
                valid_from=normalized["valid_from"],
                properties={
                    "produkt_bereich": normalized["produkt_bereich"],
                    "bezeichnung": normalized["bezeichnung"],
                    "year": normalized["year"],
                    "ertraege_eur": normalized["ertraege_eur"],
                    "aufwendungen_eur": normalized["aufwendungen_eur"],
                    "saldo_eur": normalized["saldo_eur"],
                    "investive_einzahlungen_eur": normalized["investive_einzahlungen_eur"],
                    "investive_auszahlungen_eur": normalized["investive_auszahlungen_eur"],
                    "saldo_investition_eur": normalized["saldo_investition_eur"],
                },
                **prov,
            )
        ]

    async def emit_edges(self, normalized: dict[str, Any], nodes: list[NodeBase]) -> list[EdgeBase]:
        return []
