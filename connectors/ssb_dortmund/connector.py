"""
StadtSportBund Dortmund (ssb-do.de) — the city's authoritative sports-club
directory (~530 Vereine across 12 districts).

Shape: reference (slow full refresh). Public directory, no login/paywall/CAPTCHA.
No robots.txt and no data-reuse terms on the site (only a cookie notice); we
rate-limit and identify the bot honestly via the configured User-Agent.

GDPR (rule 5): the page lists a named Ansprechpartner per club — an identifiable
private individual. We DROP that name and store only ORGANISATIONAL facts: club
name, sport flag, address, and the club's own email/phone/website. Clubs enter as
POI nodes tagged club=sport so they participate in proximity + complementary
synergies (a Verein offers Publikum/Fläche, needs Sponsoring).
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, AsyncGenerator

from bs4 import BeautifulSoup

from connectors.base import BaseConnector, ConnectorShape
from ontology.edges import EdgeBase
from ontology.nodes import NodeBase, POI

logger = logging.getLogger(__name__)

_BASE = "https://www.ssb-do.de"
_INDEX = _BASE + "/startseite/vereine/vereinssuche/von_a___z?index={letter}"
_LETTERS = "abcdefghijklmnopqrstuvwxyz"
_PLZ = re.compile(r"\b(\d{5})\b")


def parse_club(block_html: str) -> dict[str, Any] | None:
    """Pure parser: one club's HTML block → normalized dict. None if not a club.
    Drops the personal Ansprechpartner name (GDPR)."""
    soup = BeautifulSoup(block_html, "html.parser")
    head = soup.find("h2")
    link = head.find("a") if head else None
    if not link:
        return None
    name = link.get_text(strip=True)
    href = link.get("href", "")
    m = re.search(r"[?&]id=(\d+)", href)
    club_id = m.group(1) if m else None
    if not name or not club_id:
        return None

    p = soup.find("p")
    # contact block text WITHOUT the <strong>person</strong> (drop it entirely)
    if p:
        for strong in p.find_all("strong"):
            strong.decompose()
    text = p.get_text("\n", strip=True) if p else ""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]

    addr_street = addr_postcode = addr_city = None
    for ln in lines:
        pm = _PLZ.search(ln)
        if pm and "," in ln:  # "Street 7, 44149 Dortmund"
            street_part, _, loc = ln.partition(",")
            addr_street = street_part.strip()
            addr_postcode = pm.group(1)
            addr_city = loc.replace(pm.group(1), "").strip() or "Dortmund"
            break

    phone = None
    for ln in lines:
        if ln.lower().startswith("tel"):
            phone = ln.split(":", 1)[-1].split("oder")[0].strip() or None
            break

    email = website = None
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if h.startswith("mailto:") and not email:
            email = h[len("mailto:"):].strip()
        elif h.startswith("http") and "ssb-do.de" not in h and not website:
            website = h.strip()

    return {
        "source_id": club_id,
        "label": name,
        "addr_street": addr_street,
        "addr_postcode": addr_postcode,
        "addr_city": addr_city,
        "phone": phone,
        "email": email,
        "website": website,
        "source_url": f"{_BASE}/startseite/vereine/vereinssuche/von_a___z?id={club_id}",
    }


class SSBDortmundConnector(BaseConnector):
    shape = ConnectorShape.REFERENCE
    source_name = "ssb_dortmund"

    async def fetch(self, checkpoint: dict[str, Any] | None = None) -> AsyncGenerator[Any, None]:
        seen: set[str] = set()
        for letter in _LETTERS:
            try:
                resp = await self._get(_INDEX.format(letter=letter))
            except Exception as exc:  # one bad page shouldn't abort the crawl
                logger.warning("ssb letter %s failed: %s", letter, exc)
                continue
            # split the listing into per-club blocks (each starts at an <h2>)
            parts = re.split(r"(?=<h2)", resp.text)
            for part in parts:
                if "?id=" not in part or "mailto:" not in part:
                    continue
                club = parse_club(part)
                if club and club["source_id"] not in seen:
                    seen.add(club["source_id"])
                    yield part  # raw block → normalize re-parses (testable)
            await asyncio.sleep(1.0)  # be polite

    def normalize(self, raw: Any) -> dict[str, Any]:
        return parse_club(raw) or {}

    async def emit_entities(self, normalized: dict[str, Any]) -> list[NodeBase]:
        if not normalized.get("source_id"):
            return []
        props = {
            "club": "sport",
            "addr_street": normalized.get("addr_street"),
            "addr_postcode": normalized.get("addr_postcode"),
            "addr_city": normalized.get("addr_city"),
            "email": normalized.get("email"),
            "phone": normalized.get("phone"),
            "website": normalized.get("website"),
        }
        return [
            POI(
                label=normalized["label"],
                geom=None,
                properties=props,
                **self._provenance(normalized["source_id"], normalized["source_url"]),
            )
        ]

    async def emit_edges(self, normalized: dict[str, Any], nodes: list[NodeBase]) -> list[EdgeBase]:
        return []
