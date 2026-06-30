"""
Connector: dortmund.de Veranstaltungskalender (public events).

Source:  https://www.dortmund.de/dortmund-erleben/veranstaltungskalender/
API:     POST https://www.dortmund.de/api/search/proxy/search  (Elasticsearch-
         backed site search proxy; events are docs of type "eventdatetime")
Access:  public, robots.txt has NO Disallow; works with our honest bot UA.
License: public-authority content (city portal).
Shape:   reference — paginated full pull, dedupe by event-date id

What this covers (the actual fairs / festivals / concerts / markets calendar):
  - Event nodes (event_type="public_event") with title, start datetime, category
    (Konzert/Musik, Fest, …), venue + address, a Point geometry, Stadtbezirk,
    detail URL, cancelled/sold-out/free.
Notes:
  - Earlier docs said this was CAPTCHA-walled — that was the pre-relaunch site.
    The relaunched portal serves events via this open search proxy.
  - Each "eventdatetime" doc is one dated occurrence; recurring events appear
    once per date. The feed DOES carry venue coordinates in locationAddress.geo
    (lat/lng), so events get an exact Point and the flow snaps each to its
    statistischer Bezirk via ST_Within. The Stadtbezirk tag is a coarse fallback
    for the rare event whose coords are missing/outside Dortmund.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, AsyncGenerator

from connectors.base import BaseConnector, ConnectorShape
from ontology.edges import EdgeBase
from ontology.nodes import Event, NodeBase

_SEARCH_URL = "https://www.dortmund.de/api/search/proxy/search"
_BASE_URL = "https://www.dortmund.de"
_PAGE_SIZE = 100
# The ES proxy paginates a mixed doc stream (events + news + pages) by from+size
# and rejects offset+size > 10000 (Elasticsearch's default result window) with a
# 500. ~5.4k of the reachable docs are events. Cap generously above that and stop
# before the window so we ingest every reachable event without crashing. (A
# size-3000 cap previously truncated the feed, dropping whole venues like FZW.)
_MAX_RESULT_WINDOW = 10000
_MAX_EVENTS_PER_RUN = 8000
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str | None) -> str | None:
    if not text:
        return None
    return _TAG_RE.sub("", text).strip() or None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    # e.g. "2027-06-12T10:00:00.000000+0200" — normalise +0200 → +02:00.
    fixed = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", value)
    try:
        return datetime.fromisoformat(fixed)
    except (ValueError, TypeError):
        m = re.match(r"(\d{4}-\d{2}-\d{2})", value)
        return datetime.fromisoformat(m.group(1)) if m else None


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


# Rough Dortmund bounding box — guard against stray/0,0 or out-of-city coords.
_DTM_BBOX = (7.30, 51.40, 7.70, 51.62)  # (min_lng, min_lat, max_lng, max_lat)


def _in_dortmund(lng: float | None, lat: float | None) -> bool:
    if lng is None or lat is None:
        return False
    return _DTM_BBOX[0] <= lng <= _DTM_BBOX[2] and _DTM_BBOX[1] <= lat <= _DTM_BBOX[3]


def _tag_by_parent(content_tags: list[dict[str, Any]], parent: str) -> str | None:
    """First contentTag whose parentValues contain `parent` (e.g. 'Stadtbezirke')."""
    for tag in content_tags or []:
        if parent in (tag.get("parentValues") or []):
            return tag.get("value")
    return None


def parse_event(source: dict[str, Any]) -> dict[str, Any] | None:
    """Flatten one eventdatetime ES doc into our intermediate schema, or None."""
    if source.get("type") != "eventdatetime":
        return None
    content = source.get("content") or []
    if not content:
        return None
    data = content[0].get("data", {})
    event = data.get("event", {})
    title = event.get("title")
    if not title:
        return None

    tags = source.get("contentTags") or []
    website_url = source.get("website_url") or ""

    # Venue location: the feed carries an explicit point in locationAddress.geo
    # (lat/lng as strings), plus a street address. Use it for an exact Point.
    loc = event.get("locationAddress") or {}
    geo = loc.get("geo") or {}
    lat = _to_float(geo.get("lat"))
    lng = _to_float(geo.get("lng"))
    if not _in_dortmund(lng, lat):
        lat = lng = None

    return {
        "source_id": source.get("id"),
        "title": title,
        "description": _strip_html(event.get("description") or event.get("text")),
        "category": _tag_by_parent(tags, "Veranstaltungskalender"),
        "stadtbezirk": _tag_by_parent(tags, "Stadtbezirke"),
        "venue": loc.get("title"),
        "street": " ".join(p for p in (loc.get("street"), loc.get("houseNumber")) if p) or None,
        "postcode": loc.get("postcode"),
        "lat": lat,
        "lng": lng,
        "start_datetime": data.get("startDateTime") or (data.get("startCalendarDay") or {}).get("date"),
        "is_cancelled": bool(data.get("isCancelled")),
        "is_sold_out": bool(data.get("isSoldOut")),
        "free_of_charge": bool(data.get("freeOfCharge")),
        "url": f"{_BASE_URL}{website_url}" if website_url.startswith("/") else website_url,
    }


class DortmundEventsConnector(BaseConnector):
    shape = ConnectorShape.REFERENCE
    source_name = "dortmund_veranstaltungskalender"

    async def fetch(self, checkpoint: dict[str, Any] | None = None) -> AsyncGenerator[Any, None]:
        offset = 0
        emitted = 0
        while emitted < _MAX_EVENTS_PER_RUN and offset + _PAGE_SIZE <= _MAX_RESULT_WINDOW:
            resp = await self._post(
                _SEARCH_URL,
                json={"query": "*", "from": offset, "size": _PAGE_SIZE},
                headers={"Content-Type": "application/json"},
            )
            hits = resp.json().get("hits", {}).get("hits", [])
            if not hits:
                break
            for hit in hits:
                src = hit.get("_source", {})
                if src.get("type") == "eventdatetime":
                    yield src
                    emitted += 1
                    if emitted >= _MAX_EVENTS_PER_RUN:
                        break
            offset += _PAGE_SIZE

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        parsed = parse_event(raw) or {}
        return {
            **parsed,
            "label": (parsed.get("title") or "Veranstaltung")[:200],
            "valid_from": _parse_dt(parsed.get("start_datetime")),
        }

    async def emit_entities(self, normalized: dict[str, Any]) -> list[NodeBase]:
        if not normalized.get("source_id"):
            return []
        prov = self._provenance(normalized["source_id"], normalized.get("url"))
        lat, lng = normalized.get("lat"), normalized.get("lng")
        geom = {"type": "Point", "coordinates": [lng, lat]} if lat and lng else None
        node = Event(
            label=normalized["label"],
            valid_from=normalized["valid_from"],
            geom=geom,
            properties={
                "event_type": "public_event",
                "category": normalized.get("category"),
                "stadtbezirk": normalized.get("stadtbezirk"),
                "venue": normalized.get("venue"),
                "street": normalized.get("street"),
                "postcode": normalized.get("postcode"),
                "description": normalized.get("description"),
                "is_cancelled": normalized.get("is_cancelled"),
                "is_sold_out": normalized.get("is_sold_out"),
                "free_of_charge": normalized.get("free_of_charge"),
                "tags": ["veranstaltung", "dortmund"],
            },
            **prov,
        )
        return [node]

    async def emit_edges(self, normalized: dict[str, Any], nodes: list[NodeBase]) -> list[EdgeBase]:
        # The Stadtbezirk → GeoArea link is handled by the text linker (Event is a
        # text node type), which matches the district in the title/description.
        return []
