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
type ViewLayout = "hub" | "temporalRoots";
export const LAYOUTS: ViewLayout[] = ["hub", "temporalRoots"];
export const isRootsLayout = (layout: ViewLayout) => layout === "temporalRoots";
export const layoutsFor = (_view: Viewpoint): ViewLayout[] => LAYOUTS;
/** 서버는 `hub` 좌표만 받는다. 가지 배치는 받은 그래프를 워커에서 재배치한다. */
export const serverLayoutFor = (_layout: ViewLayout): "hub" => "hub";
/** 0.9.7 에서 뺀 배치 이름(`rank`=순서대로, `rankedRoots`=관계순 가지) → 대체.
 *  판 3(`PREFS_VERSION`) 이전에 저장된 값에만 쓰는 옮겨 읽기다(loadPrefs). 판 3 으로
 *  한 번 저장되면 다시 읽히지 않는다. 그 전의 radial·circle·tree·organic·dendro·shell 은
 *  별칭 없이 모르는 값과 같이 `hub` 로 떨어진다(결과가 같아서 표를 뺐다).
 *  제거 조건: 0.9.9 태그 뒤(판 3 을 쓰는 0.9.8 이 한 판 나간 뒤). 빼면 남은 두 이름도 `hub` 로 떨어질 뿐이다. */
const LAYOUT_ALIAS: Record<string, ViewLayout> = {
  rank: "temporalRoots", rankedRoots: "temporalRoots",
};
/** 지금 있는 배치 이름만 받는다. 모르는 값은 기본(`hub`)이다. */
export const normalizeLayout = (v: unknown): ViewLayout =>
  LAYOUTS.includes(v as ViewLayout) ? v as ViewLayout : "hub";

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
/** 끝이 원천(조상)을 가리키는 관계 — "a 가 b 에서 나왔다". 시간순 배치는 이 둘만
 *  뒤집어 조상을 앞에 세운다(`view-layout.ts` 의 `ancestry`). */
export const ANCESTRY_RELS = ["DERIVES_FROM", "INHERITS_FROM"];
/** 화살촉이 붙는 방향 3종. 렌더러(view2d·view3d·view)가 모두 이 목록을 읽는다. */
export const DIRECTED_RELS = ["PREREQUISITE_OF", ...ANCESTRY_RELS];
export function edgeKindOf(e: { type: string; cross_domain?: boolean }): EdgeKind {
  if (DIRECTED_RELS.includes(e.type)) return "directed";
  if (e.type === "CONNECTS") return "bridge";
  if (e.cross_domain) return "cross";
  return "related";
}

/** 선을 **언제** 그리는가. 인코딩(화살촉·굵기·농도)은 하나도 바꾸지 않고 그려지는
 *  순간만 좁힌다 — 헤어볼의 정체는 선 자체가 아니라 관심 밖 선까지 늘 그려져 있다는
 *  것이다([[vinc.research.link-expression]]).
 *    none      노드만 — 선을 그리지 않는다(기본). 관계는 짚거나 고를 때 점등·카드가 말한다
 *    always    전량 상시 · focus 짚은 노드에 걸린 선만(2D)
 *    particles 3D 전용 — 선을 지우고 궤적 위로 입자를 흘린다
 *
 *  `bring`(Bring & Go: 짚으면 이웃이 날아와 고리로 서던 것)은 걷었다(사람 지시
 *  2026-09-26). 저장값에 남은 `bring` 은 가장 가까운 뜻인 `none` 으로 읽는다 — 둘 다
 *  "선을 그리지 않는다"가 출발점이다.
 *  근거: [[decision:bring-and-go-is-retired-and-nodes-only-is-the-default]]
 *
 *  ⚠ 렌더러가 모르는 값은 **가까운 등가물**로 보낸다(3D 에서 `focus` → `none`). 무조건
 *  `always` 로 떨어뜨리면 "상시 전량을 그만두겠다"고 고른 사람이 관점을 옮기는 순간
 *  정반대를 받는다. 정말 모르는 값만 `always` 로 간다.
 *
 *  ⚠ **3D 의 등가물은 `particles` 가 아니다**(지시 2026-09-18). 입자는 상시
 *  애니메이션이라 F6 유휴 정지를 못 받아, 아무도 안 만지는 화면이 계속 그려진다. */
