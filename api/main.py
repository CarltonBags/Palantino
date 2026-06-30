"""FastAPI — civic-graph query API."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import date
from typing import Any
from uuid import UUID

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from pydantic import BaseModel

from config import settings
from db.session import close_pool, get_conn
from reasoning.llm import complete
from db.temporal import parse_as_of, validity_clause
from reasoning.prompts import (
    INEFFICIENCY_PROMPT,
    SYNERGY_PROMPT,
    SYSTEM_PROMPT,
    format_subgraph,
)


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    yield
    await close_pool()


app = FastAPI(title="civic-graph", version="0.1.0", lifespan=lifespan)


# ── Nodes ──────────────────────────────────────────────────────────────────────

@app.get("/nodes")
async def list_nodes(
    node_type: str | None = None,
    source: str | None = None,
    as_of: str | None = None,
    limit: int = Query(default=50, le=500),
    offset: int = 0,
) -> list[dict[str, Any]]:
    params: list[Any] = []
    try:
        validity, vparams = validity_clause(parse_as_of(as_of), param_index=1)
    except ValueError:
        raise HTTPException(status_code=400, detail="as_of must be ISO-8601")
    params += vparams
    filters = [validity]

    if node_type:
        params.append(node_type)
        filters.append(f"node_type = ${len(params)}")
    if source:
        params.append(source)
        filters.append(f"source = ${len(params)}")

    where = " AND ".join(filters)
    params += [limit, offset]

    async with get_conn() as conn:
        rows = await conn.fetch(
            f"SELECT id, node_type, label, properties, source, source_url, valid_from, observed_at "
            f"FROM nodes WHERE {where} ORDER BY observed_at DESC "
            f"LIMIT ${len(params) - 1} OFFSET ${len(params)}",
            *params,
        )
    return [dict(r) for r in rows]


@app.get("/nodes/{node_id}")
async def get_node(node_id: UUID) -> dict[str, Any]:
    async with get_conn() as conn:
        row = await conn.fetchrow("SELECT * FROM nodes WHERE id = $1", str(node_id))
    if not row:
        raise HTTPException(status_code=404, detail="Node not found")
    return dict(row)


@app.get("/nodes/{node_id}/history")
async def get_node_history(node_id: UUID) -> list[dict[str, Any]]:
    """
    All versions of an entity over time. Identity is (source, source_id) — the
    bitemporal writer closes the old version and opens a new one on change, so
    this returns the change log for the thing this node refers to.
    """
    async with get_conn() as conn:
        anchor = await conn.fetchrow(
            "SELECT source, source_id FROM nodes WHERE id = $1", str(node_id)
        )
        if not anchor:
            raise HTTPException(status_code=404, detail="Node not found")
        if anchor["source_id"] is None:
            # No stable identity to group by — return just this row.
            rows = await conn.fetch("SELECT * FROM nodes WHERE id = $1", str(node_id))
        else:
            rows = await conn.fetch(
                "SELECT * FROM nodes WHERE source = $1 AND source_id = $2 "
                "ORDER BY valid_from NULLS FIRST, observed_at",
                anchor["source"], anchor["source_id"],
            )
    return [dict(r) for r in rows]


@app.get("/nodes/{node_id}/edges")
async def get_node_edges(
    node_id: UUID,
    direction: str = Query(default="both", pattern="^(in|out|both)$"),
    as_of: str | None = None,
) -> list[dict[str, Any]]:
    node_str = str(node_id)
    try:
        validity, vparams = validity_clause(parse_as_of(as_of), param_index=2)
    except ValueError:
        raise HTTPException(status_code=400, detail="as_of must be ISO-8601")
    if direction == "out":
        endpoint = "from_node_id = $1"
    elif direction == "in":
        endpoint = "to_node_id = $1"
    else:
        endpoint = "(from_node_id = $1 OR to_node_id = $1)"
    async with get_conn() as conn:
        rows = await conn.fetch(
            f"SELECT * FROM edges WHERE {endpoint} AND {validity}",
            node_str, *vparams,
        )
    return [dict(r) for r in rows]


# ── Geo ───────────────────────────────────────────────────────────────────────

@app.get("/geo/areas")
async def list_geo_areas(area_type: str | None = None) -> list[dict[str, Any]]:
    async with get_conn() as conn:
        if area_type:
            rows = await conn.fetch(
                "SELECT id, label, properties, ST_AsGeoJSON(geom)::jsonb AS geometry "
                "FROM nodes WHERE node_type = 'GeoArea' "
                "AND properties->>'area_type' = $1 AND valid_to IS NULL",
                area_type,
            )
        else:
            rows = await conn.fetch(
                "SELECT id, label, properties, ST_AsGeoJSON(geom)::jsonb AS geometry "
                "FROM nodes WHERE node_type = 'GeoArea' AND valid_to IS NULL"
            )
    return [dict(r) for r in rows]


@app.get("/geo/nodes")
async def geo_nodes(
    node_type: str | None = None,
    source: str | None = None,
    as_of: str | None = None,
    limit: int = Query(default=2000, le=10000),
) -> dict[str, Any]:
    """Map layer: nodes that carry a point/geometry, as a GeoJSON FeatureCollection."""
    params: list[Any] = []
    try:
        validity, vparams = validity_clause(parse_as_of(as_of), param_index=1)
    except ValueError:
        raise HTTPException(status_code=400, detail="as_of must be ISO-8601")
    params += vparams
    filters = [validity, "geom IS NOT NULL"]
    if node_type:
        params.append(node_type)
        filters.append(f"node_type = ${len(params)}")
    if source:
        params.append(source)
        filters.append(f"source = ${len(params)}")
    where = " AND ".join(filters)
    params.append(limit)

    async with get_conn() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, node_type, label, source,
                   ST_AsGeoJSON(geom)::jsonb AS geometry
            FROM nodes WHERE {where}
            ORDER BY observed_at DESC LIMIT ${len(params)}
            """,
            *params,
        )
    features = [
        {
            "type": "Feature",
            "geometry": r["geometry"],
            "properties": {
                "id": str(r["id"]),
                "node_type": r["node_type"],
                "label": r["label"],
                "source": r["source"],
            },
        }
        for r in rows
    ]
    return {"type": "FeatureCollection", "features": features}


