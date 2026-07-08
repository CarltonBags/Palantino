"""
Ask-the-city Q&A — grounded retrieval-augmented generation over the graph.

Pipeline:
  1. intent pre-pass (cheap/fast model): turn the NL question into a focused
     search phrase + structured filters (node types, date range) — the things
     vector similarity is blind to.
  2. hybrid retrieval: embed the search phrase, pgvector KNN, AND apply the
     filters as SQL WHERE. Falls back to unfiltered KNN if the filters yield
     nothing.
  3. grounded answer: hand the retrieved subgraph to the main model, which
     answers in German using only those facts and names its sources.
"""

from __future__ import annotations

import json
import logging
import random
from datetime import date, timedelta
from typing import Any

from db.session import get_conn
from embeddings.embedder import embed_texts, to_pgvector
from reasoning.llm import complete
from reasoning.prompts import (
    ANALYSIS_PROMPT,
    ANALYSIS_SYSTEM_PROMPTS,
    DISCUSS_PROMPT,
    DISCUSS_SYSTEM_PROMPT,
    QA_PROMPT,
    QA_SYSTEM_PROMPT,
    QUERY_INTENT_PROMPT,
    QUERY_INTENT_SYSTEM,
    format_subgraph,
)

_LENSES = {
    "factual", "synergy", "inefficiency", "scandal", "crime", "leads",
    "bedarf", "problem", "foerderung", "chance",
}

logger = logging.getLogger(__name__)

_VALID_NODE_TYPES = {
    "AgendaItem", "Resolution", "Meeting", "Event", "Tender",
    "POI", "Organization", "Road", "GeoArea", "Problem",
}


