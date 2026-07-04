"""
Deep synergy finder: generate candidate synergies, then research + validate each
with a per-synergy sub-agent before returning it.

Stage 1 — candidate pairs from the concrete-pair generators (proximity +
complementary), which name the two INVOLVED PARTNERS.
Stage 2 — per candidate, a research sub-agent gathers each partner's full graph
context AND fetches their website (where a URL is known), then judges whether the
synergy is realistic. Implausible ones are dropped; more candidates are validated
until `n` hold up.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from datetime import date
from typing import Any

import httpx
from bs4 import BeautifulSoup

from config import settings
from db.session import get_conn
from reasoning.llm import complete
from reasoning.prompts import (
    SYNERGY_COMPARE_PROMPT,
    SYNERGY_COMPARE_SYSTEM,
    SYNERGY_RESEARCH_PROMPT,
    SYNERGY_RESEARCH_SYSTEM,
)

logger = logging.getLogger(__name__)

_URL_KEYS = ("website", "contact_website", "url")


def _parse_obj(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1].lstrip("json").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}


def pair_key(a: str, b: str) -> str:
    x, y = sorted([str(a), str(b)])
    return f"{x}|{y}"


async def suppressed_pair_keys() -> set[str]:
    """Pairs that must never be proposed as partners: ones the finder or the
    user judged negative, plus pairs entity resolution merged (SAME_AS) — an
    actor can't have a synergy with its own register entry or storefront."""
    async with get_conn() as conn:
        rows = await conn.fetch(
            "SELECT pair_key FROM synergy_feedback WHERE verdict IN ('reject','dismissed')"
        )
        same = await conn.fetch(
            "SELECT from_node_id, to_node_id FROM edges"
            " WHERE edge_type = 'SAME_AS' AND valid_to IS NULL"
        )
    keys = {r["pair_key"] for r in rows}
    keys |= {pair_key(r["from_node_id"], r["to_node_id"]) for r in same}
    return keys


async def record_synergy_feedback(results: list[dict[str, Any]], source: str = "llm") -> int:
    """Persist verdicts on pairs. User verdicts outrank the LLM's (a later llm write
    never clobbers a user one)."""
    rows = []
    for r in results:
        ev = r.get("evidence_node_ids") or []
        if len(ev) < 2 or not r.get("verdict"):
            continue
        a, b = sorted([str(ev[0]), str(ev[1])])
        rows.append((f"{a}|{b}", a, b, r["verdict"], (r.get("reason") or "")[:500], source))
    if not rows:
        return 0
    async with get_conn() as conn:
        await conn.executemany(
            """
            INSERT INTO synergy_feedback (pair_key, node_a, node_b, verdict, reason, source, updated_at)
            VALUES ($1,$2::uuid,$3::uuid,$4,$5,$6, now())
            ON CONFLICT (pair_key) DO UPDATE SET
                verdict = EXCLUDED.verdict, reason = EXCLUDED.reason,
                source = EXCLUDED.source, updated_at = now()
            WHERE synergy_feedback.source <> 'user' OR EXCLUDED.source = 'user'
            """,
            rows,
        )
        # keep the all-pairs frontier in sync: judged pairs leave `new` at once,
        # not only at the next candidate refresh
        await conn.execute(
            """
            UPDATE synergy_candidates sc
            SET status = CASE WHEN f.verdict IN ('reject', 'dismissed')
                              THEN 'suppressed' ELSE 'validated' END
            FROM synergy_feedback f
            WHERE f.pair_key = sc.pair_key AND sc.status = 'new'
            """
        )
    return len(rows)


def _parse_list(raw: str) -> list[dict[str, Any]]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1].lstrip("json").strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    return [d for d in data if isinstance(d, dict)]


