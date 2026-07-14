"""
Snap calendar events + their venue actors to OSM ground truth.

The city event feed's locationAddress.geo is unreliable: latitudes are roughly
right but longitudes often land kilometres east of the real venue (Zoo Dortmund
14.6 km off, Subrosa 2.5 km). OSM POIs carry surveyed coordinates, so whenever
an event's `venue` name matches a POI unambiguously, the event (and the
extracted event_venue Organization) is moved to the POI position.

The original feed point is preserved in properties.feed_geo and the move is
flagged with properties.geo_corrected='osm_poi' — a correction of a bad
upstream observation, not a new fact version.

Runs after each events ingest (the feed refresh would otherwise reintroduce the
bad coordinates) — chained in ingestion/flows.py.
"""
from __future__ import annotations

import logging
import math

from db.session import get_conn
from resolution.resolver import norm_name_sql

logger = logging.getLogger(__name__)

# POIs closer than this are one place (a cemetery's chapel + gates, a park's
# entrances); farther apart they are distinct candidates (chains, Bezirk town
# halls) and need disambiguation.
_CLUSTER_M = 400.0

# The feed's latitude is consistently right even where its longitude is
# kilometres off — so a candidate cluster must agree with the feed latitude
# (within ~550 m) to win a disambiguation.
_FEED_LAT_TOL = 0.005

_POI_MATCH = f"""
    WITH cand AS (
        SELECT id, label, ST_X(geom) AS lng, ST_Y(geom) AS lat,
               {norm_name_sql("label")} AS nlabel
        FROM nodes
        WHERE node_type = 'POI' AND valid_to IS NULL
          AND geom IS NOT NULL AND label NOT LIKE 'OSM %'
    )
    SELECT label, lng, lat, (nlabel = {norm_name_sql("$1::text")}) AS exact
    FROM cand
    WHERE nlabel = {norm_name_sql("$1::text")}
       OR (length($1) >= 5 AND nlabel LIKE '%' || {norm_name_sql("$1::text")} || '%')
       OR (length($1) >= 8 AND similarity(nlabel, {norm_name_sql("$1::text")}) >= 0.55)
    ORDER BY exact DESC, length(label) ASC
    LIMIT 8
"""

# move the node and keep the original feed point + a correction flag; only
# touch nodes that are missing a geom or sit farther than min_offset_m away
_SNAP = """
    UPDATE nodes SET
        geom = ST_SetSRID(ST_MakePoint($2, $3), 4326),
        properties = properties || jsonb_strip_nulls(jsonb_build_object(
            'feed_geo', CASE WHEN geom IS NOT NULL AND NOT properties ? 'feed_geo'
                             THEN ST_AsGeoJSON(geom)::jsonb END,
            'geo_corrected', 'osm_poi'))
    WHERE valid_to IS NULL AND {target}
      AND (geom IS NULL
           OR ST_Distance(geom::geography,
                          ST_SetSRID(ST_MakePoint($2, $3), 4326)::geography) > $4)
"""
_SNAP_EVENTS = _SNAP.format(target="node_type = 'Event' AND properties->>'venue' = $1")
_SNAP_VENUE_ORG = _SNAP.format(target="source = 'event_venue' AND label = $1")

# a venue that no longer resolves (match logic evolved, POI vanished) gets its
# original feed point back — the pass must never leave a stale correction
_RESTORE = """
    UPDATE nodes SET
        geom = ST_SetSRID(ST_GeomFromGeoJSON(properties->>'feed_geo'), 4326),
        properties = properties - 'feed_geo' - 'geo_corrected'
    WHERE valid_to IS NULL AND properties ? 'feed_geo' AND {target}
"""
_RESTORE_EVENTS = _RESTORE.format(target="node_type = 'Event' AND properties->>'venue' = $1")
_RESTORE_VENUE_ORG = _RESTORE.format(target="source = 'event_venue' AND label = $1")


