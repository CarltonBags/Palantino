"""Frozen raw → normalized tests for the street-segment connector. No network."""

import pytest

from connectors.strassenabschnitte.connector import StrassenabschnitteConnector, _geometry
from ontology.nodes import Road

RAW = {
    "strassenname": "Im Odemsloh",
    "strassenschlussel": "71586",
    "strassenabschnittsnummer": "180",
    "lange_strassenabschnitt_m": "38",
    "strassenklasse": "Anliegerstraße",
    "strassengruppe": "Gemeindestraße",
    "geo_shape": {
        "type": "Feature",
        "properties": {},
        "geometry": {
            "type": "LineString",
            "coordinates": [[7.3643, 51.5412], [7.3638, 51.5410]],
        },
    },
}


@pytest.fixture
def connector() -> StrassenabschnitteConnector:
    return StrassenabschnitteConnector()


def test_geometry_unwraps_feature() -> None:
    g = _geometry(RAW["geo_shape"])
    assert g["type"] == "LineString"
    assert len(g["coordinates"]) == 2


def test_geometry_handles_none() -> None:
    assert _geometry(None) is None


def test_normalize(connector: StrassenabschnitteConnector) -> None:
    n = connector.normalize(RAW)
    assert n["source_id"] == "71586-180"
    assert n["length_m"] == 38
    assert n["geom"]["type"] == "LineString"


@pytest.mark.asyncio
async def test_emit_road_with_linestring(connector: StrassenabschnitteConnector) -> None:
    node = (await connector.emit_entities(connector.normalize(RAW)))[0]
    assert isinstance(node, Road)
    assert node.source == "opendata_dortmund_strassenabschnitte"
    assert node.geom["type"] == "LineString"
    assert node.properties["road_type"] == "Anliegerstraße"
    assert node.properties["length_m"] == 38
    assert node.inferred is False


@pytest.mark.asyncio
async def test_emit_no_edges(connector: StrassenabschnitteConnector) -> None:
    n = connector.normalize(RAW)
    nodes = await connector.emit_entities(n)
    assert await connector.emit_edges(n, nodes) == []
