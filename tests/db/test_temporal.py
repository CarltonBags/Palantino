"""Unit tests for the point-in-time query helpers (no DB)."""

from datetime import datetime, timezone

import pytest

from db.temporal import parse_as_of, validity_clause


# ── parse_as_of ──────────────────────────────────────────────────────────────

def test_parse_none_and_empty() -> None:
    assert parse_as_of(None) is None
    assert parse_as_of("") is None


def test_parse_naive_becomes_utc() -> None:
    dt = parse_as_of("2024-06-01T12:00:00")
    assert dt == datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)


def test_parse_aware_preserved() -> None:
    dt = parse_as_of("2024-06-01T12:00:00+02:00")
    assert dt.utcoffset().total_seconds() == 7200


def test_parse_invalid_raises() -> None:
    with pytest.raises(ValueError):
        parse_as_of("not-a-date")


# ── validity_clause ──────────────────────────────────────────────────────────

def test_current_clause_no_params() -> None:
    sql, params = validity_clause(None)
    assert sql == "valid_to IS NULL"
    assert params == []


def test_current_clause_with_alias() -> None:
    sql, _ = validity_clause(None, alias="n")
    assert sql == "n.valid_to IS NULL"


def test_as_of_clause_uses_single_param_twice() -> None:
    dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
    sql, params = validity_clause(dt, param_index=3)
    assert params == [dt]
    assert sql.count("$3") == 2
    assert "valid_from IS NULL OR valid_from <= $3" in sql
    assert "valid_to IS NULL OR valid_to > $3" in sql


def test_as_of_clause_with_alias() -> None:
    dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
    sql, _ = validity_clause(dt, alias="e", param_index=2)
    assert "e.valid_from" in sql and "e.valid_to" in sql
    assert "$2" in sql
