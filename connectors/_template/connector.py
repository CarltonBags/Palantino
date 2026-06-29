"""
Template connector — copy this folder to connectors/<your_source>/ to add a new source.

Steps:
1. Set shape (snapshot / event_stream / reference) and source_name.
2. Implement fetch() — pull raw data.
3. Implement normalize() — map to common intermediate dict.
4. Implement emit_entities() — return NodeBase subclasses with provenance + bitemporal fields.
5. Implement emit_edges() — return EdgeBase instances linking those nodes.
6. Register a Prefect flow in ingestion/flows.py with the correct cadence.
7. Add a frozen-fixture test in tests/connectors/test_<your_source>.py.

Legal checklist before shipping:
  [ ] robots.txt allows this path
  [ ] terms/AGB don't forbid automated use
  [ ] no login/CAPTCHA/paywall crossed
  [ ] personal data minimized + justified
  [ ] not extracting a 'substantial part' of a protected DB
  [ ] license recorded in source_url / source field
"""

from __future__ import annotations

from typing import Any, AsyncGenerator

from connectors.base import BaseConnector, ConnectorShape
from ontology.edges import EdgeBase
from ontology.nodes import NodeBase


class TemplateConnector(BaseConnector):
    shape = ConnectorShape.EVENT_STREAM
    source_name = "template"

    async def fetch(self, checkpoint: dict[str, Any] | None = None) -> AsyncGenerator[Any, None]:
        # Example: paginate an API
        page = 0
        while True:
            resp = await self._get("https://example.com/api", params={"page": page})
            data = resp.json()
            items = data.get("items", [])
            if not items:
                break
            for item in items:
                yield item
            page += 1

    def normalize(self, raw: Any) -> dict[str, Any]:
        return {
            "id": str(raw["id"]),
            "label": raw["name"],
            "url": raw.get("url"),
        }

    async def emit_entities(self, normalized: dict[str, Any]) -> list[NodeBase]:
        from ontology.nodes import Organization
        return [
            Organization(
                label=normalized["label"],
                **self._provenance(normalized["id"], normalized.get("url")),
            )
        ]

    async def emit_edges(self, normalized: dict[str, Any], nodes: list[NodeBase]) -> list[EdgeBase]:
        return []