@app.get("/geo/pois-in-area/{area_id}")
async def pois_in_area(area_id: UUID) -> list[dict[str, Any]]:
    async with get_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT p.id, p.label, p.properties,
                   ST_AsGeoJSON(p.geom)::jsonb AS geometry
            FROM nodes p
            JOIN edges e ON e.from_node_id = p.id
                AND e.edge_type = 'LOCATED_IN'
                AND e.to_node_id = $1
                AND e.valid_to IS NULL
            WHERE p.node_type = 'POI' AND p.valid_to IS NULL
            """,
            str(area_id),
        )
    return [dict(r) for r in rows]


# ── Search ─────────────────────────────────────────────────────────────────────

@app.get("/search")
async def search_nodes(
    q: str,
    node_type: str | None = None,
    limit: int = Query(default=20, le=100),
) -> list[dict[str, Any]]:
    params: list[Any] = [q, limit]
    type_filter = ""
    if node_type:
        params.insert(1, node_type)
        type_filter = "AND node_type = $2"
        params[-1] = limit  # re-set limit index

    async with get_conn() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, node_type, label, source, source_url,
                   similarity(label, $1) AS score
            FROM nodes
            WHERE label % $1 {type_filter} AND valid_to IS NULL
            ORDER BY score DESC
            LIMIT ${ len(params) }
            """,
            *params,
        )
    return [dict(r) for r in rows]


# ── Reasoning ─────────────────────────────────────────────────────────────────

class InsightRequest(BaseModel):
    node_ids: list[UUID]
    insight_type: str = "inefficiency"  # inefficiency | synergy


