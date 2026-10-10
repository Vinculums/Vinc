import type { GraphNode, GraphEdge } from "./api";
import type { ElkNode, ElkExtendedEdge } from "elkjs/lib/elk-api";
import { simplifyRoute, type RoutePoint } from "./view-route";

export type LayoutGraph = { nodes: GraphNode[]; edges: GraphEdge[]; topicEdges: GraphEdge[] };
export type LayoutEngine = { layout(graph: ElkNode): Promise<ElkNode> };
export type CrossRouter = (nodes: GraphNode[], edges: GraphEdge[]) => Promise<GraphEdge[]>;
const UNIT = 1000;
const directed = new Set(["PREREQUISITE_OF", "DERIVES_FROM", "INHERITS_FROM"]);
const timeOf = (n: GraphNode, basis: "recorded" | "content") => {
  const value = (basis === "content" ? n.content_ts : n.ts) || "";
  // Partial dates retain their authored precision. Explicit ISO instants are
  // compared in UTC so timezone offsets cannot reverse chronological order.
  if (/^\d{4}$/.test(value)) return value;
  if (/^\d{4}-\d{2}$/.test(value) && +value.slice(5) >= 1 && +value.slice(5) <= 12) return value;
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    const instant = Date.parse(value);
    return Number.isFinite(instant) && new Date(instant).toISOString().slice(0, 10) === value ? value : "";
  }
  if (/^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value)) {
    const instant = Date.parse(value); return Number.isFinite(instant) ? new Date(instant).toISOString() : "";
  }
  return "";
};

function components(ids: string[], edges: GraphEdge[]): string[][] {
  const adj = new Map(ids.map(id => [id, new Set<string>()]));
  for (const e of edges) { adj.get(e.source)?.add(e.target); adj.get(e.target)?.add(e.source); }
  const seen = new Set<string>(), groups: string[][] = [];
  for (const id of ids) {
    if (seen.has(id)) continue;
    const pending = [id], group: string[] = [];
    while (pending.length) {
      const at = pending.pop()!;
      if (seen.has(at)) continue;
      seen.add(at); group.push(at);
      for (const next of adj.get(at) ?? []) if (!seen.has(next)) pending.push(next);
    }
    groups.push(group.sort());
  }
  return groups.sort((a, b) => b.length - a.length || a[0].localeCompare(b[0]));
}

/** Port dimensions and ELK options come from the approved temporal prototype.
 * Only the presentation's ancestry input reverses derives/inherits; output
 * paths are restored to the original relation's source-to-target direction. */
const ancestry = (e: GraphEdge) => ["DERIVES_FROM", "INHERITS_FROM"].includes(e.type)
  ? [e.target, e.source] : [e.source, e.target];
function child(id: string, partition: number): ElkNode {
  return { id, width: 220, height: 90,
    layoutOptions: { "elk.portConstraints": "FIXED_POS", "elk.partitioning.partition": String(partition) },
    ports: [
      { id: `${id}:out`, x: 220, y: 24, layoutOptions: { "elk.port.side": "EAST" } },
      { id: `${id}:in`, x: 0, y: 24, layoutOptions: { "elk.port.side": "WEST" } },
    ] };
}

