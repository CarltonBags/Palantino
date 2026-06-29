"""Frozen record → normalized tests for the GTFS-Realtime connector. No network."""

from datetime import datetime, timezone

import pytest

from connectors.gtfs_realtime.connector import GtfsRealtimeConnector
from ontology.nodes import Event

RAW_ALERT = {
    "kind": "alert",
    "source_id": "alert:e1:1750000000",
    "label": "Störung U41",
    "description": "Linie U41 unterbrochen.",
    "valid_from": datetime(2026, 6, 29, 7, 0, tzinfo=timezone.utc),
    "route_ids": ["r-u41"],
    "delay_s": None,
}

RAW_DELAY = {
    "kind": "delay",
    "source_id": "delay:e2:1750000000",
    "label": "Delay 16 min route r-u41",
    "description": None,
    "valid_from": datetime(2026, 6, 29, 7, 11, tzinfo=timezone.utc),
    "route_ids": ["r-u41"],
    "delay_s": 960,
}


@pytest.fixture
def connector() -> GtfsRealtimeConnector:
    return GtfsRealtimeConnector()


def test_normalize_alert(connector: GtfsRealtimeConnector) -> None:
    n = connector.normalize(RAW_ALERT)
    assert n["event_type"] == "transit_disruption"
    assert n["kind"] == "alert"
    assert n["route_ids"] == ["r-u41"]


def test_normalize_delay(connector: GtfsRealtimeConnector) -> None:
    n = connector.normalize(RAW_DELAY)
    assert n["delay_s"] == 960
    assert n["kind"] == "delay"


@pytest.mark.asyncio
async def test_emit_event(connector: GtfsRealtimeConnector) -> None:
    n = connector.normalize(RAW_ALERT)
    node = (await connector.emit_entities(n))[0]
    assert isinstance(node, Event)
    assert node.source == "gtfs_de_realtime"
    assert "transit" in node.properties["tags"]
    assert node.inferred is False


@pytest.mark.asyncio
async def test_emit_no_edges(connector: GtfsRealtimeConnector) -> None:
    n = connector.normalize(RAW_DELAY)
    nodes = await connector.emit_entities(n)
    assert await connector.emit_edges(n, nodes) == []
