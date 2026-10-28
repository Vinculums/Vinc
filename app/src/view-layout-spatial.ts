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
 * 3D 는 시간 축(x)을 뺀 두 축(y·z)도 모두 쓴다: 한 주제의 레인은 y·z 격자로 서고, 주제 칸은
 * 첫 주제(메인)를 시간 축 위에 두고 나머지를 그 둘레에 받은 순서대로 감아 돌린다. 칸마다 시작이
 * x 로 한 칸씩 늦어지므로(나선) 주제 사이에서도 이른 것이 왼쪽이다.
 * 근거: [[decision:time-branches-read-left-to-right]] */
export function spatialLayout(nodes: GraphNode[], edges: GraphEdge[], topics: SpatialTopic[]): void {
  const map = new Map(nodes.map(n => [n.id, n]));
  const placements = new Map<string, Placement>();
  const cells: { topic: SpatialTopic; points: Map<string, RoutePoint>; min: RoutePoint; max: RoutePoint }[] = [];
  for (const topic of topics) {
    const count = topic.branches.reduce((n, b) => n + b.ids.length, 0);
    // 한 레인이 끝없이 길어지지 않게 자른다. 잘린 레인은 옆(y)이나 뒤(z)에 선다.
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
        z: (Math.floor(i / columns) - Math.floor((rows - 1) / 2)) * pitch };
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
  // 주제 칸의 자리(y·z). 칸을 영역(지도 뿌리 아래 첫 단계)별로 묶어 영역마다 한 덩어리로 세운다.
  // 영역 안에서는 그 영역의 첫 칸을 영역 중심에 두고 나머지를 동심 고리에 채우고(`ringCentres`),
  // 영역 덩어리들도 같은 규칙으로 첫 영역(메인 토픽의 영역)을 시간 축 위에 두고 둘레에 세운다.
  // x 는 건드리지 않는다: 시작 x 는 여전히 전체 순서마다 `lead` 만큼 늦어서 영역이 달라도 이른
  // 주제가 왼쪽이다. 영역이 하나뿐이면 예전과 같은 자리가 나온다.
  // 근거: [[vinc.research.graph-layout-direction-rules-2026-10-28]] §6
  const snap = (v: number) => Math.round(v / STEP) * STEP;
  const gap = STEP * 2;
  const reach = cells.map(c => Math.hypot(c.max.y - c.min.y, c.max.z - c.min.z));
  const areaKey = areaKeys(cells.map(c => map.get(c.topic.id)?.domain ?? ""));
  const areas: number[][] = [];
  const areaIndex = new Map<string, number>();
  cells.forEach((_, i) => {
    const key = areaKey[i];
    if (!areaIndex.has(key)) { areaIndex.set(key, areas.length); areas.push([]); }
    areas[areaIndex.get(key)!].push(i);
  });
  const local = areas.map(members => ringCentres(members.map(i => reach[i]), gap));
  const areaReach = areas.map((members, a) => 2 * Math.max(...members.map((i, k) =>
    Math.hypot(local[a][k].y, local[a][k].z) + reach[i] / 2)));
  const areaCentres = ringCentres(areaReach, gap);
  const centres: { y: number; z: number }[] = new Array(cells.length);
  areas.forEach((members, a) => members.forEach((i, k) => {
    centres[i] = { y: areaCentres[a].y + local[a][k].y, z: areaCentres[a].z + local[a][k].z };
  }));
  const lead = STEP * 2;
  const length = Math.max(0, ...cells.map((c, i) => c.max.x - c.min.x + i * lead));
  cells.forEach((cell, i) => {
    const shift = { x: snap(-cell.min.x + i * lead - length / 2), y: snap(centres[i].y - (cell.min.y + cell.max.y) / 2),
      z: snap(centres[i].z - (cell.min.z + cell.max.z) / 2) };
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

/** 칸마다 영역 열쇠. 모든 칸 domain 의 공통 접두(경로 단위)를 벗기고 남은 첫 단계다. 허브 지도가
 * 뿌리를 정하는 방식(공통 접두를 벗긴다)과 같다. 접두와 같은 domain(뿌리 주제)은 빈 열쇠다. */
export function areaKeys(domains: string[]): string[] {
  const parts = domains.map(d => d ? d.split("/") : []);
  let common = parts.length ? Math.min(...parts.map(p => p.length)) : 0;
  for (let i = 0; i < common; i++) if (parts.some(p => p[i] !== parts[0][i])) { common = i; break; }
  return parts.map(p => p.slice(common, common + 1).join("/"));
}

/** 첫 자리는 원점, 나머지는 그 둘레의 동심 고리에 받은 순서대로 안쪽 고리부터 채운다. 고리 하나가
 * 차면 바깥에 새 고리를 연다. 고리 하나에 전부 세우면 수가 많을 때 반지름이 수에 비례해 커져서 가는
 * 테 하나가 된다(주제 174개에서 실측). 고리마다 호는 지름(`reach`)만큼 나눠 가지므로 큰 것이 이웃을
 * 덮지 않는다. */
export function ringCentres(reach: number[], gap: number): { y: number; z: number }[] {
  const centres: { y: number; z: number }[] = reach.length ? [{ y: 0, z: 0 }] : [];
  let inner = 0, innerReach = reach[0] ?? 0;
  for (let i = 1; i < reach.length;) {
    // 고리 반지름은 안쪽 고리와 이 고리의 가장 큰 것이 닿지 않는 만큼이고, 둘레가 모자라면 그만큼
    // 넓힌다. 넓힌 반지름이 바깥에 새 고리를 여는 자리를 넘으면 더 받지 않는다(첫 것은 늘 받는다).
    // 그래서 수가 적으면 고리 하나에 다 서고, 하나만 혼자 바깥으로 밀려나지 않는다.
    const ring: number[] = [];
    let widest = 0, used = 0, radius = 0;
    for (; i < reach.length; i++) {
      const w = Math.max(widest, reach[i]);
      const least = inner + (innerReach + w) / 2 + gap;
      const r = Math.max(least, (used + reach[i] + gap) / (2 * Math.PI));
      if (ring.length && r > least + w + gap) break;
      ring.push(i); widest = w; used += reach[i] + gap; radius = r;
    }
    // 고리 안의 자리는 받은 순서가 아니라 황금비 보폭으로 섞는다. 순서대로 돌리면 x 가 순서마다
    // 늦어지므로 고리 하나가 용수철 한 바퀴처럼 보인다. 섞으면 이웃한 순서가 고리의 먼 쪽에 선다.
    const golden = (Math.sqrt(5) - 1) / 2;
    ring.sort((a, b) => (a * golden) % 1 - (b * golden) % 1);
    let arc = 0;
    for (const j of ring) {
      const angle = 2 * Math.PI * (arc + (reach[j] + gap) / 2) / used;
      arc += reach[j] + gap;
      centres[j] = { y: radius * Math.cos(angle), z: radius * Math.sin(angle) };
    }
    inner = radius; innerReach = widest;
  }
  return centres;
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
