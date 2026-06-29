"""
Connector: Dortmund construction sites (Baustellen tagesaktuell).

Source:  Dortmund Open Data Portal — dataset fb66-baustellen-tagesaktuell
API:     ODS v2.1
License: DL-DE-Zero
Shape:   snapshot — full refresh, ~100 records, daily cadence

What this covers:
  - Active roadworks with coordinates, type, owner, time window, Stadtbezirk.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from config import settings
from connectors.base import BaseConnector, ConnectorShape
from ontology.edges import EdgeBase
from ontology.nodes import ConstructionSite, NodeBase

_BASE = settings.opendata_dortmund_base_url
_DATASET = "fb66-baustellen-tagesaktuell"
_SOURCE_URL = f"https://open-data.dortmund.de/explore/dataset/{_DATASET}/"
_RECORDS_URL = f"{_BASE}/catalog/datasets/{_DATASET}/records"
_PAGE_SIZE = 100


def _make_source_id(art: str, von: str, lon: float | None, lat: float | None) -> str:
    key = f"{art}|{von}|{lon}|{lat}"
    return hashlib.sha256(key.encode()).hexdigest()[:20]


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


class BaustellenConnector(BaseConnector):
    shape = ConnectorShape.SNAPSHOT
    source_name = "opendata_dortmund_baustellen"

    async def fetch(self, checkpoint: dict[str, Any] | None = None) -> AsyncGenerator[Any, None]:
        offset = 0
        while True:
            resp = await self._get(_RECORDS_URL, params={"limit": _PAGE_SIZE, "offset": offset})
            data = resp.json()
            results = data.get("results", [])
            for record in results:
                yield record
            if offset + _PAGE_SIZE >= data.get("total_count", 0):
                break
            offset += _PAGE_SIZE

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        coord = raw.get("geografische_koordinate") or {}
        lon = coord.get("lon")
        lat = coord.get("lat")
        art = raw.get("art_der_baumassnahme") or "Baustelle"
        von = raw.get("von") or ""
        bis = raw.get("bis") or ""

        return {
            "source_id": _make_source_id(art, von, lon, lat),
            "label": art[:120],
            "valid_from": _parse_date(von),
            "valid_to": _parse_date(bis),
            "reason": art,
            "operator": raw.get("auftraggeber"),
            "planned_end": bis or None,
            "stadtbezirk": raw.get("stadtbezirk"),
            "einschrankung": raw.get("einschrankung"),
            "lon": lon,
            "lat": lat,
        }

    async def emit_entities(self, normalized: dict[str, Any]) -> list[NodeBase]:
        prov = self._provenance(normalized["source_id"], _SOURCE_URL)
        geom = None
        if normalized["lon"] is not None and normalized["lat"] is not None:
            geom = {"type": "Point", "coordinates": [normalized["lon"], normalized["lat"]]}
        node = ConstructionSite(
            label=normalized["label"],
            valid_from=normalized["valid_from"],
            valid_to=normalized["valid_to"],
            geom=geom,
            properties={
                "reason": normalized["reason"],
                "operator": normalized["operator"],
                "planned_end": normalized["planned_end"],
                "stadtbezirk": normalized["stadtbezirk"],
                "einschrankung": normalized["einschrankung"],
            },
            **prov,
        )
        return [node]

    async def emit_edges(self, normalized: dict[str, Any], nodes: list[NodeBase]) -> list[EdgeBase]:
        # LOCATED_IN edges resolved by PostGIS spatial join in the flow (like POIs).
        return []
