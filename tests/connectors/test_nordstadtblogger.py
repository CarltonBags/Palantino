"""Frozen raw → normalized tests for the Nordstadtblogger RSS connector. No network."""

import pytest

from connectors.nordstadtblogger.connector import (
    NordstadtbloggerConnector,
    _make_source_id,
    _parse_pubdate,
    _strip_html,
)
from ontology.nodes import Event

RAW = {
    "guid": "https://www.nordstadtblogger.de/neue-regeln-fuer-leih-scooter/",
    "title": "Neue Regeln für Leih-Scooter rund um den Hauptbahnhof",
    "link": "https://www.nordstadtblogger.de/neue-regeln-fuer-leih-scooter/",
    "description": "<p>Wer mit einem <b>Leih-Scooter</b> zum Hauptbahnhof fährt …</p>",
    "pubDate": "Mon, 29 Jun 2026 22:00:30 +0000",
    "creator": "Nordstadtblogger-Redaktion",
    "categories": ["Politik", "Verkehr & Mobilität", "Innenstadt-Nord"],
}

RAW_NO_DATE = {
    "guid": "g2",
    "title": "Stadtteilfest in der Nordstadt",
    "link": "l2",
    "description": "Ein Fest.",
    "pubDate": "",
    "creator": "",
    "categories": [],
}


@pytest.fixture
def connector() -> NordstadtbloggerConnector:
    return NordstadtbloggerConnector()


def test_strip_html() -> None:
    assert _strip_html("<p>Wer mit einem <b>Leih-Scooter</b> fährt</p>") == "Wer mit einem Leih-Scooter fährt"


def test_source_id_deterministic() -> None:
    assert _make_source_id("x") == _make_source_id("x")
    assert _make_source_id("x") != _make_source_id("y")


def test_parse_rfc822_pubdate() -> None:
    dt = _parse_pubdate("Mon, 29 Jun 2026 22:00:30 +0000")
    assert dt is not None and dt.year == 2026 and dt.month == 6 and dt.day == 29


def test_normalize(connector: NordstadtbloggerConnector) -> None:
    n = connector.normalize(RAW)
    assert n["valid_from"] is not None and n["valid_from"].year == 2026
    assert "<" not in n["description"]
    assert n["author"] == "Nordstadtblogger-Redaktion"
    assert "Innenstadt-Nord" in n["categories"]


def test_normalize_defaults(connector: NordstadtbloggerConnector) -> None:
    n = connector.normalize(RAW_NO_DATE)
    assert n["valid_from"] is None
    assert n["author"] == "Nordstadtblogger-Redaktion"  # default when feed omits creator


@pytest.mark.asyncio
async def test_emit_event_provenance(connector: NordstadtbloggerConnector) -> None:
    n = connector.normalize(RAW)
    nodes = await connector.emit_entities(n)
    assert isinstance(nodes[0], Event)
    assert nodes[0].source == "nordstadtblogger"
    assert nodes[0].source_url == RAW["link"]
    assert nodes[0].properties["event_type"] == "news"
    assert nodes[0].inferred is False


@pytest.mark.asyncio
async def test_emit_no_edges(connector: NordstadtbloggerConnector) -> None:
    n = connector.normalize(RAW)
    nodes = await connector.emit_entities(n)
    assert await connector.emit_edges(n, nodes) == []
