import type { GraphNode, GraphEdge } from "./api";
import type { ElkNode, ElkExtendedEdge } from "elkjs/lib/elk-api";
import { simplifyRoute, type RoutePoint } from "./view-route";
import type { RoutingNode } from "./view-layout-avoid";
import { spatialLayout, type SpatialTopic } from "./view-layout-spatial";
import { shortenPlanarRoutes } from "./view-route-shortcuts";

export type LayoutGraph = { nodes: GraphNode[]; edges: GraphEdge[]; topicEdges: GraphEdge[] };
export type LayoutEngine = { layout(graph: ElkNode): Promise<ElkNode> };
export type LayoutOrdering = "temporal" | "ranked";
export type CrossRouter = (nodes: GraphNode[], edges: GraphEdge[], batching?: "connector") => Promise<GraphEdge[]>;
const UNIT = 1000;
const GRID = 330;
type Direction = "RIGHT" | "LEFT" | "DOWN" | "UP";
// ELK가 정한 순서·직각 경로를 함께 늘려 격자에 맞춘다. 축을 줄이지 않아
// 원래 경로가 피한 글자 상자 안으로 선을 밀어 넣지 않는다.
function gridAxis(values: number[]): (value: number) => number {
  const xs = [...new Set([0, ...values].map(x => Math.round(x * 1e6) / 1e6))].sort((a, b) => a - b);
  const zero = xs.indexOf(0), ys = new Array<number>(xs.length); ys[zero] = 0;
  for (let i = zero + 1; i < xs.length; i++) ys[i] = ys[i - 1] + Math.ceil((xs[i] - xs[i - 1]) / GRID) * GRID;
  for (let i = zero - 1; i >= 0; i--) ys[i] = ys[i + 1] - Math.ceil((xs[i + 1] - xs[i]) / GRID) * GRID;
  return value => {
    if (value <= xs[0]) return ys[0] + value - xs[0];
    for (let i = 1; i < xs.length; i++) if (value <= xs[i])
      return ys[i - 1] + (value - xs[i - 1]) * (ys[i] - ys[i - 1]) / (xs[i] - xs[i - 1]);
    return ys.at(-1)! + value - xs.at(-1)!;
  };
}
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
function child(id: string, partition: number, direction: Direction): ElkNode {
  const ports = {
    RIGHT: [[220, 24, "EAST"], [0, 24, "WEST"]], LEFT: [[0, 24, "WEST"], [220, 24, "EAST"]],
    DOWN: [[110, 90, "SOUTH"], [110, 0, "NORTH"]], UP: [[110, 0, "NORTH"], [110, 90, "SOUTH"]],
  }[direction];
  return { id, width: 220, height: 90,
    layoutOptions: { "elk.portConstraints": "FIXED_POS", "elk.partitioning.partition": String(partition) },
    ports: [
      ...ports.map(([x, y, side], i) => ({ id: `${id}:${i ? "in" : "out"}`, x: Number(x), y: Number(y),
        layoutOptions: { "elk.port.side": String(side) } })),
    ] };
}

/** One topic's branches, laid out from that topic's members and inner relations only.
 * Nothing here reads the rest of the graph, so a piece can be reused whenever its key
 * (`pieceKey`) is unchanged. Positions and routes are relative to the topic mark. */
export type TopicPiece = {
  positions: [string, RoutePoint][];
  /** Route points of each inner relation in `inside` order; null when it is routed later across branches. */
  localRoutes: (RoutePoint[] | null)[];
  /** Membership guides in output order: routed in place, or left for the cross router. */
  guides: { edge: GraphEdge; points?: RoutePoint[] }[];
  box: { id: string; width: number; height: number; left: number; top: number; ids: string[] };
  spatial: SpatialTopic;
};
type PieceCache = { get(key: string): TopicPiece | undefined; set(key: string, piece: TopicPiece): void };
/** Where each topic box sat last time: its shift, depth and size. Passing the previous
 * placement keeps unchanged boxes where they were, so the corridors between them can be reused. */
