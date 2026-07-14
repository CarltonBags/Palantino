"""
Parse-result semantics for the LLM enrichment jobs — no DB required.

The done-markers depend on the distinction: None = unparseable output (leave
pending, retry on a later sweep); empty result = parsed fine, node genuinely
yields nothing (mark done, never re-send to the LLM).
"""

from reasoning.actor_extraction import _parse_list
from reasoning.resource_enrich import _parse_tags


def test_parse_list_valid() -> None:
    raw = '[{"name": "TU Dortmund", "type": "hochschule", "role": "forscht"}]'
    assert _parse_list(raw) == [
        {"name": "TU Dortmund", "type": "hochschule", "role": "forscht"}
    ]


def test_parse_list_fenced() -> None:
    raw = '```json\n[{"name": "BVB"}]\n```'
    assert _parse_list(raw) == [{"name": "BVB"}]


def test_parse_list_empty_is_done() -> None:
    # a parsed empty list means "no actors in this article" — NOT a failure
    assert _parse_list("[]") == []


def test_parse_list_garbage_is_retryable() -> None:
    assert _parse_list("Es gibt keine Akteure.") is None
    assert _parse_list("[{broken json") is None
    assert _parse_list("") is None


def test_parse_tags_valid() -> None:
    raw = '{"needs": ["sponsoring"], "offers": ["publikum"]}'
    assert _parse_tags(raw) == (["sponsoring"], ["publikum"])


def test_parse_tags_unknown_tags_dropped() -> None:
    raw = '{"needs": ["not_a_real_tag"], "offers": ["publikum"]}'
    assert _parse_tags(raw) == ([], ["publikum"])


def test_parse_tags_empty_is_done() -> None:
    assert _parse_tags('{"needs": [], "offers": []}') == ([], [])


def test_parse_tags_garbage_is_retryable() -> None:
    assert _parse_tags("keine Tags") is None
    assert _parse_tags('{"needs": [broken') is None


def test_problem_parse_semantics() -> None:
    from reasoning.problem_extraction import _parse_list as parse_problems

    raw = '[{"name": "Leerstand City-Passagen", "theme": "Leerstand", "needs": ["einzelhandel"]}]'
    assert parse_problems(raw)[0]["name"] == "Leerstand City-Passagen"
    assert parse_problems("[]") == []          # parsed, no problem → mark done
    assert parse_problems("kein Problem") is None  # unparseable → retry
