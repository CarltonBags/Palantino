"""
Frozen HTML → parsed tests for the Gremienniederschriften connector. No network.

The HTML snippet mimics the legacy Domino minutes markup: <font> blocks split by
<br>, TOPs as "N.M\tTitle", Beschlüsse with "(Dr. Nr.: NNNNN-YY)" and a vote
keyword shortly after the Drucksachen-Nr in the protocol section.
"""

import pytest

from connectors.gremienniederschriften.connector import (
    GremienNiederschriftenConnector,
    extract_document_id,
    parse_minutes,
)
from ontology.nodes import AgendaItem, Meeting, Resolution

LINK = (
    "https://rathaus.dortmund.de/dosys/doRat.nsf/NiederschriftXP.xsp"
    "?action=openDocument&documentId=DEADBEEF1234"
)

# Agenda listing + protocol section with two Beschlüsse, one voted, one not.
HTML = """
<html><body>
<font size="1" face="Arial"><b>Tagesordnung (öffentlich):</b></font><br>
<font size="1" face="Arial">3.1\tParksituation an der Musterstraße Beschluss (Dr. Nr.: 06123-11)</font><br>
<font size="1" face="Arial">3.2\tBericht der Verwaltung (Drucksache Nr.: 08800-13)</font><br>
<font size="1" face="Arial"><b>Protokoll:</b></font><br>
<font size="1" face="Arial">Zu Dr. Nr.: 06123-11 wird der Antrag einstimmig genehmigt.</font><br>
<font size="1" face="Arial">Drucksache Nr.: 08800-13 wird zur Kenntnis genommen.</font><br>
</body></html>
"""


@pytest.fixture
def connector() -> GremienNiederschriftenConnector:
    return GremienNiederschriftenConnector()


# ── helpers ───────────────────────────────────────────────────────────────────

def test_extract_document_id() -> None:
    assert extract_document_id(LINK) == "DEADBEEF1234"
    assert extract_document_id("https://x/y?foo=bar") is None


def test_parse_minutes_structure() -> None:
    agenda = parse_minutes(HTML)
    numbers = {a["number"] for a in agenda}
    assert "3.1" in numbers and "3.2" in numbers


def test_parse_minutes_drucksachen_and_vote() -> None:
    agenda = {a["number"]: a for a in parse_minutes(HTML)}
    top31 = agenda["3.1"]
    nrs = {d["nr"]: d for d in top31["drucksachen"]}
    assert "06123-11" in nrs
    # einstimmig genehmigt → passed True
    assert nrs["06123-11"]["passed"] is True
    # 08800-13 only "zur Kenntnis genommen" → no vote keyword → None
    agenda32 = agenda["3.2"]
    nrs32 = {d["nr"]: d for d in agenda32["drucksachen"]}
    assert nrs32["08800-13"]["passed"] is None


# ── emit ──────────────────────────────────────────────────────────────────────

def _norm(connector: GremienNiederschriftenConnector) -> dict:
    raw = {
        "document_id": "DEADBEEF1234",
        "link": LINK,
        "gremium": "Bezirksvertretung Eving",
        "datum": "2013-02-13",
        "agenda": parse_minutes(HTML),
    }
    return connector.normalize(raw)


@pytest.mark.asyncio
async def test_emit_nodes(connector: GremienNiederschriftenConnector) -> None:
    nodes = await connector.emit_entities(_norm(connector))
    meetings = [n for n in nodes if isinstance(n, Meeting)]
    agenda = [n for n in nodes if isinstance(n, AgendaItem)]
    resolutions = [n for n in nodes if isinstance(n, Resolution)]
    assert len(meetings) == 1
    assert len(agenda) == 2
    assert len(resolutions) == 2
    # provenance: every node points at the minutes document (rule 1)
    for n in nodes:
        assert n.source == "ris_dortmund_niederschriften"
        assert n.source_url == LINK
        assert n.inferred is False


@pytest.mark.asyncio
async def test_resolution_carries_drucksache(connector: GremienNiederschriftenConnector) -> None:
    nodes = await connector.emit_entities(_norm(connector))
    res = {n.properties["resolution_number"]: n for n in nodes if isinstance(n, Resolution)}
    assert res["06123-11"].properties["passed"] is True
    assert res["06123-11"].source_id == "06123-11"
    assert res["08800-13"].properties["passed"] is None


@pytest.mark.asyncio
async def test_emit_edges_link_graph(connector: GremienNiederschriftenConnector) -> None:
    norm = _norm(connector)
    nodes = await connector.emit_entities(norm)
    edges = await connector.emit_edges(norm, nodes)
    relations = {e.properties.get("relation") for e in edges}
    assert "agenda_of" in relations
    assert "decided_in" in relations
    for e in edges:
        assert e.source == "ris_dortmund_niederschriften"
        assert e.source_url == LINK
        assert e.observed_at is not None
