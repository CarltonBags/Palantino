-- ──────────────────────────────────────────────
-- Semantic layer: pgvector embeddings for nodes.
-- A separate table (not a column on nodes) keeps derived vectors out of the
-- append-only fact store: re-embedding never touches a fact row, and an
-- embedding is tied to exactly one node version (node_id). text_hash lets the
-- backfill skip nodes whose embed-text is unchanged.
-- Dimension 1536 = OpenAI text-embedding-3-large shortened via the `dimensions`
-- param, kept <= pgvector's 2000-dim HNSW limit. Must match
-- settings.embedding_dimensions.
-- ──────────────────────────────────────────────

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS node_embeddings (
    node_id     uuid PRIMARY KEY REFERENCES nodes(id) ON DELETE CASCADE,
    embedding   vector(1536) NOT NULL,
    model       text NOT NULL,
    text_hash   text NOT NULL,
    embedded_at timestamptz NOT NULL DEFAULT now()
);

-- Cosine-distance ANN index for nearest-neighbour retrieval.
CREATE INDEX IF NOT EXISTS node_embeddings_hnsw_idx
    ON node_embeddings USING hnsw (embedding vector_cosine_ops);
