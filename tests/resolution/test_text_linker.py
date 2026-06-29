"""Unit tests for the pure text→geo matcher (no DB)."""

from resolution.text_linker import (
    Gazetteer,
    build_gazetteer,
    find_mentions,
    node_text,
    normalize,
)

DISTRICTS = [
    {"id": "geo-nord", "label": "Innenstadt-Nord"},
    {"id": "geo-city", "label": "Innenstadt"},
    {"id": "geo-eving", "label": "Eving"},
    {"id": "geo-horde", "label": "Hörde"},
    {"id": "geo-brechten", "label": "Brechten"},
    {"id": "geo-x", "label": "Ev"},  # too short → dropped
]

STREETS = [
    {"id": "road-brecht", "label": "Brechtener Straße"},
    {"id": "road-west", "label": "Westfalendamm"},
]

HOERDE = "hörde".replace("ß", "ss")


def test_normalize_eszett_and_space() -> None:
    assert normalize("Brechtener  STRAßE") == "brechtener strasse"


def test_build_gazetteer_drops_short_terms() -> None:
    gaz = build_gazetteer(DISTRICTS)
    assert "ev" not in gaz.terms
    assert gaz.terms["eving"].node_id == "geo-eving"
    assert gaz.terms["eving"].relation == "mentions_area"


def test_find_mentions_district() -> None:
    gaz = build_gazetteer(DISTRICTS)
    out = find_mentions("Bezirksvertretung Eving am 13.02.", gaz)
    assert out == [("eving", "geo-eving", "mentions_area")]


def test_find_mentions_longest_wins() -> None:
    gaz = build_gazetteer(DISTRICTS)
    out = find_mentions("Sitzung der BV Innenstadt-Nord", gaz)
    assert out == [("innenstadt-nord", "geo-nord", "mentions_area")]


def test_find_mentions_word_boundary() -> None:
    gaz = build_gazetteer(DISTRICTS)
    assert find_mentions("Arbeiten am Evingerfeld", gaz) == []


def test_street_layer_overrides_and_wins_longest() -> None:
    gaz = build_gazetteer(DISTRICTS)
    build_gazetteer(STREETS, relation="mentions_street", into=gaz)
    # "Brechtener Straße" (street) beats the substring "Brechten" (district).
    out = find_mentions("Beschluss zur Brechtener Straße", gaz)
    assert out == [("brechtener strasse", "road-brecht", "mentions_street")]


def test_mixed_mentions() -> None:
    gaz = build_gazetteer(DISTRICTS)
    build_gazetteer(STREETS, relation="mentions_street", into=gaz)
    out = dict((t, (i, r)) for t, i, r in find_mentions("Westfalendamm in Hörde", gaz))
    assert out["westfalendamm"] == ("road-west", "mentions_street")
    assert out[HOERDE] == ("geo-horde", "mentions_area")


def test_empty_gazetteer() -> None:
    assert find_mentions("Eving", Gazetteer(terms={})) == []


def test_node_text_concatenates_label_and_props() -> None:
    node = {
        "label": "TOP 3 Straßenbau",
        "properties": {"gremium": "Bezirksvertretung Eving", "title": "Sanierung", "x": 5},
    }
    text = node_text(node)
    assert "Bezirksvertretung Eving" in text
    assert "Sanierung" in text
    assert "TOP 3" in text
    assert "5" not in text