export type EdgeShow = "none" | "always" | "focus" | "particles";
/** 관점마다 고를 수 있는 것이 다르다 — 보이는 손잡이는 반드시 무언가를 해야 한다. */
export const edgeShowsFor = (view: Viewpoint): EdgeShow[] =>
  view === "3d" ? ["none", "always", "particles"] : ["none", "always", "focus"];

/** 저장된 값을 **이 관점이 실제로 하는 것**으로 해석한다. 화면과 패널이 같은 함수를
 *  거치므로, 3D 에서 `focus` 가 노드만으로 그려지는데 패널의 활성 칩만 아무것도 없는
 *  상태가 되지 않는다(그 어긋남이 "손잡이가 안 먹는다"로 읽힌다). */
export const edgeShowFor = (view: Viewpoint, v: EdgeShow): EdgeShow =>
  edgeShowsFor(view).includes(v) ? v
    : v === "focus" || v === "particles" ? (view === "3d" ? "none" : "focus")
    : "always";

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
  edges: EdgeShow;             // 2D — 선을 상시로 그릴지, 짚은 노드에서만 그릴지
  spread: number;              // layout spread multiplier (0.6–1.6)
  unlinked: "show" | "hide";   // orphan nodes (degree 0) — D32 orphan minimization
  layout: ViewLayout;         // 기존 좌표는 서버, 추가 가지 배치는 워커가 계산한다.
  timelineDocs: boolean;       // Timeline — 문서(ABOUT/ATTACHES_TO) 아크 포함
  railW: number;               // D70 — 우측 문서 패널 폭(rem). 끌어서 조절, 기기별 지속
  // D72 — 시간축. `recorded` = 기록 시각(기본), `content` = 내용상 시점.
  // 관점(view)이 아니라 그 관점 **안의 옵션**이다: 같은 것을 다른 시계로 볼 뿐이다.
  //
  // ⚠ 이름이 `timelineBasis` 였다(2026-08-17 개명). 타임라인 전용이던 시절의 이름인데
  // 이제 지도의 시간 커서(`when`)가 같은 값을 읽는다 — **두 번째 시간 어휘를 만들지
  // 않는다**가 그 결정의 일부였다(decision:time-is-a-control-not-a-viewpoint). 이름이
  // 소유자를 잘못 말하면 다음 사람이 지도에서 축을 바꿔 놓고 타임라인만 바뀐 줄 안다.
  timeBasis: TimeBasis;
  /** **시간 커서**(2026-08-17 발주자 결정) — 0..1, 1 = 끔(전부 보임).
   *
   *  시간은 투영과 직교하므로 뷰포인트가 아니라 **세 번째 상태 축**이다: 3D 와 2D 가
   *  이 하나를 공유한다. 값이 분수인 이유는 시각이 고르게 퍼져 있지 않아서다 —
   *  절대 날짜를 자르면 아무 일도 안 일어나는 구간이 길게 생긴다. 실제 값들의
   *  **분위수**로 읽으면 어느 위치에서도 무언가가 갈린다(`timeCutoff`).
   *
   *  타임라인·번들에는 이 손잡이를 안 낸다. 타임라인은 시간을 **축으로 펴 보이는**
   *  곳이라 커서가 하는 일을 이미 스크롤이 한다 — 내놓으면 같은 말을 두 벌 하는
   *  손잡이가 된다(폐기된 flow·totals 가 그렇게 걷혔다). 값은 남으므로 3D·2D 로
   *  돌아오면 그대로 적용된다. */
  when: number;
  /** D74 — 지금 그리는 연결선 갈래. 범례 항목을 눌러 끈다. 빈 배열이 아니라
   *  **없는 갈래 목록**으로 저장한다: 나중에 갈래가 늘어도 저장된 값이 새 갈래를
   *  조용히 숨기지 않는다(모르는 갈래는 켜짐이 기본). */
  edgeOff: EdgeKind[];
  /** D74 — 타임라인 원인·결과 겹치기. 방향 관계를 마디 사이 곡선으로 잇는다. */
  timelineLineage: boolean;
  /** 타임라인 기간(2026-09-28). 프리셋 키 하나이고, `custom` 일 때만 `periodFrom`·
   *  `periodTo` 를 읽는다(둘 다 `YYYY[-MM[-DD]]`, 양 끝 포함). 상대 프리셋(최근 N일·
   *  올해)은 **그릴 때** 오늘에서 푼다 — 저장한 날짜로 굳히면 다음 날 열었을 때 이미
   *  "최근 7일"이 아니다. 주소(view URL)에는 싣지 않는다: 그 문법은 MCP 서버의 파서와
   *  나눠 쓰는 계약이다(`vinc_mcp/viewurl.py`).
   *  근거: [[vinc.plan.view-card-timeline-period-authoring-2026-09-27]] §2 */
  period: PeriodKey;
  periodFrom: string;
  periodTo: string;
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
};
export type TimeBasis = "recorded" | "content";

