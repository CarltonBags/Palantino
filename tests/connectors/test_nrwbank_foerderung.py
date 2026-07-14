"""Frozen HTML → schema tests for the NRW.BANK funding connector. No network."""

from connectors.nrwbank_foerderung.connector import page_text, parse_norm

HTML = """<html><head><title>Test-Kredit - NRW.BANK</title></head><body>
<main><nav>menu</nav><h1>Test-Kredit</h1>
<p>Wer wird gefördert? Kleine Unternehmen. Was wird gefördert? Investitionen.</p>
<footer>foot</footer></main></body></html>"""


def test_page_text_strips_chrome() -> None:
    title, text = page_text(HTML)
    assert title == "Test-Kredit"
    assert "menu" not in text and "foot" not in text
    assert "Wer wird gefördert?" in text


def test_parse_norm_validates_vocab() -> None:
    raw = """{"level": "bund", "funder": "KfW", "target_groups": ["kmu", "erfundene_gruppe"],
      "themes": ["Digitalisierung"], "funding_type": "darlehen",
      "max_amount_eur": 100000, "open_ended": true, "deadline": null,
      "summary": "Kredit für KMU."}"""
    d = parse_norm(raw)
    assert d is not None
    assert d["target_groups"] == ["kmu"]  # unknown vocab dropped
    assert d["level"] == "bund" and d["funding_type"] == "darlehen"


def test_parse_norm_defaults_and_garbage() -> None:
    assert parse_norm("kein json") is None
    d = parse_norm('{"level": "galaxis", "funding_type": "wunder"}')
    assert d["level"] == "land" and d["funding_type"] is None
