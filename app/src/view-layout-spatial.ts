import type { GraphNode, GraphEdge } from "./api";
import { simplifyRoute, routeLength, type RoutePoint } from "./view-route";

type Axis = keyof RoutePoint;
const AXES: Axis[] = ["x", "y", "z"];
const STEP = .33;
export type SpatialBranch = { ids: string[]; positions: Map<string, RoutePoint>; horizontal: boolean; sign: number };
export type SpatialTopic = { id: string; branches: SpatialBranch[] };
type Placement = { branch: SpatialBranch; primary: Axis; across: Axis; sign: number; start: number; lateral: number; offset: RoutePoint };

/** ELK remains a planar engine. Compose its chronological branch sections into
 * six spatial arms, without rotating the actual marks or flattening the 2D map. */
export function spatialLayout(nodes: GraphNode[], edges: GraphEdge[], topics: SpatialTopic[]): void {
  const map = new Map(nodes.map(n => [n.id, n]));
  const placements = new Map<string, Placement>();
  const cells: { topic: SpatialTopic; points: Map<string, RoutePoint>; radius: RoutePoint }[] = [];
  for (const topic of topics) {
    const count = topic.branches.reduce((n, b) => n + b.ids.length, 0);
    const limit = Math.max(1, Math.min(32, Math.ceil(count / 6)));
    const arms: { branch: SpatialBranch; ids: string[] }[][] = Array.from({ length: 6 }, () => []);
    const loads = [0, 0, 0, 0, 0, 0];
    for (const branch of topic.branches) {
      const primary = branch.horizontal ? "x" : "y";
      const sorted = branch.ids.slice().sort((a, b) => branch.sign *
        (branch.positions.get(a)![primary] - branch.positions.get(b)![primary]) || a.localeCompare(b));
      for (let i = 0; i < sorted.length; i += limit) {
        const ids = sorted.slice(i, i + limit), arm = loads.indexOf(Math.min(...loads));
        arms[arm].push({ branch, ids }); loads[arm] += ids.length;
      }
    }
    const points = new Map<string, RoutePoint>([[topic.id, { x: 0, y: 0, z: 0 }]]);
    const topicPlacements: Placement[] = [];
    let core = STEP;
    arms.forEach((groups, arm) => {
      const primary = AXES[Math.floor(arm / 2)], across = AXES[(Math.floor(arm / 2) + 1) % 3];
      const depth = AXES[(Math.floor(arm / 2) + 2) % 3], sign = arm % 2 ? -1 : 1;
      const spans = groups.map(({ branch, ids }) => {
        const lateral = branch.horizontal ? "y" : "x";
        const values = ids.map(id => branch.positions.get(id)![lateral]);
        return Math.max(...values) - Math.min(...values);
      });
      const columns = Math.ceil(Math.sqrt(groups.length));
      // Centring an odd-width branch on the integer grid can move either end
      // half a cell. Reserve both halves so adjacent lanes never touch.
      const pitch = Math.ceil(Math.max(STEP, ...spans) / STEP) * STEP + STEP * 2;
      groups.forEach(({ branch, ids }, i) => {
        const along = branch.horizontal ? "x" : "y", lateralAxis = branch.horizontal ? "y" : "x";
        const start = Math.min(...ids.map(id => branch.sign * branch.positions.get(id)![along])) - STEP;
        const lateral = Math.round((Math.min(...ids.map(id => branch.positions.get(id)![lateralAxis])) +
          Math.max(...ids.map(id => branch.positions.get(id)![lateralAxis]))) / 2 / STEP) * STEP;
        const offset: RoutePoint = { x: 0, y: 0, z: 0 };
        offset[across] = (i % columns - Math.floor((columns - 1) / 2)) * pitch;
        offset[depth] = (Math.floor(i / columns) - Math.floor((Math.ceil(groups.length / columns) - 1) / 2)) * STEP;
        const placement = { branch, primary, across, sign, start, lateral, offset };
        topicPlacements.push(placement);
        ids.forEach(id => {
          placements.set(id, placement);
          const p = transform(branch.positions.get(id)!, placement);
          points.set(id, p); core = Math.max(core, Math.abs(p[across]), Math.abs(p[depth]));
        });
      });
    });
    // A shared empty core keeps the perpendicular arms apart. The Topic stays
    // central; no chronology or original relation direction is inferred here.
    core = Math.ceil(core / STEP) * STEP;
    topicPlacements.forEach(p => { p.offset[p.primary] = p.sign * core; });
    for (const [id, point] of points) if (id !== topic.id) {
      const p = placements.get(id)!; point[p.primary] += p.sign * core;
    }
    const radius = Object.fromEntries(AXES.map(axis => [axis,
      Math.ceil(Math.max(STEP, ...[...points.values()].map(p => Math.abs(p[axis]))) / STEP) * STEP + STEP])) as RoutePoint;
    cells.push({ topic, points, radius });
  }
  // Variable-sized shelves fill x/y/z. Topic volumes never intersect, and all
  // centres retain the same grid; a shallow index-based depth offset is absent.
  const columns = Math.ceil(Math.cbrt(cells.length)), rows = Math.ceil(Math.sqrt(cells.length / columns));
  const widths = Array(columns).fill(0), heights = Array(rows).fill(0), depths: number[] = [];
  cells.forEach((cell, i) => {
    const x = i % columns, y = Math.floor(i / columns) % rows, z = Math.floor(i / (columns * rows));
    widths[x] = Math.max(widths[x], cell.radius.x * 2); heights[y] = Math.max(heights[y], cell.radius.y * 2);
    depths[z] = Math.max(depths[z] || 0, cell.radius.z * 2);
  });
  const centres = (sizes: number[]) => sizes.map((size, i) =>
    sizes.slice(0, i).reduce((a, b) => a + b, 0) + size / 2 - sizes.reduce((a, b) => a + b, 0) / 2);
  const xs = centres(widths), ys = centres(heights), zs = centres(depths);
  cells.forEach((cell, i) => {
    const shift = { x: xs[i % columns], y: ys[Math.floor(i / columns) % rows], z: zs[Math.floor(i / (columns * rows))] };
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
  const index = new Map<string, GraphNode[]>();
  const cell = (v: number) => Math.round(v / STEP);
  const key = (x: number, y: number, z: number) => `${x},${y},${z}`;
  for (const n of nodes) {
    const k = key(cell(n.x!), cell(n.y!), cell(n.z!));
    if (index.has(k)) throw new Error("Spatial View mark centres overlap");
    if (!index.has(k)) index.set(k, []); index.get(k)!.push(n);
  }
  const clear = (points: RoutePoint[], source: string, target: string) => {
    for (let i = 1; i < points.length; i++) {
      const a = points[i - 1], b = points[i], along = AXES.find(axis => Math.abs(a[axis] - b[axis]) > 1e-9);
      if (!along) continue;
      const others = AXES.filter(axis => axis !== along), [u, v] = others;
      for (let t = Math.min(cell(a[along]), cell(b[along])); t <= Math.max(cell(a[along]), cell(b[along])); t++) {
        const p = { ...a, [along]: t * STEP };
        for (const n of index.get(key(cell(p.x), cell(p.y), cell(p.z))) ?? []) {
          if (n.id === source || n.id === target) continue;
          if (Math.abs(a[u] - n[u]!) < .12 && Math.abs(a[v] - n[v]!) < .12 &&
              Math.max(a[along], b[along]) > n[along]! - .12 && Math.min(a[along], b[along]) < n[along]! + .12) return false;
        }
      }
    }
    return true;
  };
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
