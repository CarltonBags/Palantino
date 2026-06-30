"""
Connector: Wir in Dortmund — local news portal (RSS).

Source:  https://www.wirindortmund.de/
Feed:    https://www.wirindortmund.de/feed  (WordPress RSS 2.0)
Access:  publisher-offered RSS feed. robots.txt allows the feed but DISALLOWS
         /archiv/, so we take only the live feed (newest ~25), never the
         historical archive — respecting that opt-out.
License: store the published fields (headline, author, summary excerpt,
         categories) + a backlink; do NOT mirror full article bodies. Confirm
         reuse terms before any redistribution beyond this internal civic graph.
Shape:   event_stream — fetch new items, dedupe by GUID.

What this covers:
  - A second independent local-news signal (events, business, neighbourhood,
    culture) complementing Nordstadtblogger.
GDPR note (rule 5): store the published text as-is; do NOT re-identify private
individuals named in articles.

District linking: handled downstream by the text linker over headline + excerpt
+ category names.
"""

from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, AsyncGenerator
from xml.etree import ElementTree as ET

from connectors.base import BaseConnector, ConnectorShape
from ontology.edges import EdgeBase
from ontology.nodes import Event, NodeBase

_FEED_URL = "https://www.wirindortmund.de/feed"
_DC_CREATOR = "{http://purl.org/dc/elements/1.1/}creator"
_TAG_RE = re.compile(r"<[^>]+>")
_SUMMARY_MAX = 600
# Generic feed tags that carry no district/topic signal — dropped from categories.
_NOISE_CATEGORIES = {"Startseite", "Allgemein", "Dortmund"}


def _strip_html(text: str | None) -> str:
    return html.unescape(_TAG_RE.sub("", text or "")).strip()


def _make_source_id(guid: str) -> str:
    return hashlib.sha256(f"wid:{guid}".encode()).hexdigest()[:20]


def _parse_pubdate(pub: str | None) -> datetime | None:
    if not pub:
        return None
    try:
        return parsedate_to_datetime(pub)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(pub)
    except (TypeError, ValueError):
        return None


class WirInDortmundConnector(BaseConnector):
    shape = ConnectorShape.EVENT_STREAM
    source_name = "wirindortmund"

    async def fetch(self, checkpoint: dict[str, Any] | None = None) -> AsyncGenerator[Any, None]:
        seen_guids: set[str] = set((checkpoint or {}).get("seen_guids", []))
        resp = await self._get(_FEED_URL)
        root = ET.fromstring(resp.text)
        for item in root.iter("item"):
            guid = (item.findtext("guid") or item.findtext("link") or "").strip()
            if guid and guid in seen_guids:
                continue
            yield {
                "guid": guid,
                "title": (item.findtext("title") or "").strip(),
                "link": (item.findtext("link") or "").strip(),
                "description": item.findtext("description") or "",
                "pubDate": (item.findtext("pubDate") or "").strip(),
                "creator": (item.findtext(_DC_CREATOR) or "").strip(),
                "categories": [c.text.strip() for c in item.findall("category") if c.text],
            }

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        title = _strip_html(raw.get("title", ""))
        guid = raw.get("guid") or raw.get("link", "")
        cats = [c for c in raw.get("categories", []) if c not in _NOISE_CATEGORIES]
        return {
            "source_id": _make_source_id(guid),
            "label": title or "Wir-in-Dortmund-Artikel",
            "valid_from": _parse_pubdate(raw.get("pubDate")),
            "description": _strip_html(raw.get("description", ""))[:_SUMMARY_MAX],
            "author": raw.get("creator") or "Wir in Dortmund",
            "categories": cats,
            "link": raw.get("link", ""),
            "guid": guid,
        }

    async def emit_entities(self, normalized: dict[str, Any]) -> list[NodeBase]:
        prov = self._provenance(normalized["source_id"], normalized["link"] or _FEED_URL)
        node = Event(
            label=normalized["label"],
            valid_from=normalized["valid_from"],
            properties={
                "event_type": "news",
                "description": normalized["description"],
                "author": normalized["author"],
                "categories": normalized["categories"],
                "tags": ["wirindortmund", "news", "dortmund"],
            },
            **prov,
        )
        return [node]

    async def emit_edges(self, normalized: dict[str, Any], nodes: list[NodeBase]) -> list[EdgeBase]:
        return []
