"""
Problem-anchored coalitions.

A single actor rarely solves a civic problem; a COALITION does. Each Problem node
carries the resource needs distilled from its coverage (node_resources kind='need').
This assembles a small set of real actors whose combined OFFERS cover those needs —
a set-cover, relevance-ranked so members are actually related to the problem, not
just anyone who happens to carry a generic tag.

Output is candidate structure (inferred): who could contribute what. The answer
layer frames it and an LLM sanity-checks it; nothing here asserts a partnership
exists.
"""
from __future__ import annotations

from typing import Any

# An offerer must be at least this semantically close to the problem to join a
# coalition. The offer tags are broad (sichtbarkeit, ziel…) with thousands of
# holders, so without this gate set-cover would pick arbitrary actors.
_RELEVANCE_MAX_DIST = 0.60
_POOL = 60  # candidate offerers to consider before greedy selection


async def assemble_coalition(
    conn: Any, problem_id: str, max_members: int = 4
) -> dict[str, Any]:
    """Greedy set-cover of a problem's needs by relevance-ranked offerers.

    Returns {needs, covered, uncovered, members:[{id,label,node_type,source,
    source_url,covers:[tag]}]}. Empty members if nothing relevant covers a need."""
    needs = [
        r["tag"] for r in await conn.fetch(
            "SELECT tag FROM node_resources WHERE node_id = $1 AND kind = 'need'",
            problem_id,
        )
    ]
    if not needs:
        return {"needs": [], "covered": [], "uncovered": [], "members": []}

    # one query: actors offering ANY needed tag, the needed tags each covers, and
    # their distance to the problem embedding (relevance). Gate by that distance.
    pool = await conn.fetch(
        """
        SELECT n.id, n.label, n.node_type, n.source, n.source_url,
               array_agg(DISTINCT r.tag) AS covers,
               min(e.embedding <=> (SELECT embedding FROM node_embeddings WHERE node_id = $1)) AS dist
        FROM node_resources r
        JOIN nodes n ON n.id = r.node_id AND n.valid_to IS NULL AND n.node_type <> 'Problem'
        JOIN node_embeddings e ON e.node_id = n.id
        WHERE r.kind = 'offer' AND r.tag = ANY($2::text[])
          AND n.id <> $1
        GROUP BY n.id, n.label, n.node_type, n.source, n.source_url
        HAVING min(e.embedding <=> (SELECT embedding FROM node_embeddings WHERE node_id = $1)) <= $3
        ORDER BY dist ASC
        LIMIT $4
        """,
        problem_id, needs, _RELEVANCE_MAX_DIST, _POOL,
    )

    need_set = set(needs)
    candidates = [
        {
            "id": str(p["id"]), "label": p["label"], "node_type": p["node_type"],
            "source": p["source"], "source_url": p["source_url"],
            "covers": set(p["covers"]) & need_set, "dist": float(p["dist"]),
        }
        for p in pool
    ]

    # greedy set-cover: repeatedly take the candidate covering the most still-
    # uncovered needs; break ties by relevance (smaller dist). Stop when needs are
    # covered or the size cap is hit.
    uncovered = set(need_set)
    members: list[dict] = []
    while uncovered and len(members) < max_members and candidates:
        best = max(
            candidates,
            key=lambda c: (len(c["covers"] & uncovered), -c["dist"]),
        )
        gain = best["covers"] & uncovered
        if not gain:
            break  # no remaining candidate adds coverage
        members.append({
            "id": best["id"], "label": best["label"], "node_type": best["node_type"],
            "source": best["source"], "source_url": best["source_url"],
            "covers": sorted(gain),
        })
        uncovered -= gain
        candidates.remove(best)

    return {
        "needs": needs,
        "covered": sorted(need_set - uncovered),
        "uncovered": sorted(uncovered),
        "members": members,
    }
