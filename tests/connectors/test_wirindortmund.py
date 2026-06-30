"""Frozen raw → normalized tests for the Wir-in-Dortmund RSS connector. No network."""

import pytest

from connectors.wirindortmund.connector import (
    WirInDortmundConnector,
    _make_source_id,
    _parse_pubdate,
    _strip_html,
)
from ontology.nodes import Event

RAW = {
    "guid": "https://www.wirindortmund.de/?p=311613",
    "title": "Das ist &#8222;Dortmunder Tatendrang&#8220;: 35 Projekte",
    "link": "https://www.wirindortmund.de/dortmund/das-ist-dortmunder-tatendrang/",
    "description": "<p>Rund 35 <b>Projekte</b> in Hörde …</p>",
    "pubDate": "Tue, 30 Jun 2026 09:28:55 +0000",
    "creator": "Wir in Dortmund (SK)",
    "categories": ["Dortmund", "Hörde", "Startseite", "Tatendrang"],
}


@pytest.fixture
def connector() -> WirInDortmundConnector:
    return WirInDortmundConnector()


def test_strip_html_unescapes() -> None:
    assert _strip_html("<p>Rund 35 <b>Projekte</b></p>") == "Rund 35 Projekte"


def test_source_id_deterministic() -> None:
    assert _make_source_id("a") == _make_source_id("a")
    assert _make_source_id("a") != _make_source_id("b")


def test_parse_rfc822() -> None:
    dt = _parse_pubdate("Tue, 30 Jun 2026 09:28:55 +0000")
    assert dt is not None and dt.year == 2026 and dt.day == 30


def test_normalize_drops_noise_categories(connector: WirInDortmundConnector) -> None:
    n = connector.normalize(RAW)
    assert n["valid_from"] is not None
    assert "<" not in n["description"]
    assert n["author"] == "Wir in Dortmund (SK)"
    # "Dortmund" + "Startseite" dropped; "Hörde" kept
    assert "Hörde" in n["categories"]
    assert "Startseite" not in n["categories"] and "Dortmund" not in n["categories"]


@pytest.mark.asyncio
async def test_emit_event_provenance(connector: WirInDortmundConnector) -> None:
    n = connector.normalize(RAW)
    nodes = await connector.emit_entities(n)
    assert isinstance(nodes[0], Event)
    assert nodes[0].source == "wirindortmund"
    assert nodes[0].properties["event_type"] == "news"
    assert nodes[0].inferred is False


@pytest.mark.asyncio
async def test_emit_no_edges(connector: WirInDortmundConnector) -> None:
    n = connector.normalize(RAW)
    nodes = await connector.emit_entities(n)
    assert await connector.emit_edges(n, nodes) == []
