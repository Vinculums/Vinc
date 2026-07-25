/** Atlas view preferences (DEC D31 — spec vinc.d.atlas-visual-language §1.5).
 *
 *  Two-layer policy: these are the OPEN layer only ("how strongly to show") —
 *  view dimension, size scale, label density, arrowheads, spread. The CLOSED
 *  layer (type→shape, point color=focus, monochrome ink) is owned by the spec
 *  and is deliberately not user-configurable. Persisted per machine via
 *  localStorage; no sidecar contract involved (coords stay D17 canonical). */

// rank(D50) — 전역 위상 순서를 좌표에 인코딩(x=랭크). 다른 프리셋과 달리 도메인
// 클러스터를 만들지 않는다. 2D 관점에서 "흐름으로 읽히는 지도"가 되는 프리셋.
export type AtlasLayout = "radial" | "circle" | "tree" | "organic" | "rank" | "dendro";
export const LAYOUTS: AtlasLayout[] = ["radial", "circle", "tree", "organic", "rank", "dendro"];

// D53 — `flow` 관점 폐기(발주자 지시 2026-07-26). 노드 단위로 훑는 피로가 크고,
// 2D 의 `rank` 프리셋이 같은 위상 순서를 더 넓은 맥락과 함께 보여준다.
export type AtlasView = "3d" | "2d" | "timeline" | "totals";
export const VIEWS: AtlasView[] = ["3d", "2d", "timeline", "totals"];
export const isPlanar = (v: AtlasView) => v === "timeline" || v === "totals";

export type AtlasPrefs = {
  view: AtlasView;             // D43 — 관점 단일화(차원 토글을 흡수). 스코프는 trail 이 쥔다
  size: "links" | "uniform";   // node radius: √degree vs uniform
  labels: "hover" | "none";
  arrows: boolean;             // directional arrowheads (prerequisite/derives/inherits)
  spread: number;              // layout spread multiplier (0.6–1.6)
  unlinked: "show" | "hide";   // orphan nodes (degree 0) — D32 orphan minimization
  layout: AtlasLayout;         // D34 preset — coords stay sidecar-computed (D17)
  timelineDocs: boolean;       // Timeline — 문서(ABOUT/ATTACHES_TO) 아크 포함
};
// D47 — `totalsGroup`("주제별/출처별" 묶음 토글)은 폐기했다. 합계가 3열 생키가
// 되면서 출처와 상태를 동시에 보여주므로 고를 것이 없다. 저장된 구 값은 아래
// loadPrefs 가 그냥 읽지 않는다(마이그레이션 불필요 — 남은 키는 무해).

const KEY = "vinc.atlas.prefs";
const DEFAULTS: AtlasPrefs = {
  view: "3d", size: "links", labels: "hover", arrows: true, spread: 1,
  unlinked: "show", layout: "radial",
  timelineDocs: true,
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
      layout: LAYOUTS.includes(p.layout) ? p.layout : "radial",
      timelineDocs: p.timelineDocs !== false,
    };
  } catch {
    return { ...DEFAULTS };
  }
}

export function savePrefs(p: AtlasPrefs) {
  localStorage.setItem(KEY, JSON.stringify(p));
}
