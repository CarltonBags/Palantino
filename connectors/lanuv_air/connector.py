"""
Connector: LANUV LUQS air quality (NRW) — Dortmund stations.

Source:  opengeodata.nrw.de — OpenKontiLUQS aktuelle Messwerte (last 24h)
File:    .../aktuelle_luftqualitaet/OpenKontiLUQS_aktuelle-messwerte-24h.csv
License: open (LANUV/LANUK)
Shape:   snapshot — poll, append the latest hourly row per Dortmund station

Format notes:
  - Semicolon CSV, latin-1, German decimal comma.
  - 2 comment lines, then header, then hourly rows. Last row = most recent.
  - Wide layout: one column per "STATION COMPONENT ... [unit]" (e.g.
    "VDOM NO2 1H Mittelwert [µg/m³]"). We pivot the Dortmund columns back into
    one observation per station.
  - Values: "<7" (below detection → None), "--" (no data → None), "26,3" numeric.
  - Values are preliminary/unvalidated → preliminary=True.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from connectors.base import BaseConnector, ConnectorShape
from ontology.edges import EdgeBase
from ontology.nodes import AirQualityObservation, NodeBase

_CSV_URL = (
    "https://www.opengeodata.nrw.de/produkte/umwelt_klima/luftqualitaet/luqs/"
    "aktuelle_luftqualitaet/OpenKontiLUQS_aktuelle-messwerte-24h.csv"
)

# Dortmund station code → (lat, lon). UTM32→WGS84 from the LUQS Messorte table.
_DO_STATIONS: dict[str, tuple[float, float]] = {
    "DOB12": (51.50058, 7.48101),
    "DOB11": (51.50404, 7.50251),
    "VDOM3": (51.52409, 7.47962),
    "VDOM": (51.52355, 7.48356),
    "DMD2": (51.53689, 7.45741),
    "DOMA": (51.52402, 7.45555),
    "DOMM": (51.50563, 7.47412),
    "DOMR": (51.50440, 7.46712),
}
# Map the CSV component token → our AirQualityObservation field.
_COMPONENTS = {"PM10F": "pm10", "NO2": "no2", "NO": "no", "SO2": "so2", "O3": "o3"}


def _parse_value(raw: str | None) -> float | None:
    if not raw:
        return None
    raw = raw.strip()
    if raw in ("--", "", "<7") or raw.startswith("<"):
        return None
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        return None


def _parse_header_col(col: str) -> tuple[str, str] | None:
    """'VDOM NO2 1H Mittelwert [µg/m³]' → ('VDOM', 'no2'), else None."""
    parts = col.split()
    if len(parts) < 2:
        return None
    station, component = parts[0], parts[1]
    if station in _DO_STATIONS and component in _COMPONENTS:
        return station, _COMPONENTS[component]
    return None


class LanuvAirConnector(BaseConnector):
    shape = ConnectorShape.SNAPSHOT
    source_name = "lanuv_luqs"

    async def fetch(self, checkpoint: dict[str, Any] | None = None) -> AsyncGenerator[Any, None]:
        resp = await self._get(_CSV_URL)
        text = resp.content.decode("latin-1", errors="replace")
        # Drop comment lines until the header row (starts with "Datum").
        lines = text.splitlines()
        start = next((i for i, ln in enumerate(lines) if ln.startswith('"Datum"')), 0)
        reader = csv.reader(io.StringIO("\n".join(lines[start:])), delimiter=";")
        header = next(reader, None)
        if not header:
            return
        rows = [r for r in reader if r and r[0]]
        if not rows:
            return
        latest = rows[-1]  # most recent hour

        # Build column index → (station, field) for Dortmund columns only.
        col_map: dict[int, tuple[str, str]] = {}
        for idx, col in enumerate(header):
            parsed = _parse_header_col(col)
            if parsed:
                col_map[idx] = parsed

        datum = latest[0] if len(latest) > 0 else ""
        zeit = latest[1] if len(latest) > 1 else ""

        # Pivot: collect components per station.
        per_station: dict[str, dict[str, float | None]] = {}
        for idx, (station, field) in col_map.items():
            value = _parse_value(latest[idx]) if idx < len(latest) else None
            per_station.setdefault(station, {})[field] = value

        for station, measures in per_station.items():
            yield {"station": station, "datum": datum, "zeit": zeit, "measures": measures}

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        station = raw["station"]
        datum = raw["datum"]
        zeit = raw["zeit"]
        measures = raw["measures"]
        lat, lon = _DO_STATIONS[station]

        valid_from = _parse_timestamp(datum, zeit)

        return {
            "source_id": f"{station}|{datum}|{zeit}",
            "label": f"Air quality {station} {datum} {zeit}",
            "valid_from": valid_from,
            "station_id": station,
            "pm10": measures.get("pm10"),
            "no2": measures.get("no2"),
            "no": measures.get("no"),
            "so2": measures.get("so2"),
            "o3": measures.get("o3"),
            "lat": lat,
            "lon": lon,
        }

    async def emit_entities(self, normalized: dict[str, Any]) -> list[NodeBase]:
        prov = self._provenance(normalized["source_id"], _CSV_URL)
        node = AirQualityObservation(
            label=normalized["label"],
            valid_from=normalized["valid_from"],
            geom={"type": "Point", "coordinates": [normalized["lon"], normalized["lat"]]},
            properties={
                "pm10": normalized["pm10"],
                "no2": normalized["no2"],
                "no": normalized["no"],
                "so2": normalized["so2"],
                "o3": normalized["o3"],
                "station_id": normalized["station_id"],
                "preliminary": True,
            },
            **prov,
        )
        return [node]

    async def emit_edges(self, normalized: dict[str, Any], nodes: list[NodeBase]) -> list[EdgeBase]:
        return []


def _parse_timestamp(datum: str, zeit: str) -> datetime | None:
    """'28.06.2026' + '23:00' → aware datetime. '24:00' rolls to next-day 00:00."""
    if not datum:
        return None
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", datum)
    if not m:
        return None
    day, month, year = (int(g) for g in m.groups())
    hour = 0
    minute = 0
    tm = re.match(r"(\d{1,2}):(\d{2})", zeit or "")
    if tm:
        hour, minute = int(tm.group(1)), int(tm.group(2))
    base = datetime(year, month, day, 0, minute, tzinfo=timezone.utc)
    from datetime import timedelta

    return base + timedelta(hours=hour)
