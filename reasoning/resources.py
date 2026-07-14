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

# Closed resource vocabulary — the join keys. Two layers:
#  - LOGISTICS: what event planning needs/offers (the original axis, used by bedarf)
#  - CAPABILITY/DOMAIN: what field an actor works in / can contribute — the axis
#    that makes actor-to-actor and problem→coalition matching specific instead of
#    collapsing onto generic logistics tags.
RESOURCES: set[str] = {
    # logistics
    "verpflegung", "getraenke", "uebernachtung", "transport", "parkraum",
    "publikum", "veranstaltungsflaeche", "technik", "sponsoring", "sanitaer",
    "kinderbetreuung", "unterhaltung", "sichtbarkeit", "einzelhandel",
    "reparatur", "ziel", "sicherheit", "erste_hilfe",
    # capability / domain
    "betreuung", "beratung", "bildung", "gesundheit", "integration",
    "begegnung", "kultur", "sport", "umwelt", "digital", "ehrenamt",
    "finanzierung",
}

# Near-universal tags carry little complementary signal on their own; kept in the
# vocab because bedarf/event planning still needs them, but callers may discount
# them (the complementary finder already drops tags held by >40% of actors).
GENERIC_TAGS: frozenset[str] = frozenset({"publikum", "sichtbarkeit", "ziel"})

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
    "marketplace": ["einzelhandel", "verpflegung"],
    "community_centre": ["veranstaltungsflaeche", "begegnung"],
    "events_venue": ["veranstaltungsflaeche", "ziel"],
    # capability/domain from amenity
    "social_facility": ["betreuung", "beratung"],
    "social_centre": ["begegnung", "beratung"],
    "school": ["bildung"], "college": ["bildung"], "university": ["bildung"],
    "library": ["bildung", "begegnung"], "language_school": ["bildung", "integration"],
    "music_school": ["bildung", "kultur"], "dentist": ["gesundheit"],
    "hospital": ["erste_hilfe", "gesundheit"], "clinic": ["erste_hilfe", "gesundheit"],
    "doctors": ["erste_hilfe", "gesundheit"], "place_of_worship": ["begegnung"],
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
    "park": ["veranstaltungsflaeche", "umwelt"],
    "sports_centre": ["veranstaltungsflaeche", "sport"],
    "stadium": ["veranstaltungsflaeche", "sport"], "pitch": ["veranstaltungsflaeche", "sport"],
    "garden": ["umwelt"], "fitness_centre": ["sport"],
}
_OFFICE_OFFERS: dict[str, list[str]] = {
    "educational_institution": ["bildung"], "ngo": ["ehrenamt", "beratung"],
    "association": ["ehrenamt"], "lawyer": ["beratung"], "tax_advisor": ["beratung"],
    "insurance": ["beratung"], "financial_advisor": ["beratung", "finanzierung"],
    "it": ["digital"], "telecommunication": ["digital"],
    "employment_agency": ["beratung"], "government": ["beratung"],
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
        (p.get("office"), _OFFICE_OFFERS),
    ):
        if value:
            offers.update(table.get(value, []))
    # any shop sells goods
    if p.get("shop") and "einzelhandel" not in offers:
        offers.add("einzelhandel")
    needs: set[str] = set()
    # sports clubs / Vereine: offer a venue + sport, seek sponsoring. No blanket
    # publikum/sichtbarkeit stamping — those near-universal tags (was 50-58% of
    # actors) drowned the specific signals and were then discarded by the
    # complementary finder anyway.
    if p.get("club") == "sport" or p.get("sport"):
        offers.update(["sport", "veranstaltungsflaeche"])
        needs.add("sponsoring")
    return sorted(needs), sorted(offers & RESOURCES)
