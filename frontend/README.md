# civic-graph frontend

React + TypeScript + Vite + MapLibre GL. Visualization layer for the Dortmund
civic knowledge graph.

## Run

```bash
npm install
npm run dev      # http://localhost:5173  (proxies /api → http://localhost:8000)
```

Needs the API running (`docker compose up api db` from the repo root, or
`uvicorn api.main:app --reload`).

## Build / typecheck

```bash
npm run build      # tsc --noEmit + vite build → dist/
npm run typecheck
```

## What it does

- **Map** (MapLibre, free demo basemap — no token): statistischer-Bezirk
  boundaries + geo-located nodes, colour-coded by type. Toggle layers in the
  sidebar. Click a point to open it.
- **Search** the graph by label (pg_trgm) → open any node.
- **Node detail**: properties, provenance link, connected edges (walk the graph),
  and a "reason over subgraph" action that calls `/insights`
  (inefficiency / synergy) on the node's ego-network.
- **Insights tab**: insights the reasoning scanner found (inefficiency /
  synergy), filterable; Confirm / Dismiss; click evidence nodes to open them.
- **Resolution review**: pending cross-source merge candidates → Merge / Reject
  (writes a `SAME_AS` edge on merge).
- **Ingestion status bar**: per-connector last-run health, auto-refreshing.

## Layout

```
src/
├── api.ts            typed client for the FastAPI backend
├── nodeTypes.ts      geo node types + colours + map centre
├── App.tsx           state + layer fetching/merging
└── components/
    ├── MapView.tsx        MapLibre map (areas + points)
    ├── Sidebar.tsx        Explore/Insights tabs · search · layers · detail · review
    ├── NodeDetail.tsx     properties · edges · reasoning
    ├── InsightsPanel.tsx  scanner insights · confirm/dismiss · evidence
    ├── ResolutionPanel.tsx merge/reject candidates
    └── IngestionStatus.tsx per-connector health
```
