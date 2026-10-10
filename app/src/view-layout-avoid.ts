import type { GraphEdge, GraphNode } from "./api";
import { simplifyRoute, type RoutePoint } from "./view-route";

/** The WASM binding has incomplete type declarations. Keep that boundary here.
 * The router owns shapes, pins, and connectors; temporary value objects do not. */
export function routeAcrossTopics(Avoid: any, nodes: GraphNode[], edges: GraphEdge[]): GraphEdge[] {
  if (!edges.length) return [];
  const router = new Avoid.Router(Avoid.OrthogonalRouting);
  const temporaries: any[] = [], shapes = new Map<string, { shape: any; ports: any[] }>();
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
        { id: 1, x: n.x + 110, y: n.y, px: 1, py: 24 / 90, dir: Avoid.ConnDirRight },
        { id: 2, x: n.x - 110, y: n.y, px: 0, py: 24 / 90, dir: Avoid.ConnDirLeft },
        { id: 3, x: n.x, y: n.y - 24, px: .5, py: 0, dir: Avoid.ConnDirUp },
      ];
      for (const p of ports) new Avoid.ShapeConnectionPin(shape, p.id, p.px, p.py, true, 0, p.dir).setExclusive(false);
      shapes.set(node.id, { shape, ports });
    }
    const connections = edges.map(edge => {
      const a = shapes.get(edge.source)!, b = shapes.get(edge.target)!;
      let best: { pa: any; pb: any; length: number } | undefined;
      for (const pa of a.ports) for (const pb of b.ports) {
        if (edge.source === edge.target && pa.id === pb.id) continue;
        const length = Math.abs(pa.x - pb.x) + Math.abs(pa.y - pb.y);
        if (!best || length < best.length) best = { pa, pb, length };
      }
      const connection = new Avoid.ConnRef(router,
        own(new Avoid.ConnEnd(a.shape, best!.pa.id)), own(new Avoid.ConnEnd(b.shape, best!.pb.id)));
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
      // Change depth at the target port, keeping every segment axis aligned.
      points.push({ ...points[points.length - 1], z: b.z }, b);
      return { ...edge, route: { engine: "libavoid" as const, points: simplifyRoute(points)
        .map(p => ({ x: p.x / 1000, y: p.y / 1000, z: p.z / 1000 })) } };
    });
  } finally {
    Avoid.destroy(router);
    for (const value of temporaries.reverse()) Avoid.destroy(value);
  }
}
