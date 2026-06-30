import { useEffect, useState } from "react";
import { api, type ResolutionCandidate } from "../api";

interface Props {
  onSelect: (id: string) => void;
}

export default function ResolutionPanel({ onSelect }: Props) {
  const [cands, setCands] = useState<ResolutionCandidate[]>([]);
  const [err, setErr] = useState<string | null>(null);

  function load() {
    api.resolutionCandidates("pending").then(setCands).catch((e) => setErr(String(e)));
  }
  useEffect(load, []);

  async function decide(id: string, merge: boolean) {
    await api.resolveCandidate(id, merge);
    setCands((cs) => cs.filter((c) => c.id !== id));
  }

  if (err) return null; // backend not up yet — stay quiet
  if (cands.length === 0) return null;

  return (
    <div className="section">
      <h3>Abgleich-Prüfung ({cands.length})</h3>
      {cands.map((c) => (
        <div className="cand" key={c.id} style={{ flexDirection: "column", alignItems: "stretch" }}>
          <div className="muted">
            {c.method} · {Math.round(c.confidence * 100)}%
          </div>
          <div style={{ fontSize: 13 }}>
            <a onClick={() => onSelect(c.a_id)} style={{ cursor: "pointer" }}>
              {c.a_label}
            </a>{" "}
            <span className="muted">({c.a_source})</span>
          </div>
          <div style={{ fontSize: 13 }}>
            ↔{" "}
            <a onClick={() => onSelect(c.b_id)} style={{ cursor: "pointer" }}>
              {c.b_label}
            </a>{" "}
            <span className="muted">({c.b_source})</span>
          </div>
          <div className="row" style={{ marginTop: 6 }}>
            <button className="primary" onClick={() => decide(c.id, true)}>
              Zusammenführen
            </button>
            <button onClick={() => decide(c.id, false)}>Ablehnen</button>
          </div>
        </div>
      ))}
    </div>
  );
}
