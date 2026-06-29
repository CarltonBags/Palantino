"""
Text → entity linker (entity resolution, the project's core problem).

Bridges nodes that are only *text* (Meetings, Resolutions, AgendaItems, Tenders,
police/news Events) to the geographic spine by spotting place mentions in their
label + key text properties and emitting RELATES_TO edges to the matching
GeoArea. Because GeoAreas already link to physical nodes via LOCATED_IN, this is
what lets the reasoning scanner see a council meeting and the roadworks in that
district as one connected subgraph.

These are inferences, not source facts: edges are inferred=True with a confidence
and a reasoning_trace naming the matched term (rule 3).

v1 gazetteer = Dortmund district names (Stadtbezirk + statistischer Bezirk), which
are already loaded as GeoArea nodes. The matcher is generic, so a street gazetteer
(from an ODS fb62-strassen connector) can be added later without code changes.

The matcher (`normalize`, `build_gazetteer`, `find_mentions`) is pure and
unit-tested; the DB pass is lazy-imported so tests need no psycopg.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

# Node types that are essentially text and should be linked to geography.
TEXT_NODE_TYPES = ("Meeting", "Resolution", "AgendaItem", "Tender", "Event")
# Properties (besides label) worth scanning for place mentions, per the schema.
TEXT_PROPERTY_KEYS = ("gremium", "title", "description", "reason", "subtitle")
# Gazetteer terms shorter than this are too ambiguous to match safely.
MIN_TERM_LEN = 4


def normalize(text: str) -> str:
    """Lowercase, ß→ss, strip accents-of-convenience, collapse whitespace."""
    t = (text or "").lower().replace("ß", "ss")
    return re.sub(r"\s+", " ", t).strip()


@dataclass
class Entry:
    node_id: str
    relation: str  # mentions_area | mentions_street


@dataclass
class Gazetteer:
    # normalized term -> Entry(node_id, relation)
    terms: dict[str, Entry]
    _compiled: re.Pattern[str] | None = None

    def pattern(self) -> re.Pattern[str]:
        if self._compiled is None:
            # Longest terms first so "brechtener strasse" beats "brechten" and
            # "innenstadt-nord" beats "innenstadt".
            ordered = sorted(self.terms, key=len, reverse=True)
            alt = "|".join(re.escape(t) for t in ordered)
            # term not flanked by word chars (hyphen counts as a boundary)
            self._compiled = re.compile(rf"(?<!\w)({alt})(?!\w)")
        return self._compiled


def build_gazetteer(
    rows: list[dict[str, Any]],
    relation: str = "mentions_area",
    into: Gazetteer | None = None,
) -> Gazetteer:
    """
    rows: [{id, label}]. Builds/extends a normalized term→Entry map, dropping
    terms shorter than MIN_TERM_LEN. When extending (`into`), later terms
    override earlier ones on collision — so a street layer on top of districts
    wins, streets being the more specific match. Within one call the first wins.
    """
    gaz = into or Gazetteer(terms={})
    for r in rows:
        name = normalize(str(r.get("label", "")))
        if len(name) < MIN_TERM_LEN:
            continue
        if into is not None:
            gaz.terms[name] = Entry(str(r["id"]), relation)  # later layer overrides
        else:
            gaz.terms.setdefault(name, Entry(str(r["id"]), relation))
    gaz._compiled = None  # invalidate any compiled pattern
    return gaz


def find_mentions(text: str, gaz: Gazetteer) -> list[tuple[str, str, str]]:
    """Return unique (matched_term, node_id, relation) tuples found in `text`."""
    if not gaz.terms:
        return []
    haystack = normalize(text)
    seen: dict[str, Entry] = {}
    for m in gaz.pattern().finditer(haystack):
        term = m.group(1)
        entry = gaz.terms.get(term)
        if entry and term not in seen:
            seen[term] = entry
    return [(term, e.node_id, e.relation) for term, e in seen.items()]


def node_text(node: dict[str, Any]) -> str:
    """Concatenate a node's label and scan-worthy text properties."""
    parts = [str(node.get("label", ""))]
    props = node.get("properties") or {}
    for key in TEXT_PROPERTY_KEYS:
        val = props.get(key)
        if isinstance(val, str):
            parts.append(val)
    return " ".join(parts)


class TextLinker:
    """Run the text→geo linking pass against the current graph state."""

    def __init__(self, min_confidence: float = 0.6) -> None:
        self.min_confidence = min_confidence

    def _get_conn(self) -> Any:
        from db.session import get_conn

        return get_conn()

    async def _load_gazetteer(self, conn: Any) -> Gazetteer:
        districts = await conn.fetch(
            """
            SELECT id, label FROM nodes
            WHERE node_type = 'GeoArea'
              AND properties->>'area_type' IN ('stadtbezirk', 'statistischer_bezirk')
              AND valid_to IS NULL
            """
        )
        gaz = build_gazetteer([dict(r) for r in districts], relation="mentions_area")

        # Layer streets on top — more specific, so they override on collision.
        # Only the street-level register (one Road per name), NOT the ~19.5k
        # geometric segments, which share names and would bloat the gazetteer.
        streets = await conn.fetch(
            "SELECT id, label FROM nodes WHERE node_type = 'Road' "
            "AND source = 'opendata_dortmund_strassen' AND valid_to IS NULL"
        )
        build_gazetteer([dict(r) for r in streets], relation="mentions_street", into=gaz)
        return gaz

    async def run(self, limit: int = 5000) -> dict[str, int]:
        from ingestion.writer import upsert_edge
        from ontology.edges import relates_to

        counts = {"scanned": 0, "edges": 0}
        async with self._get_conn() as conn:
            gaz = await self._load_gazetteer(conn)
            if not gaz.terms:
                logger.warning("text linker: empty gazetteer (no districts/streets loaded)")
                return counts

            nodes = await conn.fetch(
                """
                SELECT id, node_type, label, properties FROM nodes
                WHERE node_type = ANY($1::text[]) AND valid_to IS NULL
                ORDER BY observed_at DESC LIMIT $2
                """,
                list(TEXT_NODE_TYPES), limit,
            )

        for n in nodes:
            counts["scanned"] += 1
            node = dict(n)
            mentions = find_mentions(node_text(node), gaz)
            # A street match is specific (high confidence); a lone district match
            # is decent; many competing matches dilute confidence.
            for term, target_id, relation in mentions:
                if relation == "mentions_street":
                    confidence = 0.9
                else:
                    confidence = 0.85 if len(mentions) == 1 else 0.7
                if confidence < self.min_confidence:
                    continue
                source_id = f"{node['id']}->{target_id}:{relation}"
                edge = relates_to(
                    from_id=UUID(str(node["id"])),
                    to_id=UUID(target_id),
                    source="resolution_text",
                    source_id=source_id,
                    inferred=True,
                    confidence=confidence,
                    reasoning_trace=f"matched '{term}' ({relation}) in {node['node_type']} text",
                    observed_at=datetime.now(timezone.utc),
                    properties={"relation": relation, "term": term},
                )
                _, was_new = await upsert_edge(edge)
                if was_new:
                    counts["edges"] += 1
        logger.info("text linker: scanned %d nodes, %d edges", counts["scanned"], counts["edges"])
        return counts
