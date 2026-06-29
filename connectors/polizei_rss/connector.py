"""
Connector: Polizei NRW Dortmund — Pressemeldungen (RSS).

Source:  https://dortmund.polizei.nrw/presse/pressemitteilungen
Feed:    .../rss/all/14555/all/all/all
Access:  RSS 2.0 (authorized machine-readable channel)
License: public-authority content
Shape:   event_stream — fetch new items, dedupe by GUID

What this covers:
  - Police press releases: incidents, accidents, raids, demonstrations.
GDPR note (rule 5): releases name no private individuals; do NOT re-identify.
We store the release as an Event node with the text the authority published.
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

_FEED_URL = "https://dortmund.polizei.nrw/presse/pressemitteilungen/rss/all/14555/all/all/all"
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return _TAG_RE.sub("", text or "").strip()


def _make_source_id(guid: str) -> str:
    return hashlib.sha256(guid.encode()).hexdigest()[:20]


def _parse_pubdate(pub: str | None) -> datetime | None:
    """Polizei NRW emits ISO 8601 (2026-06-26T18:27:40+02:00); fall back to RFC822."""
    if not pub:
        return None
    try:
        return datetime.fromisoformat(pub)
    except (TypeError, ValueError):
        pass
    try:
        return parsedate_to_datetime(pub)
    except (TypeError, ValueError):
        return None


def _classify(title: str, description: str) -> str:
    blob = f"{title} {description}".lower()
    if any(k in blob for k in ("versammlung", "demonstration", "kundgebung", "aufzug")):
        return "demonstration"
    if any(k in blob for k in ("unfall", "verkehr", "kollision")):
        return "traffic_accident"
    if any(k in blob for k in ("razzia", "durchsuchung", "festnahme")):
        return "police_operation"
    return "police_report"


class PolizeiRssConnector(BaseConnector):
    shape = ConnectorShape.EVENT_STREAM
    source_name = "polizei_nrw_dortmund_rss"

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
            }

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        title = raw.get("title", "")
        description = _strip_html(raw.get("description", ""))
        guid = raw.get("guid") or raw.get("link", "")

        valid_from = _parse_pubdate(raw.get("pubDate"))

        return {
            "source_id": _make_source_id(guid),
            "label": title or "Polizeimeldung",
            "valid_from": valid_from,
            "event_type": _classify(title, description),
            "description": description,
            "link": raw.get("link", ""),
            "guid": guid,
        }

    async def emit_entities(self, normalized: dict[str, Any]) -> list[NodeBase]:
        prov = self._provenance(normalized["source_id"], normalized["link"] or _FEED_URL)
        node = Event(
            label=normalized["label"],
            valid_from=normalized["valid_from"],
            properties={
                "event_type": normalized["event_type"],
                "description": normalized["description"],
                "tags": ["polizei", "dortmund"],
            },
            **prov,
        )
        return [node]

    async def emit_edges(self, normalized: dict[str, Any], nodes: list[NodeBase]) -> list[EdgeBase]:
        # MENTIONS edges (release → road/geo) require NER over the text — defer
        # to the resolution/reasoning layer, not this raw connector.
        return []
