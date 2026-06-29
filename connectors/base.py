"""
Abstract base connector. Every connector inherits from this and implements
fetch / normalize / emit_entities / emit_edges.

Connector shapes (per ingestion-and-temporal-design.md):
  snapshot    — poll & append timestamped observations
  event_stream — fetch new items since last seen
  reference   — full refresh + diff
"""

from __future__ import annotations

import abc
import logging
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from config import settings
from ontology.edges import EdgeBase
from ontology.nodes import NodeBase

logger = logging.getLogger(__name__)


class ConnectorShape:
    SNAPSHOT = "snapshot"
    EVENT_STREAM = "event_stream"
    REFERENCE = "reference"


class BaseConnector(abc.ABC):
    """
    shape: ConnectorShape constant
    source_name: stable identifier stored in nodes/edges.source
    """

    shape: str
    source_name: str

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(
            headers={"User-Agent": settings.bot_user_agent},
            timeout=30.0,
            follow_redirects=True,
        )

    async def __aenter__(self) -> "BaseConnector":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self._http.aclose()

    # ── interface ──────────────────────────────────────────────────────────────

    @abc.abstractmethod
    async def fetch(self, checkpoint: dict[str, Any] | None = None) -> AsyncGenerator[Any, None]:
        """Yield raw items from the source. Accepts last-run checkpoint for incremental."""
        ...

    @abc.abstractmethod
    def normalize(self, raw: Any) -> dict[str, Any]:
        """Map one raw item to the common intermediate schema."""
        ...

    @abc.abstractmethod
    async def emit_entities(self, normalized: dict[str, Any]) -> list[NodeBase]:
        """Yield typed NodeBase instances from a normalized record."""
        ...

    @abc.abstractmethod
    async def emit_edges(self, normalized: dict[str, Any], nodes: list[NodeBase]) -> list[EdgeBase]:
        """Yield typed EdgeBase instances linking the emitted nodes."""
        ...

    # ── helpers ────────────────────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
    async def _get(self, url: str, **kwargs: Any) -> httpx.Response:
        resp = await self._http.get(url, **kwargs)
        resp.raise_for_status()
        return resp

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
    async def _post(self, url: str, **kwargs: Any) -> httpx.Response:
        resp = await self._http.post(url, **kwargs)
        resp.raise_for_status()
        return resp

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _provenance(self, source_id: str | None, source_url: str | None) -> dict[str, Any]:
        return {
            "source": self.source_name,
            "source_id": source_id,
            "source_url": source_url,
            "observed_at": self._now(),
        }
