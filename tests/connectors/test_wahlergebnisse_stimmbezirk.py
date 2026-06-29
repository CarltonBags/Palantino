"""Frozen raw → normalized tests for the per-Stimmbezirk results connector."""

import pytest

from connectors.wahlergebnisse_stimmbezirk.connector import (
    WahlergebnisseStimmbezirkConnector,
    extract_party_votes,
)
from ontology.nodes import Event

RAW_2025 = {
    "_dataset": "fb33-rat-20250914-stimmbezirke",
    "_wahltermin": "2025-09-14",
    "wahltermin": "2025-09-14",
    "stadtbezirk_nr": 2,
    "stadtbezirk": "Innenstadt-Nord",
    "kommunalwahlbezirk_nr": "1",
    "kommunalwahlbezirk": "Kommunalwahlbezirk 1",
    "stimmbezirk": "1101",
    "wahlberechtigte": 1000,
    "wahler_innen": 226,
    "gultige_stimmen": 220,
    "ungultige_stimmen": 6,
    "spd": 65,
    "grune": 43,
    "cdu": 21,
    "die_linke": 70,
    "afd": None,
    "geo_point_2d": {"lon": 7.4531, "lat": 51.5327},
    "ostwert": "...",
    "kommune": "Dortmund",
}

RAW_2020_NOGEO = {
    "_dataset": "fb33-rat-20200913-stimmbezirke",
    "_wahltermin": "2020-09-13",
    "wahltermin": "2020-09-13",
    "stadtbezirk_nr": 20,
    "stadtbezirk": "Innenstadt-Nord",
    "stimmbezirk": "2001",
    "wahlberechtigte": 800,
    "wahler_innen": 400,
    "spd": 120,
    "cdu": 80,
    "kommune": "Dortmund",
}


@pytest.fixture
def connector() -> WahlergebnisseStimmbezirkConnector:
    return WahlergebnisseStimmbezirkConnector()


def test_extract_party_votes_drops_meta_and_nulls() -> None:
    votes = extract_party_votes(RAW_2025)
    assert votes == {"spd": 65, "grune": 43, "cdu": 21, "die_linke": 70}
    assert "wahlberechtigte" not in votes  # turnout metadata
    assert "afd" not in votes              # null dropped
    assert "stadtbezirk_nr" not in votes   # metadata


def test_normalize_turnout_and_geo(connector: WahlergebnisseStimmbezirkConnector) -> None:
    n = connector.normalize(RAW_2025)
    assert n["stimmbezirk"] == "1101"
    assert n["turnout_pct"] == 22.6   # 226/1000
    assert n["lon"] == 7.4531
    assert n["valid_from"].year == 2025


def test_normalize_no_geometry(connector: WahlergebnisseStimmbezirkConnector) -> None:
    n = connector.normalize(RAW_2020_NOGEO)
    assert n["lon"] is None and n["lat"] is None
    assert n["turnout_pct"] == 50.0
    assert n["stadtbezirk"] == "Innenstadt-Nord"


@pytest.mark.asyncio
async def test_emit_geo_event(connector: WahlergebnisseStimmbezirkConnector) -> None:
    node = (await connector.emit_entities(connector.normalize(RAW_2025)))[0]
    assert isinstance(node, Event)
    assert node.source == "opendata_dortmund_wahl_stimmbezirk"
    assert node.properties["event_type"] == "election_precinct"
    assert node.properties["party_votes"]["die_linke"] == 70
    assert node.geom["coordinates"] == [7.4531, 51.5327]
    assert node.inferred is False


@pytest.mark.asyncio
async def test_emit_no_geom_event_has_no_geom(connector: WahlergebnisseStimmbezirkConnector) -> None:
    node = (await connector.emit_entities(connector.normalize(RAW_2020_NOGEO)))[0]
    assert node.geom is None
    assert node.properties["stadtbezirk"] == "Innenstadt-Nord"


@pytest.mark.asyncio
async def test_emit_no_edges(connector: WahlergebnisseStimmbezirkConnector) -> None:
    n = connector.normalize(RAW_2025)
    nodes = await connector.emit_entities(n)
    assert await connector.emit_edges(n, nodes) == []
