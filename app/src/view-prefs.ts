/** View preferences (DEC D31 — as-built [[vinc.reference.view-controls]]).
 *
 *  Two-layer policy: these are the OPEN layer only ("how strongly to show") —
 *  view dimension, size scale, label density, arrowheads, spread. The CLOSED
 *  layer (type→shape, point color=focus, monochrome ink) is owned by the spec
 *  and is deliberately not user-configurable. Persisted per machine via
 *  localStorage; no sidecar contract involved (coords stay D17 canonical). */

// 배치는 둘이다.
//   hub           — `중심에서`. 서버가 준 좌표 그대로다: 연결이 많은 노드가 가운데,
//                   적을수록 바깥으로 밀려나는 동심 배치. "무엇이 중심인가"를 말한다.
//   temporalRoots — `시간순 가지`. 받은 그래프를 워커가 주제마다 날짜 순서의 가지로
//                   재배치한다(view-layout.ts). "무엇이 먼저인가"를 말한다.
// `순서대로`·`관계순 가지`는 뺐고, 그 저장값은 loadPrefs 의 옮겨 읽기가 `시간순 가지`로 접는다.
// 근거: [[decision:view-offers-two-layouts-by-centre-and-time-branches-2026-10-11]]
/** 배치는 시간순 가지 하나다. 3D 전체 보기를 인프라 배치 하나로 정하면서 `중심에서`(hub) 선택지를
 *  걷었다. 서버가 주는 중심에서 좌표는 가지 배치가 오기 전의 자리 잡기로만 쓴다(`serverLayoutFor`).
 *  근거: [[decision:the-graph-is-one-2d-compass-lens-2026-11-06]] */
type ViewLayout = "temporalRoots";
export const LAYOUTS: ViewLayout[] = ["temporalRoots"];
export const isRootsLayout = (layout: ViewLayout) => layout === "temporalRoots";
/** 서버는 `hub` 좌표만 받는다. 가지 배치는 받은 그래프를 워커에서 재배치한다. */
export const serverLayoutFor = (_layout: ViewLayout): "hub" => "hub";
/** 배치 이름은 하나뿐이라 저장된 무엇이든(`hub` 포함) 시간순 가지로 읽는다. */
export const normalizeLayout = (_v: unknown): ViewLayout => "temporalRoots";

/** 그래프 탭의 보기는 3D 전체 보기 하나다. 저장값이나 주소(`/as/<관점>`)에 남은 옛 관점
 *  (`2d`·`timeline`·`bundle`)은 `VIEWPOINTS.includes` 가 걸러 3D 로 접힌다. 주소 문법의
 *  `/as/` 칸은 MCP 서버의 파서와 나눠 쓰는 계약이라 그대로 둔다(`view-url.ts`).
 *  근거: [[decision:the-graph-is-one-2d-compass-lens-2026-11-06]] */
/** 그래프 보기는 나침반(`compass`) 하나다: 가운데가 지금 보는 것인 렌즈(view-compass.ts). 저장값의 다른 이름(`3d` 등)도 여기로 읽는다. */
export type Viewpoint = "compass";
export const VIEWPOINTS: Viewpoint[] = ["compass"];

/** 연결선 갈래(D74) — **범례가 곧 필터**다(발주자 지시 2026-08-02).
 *
 *  갈래는 렌더러가 이미 쓰던 우선순위 사슬을 그대로 이름 붙인 것이다
 *  (방향 → 다리 → 주제교차 → 나머지). 한 엣지는 정확히 한 갈래에 속하므로
 *  범례 항목 · 필터 스위치 · 실제 그려지는 선이 **같은 어휘 하나**를 쓴다.
 *  갈래를 새로 만들거나 순서를 바꾸면 세 곳이 함께 움직여야 한다 — 그래서
 *  판정 함수(`edgeKindOf`)를 여기 한 곳에 둔다. */
export type EdgeKind = "directed" | "bridge" | "cross" | "related";
export const EDGE_KINDS: EdgeKind[] = ["directed", "related", "cross", "bridge"];
/** 끝이 원천(조상)을 가리키는 관계 — "a 가 b 에서 나왔다". 시간순 배치는 이 둘만
 *  뒤집어 조상을 앞에 세운다(`view-layout.ts` 의 `ancestry`). */
