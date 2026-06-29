"""Frozen raw → normalized tests for the streets connector. No network."""

import pytest

from connectors.strassen.connector import StrassenConnector
from ontology.nodes import Road

RAW = {
    "strassenname": "Brechtener Straße",
    "strassenschlussel": "70644",
    "kommune": "Dortmund",
}


@pytest.fixture
def connector() -> StrassenConnector:
    return StrassenConnector()


def test_normalize(connector: StrassenConnector) -> None:
    n = connector.normalize(RAW)
    assert n["source_id"] == "70644"
    assert n["name_de"] == "Brechtener Straße"
    # plain fb62-strassen has no Bezirk columns
    assert n["stat_bezirk"] is None


@pytest.mark.asyncio
async def test_emit_road(connector: StrassenConnector) -> None:
    node = (await connector.emit_entities(connector.normalize(RAW)))[0]
    assert isinstance(node, Road)
    assert node.source == "opendata_dortmund_strassen"
    assert node.label == "Brechtener Straße"
    assert node.properties["road_type"] == "street"
    assert node.inferred is False


@pytest.mark.asyncio
async def test_emit_no_edges(connector: StrassenConnector) -> None:
    n = connector.normalize(RAW)
    nodes = await connector.emit_entities(n)
    assert await connector.emit_edges(n, nodes) == []