@app.post("/insights")
async def get_insights(req: InsightRequest) -> dict[str, Any]:
    key = settings.deepseek_api_key if settings.llm_provider == "deepseek" else settings.anthropic_api_key
    if not key:
        raise HTTPException(
            status_code=503, detail=f"No API key for llm_provider={settings.llm_provider}"
        )

    node_strs = [str(nid) for nid in req.node_ids]
    async with get_conn() as conn:
        nodes = await conn.fetch(
            "SELECT id, node_type, label, properties, source, source_url, valid_from "
            "FROM nodes WHERE id = ANY($1::uuid[]) AND valid_to IS NULL",
            node_strs,
        )
        edges = await conn.fetch(
            "SELECT id, edge_type, from_node_id, to_node_id, properties, source, source_url "
            "FROM edges WHERE (from_node_id = ANY($1::uuid[]) OR to_node_id = ANY($1::uuid[])) "
            "AND valid_to IS NULL AND inferred = FALSE",
            node_strs,
        )

    subgraph = format_subgraph(
        nodes=[dict(n) for n in nodes],
        edges=[dict(e) for e in edges],
    )

    template = INEFFICIENCY_PROMPT if req.insight_type == "inefficiency" else SYNERGY_PROMPT
    prompt = template.format(subgraph_json=subgraph, today=date.today().isoformat())

    raw_text = await complete(SYSTEM_PROMPT, prompt, max_tokens=8000)

    # Tolerate ```json fences (providers vary) before parsing.
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        raise HTTPException(status_code=502, detail="Reasoning layer returned non-JSON")

    return result


# ── Ask-the-city chat (grounded RAG) ────────────────────────────────────────────

class ChatRequest(BaseModel):
    question: str
    k: int = 24
    lens: str | None = None  # force a lens (e.g. the leads window); else auto-detect


@app.post("/chat")
async def chat(req: ChatRequest) -> dict[str, Any]:
    """Answer a question from the most relevant graph facts; log it for history."""
    if not settings.openai_api_key:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY not configured (embeddings)")
    llm_key = settings.deepseek_api_key if settings.llm_provider == "deepseek" else settings.anthropic_api_key
    if not llm_key:
        raise HTTPException(
            status_code=503, detail=f"No API key for llm_provider={settings.llm_provider}"
        )
    from reasoning.llm import active_model
    from reasoning.qa import answer_question

    result = await answer_question(req.question, k=min(max(req.k, 4), 48), lens_override=req.lens)
    intent = result.get("intent") or {}
    async with get_conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO chat_queries (question, answer, lens, intent, citations, model)
            VALUES ($1, $2, $3, $4, $5, $6) RETURNING id
            """,
            req.question, result["answer"], intent.get("lens"),
            intent, result.get("citations", []), active_model(),
        )
    result["id"] = str(row["id"])
    return result


class DiscussRequest(BaseModel):
    node_ids: list[UUID]
    messages: list[dict[str, str]]  # [{role: user|assistant, content}]


@app.post("/chat/discuss")
async def chat_discuss(req: DiscussRequest) -> dict[str, Any]:
    """Deepen a previously-found result via follow-up, grounded in its evidence."""
    if not settings.openai_api_key:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY not configured (embeddings)")
    llm_key = settings.deepseek_api_key if settings.llm_provider == "deepseek" else settings.anthropic_api_key
    if not llm_key:
        raise HTTPException(status_code=503, detail=f"No API key for llm_provider={settings.llm_provider}")
    from reasoning.llm import active_model
    from reasoning.qa import discuss

    result = await discuss([str(n) for n in req.node_ids], req.messages)
    last_user = next((m.get("content", "") for m in reversed(req.messages) if m.get("role") == "user"), "")
    async with get_conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO chat_queries (question, answer, lens, citations, model)
            VALUES ($1, $2, 'discuss', $3, $4) RETURNING id
            """,
            last_user, result["answer"], result.get("citations", []), active_model(),
        )
    result["id"] = str(row["id"])
    return result


