import type { GraphNode, GraphEdge } from "./api";
import { routeLength, simplifyRoute, type RoutePoint } from "./view-route";

/** Remove clear detours after ELK/libavoid and shared Topic corridors have
 * composed the complete visible path. Never trade a longer line for fewer
 * turns, cross a mark/label, or erase a self relation. */
export function shortenPlanarRoutes(nodes: GraphNode[], edges: GraphEdge[]): void {
  const cells = new Map<string, { id: string; left: number; right: number; top: number; bottom: number }[]>();
  const size = .5;
  for (const n of nodes) {
    const box = { id: n.id, left: n.x! - .114, right: n.x! + .114, top: n.y! - .028, bottom: n.y! + .070 };
    for (let x = Math.floor(box.left / size); x <= Math.floor(box.right / size); x++)
      for (let y = Math.floor(box.top / size); y <= Math.floor(box.bottom / size); y++) {
        const key = `${x}:${y}`; if (!cells.has(key)) cells.set(key, []); cells.get(key)!.push(box);
      }
  }
  const clear = (points: RoutePoint[], source: string, target: string) => points.slice(1).every((b, i) => {
    const a = points[i], left = Math.min(a.x, b.x), right = Math.max(a.x, b.x);
    const top = Math.min(a.y, b.y), bottom = Math.max(a.y, b.y);
    for (let x = Math.floor(left / size); x <= Math.floor(right / size); x++)
      for (let y = Math.floor(top / size); y <= Math.floor(bottom / size); y++)
        for (const box of cells.get(`${x}:${y}`) ?? []) {
          if (box.id === source || box.id === target) continue;
          if (right > box.left + 1e-9 && left < box.right - 1e-9 &&
              bottom > box.top + 1e-9 && top < box.bottom - 1e-9) return false;
        }
    return true;
  });
  const cache = new Map<string, RoutePoint[]>();
  for (const edge of edges) {
    if (!edge.route || edge.source === edge.target) continue;
    const key = JSON.stringify([edge.source, edge.target].sort());
    const saved = cache.get(key);
    if (saved) { edge.route.points = edge.source <= edge.target ? saved : saved.slice().reverse(); continue; }
    let points = edge.route.points;
    for (let from = 0; from < points.length - 2; from++) {
      for (let to = points.length - 1; to > from + 1; to--) {
        to = Math.min(to, points.length - 1);
        if (to <= from + 1) break;
        const a = points[from], b = points[to], original = points.slice(from, to + 1);
        const candidates = [
          [a, { ...a, x: b.x }, { ...a, x: b.x, y: b.y }, b],
          [a, { ...a, y: b.y }, { ...a, x: b.x, y: b.y }, b],
        ].map(simplifyRoute).sort((a, b) => a.length - b.length);
        const shortcut = candidates.find(path => path.length <= original.length &&
          routeLength(path) <= routeLength(original) + 1e-8 && clear(path, edge.source, edge.target));
        if (shortcut) points = simplifyRoute([...points.slice(0, from), ...shortcut, ...points.slice(to + 1)]);
      }
    }
    edge.route.points = points;
    cache.set(key, edge.source <= edge.target ? points : points.slice().reverse());
  }
}
