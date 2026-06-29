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
- Report your honest confidence (0-1); calibrate it, don't inflate. Do not return
  anything below 0.5 (the system filters the rest by type).
- Language: write every human-readable field (title, description, reasoning_trace)
  in GERMAN. Keep the JSON keys and the "type" value (inefficiency/synergy) in
  English; node/edge IDs stay verbatim.
- Respond only with valid JSON. No prose outside the JSON object.
"""

INEFFICIENCY_PROMPT = """\
Current date: {today}.

Analyze this subgraph for inefficiencies: cases where two or more civic actions
conflict, duplicate effort, or produce waste (e.g., a road repaved the same month
a council resolution approved a new bus route through it, or two overlapping tenders
for the same street segment).

IGNORE artifacts of how the graph stores data — these are NOT inefficiencies and
must never be reported:
  - a road represented as several segments (Abschnitte) sharing one
    Straßenschlüssel — that is the normal segmented road model, not duplication;
  - the same place existing as both a Stadtbezirk and a statistischer Bezirk with
    the same name — those are two distinct administrative levels;
  - multiple/parallel edges between the same two nodes.
Report only real-world civic inefficiencies (conflicting or duplicated actions,
wasted public effort), not quirks of the data representation.

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
Current date: {today}.

Analyze this subgraph for POTENTIAL, not-yet-realized synergies — untapped
opportunities the city has NOT acted on, where two or more civic facts COULD
reinforce or benefit each other if someone coordinated them.

Focus on latent potential ACROSS otherwise-unconnected actors or domains, e.g.:
  - an event and an UNRELATED nearby actor — a different organizer, a community
    group, a local business, a civic initiative — that could cross-promote or
    share audience / logistics;
  - a council initiative and a nearby business / POI / infrastructure that could
    partner or be timed together;
  - planned works that could be coordinated with an event or another project.

News as civic signal: if the subgraph contains news articles (event_type =
"news"), read them as SIGNALS of what the city needs, feels, or is talking about
— a problem, a mood, an underserved group, an emerging theme. Then creatively but
plausibly connect that signal to an event, place, business, or civic action that
could address, serve, or amplify it (e.g. an article about social isolation and a
community/social event that could reach those residents). This link MAY span
different districts — it need not be nearby. Be imaginative, but stay grounded:
cite the specific article, make the benefit concrete, and don't force a connection
that isn't genuinely plausible.

Hard rules:
  - NO intra-venue bundling of commercial venues. Do NOT propose synergies that
    merely pool several events at the SAME large, professionally-run venue
    (Westfalenhalle, Konzerthaus, Messe/arenas, big private clubs). Those are
    already well marketed; the city adds nothing by bundling them. A commercial-
    venue event may appear in a synergy ONLY when paired with a DIFFERENT,
    otherwise-unconnected actor or domain (a community event, a civic/council
    action, a small local business) — never with another event at the same venue.
  - TEMPORAL RELEVANCE (critical for events — they are time-sensitive): an
    opportunity is only actionable if its parts are timely relative to the
    current date and to each other. Use each node's valid_from. Do NOT pair a
    years-old, already-concluded council item (e.g. a 2022 Antrag) with a
    2026/2027 event and call it a live synergy — the idea may be sound but the
    window has passed. If the gap between a concluded action and the event is
    large (roughly > 1 year), or the action is clearly already finished, omit it
    or set very low confidence.
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
