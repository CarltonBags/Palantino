"""
Semantic layer — turn nodes into vectors for nearest-neighbour retrieval.

Provider-abstracted (default OpenAI); the rest of the system only calls
`embed_texts`. `node_embedding_text` decides what text represents a node, and
`text_hash` lets the backfill skip nodes whose embed-text hasn't changed.

The pure helpers (node_embedding_text, text_hash, to_pgvector) need no network
and are unit-tested; embed_texts hits the provider API.
"""

from __future__ import annotations

import hashlib
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from config import settings

# Node properties (besides label) worth folding into the embed-text, across the
# node types we embed. Missing keys are skipped, so one list covers all types.
_PROP_KEYS = (
    "description", "venue", "author", "categories", "event_type",
    "stadtbezirk", "stat_bezirk", "category", "gremium", "title", "subtitle",
    "reason", "name_de", "road_type", "strassenklasse", "strassengruppe",
    "area_type", "register_id",
    # companies / planned works / clubs
    "rechtsform", "org_type", "beschreibung", "art", "gewerk", "objektart", "sport",
)

# OpenAI accepts up to 2048 inputs/request; keep batches modest for payload size.
_BATCH = 256


def node_embedding_text(node: dict[str, Any]) -> str:
    """Compose the text that represents a node for embedding (type + label + key props)."""
    parts: list[str] = [str(node.get("node_type", "")), str(node.get("label", ""))]
    props = node.get("properties")
    if not isinstance(props, dict):  # some rows store properties as a list/str
        props = {}
    for key in _PROP_KEYS:
        val = props.get(key)
        if isinstance(val, str) and val.strip():
            parts.append(val.strip())
        elif isinstance(val, list):
            parts.extend(str(v) for v in val if isinstance(v, str) and v.strip())
    return " | ".join(p for p in parts if p)[:8000]  # stay well under token limits


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def to_pgvector(vec: list[float]) -> str:
    """pgvector text literal, e.g. '[0.1,0.2,...]', for an asyncpg ::vector cast."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1, min=2, max=30))
async def _openai_batch(client: httpx.AsyncClient, chunk: list[str]) -> list[list[float]]:
    resp = await client.post(
        f"{settings.openai_base_url}/embeddings",
        headers={"Authorization": f"Bearer {settings.openai_api_key}"},
        json={
            "model": settings.embedding_model,
            "input": chunk,
            "dimensions": settings.embedding_dimensions,
        },
    )
    resp.raise_for_status()
    data = sorted(resp.json()["data"], key=lambda d: d["index"])
    return [d["embedding"] for d in data]


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a list of texts, preserving order. Batches internally."""
    if not texts:
        return []
    if settings.embedding_provider != "openai":
        raise RuntimeError(f"Unsupported embedding_provider: {settings.embedding_provider!r}")
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY not configured — cannot embed")

    out: list[list[float]] = []
    async with httpx.AsyncClient(timeout=60.0) as client:
        for i in range(0, len(texts), _BATCH):
            out.extend(await _openai_batch(client, texts[i : i + _BATCH]))
    return out
