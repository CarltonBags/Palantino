# Reasoning, Search & Synergy — how the app finds things

How the chat, insights, synergy finder and Tellerrand actually work. Written for
future-you: read this before changing retrieval or the synergy engine.

Everything runs on the graph in Postgres (nodes + edges, bitemporal), pgvector
embeddings, and an LLM (DeepSeek v4-pro by default; OpenAI for embeddings).

---

## 1. The chat search modes

Every chat query starts the same:

1. **Intent extraction** — an LLM reads the query → `{lens, search_text,
   node_types, category, list, date_from/to}`. Lens ∈ factual / synergy /
   inefficiency / scandal / crime / leads.
2. **Embed** `search_text` (OpenAI `text-embedding-3-large`, 1536-dim).

Then the **retrieval mode** (the toggle: Semantisch / Strukturell / Komplementär /
Tiefensuche) decides how candidates are picked:

### Semantisch — *similarity* (default)
- Query-vector → nearest node vectors (pgvector cosine over an HNSW index).
- Broad/analytical lenses use **MMR** (diverse seeds + random anchor) so it doesn't
  keep returning the same dense cluster; factual/`list` asks use deterministic
  KNN + structured filters (type/category/date). Filtered vector queries use
  pgvector **iterative scan** so a type/date filter doesn't starve results.
- Then **multi-hop graph expansion** (`_expand`): walk edges out from the seeds to
  pull in connected facts (a tender + the resolution behind it). Skips hub types,
  caps ~40 nodes.
- **Good for:** factual Q&A, exploring a topic/area.

### Strukturell — *nearness* (synergy / leads)
- Query-relevant seeds → for each, its **physically nearby** (25–300 m, PostGIS
  `ST_DWithin`), **cross-type**, currently-**unconnected** partners
  (`_structural_partners`).
- No expansion — the pairs *are* the signal.
- **Good for:** "who's right next to this but not yet linked" — location-driven,
  untapped synergies vector search can't reach (a concert ↔ the café 80 m away).

### Komplementär — *need ↔ offer* (synergy / leads)
- Query-relevant seeds → partners whose **offer matches a seed's need** (or vice
  versa), matched on the **resource layer** (`node_resources`, closed tag
  vocabulary: verpflegung, getraenke, publikum, ziel, sponsoring, veranstaltungs-
  flaeche, …) via `_complementary_partners`.
- **Good for:** "who supplies what this actor is missing" — fit, regardless of
  distance or topic (a bike tour needs a rest stop → a beer festival / café offers
  it). See §3 for how tags get onto nodes.

### Tiefensuche — *researched & verified* (the heavy one)
- **Anchors** on the entities the query names (top real-actor matches), then builds
  candidate partners from **all three** signals above (semantic + near + need/offer).
- For each candidate pair a **research sub-agent** gathers both partners' full graph
  context **and fetches their websites**, then an LLM validates the audience/occasion
  fit — rejecting the implausible (proximity ≠ compatibility; a fitness studio and a
  jazz concert are not a synergy).
- Drop-and-keep until ~5 hold; **rejected pairs are shown with the reason**.
- Slow (many fetches + validations, evaluated concurrently), precise.
- **Good for:** "actually check whether these synergies are real."

**One line each:** Semantisch = *similar* · Strukturell = *near* · Komplementär =
*fits* · Tiefensuche = *researched*.

---

## 2. The actor filter (applies to all synergy retrieval)

A candidate must be a **real, partnerable ACTOR** (`_actor_clause` in
`reasoning/qa.py`):

- named venues / clubs / businesses → **POI** (OSM, not the raw `OSM node …` ones)
- real events → **Event** where `event_type <> 'news'`
- **civic actors extracted from news** → Organization, `source='news_extraction'`
- Handelsregister companies → Organization, `source='offeneregister'`, **only when
  the query is explicitly about business** (keywords: unternehmen, firma, gmbh,
  vergabe, …), and **only if `status='currently registered'`** (dissolved firms
  excluded).

Excluded everywhere: **news articles as actors** (see §4) and **dissolved
companies**. Node reuse is capped (≤2×) so a few venues/POIs don't dominate.