class RatingRequest(BaseModel):
    rating: int


@app.post("/chat/{query_id}/rating")
async def rate_chat(query_id: UUID, req: RatingRequest) -> dict[str, Any]:
    if not 1 <= req.rating <= 10:
        raise HTTPException(status_code=400, detail="rating must be 1–10")
    async with get_conn() as conn:
        row = await conn.fetchrow(
            "UPDATE chat_queries SET rating = $2 WHERE id = $1 RETURNING id",
            str(query_id), req.rating,
        )
    if not row:
        raise HTTPException(status_code=404, detail="query not found")
    return {"id": str(query_id), "rating": req.rating}


@app.get("/chat/history")
async def chat_history(
    min_rating: int = 0,
    lens: str | None = None,
    rated_only: bool = False,
    limit: int = Query(default=50, le=200),
) -> list[dict[str, Any]]:
    filters = ["TRUE"]
    params: list[Any] = []
    if min_rating > 0:
        params.append(min_rating)
        filters.append(f"rating >= ${len(params)}")
    elif rated_only:
        filters.append("rating IS NOT NULL")
    if lens:
        params.append(lens)
        filters.append(f"lens = ${len(params)}")
    params.append(limit)
    async with get_conn() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, question, answer, lens, citations, model, rating, created_at
            FROM chat_queries WHERE {' AND '.join(filters)}
            ORDER BY created_at DESC LIMIT ${len(params)}
            """,
            *params,
        )
    return [dict(r) for r in rows]


# ── Event picker + anchored analysis ("add to chat") ────────────────────────────

@app.get("/events/categories")
async def event_categories() -> list[dict[str, Any]]:
    """Categories of upcoming public events, for the picker's pre-filter."""
    async with get_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT properties->>'category' AS category, count(*) AS n
            FROM nodes
            WHERE node_type = 'Event' AND source = 'dortmund_veranstaltungskalender'
              AND valid_to IS NULL AND valid_from >= now()
              AND properties->>'category' IS NOT NULL
            GROUP BY 1 ORDER BY n DESC
            """
        )
    return [dict(r) for r in rows]


@app.get("/events")
async def list_events(
    category: str | None = None,
    q: str | None = None,
    limit: int = Query(default=60, le=200),
) -> list[dict[str, Any]]:
    """Upcoming public events, pre-filtered by category + optional label search."""
    filters = [
        "node_type = 'Event'", "source = 'dortmund_veranstaltungskalender'",
        "valid_to IS NULL", "valid_from >= now()",
    ]
    params: list[Any] = []
    if category:
        params.append(category)
        filters.append(f"properties->>'category' = ${len(params)}")
    if q:
        params.append(f"%{q}%")
        filters.append(f"label ILIKE ${len(params)}")
    params.append(limit)
    async with get_conn() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, label, properties->>'category' AS category,
                   properties->>'venue' AS venue, properties->>'stadtbezirk' AS stadtbezirk,
                   valid_from
            FROM nodes WHERE {' AND '.join(filters)}
            ORDER BY valid_from ASC LIMIT ${len(params)}
            """,
            *params,
        )
    return [dict(r) for r in rows]


class NodeAnalysisRequest(BaseModel):
    node_id: UUID
    lens: str = "synergy"


