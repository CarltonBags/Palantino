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
from reasoning.prompts import SYNERGY_RESEARCH_PROMPT, SYNERGY_RESEARCH_SYSTEM

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


async def _evaluate_pair(client: httpx.AsyncClient, a: dict, b: dict, note: str) -> dict[str, Any]:
    # own pool connection so pairs can be evaluated concurrently
    async with get_conn() as conn:
        ctx_a, url_a = await _gather_partner_context(conn, a)
        ctx_b, url_b = await _gather_partner_context(conn, b)
    site_a = await _fetch_website(url_a, client)
    site_b = await _fetch_website(url_b, client)
    v = await _validate(ctx_a, site_a, ctx_b, site_b, note)
    if v.get("verdict") not in ("makes_sense", "reject"):
        v["verdict"] = "reject"
    v["partners"] = [a["label"], b["label"]]
    v["evidence_node_ids"] = [str(a["id"]), str(b["id"])]
    v["researched_websites"] = [u for u in (url_a, url_b) if u]
    return v


async def _global_pairs(pool: int = 40) -> list[tuple[dict, dict, str]]:
    from reasoning.scanner import (
        complementary_candidates,
        dedup_candidates,
        structural_synergy_candidates,
    )

    # Favour complementary (need↔offer = real audience/occasion fit) over pure
    # proximity, which produces near-but-incompatible pairs.
    cands = dedup_candidates(
        await complementary_candidates(limit=pool)
        + await structural_synergy_candidates(limit=max(pool // 2, 8))
    )
    pairs = []
    for c in cands:
        p = [nd for nd in c.nodes if nd["node_type"] != "GeoArea"][:2]
        if len(p) >= 2:
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
    if shuffle:
        random.shuffle(pairs)  # variety across un-scoped calls

    results: list[dict[str, Any]] = []
    validated = 0
    batch = 6  # evaluate pairs concurrently, stop once n validated
    async with httpx.AsyncClient(headers={"User-Agent": settings.bot_user_agent}) as client:
        for i in range(0, len(pairs), batch):
            chunk = pairs[i : i + batch]
            evaluated = await asyncio.gather(
                *(_evaluate_pair(client, a, b, note) for a, b, note in chunk)
            )
            results.extend(evaluated)
            validated = sum(
                1 for r in results if r.get("verdict") == "makes_sense" and r.get("description")
            )
            if validated >= n:
                break
    logger.info("synergy finder: %d validated / %d evaluated", validated, len(results))
    return results
