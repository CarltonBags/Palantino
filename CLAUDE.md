# CLAUDE.md — civic-graph

Context file for Claude Code. Read this first, then `docs/` as needed. Keep this
file lean: it's a map, not the territory. Detail lives in the linked docs.

## What this project is

A unified, queryable **knowledge graph of the city of Dortmund**. We ingest every
reachable civic data source — council decisions, transit, traffic, roads, events,
businesses, sports, weather, air quality, police/news, demographics, budgets,
tenders — resolve entities across them, and let a model reason over the connected
graph to surface **inefficiencies** (e.g. a road repaved the same month a council
approved a new bus route through it) and **synergies** (e.g. a tender award that
links a council resolution to a local company).

This is a "Palantir Gotham, scoped to one city" pattern: ingest → resolve
entities → temporal graph → reason → visualize.

## Where things are

- `docs/data-sources.md` — the full source inventory: every evaluated source, its
  access method, auth, a tier (1/2/3/Avoid), and a verdict. **Consult this before
  building any connector.** Also contains the German scraping/legal rules.
- `docs/ingestion-and-temporal-design.md` — ingestion cadence per source and the
  **bitemporal model**. Read before writing ingestion or storage logic.
- `docs/data-sources.xlsx` — the same inventory as a spreadsheet (human reference).

## Non-negotiable rules

1. **Provenance on every node and edge.** Nothing enters the graph without
   `source` + `source_url` + `observed_at`. No edge without a traceable origin —
   especially edges about politicians (a `VOTED_FOR` edge must point at the actual
   vote record).
2. **Bitemporal, append-only.** Every fact carries `valid_from` / `valid_to` (true
   in the world) AND `observed_at` (when we recorded it). Never overwrite or
   delete; when something changes, close the old version and open a new one. This
   is what lets the graph reason about *change over time* — see the design doc.
   It's the single most important architectural decision in the project.
3. **Facts vs. inferences are separate.** Source facts have `inferred=false`.
   Anything the reasoning layer concludes is `inferred=true` with a confidence
   score and a reasoning trace — never merged in as if it were ground truth.
4. **Public officials, factual only.** Insights about a real named official must
   read as sourced observations ("Council member X voted Y on date Z, source:
   [link]"), never as generated characterizations of motive or character. Let the
   connected facts speak; a human draws conclusions. This is an accuracy/legal
   guardrail, not just etiquette.
5. **Legal & polite ingestion.** Before any connector that scrapes: check
   `robots.txt` AND the site's terms; never cross a login/CAPTCHA/paywall;
   rate-limit and identify the bot honestly. Minimize personal data (GDPR is the
   real constraint, not copyright — store roles/facts, not identifiable private
   individuals). Honor each source's license. Full rules in `docs/data-sources.md`.

## Architecture

```
[Sources] → [Connectors] → [Staging (Postgres/PostGIS)] → [Entity Resolution]
   → [Knowledge Graph (temporal)] → [Reasoning Layer] → [API] → [Visualization]
```

**Geography is the spine.** Almost everything has a location. Build the spatial
hierarchy early (Stadtbezirk → statistischer Bezirk → address, in PostGIS) so
every node snaps to a consistent place and district.

## Tech stack (defaults — change if you have reason)

Python 3.12 + FastAPI · PostgreSQL + PostGIS (staging + geo) · a graph store
(Neo4j, or Apache AGE on Postgres to avoid a second DB for v1) · Prefect for
scheduled ingestion · React + TypeScript + a map lib (Mapbox/deck.gl) + a
graph-viz lib for the frontend · the Claude API for the reasoning layer (query
subgraphs, never the whole graph).

## Repo layout

```
civic-graph/
├── CLAUDE.md
├── docs/
├── connectors/        # one folder per source; copy _template/ to add one
│   └── _template/
├── ontology/          # node + edge type definitions; temporal/versioning lives here
├── resolution/        # cross-source entity matching (the other hard problem)
├── reasoning/         # subgraph queries + LLM prompt templates for insights
├── api/
├── frontend/
└── tests/
```

## Connector convention

Every connector implements the same interface so the rest of the system doesn't
care what it's ingesting:
- `fetch()` — pull raw data (API / download / polite scrape per the source's tier).
- `normalize()` — map to the common intermediate schema.
- `emit_entities()` / `emit_edges()` — yield typed nodes/edges, each carrying the
  provenance + bitemporal fields from rule 1 & 2.

Classify each source as **snapshot** (poll + append timestamped observations),
**event-stream** (fetch new-since-last-seen), or **reference** (slow full refresh +
diff). Cadence per source is in the design doc. One connector = one PR.

## The two hard problems (don't under-invest here)

- **Temporal modeling** — covered above and in the design doc. Most civic APIs only
  give you "now"; the append discipline is what turns a stream of "nows" into
  history.
- **Entity resolution** — deciding that `Klinikum Dortmund` (OSM) =
  `Klinikum Dortmund gGmbH` (tender data) = the entity named in an OParl resolution.
  This cross-source linking is the actual core of the system. `resolution/` needs a
  real strategy (deterministic keys where they exist — geo, register IDs — plus
  fuzzy matching with confidence scores and human review for low-confidence merges),
  not ad-hoc string matching. Test it with precision/recall on a labeled sample.

## Suggested first steps

1. Stand up Postgres+PostGIS and load the **geographic spine** (district boundaries
   + addresses from the Dortmund Open Data Portal).
2. Build the **OParl council connector** — highest-value structured source
   (sittings, resolutions, committees, members as JSON). First task: confirm
   Dortmund's live OParl endpoint URL from its RIS vendor.
3. Build the **Overpass business/POI connector** (the storefront layer).
Each of these is a Tier-1 open source — see `docs/data-sources.md`.

## Conventions

Type hints everywhere; `ruff` + `black`. Tests: every connector has a frozen
raw-input → expected-normalized-output test; resolution has precision/recall tests.
Don't bundle ontology changes with connector changes in one PR. Start with 2–3
sources working end-to-end before expanding — resist generalizing the ontology for
hypothetical other cities before it works for Dortmund.
