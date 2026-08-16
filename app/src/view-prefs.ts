/** View view preferences (DEC D31 — spec vinc.d.view-visual-language §1.5).
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
export type ViewLayout = "hub" | "rank";
export const LAYOUTS: ViewLayout[] = ["hub", "rank"];
/** 폐기된 프리셋 이름 → 대체. 저장된 값과 딥링크가 조용히 깨지지 않게 접어 받는다. */
const LAYOUT_ALIAS: Record<string, ViewLayout> = {
  radial: "hub", circle: "hub", tree: "hub", organic: "hub", dendro: "hub", shell: "hub",
};
export const normalizeLayout = (v: unknown): ViewLayout =>
  LAYOUTS.includes(v as ViewLayout) ? v as ViewLayout
  : LAYOUT_ALIAS[String(v)] || "hub";

// D53 — `flow` 관점 폐기(발주자 지시 2026-07-26). 노드 단위로 훑는 피로가 크고,
// 2D 의 `rank` 프리셋이 같은 위상 순서를 더 넓은 맥락과 함께 보여준다.
// Sankey(합계) 폐기 2026-08-14 — 리본 굵기로 양을 말하던 문법을 버리고, 개수는
// 타임라인이 **점의 개수**로 말한다(번역 없는 표현이라 스케일도 하한도 없다).
// 저장된 `totals` 와 딥링크 `/as/totals` 는 **마이그레이션이 필요 없다**: 아래
// loadPrefs 와 해시 파싱이 둘 다 `VIEWPOINTS.includes` 로 거르므로 조용히 기본값으로
// 접힌다(구 프레임 딥링크가 L0 로 접히는 것과 같은 규율).
export type Viewpoint = "3d" | "2d" | "timeline" | "bundle";
export const VIEWPOINTS: Viewpoint[] = ["3d", "2d", "timeline", "bundle"];
export const isPlanar = (v: Viewpoint) => v === "timeline" || v === "bundle";

/** 방사형 계층 번들(Sankey 대체) — **선이 묶이는 단**.
 *  1 = 도메인끼리 · 2 = 도메인/종류 · 3 = 노드끼리(전개).
 *  ⚠ 라벨은 어느 단에서도 **전량 남는다**(발주자 결정 2026-08-14): 컨트롤이 조절하는
 *  것은 목록이 아니라 선이다. 그래서 저밀도에서도 "무엇이 있는가"는 안 줄어든다. */
export type BundleDepth = 1 | 2 | 3;
export const BUNDLE_DEPTHS: BundleDepth[] = [1, 2, 3];

/** 번들의 선 필터 어휘 — `/graph` 가 실제로 돌려주는 관계 이름(대문자)이다.
 *  소문자 적재 어휘(`relates_to` 등)가 아니다: 읽기는 `label(r)` 을 투영한다.
 *
 *  ⚠ 위 `EdgeKind`(4갈래)와 **다른 층위**다. 3D·2D 는 갈래로 거르고 번들은 스키마
 *  타입으로 거른다(발주자 요구 "각 스키마 타입별로"). 갈래를 7종으로 늘리지 않은
 *  이유는 D74 의 규율을 지키기 위해서다 — 한 화면은 어휘 **하나만** 쓴다. 두 어휘가
 *  한 화면에 섞이면 범례와 실제 선이 어긋난다. */
export const BUNDLE_RELS = [
  "PREREQUISITE_OF", "DERIVES_FROM", "INHERITS_FROM",
  "RELATED_TO", "CONNECTS", "ABOUT", "ATTACHES_TO",
];

/** 연결선 갈래(D74) — **범례가 곧 필터**다(발주자 지시 2026-08-02).
 *
 *  갈래는 렌더러가 이미 쓰던 우선순위 사슬을 그대로 이름 붙인 것이다
 *  (방향 → 다리 → 주제교차 → 나머지). 한 엣지는 정확히 한 갈래에 속하므로
 *  범례 항목 · 필터 스위치 · 실제 그려지는 선이 **같은 어휘 하나**를 쓴다.
 *  갈래를 새로 만들거나 순서를 바꾸면 세 곳이 함께 움직여야 한다 — 그래서
 *  판정 함수(`edgeKindOf`)를 여기 한 곳에 둔다. */
export type EdgeKind = "directed" | "bridge" | "cross" | "related";
export const EDGE_KINDS: EdgeKind[] = ["directed", "related", "cross", "bridge"];
/** 화살촉이 붙는 방향 3종 — view2d/view-planar 의 DIRECTED 와 같은 정의. */
export const DIRECTED_RELS = ["PREREQUISITE_OF", "DERIVES_FROM", "INHERITS_FROM"];
export function edgeKindOf(e: { type: string; cross_domain?: boolean }): EdgeKind {
  if (DIRECTED_RELS.includes(e.type)) return "directed";
  if (e.type === "CONNECTS") return "bridge";
  if (e.cross_domain) return "cross";
  return "related";
}

