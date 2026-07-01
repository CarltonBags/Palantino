import { useState } from "react";
import { api, type TellerrandRec } from "../api";

interface Props {
  onOpenNode: (id: string) => void;
}

const EXAMPLES = [
  "Ich bin in einem Schachverein",
  "Fotografie",
  "Ehrenamt im Tierheim",
  "Ich spiele Fußball beim TuS",
  "Klettern",
];

export default function TellerrandView({ onOpenNode }: Props) {
  const [interest, setInterest] = useState("");
  const [recs, setRecs] = useState<TellerrandRec[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [asked, setAsked] = useState(false);

  async function go(q: string) {
    q = q.trim();
    if (!q || loading) return;
    setInterest(q);
    setLoading(true);
    setErr(null);
    setAsked(true);
    try {
      const r = await api.tellerrand(q, 5);
      setRecs(r.recommendations);
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="chat">
      <div className="chat-thread">
        {!asked && (
          <div className="chat-hero">
            <div className="chat-hero-title">Über den Tellerrand</div>
            <div className="chat-hero-sub">
              Gib ein Interesse oder deinen Verein ein — wir zeigen dir benachbarte
              Felder, die deinen Horizont erweitern, mit echten Dortmunder Angeboten.
            </div>
            <div className="chat-examples">
              {EXAMPLES.map((ex) => (
                <button key={ex} className="chat-chip" onClick={() => go(ex)}>
                  {ex}
                </button>
              ))}
            </div>
          </div>
        )}

        {err && <div className="err">{err}</div>}
        {loading && (
          <div className="muted" style={{ marginTop: 16 }}>Denke über den Tellerrand hinaus…</div>
        )}

        {recs.map((r, i) => (
          <div className="insight-card" key={i} style={{ borderLeftColor: "var(--accent)" }}>
            <div className="ic-title">{r.interest}</div>
            <div className="md ic-desc" style={{ marginTop: 4 }}>{r.bridge}</div>
            {r.options.length > 0 && (
              <div className="ic-belege">
                <span className="ic-label">In Dortmund</span>
                <div className="cites-row">
                  {r.options.map((o) => (
                    <button key={o.id} className="cite-chip" onClick={() => onOpenNode(o.id)}>
                      {o.label.length > 36 ? o.label.slice(0, 35) + "…" : o.label}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        ))}

        {asked && !loading && !err && recs.length === 0 && (
          <div className="muted" style={{ marginTop: 16 }}>Keine Vorschläge gefunden.</div>
        )}
      </div>

      <div className="chat-inputbar">
        <div className="chat-input">
          <input
            type="text"
            placeholder="Interesse oder Verein eingeben…"
            value={interest}
            onChange={(e) => setInterest(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && go(interest)}
          />
          <button onClick={() => go(interest)} disabled={loading || !interest.trim()}>
            {loading ? "…" : "Zeigen"}
          </button>
        </div>
      </div>
    </div>
  );
}
