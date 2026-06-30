"""
OffeneRegister.de — open re-publication of the German Handelsregister (company
register), CC-BY 4.0 (attribution: OpenCorporates). The official handelsregister.de
is CAPTCHA-walled with restrictive terms (Avoid); this is the open path.

Shape: reference (slow full refresh). We stream the national line-delimited JSON
dump (~260 MB bz2), cheaply prefilter lines mentioning "Dortmund", and keep
companies whose registered city is Dortmund.

Companies enter as Organization nodes keyed by company_number (the deterministic
register id) — the entity-resolution anchor that links a tender winner / OSM
storefront to its legal entity. GDPR (rule 5 + the Person ontology, which is
public-officials-only): we DROP officers (private individuals) and store only the
organisational facts (name, register id, Rechtsform, status, address).
"""
from __future__ import annotations

import bz2
import json
import logging
import re
from typing import Any, AsyncGenerator

import httpx

from connectors.base import BaseConnector, ConnectorShape
from ontology.edges import EdgeBase
from ontology.nodes import NodeBase, Organization

logger = logging.getLogger(__name__)

_DUMP_URL = "https://daten.offeneregister.de/de_companies_ocdata.jsonl.bz2"
_PLZ = re.compile(r"\b(\d{5})\b")
_RECHTSFORM = [
    "gGmbH", "GmbH & Co. KG", "GmbH", "Gesellschaft mit beschränkter Haftung",
    "UG (haftungsbeschränkt)", "UG", "AG & Co. KG", "Aktiengesellschaft", "AG",
    "e. V.", "e.V.", "eingetragene Genossenschaft", "eG", "KGaA", "KG", "OHG",
    "GbR", "SE", "Stiftung", "Verein",
]


def parse_address(addr: str) -> tuple[str | None, str | None, str | None]:
    """'Waidmannstraße 1, 44137 Dortmund.' → (street, plz, city)."""
    addr = (addr or "").strip().rstrip(".")
    if not addr:
        return None, None, None
    street, _, loc = addr.partition(",")
    pm = _PLZ.search(loc or addr)
    plz = pm.group(1) if pm else None
    city = (loc.replace(plz, "").strip() if plz else "") or None
    return street.strip() or None, plz, city


def is_dortmund(addr: str) -> bool:
    _, _, city = parse_address(addr)
    return bool(city) and city.lower() == "dortmund"


def rechtsform(name: str) -> str | None:
    for rf in _RECHTSFORM:
        if name.endswith(rf) or f" {rf}" in name:
            return rf
    return None


class OffeneRegisterConnector(BaseConnector):
    shape = ConnectorShape.REFERENCE
    source_name = "offeneregister"

    def _dortmund_record(self, line: bytes) -> dict[str, Any] | None:
        if b"Dortmund" not in line:  # cheap prefilter — skip JSON parse
            return None
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            return None
        return rec if is_dortmund(rec.get("registered_address", "")) else None

    async def fetch(self, checkpoint: dict[str, Any] | None = None) -> AsyncGenerator[Any, None]:
        # The dump is a MULTI-STREAM bz2 (concatenated blocks); roll a fresh
        # decompressor at each stream boundary and feed it the leftover bytes.
        dec = bz2.BZ2Decompressor()
        tail = b""
        try:
            async with self._http.stream("GET", _DUMP_URL, timeout=600.0) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes(1 << 16):
                    while chunk:
                        out = dec.decompress(chunk)
                        data = tail + out
                        *lines, tail = data.split(b"\n")
                        for ln in lines:
                            rec = self._dortmund_record(ln)
                            if rec is not None:
                                yield rec
                        if dec.eof:
                            chunk = dec.unused_data
                            dec = bz2.BZ2Decompressor()
                        else:
                            chunk = b""
        except (httpx.RemoteProtocolError, httpx.StreamError, EOFError) as exc:
            # Large bulk download occasionally truncates near the end; keep the
            # ~99% we parsed rather than failing the whole run.
            logger.warning("offeneregister stream ended early (%s) — keeping parsed records", exc)
        rec = self._dortmund_record(tail)
        if rec is not None:
            yield rec

    def normalize(self, raw: Any) -> dict[str, Any]:
        name = raw.get("name") or ""
        street, plz, city = parse_address(raw.get("registered_address", ""))
        number = raw.get("company_number")
        return {
            "source_id": number,
            "label": name,
            "status": raw.get("current_status"),
            "rechtsform": rechtsform(name),
            "addr_street": street,
            "addr_postcode": plz,
            "addr_city": city,
            "source_url": f"https://offeneregister.de/company/{number}" if number else None,
        }

    async def emit_entities(self, normalized: dict[str, Any]) -> list[NodeBase]:
        if not normalized.get("source_id") or not normalized.get("label"):
            return []
        return [
            Organization(
                label=normalized["label"],
                properties={
                    "org_type": "company",
                    "register_id": normalized["source_id"],
                    "status": normalized.get("status"),
                    "rechtsform": normalized.get("rechtsform"),
                    "addr_street": normalized.get("addr_street"),
                    "addr_postcode": normalized.get("addr_postcode"),
                    "addr_city": normalized.get("addr_city"),
                },
                **self._provenance(normalized["source_id"], normalized["source_url"]),
            )
        ]

    async def emit_edges(self, normalized: dict[str, Any], nodes: list[NodeBase]) -> list[EdgeBase]:
        return []