async def _gather_partner_context(conn: Any, node: dict[str, Any]) -> tuple[str, str | None]:
    """Full graph context for one partner + its website URL (if any)."""
    nid = str(node["id"])
    raw_props = node.get("properties")
    raw_props = raw_props if isinstance(raw_props, dict) else {}
    props = {k: v for k, v in raw_props.items() if v not in (None, "")}
    rows = await conn.fetch(
        """
        SELECT DISTINCT e.edge_type AS et, n2.node_type AS nt, n2.label AS lbl
        FROM edges e
        JOIN nodes n2 ON n2.id = CASE WHEN e.from_node_id = $1 THEN e.to_node_id
                                      ELSE e.from_node_id END
        WHERE (e.from_node_id = $1 OR e.to_node_id = $1)
          AND e.valid_to IS NULL AND n2.valid_to IS NULL
        LIMIT 15
        """,
        nid,
    )
    neighbours = "; ".join(f"{r['et']} {r['nt']}:{(r['lbl'] or '')[:40]}" for r in rows) or "keine"
    url = next((props[k] for k in _URL_KEYS if props.get(k)), None)
    ctx = (
        f"{node['node_type']}: {node['label']}\n"
        f"Quelle: {node.get('source')} {node.get('source_url') or ''}\n"
        f"Eigenschaften: {json.dumps(props, ensure_ascii=False)[:800]}\n"
        f"Verbindungen im Graphen: {neighbours}"
    )
    return ctx, url


async def _fetch_website(url: str | None, client: httpx.AsyncClient) -> str:
    if not url:
        return ""
    if not url.startswith("http"):
        url = "https://" + url
    try:
        r = await client.get(url, timeout=8.0, follow_redirects=True)
        if r.status_code >= 400 or "text/html" not in r.headers.get("content-type", "text/html"):
            return ""
        soup = BeautifulSoup(r.text, "html.parser")
        for t in soup(["script", "style", "nav", "footer", "header"]):
            t.decompose()
        return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))[:2000]
    except Exception as exc:  # unreachable / timeout / bad cert — research without it
        logger.info("website fetch failed (%s): %s", url, exc)
        return ""


async def _validate(ctx_a: str, site_a: str, ctx_b: str, site_b: str, note: str) -> dict[str, Any]:
    prompt = SYNERGY_RESEARCH_PROMPT.format(
        today=date.today().isoformat(),
        note=note or "—",
        ctx_a=ctx_a, site_a=site_a or "(keine Website gefunden)",
        ctx_b=ctx_b, site_b=site_b or "(keine Website gefunden)",
    )
    return _parse_obj(await complete(SYNERGY_RESEARCH_SYSTEM, prompt, max_tokens=4000))


async def _research_actor(client: httpx.AsyncClient, node: dict) -> tuple[str, str, str]:
    """Graph context + website for one actor (own pool conn → concurrency-safe)."""
    async with get_conn() as conn:
        ctx, url = await _gather_partner_context(conn, node)
    site = await _fetch_website(url, client)
    return ctx, site, url


async def evaluate_anchor(
    client: httpx.AsyncClient, anchor: dict, partners: list[dict],
) -> list[dict[str, Any]]:
    """Comparative validation (#2): research the anchor + ALL its candidate partners,
    then ONE LLM call ranks/picks the best real synergies (and rejects the rest with
    reasons) — instead of N isolated per-pair yes/no calls. The prompt prefers
    non-obvious cross-domain bridges (#4)."""
    if not partners:
        return []
    a_ctx, a_site, a_url = await _research_actor(client, anchor)
    researched = await asyncio.gather(*(_research_actor(client, p) for p in partners))

    blocks = []
    for i, (p, (ctx, site, _url)) in enumerate(zip(partners, researched)):
        blocks.append(f"[{i}] {ctx}\nWebsite: {site or '(keine gefunden)'}")
    prompt = SYNERGY_COMPARE_PROMPT.format(
        today=date.today().isoformat(),
        anchor_ctx=a_ctx, anchor_site=a_site or "(keine gefunden)",
        candidates="\n\n".join(blocks),
    )
    items = _parse_list(await complete(SYNERGY_COMPARE_SYSTEM, prompt, max_tokens=6000))

    results: list[dict[str, Any]] = []
    for it in items:
        idx = it.get("partner_index")
        if not isinstance(idx, int) or not (0 <= idx < len(partners)):
            continue
        p = partners[idx]
        if it.get("verdict") not in ("makes_sense", "reject"):
            it["verdict"] = "reject"
        it["partners"] = [anchor["label"], p["label"]]
        it["evidence_node_ids"] = [str(anchor["id"]), str(p["id"])]
        it["researched_websites"] = [u for u in (a_url, researched[idx][2]) if u]
        results.append(it)
    return results


