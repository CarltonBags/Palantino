-- Enable extensions
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pg_trgm;  -- fuzzy text search for entity resolution

-- ──────────────────────────────────────────────
-- NODES
-- Every civic entity: GeoArea, Organization, Person, Resolution,
-- POI, Event, Road, Tender, WeatherObs, AirQualityObs, etc.
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nodes (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    node_type       TEXT NOT NULL,          -- e.g. 'GeoArea', 'Organization', 'Resolution'
    label           TEXT NOT NULL,          -- human display name
    properties      JSONB NOT NULL DEFAULT '{}',

    -- provenance (rule 1)
    source          TEXT NOT NULL,
    source_id       TEXT,                   -- stable ID from the source system
    source_url      TEXT,

    -- bitemporal (rule 2)
    observed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    valid_from      TIMESTAMPTZ,
    valid_to        TIMESTAMPTZ,            -- NULL = still current

    -- inference (rule 3)
    inferred        BOOLEAN NOT NULL DEFAULT FALSE,
    confidence      REAL,
    reasoning_trace TEXT,

    -- spatial (nullable — not all nodes have a location)
    geom            GEOMETRY(Geometry, 4326),

    -- dedup key
    UNIQUE (source, source_id)
);

CREATE INDEX IF NOT EXISTS nodes_type_idx        ON nodes (node_type);
CREATE INDEX IF NOT EXISTS nodes_source_idx      ON nodes (source, source_id);
CREATE INDEX IF NOT EXISTS nodes_valid_range_idx ON nodes (valid_from, valid_to);
CREATE INDEX IF NOT EXISTS nodes_observed_idx    ON nodes (observed_at);
CREATE INDEX IF NOT EXISTS nodes_geom_idx        ON nodes USING GIST (geom);
CREATE INDEX IF NOT EXISTS nodes_props_idx       ON nodes USING GIN (properties);
CREATE INDEX IF NOT EXISTS nodes_label_trgm_idx  ON nodes USING GIN (label gin_trgm_ops);


-- ──────────────────────────────────────────────
-- EDGES
-- Typed relationships between nodes.
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS edges (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    edge_type       TEXT NOT NULL,          -- e.g. 'VOTED_ON', 'LOCATED_IN', 'AWARDED_TO'
    from_node_id    UUID NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    to_node_id      UUID NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    properties      JSONB NOT NULL DEFAULT '{}',

    -- provenance (rule 1)
    source          TEXT NOT NULL,
    source_id       TEXT,
    source_url      TEXT,

    -- bitemporal (rule 2)
    observed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    valid_from      TIMESTAMPTZ,
    valid_to        TIMESTAMPTZ,

    -- inference (rule 3)
    inferred        BOOLEAN NOT NULL DEFAULT FALSE,
    confidence      REAL,
    reasoning_trace TEXT
);

CREATE INDEX IF NOT EXISTS edges_type_idx        ON edges (edge_type);
CREATE INDEX IF NOT EXISTS edges_from_idx        ON edges (from_node_id);
CREATE INDEX IF NOT EXISTS edges_to_idx          ON edges (to_node_id);
CREATE INDEX IF NOT EXISTS edges_valid_range_idx ON edges (valid_from, valid_to);
CREATE INDEX IF NOT EXISTS edges_source_idx      ON edges (source, source_id);
CREATE INDEX IF NOT EXISTS edges_props_idx       ON edges USING GIN (properties);


-- ──────────────────────────────────────────────
-- INGESTION LOG
-- One row per connector run — idempotency + cadence tracking.
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ingestion_runs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    connector       TEXT NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,
    status          TEXT NOT NULL DEFAULT 'running',  -- running | ok | error
    nodes_written   INT NOT NULL DEFAULT 0,
    edges_written   INT NOT NULL DEFAULT 0,
    error_message   TEXT,
    checkpoint      JSONB                             -- last-seen cursor / page for incremental
);

CREATE INDEX IF NOT EXISTS ingestion_runs_connector_idx ON ingestion_runs (connector, started_at DESC);


-- ──────────────────────────────────────────────
-- ENTITY RESOLUTION CANDIDATES
-- Low-confidence cross-source merge candidates awaiting review.
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS resolution_candidates (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    node_a_id       UUID NOT NULL REFERENCES nodes(id),
    node_b_id       UUID NOT NULL REFERENCES nodes(id),
    method          TEXT NOT NULL,      -- 'geo', 'name_fuzzy', 'register_id', etc.
    confidence      REAL NOT NULL,
    resolved        BOOLEAN,            -- NULL=pending, TRUE=merged, FALSE=rejected
    resolved_at     TIMESTAMPTZ,
    resolved_by     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS resolution_pending_idx
    ON resolution_candidates (resolved) WHERE resolved IS NULL;
