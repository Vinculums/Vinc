/** One source-to-target path, as the layout returns it. Coordinates use the
 * same graph units as GraphNode; sections never become topology nodes. */
export type RoutePoint = { x: number; y: number; z: number };
export type ViewRoute = { points: RoutePoint[]; points2d?: RoutePoint[];
  engine: "elk" | "libavoid" | "spatial"; engine2d?: "elk" | "libavoid" };

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

export const routeLength = (points: RoutePoint[]) => points.slice(1).reduce((sum, b, i) => {
  const a = points[i]; return sum + Math.hypot(b.x - a.x, b.y - a.y, b.z - a.z);
}, 0);
