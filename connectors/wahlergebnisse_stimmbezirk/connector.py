"""
Connector: Dortmund Ratswahl results per Stimmbezirk (precinct-level).

Source:  Dortmund Open Data Portal — fb33-rat-<date>-stimmbezirke datasets
API:     ODS v2.1
License: DL-DE-Zero
Shape:   reference — one row per polling precinct per election, full refresh

What this covers (the fine-grained politics-on-the-map layer):
  - Per-Stimmbezirk Ratswahl turnout + party votes → Event nodes
    (event_type="election_precinct"), carrying Stadtbezirk + Stimmbezirk.
  - 2025 rows have the polling-station point (geo_point_2d) → geom set, so the
    flow can ST_Within them into a statistischer Bezirk. Earlier years have no
    geometry but carry the Stadtbezirk name for the resolution layer.

Party columns vary per year (bare party-name columns). Everything not in the
fixed metadata/turnout set is treated as a party vote count.
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

# dataset_id -> election date (Ratswahl). Add new elections here.
_DATASETS = {
    "fb33-rat-20140525-stimmbezirke": "2014-05-25",
    "fb33-rat-20200913-stimmbezirke": "2020-09-13",
    "fb33-rat-20250914-stimmbezirke": "2025-09-14",
}

# Fixed non-party columns (metadata, geography, turnout). Everything else numeric
# is a party vote count.
_META = {
    "wahltermin", "ags", "europawahlkreis", "bundestagswahlkreis_nr",
    "bundestagswahlkreis", "landtagswahlkreis_nr", "landtagswahlkreis",
    "stadtbezirk_nr", "stadtbezirk", "kommunalwahlbezirk_nr", "kommunalwahlbezirk",
    "stimmbezirk", "briefwahlbezirk", "typ", "wahlraumbezeichnung",
    "wahlraum_zusatz", "wahlraum", "strasse", "hausnummer", "zusatz", "ort",
    "barrierefreiheit", "geo_point_2d", "ostwert", "hochwert",
    "wahlberechtigte_ohne_sperrvermerk", "wahlberechtigte_mit_sperrvermerk",
    "wahlberechtigte_nicht_im_wahlerverzeichnis", "wahlberechtigte",
    "wahler_innen", "wahler_innen_mit_wahlschein", "ungultige_stimmen",
    "gultige_stimmen", "kommune",
}


def _make_source_id(dataset: str, stimmbezirk: str) -> str:
    return hashlib.sha256(f"{dataset}|{stimmbezirk}".encode()).hexdigest()[:20]


def _source_url(dataset: str) -> str:
    return f"https://open-data.dortmund.de/explore/dataset/{dataset}/"


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def extract_party_votes(raw: dict[str, Any]) -> dict[str, int]:
    """Bare party-name columns → {party: votes}; metadata/turnout dropped."""
    out: dict[str, int] = {}
    for key, value in raw.items():
        if key in _META or key.startswith("_"):
            continue
        if isinstance(value, (int, float)) and value:
            out[key] = int(value)
    return out


def _turnout(raw: dict[str, Any]) -> float | None:
    berechtigte = raw.get("wahlberechtigte")
    waehler = raw.get("wahler_innen")
    if berechtigte and waehler:
        return round(100.0 * waehler / berechtigte, 2)
    return None


class WahlergebnisseStimmbezirkConnector(BaseConnector):
    shape = ConnectorShape.REFERENCE
    source_name = "opendata_dortmund_wahl_stimmbezirk"

    async def fetch(self, checkpoint: dict[str, Any] | None = None) -> AsyncGenerator[Any, None]:
        for dataset, wahltermin in _DATASETS.items():
            offset = 0
            records_url = f"{_BASE}/catalog/datasets/{dataset}/records"
            while True:
                resp = await self._get(records_url, params={"limit": _PAGE_SIZE, "offset": offset})
                data = resp.json()
                for record in data.get("results", []):
                    record["_dataset"] = dataset
                    record["_wahltermin"] = wahltermin
                    yield record
                if offset + _PAGE_SIZE >= data.get("total_count", 0):
                    break
                offset += _PAGE_SIZE

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        dataset = raw["_dataset"]
        wahltermin = raw.get("wahltermin") or raw["_wahltermin"]
        stimmbezirk = str(raw.get("stimmbezirk") or "")
        stadtbezirk = raw.get("stadtbezirk")
        geo = raw.get("geo_point_2d") or {}
        lon = geo.get("lon")
        lat = geo.get("lat")

        return {
            "source_id": _make_source_id(dataset, stimmbezirk),
            "label": f"Ratswahl {wahltermin} Stimmbezirk {stimmbezirk} ({stadtbezirk})",
            "valid_from": _parse_date(wahltermin),
            "dataset": dataset,
            "wahltermin": wahltermin,
            "stimmbezirk": stimmbezirk,
            "stadtbezirk": stadtbezirk,
            "stadtbezirk_nr": raw.get("stadtbezirk_nr"),
            "kommunalwahlbezirk": raw.get("kommunalwahlbezirk"),
            "wahlberechtigte": raw.get("wahlberechtigte"),
            "wahler_innen": raw.get("wahler_innen"),
            "gultige_stimmen": raw.get("gultige_stimmen"),
            "turnout_pct": _turnout(raw),
            "party_votes": extract_party_votes(raw),
            "lon": lon,
            "lat": lat,
        }

    async def emit_entities(self, normalized: dict[str, Any]) -> list[NodeBase]:
        prov = self._provenance(normalized["source_id"], _source_url(normalized["dataset"]))
        geom = None
        if normalized["lon"] is not None and normalized["lat"] is not None:
            geom = {"type": "Point", "coordinates": [normalized["lon"], normalized["lat"]]}
        node = Event(
            label=normalized["label"],
            valid_from=normalized["valid_from"],
            geom=geom,
            properties={
                "event_type": "election_precinct",
                "wahltermin": normalized["wahltermin"],
                "stimmbezirk": normalized["stimmbezirk"],
                "stadtbezirk": normalized["stadtbezirk"],
                "stadtbezirk_nr": normalized["stadtbezirk_nr"],
                "kommunalwahlbezirk": normalized["kommunalwahlbezirk"],
                "wahlberechtigte": normalized["wahlberechtigte"],
                "wahler_innen": normalized["wahler_innen"],
                "gultige_stimmen": normalized["gultige_stimmen"],
                "turnout_pct": normalized["turnout_pct"],
                "party_votes": normalized["party_votes"],
                "tags": ["wahl", "ratswahl", "stimmbezirk"],
            },
            **prov,
        )
        return [node]

    async def emit_edges(self, normalized: dict[str, Any], nodes: list[NodeBase]) -> list[EdgeBase]:
        # LOCATED_IN (precinct point → statistischer Bezirk) resolved by the
        # PostGIS spatial join in the flow, for rows that carry geometry.
        return []
