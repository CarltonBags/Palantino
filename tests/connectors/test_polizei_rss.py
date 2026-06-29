"""Frozen raw → normalized tests for the Polizei NRW RSS connector. No network."""

import pytest

from connectors.polizei_rss.connector import (
    PolizeiRssConnector,
    _classify,
    _make_source_id,
    _strip_html,
)
from ontology.nodes import Event

RAW = {
    "guid": "https://dortmund.polizei.nrw/presse/raeuberischer-erpresser",
    "title": "Die Polizei Dortmund sucht einen räuberischen Erpresser",
    "link": "https://dortmund.polizei.nrw/presse/raeuberischer-erpresser",
    "description": "<p>Am Freitag kam es zu einer <b>Tat</b>.</p>",
    "pubDate": "2026-06-26T18:27:40+02:00",
}

RAW_DEMO = {
    "guid": "g2",
    "title": "Versammlung in der Innenstadt",
    "link": "l2",
    "description": "Eine Demonstration mit 500 Teilnehmern.",
    "pubDate": "",
}


@pytest.fixture
def connector() -> PolizeiRssConnector:
    return PolizeiRssConnector()


def test_strip_html() -> None:
    assert _strip_html("<p>Am Freitag kam es zu einer <b>Tat</b>.</p>") == "Am Freitag kam es zu einer Tat."


def test_source_id_deterministic() -> None:
    assert _make_source_id("x") == _make_source_id("x")
    assert _make_source_id("x") != _make_source_id("y")


def test_classify_demo() -> None:
    assert _classify("Versammlung", "Demonstration mit 500") == "demonstration"


def test_normalize_isoformat_pubdate(connector: PolizeiRssConnector) -> None:
    n = connector.normalize(RAW)
    assert n["valid_from"] is not None
    assert n["valid_from"].year == 2026
    assert n["valid_from"].hour == 18
    assert n["event_type"] == "police_report"
    assert "<" not in n["description"]


def test_normalize_missing_pubdate(connector: PolizeiRssConnector) -> None:
    n = connector.normalize(RAW_DEMO)
    assert n["valid_from"] is None
    assert n["event_type"] == "demonstration"


@pytest.mark.asyncio
async def test_emit_event_provenance(connector: PolizeiRssConnector) -> None:
    n = connector.normalize(RAW)
    nodes = await connector.emit_entities(n)
    assert isinstance(nodes[0], Event)
    assert nodes[0].source == "polizei_nrw_dortmund_rss"
    assert nodes[0].source_url == RAW["link"]
    assert nodes[0].inferred is False


@pytest.mark.asyncio
async def test_emit_no_edges(connector: PolizeiRssConnector) -> None:
    n = connector.normalize(RAW)
    nodes = await connector.emit_entities(n)
    assert await connector.emit_edges(n, nodes) == []
