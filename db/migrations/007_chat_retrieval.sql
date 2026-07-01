-- ──────────────────────────────────────────────
-- Record which retrieval mode produced a chat answer (semantic vs structural),
-- so Verlauf can badge it. Defaults to 'semantic' for existing rows.
-- ──────────────────────────────────────────────

ALTER TABLE chat_queries ADD COLUMN IF NOT EXISTS retrieval text NOT NULL DEFAULT 'semantic';
