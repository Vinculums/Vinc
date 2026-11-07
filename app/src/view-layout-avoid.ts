import type { GraphEdge, GraphNode } from "./api";
import { simplifyRoute, type RoutePoint } from "./view-route";
import { topicOf } from "./node-topic";

export type RoutingNode = GraphNode & { routingBox?: { left: number; right: number; top: number; bottom: number }; routingObstacle?: boolean };
/** 노드 상자 한 벌(배치 좌표 × `UNIT` = px). ELK 자식 상자(`view-layout.ts` 의 `child`)와
 *  libavoid 장애물이 같은 상자를 써야 경로가 마크를 비켜 간다. 노드 좌표는 상자의 가로
 *  중앙 · 위에서 `NODE_PORT_Y` 아래(왼쪽·오른쪽 포트 높이)다. */
export const UNIT = 1000;
export const NODE_W = 220, NODE_H = 90, NODE_PORT_Y = 24;
/** 노드 좌표에서 상자 옆면까지, 아랫면까지. */
export const NODE_HALF_W = NODE_W / 2, NODE_BELOW = NODE_H - NODE_PORT_Y;
const rectangleOf = (n: RoutingNode) => n.routingBox || { left: n.x! * UNIT - NODE_HALF_W, right: n.x! * UNIT + NODE_HALF_W,
  top: n.y! * UNIT - NODE_PORT_Y, bottom: n.y! * UNIT + NODE_BELOW };

/** The WASM binding has incomplete type declarations. Keep that boundary here.
 * The router owns shapes, pins, and connectors; temporary value objects do not. */
function routeBatch(Avoid: any, nodes: GraphNode[], edges: GraphEdge[]): GraphEdge[] {
  if (!edges.length) return [];
  const router = new Avoid.Router(Avoid.OrthogonalRouting);
  const temporaries: any[] = [], shapes = new Map<string, any>();
  const loops = new Set(edges.filter(e => e.source === e.target).map(e => e.source));
  const own = (v: any) => { temporaries.push(v); return v; };
  const map = new Map(nodes.map(n => [n.id, n]));
  const raw = (n: GraphNode): RoutePoint => ({ x: n.x! * UNIT, y: n.y! * UNIT, z: n.z! * UNIT });
  try {
    // libavoid documents crossing penalties as experimental: they add a global
    // crossing-detection/rerouting pass and can create detours. Keep the normal
    // orthogonal route and bend cost, plus obstacle clearance and nudging.
    for (const [key, value] of [["segmentPenalty", 120],
      ["shapeBufferDistance", 4], ["idealNudgingDistance", 12]] as const)
      router.setRoutingParameter(Avoid[key], value);
    router.setRoutingOption(Avoid.nudgeSharedPathsWithCommonEndPoint, true);
    for (const node of nodes) {
      const n = raw(node);
      const rect = rectangleOf(node);
      const rectangle = own(new Avoid.Rectangle(own(new Avoid.Point(rect.left, rect.top)),
        own(new Avoid.Point(rect.right, rect.bottom))));
      const shape = new Avoid.ShapeRef(router, rectangle);
      const ports = [
        { x: NODE_W, y: NODE_PORT_Y, dir: Avoid.ConnDirRight },
        { x: 0, y: NODE_PORT_Y, dir: Avoid.ConnDirLeft },
      ];
      // Route from the actual mark centre with all departure directions. This
      // makes bend cost include the complete visible path rather than choosing
      // a boundary port first and appending centre-to-port stubs afterwards.
      new Avoid.ShapeConnectionPin(shape, 1, n.x - rect.left, n.y - rect.top, false, 0, Avoid.ConnDirAll).setExclusive(false);
      if (loops.has(node.id)) for (let i = 0; i < 2; i++) {
        const p = ports[i];
        new Avoid.ShapeConnectionPin(shape, i + 2, p.x, p.y, false, 0, p.dir).setExclusive(false);
      }
      shapes.set(node.id, shape);
    }
    const connections = edges.map(edge => {
      const a = shapes.get(edge.source)!, b = shapes.get(edge.target)!;
      const loop = edge.source === edge.target;
      const connection = new Avoid.ConnRef(router,
        own(new Avoid.ConnEnd(a, loop ? 2 : 1)), own(new Avoid.ConnEnd(b, loop ? 3 : 1)));
      connection.setRoutingType(Avoid.ConnType_Orthogonal);
      return { edge, connection };
    });
    router.processTransaction();
    return connections.map(({ edge, connection }) => {
      const path = connection.displayRoute(), a = raw(map.get(edge.source)!), b = raw(map.get(edge.target)!);
      const points: RoutePoint[] = [a];
      for (let i = 0; i < path.size(); i++) {
        const p = path.get_ps(i); points.push({ x: p.x, y: p.y, z: a.z });
      }
      if (points.length < 3) throw new Error("Missing libavoid route");
      // Change depth at the target mark, keeping every segment axis aligned.
      points.push({ ...points[points.length - 1], z: b.z }, b);
      return { ...edge, route: { engine: "libavoid" as const, points: simplifyRoute(points)
        .map(p => ({ x: p.x / UNIT, y: p.y / UNIT, z: p.z / UNIT })) } };
    });
  } finally {
    Avoid.destroy(router);
    for (const value of temporaries.reverse()) Avoid.destroy(value);
  }
}

