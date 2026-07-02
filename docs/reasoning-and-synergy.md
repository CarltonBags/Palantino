# Reasoning, Search & Synergy — how the app finds things

How the chat, insights, synergy finder and Tellerrand work. Written for future-you:
read this before changing retrieval or the synergy engine.

Everything runs on the graph in Postgres (nodes + edges, bitemporal), pgvector
embeddings, and an LLM (DeepSeek v4-pro by default; OpenAI for embeddings).

---

## 1. The chat search modes

Every chat query starts the same:

1. **Intent extraction** — an LLM reads the query → `{lens, search_text,
   node_types, category, list, date_from/to}`. Lens ∈ factual / synergy /
   inefficiency / scandal / crime / leads.
2. **Embed** `search_text` (OpenAI `text-embedding-3-large`).

Then the **retrieval mode** (toggle: Semantisch / Strukturell / Komplementär /
Graph / Tiefensuche) decides how candidates are picked. The four non-default modes
apply to the synergy/leads lens.

### Semantisch — *similarity* (default)
- Query-vector → nearest node vectors (pgvector cosine, HNSW).
- Broad lenses use **MMR** (diverse + random anchor) so it doesn't keep returning
  the same cluster; factual/`list` use KNN + filters (type/category/date), with
  pgvector **iterative scan** so a filter doesn't starve results.
- Then **multi-hop graph expansion** (`_expand`): walk edges from the seeds to pull
  in connected facts (a tender + the resolution behind it).
- **Good for:** factual Q&A, exploring a topic/area.

### Strukturell — *nearness*
- Query seeds → their **physically nearby** (25–300 m, PostGIS `ST_DWithin`),
  cross-type, **unconnected** partners (`_structural_partners`). The pairs are the
  signal (no expand).
- **Good for:** "who's right next to this but not yet linked."

### Komplementär — *need ↔ offer*
- Query seeds → partners whose **offer matches a seed's need** (or vice versa) on
  the **resource layer** (`node_resources` tags) via `_complementary_partners`.
- **Good for:** "who supplies what this actor is missing" (a bike tour needs a rest
  stop → a café offers it).

### Graph — *graph structure* (link prediction)
- Query seeds (name-match + semantic, actor-filtered) → actors that **share specific
  neighbours** with them (same articles, events, tenders) but are **not directly
  linked**, ranked by shared-count (`_link_prediction_partners`). **Hubs excluded**
  (GeoArea + any connector wired to >60 nodes) so a shared Stadtbezirk doesn't link
  everything.
- Targets **should-connect-but-doesn't** — the untapped signal similarity/proximity
  miss. Subsumes plain co-mention (the 1-shared-neighbour case).
- **Good for:** synergies grounded in the actual civic fabric, not embedding
  distance.

### Tiefensuche — *researched & verified* (the heavy one)
- **Anchors** on the entities the query names — a fuzzy **name-match** pass (so a
  query about a named actor reliably hits it, not just the theme) + semantic.
- Per anchor, builds candidate partners from **all four** signals (semantic +
  complementary + proximity + link prediction), skipping near-duplicates/same-venue
  (`_same_actor`) and pairs the feedback loop suppressed (§7).
- **Comparative validation:** researches the anchor + all its candidates, then ONE
  LLM call ranks/picks the best real synergies (rejects the rest with reasons) —
  preferring **non-obvious cross-domain bridges** (🌉), sorted first.
- Shows validated + the checked-and-rejected (with reasons). Precise, slow.
- **Good for:** "actually check whether these synergies are real."

**One line each:** Semantisch = *similar* · Strukturell = *near* · Komplementär =
*fits* · Graph = *structurally linked* · Tiefensuche = *researched & verified*.

---

## 2. The actor filter (all synergy retrieval)

A candidate must be a real, partnerable **ACTOR** (`_actor_clause` in `qa.py`):

- named venues/clubs/businesses → **POI** (not raw `OSM node …`)
- real events → **Event** where `event_type <> 'news'`
- civic actors extracted from news → Organization `source='news_extraction'`
- **calendar venues** → Organization `source='event_venue'` (§5)
- Handelsregister companies → Organization `source='offeneregister'`, **only on a
  business query** and only if `status='currently registered'`

Excluded everywhere: **news articles as actors** (§4) and dissolved companies. Node
reuse capped (≤2×) so a few hubs don't dominate.

---

## 3. The resource layer (complementary synergies)

`reasoning/resources.py` — closed need/offer vocabulary; `node_resources` table:
- **POIs** — deterministic from OSM tags (café → offers verpflegung; club → offers
  publikum/flaeche, needs sponsoring).
