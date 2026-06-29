# Dortmund Civic-Graph — Data Source Inventory

> Source-of-truth list of every data source evaluated for the project, with access method, auth, a tier, and a verdict. Tiers: **Tier 1** = open API/data, build now · **Tier 2** = usable with constraints · **Tier 3** = no API, manual/RSS-watch · **Avoid** = do not automate (bot-protected, paywalled, register-restricted, or reuse-forbidden).

> Companion docs: ingestion cadence + temporal model in `ingestion-and-temporal-design.md`. Legal/scraping rules summarized at the bottom of this file.


## Council / Politics

### Dortmund Ratsinformationssystem — somacos Session via OWL-IT  `[Tier 3 — OParl DISABLED]`
**System:** somacos Session (formerly CC e-GOV / Solutionteam)  
**Hosted by:** OWL-IT (Ostwestfalen-Lippe IT)  
**SessionNet UI:** https://sessionnet.owl-it.de/dortmund/bi/  
**OParl:** DISABLED in production. City confirmed via FragDenStaat FOI (Oct 2024): _"Die Schnittstelle ist in der Produktion aus Sicherheitsgründen inaktiv."_ No endpoint URL exists publicly.  
**OParl connector:** Framework in place (`connectors/oparl/`). Activate when/if Dortmund enables it. `OPARL_ENDPOINT_URL` left blank in `.env.example`.  
**Fallback for committee dates:** `fb1-gremientermine` ODS dataset (see Open Data Portal below) — daily updates.

### Dortmund Gremientermine (Committee Dates)  `[Tier 1]`
**URL:** https://open-data.dortmund.de/api/explore/v2.1/catalog/datasets/fb1-gremientermine/records  ·  **Access:** ODS REST/JSON  ·  **Auth:** None  
Daily-refreshed committee meeting schedule from the Session system. Partial substitute for OParl Meeting objects: gives **upcoming** dates, committee names, locations. Missing: agenda items, resolutions, votes. Connector `connectors/gremientermine/` (snapshot). For the historical record WITH decisions, see Gremienniederschriften below.

### Dortmund Gremienniederschriften (Council MINUTES + Beschlüsse)  `[Tier 1]`  ★ best politics source
**Index:** https://open-data.dortmund.de/api/explore/v2.1/catalog/datasets/fb1-gremienniederschriften/records (5055 past sittings back to 2013: date, Gremium, document link) · **Auth:** None  
**Documents:** https://rathaus.dortmund.de/dosys/doRat.nsf — the linked minutes HTML (city's own Lotus Domino RIS, separate host from SessionNet).  
**robots.txt — ALLOWED:** `User-Agent: * / Allow: /dosys/` then `Disallow: /`; only specific `gremrech2.nsf` document hashes are disallowed. `doRat.nsf` minutes are NOT blocked → fetching them is robots-permitted (unlike SessionNet which blocks the whole Dortmund path). Public-authority records.  
**Yield:** each minutes doc carries ~40-60 TOPs and ~40 Beschlüsse with Drucksachen-Nr + (best-effort) vote outcome. Connector `connectors/gremienniederschriften/` (event_stream): emits **Meeting + AgendaItem + Resolution** nodes, all with `source_url` = the actual minutes document (rule 1), plus `agenda_of` / `decided_in` edges. Vote/`passed` is only set when the minutes state it plainly, else None (rule 4 — never fabricate a decision). Polite: 1s delay, capped 150 docs/run so the 5055-doc backfill spreads over runs.

### Dortmund election results (ODS)  `[Tier 1]`
**Built:** `connectors/wahlergebnisse/` (reference). Covers the aggregate city-wide tables: `kommunalwahlen-wahlergebnisse`, `bundestagswahlen-zweitstimme-wahlergebnisse-`, `landtagswahlen-zweitstimme-wahlergebnisse`, `europawahlen-wahlergebnisse` → Event nodes (`event_type=election`, party vote shares); plus `kommunalwahlen-ratsmitglieder` → Event (`event_type=council_composition`, seats per party) = the "who holds the seats" layer. Generic `<party>_absolut`/`<party>_in` parser; nulls + totals dropped.  
**Deferred:** OB-Wahl (`oberburgermeisterinwahlen-wahlergebnisse`, ~90 candidate columns — candidate-level, messy).