export const ANCESTRY_RELS = ["DERIVES_FROM", "INHERITS_FROM"];
/** 화살촉이 붙는 방향 3종. `edgeKindOf` 가 읽는다. */
const DIRECTED_RELS = ["PREREQUISITE_OF", ...ANCESTRY_RELS];
export function edgeKindOf(e: { type: string; cross_domain?: boolean }): EdgeKind {
  if (DIRECTED_RELS.includes(e.type)) return "directed";
  if (e.type === "CONNECTS") return "bridge";
  if (e.cross_domain) return "cross";
  return "related";
}

/** 선을 **언제** 그리는가. 인코딩(화살촉·굵기·농도)은 하나도 바꾸지 않고 그려지는
 *  순간만 좁힌다 — 헤어볼의 정체는 선 자체가 아니라 관심 밖 선까지 늘 그려져 있다는
 *  것이다([[vinc.research.link-expression]]).
 *    none      노드만 — 선을 그리지 않는다. 관계는 짚거나 고를 때 점등·카드가 말한다
 *    always    전량 상시
 *    particles 선을 지우고 궤적 위로 입자를 흘린다
 *
 *  `bring`(Bring & Go: 짚으면 이웃이 날아와 고리로 서던 것)은 걷었다(사람 지시
 *  2026-09-26). 저장값에 남은 `bring` 은 가장 가까운 뜻인 `none` 으로 읽는다 — 둘 다
 *  "선을 그리지 않는다"가 출발점이다.
 *  근거: [[decision:bring-and-go-is-retired-and-nodes-only-is-the-default]]
 *
 *  저장값에 남은 `focus`(짚은 노드의 선만)도 `none` 으로 읽는다(loadPrefs). 무조건 `always`
 *  로 떨어뜨리면 "상시 전량을 그만두겠다"고 고른 사람이 정반대를 받는다. 정말 모르는 값만
 *  `always` 로 간다.
 *
 *  ⚠ `none` 의 등가물은 `particles` 가 아니다. 입자는 상시 애니메이션이라 F6 유휴 정지를
 *  못 받아, 아무도 안 만지는 화면이 계속 그려진다. */
export type EdgeShow = "none" | "always" | "particles";
export const EDGE_SHOWS: EdgeShow[] = ["none", "always", "particles"];

export type ViewPrefs = {
  view: Viewpoint;             // D43 — 관점 단일화(차원 토글을 흡수). 스코프는 trail 이 쥔다
  size: "links" | "uniform";   // node radius: √degree vs uniform
  // 아래 둘은 **인코딩이 아니라 배율**이다(발주자 지시 2026-08-02).
  //   nodeScale : size 가 정한 반지름에 곱한다. 무엇이 크고 작은지는 안 바꾸고 전체를 키운다.
  //   edgeScale : 엣지 두께 배율. ⚠ D33 이 "농도=가중치, 굵기 개정"으로 정해 뒀으므로
  //     가중치 표현은 **여전히 농도**다. 이 값은 전역 배율일 뿐 가중치를 굵기로
  //     되돌리는 것이 아니다(그 구분이 무너지면 D33 이 무의미해진다).
  nodeScale: number;           // 0.5–2.5
  /** 가지가 퍼지는 각도(hub · 3D). **서버가 읽는 두 번째 값**이다 — layout 과 함께
   *  좌표를 다시 받아야 하므로, 이것이 바뀌면 재조회가 필요하다(다른 손잡이는
   *  이미 받은 좌표의 표현만 바꾼다). 2D 는 부채꼴이라 이 값과 무관하다. */
  cone: number;                // 0.3–1.4
  edgeScale: number;           // 0.5–3
  labels: "hover" | "none";
  arrows: boolean;             // directional arrowheads (prerequisite/derives/inherits)
  edges: EdgeShow;             // 선을 상시로 그릴지, 입자로 흘릴지, 안 그릴지
  spread: number;              // layout spread multiplier (0.6–1.6)
  unlinked: "show" | "hide";   // orphan nodes (degree 0) — D32 orphan minimization
  layout: ViewLayout;         // 기존 좌표는 서버, 추가 가지 배치는 워커가 계산한다.
  railW: number;               // D70 — 우측 문서 패널 폭(rem). 끌어서 조절, 기기별 지속
  // D72 — 시간축. `recorded` = 기록 시각(기본), `content` = 내용상 시점. 같은 것을 다른
  // 시계로 볼 뿐이다. 시간 커서(`when`)와 배치(시간순 가지)가 이 값을 읽는다.
  timeBasis: TimeBasis;
  /** **시간 커서**(2026-08-17 발주자 결정) — 0..1, 1 = 끔(전부 보임).
   *
   *  값이 분수인 이유는 시각이 고르게 퍼져 있지 않아서다 — 절대 날짜를 자르면 아무 일도
   *  안 일어나는 구간이 길게 생긴다. 실제 값들의 **분위수**로 읽으면 어느 위치에서도
   *  무언가가 갈린다(`timeCutoff`). */
  when: number;
  /** D74 — 지금 그리는 연결선 갈래. 범례 항목을 눌러 끈다. 빈 배열이 아니라
   *  **없는 갈래 목록**으로 저장한다: 나중에 갈래가 늘어도 저장된 값이 새 갈래를
   *  조용히 숨기지 않는다(모르는 갈래는 켜짐이 기본). */
  edgeOff: EdgeKind[];
};
export type TimeBasis = "recorded" | "content";


