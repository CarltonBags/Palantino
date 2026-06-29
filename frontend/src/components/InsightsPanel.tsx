import { useEffect, useState } from "react";
import { api, type StoredInsight } from "../api";
import SubgraphView from "./SubgraphView";

interface Props {
  onSelect: (id: string) => void;
}

const TYPE_COLOR: Record<string, string> = {
  inefficiency: "#f59e0b",
  synergy: "#2dd4bf",
};

export default function InsightsPanel({ onSelect }: Props) {
  const [items, setItems] = useState<StoredInsight[]>([]);
  const [typeFilter, setTypeFilter] = useState<string>("");
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);

  function load() {
    setLoading(true);
    setErr(null);
    api
      .storedInsights("new", typeFilter || undefined)
      .then(setItems)
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false));
  }
  useEffect(load, [typeFilter]);

  async function decide(id: string, status: "confirmed" | "dismissed") {
    await api.setInsightStatus(id, status);
    setItems((xs) => xs.filter((x) => x.id !== id));
  }

  return (
    <div>
      <div className="section">
        <h3>Insights — new</h3>
        <div className="row">
          <button className={typeFilter === "" ? "primary" : ""} onClick={() => setTypeFilter("")}>
            All
          </button>
          <button
            className={typeFilter === "inefficiency" ? "primary" : ""}
            onClick={() => setTypeFilter("inefficiency")}
          >
            Inefficiency
          </button>
          <button
            className={typeFilter === "synergy" ? "primary" : ""}
            onClick={() => setTypeFilter("synergy")}
          >
            Synergy
          </button>
        </div>
      </div>

      {loading && <div className="muted">Loading…</div>}
      {err && <div className="err">{err}</div>}
      {!loading && !err && items.length === 0 && (
        <div className="muted">
          No new insights. Run the scan flow (<code>run_insight_scan</code>) to generate some.
        </div>
      )}

      {items.map((it) => (
        <div className="insight" key={it.id}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <span
              className="tag"
              style={{ color: TYPE_COLOR[it.insight_type] ?? "var(--text)" }}
            >
              {it.insight_type}
            </span>
            <span className="muted">{Math.round(it.confidence * 100)}%</span>
          </div>
          <div className="it" style={{ marginTop: 6 }}>
            {it.title}
          </div>
          <div>{it.description}</div>
          {it.reasoning_trace && (
            <div className="muted" style={{ marginTop: 4 }}>
              {it.reasoning_trace}
            </div>
          )}
          <div className="muted" style={{ marginTop: 4 }}>
            via {it.generator}
          </div>

          <div className="section" style={{ marginTop: 8 }}>
            <h3>Evidence ({it.evidence_node_ids.length})</h3>
            <button onClick={() => setExpanded(expanded === it.id ? null : it.id)}>
              {expanded === it.id ? "hide graph" : "view graph"}
            </button>
            {expanded === it.id && (
              <SubgraphView nodeIds={it.evidence_node_ids} onSelect={onSelect} />
            )}
          </div>

          <div className="row" style={{ marginTop: 8 }}>
            <button className="primary" onClick={() => decide(it.id, "confirmed")}>
              Confirm
            </button>
            <button onClick={() => decide(it.id, "dismissed")}>Dismiss</button>
          </div>
        </div>
      ))}
    </div>
  );
}
