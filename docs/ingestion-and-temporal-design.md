# Ingestion Cadence & The Temporal Model

> How `civic-graph` handles the difference between "current state" data and
> "history of change," and how often to pull each source. This is the design
> note behind the question: *"do we just call the APIs a couple of times a day?"*

## The short answer

Polling a few times a day is **part** of it — but the important move is not how
*often* you call, it's what you **do with each result**. If every poll overwrites
the previous value, you have a system that only ever knows "now" and can never
reason about change. If every poll instead *appends a timestamped observation*,
you turn a pile of "current state" APIs into a full history — which is exactly
where the inefficiencies and synergies live ("they repaved the road three months
after approving the route change").

So: **poll on a cadence that matches how fast each source changes, and store
every poll as an immutable, timestamped fact rather than overwriting.**

## Bitemporal facts (the core idea)

Every fact in the graph carries **two** time axes:

- **valid_from / valid_to** — when the fact was true *in the real world*
  (e.g. a bus route existed from March to June).
- **observed_at** — when *we* recorded it (the poll timestamp).

This is called a *bitemporal* model. It lets you ask both "what was true on
1 May?" and "what did we *know* on 1 May?" — and it's what makes "this changed
after that" queries possible at all. A node/edge is never deleted or overwritten;
when something changes, you close the old version (set `valid_to`) and open a new
one. The graph becomes append-only.

Minimum fields on every node and edge:
```
source        # which connector / dataset
source_url    # exact provenance link
observed_at   # when we polled/ingested
valid_from    # when it became true in the world (best estimate)
valid_to      # when it stopped (null = still current)
inferred      # false for source facts, true for reasoning-layer output
```

## Three data shapes, three strategies

Not all sources are the same. Classify each connector into one of three shapes:

### 1. Snapshot sources ("current state only")
The API tells you what's true *right now* and forgets the past
(air quality reading, parking occupancy, transit realtime, weather now,
"current construction sites", a business's current opening hours).

**Strategy:** poll on a cadence, and **append each reading as a new timestamped
observation**. You are *manufacturing* the time series the source doesn't keep.
Don't overwrite. This is the case the user's question is really about — and the
answer is: yes, call it on a schedule, but the value comes from *accumulating*
the snapshots, not from the call itself.

### 2. Event-stream / append sources
The source already publishes discrete dated items (council resolutions via OParl,
police press releases via RSS, news RSS, tender announcements, sports fixtures).

**Strategy:** poll for *new items since last seen* (OParl and most RSS support
"modified since" / stable pagination, so this is cheap). Each item is already a
timestamped event — just insert it and link it. Dedduplicate on the source's
stable ID.

### 3. Reference / slow-changing sources
Big, mostly-static datasets (road network, district boundaries, building
footprints, the full business/POI set, demographics).

**Strategy:** full refresh on a slow cadence (weekly/monthly), diff against what
you have, and only write *changes* as new fact versions. No point polling
boundaries hourly.

## A concrete cadence table (starting point — tune later)

| Source | Shape | Suggested cadence |
|---|---|---|
| Transit GTFS-Realtime (delays/alerts) | Snapshot | 30–60 sec *when actively needed*, else skip |
| Air quality (LANUV, hourly) | Snapshot | Hourly (matches their update) |
| Weather (Bright Sky) | Snapshot | Hourly; forecasts a few times/day |
| Traffic / roadworks (NRW) | Snapshot | 15–30 min |
| Parking occupancy | Snapshot | 5–15 min when needed |
| Police press RSS | Event-stream | Every 1–2 hours |
| Local news RSS (Nordstadtblogger) | Event-stream | Every 1–2 hours |
| Council OParl (resolutions/meetings) | Event-stream | Daily (they don't change faster) |
| Public tenders (Vergabe.NRW) | Event-stream | Daily |
| Sports fixtures/results | Event-stream | Daily; hourly on match days |
| Events (Ticketmaster etc.) | Event-stream | Daily |
| Transit GTFS static schedule | Reference | Weekly (matches their Wed refresh) |
| Road network / boundaries / buildings | Reference | Monthly |
| Business / POI set (OSM Overpass) | Reference | Weekly–monthly |
| Demographics / statistics | Reference | Monthly / on publish |

Rule of thumb: **poll at roughly the rate the source itself updates.** Polling
faster than the data changes just wastes requests and risks rate limits; polling
slower means you miss transitions. Match the source's own heartbeat.

## Operational notes

- **Idempotency:** ingesting the same item twice must not create duplicates.
  Key on (source, source_id) and on a content hash; if unchanged since last
  observation, just extend `valid_to`/touch `observed_at`, don't write a new row.
- **Backfill once, then incremental:** for event-streams, do one historical
  backfill (OParl and many archives expose the full history), then only fetch new.
- **Respect rate limits & be polite:** especially for scraped sources — schedule,
  cache, set a real User-Agent, honor crawl-delay. (Tie this back to the legal tab:
  hammering a server can be unlawful independent of copyright.)
- **Snapshots can get big:** a reading every minute is ~half a million rows/year
  per sensor. Downsample old high-frequency snapshots (e.g. keep 1-min for 7 days,
  then roll up to hourly averages) rather than keeping everything forever.
- **Orchestration:** a scheduler (Prefect/cron) runs each connector on its cadence;
  each connector is responsible only for "fetch new since last run -> emit
  timestamped facts." The temporal/versioning logic lives in one shared layer so
  every connector behaves the same way.

## Why this matters for the whole project

The entire value proposition — "a model reasoning about what's going on, where
inefficiencies are, where synergies lie" — depends on the graph being able to see
*change over time*, not just a snapshot. Most civic APIs only give you "now."
The temporal/append discipline above is what converts that stream of "nows" into
the historical, queryable fabric the reasoning layer needs. It's the single most
important architectural decision in the project, more than any individual source.
