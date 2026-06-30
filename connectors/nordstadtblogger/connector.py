"""
Connector: Nordstadtblogger — independent local Dortmund news (WordPress REST).

Source:  https://www.nordstadtblogger.de/
API:     https://www.nordstadtblogger.de/wp-json/wp/v2/posts  (?_embed for author
         + category names). robots.txt allows it (only one json path is blocked).
License: non-commercial independent outlet. We store only the published fields
         (headline, author, excerpt, category names) and link back to the
         article; we do NOT mirror full article bodies. Confirm reuse terms
         before any redistribution beyond this internal civic graph.
Shape:   event_stream — newest page each run, dedup by post id. With
         full_history=True it pages the whole archive (one-off backfill).

Why WP REST over the RSS feed: the feed only exposes the latest ~10 posts; the
REST API is paginated (X-WP-TotalPages) so we can backfill years of articles,
and it returns the same fields as structured JSON.

GDPR note (rule 5): store the published text as-is; do NOT re-identify private
individuals named in articles.

District linking: handled downstream by the text linker, which matches district
names in the headline + excerpt + the embedded category names (often a Stadtteil).
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import re
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from connectors.base import BaseConnector, ConnectorShape
from ontology.edges import EdgeBase
from ontology.nodes import Event, NodeBase

_API_URL = "https://www.nordstadtblogger.de/wp-json/wp/v2/posts"
_PER_PAGE = 100
_TAG_RE = re.compile(r"<[^>]+>")
_SUMMARY_MAX = 600


def _strip_html(text: str | None) -> str:
    return html.unescape(_TAG_RE.sub("", text or "")).strip()


def _make_source_id(post_id: str) -> str:
    return hashlib.sha256(f"nsb:{post_id}".encode()).hexdigest()[:20]


def _parse_wp_date(value: str | None) -> datetime | None:
    """WP date_gmt is naive ISO (UTC); attach tzinfo."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _embedded_author(post: dict[str, Any]) -> str:
    authors = (post.get("_embedded") or {}).get("author") or []
    if authors and isinstance(authors[0], dict):
        return authors[0].get("name") or "Nordstadtblogger-Redaktion"
    return "Nordstadtblogger-Redaktion"


def _embedded_categories(post: dict[str, Any]) -> list[str]:
    """Category names from the _embed payload (wp:term groups)."""
    out: list[str] = []
    for group in (post.get("_embedded") or {}).get("wp:term") or []:
        for term in group:
            if isinstance(term, dict) and term.get("taxonomy") == "category" and term.get("name"):
                out.append(term["name"])
    return out


class NordstadtbloggerConnector(BaseConnector):
    shape = ConnectorShape.EVENT_STREAM
    source_name = "nordstadtblogger"

    def __init__(self, full_history: bool = False, max_pages: int = 500) -> None:
        super().__init__()
        # full_history=True walks the whole archive (one-off backfill); the
        # default fetches only the newest page (the scheduled event-stream run).
        self.full_history = full_history
        self.max_pages = max_pages

    async def fetch(self, checkpoint: dict[str, Any] | None = None) -> AsyncGenerator[Any, None]:
        seen_ids: set[str] = set((checkpoint or {}).get("seen_ids", []))
        params = {"per_page": _PER_PAGE, "page": 1, "_embed": "1",
                  "orderby": "date", "order": "desc"}
        resp = await self._get(_API_URL, params=params)
        total_pages = int(resp.headers.get("X-WP-TotalPages", "1"))
        last_page = min(total_pages, self.max_pages) if self.full_history else 1

        for page in range(1, last_page + 1):
            if page > 1:
                await asyncio.sleep(0.5)  # polite rate-limit for the archive walk
                try:
                    resp = await self._get(_API_URL, params={**params, "page": page})
                except Exception:
                    # A transient WP error on one page shouldn't abort a 160-page
                    # archive walk — stop gracefully and keep what we have.
                    break
            posts = resp.json()
            if not isinstance(posts, list) or not posts:
                break
            for post in posts:
                pid = str(post.get("id"))
                if pid and pid in seen_ids:
                    continue
                yield post

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        pid = str(raw.get("id"))
        title = _strip_html((raw.get("title") or {}).get("rendered", ""))
        summary = _strip_html((raw.get("excerpt") or {}).get("rendered", ""))[:_SUMMARY_MAX]
        return {
            "source_id": _make_source_id(pid),
            "label": title or "Nordstadtblogger-Artikel",
            "valid_from": _parse_wp_date(raw.get("date_gmt") or raw.get("date")),
            "description": summary,
            "author": _embedded_author(raw),
            "categories": _embedded_categories(raw),
            "link": raw.get("link", ""),
            "post_id": pid,
        }

    async def emit_entities(self, normalized: dict[str, Any]) -> list[NodeBase]:
        prov = self._provenance(normalized["source_id"], normalized["link"] or _API_URL)
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
        # District/road links come from the text linker over headline + excerpt +
        # category names, not this raw connector.
        return []
