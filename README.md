# civic-graph — Dortmund

A unified, queryable knowledge graph of the city of Dortmund: ingest every
reachable civic data source → resolve entities across them → temporal graph →
reason over it → visualize. "Palantir Gotham, scoped to one city."

See `CLAUDE.md` for the architecture and non-negotiable rules, and `docs/` for
the data-source inventory and temporal design.

## Stack

Python 3.12 · FastAPI · PostgreSQL + PostGIS · Prefect (ingestion) · Claude
(reasoning) · React + TypeScript + MapLibre (frontend).

The knowledge graph is a **property graph on Postgres** (`nodes` + `edges`
tables, typed, with provenance + bitemporal + inference), traversed via SQL — not
Neo4j. Apache AGE can be added later if deep Cypher pathfinding is ever needed.

### Cloud Postgres

Needs PostGIS + pg_trgm + uuid-ossp. Supabase and Neon both support all three.
Set `DATABASE_URL` to point at a managed instance. Behind a transaction pooler
(Supabase :6543, Neon pooled, PgBouncer) also set `DB_STATEMENT_CACHE_SIZE=0`
(asyncpg prepared statements require it). Note: Apache AGE is not available on
Supabase — irrelevant here since the graph is plain relational tables.

## Quickstart

```bash
cp .env.example .env          # set ANTHROPIC_API_KEY for the reasoning layer

# 1. Bring up Postgres+PostGIS (migrations auto-run via initdb), Prefect, API.
docker compose up -d db prefect api
#    API → http://localhost:8000/docs   ·   Prefect → http://localhost:4200

# 2. Run a connector to load some data (geographic spine first).
python -m ingestion.flows           # deploys all flows on their cadences
#    or trigger one ad hoc:
python -c "import asyncio; from ingestion.flows import run_geo_spine; asyncio.run(run_geo_spine())"

# 3. Frontend.
cd frontend && npm install && npm run dev    # http://localhost:5173
```

## Data layers (connectors)

Geography spine · OSM POIs · council meeting dates, **minutes + Beschlüsse**,
**election results (city + per-Stimmbezirk)** · weather · air quality · police
press · tenders · GTFS static + realtime · construction sites · civic POIs ·
demographics · **Autobahn live traffic**. One folder per source under
`connectors/` (copy `_template/`). Full inventory + tiers in
`docs/data-sources.md`.

## API (FastAPI, :8000)

- `GET /nodes`, `/nodes/{id}`, `/nodes/{id}/edges` — graph access
  (`?as_of=<ISO-8601>` on `/nodes`, `/nodes/{id}/edges`, `/geo/nodes` reconstructs
  the graph at a past instant; `/nodes/{id}/history` returns an entity's versions)
- `GET /geo/areas`, `/geo/nodes`, `/geo/pois-in-area/{id}` — map layers (GeoJSON)
- `GET /search?q=` — fuzzy label search (pg_trgm)
- `POST /subgraph` — nodes + edges among a given node set (graph viz)
- `POST /insights` — Claude reasoning over a hand-picked subgraph (ad hoc)
- `GET /insights/stored`, `POST /insights/stored/{id}/status` — insights the
  reasoning **scanner** found automatically (confirm / dismiss)
- `GET /resolution/candidates`, `POST /resolution/candidates/{id}/resolve` —
  human-in-the-loop entity-resolution review
- `GET /status/ingestion` — per-connector run health

## Tests

```bash
pip install -e ".[dev]"
pytest tests/connectors        # frozen raw→normalized per connector
pytest tests/resolution        # precision/recall on a labeled sample
```

## Layout

```
connectors/   one folder per source (fetch / normalize / emit_entities / emit_edges)
ontology/     node + edge type definitions (bitemporal + provenance)
ingestion/    Prefect flows + bitemporal writer
resolution/   cross-source entity matching + text→geo linking
reasoning/    candidate-subgraph scanner + Claude prompt templates
api/          FastAPI query + reasoning + review endpoints
frontend/     React + MapLibre visualization
db/           PostGIS schema (migrations/) + async pool
docs/         data-source inventory · temporal design
```
