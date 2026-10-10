import type { GraphEdge, GraphNode } from "./api";
import { simplifyRoute, type RoutePoint } from "./view-route";

/** The WASM binding has incomplete type declarations. Keep that boundary here.
 * The router owns shapes, pins, and connectors; temporary value objects do not. */
export function routeAcrossTopics(Avoid: any, nodes: GraphNode[], edges: GraphEdge[]): GraphEdge[] {
  if (!edges.length) return [];
  const router = new Avoid.Router(Avoid.OrthogonalRouting);
  const temporaries: any[] = [], shapes = new Map<string, any>();
  const loops = new Set(edges.filter(e => e.source === e.target).map(e => e.source));
  const own = (v: any) => { temporaries.push(v); return v; };
  const map = new Map(nodes.map(n => [n.id, n]));
  const raw = (n: GraphNode): RoutePoint => ({ x: n.x! * 1000, y: n.y! * 1000, z: n.z! * 1000 });
  try {
    for (const [key, value] of [["segmentPenalty", 120], ["crossingPenalty", 50],
      ["shapeBufferDistance", 4], ["idealNudgingDistance", 12], ["reverseDirectionPenalty", 80]] as const)
      router.setRoutingParameter(Avoid[key], value);
    router.setRoutingOption(Avoid.nudgeSharedPathsWithCommonEndPoint, true);
    for (const node of nodes) {
      const n = raw(node);
      const rectangle = own(new Avoid.Rectangle(own(new Avoid.Point(n.x - 110, n.y - 24)),
        own(new Avoid.Point(n.x + 110, n.y + 66))));
      const shape = new Avoid.ShapeRef(router, rectangle);
      const ports = [
        { x: 220, y: 24, dir: Avoid.ConnDirRight },
        { x: 0, y: 24, dir: Avoid.ConnDirLeft },
      ];
      // Route from the actual mark centre with all departure directions. This
      // makes bend cost include the complete visible path rather than choosing
      // a boundary port first and appending centre-to-port stubs afterwards.
      new Avoid.ShapeConnectionPin(shape, 1, 110, 24, false, 0, Avoid.ConnDirAll).setExclusive(false);
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