async def _global_pairs(pool: int = 40) -> list[tuple[dict, dict, str]]:
    from reasoning.scanner import (
        complementary_candidates,
        dedup_candidates,
        structural_synergy_candidates,
    )

    # First choice: the precomputed all-pairs frontier (candidate_engine) —
    # the highest-scoring not-yet-validated pairs across the whole actor set.
    from reasoning.candidate_engine import frontier_pairs

    frontier = await frontier_pairs(limit=pool)
    if len(frontier) >= pool // 2:
        return frontier

    # Fallback (frontier empty / not yet refreshed): the live generators.
    # Favour complementary (need↔offer = real audience/occasion fit) over pure
    # proximity, which produces near-but-incompatible pairs.
    from collections import Counter

    cands = dedup_candidates(
        await complementary_candidates(limit=pool)
        + await structural_synergy_candidates(limit=max(pool // 2, 8))
    )
    pairs = []
    used: Counter[str] = Counter()
    for c in cands:
        p = [nd for nd in c.nodes if nd["node_type"] != "GeoArea"][:2]
        if len(p) < 2:
            continue
        # cap node reuse so a few venues/POIs (e.g. the nearest gym) don't recur
        if any(used[str(nd["id"])] >= 2 for nd in p):
            continue
        for nd in p:
            used[str(nd["id"])] += 1
        pairs.append((p[0], p[1], c.note))
    return pairs


async def find_synergies(
    n: int = 5, pairs: list[tuple[dict, dict, str]] | None = None, shuffle: bool = True,
) -> list[dict[str, Any]]:
    """
    Research + validate synergy pairs. Returns ALL evaluated results (validated
    AND rejected, each with a `verdict` + `reason`), stopping once `n` have been
    validated. `pairs` lets the caller pass query-scoped partner pairs; otherwise
    they come from the global proximity + complementary generators.
    """
    if pairs is None:
        pairs = await _global_pairs(pool=40)
    suppressed = await suppressed_pair_keys()
    pairs = [
        (a, b, note) for a, b, note in pairs
        if pair_key(str(a["id"]), str(b["id"])) not in suppressed
    ]

    # Group by anchor (first node of each pair) → one comparative LLM call per anchor
    # over all its candidate partners (fewer calls, better selection).
    anchors: dict[str, tuple[dict, list[dict]]] = {}
    for a, b, _note in pairs:
        aid = str(a["id"])
        if aid not in anchors:
            anchors[aid] = (a, [])
        if str(b["id"]) != aid and all(str(b["id"]) != str(x["id"]) for x in anchors[aid][1]):
            anchors[aid][1].append(b)
    order = list(anchors.values())
    if shuffle:
        random.shuffle(order)

    results: list[dict[str, Any]] = []
    validated = 0
    async with httpx.AsyncClient(headers={"User-Agent": settings.bot_user_agent}) as client:
        for anchor, partners in order:
            evaluated = await evaluate_anchor(client, anchor, partners[:6])
            results.extend(evaluated)
            validated = sum(
                1 for r in results if r.get("verdict") == "makes_sense" and r.get("description")
            )
            if validated >= n:
                break
    logger.info("synergy finder: %d validated / %d evaluated", validated, len(results))
    return results
