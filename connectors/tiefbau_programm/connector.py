"""
Connector: Tiefbauamt Arbeitsprogramm — Dortmund's PLANNED road/construction
measures (multi-year work programme), distinct from live Baustellen.

Source:  Dortmund Open Data Portal (ODS v2.1)
License: DL-DE-Zero · Shape: reference (yearly programme, slow refresh)

Why it matters: each measure carries a `drucksachennummer` (the council Drucksache
that authorised it) + a year-by-year phase timeline (Planung → Bau → Schlussrechnung)
+ a Stadtbezirk. That's the backbone of inefficiency detection — a planned repaving
vs a council resolution / bus-route decision on the same street & time window.
Emitted as planned ConstructionSite nodes; the drucksachennummer → Resolution link
and the Stadtbezirk → GeoArea link are resolved downstream.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, AsyncGenerator

from config import settings
from connectors.base import BaseConnector, ConnectorShape
from ontology.edges import EdgeBase
from ontology.nodes import ConstructionSite, NodeBase

_BASE = settings.opendata_dortmund_base_url
_DATASET = "baumassnahmen-arbeitsprogramm-des-tiefbauamtes-2026-gesamtubersicht"
_PROGRAM_YEAR = 2026
_PAGE_SIZE = 100
_PHASE_KEY = re.compile(r"^20\d\d(_[iv]+)?$")  # 2025, 2026_i, 2026_ii, 2027 …


def _source_id(projektnummer: str, bezeichnung: str) -> str:
    key = f"{_DATASET}|{projektnummer}|{bezeichnung}"
    return hashlib.sha256(key.encode()).hexdigest()[:20]


class TiefbauProgrammConnector(BaseConnector):
    shape = ConnectorShape.REFERENCE
    source_name = "opendata_dortmund_tiefbau_programm"

    async def fetch(self, checkpoint: dict[str, Any] | None = None) -> AsyncGenerator[Any, None]:
        offset = 0
        url = f"{_BASE}/catalog/datasets/{_DATASET}/records"
        while True:
            resp = await self._get(url, params={"limit": _PAGE_SIZE, "offset": offset})
            data = resp.json()
            results = data.get("results", [])
            for record in results:
                yield record
            if offset + _PAGE_SIZE >= data.get("total_count", 0) or not results:
                break
            offset += _PAGE_SIZE

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        bezeichnung = raw.get("bezeichnung") or "Unbenannte Maßnahme"
        projektnummer = str(raw.get("projektnummer") or "")
        # collapse the year/phase columns into an ordered timeline dict
        phasen = {k: raw[k] for k in raw if _PHASE_KEY.match(k) and raw.get(k)}
        link = raw.get("link_zur_ds_nr")
        return {
            "source_id": _source_id(projektnummer, bezeichnung),
            "label": bezeichnung,
            "projektnummer": projektnummer,
            "stadtbezirk": raw.get("stadtbezirk"),
            "gewerk": raw.get("gewerk"),
            "art": raw.get("art"),
            "status": raw.get("massnahmenstatus"),
            "prioritaetsklasse": raw.get("prioritatenklasse"),
            "drucksachennummer": raw.get("drucksachennummer"),
            "beschreibung": raw.get("beschreibung"),
            "bemerkungen": raw.get("bemerkungen"),
            "phasen": phasen,
            "source_url": link or f"https://open-data.dortmund.de/explore/dataset/{_DATASET}/",
        }

    async def emit_entities(self, normalized: dict[str, Any]) -> list[NodeBase]:
        return [
            ConstructionSite(
                label=normalized["label"],
                geom=None,
                properties={
                    "planned": True,
                    "program_year": _PROGRAM_YEAR,
                    "projektnummer": normalized["projektnummer"],
                    "stadtbezirk": normalized["stadtbezirk"],
                    "gewerk": normalized["gewerk"],
                    "art": normalized["art"],
                    "status": normalized["status"],
                    "prioritaetsklasse": normalized["prioritaetsklasse"],
                    "drucksachennummer": normalized["drucksachennummer"],
                    "beschreibung": normalized["beschreibung"],
                    "bemerkungen": normalized["bemerkungen"],
                    "phasen": normalized["phasen"],
                },
                **self._provenance(normalized["source_id"], normalized["source_url"]),
            )
        ]

    async def emit_edges(self, normalized: dict[str, Any], nodes: list[NodeBase]) -> list[EdgeBase]:
        # Stadtbezirk → GeoArea and drucksachennummer → Resolution links are
        # resolved downstream (text linker / resolution), not here.
        return []
