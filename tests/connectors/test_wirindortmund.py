"""Frozen raw → normalized tests for the Wir-in-Dortmund WP-REST connector. No network."""

import pytest

from connectors.wirindortmund.connector import (
    WirInDortmundConnector,
    _embedded_author,
    _embedded_categories,
    _make_source_id,
    _parse_wp_date,
    _strip_html,
)
from ontology.nodes import Event

RAW = {
    "id": 311613,
    "date_gmt": "2026-06-30T09:28:55",
    "link": "https://www.wirindortmund.de/dortmund/das-ist-dortmunder-tatendrang/",
    "title": {"rendered": "Das ist &#8222;Dortmunder Tatendrang&#8220;: 35 Projekte"},
    "excerpt": {"rendered": "<p>Rund 35 <b>Projekte</b> in Hörde …</p>"},
    "_embedded": {
        "author": [{"name": "Wir in Dortmund (SK)"}],
        "wp:term": [
            [
                {"taxonomy": "category", "name": "Dortmund"},
                {"taxonomy": "category", "name": "Hörde"},
                {"taxonomy": "category", "name": "Startseite"},
            ],
        ],
    },
}


@pytest.fixture
def connector() -> WirInDortmundConnector:
    return WirInDortmundConnector()


def test_strip_html_unescapes() -> None:
    assert _strip_html("<p>Rund 35 <b>Projekte</b></p>") == "Rund 35 Projekte"


def test_source_id_deterministic() -> None:
    assert _make_source_id("1") == _make_source_id("1")
    assert _make_source_id("1") != _make_source_id("2")


def test_parse_wp_date_utc() -> None:
    dt = _parse_wp_date("2026-06-30T09:28:55")
    assert dt is not None and dt.year == 2026 and dt.tzinfo is not None


def test_embedded_drops_noise_categories() -> None:
    assert _embedded_author(RAW) == "Wir in Dortmund (SK)"
    cats = _embedded_categories(RAW)
    assert "Hörde" in cats
    assert "Dortmund" not in cats and "Startseite" not in cats  # noise dropped


def test_normalize(connector: WirInDortmundConnector) -> None:
    n = connector.normalize(RAW)
    assert n["label"].startswith("Das ist „Dortmunder Tatendrang")  # entities unescaped
    assert n["valid_from"] is not None and n["valid_from"].year == 2026
    assert "<" not in n["description"]
    assert "Hörde" in n["categories"]


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