export type PeriodKey = "all" | "7d" | "30d" | "90d" | "year" | "custom";
export const PERIODS: PeriodKey[] = ["all", "7d", "30d", "90d", "year", "custom"];
/** 사람이 적는 시점 한 칸 — 연 · 월 · 일. 내용 시점(content_time)의 저장 형식과 같다. */
export const TIME_KEY = /^\d{4}(-(0[1-9]|1[0-2])(-(0[1-9]|[12]\d|3[01]))?)?$/;

/** 시점 한 칸의 **다음 칸**(같은 알갱이). `2026` → `2027`, `2026-12` → `2027-01`,
 *  `2026-02-28` → `2026-03-01`. 기간의 끝을 반열린 구간으로 바꾸는 데 쓴다.
 *  날짜 계산은 UTC 달력으로만 한다(저장된 시각을 파싱하지 않는다 — 비교는 늘 문자열). */
export function nextTimeKey(k: string): string {
  const [y, m, d] = k.split("-").map(Number);
  if (m === undefined) return String(y + 1).padStart(4, "0");
  if (d === undefined) {
    return m === 12 ? `${String(y + 1).padStart(4, "0")}-01`
      : `${String(y).padStart(4, "0")}-${String(m + 1).padStart(2, "0")}`;
  }
  return utcDay(new Date(Date.UTC(y, m - 1, d + 1)));
}
/** 시각 문자열의 날짜 머리 `YYYY-MM-DD`(앞 10자). 변환하지 않는다 — 이미 정한 달력(UTC
 *  ISO 면 UTC, `localStamp` 를 거쳤으면 현지)의 날을 그대로 자른다. 짧은 값은 그대로 온다. */
export const dayOf = (ts: string): string => ts.slice(0, 10);
/** UTC 달력의 `YYYY-MM-DD`. 달력 셈(다음 날·달의 길이)에만 쓴다 — 오늘이 언제인지는 `localDay`. */
export const utcDay = (t: Date): string => dayOf(t.toISOString());

/** 기록 시각(UTC ISO)을 현지 시각 `YYYY-MM-DDTHH:MM:SS` 로 바꾼다. 접두가 곧 현지의 날·달·해라
 *  타임라인의 열·점·기간이 사람이 보는 달력으로 나뉜다(VIN-262: UTC 로 나누면 한국 00~09시
 *  기록이 전날 칸에 섰다). 시각이 없는 날짜 키나 못 읽는 값은 그대로 돌려준다. */
export function localStamp(t: Date | string): string {
  if (typeof t === "string" && t.length <= 10) return t;
  const d = typeof t === "string" ? new Date(t) : t;
  if (Number.isNaN(d.getTime())) return typeof t === "string" ? t : "";
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 19);
}
/** 현지 달력의 `YYYY-MM-DD`. */
export const localDay = (t: Date): string => dayOf(localStamp(t));
/** 현지 시점 키(`YYYY`·`YYYY-MM`·`YYYY-MM-DD`)가 시작하는 순간의 UTC ISO. 서버는 저장된 UTC
 *  시각과 비교하므로 현지 칸을 물을 때 경계를 이렇게 옮겨 보낸다. */
