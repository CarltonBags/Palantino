"""Unit tests for the pure scanner helpers (no DB, no Claude)."""

from reasoning.scanner import candidate_key, derive_title, parse_insights


# ── candidate_key ────────────────────────────────────────────────────────────

def test_candidate_key_order_independent() -> None:
    a = candidate_key("inefficiency", ["n1", "n2", "n3"])
    b = candidate_key("inefficiency", ["n3", "n1", "n2"])
    assert a == b


def test_candidate_key_differs_by_type() -> None:
    assert candidate_key("inefficiency", ["n1"]) != candidate_key("synergy", ["n1"])


def test_candidate_key_differs_by_nodes() -> None:
    assert candidate_key("synergy", ["n1"]) != candidate_key("synergy", ["n2"])


# ── parse_insights ───────────────────────────────────────────────────────────

def test_parse_insights_object() -> None:
    raw = '{"insights": [{"type": "inefficiency", "description": "x", "confidence": 0.8}]}'
    out = parse_insights(raw)
    assert len(out) == 1 and out[0]["confidence"] == 0.8


def test_parse_insights_bare_list() -> None:
    raw = '[{"description": "a"}, {"description": "b"}]'
    assert len(parse_insights(raw)) == 2


def test_parse_insights_code_fence() -> None:
    raw = '```json\n{"insights": [{"description": "fenced"}]}\n```'
    out = parse_insights(raw)
    assert out and out[0]["description"] == "fenced"


def test_parse_insights_surrounding_noise() -> None:
    raw = 'Here is the result:\n{"insights": [{"description": "noisy"}]}\nDone.'
    out = parse_insights(raw)
    assert out and out[0]["description"] == "noisy"


def test_parse_insights_garbage_returns_empty() -> None:
    assert parse_insights("not json at all") == []
    assert parse_insights("") == []


def test_parse_insights_drops_non_dicts() -> None:
    raw = '{"insights": ["bad", {"description": "good"}]}'
    out = parse_insights(raw)
    assert out == [{"description": "good"}]


# ── derive_title ─────────────────────────────────────────────────────────────

def test_derive_title_explicit() -> None:
    assert derive_title({"title": "My Title", "description": "x"}) == "My Title"


def test_derive_title_from_first_sentence() -> None:
    title = derive_title({"description": "Roadworks overlap. More detail here."})
    assert title == "Roadworks overlap"


def test_derive_title_fallback() -> None:
    assert derive_title({}) == "Insight"
