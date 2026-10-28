import { isRootsLayout, type ViewPrefs } from "./view-prefs";

type PrefKey = keyof ViewPrefs;
const STYLE_KEYS = new Set<PrefKey>([
  "nodeScale", "edgeScale", "arrows", "labels", "size", "spread", "railW",
]);
const FILTER_KEYS = new Set<PrefKey>(["edgeOff"]);

/** Filter order has no meaning. A fresh array with the same choices is a no-op. */
export function changedViewPrefs(previous: ViewPrefs, next: Partial<ViewPrefs>): PrefKey[] {
  return (Object.keys(next) as PrefKey[]).filter(key => {
    const a = previous[key], b = next[key];
    if (FILTER_KEYS.has(key) && Array.isArray(a) && Array.isArray(b)) {
      const left = new Set(a), right = new Set(b);
      return left.size !== right.size || [...left].some(value => !right.has(value));
    }
    return a !== b;
  });
}

/** The controller owns time visibility; stale renderer snapshots must not turn
 * a later style adjustment into a complete scene rebuild. */
export function changedRendererPrefs(previous: ViewPrefs, next: ViewPrefs): PrefKey[] {
  return changedViewPrefs(previous, next).filter(key => key !== "when" && key !== "timeBasis");
}

export const canUpdateViewStyle = (keys: PrefKey[], liveLinks = false) =>
  keys.every(key => STYLE_KEYS.has(key) || liveLinks && key === "edges");

/** The panel inventory of the one 3D view. Only hub consumes cone geometry. */
export function viewControlRows(p: ViewPrefs): string[] {
  const rows = ["size", "layout", "nodeScale", "labels", "arrows", "unlinked",
    "spread", "edgeWidth", "edgeShow", "basis", "when"];
  if (p.layout === "hub") rows.splice(2, 0, "cone");
  rows.push("replay");
  return rows;
}

export function viewPreferenceWork(p: ViewPrefs, keys: PrefKey[],
  available: { map: boolean; current?: boolean }) {
  const serverLayout = !isRootsLayout(p.layout)
    && keys.some(key => key === "layout" || key === "cone");
  const relayout = serverLayout || keys.includes("layout")
    || (isRootsLayout(p.layout) && keys.includes("timeBasis"));
  let mode: "noop" | "time" | "style" | "remount" = "remount";
  if (!keys.length) mode = "noop";
  // A pending show owns a captured preference snapshot. Restart that generation
  // rather than updating the old scene and then losing the change at mount.
  else if (available.current === false) mode = "remount";
  else if (keys.every(key => key === "when" || key === "timeBasis")
    && (!keys.includes("timeBasis") || !isRootsLayout(p.layout))
    && available.map) mode = "time";
  else if (!relayout && available.map && canUpdateViewStyle(keys, true)) mode = "style";
  return { mode, serverLayout, relayout, reuseGraph: !serverLayout, keepCamera: !serverLayout,
    refreshPanel: !keys.every(key => key === "nodeScale" || key === "edgeScale" || key === "spread") };
}
