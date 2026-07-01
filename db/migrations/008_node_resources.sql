-- ──────────────────────────────────────────────
-- Resource/capability layer for COMPLEMENTARY synergies (need ↔ offer).
-- One row per (node, kind, tag); tags come from the closed vocab in
-- reasoning/resources.py. Lets us JOIN an event's needs to a provider's offers.
-- ──────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS node_resources (
    node_id uuid NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    kind    text NOT NULL CHECK (kind IN ('need', 'offer')),
    tag     text NOT NULL,
    PRIMARY KEY (node_id, kind, tag)
);

CREATE INDEX IF NOT EXISTS node_resources_tag_idx  ON node_resources (kind, tag);
CREATE INDEX IF NOT EXISTS node_resources_node_idx ON node_resources (node_id);
