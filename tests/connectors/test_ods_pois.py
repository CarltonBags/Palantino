"""Frozen raw → normalized tests for the XErleben POI connector. No network."""

import pytest

from connectors.ods_pois.connector import OdsPoisConnector
from ontology.nodes import POI

RAW = {
    "_dataset": "schulen",
    "_poi_type": "school",
    "geo_point_2d": {"lon": 7.55948, "lat": 51.49106},
    "objektart": "Realschule",
    "objektname": "Albrecht-Dürer-Realschule",
    "strasse": "Schweizer Allee",
    "hausnummer": "25",
    "stadtbezbe": "Aplerbeck",
    "statbezibe": "Aplerbeck",
    "link": None,
}


@pytest.fixture
def connector() -> OdsPoisConnector:
    return OdsPoisConnector()


def test_normalize(connector: OdsPoisConnector) -> None:
    n = connector.normalize(RAW)
    assert n["label"] == "Albrecht-Dürer-Realschule"
    assert n["poi_type"] == "school"
    assert n["addr_street"] == "Schweizer Allee"
    assert n["stadtbezirk"] == "Aplerbeck"
    assert len(n["source_id"]) == 20


@pytest.mark.asyncio
async def test_emit_poi(connector: OdsPoisConnector) -> None:
    n = connector.normalize(RAW)
    nodes = await connector.emit_entities(n)
    node = nodes[0]
    assert isinstance(node, POI)
    assert node.source == "opendata_dortmund_xerleben"
    assert node.geom["coordinates"] == [7.55948, 51.49106]
    assert node.properties["objektart"] == "Realschule"
    assert node.inferred is False


RAW_MARKET = {
    "_dataset": "wochenmarkt",
    "_poi_type": "market",
    "geo_point_2d": {"lon": 7.4238, "lat": 51.5127},
    "objektart": "Wochenmarkt",
    "objektname": "Wochenmarkt Dorstfeld",
    "i_zusinfo": "Markttag: freitags, Standort: Wilhelmplatz",
    "strasse": "Wilhelmplatz",
    "stadtbezbe": "Innenstadt-West",
    "statbezibe": "Dorstfeld",
    "link": None,
}


def test_normalize_market_captures_info(connector: OdsPoisConnector) -> None:
    n = connector.normalize(RAW_MARKET)
    assert n["poi_type"] == "market"
    assert n["info"] == "Markttag: freitags, Standort: Wilhelmplatz"


@pytest.mark.asyncio
async def test_emit_market_poi(connector: OdsPoisConnector) -> None:
    node = (await connector.emit_entities(connector.normalize(RAW_MARKET)))[0]
    assert isinstance(node, POI)
    assert node.properties["poi_type"] == "market"
    assert "freitags" in node.properties["info"]


@pytest.mark.asyncio
async def test_emit_no_edges(connector: OdsPoisConnector) -> None:
    n = connector.normalize(RAW)
    nodes = await connector.emit_entities(n)
    assert await connector.emit_edges(n, nodes) == []
