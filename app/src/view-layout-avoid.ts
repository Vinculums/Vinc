import type { GraphEdge, GraphNode } from "./api";
import { simplifyRoute, type RoutePoint } from "./view-route";

export type RoutingNode = GraphNode & { routingBox?: { left: number; right: number; top: number; bottom: number } };
const rectangleOf = (n: RoutingNode) => n.routingBox || { left: n.x! * 1000 - 110, right: n.x! * 1000 + 110,
  top: n.y! * 1000 - 24, bottom: n.y! * 1000 + 66 };

/** The WASM binding has incomplete type declarations. Keep that boundary here.
 * The router owns shapes, pins, and connectors; temporary value objects do not. */
function routeBatch(Avoid: any, nodes: GraphNode[], edges: GraphEdge[]): GraphEdge[] {
  if (!edges.length) return [];
  const router = new Avoid.Router(Avoid.OrthogonalRouting);
  const temporaries: any[] = [], shapes = new Map<string, any>();
  const loops = new Set(edges.filter(e => e.source === e.target).map(e => e.source));
  const own = (v: any) => { temporaries.push(v); return v; };
  const map = new Map(nodes.map(n => [n.id, n]));
  const raw = (n: GraphNode): RoutePoint => ({ x: n.x! * 1000, y: n.y! * 1000, z: n.z! * 1000 });
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
        { x: 220, y: 24, dir: Avoid.ConnDirRight },
        { x: 0, y: 24, dir: Avoid.ConnDirLeft },
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
        .map(p => ({ x: p.x / 1000, y: p.y / 1000, z: p.z / 1000 })) } };
    });
  } finally {
    Avoid.destroy(router);
    for (const value of temporaries.reverse()) Avoid.destroy(value);
  }
}

/** A full-store visibility graph grows even when only two topics are linked.
 * Route each topic pair from its endpoint shapes, then check every mark.
 * Any obstacle reached by a detour is added before recomputing the whole pair. */
export function routeAcrossTopics(Avoid: any, nodes: GraphNode[], edges: GraphEdge[]): GraphEdge[] {
  if (!edges.length) return [];
  const map = new Map(nodes.map(n => [n.id, n]));
  const topic = (id: string) => { const n = map.get(id)!; return n.kind === "domain" ? n.id.slice(7) : n.domain || ""; };
  const batches = new Map<string, number[]>();
  edges.forEach((e, i) => {
    // Large topic pairs can span many columns. Combining their unrelated
    // corridors recreates the full-store visibility graph; keep each original
    // connector's obstacle set bounded instead.
    const key = JSON.stringify((nodes.length > 500 ? [e.source, e.target] : [topic(e.source), topic(e.target)]).sort());
    if (!batches.has(key)) batches.set(key, []);
    batches.get(key)!.push(i);
  });
  const bounds = nodes.map(n => { const r = rectangleOf(n); return { n, left: r.left - 4, right: r.right + 4,
    top: r.top - 4, bottom: r.bottom + 4 }; });
  // 모든 장애물을 색인한다. 각 직각 구간과 겹치는 칸만 조회하며 멀리 우회한 선도 검사한다.
  const cells = new Map<string, typeof bounds>(), cellSize = 500;
  for (const b of bounds) for (let x = Math.floor(b.left / cellSize); x <= Math.floor(b.right / cellSize); x++)
    for (let y = Math.floor(b.top / cellSize); y <= Math.floor(b.bottom / cellSize); y++) {
      const key = `${x}:${y}`; if (!cells.has(key)) cells.set(key, []); cells.get(key)!.push(b);
    }
  const result: GraphEdge[] = new Array(edges.length);
  for (const indices of batches.values()) {
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
          const x0 = Math.min(a.x, c.x) * 1000, x1 = Math.max(a.x, c.x) * 1000;
          const y0 = Math.min(a.y, c.y) * 1000, y1 = Math.max(a.y, c.y) * 1000;
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
