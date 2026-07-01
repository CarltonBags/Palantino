"""
Node type definitions for the civic graph.

Every node must carry provenance (source, source_url, observed_at) and
bitemporal timestamps (valid_from, valid_to). Inferred nodes get
inferred=True + confidence + reasoning_trace.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class NodeBase(BaseModel):
    """Fields every node must carry."""

    id: UUID = Field(default_factory=uuid4)
    node_type: str
    label: str
    properties: dict[str, Any] = Field(default_factory=dict)

    # provenance
    source: str
    source_id: str | None = None
    source_url: str | None = None

    # bitemporal
    observed_at: datetime = Field(default_factory=datetime.utcnow)
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    # inference
    inferred: bool = False
    confidence: float | None = None
    reasoning_trace: str | None = None

    # spatial (GeoJSON geometry or None)
    geom: dict[str, Any] | None = None


# ── Concrete node types ────────────────────────────────────────────────────────

class GeoArea(NodeBase):
    """Stadtbezirk, statistischer Bezirk, or address point."""

    node_type: str = "GeoArea"

    @classmethod
    def fields(cls) -> set[str]:
        return {"area_type", "ags", "nuts3"}  # common extra props


class Organization(NodeBase):
    """Council, committee, company, sports club, public authority."""

    node_type: str = "Organization"

    @classmethod
    def fields(cls) -> set[str]:
        return {"org_type", "register_id", "url"}


class Person(NodeBase):
    """
    Public official acting in official capacity only.
    Do NOT store private individuals. Facts only — no motive/character.
    """

    node_type: str = "Person"

    @classmethod
    def fields(cls) -> set[str]:
        return {"role", "party", "committee_ids"}


class Resolution(NodeBase):
    """Council decision / Beschluss from OParl."""

    node_type: str = "Resolution"

    @classmethod
    def fields(cls) -> set[str]:
        return {"resolution_number", "passed", "meeting_id", "text_url"}


class Meeting(NodeBase):
    """Council sitting / Sitzung from OParl."""

    node_type: str = "Meeting"

    @classmethod
    def fields(cls) -> set[str]:
        return {"meeting_type", "committee_id", "location", "agenda_url"}


class AgendaItem(NodeBase):
    """Single Tagesordnungspunkt within a Meeting."""

    node_type: str = "AgendaItem"

    @classmethod
    def fields(cls) -> set[str]:
        return {"number", "public", "result"}


class POI(NodeBase):
    """Business / point-of-interest from OSM Overpass or city portal."""

    node_type: str = "POI"

    @classmethod
    def fields(cls) -> set[str]:
        return {"amenity", "shop", "office", "opening_hours", "phone", "website",
                "addr_street", "addr_housenumber", "addr_postcode"}


class Road(NodeBase):
    """Road segment from Dortmund Open Data (fb62-strassen)."""

    node_type: str = "Road"

    @classmethod
    def fields(cls) -> set[str]:
        return {"road_type", "name_de", "length_m"}


class ConstructionSite(NodeBase):
    """Baustelle from Dortmund Open Data or NRW traffic feed."""

    node_type: str = "ConstructionSite"

    @classmethod
    def fields(cls) -> set[str]:
        return {"reason", "operator", "planned_end"}


class Tender(NodeBase):
    """Public procurement announcement from Vergabe.NRW."""

    node_type: str = "Tender"

    @classmethod
    def fields(cls) -> set[str]:
        return {"tender_type", "value_eur", "deadline", "award_criteria"}


class BudgetItem(NodeBase):
    """A line of the municipal budget (Haushaltsplan) — a Produktbereich or plan
    line with income/expense/investment amounts for a year."""

    node_type: str = "BudgetItem"

    @classmethod
    def fields(cls) -> set[str]:
        return {"produkt_bereich", "year", "ertraege_eur", "aufwendungen_eur",
                "saldo_eur", "investive_auszahlungen_eur"}


class Event(NodeBase):
    """Police incident, public event, sports fixture, demonstration."""

    node_type: str = "Event"

    @classmethod
    def fields(cls) -> set[str]:
        return {"event_type", "venue", "start_time", "end_time",
                "attendance_estimate", "tags"}


class WeatherObservation(NodeBase):
    """Hourly weather reading from Bright Sky / DWD."""

    node_type: str = "WeatherObservation"

    @classmethod
    def fields(cls) -> set[str]:
        return {"temperature", "precipitation", "wind_speed", "condition",
                "station_id", "dwd_station_id"}


class AirQualityObservation(NodeBase):
    """Hourly air quality reading from LANUV LUQS."""

    node_type: str = "AirQualityObservation"

    @classmethod
    def fields(cls) -> set[str]:
        return {"pm10", "no2", "no", "so2", "o3", "station_id", "preliminary"}


class TransitStop(NodeBase):
    """GTFS stop — tram, bus, U-Bahn, regional rail."""

    node_type: str = "TransitStop"

    @classmethod
    def fields(cls) -> set[str]:
        return {"stop_code", "platform_code", "wheelchair_boarding"}


class TransitRoute(NodeBase):
    """GTFS route / line."""

    node_type: str = "TransitRoute"

    @classmethod
    def fields(cls) -> set[str]:
        return {"route_short_name", "route_type", "agency_id"}


NODE_TYPES: dict[str, type[NodeBase]] = {
    "GeoArea": GeoArea,
    "Organization": Organization,
    "Person": Person,
    "Resolution": Resolution,
    "Meeting": Meeting,
    "AgendaItem": AgendaItem,
    "POI": POI,
    "Road": Road,
    "ConstructionSite": ConstructionSite,
    "Tender": Tender,
    "Event": Event,
    "WeatherObservation": WeatherObservation,
    "AirQualityObservation": AirQualityObservation,
    "TransitStop": TransitStop,
    "TransitRoute": TransitRoute,
}
