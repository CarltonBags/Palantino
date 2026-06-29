import { useEffect, useMemo, useState } from "react";
import { api, type GraphEdge, type GraphNode } from "../api";
import { COLOR_BY_TYPE } from "../nodeTypes";

interface Props {
  nodeIds: string[];
  focusId?: string;
  onSelect: (id: string) => void;
}

const W = 340;
const H = 300;
const R = 110;

/**
 * Lightweight SVG graph view: nodes on a circle, edges as chords. No layout
 * engine — deterministic circular placement keeps it dependency-free and stable.
 */
export default function SubgraphView({ nodeIds, focusId, onSelect }: Props) {
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [edges, setEdges] = useState<GraphEdge[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setErr(null);
    api
      .subgraph(nodeIds)
      .then((g) => {
        if (!live) return;
        setNodes(g.nodes);
        setEdges(g.edges);
      })
      .catch((e) => live && setErr(String(e)));
    return () => {
      live = false;
    };
  }, [nodeIds.join(",")]);

  const pos = useMemo(() => {
    const map = new Map<string, { x: number; y: number }>();
    const cx = W / 2;
    const cy = H / 2;
    // Focus node (if present) sits in the centre; the rest ring around it.
    const ring = nodes.filter((n) => n.id !== focusId);
    if (focusId && nodes.some((n) => n.id === focusId)) {
      map.set(focusId, { x: cx, y: cy });
    }
    ring.forEach((n, i) => {
      const a = (2 * Math.PI * i) / Math.max(ring.length, 1) - Math.PI / 2;
      map.set(n.id, { x: cx + R * Math.cos(a), y: cy + R * Math.sin(a) });
    });
    return map;
  }, [nodes, focusId]);

  if (err) return <div className="err">{err}</div>;
  if (nodes.length === 0) return <div className="muted">Kein Subgraph.</div>;

  return (
    <svg width={W} height={H} style={{ display: "block", margin: "0 auto" }}>
      {edges.map((e) => {
        const a = pos.get(e.from_node_id);
        const b = pos.get(e.to_node_id);
        if (!a || !b) return null;
        return (
          <line
            key={e.id}
            x1={a.x}
            y1={a.y}
            x2={b.x}
            y2={b.y}
            stroke={e.inferred ? "#6b7280" : "#3a3f4a"}
            strokeDasharray={e.inferred ? "4 3" : undefined}
            strokeWidth={1}
          />
        );
      })}
      {nodes.map((n) => {
        const p = pos.get(n.id);
        if (!p) return null;
        const color = COLOR_BY_TYPE[n.node_type] ?? "#9aa3b2";
        const isFocus = n.id === focusId;
        return (
          <g
            key={n.id}
            transform={`translate(${p.x},${p.y})`}
            style={{ cursor: "pointer" }}
            onClick={() => onSelect(n.id)}
          >
            <circle r={isFocus ? 9 : 6} fill={color} stroke="#0f1115" strokeWidth={1.5} />
            <text x={10} y={4} fontSize={9} fill="#e6e8ec">
              {n.label.length > 22 ? n.label.slice(0, 21) + "…" : n.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
