import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type StoredInsight } from "../api";

interface Props {
  onOpenNode: (id: string) => void;
}

const TYPE_LABEL: Record<string, string> = {
  inefficiency: "Ineffizienz",
  synergy: "Synergie",
  scandal: "Auffälligkeit",
};
const TYPE_COLOR: Record<string, string> = {
  inefficiency: "#f59e0b",
  synergy: "#2dd4bf",
  scandal: "#ef4444",
};

export default function InsightsView({ onOpenNode }: Props) {
  const [items, setItems] = useState<StoredInsight[]>([]);
  const [typeFilter, setTypeFilter] = useState("");
  const [scanning, setScanning] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  function load() {
    setErr(null);
    api
      .storedInsights("new", typeFilter || undefined)
      .then(setItems)
      .catch((e) => setErr(String(e)));
  }
  useEffect(load, [typeFilter]);

  async function decide(id: string, status: "confirmed" | "dismissed") {
    await api.setInsightStatus(id, status);
    setItems((xs) => xs.filter((x) => x.id !== id));
  }

  async function scan() {
    setScanning(true);
    try {
      await api.scanInsights();
      setTimeout(load, 10000);
      setTimeout(() => {
        load();
        setScanning(false);
      }, 25000);
    } catch (e) {
      setErr(String(e));
      setScanning(false);
    }
  }

  return (
    <div className="chat">
      <div className="chat-thread">
        <div className="history-head">
          <div className="chat-hero-title" style={{ fontSize: 26 }}>
            Erkenntnisse über Dortmund
          </div>
          <div className="history-filters" style={{ alignItems: "center" }}>
            <div className="row">
              <button className={typeFilter === "" ? "primary" : ""} onClick={() => setTypeFilter("")}>
                Alle
              </button>
              <button
                className={typeFilter === "synergy" ? "primary" : ""}
                onClick={() => setTypeFilter("synergy")}
              >
                Synergien
              </button>
              <button
                className={typeFilter === "inefficiency" ? "primary" : ""}
                onClick={() => setTypeFilter("inefficiency")}
              >
                Ineffizienzen
              </button>
            </div>
            <button className="primary" onClick={scan} disabled={scanning}>
              {scanning ? "Suche läuft…" : "Neue suchen"}
            </button>
          </div>
        </div>

        {err && <div className="err">{err}</div>}
        {!err && items.length === 0 && !scanning && (
          <div className="muted" style={{ marginTop: 16 }}>
            Noch keine Erkenntnisse. „Neue suchen“ starten — das dauert einen Moment.
          </div>
        )}
        {scanning && items.length === 0 && (
          <div className="muted" style={{ marginTop: 16 }}>Der Graph wird durchsucht…</div>
        )}

        {items.slice(0, 12).map((it) => (
          <div className="insight-card" key={it.id}>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <span className="tag" style={{ color: TYPE_COLOR[it.insight_type] ?? "var(--text)" }}>
                {TYPE_LABEL[it.insight_type] ?? it.insight_type}
              </span>
              <span className="muted">
                {Math.round(it.confidence * 100)}% · {it.generator}
              </span>
            </div>
            <div className="it" style={{ marginTop: 6, fontSize: 15 }}>{it.title}</div>
            <div className="md">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{it.description}</ReactMarkdown>
            </div>
            {it.reasoning_trace && (
              <div className="muted" style={{ marginTop: 4 }}>{it.reasoning_trace}</div>
            )}
            {it.evidence_node_ids.length > 0 && (
              <div className="cites-row" style={{ marginTop: 8 }}>
                {it.evidence_node_ids.slice(0, 8).map((id, n) => (
                  <button key={id} className="cite-chip" onClick={() => onOpenNode(id)}>
                    Beleg {n + 1}
                  </button>
                ))}
              </div>
            )}
            <div className="row" style={{ marginTop: 8 }}>
              <button className="primary" onClick={() => decide(it.id, "confirmed")}>
                Bestätigen
              </button>
              <button onClick={() => decide(it.id, "dismissed")}>Verwerfen</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