export function localKeyToUtc(k: string): string {
  const [y, m = 1, d = 1] = k.split("-").map(Number);
  return new Date(y, m - 1, d).toISOString();
}

/** 프리셋을 오늘(현지) 기준의 **반열린 구간** `[start, end)` 로 푼다. `all` 은 null(거르지 않음).
 *  경계도 노드 시각과 같은 **문자열 키 공간**이다: `start <= ts < end` 가 사전식 비교로
 *  성립한다(ISO 는 고정폭이라 사전순 = 시간순). `custom` 인데 값이 비었거나 형식이
 *  틀리면 거르지 않는다 — 망가진 저장값이 화면을 조용히 비우지 않게. */
export function periodRange(p: Pick<ViewPrefs, "period" | "periodFrom" | "periodTo">,
                            now = new Date()): { start: string; end: string } | null {
  const today = localDay(now);
  const back = (days: number) =>
    localDay(new Date(now.getFullYear(), now.getMonth(), now.getDate() - days));
  switch (p.period) {
    case "7d": return { start: back(6), end: nextTimeKey(today) };
    case "30d": return { start: back(29), end: nextTimeKey(today) };
    case "90d": return { start: back(89), end: nextTimeKey(today) };
    case "year": return { start: today.slice(0, 4), end: nextTimeKey(today.slice(0, 4)) };
    case "custom": {
      const a = p.periodFrom, b = p.periodTo;
      if (!TIME_KEY.test(a) || !TIME_KEY.test(b)) return null;
      const [lo, hi] = a <= b ? [a, b] : [b, a];
      return { start: lo, end: nextTimeKey(hi) };
    }
    default: return null;
  }
}
/** 칸 키를 그 칸의 첫날로 편다(`2026` → `2026-01-01`, `2026-09` → `2026-09-01`). 해·달
 *  알갱이 경계를 날 알갱이 시각과 사전식으로 견주려면 자리수가 같아야 한다. */
const dayStart = (k: string) => (k.length === 4 ? `${k}-01-01` : k.length === 7 ? `${k}-01` : k);
/** 시각 `t` 가 기간 안인가. 양 끝은 날 첫 칸으로 펴서 견준다. 해·달만 적힌 시점(내용 시점)은
 *  점이 아니라 구간이라, **그 구간이 통째로** 기간 안에 들 때만 든다: 9월 한 달을 고르면
 *  `2026-09` 가 들고, 9월 마지막 주만 고르면 안 든다. */
