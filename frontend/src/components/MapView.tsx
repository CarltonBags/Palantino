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
    const id = feat?.properties?.id as string | undefined;
    if (id) onSelect(id);
  }

  return (
    <Map
      initialViewState={DORTMUND_CENTER}
      mapStyle={STYLE}
      interactiveLayerIds={["node-points", "road-lines"]}
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

      <Source id="nodes" type="geojson" data={points}>
        <Layer
          id="node-points"
          type="circle"
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
