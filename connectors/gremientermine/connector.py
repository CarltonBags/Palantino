"""
Connector: Dortmund Gremientermine (committee meeting schedule).

Source:  Dortmund Open Data Portal — dataset fb1-gremientermine
API:     ODS v2.1  https://open-data.dortmund.de/api/explore/v2.1/
License: DL-DE-Zero (attribution-free)
Shape:   snapshot — full refresh, ~20-50 records, daily cadence
Update:  "Gremientermine mit täglicher Aktualisierung" (city publishes daily)

What this covers (partial substitute for OParl while it is disabled):
  - Committee name (Gremium)
  - Meeting date + start time
  - Location (free-text address)
What it DOES NOT cover:
  - Agenda items, resolutions, votes, documents
  - Session ID linkable to SessionNet
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from connectors.base import BaseConnector, ConnectorShape
from config import settings
from ontology.edges import EdgeBase
from ontology.nodes import Meeting, NodeBase

_BASE = settings.opendata_dortmund_base_url
_DATASET = "fb1-gremientermine"
_SOURCE_URL = f"https://open-data.dortmund.de/explore/dataset/{_DATASET}/"
_RECORDS_URL = f"{_BASE}/catalog/datasets/{_DATASET}/records"
_PAGE_SIZE = 100


def _make_source_id(datum: str, gremium: str, beginn: str) -> str:
    key = f"{datum}|{gremium}|{beginn}"
    return hashlib.sha256(key.encode()).hexdigest()[:20]


def _parse_dt(datum: str, beginn: str) -> datetime | None:
    try:
        return datetime.fromisoformat(f"{datum}T{beginn}").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


class GremienTermineConnector(BaseConnector):
    shape = ConnectorShape.SNAPSHOT
    source_name = "opendata_dortmund_gremientermine"

    async def fetch(self, checkpoint: dict[str, Any] | None = None) -> AsyncGenerator[Any, None]:
        offset = 0
        while True:
            resp = await self._get(
                _RECORDS_URL,
                params={"limit": _PAGE_SIZE, "offset": offset},
            )
            data = resp.json()
            results = data.get("results", [])
            for record in results:
                yield record
            if offset + _PAGE_SIZE >= data.get("total_count", 0):
                break
            offset += _PAGE_SIZE

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        datum: str = raw.get("datum", "")
        beginn: str = raw.get("beginn", "00:00:00")
        gremium: str = raw.get("gremium") or "Unbekanntes Gremium"
        sitzungsort: str | None = raw.get("sitzungsort")

        dt = _parse_dt(datum, beginn)
        label = f"{gremium} {datum}"

        return {
            "source_id": _make_source_id(datum, gremium, beginn),
            "label": label,
            "gremium": gremium,
            "datum": datum,
            "beginn": beginn,
            "sitzungsort": sitzungsort,
            "valid_from": dt,
        }

    async def emit_entities(self, normalized: dict[str, Any]) -> list[NodeBase]:
        prov = self._provenance(normalized["source_id"], _SOURCE_URL)
        node = Meeting(
            label=normalized["label"],
            valid_from=normalized["valid_from"],
            properties={
                "gremium": normalized["gremium"],
                "datum": normalized["datum"],
                "beginn": normalized["beginn"],
                "sitzungsort": normalized["sitzungsort"],
                "meeting_type": "scheduled",
            },
            **prov,
        )
        return [node]

    async def emit_edges(self, normalized: dict[str, Any], nodes: list[NodeBase]) -> list[EdgeBase]:
        # No edges at this layer — committee-to-meeting PART_OF edges require
        # Organization nodes which are not in this dataset.
        return []
