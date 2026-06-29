"""
Connector: Vergabe.NRW public tenders (Vergabemarktplatz Metropole Ruhr).

Source:  open.nrw dataset "Ausschreibungen des Vergabemarktplatzes NRW"
Feed:    https://www.vergabe.metropoleruhr.de/VMPSatellite/opendata?id=...
Format:  ZIP of eForms UBL ContractNotice XML files
License: DL-DE-Zero
Shape:   reference — full ZIP each run; dedupe by ContractFolderID

What this covers (links council resolution → tender → awarded company):
  - Tender title, type, buyer, location, value, submission deadline.
We FILTER to Dortmund: keep a notice only if the buyer's city is Dortmund.
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import datetime
from typing import Any, AsyncGenerator

from connectors.base import BaseConnector, ConnectorShape
from ontology.edges import EdgeBase
from ontology.nodes import NodeBase, Tender

# Metropole Ruhr satellite (covers Dortmund). From open.nrw CKAN resource list.
_ZIP_URL = (
    "https://www.vergabe.metropoleruhr.de/VMPSatellite/opendata"
    "?id=4985b1a5-d2e8-4611-a4c6-20694a130bb3"
)
_NS = {
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
}
# eForms notice subtype → coarse tender_type
_SUBTYPE = {"16": "works", "17": "services", "18": "supplies"}


def _first(text: str, pattern: str) -> str | None:
    m = re.search(pattern, text, re.S)
    return m.group(1).strip() if m else None


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    # eForms dates carry a TZ offset, e.g. 2026-07-14+02:00
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _buyer_city(xml: str) -> str | None:
    return _first(xml, r"<cbc:CityName[^>]*>(.*?)</cbc:CityName>")


def parse_notice(xml: str) -> dict[str, Any] | None:
    """Parse one eForms ContractNotice into a flat dict, or None if not Dortmund."""
    if "Dortmund" not in xml:
        return None

    folder_id = _first(xml, r"<cbc:ContractFolderID[^>]*>(.*?)</cbc:ContractFolderID>")
    if not folder_id:
        return None

    # Project block holds the human title + classification.
    project = _first(xml, r"<cac:ProcurementProject>(.*?)</cac:ProcurementProject>") or ""
    title = _first(project, r"<cbc:Name[^>]*>(.*?)</cbc:Name>") or "Bekanntmachung"
    cpv = _first(project, r"<cbc:ItemClassificationCode[^>]*>(.*?)</cbc:ItemClassificationCode>")
    subtype = _first(xml, r"<cbc:SubTypeCode[^>]*>(.*?)</cbc:SubTypeCode>")
    issue = _first(xml, r"<cbc:IssueDate[^>]*>(.*?)</cbc:IssueDate>")
    deadline = _first(xml, r"<cbc:EndDate[^>]*>(.*?)</cbc:EndDate>")
    amount = _first(xml, r"<cbc:EstimatedOverallContractAmount[^>]*>(.*?)</cbc:Estimated")

    return {
        "folder_id": folder_id,
        "title": title,
        "tender_type": _SUBTYPE.get(subtype or "", "other"),
        "cpv": cpv,
        "subtype": subtype,
        "buyer_city": _buyer_city(xml),
        "issue_date": issue,
        "deadline": deadline,
        "value_eur": amount,
    }


class VergabeNrwConnector(BaseConnector):
    shape = ConnectorShape.REFERENCE
    source_name = "vergabe_nrw_metropole_ruhr"

    async def fetch(self, checkpoint: dict[str, Any] | None = None) -> AsyncGenerator[Any, None]:
        resp = await self._get(_ZIP_URL)
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            for name in zf.namelist():
                if not name.lower().endswith(".xml"):
                    continue
                xml = zf.read(name).decode("utf-8", errors="replace")
                parsed = parse_notice(xml)
                if parsed is not None:
                    parsed["_file"] = name
                    yield parsed

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        return {
            "source_id": raw["folder_id"],
            "label": raw["title"][:200],
            "valid_from": _parse_date(raw.get("issue_date")),
            "tender_type": raw.get("tender_type"),
            "cpv": raw.get("cpv"),
            "buyer_city": raw.get("buyer_city"),
            "deadline": raw.get("deadline"),
            "value_eur": raw.get("value_eur"),
        }

    async def emit_entities(self, normalized: dict[str, Any]) -> list[NodeBase]:
        source_url = (
            "https://www.vergabe.metropoleruhr.de/VMPSatellite/"
            f"notice/{normalized['source_id']}"
        )
        prov = self._provenance(normalized["source_id"], source_url)
        node = Tender(
            label=normalized["label"],
            valid_from=normalized["valid_from"],
            properties={
                "tender_type": normalized["tender_type"],
                "cpv": normalized["cpv"],
                "buyer_city": normalized["buyer_city"],
                "deadline": normalized["deadline"],
                "value_eur": normalized["value_eur"],
            },
            **prov,
        )
        return [node]

    async def emit_edges(self, normalized: dict[str, Any], nodes: list[NodeBase]) -> list[EdgeBase]:
        # AWARDED_TO (tender → winning company) appears only in award notices and
        # needs entity resolution against business nodes — defer to resolution layer.
        return []
