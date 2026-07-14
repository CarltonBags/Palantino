-- ──────────────────────────────────────────────
-- Akquise pipeline memory. One row per actor the user has acted on.
-- Lead generation (lens=leads) suppresses every node with ANY status here —
-- fresh lists contain only never-touched actors; the status list itself is the
-- working pipeline view.
-- ──────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS lead_feedback (
    node_id    uuid PRIMARY KEY,
    status     text NOT NULL CHECK (status IN ('contacted', 'not_interested', 'customer', 'ignore')),
    note       text,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS lead_feedback_status_idx ON lead_feedback (status);
