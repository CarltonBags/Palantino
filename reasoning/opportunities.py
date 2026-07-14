"""
Business-opportunity detection for Dortmund.

Finds GAPS — places where demand plausibly outstrips local supply — from the
spatial POI layer, cross-referenced with district demographics, the news-distilled
Problem layer and vacant storefronts. General purpose (any sector), not scoped to
one field.

Core signal: per-Stadtbezirk category UNDERSUPPLY, normalised by each district's
own commercial footprint (expected-share), so a small district isn't flagged just
for being small. A district that holds 15% of the city's shops but 0% of its
pharmacies has a pharmacy gap regardless of size.

Everything returned is a sourced observation (POI counts, census rates, vacant
units); the opportunity framing an LLM writes on top is an inference and is
labelled as such in the answer layer.
"""
from __future__ import annotations

from typing import Any

# Consumer-facing businesses / services worth a supply analysis. Deliberately
# excludes street furniture, parking, vacant/"yes"/None and civic infrastructure.
_COMMERCIAL: tuple[str, ...] = (
    # shop=*
    "supermarket", "bakery", "butcher", "greengrocer", "convenience", "kiosk",
    "beverages", "chemist", "clothes", "shoes", "hairdresser", "beauty",
    "cosmetics", "optician", "jewelry", "mobile_phone", "florist", "tailor",
    "variety_store", "travel_agency", "furniture", "bicycle", "hardware",
    "books", "laundry", "massage", "hearing_aids", "car", "car_repair",
    # amenity=*
    "restaurant", "fast_food", "cafe", "pub", "bar", "pharmacy", "doctors",
    "dentist", "veterinary", "kindergarten", "fuel", "bank", "cinema",
    "car_wash", "driving_school",
    # office=*
    "insurance", "lawyer", "estate_agent", "tax_advisor", "consulting",
)

# A gap is only interesting if the district would be expected to hold at least
# this many of the category — filters out long-tail noise (one tailor citywide).
_MIN_EXPECTED = 2.5


_GAPS_SQL = """
WITH sb AS (
    SELECT id, label, geom FROM nodes
    WHERE node_type = 'GeoArea' AND properties->>'area_type' = 'stadtbezirk'
      AND valid_to IS NULL AND geom IS NOT NULL
),
poi AS (
    SELECT coalesce(properties->>'shop', properties->>'amenity',
                    properties->>'office') AS cat, geom
    FROM nodes
    WHERE node_type = 'POI' AND valid_to IS NULL AND geom IS NOT NULL
      AND coalesce(properties->>'shop', properties->>'amenity',
                   properties->>'office') = ANY($1::text[])
),
joined AS (
    SELECT sb.label AS district, p.cat, count(*)::float AS actual
    FROM sb JOIN poi p ON ST_Contains(sb.geom, p.geom)
    GROUP BY 1, 2
),
grid AS (  -- full district × category cross product so actual=0 gaps appear too
    SELECT d.district, c.cat
    FROM (SELECT DISTINCT district FROM joined) d
    CROSS JOIN (SELECT DISTINCT cat FROM joined) c
),
cell AS (
    SELECT g.district, g.cat, coalesce(j.actual, 0) AS actual
    FROM grid g LEFT JOIN joined j ON j.district = g.district AND j.cat = g.cat
),
dt AS (SELECT district, sum(actual) AS dist_total FROM cell GROUP BY 1),
ct AS (SELECT cat, sum(actual) AS cat_total FROM cell GROUP BY 1),
tot AS (SELECT sum(actual) AS grand FROM cell)
SELECT c.district, c.cat, c.actual::int AS actual,
       round((ct.cat_total * dt.dist_total / nullif(tot.grand, 0))::numeric, 1) AS expected,
       round((ct.cat_total * dt.dist_total / nullif(tot.grand, 0) - c.actual)::numeric, 1) AS deficit
FROM cell c
JOIN dt ON dt.district = c.district
JOIN ct ON ct.cat = c.cat
CROSS JOIN tot
WHERE ct.cat_total * dt.dist_total / nullif(tot.grand, 0) >= $2
ORDER BY deficit DESC
LIMIT $3
"""


async def commercial_gaps(conn: Any, limit: int = 25) -> list[dict]:
    """Top undersupplied (district, category) cells by expected-share deficit."""
    rows = await conn.fetch(_GAPS_SQL, list(_COMMERCIAL), _MIN_EXPECTED, limit)
    return [dict(r) for r in rows]


_VACANT_SQL = """
WITH sb AS (
    SELECT label, geom FROM nodes
    WHERE node_type = 'GeoArea' AND properties->>'area_type' = 'stadtbezirk'
      AND valid_to IS NULL AND geom IS NOT NULL
)
SELECT sb.label AS district, count(*) AS vacant_units
FROM sb JOIN nodes n ON n.node_type = 'POI' AND n.valid_to IS NULL
     AND n.geom IS NOT NULL AND n.properties->>'shop' = 'vacant'
     AND ST_Contains(sb.geom, n.geom)
GROUP BY 1 ORDER BY 2 DESC
"""


async def vacant_storefronts(conn: Any) -> list[dict]:
    """Empty retail units per district — literal spots to open something."""
    return [dict(r) for r in await conn.fetch(_VACANT_SQL)]


_DISTRICT_CONTEXT_SQL = """
SELECT label, properties->>'area_type' AS area_type, properties
FROM nodes
WHERE node_type = 'GeoArea' AND valid_to IS NULL
  AND properties->>'area_type' IN ('stadtbezirk_demographics', 'stadtbezirk_sozialindikatoren')
  AND lower(properties->>'stadtbezirk') = lower($1)
"""


async def district_context(conn: Any, district: str) -> dict[str, Any]:
    """Demographic + social-indicator profile for one Stadtbezirk (demand side)."""
    out: dict[str, Any] = {}
    for r in await conn.fetch(_DISTRICT_CONTEXT_SQL, district):
        props = r["properties"] if isinstance(r["properties"], dict) else {}
        for k, v in props.items():
            if k not in ("area_type", "stadtbezirk", "stadtbezirk_nr", "year") and v not in (None, ""):
                out[k] = v
    return out
