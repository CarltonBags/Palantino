import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type DeepSynergy, type StoredInsight } from "../api";

interface Props {
  onOpenNode: (id: string) => void;
  pipeline?: "classic" | "structural";
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

export default function InsightsView({ onOpenNode, pipeline = "classic" }: Props) {
  const [mode, setMode] = useState<"classic" | "structural" | "complementary" | "deep">(pipeline);
  const structural = mode === "structural";
  const complementary = mode === "complementary";
  const deep = mode === "deep";
  const [items, setItems] = useState<StoredInsight[]>([]);
  const [typeFilter, setTypeFilter] = useState("");
  const [scanning, setScanning] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [deepItems, setDeepItems] = useState<DeepSynergy[]>([]);
  const [deepLoading, setDeepLoading] = useState(false);

  function load() {
    if (mode === "deep") return;
    setErr(null);
    api
      .storedInsights("new", typeFilter || undefined, mode)
      .then(setItems)
      .catch((e) => setErr(String(e)));
  }
  useEffect(load, [typeFilter, mode]);

  async function runDeep() {
    setDeepLoading(true);
    setErr(null);
    try {
      const r = await api.deepSynergies(5);
      setDeepItems(r.synergies);
    } catch (e) {
      setErr(String(e));
    } finally {
      setDeepLoading(false);
    }
  }

  async function decide(id: string, status: "confirmed" | "dismissed") {
    await api.setInsightStatus(id, status);
    setItems((xs) => xs.filter((x) => x.id !== id));
  }

  async function scan() {
    setScanning(true);
    try {
      await api.scanInsights(mode);
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
          <div className="mode-toggle" style={{ margin: "8px 0" }} title="Erkenntnis-Modus">
            <button className={mode === "classic" ? "on" : ""} onClick={() => setMode("classic")}>
              Klassisch
            </button>
            <button className={structural ? "on" : ""} onClick={() => setMode("structural")}>
              Strukturell
            </button>
            <button className={complementary ? "on" : ""} onClick={() => setMode("complementary")}>
              Komplementär
            </button>
            <button className={deep ? "on" : ""} onClick={() => setMode("deep")}>
              Tiefensuche
            </button>
          </div>
          {deep && (
            <div className="muted" style={{ marginBottom: 8 }}>
              5 Synergien, jede recherchiert: die beteiligten Akteure werden im Graphen
              UND auf ihren Websites geprüft — unplausible verworfen und ersetzt.
            </div>
          )}
          {structural && (
            <div className="muted" style={{ marginBottom: 8 }}>
              Räumliche Nähe statt Ähnlichkeit: anstehende Events neben noch nicht
              verbundenen Geschäften (PostGIS-Distanz, gegensätzliche Typen).
            </div>
          )}
          {complementary && (
            <div className="muted" style={{ marginBottom: 8 }}>
              Bedarf trifft Angebot: was eine Veranstaltung braucht und wer es liefern
              kann (z.B. Radtour ↔ Rastmöglichkeit/Fest) — über ein Ressourcen-Vokabular.
            </div>
          )}
          <div className="history-filters" style={{ alignItems: "center" }}>
            {!structural && !deep && (
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
            )}
            {deep ? (
              <button className="primary" onClick={runDeep} disabled={deepLoading}>
                {deepLoading ? "Recherche läuft…" : "5 Synergien recherchieren"}
              </button>
            ) : (
              <button className="primary" onClick={scan} disabled={scanning}>
                {scanning ? "Suche läuft…" : "Neue suchen"}
              </button>
            )}
          </div>
        </div>

        {err && <div className="err">{err}</div>}

        {deep && (
          <>
            {deepLoading && (
              <div className="muted" style={{ marginTop: 16 }}>
                Akteure werden recherchiert (Graph + Websites)… das dauert einen Moment.
              </div>
            )}
            {!deepLoading && deepItems.length === 0 && (
              <div className="muted" style={{ marginTop: 16 }}>
                „5 Synergien recherchieren“ starten.
              </div>
            )}
            {deepItems.map((s, i) => (
              <div className="insight-card" key={i} style={{ borderLeftColor: "#2dd4bf" }}>
                <div className="ic-head">
                  <span className="ic-type" style={{ color: "#2dd4bf" }}>
                    <span className="dot" style={{ background: "#2dd4bf" }} />
                    Synergie · recherchiert
                  </span>
                </div>
                <div className="ic-title">{s.title}</div>
                {s.partners?.length > 0 && (
                  <div className="muted" style={{ marginBottom: 6 }}>{s.partners.join("  ↔  ")}</div>
                )}
                <div className="md ic-desc">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{s.description}</ReactMarkdown>
                </div>
                {s.first_step && (
                  <div className="ic-reason">
                    <span className="ic-label">Erster Schritt</span>
                    {s.first_step}
                  </div>
                )}
                {s.contacts && s.contacts.length > 0 && (
                  <div className="ic-reason">
                    <span className="ic-label">Kontakt</span>
                    {s.contacts.join(", ")}
                  </div>
                )}
                {s.researched_websites && s.researched_websites.length > 0 && (
                  <div className="ic-belege">
                    <span className="ic-label">Recherchiert</span>
                    <div className="cites-row">
                      {s.researched_websites.map((u) => (
                        <a key={u} className="cite-chip" href={u} target="_blank" rel="noreferrer">
                          {u.replace(/^https?:\/\//, "").slice(0, 32)}
                        </a>
                      ))}
                    </div>
                  </div>
                )}
                {s.evidence_node_ids?.length > 0 && (
                  <div className="cites-row" style={{ marginTop: 8 }}>
                    {s.evidence_node_ids.map((id, n) => (
                      <button key={id} className="cite-chip" onClick={() => onOpenNode(id)}>
                        Akteur {n + 1}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </>
        )}

        {!deep && !err && items.length === 0 && !scanning && (
          <div className="muted" style={{ marginTop: 16 }}>
            Noch keine Erkenntnisse. „Neue suchen“ starten — das dauert einen Moment.
          </div>
        )}
        {!deep && scanning && items.length === 0 && (
          <div className="muted" style={{ marginTop: 16 }}>Der Graph wird durchsucht…</div>
        )}

        {!deep && groups.map((g, gi) => (
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
