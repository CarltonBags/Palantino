"""
Incremental embedding backfill.

Embeds the semantically-useful node types and skips nodes whose embed-text (and
model) are unchanged, so re-runs are cheap. DB writes go through executemany +
ON CONFLICT — batched, unlike the per-row connector path — because this touches
tens of thousands of rows.
"""

from __future__ import annotations

import logging
from typing import Any

from config import settings
from db.session import get_conn
from embeddings.embedder import embed_texts, node_embedding_text, text_hash, to_pgvector

logger = logging.getLogger(__name__)

# Types worth a vector: text-ish records + named entities. Road segments and
# observations (weather/air) are skipped — low semantic value, high volume.
EMBED_NODE_TYPES = (
    "Event", "Meeting", "Resolution", "AgendaItem", "Tender",
    "POI", "Organization", "GeoArea", "ConstructionSite",
)

_WRITE_BATCH = 256


async def embed_nodes(limit: int | None = None) -> dict[str, int]:
    """Embed new/changed nodes and upsert their vectors. Returns counts."""
    model = settings.embedding_model

    async with get_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT n.id, n.node_type, n.label, n.properties,
                   e.text_hash AS old_hash, e.model AS old_model
            FROM nodes n
            LEFT JOIN node_embeddings e ON e.node_id = n.id
            WHERE n.valid_to IS NULL AND n.node_type = ANY($1::text[])
            """,
            list(EMBED_NODE_TYPES),
        )

    todo: list[tuple[str, str, str]] = []  # (node_id, text, hash)
    for r in rows:
        node = dict(r)
        txt = node_embedding_text(node)
        if not txt:
            continue
        h = text_hash(f"{model}::{txt}")
        if node["old_hash"] == h and node["old_model"] == model:
            continue  # unchanged — skip
        todo.append((str(node["id"]), txt, h))
        if limit and len(todo) >= limit:
            break

    embedded = 0
    for i in range(0, len(todo), _WRITE_BATCH):
        batch = todo[i : i + _WRITE_BATCH]
        vecs = await embed_texts([t for _, t, _ in batch])
        records = [
            (nid, to_pgvector(v), model, h)
            for (nid, _, h), v in zip(batch, vecs)
        ]
        async with get_conn() as conn:
            await conn.executemany(
                """
                INSERT INTO node_embeddings (node_id, embedding, model, text_hash)
                VALUES ($1, $2::vector, $3, $4)
                ON CONFLICT (node_id) DO UPDATE
                SET embedding = EXCLUDED.embedding, model = EXCLUDED.model,
                    text_hash = EXCLUDED.text_hash, embedded_at = now()
                """,
                records,
            )
        embedded += len(records)
        logger.info("embed-nodes: %d/%d embedded", embedded, len(todo))

    return {"scanned": len(rows), "to_embed": len(todo), "embedded": embedded}
