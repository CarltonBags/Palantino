import { useEffect, useState } from "react";
import { api, type SourceCatalogEntry } from "../api";

const SHAPE_LABEL: Record<string, string> = {
  snapshot: "snapshot",
  event_stream: "stream",
  reference: "reference",
};

function dot(entry: SourceCatalogEntry): { color: string; title: string } {
  if (!entry.enabled) return { color: "#6b7280", title: "deaktiviert" };
  if (!entry.last_run) return { color: "#6b7280", title: "nie gelaufen" };
  if (entry.last_run.status === "ok") return { color: "#4ade80", title: "ok" };
  if (entry.last_run.status === "error") return { color: "#f87171", title: "Fehler" };
  return { color: "#fbbf24", title: entry.last_run.status };
}

export default function SourcesPanel() {
  const [items, setItems] = useState<SourceCatalogEntry[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.sourceCatalog().then(setItems).catch((e) => setErr(String(e)));
  }, []);

  if (err) return <div className="err">{err}</div>;

  const runCount = items.filter((s) => s.last_run).length;

  return (
    <div className="section">
      <h3>
        Quellen ({runCount}/{items.length} geladen)
      </h3>
      {items.map((s) => {
        const d = dot(s);
        return (
          <div className="insight" key={s.name} style={{ padding: 8 }}>
            <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ fontSize: 13 }}>
                <span className="dot" style={{ background: d.color, display: "inline-block" }} title={d.title} />{" "}
                {s.name}
              </span>
              <span className="tag">{SHAPE_LABEL[s.shape] ?? s.shape}</span>
            </div>
            <div className="muted" style={{ marginTop: 4 }}>
              {s.description}
            </div>
            <div className="muted" style={{ marginTop: 2 }}>
              cron {s.cadence_cron}
              {s.last_run
                ? ` · ${s.last_run.nodes_written}n/${s.last_run.edges_written}e`
                : s.enabled
                  ? " · nie gelaufen"
                  : " · deaktiviert"}
            </div>
          </div>
        );
      })}
    </div>
  );
}