export type BoxPlacement = { x: number; y: number; z: number; width: number; height: number; left: number; top: number };
/** Routes keyed by everything the router saw. A stored route is reused only if it still clears every box. */
type RouteCache = { get(key: string): RoutePoint[][] | undefined; set(key: string, routes: RoutePoint[][]): void };
type LayoutOptions = {
  pieces?: PieceCache;
  routes?: RouteCache;
  /** In: the previous placement. Out: `next` receives this run's placement. */
  placement?: { previous?: Map<string, BoxPlacement>; next?: Map<string, BoxPlacement> };
};
type Rect = { x0: number; y0: number; x1: number; y1: number };
const overlaps = (a: Rect, b: Rect, gap: number) =>
  a.x0 < b.x1 + gap && b.x0 < a.x1 + gap && a.y0 < b.y1 + gap && b.y0 < a.y1 + gap;
/** Keep boxes whose size is unchanged where they were. A changed or new box goes back to its old
 * spot if it is free, otherwise to the nearest free spot beside an already placed box. */
function stablePlacement(boxes: { id: string; width: number; height: number; left: number; top: number }[],
  previous: Map<string, BoxPlacement>): Map<string, BoxPlacement> {
  const gap = GRID * 2, placed: Rect[] = [], out = new Map<string, BoxPlacement>();
  const rectAt = (b: { width: number; height: number; left: number; top: number }, x: number, y: number): Rect =>
    ({ x0: x + b.left, y0: y + b.top, x1: x + b.left + b.width, y1: y + b.top + b.height });
  const same = (b: (typeof boxes)[number], q: BoxPlacement) =>
    q.width === b.width && q.height === b.height && q.left === b.left && q.top === b.top;
  // Unchanged boxes first, in topic order, so they never move.
  for (const b of boxes) {
    const q = previous.get(b.id);
    if (q && same(b, q)) { out.set(b.id, { ...q }); placed.push(rectAt(b, q.x, q.y)); }
  }
  const used = [...out.values()].map(q => q.z);
  for (const [i, b] of boxes.entries()) {
    if (out.has(b.id)) continue;
    const q = previous.get(b.id);
    const want = q ? { x: q.x, y: q.y } : { x: 0, y: 0 };
    const free = (x: number, y: number) => !placed.some(r => overlaps(rectAt(b, x, y), r, gap));
    let at = q && free(q.x, q.y) ? { x: q.x, y: q.y } : undefined;
    if (!at) {
      const candidates: { x: number; y: number }[] = [];
      for (const r of placed) {
        candidates.push({ x: r.x1 + gap - b.left, y: r.y0 - b.top }, { x: r.x0 - b.left, y: r.y1 + gap - b.top },
          { x: r.x0 - gap - b.width - b.left, y: r.y0 - b.top }, { x: r.x0 - b.left, y: r.y0 - gap - b.height - b.top });
      }
      if (!placed.length) candidates.push(want);
      at = candidates.map(c => ({ x: Math.round(c.x / GRID) * GRID, y: Math.round(c.y / GRID) * GRID }))
        .filter(c => free(c.x, c.y))
        .map(c => ({ c, d: Math.hypot(c.x - want.x, c.y - want.y) }))
        .sort((a, b2) => a.d - b2.d || a.c.x - b2.c.x || a.c.y - b2.c.y)[0]?.c;
      // Candidates beside placed boxes always include a free one past the outermost box.
      if (!at) throw new Error("No free spot for a topic box");
    }
    // Depth: keep the old level; a new box takes the least used of the seven levels.
    const z = q ? q.z : [0, 1, 2, 3, 4, 5, 6].map(k => k * 260)
      .sort((a, b2) => used.filter(u => u === a).length - used.filter(u => u === b2).length || a - b2)[0];
    used.push(z);
    out.set(b.id, { x: at.x, y: at.y, z, width: b.width, height: b.height, left: b.left, top: b.top });
    placed.push(rectAt(b, at.x, at.y));
    void i;
  }
  return out;
}
const clears = (points: RoutePoint[], boxes: Rect[]) => {
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1], b = points[i];
    const seg = { x0: Math.min(a.x, b.x), x1: Math.max(a.x, b.x), y0: Math.min(a.y, b.y), y1: Math.max(a.y, b.y) };
    for (const r of boxes) if (seg.x1 > r.x0 + 1e-6 && seg.x0 < r.x1 - 1e-6 && seg.y1 > r.y0 + 1e-6 && seg.y0 < r.y1 - 1e-6) return false;
  }
  return true;
};