export async function temporalLayout(inputNodes: GraphNode[], inputEdges: GraphEdge[],
  basis: "recorded" | "content", elk: LayoutEngine, crossRouter: CrossRouter): Promise<LayoutGraph> {
  if (!inputNodes.length) return { nodes: [], edges: [], topicEdges: [] };
  // Clone rather than changing the API cache, details, or source relation metadata.
  const nodes = inputNodes.map(n => ({ ...n }));
  const map = new Map(nodes.map(n => [n.id, n]));
  const topicOf = (n: GraphNode) => n.kind === "domain" ? n.id.slice(7) : n.domain || "";
  const topics = new Map<string, GraphNode>();
  for (const n of nodes) if (n.kind === "domain") topics.set(topicOf(n), n);
  for (const n of inputNodes) {
    const domain = topicOf(n);
    if (!topics.has(domain)) {
      // Real domain coordinates remain navigable; these marks contain no time.
      const id = domain ? `domain:${domain}` : "domain:";
      const topic = { id, label: domain.split("/").at(-1) || "—", domain,
        kind: "domain", degree: inputNodes.filter(m => m.domain === domain).length };
      topics.set(domain, topic); nodes.push(topic); map.set(id, topic);
    }
  }
  const usable = inputEdges.filter(e => map.has(e.source) && map.has(e.target));
  const positions = new Map<string, RoutePoint>();
  const localRoutes = new Map<GraphEdge, GraphEdge>();
  const topicEdges: GraphEdge[] = [];
  const dates = [...new Set(inputNodes.map(n => timeOf(n, basis)).filter(Boolean))].sort();
  const partitions = new Map(dates.map((d, i) => [d, i + 1]));
  const orderedTopics = [...topics].sort(([a], [b]) => a.localeCompare(b));
  let bottom = 0;
  for (let ti = 0; ti < orderedTopics.length; ti++) {
    const [domain, topic] = orderedTopics[ti];
    const members = nodes.filter(n => n.kind !== "domain" && topicOf(n) === domain);
    const ids = new Set(members.map(n => n.id));
    const inside = usable.filter(e => ids.has(e.source) && ids.has(e.target));
    const groups = components([...ids], inside), wings: string[][] = [[], []];
    for (const group of groups) (wings[0].length <= wings[1].length ? wings[0] : wings[1]).push(...group);
    let y0 = -24, y1 = 66;
    const paths: { edge: GraphEdge; points: RoutePoint[]; original?: GraphEdge }[] = [];
    for (let wing = 0; wing < 2; wing++) {
      const wingIds = new Set(wings[wing]);
      if (!wingIds.size) continue;
      const anchor = `@topic-anchor:${ti}:${wing}`;
      const edges = inside.filter(e => wingIds.has(e.source));
      const incoming = new Set(edges.filter(e => directed.has(e.type)).map(e => ancestry(e)[1]));
      const guides: GraphEdge[] = [];
      for (const group of groups.filter(g => wingIds.has(g[0]))) {
        let roots = group.filter(id => !incoming.has(id));
        if (!roots.length) roots = [group.slice().sort((a, b) =>
          timeOf(map.get(a)!, basis).localeCompare(timeOf(map.get(b)!, basis)) || a.localeCompare(b))[0]];
        for (const id of roots) guides.push({ source: topic.id, target: id,
          type: "IN_DOMAIN", cross_domain: false, presentation: true });
      }
      const entries = [...edges.map(e => ({ edge: e, ends: ancestry(e), original: e })),
        ...guides.map(e => ({ edge: e, ends: [anchor, e.target], original: undefined }))];
      const root = child(anchor, 0);
      root.layoutOptions!["elk.layered.layering.layerConstraint"] = "FIRST";
      const result = await elk.layout({ id: `topic:${ti}:${wing}`, layoutOptions: {
        "elk.algorithm": "layered", "elk.direction": "RIGHT", "elk.edgeRouting": "ORTHOGONAL",
        "elk.randomSeed": "10", "elk.separateConnectedComponents": "false",
        "elk.spacing.nodeNode": "80", "elk.layered.spacing.nodeNodeBetweenLayers": "110",
        "elk.spacing.edgeNode": "30", "elk.layered.spacing.edgeNodeBetweenLayers": "30",
        "elk.spacing.edgeEdge": "18", "elk.layered.spacing.edgeEdgeBetweenLayers": "18",
        "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
        "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP", "elk.partitioning.activate": "true",
      }, children: [root, ...members.filter(n => wingIds.has(n.id)).map(n =>
        child(n.id, partitions.get(timeOf(n, basis)) ?? 1))],
      edges: entries.map((e, i) => ({ id: String(i), sources: [`${e.ends[0]}:out`], targets: [`${e.ends[1]}:in`] })) });
      const origin = result.children!.find(n => n.id === anchor)!;
      const sign = wing === 0 ? 1 : -1;
      const convert = (p: { x: number; y: number }): RoutePoint =>
        ({ x: sign * (p.x - origin.x! - 110), y: p.y - origin.y! - 24, z: 0 });
      positions.set(topic.id, { x: 0, y: 0, z: 0 });
      for (const n of result.children!) {
        y0 = Math.min(y0, n.y! - origin.y! - 24);
        y1 = Math.max(y1, n.y! + n.height! - origin.y! - 24);
        if (n.id !== anchor) positions.set(n.id, convert({ x: n.x! + 110, y: n.y! + 24 }));
      }
      for (const output of result.edges as ElkExtendedEdge[]) {
        const entry = entries[Number(output.id)], section = output.sections?.[0];
        if (!section || output.sections?.length !== 1) throw new Error(`Missing ELK route: ${output.id}`);
        const a = entry.ends[0] === anchor ? positions.get(topic.id)! : positions.get(entry.ends[0])!;
        const b = positions.get(entry.ends[1])!;
        let points = simplifyRoute([a, ...[section.startPoint, ...(section.bendPoints ?? []), section.endPoint].map(convert), b]);
        if (entry.original && entry.ends[0] !== entry.original.source) points = points.reverse();
        paths.push({ edge: entry.edge, original: entry.original, points });
      }
    }
    const offset = ti ? bottom - y0 + 240 : 0, z = ti * 260;
    bottom = offset + y1;
    positions.set(topic.id, { x: 0, y: offset, z });
    for (const n of members) {
      const p = positions.get(n.id)!; positions.set(n.id, { x: p.x, y: p.y + offset, z });
    }
    for (const { edge, points, original } of paths) {
      const route = { engine: "elk" as const, points: points.map(p => ({ x: p.x / UNIT, y: (p.y + offset) / UNIT, z: z / UNIT })) };
      if (original) localRoutes.set(original, { ...edge, route });
      else topicEdges.push({ ...edge, route });
    }
  }
  for (const n of nodes) {
    const p = positions.get(n.id)!;
    Object.assign(n, { x: p.x / UNIT, y: p.y / UNIT, z: p.z / UNIT, x2: p.x / UNIT, y2: -p.y / UNIT });
  }
  // Topic hierarchy is membership, never ancestry or a causal arrow.
  for (const [domain, topic] of topics) {
    const parent = topics.get(domain.split("/").slice(0, -1).join("/"));
    if (parent && parent !== topic && domain.includes("/")) topicEdges.push({ source: parent.id,
      target: topic.id, type: "PARENT_OF", cross_domain: true, presentation: true });
  }
  const cross = usable.filter(e => !localRoutes.has(e));
  const routed = await crossRouter(nodes, [...cross, ...topicEdges.filter(e => !e.route)]);
  const crossMap = new Map(cross.map((e, i) => [e, routed[i]]));
  const originalEdges = inputEdges.map(e => localRoutes.get(e) ?? crossMap.get(e) ?? { ...e });
  return { nodes, edges: originalEdges, topicEdges: [
    ...topicEdges.filter(e => e.route), ...routed.slice(cross.length),
  ] };
}
