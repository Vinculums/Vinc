/** One source-to-target path, shared by the two renderers. Coordinates use the
 * same graph units as GraphNode; sections never become topology nodes. */
export type RoutePoint = { x: number; y: number; z: number };
export type ViewRoute = { points: RoutePoint[]; engine: "elk" | "libavoid" };

export function simplifyRoute(points: RoutePoint[]): RoutePoint[] {
  const out: RoutePoint[] = [];
  for (const p of points) {
    const b = out.at(-1);
    if (b && p.x === b.x && p.y === b.y && p.z === b.z) continue;
    const a = out.at(-2);
    if (a && b) {
      const u = [b.x - a.x, b.y - a.y, b.z - a.z];
      const v = [p.x - b.x, p.y - b.y, p.z - b.z];
      if (u.every((n, i) => n === 0 || v[i] !== 0) &&
          u.filter(n => n !== 0).length === 1 && v.filter(n => n !== 0).length === 1 &&
          u.some((n, i) => n * v[i] > 0)) out.pop();
    }
    out.push({ ...p });
  }
  return out;
}

/** Follow a drag without drawing a diagonal. Shared translations keep the
 * entire route; independently moved ends reconnect to its original sections. */
export function movedRoute(points: RoutePoint[], source: RoutePoint, target: RoutePoint): RoutePoint[] {
  if (points.length < 2) return [source, { ...source, x: target.x }, { ...target, z: source.z }, target];
  const a = points[0], b = points[points.length - 1];
  const d = { x: source.x - a.x, y: source.y - a.y, z: source.z - a.z };
  if (Math.abs(target.x - b.x - d.x) < 1e-8 && Math.abs(target.y - b.y - d.y) < 1e-8 &&
      Math.abs(target.z - b.z - d.z) < 1e-8)
    return points.map(p => ({ x: p.x + d.x, y: p.y + d.y, z: p.z + d.z }));
  const connect = (from: RoutePoint, to: RoutePoint, neighbor: RoutePoint) => {
    const axes = (Object.keys(from) as (keyof RoutePoint)[]).sort((u, v) =>
      Number(neighbor[v] !== to[v]) - Number(neighbor[u] !== to[u]));
    const path = [{ ...from }];
    for (const axis of axes) { const p = { ...path[path.length - 1], [axis]: to[axis] }; path.push(p); }
    return path;
  };
  return simplifyRoute([
    ...connect(source, a, points[1]), ...points,
    ...connect(target, b, points[points.length - 2]).reverse(),
  ]);
}

export function pointOnRoute(points: RoutePoint[], distance: number): RoutePoint {
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1], b = points[i];
    const length = Math.hypot(b.x - a.x, b.y - a.y, b.z - a.z);
    if (distance <= length) {
      const t = length ? Math.max(0, distance) / length : 0;
      return { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t, z: a.z + (b.z - a.z) * t };
    }
    distance -= length;
  }
  return points.at(-1) ?? { x: 0, y: 0, z: 0 };
}

export const routeLength = (points: RoutePoint[]) => points.slice(1).reduce((sum, b, i) => {
  const a = points[i]; return sum + Math.hypot(b.x - a.x, b.y - a.y, b.z - a.z);
}, 0);
