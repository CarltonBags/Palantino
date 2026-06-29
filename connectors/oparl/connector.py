"""
OParl 1.1 council connector — Dortmund Ratsinformationssystem.

Pulls:
  - Body (top-level system object — confirms endpoint)
  - Organization (committees/Gremien)
  - Person (members — official capacity only, role/party not private data)
  - Meeting (Sitzungen)
  - AgendaItem (Tagesordnungspunkte)
  - Paper / Resolution (Beschlüsse via AgendaItem.resolutions)

Shape: event_stream (daily poll, fetch modified-since last checkpoint)
License: open (OParl standard, public-sector content)
Auth: none

NOTE: Dortmund's exact OParl endpoint URL must be confirmed from the RIS vendor
(e.g. Sternberg, citeq). The URL in .env is a best-guess placeholder.
Run `GET <OPARL_ENDPOINT_URL>` and confirm it returns {"@type": "OParl:System"}.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, AsyncGenerator
from uuid import UUID

from config import settings
from connectors.base import BaseConnector, ConnectorShape
from ontology.edges import EdgeBase, member_of, passed_by, relates_to
from ontology.nodes import AgendaItem, Meeting, NodeBase, Organization, Person, Resolution

logger = logging.getLogger(__name__)

# Dortmund disabled OParl in production, so the endpoint is normally unset.
# Tolerate None at import time (empty string) and guard at runtime in fetch().
ENDPOINT = (settings.oparl_endpoint_url or "").rstrip("/")
SOURCE_URL_BASE = ENDPOINT


class OParlConnector(BaseConnector):
    shape = ConnectorShape.EVENT_STREAM
    source_name = "oparl_dortmund"

    # ── fetch ──────────────────────────────────────────────────────────────────

    async def fetch(self, checkpoint: dict[str, Any] | None = None) -> AsyncGenerator[Any, None]:
        if not ENDPOINT:
            logger.warning("OParl endpoint not configured — skipping (Dortmund disabled it).")
            return
        modified_since: str | None = (checkpoint or {}).get("modified_since")

        body_url = await self._get_body_url()
        if body_url is None:
            logger.error("OParl system endpoint did not return a Body list URL")
            return

        # Committees / Organizations
        async for item in self._paginate(f"{body_url}/organization", modified_since):
            yield {"oparl_type": "Organization", "data": item}

        # Members
        async for item in self._paginate(f"{body_url}/person", modified_since):
            yield {"oparl_type": "Person", "data": item}

        # Meetings (which embed AgendaItems + Resolutions)
        async for item in self._paginate(f"{body_url}/meeting", modified_since):
            yield {"oparl_type": "Meeting", "data": item}

    async def _get_body_url(self) -> str | None:
        try:
            resp = await self._get(ENDPOINT)
            system = resp.json()
            bodies = system.get("body", [])
            if not bodies:
                return None
            # Dereference if it's a list URL
            if isinstance(bodies, str):
                body_list_resp = await self._get(bodies)
                body_list = body_list_resp.json()
                bodies = body_list.get("data", [])
            if isinstance(bodies, list) and bodies:
                first = bodies[0]
                return first if isinstance(first, str) else first.get("id")
        except Exception as exc:
            logger.exception("Failed to resolve OParl Body URL: %s", exc)
        return None

    async def _paginate(
        self, url: str, modified_since: str | None = None
    ) -> AsyncGenerator[dict[str, Any], None]:
        params: dict[str, Any] = {}
        if modified_since:
            params["modified_since"] = modified_since

        next_url: str | None = url
        while next_url:
            try:
                resp = await self._get(next_url, params=params)
                page = resp.json()
                params = {}  # only on first request
            except Exception as exc:
                logger.error("OParl pagination error at %s: %s", next_url, exc)
                break

            for item in page.get("data", []):
                yield item

            links = page.get("links", {})
            next_url = links.get("next")

    # ── normalize ──────────────────────────────────────────────────────────────

    def normalize(self, raw: Any) -> dict[str, Any]:
        oparl_type = raw["oparl_type"]
        data = raw["data"]
        base = {
            "oparl_type": oparl_type,
            "source_id": data.get("id", ""),
            "source_url": data.get("id", ""),  # OParl ID is the canonical URL
            "modified": data.get("modified"),
            "deleted": data.get("deleted", False),
        }

        if oparl_type == "Organization":
            return {
                **base,
                "label": data.get("name", "Unknown committee"),
                "org_type": data.get("organizationType", data.get("classification")),
                "membership_urls": [m.get("person") for m in data.get("membership", []) if m.get("person")],
            }

        if oparl_type == "Person":
            return {
                **base,
                "label": data.get("name", "Unknown person"),
                "role": data.get("title"),
                "party": self._extract_party(data),
                "memberships": data.get("membership", []),
            }

        if oparl_type == "Meeting":
            resolutions: list[dict[str, Any]] = []
            agenda_items: list[dict[str, Any]] = []
            for agi in data.get("agendaItem", []):
                if isinstance(agi, dict):
                    agenda_items.append(agi)
                    for res in agi.get("resolution", []):
                        resolutions.append({"agenda_item": agi, "resolution": res})
            return {
                **base,
                "label": data.get("name", data.get("shortName", "Meeting")),
                "meeting_type": data.get("meetingType"),
                "start": data.get("start"),
                "end": data.get("end"),
                "location": data.get("location", {}).get("description") if data.get("location") else None,
                "committee_url": data.get("organization", [None])[0] if data.get("organization") else None,
                "agenda_items": agenda_items,
                "resolutions": resolutions,
            }

        return base

    def _extract_party(self, data: dict[str, Any]) -> str | None:
        for m in data.get("membership", []):
            if isinstance(m, dict):
                org = m.get("organization", {})
                if isinstance(org, dict) and org.get("classification") == "party":
                    return org.get("shortName") or org.get("name")
        return None

    # ── emit ───────────────────────────────────────────────────────────────────

    async def emit_entities(self, normalized: dict[str, Any]) -> list[NodeBase]:
        if normalized.get("deleted"):
            return []

        t = normalized["oparl_type"]
        prov = self._provenance(normalized["source_id"], normalized["source_url"])

        if t == "Organization":
            return [
                Organization(
                    label=normalized["label"],
                    properties={
                        "org_type": normalized.get("org_type"),
                        "_membership_urls": normalized.get("membership_urls", []),
                    },
                    **prov,
                )
            ]

        if t == "Person":
            return [
                Person(
                    label=normalized["label"],
                    properties={
                        "role": normalized.get("role"),
                        "party": normalized.get("party"),
                        "_memberships": normalized.get("memberships", []),
                    },
                    **prov,
                )
            ]

        if t == "Meeting":
            nodes: list[NodeBase] = [
                Meeting(
                    label=normalized["label"],
                    valid_from=self._parse_dt(normalized.get("start")),
                    valid_to=self._parse_dt(normalized.get("end")),
                    properties={
                        "meeting_type": normalized.get("meeting_type"),
                        "location": normalized.get("location"),
                        "_committee_url": normalized.get("committee_url"),
                    },
                    **prov,
                )
            ]
            for agi in normalized.get("agenda_items", []):
                nodes.append(
                    AgendaItem(
                        label=agi.get("name", agi.get("title", "Agenda item")),
                        properties={
                            "number": agi.get("number"),
                            "public": agi.get("public", True),
                        },
                        source=self.source_name,
                        source_id=agi.get("id", ""),
                        source_url=agi.get("id", ""),
                        observed_at=self._now(),
                    )
                )
            for r in normalized.get("resolutions", []):
                res_data = r["resolution"]
                if isinstance(res_data, dict):
                    nodes.append(
                        Resolution(
                            label=res_data.get("name", res_data.get("title", "Resolution")),
                            valid_from=self._parse_dt(normalized.get("start")),
                            properties={
                                "resolution_number": res_data.get("resolutionNumber"),
                                "text_url": res_data.get("text"),
                            },
                            source=self.source_name,
                            source_id=res_data.get("id", ""),
                            source_url=res_data.get("id", ""),
                            observed_at=self._now(),
                        )
                    )
            return nodes

        return []

    async def emit_edges(self, normalized: dict[str, Any], nodes: list[NodeBase]) -> list[EdgeBase]:
        edges: list[EdgeBase] = []
        t = normalized["oparl_type"]
        prov_kwargs = dict(
            source=self.source_name,
            source_url=normalized["source_url"],
            observed_at=self._now(),
        )

        if t == "Meeting" and nodes:
            meeting_node = next((n for n in nodes if isinstance(n, Meeting)), None)
            resolution_nodes = [n for n in nodes if isinstance(n, Resolution)]
            committee_url = normalized.get("committee_url")

            for res in resolution_nodes:
                if meeting_node and committee_url:
                    # Mark committee_url for resolution in flow
                    edges.append(
                        passed_by(
                            resolution_id=res.id,
                            committee_id=UUID(int=0),  # placeholder — flow resolves via _committee_url
                            source_url=res.source_url or "",
                            **prov_kwargs,
                            properties={"_committee_url": committee_url},
                        )
                    )

        return edges

    def _parse_dt(self, value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
