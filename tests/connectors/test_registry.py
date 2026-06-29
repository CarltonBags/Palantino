"""
Conformance test: every registered connector honours the BaseConnector contract.

This is the uniform guard across all connectors — a new one that forgets a
method, a shape, or a source_name fails here.
"""

import inspect

import pytest

from connectors.base import BaseConnector, ConnectorShape
from connectors.registry import REGISTRY, registry_catalog

VALID_SHAPES = {ConnectorShape.SNAPSHOT, ConnectorShape.EVENT_STREAM, ConnectorShape.REFERENCE}
REQUIRED_METHODS = ("fetch", "normalize", "emit_entities", "emit_edges")


def test_registry_non_empty() -> None:
    assert len(REGISTRY) >= 19


def test_source_names_unique() -> None:
    names = [s.name for s in REGISTRY]
    assert len(names) == len(set(names)), "duplicate connector source_name"


@pytest.mark.parametrize("spec", REGISTRY, ids=lambda s: s.name)
def test_connector_contract(spec) -> None:
    cls = spec.connector_cls
    assert issubclass(cls, BaseConnector)
    assert isinstance(cls.source_name, str) and cls.source_name
    assert cls.shape in VALID_SHAPES
    for method in REQUIRED_METHODS:
        assert callable(getattr(cls, method, None)), f"{cls.__name__} missing {method}"


@pytest.mark.parametrize("spec", REGISTRY, ids=lambda s: s.name)
def test_async_interface(spec) -> None:
    cls = spec.connector_cls
    # fetch / emit_* are async; normalize is sync.
    assert inspect.iscoroutinefunction(cls.emit_entities)
    assert inspect.iscoroutinefunction(cls.emit_edges)
    assert inspect.isasyncgenfunction(cls.fetch)


def test_cadence_present() -> None:
    for s in REGISTRY:
        assert s.cadence_cron.count(" ") == 4, f"{s.name}: bad cron '{s.cadence_cron}'"


def test_catalog_shape() -> None:
    cat = registry_catalog()
    assert len(cat) == len(REGISTRY)
    assert {"name", "shape", "cadence_cron", "description", "enabled"} <= set(cat[0])
