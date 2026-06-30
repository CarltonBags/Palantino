"""
Ask-the-city Q&A — grounded retrieval-augmented generation over the graph.

Pipeline: embed the question -> pgvector KNN to the most relevant nodes -> gather
the edges among them -> hand that subgraph to the LLM, which answers in German
using only those facts and naming its sources. Reuses the semantic layer
(embeddings) and the swappable LLM provider.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from db.session import get_conn
from embeddings.embedder import embed_texts, to_pgvector
from reasoning.llm import complete
from reasoning.prompts import QA_PROMPT, QA_SYSTEM_PROMPT, format_subgraph


async def answer_question(question: str, k: int = 24) -> dict[str, Any]:
    """Answer a natural-language question from the k most relevant graph facts."""
    question = (question or "").strip()
    if not question:
        return {"answer": "Bitte eine Frage eingeben.", "citations": []}

    qvec = (await embed_texts([question]))[0]
    lit = to_pgvector(qvec)

    async with get_conn() as conn:
        nodes = await conn.fetch(
            """
            SELECT n.id, n.node_type, n.label, n.properties, n.source, n.source_url,
                   n.valid_from
            FROM node_embeddings e
            JOIN nodes n ON n.id = e.node_id
            WHERE n.valid_to IS NULL
            ORDER BY e.embedding <=> $1::vector
            LIMIT $2
            """,
            lit, k,
        )
        ids = [str(n["id"]) for n in nodes]
        edges = await conn.fetch(
            """
            SELECT id, edge_type, from_node_id, to_node_id, properties, source
            FROM edges
            WHERE from_node_id = ANY($1::uuid[]) AND to_node_id = ANY($1::uuid[])
              AND valid_to IS NULL
            """,
            ids,
        )

    if not nodes:
        return {
            "answer": "Dazu liegen im Wissensgraphen keine passenden Fakten vor.",
            "citations": [],
        }

    subgraph = format_subgraph([dict(n) for n in nodes], [dict(e) for e in edges])
    prompt = QA_PROMPT.format(
        question=question, subgraph_json=subgraph, today=date.today().isoformat()
    )
    answer = await complete(QA_SYSTEM_PROMPT, prompt, max_tokens=1500)

    citations = [
        {
            "id": str(n["id"]),
            "label": n["label"],
            "node_type": n["node_type"],
            "source": n["source"],
            "source_url": n["source_url"],
        }
        for n in nodes
    ]
    return {"answer": answer, "citations": citations}