### Dortmund Ratswahl per Stimmbezirk (precinct-level)  `[Tier 1]`
**Built:** `connectors/wahlergebnisse_stimmbezirk/` (reference). Datasets `fb33-rat-20140525/20200913/20250914-stimmbezirke` (~419/671/666 precincts) → Event nodes (`event_type=election_precinct`): per-precinct turnout + party votes + Stadtbezirk/Stimmbezirk. **2025 rows carry the polling-station point** (`geo_point_2d`) → geom set, flow does ST_Within into a statistischer Bezirk (LOCATED_IN). Earlier years lack geometry but carry the Stadtbezirk name for the resolution layer. Generic party parser (bare party columns; metadata/turnout set excluded). Live: 1756 precinct events, 666 geo-located.  
**Still deferred:** `fb33-ob-*` / `fb33-bv-*` per-precinct (same pattern, add datasets when needed).

### Poliscope (aggregator)  `[Tier 3]`
**URL:** https://poliscope.de/ratsinformationssystem/dortmund-stadt/05913  ·  **Access:** Web / commercial  ·  **Auth:** Varies  
Third-party that already parses Dortmund RIS. Useful cross-check but build directly on the source — depends on OParl becoming available.

### Poliscope (aggregator)  `[Tier 3]`
**URL:** https://poliscope.de/ratsinformationssystem/dortmund-stadt/05913  ·  **Access:** Web / commercial  ·  **Auth:** Varies  
Third-party that already parses Dortmund RIS with AI search + alerts. Useful as a cross-check / inspiration, but build directly on OParl rather than depending on them.


## Open Data

### Dortmund Open Data Portal (OpenDataSoft)  `[Tier 1]`
**URL:** https://open-data.dortmund.de/api/explore/v2.1/  ·  **Access:** API (REST/JSON)  ·  **Auth:** None (open)  
CONFIRMED LIVE. 470+ datasets, DL-DE-Zero license. Roads (fb62-strassen), district boundaries, construction sites, election results, buildings, P+R, Gewerbe/insolvency stats, smart-city sensors. Build your FIRST connector here. No live events feed though.

### Open Data Portal Ruhr / Open.NRW  `[Tier 2]`
**URL:** https://opendata.ruhr / https://open.nrw  ·  **Access:** API/CKAN  ·  **Auth:** None (open)  
Regional + state portals that re-publish many Dortmund datasets (incl. roads). Good fallback / for Ruhr-wide expansion later.


## Businesses

### OpenStreetMap (Overpass API)  `[Tier 1]`
**URL:** https://overpass-api.de/  ·  **Access:** API  ·  **Auth:** None (open)  
BEST practical source for 'businesses that physically exist': shops, restaurants, offices with name, category, coordinates, opening hours. Query by Dortmund boundary. ODbL license (attribution + share-alike). This is your storefront/POI layer.

### OffeneRegister.de (Handelsregister dump)  `[Tier 2]`
**URL:** https://offeneregister.de/daten/  ·  **Access:** Bulk download  ·  **Auth:** None (open)  
Free bulk Handelsregister data = registered legal entities (GmbH etc.), NOT storefronts. Good for the 'company/ownership' layer. Note: Unternehmensregister/Bundesanzeiger themselves forbid republishing, so this is the open alternative.

### Dortmund Gewerbeanzeigen + Insolvenzen (stats)  `[Tier 1]`
**URL:** https://open-data.dortmund.de/explore/dataset/gewerbeanzeigen-und-insolvenzen/  ·  **Access:** API  ·  **Auth:** None (open)  
Aggregate annual counts only (2002-2023), not individual firms. Useful as a trend/context node, not entity-level.

### Official Gewerberegister (Ordnungsamt)  `[Avoid]`
**URL:** https://www.dortmund.de/themen/gewerbe/  ·  **Access:** Manual / on request  ·  **Auth:** Restricted  
The actual business register is NOT publicly browsable in Germany. Auskunft only on written request with legitimate interest. Cannot bulk-ingest. Do not try to scrape.

