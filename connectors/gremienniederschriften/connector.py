"""
Connector: Dortmund Gremienniederschriften (council/committee meeting MINUTES).

Index:   Dortmund Open Data Portal — dataset fb1-gremienniederschriften (5055
         records back to 2013: date, Gremium, link to the full minutes document).
Docs:    rathaus.dortmund.de/dosys/doRat.nsf — the linked HTML minutes. These ARE
         robots-permitted: robots.txt has `User-Agent: * / Allow: /dosys/` and
         only blocks specific gremrech2.nsf document hashes (doRat.nsf is open).
License: index is DL-DE-Zero; minutes are public-authority records.
Shape:   event_stream — fetch index, parse only minutes not yet seen.

What this covers (the real politics layer, unlike fb1-gremientermine which is
upcoming dates only):
  - Meeting nodes (past sittings with their minutes document).
  - AgendaItem nodes (TOPs: number + title).
  - Resolution nodes (Beschlüsse keyed by Drucksachen-Nr, with best-effort vote
    outcome). Vote is only set when the minutes state it plainly; otherwise None
    (rule 4: factual only, never fabricate a decision).

Provenance: every node/edge carries source_url = the minutes document, so a
Resolution points at the actual record (rule 1).

Politeness: rate-limited; capped per run so a backfill spreads over several runs
(rule 5). The checkpoint stores seen documentIds.
"""

from __future__ import annotations

import asyncio
import html as htmllib
import re
from datetime import datetime, timezone
from typing import Any, AsyncGenerator
from urllib.parse import parse_qs, urlparse

from config import settings
from connectors.base import BaseConnector, ConnectorShape
from ontology.edges import EdgeBase, relates_to
from ontology.nodes import AgendaItem, Meeting, NodeBase, Resolution

_BASE = settings.opendata_dortmund_base_url
_DATASET = "fb1-gremienniederschriften"
_INDEX_SOURCE_URL = f"https://open-data.dortmund.de/explore/dataset/{_DATASET}/"
_RECORDS_URL = f"{_BASE}/catalog/datasets/{_DATASET}/records"
_PAGE_SIZE = 100

# Operational bounds (rule 5): polite delay between document fetches and a cap so
# the first 5055-doc backfill spreads across runs instead of one marathon.
_FETCH_DELAY_S = 1.0
_MAX_DOCS_PER_RUN = 150

_DRUCKSACHE = re.compile(r"(?:Dr\.|Drucksache)\s*Nr\.?\:?\s*(\d{3,}-\d{2})", re.I)
_TOP = re.compile(r"^(\d+(?:\.\d+){0,3})\s+(\S.*)$")
_VOTE = re.compile(
    r"(einstimmig|mehrheitlich|abgelehnt|zugestimmt|genehmigt|beschlossen)", re.I
)
_PASSED_WORDS = {"einstimmig", "mehrheitlich", "zugestimmt", "genehmigt", "beschlossen"}


def extract_document_id(link: str) -> str | None:
    """Pull the Domino documentId query param from a minutes link."""
    try:
        qs = parse_qs(urlparse(link).query)
        return qs.get("documentId", [None])[0]
    except (ValueError, TypeError):
        return None


def _clean_text(raw_html: str) -> str:
    t = re.sub(r"(?i)<br\s*/?>", "\n", raw_html)
    t = re.sub(r"(?i)</(p|div|tr|td|font|li|h\d)>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    return htmllib.unescape(t)


def _vote_to_passed(vote: str | None) -> bool | None:
    if vote is None:
        return None
    if vote == "abgelehnt":
        return False
    if vote in _PASSED_WORDS:
        return True
    return None


def parse_minutes(raw_html: str) -> list[dict[str, Any]]:
    """
    Parse a Domino minutes HTML doc into agenda items.

    Returns [{number, title, drucksachen: [{nr, vote, passed}]}]. Vote is scanned
    in the ~250-char window after each Drucksachen-Nr (the protocol states the
    outcome right after the number); absent → None.
    """
    text = _clean_text(raw_html)
    flat = re.sub(r"\s+", " ", text)

    # Outcome per Drucksachen-Nr, from the protocol section. The window after a
    # number is bounded by the NEXT Drucksachen-Nr and the first sentence end, so
    # a neighbouring Beschluss's outcome can't bleed onto this one.
    matches = list(_DRUCKSACHE.finditer(flat))
    vote_by_nr: dict[str, str | None] = {}
    for i, m in enumerate(matches):
        nr = m.group(1)
        hard_end = matches[i + 1].start() if i + 1 < len(matches) else len(flat)
        window = flat[m.end() : min(m.end() + 250, hard_end)]
        sentence_end = window.find(". ")
        if sentence_end != -1:
            window = window[: sentence_end + 1]
        found = _VOTE.search(window)
        if found and not vote_by_nr.get(nr):
            vote_by_nr[nr] = found.group(1).lower()
        vote_by_nr.setdefault(nr, None)

    # Agenda structure from the line-broken text.
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]

    agenda: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for ln in lines:
        m = _TOP.match(ln)
        if m and len(m.group(1)) <= 10:
            current = {"number": m.group(1), "title": m.group(2)[:200], "_nrs": set()}
            agenda.append(current)
        if current is not None:
            for d in _DRUCKSACHE.findall(ln):
                current["_nrs"].add(d)

    for item in agenda:
        drucksachen = []
        for nr in sorted(item.pop("_nrs")):
            vote = vote_by_nr.get(nr)
            drucksachen.append({"nr": nr, "vote": vote, "passed": _vote_to_passed(vote)})
        item["drucksachen"] = drucksachen
    return agenda


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


