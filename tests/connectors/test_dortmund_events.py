"""Frozen ES-doc → normalized tests for the events connector. No network."""

import pytest

from connectors.dortmund_events.connector import (
    DortmundEventsConnector,
    _parse_dt,
    _strip_html,
    _tag_by_parent,
    parse_event,
)
from ontology.nodes import Event

# Trimmed real "eventdatetime" Elasticsearch document.
DOC = {
    "id": "58c3f608-de-DE",
    "type": "eventdatetime",
    "website_url": "/dortmund-erleben/veranstaltungskalender/termin_137340.html",
    "contentTags": [
        {"value": "Konzert / Musik", "parentValues": ["Veranstaltungskalender", "Dortmund erleben"]},
        {"value": "Innenstadt-West", "parentValues": ["Stadtbezirke", "Verwaltung"]},
    ],
    "content": [
        {
            "data": {
                "startDateTime": "2027-06-12T10:00:00.000000+0200",
                "startCalendarDay": {"date": "2027-06-12"},
                "isCancelled": False,
                "isSoldOut": False,
                "freeOfCharge": True,
                "event": {
                    "title": "19. Fest der Chöre",
                    "description": "Treffen der Chorszene",
                    "text": "<p>Am 12. Juni 2027 lädt KLANGVOKAL ein.</p>",
                },
            }
        }
    ],
}

NON_EVENT = {"id": "x", "type": "node", "content": []}


@pytest.fixture
def connector() -> DortmundEventsConnector:
    return DortmundEventsConnector()


# ── helpers ──────────────────────────────────────────────────────────────────

def test_strip_html() -> None:
    assert _strip_html("<p>Hallo <b>Welt</b></p>") == "Hallo Welt"
    assert _strip_html(None) is None


def test_parse_dt_offset_without_colon() -> None:
    dt = _parse_dt("2027-06-12T10:00:00.000000+0200")
    assert dt.year == 2027 and dt.month == 6 and dt.day == 12 and dt.hour == 10


def test_parse_dt_fallback_date_only() -> None:
    assert _parse_dt("2027-06-12").day == 12
    assert _parse_dt(None) is None


def test_tag_by_parent() -> None:
    assert _tag_by_parent(DOC["contentTags"], "Stadtbezirke") == "Innenstadt-West"
    assert _tag_by_parent(DOC["contentTags"], "Veranstaltungskalender") == "Konzert / Musik"
    assert _tag_by_parent(DOC["contentTags"], "Nope") is None


# ── parse_event ──────────────────────────────────────────────────────────────

def test_parse_event() -> None:
    p = parse_event(DOC)
    assert p["title"] == "19. Fest der Chöre"
    assert p["category"] == "Konzert / Musik"
    assert p["stadtbezirk"] == "Innenstadt-West"
    assert p["free_of_charge"] is True
    assert p["url"].endswith("/termin_137340.html")
    assert p["url"].startswith("https://www.dortmund.de")


def test_parse_event_rejects_non_event() -> None:
    assert parse_event(NON_EVENT) is None


# ── emit ─────────────────────────────────────────────────────────────────────

def test_normalize(connector: DortmundEventsConnector) -> None:
    n = connector.normalize(DOC)
    assert n["label"] == "19. Fest der Chöre"
    assert n["valid_from"].year == 2027


@pytest.mark.asyncio
async def test_emit_event(connector: DortmundEventsConnector) -> None:
    node = (await connector.emit_entities(connector.normalize(DOC)))[0]
    assert isinstance(node, Event)
    assert node.source == "dortmund_veranstaltungskalender"
    assert node.properties["event_type"] == "public_event"
    assert node.properties["category"] == "Konzert / Musik"
    assert node.properties["stadtbezirk"] == "Innenstadt-West"
    assert node.inferred is False


@pytest.mark.asyncio
async def test_emit_no_edges(connector: DortmundEventsConnector) -> None:
    n = connector.normalize(DOC)
    nodes = await connector.emit_entities(n)
    assert await connector.emit_edges(n, nodes) == []
