"""Frozen raw → normalized tests for the LANUV LUQS air connector. No network."""

import pytest

from connectors.lanuv_air.connector import (
    LanuvAirConnector,
    _parse_header_col,
    _parse_timestamp,
    _parse_value,
)
from ontology.nodes import AirQualityObservation

RAW = {
    "station": "DMD2",
    "datum": "28.06.2026",
    "zeit": "23:00",
    "measures": {"no": None, "no2": 18.0, "o3": 50.0, "pm10": 27.0, "so2": None},
}


@pytest.fixture
def connector() -> LanuvAirConnector:
    return LanuvAirConnector()


def test_parse_value_german_decimal() -> None:
    assert _parse_value("26,3") == 26.3
    assert _parse_value("<7") is None     # below detection limit
    assert _parse_value("--") is None     # no data
    assert _parse_value("") is None


def test_parse_header_col_dortmund_only() -> None:
    assert _parse_header_col("DMD2 NO2 1H Mittelwert [µg/m³]") == ("DMD2", "no2")
    assert _parse_header_col("VDOM PM10F 24H gleitender Mittelwert [µg/m³]") == ("VDOM", "pm10")
    # Non-Dortmund station ignored
    assert _parse_header_col("BIEL NO2 1H Mittelwert [µg/m³]") is None


def test_parse_timestamp_2400_rolls_over() -> None:
    ts = _parse_timestamp("28.06.2026", "24:00")
    assert ts.day == 29 and ts.hour == 0
    ts2 = _parse_timestamp("28.06.2026", "23:00")
    assert ts2.day == 28 and ts2.hour == 23


def test_normalize(connector: LanuvAirConnector) -> None:
    n = connector.normalize(RAW)
    assert n["station_id"] == "DMD2"
    assert n["no2"] == 18.0
    assert n["no"] is None
    assert n["valid_from"].day == 28 and n["valid_from"].hour == 23


@pytest.mark.asyncio
async def test_emit_air_obs(connector: LanuvAirConnector) -> None:
    n = connector.normalize(RAW)
    nodes = await connector.emit_entities(n)
    node = nodes[0]
    assert isinstance(node, AirQualityObservation)
    assert node.source == "lanuv_luqs"
    assert node.properties["preliminary"] is True
    assert node.properties["pm10"] == 27.0
    assert node.geom["type"] == "Point"
    assert node.inferred is False


@pytest.mark.asyncio
async def test_emit_no_edges(connector: LanuvAirConnector) -> None:
    n = connector.normalize(RAW)
    nodes = await connector.emit_entities(n)
    assert await connector.emit_edges(n, nodes) == []
