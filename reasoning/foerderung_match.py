"""
Förder-Radar: match funding programs to a specific actor.

Funnel (same shape as the synergy pipeline):
  1. actor → plausible target groups (deterministic, from source/type/tags)
  2. candidate programs: target-group overlap + embedding closeness (SQL)
  3. ONE comparative LLM call judges eligibility per program, strict, with
     the concrete condition the actor must verify themselves
  4. passt/vielleicht → ELIGIBLE_FOR edge (inferred=True, confidence, trace)

Programs come from the NRW.BANK connector (Land + passed-through Bund).
"""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from db.session import get_conn
from ingestion.writer import upsert_edge
from ontology.edges import eligible_for
from reasoning.llm import complete
from reasoning.prompts import FOERDERUNG_MATCH_PROMPT, FOERDERUNG_MATCH_SYSTEM

logger = logging.getLogger(__name__)

_NODE_COLS = "id, node_type, label, properties, source, source_url"


def actor_target_groups(node: dict[str, Any]) -> list[str]:
    """Deterministic mapping: what program target groups could this actor be?"""
    props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
    src = node.get("source") or ""
    groups: set[str] = set()
    if node["node_type"] == "POI":
        groups |= {"unternehmen", "kmu"}
    if src in ("offeneregister", "vergabe_nrw_metropole_ruhr"):
        groups |= {"unternehmen", "kmu"}
    if src in ("news_extraction", "event_venue"):
        org_type = (props.get("org_type") or "").lower()
        role = (props.get("role") or "").lower()
        blob = f"{org_type} {role} {node.get('label', '')}".lower()
        if any(k in blob for k in ("verein", "initiative", "stiftung", "gemeinnützig", "e.v")):
            groups |= {"verein_gemeinnuetzig"}
        if any(k in blob for k in ("kultur", "musik", "theater", "kunst", "museum")):
            groups |= {"kultur"}
        if any(k in blob for k in ("sozial", "beratung", "wohlfahrt", "jugend", "senioren")):
            groups |= {"sozial"}
        if any(k in blob for k in ("gmbh", "unternehmen", "betrieb", "firma")):
            groups |= {"unternehmen", "kmu"}
        if not groups:
            groups |= {"verein_gemeinnuetzig", "unternehmen"}
    return sorted(groups)


def _parse_verdicts(raw: str) -> list[dict[str, Any]]:
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


async def match_programs(node_id: str, k: int = 12) -> list[dict[str, Any]]:
    """Judge the k best-fitting programs for one actor; persist ELIGIBLE_FOR
    edges for passt/vielleicht; return all verdicts (UI shows both)."""
    from reasoning.synergy_finder import _gather_partner_context

    async with get_conn() as conn:
        actor = await conn.fetchrow(
            f"SELECT {_NODE_COLS} FROM nodes WHERE id = $1::uuid AND valid_to IS NULL",
            node_id,
        )
        if not actor:
            return []
        actor = dict(actor)
        groups = actor_target_groups(actor)
        if not groups:
            return []
        actor_ctx, _ = await _gather_partner_context(conn, actor)

        async with conn.transaction():
            await conn.execute("SET LOCAL hnsw.iterative_scan = 'relaxed_order'")
            programs = await conn.fetch(
                f"""
                SELECT {_NODE_COLS},
                       (e.embedding <=> (SELECT embedding FROM node_embeddings
                                         WHERE node_id = $1::uuid)) AS dist
                FROM nodes n
                LEFT JOIN node_embeddings e ON e.node_id = n.id
                WHERE n.node_type = 'FundingProgram' AND n.valid_to IS NULL
                  AND n.properties->'target_groups' ?| $2::text[]
                  AND (coalesce(n.properties->>'open_ended', 'true') = 'true'
                       OR coalesce(n.properties->>'deadline', '9999-12-31') >= $3)
                ORDER BY dist ASC NULLS LAST
                LIMIT $4
                """,
                node_id, groups, date.today().isoformat(), k,
            )
    if not programs:
        return []

    blocks = []
    for i, p in enumerate(programs):
        props = p["properties"] if isinstance(p["properties"], dict) else {}
        blocks.append(
            f"[{i}] {p['label']}\n"
            f"    Ebene: {props.get('level')} · Art: {props.get('funding_type')}"
            f" · Zielgruppen: {', '.join(props.get('target_groups') or [])}"
            f" · max: {props.get('max_amount_eur') or '—'} €"
            f" · Frist: {props.get('deadline') or 'laufend'}\n"
            f"    {props.get('summary') or ''}"
        )
    raw = await complete(
        FOERDERUNG_MATCH_SYSTEM,
        FOERDERUNG_MATCH_PROMPT.format(
            today=date.today().isoformat(), actor_ctx=actor_ctx,
            programs="\n".join(blocks),
        ),
        max_tokens=20000,
    )
    verdicts = _parse_verdicts(raw)

    results: list[dict[str, Any]] = []
    for v in verdicts:
        idx = v.get("program_index")
        if not isinstance(idx, int) or not (0 <= idx < len(programs)):
            continue
        p = programs[idx]
        verdict = v.get("verdict")
        result = {
            "program_id": str(p["id"]), "program": p["label"],
            "source_url": p["source_url"], "verdict": verdict,
            "confidence": v.get("confidence"),
            "begruendung": v.get("begruendung"),
            "zu_pruefen": v.get("zu_pruefen"),
            "properties": p["properties"],
        }
        results.append(result)
        if verdict in ("passt", "vielleicht"):
            await upsert_edge(
                eligible_for(
                    actor["id"], p["id"],
                    source="foerderung_match",
                    source_id=f"{actor['id']}->{p['id']}",
                    confidence=float(v.get("confidence") or 0.5),
                    reasoning_trace=(v.get("begruendung") or "")[:500],
                )
            )
    order = {"passt": 0, "vielleicht": 1, "passt_nicht": 2}
    results.sort(key=lambda r: (order.get(r["verdict"], 3), -(r["confidence"] or 0)))
    logger.info("foerderung match for %s: %d programs judged", actor["label"], len(results))
    return results