/** 선을 **언제** 그리는가(2026-08-14 발주자 지시 — 2D 전용).
 *
 *  `always` 는 여태 그림 그대로(전량 상시), `focus` 는 짚은 노드에 걸린 선만 그리고
 *  나머지는 숨긴다. 헤어볼의 정체는 선 자체가 아니라 "관심 밖 선까지 늘 그려져
 *  있다"는 것이므로, 인코딩(화살촉·굵기·농도·갈래)은 **하나도 바꾸지 않고** 그려지는
 *  순간만 좁힌다. 방향은 그대로 화살촉이 말한다 — 한 번에 한 노드 분량만 뜰 뿐이다.
 *
 *  `particles` 는 **3D 전용**이다: 선을 지우고 그 궤적 위로 입자를 흘려 방향을
 *  움직임으로 말한다(구현·급소는 view3d.ts 상단).
 *
 *  `bring` 은 **양쪽 다** 된다: 선을 아예 그리지 않고, 짚으면 이웃이 초점 둘레로
 *  날아와 고리로 선다. 연결을 그림이 아니라 **움직임**으로 말한다. 3D 는 그 고리를
 *  카메라를 향한 평면에 세운다(월드 축에 세우면 각도에 따라 선으로 눌린다).
 *
 *  렌더러가 모르는 값을 받으면 그 렌더러의 기본 동작(`always`)으로 떨어진다 —
 *  관점을 옮겼다고 선이 조용히 사라지지 않게. */
export type EdgeShow = "always" | "focus" | "particles" | "bring";
/** 관점마다 고를 수 있는 것이 다르다 — 보이는 손잡이는 반드시 무언가를 해야 한다. */
export const edgeShowsFor = (view: Viewpoint): EdgeShow[] =>
  view === "3d" ? ["always", "particles", "bring"] : ["always", "focus", "bring"];

export type ViewPrefs = {
  view: Viewpoint;             // D43 — 관점 단일화(차원 토글을 흡수). 스코프는 trail 이 쥔다
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
  edges: EdgeShow;             // 2D — 선을 상시로 그릴지, 짚은 노드에서만 그릴지
  spread: number;              // layout spread multiplier (0.6–1.6)
  unlinked: "show" | "hide";   // orphan nodes (degree 0) — D32 orphan minimization
  layout: ViewLayout;         // D34 preset — coords stay sidecar-computed (D17)
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
  /** 짜임 — 선이 묶이는 단.
   *
   *  기본이 1(주제끼리)이었는데 **실제 데이터에서 거의 항상 빈 화면이 됐다**. 주제
   *  안쪽 연결은 계층 경로가 한 점이라 그릴 수 없어 버려지는데, 이 코퍼스의 연결은
   *  대부분 같은 주제 안쪽이다(실측 2026-08-16: 노드 204·연결 260 에서 단 1 은
   *  255개를 버리고 선 0개를 그렸다). 기본은 버리는 것이 없는 3(노드끼리)이다.
   *  대신 선이 많아져 밀도가 올라가는데, 그건 겹침으로 읽히는 정보다. */
  bundleDepth: BundleDepth;
  /** 번들 — 끈 관계 타입. `edgeOff` 와 같은 이유로 **끈 목록**으로 저장한다:
   *  나중에 타입이 늘어도 저장된 값이 새 타입을 조용히 숨기지 않는다. */
  bundleOff: string[];
  /** 주제 틴트 — 노드 잉크에 주제별 색조를 얹는다. **기본은 켬**(발주자 지시
   *  2026-08-16). 원래 끔이었는데, 그 기본이 실제 화면에서 뜻한 것은 "색이 없다"가
   *  아니라 **"이 기능이 있는 줄 모른다"** 였다. 토글은 옵션 패널 안쪽에 있고,
   *  Weave 에서 선이 전부 한 색으로 보이는 것을 고장으로 읽는 일이 실제로 있었다.
   *  꺼도 아무 정보가 사라지지 않는다는 성질은 그대로다 — 끄는 쪽이 언제나 안전하다.
   *
   *  이것이 열린 층에 있는 이유: 색은 원래 닫힌 층이고(팩을 받은 쪽이 색을 다시
   *  매핑하면 보낸 사람과 다른 그래프를 보는데 아무도 오류를 안 낸다), 그래서
   *  **뜻을 나르지 않는 색만** 사용자에게 준다. 틴트는 강조다: 꺼도 아무 정보가
   *  사라지지 않아야 한다. 주제는 이미 자리(hub 부챗살)와 lane 이 말하고 있고,
   *  틴트는 그 위에 얹는 색조일 뿐이다.
   *
   *  색상환 각도는 주제 문자열 **해시**라 주제가 자기 색을 계속 갖는다(다른 주제가
   *  화면에 들고 나도 안 바뀐다). 충돌은 가능하고 허용된다 — 뜻이 아니기 때문이다. */
  domainTint: boolean;
};
export type TimeBasis = "recorded" | "content";
export const TIME_BASES: TimeBasis[] = ["recorded", "content"];
export const RAIL_MIN = 16;    // rem — 원문 한 줄이 읽히는 하한
// 상한을 크게 둔다(발주자 지시 2026-08-05) — 문서 패널은 지도 **위에 겹치는** 글라스
// 이므로, 끝까지 끌어 화면을 다 덮는 것이 정당한 상태다(읽는 동안 지도를 잠시 덮는
// 것과 지도를 잃는 것은 다르다 — 접으면 그대로 돌아온다). 실제 한계는 CSS 가
// `min(var(--rail-size), 100%)` 로 잡으므로 여기서는 화면보다 큰 값만 막는다.
export const RAIL_MAX = 120;   // rem — 사실상 화면 폭(CSS 가 100% 로 클램프)
// D47 — `totalsGroup`("주제별/출처별" 묶음 토글)은 폐기했다. 합계가 3열 생키가
// 되면서 출처와 상태를 동시에 보여주므로 고를 것이 없다. 저장된 구 값은 아래
// loadPrefs 가 그냥 읽지 않는다(마이그레이션 불필요 — 남은 키는 무해).

