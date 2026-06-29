import { useEffect, useMemo, useState } from "react";
import { api, type GraphEdge, type GraphNode, type Insight } from "../api";
import SubgraphView from "./SubgraphView";

interface Props {
  nodeId: string;
  onSelect: (id: string) => void;
}

export default function NodeDetail({ nodeId, onSelect }: Props) {
  const [node, setNode] = useState<GraphNode | null>(null);
  const [edges, setEdges] = useState<GraphEdge[]>([]);
  const [history, setHistory] = useState<GraphNode[]>([]);
  const [insight, setInsight] = useState<Insight | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setNode(null);
    setEdges([]);
    setHistory([]);
    setInsight(null);
    setErr(null);
    Promise.all([api.node(nodeId), api.nodeEdges(nodeId), api.nodeHistory(nodeId)])
      .then(([n, e, h]) => {
        if (!live) return;
        setNode(n);
        setEdges(e);
        setHistory(h);
      })
      .catch((ex) => live && setErr(String(ex)));
    return () => {
      live = false;
    };
  }, [nodeId]);

  async function explain(kind: "inefficiency" | "synergy") {
    setBusy(true);
    setErr(null);
    try {
      const neighbours = edges
        .flatMap((e) => [e.from_node_id, e.to_node_id])
        .filter((id) => id !== nodeId);
      const ids = Array.from(new Set([nodeId, ...neighbours])).slice(0, 30);
      setInsight(await api.insights(ids, kind));
    } catch (ex) {
      setErr(String(ex));
    } finally {
      setBusy(false);
    }
  }

  const egoIds = useMemo(() => {
    const neighbours = edges.flatMap((e) => [e.from_node_id, e.to_node_id]);
    return Array.from(new Set([nodeId, ...neighbours]));
  }, [edges, nodeId]);

  if (err) return <div className="err">{err}</div>;
  if (!node) return <div className="muted">Loading…</div>;

  return (
    <div>
      <div className="detail-label">{node.label}</div>
      <div className="row">
        <span className="tag">{node.node_type}</span>
        <span className="tag">{node.source}</span>
      </div>

      {node.valid_from && (
        <div className="kv">
          <span className="k">when</span>
          <span className="v">{formatWhen(node.valid_from)}</span>
        </div>
      )}

      <div className="section">
        <h3>Properties</h3>
        {Object.entries(node.properties).map(([k, v]) => (
          <div className="kv" key={k}>
            <span className="k">{k}</span>
            <span className="v">{formatVal(v)}</span>
          </div>
        ))}
        {node.source_url && (
          <div className="kv">
            <span className="k">source_url</span>
            <span className="v">
              <a href={node.source_url} target="_blank" rel="noreferrer">
                open ↗
              </a>
            </span>
          </div>
        )}
      </div>

      {egoIds.length > 1 && (
        <div className="section">
          <h3>Ego graph</h3>
          <SubgraphView nodeIds={egoIds} focusId={nodeId} onSelect={onSelect} />
        </div>
      )}

      <div className="section">
        <h3>Edges ({edges.length})</h3>
        {edges.length === 0 && <div className="muted">No connected edges.</div>}
        {edges.map((e) => {
          const otherId = e.from_node_id === nodeId ? e.to_node_id : e.from_node_id;
          const dir = e.from_node_id === nodeId ? "→" : "←";
          return (
            <div className="edge-row" key={e.id}>
              <span className="et">
                {dir} {e.edge_type}
                {e.inferred ? " *" : ""}
              </span>
              <button onClick={() => onSelect(otherId)}>open</button>
            </div>
          );
        })}
      </div>

      {history.length > 1 && (
        <div className="section">
          <h3>History ({history.length} versions)</h3>
          {history.map((v, i) => (
            <div className="kv" key={v.id}>
              <span className="k">v{i + 1}</span>
              <span className="v">
                {(v.valid_from ?? "—").slice(0, 10)} →{" "}
                {(v.valid_to ?? "current").slice(0, 10)}
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="section">
        <h3>Reason over subgraph</h3>
        <div className="row">
          <button onClick={() => explain("inefficiency")} disabled={busy}>
            Find inefficiencies
          </button>
          <button onClick={() => explain("synergy")} disabled={busy}>
            Find synergies
          </button>
        </div>
        {busy && <div className="muted">Querying reasoning layer…</div>}
        {insight?.insights?.map((it, i) => (
          <div className="insight" key={i}>
            <div className="it">{it.title}</div>
            <div>{it.description}</div>
            <div className="muted">confidence {Math.round((it.confidence ?? 0) * 100)}%</div>
          </div>
        ))}
        {insight && !insight.insights && (
          <pre className="insight">{JSON.stringify(insight, null, 2)}</pre>
        )}
      </div>
    </div>
  );
}

function formatWhen(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso.slice(0, 10);
  // If the timestamp has no time-of-day, show date only; else date + time.
  const hasTime = d.getHours() !== 0 || d.getMinutes() !== 0;
  return d.toLocaleString("de-DE", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    ...(hasTime ? { hour: "2-digit", minute: "2-digit" } : {}),
  });
}

function formatVal(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}
