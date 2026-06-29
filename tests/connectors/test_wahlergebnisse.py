"""Frozen raw → normalized tests for the election-results connector. No network."""

import pytest

from connectors.wahlergebnisse.connector import (
    WahlergebnisseConnector,
    extract_party_shares,
    extract_seats,
)
from ontology.nodes import Event

RAW_SHARE = {
    "_dataset": "kommunalwahlen-wahlergebnisse",
    "_kind": "result",
    "_type": "kommunalwahl",
    "tag_der_wahl": "2020-09-13",
    "gultige_stimmen": 300000,
    "spd_absolut": 90000,
    "spd_in": 30.0,
    "cdu_absolut": 75000,
    "cdu_in": 25.0,
    "grune_absolut": 60000,
    "grune_in": 20.0,
    "afd_absolut": None,
    "afd_in": None,
    "erzeugt_am": "16.11.2023",
    "kommune": "Dortmund",
}

RAW_SEATS = {
    "_dataset": "kommunalwahlen-ratsmitglieder",
    "_kind": "seats",
    "_type": "council_composition",
    "tag_der_wahl": "2020-09-13",
    "sitze_insgesamt": 90,
    "spd": 26,
    "cdu": 21,
    "grune": 18,
    "afd": None,
    "erzeugt_am": "16.11.2023",
    "kommune": "Dortmund",
}


@pytest.fixture
def connector() -> WahlergebnisseConnector:
    return WahlergebnisseConnector()


# ── helpers ───────────────────────────────────────────────────────────────────

def test_extract_party_shares_skips_totals_and_nulls() -> None:
    shares = extract_party_shares(RAW_SHARE)
    assert "spd" in shares and shares["spd"]["pct"] == 30.0
    assert "afd" not in shares          # null dropped
    assert "gultige" not in shares      # total dropped


def test_extract_seats_skips_metadata_and_nulls() -> None:
    seats = extract_seats(RAW_SEATS)
    assert seats == {"spd": 26, "cdu": 21, "grune": 18}
    assert "sitze_insgesamt" not in seats
    assert "afd" not in seats


# ── normalize ─────────────────────────────────────────────────────────────────

def test_normalize_result(connector: WahlergebnisseConnector) -> None:
    n = connector.normalize(RAW_SHARE)
    assert n["kind"] == "result"
    assert n["valid_from"].year == 2020
    assert n["party_shares"]["cdu"]["votes"] == 75000


def test_normalize_seats(connector: WahlergebnisseConnector) -> None:
    n = connector.normalize(RAW_SEATS)
    assert n["kind"] == "seats"
    assert n["seats_total"] == 90
    assert n["party_seats"]["spd"] == 26


# ── emit ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_emit_result_event(connector: WahlergebnisseConnector) -> None:
    node = (await connector.emit_entities(connector.normalize(RAW_SHARE)))[0]
    assert isinstance(node, Event)
    assert node.source == "opendata_dortmund_wahlergebnisse"
    assert node.properties["event_type"] == "election"
    assert node.properties["election_type"] == "kommunalwahl"
    assert node.inferred is False


@pytest.mark.asyncio
async def test_emit_seats_event(connector: WahlergebnisseConnector) -> None:
    node = (await connector.emit_entities(connector.normalize(RAW_SEATS)))[0]
    assert node.properties["event_type"] == "council_composition"
    assert node.properties["party_seats"]["cdu"] == 21
    assert node.properties["seats_total"] == 90


@pytest.mark.asyncio
async def test_emit_no_edges(connector: WahlergebnisseConnector) -> None:
    n = connector.normalize(RAW_SHARE)
    nodes = await connector.emit_entities(n)
    assert await connector.emit_edges(n, nodes) == []
