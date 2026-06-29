"""Frozen raw → normalized tests for the GTFS static connector. No network."""

import pytest

from connectors.gtfs_static.connector import GtfsStaticConnector, _in_bbox
from ontology.nodes import TransitRoute, TransitStop

RAW_STOP = {
    "_kind": "stop",
    "_url": "https://download.gtfs.de/germany/nv_free/latest.zip",
    "stop_id": "de:05913:1234",
    "stop_name": "Dortmund Hbf",
    "stop_code": "DOHB",
    "platform_code": "1",
    "wheelchair_boarding": "1",
    "stop_lat": "51.5177",
    "stop_lon": "7.4593",
}

RAW_ROUTE = {
    "_kind": "route",
    "_url": "https://download.gtfs.de/germany/nv_free/latest.zip",
    "route_id": "r-u41",
    "route_short_name": "U41",
    "route_long_name": "U41 Brambauer - Hörde",
    "route_type": "0",
    "agency_id": "dsw21",
}


@pytest.fixture
def connector() -> GtfsStaticConnector:
    return GtfsStaticConnector()


def test_bbox() -> None:
    assert _in_bbox(51.5177, 7.4593) is True       # Dortmund Hbf
    assert _in_bbox(50.94, 6.95) is False          # Köln


def test_normalize_stop(connector: GtfsStaticConnector) -> None:
    n = connector.normalize(RAW_STOP)
    assert n["kind"] == "stop"
    assert n["source_id"] == "stop:de:05913:1234"
    assert n["lat"] == 51.5177


def test_normalize_route(connector: GtfsStaticConnector) -> None:
    n = connector.normalize(RAW_ROUTE)
    assert n["kind"] == "route"
    assert n["source_id"] == "route:r-u41"
    assert n["route_short_name"] == "U41"


@pytest.mark.asyncio
async def test_emit_stop(connector: GtfsStaticConnector) -> None:
    node = (await connector.emit_entities(connector.normalize(RAW_STOP)))[0]
    assert isinstance(node, TransitStop)
    assert node.source == "gtfs_nrw_static"
    assert node.geom["coordinates"] == [7.4593, 51.5177]
    assert node.properties["wheelchair_boarding"] == "1"


@pytest.mark.asyncio
async def test_emit_route(connector: GtfsStaticConnector) -> None:
    node = (await connector.emit_entities(connector.normalize(RAW_ROUTE)))[0]
    assert isinstance(node, TransitRoute)
    assert node.properties["route_short_name"] == "U41"
    assert node.inferred is False


@pytest.mark.asyncio
async def test_emit_no_edges(connector: GtfsStaticConnector) -> None:
    n = connector.normalize(RAW_STOP)
    nodes = await connector.emit_entities(n)
    assert await connector.emit_edges(n, nodes) == []
