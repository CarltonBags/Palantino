import { useState } from "react";
import { api, type ChatAnswer } from "../api";

interface Props {
  onSelect: (id: string) => void;
}

interface Turn {
  q: string;
  a?: ChatAnswer;
  err?: string;
}

export default function ChatPanel({ onSelect }: Props) {
  const [q, setQ] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);

  async function ask() {
    const question = q.trim();
    if (!question || busy) return;
    setBusy(true);
    setQ("");
    const idx = turns.length;
    setTurns((t) => [...t, { q: question }]);
    try {
      const a = await api.chat(question);
      setTurns((t) => t.map((x, i) => (i === idx ? { ...x, a } : x)));
    } catch (e) {
      setTurns((t) => t.map((x, i) => (i === idx ? { ...x, err: String(e) } : x)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="section">
      <h3>Frag die Stadt</h3>
      <div className="row">
        <input
          type="text"
          placeholder="Frage zu Dortmund stellen…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && ask()}
        />
        <button className="primary" onClick={ask} disabled={busy}>
          Fragen
        </button>
      </div>
      {busy && <div className="muted">Suche im Graphen…</div>}

      <div style={{ marginTop: 8 }}>
        {turns
          .map((t, i) => ({ t, i }))
          .reverse()
          .map(({ t, i }) => (
            <div className="insight" key={i} style={{ marginTop: 8 }}>
              <div className="it">{t.q}</div>
              {t.err && <div className="err">{t.err}</div>}
              {t.a && (
                <>
                  <div style={{ whiteSpace: "pre-wrap", marginTop: 6 }}>{t.a.answer}</div>
                  {t.a.citations.length > 0 && (
                    <div className="section" style={{ marginTop: 6 }}>
                      <h3>Quellen ({t.a.citations.length})</h3>
                      {t.a.citations.slice(0, 8).map((c) => (
                        <div className="hit" key={c.id} onClick={() => onSelect(c.id)}>
                          <span className="ttl">{c.label}</span>
                          <span className="meta">
                            {c.node_type} · {c.source}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </>
              )}
            </div>
          ))}
      </div>
    </div>
  );
}
