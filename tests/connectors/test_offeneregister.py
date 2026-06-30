"""Frozen record → normalized/entity test for the OffeneRegister connector,
incl. the GDPR guarantee that officers (private individuals) are never stored."""
import asyncio

from connectors.offeneregister.connector import (
    OffeneRegisterConnector,
    is_dortmund,
    parse_address,
    rechtsform,
)

_REC = {
    "name": "Beispiel Handel GmbH",
    "company_number": "R1234_HRB12345",
    "current_status": "currently registered",
    "jurisdiction_code": "de",
    "registered_address": "Kampstraße 10, 44137 Dortmund.",
    "officers": [{"name": "Max Mustermann", "other_attributes": {"city": "Dortmund"}}],
    "retrieved_at": "2024-01-01",
}


def test_address_and_city() -> None:
    assert parse_address("Kampstraße 10, 44137 Dortmund.") == ("Kampstraße 10", "44137", "Dortmund")
    assert is_dortmund("Kampstraße 10, 44137 Dortmund.")
    assert not is_dortmund("Waidmannstraße 1, 22769 Hamburg.")


def test_rechtsform() -> None:
    assert rechtsform("Beispiel Handel GmbH") == "GmbH"
    assert rechtsform("olly UG (haftungsbeschränkt)") == "UG (haftungsbeschränkt)"
    assert rechtsform("Turnverein 1878 e. V.") == "e. V."


def test_normalize_and_emit() -> None:
    conn = OffeneRegisterConnector()
    n = conn.normalize(_REC)
    assert n["source_id"] == "R1234_HRB12345"
    assert n["rechtsform"] == "GmbH"
    assert n["addr_postcode"] == "44137"
    nodes = asyncio.run(conn.emit_entities(n))
    assert len(nodes) == 1 and nodes[0].node_type == "Organization"
    assert nodes[0].properties["register_id"] == "R1234_HRB12345"
    assert nodes[0].properties["org_type"] == "company"
    # GDPR: no officer / private-individual name anywhere on the node
    blob = nodes[0].label + " " + " ".join(str(v) for v in nodes[0].properties.values())
    assert "Mustermann" not in blob and "Max" not in blob