/** A full-store visibility graph grows even when only two topics are linked.
 * Route each topic pair from its endpoint shapes, then check every mark.
 * Any obstacle reached by a detour is added before recomputing the whole pair. */
export function routeAcrossTopics(Avoid: any, nodes: GraphNode[], edges: GraphEdge[], batching?: "connector"): GraphEdge[] {
  if (!edges.length) return [];
  // Geometry is independent of relation type/evidence. Shared access legs can
  // repeat hundreds of times; routing/nudging each copy multiplies WASM work.
  // Retain every original relation and restore its direction after routing.
  const pairKey = (e: GraphEdge) => JSON.stringify([e.source, e.target].sort());
  const unique = new Map<string, GraphEdge>();
  for (const e of edges) if (!unique.has(pairKey(e))) unique.set(pairKey(e), e);
  if (unique.size < edges.length) {
    const geometry = routeAcrossTopics(Avoid, nodes, [...unique.values()], batching);
    const paths = new Map([...unique.keys()].map((key, i) => [key, geometry[i]]));
    return edges.map(e => {
      const path = paths.get(pairKey(e))!;
      return { ...e, route: { ...path.route!, points: e.source === path.source
        ? path.route!.points : [...path.route!.points].reverse() } };
    });
  }
  const map = new Map(nodes.map(n => [n.id, n]));
  const topic = (id: string) => topicOf(map.get(id)!);
  const batches = new Map<string, number[]>();
  edges.forEach((e, i) => {
    // Large topic pairs can span many columns. Combining their unrelated
    // corridors recreates the full-store visibility graph; keep each original
    // connector's obstacle set bounded instead.
    const endpoints = batching === "connector" || nodes.length > 500 ? [e.source, e.target] : [topic(e.source), topic(e.target)];
    // Shared corridors already deduplicate parallel geometry. Do not nudge an
    // opposing corridor against its reverse in the same visibility graph.
    const key = JSON.stringify(batching === "connector" ? endpoints : endpoints.sort());
    if (!batches.has(key)) batches.set(key, []);
    batches.get(key)!.push(i);
  });
  // 전체 노드가 적어도 한 Topic 쌍에 끝점이 몰리면 가시성 그래프가 커진다.
  // 평행 관계는 같은 끝점 묶음에 남기고, 서로 다른 연결은 따로 계산한다.
  const bounded: number[][] = [];
  for (const indices of batches.values()) {
    const endpointCount = new Set(indices.flatMap(i => [edges[i].source, edges[i].target])).size;
    if (endpointCount <= 48 && indices.length <= 64) { bounded.push(indices); continue; }
    const pairs = new Map<string, number[]>();
    for (const i of indices) {
      const key = JSON.stringify([edges[i].source, edges[i].target].sort());
      if (!pairs.has(key)) pairs.set(key, []); pairs.get(key)!.push(i);
    }
    bounded.push(...pairs.values());
  }
  const bounds = nodes.filter(n => (n as RoutingNode).routingObstacle !== false).map(n => { const r = rectangleOf(n); return { n, left: r.left - 4, right: r.right + 4,
    top: r.top - 4, bottom: r.bottom + 4 }; });
  // 모든 장애물을 색인한다. 각 직각 구간과 겹치는 칸만 조회하며 멀리 우회한 선도 검사한다.
  const cells = new Map<string, typeof bounds>(), cellSize = 500;
  for (const b of bounds) for (let x = Math.floor(b.left / cellSize); x <= Math.floor(b.right / cellSize); x++)
    for (let y = Math.floor(b.top / cellSize); y <= Math.floor(b.bottom / cellSize); y++) {
      const key = `${x}:${y}`; if (!cells.has(key)) cells.set(key, []); cells.get(key)!.push(b);
    }
  const result: GraphEdge[] = new Array(edges.length);
  for (const indices of bounded) {
    const batch = indices.map(i => edges[i]);
    const endpoints = new Set(batch.flatMap(e => [e.source, e.target]));
    const included = new Set(endpoints);
    while (true) {
      const routed = routeBatch(Avoid, [...included].map(id => map.get(id)!), batch);
      const missed = new Set<string>();
      for (const e of routed) {
        const ps = e.route!.points;
        for (let i = 1; i < ps.length; i++) {
          const a = ps[i - 1], c = ps[i];
          const x0 = Math.min(a.x, c.x) * UNIT, x1 = Math.max(a.x, c.x) * UNIT;
          const y0 = Math.min(a.y, c.y) * UNIT, y1 = Math.max(a.y, c.y) * UNIT;
          const candidates = new Set<(typeof bounds)[number]>();
          for (let x = Math.floor(x0 / cellSize); x <= Math.floor(x1 / cellSize); x++)
            for (let y = Math.floor(y0 / cellSize); y <= Math.floor(y1 / cellSize); y++)
              for (const b of cells.get(`${x}:${y}`) || []) if (!included.has(b.n.id)) candidates.add(b);
          for (const b of candidates) if (x1 > b.left + 1e-6 && x0 < b.right - 1e-6 && y1 > b.top + 1e-6 && y0 < b.bottom - 1e-6)
            missed.add(b.n.id);
        }
      }
      if (!missed.size) { indices.forEach((index, i) => { result[index] = routed[i]; }); break; }
      for (const id of missed) included.add(id);
    }
  }
  return result;
}
