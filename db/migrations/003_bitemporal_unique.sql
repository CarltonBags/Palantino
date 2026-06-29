-- ──────────────────────────────────────────────
-- Bitemporal uniqueness fix.
-- The original UNIQUE (source, source_id) blocks append-only versioning: when a
-- fact changes we close the old row (set valid_to) and insert a new one with the
-- SAME (source, source_id) — which the table-level constraint rejects, because it
-- ignores valid_to. Replace it with a PARTIAL unique index that only constrains
-- the currently-valid version (valid_to IS NULL). Many closed versions + exactly
-- one open version per (source, source_id) is now allowed.
-- ──────────────────────────────────────────────

ALTER TABLE nodes DROP CONSTRAINT IF EXISTS nodes_source_source_id_key;

CREATE UNIQUE INDEX IF NOT EXISTS nodes_source_current_idx
    ON nodes (source, source_id)
    WHERE valid_to IS NULL AND source_id IS NOT NULL;