export function inPeriod(t: string, r: { start: string; end: string } | null): boolean {
  if (!r) return true;
  if (!t) return false;
  const lo = dayStart(r.start), hi = dayStart(r.end);
  if (t.length === 4 || t.length === 7) return dayStart(t) >= lo && dayStart(nextTimeKey(t)) <= hi;
  return t >= lo && t < hi;
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
  view: "3d", size: "links", nodeScale: 0.7, edgeScale: 1,
  labels: "hover", arrows: true, edges: "always", spread: 1, cone: 0.7,
  unlinked: "show", layout: "temporalRoots",
  timelineDocs: true,
  //: 읽는 면의 기본 폭(rem). `app.css` 의 `#docpane` 폴백과 같은 값이다.
  //: ⚠ 한때 21 이었다 — 왼쪽 끝을 범례 버튼의 시작점에 맞추려고 상단 바 손잡이의
  //: 폭에서 계산한 값이었는데, 그 규율은 철회됐다(발주자 지시 2026-09-09).
  railW: 20,
  timeBasis: "recorded",
  when: 1,
  edgeOff: ["directed", "cross", "bridge"],
  timelineLineage: false,
  // 기본은 전체다(Q2.2) — 여태와 같은 그림. 넘치면 캡션이 "기간 좁히기"를 권한다.
  period: "all", periodFrom: "", periodTo: "",
  bundleDepth: 3,
  bundleOff: [],
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
    // 판 3 이전에 저장된 `rank`·`rankedRoots` 는 `시간순 가지`로 접는다(`LAYOUT_ALIAS`).
    if (pv < 3 && typeof p.layout === "string" && Object.prototype.hasOwnProperty.call(LAYOUT_ALIAS, p.layout))
      p.layout = LAYOUT_ALIAS[p.layout];
    // 판 4 이전 저장값은 첫 그림 넷을 한 번 새 기본으로 옮긴다. 판 4 로 한 번 저장되면 다시
    // 옮기지 않으므로, 그 뒤에 사람이 고른 값은 그대로 남는다.
    if (pv < 4) for (const k of FIRST_LOOK) p[k] = DEFAULTS[k];
    return {
      view: VIEWPOINTS.includes(p.view) ? p.view : "3d",
      size: p.size === "uniform" ? "uniform" : "links",
      nodeScale: clamp(p.nodeScale, NODE_SCALE.min, NODE_SCALE.max, DEFAULTS.nodeScale),
      edgeScale: clamp(p.edgeScale, EDGE_SCALE.min, EDGE_SCALE.max, 1),
      labels: p.labels === "none" ? "none" : "hover",
      arrows: p.arrows !== false,
      // 키가 없으면(구버전 저장값) **기본값**이다 — 여태 always 로 떨어져 기본 focus 가
      // 저장 파일 하나로 조용히 뒤집혔다(2026-08-17). 아는 값은 그대로, 정말 모르는
      // 값만 always(위 EdgeShow 주석의 규율).
      // 걷어 낸 `bring` 은 `none` 으로 읽는다(위 EdgeShow 주석).
      edges: p.edges == null || p.edges === "bring" ? DEFAULTS.edges
        : ["none", "always", "focus", "particles"].includes(p.edges) ? p.edges : "always",
      spread: Math.min(1.6, Math.max(0.6, Number(p.spread) || 1)),
      unlinked: p.unlinked === "hide" ? "hide" : "show",
      // 모르는 이름(폐기된 프리셋 포함)은 조용히 hub 로 접는다.
      layout: normalizeLayout(p.layout),
      timelineDocs: p.timelineDocs !== false,
      railW: Math.min(RAIL_MAX, Math.max(RAIL_MIN, Number(p.railW) || DEFAULTS.railW)),
      // 구 키(`timelineBasis`)도 읽는다 — 개명이 저장값을 조용히 기본으로
      // 되돌리면 축을 content 로 두고 쓰던 사람이 이유 없이 recorded 로 돌아간다.
      timeBasis: (p.timeBasis ?? p.timelineBasis) === "content" ? "content" : "recorded",
      when: clamp(p.when, WHEN_SCALE.min, WHEN_SCALE.max, 1),
      // 모르는 이름은 버린다 — 갈래가 폐기돼도 저장값이 유령 스위치를 남기지 않게.
      edgeOff: Array.isArray(p.edgeOff)
        ? p.edgeOff.filter((k: unknown) => EDGE_KINDS.includes(k as EdgeKind)) : [],
      timelineLineage: p.timelineLineage === true,
      period: PERIODS.includes(p.period) ? p.period : "all",
      periodFrom: typeof p.periodFrom === "string" && TIME_KEY.test(p.periodFrom) ? p.periodFrom : "",
      periodTo: typeof p.periodTo === "string" && TIME_KEY.test(p.periodTo) ? p.periodTo : "",
      // 저장값이 문자열로 돌아올 수 있어(구 버전·수기 편집) Number 로 접어 받는다.
      bundleDepth: BUNDLE_DEPTHS.includes(Number(p.bundleDepth) as BundleDepth)
        ? (Number(p.bundleDepth) as BundleDepth) : DEFAULTS.bundleDepth,
      cone: clamp(p.cone, CONE_SCALE.min, CONE_SCALE.max, DEFAULTS.cone),
      bundleOff: Array.isArray(p.bundleOff)
        ? p.bundleOff.filter((k: unknown) => BUNDLE_RELS.includes(String(k))) : [],
    };
  } catch {
    return { ...DEFAULTS, edgeOff: [...DEFAULTS.edgeOff] };
  }
}

export function savePrefs(p: ViewPrefs) {
  localStorage.setItem(KEY, JSON.stringify({ ...p, pv: PREFS_VERSION }));
}