- **Events + extracted actors + venues** — LLM-tagged (`enrich_events`,
  `enrich_actors`) from label/description/role.

`Komplementär` + the complementary insight scan join `need ↔ offer` on these tags.

---

## 4. News → actors

~77 % of Event nodes are **news articles**, not partnerable entities — the article
just *describes* a real actor. `reasoning/actor_extraction.py` LLM-extracts the named
orgs/initiatives/offices → **Organization** nodes (`source='news_extraction'`,
`inferred=True`, article as provenance, deduped by name), linked `MENTIONS`. GDPR:
organisations only, never private individuals.

**Self-maintaining:** extraction embeds incrementally (flush every 100 + final), so
a killed run never leaves actors invisible. `_maintain_actor_layer` (extract + tag +
embed) is chained after both news ingests (every 6h → new articles get actors on the
fly) and runs a daily backlog sweep.

---

## 5. Calendar venues → actors

`reasoning/venue_extraction.py`: each calendar event names its `venue`. Distinct
venues become **Organization** actors (`source='event_venue'`), **with geom from
their events** and contacts carried over; events linked `LOCATED_IN` (batched
inserts, capped 80/venue). ~163 venues, each hosting many events = active, contactable,
**geolocated** hub actors — unlike news actors they can also do **proximity**
synergies. Chained into the events flow (extract + tag + embed on each ingest).

---

## 6. Insights (the proactive scanner)

`reasoning/scanner.py` pre-computes synergies/inefficiencies into `insights`, grouped
by scan run. Modes: **Klassisch** (traversal generators), **Strukturell** (proximity),
**Komplementär** (need↔offer), **Tiefensuche** (same researched+validated pipeline).

Freshness (each scan, automatically):
- **`_cap_node_reuse`** — no primary actor in >2 insights per scan (one market can't
  spawn many near-identical ones).
- **`expire_stale_insights`** — dismiss `new` insights older than 14 d, whose evidence
  node is gone, OR tied to a **calendar event already past** (`valid_from` = the exact
  event date). So an ended exhibition drops out the next day.

Each insight carries confidence, evidence node ids, scan_id, `inferred=True`.

---

## 7. Feedback loop

`synergy_feedback` table (migration 009), one row per unordered actor pair:
- The deep finder **persists its own verdicts** (`record_synergy_feedback`,
  source='llm'): reject / makes_sense.
- The user's insight **confirm/dismiss** writes too (source='user') and **outranks**
  the LLM (a later llm write can't clobber a user verdict).
- `_deep_synergy_pairs` calls `suppressed_pair_keys()` and **skips rejected/dismissed
  pairs** — so the same rejected candidates stop re-appearing, and confirmed ones
  survive. Cross-surface: dismissing an insight suppresses that pair in chat too.

---

## 8. The deep synergy finder pipeline

`reasoning/synergy_finder.py`:
1. **Candidate pairs** — query anchors (chat) or global proximity/complementary
   generators (insights), grouped by anchor.
2. **Comparative research + validation** (`evaluate_anchor`): research the anchor +
   ALL its candidates (graph context + fetch websites), then ONE LLM call
   (`SYNERGY_COMPARE_SYSTEM`) ranks/picks the best, prefers cross-domain bridges,
   rejects the rest with reasons. One call per anchor, not per pair.
3. Keep validated until n; return validated + rejected. Verdicts fed to §7.

Precision over speed — returns as many *genuine* synergies as exist, not padded.

---

## 9. Tellerrand (horizon-broadening)

`reasoning/tellerrand.py`: input an interest / Verein / attended event / personality
traits ("keine großen Menschenmengen"). The LLM derives a profile + proposes
*adjacent-but-different* interests with a bridge, respecting constraints; each is
grounded in real Dortmund Event/POI/Organization nodes by semantic retrieval.

---

## Key files

| Area | File |
|------|------|
| Chat retrieval + modes, actor filter, link prediction | `reasoning/qa.py` |
| Deep synergy finder + feedback | `reasoning/synergy_finder.py` |
| Resource layer (need/offer) | `reasoning/resources.py`, `reasoning/resource_enrich.py` |
| News → actors | `reasoning/actor_extraction.py` |
| Calendar venues → actors | `reasoning/venue_extraction.py` |
| Insight scanner + expiry/cap | `reasoning/scanner.py` |
| Tellerrand | `reasoning/tellerrand.py` |
| Prompts | `reasoning/prompts.py` |
| Flows (auto-run + crons) | `ingestion/flows.py` |