def _dist_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Equirectangular distance in metres — fine at city scale."""
    dx = (b[0] - a[0]) * 111_320 * math.cos(math.radians(a[1]))
    dy = (b[1] - a[1]) * 110_540
    return math.hypot(dx, dy)


def _clusters(points: list[tuple[float, float]]) -> list[list[tuple[float, float]]]:
    """Greedy single-linkage clustering at _CLUSTER_M (n ≤ 8, so O(n²) is fine)."""
    groups: list[list[tuple[float, float]]] = []
    for p in points:
        home = None
        for g in groups:
            if any(_dist_m(p, q) <= _CLUSTER_M for q in g):
                if home is None:
                    g.append(p)
                    home = g
                else:  # p bridges two clusters — merge them
                    home.extend(g)
                    g.clear()
        if home is None:
            groups.append([p])
    return [g for g in groups if g]


def _centroid(group: list[tuple[float, float]]) -> tuple[float, float]:
    return (
        sum(p[0] for p in group) / len(group),
        sum(p[1] for p in group) / len(group),
    )


def _pick_cluster(
    points: list[tuple[float, float, bool]], feed_lat: float | None
) -> tuple[float, float] | None:
    """Pick the winning cluster of candidate POIs (lng, lat, exact_name_match).

    One cluster → its centroid. Several → exact-name clusters outrank
    containment-only ones; then the feed latitude arbitrates (it is reliable
    even where the feed longitude is kilometres off); then a strict size
    majority. Still tied → None (don't guess)."""
    groups = _clusters([(p[0], p[1]) for p in points])
    exact_pts = {(p[0], p[1]) for p in points if p[2]}
    if len(groups) > 1 and exact_pts:
        with_exact = [g for g in groups if any(tuple(q) in exact_pts for q in g)]
        if with_exact:
            groups = with_exact
    if len(groups) > 1 and feed_lat is not None:
        agreeing = [g for g in groups if abs(_centroid(g)[1] - feed_lat) <= _FEED_LAT_TOL]
        if agreeing:
            groups = agreeing
    if len(groups) == 1:
        c = _centroid(groups[0])
    else:
        groups.sort(key=len, reverse=True)
        if len(groups[0]) <= len(groups[1]):
            return None
        c = _centroid(groups[0])
    # the winner must never contradict a known feed latitude — a name match
    # at the wrong latitude is a different place, not a correction
    if feed_lat is not None and abs(c[1] - feed_lat) > _FEED_LAT_TOL:
        return None
    return c


async def snap_event_geoms(min_offset_m: float = 150.0) -> dict[str, int]:
    """Snap every current event with a `venue` (and the venue's Organization
    node) to the position of the unambiguously matching OSM POI."""
    counts = {"venues": 0, "matched": 0, "ambiguous": 0, "events_moved": 0, "orgs_moved": 0}
    async with get_conn() as conn:
        venues = await conn.fetch(
            """
            SELECT properties->>'venue' AS venue
            FROM nodes
            WHERE node_type = 'Event' AND valid_to IS NULL
              AND coalesce(properties->>'venue', '') <> ''
            GROUP BY 1
            """
        )
        for v in venues:
            counts["venues"] += 1
            name = v["venue"]
            pois = await conn.fetch(_POI_MATCH, name)
            if not pois:
                await conn.execute(_RESTORE_EVENTS, name)
                await conn.execute(_RESTORE_VENUE_ORG, name)
                continue
            # the feed's latitude is trustworthy — use the venue's median feed
            # latitude to arbitrate between distant same-name candidates
            feed_lat = await conn.fetchval(
                """
                SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY
                    COALESCE((properties->'feed_geo'->'coordinates'->>1)::float,
                             ST_Y(geom)))
                FROM nodes
                WHERE node_type = 'Event' AND valid_to IS NULL
                  AND properties->>'venue' = $1 AND geom IS NOT NULL
                """,
                name,
            )
            picked = _pick_cluster(
                [(p["lng"], p["lat"], p["exact"]) for p in pois], feed_lat
            )
            if picked is None:
                counts["ambiguous"] += 1
                logger.info("geo snap: %r ambiguous (%d candidate POIs)", name, len(pois))
                await conn.execute(_RESTORE_EVENTS, name)
                await conn.execute(_RESTORE_VENUE_ORG, name)
                continue
            counts["matched"] += 1
            lng, lat = picked
            res = await conn.execute(_SNAP_EVENTS, name, lng, lat, min_offset_m)
            counts["events_moved"] += int(res.split()[-1])
            res = await conn.execute(_SNAP_VENUE_ORG, name, lng, lat, min_offset_m)
            counts["orgs_moved"] += int(res.split()[-1])
    logger.info("geo snap: %s", counts)
    return counts
