"""Frozen record → normalized/entity test for the sports-fixtures connector."""
import asyncio

from connectors.sports_fixtures.connector import SportsFixturesConnector

_MATCH = {
    "matchID": 71234,
    "matchDateTime": "2025-08-31T17:30:00",
    "team1": {"teamName": "Borussia Dortmund"},
    "team2": {"teamName": "1. FC Union Berlin"},
    "leagueName": "1. Fußball-Bundesliga 2025/2026",
    "group": {"groupName": "3. Spieltag"},
    "matchIsFinished": True,
    "matchResults": [{"pointsTeam1": 1, "pointsTeam2": 0, "resultName": "Endergebnis"}],
    "numberOfViewers": 81365,
    "_team": "Borussia Dortmund",
    "_cfg": {"venue": "Signal Iduna Park", "lat": 51.4926, "lon": 7.4517},
}


def test_normalize() -> None:
    n = SportsFixturesConnector().normalize(_MATCH)
    assert n["label"] == "Borussia Dortmund – 1. FC Union Berlin"
    assert n["source_id"] == "71234"
    assert n["venue"] == "Signal Iduna Park"
    assert n["result"] == "1:0"
    assert n["valid_from"].year == 2025 and n["valid_from"].month == 8


def test_emit_event_with_geo() -> None:
    conn = SportsFixturesConnector()
    nodes = asyncio.run(conn.emit_entities(conn.normalize(_MATCH)))
    assert len(nodes) == 1
    node = nodes[0]
    assert node.node_type == "Event"
    assert node.properties["event_type"] == "sports_fixture"
    assert node.properties["category"] == "Sport"
    assert node.geom == {"type": "Point", "coordinates": [7.4517, 51.4926]}
