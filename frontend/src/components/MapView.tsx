import Map, { Layer, Source, type MapLayerMouseEvent } from "react-map-gl/maplibre";
import type { FeatureCollection } from "../api";
import { DORTMUND_CENTER } from "../nodeTypes";

// Free MapLibre demo basemap — no API token required.
const STYLE = "https://demotiles.maplibre.org/style.json";

interface Props {
  points: FeatureCollection; // each feature.properties has color + id + node_type
  areas: GeoJSON.FeatureCollection;
  roads: FeatureCollection; // Road LineStrings (properties has id)
  onSelect: (id: string) => void;
}

export default function MapView({ points, areas, roads, onSelect }: Props) {
  function handleClick(e: MapLayerMouseEvent) {
    const feat = e.features?.[0];
    if (!feat) return;
    const props = feat.properties ?? {};
    // Cluster click → zoom to the level where it splits apart.
    if (props.cluster_id != null) {
      const map = e.target;
      const src = map.getSource("nodes") as unknown as {
        getClusterExpansionZoom: (id: number) => Promise<number>;
      };
      const coords = (feat.geometry as GeoJSON.Point).coordinates as [number, number];
      src
        .getClusterExpansionZoom(props.cluster_id as number)
        .then((zoom) => map.easeTo({ center: coords, zoom }))
        .catch(() => undefined);
      return;
    }
    const id = props.id as string | undefined;
    if (id) onSelect(id);
  }

  return (
    <Map
      initialViewState={DORTMUND_CENTER}
      mapStyle={STYLE}
      interactiveLayerIds={["clusters", "unclustered-point", "road-lines"]}
      onClick={handleClick}
      style={{ width: "100%", height: "100%" }}
    >
      <Source id="areas" type="geojson" data={areas}>
        <Layer
          id="area-fill"
          type="fill"
          paint={{ "fill-color": "#2dd4bf", "fill-opacity": 0.04 }}
        />
        <Layer
          id="area-line"
          type="line"
          paint={{ "line-color": "#2dd4bf", "line-opacity": 0.35, "line-width": 1 }}
        />
      </Source>

      <Source id="roads" type="geojson" data={roads}>
        <Layer
          id="road-lines"
          type="line"
          paint={{ "line-color": "#64748b", "line-opacity": 0.5, "line-width": 1.5 }}
        />
      </Source>

      <Source
        id="nodes"
        type="geojson"
        data={points}
        cluster
        clusterRadius={50}
        clusterMaxZoom={14}
      >
        {/* Cluster bubbles: radius + colour step up with how many points merged. */}
        <Layer
          id="clusters"
          type="circle"
          filter={["has", "point_count"]}
          paint={{
            "circle-color": [
              "step",
              ["get", "point_count"],
              "#3b82f6", 25, "#f59e0b", 100, "#ef4444",
            ],
            "circle-radius": ["step", ["get", "point_count"], 12, 25, 18, 100, 26],
            "circle-opacity": 0.85,
            "circle-stroke-color": "#0f1115",
            "circle-stroke-width": 1,
          }}
        />
        <Layer
          id="cluster-count"
          type="symbol"
          filter={["has", "point_count"]}
          layout={{
            "text-field": ["get", "point_count_abbreviated"],
            "text-font": ["Open Sans Semibold"],
            "text-size": 12,
          }}
          paint={{ "text-color": "#ffffff" }}
        />
        {/* Individual (unclustered) points keep their per-type colour. */}
        <Layer
          id="unclustered-point"
          type="circle"
          filter={["!", ["has", "point_count"]]}
          paint={{
            "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 3, 14, 6],
            "circle-color": ["coalesce", ["get", "color"], "#2dd4bf"],
            "circle-stroke-color": "#0f1115",
            "circle-stroke-width": 1,
            "circle-opacity": 0.85,
          }}
        />
      </Source>
    </Map>
  );
}
