/** Atlas view preferences (DEC D31 — spec vinc.d.atlas-visual-language §1.5).
 *
 *  Two-layer policy: these are the OPEN layer only ("how strongly to show") —
 *  view dimension, size scale, label density, arrowheads, spread. The CLOSED
 *  layer (type→shape, point color=focus, monochrome ink) is owned by the spec
 *  and is deliberately not user-configurable. Persisted per machine via
 *  localStorage; no sidecar contract involved (coords stay D17 canonical). */

// 프리셋은 **2종뿐**이다(발주자 지시 2026-08-02 — D34 개정, 6종에서 축소).
//   rank  (D50) — 전역 위상 순서를 좌표에 인코딩(x=랭크). 계보가 있는 데이터를
//                 "무엇이 무엇보다 먼저인가"로 읽는다. 도메인 클러스터를 만들지 않는다.
//   hub         — 연결이 많은 노드가 가운데, 적을수록 바깥으로 밀려나는 동심 배치.
//                 "무엇이 중심인가"를 한눈에 준다. 구 radial/circle/tree/organic/dendro
//                 를 대체한다(옛 이름은 loadPrefs 가 hub 로 접는다).
// 둘을 남긴 기준: 하나는 **순서**를, 하나는 **중심성**을 말한다. 나머지는 그 둘의
// 변주라 고를 이유가 없었다.
export type AtlasLayout = "hub" | "rank";
export const LAYOUTS: AtlasLayout[] = ["hub", "rank"];
/** 폐기된 프리셋 이름 → 대체. 저장된 값과 딥링크가 조용히 깨지지 않게 접어 받는다. */
const LAYOUT_ALIAS: Record<string, AtlasLayout> = {
  radial: "hub", circle: "hub", tree: "hub", organic: "hub", dendro: "hub", shell: "hub",
};
export const normalizeLayout = (v: unknown): AtlasLayout =>
  LAYOUTS.includes(v as AtlasLayout) ? v as AtlasLayout
  : LAYOUT_ALIAS[String(v)] || "hub";

// D53 — `flow` 관점 폐기(발주자 지시 2026-07-26). 노드 단위로 훑는 피로가 크고,
// 2D 의 `rank` 프리셋이 같은 위상 순서를 더 넓은 맥락과 함께 보여준다.
export type AtlasView = "3d" | "2d" | "timeline" | "totals";
export const VIEWS: AtlasView[] = ["3d", "2d", "timeline", "totals"];
export const isPlanar = (v: AtlasView) => v === "timeline" || v === "totals";

/** 연결선 갈래(D74) — **범례가 곧 필터**다(발주자 지시 2026-08-02).
 *
 *  갈래는 렌더러가 이미 쓰던 우선순위 사슬을 그대로 이름 붙인 것이다
 *  (방향 → 다리 → 주제교차 → 나머지). 한 엣지는 정확히 한 갈래에 속하므로
 *  범례 항목 · 필터 스위치 · 실제 그려지는 선이 **같은 어휘 하나**를 쓴다.
 *  갈래를 새로 만들거나 순서를 바꾸면 세 곳이 함께 움직여야 한다 — 그래서
 *  판정 함수(`edgeKindOf`)를 여기 한 곳에 둔다. */
export type EdgeKind = "directed" | "bridge" | "cross" | "related";
export const EDGE_KINDS: EdgeKind[] = ["directed", "related", "cross", "bridge"];
/** 화살촉이 붙는 방향 3종 — atlas2d/atlas-planar 의 DIRECTED 와 같은 정의. */
export const DIRECTED_RELS = ["PREREQUISITE_OF", "DERIVES_FROM", "INHERITS_FROM"];
export function edgeKindOf(e: { type: string; cross_domain?: boolean }): EdgeKind {
  if (DIRECTED_RELS.includes(e.type)) return "directed";
  if (e.type === "CONNECTS") return "bridge";
  if (e.cross_domain) return "cross";
  return "related";
}