### OpenStreetMap Overpass (already listed) - PRIMARY  `[Tier 1]`
**URL:** https://overpass-api.de/  ·  **Access:** API  ·  **Auth:** None (open, ODbL)  
Reconfirmed as the backbone of 'as much business data as possible': every mapped shop/office/restaurant with name, branch, address, coords, hours. ODbL = free to use WITH attribution + share-alike. Filter to Dortmund admin boundary. Pair with periodic refresh.

### Wirtschaftsförderung Dortmund  `[Tier 3]`
**URL:** https://www.wirtschaftsfoerderung-dortmund.de/  ·  **Access:** Web  ·  **Auth:** None  
City economic-development agency: business news, funded firms, startup ecosystem, CORPORATE EVENTS (diwodo etc.). No API; manual/RSS-watch. Good source for the 'corporate events' you asked about + major employer signals.

### IHK zu Dortmund (Chamber of Commerce)  `[Tier 3]`
**URL:** https://www.ihk.de/dortmund  ·  **Access:** Web/newsletter  ·  **Auth:** None  
Chamber membership = most local businesses. Public member directory + business events. No bulk API; directory lookups + newsletter. Corporate-events source too.

### Bundesanzeiger (financials)  `[Avoid (bulk)]`
**URL:** https://www.bundesanzeiger.de/  ·  **Access:** Web (restricted)  ·  **Auth:** None to search  
Mandatory company financial filings. Searchable free, but ToS forbids systematic/automated extraction and republishing (this is exactly why OffeneRegister exists). Manual lookups only.


## Sports

### Borussia Dortmund (BVB) official  `[Tier 2]`
**URL:** https://www.bvb.de/de/en/match-schedule.html  ·  **Access:** Embedded JSON  ·  **Auth:** None  
BVB's own match-schedule pages embed structured JSON (dates, times, venue, opponent, results) for football + other BVB departments (handball etc.). Parseable, but check bvb.de ToS before automating; consider it semi-official.

### Anthropic sports-data tool (this assistant)  `[Tier 1]`
**URL:** built-in  ·  **Access:** Tool  ·  **Auth:** n/a  
I have a live sports tool covering football (BVB Bundesliga/CL), basketball, NHL hockey, baseball/MLB: scores, standings, box scores. Best for live/recent results without building a scraper. Coverage is major leagues, not local amateur clubs.

### Eisadler Dortmund (ice hockey)  `[Tier 3]`
**URL:** club website  ·  **Access:** Manual  ·  **Auth:** None  
Lower-league local club (Oberliga). Schedule only on own site / league site. Manual or light scrape.

### SVD 49 Dortmund (basketball)  `[Tier 3]`
**URL:** club website  ·  **Access:** Manual  ·  **Auth:** None  
Second-division local club. Own site / league site only.

### Dortmund Wanderers (baseball)  `[Tier 3]`
**URL:** club website  ·  **Access:** Manual  ·  **Auth:** None  
Baseball Bundesliga club. Own site / league site only.

### StadtSportBund Dortmund - club directory  `[Tier 2]`
**URL:** https://www.ssb-do.de/startseite/vereine/vereinssuche  ·  **Access:** Web (structured search)  ·  **Auth:** None  
THE master list of EVERY sport in Dortmund: ~80 sport types incl. rowing, baseball, cricket, lacrosse, archery, fencing, canoe, climbing... Searchable by sport / district / A-Z, with club name, address, contact, age groups. Best single source for 'every sport imaginable' as the club/venue layer. Check robots.txt + terms before automating; data is factual club info (low IP concern) but be polite (rate-limit).

### basketball-bund.net / TeamSL (DBB)  `[Tier 2]`
**URL:** https://www.basketball-bund.net/  ·  **Access:** Web portal  ·  **Auth:** None to view  
The REAL backend for DoBasket + WBV games - all NRW basketball schedules/results run through DBB's TeamSL portal, not the club sites. dobasket.de and wbv-online are just info homepages. Scrape TeamSL pages politely / check terms; this is where actual fixtures+results live.

