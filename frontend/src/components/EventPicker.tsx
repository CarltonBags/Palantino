import { useEffect, useState } from "react";
import { api, type EventCategory, type EventItem } from "../api";

interface Props {
  onPick: (ev: EventItem, lens: string) => void;
  onClose: () => void;
}

export default function EventPicker({ onPick, onClose }: Props) {
  const [cats, setCats] = useState<EventCategory[]>([]);
  const [category, setCategory] = useState("");
  const [q, setQ] = useState("");
  const [items, setItems] = useState<EventItem[]>([]);
  const [sel, setSel] = useState<EventItem | null>(null);

  useEffect(() => {
    api.eventCategories().then(setCats).catch(() => setCats([]));
  }, []);

  useEffect(() => {
    const t = setTimeout(() => {
      api
        .events({ category: category || undefined, q: q || undefined })
        .then(setItems)
        .catch(() => setItems([]));
    }, 200);
    return () => clearTimeout(t);
  }, [category, q]);

  return (
    <div className="picker-overlay" onClick={onClose}>
      <div className="picker" onClick={(e) => e.stopPropagation()}>
        <div className="picker-head">
          <h3>Event auswählen</h3>
          <button onClick={onClose}>✕</button>
        </div>
        <div className="picker-filters">
          <select
            value={category}
            onChange={(e) => {
              setCategory(e.target.value);
              setSel(null);
            }}
          >
            <option value="">Alle Kategorien</option>
            {cats.map((c) => (
              <option key={c.category} value={c.category}>
                {c.category} ({c.n})
              </option>
            ))}
          </select>
          <input
            type="search"
            placeholder="Event suchen…"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setSel(null);
            }}
          />
        </div>
        <div className="picker-list">
          {items.map((ev) => (
            <div
              key={ev.id}
              className={`picker-item${sel?.id === ev.id ? " sel" : ""}`}
              onClick={() => setSel(ev)}
            >
              <span className="ttl">{ev.label}</span>
              <span className="meta">
                {ev.category}
                {ev.stadtbezirk ? ` · ${ev.stadtbezirk}` : ""}
                {ev.valid_from ? ` · ${new Date(ev.valid_from).toLocaleDateString("de-DE")}` : ""}
              </span>
            </div>
          ))}
          {items.length === 0 && <div className="muted">Keine Events gefunden.</div>}
        </div>
        {sel && (
          <div className="picker-actions">
            <span className="muted">Analyse für „{sel.label.slice(0, 36)}…":</span>
            <button className="primary" onClick={() => onPick(sel, "synergy")}>
              Synergien
            </button>
            <button onClick={() => onPick(sel, "inefficiency")}>Ineffizienzen</button>
          </div>
        )}
      </div>
    </div>
  );
}
