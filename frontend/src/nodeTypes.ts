// Node types that carry geometry, with display colours for the map + legend.
export interface NodeTypeMeta {
  type: string;
  label: string;
  color: string;
}

export const GEO_NODE_TYPES: NodeTypeMeta[] = [
  { type: "POI", label: "Civic POIs", color: "#2dd4bf" },
  { type: "ConstructionSite", label: "Construction / roadworks", color: "#f59e0b" },
  { type: "TransitStop", label: "Transit stops", color: "#3b82f6" },
  { type: "WeatherObservation", label: "Weather", color: "#a78bfa" },
  { type: "AirQualityObservation", label: "Air quality", color: "#84cc16" },
  { type: "Event", label: "Events / incidents", color: "#ef4444" },
  { type: "Meeting", label: "Council meetings", color: "#ec4899" },
];

export const COLOR_BY_TYPE: Record<string, string> = Object.fromEntries(
  GEO_NODE_TYPES.map((t) => [t.type, t.color]),
);

export const ROAD_COLOR = "#64748b";

export const DORTMUND_CENTER = { longitude: 7.4653, latitude: 51.5136, zoom: 11 };