### DoBasket (Basketballkreis Dortmund)  `[Tier 3]`
**URL:** https://www.dobasket.de/  ·  **Access:** Web  ·  **Auth:** None  
Local basketball district info site (news, announcements, club list at /vereine). Game data itself is on TeamSL. Light reference.

### WBV (Westdeutscher Basketball-Verband)  `[Tier 3]`
**URL:** https://www.basketball.nrw/  ·  **Access:** Web  ·  **Auth:** None  
Regional federation news + standings context. Actual fixtures via TeamSL. Reference.

### Deutscher Ruderverband - club finder  `[Tier 3]`
**URL:** https://www.rudern.de/service/vereine  ·  **Access:** Web  ·  **Auth:** None  
Rowing: national club directory locates Dortmund clubs (RC Hansa 1898, RC Germania, Gymnasialer RV) + the national rowing training base at An den Bootshäusern. Reference for the rowing node; events via club sites.

### Sport federation portals (per sport)  `[Tier 3]`
**URL:** various (DFB, DEB, DBV, etc.)  ·  **Access:** Web/portal  ·  **Auth:** Varies  
Pattern for amateur leagues: the sport's national/regional federation portal holds fixtures+results (e.g. fussball.de for football down to amateur, DEB for hockey, DBV for baseball). Build per-federation as needed; many are politely scrapeable, none offer a clean open API.


## Weather

### Bright Sky (DWD wrapper)  `[Tier 1]`
**URL:** https://api.brightsky.dev/  ·  **Access:** API (JSON)  ·  **Auth:** None (open)  
CONFIRMED LIVE. Clean JSON over official DWD data: pass Dortmund lat/lon, get current + forecast + historical. DWD Terms of Use apply (open). Easiest weather connector by far.

### DWD Open Data (raw)  `[Tier 2]`
**URL:** https://opendata.dwd.de/  ·  **Access:** FTP/HTTPS files  ·  **Auth:** None (open)  
Official source, free by law, but raw formats (GRIB2, BUFR, thousands of zipped txt). Use only if you need something Bright Sky doesn't expose.

### DWD GeoWebService (warnings)  `[Tier 2]`
**URL:** https://maps.dwd.de/  ·  **Access:** WMS/WFS  ·  **Auth:** None (open)  
For official weather WARNINGS as geodata (storm, heat). Good 'event-trigger' input to the graph (e.g. correlate warnings with traffic/events).


## Local News

### Nordstadtblogger  `[Tier 2]`
**URL:** https://www.nordstadtblogger.de/  ·  **Access:** Likely RSS/WordPress  ·  **Auth:** None  
Independent, non-commercial Dortmund news (WordPress -> almost certainly has /feed/ RSS). Non-commercial stance = friendliest to ingest, but confirm their reuse terms. Good civic/political signal source.

### Ruhr Nachrichten  `[Tier 3]`
**URL:** https://www.ruhrnachrichten.de/dortmund/  ·  **Access:** Web (paywalled)  ·  **Auth:** Subscription  
Largest local paper (Lensing-Wolff, same group as coolibri). Mostly paywalled, commercial ToS. Headlines only / manual; do not scrape full articles.

### dortmund.de RSS  `[Avoid (for now)]`
**URL:** https://www.dortmund.de/allgemeines/rss-feed/  ·  **Access:** RSS  ·  **Auth:** None  
City RSS is CURRENTLY DISABLED after their site relaunch. Also: their terms forbid embedding city feeds in any service showing commercial ads. Revisit later; respect the no-commercial clause.


## Events

### Ticketmaster Discovery API  `[Tier 2]`
**URL:** https://app.ticketmaster.com/discovery/v2/  ·  **Access:** API  ·  **Auth:** Free key  
Works for DE. 5k req/day. ToS restricts caching beyond runtime - query live, don't permanently store.

