import { useEffect, useMemo, useState } from "react";
import { api, type FeatureCollection } from "./api";
import { COLOR_BY_TYPE, GEO_NODE_TYPES } from "./nodeTypes";
import MapView from "./components/MapView";
import Sidebar from "./components/Sidebar";
import IngestionStatus from "./components/IngestionStatus";
import ChatView from "./components/ChatView";

const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

// Sources kept in the graph/data but hidden from the map to cut clutter. The
// ratswahl precinct results are one Event point per polling station (~hundreds,
// densely overlapping) and add no spatial insight on the map.
const MAP_HIDDEN_SOURCES = new Set(["opendata_dortmund_wahl_stimmbezirk"]);

export default function App() {
  const [activeTypes, setActiveTypes] = useState<Set<string>>(
    () => new Set(GEO_NODE_TYPES.map((t) => t.type)),
  );
  const [byType, setByType] = useState<Record<string, FeatureCollection>>({});
  const [areas, setAreas] = useState<GeoJSON.FeatureCollection>(EMPTY);
  const [roads, setRoads] = useState<FeatureCollection>({ type: "FeatureCollection", features: [] });
  const [showRoads, setShowRoads] = useState(true);
  const [asOf, setAsOf] = useState<string>(""); // "" = current
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [view, setView] = useState<"map" | "chat">("map");

  // Changing the as-of instant invalidates all cached point layers.
  useEffect(() => {
    setByType({});
  }, [asOf]);

  // District boundaries (once).
  useEffect(() => {
    api
      .geoAreas("statistischer_bezirk")
      .then((rows) =>
        setAreas({
          type: "FeatureCollection",
          features: rows
            .filter((r) => r.geometry)
            .map((r) => ({
              type: "Feature" as const,
              geometry: r.geometry,
              properties: { id: r.id, label: r.label },
            })),
        }),
      )
      .catch(() => setAreas(EMPTY));
    // Road segments (LineStrings) — capped by the API for map performance.
    api
      .geoNodes("Road")
      .then(setRoads)
      .catch(() => setRoads({ type: "FeatureCollection", features: [] }));
  }, []);

  const roadsShown: FeatureCollection = useMemo(
    () => (showRoads ? roads : { type: "FeatureCollection", features: [] }),
    [showRoads, roads],
  );

  // Fetch each active type's points lazily, cache by type.
  useEffect(() => {
    activeTypes.forEach((t) => {
      if (byType[t]) return;
      api
        .geoNodes(t, undefined, asOf || undefined)
        .then((fc) => setByType((prev) => ({ ...prev, [t]: fc })))
        .catch(() => setByType((prev) => ({ ...prev, [t]: { type: "FeatureCollection", features: [] } })));
    });
  }, [activeTypes, byType, asOf]);

  // Merge active layers into one collection, tagging each feature with its colour.
  const points: FeatureCollection = useMemo(() => {
    const features = Array.from(activeTypes).flatMap((t) => {
      const fc = byType[t];
      if (!fc) return [];
      const color = COLOR_BY_TYPE[t] ?? "#2dd4bf";
      return fc.features
        .filter((f) => !MAP_HIDDEN_SOURCES.has(String((f.properties as { source?: string })?.source ?? "")))
        .map((f) => ({
          ...f,
          properties: { ...f.properties, color },
        }));
    });
    return { type: "FeatureCollection", features };
  }, [activeTypes, byType]);

  function toggleType(t: string) {
    setActiveTypes((prev) => {
      const next = new Set(prev);
      next.has(t) ? next.delete(t) : next.add(t);
      return next;
    });
  }

  return (
    <>
      <div className="view-switch">
        <button className={view === "map" ? "active" : ""} onClick={() => setView("map")}>
          Karte
        </button>
        <button className={view === "chat" ? "active" : ""} onClick={() => setView("chat")}>
          Chat
        </button>
      </div>

      {view === "chat" ? (
        <ChatView
          onOpenNode={(id) => {
            setSelectedId(id);
            setView("map");
          }}
        />
      ) : (
        <div className="app">
          <Sidebar
            activeTypes={activeTypes}
            toggleType={toggleType}
            showRoads={showRoads}
            toggleRoads={() => setShowRoads((v) => !v)}
            asOf={asOf}
            setAsOf={setAsOf}
            selectedId={selectedId}
            onSelect={setSelectedId}
          />
          <div className="map-wrap">
            <MapView points={points} areas={areas} roads={roadsShown} onSelect={setSelectedId} />
          </div>
          <IngestionStatus />
        </div>
      )}
    </>
  );
}
