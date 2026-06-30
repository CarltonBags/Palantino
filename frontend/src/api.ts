// Typed client for the civic-graph FastAPI backend.
// In dev, Vite proxies /api → http://localhost:8000 (see vite.config.ts).

const BASE = "/api";

export interface GraphNode {
  id: string;
  node_type: string;
  label: string;
  properties: Record<string, unknown>;
  source: string;
  source_url: string | null;
  valid_from: string | null;
  valid_to: string | null;
  observed_at: string;
}

export interface GraphEdge {
  id: string;
  edge_type: string;
  from_node_id: string;
  to_node_id: string;
  properties: Record<string, unknown>;
  source: string;
  source_url: string | null;
  inferred: boolean;
}

export interface SearchHit {
  id: string;
  node_type: string;
  label: string;
  source: string;
  source_url: string | null;
  score: number;
}

export interface GeoFeature {
  type: "Feature";
  geometry: GeoJSON.Geometry;
  properties: Record<string, unknown>;
}

export interface FeatureCollection {
  type: "FeatureCollection";
  features: GeoFeature[];
}

export interface IngestionRun {
  connector: string;
  started_at: string;
  finished_at: string | null;
  status: string;
  nodes_written: number;
  edges_written: number;
  error_message: string | null;
}

export interface SourceCatalogEntry {
  name: string;
  shape: string;
  cadence_cron: string;
  description: string;
  enabled: boolean;
  last_run: {
    status: string;
    started_at: string;
    nodes_written: number;
    edges_written: number;
  } | null;
}

export interface ResolutionCandidate {
  id: string;
  method: string;
  confidence: number;
  resolved: boolean | null;
  created_at: string;
  a_id: string;
  a_label: string;
  a_type: string;
  a_source: string;
  b_id: string;
  b_label: string;
  b_type: string;
  b_source: string;
}

export interface Insight {
  insights?: Array<{
    title: string;
    description: string;
    confidence: number;
    evidence?: string[];
  }>;
  [key: string]: unknown;
}

export interface StoredInsight {
  id: string;
  insight_type: string;
  title: string;
  description: string;
  confidence: number;
  evidence_node_ids: string[];
  reasoning_trace: string | null;
  model: string;
  generator: string;
  status: string;
  created_at: string;
}

export interface ChatCitation {
  id: string;
  label: string;
  node_type: string;
  source: string;
  source_url: string | null;
}
export interface ChatAnswer {
  id?: string;
  answer: string;
  citations: ChatCitation[];
  intent?: { lens?: string };
  question?: string;
}
export interface EventCategory {
  category: string;
  n: number;
}
export interface EventItem {
  id: string;
  label: string;
  category: string | null;
  venue: string | null;
  stadtbezirk: string | null;
  valid_from: string | null;
}
export interface ChatHistoryItem {
  id: string;
  question: string;
  answer: string;
  lens: string | null;
  citations: ChatCitation[];
  rating: number | null;
  created_at: string;
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} on ${path}`);
  return res.json() as Promise<T>;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} on ${path}`);
  return res.json() as Promise<T>;
}

export const api = {
  geoAreas: (areaType?: string) =>
    get<Array<{ id: string; label: string; properties: Record<string, unknown>; geometry: GeoJSON.Geometry }>>(
      `/geo/areas${areaType ? `?area_type=${encodeURIComponent(areaType)}` : ""}`,
    ),
  geoNodes: (nodeType?: string, source?: string, asOf?: string) => {
    const q = new URLSearchParams();
    if (nodeType) q.set("node_type", nodeType);
    if (source) q.set("source", source);
    if (asOf) q.set("as_of", asOf);
    const qs = q.toString();
    return get<FeatureCollection>(`/geo/nodes${qs ? `?${qs}` : ""}`);
  },
  subgraph: (nodeIds: string[]) =>
    post<{ nodes: GraphNode[]; edges: GraphEdge[] }>(`/subgraph`, { node_ids: nodeIds }),
  chat: (question: string) => post<ChatAnswer>(`/chat`, { question }),
  eventCategories: () => get<EventCategory[]>(`/events/categories`),
  events: (opts: { category?: string; q?: string } = {}) => {
    const p = new URLSearchParams();
    if (opts.category) p.set("category", opts.category);
    if (opts.q) p.set("q", opts.q);
    const qs = p.toString();
    return get<EventItem[]>(`/events${qs ? `?${qs}` : ""}`);
  },
  analyzeNode: (nodeId: string, lens: string) =>
    post<ChatAnswer>(`/chat/node`, { node_id: nodeId, lens }),
  rateChat: (id: string, rating: number) =>
    post<{ id: string; rating: number }>(`/chat/${id}/rating`, { rating }),
  chatHistory: (opts: { minRating?: number; lens?: string } = {}) => {
    const q = new URLSearchParams();
    if (opts.minRating) q.set("min_rating", String(opts.minRating));
    if (opts.lens) q.set("lens", opts.lens);
    const qs = q.toString();
    return get<ChatHistoryItem[]>(`/chat/history${qs ? `?${qs}` : ""}`);
  },
  node: (id: string) => get<GraphNode>(`/nodes/${id}`),
  nodeHistory: (id: string) => get<GraphNode[]>(`/nodes/${id}/history`),
  nodeEdges: (id: string) => get<GraphEdge[]>(`/nodes/${id}/edges`),
  search: (q: string, nodeType?: string) =>
    get<SearchHit[]>(
      `/search?q=${encodeURIComponent(q)}${nodeType ? `&node_type=${encodeURIComponent(nodeType)}` : ""}`,
    ),
  insights: (nodeIds: string[], insightType: "inefficiency" | "synergy") =>
    post<Insight>(`/insights`, { node_ids: nodeIds, insight_type: insightType }),
  storedInsights: (status = "new", insightType?: string) => {
    const q = new URLSearchParams({ status });
    if (insightType) q.set("insight_type", insightType);
    return get<StoredInsight[]>(`/insights/stored?${q.toString()}`);
  },
  setInsightStatus: (id: string, status: "confirmed" | "dismissed" | "new") =>
    post<{ id: string; status: string }>(`/insights/stored/${id}/status`, { status }),
  ingestionStatus: () => get<IngestionRun[]>(`/status/ingestion`),
  sourceCatalog: () => get<SourceCatalogEntry[]>(`/status/sources`),
  resolutionCandidates: (status = "pending") =>
    get<ResolutionCandidate[]>(`/resolution/candidates?status=${status}`),
  resolveCandidate: (id: string, merge: boolean) =>
    post<{ id: string; merged: boolean }>(`/resolution/candidates/${id}/resolve`, {
      merge,
      resolved_by: "frontend",
    }),
};
