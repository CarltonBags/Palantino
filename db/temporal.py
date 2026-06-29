"""
Temporal query helpers — point-in-time ("as of") reconstruction of the graph.

The writer is bitemporal/append-only (CLAUDE.md rule 2): a fact's `valid_from`/
`valid_to` bound when it was true in the world, and superseded versions are
closed (valid_to set) rather than deleted. That history is only useful if you can
ask "what did the graph look like on date X". This module builds the WHERE
fragment for that, for both the "current" case (valid_to IS NULL) and any past
instant.

Pure and unit-tested; callers pass the resulting SQL + params to asyncpg.
"""

from __future__ import annotations

from datetime import datetime, timezone


def parse_as_of(value: str | None) -> datetime | None:
    """Parse an ISO-8601 'as of' timestamp; None means 'current'. Raises ValueError."""
    if value is None or value == "":
        return None
    dt = datetime.fromisoformat(value)
    # Treat naive timestamps as UTC so comparisons are well-defined.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def validity_clause(
    as_of: datetime | None,
    alias: str = "",
    param_index: int = 1,
) -> tuple[str, list[datetime]]:
    """
    Build the validity WHERE fragment and its params.

    as_of is None  → only currently-valid rows (valid_to IS NULL).
    as_of is a time → rows whose [valid_from, valid_to) window contains it; rows
                      with NULL valid_from/valid_to are treated as open-ended.
    `alias` qualifies the columns (e.g. "n"); `param_index` is the $N to use
    (the same placeholder is referenced twice — asyncpg allows reuse).
    """
    col_from = f"{alias}.valid_from" if alias else "valid_from"
    col_to = f"{alias}.valid_to" if alias else "valid_to"

    if as_of is None:
        return f"{col_to} IS NULL", []

    fragment = (
        f"({col_from} IS NULL OR {col_from} <= ${param_index}) "
        f"AND ({col_to} IS NULL OR {col_to} > ${param_index})"
    )
    return fragment, [as_of]
