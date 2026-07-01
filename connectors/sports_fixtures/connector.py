"""
Connector: Dortmund sports fixtures via OpenLigaDB (free open football API).

Source:  https://api.openligadb.de  · Auth: none · community open data
Shape:   event_stream / reference — league schedule per season

Covers BVB home games — the city's highest-impact events (Signal Iduna Park,
~80k spectators): each matchday moves transport, gastronomy and retail near the
stadium, so they're prime proximity/complementary synergy anchors. Only HOME games
(team1 = the tracked team) are in Dortmund. OpenLigaDB leaves `location` empty, so
the known venue + coordinates are attached here. Extend _TEAMS for more clubs.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, AsyncGenerator

from connectors.base import BaseConnector, ConnectorShape
from ontology.edges import EdgeBase
from ontology.nodes import Event, NodeBase

logger = logging.getLogger(__name__)

_API = "https://api.openligadb.de/getmatchdata/{league}/{season}"
_SEASONS = ["2025", "2026"]  # current + upcoming; unpublished season returns few

# Tracked teams → their league shortcut + home venue (OpenLigaDB has no location).
_TEAMS = {
    "Borussia Dortmund": {
        "league": "bl1",
        "venue": "Signal Iduna Park",
        "lat": 51.4926,
        "lon": 7.4517,
    },
}


class SportsFixturesConnector(BaseConnector):
    shape = ConnectorShape.REFERENCE
    source_name = "openligadb"

    async def fetch(self, checkpoint: dict[str, Any] | None = None) -> AsyncGenerator[Any, None]:
        seen: set[Any] = set()
        for team, cfg in _TEAMS.items():
            for season in _SEASONS:
                url = _API.format(league=cfg["league"], season=season)
                try:
                    resp = await self._get(url)
                    matches = resp.json()
                except Exception as exc:  # unpublished season / transient error
                    logger.warning("openligadb %s %s failed: %s", cfg["league"], season, exc)
                    continue
                if not isinstance(matches, list):
                    continue
                for m in matches:
                    # HOME games only → played in the tracked team's city
                    if (m.get("team1") or {}).get("teamName") != team:
                        continue
                    mid = m.get("matchID")
                    if mid in seen:
                        continue
                    seen.add(mid)
                    m["_team"] = team
                    m["_cfg"] = cfg
                    yield m

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        cfg = raw.get("_cfg", {})
        team = raw.get("_team", "")
        opponent = (raw.get("team2") or {}).get("teamName", "")
        when = raw.get("matchDateTime") or raw.get("matchDateTimeUTC")
        valid_from = None
        if when:
            try:
                valid_from = datetime.fromisoformat(when.replace("Z", "+00:00"))
            except ValueError:
                valid_from = None
        finished = bool(raw.get("matchIsFinished"))
        result = None
        if finished and raw.get("matchResults"):
            end = raw["matchResults"][-1]
            result = f"{end.get('pointsTeam1')}:{end.get('pointsTeam2')}"
        return {
            "source_id": str(raw.get("matchID")),
            "label": f"{team} – {opponent}",
            "valid_from": valid_from,
            "team": team,
            "opponent": opponent,
            "league": raw.get("leagueName"),
            "spieltag": (raw.get("group") or {}).get("groupName"),
            "venue": cfg.get("venue"),
            "lat": cfg.get("lat"),
            "lon": cfg.get("lon"),
            "finished": finished,
            "result": result,
            "attendance": raw.get("numberOfViewers"),
            "source_url": "https://www.openligadb.de/",
        }

    async def emit_entities(self, normalized: dict[str, Any]) -> list[NodeBase]:
        geom = None
        if normalized["lat"] is not None and normalized["lon"] is not None:
            geom = {"type": "Point", "coordinates": [normalized["lon"], normalized["lat"]]}
        return [
            Event(
                label=normalized["label"],
                geom=geom,
                valid_from=normalized["valid_from"],
                properties={
                    "event_type": "sports_fixture",
                    "category": "Sport",
                    "team": normalized["team"],
                    "opponent": normalized["opponent"],
                    "league": normalized["league"],
                    "spieltag": normalized["spieltag"],
                    "venue": normalized["venue"],
                    "finished": normalized["finished"],
                    "result": normalized["result"],
                    "attendance": normalized["attendance"],
                },
                **self._provenance(normalized["source_id"], normalized["source_url"]),
            )
        ]

    async def emit_edges(self, normalized: dict[str, Any], nodes: list[NodeBase]) -> list[EdgeBase]:
        return []
