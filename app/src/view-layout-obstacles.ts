import type { GraphNode } from "./api";
import type { RoutePoint } from "./view-route";

const AXES = ["x", "y", "z"] as const;
const STEP = .33;

/** 긴 경로의 빈 cell을 순회하지 않고 해당 축의 점유 cell만 조회한다.
 * 기존 반올림 범위와 정확한 간격 판정은 유지한다. */
export function spatialClearance(nodes: GraphNode[]) {
  const cell = (value: number) => Math.round(value / STEP);
  const occupied = new Set<string>();
  const lines = AXES.map(() => new Map<string, { cell: number; node: GraphNode }[]>());
  for (const node of nodes) {
    const cells = AXES.map(axis => cell(node[axis]!));
    const key = cells.join(",");
    if (occupied.has(key)) throw new Error("Spatial View mark centres overlap");
    occupied.add(key);
    AXES.forEach((_, i) => {
      const key = cells.filter((_, j) => j !== i).join(",");
      let entries = lines[i].get(key);
      if (!entries) lines[i].set(key, entries = []);
      entries.push({ cell: cells[i], node });
    });
  }
  for (const line of lines) for (const entries of line.values()) entries.sort((a, b) => a.cell - b.cell);
  return (points: RoutePoint[], source: string, target: string) => {
    for (let i = 1; i < points.length; i++) {
      const a = points[i - 1], b = points[i];
      const axis = AXES.findIndex(axis => Math.abs(a[axis] - b[axis]) > 1e-9);
      if (axis < 0) continue;
      const along = AXES[axis], [u, v] = AXES.filter((_, j) => j !== axis);
      const entries = lines[axis].get(`${cell(a[u])},${cell(a[v])}`);
      if (!entries) continue;
      const first = Math.min(cell(a[along]), cell(b[along]));
      const last = Math.max(cell(a[along]), cell(b[along]));
      let low = 0, high = entries.length;
      while (low < high) {
        const middle = (low + high) >>> 1;
        if (entries[middle].cell < first) low = middle + 1; else high = middle;
      }
      for (let j = low; j < entries.length && entries[j].cell <= last; j++) {
        const n = entries[j].node;
        if (n.id === source || n.id === target) continue;
        if (Math.abs(a[u] - n[u]!) < .12 && Math.abs(a[v] - n[v]!) < .12 &&
          Math.max(a[along], b[along]) > n[along]! - .12 &&
          Math.min(a[along], b[along]) < n[along]! + .12) return false;
      }
    }
    return true;
  };
}
