import type { GraphNode, GraphEdge } from "./api";
import type { ElkNode, ElkExtendedEdge } from "elkjs/lib/elk-api";
import { simplifyRoute, type RoutePoint } from "./view-route";
import type { RoutingNode } from "./view-layout-avoid";

export type LayoutGraph = { nodes: GraphNode[]; edges: GraphEdge[]; topicEdges: GraphEdge[] };
export type LayoutEngine = { layout(graph: ElkNode): Promise<ElkNode> };
export type LayoutOrdering = "temporal" | "ranked";
export type CrossRouter = (nodes: GraphNode[], edges: GraphEdge[]) => Promise<GraphEdge[]>;
const UNIT = 1000;
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
  basis: "recorded" | "content", elk: LayoutEngine, crossRouter: CrossRouter,
  ordering: LayoutOrdering = "temporal", aspectRatio = 1.6): Promise<LayoutGraph> {
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
  const orderedTopics = [...topics].sort(([a], [b]) => a.localeCompare(b));
  const boxes: { id: string; width: number; height: number; left: number; top: number; ids: string[] }[] = [];
  aspectRatio = Number.isFinite(aspectRatio) ? Math.max(.5, Math.min(3, aspectRatio)) : 1.6;
  for (let ti = 0; ti < orderedTopics.length; ti++) {
    const [domain, topic] = orderedTopics[ti];
    const members = nodes.filter(n => n.kind !== "domain" && topicOf(n) === domain);
    const ids = new Set(members.map(n => n.id));
    const inside = usable.filter(e => ids.has(e.source) && ids.has(e.target));
    // 큰 연결 묶음도 날짜·관계를 버리지 않고 여러 가지로 나눈다. 가지 사이의
    // 원래 관계는 아래 공통 라우터가 잇는다. 날짜 순서는 각 가지의 바깥 방향이다.
    const limit = Math.max(6, Math.min(32, Math.ceil(Math.sqrt(members.length * aspectRatio / 7.8))));
    let groups = components([...ids], inside).flatMap(group => {
      const dates = new Set(group.map(id => timeOf(map.get(id)!, basis)).filter(Boolean));
      if (group.length <= 32 && dates.size <= 16) return [group];
      const adj = new Map(group.map(id => [id, [] as string[]]));
      for (const e of inside) if (adj.has(e.source) && adj.has(e.target)) {
        adj.get(e.source)!.push(e.target); adj.get(e.target)!.push(e.source);
      }
      const compare = (a: string, b: string) =>
        timeOf(map.get(a)!, basis).localeCompare(timeOf(map.get(b)!, basis)) || a.localeCompare(b);
      const seen = new Set<string>(), sorted: string[] = [];
      const incoming = new Set(inside.filter(e => ["PREREQUISITE_OF", "DERIVES_FROM", "INHERITS_FROM"].includes(e.type))
        .map(e => ancestry(e)[1]));
      // 날짜만으로 잘라 관계를 대량 분단하지 않도록 실제 이웃을 따라 가지를 묶는다.
      for (const root of group.slice().sort((a, b) => ordering === "ranked"
        ? Number(incoming.has(a)) - Number(incoming.has(b)) || compare(a, b) : compare(a, b))) {
        if (seen.has(root)) continue;
        const queue = [root]; seen.add(root);
        for (let at = 0; at < queue.length; at++) {
          const id = queue[at]; sorted.push(id);
          for (const next of adj.get(id)!.slice().sort(compare)) if (!seen.has(next)) { seen.add(next); queue.push(next); }
        }
      }
      const parts: string[][] = [];
      for (let i = 0; i < sorted.length; i += limit) parts.push(sorted.slice(i, i + limit));
      return parts;
    });
    // 고립된 작은 묶음마다 worker 왕복을 만들지 않는다. ELK 안에서는 서로
    // 다른 연결 묶음과 각 소속선을 유지한 채 한 번에 계산한다.
    if (groups.length > 8) {
      const batches: string[][] = []; let pending: string[] = [];
      for (const group of groups) {
        if (group.length > limit) { batches.push(group); continue; }
        if (pending.length + group.length > limit) { batches.push(pending); pending = []; }
        pending.push(...group);
      }
      if (pending.length) batches.push(pending);
      groups = batches;
    }
    const wings: string[][][] = [[], []], loads = [0, 0];
    for (const group of groups) {
      const wing = loads[0] <= loads[1] ? 0 : 1;
      wings[wing].push(group); loads[wing] += group.length;
    }
    const paths: { edge: GraphEdge; points: RoutePoint[]; original?: GraphEdge }[] = [];
    positions.set(topic.id, { x: 0, y: 0, z: 0 });
    for (let wing = 0; wing < 2; wing++) {
      const branches: { ids: string[]; height: number; points: typeof paths; top: number }[] = [];
      for (let bi = 0; bi < wings[wing].length; bi++) {
        const group = wings[wing][bi], wingIds = new Set(group);
        if (!wingIds.size) continue;
        const dates = [...new Set(group.map(id => timeOf(map.get(id)!, basis)).filter(Boolean))].sort();
        const partitions = new Map(dates.map((d, i) => [d, i + 1]));
        const anchor = `@topic-anchor:${ti}:${wing}:${bi}`;
        const edges = inside.filter(e => wingIds.has(e.source) && wingIds.has(e.target));
        const guides: GraphEdge[] = [];
        for (const branch of components(group, edges)) {
          // One membership guide anchors a connected component. Linking every
          // causal root duplicates membership and sends long guides across the
          // very branches the original relations already connect.
          const roots = ordering === "ranked" ? branch.filter(id => !edges.some(e =>
            ["PREREQUISITE_OF", "DERIVES_FROM", "INHERITS_FROM"].includes(e.type) && ancestry(e)[1] === id)) : branch;
          const candidates = roots.length ? roots : branch;
          const dated = candidates.filter(id => timeOf(map.get(id)!, basis));
          const id = (dated.length ? dated : candidates).slice().sort((a, b) =>
            timeOf(map.get(a)!, basis).localeCompare(timeOf(map.get(b)!, basis)) || a.localeCompare(b))[0];
          guides.push({ source: topic.id, target: id,
            type: "IN_DOMAIN", cross_domain: false, presentation: true });
        }
        const entries = [...edges.map(e => ({ edge: e, ends: ancestry(e), original: e })),
          ...guides.map(e => ({ edge: e, ends: [anchor, e.target], original: undefined }))];
        const root = child(anchor, 0);
        root.layoutOptions!["elk.layered.layering.layerConstraint"] = "FIRST";
        const result = await elk.layout({ id: `topic:${ti}:${wing}:${bi}`, layoutOptions: {
          "elk.algorithm": "layered", "elk.direction": "RIGHT", "elk.edgeRouting": "ORTHOGONAL",
          "elk.randomSeed": "10", "elk.separateConnectedComponents": "false",
          "elk.spacing.nodeNode": "80", "elk.layered.spacing.nodeNodeBetweenLayers": "110",
          "elk.spacing.edgeNode": "30", "elk.layered.spacing.edgeNodeBetweenLayers": "30",
          "elk.spacing.edgeEdge": "18", "elk.layered.spacing.edgeEdgeBetweenLayers": "18",
          "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
          // Chronological partitions already constrain the layers. The simplex
          // optimiser is costly for large cyclic branches; longest-path layering
          // honours those same partitions without its repeated optimisation.
          "elk.layered.layering.strategy": wingIds.size > 128 ? "LONGEST_PATH" : "NETWORK_SIMPLEX",
          "elk.layered.thoroughness": inputNodes.length > 500 ? "1" : "7",
          "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
          "elk.partitioning.activate": String(ordering === "temporal"),
        }, children: [root, ...members.filter(n => wingIds.has(n.id)).map(n =>
          child(n.id, partitions.get(timeOf(n, basis)) ?? 1))],
        edges: entries.map((e, i) => {
          const partition = (id: string) => id === anchor ? 0 : partitions.get(timeOf(map.get(id)!, basis)) ?? 1;
          // Relation direction and chronological placement are independent. A
          // backwards edge must leave toward its target instead of taking a lap
          // around both nodes through forward-facing ports.
          const backwards = ordering === "temporal" && partition(e.ends[0]) > partition(e.ends[1]);
          return { id: String(i), sources: [`${e.ends[0]}:${backwards ? "in" : "out"}`],
            targets: [`${e.ends[1]}:${backwards ? "out" : "in"}`] };
        }) });
        const origin = result.children!.find(n => n.id === anchor)!;
        const sign = wing === 0 ? 1 : -1;
        const convert = (p: { x: number; y: number }): RoutePoint =>
          ({ x: sign * (p.x - origin.x! - 110), y: p.y - origin.y! - 24, z: 0 });
        let y0 = -24, y1 = 66;
        for (const n of result.children!) {
          y0 = Math.min(y0, n.y! - origin.y! - 24);
          y1 = Math.max(y1, n.y! + n.height! - origin.y! - 24);
          if (n.id !== anchor) positions.set(n.id, convert({ x: n.x! + 110, y: n.y! + 24 }));
        }
        const branchPaths: typeof paths = [];
        for (const output of result.edges as ElkExtendedEdge[]) {
          const entry = entries[Number(output.id)], section = output.sections?.[0];
          if (!section || output.sections?.length !== 1) throw new Error(`Missing ELK route: ${output.id}`);
          const a = entry.ends[0] === anchor ? positions.get(topic.id)! : positions.get(entry.ends[0])!;
          const b = positions.get(entry.ends[1])!;
          let points = simplifyRoute([a, ...[section.startPoint, ...(section.bendPoints ?? []), section.endPoint].map(convert), b]);
          if (entry.original && entry.ends[0] !== entry.original.source) points = points.reverse();
          branchPaths.push({ edge: entry.edge, original: entry.original, points });
        }
        branches.push({ ids: group, height: y1 - y0, top: y0, points: branchPaths });
      }
      const height = branches.reduce((sum, b) => sum + b.height, 0) + Math.max(0, branches.length - 1) * 80;
      let cursor = -height / 2;
      for (const branch of branches) {
        const dy = branches.length === 1 ? 0 : cursor - branch.top;
        for (const id of branch.ids) { const p = positions.get(id)!; positions.set(id, { ...p, y: p.y + dy }); }
        for (const path of branch.points) {
          if (!path.original && branches.length > 1) topicEdges.push({ ...path.edge });
          else paths.push({ ...path, points: path.points.map(p => ({ ...p, y: p.y + dy })) });
        }
        cursor += branch.height + 80;
      }
    }
    for (const { edge, points, original } of paths) {
      const route = { engine: "elk" as const, points: points.map(p => ({ x: p.x / UNIT, y: p.y / UNIT, z: 0 })) };
      if (original) localRoutes.set(original, { ...edge, route });
      else topicEdges.push({ ...edge, route });
    }
    const boxIds = [topic.id, ...members.map(n => n.id)], ps = boxIds.map(id => positions.get(id)!);
    // 대칭 여백은 Topic을 각 묶음의 중앙에 둔다. 글자 상자는 회전하거나 늘리지 않는다.
    const radius = Math.max(...ps.map(p => Math.abs(p.x) + 110));
    const top = Math.min(...ps.map(p => p.y - 24)), bottom = Math.max(...ps.map(p => p.y + 66));
    boxes.push({ id: topic.id, width: radius * 2, height: bottom - top, left: -radius, top, ids: boxIds });
  }
  // Topic 묶음 자체도 ELK가 화면 비율에 맞춰 배치한다. 세로 목록을 직접 만들지 않는다.
  const packed = await elk.layout({ id: "view-topics", layoutOptions: { "elk.algorithm": "rectpacking",
    "elk.aspectRatio": String(aspectRatio), "elk.spacing.nodeNode": "240",
    "elk.rectpacking.widthApproximation.targetWidth": String(Math.sqrt(boxes.reduce((sum, b) =>
      sum + (b.width + 240) * (b.height + 240), 0) * aspectRatio)) },
    children: boxes.map(b => ({ id: b.id, width: b.width, height: b.height })) });
  const shifts = new Map<string, RoutePoint>();
  for (let i = 0; i < boxes.length; i++) {
    const box = boxes[i], at = packed.children!.find(n => n.id === box.id)!;
    const shift = { x: at.x! - box.left - (packed.width || 0) / 2,
      y: at.y! - box.top - (packed.height || 0) / 2, z: (i % 7) * 260 };
    for (const id of box.ids) {
      const p = positions.get(id)!; positions.set(id, { x: p.x + shift.x, y: p.y + shift.y, z: shift.z });
      shifts.set(id, shift);
    }
  }
  for (const e of [...localRoutes.values(), ...topicEdges]) if (e.route) {
    const d = shifts.get(e.source)!;
    e.route.points = e.route.points.map(p => ({ x: p.x + d.x / UNIT, y: p.y + d.y / UNIT, z: d.z / UNIT }));
  }
  for (const n of nodes) {
    const p = positions.get(n.id)!;
    Object.assign(n, { x: p.x / UNIT, y: p.y / UNIT, z: p.z / UNIT, x2: p.x / UNIT, y2: -p.y / UNIT });
  }
  for (const e of [...localRoutes.values(), ...topicEdges]) if (e.route) {
    const a = map.get(e.source)!, b = map.get(e.target)!;
    e.route.points[0] = { x: a.x!, y: a.y!, z: a.z! };
    e.route.points[e.route.points.length - 1] = { x: b.x!, y: b.y!, z: b.z! };
  }
  // Topic hierarchy is membership, never ancestry or a causal arrow.
  for (const [domain, topic] of topics) {
    const parent = topics.get(domain.split("/").slice(0, -1).join("/"));
    if (parent && parent !== topic && domain.includes("/")) topicEdges.push({ source: parent.id,
      target: topic.id, type: "PARENT_OF", cross_domain: true, presentation: true });
  }
  const cross = usable.filter(e => !localRoutes.has(e));
  const toRoute = [...cross, ...topicEdges.filter(e => !e.route)];
  // 큰 지도는 Topic 내부와 Topic 사이를 별도로 라우팅한다. 전량의 작은 글자
  // 상자가 서로 먼 연결 하나의 가시성 그래프에 들어가지 않도록 한다.
  const routed = nodes.length > 500 ? await routeMap(nodes, toRoute, boxes, positions, topicOf, crossRouter) : await crossRouter(nodes, toRoute);
  const crossMap = new Map(cross.map((e, i) => [e, routed[i]]));
  const originalEdges = inputEdges.map(e => localRoutes.get(e) ?? crossMap.get(e) ?? { ...e });
  return { nodes, edges: originalEdges, topicEdges: [
    ...topicEdges.filter(e => e.route), ...routed.slice(cross.length),
  ] };
}

