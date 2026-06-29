"""
Precision/recall of the text linker on a hand-labeled corpus of realistic
Dortmund civic text (council minutes, tenders, police reports, roadworks).

Gold = the (target_id, relation) set each snippet SHOULD produce. The corpus
deliberately includes hard cases:
  - street vs. its containing district (longest-match must pick the street)
  - sub-string district names ("Innenstadt" inside "Innenstadt-Nord")
  - word-boundary traps ("Evingerfeld" must NOT match "Eving")
  - generic words ("Straße", "Markt") that must NOT match
  - ß / umlaut normalization
This is the regression harness CLAUDE.md requires for the resolution layer.
"""

from __future__ import annotations

from resolution.evaluation import aggregate, prf
from resolution.text_linker import build_gazetteer, find_mentions, node_text

# Gazetteer (id == label for readability). Districts first, then streets layered.
DISTRICTS = [
    {"id": "Innenstadt-Nord", "label": "Innenstadt-Nord"},
    {"id": "Innenstadt-West", "label": "Innenstadt-West"},
    {"id": "Innenstadt", "label": "Innenstadt"},
    {"id": "Eving", "label": "Eving"},
    {"id": "Brechten", "label": "Brechten"},
    {"id": "Hörde", "label": "Hörde"},
    {"id": "Hombruch", "label": "Hombruch"},
    {"id": "Aplerbeck", "label": "Aplerbeck"},
    {"id": "Scharnhorst", "label": "Scharnhorst"},
    {"id": "Lütgendortmund", "label": "Lütgendortmund"},
]
STREETS = [
    {"id": "Brechtener Straße", "label": "Brechtener Straße"},
    {"id": "Westfalendamm", "label": "Westfalendamm"},
    {"id": "Hombrucher Straße", "label": "Hombrucher Straße"},
    {"id": "Münsterstraße", "label": "Münsterstraße"},
    {"id": "Aplerbecker Marktplatz", "label": "Aplerbecker Marktplatz"},
]

# (text, {(target_id, relation), ...})
CORPUS: list[tuple[str, set[tuple[str, str]]]] = [
    ("Niederschrift der Bezirksvertretung Eving vom 13.02.", {("Eving", "mentions_area")}),
    ("Beschluss zur Sanierung der Brechtener Straße", {("Brechtener Straße", "mentions_street")}),
    ("Sitzung der BV Innenstadt-Nord", {("Innenstadt-Nord", "mentions_area")}),
    (
        "Halbseitige Sperrung Westfalendamm in Hörde",
        {("Westfalendamm", "mentions_street"), ("Hörde", "mentions_area")},
    ),
    ("Vergabe Kanalbau Hombrucher Straße, Hombruch", {("Hombrucher Straße", "mentions_street"), ("Hombruch", "mentions_area")}),
    ("Verkehrsunfall auf der Münsterstraße", {("Münsterstraße", "mentions_street")}),
    ("Umbau Aplerbecker Marktplatz", {("Aplerbecker Marktplatz", "mentions_street")}),
    ("Ausschuss tagt zu Projekten in Scharnhorst und Aplerbeck",
     {("Scharnhorst", "mentions_area"), ("Aplerbeck", "mentions_area")}),
    ("Sanierungsgebiet Lütgendortmund", {("Lütgendortmund", "mentions_area")}),
    ("Bauarbeiten Innenstadt-West", {("Innenstadt-West", "mentions_area")}),
    # ── hard negatives ──
    ("Arbeiten am Evingerfeld werden fortgesetzt", set()),       # not "Eving"
    ("Die Straße wird vollständig saniert", set()),               # generic "Straße"
    ("Eröffnung am Markt mit Festprogramm", set()),               # generic "Markt"
    ("Allgemeine Hinweise zur Tagesordnung", set()),              # nothing
    ("Hombruchfeld ist kein Stadtbezirk", set()),                 # boundary: not "Hombruch"
]


def _gazetteer():
    gaz = build_gazetteer(DISTRICTS, relation="mentions_area")
    build_gazetteer(STREETS, relation="mentions_street", into=gaz)
    return gaz


def test_corpus_micro_precision_recall() -> None:
    gaz = _gazetteer()
    scores = []
    failures = []
    for text, gold in CORPUS:
        predicted = {(tid, rel) for _term, tid, rel in find_mentions(text, gaz)}
        s = prf(predicted, gold)
        scores.append(s)
        if s.false_positives or s.false_negatives:
            failures.append((text, predicted, gold))

    micro = aggregate(scores)
    # High bar: the matcher should be both precise and complete on this corpus.
    assert micro.precision >= 0.95, (micro, failures)
    assert micro.recall >= 0.95, (micro, failures)


def test_street_beats_containing_district() -> None:
    gaz = _gazetteer()
    out = {(tid, rel) for _t, tid, rel in find_mentions("Brechtener Straße in Brechten", gaz)}
    # Both should appear: the street (specific) and the district it sits in.
    assert ("Brechtener Straße", "mentions_street") in out
    assert ("Brechten", "mentions_area") in out


def test_node_text_used_for_matching() -> None:
    gaz = _gazetteer()
    node = {"label": "TOP 5 Sanierung", "properties": {"gremium": "BV Hörde"}}
    out = {tid for _t, tid, _r in find_mentions(node_text(node), gaz)}
    assert "Hörde" in out


def test_no_false_positives_on_negatives() -> None:
    gaz = _gazetteer()
    for text, gold in CORPUS:
        if gold:
            continue
        predicted = find_mentions(text, gaz)
        assert predicted == [], (text, predicted)
