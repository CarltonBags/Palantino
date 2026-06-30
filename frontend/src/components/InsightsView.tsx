import { useEffect, useMemo, useState } from "react";
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

  // Group by the scan run that produced them (newest run first), so a fresh
  // "Neue suchen" batch is clearly separated from older ones.
  const groups = useMemo(() => {
    const m = new Map<string, StoredInsight[]>();
    for (const it of items) {
      const key = it.scan_id ?? "older";
      if (!m.has(key)) m.set(key, []);
      m.get(key)!.push(it);
    }
    const arr = [...m.entries()].map(([key, its]) => ({
      key,
      older: key === "older",
      time: its.reduce((mx, x) => (x.created_at > mx ? x.created_at : mx), its[0].created_at),
      items: [...its].sort((a, b) => b.confidence - a.confidence),
    }));
    arr.sort((a, b) => (a.older ? 1 : b.older ? -1 : a.time < b.time ? 1 : -1));
    return arr;
  }, [items]);

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

        {groups.map((g, gi) => (
          <div key={g.key} className="scan-group">
            <div className="scan-group-head">
              {g.older
                ? "Frühere Erkenntnisse"
                : `Suche vom ${new Date(g.time).toLocaleString("de-DE")}`}
              {gi === 0 && !g.older ? " · neueste" : ""} · {g.items.length}
            </div>
            {g.items.map((it) => {
              const color = TYPE_COLOR[it.insight_type] ?? "var(--accent)";
              return (
                <div className="insight-card" key={it.id} style={{ borderLeftColor: color }}>
                  <div className="ic-head">
                    <span className="ic-type" style={{ color }}>
                      <span className="dot" style={{ background: color }} />
                      {TYPE_LABEL[it.insight_type] ?? it.insight_type}
                    </span>
                    <span className="ic-conf">{Math.round(it.confidence * 100)}%</span>
                  </div>
                  <div className="ic-title">{it.title}</div>
                  <div className="md ic-desc">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{it.description}</ReactMarkdown>
                  </div>
                  {it.reasoning_trace && (
                    <div className="ic-reason">
                      <span className="ic-label">Begründung</span>
                      {it.reasoning_trace}
                    </div>
                  )}
                  {it.evidence_node_ids.length > 0 && (
                    <div className="ic-belege">
                      <span className="ic-label">Belege</span>
                      <div className="cites-row">
                        {it.evidence_node_ids.slice(0, 8).map((id, n) => (
                          <button key={id} className="cite-chip" onClick={() => onOpenNode(id)}>
                            Beleg {n + 1}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                  <div className="ic-actions">
                    <button className="primary" onClick={() => decide(it.id, "confirmed")}>
                      Bestätigen
                    </button>
                    <button onClick={() => decide(it.id, "dismissed")}>Verwerfen</button>
                  </div>
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}