class GremienNiederschriftenConnector(BaseConnector):
    shape = ConnectorShape.EVENT_STREAM
    source_name = "ris_dortmund_niederschriften"

    async def fetch(self, checkpoint: dict[str, Any] | None = None) -> AsyncGenerator[Any, None]:
        seen: set[str] = set((checkpoint or {}).get("seen_document_ids", []))
        fetched = 0
        offset = 0
        done = False
        while not done:
            resp = await self._get(
                _RECORDS_URL,
                params={
                    "limit": _PAGE_SIZE,
                    "offset": offset,
                    "order_by": "datum DESC",
                },
            )
            data = resp.json()
            results = data.get("results", [])
            for record in results:
                link = record.get("link") or ""
                doc_id = extract_document_id(link)
                if not doc_id or doc_id in seen:
                    continue
                doc = await self._get(link)
                agenda = parse_minutes(doc.text)
                yield {
                    "document_id": doc_id,
                    "link": link,
                    "gremium": record.get("gremium") or "Unbekanntes Gremium",
                    "datum": record.get("datum") or "",
                    "agenda": agenda,
                }
                fetched += 1
                if fetched >= _MAX_DOCS_PER_RUN:
                    done = True
                    break
                await asyncio.sleep(_FETCH_DELAY_S)
            if offset + _PAGE_SIZE >= data.get("total_count", 0):
                break
            offset += _PAGE_SIZE

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        return {
            "document_id": raw["document_id"],
            "link": raw["link"],
            "gremium": raw["gremium"],
            "datum": raw["datum"],
            "valid_from": _parse_date(raw["datum"]),
            "label": f"{raw['gremium']} {raw['datum']}",
            "agenda": raw["agenda"],
        }

    def _agenda_source_id(self, document_id: str, number: str) -> str:
        return f"{document_id}#{number}"

    async def emit_entities(self, normalized: dict[str, Any]) -> list[NodeBase]:
        doc_id = normalized["document_id"]
        link = normalized["link"]
        nodes: list[NodeBase] = []

        meeting = Meeting(
            label=normalized["label"],
            valid_from=normalized["valid_from"],
            properties={
                "meeting_type": "protokoll",
                "gremium": normalized["gremium"],
                "datum": normalized["datum"],
                "agenda_url": link,
            },
            **self._provenance(doc_id, link),
        )
        nodes.append(meeting)

        seen_resolution_nrs: set[str] = set()
        for item in normalized["agenda"]:
            agenda_sid = self._agenda_source_id(doc_id, item["number"])
            # Single, plain outcome → record it on the AgendaItem too.
            votes = {d["vote"] for d in item["drucksachen"] if d["vote"]}
            result = votes.pop() if len(votes) == 1 else None
            nodes.append(
                AgendaItem(
                    label=f"TOP {item['number']} {item['title']}"[:200],
                    valid_from=normalized["valid_from"],
                    properties={
                        "number": item["number"],
                        "title": item["title"],
                        "public": None,
                        "result": result,
                    },
                    **self._provenance(agenda_sid, link),
                )
            )
            for d in item["drucksachen"]:
                nr = d["nr"]
                if nr in seen_resolution_nrs:
                    continue
                seen_resolution_nrs.add(nr)
                nodes.append(
                    Resolution(
                        label=f"Drucksache {nr}",
                        valid_from=normalized["valid_from"],
                        properties={
                            "resolution_number": nr,
                            "passed": d["passed"],
                            "vote": d["vote"],
                            "meeting_id": doc_id,
                            "text_url": link,
                        },
                        **self._provenance(nr, link),
                    )
                )
        return nodes

    async def emit_edges(self, normalized: dict[str, Any], nodes: list[NodeBase]) -> list[EdgeBase]:
        doc_id = normalized["document_id"]
        link = normalized["link"]
        by_sid = {n.source_id: n for n in nodes if n.source_id}
        meeting = by_sid.get(doc_id)
        if meeting is None:
            return []

        edges: list[EdgeBase] = []
        prov = {
            "source": self.source_name,
            "source_url": link,
            "observed_at": self._now(),
        }
        for item in normalized["agenda"]:
            agenda_node = by_sid.get(self._agenda_source_id(doc_id, item["number"]))
            if agenda_node is None:
                continue
            # AgendaItem belongs to the Meeting.
            edges.append(
                relates_to(
                    agenda_node.id, meeting.id,
                    properties={"relation": "agenda_of"}, **prov,
                )
            )
            # Each Beschluss was decided in this AgendaItem.
            for d in item["drucksachen"]:
                resolution_node = by_sid.get(d["nr"])
                if resolution_node is not None:
                    edges.append(
                        relates_to(
                            resolution_node.id, agenda_node.id,
                            properties={"relation": "decided_in"}, **prov,
                        )
                    )
        return edges
