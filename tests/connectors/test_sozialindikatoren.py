"""Frozen record → normalized/entity test for the Sozialindikatoren connector."""
import asyncio
from datetime import datetime, timezone

from connectors.sozialindikatoren.connector import SozialindikatorenConnector

_REC = {
    "_dataset": "sozialindikatoren-in-den-stadtbezirken-2023",
    "_year": 2023,
    "stadtbezirk_nr": "10",
    "stadtbezirk": "Innenstadt-West",
    "quote_leistungen_zur_sicherung_des_lebensunterhalts": "14.9",
    "sozialgeldquote": "27.0",
    "grundsicherung_im_alter_quote": "10.6",
    "kommune": "Dortmund",
}


def test_normalize() -> None:
    n = SozialindikatorenConnector().normalize(_REC)
    assert n["label"] == "Innenstadt-West Sozialindikatoren 2023"
    assert n["stadtbezirk_nr"] == "10"
    assert n["leistungen_lebensunterhalt_quote"] == "14.9"
    assert n["sozialgeldquote"] == "27.0"
    assert n["valid_from"] == datetime(2023, 1, 1, tzinfo=timezone.utc)


def test_emit_geoarea_snapshot() -> None:
    conn = SozialindikatorenConnector()
    nodes = asyncio.run(conn.emit_entities(conn.normalize(_REC)))
    assert len(nodes) == 1
    node = nodes[0]
    assert node.node_type == "GeoArea"
    assert node.properties["area_type"] == "stadtbezirk_sozialindikatoren"
    assert node.properties["grundsicherung_im_alter_quote"] == "10.6"
    assert node.properties["stadtbezirk"] == "Innenstadt-West"
