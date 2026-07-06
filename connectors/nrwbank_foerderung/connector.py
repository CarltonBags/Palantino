"""
Connector: NRW.BANK Förderprodukte (funding programs, Land NRW + passed-through
Bund programs).

Source:  https://www.nrwbank.de/de/foerderung/foerderprodukte/…
Access:  Polite crawl via the public sitemap (robots.txt allows current
         products, only /programmarchiv/ is disallowed; honest UA; ~2 s delay).
License: public institutional information; we store the structured facts +
         a 2-sentence summary, always linking the source page.
Shape:   reference — full sitemap sweep per run; dedupe by product id.

The canonical Bund directory (foerderdatenbank.de) sits behind Radware bot
protection — rule 5 forbids crossing it; NRW.BANK covers Land NRW and passes
through the major Bund products (ERP, BAFA, KfW-adjacent).

Each page is normalised by the fast LLM into the FundingProgram schema
(level, target_groups from a closed vocabulary, funding_type, amount,
deadline) — the offer side of funding↔actor matching.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncGenerator
from typing import Any

from bs4 import BeautifulSoup

from connectors.base import BaseConnector, ConnectorShape
from ontology.edges import EdgeBase
from ontology.nodes import FundingProgram, NodeBase
from reasoning.llm import complete, fast_model
from reasoning.prompts import FOERDERUNG_NORM_PROMPT, FOERDERUNG_NORM_SYSTEM

_SITEMAP = "https://www.nrwbank.de/sitemap.xml"
_PRODUCT_RE = re.compile(r"/de/foerderung/foerderprodukte/(\d+)/[^<]*\.html")
_CRAWL_DELAY_S = 2.0

_TARGET_GROUPS = {
    "gruendung", "kmu", "unternehmen", "verein_gemeinnuetzig", "kommune",
    "kultur", "sozial", "wohnen", "landwirtschaft", "privatperson",
    "bildung_forschung",
}


def page_text(html: str) -> tuple[str, str]:
    """(title, main text) of a product page."""
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.string or "").replace("- NRW.BANK", "").strip() if soup.title else ""
    main = soup.find("main") or soup.body
    if main is None:
        return title, ""
    for t in main(["script", "style", "nav", "footer", "header"]):
        t.decompose()
    return title, re.sub(r"\s+", " ", main.get_text(" ", strip=True))


def parse_norm(raw: str) -> dict[str, Any] | None:
    """LLM JSON → validated schema dict, or None if unparseable."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1].lstrip("json").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    data["target_groups"] = [t for t in (data.get("target_groups") or []) if t in _TARGET_GROUPS]
    if data.get("level") not in ("bund", "land", "kommune"):
        data["level"] = "land"
    if data.get("funding_type") not in ("zuschuss", "darlehen", "buergschaft", "beteiligung", "preis"):
        data["funding_type"] = None
    return data


class NrwBankFoerderungConnector(BaseConnector):
    shape = ConnectorShape.REFERENCE
    source_name = "nrwbank_foerderung"

    async def fetch(self, checkpoint: dict[str, Any] | None = None) -> AsyncGenerator[Any, None]:
        resp = await self._get(_SITEMAP)
        urls: dict[str, str] = {}  # product id → url (sitemap can repeat)
        for m in _PRODUCT_RE.finditer(resp.text):
            urls[m.group(1)] = "https://www.nrwbank.de" + m.group(0)
        for pid, url in urls.items():
            await asyncio.sleep(_CRAWL_DELAY_S)
            try:
                page = await self._get(url)
            except Exception:
                continue  # single dead page must not kill the sweep
            yield {"product_id": pid, "url": url, "html": page.text}

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        title, text = page_text(raw["html"])
        return {
            "source_id": raw["product_id"],
            "source_url": raw["url"],
            "label": title[:200] or f"Förderprodukt {raw['product_id']}",
            "text": text[:6000],
        }

    async def emit_entities(self, normalized: dict[str, Any]) -> list[NodeBase]:
        if not normalized["text"]:
            return []
        raw = await complete(
            FOERDERUNG_NORM_SYSTEM,
            FOERDERUNG_NORM_PROMPT.format(title=normalized["label"], text=normalized["text"]),
            max_tokens=2000, model=fast_model(),
        )
        schema = parse_norm(raw)
        if schema is None:
            return []
        prov = self._provenance(normalized["source_id"], normalized["source_url"])
        return [FundingProgram(label=normalized["label"], properties=schema, **prov)]

    async def emit_edges(self, normalized: dict[str, Any], nodes: list[NodeBase]) -> list[EdgeBase]:
        return []  # ELIGIBLE_FOR edges come from the matcher, not ingestion
