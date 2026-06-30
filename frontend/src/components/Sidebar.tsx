import { useEffect, useRef, useState } from "react";
import { api, type SearchHit } from "../api";
import { GEO_NODE_TYPES, ROAD_COLOR } from "../nodeTypes";
import NodeDetail from "./NodeDetail";
import ResolutionPanel from "./ResolutionPanel";
import InsightsPanel from "./InsightsPanel";
import SourcesPanel from "./SourcesPanel";

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

type Tab = "explore" | "insights" | "sources";

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
            Erkunden
          </button>
          <button className={tab === "insights" ? "primary" : ""} onClick={() => setTab("insights")}>
            Erkenntnisse
          </button>
          <button className={tab === "sources" ? "primary" : ""} onClick={() => setTab("sources")}>
            Quellen
          </button>
        </div>
      )}

      {selectedId ? (
        <>
          <button onClick={() => onSelect(null)} style={{ marginTop: 12 }}>
            ← zurück zur Karte
          </button>
          <div className="section">
            <NodeDetail nodeId={selectedId} onSelect={onSelect} />
          </div>
        </>
      ) : tab === "insights" ? (
        <InsightsPanel onSelect={onSelect} />
      ) : tab === "sources" ? (
        <SourcesPanel />
      ) : (
        <>
          <div className="section">
            <h3>Suche</h3>
            <input
              type="search"
              placeholder="Knoten suchen (≥2 Zeichen)…"
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
            <h3>Ebenen</h3>
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
              Straßen (Abschnitte)
            </div>
          </div>

          <div className="section">
            <h3>Zeitreise (Stand)</h3>
            <input
              type="datetime-local"
              value={asOf}
              onChange={(e) => setAsOf(e.target.value)}
            />
            <div className="muted" style={{ marginTop: 6 }}>
              {asOf ? `Graph zum Stand ${asOf}` : "Aktueller Graph"}
              {asOf && (
                <>
                  {" "}
                  <a style={{ cursor: "pointer" }} onClick={() => setAsOf("")}>
                    zurücksetzen
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
