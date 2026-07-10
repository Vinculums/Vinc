/** Atlas view preferences (DEC D31 — spec vinc.d.atlas-visual-language §1.5).
 *
 *  Two-layer policy: these are the OPEN layer only ("how strongly to show") —
 *  view dimension, size scale, label density, arrowheads, spread. The CLOSED
 *  layer (type→shape, point color=focus, monochrome ink) is owned by the spec
 *  and is deliberately not user-configurable. Persisted per machine via
 *  localStorage; no sidecar contract involved (coords stay D17 canonical). */

export type AtlasPrefs = {
  view: "3d" | "2d";           // 2d = same renderer, z flattened + locked camera
  size: "links" | "uniform";   // node radius: √degree vs uniform
  labels: "hover" | "none";
  arrows: boolean;             // directional arrowheads (prerequisite/derives/inherits)
  spread: number;              // layout spread multiplier (0.6–1.6)
  unlinked: "show" | "hide";   // orphan nodes (degree 0) — D32 orphan minimization
};

const KEY = "vinc.atlas.prefs";
const DEFAULTS: AtlasPrefs = {
  view: "3d", size: "links", labels: "hover", arrows: true, spread: 1, unlinked: "show",
};

export function loadPrefs(): AtlasPrefs {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return { ...DEFAULTS };
    const p = JSON.parse(raw);
    return {
      view: p.view === "2d" ? "2d" : "3d",
      size: p.size === "uniform" ? "uniform" : "links",
      labels: p.labels === "none" ? "none" : "hover",
      arrows: p.arrows !== false,
      spread: Math.min(1.6, Math.max(0.6, Number(p.spread) || 1)),
      unlinked: p.unlinked === "hide" ? "hide" : "show",
    };
  } catch {
    return { ...DEFAULTS };
  }
}

export function savePrefs(p: AtlasPrefs) {
  localStorage.setItem(KEY, JSON.stringify(p));
}