@app.post("/chat/node")
async def chat_node(req: NodeAnalysisRequest) -> dict[str, Any]:
    """Run a lens analysis anchored on one node (event/business/…); logged like chat."""
    if not settings.openai_api_key:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY not configured (embeddings)")
    llm_key = settings.deepseek_api_key if settings.llm_provider == "deepseek" else settings.anthropic_api_key
    if not llm_key:
        raise HTTPException(status_code=503, detail=f"No API key for llm_provider={settings.llm_provider}")
    from reasoning.llm import active_model
    from reasoning.qa import analyze_node

    result = await analyze_node(str(req.node_id), req.lens)
    intent = result.get("intent") or {}
    question = result.get("question") or f"Analyse rund um {intent.get('anchor', '')}"
    async with get_conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO chat_queries (question, answer, lens, intent, citations, model)
            VALUES ($1, $2, $3, $4, $5, $6) RETURNING id
            """,
            question, result["answer"], intent.get("lens"),
            intent, result.get("citations", []), active_model(),
        )
    result["id"] = str(row["id"])
    result["question"] = question
    return result


# ── Subgraph (for graph visualization) ──────────────────────────────────────────

class SubgraphRequest(BaseModel):
    node_ids: list[UUID]


@app.post("/subgraph")
async def get_subgraph(req: SubgraphRequest) -> dict[str, Any]:
    """
    Given a set of node IDs, return those nodes plus the edges among them.
    Powers the frontend graph view (insight evidence, a node's ego-network).
    """
    ids = [str(nid) for nid in req.node_ids]
    if not ids:
        return {"nodes": [], "edges": []}
    async with get_conn() as conn:
        nodes = await conn.fetch(
            "SELECT id, node_type, label, properties, source, source_url "
            "FROM nodes WHERE id = ANY($1::uuid[]) AND valid_to IS NULL",
            ids,
        )
        edges = await conn.fetch(
            "SELECT id, edge_type, from_node_id, to_node_id, properties, inferred "
            "FROM edges WHERE from_node_id = ANY($1::uuid[]) "
            "AND to_node_id = ANY($1::uuid[]) AND valid_to IS NULL",
            ids,
        )
    return {"nodes": [dict(n) for n in nodes], "edges": [dict(e) for e in edges]}


# ── Stored insights (from the reasoning scanner) ────────────────────────────────

@app.post("/insights/scan")
async def trigger_insight_scan(background: BackgroundTasks) -> dict[str, Any]:
    """Kick off a bounded insight scan in the background (populates /insights/stored)."""
    llm_key = settings.deepseek_api_key if settings.llm_provider == "deepseek" else settings.anthropic_api_key
    if not llm_key:
        raise HTTPException(status_code=503, detail=f"No API key for llm_provider={settings.llm_provider}")
    from reasoning.scanner import scan

    background.add_task(scan, limit=5)
    return {"status": "scan_started"}


@app.get("/insights/stored")
async def list_stored_insights(
    insight_type: str | None = None,
    status: str = Query(default="new", pattern="^(new|confirmed|dismissed|all)$"),
    min_confidence: float = 0.0,
    limit: int = Query(default=100, le=500),
) -> list[dict[str, Any]]:
    """Insights produced by the reasoning scanner (rule 3: inferred, separate)."""
    filters = ["confidence >= $1"]
    params: list[Any] = [min_confidence]
    if status != "all":
        params.append(status)
        filters.append(f"status = ${len(params)}")
    if insight_type:
        params.append(insight_type)
        filters.append(f"insight_type = ${len(params)}")
    where = " AND ".join(filters)
    params.append(limit)

    async with get_conn() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, insight_type, title, description, confidence,
                   evidence_node_ids, reasoning_trace, model, generator,
                   status, created_at
            FROM insights WHERE {where}
            ORDER BY confidence DESC, created_at DESC
            LIMIT ${len(params)}
            """,
            *params,
        )
    return [dict(r) for r in rows]


class InsightStatusRequest(BaseModel):
    status: str  # confirmed | dismissed | new


