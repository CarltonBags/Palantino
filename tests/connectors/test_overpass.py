"""
Frozen raw-input → normalized-output tests for the Overpass connector.
No network calls.
"""

import pytest

from connectors.overpass.connector import OverpassConnector
from ontology.nodes import POI


@pytest.fixture
def connector() -> OverpassConnector:
    return OverpassConnector()


NODE_RAW = {
    "type": "node",
    "id": 123456789,
    "lat": 51.5136,
    "lon": 7.4653,
    "tags": {
        "name": "Bäckerei Schmidt",
        "shop": "bakery",
        "addr:street": "Reinoldistraße",
        "addr:housenumber": "12",
        "addr:postcode": "44135",
        "opening_hours": "Mo-Fr 07:00-18:00",
        "phone": "+49 231 123456",
        "website": "https://example.de",
    },
}

WAY_RAW = {
    "type": "way",
    "id": 987654321,
    "center": {"lat": 51.520, "lon": 7.470},
    "tags": {
        "name": "Klinikum Dortmund",
        "amenity": "hospital",
        "addr:street": "Beurhausstraße",
        "addr:housenumber": "40",
    },
}

UNNAMED_RAW = {
    "type": "node",
    "id": 111,
    "lat": 51.50,
    "lon": 7.46,
    "tags": {
        "shop": "clothes",
    },
}


# ── normalize ──────────────────────────────────────────────────────────────────

def test_normalize_node(connector: OverpassConnector) -> None:
    norm = connector.normalize(NODE_RAW)
    assert norm["source_id"] == "node/123456789"
    assert norm["label"] == "Bäckerei Schmidt"
    assert norm["shop"] == "bakery"
    assert norm["lat"] == 51.5136
    assert norm["lon"] == 7.4653
    assert norm["addr_street"] == "Reinoldistraße"
    assert norm["addr_postcode"] == "44135"
    assert norm["opening_hours"] == "Mo-Fr 07:00-18:00"


def test_normalize_way_uses_center(connector: OverpassConnector) -> None:
    norm = connector.normalize(WAY_RAW)
    assert norm["source_id"] == "way/987654321"
    assert norm["lat"] == 51.520
    assert norm["lon"] == 7.470
    assert norm["amenity"] == "hospital"


def test_normalize_unnamed_falls_back(connector: OverpassConnector) -> None:
    norm = connector.normalize(UNNAMED_RAW)
    assert "OSM node 111" in norm["label"]


def test_normalize_source_url(connector: OverpassConnector) -> None:
    norm = connector.normalize(NODE_RAW)
    assert norm["source_url"] == "https://www.openstreetmap.org/node/123456789"


# ── emit ───────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_emit_poi_node(connector: OverpassConnector) -> None:
    norm = connector.normalize(NODE_RAW)
    nodes = await connector.emit_entities(norm)
    assert len(nodes) == 1
    node = nodes[0]
    assert isinstance(node, POI)
    assert node.node_type == "POI"
    assert node.label == "Bäckerei Schmidt"
    assert node.source == "osm_overpass"
    assert node.source_id == "node/123456789"


@pytest.mark.asyncio
async def test_emit_poi_has_point_geom(connector: OverpassConnector) -> None:
    norm = connector.normalize(NODE_RAW)
    nodes = await connector.emit_entities(norm)
    node = nodes[0]
    assert node.geom is not None
    assert node.geom["type"] == "Point"
    assert node.geom["coordinates"] == [7.4653, 51.5136]


@pytest.mark.asyncio
async def test_emit_poi_properties(connector: OverpassConnector) -> None:
    norm = connector.normalize(NODE_RAW)
    nodes = await connector.emit_entities(norm)
    props = nodes[0].properties
    assert props["shop"] == "bakery"
    assert props["addr_street"] == "Reinoldistraße"
    assert props["opening_hours"] == "Mo-Fr 07:00-18:00"


@pytest.mark.asyncio
async def test_emit_way_poi_has_center_geom(connector: OverpassConnector) -> None:
    norm = connector.normalize(WAY_RAW)
    nodes = await connector.emit_entities(norm)
    node = nodes[0]
    assert node.geom is not None
    assert node.geom["coordinates"][0] == 7.470


@pytest.mark.asyncio
async def test_emit_edges_empty(connector: OverpassConnector) -> None:
    norm = connector.normalize(NODE_RAW)
    nodes = await connector.emit_entities(norm)
    edges = await connector.emit_edges(norm, nodes)
    assert edges == []  # LOCATED_IN resolved by spatial join in flow


@pytest.mark.asyncio
async def test_provenance_complete(connector: OverpassConnector) -> None:
    norm = connector.normalize(NODE_RAW)
    nodes = await connector.emit_entities(norm)
    node = nodes[0]
    assert node.source == "osm_overpass"
    assert node.source_url == "https://www.openstreetmap.org/node/123456789"
    assert node.observed_at is not None
    assert node.inferred is False