async function routeMap(nodes: GraphNode[], edges: GraphEdge[],
  boxes: { id: string; width: number; height: number; left: number; top: number; ids: string[] }[],
  positions: Map<string, RoutePoint>, topicOf: (n: GraphNode) => string, router: CrossRouter): Promise<GraphEdge[]> {
  const map = new Map(nodes.map(n => [n.id, n])), boxOf = new Map<string, string>();
  const obstacles: RoutingNode[] = [];
  for (const box of boxes) {
    const ps = box.ids.map(id => positions.get(id)!);
    const left = Math.min(...ps.map(p => p.x - 110)), right = Math.max(...ps.map(p => p.x + 110));
    const top = Math.min(...ps.map(p => p.y - 24)), bottom = Math.max(...ps.map(p => p.y + 66));
    const x = (left + right) / 2, y = (top + bottom) / 2;
    obstacles.push({ id: `@box:${box.id}`, label: "", kind: "domain", degree: 0, domain: box.id, x: x / UNIT, y: y / UNIT,
      z: 0, routingBox: { left, right, top, bottom } });
    box.ids.forEach(id => boxOf.set(id, box.id));
  }
  const byBox = new Map(obstacles.map(n => [n.id.slice(5), n]));
  const local = new Map<string, { edges: GraphEdge[]; indices: number[] }>();
  const portals = new Map<string, RoutingNode>(), legs: { index: number; source: GraphEdge; target: GraphEdge; middle: GraphEdge }[] = [];
  const addLocal = (box: string, e: GraphEdge, index: number) => {
    if (!local.has(box)) local.set(box, { edges: [], indices: [] });
    local.get(box)!.edges.push(e); local.get(box)!.indices.push(index);
  };
  const portal = (id: string, toward: string) => {
    const node = map.get(id)!, other = map.get(toward)!, box = byBox.get(boxOf.get(id)!)!.routingBox!;
    const dx = other.x! - node.x!, dy = other.y! - node.y!;
    let x = node.x! * UNIT, y = node.y! * UNIT;
    if (Math.abs(dx) / Math.max(1, box.right - box.left) >= Math.abs(dy) / Math.max(1, box.bottom - box.top))
      x = dx >= 0 ? box.right + 120 : box.left - 120;
    else y = dy >= 0 ? box.bottom + 120 : box.top - 120;
    const key = `@portal:${boxOf.get(id)}:${x}:${y}`;
    if (!portals.has(key)) portals.set(key, { id: key, label: "", kind: "concept", degree: 0, domain: topicOf(node),
      x: x / UNIT, y: y / UNIT, z: node.z, routingBox: { left: x - .5, right: x + .5, top: y - .5, bottom: y + .5 } });
    return key;
  };
  const result: GraphEdge[] = new Array(edges.length);
  edges.forEach((e, index) => {
    const a = boxOf.get(e.source)!, b = boxOf.get(e.target)!;
    if (a === b) { addLocal(a, e, index); return; }
    const pa = portal(e.source, e.target), pb = portal(e.target, e.source);
    const source = { ...e, target: pa }, target = { ...e, source: pb }, middle = { ...e, source: pa, target: pb };
    addLocal(a, source, -index - 1); addLocal(b, target, -index - 1);
    legs.push({ index, source, target, middle });
  });
  const localPaths = new Map<GraphEdge, GraphEdge>();
  for (const [id, batch] of local) {
    const subset = nodes.filter(n => boxOf.get(n.id) === id);
    const extra = new Set(batch.edges.flatMap(e => [e.source, e.target]).filter(id => portals.has(id)));
    const out = await router([...subset, ...[...extra].map(id => portals.get(id)!),
      ...obstacles.filter(n => n.id !== `@box:${id}`)], batch.edges);
    batch.edges.forEach((e, i) => { localPaths.set(e, out[i]); if (batch.indices[i] >= 0) result[batch.indices[i]] = out[i]; });
  }
  const middles = await router([...obstacles, ...portals.values()], legs.map(l => l.middle));
  legs.forEach((l, i) => { result[l.index] = { ...edges[l.index], route: { engine: "libavoid", points: simplifyRoute([
    ...localPaths.get(l.source)!.route!.points, ...middles[i].route!.points, ...localPaths.get(l.target)!.route!.points,
  ]) } }; });
  return result;
}
