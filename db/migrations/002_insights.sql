-- ──────────────────────────────────────────────
-- INSIGHTS
-- Output of the reasoning layer (rule 3): inferred findings, kept strictly
-- separate from source facts. Each insight references the subgraph nodes it was
-- derived from, carries a confidence + reasoning trace + the model that produced
-- it, and a deterministic candidate_key so re-scans don't duplicate.
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS insights (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    insight_type      TEXT NOT NULL,            -- inefficiency | synergy
    title             TEXT NOT NULL,
    description       TEXT NOT NULL,
    confidence        REAL NOT NULL,
    evidence_node_ids UUID[] NOT NULL DEFAULT '{}',  -- subgraph the model saw
    evidence          JSONB NOT NULL DEFAULT '[]',   -- raw node/edge ids the model cited
    reasoning_trace   TEXT,
    model             TEXT NOT NULL,
    generator         TEXT NOT NULL,            -- spatial_temporal | ego_network
    candidate_key     TEXT NOT NULL,            -- hash(sorted node_ids + type) for dedup
    status            TEXT NOT NULL DEFAULT 'new',   -- new | confirmed | dismissed
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (candidate_key)
);

CREATE INDEX IF NOT EXISTS insights_type_idx   ON insights (insight_type);
CREATE INDEX IF NOT EXISTS insights_status_idx ON insights (status);
CREATE INDEX IF NOT EXISTS insights_conf_idx   ON insights (confidence DESC);
CREATE INDEX IF NOT EXISTS insights_nodes_idx  ON insights USING GIN (evidence_node_ids);
