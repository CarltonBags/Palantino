"""
Connector registry — the single source of truth for what sources exist.

Each entry pairs a connector class with its ingestion cadence and a one-line
description. The registry powers:
  - a uniform conformance test (every connector honours the BaseConnector
    contract: shape, source_name, the four methods),
  - the /status/sources API (the full source catalog, including sources that
    have never run yet), and documentation.

The flow definitions in ingestion/flows.py still own the per-connector
orchestration (spatial joins, checkpoints); this is the catalog, not the runner.
"""

from __future__ import annotations

from dataclasses import dataclass

from connectors.autobahn.connector import AutobahnConnector
from connectors.base import BaseConnector
from connectors.baustellen.connector import BaustellenConnector
from connectors.brightsky.connector import BrightSkyConnector
from connectors.dortmund_events.connector import DortmundEventsConnector
from connectors.geo_spine.connector import GeoSpineConnector
from connectors.gremienniederschriften.connector import GremienNiederschriftenConnector
from connectors.gremientermine.connector import GremienTermineConnector
from connectors.gtfs_realtime.connector import GtfsRealtimeConnector
from connectors.gtfs_static.connector import GtfsStaticConnector
from connectors.lanuv_air.connector import LanuvAirConnector
from connectors.nordstadtblogger.connector import NordstadtbloggerConnector
from connectors.ods_pois.connector import OdsPoisConnector
from connectors.ods_stats.connector import OdsStatsConnector
from connectors.oparl.connector import OParlConnector
from connectors.overpass.connector import OverpassConnector
from connectors.polizei_rss.connector import PolizeiRssConnector
from connectors.strassen.connector import StrassenConnector
from connectors.strassenabschnitte.connector import StrassenabschnitteConnector
from connectors.vergabe_nrw.connector import VergabeNrwConnector
from connectors.wirindortmund.connector import WirInDortmundConnector
from connectors.wahlergebnisse.connector import WahlergebnisseConnector
from connectors.wahlergebnisse_stimmbezirk.connector import (
    WahlergebnisseStimmbezirkConnector,
)


@dataclass(frozen=True)
class ConnectorSpec:
    connector_cls: type[BaseConnector]
    cadence_cron: str
    description: str
    enabled: bool = True

    @property
    def name(self) -> str:
        return self.connector_cls.source_name

    @property
    def shape(self) -> str:
        return self.connector_cls.shape


REGISTRY: list[ConnectorSpec] = [
    ConnectorSpec(GeoSpineConnector, "0 3 1 * *", "District boundaries (geographic spine)"),
    ConnectorSpec(OverpassConnector, "0 4 * * 3", "OSM businesses / POIs"),
    ConnectorSpec(
        OParlConnector, "0 6 * * *",
        "Council OParl (DISABLED by Dortmund in production)", enabled=False,
    ),
    ConnectorSpec(GremienTermineConnector, "30 6 * * *", "Upcoming committee meeting dates"),
    ConnectorSpec(
        GremienNiederschriftenConnector, "45 6 * * *",
        "Council minutes + Beschlüsse (meetings, agenda items, resolutions)",
    ),
    ConnectorSpec(WahlergebnisseConnector, "0 4 4 * *", "City-wide election results + council seats"),
    ConnectorSpec(
        WahlergebnisseStimmbezirkConnector, "30 4 4 * *",
        "Ratswahl results per Stimmbezirk (precinct)",
    ),
    ConnectorSpec(BrightSkyConnector, "5 * * * *", "Weather (Bright Sky / DWD)"),
    ConnectorSpec(PolizeiRssConnector, "25 * * * *", "Police press releases (RSS)"),
    ConnectorSpec(
        NordstadtbloggerConnector, "40 */6 * * *",
        "Independent local news (Nordstadtblogger WP REST)",
    ),
    ConnectorSpec(
        WirInDortmundConnector, "50 */6 * * *",
        "Local news portal (Wir in Dortmund RSS)",
    ),
    ConnectorSpec(
        DortmundEventsConnector, "0 8 * * *",
        "Public events calendar (fairs, festivals, concerts) from dortmund.de",
    ),
    ConnectorSpec(BaustellenConnector, "0 5 * * *", "City construction sites / roadworks"),
    ConnectorSpec(
        OdsPoisConnector, "0 4 2 * *",
        "Civic POIs (XErleben: schools, weekly markets, event venues)",
    ),
    ConnectorSpec(OdsStatsConnector, "0 4 3 * *", "Demographics by Stadtbezirk"),
    ConnectorSpec(VergabeNrwConnector, "0 7 * * *", "Public tenders (Vergabe Metropole Ruhr)"),
    ConnectorSpec(GtfsStaticConnector, "0 4 * * 3", "Transit schedules (GTFS static)"),
    ConnectorSpec(GtfsRealtimeConnector, "*/10 * * * *", "Live transit disruptions (GTFS-RT)"),
    ConnectorSpec(LanuvAirConnector, "15 * * * *", "Air quality (LANUV LUQS)"),
    ConnectorSpec(AutobahnConnector, "*/30 * * * *", "Live motorway traffic (Autobahn GmbH)"),
    ConnectorSpec(StrassenConnector, "0 3 2 * *", "Street register (name gazetteer + districts)"),
    ConnectorSpec(
        StrassenabschnitteConnector, "30 3 2 * *",
        "Street segments with geometry (road layer)",
    ),
]


def registry_catalog() -> list[dict[str, str | bool]]:
    """Plain-dict view of the registry for the API / docs."""
    return [
        {
            "name": s.name,
            "shape": s.shape,
            "cadence_cron": s.cadence_cron,
            "description": s.description,
            "enabled": s.enabled,
        }
        for s in REGISTRY
    ]
