import type { GraphNode, GraphEdge } from "./api";
import { simplifyRoute, routeLength, type RoutePoint } from "./view-route";
import { spatialClearance, STEP } from "./view-layout-obstacles";

type Axis = keyof RoutePoint;
const AXES: Axis[] = ["x", "y", "z"];
type SpatialBranch = { ids: string[]; positions: Map<string, RoutePoint>; horizontal: boolean; sign: number };
export type SpatialTopic = { id: string; branches: SpatialBranch[] };
type Placement = { branch: SpatialBranch; primary: Axis; across: Axis; sign: number; start: number; lateral: number; offset: RoutePoint };

/** 시간순 열 흐름(평면 지도와 3D 가 같이 쓴다). 받은 순서대로 위에서 아래로 쌓고, 열이 목표
 * 높이를 넘으면 오른쪽에 새 열을 연다. 그래서 왼쪽 위가 가장 먼저이고 오른쪽 아래가 가장
 * 나중이다. 목표 높이는 전체 넓이와 화면 비율에서 나온다(넓은 화면이면 열이 짧아져 가로로
 * 길어진다). 돌려주는 것은 화면 좌표(아래로 +y)의 왼쪽 위 모서리이고, 전체 묶음의 가운데가
 * 원점이 되도록 옮겨 둔다. */
export function flowColumns(sizes: { width: number; height: number }[], aspectRatio: number,
  gap: number): { x: number; y: number }[] {
  if (!sizes.length) return [];
  const area = sizes.reduce((sum, s) => sum + (s.width + gap) * (s.height + gap), 0);
  const target = Math.max(...sizes.map(s => s.height), Math.sqrt(area / aspectRatio));
  const out: { x: number; y: number }[] = [];
  let x = 0, y = 0, column = 0, width = 0, height = 0;
  for (const s of sizes) {
    if (y > 0 && y + s.height > target) { x += column + gap; y = 0; column = 0; }
    out.push({ x, y });
    y += s.height + gap; column = Math.max(column, s.width);
    width = Math.max(width, x + s.width); height = Math.max(height, y - gap);
  }
  return out.map(p => ({ x: p.x - width / 2, y: p.y - height / 2 }));
}

/** ELK remains a planar engine. Compose its chronological branch sections into one
 * spatial arm per Topic that grows along +x, so later is further right as on the 2D map.
 * The arm's lanes sit side by side across y and step back in depth (z). Topic cells then
 * flow left to right in the order given, which is the planar layout's chronological order. */
