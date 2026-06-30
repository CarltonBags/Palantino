"""Frozen raw → normalized tests for the Nordstadtblogger WP-REST connector. No network."""

import pytest

from connectors.nordstadtblogger.connector import (
    NordstadtbloggerConnector,
    _embedded_author,
    _embedded_categories,
    _make_source_id,
    _parse_wp_date,
    _strip_html,
)
from ontology.nodes import Event

# Shape of one wp/v2/posts?_embed item (trimmed to the fields we read).
RAW = {
    "id": 123456,
    "date": "2026-06-30T13:00:39",
    "date_gmt": "2026-06-30T11:00:39",
    "link": "https://www.nordstadtblogger.de/wer-bekommt-2026-den-heimat-preis/",
    "title": {"rendered": "Wer bekommt 2026 den Heimat&#8209;Preis?"},
    "excerpt": {"rendered": "<p>Die Stadt sucht <b>Vorschläge</b> aus der Nordstadt …</p>"},
    "_embedded": {
        "author": [{"name": "Susanne Schulte"}],
        "wp:term": [
            [
                {"taxonomy": "category", "name": "Politik"},
                {"taxonomy": "category", "name": "Innenstadt-Nord"},
            ],
            [{"taxonomy": "post_tag", "name": "Heimatpreis"}],
        ],
    },
}

RAW_MINIMAL = {
    "id": 999,
    "date_gmt": "",
    "link": "https://www.nordstadtblogger.de/x/",
    "title": {"rendered": "Kurzmeldung"},
    "excerpt": {"rendered": ""},
}


@pytest.fixture
def connector() -> NordstadtbloggerConnector:
    return NordstadtbloggerConnector()


def test_strip_html_unescapes() -> None:
    assert _strip_html("<p>Die Stadt sucht <b>Vorschläge</b></p>") == "Die Stadt sucht Vorschläge"


def test_source_id_deterministic() -> None:
    assert _make_source_id("123") == _make_source_id("123")
    assert _make_source_id("123") != _make_source_id("124")


def test_parse_wp_date_utc() -> None:
    dt = _parse_wp_date("2026-06-30T11:00:39")
    assert dt is not None and dt.year == 2026 and dt.tzinfo is not None


def test_embedded_author_and_categories() -> None:
    assert _embedded_author(RAW) == "Susanne Schulte"
    cats = _embedded_categories(RAW)
    assert cats == ["Politik", "Innenstadt-Nord"]  # post_tag excluded


def test_normalize(connector: NordstadtbloggerConnector) -> None:
    n = connector.normalize(RAW)
    assert n["label"] == "Wer bekommt 2026 den Heimat‑Preis?"  # &#8209; unescaped
    assert n["valid_from"] is not None and n["valid_from"].year == 2026
    assert "<" not in n["description"] and "Vorschläge" in n["description"]
    assert n["author"] == "Susanne Schulte"
    assert "Innenstadt-Nord" in n["categories"]


def test_normalize_minimal(connector: NordstadtbloggerConnector) -> None:
    n = connector.normalize(RAW_MINIMAL)
    assert n["valid_from"] is None
    assert n["author"] == "Nordstadtblogger-Redaktion"  # default when no _embedded
    assert n["categories"] == []


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
