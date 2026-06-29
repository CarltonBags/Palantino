"""
Connector: gtfs.de GTFS-Realtime stream (live transit disruptions).

Source:  https://realtime.gtfs.de/realtime-free.pb
Format:  GTFS-Realtime protobuf (TripUpdates + ServiceAlerts)
License: free, Germany-wide
Shape:   event_stream — poll, dedupe by entity id + timestamp

What this covers:
  - ServiceAlerts (disruptions, cancellations) → Event nodes.
  - Significant TripUpdate delays (> threshold) → Event nodes.
Scope:
  - Germany-wide feed. We keep alerts/updates whose informed entity references a
    route we already track is left to the resolution layer; here we emit all and
    tag them, since the protobuf alone carries no city. The flow may pre-filter.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from google.transit import gtfs_realtime_pb2

from connectors.base import BaseConnector, ConnectorShape
from ontology.edges import EdgeBase
from ontology.nodes import Event, NodeBase

_FEED_URL = "https://realtime.gtfs.de/realtime-free.pb"
# Only surface delays at least this many seconds as their own Event.
_DELAY_THRESHOLD_S = 600


def _ts_to_dt(seconds: int | None) -> datetime | None:
    if not seconds:
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


class GtfsRealtimeConnector(BaseConnector):
    shape = ConnectorShape.EVENT_STREAM
    source_name = "gtfs_de_realtime"

    async def fetch(self, checkpoint: dict[str, Any] | None = None) -> AsyncGenerator[Any, None]:
        seen: set[str] = set((checkpoint or {}).get("seen_ids", []))
        resp = await self._get(_FEED_URL)
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(resp.content)
        feed_ts = feed.header.timestamp

        for entity in feed.entity:
            if entity.HasField("alert"):
                rec = self._alert_record(entity, feed_ts)
            elif entity.HasField("trip_update"):
                rec = self._delay_record(entity, feed_ts)
            else:
                rec = None
            if rec and rec["source_id"] not in seen:
                yield rec

    def _alert_record(self, entity: Any, feed_ts: int) -> dict[str, Any]:
        alert = entity.alert
        header = self._first_translation(alert.header_text)
        desc = self._first_translation(alert.description_text)
        start = alert.active_period[0].start if alert.active_period else feed_ts
        route_ids = [ie.route_id for ie in alert.informed_entity if ie.route_id]
        return {
            "kind": "alert",
            "source_id": f"alert:{entity.id}:{start}",
            "label": header or "Service alert",
            "description": desc,
            "valid_from": _ts_to_dt(start),
            "route_ids": route_ids,
            "delay_s": None,
        }

    def _delay_record(self, entity: Any, feed_ts: int) -> dict[str, Any] | None:
        tu = entity.trip_update
        max_delay = 0
        for stu in tu.stop_time_update:
            for ev in (stu.arrival, stu.departure):
                if ev and ev.delay and ev.delay > max_delay:
                    max_delay = ev.delay
        if max_delay < _DELAY_THRESHOLD_S:
            return None
        route_id = tu.trip.route_id
        return {
            "kind": "delay",
            "source_id": f"delay:{entity.id}:{feed_ts}",
            "label": f"Delay {max_delay // 60} min route {route_id or '?'}",
            "description": None,
            "valid_from": _ts_to_dt(feed_ts),
            "route_ids": [route_id] if route_id else [],
            "delay_s": max_delay,
        }

    @staticmethod
    def _first_translation(text_field: Any) -> str | None:
        if text_field and text_field.translation:
            return text_field.translation[0].text
        return None

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        return {
            "source_id": raw["source_id"],
            "label": raw["label"][:200],
            "valid_from": raw["valid_from"],
            "event_type": "transit_disruption",
            "kind": raw["kind"],
            "description": raw.get("description"),
            "route_ids": raw.get("route_ids", []),
            "delay_s": raw.get("delay_s"),
        }

    async def emit_entities(self, normalized: dict[str, Any]) -> list[NodeBase]:
        prov = self._provenance(normalized["source_id"], _FEED_URL)
        node = Event(
            label=normalized["label"],
            valid_from=normalized["valid_from"],
            properties={
                "event_type": normalized["event_type"],
                "kind": normalized["kind"],
                "description": normalized["description"],
                "route_ids": normalized["route_ids"],
                "delay_s": normalized["delay_s"],
                "tags": ["transit", "gtfs-rt"],
            },
            **prov,
        )
        return [node]

    async def emit_edges(self, normalized: dict[str, Any], nodes: list[NodeBase]) -> list[EdgeBase]:
        # RELATES_TO (disruption → affected TransitRoute) needs route resolution
        # against gtfs_static nodes — deferred to the resolution layer.
        return []