export function spatialLayout(nodes: GraphNode[], edges: GraphEdge[], topics: SpatialTopic[],
  aspectRatio = 1.6): void {
  const map = new Map(nodes.map(n => [n.id, n]));
  const placements = new Map<string, Placement>();
  const cells: { topic: SpatialTopic; points: Map<string, RoutePoint>; min: RoutePoint; max: RoutePoint }[] = [];
  for (const topic of topics) {
    const count = topic.branches.reduce((n, b) => n + b.ids.length, 0);
    // 한 레인이 끝없이 길어지지 않게 자른다. 잘린 레인은 옆(y)이나 한 칸 뒤(z)에 선다.
    const limit = Math.max(1, Math.min(32, Math.ceil(count / 6)));
    const groups: { branch: SpatialBranch; ids: string[] }[] = [];
    for (const branch of topic.branches) {
      const sorted = branch.ids.slice().sort((a, b) =>
        branch.positions.get(a)!.x - branch.positions.get(b)!.x || a.localeCompare(b));
      for (let i = 0; i < sorted.length; i += limit) groups.push({ branch, ids: sorted.slice(i, i + limit) });
    }
    const points = new Map<string, RoutePoint>([[topic.id, { x: 0, y: 0, z: 0 }]]);
    const spans = groups.map(({ branch, ids }) => {
      const values = ids.map(id => branch.positions.get(id)!.y);
      return Math.max(...values) - Math.min(...values);
    });
    const columns = Math.ceil(Math.sqrt(groups.length)), rows = Math.ceil(groups.length / columns);
    // Centring an odd-width branch on the integer grid can move either end
    // half a cell. Reserve both halves so adjacent lanes never touch.
    const pitch = Math.ceil(Math.max(STEP, ...spans) / STEP) * STEP + STEP * 2;
    groups.forEach(({ branch, ids }, i) => {
      const start = Math.min(...ids.map(id => branch.positions.get(id)!.x)) - STEP;
      const lateral = Math.round((Math.min(...ids.map(id => branch.positions.get(id)!.y)) +
        Math.max(...ids.map(id => branch.positions.get(id)!.y))) / 2 / STEP) * STEP;
      const offset: RoutePoint = { x: 0, y: (i % columns - Math.floor((columns - 1) / 2)) * pitch,
        z: (Math.floor(i / columns) - Math.floor((rows - 1) / 2)) * STEP };
      const placement: Placement = { branch, primary: "x", across: "y", sign: 1, start, lateral, offset };
      ids.forEach(id => {
        placements.set(id, placement);
        points.set(id, transform(branch.positions.get(id)!, placement));
      });
    });
    const ps = [...points.values()];
    const bound = (pick: (...v: number[]) => number, round: (v: number) => number, pad: number) =>
      Object.fromEntries(AXES.map(axis => [axis, round(pick(...ps.map(p => p[axis])) / STEP) * STEP + pad])) as RoutePoint;
    cells.push({ topic, points, min: bound(Math.min, Math.floor, -STEP), max: bound(Math.max, Math.ceil, STEP) });
  }
  // Topic 칸은 평면 지도와 같은 열 흐름으로 선다. 화면 좌표(아래로 +y)의 모서리를 3D(위로 +y)로
  // 옮기고, 깊이는 칸마다 가운데에 맞춘다. 모든 중심은 같은 격자에 남는다.
  const corners = flowColumns(cells.map(c => ({ width: c.max.x - c.min.x, height: c.max.y - c.min.y })),
    aspectRatio, STEP * 2);
  const snap = (v: number) => Math.round(v / STEP) * STEP;
  cells.forEach((cell, i) => {
    const shift = { x: snap(corners[i].x - cell.min.x), y: snap(-corners[i].y - cell.max.y),
      z: -snap((cell.min.z + cell.max.z) / 2) };
    for (const [id, point] of cell.points) {
      const node = map.get(id)!;
      node.x = point.x + shift.x; node.y = point.y + shift.y; node.z = point.z + shift.z;
    }
    const unique = new Set([...cell.points.keys()].map(id => placements.get(id)).filter((p): p is Placement => !!p));
    for (const p of unique) for (const axis of AXES) p.offset[axis] += shift[axis];
  });
  const router = spatialRouter(nodes), cache = new Map<string, RoutePoint[]>();
  for (const edge of edges) {
    const source = map.get(edge.source), target = map.get(edge.target);
    if (!edge.route || !source || !target) continue;
    const planar = edge.route.points;
    const planarEngine = edge.route.engine as "elk" | "libavoid";
    const a = placements.get(edge.source), b = placements.get(edge.target);
    let points: RoutePoint[] | undefined;
    if (a && a === b && edge.route.engine === "elk") {
      points = simplifyRoute(planar.map(p => {
        // The saved branch positions precede planar Topic/arm translations.
        const original = a.branch.positions.get(edge.source)!;
        const delta = { x: planar[0].x - original.x, y: planar[0].y - original.y, z: planar[0].z };
        return transform({ x: p.x - delta.x, y: p.y - delta.y, z: 0 }, a);
      }));
      points[0] = xyz(source); points[points.length - 1] = xyz(target);
      if (!router.clear(points, edge.source, edge.target)) points = undefined;
    }
    let spatial: RoutePoint[];
    {
      const key = JSON.stringify([edge.source, edge.target].sort());
      const cached = cache.get(key);
      if (cached) spatial = edge.source <= edge.target ? cached : cached.slice().reverse();
      else {
        spatial = router.route(xyz(source), xyz(target), edge.source, edge.target);
        cache.set(key, edge.source <= edge.target ? spatial : spatial.slice().reverse());
      }
    }
    const retainElk = points && (routeLength(points) < routeLength(spatial) - 1e-8 ||
      Math.abs(routeLength(points) - routeLength(spatial)) < 1e-8 && points.length <= spatial.length);
    edge.route = { ...edge.route, points2d: planar, points: retainElk ? points! : spatial,
      engine2d: planarEngine, engine: retainElk ? "elk" : "spatial" };
  }
}

const xyz = (p: GraphNode): RoutePoint => ({ x: p.x!, y: p.y!, z: p.z! });
function transform(point: RoutePoint, p: Placement): RoutePoint {
  const out = { ...p.offset };
  out[p.primary] += p.sign * (p.branch.sign * point[p.branch.horizontal ? "x" : "y"] - p.start);
  out[p.across] += point[p.branch.horizontal ? "y" : "x"] - p.lateral;
  return out;
}

/** Prefer the six shortest Manhattan paths. Only blocked paths use half-grid
 * channels: every mark centre is on the integer grid, so these channels remain
 * clear in all three dimensions. Geometry is reused, authored edges are not. */
function spatialRouter(nodes: GraphNode[]) {
  const clear = spatialClearance(nodes);
  const permutations: Axis[][] = [["x", "y", "z"], ["x", "z", "y"], ["y", "x", "z"],
    ["y", "z", "x"], ["z", "x", "y"], ["z", "y", "x"]];
  const route = (a: RoutePoint, b: RoutePoint, source: string, target: string) => {
    if (source === target) return [a, { ...a, x: a.x + STEP / 2 },
      { ...a, x: a.x + STEP / 2, y: a.y + STEP / 2 }, { ...a, y: a.y + STEP / 2 }, b];
    const candidates: RoutePoint[][] = [];
    for (const axes of permutations) {
      const path = [{ ...a }];
      for (const axis of axes) path.push({ ...path.at(-1)!, [axis]: b[axis] });
      candidates.push(simplifyRoute(path));
    }
    for (const [u, v] of permutations) for (const sign of [-1, 1]) {
      const channel = Math.round(a[u] / STEP) * STEP + sign * STEP / 2;
      const lane = Math.round(b[v] / STEP) * STEP + sign * STEP / 2;
      candidates.push(simplifyRoute([a, { ...a, [u]: channel }, { ...a, [u]: channel, [v]: lane },
        { ...b, [u]: channel, [v]: lane }, { ...b, [v]: lane }, b]));
    }
    candidates.sort((a, b) => Math.abs(routeLength(a) - routeLength(b)) > 1e-8
      ? routeLength(a) - routeLength(b) : a.length - b.length);
    const result = candidates.find(path => clear(path, source, target));
    if (!result) throw new Error("No clear spatial View route");
    return result;
  };
  return { clear, route };
}
