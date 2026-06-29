"""Frozen raw → normalized tests for the Autobahn traffic connector. No network."""

import pytest

from connectors.autobahn.connector import AutobahnConnector, _in_bbox, _join
from ontology.nodes import ConstructionSite, Event

RAW_ROADWORK = {
    "_road": "A40",
    "_kind": "roadwork",
    "_lat": 51.514,
    "_lon": 7.465,
    "identifier": "rw-1",
    "title": ["A40 | Dortmund-West - Dortmund-Zentrum"],
    "subtitle": " Essen -> Dortmund",
    "description": ["Fahrbahnerneuerung", "bis Herbst"],
    "isBlocked": False,
    "startTimestamp": "2026-06-26T10:00:00+02:00",
    "coordinate": {"lat": 51.514, "long": 7.465},
}

RAW_CLOSURE = {
    "_road": "A45",
    "_kind": "closure",
    "_lat": 51.50,
    "_lon": 7.45,
    "identifier": "cl-1",
    "title": "A45 | Vollsperrung",
    "subtitle": None,
    "description": None,
    "isBlocked": True,
    "startTimestamp": "2026-06-28T20:00:00+02:00",
    "coordinate": {"lat": 51.50, "long": 7.45},
}


@pytest.fixture
def connector() -> AutobahnConnector:
    return AutobahnConnector()


def test_bbox() -> None:
    assert _in_bbox(51.514, 7.465) is True       # Dortmund
    assert _in_bbox(51.43, 6.63) is False         # Moers (A40 west end)


def test_join_handles_list_and_str() -> None:
    assert _join(["a", "b"]) == "a b"
    assert _join("x") == "x"
    assert _join([]) is None


def test_normalize_roadwork(connector: AutobahnConnector) -> None:
    n = connector.normalize(RAW_ROADWORK)
    assert n["kind"] == "roadwork"
    assert n["road"] == "A40"
    assert "Dortmund-West" in n["label"]
    assert n["description"] == "Fahrbahnerneuerung bis Herbst"
    assert n["valid_from"].year == 2026


@pytest.mark.asyncio
async def test_emit_roadwork_is_construction_site(connector: AutobahnConnector) -> None:
    node = (await connector.emit_entities(connector.normalize(RAW_ROADWORK)))[0]
    assert isinstance(node, ConstructionSite)
    assert node.source == "autobahn_gmbh"
    assert node.properties["operator"] == "Autobahn GmbH"
    assert node.geom["coordinates"] == [7.465, 51.514]
    assert node.inferred is False


@pytest.mark.asyncio
async def test_emit_closure_is_event(connector: AutobahnConnector) -> None:
    node = (await connector.emit_entities(connector.normalize(RAW_CLOSURE)))[0]
    assert isinstance(node, Event)
    assert node.properties["event_type"] == "road_closure"
    assert node.properties["is_blocked"] is True


@pytest.mark.asyncio
async def test_emit_no_edges(connector: AutobahnConnector) -> None:
    n = connector.normalize(RAW_ROADWORK)
    nodes = await connector.emit_entities(n)
    assert await connector.emit_edges(n, nodes) == []
