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
- Language: write every human-readable field (title, description, reasoning_trace)
  in GERMAN. Keep the JSON keys and the "type" value (inefficiency/synergy) in
  English; node/edge IDs stay verbatim.
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
Analyze this subgraph for POTENTIAL, not-yet-realized synergies — untapped
opportunities the city has NOT acted on, where two or more civic facts COULD
reinforce or benefit each other if someone coordinated them.

Focus on latent potential, e.g.:
  - many separate events at the same venue / date cluster that could be jointly
    promoted or share logistics, security, transit (as partly seen at the
    Westfalenhalle event cluster);
  - a council initiative and a nearby business / POI / infrastructure that could
    partner or be timed together;
  - planned works that could be coordinated with an event or another project.

Hard rules:
  - Do NOT report synergies that ALREADY exist or are already realized in the
    data (e.g. a resolution that already enabled a tender, an edge that already
    connects the two). Only surface opportunities that are NOT yet connected.
  - You MUST validate each opportunity in reasoning_trace: state the concrete
    mechanism by which it would create value, cite which facts in the subgraph
    make the opportunity real and plausible, and what action would be required
    to realize it. If you cannot justify genuine, actionable potential, omit it.
  - Let confidence reflect how strong and actionable the unrealized potential is.

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