---

## 3. The resource layer (complementary synergies)

`reasoning/resources.py` defines a closed vocabulary of resources. Nodes get
`need` / `offer` tags in the `node_resources` table:

- **POIs** — deterministic map from OSM tags (café → offers verpflegung; hotel →
  uebernachtung; sports club → offers publikum/flaeche, needs sponsoring).
- **Events** — LLM-tagged from title/description (a tour → needs rast/verpflegung;
  a festival → offers verpflegung/publikum, needs transport/parkraum).

`Komplementär` mode and the complementary insight scan join `need ↔ offer` on these
tags.

---

## 4. News → actors (why articles aren't partners)

77 % of Event nodes are **news articles** (Nordstadtblogger / Wir in Dortmund),
not entities you can partner with. The article usually *describes* a real actor
(a Verein, a Stiftung, a city office).

`reasoning/actor_extraction.py` reads each article and LLM-extracts the named
orgs/initiatives/offices → **Organization** nodes (`source='news_extraction'`,
`inferred=true`, article as provenance, deduped by normalised name), linked
`MENTIONS`. GDPR: organisations only, never private individuals.

Synergies then anchor on the **actors**, not the articles. Status: the pipeline
works; coverage is incremental — extraction is **not yet automatic** (run via the
`run_actor_extraction` flow; wiring it into news ingestion + a backlog cron is the
open task).

---

## 5. Insights modes (the proactive scanner)

The Insights tab pre-computes synergies/inefficiencies into the `insights` table,
grouped by scan run. Modes:

- **Klassisch** — graph-traversal generators (spatial-temporal, area-bridge,
  ego-network, news-context) → inefficiency + synergy.
- **Strukturell** — PostGIS proximity synergy candidates.
- **Komplementär** — need ↔ offer candidates.
- **Tiefensuche** — the same researched+validated pipeline as chat Tiefensuche,
  returning validated synergies + the rejected ones with reasons.

Each insight carries confidence, evidence node ids (→ open on map), and a
scan_id (which run produced it). `inferred=true` — kept separate from source facts.

---

## 6. Tellerrand (horizon-broadening discovery)

`reasoning/tellerrand.py`: input an interest, a Verein, an attended event, and/or
personality traits ("keine großen Menschenmengen"). Stage 1 the LLM derives a
profile and proposes *adjacent-but-different* interests + a bridge (why it connects
& widens), respecting the constraints. Stage 2 grounds each in real Dortmund
Event/POI/Organization nodes by semantic retrieval. (Chess club → Improvisations-
theater / Go / Programmieren; festival + no-crowds → intimate concerts / jam
sessions.)

---

## 7. The deep synergy finder pipeline (chat & insights Tiefensuche)

`reasoning/synergy_finder.py`:

1. **Candidate pairs** — from the query anchors (chat) or the global
   proximity/complementary generators (insights), each naming two involved partners.
2. **Research per pair** (`_evaluate_pair`, concurrent in batches): gather each
   partner's graph context (properties, edges, contacts, temporal status) + fetch
   their website.
3. **Validate** (`SYNERGY_RESEARCH_SYSTEM` prompt): strict ZIELGRUPPE/ANLASS test —
   would the same audience plausibly use both? Mere nearness never qualifies.
   Returns a structured `description` (Akteur A, Akteur B, Zielgruppe/Anlass,
   Mechanismus, Synergie-Potenzial) + first_step + contacts, OR a reject + reason.
4. Keep validated until n; return validated + rejected (with reasons).

Precision over speed: it returns as many *genuine* synergies as exist rather than
padding to n with weak ones.

---

## Key files

| Area | File |
|------|------|
| Chat retrieval + modes | `reasoning/qa.py` |
| Deep synergy finder | `reasoning/synergy_finder.py` |
| Resource layer (need/offer) | `reasoning/resources.py`, `reasoning/resource_enrich.py` |
| Actor extraction from news | `reasoning/actor_extraction.py` |
| Insight scanner + generators | `reasoning/scanner.py` |
| Tellerrand | `reasoning/tellerrand.py` |
| Prompts (all of the above) | `reasoning/prompts.py` |
