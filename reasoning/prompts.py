"""
Prompt templates for the reasoning layer.

The Claude API receives subgraph JSON — never the whole graph.
All insights are returned as inferred=True edges/notes with confidence + trace.
"""

from __future__ import annotations

import json
from typing import Any

SYSTEM_PROMPT = """\
You are a civic intelligence assistant analyzing a knowledge graph of the city of Dortmund, Germany.
You receive subgraph excerpts as structured JSON (nodes + edges with provenance and timestamps).
Your job: surface inefficiencies and synergies — patterns where civic facts interact in notable ways.

Rules:
- Only reason over facts provided. Do not hallucinate entities or relationships.
- For public officials: report sourced observations only ("Person X voted Y on date Z, source: URL").
  Never characterize motives or personality.
- Return structured JSON: a list of insights, each with type, description, evidence (node/edge IDs),
  confidence (0-1), and a reasoning_trace.
- confidence < 0.7: do not return.
- Respond only with valid JSON. No prose outside the JSON object.
"""

INEFFICIENCY_PROMPT = """\
Analyze this subgraph for inefficiencies: cases where two or more civic actions
conflict, duplicate effort, or produce waste (e.g., a road repaved the same month
a council resolution approved a new bus route through it, or two overlapping tenders
for the same street segment).

Subgraph:
{subgraph_json}

Return JSON:
{{
  "insights": [
    {{
      "type": "inefficiency",
      "description": "...",
      "evidence": ["<node_id>", "<edge_id>", ...],
      "confidence": 0.0,
      "reasoning_trace": "..."
    }}
  ]
}}
"""

SYNERGY_PROMPT = """\
Analyze this subgraph for synergies: cases where civic entities or decisions
reinforce, benefit from, or enable each other in a positive way
(e.g., a council resolution that directly enabled a tender awarded to a local company).

Subgraph:
{subgraph_json}

Return JSON:
{{
  "insights": [
    {{
      "type": "synergy",
      "description": "...",
      "evidence": ["<node_id>", "<edge_id>", ...],
      "confidence": 0.0,
      "reasoning_trace": "..."
    }}
  ]
}}
"""


def format_subgraph(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> str:
    return json.dumps({"nodes": nodes, "edges": edges}, default=str, ensure_ascii=False, indent=2)
