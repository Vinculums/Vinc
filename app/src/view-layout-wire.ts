import type { GraphEdge, GraphNode } from "./api";

/** What the layout service needs from a graph, and nothing else: no labels, evidence or props.
 * Both the hub (which sends it) and the app (which merges the answer) build it the same way. */
export type SlimNode = { id: string; kind: string; domain?: string; ts?: string; content_ts?: string };
export type SlimEdge = { source: string; target: string; type: string };
type LayoutRoute = NonNullable<GraphEdge["route"]>;
/** A stored time-branches layout: coordinates per node id and a route per relation key. */
export type LayoutAnswer = {
  /** [id, x, y, z, x2, y2] for every node that was sent. */
  nodes: [string, number, number, number, number, number][];
  /** Topic marks the layout added because the read had none for that domain. */
  topics: GraphNode[];
  /** [relation key, route] for every relation that was sent. */
  edges: [string, LayoutRoute | null][];
  topicEdges: GraphEdge[];
};

/** Relations carry no id. Key them by their ends and type, numbering repeats in order, so a
 * route lands on the same relation however the read happens to order them. */
export function edgeKeys(edges: { source: string; target: string; type?: string }[]): string[] {
  const seen = new Map<string, number>();
  return edges.map(e => {
    const base = `${e.source}\u0000${e.target}\u0000${e.type || ""}`;
    const n = seen.get(base) || 0;
    seen.set(base, n + 1);
    return `${base}\u0000${n}`;
  });
}

export const slimNodes = (nodes: GraphNode[]): SlimNode[] => nodes.map(n => ({ id: n.id, kind: n.kind || "",
  ...(n.domain ? { domain: n.domain } : {}), ...(n.ts ? { ts: n.ts } : {}), ...(n.content_ts ? { content_ts: n.content_ts } : {}) }));
export const slimEdges = (edges: GraphEdge[]): SlimEdge[] => edges.map(e => ({ source: e.source, target: e.target, type: e.type || "" }));

/** Put a stored answer onto the graph the read returned. Returns null when they do not match
 * (a node or relation the answer does not know), so the caller lays it out another way. */
export function applyLayoutAnswer(nodes: GraphNode[], edges: GraphEdge[], answer: LayoutAnswer):
  { nodes: GraphNode[]; edges: GraphEdge[]; topicEdges: GraphEdge[] } | null {
  const at = new Map(answer.nodes.map(([id, x, y, z, x2, y2]) => [id, { x, y, z, x2, y2 }]));
  const routes = new Map(answer.edges);
  const keys = edgeKeys(edges);
  if (nodes.some(n => !at.has(n.id)) || keys.some(k => !routes.has(k))) return null;
  return {
    nodes: [...nodes.map(n => ({ ...n, ...at.get(n.id)! })), ...answer.topics.map(t => ({ ...t }))],
    edges: edges.map((e, i) => { const route = routes.get(keys[i]); return route ? { ...e, route } : { ...e }; }),
    topicEdges: answer.topicEdges.map(e => ({ ...e })),
  };
}
