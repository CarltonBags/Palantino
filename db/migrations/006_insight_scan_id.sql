-- ──────────────────────────────────────────────
-- Group insights by the scan run that produced them, so the UI can separate a
-- fresh "Neue suchen" batch from older ones. One scan_id per scan() run.
-- ──────────────────────────────────────────────

ALTER TABLE insights ADD COLUMN IF NOT EXISTS scan_id uuid;
CREATE INDEX IF NOT EXISTS insights_scan_idx ON insights (scan_id);
