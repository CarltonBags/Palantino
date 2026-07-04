"""
Cluster-picking logic for the event geo snap — no DB required.

Points are (lng, lat, exact_name_match); Dortmund is ~7.4–7.7 E, 51.4–51.6 N.
"""

from resolution.geo_correction import _clusters, _dist_m, _pick_cluster

SUBROSA = (7.4484, 51.5214)
ZOO = (7.4716, 51.4722)


def test_dist_m_plausible() -> None:
    # Subrosa ↔ Zoo is ~5.7 km
    assert 5000 < _dist_m(SUBROSA, ZOO) < 6500


def test_clusters_merges_sub_pois() -> None:
    # a cemetery's chapel + gate + info sign, metres apart → one cluster
    pts = [(7.4700, 51.5450), (7.4705, 51.5452), (7.4710, 51.5448)]
    assert len(_clusters(pts)) == 1


def test_clusters_separates_distinct_places() -> None:
    assert len(_clusters([SUBROSA, ZOO])) == 2


def test_pick_single_cluster() -> None:
    pts = [(7.4700, 51.5450, True), (7.4705, 51.5452, False)]
    picked = _pick_cluster(pts, feed_lat=51.545)
    assert picked is not None and abs(picked[1] - 51.545) < 0.001


def test_pick_majority_cluster() -> None:
    # OSM duplicate: 3 POIs at the real site, 1 stray — majority wins
    pts = [
        (7.4207, 51.4983, True),
        (7.4204, 51.4985, False),
        (7.4195, 51.4987, False),
        (7.4393, 51.4977, True),
    ]
    picked = _pick_cluster(pts, feed_lat=51.4984)
    assert picked is not None and picked[0] < 7.43


def test_pick_rejects_lat_contradiction() -> None:
    # single name match, but 2.4 km north of where the feed puts the venue —
    # that's a different place, not a correction
    assert _pick_cluster([(7.3787, 51.5709, False)], feed_lat=51.5494) is None


def test_pick_refuses_tie() -> None:
    # two equal, distant candidates at the same latitude → don't guess
    pts = [(7.4207, 51.4983, True), (7.4393, 51.4980, True)]
    assert _pick_cluster(pts, feed_lat=51.4982) is None


def test_exact_outranks_containment_noise() -> None:
    # one exact match + two containment hits elsewhere (same lat) → exact wins
    pts = [
        (7.4652, 51.5113, True),
        (7.5300, 51.5115, False),
        (7.5302, 51.5114, False),
    ]
    picked = _pick_cluster(pts, feed_lat=51.5113)
    assert picked is not None and abs(picked[0] - 7.4652) < 0.001
