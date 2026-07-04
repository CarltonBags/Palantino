-- ──────────────────────────────────────────────
-- All-pairs synergy candidate frontier.
-- Every plausible actor pair, scored by cheap signals computed in-database
-- (semantic similarity, IDF-weighted need↔offer complementarity, physical
-- proximity, shared graph neighbours). The LLM only ever validates the top of
-- this ranked queue; verdicts land in synergy_feedback and flip the status
-- here, so validation coverage accumulates run over run instead of restarting
-- from a handful of per-query anchors.
-- ──────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS synergy_candidates (
    pair_key    text PRIMARY KEY,          -- sorted(node_a, node_b) joined by '|'
    node_a      uuid NOT NULL,
    node_b      uuid NOT NULL,
    sem_sim     real,                      -- cosine similarity of embeddings
    comp_score  real,                      -- Σ idf-weight over matched need↔offer tags
    dist_m      real,                      -- metres apart (both geolocated, unconnected)
    shared_n    integer,                   -- shared specific (non-hub) neighbours
    score       real NOT NULL DEFAULT 0,   -- combined rank, recomputed each refresh
    status      text NOT NULL DEFAULT 'new'
        CHECK (status IN ('new', 'validated', 'suppressed')),
    computed_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS synergy_candidates_frontier_idx
    ON synergy_candidates (status, score DESC);
CREATE INDEX IF NOT EXISTS synergy_candidates_a_idx ON synergy_candidates (node_a);
CREATE INDEX IF NOT EXISTS synergy_candidates_b_idx ON synergy_candidates (node_b);

-- Working set for the refresh: the current partnerable actors, one row per
-- entity (event series collapsed to one representative). Unlogged: derived
-- data, rebuilt on every refresh. Deliberately does NOT copy embeddings —
-- duplicating 76k × 1536-dim vectors once cost 210 MB and tipped the database
-- over its disk quota into read-only mode; the semantic signal joins
-- node_embeddings directly instead (pgvector iterative scan handles the
-- actor-set filter).
CREATE UNLOGGED TABLE IF NOT EXISTS synergy_actor_set (
    node_id   uuid PRIMARY KEY,
    node_type text NOT NULL,
    label     text NOT NULL,
    geom      geometry
);

CREATE INDEX IF NOT EXISTS synergy_actor_set_geom_idx
    ON synergy_actor_set USING gist (geom);
