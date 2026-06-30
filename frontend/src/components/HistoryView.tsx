import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type ChatHistoryItem } from "../api";
import RatingBar from "./RatingBar";

interface Props {
  onOpenNode: (id: string) => void;
}

const LENSES = ["", "factual", "synergy", "inefficiency", "scandal", "crime"];
const LENS_LABEL: Record<string, string> = {
  factual: "Faktisch",
  synergy: "Synergie",
  inefficiency: "Ineffizienz",
  scandal: "Auffälligkeit",
  crime: "Kriminalität",
};

export default function HistoryView({ onOpenNode }: Props) {
  const [items, setItems] = useState<ChatHistoryItem[]>([]);
  const [minRating, setMinRating] = useState(0);
  const [lens, setLens] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setErr(null);
    api
      .chatHistory({ minRating: minRating || undefined, lens: lens || undefined })
      .then(setItems)
      .catch((e) => setErr(String(e)));
  }, [minRating, lens]);

  async function rate(id: string, n: number) {
    setItems((xs) => xs.map((x) => (x.id === id ? { ...x, rating: n } : x)));
    try {
      await api.rateChat(id, n);
    } catch {
      /* keep optimistic */
    }
  }

  return (
    <div className="chat">
      <div className="chat-thread">
        <div className="history-head">
          <div className="chat-hero-title" style={{ fontSize: 26 }}>
            Verlauf
          </div>
          <div className="history-filters">
            <label>
              Mind. Bewertung
              <select value={minRating} onChange={(e) => setMinRating(Number(e.target.value))}>
                <option value={0}>alle</option>
                {[5, 6, 7, 8, 9, 10].map((n) => (
                  <option key={n} value={n}>
                    ≥ {n}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Linse
              <select value={lens} onChange={(e) => setLens(e.target.value)}>
                {LENSES.map((l) => (
                  <option key={l} value={l}>
                    {l ? LENS_LABEL[l] ?? l : "alle"}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </div>

        {err && <div className="err">{err}</div>}
        {!err && items.length === 0 && (
          <div className="muted" style={{ marginTop: 16 }}>Keine gespeicherten Abfragen.</div>
        )}

        {items.map((it) => (
          <div className="hist-item" key={it.id}>
            <div className="hist-row" onClick={() => setExpanded(expanded === it.id ? null : it.id)}>
              <span className="hist-q">{it.question}</span>
              <span className="hist-meta">
                {it.lens && <span className="tag">{LENS_LABEL[it.lens] ?? it.lens}</span>}
                {it.retrieval === "structural" && (
                  <span className="tag tag-structural">Strukturell</span>
                )}
                {it.retrieval === "semantic" && <span className="tag">Semantisch</span>}
                {it.rating != null && <span className="hist-rating">{it.rating}/10</span>}
                <span className="muted">{new Date(it.created_at).toLocaleDateString("de-DE")}</span>
              </span>
            </div>
            {expanded === it.id && (
              <div className="hist-detail">
                <div className="md">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{it.answer}</ReactMarkdown>
                </div>
                {it.citations?.length > 0 && (
                  <div className="cites-row" style={{ marginTop: 10 }}>
                    {it.citations.slice(0, 10).map((c) => (
                      <button key={c.id} className="cite-chip" onClick={() => onOpenNode(c.id)}>
                        {c.label.length > 38 ? c.label.slice(0, 37) + "…" : c.label}
                      </button>
                    ))}
                  </div>
                )}
                <RatingBar value={it.rating} onRate={(n) => rate(it.id, n)} />
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
