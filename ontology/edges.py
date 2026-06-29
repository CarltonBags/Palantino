"""
Edge type definitions for the civic graph.

Every edge must carry the same provenance + bitemporal fields as nodes.
Inferred edges (reasoning layer) carry inferred=True + confidence + reasoning_trace.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class EdgeBase(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    edge_type: str
    from_node_id: UUID
    to_node_id: UUID
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


# ── Typed edge constructors ────────────────────────────────────────────────────

def located_in(from_node_id: UUID, to_node_id: UUID, **kwargs: Any) -> EdgeBase:
    """Entity → GeoArea it physically belongs to."""
    return EdgeBase(edge_type="LOCATED_IN", from_node_id=from_node_id,
                    to_node_id=to_node_id, **kwargs)


def voted_on(person_id: UUID, resolution_id: UUID, vote: str, **kwargs: Any) -> EdgeBase:
    """Person → Resolution with vote value (yes/no/abstain)."""
    return EdgeBase(edge_type="VOTED_ON", from_node_id=person_id,
                    to_node_id=resolution_id,
                    properties={"vote": vote, **kwargs.pop("properties", {})},
                    **kwargs)


def passed_by(resolution_id: UUID, committee_id: UUID, **kwargs: Any) -> EdgeBase:
    """Resolution → Organization (committee that passed it)."""
    return EdgeBase(edge_type="PASSED_BY", from_node_id=resolution_id,
                    to_node_id=committee_id, **kwargs)


def relates_to(from_id: UUID, to_id: UUID, **kwargs: Any) -> EdgeBase:
    """Generic cross-entity relation (Resolution → Road, Event → GeoArea, …)."""
    return EdgeBase(edge_type="RELATES_TO", from_node_id=from_id,
                    to_node_id=to_id, **kwargs)


def awarded_to(tender_id: UUID, org_id: UUID, **kwargs: Any) -> EdgeBase:
    """Tender → Organization that won the contract."""
    return EdgeBase(edge_type="AWARDED_TO", from_node_id=tender_id,
                    to_node_id=org_id, **kwargs)


def member_of(person_id: UUID, org_id: UUID, **kwargs: Any) -> EdgeBase:
    """Person → Organization (committee membership)."""
    return EdgeBase(edge_type="MEMBER_OF", from_node_id=person_id,
                    to_node_id=org_id, **kwargs)


def part_of(child_id: UUID, parent_id: UUID, **kwargs: Any) -> EdgeBase:
    """GeoArea → parent GeoArea (statistischer Bezirk → Stadtbezirk)."""
    return EdgeBase(edge_type="PART_OF", from_node_id=child_id,
                    to_node_id=parent_id, **kwargs)


def serves(route_id: UUID, stop_id: UUID, sequence: int, **kwargs: Any) -> EdgeBase:
    """TransitRoute → TransitStop."""
    return EdgeBase(edge_type="SERVES", from_node_id=route_id,
                    to_node_id=stop_id,
                    properties={"stop_sequence": sequence, **kwargs.pop("properties", {})},
                    **kwargs)


def mentions(event_id: UUID, entity_id: UUID, **kwargs: Any) -> EdgeBase:
    """Event (news/police RSS) → any entity it references."""
    return EdgeBase(edge_type="MENTIONS", from_node_id=event_id,
                    to_node_id=entity_id, **kwargs)


def same_as(node_a_id: UUID, node_b_id: UUID, method: str, **kwargs: Any) -> EdgeBase:
    """Cross-source entity resolution merge (inferred=True always)."""
    return EdgeBase(
        edge_type="SAME_AS",
        from_node_id=node_a_id,
        to_node_id=node_b_id,
        inferred=True,
        properties={"method": method, **kwargs.pop("properties", {})},
        **kwargs,
    )


EDGE_TYPES = {
    "LOCATED_IN", "VOTED_ON", "PASSED_BY", "RELATES_TO",
    "AWARDED_TO", "MEMBER_OF", "PART_OF", "SERVES",
    "MENTIONS", "SAME_AS",
}
