"""Frozen raw → normalized tests for the demographics connector. No network."""

import pytest

from connectors.ods_stats.connector import OdsStatsConnector
from ontology.nodes import GeoArea

RAW = {
    "_dataset": "hauptwohnbevolkerung-der-stadtbezirke-masse-der-altersstruktur-2024",
    "_year": 2024,
    "stadtbezirk": "Innenstadt-Ost",
    "stadtbezirk_nr": "30",
    "durchschnittsalter": 43.30281,
    "minderjahrigenanteil": 14.4,
    "altenanteil": 19.9,
    "hochbetagtenanteil": 6.7,
}


@pytest.fixture
def connector() -> OdsStatsConnector:
    return OdsStatsConnector()


def test_normalize(connector: OdsStatsConnector) -> None:
    n = connector.normalize(RAW)
    assert n["year"] == 2024
    assert n["valid_from"].year == 2024 and n["valid_from"].month == 1
    assert n["durchschnittsalter"] == 43.30281
    assert "Innenstadt-Ost" in n["label"]


@pytest.mark.asyncio
async def test_emit_geoarea_snapshot(connector: OdsStatsConnector) -> None:
    n = connector.normalize(RAW)
    nodes = await connector.emit_entities(n)
    node = nodes[0]
    assert isinstance(node, GeoArea)
    assert node.source == "opendata_dortmund_demographics"
    assert node.properties["area_type"] == "stadtbezirk_demographics"
    assert node.properties["altenanteil"] == 19.9
    assert node.inferred is False


@pytest.mark.asyncio
async def test_emit_no_edges(connector: OdsStatsConnector) -> None:
    n = connector.normalize(RAW)
    nodes = await connector.emit_entities(n)
    assert await connector.emit_edges(n, nodes) == []
