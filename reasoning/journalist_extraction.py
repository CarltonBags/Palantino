"""
Materialise local JOURNALISTS from news-article bylines.

News articles enter as Event nodes carrying a structured `author` byline. This
pass aggregates those bylines into Journalist nodes — one per recurring named
contributor — and links each to the articles they are bylined on via WROTE.
Synergy/retrieval can then treat a journalist as an actor (a reach/amplifier
node) grounded in real published work.

Everything here is a source fact (inferred=False): a byline is metadata the
outlet publishes, not something a model guessed. No characterization of the
person is stored — only outlet, byline, volume and beats.

GDPR (rule 5): a byline is a public professional identity, so this is permitted
where storing a private individual would not be. We therefore include ONLY named
bylines. Collective bylines ("…-Redaktion") and pseudonymous desk codes
("Wir in Dortmund (SK)") are NOT resolved to persons — they stay as the outlet.
"""
from __future__ import annotations

import hashlib
import html
import logging
import re
from collections import Counter
from datetime import datetime

from db.session import get_conn
from ingestion.writer import upsert_edge, upsert_node
from ontology.edges import wrote
from ontology.nodes import Journalist

logger = logging.getLogger(__name__)

# Outlets whose `author` field is a real personal byline. wirindortmund is
# excluded on purpose: its bylines are desk-code initials, not identifiable
# persons — resolving them would be both impossible and a data-minimisation
# violation.
_BYLINE_OUTLETS = {"nordstadtblogger": "Nordstadtblogger"}

# A byline must recur at least this many times to become a Journalist node. One
# appearance is usually a guest author / interviewee, not a contributor.
_MIN_ARTICLES = 2

# Bylines that are collectives or the outlet itself, never a single person.
_COLLECTIVE = re.compile(r"redaktion|newsdesk|\(.*\)", re.IGNORECASE)


def _byline_key(outlet: str, name: str) -> str:
    norm = re.sub(r"\s+", " ", f"{outlet}|{name}".lower()).strip()
    return hashlib.sha256(norm.encode()).hexdigest()[:20]


async def extract_journalists() -> dict[str, int]:
    """Rebuild Journalist nodes + WROTE edges from current article bylines.

    Idempotent: source_id is a stable byline key, so re-runs upsert the same
    node and only add WROTE edges for newly-seen articles."""
    counts = {"journalists": 0, "wrote": 0}

    async with get_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT n.source AS outlet, n.properties->>'author' AS author,
                   n.id AS article_id, n.source_url, n.valid_from,
                   n.properties->'categories' AS categories
            FROM nodes n
            WHERE n.node_type = 'Event' AND n.valid_to IS NULL
              AND n.source = ANY($1::text[])
              AND coalesce(n.properties->>'author', '') <> ''
            """,
            list(_BYLINE_OUTLETS.keys()),
        )

    # group articles by (outlet, byline)
    by_author: dict[tuple[str, str], list] = {}
    for r in rows:
        name = (r["author"] or "").strip()
        if len(name) < 3 or _COLLECTIVE.search(name):
            continue
        by_author.setdefault((r["outlet"], name), []).append(r)

    from embeddings.backfill import embed_nodes

    processed = 0
    for (outlet, name), arts in by_author.items():
        if len(arts) < _MIN_ARTICLES:
            continue
        outlet_label = _BYLINE_OUTLETS[outlet]

        beats = Counter()
        for a in arts:
            for c in a["categories"] or []:
                beats[html.unescape(c)] += 1
        dates = [a["valid_from"] for a in arts if a["valid_from"]]
        first_seen = min(dates).date().isoformat() if dates else None
        last_seen = max(dates).date().isoformat() if dates else None

        node = Journalist(
            label=name,
            properties={
                "outlet": outlet_label,
                "byline": name,
                "role": f"Journalist:in / Autor:in bei {outlet_label}",
                "article_count": len(arts),
                "beats": [b for b, _ in beats.most_common(6)],
                "first_seen": first_seen,
                "last_seen": last_seen,
            },
            source="byline_extraction",
            source_id=_byline_key(outlet, name),
            source_url="https://www.nordstadtblogger.de/",
            valid_from=min(dates) if dates else None,
        )
        jid, was_new = await upsert_node(node)
        if was_new:
            counts["journalists"] += 1

        for a in arts:
            _, edge_new = await upsert_edge(
                wrote(
                    jid, a["article_id"],
                    source="byline_extraction",
                    source_url=a["source_url"],
                    source_id=f"{jid}->{a['article_id']}",
                )
            )
            if edge_new:
                counts["wrote"] += 1

        processed += 1
        if processed % 40 == 0:
            await embed_nodes()

    await embed_nodes()
    logger.info("journalist extraction: %s", counts)
    return counts
