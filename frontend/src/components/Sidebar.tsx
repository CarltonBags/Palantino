import { useEffect, useRef, useState } from "react";
import { api, type SearchHit } from "../api";
import { GEO_NODE_TYPES, ROAD_COLOR } from "../nodeTypes";
import NodeDetail from "./NodeDetail";
import ResolutionPanel from "./ResolutionPanel";
import InsightsPanel from "./InsightsPanel";

interface Props {
  activeTypes: Set<string>;
  toggleType: (t: string) => void;
  showRoads: boolean;
  toggleRoads: () => void;
  asOf: string;
  setAsOf: (v: string) => void;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
}

type Tab = "explore" | "insights";

export default function Sidebar({
  activeTypes,
  toggleType,
  showRoads,
  toggleRoads,
  asOf,
  setAsOf,
  selectedId,
  onSelect,
}: Props) {
  const [tab, setTab] = useState<Tab>("explore");
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const debounce = useRef<number | undefined>(undefined);

  useEffect(() => {
    window.clearTimeout(debounce.current);
    if (q.trim().length < 2) {
      setHits([]);
      return;
    }
    debounce.current = window.setTimeout(() => {
      api.search(q.trim()).then(setHits).catch(() => setHits([]));
    }, 250);
    return () => window.clearTimeout(debounce.current);
  }, [q]);

  return (
    <div className="sidebar">
      <div className="brand">
        civic-graph <small>· Dortmund</small>
      </div>

      {!selectedId && (
        <div className="row" style={{ marginTop: 12 }}>
          <button className={tab === "explore" ? "primary" : ""} onClick={() => setTab("explore")}>
            Explore
          </button>
          <button className={tab === "insights" ? "primary" : ""} onClick={() => setTab("insights")}>
            Insights
          </button>
        </div>
      )}

      {selectedId ? (
        <>
          <button onClick={() => onSelect(null)} style={{ marginTop: 12 }}>
            ← back to map
          </button>
          <div className="section">
            <NodeDetail nodeId={selectedId} onSelect={onSelect} />
          </div>
        </>
      ) : tab === "insights" ? (
        <InsightsPanel onSelect={onSelect} />
      ) : (
        <>
          <div className="section">
            <h3>Search</h3>
            <input
              type="search"
              placeholder="Search nodes (≥2 chars)…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            <div style={{ marginTop: 8 }}>
              {hits.map((h) => (
                <div className="hit" key={h.id} onClick={() => onSelect(h.id)}>
                  <span className="ttl">{h.label}</span>
                  <span className="meta">
                    {h.node_type} · {h.source} · {h.score.toFixed(2)}
                  </span>
                </div>
              ))}
            </div>
          </div>

          <div className="section">
            <h3>Layers</h3>
            {GEO_NODE_TYPES.map((t) => (
              <div
                className={`legend-row ${activeTypes.has(t.type) ? "" : "off"}`}
                key={t.type}
                onClick={() => toggleType(t.type)}
              >
                <span className="dot" style={{ background: t.color }} />
                {t.label}
              </div>
            ))}
            <div
              className={`legend-row ${showRoads ? "" : "off"}`}
              onClick={toggleRoads}
            >
              <span className="dot" style={{ background: ROAD_COLOR }} />
              Roads (segments)
            </div>
          </div>

          <div className="section">
            <h3>Time travel (as of)</h3>
            <input
              type="datetime-local"
              value={asOf}
              onChange={(e) => setAsOf(e.target.value)}
            />
            <div className="muted" style={{ marginTop: 6 }}>
              {asOf ? `Showing graph as of ${asOf}` : "Showing current graph"}
              {asOf && (
                <>
                  {" "}
                  <a style={{ cursor: "pointer" }} onClick={() => setAsOf("")}>
                    reset
                  </a>
                </>
              )}
            </div>
          </div>

          <ResolutionPanel onSelect={onSelect} />
        </>
      )}
    </div>
  );
}
