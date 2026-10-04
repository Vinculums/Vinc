/** Screen-space label thinning. Input order is degree/id priority. */
export type LabelBox = { id: string; left: number; right: number; top: number; bottom: number };
export const SMALL_GRAPH_LABEL_LIMIT = 50;

export function nonOverlappingLabels(boxes: LabelBox[], gap = 2): Set<string> {
  const kept: LabelBox[] = [];
  for (const box of boxes) {
    if (![box.left, box.right, box.top, box.bottom].every(Number.isFinite)
        || box.right <= box.left || box.bottom <= box.top) continue;
    if (kept.some(other => box.left < other.right + gap && box.right + gap > other.left
        && box.top < other.bottom + gap && box.bottom + gap > other.top)) continue;
    kept.push(box);
  }
  return new Set(kept.map(box => box.id));
}