### Eventbrite API v3  `[Tier 2]`
**URL:** https://www.eventbriteapi.com/v3/  ·  **Access:** API  ·  **Auth:** OAuth  
No keyword/location search since 2020. Only usable via pinned Dortmund venue/organizer IDs.

### coolibri  `[Tier 3 / Avoid]`
**URL:** https://coolibri.de/veranstaltungen/dortmund/  ·  **Access:** Web  ·  **Auth:** None  
CHECKED: no public API. Commercial publisher (Verlag Lensing-Wolff). Strong Dortmund event coverage but scraping a commercial site is a ToS risk. Manual reference, or seek a data partnership.

### Rausgegangen  `[Tier 3 / Avoid]`
**URL:** https://rausgegangen.de/  ·  **Access:** Web / partner-only  ·  **Auth:** Account  
CHECKED: no public/open API. 'Zentrale' API is partner/organizer-only (scannerapi behind login). Good curated Dortmund culture events; manual reference or partnership only.

### dortmund.de Veranstaltungskalender  `[Avoid]`
**URL:** https://www.dortmund.de/dortmund-erleben/veranstaltungskalender/  ·  **Access:** Web (bot-protected)  ·  **Auth:** None  
CONFIRMED behind Link11 CAPTCHA. 4,000+ listings = best in city, but do NOT scrape. Pursue a direct data ask to the city.

### Open Data Portal - historical event stats  `[Tier 1]`
**URL:** https://open-data.dortmund.de/  ·  **Access:** API  ·  **Auth:** None (open)  
Konzerthaus / Volkshochschule attendance counts only - historical, not a live calendar. Context node.


## Police / Security

### Polizei NRW Dortmund - Pressemeldungen (RSS)  `[Tier 1]`
**URL:** https://dortmund.polizei.nrw/presse/pressemitteilungen  ·  **Access:** RSS  ·  **Auth:** None (open)  
Official police press releases WITH an explicit RSS feed = authorized machine-readable channel. Covers incidents, traffic accidents, raids, and crucially DEMONSTRATIONS/Versammlungen (with crowd sizes, routes, locations, political framing). Public-authority content. Excellent civic-signal source. Watch GDPR: releases name no private individuals, but don't re-identify anyone.

### Presseportal Blaulicht (Polizei Dortmund)  `[Tier 2]`
**URL:** https://www.presseportal.de/blaulicht/nr/4971  ·  **Access:** RSS/web  ·  **Auth:** None  
Same police releases syndicated by dpa's Presseportal, also offers RSS. Useful as a mirror/backfill. Respect Presseportal ToS; prefer the official police feed as primary.


## Demonstrations

### Versammlungsbehörde (announced demos) + Polizei recaps  `[Tier 2]`
**URL:** via Polizei RSS + city Ordnungsamt  ·  **Access:** RSS / manual  ·  **Auth:** None  
No single open 'demonstrations API'. Two-part approach: (1) police RSS reports demos after the fact (size, route); (2) announced/registered assemblies sometimes pre-published by the city Ordnungsamt or counter-protest organizers - manual watch. Politically sensitive: keep to facts (where/when/how many), no inferences about attendees.


## Corporate Events

### Messe Dortmund + Wirtschaftsförderung + IHK  `[Tier 3]`
**URL:** see rows above  ·  **Access:** Web/newsletter  ·  **Auth:** None  
Corporate/B2B events aggregate from: Messe Dortmund (trade fairs), Wirtschaftsförderung (diwodo, economic events), IHK (chamber events), plus the AI/tech ecosystem rows in the events sheet (Lamarr, KI.NRW, Fraunhofer IML). No single API - this is a curated manual layer, ideally an LLM pass over their newsletters/pages.


## Mobility / Transit

### GTFS static schedules  `[Tier 1]`
**URL (used):** https://download.gtfs.de/germany/nv_free/latest.zip  ·  **Access:** Download (GTFS zip, no auth)  ·  **License:** CC-BY (gtfs.de/DELFI)  
**NOTE:** The official VRR/NRW feed on opendata-oepnv.de GATES downloads behind a free account (anonymous requests return 404 — confirmed). We use the gtfs.de free Germany-wide local-transit feed instead: open, stable `latest.zip`, AND same provider as our GTFS-Realtime stream so static IDs line up with realtime. Connector bounds stops to a Dortmund bbox and keeps only routes touching those stops (streams stop_times→trips→routes to stay memory-bounded). Timetable spine — stops, routes, scheduled times.

