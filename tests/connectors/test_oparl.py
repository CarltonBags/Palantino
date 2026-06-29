"""
Frozen raw-input → normalized-output tests for the OParl connector.
No network calls.
"""

import pytest

from connectors.oparl.connector import OParlConnector
from ontology.nodes import AgendaItem, Meeting, Organization, Person, Resolution


@pytest.fixture
def connector() -> OParlConnector:
    return OParlConnector()


ORGANIZATION_RAW = {
    "oparl_type": "Organization",
    "data": {
        "id": "https://ratsinfo.dortmund.de/oparl/v1.1/organization/42",
        "name": "Ausschuss für Stadtentwicklung",
        "organizationType": "committee",
        "modified": "2024-03-01T10:00:00Z",
        "membership": [],
    },
}

PERSON_RAW = {
    "oparl_type": "Person",
    "data": {
        "id": "https://ratsinfo.dortmund.de/oparl/v1.1/person/7",
        "name": "Erika Mustermann",
        "title": "Ratsfrau",
        "modified": "2024-02-15T08:00:00Z",
        "membership": [
            {
                "organization": {
                    "classification": "party",
                    "shortName": "SPD",
                    "name": "Sozialdemokratische Partei Deutschlands",
                }
            }
        ],
    },
}

MEETING_RAW = {
    "oparl_type": "Meeting",
    "data": {
        "id": "https://ratsinfo.dortmund.de/oparl/v1.1/meeting/100",
        "name": "Sitzung des Rates der Stadt Dortmund",
        "start": "2024-04-10T16:00:00Z",
        "end": "2024-04-10T19:00:00Z",
        "organization": ["https://ratsinfo.dortmund.de/oparl/v1.1/organization/1"],
        "agendaItem": [
            {
                "id": "https://ratsinfo.dortmund.de/oparl/v1.1/agendaitem/200",
                "number": "1.1",
                "name": "Bericht des Bürgermeisters",
                "public": True,
                "resolution": [
                    {
                        "id": "https://ratsinfo.dortmund.de/oparl/v1.1/paper/300",
                        "name": "Beschluss zum Haushalt 2025",
                        "resolutionNumber": "2024/042",
                    }
                ],
            }
        ],
    },
}

DELETED_RAW = {
    "oparl_type": "Organization",
    "data": {
        "id": "https://ratsinfo.dortmund.de/oparl/v1.1/organization/99",
        "name": "Deleted org",
        "deleted": True,
    },
}


# ── normalize ──────────────────────────────────────────────────────────────────

def test_normalize_organization(connector: OParlConnector) -> None:
    norm = connector.normalize(ORGANIZATION_RAW)
    assert norm["oparl_type"] == "Organization"
    assert norm["label"] == "Ausschuss für Stadtentwicklung"
    assert norm["org_type"] == "committee"
    assert norm["deleted"] is False


def test_normalize_person_extracts_party(connector: OParlConnector) -> None:
    norm = connector.normalize(PERSON_RAW)
    assert norm["label"] == "Erika Mustermann"
    assert norm["party"] == "SPD"
    assert norm["role"] == "Ratsfrau"


def test_normalize_meeting_extracts_items_and_resolutions(connector: OParlConnector) -> None:
    norm = connector.normalize(MEETING_RAW)
    assert norm["label"] == "Sitzung des Rates der Stadt Dortmund"
    assert len(norm["agenda_items"]) == 1
    assert len(norm["resolutions"]) == 1
    assert norm["resolutions"][0]["resolution"]["resolutionNumber"] == "2024/042"


def test_normalize_deleted_flag(connector: OParlConnector) -> None:
    norm = connector.normalize(DELETED_RAW)
    assert norm["deleted"] is True


# ── emit ───────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_emit_organization(connector: OParlConnector) -> None:
    norm = connector.normalize(ORGANIZATION_RAW)
    nodes = await connector.emit_entities(norm)
    assert len(nodes) == 1
    assert isinstance(nodes[0], Organization)
    assert nodes[0].source == "oparl_dortmund"
    assert nodes[0].inferred is False


@pytest.mark.asyncio
async def test_emit_person(connector: OParlConnector) -> None:
    norm = connector.normalize(PERSON_RAW)
    nodes = await connector.emit_entities(norm)
    assert len(nodes) == 1
    node = nodes[0]
    assert isinstance(node, Person)
    assert node.properties["party"] == "SPD"
    assert node.properties["role"] == "Ratsfrau"


@pytest.mark.asyncio
async def test_emit_meeting_produces_multiple_nodes(connector: OParlConnector) -> None:
    norm = connector.normalize(MEETING_RAW)
    nodes = await connector.emit_entities(norm)
    types = {type(n).__name__ for n in nodes}
    assert "Meeting" in types
    assert "AgendaItem" in types
    assert "Resolution" in types


@pytest.mark.asyncio
async def test_emit_resolution_has_number(connector: OParlConnector) -> None:
    norm = connector.normalize(MEETING_RAW)
    nodes = await connector.emit_entities(norm)
    res = next(n for n in nodes if isinstance(n, Resolution))
    assert res.properties["resolution_number"] == "2024/042"


@pytest.mark.asyncio
async def test_deleted_emits_nothing(connector: OParlConnector) -> None:
    norm = connector.normalize(DELETED_RAW)
    nodes = await connector.emit_entities(norm)
    assert nodes == []


@pytest.mark.asyncio
async def test_meeting_valid_from_parsed(connector: OParlConnector) -> None:
    norm = connector.normalize(MEETING_RAW)
    nodes = await connector.emit_entities(norm)
    meeting = next(n for n in nodes if isinstance(n, Meeting))
    assert meeting.valid_from is not None
    assert meeting.valid_from.year == 2024
