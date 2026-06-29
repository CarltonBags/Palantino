"""
Frozen raw-input → normalized-output tests for the Gremientermine connector.
No network calls.
"""

import pytest

from connectors.gremientermine.connector import GremienTermineConnector, _make_source_id
from ontology.nodes import Meeting

RAW_FULL = {
    "datum": "2025-09-23",
    "beginn": "15:30:00",
    "gremium": "Ausschuss für Kultur, Sport und Freizeit",
    "sitzungsort": "Kongresszentrum Westfalenhallen, Halle 1U, Rheinlanddamm 200, 44139 Dortmund",
    "kommune": "Dortmund",
}

RAW_NO_LOCATION = {
    "datum": "2025-10-02",
    "beginn": "16:00:00",
    "gremium": "Behindertenpolitisches Netzwerk",
    "sitzungsort": None,
    "kommune": "Dortmund",
}


@pytest.fixture
def connector() -> GremienTermineConnector:
    return GremienTermineConnector()


# ── source_id ─────────────────────────────────────────────────────────────────

def test_source_id_deterministic() -> None:
    a = _make_source_id("2025-09-23", "Rat der Stadt", "15:00:00")
    b = _make_source_id("2025-09-23", "Rat der Stadt", "15:00:00")
    assert a == b


def test_source_id_differs_by_gremium() -> None:
    a = _make_source_id("2025-09-23", "Rat der Stadt", "15:00:00")
    b = _make_source_id("2025-09-23", "Hauptausschuss", "15:00:00")
    assert a != b


def test_source_id_differs_by_time() -> None:
    a = _make_source_id("2025-09-23", "Rat der Stadt", "15:00:00")
    b = _make_source_id("2025-09-23", "Rat der Stadt", "16:00:00")
    assert a != b


# ── normalize ─────────────────────────────────────────────────────────────────

def test_normalize_basic(connector: GremienTermineConnector) -> None:
    norm = connector.normalize(RAW_FULL)
    assert norm["gremium"] == "Ausschuss für Kultur, Sport und Freizeit"
    assert norm["datum"] == "2025-09-23"
    assert norm["beginn"] == "15:30:00"
    assert norm["sitzungsort"] == "Kongresszentrum Westfalenhallen, Halle 1U, Rheinlanddamm 200, 44139 Dortmund"


def test_normalize_label_contains_gremium_and_date(connector: GremienTermineConnector) -> None:
    norm = connector.normalize(RAW_FULL)
    assert "Ausschuss für Kultur, Sport und Freizeit" in norm["label"]
    assert "2025-09-23" in norm["label"]


def test_normalize_valid_from_parsed(connector: GremienTermineConnector) -> None:
    norm = connector.normalize(RAW_FULL)
    assert norm["valid_from"] is not None
    assert norm["valid_from"].year == 2025
    assert norm["valid_from"].hour == 15
    assert norm["valid_from"].minute == 30


def test_normalize_null_location(connector: GremienTermineConnector) -> None:
    norm = connector.normalize(RAW_NO_LOCATION)
    assert norm["sitzungsort"] is None


def test_normalize_source_id_present(connector: GremienTermineConnector) -> None:
    norm = connector.normalize(RAW_FULL)
    assert norm["source_id"] is not None
    assert len(norm["source_id"]) == 20


# ── emit ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_emit_returns_meeting(connector: GremienTermineConnector) -> None:
    norm = connector.normalize(RAW_FULL)
    nodes = await connector.emit_entities(norm)
    assert len(nodes) == 1
    assert isinstance(nodes[0], Meeting)


@pytest.mark.asyncio
async def test_emit_meeting_provenance(connector: GremienTermineConnector) -> None:
    norm = connector.normalize(RAW_FULL)
    nodes = await connector.emit_entities(norm)
    node = nodes[0]
    assert node.source == "opendata_dortmund_gremientermine"
    assert node.source_url is not None
    assert node.observed_at is not None
    assert node.inferred is False


@pytest.mark.asyncio
async def test_emit_meeting_properties(connector: GremienTermineConnector) -> None:
    norm = connector.normalize(RAW_FULL)
    nodes = await connector.emit_entities(norm)
    props = nodes[0].properties
    assert props["gremium"] == "Ausschuss für Kultur, Sport und Freizeit"
    assert props["sitzungsort"] is not None
    assert props["meeting_type"] == "scheduled"


@pytest.mark.asyncio
async def test_emit_meeting_valid_from(connector: GremienTermineConnector) -> None:
    norm = connector.normalize(RAW_FULL)
    nodes = await connector.emit_entities(norm)
    assert nodes[0].valid_from is not None
    assert nodes[0].valid_from.year == 2025


@pytest.mark.asyncio
async def test_emit_no_edges(connector: GremienTermineConnector) -> None:
    norm = connector.normalize(RAW_FULL)
    nodes = await connector.emit_entities(norm)
    edges = await connector.emit_edges(norm, nodes)
    assert edges == []