export type AtlasPrefs = {
  view: AtlasView;             // D43 — 관점 단일화(차원 토글을 흡수). 스코프는 trail 이 쥔다
  size: "links" | "uniform";   // node radius: √degree vs uniform
  // 아래 둘은 **인코딩이 아니라 배율**이다(발주자 지시 2026-08-02).
  //   nodeScale : size 가 정한 반지름에 곱한다. 무엇이 크고 작은지는 안 바꾸고 전체를 키운다.
  //   edgeScale : 엣지 두께 배율. ⚠ D33 이 "농도=가중치, 굵기 개정"으로 정해 뒀으므로
  //     가중치 표현은 **여전히 농도**다. 이 값은 전역 배율일 뿐 가중치를 굵기로
  //     되돌리는 것이 아니다(그 구분이 무너지면 D33 이 무의미해진다).
  nodeScale: number;           // 0.5–2.5
  edgeScale: number;           // 0.5–3
  labels: "hover" | "none";
  arrows: boolean;             // directional arrowheads (prerequisite/derives/inherits)
  spread: number;              // layout spread multiplier (0.6–1.6)
  unlinked: "show" | "hide";   // orphan nodes (degree 0) — D32 orphan minimization
  layout: AtlasLayout;         // D34 preset — coords stay sidecar-computed (D17)
  timelineDocs: boolean;       // Timeline — 문서(ABOUT/ATTACHES_TO) 아크 포함
  railW: number;               // D70 — 우측 문서 패널 폭(rem). 끌어서 조절, 기기별 지속
  // D72 — 타임라인 시간축. `recorded` = 기록 시각(기본), `content` = 내용상 시점.
  // 관점(view)이 아니라 그 관점 **안의 옵션**이다: 같은 것을 다른 시계로 볼 뿐이다.
  timelineBasis: TimeBasis;
  /** D74 — 지금 그리는 연결선 갈래. 범례 항목을 눌러 끈다. 빈 배열이 아니라
   *  **없는 갈래 목록**으로 저장한다: 나중에 갈래가 늘어도 저장된 값이 새 갈래를
   *  조용히 숨기지 않는다(모르는 갈래는 켜짐이 기본). */
  edgeOff: EdgeKind[];
  /** D74 — 타임라인 원인·결과 겹치기. 방향 관계를 마디 사이 곡선으로 잇는다. */
  timelineLineage: boolean;
};
export type TimeBasis = "recorded" | "content";
export const TIME_BASES: TimeBasis[] = ["recorded", "content"];
export const RAIL_MIN = 16;    // rem — 원문 한 줄이 읽히는 하한
export const RAIL_MAX = 36;    // rem — 지도가 남는 상한(패널이 화면을 삼키지 않게)
// D47 — `totalsGroup`("주제별/출처별" 묶음 토글)은 폐기했다. 합계가 3열 생키가
// 되면서 출처와 상태를 동시에 보여주므로 고를 것이 없다. 저장된 구 값은 아래
// loadPrefs 가 그냥 읽지 않는다(마이그레이션 불필요 — 남은 키는 무해).

const KEY = "vinc.atlas.prefs";
const DEFAULTS: AtlasPrefs = {
  view: "3d", size: "links", nodeScale: 1, edgeScale: 1,
  labels: "hover", arrows: true, spread: 1,
  unlinked: "show", layout: "hub",
  timelineDocs: true,
  railW: 20,
  timelineBasis: "recorded",
  edgeOff: [],
  timelineLineage: false,
};
const clamp = (v: unknown, lo: number, hi: number, dflt: number) =>
  Math.min(hi, Math.max(lo, Number(v) || dflt));
export const NODE_SCALE = { min: 0.5, max: 2.5, step: 0.1 };
export const EDGE_SCALE = { min: 0.5, max: 3, step: 0.1 };

export function loadPrefs(): AtlasPrefs {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return { ...DEFAULTS };
    const p = JSON.parse(raw);
    return {
      view: VIEWS.includes(p.view) ? p.view : "3d",
      size: p.size === "uniform" ? "uniform" : "links",
      nodeScale: clamp(p.nodeScale, NODE_SCALE.min, NODE_SCALE.max, 1),
      edgeScale: clamp(p.edgeScale, EDGE_SCALE.min, EDGE_SCALE.max, 1),
      labels: p.labels === "none" ? "none" : "hover",
      arrows: p.arrows !== false,
      spread: Math.min(1.6, Math.max(0.6, Number(p.spread) || 1)),
      unlinked: p.unlinked === "hide" ? "hide" : "show",
      // 폐기된 프리셋(radial 등)이 저장돼 있어도 조용히 hub 로 접는다.
      layout: normalizeLayout(p.layout),
      timelineDocs: p.timelineDocs !== false,
      railW: Math.min(RAIL_MAX, Math.max(RAIL_MIN, Number(p.railW) || DEFAULTS.railW)),
      timelineBasis: p.timelineBasis === "content" ? "content" : "recorded",
      // 모르는 이름은 버린다 — 갈래가 폐기돼도 저장값이 유령 스위치를 남기지 않게.
      edgeOff: Array.isArray(p.edgeOff)
        ? p.edgeOff.filter((k: unknown) => EDGE_KINDS.includes(k as EdgeKind)) : [],
      timelineLineage: p.timelineLineage === true,
    };
  } catch {
    return { ...DEFAULTS };
  }
}

export function savePrefs(p: AtlasPrefs) {
  localStorage.setItem(KEY, JSON.stringify(p));
}
