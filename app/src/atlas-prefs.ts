/** Atlas view preferences (DEC D31 — spec vinc.d.atlas-visual-language §1.5).
 *
 *  Two-layer policy: these are the OPEN layer only ("how strongly to show") —
 *  view dimension, size scale, label density, arrowheads, spread. The CLOSED
 *  layer (type→shape, point color=focus, monochrome ink) is owned by the spec
 *  and is deliberately not user-configurable. Persisted per machine via
 *  localStorage; no sidecar contract involved (coords stay D17 canonical). */

export type AtlasLayout = "shell" | "radial" | "circle" | "tree" | "organic";
export const LAYOUTS: AtlasLayout[] = ["shell", "radial", "circle", "tree", "organic"];

export type AtlasView = "3d" | "2d" | "flow" | "timeline" | "totals";
export const VIEWS: AtlasView[] = ["3d", "2d", "flow", "timeline", "totals"];
export const isPlanar = (v: AtlasView) => v === "flow" || v === "timeline" || v === "totals";

export type AtlasPrefs = {
  view: AtlasView;             // D43 — 관점 단일화(차원 토글을 흡수). 스코프는 trail 이 쥔다
  size: "links" | "uniform";   // node radius: √degree vs uniform
  labels: "hover" | "none";
  arrows: boolean;             // directional arrowheads (prerequisite/derives/inherits)
  spread: number;              // layout spread multiplier (0.6–1.6)
  unlinked: "show" | "hide";   // orphan nodes (degree 0) — D32 orphan minimization
  layout: AtlasLayout;         // D34 preset — coords stay sidecar-computed (D17)
  flowDepth: 1 | 2 | 3;        // Flow — 이미 받아온 서브그래프의 클라이언트 순회 상한
  timelineDocs: boolean;       // Timeline — 문서(ABOUT/ATTACHES_TO) 아크 포함
  totalsGroup: "topic" | "origin";  // Totals — 묶음 기준
};

const KEY = "vinc.atlas.prefs";
const DEFAULTS: AtlasPrefs = {
  view: "3d", size: "links", labels: "hover", arrows: true, spread: 1,
  unlinked: "show", layout: "shell",
  flowDepth: 1, timelineDocs: true, totalsGroup: "topic",
};

export function loadPrefs(): AtlasPrefs {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return { ...DEFAULTS };
    const p = JSON.parse(raw);
    return {
      view: VIEWS.includes(p.view) ? p.view : "3d",
      size: p.size === "uniform" ? "uniform" : "links",
      labels: p.labels === "none" ? "none" : "hover",
      arrows: p.arrows !== false,
      spread: Math.min(1.6, Math.max(0.6, Number(p.spread) || 1)),
      unlinked: p.unlinked === "hide" ? "hide" : "show",
      layout: LAYOUTS.includes(p.layout) ? p.layout : "shell",
      flowDepth: p.flowDepth === 2 || p.flowDepth === 3 ? p.flowDepth : 1,
      timelineDocs: p.timelineDocs !== false,
      totalsGroup: p.totalsGroup === "origin" ? "origin" : "topic",
    };
  } catch {
    return { ...DEFAULTS };
  }
}

export function savePrefs(p: AtlasPrefs) {
  localStorage.setItem(KEY, JSON.stringify(p));
}