### gtfs.de GTFS-Realtime stream  `[Tier 1]`
**URL:** https://realtime.gtfs.de/realtime-free.pb  ·  **Access:** API (GTFS-RT protobuf)  ·  **Auth:** None (free)  
FREE Germany-wide GTFS-Realtime: TripUpdates + ServiceAlerts (delays, cancellations, disruptions). Pairs with the static feed above. This is your LIVE transit layer - poll every 30-60s when you need realtime, else hourly.

### DSW21 (Dortmund operator)  `[Tier 3]`
**URL:** https://www.dsw21.de/  ·  **Access:** App/web  ·  **Auth:** Varies  
Local operator. Its app data flows into VRR/GTFS already; direct site useful for service notices + Metropolradruhr bike-share integration. Prefer the GTFS feeds as primary.


## Traffic / Roadworks

### Autobahn GmbH live traffic  `[Tier 1]`  ★ built
**URL:** https://verkehr.autobahn.de/o/autobahn/<road>/services/<service>  ·  **Access:** public REST/JSON, no auth  ·  **License:** DL-DE-Zero  
**Built:** `connectors/autobahn/` (event_stream). Per-Autobahn services `roadworks` / `warning` / `closure`. We poll the motorways through Dortmund (A1, A2, A40, A42, A44, A45) and keep items inside the Dortmund bbox → `roadworks`→ConstructionSite, `warning`→Event(`traffic_disruption`), `closure`→Event(`road_closure`). Each has a coordinate → geom set; flow does ST_Within LOCATED_IN. Live: ~81 items in-bbox. Checkpoint dedupes by identifier; polled every 30 min.

### Umweltportal / VIZ.NRW - Stau & Baustellen  `[Tier 2 — gated]`
**URL:** https://www.umweltportal.nrw.de/en/open-data ; VIZ.NRW feed via mobilithek.info  ·  **Auth:** Mobilithek registration  
NRW state traffic-info-centre (VIZ) publishes statewide Stau + roadworks XML, but the open.nrw CKAN resource now points at a **mobilithek.info** offer (registration/contract). `verkehrsmeldungen-mobilitatsdaten-d` is **Düsseldorf**, not Dortmund. Use the Autobahn GmbH API above for motorway state + ODS `fb66-baustellen` for city streets; revisit VIZ if a non-gated endpoint appears.

### Dortmund Open Data - construction sites  `[Tier 1]`
**URL:** https://open-data.dortmund.de/  ·  **Access:** API  ·  **Auth:** None (open)  
City portal has current + planned construction sites (Baustellen) as a dataset - already noted, reconfirmed as a live-ish operational layer (refresh daily).


## Environment

### LANUV LUQS air quality (NRW)  `[Tier 1]`
**URL:** https://open.nrw (LUQS dataset) / aqicn API  ·  **Access:** API / hourly data  ·  **Auth:** None (open)  
Hourly air-quality measurements (PM10, NO2, NO, SO2, O3, temp, wind) for all NRW stations incl. Dortmund, last 24h, updated hourly. Values preliminary/unvalidated - flag as such. Great input to correlate with traffic/events/weather.

### LANUV noise (Umgebungslärm) + Solarkataster  `[Tier 2]`
**URL:** https://www.umweltportal.nrw.de/  ·  **Access:** Geo data / portal  ·  **Auth:** None (open)  
Environmental noise maps (road/rail/industry) and solar-potential cadastre as geodata. Context layers for council/planning reasoning. Geo formats (WMS/WFS).

### OpenGeodata.NRW (aerial/satellite, terrain)  `[Tier 2]`
**URL:** https://www.opengeodata.nrw.de/  ·  **Access:** Download (geo)  ·  **Auth:** None (open)  
State geobasis open data: aerial imagery, terrain, land use. Heavy but authoritative for the geographic spine.


