import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type ChatAnswer, type EventItem } from "../api";
import RatingBar from "./RatingBar";
import EventPicker from "./EventPicker";

interface Props {
  onOpenNode: (id: string) => void;
  lens?: string;
  title?: string;
  subtitle?: string;
  examples?: string[];
  showEventPicker?: boolean;
}

interface Turn {
  q: string;
  a?: ChatAnswer;
  err?: string;
  pending?: boolean;
  rating?: number;
}

const DEFAULT_EXAMPLES = [
  "Welche Konzerte gibt es nächstes Wochenende?",
  "Was wurde 2023 zu Radwegen in Hörde beschlossen?",
  "Welche ungenutzten Synergien gibt es in der Nordstadt?",
  "Gibt es Auffälligkeiten bei öffentlichen Vergaben?",
  "Welche Muster gibt es bei Polizeimeldungen?",
];

export default function ChatView({
  onOpenNode,
  lens,
  title = "Frag die Stadt Dortmund",
  subtitle = "Stell eine Frage – die Antwort kommt mit Quellen direkt aus dem Wissensgraphen (Ratsbeschlüsse, Veranstaltungen, Nachrichten, Vergaben und mehr).",
  examples = DEFAULT_EXAMPLES,
  showEventPicker = true,
}: Props) {
  const [q, setQ] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [picker, setPicker] = useState(false);
  const busy = turns.some((t) => t.pending);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns]);

  async function ask(question: string) {
    question = question.trim();
    if (!question || busy) return;
    setQ("");
    const idx = turns.length;
    setTurns((t) => [...t, { q: question, pending: true }]);
    try {
      const a = await api.chat(question, lens);
      setTurns((t) => t.map((x, i) => (i === idx ? { q: x.q, a } : x)));
    } catch (e) {
      setTurns((t) => t.map((x, i) => (i === idx ? { q: x.q, err: String(e) } : x)));
    }
  }

  async function analyzeEvent(ev: EventItem, lens: string) {
    setPicker(false);
    if (busy) return;
    const label = lens === "synergy" ? "Synergien" : "Ineffizienzen";
    const idx = turns.length;
    setTurns((t) => [...t, { q: `${label} rund um „${ev.label}"`, pending: true }]);
    try {
      const a = await api.analyzeNode(ev.id, lens);
      setTurns((t) => t.map((x, i) => (i === idx ? { q: a.question ?? x.q, a } : x)));
    } catch (e) {
      setTurns((t) => t.map((x, i) => (i === idx ? { q: x.q, err: String(e) } : x)));
    }
  }

  async function rate(idx: number, id: string, n: number) {
    setTurns((t) => t.map((x, i) => (i === idx ? { ...x, rating: n } : x)));
    try {
      await api.rateChat(id, n);
    } catch {
      /* keep optimistic value */
    }
  }

  return (
    <div className="chat">
      <div className="chat-thread">
        {turns.length === 0 && (
          <div className="chat-hero">
            <div className="chat-hero-title">{title}</div>
            <div className="chat-hero-sub">{subtitle}</div>
            <div className="chat-examples">
              {examples.map((ex) => (
                <button key={ex} className="chat-chip" onClick={() => ask(ex)}>
                  {ex}
                </button>
              ))}
            </div>
          </div>
        )}

        {turns.map((t, i) => (
          <div className="chat-turn" key={i}>
            <div className="bubble user">{t.q}</div>
            {t.pending && (
              <div className="bubble assistant">
                <span className="typing">
                  <span /> <span /> <span />
                </span>
              </div>
            )}
            {t.err && <div className="bubble assistant err">{t.err}</div>}
            {t.a && (
              <div className="bubble assistant">
                <div className="md">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{t.a.answer}</ReactMarkdown>
                </div>
                {t.a.citations.length > 0 && (
                  <div className="cites">
                    <div className="cites-label">Quellen</div>
                    <div className="cites-row">
                      {t.a.citations.slice(0, 10).map((c) => (
                        <button
                          key={c.id}
                          className="cite-chip"
                          title={`${c.node_type} · ${c.source}`}
                          onClick={() => onOpenNode(c.id)}
                        >
                          {c.label.length > 38 ? c.label.slice(0, 37) + "…" : c.label}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                {t.a.id && (
                  <RatingBar value={t.rating ?? null} onRate={(n) => rate(i, t.a!.id!, n)} />
                )}
              </div>
            )}
          </div>
        ))}
        <div ref={endRef} />
      </div>

      <div className="chat-inputbar">
        <div className="chat-input">
          {showEventPicker && (
            <button
              className="add-event"
              title="Event zur Analyse hinzufügen"
              onClick={() => setPicker(true)}
            >
              + Event
            </button>
          )}
          <input
            type="text"
            placeholder="Frage zu Dortmund stellen…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && ask(q)}
            autoFocus
          />
          <button className="primary" onClick={() => ask(q)} disabled={busy || !q.trim()}>
            {busy ? "…" : "Fragen"}
          </button>
        </div>
      </div>

      {picker && <EventPicker onPick={analyzeEvent} onClose={() => setPicker(false)} />}
    </div>
  );
}
