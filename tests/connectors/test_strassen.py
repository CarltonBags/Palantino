"""Frozen raw → normalized tests for the streets connector. No network."""

import pytest

from connectors.strassen.connector import StrassenConnector
from ontology.nodes import Road

RAW = {
    "strassenname": "Brechtener Straße",
    "strassenschlussel": "70644",
    "statistischer_bezirk_nr": "210",
    "statistischer_bezirk_bezeichnung": "Brechten",
    "stadtbezirk_nr": "2",
    "stadtbezirk_bezeichnung": "Eving",
    "kommune": "Dortmund",
}


@pytest.fixture
def connector() -> StrassenConnector:
    return StrassenConnector()


def test_normalize(connector: StrassenConnector) -> None:
    n = connector.normalize(RAW)
    assert n["source_id"] == "70644"
    assert n["name_de"] == "Brechtener Straße"
    assert n["stat_bezirk"] == "Brechten"
    assert n["stadtbezirk"] == "Eving"


@pytest.mark.asyncio
async def test_emit_road(connector: StrassenConnector) -> None:
    node = (await connector.emit_entities(connector.normalize(RAW)))[0]
    assert isinstance(node, Road)
    assert node.source == "opendata_dortmund_strassen"
    assert node.label == "Brechtener Straße"
    assert node.properties["road_type"] == "street"
    assert node.properties["stat_bezirk"] == "Brechten"
    assert node.inferred is False


@pytest.mark.asyncio
async def test_emit_no_edges(connector: StrassenConnector) -> None:
    n = connector.normalize(RAW)
    nodes = await connector.emit_entities(n)
    assert await connector.emit_edges(n, nodes) == []
