"""
Connector: Dortmund election results ("who holds the seats").

Source:  Dortmund Open Data Portal — aggregate election-result datasets
API:     ODS v2.1
License: DL-DE-Zero
Shape:   reference — small historical tables, full refresh + diff

What this covers (complements the council minutes layer):
  - City-wide party vote shares per election (Kommunal / Bundestag / Landtag /
    Europa) → Event nodes (event_type="election").
  - Rat seat distribution per party per Kommunalwahl → Event node
    (event_type="council_composition") — the actual "who holds the seats".

Not covered here:
  - OB-Wahl (candidate-level, ~90 wide candidate columns) — deferred.
  - Per-Stimmbezirk breakdowns (fb33-rat-* etc.) — geo-heavy, deferred.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from config import settings
from connectors.base import BaseConnector, ConnectorShape
from ontology.edges import EdgeBase
from ontology.nodes import Event, NodeBase

_BASE = settings.opendata_dortmund_base_url
_PAGE_SIZE = 100

# Party-share datasets: dataset_id -> election_type. Generic "<party>_absolut" /
# "<party>_in" column pattern.
_SHARE_DATASETS = {
    "kommunalwahlen-wahlergebnisse": "kommunalwahl",
    "bundestagswahlen-zweitstimme-wahlergebnisse-": "bundestagswahl",
    "landtagswahlen-zweitstimme-wahlergebnisse": "landtagswahl",
    "europawahlen-wahlergebnisse": "europawahl",
}
# Seat-distribution dataset: bare party-name columns = seats.
_SEATS_DATASET = "kommunalwahlen-ratsmitglieder"

# Columns that are totals/metadata, never a party.
_NON_PARTY = {
    "tag_der_wahl", "erzeugt_am", "kommune", "wahlart", "sitze_insgesamt",
}


def _make_source_id(dataset: str, tag_der_wahl: str) -> str:
    return hashlib.sha256(f"{dataset}|{tag_der_wahl}".encode()).hexdigest()[:20]


def _source_url(dataset: str) -> str:
    return f"https://open-data.dortmund.de/explore/dataset/{dataset}/"


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def extract_party_shares(raw: dict[str, Any]) -> dict[str, dict[str, float]]:
    """{party: {votes, pct}} from the `<party>_absolut` / `<party>_in` columns."""
    out: dict[str, dict[str, float]] = {}
    for key, value in raw.items():
        if not key.endswith("_absolut") or value is None:
            continue
        party = key[: -len("_absolut")]
        if party.startswith("gultige") or party in _NON_PARTY:
            continue
        pct = raw.get(f"{party}_in")
        out[party] = {"votes": value, "pct": pct}
    return out


def extract_seats(raw: dict[str, Any]) -> dict[str, int]:
    """{party: seats} from the bare party-name columns of the Ratsmitglieder set."""
    out: dict[str, int] = {}
    for key, value in raw.items():
        if key in _NON_PARTY or key.startswith("_"):
            continue
        if isinstance(value, (int, float)) and value:
            out[key] = int(value)
    return out


class WahlergebnisseConnector(BaseConnector):
    shape = ConnectorShape.REFERENCE
    source_name = "opendata_dortmund_wahlergebnisse"

    async def _records(self, dataset: str) -> AsyncGenerator[dict[str, Any], None]:
        offset = 0
        records_url = f"{_BASE}/catalog/datasets/{dataset}/records"
        while True:
            resp = await self._get(records_url, params={"limit": _PAGE_SIZE, "offset": offset})
            data = resp.json()
            for record in data.get("results", []):
                yield record
            if offset + _PAGE_SIZE >= data.get("total_count", 0):
                break
            offset += _PAGE_SIZE

    async def fetch(self, checkpoint: dict[str, Any] | None = None) -> AsyncGenerator[Any, None]:
        for dataset, election_type in _SHARE_DATASETS.items():
            async for record in self._records(dataset):
                yield {"_dataset": dataset, "_kind": "result", "_type": election_type, **record}
        async for record in self._records(_SEATS_DATASET):
            yield {"_dataset": _SEATS_DATASET, "_kind": "seats", "_type": "council_composition", **record}

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        dataset = raw["_dataset"]
        kind = raw["_kind"]
        election_type = raw["_type"]
        tag = raw.get("tag_der_wahl") or ""

        if kind == "seats":
            payload = {"party_seats": extract_seats(raw), "seats_total": raw.get("sitze_insgesamt")}
        else:
            payload = {"party_shares": extract_party_shares(raw)}

        return {
            "source_id": _make_source_id(dataset, tag),
            "label": f"{election_type} {tag}",
            "valid_from": _parse_date(tag),
            "dataset": dataset,
            "election_type": election_type,
            "kind": kind,
            "tag_der_wahl": tag,
            **payload,
        }

    async def emit_entities(self, normalized: dict[str, Any]) -> list[NodeBase]:
        prov = self._provenance(normalized["source_id"], _source_url(normalized["dataset"]))
        properties: dict[str, Any] = {
            "event_type": "election",
            "election_type": normalized["election_type"],
            "tag_der_wahl": normalized["tag_der_wahl"],
            "tags": ["wahl", "dortmund"],
        }
        if normalized["kind"] == "seats":
            properties["event_type"] = "council_composition"
            properties["party_seats"] = normalized["party_seats"]
            properties["seats_total"] = normalized["seats_total"]
        else:
            properties["party_shares"] = normalized["party_shares"]

        node = Event(
            label=normalized["label"],
            valid_from=normalized["valid_from"],
            properties=properties,
            **prov,
        )
        return [node]

    async def emit_edges(self, normalized: dict[str, Any], nodes: list[NodeBase]) -> list[EdgeBase]:
        # Linking parties/officials to results needs Organization/Person nodes —
        # deferred to the resolution layer.
        return []