/** 시각 문자열의 날짜 머리 `YYYY-MM-DD`(앞 10자). 변환하지 않는다 — 시각이 쓰인 달력의
 *  날을 그대로 자른다. 짧은 값은 그대로 온다. */
export const dayOf = (ts: string): string => ts.slice(0, 10);
/** UTC 달력의 `YYYY-MM-DD`. */
export const utcDay = (t: Date): string => dayOf(t.toISOString());
/** 기록 시각(UTC ISO)을 현지 시각 `YYYY-MM-DDTHH:MM:SS` 로 바꾼다. 접두가 곧 현지의 날이라
 *  상태 줄의 "최근" 날짜가 사람이 보는 달력으로 읽힌다(VIN-262: UTC 로 자르면 한국 00~09시
 *  기록이 전날로 섰다). 시각이 없는 날짜 키나 못 읽는 값은 그대로 돌려준다. */
export function localStamp(t: Date | string): string {
  if (typeof t === "string" && t.length <= 10) return t;
  const d = typeof t === "string" ? new Date(t) : t;
  if (Number.isNaN(d.getTime())) return typeof t === "string" ? t : "";
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 19);
}

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
/** 저장값의 판. loadPrefs 의 옮겨 읽기가 판마다 하나씩이다.
 *  2 = 2026-09-26 기본값 개정 이후. 3 = 0.9.7 에서 뺀 배치 이름(`LAYOUT_ALIAS`)을 접은 뒤.
 *  4 = 첫 그림 개정 이후: 3D · 시간순 가지 · 선 상시 · 범례는 관계 선만. */
