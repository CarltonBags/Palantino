import { useEffect, useState } from "react";
import { api, type IngestionRun } from "../api";

export default function IngestionStatus() {
  const [runs, setRuns] = useState<IngestionRun[]>([]);

  useEffect(() => {
    const load = () => api.ingestionStatus().then(setRuns).catch(() => setRuns([]));
    load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, []);

  if (runs.length === 0) return <div className="status-bar">No ingestion runs yet.</div>;

  return (
    <div className="status-bar">
      {runs.map((r) => (
        <span className="status-pill" key={r.connector}>
          <span className={r.status === "ok" ? "ok" : r.status === "error" ? "error" : ""}>
            ●
          </span>{" "}
          {r.connector} · {r.nodes_written}n/{r.edges_written}e
        </span>
      ))}
    </div>
  );
}