@app.post("/insights/stored/{insight_id}/status")
async def set_insight_status(insight_id: UUID, req: InsightStatusRequest) -> dict[str, Any]:
    if req.status not in ("confirmed", "dismissed", "new"):
        raise HTTPException(status_code=400, detail="Invalid status")
    async with get_conn() as conn:
        row = await conn.fetchrow(
            "UPDATE insights SET status = $2 WHERE id = $1 RETURNING id",
            str(insight_id), req.status,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Insight not found")
    return {"id": str(insight_id), "status": req.status}


# ── Entity resolution review ────────────────────────────────────────────────────

@app.get("/resolution/candidates")
async def list_resolution_candidates(
    status: str = Query(default="pending", pattern="^(pending|resolved|rejected|all)$"),
    limit: int = Query(default=100, le=500),
) -> list[dict[str, Any]]:
    """Low-confidence cross-source merge candidates awaiting human review (rule 3)."""
    clause = {
        "pending": "rc.resolved IS NULL",
        "resolved": "rc.resolved IS TRUE",
        "rejected": "rc.resolved IS FALSE",
        "all": "TRUE",
    }[status]
    async with get_conn() as conn:
        rows = await conn.fetch(
            f"""
            SELECT rc.id, rc.method, rc.confidence, rc.resolved, rc.created_at,
                   a.id AS a_id, a.label AS a_label, a.node_type AS a_type, a.source AS a_source,
                   b.id AS b_id, b.label AS b_label, b.node_type AS b_type, b.source AS b_source
            FROM resolution_candidates rc
            JOIN nodes a ON a.id = rc.node_a_id
            JOIN nodes b ON b.id = rc.node_b_id
            WHERE {clause}
            ORDER BY rc.confidence DESC
            LIMIT $1
            """,
            limit,
        )
    return [dict(r) for r in rows]


class ResolveRequest(BaseModel):
    merge: bool
    resolved_by: str = "api"


@app.post("/resolution/candidates/{candidate_id}/resolve")
async def resolve_candidate(candidate_id: UUID, req: ResolveRequest) -> dict[str, Any]:
    """Approve (merge → SAME_AS edge) or reject a candidate. Human-in-the-loop."""
    from ingestion.writer import upsert_edge
    from ontology.edges import same_as

    async with get_conn() as conn:
        cand = await conn.fetchrow(
            "SELECT node_a_id, node_b_id, method, confidence, resolved "
            "FROM resolution_candidates WHERE id = $1",
            str(candidate_id),
        )
        if not cand:
            raise HTTPException(status_code=404, detail="Candidate not found")
        if cand["resolved"] is not None:
            raise HTTPException(status_code=409, detail="Candidate already resolved")

    if req.merge:
        edge = same_as(
            node_a_id=UUID(str(cand["node_a_id"])),
            node_b_id=UUID(str(cand["node_b_id"])),
            method=cand["method"],
            source="resolution_review",
            confidence=cand["confidence"],
            reasoning_trace=f"human-approved by {req.resolved_by}",
        )
        await upsert_edge(edge)

    async with get_conn() as conn:
        await conn.execute(
            "UPDATE resolution_candidates "
            "SET resolved = $2, resolved_at = NOW(), resolved_by = $3 WHERE id = $1",
            str(candidate_id), req.merge, req.resolved_by,
        )
    return {"id": str(candidate_id), "merged": req.merge}


# ── Ingestion status ───────────────────────────────────────────────────────────

@app.get("/status/sources")
async def source_catalog() -> list[dict[str, Any]]:
    """
    Full source catalog from the connector registry, left-joined to each
    connector's latest run — so sources that have never run still show up.
    """
    from connectors.registry import registry_catalog

    async with get_conn() as conn:
        runs = await conn.fetch(
            """
            SELECT DISTINCT ON (connector)
                connector, started_at, finished_at, status, nodes_written, edges_written
            FROM ingestion_runs
            ORDER BY connector, started_at DESC
            """
        )
    by_connector = {r["connector"]: dict(r) for r in runs}
    catalog = []
    for spec in registry_catalog():
        run = by_connector.get(spec["name"])
        catalog.append({**spec, "last_run": run})
    return catalog


@app.get("/status/ingestion")
async def ingestion_status() -> list[dict[str, Any]]:
    async with get_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (connector)
                connector, started_at, finished_at, status,
                nodes_written, edges_written, error_message
            FROM ingestion_runs
            ORDER BY connector, started_at DESC
            """
        )
    return [dict(r) for r in rows]
