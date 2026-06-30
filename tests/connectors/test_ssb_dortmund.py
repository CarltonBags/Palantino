"""Frozen raw-HTML → normalized-output test for the SSB Dortmund connector,
including the GDPR guarantee that the named Ansprechpartner is dropped."""
import asyncio

from connectors.ssb_dortmund.connector import SSBDortmundConnector, parse_club

_BLOCK = """
<h2><a href="/startseite/vereine/vereinssuche/von_a___z?id=4064&s[]=14886&s[]=14888">Allgemeiner Turnverein Dorstfeld 1878 e. V.</a></h2>
<a class="pull-left" href="/startseite/vereine/vereinssuche/von_a___z?id=4064"><img src="/img/70x80/7552.jpg" width="70" height="80" alt="ATV Dorstfeld Logo" /></a>
</div>
<div><p>
<strong>Herr Peter Lemke</strong><br />
Kortental 7, 44149 Dortmund<br />
Tel.: 0231 20695142 oder 0170 2159815<br />
E-Mail: <a href="mailto:atv_dorstfeld_1878@dokom.net">atv_dorstfeld_1878@dokom.net</a><br />
Internet: <a href="http://www.atv-dorstfeld.de">www.atv-dorstfeld.de</a>
</p></div>
"""


def test_parse_club_fields() -> None:
    d = parse_club(_BLOCK)
    assert d["source_id"] == "4064"
    assert d["label"] == "Allgemeiner Turnverein Dorstfeld 1878 e. V."
    assert d["addr_street"] == "Kortental 7"
    assert d["addr_postcode"] == "44149"
    assert d["addr_city"] == "Dortmund"
    assert d["phone"] == "0231 20695142"
    assert d["email"] == "atv_dorstfeld_1878@dokom.net"
    assert d["website"] == "http://www.atv-dorstfeld.de"


def test_parse_club_drops_personal_name() -> None:
    # GDPR rule 5: the named Ansprechpartner must never be stored.
    d = parse_club(_BLOCK)
    blob = " ".join(str(v) for v in d.values())
    assert "Lemke" not in blob and "Peter" not in blob


def test_emit_entities_poi_club_sport() -> None:
    conn = SSBDortmundConnector()
    nodes = asyncio.run(conn.emit_entities(parse_club(_BLOCK)))
    assert len(nodes) == 1
    node = nodes[0]
    assert node.node_type == "POI"
    assert node.properties["club"] == "sport"
    assert node.properties["email"] == "atv_dorstfeld_1878@dokom.net"
