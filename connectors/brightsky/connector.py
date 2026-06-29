"""
Connector: Bright Sky (DWD weather) for Dortmund.

Source:  https://api.brightsky.dev/
API:     JSON over official DWD open data
License: DWD Terms of Use (open); Bright Sky is CC0
Shape:   snapshot — poll current weather, append one timestamped observation

What this covers:
  - Current temperature, precipitation, wind speed, condition for Dortmund.
What it does NOT cover:
  - Forecast (separate endpoint) and per-Stadtbezirk granularity (single point).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, AsyncGenerator

from config import settings
from connectors.base import BaseConnector, ConnectorShape
from ontology.edges import EdgeBase
from ontology.nodes import NodeBase, WeatherObservation

# Dortmund city centre (Friedensplatz). Bright Sky snaps to nearest DWD station.
_LAT = 51.5136
_LON = 7.4653
_SOURCE_URL = f"{settings.brightsky_api_url}/current_weather?lat={_LAT}&lon={_LON}"


class BrightSkyConnector(BaseConnector):
    shape = ConnectorShape.SNAPSHOT
    source_name = "brightsky_dwd"

    async def fetch(self, checkpoint: dict[str, Any] | None = None) -> AsyncGenerator[Any, None]:
        resp = await self._get(
            f"{settings.brightsky_api_url}/current_weather",
            params={"lat": _LAT, "lon": _LON},
        )
        data = resp.json()
        weather = data.get("weather")
        if weather:
            # Attach source station metadata so normalize can record provenance.
            sources = data.get("sources") or [{}]
            weather["_source"] = sources[0]
            yield weather

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        src = raw.get("_source", {})
        ts: str = raw.get("timestamp", "")
        valid_from = None
        if ts:
            try:
                valid_from = datetime.fromisoformat(ts)
            except (ValueError, TypeError):
                valid_from = None

        dwd_station_id = str(src.get("dwd_station_id") or "")
        station_name = src.get("station_name") or "Dortmund"

        return {
            "source_id": f"{dwd_station_id}|{ts}",
            "label": f"Weather {station_name} {ts}",
            "valid_from": valid_from,
            "temperature": raw.get("temperature"),
            "precipitation": raw.get("precipitation"),
            "wind_speed": raw.get("wind_speed"),
            "condition": raw.get("condition"),
            "station_id": src.get("id"),
            "dwd_station_id": dwd_station_id or None,
            "lat": src.get("lat", _LAT),
            "lon": src.get("lon", _LON),
        }

    async def emit_entities(self, normalized: dict[str, Any]) -> list[NodeBase]:
        prov = self._provenance(normalized["source_id"], _SOURCE_URL)
        node = WeatherObservation(
            label=normalized["label"],
            valid_from=normalized["valid_from"],
            geom={"type": "Point", "coordinates": [normalized["lon"], normalized["lat"]]},
            properties={
                "temperature": normalized["temperature"],
                "precipitation": normalized["precipitation"],
                "wind_speed": normalized["wind_speed"],
                "condition": normalized["condition"],
                "station_id": normalized["station_id"],
                "dwd_station_id": normalized["dwd_station_id"],
            },
            **prov,
        )
        return [node]

    async def emit_edges(self, normalized: dict[str, Any], nodes: list[NodeBase]) -> list[EdgeBase]:
        return []
