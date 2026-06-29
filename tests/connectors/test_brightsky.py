"""Frozen raw → normalized tests for the Bright Sky connector. No network."""

import pytest

from connectors.brightsky.connector import BrightSkyConnector
from ontology.nodes import WeatherObservation

RAW = {
    "timestamp": "2026-06-29T07:00:00+00:00",
    "temperature": 18.4,
    "precipitation": 0.0,
    "wind_speed": 11.2,
    "condition": "dry",
    "_source": {
        "id": 1234,
        "dwd_station_id": "01303",
        "station_name": "Dortmund",
        "lat": 51.51,
        "lon": 7.46,
    },
}


@pytest.fixture
def connector() -> BrightSkyConnector:
    return BrightSkyConnector()


def test_normalize_fields(connector: BrightSkyConnector) -> None:
    n = connector.normalize(RAW)
    assert n["temperature"] == 18.4
    assert n["dwd_station_id"] == "01303"
    assert n["valid_from"].hour == 7
    assert "01303" in n["source_id"]


@pytest.mark.asyncio
async def test_emit_weather_node(connector: BrightSkyConnector) -> None:
    n = connector.normalize(RAW)
    nodes = await connector.emit_entities(n)
    assert len(nodes) == 1
    node = nodes[0]
    assert isinstance(node, WeatherObservation)
    assert node.source == "brightsky_dwd"
    assert node.source_url is not None
    assert node.inferred is False
    assert node.geom["coordinates"] == [7.46, 51.51]
    assert node.properties["temperature"] == 18.4


@pytest.mark.asyncio
async def test_emit_no_edges(connector: BrightSkyConnector) -> None:
    n = connector.normalize(RAW)
    nodes = await connector.emit_entities(n)
    assert await connector.emit_edges(n, nodes) == []
