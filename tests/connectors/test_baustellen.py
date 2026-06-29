"""Frozen raw → normalized tests for the Baustellen connector. No network."""

import pytest

from connectors.baustellen.connector import BaustellenConnector, _make_source_id
from ontology.nodes import ConstructionSite

RAW = {
    "geografische_koordinate": {"lon": 7.49351, "lat": 51.50334},
    "art_der_baumassnahme": "Westfalendamm - Gebäudekernsanierung // Halbseitige Sperrung",
    "auftraggeber": "Privatmaßnahme",
    "einschrankung": None,
    "zeitraum": "19.01.2026 - 23.10.2026",
    "von": "2026-01-19",
    "bis": "2026-10-23",
    "stadtbezirk": "Innenstadt-Ost",
    "status": "tagesaktuell",
    "kommune": "Dortmund",
}


@pytest.fixture
def connector() -> BaustellenConnector:
    return BaustellenConnector()


def test_source_id_deterministic() -> None:
    a = _make_source_id("x", "2026-01-19", 7.49, 51.50)
    assert a == _make_source_id("x", "2026-01-19", 7.49, 51.50)
    assert a != _make_source_id("y", "2026-01-19", 7.49, 51.50)


def test_normalize(connector: BaustellenConnector) -> None:
    n = connector.normalize(RAW)
    assert n["operator"] == "Privatmaßnahme"
    assert n["valid_from"].month == 1 and n["valid_from"].day == 19
    assert n["valid_to"].month == 10
    assert n["stadtbezirk"] == "Innenstadt-Ost"
    assert n["lon"] == 7.49351


@pytest.mark.asyncio
async def test_emit_construction_site(connector: BaustellenConnector) -> None:
    n = connector.normalize(RAW)
    nodes = await connector.emit_entities(n)
    node = nodes[0]
    assert isinstance(node, ConstructionSite)
    assert node.source == "opendata_dortmund_baustellen"
    assert node.geom["type"] == "Point"
    assert node.valid_to is not None
    assert node.inferred is False


@pytest.mark.asyncio
async def test_emit_no_edges(connector: BaustellenConnector) -> None:
    n = connector.normalize(RAW)
    nodes = await connector.emit_entities(n)
    assert await connector.emit_edges(n, nodes) == []