def _loads(raw: str) -> dict[str, Any]:
    """Tolerant JSON parse (strip ```json fences)."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return {}
    return {}


def _valid_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


async def extract_intent(question: str) -> dict[str, Any]:
    """Cheap pre-pass: focused search phrase + structured filters."""
    fallback = {
        "lens": "factual", "search_text": question, "node_types": [],
        "category": None, "list": False, "date_from": None, "date_to": None,
        "needs": [], "district": None, "recent": False,
    }
    try:
        # Use the main model (not the fast one): reliable lens classification +
        # node-type extraction matters more here than the tiny cost of one short
        # call. Reasoning models need headroom (hidden reasoning_content).
        raw = await complete(
            QUERY_INTENT_SYSTEM,
            QUERY_INTENT_PROMPT.format(question=question, today=date.today().isoformat()),
            max_tokens=1500,
        )
    except Exception as exc:  # intent is best-effort — never block the answer
        logger.warning("intent extraction failed: %s", exc)
        return fallback
    data = _loads(raw)
    search_text = str(data.get("search_text") or "").strip() or question
    node_types = [t for t in (data.get("node_types") or []) if t in _VALID_NODE_TYPES]
    category = str(data.get("category")).strip() if data.get("category") else None
    lens = str(data.get("lens") or "factual").strip().lower()
    from reasoning.resources import RESOURCES

    return {
        "lens": lens if lens in _LENSES else "factual",
        "search_text": search_text,
        "node_types": node_types,
        "category": category,
        "list": bool(data.get("list")),
        "date_from": _valid_date(data.get("date_from")),
        "date_to": _valid_date(data.get("date_to")),
        "needs": [n for n in (data.get("needs") or []) if n in RESOURCES],
        "district": (str(data.get("district")).strip() if data.get("district") else None),
        "recent": bool(data.get("recent")),
    }


async def _retrieve(
    conn: Any, lit: str, intent: dict[str, Any], k: int, use_filters: bool, list_mode: bool
) -> list:
    """
    list_mode=False: semantic KNN (rank by similarity) — for analytical questions.
    list_mode=True: structured enumeration (filter + chronological) — for "list all
    X" questions, where similarity top-k would drop most matches.
    """
    filters = ["n.valid_to IS NULL"]
    params: list[Any] = [] if list_mode else [lit]  # $1 = query vector (semantic only)

    def add(value: Any) -> str:
        params.append(value)
        return f"${len(params)}"

    if use_filters and intent["node_types"]:
        filters.append(f"n.node_type = ANY({add(intent['node_types'])}::text[])")
    if use_filters and intent.get("category"):
        filters.append(f"n.properties->>'category' ILIKE {add('%' + intent['category'] + '%')}")
    if use_filters and intent["date_from"]:
        filters.append(f"n.valid_from >= {add(intent['date_from'])}")
    if use_filters and intent["date_to"]:
        # day-inclusive: a date_to of 2026-07-05 must include events all day on the 5th
        filters.append(f"n.valid_from < ({add(intent['date_to'])}::date + INTERVAL '1 day')")
    if use_filters and intent.get("recent_since"):
        filters.append(f"n.observed_at >= {add(intent['recent_since'])}::date")
    limit_ph = add(k)
    order = "n.valid_from ASC NULLS LAST" if list_mode else "e.embedding <=> $1::vector"
    sql = f"""
        SELECT n.id, n.node_type, n.label, n.properties, n.source, n.source_url, n.valid_from
        FROM node_embeddings e
        JOIN nodes n ON n.id = e.node_id
        WHERE {' AND '.join(filters)}
        ORDER BY {order}
        LIMIT {limit_ph}
        """
    if list_mode:
        return await conn.fetch(sql, *params)
    # filtered ANN → iterative scan so a type/date filter doesn't starve results
    async with conn.transaction():
        await conn.execute("SET LOCAL hnsw.iterative_scan = 'relaxed_order'")
        await conn.execute("SET LOCAL hnsw.ef_search = 200")
        return await conn.fetch(sql, *params)


# Don't fan out THROUGH these hub types — a district links to ~all co-located
# nodes, which would explode the subgraph with noise. We still keep a hub if a
# seed links to it (one hop in), we just don't expand its other members.
_HUB_TYPES = ("GeoArea",)
_NODE_COLS = "id, node_type, label, properties, source, source_url, valid_from"


async def _expand(conn: Any, seed_ids: list[str], max_total: int = 40, hops: int = 2) -> list[str]:
    """
    Multi-hop graph expansion: walk edges out from the vector seeds (up to `hops`),
    adding connected nodes — so the LLM sees relationally-linked facts (a tender
    and the resolution behind it, a meeting and its agenda items) that pure vector
    recall misses. Bounded: skips fan-out through hub types, caps the total.
    """
    seen: set[str] = set(seed_ids)
    frontier = list(seed_ids)
    for _ in range(hops):
        if len(seen) >= max_total or not frontier:
            break
        expandable = await conn.fetch(
            "SELECT id FROM nodes WHERE id = ANY($1::uuid[]) "
            "AND node_type <> ALL($2::text[])",
            frontier, list(_HUB_TYPES),
        )
        ex_ids = [str(r["id"]) for r in expandable]
        if not ex_ids:
            break
        rows = await conn.fetch(
            """
            SELECT DISTINCT
                CASE WHEN from_node_id = ANY($1::uuid[]) THEN to_node_id ELSE from_node_id END AS nid
            FROM edges
            WHERE (from_node_id = ANY($1::uuid[]) OR to_node_id = ANY($1::uuid[]))
              AND valid_to IS NULL
            LIMIT 400
            """,
            ex_ids,
        )
        new = [str(r["nid"]) for r in rows if str(r["nid"]) not in seen]
        new = new[: max(0, max_total - len(seen))]
        if not new:
            break
        seen.update(new)
        frontier = new
    return list(seen)


async def _diverse_seeds(
    conn: Any, qvec: list[float], k: int, intent: dict[str, Any] | None = None,
    pool_size: int = 80, lam: float = 0.55,
) -> list[dict[str, Any]]:
    """
    MMR seed selection for BROAD analytical queries. Pure KNN on a generic query
    ("Synergien für die Stadt") deterministically returns the same densest cluster
    every time → repetitive answers. Instead pull a larger relevance pool (scoped
    by the same filters, e.g. node_types=[POI,Organization] for leads), pick a
    RANDOM first seed from the top (rotation across asks), then greedily add seeds
    that are relevant but dissimilar to those already chosen (MMR → breadth).
    """
    filters = ["n.valid_to IS NULL"]
    params: list[Any] = [to_pgvector(qvec)]

    def add(value: Any) -> str:
        params.append(value)
        return f"${len(params)}"

    if intent:
        if intent.get("node_types"):
            filters.append(f"n.node_type = ANY({add(intent['node_types'])}::text[])")
        if intent.get("category"):
            filters.append(f"n.properties->>'category' ILIKE {add('%' + intent['category'] + '%')}")
        if intent.get("date_from"):
            filters.append(f"n.valid_from >= {add(intent['date_from'])}")
        if intent.get("date_to"):
            filters.append(f"n.valid_from < ({add(intent['date_to'])}::date + INTERVAL '1 day')")
        if intent.get("recent_since"):
            filters.append(f"n.observed_at >= {add(intent['recent_since'])}::date")
    params.append(pool_size)
    # Filtered ANN: without iterative scan, HNSW returns the globally-nearest
    # vectors THEN applies the filter — so a type filter (e.g. POI) can yield ~0
    # rows when the nearest are another type. pgvector 0.8 iterative scan keeps
    # searching until LIMIT rows pass the filter.
    async with conn.transaction():
        await conn.execute("SET LOCAL hnsw.iterative_scan = 'relaxed_order'")
        await conn.execute("SET LOCAL hnsw.ef_search = 200")
        rows = await conn.fetch(
            f"""
            SELECT {_NODE_COLS}, e.embedding::text AS emb
            FROM node_embeddings e
            JOIN nodes n ON n.id = e.node_id
            WHERE {' AND '.join(filters)}
            ORDER BY e.embedding <=> $1::vector
            LIMIT ${len(params)}
            """,
            *params,
        )
    if not rows:
        return []
    import numpy as np

    def _vec(s: str) -> Any:
        v = np.array([float(x) for x in s.strip("[]").split(",")])
        n = np.linalg.norm(v)
        return v / n if n else v

    vecs = [_vec(r["emb"]) for r in rows]
    q = np.array(qvec, dtype=float)
    q = q / (np.linalg.norm(q) or 1.0)
    sim_q = [float(q @ v) for v in vecs]
    n = len(rows)
    k = min(k, n)
    selected = [random.randrange(min(12, n))]  # random anchor → rotates per ask
    while len(selected) < k:
        best_i, best = -1, -1e9
        for i in range(n):
            if i in selected:
                continue
            div = max(float(vecs[i] @ vecs[j]) for j in selected)
            score = lam * sim_q[i] - (1.0 - lam) * div
            if score > best:
                best, best_i = score, i
        selected.append(best_i)
    out = []
    for i in selected:
        d = dict(rows[i])
        d.pop("emb", None)
        out.append(d)
    return out


async def _structural_partners(
    conn: Any, seed_ids: list[str], max_dist_m: int = 300, min_dist_m: int = 25,
    per_seed: int = 2, cap: int = 18, business: bool = False,
) -> list[str]:
    """
    Chat v2 retrieval signal: for the query-relevant seeds, find PHYSICALLY NEAR
    (PostGIS), CROSS-TYPE, currently-UNCONNECTED real ACTOR nodes (venues/clubs,
    real events, extracted civic actors — not news articles) — the structural shape
    of an untapped synergy that vector similarity can't reach.
    """
    if not seed_ids:
        return []
    rows = await conn.fetch(
        f"""
        SELECT DISTINCT pid FROM (
            SELECT part.pid
            FROM nodes s
            CROSS JOIN LATERAL (
                SELECT pp.id AS pid
                FROM nodes pp
                WHERE pp.valid_to IS NULL AND pp.geom IS NOT NULL
                  AND pp.node_type <> s.node_type AND pp.id <> s.id
                  AND {_actor_clause('pp', business)}
                  AND ST_DWithin(s.geom::geography, pp.geom::geography, $2)
                  AND ST_Distance(s.geom::geography, pp.geom::geography) > $3
                  AND NOT EXISTS (
                      SELECT 1 FROM edges e WHERE e.valid_to IS NULL
                        AND ((e.from_node_id = s.id AND e.to_node_id = pp.id)
                          OR (e.from_node_id = pp.id AND e.to_node_id = s.id)))
                ORDER BY s.geom <-> pp.geom
                LIMIT $4
            ) part
            WHERE s.id = ANY($1::uuid[]) AND s.geom IS NOT NULL
        ) q
        LIMIT $5
        """,
        seed_ids, max_dist_m, min_dist_m, per_seed, cap,
    )
    return [str(r["pid"]) for r in rows]


# tags held by more than this fraction of all actors are near-universal
# (publikum, sichtbarkeit, veranstaltungsflaeche) — a match on them signals
# nothing, so they're excluded from complementary matching entirely.
_COMP_GENERIC_FRAC = 0.40


async def _complementary_partners(
    conn: Any, seed_ids: list[str], cap: int = 18, business: bool = False,
) -> list[str]:
    """Complementary retrieval: partners whose OFFER matches a seed's NEED (or vice
    versa) on the resource layer. Ranked by IDF — a match on a RARE tag (reparatur,
    kinderbetreuung) weighs far more than one on a near-universal tag (publikum) —
    so the fit is specific, not the arbitrary shared-tag noise DISTINCT+LIMIT gave."""
    if not seed_ids:
        return []
    rows = await conn.fetch(
        f"""
        WITH tot AS (SELECT count(DISTINCT node_id)::float AS t FROM node_resources),
        freq AS (SELECT tag, count(DISTINCT node_id) AS n FROM node_resources GROUP BY tag),
        seed_res AS (
            SELECT DISTINCT tag, kind FROM node_resources WHERE node_id = ANY($1::uuid[])
        )
        SELECT br.node_id AS pid,
               sum(ln((SELECT t FROM tot) / freq.n)) AS score
        FROM seed_res sr
        JOIN freq ON freq.tag = sr.tag
        JOIN node_resources br ON br.tag = sr.tag AND br.kind <> sr.kind
             AND br.node_id <> ALL($1::uuid[])
        JOIN nodes n ON n.id = br.node_id AND n.valid_to IS NULL AND {_actor_clause('n', business)}
        WHERE freq.n <= (SELECT t FROM tot) * $3
        GROUP BY br.node_id
        ORDER BY score DESC
        LIMIT $2
        """,
        seed_ids, cap, _COMP_GENERIC_FRAC,
    )
    return [str(r["pid"]) for r in rows]


async def _cooccurrence_partners(
    conn: Any, anchor_id: str, business: bool, cap: int = 6,
) -> list[dict[str, Any]]:
    """Graph-structural signal: actors named in the SAME news article as the anchor
    (shared `MENTIONS`) but not directly linked. Co-occurrence in one article means
    they already share a real-world context — a stronger synergy cue than embedding
    similarity, and one only the graph knows."""
    rows = await conn.fetch(
        f"""
        SELECT DISTINCT n.id, n.node_type, n.label, n.properties, n.source,
               n.source_url, n.valid_from
        FROM edges m1
        JOIN edges m2 ON m2.from_node_id = m1.from_node_id
             AND m2.edge_type = 'MENTIONS' AND m2.valid_to IS NULL
             AND m2.to_node_id <> $1::uuid
        JOIN nodes n ON n.id = m2.to_node_id AND n.valid_to IS NULL
             AND {_actor_clause('n', business)}
        WHERE m1.edge_type = 'MENTIONS' AND m1.to_node_id = $1::uuid
          AND m1.valid_to IS NULL
          AND NOT EXISTS (SELECT 1 FROM edges g WHERE g.valid_to IS NULL
              AND ((g.from_node_id = $1::uuid AND g.to_node_id = n.id)
                OR (g.from_node_id = n.id AND g.to_node_id = $1::uuid)))
        LIMIT {cap}
        """,
        anchor_id,
    )
    return [dict(r) for r in rows]


async def _link_prediction_partners(
    conn: Any, seed_ids: list[str], business: bool, cap: int = 12,
) -> list[dict[str, Any]]:
    """Graph link prediction: actors that share SPECIFIC neighbours with the seeds
    (same articles, events, tenders, themes) but are NOT directly linked — ranked by
    how many such shared connections they have. Hub connectors (GeoArea, and any
    node wired to >60 others = a district-like hub) are excluded so a shared
    Stadtbezirk doesn't link everything; a shared niche event/article counts. This
    is 'should-connect-but-doesn't', the real untapped-synergy signal (co-mention is
    its single-shared-neighbour special case)."""
    if not seed_ids:
        return []
    rows = await conn.fetch(
        f"""
        WITH nbr AS (
            SELECT s.seed, mm.mid
            FROM unnest($1::uuid[]) AS s(seed)
            JOIN edges e ON (e.from_node_id = s.seed OR e.to_node_id = s.seed)
                 AND e.valid_to IS NULL
            CROSS JOIN LATERAL (SELECT CASE WHEN e.from_node_id = s.seed
                                THEN e.to_node_id ELSE e.from_node_id END AS mid) mm
            JOIN nodes mn ON mn.id = mm.mid AND mn.node_type <> 'GeoArea'
            WHERE (SELECT count(*) FROM edges h
                   WHERE (h.from_node_id = mm.mid OR h.to_node_id = mm.mid)
                     AND h.valid_to IS NULL) <= 60
        )
        SELECT p.id, p.node_type, p.label, p.properties, p.source, p.source_url,
               p.valid_from, count(DISTINCT nbr.mid) AS shared
        FROM nbr
        JOIN edges e2 ON (e2.from_node_id = nbr.mid OR e2.to_node_id = nbr.mid)
             AND e2.valid_to IS NULL
        CROSS JOIN LATERAL (SELECT CASE WHEN e2.from_node_id = nbr.mid
                            THEN e2.to_node_id ELSE e2.from_node_id END AS pid) pp
        JOIN nodes p ON p.id = pp.pid AND p.valid_to IS NULL AND {_actor_clause('p', business)}
        WHERE p.id <> nbr.seed
          AND NOT EXISTS (SELECT 1 FROM edges g WHERE g.valid_to IS NULL
              AND ((g.from_node_id = nbr.seed AND g.to_node_id = p.id)
                OR (g.from_node_id = p.id AND g.to_node_id = nbr.seed)))
        GROUP BY p.id, p.node_type, p.label, p.properties, p.source, p.source_url, p.valid_from
        ORDER BY shared DESC
        LIMIT $2
        """,
        seed_ids, cap,
    )
    return [dict(r) for r in rows]


def _intent_out(intent: dict[str, Any]) -> dict[str, Any]:
    return {
        "lens": intent.get("lens", "factual"),
        "search_text": intent["search_text"],
        "node_types": intent["node_types"],
        "category": intent.get("category"),
        "list": bool(intent.get("list")),
        "date_from": intent["date_from"].isoformat() if intent["date_from"] else None,
        "date_to": intent["date_to"].isoformat() if intent["date_to"] else None,
        "needs": intent.get("needs") or [],
        "district": intent.get("district"),
        "recent": bool(intent.get("recent")),
    }


_BIZ_KW = (
    "unternehmen", "firma", "gmbh", "betrieb", "gewerbe", "vergabe", "auftrag",
    "wirtschaft", "company", "business", "geschäft", "handelsregister", "startup",
)


def _same_actor(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """True if the two are really the same actor/series — a near-duplicate name
    (Buch-Club ↔ Buch-Club Kids), one label contained in the other, or the same
    venue (same host/location). Such a pair is not a synergy."""
    import difflib

    la, lb = (a.get("label") or "").lower().strip(), (b.get("label") or "").lower().strip()
    if not la or not lb:
        return False
    if la in lb or lb in la:
        return True
    if difflib.SequenceMatcher(None, la, lb).ratio() >= 0.6:
        return True
    pa = a.get("properties") if isinstance(a.get("properties"), dict) else {}
    pb = b.get("properties") if isinstance(b.get("properties"), dict) else {}
    va, vb = (pa.get("venue") or "").lower().strip(), (pb.get("venue") or "").lower().strip()
    return bool(va) and va == vb


def _is_business_query(search_text: str) -> bool:
    t = (search_text or "").lower()
    return any(k in t for k in _BIZ_KW)


def _actor_clause(alias: str, business: bool) -> str:
    """SQL predicate for a real, partnerable synergy ACTOR (not a news article):
    named venues/clubs (POI), real events (not news), civic actors extracted from
    news, and Handelsregister companies only on an explicit business query."""
    a = alias
    parts = [
        f"({a}.node_type = 'POI' AND {a}.label NOT LIKE 'OSM %')",
        f"({a}.node_type = 'Event' AND coalesce({a}.properties->>'event_type','') <> 'news')",
        f"({a}.node_type = 'Organization' AND {a}.source IN "
        f"('news_extraction', 'event_venue', 'vergabe_nrw_metropole_ruhr'))",
        # journalists are reach/connector actors (amplify, cover a beat)
        f"({a}.node_type = 'Journalist')",
    ]
    if business:
        parts.append(
            f"({a}.node_type = 'Organization' AND {a}.source = 'offeneregister'"
            f" AND coalesce({a}.properties->>'status','') = 'currently registered')"
        )
    return "(" + " OR ".join(parts) + ")"


async def _beat_bridge_partners(conn: Any, anchor: dict, actor_clause: str) -> list[dict]:
    """Partners for a Journalist (reach) anchor. Their whole-profile embedding
    clusters with other journalists, so nearest-neighbour finds only peers. Match
    on their BEATS instead — a sports reporter pairs with the sports clubs,
    venues and fixtures they could cover or amplify."""
    props = anchor.get("properties")
    if isinstance(props, str):
        try:
            props = json.loads(props)
        except json.JSONDecodeError:
            props = {}
    if not isinstance(props, dict):
        return []
    # beats ONLY — adding role/outlet ("Journalist, Autor…") pulls the vector
    # back into the journalist cluster and buries the on-beat actors
    beats = [b for b in (props.get("beats") or []) if isinstance(b, str)]
    terms = " ".join(beats).strip() or (props.get("role") or "").strip()
    if not terms:
        return []
    vec = to_pgvector((await embed_texts([terms]))[0])
    rows = await conn.fetch(
        f"""SELECT {_NODE_COLS} FROM node_embeddings e JOIN nodes n ON n.id = e.node_id
            WHERE n.valid_to IS NULL AND {actor_clause} AND n.node_type <> 'Journalist'
            ORDER BY e.embedding <=> $1::vector LIMIT 6""",
        vec,
    )
    return [dict(r) for r in rows]


async def _deep_synergy_pairs(intent: dict[str, Any]) -> list[tuple[dict, dict, str]]:
    """Query-ANCHORED pairs. Find the entities the question is about (e.g. the
    Neven-Subotic-Stiftung), then for each build candidate partners from BOTH
    semantic relatedness (mission fit) and proximity — every pair contains the
    queried actor. Cap how often any node reappears so a few venues/POIs don't
    dominate every result."""
    from collections import Counter

    from reasoning.synergy_finder import pair_key, suppressed_pair_keys

    qvec = (await embed_texts([intent["search_text"]]))[0]
    qlit = to_pgvector(qvec)
    pairs: list[tuple[dict, dict, str]] = []
    used: Counter[str] = Counter()
    suppressed = await suppressed_pair_keys()  # feedback loop: skip rejected/dismissed

    async with get_conn() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL hnsw.iterative_scan = 'relaxed_order'")
            await conn.execute("SET LOCAL hnsw.ef_search = 200")
            # anchors: the entities the query is actually about
            business = _is_business_query(intent["search_text"])
            actor = _actor_clause("n", business)
            # 1) fuzzy NAME match first, so a query about a named actor
            #    ("… für die Neven-Subotic-Stiftung") reliably anchors on it, even
            #    if filler words pull the sentence embedding toward generic themes.
            name_anchors = await conn.fetch(
                f"""SELECT {_NODE_COLS}, (n.geom IS NOT NULL) AS has_geom
                    FROM nodes n
                    WHERE n.valid_to IS NULL AND {actor}
                      AND similarity(lower(n.label), lower($1)) > 0.3
                    ORDER BY similarity(lower(n.label), lower($1)) DESC LIMIT 3""",
                intent["search_text"],
            )
            # 2) semantic anchors (the theme of the query) — overfetch, then
            #    dedupe by label so a 31-date event series can't fill every slot
            sem_anchors = await conn.fetch(
                f"""SELECT {_NODE_COLS}, (n.geom IS NOT NULL) AS has_geom
                    FROM node_embeddings e JOIN nodes n ON n.id = e.node_id
                    WHERE n.valid_to IS NULL AND {actor}
                    ORDER BY e.embedding <=> $1::vector LIMIT 24""",
                qlit,
            )
            # 3) recency anchors: for "neueste/aktuelle Ereignisse" questions, the
            #    freshest actors (recently INGESTED — observed_at, not an event's
            #    future valid_from) anchor the search, so synergies follow what was
            #    just reported, not the farthest-future concert.
            recent_anchors = []
            if intent.get("recent_since"):
                recent_anchors = await conn.fetch(
                    f"""SELECT {_NODE_COLS}, (n.geom IS NOT NULL) AS has_geom
                        FROM nodes n
                        WHERE n.valid_to IS NULL AND {actor}
                          AND n.observed_at >= $1::date
                        ORDER BY n.observed_at DESC LIMIT 12""",
                    intent["recent_since"],
                )
            seen_a: set[str] = set()
            seen_labels: set[str] = set()
            anchors = []
            for r in list(name_anchors) + list(recent_anchors) + list(sem_anchors):
                lbl = (r["label"] or "").lower().strip()
                if str(r["id"]) in seen_a or lbl in seen_labels:
                    continue
                seen_a.add(str(r["id"]))
                seen_labels.add(lbl)
                anchors.append(r)
                if len(anchors) >= 9:
                    break
            # precomputed frontier partners for all anchors (all-pairs engine):
            # the best-scored not-yet-validated pairs touching an anchor
            from reasoning.candidate_engine import frontier_pairs

            frontier_by_anchor: dict[str, list[dict]] = {}
            for fa, fb, _n in await frontier_pairs(
                limit=6 * max(len(anchors), 1),
                anchor_ids=[str(a["id"]) for a in anchors],
            ):
                for me, other in ((fa, fb), (fb, fa)):
                    mid = str(me["id"])
                    if any(str(a["id"]) == mid for a in anchors):
                        frontier_by_anchor.setdefault(mid, []).append(other)

            for anchor in anchors:
                aid = str(anchor["id"])
                # semantic partners: nearest to THIS anchor, different node, not yet connected
                sem = await conn.fetch(
                    f"""SELECT {_NODE_COLS} FROM node_embeddings e JOIN nodes n ON n.id = e.node_id
                        WHERE n.valid_to IS NULL AND {actor} AND n.id <> $1::uuid
                          AND NOT EXISTS (SELECT 1 FROM edges g WHERE g.valid_to IS NULL
                              AND ((g.from_node_id = $1::uuid AND g.to_node_id = n.id)
                                OR (g.from_node_id = n.id AND g.to_node_id = $1::uuid)))
                        ORDER BY e.embedding <=> (SELECT embedding FROM node_embeddings WHERE node_id = $1::uuid)
                        LIMIT 6""",
                    aid,
                )
                # frontier partners first — they carry the strongest combined signal
                partners = list(frontier_by_anchor.get(aid, []))
                # reach actors (journalists) match on their beats, not their
                # journalist-clustered profile embedding — and skip the peer
                # nearest-neighbours (journalist↔journalist is a thin synergy)
                if anchor["node_type"] == "Journalist":
                    partners += await _beat_bridge_partners(conn, anchor, actor)
                else:
                    partners += [dict(r) for r in sem]
                # complementary partners: need↔offer match on the resource layer
                comp = await conn.fetch(
                    f"""SELECT DISTINCT n.id, n.node_type, n.label, n.properties, n.source,
                              n.source_url, n.valid_from
                       FROM node_resources ar
                       JOIN node_resources br ON br.tag = ar.tag AND br.kind <> ar.kind
                            AND br.node_id <> ar.node_id
                       JOIN nodes n ON n.id = br.node_id AND n.valid_to IS NULL AND {actor}
                       WHERE ar.node_id = $1::uuid LIMIT 4""",
                    aid,
                )
                partners += [dict(r) for r in comp]
                if anchor.get("has_geom"):
                    prox = await _structural_partners(conn, [aid], per_seed=3, cap=3, business=business)
                    if prox:
                        prows = await conn.fetch(
                            f"SELECT {_NODE_COLS} FROM nodes WHERE id = ANY($1::uuid[]) AND valid_to IS NULL",
                            prox,
                        )
                        partners += [dict(r) for r in prows]
                # graph-structural: actors sharing specific neighbours (link prediction)
                partners += await _link_prediction_partners(conn, [aid], business, cap=4)
                added = 0
                added_labels: set[str] = set()
                for p in partners:
                    pid = str(p["id"])
                    # reuse cap by LABEL, not id — an event series is one actor,
                    # not 31 candidate slots
                    plabel = (p.get("label") or "").lower().strip()
                    if (pid == aid or used[plabel] >= 2 or added >= 4
                            or plabel in added_labels
                            or _same_actor(anchor, p)
                            or pair_key(aid, pid) in suppressed):
                        continue
                    used[plabel] += 1
                    added_labels.add(plabel)
                    added += 1
                    note = (
                        f"Beide passen zur Anfrage „{intent['search_text']}“: "
                        f"„{anchor['label']}“ und „{p['label']}“ — bislang unverbunden."
                    )
                    pairs.append((dict(anchor), p, note))
    return pairs


async def _deep_synergy_answer(intent: dict[str, Any]) -> dict[str, Any]:
    """Chat Tiefensuche: research + validate synergies SCOPED to the question;
    show validated ones and the ones checked & rejected (with reason)."""
    from reasoning.synergy_finder import find_synergies, record_synergy_feedback

    pairs = await _deep_synergy_pairs(intent)
    results = await find_synergies(n=5, pairs=pairs, shuffle=False)
    await record_synergy_feedback(results, source="llm")  # learn: don't re-judge these

    validated = [r for r in results if r.get("verdict") == "makes_sense" and r.get("description")]
    validated.sort(key=lambda r: not r.get("cross_domain"))  # non-obvious bridges first
    rejected = [r for r in results if r.get("verdict") == "reject"]

    parts: list[str] = []
    if validated:
        parts.append(
            f"**{len(validated)} recherchierte Synergien** — Akteure im Graphen und "
            "auf ihren Websites geprüft.\n"
        )
        for i, s in enumerate(validated, 1):
            marker = " 🌉 *feldübergreifend*" if s.get("cross_domain") else ""
            parts.append(f"## {i}. {s.get('title', 'Synergie')}{marker}")
            if s.get("partners"):
                parts.append(f"*{' ↔ '.join(s['partners'])}*")
            parts.append(s.get("description", ""))
            if s.get("first_step"):
                parts.append(f"**Erster Schritt:** {s['first_step']}")
            if s.get("contacts"):
                parts.append(f"**Kontakt:** {', '.join(s['contacts'])}")
            if s.get("researched_websites"):
                parts.append(f"**Recherchiert:** {', '.join(s['researched_websites'])}")
            parts.append("")
    else:
        parts.append("Keine der geprüften Paarungen hielt der Recherche stand.\n")
    if rejected:
        parts.append("---\n### Geprüft und verworfen")
        for r in rejected:
            pn = " ↔ ".join(r.get("partners", [])) or "(Paar)"
            parts.append(f"- **{pn}** — {r.get('reason', '(kein Grund angegeben)')}")

    ids = [i for r in results for i in r.get("evidence_node_ids", [])]
    citations = []
    if ids:
        async with get_conn() as conn:
            rows = await conn.fetch(
                f"SELECT {_NODE_COLS} FROM nodes WHERE id = ANY($1::uuid[]) AND valid_to IS NULL", ids
            )
        citations = [
            {"id": str(r["id"]), "label": r["label"], "node_type": r["node_type"],
             "source": r["source"], "source_url": r["source_url"]}
            for r in rows
        ]
    return {"answer": "\n".join(parts), "citations": citations, "intent": _intent_out(intent)}


async def _leads_candidates(
    conn: Any, intent: dict[str, Any], qlit: str, limit: int = 40
) -> list[dict]:
    """Akquise candidates: real business actors, district-filtered when the
    query names one, contactable-first, tender winners boosted, actors the
    user already acted on (lead_feedback) excluded. Replaces the generic
    semantic sample for lens=leads."""
    district_geom_id: str | None = None
    if intent.get("district"):
        row = await conn.fetchrow(
            """
            SELECT id FROM nodes
            WHERE node_type = 'GeoArea' AND valid_to IS NULL AND geom IS NOT NULL
              AND lower(label) = lower($1)
            ORDER BY (properties->>'area_type' = 'stadtbezirk') DESC
            LIMIT 1
            """,
            intent["district"],
        )
        district_geom_id = str(row["id"]) if row else None

    params: list[Any] = [qlit, limit]

    def add(v: Any) -> str:
        params.append(v)
        return f"${len(params)}"

    district_clause = "TRUE"
    if district_geom_id:
        d = add(district_geom_id)
        dn = add(intent["district"])
        district_clause = f"""(
            (n.geom IS NOT NULL AND ST_Within(n.geom,
                (SELECT geom FROM nodes WHERE id = {d}::uuid)))
            OR lower(coalesce(n.properties->>'stadtbezirk', '')) = lower({dn})
            OR EXISTS (SELECT 1 FROM edges le WHERE le.valid_to IS NULL
                AND le.edge_type = 'LOCATED_IN' AND le.from_node_id = n.id
                AND le.to_node_id = {d}::uuid)
        )"""

    rows = await conn.fetch(
        f"""
        SELECT * FROM (
            SELECT DISTINCT ON (n.label) {_NODE_COLS},
                (coalesce(n.properties->>'contact_email', n.properties->>'email',
                          n.properties->>'contact_phone', n.properties->>'phone',
                          n.properties->>'contact_website', n.properties->>'website')
                 IS NOT NULL) AS has_contact,
                (SELECT count(*) FROM edges aw WHERE aw.edge_type = 'AWARDED_TO'
                    AND aw.to_node_id = n.id AND aw.valid_to IS NULL) AS tender_wins,
                (e.embedding <=> $1::vector) AS dist
            FROM nodes n
            LEFT JOIN node_embeddings e ON e.node_id = n.id
            WHERE n.valid_to IS NULL
              AND (
                (n.node_type = 'POI' AND n.label NOT LIKE 'OSM %'
                 AND (n.properties->>'shop' IS NOT NULL
                      OR n.properties->>'office' IS NOT NULL
                      OR n.properties->>'craft' IS NOT NULL
                      OR n.properties->>'amenity' IS NOT NULL))
                OR (n.node_type = 'Organization' AND (
                     n.source IN ('news_extraction', 'event_venue',
                                  'vergabe_nrw_metropole_ruhr')
                     OR (n.source = 'offeneregister'
                         AND coalesce(n.properties->>'status', '')
                             = 'currently registered')))
              )
              AND {district_clause}
              AND NOT EXISTS (SELECT 1 FROM lead_feedback lf WHERE lf.node_id = n.id)
            ORDER BY n.label, (e.embedding <=> $1::vector) ASC NULLS LAST
        ) x
        -- blended rank: thematic relevance leads; contact data and proven
        -- public-tender budgets NUDGE (≈0.06 cosine each), they never override
        -- relevance — otherwise 40 construction firms bury every Verein on a
        -- "soziale Einrichtungen" query
        ORDER BY CASE WHEN x.dist IS NULL THEN 0.95
                      ELSE x.dist
                           - (CASE WHEN x.has_contact THEN 0.06 ELSE 0 END)
                           - (CASE WHEN x.tender_wins > 0 THEN 0.06 ELSE 0 END)
                 END ASC
        LIMIT $2
        """,
        *params,
    )
    out = []
    for r in rows:
        d = dict(r)
        props = d.get("properties") if isinstance(d.get("properties"), dict) else {}
        if d.get("tender_wins"):
            # surface the proof-of-budget to the LLM: this actor wins public tenders
            props = {**props, "oeffentliche_auftraege_gewonnen": d["tender_wins"]}
        d["properties"] = props
        for k in ("has_contact", "tender_wins", "dist"):
            d.pop(k, None)
        out.append(d)
    return out


async def _complete_nonempty(system: str, prompt: str, max_tokens: int = 20000) -> str:
    """complete(), retried once with a bigger budget when the reasoning model
    burns the whole allowance on hidden reasoning and returns empty content."""
    answer = await complete(system, prompt, max_tokens=max_tokens)
    if not answer.strip():
        logger.warning("empty completion (reasoning burn) — retrying with %d", max_tokens * 2)
        answer = await complete(system, prompt, max_tokens=max_tokens * 2)
    return answer


def _bedarf_line(n: dict[str, Any]) -> str:
    """One context line per actor: what the LLM needs to recommend them."""
    p = n.get("properties") if isinstance(n.get("properties"), dict) else {}
    bits = [f"{n['label']} ({n['node_type']}"]
    bits[0] += f", {p['category']})" if p.get("category") else ")"
    for key, prefix in (
        ("venue", "Ort: "), ("addr_street", "Adresse: "), ("addr_city", ""),
        ("contact_email", "Mail: "), ("contact_phone", "Tel: "),
        ("contact_website", "Web: "), ("role", ""),
    ):
        if p.get(key):
            bits.append(f"{prefix}{str(p[key])[:80]}")
    if p.get("description"):
        bits.append(str(p["description"])[:140])
    if n.get("source_url"):
        bits.append(f"Quelle: {n['source_url']}")
    return "- " + " · ".join(bits)


async def _offers_for_need(conn: Any, need: str, qlit: str, limit: int = 5) -> list[dict]:
    """OFFER-side actors for one resource need: series-deduped, preferring
    non-events, contactable actors, then thematic closeness to the query."""
    actor = _actor_clause("n", business=True)
    rows = await conn.fetch(
        f"""
        SELECT DISTINCT ON (n.label) {_NODE_COLS}
        FROM node_resources r
        JOIN nodes n ON n.id = r.node_id AND n.valid_to IS NULL AND {actor}
        LEFT JOIN node_embeddings e ON e.node_id = n.id
        WHERE r.tag = $1 AND r.kind = 'offer'
        ORDER BY n.label, (n.node_type = 'Event') ASC,
                 (coalesce(n.properties->>'contact_email',
                           n.properties->>'contact_website') IS NULL) ASC,
                 (e.embedding <=> $2::vector) ASC NULLS LAST
        LIMIT 40
        """,
        need, qlit,
    )
    # DISTINCT ON needs label-first ordering; re-rank the survivors by the real
    # preference (non-event, contactable)
    ranked = sorted(
        (dict(r) for r in rows),
        key=lambda x: (
            x["node_type"] == "Event",
            not (isinstance(x.get("properties"), dict)
                 and (x["properties"].get("contact_email")
                      or x["properties"].get("contact_website"))),
        ),
    )
    return ranked[:limit]


async def _bedarf_answer(question: str, intent: dict[str, Any]) -> dict[str, Any]:
    """'Ich brauche…'-Anfragen: the intent pass decomposed the plan into resource
    needs; answer with the OFFER side of the resource layer per need (thematically
    nearest, contactable actors first), plus actors with topic experience."""
    from reasoning.prompts import BEDARF_PROMPT, BEDARF_SYSTEM

    qvec = (await embed_texts([intent["search_text"]]))[0]
    qlit = to_pgvector(qvec)
    actor = _actor_clause("n", business=True)  # who can help = business actors too
    offers: dict[str, list[dict]] = {}
    async with get_conn() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL hnsw.iterative_scan = 'relaxed_order'")
            for need in intent["needs"][:8]:
                offers[need] = await _offers_for_need(conn, need, qlit)
            exp = await conn.fetch(
                f"""SELECT {_NODE_COLS}
                    FROM node_embeddings e JOIN nodes n ON n.id = e.node_id
                    WHERE n.valid_to IS NULL AND {actor}
                    ORDER BY e.embedding <=> $1::vector LIMIT 6""",
                qlit,
            )

    offers_block = "\n".join(
        f"### Bedarf: {need}\n" + ("\n".join(_bedarf_line(n) for n in rows) or "- (keine Treffer)")
        for need, rows in offers.items()
    )
    experience_block = "\n".join(_bedarf_line(dict(r)) for r in exp) or "- (keine Treffer)"

    answer = await _complete_nonempty(
        BEDARF_SYSTEM,
        BEDARF_PROMPT.format(
            today=date.today().isoformat(), question=question,
            search_text=intent["search_text"], offers_block=offers_block,
            experience_block=experience_block,
        ),
    )
    seen: set[str] = set()
    citations = []
    for n in [x for rows in offers.values() for x in rows] + [dict(r) for r in exp]:
        if str(n["id"]) in seen:
            continue
        seen.add(str(n["id"]))
        citations.append(
            {"id": str(n["id"]), "label": n["label"], "node_type": n["node_type"],
             "source": n["source"], "source_url": n["source_url"]}
        )
    return {"answer": answer, "citations": citations, "intent": _intent_out(intent)}


async def _opportunity_answer(question: str, intent: dict[str, Any]) -> dict[str, Any]:
    """General business-opportunity scan. Combines four grounded signals —
    per-Stadtbezirk supply gaps (expected-share), vacant storefronts, district
    demand profiles and news-distilled problems — and lets the model synthesise
    concrete, evidence-anchored openings. Not scoped to any one sector."""
    from reasoning.opportunities import commercial_gaps, district_context, vacant_storefronts
    from reasoning.prompts import OPPORTUNITY_PROMPT, OPPORTUNITY_SYSTEM

    focus = (intent.get("district") or "").strip()
    qvec = (await embed_texts([intent["search_text"]]))[0]
    qlit = to_pgvector(qvec)

    async with get_conn() as conn:
        gaps = await commercial_gaps(conn, limit=40)
        if focus:  # a named district narrows the scan, but keep a fallback
            narrowed = [g for g in gaps if g["district"].lower() == focus.lower()]
            gaps = narrowed or gaps
        gaps = gaps[:20]
        vac = await vacant_storefronts(conn)
        if focus:
            vac = [v for v in vac if v["district"].lower() == focus.lower()] or vac

        flagged = list(dict.fromkeys([g["district"] for g in gaps] + [v["district"] for v in vac[:4]]))[:6]
        profiles = {d: await district_context(conn, d) for d in flagged}

        # corroborating demand: problems nearest the query (and, if focused, the district)
        problems = await conn.fetch(
            f"""SELECT {_NODE_COLS}, (e.embedding <=> $1::vector) AS dist
                FROM nodes n LEFT JOIN node_embeddings e ON e.node_id = n.id
                WHERE n.node_type = 'Problem' AND n.valid_to IS NULL
                  AND ($2 = '' OR lower(coalesce(n.properties->>'district','')) = lower($2))
                ORDER BY (e.embedding <=> $1::vector) ASC NULLS LAST LIMIT 6""",
            qlit, focus,
        )

        # district nodes for provenance
        dnodes = await conn.fetch(
            f"""SELECT {_NODE_COLS} FROM nodes
                WHERE node_type = 'GeoArea' AND valid_to IS NULL
                  AND properties->>'area_type' = 'stadtbezirk' AND label = ANY($1::text[])""",
            flagged,
        )

    gaps_txt = "\n".join(
        f"- {g['district']} — {g['cat']}: ist {g['actual']}, erwartet ≈ {g['expected']} "
        f"(Defizit {g['deficit']})" for g in gaps
    ) or "(keine ausgeprägten Lücken)"
    vac_txt = "\n".join(f"- {v['district']}: {v['vacant_units']} freie Ladenlokale" for v in vac[:8]) \
        or "(keine erfassten Leerstände)"
    prof_txt = "\n".join(
        f"- {d}: " + ", ".join(f"{k}={v}" for k, v in prof.items()) for d, prof in profiles.items() if prof
    ) or "(keine Profildaten)"
    prob_txt = "\n".join(
        f"- „{p['label'][:90]}“ ({(p['properties'] or {}).get('district') or '—'} / "
        f"{(p['properties'] or {}).get('theme') or '—'})"
        for p in problems if isinstance(p["properties"], dict)
    ) or "(keine passenden Probleme erfasst)"

    answer = await _complete_nonempty(
        OPPORTUNITY_SYSTEM,
        OPPORTUNITY_PROMPT.format(
            today=date.today().isoformat(), question=question,
            gaps=gaps_txt, vacancies=vac_txt, profiles=prof_txt, problems=prob_txt,
        ),
    )
    citations = [
        {"id": str(n["id"]), "label": n["label"], "node_type": n["node_type"],
         "source": n["source"], "source_url": n["source_url"]}
        for n in list(dnodes) + list(problems)
    ]
    return {"answer": answer, "citations": citations, "intent": _intent_out(intent)}


async def _foerderung_answer(question: str, intent: dict[str, Any]) -> dict[str, Any]:
    """'Welche Förderungen passen zu X': resolve the named actor (name match
    first, semantic fallback), run the eligibility matcher, present verdicts."""
    from reasoning.foerderung_match import match_programs

    qvec = (await embed_texts([intent["search_text"]]))[0]
    qlit = to_pgvector(qvec)
    actor_clause = _actor_clause("n", business=True)
    async with get_conn() as conn:
        actor = await conn.fetchrow(
            f"""SELECT {_NODE_COLS}, similarity(lower(n.label), lower($1)) AS sim
                FROM nodes n WHERE n.valid_to IS NULL AND {actor_clause}
                  AND similarity(lower(n.label), lower($1)) > 0.35
                ORDER BY sim DESC LIMIT 1""",
            intent["search_text"],
        )
        if not actor:
            async with conn.transaction():
                await conn.execute("SET LOCAL hnsw.iterative_scan = 'relaxed_order'")
                actor = await conn.fetchrow(
                    f"""SELECT {_NODE_COLS} FROM node_embeddings e
                        JOIN nodes n ON n.id = e.node_id
                        WHERE n.valid_to IS NULL AND {actor_clause}
                        ORDER BY e.embedding <=> $1::vector LIMIT 1""",
                    qlit,
                )
    if not actor:
        return {"answer": "Dazu wurde kein passender Akteur im Graphen gefunden.",
                "citations": [], "intent": _intent_out(intent)}

    results = await match_programs(str(actor["id"]))
    if not results:
        return {
            "answer": f"Für **{actor['label']}** wurden im Förderbestand "
                      "(NRW.BANK, Land + Bund) keine Programme mit passender "
                      "Zielgruppe gefunden.",
            "citations": [], "intent": _intent_out(intent),
        }
    parts = [f"**Förder-Check für {actor['label']}** — {len(results)} Programme geprüft.\n"]
    fits = [r for r in results if r["verdict"] == "passt"]
    maybes = [r for r in results if r["verdict"] == "vielleicht"]
    rejected = [r for r in results if r["verdict"] == "passt_nicht"]
    for title, rows in (("Passt", fits), ("Vielleicht — selbst zu prüfen", maybes)):
        if rows:
            parts.append(f"## {title}")
            for r in rows:
                p = r.get("properties") or {}
                parts.append(
                    f"### {r['program']}\n"
                    f"{p.get('level', '')} · {p.get('funding_type', '')}"
                    f" · max. {p.get('max_amount_eur') or '—'} €"
                    f" · Frist: {p.get('deadline') or 'laufend'}\n\n"
                    f"{r['begruendung']}"
                    + (f"\n\n**Zu prüfen:** {r['zu_pruefen']}" if r.get("zu_pruefen") else "")
                    + f"\n\n[Zum Programm]({r['source_url']})"
                )
    if rejected:
        parts.append("---\n### Geprüft und verworfen")
        parts += [f"- **{r['program']}** — {r['begruendung']}" for r in rejected]
    citations = [{"id": str(actor["id"]), "label": actor["label"],
                  "node_type": actor["node_type"], "source": actor["source"],
                  "source_url": actor["source_url"]}]
    citations += [
        {"id": r["program_id"], "label": r["program"], "node_type": "FundingProgram",
         "source": "nrwbank_foerderung", "source_url": r["source_url"]}
        for r in results if r["verdict"] != "passt_nicht"
    ]
    return {"answer": "\n\n".join(parts), "citations": citations,
            "intent": _intent_out(intent)}


async def _problem_answer(
    question: str, intent: dict[str, Any], deep: bool = False
) -> dict[str, Any]:
    """'Welche Probleme hat X und wer löst sie': current Problem nodes relevant
    to the query (semantic + district name match), each with its evidence
    articles and solver candidates per tagged need. deep=True (Tiefensuche) adds
    the review pass: candidates researched on their websites, one critical LLM
    call per problem rejects implausible ones with reasons."""
    from reasoning.coalitions import assemble_coalition
    from reasoning.prompts import PROBLEM_ANSWER_PROMPT, PROBLEM_ANSWER_SYSTEM

    qvec = (await embed_texts([intent["search_text"]]))[0]
    qlit = to_pgvector(qvec)
    blocks: list[str] = []
    cited: list[dict] = []
    total = 0
    async with get_conn() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL hnsw.iterative_scan = 'relaxed_order'")
            total = await conn.fetchval(
                "SELECT count(*) FROM nodes WHERE node_type = 'Problem' AND valid_to IS NULL"
            )
            # district mentions in the query outrank pure embedding distance;
            # NULLS LAST keeps not-yet-embedded problems reachable
            ranked = await conn.fetch(
                f"""
                SELECT {_NODE_COLS},
                       (e.embedding <=> $1::vector) AS dist,
                       (length($2) >= 4 AND lower($2) LIKE
                        '%' || lower(coalesce(n.properties->>'district', '###')) || '%')
                       AS district_hit
                FROM nodes n
                LEFT JOIN node_embeddings e ON e.node_id = n.id
                WHERE n.node_type = 'Problem' AND n.valid_to IS NULL
                ORDER BY (length($2) >= 4 AND lower($2) LIKE
                          '%' || lower(coalesce(n.properties->>'district', '###')) || '%') DESC,
                         (e.embedding <=> $1::vector) ASC NULLS LAST
                LIMIT 4
                """,
                qlit, intent["search_text"][:80],
            )
            # relevance gate: an off-topic problem is worse than none — better
            # to say the catalog has nothing for this area/theme yet
            ranked = [
                p for p in ranked
                if p["district_hit"] or (p["dist"] is not None and p["dist"] <= 0.62)
            ]
            if deep:
                ranked = ranked[:2]  # website research is expensive
            per_problem: list[tuple[str, list[dict]]] = []  # (block, candidates)
            for p in ranked:
                pid = str(p["id"])
                props = p["properties"] if isinstance(p["properties"], dict) else {}
                cited.append(dict(p))
                cands: list[dict] = []
                evidence = await conn.fetch(
                    """
                    SELECT a.label, a.source_url, a.valid_from::date AS d
                    FROM edges e JOIN nodes a ON a.id = e.from_node_id
                    WHERE e.to_node_id = $1::uuid AND e.edge_type = 'MENTIONS'
                      AND e.valid_to IS NULL
                    ORDER BY a.valid_from DESC LIMIT 3
                    """,
                    pid,
                )
                needs = [
                    r["tag"] for r in await conn.fetch(
                        "SELECT tag FROM node_resources WHERE node_id = $1 AND kind = 'need'",
                        pid,
                    )
                ]
                lines = [
                    f"## Problem: {p['label']}",
                    f"Thema: {props.get('theme')} · Stadtteil: {props.get('district') or '—'}"
                    f" · Betroffene: {props.get('affected') or '—'}"
                    f" · letzter Beleg: {props.get('last_evidence') or '?'}",
                    "Belege:",
                ]
                lines += [
                    f"- „{e['label'][:90]}“ ({e['d']}) {e['source_url']}" for e in evidence
                ]
                if needs:
                    # problem-anchored coalition: relevance-ranked set-cover of the
                    # problem's needs by real actors' offers (who together cover it)
                    co = await assemble_coalition(conn, pid)
                    if co["members"]:
                        lines.append(
                            f"Bedarfe des Problems: {', '.join(co['needs'])}. "
                            "Mögliche KOALITION (Akteure, die sie GEMEINSAM decken):"
                        )
                        for m in co["members"]:
                            member = {**m, "properties": {}}
                            cited.append(member)
                            cands.append(member)
                            lines.append(
                                f"- {m['label']} ({m['node_type']}) — deckt: "
                                f"{', '.join(m['covers'])}"
                            )
                        if co["uncovered"]:
                            lines.append(
                                f"Nicht durch Akteure gedeckt: {', '.join(co['uncovered'])}"
                            )
                    else:
                        lines.append(
                            f"Bedarfe: {', '.join(needs)}. Keine hinreichend relevanten "
                            "Akteure im Graphen, um eine Koalition zu bilden."
                        )
                else:
                    lines.append(
                        "Keine Ressourcen-Bedarfe getaggt — nur thematisch passende Akteure:"
                    )
                    exp = await conn.fetch(
                        f"""SELECT {_NODE_COLS}
                            FROM node_embeddings e JOIN nodes n ON n.id = e.node_id
                            WHERE n.valid_to IS NULL AND {_actor_clause("n", True)}
                            ORDER BY e.embedding <=> (
                                SELECT embedding FROM node_embeddings WHERE node_id = $1::uuid)
                            LIMIT 4""",
                        pid,
                    )
                    cited += [dict(r) for r in exp]
                    cands += [dict(r) for r in exp]
                    lines += [_bedarf_line(dict(n)) for n in exp] or ["- (keine Treffer)"]
                blocks.append("\n".join(lines))
                per_problem.append(("\n".join(lines), cands))

    if not blocks:
        return {
            "answer": (
                f"Für diese Frage ist im Problem-Layer noch nichts Passendes erfasst "
                f"(aktuell {total} destillierte Probleme insgesamt; die Extraktion "
                f"läuft über den Nachrichtenbestand). Später erneut fragen."
            ),
            "citations": [], "intent": _intent_out(intent),
        }
    if deep:
        # the review pass: research each candidate's website + graph context,
        # then one critical LLM call per problem — implausible candidates are
        # rejected with a reason, contributions must be evidence-backed
        import httpx

        from config import settings
        from reasoning.prompts import PROBLEM_DEEP_PROMPT, PROBLEM_DEEP_SYSTEM
        from reasoning.synergy_finder import _research_actor

        pieces: list[str] = []
        async with httpx.AsyncClient(
            headers={"User-Agent": settings.bot_user_agent}
        ) as client:
            for block, cands in per_problem:
                dossiers = []
                for c in cands[:6]:
                    ctx, site, url = await _research_actor(client, c)
                    dossiers.append(
                        f"### {c['label']}\n{ctx}\nWebsite ({url or '—'}): "
                        f"{site or '(keine Website gefunden)'}"
                    )
                pieces.append(
                    await _complete_nonempty(
                        PROBLEM_DEEP_SYSTEM,
                        PROBLEM_DEEP_PROMPT.format(
                            today=date.today().isoformat(),
                            problem_block=block,
                            dossiers="\n\n".join(dossiers) or "(keine Kandidaten)",
                        ),
                    )
                )
        answer = "\n\n---\n\n".join(pieces)
    else:
        answer = await _complete_nonempty(
            PROBLEM_ANSWER_SYSTEM,
            PROBLEM_ANSWER_PROMPT.format(
                today=date.today().isoformat(), question=question,
                problems_block="\n\n".join(blocks),
            ),
        )
    seen: set[str] = set()
    citations = []
    for n in cited:
        if str(n["id"]) in seen:
            continue
        seen.add(str(n["id"]))
        citations.append(
            {"id": str(n["id"]), "label": n["label"], "node_type": n["node_type"],
             "source": n["source"], "source_url": n["source_url"]}
        )
    return {"answer": answer, "citations": citations, "intent": _intent_out(intent)}


async def answer_question(
    question: str, k: int = 24, lens_override: str | None = None, retrieval: str = "semantic"
) -> dict[str, Any]:
    """Answer a question from the most relevant graph facts. lens_override forces
    a lens (e.g. the dedicated leads window). retrieval='structural' (Chat v2)
    augments synergy/leads seeds with PostGIS proximity partners (link-prediction)
    instead of pure semantic similarity."""
    question = (question or "").strip()
    if not question:
        return {"answer": "Bitte eine Frage eingeben.", "citations": [], "intent": {}}

    intent = await extract_intent(question)
    if lens_override in _LENSES:
        intent["lens"] = lens_override
    # "neueste/aktuelle" → a rolling recency window on observed_at (when we
    # INGESTED the node), not valid_from (an event's future date). This is what
    # makes "synergies from recent events" follow what was just reported.
    intent["recent_since"] = (
        date.today() - timedelta(days=14) if intent.get("recent") else None
    )
    # Problem: which problems does the city/district have, and who could solve
    # them — problems from the news-distilled problem layer, solvers per need.
    # Tiefensuche on a problem question = the review pass: candidates researched
    # on their websites, implausible ones rejected with reasons.
    if intent["lens"] == "problem":
        return await _problem_answer(question, intent, deep=(retrieval == "deep"))
    # Tiefensuche: run the research+validate synergy pipeline instead of a single
    # answer (each partner researched in the graph + on their website).
    if retrieval == "deep":
        return await _deep_synergy_answer(intent)
    # Bedarf: the asker has a plan and wants help — decompose into resource needs
    # and answer with the offer side of the resource layer, grouped per need.
    if intent["lens"] == "bedarf" and intent.get("needs"):
        return await _bedarf_answer(question, intent)
    # Förderung: which funding programs fit a concrete actor — resolve the actor,
    # run the eligibility matcher, answer with verdicts + homework.
    if intent["lens"] == "foerderung":
        return await _foerderung_answer(question, intent)
    # Chance: general business-opportunity scan — spatial supply gaps + vacancies +
    # district demand profile + open problems, synthesised into concrete openings.
    if intent["lens"] == "chance":
        return await _opportunity_answer(question, intent)
    # Enumeration only makes sense for factual "list all X" queries; analytical
    # lenses always want the focused + graph-expanded subgraph, never a dump.
    list_mode = bool(intent["list"]) and intent["lens"] == "factual"
    # "List all events" without an explicit date → default to upcoming (today on).
    if list_mode and not intent["date_from"] and (intent["category"] or "Event" in intent["node_types"]):
        intent["date_from"] = date.today()

    # Leads = business development: scope to real business actors, not news, so the
    # retrieval surfaces actual prospects (POIs/Orgs) instead of editorial roundups.
    if intent["lens"] == "leads" and not intent["node_types"]:
        intent["node_types"] = ["POI", "Organization"]

    qvec = (await embed_texts([intent["search_text"]]))[0]
    lit = to_pgvector(qvec)

    has_filters = bool(
        intent["node_types"] or intent["category"] or intent["date_from"] or intent["date_to"]
    )
    # Diversify (MMR + random anchor) for exploratory analytical asks so they don't
    # keep returning the same dense cluster: any broad lens, and always leads
    # (inherently exploratory, but scoped to its business node_types via intent).
    diversify = (
        intent["lens"] != "factual" and not list_mode
        and (not has_filters or intent["lens"] == "leads")
    )
    k_eff = 60 if list_mode else k
    lens_synergy = intent["lens"] in {"synergy", "leads"} and not list_mode
    structural = retrieval == "structural" and lens_synergy
    complementary = retrieval == "complementary" and lens_synergy
    graph = retrieval == "graph" and lens_synergy
    async with get_conn() as conn:
        if graph:
            # ONLY the graph-structural signal: query-relevant actor seeds → actors
            # that share specific neighbours (articles/events/tenders) but aren't
            # linked — weighted common-neighbour link prediction.
            biz = _is_business_query(intent["search_text"])
            g_actor = _actor_clause("n", biz)
            # honor the query: fuzzy NAME-match seeds first, so a query about a named
            # actor anchors on it (not just the semantic theme), then semantic seeds.
            name_seeds = await conn.fetch(
                f"""SELECT {_NODE_COLS} FROM nodes n WHERE n.valid_to IS NULL AND {g_actor}
                    AND similarity(lower(n.label), lower($1)) > 0.3
                    ORDER BY similarity(lower(n.label), lower($1)) DESC LIMIT 3""",
                intent["search_text"],
            )
            graph_intent = {**intent, "node_types": intent["node_types"] or ["Event", "POI", "Organization"]}
            sem_seeds = await _diverse_seeds(conn, qvec, min(k_eff, 12), graph_intent)
            seen = set()
            seeds = []
            for r in list(name_seeds) + list(sem_seeds):
                if str(r["id"]) not in seen:
                    seen.add(str(r["id"]))
                    seeds.append(dict(r))
            seed_ids = [str(s["id"]) for s in seeds]
            part = await _link_prediction_partners(conn, seed_ids, biz, cap=16)
            nodes = seeds + [r for r in part if str(r["id"]) not in seen]
        elif structural or complementary:
            # query-relevant seeds, then their partners: proximity (structural) or
            # need↔offer fit (complementary). The pairs are the signal (no expand).
            biz = _is_business_query(intent["search_text"])
            # a query naming a specific actor must anchor on it — fuzzy label match
            # first (so "Synergien für Sascha Staat" seeds Sascha, a Journalist),
            # then the semantic theme. Actor types are included so orgs/journalists
            # (not just Event/POI) can be seeds.
            name_seeds = await conn.fetch(
                f"""SELECT {_NODE_COLS} FROM nodes n WHERE n.valid_to IS NULL
                    AND {_actor_clause('n', biz)}
                    AND similarity(lower(n.label), lower($1)) > 0.3
                    ORDER BY similarity(lower(n.label), lower($1)) DESC LIMIT 3""",
                intent["search_text"],
            )
            geo_intent = {
                **intent,
                "node_types": intent["node_types"]
                or ["Event", "POI", "Organization", "Journalist"],
            }
            sem_seeds = await _diverse_seeds(conn, qvec, min(k_eff, 14), geo_intent)
            seen_seed: set[str] = set()
            seeds = []
            for r in list(name_seeds) + list(sem_seeds):
                if str(r["id"]) not in seen_seed:
                    seen_seed.add(str(r["id"]))
                    seeds.append(dict(r))
            seed_ids = [str(s["id"]) for s in seeds]
            partner_ids = (
                await _complementary_partners(conn, seed_ids, business=biz) if complementary
                else await _structural_partners(conn, seed_ids, business=biz)
            )
            seen = set(seed_ids)
            extra = await conn.fetch(
                f"SELECT {_NODE_COLS} FROM nodes WHERE id = ANY($1::uuid[]) AND valid_to IS NULL",
                [pid for pid in partner_ids if pid not in seen],
            ) if partner_ids else []
            nodes = seeds + [dict(r) for r in extra]
        elif intent["lens"] == "leads":
            # Akquise: district-aware business-actor retrieval (contact-first,
            # tender winners boosted, already-worked leads suppressed)
            async with conn.transaction():
                await conn.execute("SET LOCAL hnsw.iterative_scan = 'relaxed_order'")
                nodes = await _leads_candidates(conn, intent, lit)
        elif diversify:
            nodes = await _diverse_seeds(conn, qvec, k_eff, intent)
        else:
            nodes = await _retrieve(conn, lit, intent, k_eff, use_filters=has_filters, list_mode=list_mode)
            # If filters were too narrow and found nothing, retry unfiltered + semantic.
            if not nodes and has_filters:
                nodes = await _retrieve(conn, lit, intent, k_eff, use_filters=False, list_mode=False)
        if not nodes:
            return {
                "answer": "Dazu liegen im Wissensgraphen keine passenden Fakten vor.",
                "citations": [],
                "intent": _intent_out(intent),
            }
        ids = [str(n["id"]) for n in nodes]
        # Multi-hop graph expansion (analytical/factual only — not enumeration).
        # Skipped in structural/complementary/graph: the pairs ARE the signal, and
        # expansion would flood them with text neighbours (AgendaItem/Road).
        if not list_mode and not structural and not complementary and not graph:
            ids = await _expand(conn, ids, max_total=40)
            nodes = await conn.fetch(
                f"SELECT {_NODE_COLS} FROM nodes WHERE id = ANY($1::uuid[]) AND valid_to IS NULL",
                ids,
            )
        edges = await conn.fetch(
            """
            SELECT id, edge_type, from_node_id, to_node_id, properties, source
            FROM edges
            WHERE from_node_id = ANY($1::uuid[]) AND to_node_id = ANY($1::uuid[])
              AND valid_to IS NULL
            """,
            ids,
        )

    subgraph = format_subgraph([dict(n) for n in nodes], [dict(e) for e in edges])
    today = date.today().isoformat()
    lens = intent["lens"]
    if lens == "factual":
        system = QA_SYSTEM_PROMPT
        prompt = QA_PROMPT.format(question=question, subgraph_json=subgraph, today=today)
    else:
        # analytical lens (synergy / inefficiency / scandal) over the same subgraph
        system = ANALYSIS_SYSTEM_PROMPTS[lens]
        prompt = ANALYSIS_PROMPT.format(question=question, subgraph_json=subgraph, today=today)
    # Reasoning models (e.g. deepseek-v4-pro) spend the budget on hidden
    # reasoning before the answer, so give ample headroom or `content` truncates
    # to empty. 8000 makes that rare; the guard below keeps the UI non-blank.
    answer = await _complete_nonempty(system, prompt)
    if not answer.strip():
        answer = "_Die Antwort konnte nicht erzeugt werden — bitte erneut versuchen._"

    citations = [
        {
            "id": str(n["id"]),
            "label": n["label"],
            "node_type": n["node_type"],
            "source": n["source"],
            "source_url": n["source_url"],
        }
        for n in nodes
    ]
    return {"answer": answer, "citations": citations, "intent": _intent_out(intent)}


_ANCHOR_LABEL = {
    "synergy": "Synergien", "inefficiency": "Ineffizienzen",
    "scandal": "Auffälligkeiten", "crime": "Vorfallsmuster",
}


async def analyze_node(node_id: str, lens: str, k: int = 20) -> dict[str, Any]:
    """
    Anchored analysis: run a lens (synergy/inefficiency/…) centred on ONE node
    (e.g. a chosen event), over that node + its semantically-nearest neighbours +
    their edges. Powers the 'add to chat' event picker.
    """
    lens = lens if lens in _ANCHOR_LABEL else "synergy"
    async with get_conn() as conn:
        anchor = await conn.fetchrow(
            "SELECT id, node_type, label, properties, source, source_url, valid_from "
            "FROM nodes WHERE id = $1 AND valid_to IS NULL",
            node_id,
        )
        if anchor is None:
            return {"answer": "Knoten nicht gefunden.", "citations": [], "intent": {"lens": lens}}
        neighbours = await conn.fetch(
            """
            SELECT n.id, n.node_type, n.label, n.properties, n.source, n.source_url, n.valid_from
            FROM node_embeddings e
            JOIN nodes n ON n.id = e.node_id
            WHERE n.valid_to IS NULL AND n.id <> $1
            ORDER BY e.embedding <=> (SELECT embedding FROM node_embeddings WHERE node_id = $1)
            LIMIT $2
            """,
            node_id, k,
        )
        seed_ids = [str(anchor["id"])] + [str(n["id"]) for n in neighbours]
        # graph-expand from the anchor + its semantic neighbours
        ids = await _expand(conn, seed_ids, max_total=40)
        nodes = await conn.fetch(
            f"SELECT {_NODE_COLS} FROM nodes WHERE id = ANY($1::uuid[]) AND valid_to IS NULL",
            ids,
        )
        nodes = [dict(n) for n in nodes]
        edges = await conn.fetch(
            """
            SELECT id, edge_type, from_node_id, to_node_id, properties, source
            FROM edges
            WHERE from_node_id = ANY($1::uuid[]) AND to_node_id = ANY($1::uuid[])
              AND valid_to IS NULL
            """,
            ids,
        )

    question = f"Welche {_ANCHOR_LABEL[lens]} gibt es rund um: {anchor['label']}?"
    subgraph = format_subgraph(nodes, edges)
    prompt = ANALYSIS_PROMPT.format(
        question=question, subgraph_json=subgraph, today=date.today().isoformat()
    )
    answer = await _complete_nonempty(ANALYSIS_SYSTEM_PROMPTS[lens], prompt)
    if not answer.strip():
        answer = "_Die Analyse konnte nicht erzeugt werden — bitte erneut versuchen._"
    citations = [
        {
            "id": str(n["id"]), "label": n["label"], "node_type": n["node_type"],
            "source": n["source"], "source_url": n["source_url"],
        }
        for n in nodes
    ]
    return {
        "answer": answer,
        "citations": citations,
        "question": question,
        "intent": {"lens": lens, "anchor": anchor["label"]},
    }


async def discuss(node_ids: list[str], messages: list[dict[str, str]]) -> dict[str, Any]:
    """
    Follow-up conversation grounded in a previously-found result. Reasons over the
    SAME evidence subgraph (node_ids from that answer) + the chat transcript, so
    the user can deepen a specific synergy/lead/finding without re-retrieving.
    """
    ids = [str(i) for i in (node_ids or [])][:60]
    if not ids or not messages:
        return {"answer": "Kein Kontext zum Vertiefen.", "citations": []}

    async with get_conn() as conn:
        nodes = await conn.fetch(
            f"SELECT {_NODE_COLS} FROM nodes WHERE id = ANY($1::uuid[]) AND valid_to IS NULL",
            ids,
        )
        edges = await conn.fetch(
            """
            SELECT id, edge_type, from_node_id, to_node_id, properties, source
            FROM edges
            WHERE from_node_id = ANY($1::uuid[]) AND to_node_id = ANY($1::uuid[])
              AND valid_to IS NULL
            """,
            ids,
        )

    transcript = "\n".join(
        f"{'Nutzer' if m.get('role') == 'user' else 'Assistent'}: {m.get('content', '')}"
        for m in messages
    )
    prompt = DISCUSS_PROMPT.format(
        subgraph_json=format_subgraph([dict(n) for n in nodes], [dict(e) for e in edges]),
        transcript=transcript,
        today=date.today().isoformat(),
    )
    answer = await _complete_nonempty(DISCUSS_SYSTEM_PROMPT, prompt)
    if not answer.strip():
        answer = "_Die Antwort konnte nicht erzeugt werden — bitte erneut versuchen._"
    citations = [
        {
            "id": str(n["id"]), "label": n["label"], "node_type": n["node_type"],
            "source": n["source"], "source_url": n["source_url"],
        }
        for n in nodes
    ]
    return {"answer": answer, "citations": citations}