const PREFS_VERSION = 4;
/** 첫 그림을 이루는 넷. 판 4 이전 저장값은 이 넷만 한 번 새 기본으로 옮긴다(loadPrefs). */
const FIRST_LOOK = ["view", "layout", "edges", "edgeOff"] as const;
const DEFAULTS: ViewPrefs = {
  //: 크기 배율·밀도(`cone`) 기본은 0.7이다(사람 지시 2026-09-26).
  //: 첫 그림은 3D 시간순 가지에 선을 상시로 그리고, 범례는 방향 없는 관계 선만 켠다.
  //: 선 기본이 `none` 이던 결정을 이 첫 그림이 덮는다.
  //: 근거: [[decision:the-graph-opens-as-3d-time-branches-with-related-lines]]
  view: "compass", size: "links", nodeScale: 0.7, edgeScale: 1,
  labels: "hover", arrows: true, edges: "always", spread: 1, cone: 0.7,
  unlinked: "show", layout: "temporalRoots",
  //: 읽는 면의 기본 폭(rem). `app.css` 의 `#docpane` 폴백과 같은 값이다.
  //: ⚠ 한때 21 이었다 — 왼쪽 끝을 범례 버튼의 시작점에 맞추려고 상단 바 손잡이의
  //: 폭에서 계산한 값이었는데, 그 규율은 철회됐다(발주자 지시 2026-09-09).
  railW: 20,
  timeBasis: "recorded",
  when: 1,
  edgeOff: ["directed", "cross", "bridge"],
};
// 주제 색 토글(`domainTint`)은 2026-09-26 에 없앴다 — 주제 색은 늘 켜져 있다(view-tint.ts).
// 저장값에 남은 키는 loadPrefs 가 읽지 않으므로 무해하다(옮겨 읽기 불필요).
// ⚠ `Number(v) || dflt` 였다(2026-08-17 수정). **0 을 없는 값으로 읽는다** — 시간
// 커서가 0(가장 이른 시점)일 때 저장값이 기본 1(끔)로 조용히 뒤집혔다. 이 리포가
// 불리언에서 이미 두 번 물린 것과 같은 뿌리다(`p.edges == null`). 유한한 수면 그 값을 쓰고, 아닐 때만 기본으로 간다.
const clamp = (v: unknown, lo: number, hi: number, dflt: number) => {
  const n = Number(v);
  return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : dflt;
};
// step 이 곧 **손끝의 해상도**다(2026-08-05). 0.1 이던 동안에는 0.5~2.5 구간에 정지점이
// 20개뿐이라, 230px 짜리 트랙에서 11px 마다 한 칸씩 튀었다 — 끌면 계단처럼 움직인다.
// 0.02 면 100칸이라 손을 따라온다. 값의 의미는 배율이므로 잘게 나눠도 무해하다
// (표시는 소수 둘째 자리까지 — view.ts slider 의 fmt).
export const NODE_SCALE = { min: 0.5, max: 2.5, step: 0.02 };
/** 가지가 퍼지는 각도의 배율(hub · 3D 전용). 1 = 여태의 그림.
 *  범위는 사이드카의 `_CONE_MIN`/`_CONE_MAX` 와 같은 값이어야 한다 — 클램프는
 *  서버가 하지만, 슬라이더가 서버가 안 받는 구간까지 열려 있으면 끝에서 아무 일도
 *  안 일어나 "손잡이가 죽었다"로 읽힌다. */
export const CONE_SCALE = { min: 0.3, max: 1.4, step: 0.02 };
export const EDGE_SCALE = { min: 0.5, max: 3, step: 0.02 };
/** 시간 커서. step 이 잘아도 무해하다 — 위치는 분위수로 읽히므로 정지점이 값의
 *  개수를 넘어서면 같은 컷오프가 반복될 뿐이다. */
export const WHEN_SCALE = { min: 0, max: 1, step: 0.005 };

/** 이 축에서 노드가 말하는 시각. 없으면 "" = **모름**(지어내지 않는다).
 *  두 열은 페이로드에 **둘 다** 실려 온다(api.GraphNode) — 그래서 축 전환도, 커서도
 *  재조회가 없다. */
export const nodeTimeOn = (n: { ts?: string; content_ts?: string }, basis: TimeBasis): string =>
  (basis === "content" ? n.content_ts : n.ts) || "";

/** 커서 위치(0..1) → 잘라 낼 시각. 1 이면 "" 를 돌려준다 = 끔.
 *
 *  **분위수**인 이유: 기록 시각은 뭉쳐 있다(한 번의 적재가 수십 개를 같은 순간에
 *  만든다). 최소~최대를 선형으로 자르면 슬라이더의 대부분 구간에서 아무 일도 안
 *  일어나고, 사람은 그것을 고장으로 읽는다. 값들 위에서 세면 어느 위치에서도 갈린다. */
export function timeCutoff(times: string[], when: number): string {
  if (when >= 1) return "";
  const s = [...new Set(times.filter(Boolean))].sort();
  if (!s.length) return "";
  const i = Math.max(0, Math.min(s.length - 1, Math.round(when * (s.length - 1))));
  return s[i];
}

/** 이 컷오프에서 보이는가. **시각을 모르는 노드는 언제나 보인다** — 개념에는 사건
 *  날짜가 없고 그것은 결손이 아니라 사실이다(개념은 무시간이다). 모른다고 숨기면
 *  화면이 없는 사실을 지어낸다. 비교는 사전식이고, 그래서 형식이 곧 계약이다
 *  (`YYYY[-MM[-DD]]` · ISO 밀리초 — 둘 다 고정폭이라 사전순 = 시간순). */
export const visibleAt = (t: string, cutoff: string): boolean => !cutoff || !t || t <= cutoff;