## City Finance

### Dortmund Haushaltsplan (budget)  `[Tier 2]`
**URL:** via dortmund.de / RIS (OParl Papers)  ·  **Access:** PDF / structured via RIS  ·  **Auth:** None (open)  
The city budget. Often published as PDF + as Vorlagen inside the RIS (so partly reachable via OParl Papers). Following the money is core to 'where are inefficiencies'. May need PDF parsing; link budget lines to OParl resolutions.

### Vergabe.NRW - public tenders (open data)  `[Tier 1]`
**URL:** https://www.vergabe.nrw.de/wirtschaft/offene-daten  ·  **Access:** Open data (structured)  ·  **Auth:** None (open)  
CONFIRMED: NRW publishes procurement/tender announcements (Bekanntmachungen) as structured OPEN DATA for anyone. This is the 'contracts that resulted from decisions' layer - links council resolution -> tender -> awarded company (-> your business nodes). High value for synergy/inefficiency detection.


## Demographics

### Dortmund Open Data - population/statistics set  `[Tier 1]`
**URL:** https://open-data.dortmund.de/  ·  **Access:** API  ·  **Auth:** None (open)  
Deliberately pull the FULL statistics set: population by district, age structure, population movement, unemployment, election results by district. This is the 'who lives where' layer that lets the model say which inefficiencies hit which neighborhoods. Already-available portal, just scope it in fully.

### IT.NRW / Landesdatenbank (regional stats)  `[Tier 2]`
**URL:** https://www.it.nrw / landesdatenbank.nrw.de  ·  **Access:** API/download  ·  **Auth:** None (open)  
State statistics office: deeper socioeconomic indicators at municipal/district level (income proxies, housing, migration). Backfill where the city portal is thin.


## Education/Health/Social

### Dortmund Open Data XErleben POIs  `[Tier 1]`
**URL:** https://open-data.dortmund.de/  ·  **Access:** API  ·  **Auth:** None (open)  
Points-of-interest sets: schools, libraries, swimming pools, kindergartens, etc. as 'Orte von Interesse (XErleben)'. Civic facility nodes that events/demographics/politics all link to. Already on the portal.

### Kita / school registries (NRW)  `[Tier 2]`
**URL:** open.nrw / ministry portals  ·  **Access:** Open data / web  ·  **Auth:** None (open)  
Daycare (Kita) and school registries at NRW level fill gaps the city POI set misses (capacity, type). Some open data, some web.

### Hospitals / pharmacies  `[Tier 2]`
**URL:** OSM + federal directories  ·  **Access:** API / web  ·  **Auth:** None (open)  
OSM covers hospitals/pharmacies as POIs; federal directories add licensing/type. Health-facility nodes.


## Geographic Spine

### Streets register (fb62-strassen-statistische-bezirke)  `[Tier 1]`  ★ built
**URL:** ODS `fb62-strassen-statistische-bezirke` (~4700 streets)  ·  **Auth:** None  
**Built:** `connectors/strassen/` (reference) → Road nodes (strassenname, Straßenschlüssel, statistischer Bezirk, Stadtbezirk). No geometry in this set — it's the **street gazetteer** for the text linker (street mentions in minutes/tenders/police → Road) plus per-street district assignment. Flow joins each Road `PART_OF` its statistischer Bezirk by name.

### Street segments with geometry (fb62-strassenabschnitte)  `[Tier 1]`  ★ built
**URL:** ODS `fb62-strassenabschnitte` (~19.5k LineString segments)  ·  **Auth:** None  
**Built:** `connectors/strassenabschnitte/` (reference) → segment-level Road nodes WITH LineString geometry (the drawable road layer; name, key, segment no., length, road class). Different source from `strassen` so the two never collide. Flow links each segment `PART_OF` its parent street (by Straßenschlüssel) and `LOCATED_IN` its statistischer Bezirk (ST_Within). The text-linker gazetteer deliberately uses only the street-level register, not these segments. Rendered as a line layer in the frontend.