/** Bump when layoutTopic changes what it produces for the same inputs, so stored pieces are not reused. */
const PIECE_VERSION = 1;
/** Everything a piece depends on, in the order it is read. Member and relation order matter:
 * they are the order ELK receives its children and edges in. */
function pieceKey(topic: GraphNode, members: GraphNode[], inside: GraphEdge[], basis: "recorded" | "content",
  ordering: LayoutOrdering, thorough: string): string {
  return JSON.stringify([PIECE_VERSION, topic.id, basis, ordering, thorough,
    members.map(n => [n.id, timeOf(n, basis)]), inside.map(e => [e.source, e.target, e.type])]);
}

async function layoutTopic(topic: GraphNode, members: GraphNode[], inside: GraphEdge[], basis: "recorded" | "content",
  elk: LayoutEngine, ordering: LayoutOrdering, thorough: string): Promise<TopicPiece> {
  const map = new Map(members.map(n => [n.id, n]));
  const positions = new Map<string, RoutePoint>();
  const insideIndex = new Map(inside.map((e, i) => [e, i]));
  const localRoutes: (RoutePoint[] | null)[] = inside.map(() => null);
  const guides: TopicPiece["guides"] = [];
  const spatialTopic: SpatialTopic = { id: topic.id, branches: [] };
  const ids = new Set(members.map(n => n.id));
  // 큰 연결 묶음도 날짜·관계를 버리지 않고 여러 가지로 나눈다. 가지 사이의
  // 원래 관계는 아래 공통 라우터가 잇는다. 날짜 순서는 각 가지의 바깥 방향이다.
  const limit = members.length < 8 ? 32 : Math.max(2, Math.min(32, Math.ceil(members.length / 4)));
  // 날짜만으로 잘라 관계를 대량 분단하지 않도록 실제 이웃을 따라 가지 순서를 만든다.
  const branchOrder = (group: string[]) => {
    const adj = new Map(group.map(id => [id, [] as string[]]));
    for (const e of inside) if (adj.has(e.source) && adj.has(e.target)) {
      adj.get(e.source)!.push(e.target); adj.get(e.target)!.push(e.source);
    }
    const compare = (a: string, b: string) =>
      timeOf(map.get(a)!, basis).localeCompare(timeOf(map.get(b)!, basis)) || a.localeCompare(b);
    const seen = new Set<string>(), sorted: string[] = [];
    const incoming = new Set(inside.filter(e => ["PREREQUISITE_OF", "DERIVES_FROM", "INHERITS_FROM"].includes(e.type))
      .map(e => ancestry(e)[1]));
    for (const root of group.slice().sort((a, b) => ordering === "ranked"
      ? Number(incoming.has(a)) - Number(incoming.has(b)) || compare(a, b) : compare(a, b))) {
      if (seen.has(root)) continue;
      const queue = [root]; seen.add(root);
      for (let at = 0; at < queue.length; at++) {
        const id = queue[at]; sorted.push(id);
        for (const next of adj.get(id)!.slice().sort(compare)) if (!seen.has(next)) { seen.add(next); queue.push(next); }
      }
    }
    return sorted;
  };
  let groups = components([...ids], inside).flatMap(group => {
    const dates = new Set(group.map(id => timeOf(map.get(id)!, basis)).filter(Boolean));
    if (group.length <= limit && dates.size <= 16) return [group];
    const sorted = branchOrder(group), parts: string[][] = [];
    for (let i = 0; i < sorted.length; i += limit) parts.push(sorted.slice(i, i + limit));
    return parts;
  });
  // Topic은 가지들의 중심에 있어야 한다. 작은 Topic(구성원 8 미만)은 위 분할 한도가 32라
  // 연결 묶음 하나가 날개 하나를 통째로 차지하고 Topic이 그 끝으로 밀렸다. 절반을 넘는
  // 묶음(다섯 이상)만 이웃 순서대로 반씩 나눠 맞은편 날개로 보낸다. 넷 이하는 한 방향
  // 사슬로 둔다. 나뉜 가지 사이의 원래 관계는 큰 묶음을 자를 때처럼 공통 라우터가 잇는다.
  for (;;) {
    const largest = groups.reduce((a, b) => b.length > a.length ? b : a, [] as string[]);
    if (largest.length < 5 || largest.length * 2 <= ids.size) break;
    const sorted = branchOrder(largest), half = Math.ceil(sorted.length / 2);
    groups = [...groups.filter(g => g !== largest), sorted.slice(0, half), sorted.slice(half)];
  }
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
  const wings: string[][][] = [[], [], [], []], loads = [0, 0, 0, 0];
  for (const group of groups) {
    const wing = loads.indexOf(Math.min(...loads));
    wings[wing].push(group); loads[wing] += group.length;
  }
  const paths: { edge: GraphEdge; points: RoutePoint[]; original?: GraphEdge }[] = [];
  positions.set(topic.id, { x: 0, y: 0, z: 0 });
  const directions: Direction[] = ["RIGHT", "LEFT", "DOWN", "UP"];
  const arms: { direction: Direction; branches: { ids: string[]; span: number; points: typeof paths; start: number }[]; span: number }[] = [];
  for (let wing = 0; wing < 4; wing++) {
    const direction = directions[wing], horizontal = wing < 2;
    const branches: (typeof arms)[number]["branches"] = [];
    for (let bi = 0; bi < wings[wing].length; bi++) {
      const group = wings[wing][bi], wingIds = new Set(group);
      if (!wingIds.size) continue;
      const dates = [...new Set(group.map(id => timeOf(map.get(id)!, basis)).filter(Boolean))].sort();
      const partitions = new Map(dates.map((d, i) => [d, i + 1]));
      const anchor = `@topic-anchor:${topic.id}:${wing}:${bi}`;
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
      const root = child(anchor, 0, direction);
      root.layoutOptions!["elk.layered.layering.layerConstraint"] = "FIRST";
      const result = await elk.layout({ id: `topic:${topic.id}:${wing}:${bi}`, layoutOptions: {
        "elk.algorithm": "layered", "elk.direction": direction, "elk.edgeRouting": "ORTHOGONAL",
        "elk.randomSeed": "10", "elk.separateConnectedComponents": "false",
        "elk.spacing.nodeNode": "80", "elk.layered.spacing.nodeNodeBetweenLayers": "110",
        "elk.spacing.edgeNode": "30", "elk.layered.spacing.edgeNodeBetweenLayers": "30",
        "elk.spacing.edgeEdge": "18", "elk.layered.spacing.edgeEdgeBetweenLayers": "18",
        "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
        // Chronological partitions already constrain the layers. The simplex
        // optimiser is costly for large cyclic branches; longest-path layering
        // honours those same partitions without its repeated optimisation.
        "elk.layered.layering.strategy": wingIds.size > 128 ? "LONGEST_PATH" : "NETWORK_SIMPLEX",
        "elk.layered.thoroughness": thorough,
        "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
        "elk.partitioning.activate": String(ordering === "temporal"),
      }, children: [root, ...members.filter(n => wingIds.has(n.id)).map(n =>
        child(n.id, partitions.get(timeOf(n, basis)) ?? 1, direction))],
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
      const centres = result.children!.filter(n => n.id !== anchor).map(n =>
        ({ x: n.x! - origin.x!, y: n.y! - origin.y! }));
      const gx = gridAxis(centres.map(p => p.x)), gy = gridAxis(centres.map(p => p.y));
      const convert = (p: { x: number; y: number }): RoutePoint =>
        ({ x: gx(p.x - origin.x! - 110), y: gy(p.y - origin.y! - 24), z: 0 });
      let low = 0, high = 0;
      for (const n of result.children!) {
        if (n.id === anchor) continue;
        const p = convert({ x: n.x! + 110, y: n.y! + 24 });
        positions.set(n.id, p);
        low = Math.min(low, horizontal ? p.y : p.x); high = Math.max(high, horizontal ? p.y : p.x);
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
      spatialTopic.branches.push({ ids: group, horizontal, sign: wing % 2 ? -1 : 1,
        positions: new Map(group.map(id => { const p = positions.get(id)!;
          return [id, { x: p.x / UNIT, y: p.y / UNIT, z: 0 }]; })) });
      branches.push({ ids: group, span: high - low, start: low, points: branchPaths });
    }
    const span = branches.reduce((sum, b) => sum + b.span, 0) + Math.max(0, branches.length - 1) * GRID;
    arms.push({ direction, branches, span });
  }
  // 위·아래 가지의 폭을 비워 좌우 가지와 겹치지 않게 한다. 마크·글자는 회전하지 않는다.
  const coreWidth = Math.ceil(Math.max(arms[2].span, arms[3].span) / 2 / GRID) * GRID;
  for (const arm of arms) {
    const horizontal = arm.direction === "RIGHT" || arm.direction === "LEFT";
    let cursor = -Math.floor(arm.span / 2 / GRID) * GRID;
    for (const branch of arm.branches) {
      const across = cursor - branch.start;
      const dx = horizontal ? (arm.direction === "RIGHT" ? coreWidth : -coreWidth) : across;
      const dy = horizontal ? across : 0;
      for (const id of branch.ids) { const p = positions.get(id)!; positions.set(id, { ...p, x: p.x + dx, y: p.y + dy }); }
      for (const path of branch.points) {
        if (!path.original && (dx || dy)) guides.push({ edge: { ...path.edge } });
        else paths.push({ ...path, points: path.points.map(p => ({ ...p, x: p.x + dx, y: p.y + dy })) });
      }
      cursor += branch.span + GRID;
    }
  }
  for (const { edge, points, original } of paths) {
    const route = { engine: "elk" as const, points: points.map(p => ({ x: p.x / UNIT, y: p.y / UNIT, z: 0 })) };
    if (original) localRoutes[insideIndex.get(original)!] = route.points;
    else guides.push({ edge: { ...edge }, points: route.points });
  }
  const boxIds = [topic.id, ...members.map(n => n.id)], ps = boxIds.map(id => positions.get(id)!);
  // 대칭 여백은 Topic을 각 묶음의 중앙에 둔다. 글자 상자는 회전하거나 늘리지 않는다.
  const radius = Math.ceil(Math.max(...ps.map(p => Math.abs(p.x) + 110)) / GRID) * GRID;
  const radiusY = Math.ceil(Math.max(...ps.map(p => Math.max(Math.abs(p.y - 24), Math.abs(p.y + 66)))) / GRID) * GRID;
  const box = { id: topic.id, width: radius * 2, height: radiusY * 2, left: -radius, top: -radiusY, ids: boxIds };
  return { positions: [...positions], localRoutes, guides, box, spatial: spatialTopic };
}

export async function temporalLayout(inputNodes: GraphNode[], inputEdges: GraphEdge[],
  basis: "recorded" | "content", elk: LayoutEngine, crossRouter: CrossRouter,
  ordering: LayoutOrdering = "temporal", aspectRatio = 1.6,
  options: LayoutOptions = {}): Promise<LayoutGraph> {
  const pieces = options.pieces;
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
  const spatialTopics: SpatialTopic[] = [];
  const boxes: { id: string; width: number; height: number; left: number; top: number; ids: string[] }[] = [];
  aspectRatio = Number.isFinite(aspectRatio) ? Math.max(.5, Math.min(3, aspectRatio)) : 1.6;
  for (const [domain, topic] of orderedTopics) {
    const members = nodes.filter(n => n.kind !== "domain" && topicOf(n) === domain);
    const ids = new Set(members.map(n => n.id));
    const inside = usable.filter(e => ids.has(e.source) && ids.has(e.target));
    const thorough = members.length > 32 ? "1" : "7";
    const key = pieces && pieceKey(topic, members, inside, basis, ordering, thorough);
    let piece = key ? pieces!.get(key) : undefined;
    if (!piece) { piece = await layoutTopic(topic, members, inside, basis, elk, ordering, thorough); if (key) pieces!.set(key, piece); }
    // A stored piece is never changed: everything below copies what it reads.
    for (const [id, point] of piece.positions) positions.set(id, { ...point });
    piece.localRoutes.forEach((points, i) => {
      if (points) localRoutes.set(inside[i], { ...inside[i], route: { engine: "elk" as const, points: points.map(q => ({ ...q })) } });
    });
    for (const guide of piece.guides) topicEdges.push(guide.points
      ? { ...guide.edge, route: { engine: "elk" as const, points: guide.points.map(q => ({ ...q })) } } : { ...guide.edge });
    boxes.push({ ...piece.box, ids: [...piece.box.ids] });
    spatialTopics.push({ id: piece.spatial.id, branches: piece.spatial.branches.map(branch => ({ ...branch, ids: [...branch.ids],
      positions: new Map([...branch.positions].map(([id, point]) => [id, { ...point }])) })) });
  }
  // Topic 묶음 자체도 ELK가 화면 비율에 맞춰 배치한다. 세로 목록을 직접 만들지 않는다.
  // 이전 배치가 있으면 다시 채우지 않는다: 크기가 같은 상자는 제자리에 남고, 그래야
  // 상자 사이 통로를 다시 계산하지 않는다. 근거: [[vinc.plan.incremental-derived-view-results-2026-10-12]]
  const previous = options.placement?.previous;
  let placement: Map<string, BoxPlacement>;
  if (previous && boxes.some(b => previous.has(b.id))) placement = stablePlacement(boxes, previous);
  else {
    const packed = await elk.layout({ id: "view-topics", layoutOptions: { "elk.algorithm": "rectpacking",
      "elk.aspectRatio": String(aspectRatio), "elk.spacing.nodeNode": String(GRID * 2),
      "elk.rectpacking.widthApproximation.targetWidth": String(Math.sqrt(boxes.reduce((sum, b) =>
        sum + (b.width + 240) * (b.height + 240), 0) * aspectRatio)) },
      children: boxes.map(b => ({ id: b.id, width: b.width, height: b.height })) });
    placement = new Map();
    for (let i = 0; i < boxes.length; i++) {
      const box = boxes[i], at = packed.children!.find(n => n.id === box.id)!;
      placement.set(box.id, { x: Math.round((at.x! - box.left - (packed.width || 0) / 2) / GRID) * GRID,
        y: Math.round((at.y! - box.top - (packed.height || 0) / 2) / GRID) * GRID, z: (i % 7) * 260,
        width: box.width, height: box.height, left: box.left, top: box.top });
    }
  }
  if (options.placement?.next) for (const [id, q] of placement) options.placement.next.set(id, { ...q });
  const shifts = new Map<string, RoutePoint>();
  for (let i = 0; i < boxes.length; i++) {
    const box = boxes[i], q = placement.get(box.id)!;
    const shift = { x: q.x, y: q.y, z: q.z };
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
  const routed = nodes.length > 500 ? await routeMap(nodes, toRoute, boxes, positions, topicOf, crossRouter, options.routes)
    : await crossRouter(nodes, toRoute);
  const crossMap = new Map(cross.map((e, i) => [e, routed[i]]));
  const originalEdges = inputEdges.map(e => localRoutes.get(e) ?? crossMap.get(e) ?? { ...e });
  const finalTopicEdges = [
    ...topicEdges.filter(e => e.route), ...routed.slice(cross.length),
  ];
  shortenPlanarRoutes(nodes, [...originalEdges, ...finalTopicEdges]);
  spatialLayout(nodes, [...originalEdges, ...finalTopicEdges], spatialTopics);
  return { nodes, edges: originalEdges, topicEdges: finalTopicEdges };
}

async function routeMap(nodes: GraphNode[], edges: GraphEdge[],
  boxes: { id: string; width: number; height: number; left: number; top: number; ids: string[] }[],
  positions: Map<string, RoutePoint>, topicOf: (n: GraphNode) => string, router: CrossRouter,
  cache?: RouteCache): Promise<GraphEdge[]> {
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
    // Share access points on Topic boundaries. Each original relation keeps
    // its individual source and target legs; external geometry is reused.
    let x = Math.round((box.left + box.right) / 2 / GRID) * GRID;
    let y = Math.round((box.top + box.bottom) / 2 / GRID) * GRID;
    if (Math.abs(dx) / Math.max(1, box.right - box.left) >= Math.abs(dy) / Math.max(1, box.bottom - box.top))
      x = dx >= 0 ? box.right + 120 : box.left - 120;
    else y = dy >= 0 ? box.bottom + 120 : box.top - 120;
    const key = `@portal:${boxOf.get(id)}:${x}:${y}`;
    if (!portals.has(key)) portals.set(key, { id: key, label: "", kind: "concept", degree: 0, domain: topicOf(node),
      x: x / UNIT, y: y / UNIT, z: node.z, routingObstacle: false,
      routingBox: { left: x - .5, right: x + .5, top: y - .5, bottom: y + .5 } });
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
  // 상자는 GRID*2 떨어져 있으므로 한 상자의 다리 경로가 닿을 수 있는 이웃은 그 거리 안의 상자뿐이다.
  const near = (box: { left: number; right: number; top: number; bottom: number }, margin: number) => obstacles
    .filter(o => o.routingBox!.right > box.left - margin && o.routingBox!.left < box.right + margin
      && o.routingBox!.bottom > box.top - margin && o.routingBox!.top < box.bottom + margin)
    .map(o => [o.id, o.routingBox!.left, o.routingBox!.top, o.routingBox!.right, o.routingBox!.bottom]);
  for (const [id, batch] of local) {
    const subset = nodes.filter(n => boxOf.get(n.id) === id);
    const extra = new Set(batch.edges.flatMap(e => [e.source, e.target]).filter(id => portals.has(id)));
    // 라우터가 본 것 전부가 키다: 상자 안 마크 위치, 포털, 관계 끝점, 닿을 수 있는 이웃 상자.
    const key = cache && JSON.stringify(["local", id, subset.map(n => [n.id, n.x, n.y]), [...extra].sort(),
      batch.edges.map(e => [e.source, e.target]), near(byBox.get(id)!.routingBox!, GRID * 3)]);
    const stored = key ? cache!.get(key) : undefined;
    const out = stored ? batch.edges.map((e, i) => ({ ...e, route: { engine: "libavoid" as const, points: stored[i].map(q => ({ ...q })) } }))
      : await router([...subset, ...[...extra].map(id => portals.get(id)!),
        ...obstacles.filter(n => n.id !== `@box:${id}`)], batch.edges);
    if (key && !stored) cache!.set(key, out.map(e => e.route!.points.map(q => ({ ...q }))));
    batch.edges.forEach((e, i) => { localPaths.set(e, out[i]); if (batch.indices[i] >= 0) result[batch.indices[i]] = out[i]; });
  }
  const unique = new Map<string, GraphEdge>();
  const keyOf = (e: GraphEdge) => JSON.stringify([e.source, e.target]);
  for (const l of legs) if (!unique.has(keyOf(l.middle))) unique.set(keyOf(l.middle), l.middle);
  // 통로는 양 끝 포털(상자 id 와 절대 좌표)이 같고 지금 어느 상자도 가로지르지 않을 때만 다시 쓴다.
  const boxRects = obstacles.map(o => ({ x0: o.routingBox!.left / UNIT, x1: o.routingBox!.right / UNIT,
    y0: o.routingBox!.top / UNIT, y1: o.routingBox!.bottom / UNIT }));
  const middlePaths = new Map<string, RoutePoint[]>(), pending: string[] = [];
  for (const key of unique.keys()) {
    const stored = cache?.get("corridor:" + key)?.[0];
    if (stored && clears(stored, boxRects)) middlePaths.set(key, stored.map(q => ({ ...q })));
    else pending.push(key);
  }
  const corridors = pending.length ? await router([...obstacles, ...portals.values()], pending.map(key => unique.get(key)!), "connector") : [];
  pending.forEach((key, i) => {
    middlePaths.set(key, corridors[i].route!.points);
    cache?.set("corridor:" + key, [corridors[i].route!.points.map(q => ({ ...q }))]);
  });
  legs.forEach(l => { result[l.index] = { ...edges[l.index], route: { engine: "libavoid", points: simplifyRoute([
    ...localPaths.get(l.source)!.route!.points, ...middlePaths.get(keyOf(l.middle))!, ...localPaths.get(l.target)!.route!.points,
  ]) } }; });
  return result;
}
