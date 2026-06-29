"""
Frozen raw-input → normalized-output tests for the geo spine connector.
No network calls — raw fixtures mirror the ODS v2.1 flat record shape.
"""

import pytest

from connectors.geo_spine.connector import GeoSpineConnector
from ontology.nodes import GeoArea

_POLY = {
    "type": "Feature",
    "properties": {},
    "geometry": {
        "type": "Polygon",
        "coordinates": [[[7.45, 51.51], [7.46, 51.51], [7.46, 51.52], [7.45, 51.51]]],
    },
}

STADTBEZIRK_RAW = {
    "dataset": "stadtbezirke",
    "record": {
        "stadtbezirk_nr": "1",
        "stadtbezirk_bezeichnung": "Innenstadt-West",
        "geografische_polygone": _POLY,
    },
}

STAT_BEZIRK_RAW = {
    "dataset": "stat_bezirke",
    "record": {
        "statistischer_bezirk_nr": "10",
        "statistischer_bezirk": "Stadtmitte",
        "stadtbezirk_nr": "1",
        "geografische_polygone": _POLY,
    },
}


@pytest.fixture
def connector() -> GeoSpineConnector:
    return GeoSpineConnector()


def test_normalize_stadtbezirk(connector: GeoSpineConnector) -> None:
    norm = connector.normalize(STADTBEZIRK_RAW)
    assert norm["area_type"] == "stadtbezirk"
    assert norm["source_id"] == "1"
    assert norm["label"] == "Innenstadt-West"
    assert norm["geom"]["type"] == "Polygon"  # Feature unwrapped to geometry


def test_normalize_stat_bezirk(connector: GeoSpineConnector) -> None:
    norm = connector.normalize(STAT_BEZIRK_RAW)
    assert norm["area_type"] == "statistischer_bezirk"
    assert norm["source_id"] == "10"
    assert norm["label"] == "Stadtmitte"
    assert norm["parent_source_id"] == "1"


@pytest.mark.asyncio
async def test_emit_stadtbezirk(connector: GeoSpineConnector) -> None:
    norm = connector.normalize(STADTBEZIRK_RAW)
    nodes = await connector.emit_entities(norm)
    assert len(nodes) == 1
    node = nodes[0]
    assert isinstance(node, GeoArea)
    assert node.label == "Innenstadt-West"
    assert node.source == "opendata_dortmund_geo"
    assert node.source_id == "1"
    assert node.geom is not None
    assert node.properties["area_type"] == "stadtbezirk"


@pytest.mark.asyncio
async def test_emit_stat_bezirk_sets_parent_prop(connector: GeoSpineConnector) -> None:
    norm = connector.normalize(STAT_BEZIRK_RAW)
    nodes = await connector.emit_entities(norm)
    node = nodes[0]
    assert isinstance(node, GeoArea)
    # parent stored in properties so the flow can resolve PART_OF later
    assert node.properties["_parent_source_id"] == "1"


@pytest.mark.asyncio
async def test_provenance_fields(connector: GeoSpineConnector) -> None:
    norm = connector.normalize(STADTBEZIRK_RAW)
    node = (await connector.emit_entities(norm))[0]
    assert node.source == "opendata_dortmund_geo"
    assert node.source_url is not None
    assert node.observed_at is not None
    assert node.inferred is False
