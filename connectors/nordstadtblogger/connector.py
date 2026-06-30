"""
Connector: Nordstadtblogger — independent local Dortmund news (RSS).

Source:  https://www.nordstadtblogger.de/
Feed:    https://www.nordstadtblogger.de/feed/  (WordPress RSS 2.0)
Access:  RSS feed the publisher offers (robots.txt allows; Disallow is empty).
License: non-commercial independent outlet. We store only what the feed
         publishes (headline, author, summary excerpt, categories) and link back
         to the article; we do NOT deep-scrape full article bodies. Confirm reuse
         terms before any redistribution beyond this internal civic graph.
Shape:   event_stream — fetch new items, dedupe by GUID/link.

What this covers:
  - Civic/political local journalism (the signal layer missing from the official
    feeds): council debates, neighbourhood issues, transport, culture.
GDPR note (rule 5): store the published text as-is; do NOT re-identify private
individuals named in articles.

District linking: handled downstream by the text linker, which matches district
names in the headline + summary. (Feed categories often name a Stadtteil too;
kept in properties for later use.)
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, AsyncGenerator
from xml.etree import ElementTree as ET

from connectors.base import BaseConnector, ConnectorShape
from ontology.edges import EdgeBase
from ontology.nodes import Event, NodeBase

_FEED_URL = "https://www.nordstadtblogger.de/feed/"
_DC_CREATOR = "{http://purl.org/dc/elements/1.1/}creator"
_TAG_RE = re.compile(r"<[^>]+>")
# Trim the summary so we keep a usable excerpt, not the whole rendered post.
_SUMMARY_MAX = 600


def _strip_html(text: str | None) -> str:
    return _TAG_RE.sub("", text or "").strip()


def _make_source_id(guid: str) -> str:
    return hashlib.sha256(guid.encode()).hexdigest()[:20]


def _parse_pubdate(pub: str | None) -> datetime | None:
    """WordPress emits RFC 822 (Mon, 29 Jun 2026 22:00:30 +0000); fall back to ISO."""
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


class NordstadtbloggerConnector(BaseConnector):
    shape = ConnectorShape.EVENT_STREAM
    source_name = "nordstadtblogger"

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
        title = raw.get("title", "")
        guid = raw.get("guid") or raw.get("link", "")
        summary = _strip_html(raw.get("description", ""))[:_SUMMARY_MAX]
        return {
            "source_id": _make_source_id(guid),
            "label": title or "Nordstadtblogger-Artikel",
            "valid_from": _parse_pubdate(raw.get("pubDate")),
            "description": summary,
            "author": raw.get("creator") or "Nordstadtblogger-Redaktion",
            "categories": raw.get("categories", []),
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
                "tags": ["nordstadtblogger", "news", "dortmund"],
            },
            **prov,
        )
        return [node]

    async def emit_edges(self, normalized: dict[str, Any], nodes: list[NodeBase]) -> list[EdgeBase]:
        # District/road links come from the text linker over headline + summary,
        # not this raw connector.
        return []
