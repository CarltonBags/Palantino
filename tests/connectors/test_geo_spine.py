"""
Frozen raw-input → normalized-output tests for the geo spine connector.
No network calls — raw fixtures inline.
"""

import pytest

from connectors.geo_spine.connector import GeoSpineConnector
from ontology.nodes import GeoArea, Road


@pytest.fixture
def connector() -> GeoSpineConnector:
    return GeoSpineConnector()


STADTBEZIRK_RAW = {
    "dataset": "stadtbezirke",
    "record": {
        "fields": {
            "stadtbezirk_nr": "1",
            "stadtbezirk_name": "Innenstadt-West",
            "ags": "05913001",
        },
        "geo_shape": {
            "type": "Polygon",
            "coordinates": [[[7.45, 51.51], [7.46, 51.51], [7.46, 51.52], [7.45, 51.51]]],
        },
    },
}

STAT_BEZIRK_RAW = {
    "dataset": "stat_bezirke",
    "record": {
        "fields": {
            "stat_bezirk_nr": "10",
            "stat_bezirk_name": "Stadtmitte",
            "stadtbezirk_nr": "1",
        },
        "geo_shape": {
            "type": "Polygon",
            "coordinates": [[[7.455, 51.515], [7.460, 51.515], [7.460, 51.520], [7.455, 51.515]]],
        },
    },
}

STRASSE_RAW = {
    "dataset": "strassen",
    "record": {
        "fields": {
            "strassenid": "S001",
            "strassenname": "Reinoldistraße",
            "strassenklasse": "Hauptstraße",
            "laenge_m": 320.5,
        },
        "geo_shape": {
            "type": "LineString",
            "coordinates": [[7.459, 51.514], [7.462, 51.514]],
        },
    },
}


def test_normalize_stadtbezirk(connector: GeoSpineConnector) -> None:
    norm = connector.normalize(STADTBEZIRK_RAW)
    assert norm["area_type"] == "stadtbezirk"
    assert norm["source_id"] == "1"
    assert norm["label"] == "Innenstadt-West"
    assert norm["ags"] == "05913001"
    assert norm["geom"]["type"] == "Polygon"


def test_normalize_stat_bezirk(connector: GeoSpineConnector) -> None:
    norm = connector.normalize(STAT_BEZIRK_RAW)
    assert norm["area_type"] == "statistischer_bezirk"
    assert norm["source_id"] == "10"
    assert norm["parent_source_id"] == "1"


def test_normalize_strasse(connector: GeoSpineConnector) -> None:
    norm = connector.normalize(STRASSE_RAW)
    assert norm["source_id"] == "S001"
    assert norm["label"] == "Reinoldistraße"
    assert norm["road_type"] == "Hauptstraße"
    assert norm["length_m"] == 320.5


@pytest.mark.asyncio
async def test_emit_stadtbezirk(connector: GeoSpineConnector) -> None:
    norm = connector.normalize(STADTBEZIRK_RAW)
    nodes = await connector.emit_entities(norm)
    assert len(nodes) == 1
    node = nodes[0]
    assert isinstance(node, GeoArea)
    assert node.node_type == "GeoArea"
    assert node.label == "Innenstadt-West"
    assert node.source == "opendata_dortmund_geo"
    assert node.source_id == "1"
    assert node.geom is not None
    assert node.properties["area_type"] == "stadtbezirk"


@pytest.mark.asyncio
async def test_emit_stat_bezirk_sets_parent_prop(connector: GeoSpineConnector) -> None:
    norm = connector.normalize(STAT_BEZIRK_RAW)
    nodes = await connector.emit_entities(norm)
    assert len(nodes) == 1
    node = nodes[0]
    assert isinstance(node, GeoArea)
    # parent stored in properties for later resolution
    assert node.properties["_parent_source_id"] == "1"


@pytest.mark.asyncio
async def test_emit_road(connector: GeoSpineConnector) -> None:
    norm = connector.normalize(STRASSE_RAW)
    nodes = await connector.emit_entities(norm)
    assert len(nodes) == 1
    assert isinstance(nodes[0], Road)
    assert nodes[0].properties["road_type"] == "Hauptstraße"


@pytest.mark.asyncio
async def test_provenance_fields(connector: GeoSpineConnector) -> None:
    norm = connector.normalize(STADTBEZIRK_RAW)
    nodes = await connector.emit_entities(norm)
    node = nodes[0]
    assert node.source == "opendata_dortmund_geo"
    assert node.source_url is not None
    assert node.observed_at is not None
    assert node.inferred is False
