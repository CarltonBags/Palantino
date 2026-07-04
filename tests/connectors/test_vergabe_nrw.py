"""Frozen XML → normalized tests for the Vergabe.NRW connector. No network."""

import pytest

from connectors.vergabe_nrw.connector import VergabeNrwConnector, parse_award, parse_notice
from ontology.nodes import Tender

XML_DORTMUND = """<?xml version="1.0"?>
<ContractNotice>
  <cbc:ContractFolderID>abc-123</cbc:ContractFolderID>
  <cbc:IssueDate>2026-06-28+02:00</cbc:IssueDate>
  <cbc:SubTypeCode listName="notice-subtype">16</cbc:SubTypeCode>
  <cac:PostalAddress><cbc:CityName>Dortmund</cbc:CityName></cac:PostalAddress>
  <cac:ProcurementProject>
    <cbc:Name languageID="DEU">Erweiterung der Rettungswache 20, TGA HLS</cbc:Name>
    <cbc:Description>HLS Arbeiten</cbc:Description>
    <cbc:ItemClassificationCode listName="cpv">45000000</cbc:ItemClassificationCode>
  </cac:ProcurementProject>
  <cac:TenderSubmissionDeadlinePeriod>
    <cbc:EndDate>2026-07-14+02:00</cbc:EndDate>
  </cac:TenderSubmissionDeadlinePeriod>
</ContractNotice>
"""

XML_OTHER_CITY = XML_DORTMUND.replace("Dortmund", "Essen")


@pytest.fixture
def connector() -> VergabeNrwConnector:
    return VergabeNrwConnector()


def test_parse_dortmund_notice() -> None:
    parsed = parse_notice(XML_DORTMUND)
    assert parsed is not None
    assert parsed["folder_id"] == "abc-123"
    assert parsed["title"].startswith("Erweiterung der Rettungswache")
    assert parsed["tender_type"] == "works"
    assert parsed["buyer_city"] == "Dortmund"
    assert parsed["deadline"] == "2026-07-14+02:00"


def test_parse_skips_non_dortmund() -> None:
    assert parse_notice(XML_OTHER_CITY) is None


def test_normalize_uses_folder_id(connector: VergabeNrwConnector) -> None:
    n = connector.normalize(parse_notice(XML_DORTMUND))
    assert n["source_id"] == "abc-123"
    assert n["valid_from"].year == 2026 and n["valid_from"].month == 6


@pytest.mark.asyncio
async def test_emit_tender(connector: VergabeNrwConnector) -> None:
    n = connector.normalize(parse_notice(XML_DORTMUND))
    nodes = await connector.emit_entities(n)
    node = nodes[0]
    assert isinstance(node, Tender)
    assert node.source == "vergabe_nrw_metropole_ruhr"
    assert node.source_id == "abc-123"
    assert node.properties["tender_type"] == "works"
    assert node.inferred is False


@pytest.mark.asyncio
async def test_emit_no_edges(connector: VergabeNrwConnector) -> None:
    n = connector.normalize(parse_notice(XML_DORTMUND))
    nodes = await connector.emit_entities(n)
    assert await connector.emit_edges(n, nodes) == []


# ── Award notices ─────────────────────────────────────────────────────────────

XML_AWARD = """<?xml version="1.0"?>
<ContractAwardNotice>
  <cbc:ContractFolderID>award-42</cbc:ContractFolderID>
  <cbc:IssueDate>2026-06-20+02:00</cbc:IssueDate>
  <cac:ContractingParty><cac:Party/></cac:ContractingParty>
  <cbc:CityName>Dortmund</cbc:CityName>
  <cac:ProcurementProject>
    <cbc:Name>Estricharbeiten Schulzentrum</cbc:Name>
    <cbc:ItemClassificationCode>45000000</cbc:ItemClassificationCode>
  </cac:ProcurementProject>
  <efac:Organization>
    <cbc:ID schemeName="organization">ORG-0001</cbc:ID>
    <cac:PartyName><cbc:Name>Stadt Dortmund</cbc:Name></cac:PartyName>
    <cbc:CityName>Dortmund</cbc:CityName>
  </efac:Organization>
  <efac:Organization>
    <cbc:ID schemeName="organization">ORG-0002</cbc:ID>
    <cac:PartyName><cbc:Name>Estrich Meyer GmbH</cbc:Name></cac:PartyName>
    <cbc:StreetName>Beispielweg 3</cbc:StreetName>
    <cbc:CityName>Dortmund</cbc:CityName>
    <cbc:PostalZone>44135</cbc:PostalZone>
  </efac:Organization>
  <efac:TenderingParty>
    <cbc:ID schemeName="tenderingparty">TPA-0001</cbc:ID>
    <efac:Tenderer>
      <cbc:ID schemeName="organization">ORG-0002</cbc:ID>
    </efac:Tenderer>
  </efac:TenderingParty>
  <cbc:TenderAmount currencyID="EUR">98500.00</cbc:TenderAmount>
</ContractAwardNotice>
"""


def test_parse_award_winner() -> None:
    a = parse_award(XML_AWARD)
    assert a is not None and a["kind"] == "award"
    assert a["folder_id"] == "award-42"
    assert a["title"] == "Estricharbeiten Schulzentrum"
    assert a["amount_eur"] == "98500.00"
    assert [w["name"] for w in a["winners"]] == ["Estrich Meyer GmbH"]
    assert a["winners"][0]["postcode"] == "44135"


def test_parse_award_requires_dortmund() -> None:
    xml = XML_AWARD.replace("Dortmund", "Bochum")
    assert parse_award(xml) is None


def test_parse_award_requires_winner() -> None:
    xml = XML_AWARD.replace("efac:Tenderer>", "efac:Other>")
    assert parse_award(xml) is None
