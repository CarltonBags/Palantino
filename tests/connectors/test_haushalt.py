"""Frozen record → normalized/entity test for the Haushalt (budget) connector."""
import asyncio
from datetime import datetime, timezone

from connectors.haushalt.connector import HaushaltConnector

_REC = {
    "_dataset": "fb20-haushaltsplan-uebersicht-produktbereiche-2021",
    "_year": 2021,
    "produkt_bereich": "012",
    "bezeichnung": "Verkehrsflächen und -anlagen",
    "ordentliche_ertrage_inkl_finanzertrage": "58589697",
    "ordentliche_aufwendungen_inkl_zinsen_u_sonst_finanzaufw": "136532695",
    "uberschuss_fehlbedarf": "-77942998",
    "investive_einzahlungen": "15777340",
    "investive_auszahlungen": "36097370",
    "saldo_investitionstatigkeit": "-20320030",
    "kommune": "Dortmund",
}


def test_normalize_amounts() -> None:
    n = HaushaltConnector().normalize(_REC)
    assert n["label"] == "Verkehrsflächen und -anlagen (Haushalt 2021)"
    assert n["produkt_bereich"] == "012"
    assert n["aufwendungen_eur"] == 136532695.0
    assert n["saldo_eur"] == -77942998.0
    assert n["investive_auszahlungen_eur"] == 36097370.0
    assert n["valid_from"] == datetime(2021, 1, 1, tzinfo=timezone.utc)


def test_emit_budget_item() -> None:
    conn = HaushaltConnector()
    nodes = asyncio.run(conn.emit_entities(conn.normalize(_REC)))
    assert len(nodes) == 1
    node = nodes[0]
    assert node.node_type == "BudgetItem"
    assert node.properties["bezeichnung"] == "Verkehrsflächen und -anlagen"
    assert node.properties["aufwendungen_eur"] == 136532695.0