const KEY = "vinc.view.prefs";
const DEFAULTS: ViewPrefs = {
  view: "3d", size: "links", nodeScale: 1, edgeScale: 1,
  labels: "hover", arrows: true, edges: "always", spread: 1,
  unlinked: "show", layout: "hub",
  timelineDocs: true,
  railW: 20,
  timelineBasis: "recorded",
  edgeOff: [],
  timelineLineage: false,
  bundleDepth: 3,
  bundleOff: [],
  domainTint: true,
};
const clamp = (v: unknown, lo: number, hi: number, dflt: number) =>
  Math.min(hi, Math.max(lo, Number(v) || dflt));
// step 이 곧 **손끝의 해상도**다(2026-08-05). 0.1 이던 동안에는 0.5~2.5 구간에 정지점이
// 20개뿐이라, 230px 짜리 트랙에서 11px 마다 한 칸씩 튀었다 — 끌면 계단처럼 움직인다.
// 0.02 면 100칸이라 손을 따라온다. 값의 의미는 배율이므로 잘게 나눠도 무해하다
// (표시는 소수 둘째 자리까지 — view.ts slider 의 fmt).
export const NODE_SCALE = { min: 0.5, max: 2.5, step: 0.02 };
export const EDGE_SCALE = { min: 0.5, max: 3, step: 0.02 };

export function loadPrefs(): ViewPrefs {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return { ...DEFAULTS };
    const p = JSON.parse(raw);
    return {
      view: VIEWPOINTS.includes(p.view) ? p.view : "3d",
      size: p.size === "uniform" ? "uniform" : "links",
      nodeScale: clamp(p.nodeScale, NODE_SCALE.min, NODE_SCALE.max, 1),
      edgeScale: clamp(p.edgeScale, EDGE_SCALE.min, EDGE_SCALE.max, 1),
      labels: p.labels === "none" ? "none" : "hover",
      arrows: p.arrows !== false,
      edges: ["focus", "particles", "bring"].includes(p.edges) ? p.edges : "always",
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
      // 저장값이 문자열로 돌아올 수 있어(구 버전·수기 편집) Number 로 접어 받는다.
      bundleDepth: BUNDLE_DEPTHS.includes(Number(p.bundleDepth) as BundleDepth)
        ? (Number(p.bundleDepth) as BundleDepth) : DEFAULTS.bundleDepth,
      bundleOff: Array.isArray(p.bundleOff)
        ? p.bundleOff.filter((k: unknown) => BUNDLE_RELS.includes(String(k))) : [],
      domainTint: p.domainTint === true,
    };
  } catch {
    return { ...DEFAULTS };
  }
}

export function savePrefs(p: ViewPrefs) {
  localStorage.setItem(KEY, JSON.stringify(p));
}