export function loadPrefs(): ViewPrefs {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return { ...DEFAULTS, edgeOff: [...DEFAULTS.edgeOff] };
    const p = JSON.parse(raw);
    // 기본값 개정(2026-09-26) 이전의 저장값: 저장은 늘 **객체 통째로** 하므로, 손대지 않은
    // 사람도 옛 기본(크기 1 · 밀도 1 · bring)을 값으로 들고 있다. 판(`pv`)이 없고 값이
    // 옛 기본과 **같으면** 손대지 않은 것으로 보고 새 기본을 준다 — 고른 값은 그대로다.
    // ⚠ 판 비교는 `PREFS_VERSION` 이 아니라 **그 옮겨 읽기가 생긴 판**(2)과 한다. 판을
    // 올릴 때마다 판 2 사용자가 고른 크기 1 을 기본값으로 되돌리지 않게.
    const pv = Number(p.pv) || 0;
    if (pv < 2) {
      if (p.nodeScale === 1) p.nodeScale = DEFAULTS.nodeScale;
      if (p.cone === 1) p.cone = DEFAULTS.cone;
    }
    // 판 4 이전 저장값은 첫 그림 넷을 한 번 새 기본으로 옮긴다. 판 4 로 한 번 저장되면 다시
    // 옮기지 않으므로, 그 뒤에 사람이 고른 값은 그대로 남는다.
    if (pv < 4) for (const k of FIRST_LOOK) p[k] = DEFAULTS[k];
    return {
      view: VIEWPOINTS.includes(p.view) ? p.view : "compass",
      size: p.size === "uniform" ? "uniform" : "links",
      nodeScale: clamp(p.nodeScale, NODE_SCALE.min, NODE_SCALE.max, DEFAULTS.nodeScale),
      edgeScale: clamp(p.edgeScale, EDGE_SCALE.min, EDGE_SCALE.max, 1),
      labels: p.labels === "none" ? "none" : "hover",
      arrows: p.arrows !== false,
      // 키가 없으면(구버전 저장값) **기본값**이다 — 여태 always 로 떨어져 기본 focus 가
      // 저장 파일 하나로 조용히 뒤집혔다(2026-08-17). 아는 값은 그대로, 정말 모르는
      // 값만 always(위 EdgeShow 주석의 규율).
      // 걷어 낸 `bring` 은 기본으로, `focus` 는 `none` 으로 읽는다(위 EdgeShow 주석).
      edges: p.edges == null || p.edges === "bring" ? DEFAULTS.edges
        : p.edges === "focus" ? "none"
        : EDGE_SHOWS.includes(p.edges) ? p.edges : "always",
      spread: Math.min(1.6, Math.max(0.6, Number(p.spread) || 1)),
      unlinked: p.unlinked === "hide" ? "hide" : "show",
      // 모르는 이름(폐기된 프리셋 포함)은 조용히 hub 로 접는다.
      layout: normalizeLayout(p.layout),
      railW: Math.min(RAIL_MAX, Math.max(RAIL_MIN, Number(p.railW) || DEFAULTS.railW)),
      // 구 키(`timelineBasis`)도 읽는다 — 개명이 저장값을 조용히 기본으로
      // 되돌리면 축을 content 로 두고 쓰던 사람이 이유 없이 recorded 로 돌아간다.
      timeBasis: (p.timeBasis ?? p.timelineBasis) === "content" ? "content" : "recorded",
      when: clamp(p.when, WHEN_SCALE.min, WHEN_SCALE.max, 1),
      // 모르는 이름은 버린다 — 갈래가 폐기돼도 저장값이 유령 스위치를 남기지 않게.
      edgeOff: Array.isArray(p.edgeOff)
        ? p.edgeOff.filter((k: unknown) => EDGE_KINDS.includes(k as EdgeKind)) : [],
      cone: clamp(p.cone, CONE_SCALE.min, CONE_SCALE.max, DEFAULTS.cone),
    };
  } catch {
    return { ...DEFAULTS, edgeOff: [...DEFAULTS.edgeOff] };
  }
}

export function savePrefs(p: ViewPrefs) {
  localStorage.setItem(KEY, JSON.stringify({ ...p, pv: PREFS_VERSION }));
}
