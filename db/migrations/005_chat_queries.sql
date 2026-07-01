-- ──────────────────────────────────────────────
-- Chat query log. Every ask-the-city query is stored with its answer, lens,
-- intent and citations, plus an optional 1–10 rating, so past results can be
-- browsed, filtered and re-rated.
-- ──────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS chat_queries (
    id         uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    question   text NOT NULL,
    answer     text NOT NULL,
    lens       text,
    intent     jsonb,
    citations  jsonb,
    model      text,
    rating     int CHECK (rating BETWEEN 1 AND 10),  -- NULL = unrated
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS chat_queries_created_idx ON chat_queries (created_at DESC);
CREATE INDEX IF NOT EXISTS chat_queries_rating_idx ON chat_queries (rating);
CREATE INDEX IF NOT EXISTS chat_queries_lens_idx ON chat_queries (lens);