### Stadtbezirk/Statistischer Bezirk boundaries + addresses  `[Tier 1]`
**URL:** https://open-data.dortmund.de/ (fb62 etc.)  ·  **Access:** API (geo)  ·  **Auth:** None (open)  
Make geography first-class: the city's district boundaries (Stadtbezirke, statistische Bezirke) + address/parcel data are the SPINE everything snaps to. PostGIS. Already on the portal - prioritize this early so every other node gets a consistent location + district.


---

## Legal & Scraping — quick rules (Germany)

> Not legal advice; confirm high-stakes cases with a lawyer.

- **§44b UrhG (Text & Data Mining)** — Germany has a STATUTORY exception (Sec. 44b Copyright Act, from the EU DSM Directive): you may automatically reproduce/analyse lawfully accessible online works for data mining, INCLUDING commercial purposes - UNLESS the rights holder declared an opt-out. Courts (LG/OLG Hamburg, LAION case) confirmed automated scraping = TDM under this rule.
- **The opt-out (Nutzungsvorbehalt)** — An opt-out is only binding if 'machine-readable'. The emerging gold standard is robots.txt (and /.well-known/tdmrep.json / TDMRep meta tags). IMPORTANT caveat: German courts have been willing to treat a clearly-worded opt-out in a site's TERMS OF USE as machine-readable too (since modern LLMs can read it). So: check BOTH robots.txt AND the site's Nutzungsbedingungen/AGB before scraping.
- **Delete-when-done** — Sec. 44b requires deleting copies once no longer needed for the mining. For a living knowledge graph, store derived facts + source links rather than hoarding raw page copies; document a retention/refresh policy.
- **robots.txt = your first check** — For every site you consider scraping: fetch /robots.txt first. If it disallows your path or your bot, treat that as a binding no. Respect crawl-delay, identify your bot honestly with a User-Agent + contact, and rate-limit (don't hammer servers - excessive load can itself be unlawful (Sec. 303b StGB / interference)).
- **GDPR (the bigger constraint)** — Copyright is often the EASY part; PERSONAL DATA is the hard part. Names, photos, contact details of identifiable living people are personal data. You generally need a legal basis (Art. 6 GDPR) to collect+store them. For your graph: politicians acting in OFFICIAL capacity (votes, statements) = defensible public-interest basis; private individuals in police reports = do NOT store identifying details; business owners' names need care. Minimize, justify, and prefer roles over persons.
- **Database rights (Sec. 87a UrhG)** — A structured database can have its OWN sui-generis protection separate from the content. Systematically extracting 'substantial parts' of someone's database (e.g. a whole event calendar or club directory) can infringe even if each item is unprotected. Take what you need, attribute, don't wholesale-clone a competitor's DB.
- **Logins, CAPTCHAs, paywalls** — Anything behind authentication, a CAPTCHA, or a paywall is NOT 'lawfully accessible' for scraping - circumventing it breaks the rule (and likely the contract). This is why the dortmund.de calendar (Link11 CAPTCHA), Bundesanzeiger, LinkedIn and Meta are 'Avoid'.
- **Safe by construction** — Lowest-risk sources, in order: (1) open APIs with clear licenses (Open Data Portal, OParl, Bright Sky, Overpass); (2) official public-authority publications (police RSS, city pages) - public-sector info, factual; (3) RSS feeds the publisher deliberately offers (they're inviting machine consumption); (4) politely scraping factual public pages with no opt-out. Build connectors in THAT priority order.
- **Attribution & licenses** — Honor the license of each source: OSM = ODbL (attribution + share-alike), Open Data Portal = DL-DE-Zero (do anything, attribution appreciated), DWD/Bright Sky = DWD terms (free, attribute). Keep a 'source + license' field on every node/edge - which your CLAUDE.md provenance rule already enforces.
- **Per-source checklist** — Before each new connector: 1) robots.txt allows it? 2) terms/AGB don't forbid automated use? 3) no login/CAPTCHA/paywall crossed? 4) any personal data minimized + justified? 5) not extracting a 'substantial part' of a protected DB? 6) license recorded? If all clear -> build. If any fails -> manual reference or seek permission/partnership.