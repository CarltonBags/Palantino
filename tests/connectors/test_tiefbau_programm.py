"""Frozen record → normalized/entity test for the Tiefbau-Arbeitsprogramm
connector — checks the timeline collapse + the drucksachennummer link field."""
import asyncio

from connectors.tiefbau_programm.connector import TiefbauProgrammConnector

_REC = {
    "projektnummer": "8",
    "bezeichnung": "Märkische Straße, Ophoff, Brückenplatte 1",
    "stadtbezirk": "Innenstadt-Ost",
    "gewerk": "Straße komplex",
    "art": "9 Einzelmaßnahme Straße investiv",
    "2025": "Bau",
    "2026_i": "Bau",
    "2026_iv": "Schlussrechnung",
    "2027": "Schlussrechnung",
    "2028": "Ende der Maßnahme",
    "drucksachennummer": "17138-20",
    "massnahmenstatus": "in Ausführung",
    "prioritatenklasse": "A",
    "beschreibung": "Instandsetzung Brückenplatte",
    "link_zur_ds_nr": "https://example.org/ds/17138-20",
    "kommune": "Dortmund",
}


def test_normalize_timeline_and_ds() -> None:
    n = TiefbauProgrammConnector().normalize(_REC)
    assert n["label"] == "Märkische Straße, Ophoff, Brückenplatte 1"
    assert n["drucksachennummer"] == "17138-20"
    assert n["phasen"]["2025"] == "Bau"
    assert n["phasen"]["2026_iv"] == "Schlussrechnung"
    assert "kommune" not in n["phasen"]  # only year/phase columns collapse in
    assert n["source_url"] == "https://example.org/ds/17138-20"


def test_emit_planned_construction() -> None:
    conn = TiefbauProgrammConnector()
    nodes = asyncio.run(conn.emit_entities(conn.normalize(_REC)))
    assert len(nodes) == 1
    node = nodes[0]
    assert node.node_type == "ConstructionSite"
    assert node.properties["planned"] is True
    assert node.properties["drucksachennummer"] == "17138-20"
    assert node.properties["stadtbezirk"] == "Innenstadt-Ost"
