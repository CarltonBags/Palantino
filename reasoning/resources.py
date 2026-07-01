"""
Resource/capability layer for COMPLEMENTARY synergies (need ↔ offer).

A third synergy axis beyond similarity (vectors) and proximity (PostGIS): match
what an actor NEEDS against what another OFFERS. A bike tour needs a rest/
refreshment stop; a beer festival or a café offers it. To make needs and offers
actually join, both come from one CLOSED vocabulary.

- POI offers/needs are derived deterministically from OSM tags (cheap, no LLM).
- Event needs/offers are tagged by the LLM (free text → vocab); see scanner.
Tags land in the node_resources side table (kind = 'need' | 'offer').
"""
from __future__ import annotations

from typing import Any

# Closed resource vocabulary — the join keys. Keep small + stable.
RESOURCES: set[str] = {
    "verpflegung", "getraenke", "uebernachtung", "transport", "parkraum",
    "publikum", "veranstaltungsflaeche", "technik", "sponsoring", "sanitaer",
    "kinderbetreuung", "unterhaltung", "sichtbarkeit", "einzelhandel",
    "reparatur", "ziel", "sicherheit", "erste_hilfe",
}

# OSM tag value → offered resources.
_AMENITY_OFFERS: dict[str, list[str]] = {
    "restaurant": ["verpflegung", "ziel"], "cafe": ["verpflegung", "getraenke", "ziel"],
    "fast_food": ["verpflegung"], "biergarten": ["verpflegung", "getraenke", "ziel"],
    "pub": ["getraenke", "ziel"], "bar": ["getraenke", "ziel"],
    "ice_cream": ["verpflegung"], "food_court": ["verpflegung"],
    "toilets": ["sanitaer"], "parking": ["parkraum"],
    "hospital": ["erste_hilfe"], "clinic": ["erste_hilfe"], "doctors": ["erste_hilfe"],
    "pharmacy": ["erste_hilfe"], "bicycle_repair_station": ["reparatur"],
    "fuel": ["transport"], "charging_station": ["transport"],
    "theatre": ["unterhaltung", "ziel"], "cinema": ["unterhaltung", "ziel"],
    "arts_centre": ["unterhaltung", "ziel"], "nightclub": ["unterhaltung", "ziel"],
    "kindergarten": ["kinderbetreuung"], "childcare": ["kinderbetreuung"],
    "marketplace": ["einzelhandel", "verpflegung"], "community_centre": ["veranstaltungsflaeche"],
    "events_venue": ["veranstaltungsflaeche", "ziel"],
}
_SHOP_OFFERS: dict[str, list[str]] = {
    "bakery": ["verpflegung"], "butcher": ["verpflegung"], "greengrocer": ["verpflegung"],
    "supermarket": ["verpflegung", "getraenke", "einzelhandel"],
    "convenience": ["verpflegung", "getraenke"], "beverages": ["getraenke"],
    "bicycle": ["reparatur", "einzelhandel"], "deli": ["verpflegung"],
    "confectionery": ["verpflegung"],
}
_TOURISM_OFFERS: dict[str, list[str]] = {
    "hotel": ["uebernachtung"], "hostel": ["uebernachtung"], "guest_house": ["uebernachtung"],
    "motel": ["uebernachtung"], "apartment": ["uebernachtung"],
    "attraction": ["ziel", "unterhaltung"], "museum": ["ziel", "unterhaltung"],
    "gallery": ["ziel", "unterhaltung"], "theme_park": ["ziel", "unterhaltung"],
}
_LEISURE_OFFERS: dict[str, list[str]] = {
    "park": ["veranstaltungsflaeche", "ziel"], "sports_centre": ["veranstaltungsflaeche", "ziel"],
    "stadium": ["veranstaltungsflaeche", "ziel"], "pitch": ["veranstaltungsflaeche"],
    "garden": ["ziel"], "fitness_centre": ["ziel"],
}


def poi_resources(properties: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Derive (needs, offers) for a POI from its OSM tags. Deterministic."""
    p = properties or {}
    offers: set[str] = set()
    for value, table in (
        (p.get("amenity"), _AMENITY_OFFERS),
        (p.get("shop"), _SHOP_OFFERS),
        (p.get("tourism"), _TOURISM_OFFERS),
        (p.get("leisure"), _LEISURE_OFFERS),
    ):
        if value:
            offers.update(table.get(value, []))
    # any shop sells goods; any commercial POI wants foot traffic + visibility
    if p.get("shop") and "einzelhandel" not in offers:
        offers.add("einzelhandel")
    needs: set[str] = set()
    if p.get("shop") or p.get("amenity") in {"restaurant", "cafe", "pub", "bar", "biergarten"}:
        needs.update(["publikum", "sichtbarkeit"])
    return sorted(needs), sorted(offers & RESOURCES)
