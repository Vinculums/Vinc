import type { GraphEdge, GraphNode } from "./api";
import { edgeKey } from "./spotlight";
import { topicOf } from "./node-topic";

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
 * route lands on the same relation however the read happens to order them. 바탕 키는
 * 스포트라이트의 `edgeKey` 와 같은 한 벌이다. */
export function edgeKeys(edges: { source: string; target: string; type?: string }[]): string[] {
  const seen = new Map<string, number>();
  return edges.map(e => {
    const base = edgeKey({ source: e.source, target: e.target, type: e.type || "" });
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

/** One layout grid step in scene units (`GRID / UNIT` in view-layout.ts). */
const STEP = 0.33;

/** Put an older answer onto a graph that has moved on since: nodes the answer knows keep their
 * place, and each new node stands one step right of the latest known member of its topic (the
 * topic sits at the left and dates run right, so that is where a new item would land). Relations
 * the answer did not route are drawn without a route. This is a stand-in until the hub's fresh
 * answer arrives, and it is cheap: no layout engine runs. */
export function placeOnAnswer(nodes: GraphNode[], edges: GraphEdge[], answer: LayoutAnswer):
  { nodes: GraphNode[]; edges: GraphEdge[]; topicEdges: GraphEdge[] } {
  type At = { x: number; y: number; z: number; x2: number; y2: number };
  const at = new Map<string, At>(answer.nodes.map(([id, x, y, z, x2, y2]) => [id, { x, y, z, x2, y2 }]));
  const ids = new Set(nodes.map(n => n.id));
  const topics = new Set(nodes.map(topicOf));
  // A topic mark the read now carries itself takes its own place; marks of topics no node uses go.
  const marks = answer.topics.filter(t => !ids.has(t.id) && topics.has(topicOf(t)));
  for (const t of answer.topics) if (ids.has(t.id) && t.x !== undefined)
    at.set(t.id, { x: t.x, y: t.y ?? 0, z: t.z ?? 0, x2: t.x2 ?? t.x, y2: t.y2 ?? 0 });
  const last = new Map<string, At>();
  const reach = (topic: string, p: At) => { const e = last.get(topic); if (!e || p.x > e.x) last.set(topic, p); };
  for (const t of marks) if (t.x !== undefined) reach(topicOf(t), { x: t.x, y: t.y ?? 0, z: t.z ?? 0, x2: t.x2 ?? t.x, y2: t.y2 ?? 0 });
  for (const n of nodes) { const p = at.get(n.id); if (p) reach(topicOf(n), p); }
  const placed = nodes.map(n => {
    let p = at.get(n.id);
    if (!p) {
      const base = last.get(topicOf(n)) ?? { x: 0, y: 0, z: 0, x2: 0, y2: 0 };
      p = { ...base, x: base.x + STEP, x2: base.x2 + STEP };
      last.set(topicOf(n), p);
    }
    return { ...n, ...p };
  });
  const routes = new Map(answer.edges);
  const keys = edgeKeys(edges);
  const known = new Set([...ids, ...marks.map(t => t.id)]);
  return {
    nodes: [...placed, ...marks.map(t => ({ ...t }))],
    edges: edges.map((e, i) => { const route = routes.get(keys[i]); return route ? { ...e, route } : { ...e }; }),
    topicEdges: answer.topicEdges.filter(e => known.has(e.source) && known.has(e.target)).map(e => ({ ...e })),
  };
}
