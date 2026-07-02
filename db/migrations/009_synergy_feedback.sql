-- ──────────────────────────────────────────────
-- Feedback loop for synergy discovery.
-- One row per unordered actor pair. Verdicts come from the deep finder's own
-- judgement (source='llm': makes_sense|reject) AND from the user (source='user':
-- confirmed|dismissed). Candidate generation consults it: SUPPRESS negatively-judged
-- pairs (so the same rejected/dismissed pairs stop re-appearing) and know which are
-- confirmed. User verdicts outrank the LLM's.
-- ──────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS synergy_feedback (
    pair_key   text PRIMARY KEY,   -- sorted(node_a, node_b) joined by '|'
    node_a     uuid NOT NULL,
    node_b     uuid NOT NULL,
    verdict    text NOT NULL CHECK (verdict IN ('makes_sense','reject','confirmed','dismissed')),
    reason     text,
    source     text NOT NULL CHECK (source IN ('llm','user')),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS synergy_feedback_verdict_idx ON synergy_feedback (verdict);
