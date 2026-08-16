import { COPY } from "../copy";

/** View 평면 뷰 (D43) — Timeline · Sankey.
 *
 *  3D/2D 맵과 같은 시각 문법을 평면으로 옮긴다: 모노크롬 잉크가 정본이고
 *  의미는 형태 · 농도 · 굵기 · 위치가 나른다(색상환은 뜻을 나르지 않는다, D41).
 *  초점색(`--focus`)은 딱 한 곳 — hover/고정된 리니지뿐이며, 그 외의 어떤 마크도
 *  **뜻으로** 색이 갈리지 않는다.
 *
 *  예외가 하나 있고 그것은 뜻이 아니다: 주제 틴트(`.pl-tint` + `--ink`)는 사용자가
 *  켜는 **열린 층**이라 꺼도 아무 정보가 사라지지 않는다(view-tint.ts).
 *
 *  의존성 0: 순수 DOM + 인라인 SVG(캔버스도 WebGL 도 아니다). 색은 전부 토큰
 *  참조(hsl(var(--…)))라 테마 전환에 자동으로 따라간다 — hex 하드코딩 금지.
 *  레이아웃 수치는 전부 입력 순서의 결정론(Math.random · 시간 의존 없음)이라
 *  같은 데이터는 언제 그려도 같은 그림이 된다(D17 좌표 철학의 평면판).
 *
 *  스타일 규칙(.pl-*)은 app.css 의 View 절에 산다 — 템플릿 문자열 안에 숨은
 *  두 번째 스타일시트를 만들지 않는다(디자인 시스템 · 모노크롬 게이트가 훑는
 *  파일이 곧 규칙이 사는 파일이어야 한다).
 *
 *  화면 문구는 전부 copy.ts 덱(COPY.ui.viewx)에서 온다. view3d.ts 가 COPY 를
 *  import 하지 않는 것은 그쪽이 문구를 한 줄도 저작하지 않고 데이터 필드만 그리는
 *  순수 렌더러이기 때문이다. 이 파일은 차트 이름과 구간 이름을 스스로 짓는 쪽이라
 *  문구를 저작하는 다른 모듈들과 같은 규칙을 따른다(각자 COPY 를 import).
 *
 *  D53 — Flow 렌더러는 관점 폐기와 함께 제거됐다. 남은 평면은 Timeline·Sankey 둘.
 *
 *  <svg> 에는 aria-label 만 두고 role="img" 는 두지 않는다: role="img" 는 하위
 *  트리를 통째로 접어 마크마다 달아 둔 설명을 전부 가린다. 이름은 남기고 안은
 *  열어 둔다(키보드 순회는 이 코드베이스에 없는 문법이라 새로 만들지 않는다).
 *
 *  툴팁은 `data-tip` 속성으로만 낸다(D48-T) — SVG `<title>` = 네이티브 툴팁은
 *  WCAG 1.4.13(dismissable·hoverable·persistent)을 하나도 만족하지 못해 전면
 *  교체했다. 실제 표시는 tooltip.ts 의 단일 오버레이가 담당한다. */

/** `focus(id)` 는 hover 로만 되던 강조를 **밖에서도** 걸 수 있게 연다(D70) —
 *  좌측 트리·검색 착지가 평면 관점에서도 같은 노드를 가리켜야 하기 때문이다.
 *  마운트된 집합에 없으면 false 를 돌려주고 호출측이 스코프 교체를 판단한다. */
export type PlanarHandle = { dispose(): void; focus?(id: string): boolean };
/** 타임라인이 받는 마크 — 원래 Flow 와 공유하던 모양이라 이름이 Flow* 였다.
 *  Flow 폐기(D53) 후에도 모양은 그대로라 이름만 중립화한다. */
export type PlanarNode = { id: string; label: string; domain: string };

/** 타임라인 버킷 알갱이(D72). 키는 전부 ts 의 접두라 사전식 정렬이 곧 시간순이다. */
export type Grain = "day" | "month" | "year" | "decade";

/** 고유 버킷 수가 화면에 담길 만큼일 때까지 알갱이를 물린다.
 *  일 60개·월 48개(4년)·연 60개(60년)가 상한 — 그 이상은 칸이 글자보다 좁아진다.
 *
 *  일(day)은 기둥 문법(`mountColumns`, 2026-08-14) 때문에 **아래로** 늘린 단이다.
 *  이 형식은 기둥이 여럿이어야 읽히는데, 몇 달 안 된 코퍼스는 월 단위로 묶으면
 *  기둥이 한둘로 뭉개진다(실측: 227건이 2기둥). 데이터가 얇은 것이 아니라 자가
 *  거친 것이므로, 눈금을 잘게 하면 그대로 살아난다. */
export function pickGrain(list: string[]): Grain {
  const uniq = (n: number) => new Set(list.map((t) => t.slice(0, n))).size;
  if (uniq(10) <= 60) return "day";
  if (uniq(7) <= 48) return "month";
  if (uniq(4) <= 60) return "year";
  return "decade";
}

export function bucketKey(ts: string, g: Grain): string {
  if (g === "day") return ts.slice(0, 10);
  if (g === "month") return ts.slice(0, 7);
  if (g === "year") return ts.slice(0, 4);
  return `${ts.slice(0, 3)}0`;      // 190x → 1900
}

/** 버킷 키 → 반개구간 [start, end). 문자열 산술뿐이다(날짜 파싱 금지 — ts 계약).
 *  끝값은 "다음 버킷의 시작"이고, 자리수를 유지해야 사전식 비교가 성립한다. */
export function bucketRange(key: string, g: Grain): [string, string] {
  // 일 단의 끝값만 다른 방식이다. "다음 날"은 달의 길이와 윤년을 알아야 나오는데
  // 그건 이 파일이 금지한 날짜 파싱이다(타임존·로케일이 딸려 온다). 대신 **접두
  // 상한**을 쓴다: `2026-08-14` 로 시작하는 어떤 타임스탬프도 `2026-08-14￿`
  // 보다 작고, 다음 날 `2026-08-15` 는 그보다 크다. 순수 문자열 비교로 성립한다.
  if (g === "day") return [key, `${key}￿`];
  if (g === "month") {
    const y = Number(key.slice(0, 4)), m = Number(key.slice(5, 7));
    const ny = m === 12 ? y + 1 : y, nm = m === 12 ? 1 : m + 1;
    return [key, `${String(ny).padStart(4, "0")}-${String(nm).padStart(2, "0")}`];
  }
  const step = g === "year" ? 1 : 10;
  const y = Number(key);
  return [key, String(y + step).padStart(4, "0")];
}
/** Timeline 에서 "문서 연결"(timelineDocs=off)로 걷어내는 부착 노드종. */
const DOC_KINDS = ["episode", "record"];
/** 같은 걸러내기를 호출부(빈 상태 판정·날짜 없는 수 세기)도 써야 한다 — 챠트가
 *  그리는 집합과 판정하는 집합이 어긋나면 "빈 캔버스 + 무문구"가 난다. */
export const timelinePool = <T extends { kind?: string }>(nodes: T[], docs: boolean) =>
  docs ? nodes : nodes.filter((n) => !DOC_KINDS.includes(n.kind || ""));

// 실측 여백 — app.css 의 .pl-wrap padding 과 같은 값을 유지해야 한다(둘이 어긋나면
// viewBox 가 컨테이너보다 커져 스크롤이 생긴다).
export const PAD_X = 16;      // .pl-wrap 좌우 패딩
export const PAD_TOP = 56;    // 상단 크롬(크럼 · 툴 행) 회피
export const PAD_BOT = 16;
/** 세로 스크롤바 폭 여유 — Windows 클래식 17px 을 덮는 상한. 오버레이 스크롤바
 *  (macOS)에서는 그냥 여백이 된다. 세로 타임라인만 쓴다(항상 세로로 넘치므로). */
const SBAR = 18;

// ── 문자열 조립 헬퍼 ───────────────────────────────────────────────────────
const esc = (s: string) => s.replace(/[&<>"]/g, (c) =>
  c === "&" ? "&amp;" : c === "<" ? "&lt;" : c === ">" ? "&gt;" : "&quot;");
/** 좌표 소수 1자리 — 문자열이 흔들리지 않게(결정론 + diff 가독). */
const num = (v: number) => String(Math.round(v * 10) / 10);
//: 주제 틴트로 실릴 수 있는 유일한 형태. style 속성에 넣기 전 확인한다.
const HEX6 = /^#[0-9a-fA-F]{6}$/;
/** 정육각 꼭짓점 — 형태=타입(D35): 기록물 계열은 육각, 개념은 원. */
/** 2D 마크 = 노드종(디자인 시안 `Vinc Node Kit 3D` §4 · 2026-08-15).
 *
 *  3D 조형을 그대로 축약한다 — 구=원 · 정사면체=삼각 · 정팔면체=마름모 ·
 *  겹껍질=옅은 원+코어 · 윤곽선=파선 링. 채움 위주라 작은 크기에서도 종이 갈린다.
 *  초점은 **형태를 바꾸지 않고 색만** 바꾼다(그래서 Domain 겹원과 표기가 안 겹친다).
 *
 *  타임라인과 번들이 같은 함수를 쓴다. 갈라 두면 같은 노드가 화면마다 다른 모양이
 *  되는데, 그건 형태에 뜻을 실은 것을 스스로 무너뜨리는 일이다. */
export function markSvg(kind: string | undefined, gateway: boolean,
                        x: number, y: number, r: number, cls: string): string {
  const a = (n: string, v: number) => `${n}="${Math.round(v * 100) / 100}"`;
  const poly = (pts: [number, number][]) =>
    `<polygon class="${cls}" points="${pts.map(([px2, py2]) =>
      `${Math.round(px2 * 100) / 100},${Math.round(py2 * 100) / 100}`).join(" ")}"/>`;
  if (gateway)   // 면이 없다 = 실체가 없다. 파선 간격은 시안의 실측 비율(.68r / .56r).
    return `<circle class="${cls} pl-gate" ${a("cx", x)} ${a("cy", y)} ${a("r", r)}`
      + ` fill="none" stroke-dasharray="${Math.round(r * 68) / 100} ${Math.round(r * 56) / 100}"/>`;
  if (kind === "domain")   // 담는 그릇 — 옅은 외피 + 크기를 읽히게 하는 코어
    return `<circle class="${cls} pl-shell" ${a("cx", x)} ${a("cy", y)} ${a("r", r)}`
      + ` fill-opacity=".18"/>`
      + `<circle class="${cls} pl-core" ${a("cx", x)} ${a("cy", y)} ${a("r", r * 0.42)}/>`;
  if (kind === "bridge")   // 마름모 — 방향 중립
    return poly([[x, y - r], [x + r, y], [x, y + r], [x - r, y]]);
  if (kind && kind !== "concept" && kind !== "decision") {
    // 문서류 — 정사면체의 정면. 외접원 1.08r 이라 원과 시각 무게가 같다(시안 §5).
    const R2 = r * 1.08;
    return poly([-90, 30, 150].map((deg) => {
      const t = (deg * Math.PI) / 180;
      return [x + Math.cos(t) * R2, y + Math.sin(t) * R2] as [number, number];
    }));
  }
  return `<circle class="${cls}" ${a("cx", x)} ${a("cy", y)} ${a("r", r)}/>`;
}

const hexPts = (cx: number, cy: number, r: number) => {
  const p: string[] = [];
  for (let i = 0; i < 6; i++) {
    const a = (Math.PI / 180) * (60 * i - 30);
    p.push(`${num(cx + r * Math.cos(a))},${num(cy + r * Math.sin(a))}`);
  }
  return p.join(" ");
};
const wrap = (svg: string) => `<div class="pl-wrap">${svg}</div>`;

/** 컨테이너 실측 추적 — draw 는 (w,h) 의 순수 함수라 리사이즈는 "다시 그릴" 뿐이다.
 *  rAF 로 합치고 크기가 실제로 변할 때만 그린다(리사이즈 폭주 방지). */
function trackSize(el: HTMLElement, paint: (w: number, h: number) => void): () => void {
  let last = "";
  let raf = 0;
  const run = () => {
    raf = 0;
    const w = Math.max(el.clientWidth - PAD_X * 2, 360);
    const h = Math.max(el.clientHeight - PAD_TOP - PAD_BOT, 260);
    const key = `${w}x${h}`;
    if (key === last) return;
    last = key;
    paint(w, h);
  };
  run();
  const ro = new ResizeObserver(() => { if (!raf) raf = requestAnimationFrame(run); });
  ro.observe(el);
  return () => { ro.disconnect(); if (raf) cancelAnimationFrame(raf); };
}

/** D32 hover 문법 배선 — 초점은 남고 나머지는 물러난다. 마크마다 리스너를 다는
 *  대신 컨테이너에 위임 3개만 건다(다시 그려도 재배선 불필요 · dispose 가 단순). */
function wireFocus(el: HTMLElement, h: {
  focus: (id: string) => void; clear: () => void; open: (id: string) => void;
  /** 선(엣지) 클릭 — data-e 인덱스. 노드가 우선(마크가 DOM 상 뒤 = 위)이라
   *  노드에 닿은 클릭은 여기로 오지 않는다. 미지정 시 기존 동작 그대로. */
  edge?: (idx: number) => void;
  /** 생키 구간 클릭 — (도메인, mark). 구간 rect 의 data-dom·data-seg 로 자기완결.
   *  구간이 행(data-id)보다 **우선**한다(구간 드릴다운 ≠ 행 열기, D45-V2 3단계). */
  seg?: (dom: string, mark: string) => void;
  /** 타임라인 월-밴드 클릭 — data-month("YYYY-MM"). 노드(data-id)보다 **나중**에
   *  검사해 점 클릭이 우선한다(빈 밴드 클릭만 기간 선택, D45-V2 4단계). */
  month?: (m: string) => void;
}): () => void {
  const hit = (e: Event) => (e.target as Element).closest?.("[data-id]") as SVGElement | null;
  const over = (e: Event) => { const g = hit(e); if (g) h.focus(g.dataset.id!); else h.clear(); };
  const leave = () => h.clear();
  const click = (e: Event) => {
    if (h.seg) {
      const gs = (e.target as Element).closest?.("[data-seg]") as SVGElement | null;
      if (gs) { h.seg(gs.dataset.dom!, gs.dataset.seg!); return; }
    }
    const g = hit(e);
    if (g) { h.open(g.dataset.id!); return; }
    if (h.edge) {
      const ge = (e.target as Element).closest?.("[data-e]") as SVGElement | null;
      if (ge) { h.edge(Number(ge.dataset.e)); return; }
    }
    if (h.month) {
      const gm = (e.target as Element).closest?.("[data-month]") as SVGElement | null;
      if (gm) h.month(gm.dataset.month!);
    }
  };
  el.addEventListener("mouseover", over);
  el.addEventListener("mouseleave", leave);
  el.addEventListener("click", click);
  return () => {
    el.removeEventListener("mouseover", over);
    el.removeEventListener("mouseleave", leave);
    el.removeEventListener("click", click);
  };
}

// ── 2. Timeline — 언제 무엇이 생겼는가 ────────────────────────────────────
/** **세로 시간축 + 뿌리 은유**(발주자 지시 2026-08-02 — D62 개정).
 *
 *  위가 시작 시점이고 아래로 갈수록 나중이다. **스크롤을 내리는 것이 곧 시간을
 *  흐르는 것**이다. 가로는 시간이 아니라 **가지**다(주제 하나 = 가지 하나).
 *  맨 위 한 점에서 갈라져 나온 가지들이 아래로 자라며 마디를 단다.
 *
 *  왜 다시 세로인가: D62 가 가로로 눕힌 사유는 "주제가 두셋뿐이면 레인이 화면
 *  좌우 끝에 붙고 가운데가 진공"이었다. 그 진공은 축의 방향이 아니라 **레인을
 *  화면 가득 펴는 배치**에서 나온다. 지금은 가지 폭이 고정(BRANCH_W)이고 가지
 *  다발이 뿌리 아래 모여 있어, 주제가 적으면 다발이 좁아질 뿐 진공이 없다.
 *
 *  ts 는 여전히 파싱하지 않는다: ISO 는 사전순이 곧 시간순이고 버킷 키는 접두
 *  문자열이다(D72 알갱이 적응 — 월 → 연 → 십년). 세로 간격은 실경과에 비례하지
 *  않는다 — 버킷의 **순서**만 뜻이 있고, 칸 높이는 그 칸의 밀도에서 온다.
 *
 *  **원인·결과 겹치기**(D74, `lineage` — 발주자 지시 2026-08-02). 가지는 시간에
 *  따라 갈라지지만 갈라진 이유는 대개 **다른 가지에 있다**. 그 이유는 개념 간
 *  방향 관계(PREREQUISITE_OF · DERIVES_FROM · INHERITS_FROM)로 이미 적혀 있으므로,
 *  마디와 마디를 곡선으로 이어 주면 "이건 저것 때문"이 화면에 그대로 나온다.
 *  세로축이 시간이라 **원인이 위, 결과가 아래**이고, 가지를 가로지르는 선이 곧
 *  "다른 주제가 원인"이다. 시간을 거스르는 선(원인이 아래)은 그리되 점선으로
 *  가른다 — 데이터가 그렇게 적혀 있는 것이지 화면이 틀린 것이 아니다. */
/** 시간 = **가로**, 한 구간이 한 기둥, 항목 하나가 점 하나.
 *
 *  왜 갈아엎었나(발주자 지시 2026-08-14, 참조 이미지 제시). 앞선 두 문법은 **양을
 *  인코딩**하려 했다 — 생키는 리본 굵기로, 직전 타임라인은 가지 굵기로. 둘 다 스케일과
 *  하한이 필요했고, 하한이 작은 칸만 부풀려 비례가 깨지는 것을 실측으로 확인했다
 *  (항목당 굵기 0.41 vs 0.19). 이 형태는 그 문제를 **없앤다**: 개수를 굵기로 번역하지
 *  않고 점을 그냥 그만큼 찍는다. 기둥 길이가 곧 개수이고, 세어서 확인할 수 있다.
 *  스케일도 하한도 아핀 오차도 없다 — 보존이 인코딩이 아니라 **사실**이 된다.
 *
 *  채널 배정:
 *    가로 = 언제 (버킷) · 기둥 길이 = 그때 몇 개 · 점 크기 = 연결 수
 *    속 빈 점 = 연결 0(고아) · 모양 = 종류(D35 닫힌 층 그대로: 문서류는 육각)
 *
 *  고아를 속 빈 점으로 둔 것은 장식이 아니다. 합계 뷰가 폐기되면 고아 리본이 사라지고
 *  그것이 **고아 최소화의 유일한 진입점**이었다(spec §4.1). 여기서 고아는 화면에서
 *  저절로 눈에 띄고, 눌러서 바로 열린다.
 *
 *  ⚠ 주제(도메인)는 **자리**를 갖지 않는다. 기둥 안 정렬 키일 뿐이라 세로 위치는 아무
 *  것도 뜻하지 않는다(참조 이미지도 같다). 대신 `.pl-lane` 묶음이 주제별로 남아 있어
 *  hover 하면 그 주제만 전 기간에 걸쳐 살아난다 — 자리 대신 **강조**로 답한다. */
export function mountColumns(el: HTMLElement,
  nodes: (PlanarNode & { ts?: string; kind?: string; degree?: number; gateway?: boolean;
                         tint?: string })[],
  o: { docs: boolean; onOpen: (id: string) => void; onMonth?: (bucket: string) => void;
       lineage?: { source: string; target: string; type: string }[];
       /** 초점이 걸렸을 때 **남을 것**을 정하는 관계 — 갈래를 가리지 않고 전부 받는다.
        *  그려지는 선(lineage)과 다른 입력인 이유: 그리는 것은 방향 관계만이지만,
        *  "관련"은 그보다 넓다(연관·부착·다리도 이어진 것이다). */
       related?: { source: string; target: string }[] },
): PlanarHandle {
  const pool = timelinePool(nodes, o.docs);
  const dated = pool.filter((n) => !!(n.ts && n.ts.trim()))
    .sort((a, b) => (a.ts! < b.ts! ? -1 : a.ts! > b.ts! ? 1 : a.id < b.id ? -1 : 1));
  const grain = pickGrain(dated.map((n) => n.ts!));
  const bucketOf = (ts: string) => bucketKey(ts, grain);
  const months = [...new Set(dated.map((n) => bucketOf(n.ts!)))];
  const monthAt = new Map(months.map((m, i) => [m, i]));
  const laneKey = (n: { domain: string }) => n.domain || "";
  const lanes = [...new Set(dated.map(laneKey))]
    .sort((a, b) => (a === "" ? 1 : b === "" ? -1 : a < b ? -1 : 1));
  const laneAt = new Map(lanes.map((d, i) => [d, i]));
  // 기둥 안 순서 — 주제, 그 다음 id. **결정론**이 요건이다(같은 데이터면 같은 그림).
  const cols = months.map(() => [] as typeof dated);
  for (const n of dated) cols[monthAt.get(bucketOf(n.ts!))!].push(n);
  for (const c of cols) {
    c.sort((a, b) => (laneAt.get(laneKey(a))! - laneAt.get(laneKey(b))!)
      || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  }
  const maxDeg = Math.max(1, ...dated.map((n) => n.degree || 0));
  const seen = new Map(dated.map((n) => [n.id, n]));
  const lineage = (o.lineage || [])
    .filter((e) => e.source !== e.target && seen.has(e.source) && seen.has(e.target));
  const label = COPY.ui.viewx.timelineChart(dated.length, lanes.length);

  // TOP 은 44 였다. 연도 줄(2026-08-16)이 눈금 위에 한 단 더 서면서 14px 을 더 준다 —
// 연도 22 · 밑줄 28 · 날짜 38 · 눈금 44~51 순으로 쌓인다.
const PAD_L = 42, PAD_R = 22, TOP = 58, BOT = 26;
  const COL_MIN = 15, COL_MAX = 46;   // 기둥 간격. 좁아지면 가로 스크롤이 받는다
  const SLOT = 12;                    // 점 하나가 세로로 차지하는 몫
  const R_MIN = 1.9, R_MAX = 5.2;
  let first = true;
  /** 마지막으로 그린 점들의 좌표. 초점 선을 **다시 그리지 않고** 얹기 위해 남긴다
   *  (짚을 때마다 227개 마크를 재생성하면 호버가 무거워진다). */
  let posOut = new Map<string, { x: number; y: number }>();

  function draw(w0: number, h: number): string {
    const w = w0 - SBAR;
    // 한 줄이 담는 최대 개수 = **화면 높이가 정한다**. 그래야 꽉 찬 줄이 화면을 정확히
    // 채우고, 넘치는 구간만 옆으로 접힌다(발주자 규칙 2026-08-14).
    //   · 넘지 않으면 줄을 더 만들지 않는다
    //   · 넘으면 옆에 새 줄이 생긴다
    //   · 한 구간의 줄은 최대 3개
    // 채우고-넘기기(균등 분배가 아니다)인 이유: 균등하게 나누면 61개가 31+30 이 되어
    // 60개짜리 한 줄보다 **짧아 보인다** — "줄 길이 = 개수"라는 이 관점의 뼈대가 깨진다.
    const perLine = Math.max(20, Math.floor((h - TOP - BOT) / SLOT));
    // 줄 하나 = 기둥 하나. 구간이 여러 줄이면 그 구간이 여러 기둥을 차지한다.
    type Line = { m: string; items: typeof dated };
    const lines: Line[] = [];
    const span: { m: string; from: number; n: number }[] = [];
    months.forEach((m, i) => {
      const list = cols[i];
      const want = Math.max(1, Math.ceil(list.length / perLine));
      const use = Math.min(3, want);
      const from = lines.length;
      for (let j = 0; j < use; j++) {
        // 마지막 줄은 남은 것을 전부 받는다 — 3줄 상한을 넘겨도 **버리지 않는다**.
        // (조용한 절단 금지. 넘친 만큼은 아래 `over` 로 세어 캡션이 밝힌다.)
        const items = j === use - 1 ? list.slice(j * perLine) : list.slice(j * perLine, (j + 1) * perLine);
        lines.push({ m, items });
      }
      // 3줄로도 못 담으면 마지막 줄이 화면보다 길어져 세로 스크롤이 돌아온다.
      // 숨기지 않는다 — 그 기둥이 눈에 띄게 길어지는 것 자체가 정직한 신호다.
      span.push({ m, from, n: use });
    });
    const tallest = Math.max(1, ...lines.map((l) => l.items.length));
    const avail = Math.max(w - PAD_L - PAD_R, 240);
    const colW = Math.max(COL_MIN, Math.min(COL_MAX, avail / Math.max(lines.length, 1)));
    const W = Math.max(w, PAD_L + colW * lines.length + PAD_R);
    const H = Math.max(h, TOP + tallest * SLOT + BOT);
    const colX = (i: number) => PAD_L + (i + 0.5) * colW;
    const slotY = (j: number) => TOP + (j + 0.5) * SLOT;
    // 크기 = 연결 수. 제곱근이라 **면적**이 연결에 비례한다(반지름 비례면 큰 쪽이
    // 과장된다 — 넓이로 읽히는 것이 원이다).
    const rOf = (deg: number) => R_MIN + (R_MAX - R_MIN) * Math.sqrt(deg / maxDeg);
    // 눈금 이름은 자리가 있을 때만 — 좁으면 건너뛰며 단다(겹친 글자는 눈금이 아니다).
    // 눈금·띠·클릭 표적은 **줄이 아니라 구간** 단위다. 접힌 줄들이 한 덩어리로 읽혀야
    // "이 하루가 이만큼"이 유지된다 — 줄마다 이름을 달면 같은 날짜가 세 번 나온다.
    const step = Math.max(1, Math.ceil(38 / colW));
    /** 기둥 이름은 **연도를 뗀 나머지**다. 여태 `2026-07-11` 을 통째로 찍어, 같은
     *  연도가 기둥마다 반복되면서 정작 달과 날이 그 뒤에 묻혔다(발주자 지적
     *  2026-08-16). 연도는 위 단이 한 번만 말한다. 연·십년 단에서는 키 자체가
     *  연도라 뗄 것이 없다. */
    const colName = (key: string) =>
      grain === "day" || grain === "month" ? key.slice(5) : key;

    /** 연도 묶음 — 이웃한 기둥들이 같은 해면 한 덩어리다. 연도는 그 덩어리 가운데에
     *  한 번 서고, 밑줄이 어디까지가 그 해인지를 말한다. 눈금 이름과 달리 **건너뛰지
     *  않는다**: 연도가 빠지면 그 구간이 어느 해인지 알 길이 없다. */
    const yearSpans = (() => {
      if (grain !== "day" && grain !== "month") return [];
      const out: { y: string; from: number; n: number }[] = [];
      for (const sp of span) {
        const y = sp.m.slice(0, 4);
        const last = out[out.length - 1];
        if (last && last.y === y) last.n += sp.n;
        else out.push({ y, from: sp.from, n: sp.n });
      }
      return out;
    })();
    const years = yearSpans.map((g) => {
      const x0 = PAD_L + g.from * colW, wSpan = colW * g.n;
      return `<path class="pl-ax pl-yr-rule" d="M${num(x0 + 1)} ${num(TOP - 30)}`
        + ` L${num(x0 + wSpan - 1)} ${num(TOP - 30)}"/>`
        + `<text class="pl-cap pl-num pl-yr" x="${num(x0 + wSpan / 2)}" y="${num(TOP - 36)}"`
        + ` text-anchor="middle">${esc(g.y)}</text>`;
    }).join("");
    const axis = span.map((s, i) => {
      const x0 = PAD_L + s.from * colW;
      const wSpan = colW * s.n;
      const cx = x0 + wSpan / 2;
      const y = TOP - 6, hh = H - TOP - BOT + 6;
      const tick = `<path class="pl-ax" d="M${num(cx)} ${num(TOP - 14)} L${num(cx)} ${num(TOP - 7)}"/>`;
      const name = i % step === 0
        ? `<text class="pl-cap pl-num" x="${num(cx)}" y="${num(TOP - 20)}"`
          + ` text-anchor="middle">${esc(colName(s.m))}</text>` : "";
      const hit = o.onMonth
        ? `<rect class="pl-band-hit" data-month="${esc(s.m)}" x="${num(x0)}" y="${num(y)}"`
          + ` width="${num(wSpan)}" height="${num(hh)}" fill="transparent"`
          + ` style="cursor:pointer"/>` : "";
      return `${i % 2 === 1 ? `<rect class="pl-band" x="${num(x0)}" y="${num(y)}"`
        + ` width="${num(wSpan)}" height="${num(hh)}"/>` : ""}${hit}${tick}${name}`;
    }).join("");

    const pos = new Map<string, { x: number; y: number }>();
    const byLane = new Map<string, string[]>(lanes.map((d) => [d, []]));
    const grow = first ? " pl-grow" : "";
    lines.forEach((ln, i) => {
      const x = colX(i);
      ln.items.forEach((n, j) => {
        const y = slotY(j);
        pos.set(n.id, { x, y });
        const deg = n.degree || 0;
        const r = rOf(deg);
        // 속이 빈 점 = 연결 0. 합계 뷰의 고아 리본이 하던 일을 여기서 받는다.
        const solid = deg > 0 ? " pl-solid" : "";
        // 모양은 종이 정한다(markSvg) — 지도·타임라인·번들이 같은 어휘를 쓴다.
        const mark = markSvg(n.kind, !!n.gateway, x, y, r, `pl-mark${solid}`);
        // 자람은 **왼쪽(과거)에서 오른쪽(현재)으로** — 시간이 가로니 안무도 가로다.
        const d = num(Math.min(1.1, 0.1 + (i / Math.max(lines.length - 1, 1)) * 0.9));
        // 주제 틴트(열린 층) — 켜져 있을 때만 `--ink` 를 실어 CSS 가 잉크 대신 쓴다.
        // `#rrggbb` 만 통과시킨다: 값은 tintFor 가 만든 것뿐이지만, style 속성에
        // 문자열을 넣는 자리는 형태를 확인하고 넣는 것이 규율이다.
        const tint = HEX6.test(n.tint || "") ? n.tint! : "";
        const cls = `pl-node${grow}${tint ? " pl-tint" : ""}`;
        const style = `--d:${d}s${tint ? `;--ink:${tint}` : ""}`;
        byLane.get(laneKey(n))!.push(
          `<g class="${cls}" style="${style}" data-id="${esc(n.id)}"`
          + ` data-tip="${esc(n.label)} · ${esc(n.ts || "")}${deg ? ` · ${deg}` : ""}">${mark}</g>`);
      });
    });
    const laneGs = lanes.map((d) =>
      `<g class="pl-lane" data-lane="${esc(d)}">${byLane.get(d)!.join("")}</g>`).join("");

    const lineGs = lineage.map((e, i) => {
      const a = pos.get(e.source)!, b = pos.get(e.target)!;
      const back = b.x < a.x;                 // 시간을 거스르는 기록 — 지우지 않고 가른다
      const dx = b.x - a.x, dy = b.y - a.y;
      const len = Math.hypot(dx, dy) || 1;
      const bow = Math.min(46, 12 + len * 0.16);
      const mx = (a.x + b.x) / 2 - (dy / len) * bow;
      const my = (a.y + b.y) / 2 + (dx / len) * bow;
      return `<path class="pl-lin${back ? " pl-lin-back" : ""}${grow ? " pl-grow" : ""}"`
        + ` pathLength="100" style="--d:${num(0.5 + 0.006 * i)}s"`
        + ` data-from="${esc(e.source)}" data-to="${esc(e.target)}"`
        + ` d="M${num(a.x)} ${num(a.y)} Q${num(mx)} ${num(my)} ${num(b.x)} ${num(b.y)}"/>`;
    }).join("");

    first = false;
    posOut = pos;
    // `pl-hov` 는 비워 둔 채 나간다 — 초점이 걸릴 때 paint 가 그 자리에 선을 채운다.
    // 점 **아래**에 두어 선이 마크의 hover·클릭을 가리지 않게 한다.
    return `<svg class="pl-svg pl-timeline pl-cols" aria-label="${esc(label)}"`
      + ` viewBox="0 0 ${num(W)} ${num(H)}" width="${num(W)}" height="${num(H)}">`
      + `${years}${axis}`
      + (lineGs ? `<g class="pl-lins">${lineGs}</g>` : "")
      + `<g class="pl-hov"></g>${laneGs}</svg>`;
  }

  const domOf = new Map(dated.map((n) => [n.id, laneKey(n)]));
  /** 이웃 표 — **실제로 이어진 것**만 관련이다. 같은 주제라는 것은 분류가 같을 뿐
   *  관련이 아니다(예전 판정이 그랬는데, 자리가 뜻을 잃은 이 형태에서는 그 오독이
   *  훨씬 커진다: 아무 관계 없는 200개가 함께 밝아진다). */
  const near = new Map<string, Set<string>>();
  for (const e of (o.related || [])) {
    if (!seen.has(e.source) || !seen.has(e.target) || e.source === e.target) continue;
    (near.get(e.source) || near.set(e.source, new Set()).get(e.source)!).add(e.target);
    (near.get(e.target) || near.set(e.target, new Set()).get(e.target)!).add(e.source);
  }
  let laneEls: SVGElement[] = [], nodeEls: SVGElement[] = [], linEls: SVGElement[] = [];
  const collect = () => {
    laneEls = [...el.querySelectorAll<SVGElement>(".pl-lane")];
    nodeEls = [...el.querySelectorAll<SVGElement>(".pl-node")];
    linEls = [...el.querySelectorAll<SVGElement>(".pl-lin")];
  };
  /** 클릭으로 **고정된** 초점. 호버는 잠깐 덮어쓰고, 손을 떼면 여기로 돌아온다 —
   *  그래서 짚어 둔 것을 잃지 않고 주변을 훑을 수 있다. */
  let pinned = "";
  /** 초점 = 자기와 이어진 것만 남고 나머지가 어두워진다(발주자 지시 2026-08-14).
   *  빈 문자열이면 전부 원래대로. */
  function paint(id: string) {
    const hov = el.querySelector<SVGGElement>(".pl-hov");
    if (!id) {
      if (hov) hov.innerHTML = "";
      for (const g of laneEls) g.classList.remove("pl-fade");
      for (const g of nodeEls) g.classList.remove("pl-hl", "pl-fade");
      for (const g of linEls) g.classList.remove("pl-hl", "pl-fade");
      return;
    }
    const kin = near.get(id);
    // 이어진 곳으로 선을 긋는다 — **짚은 하나 분량만**이라 스파게티가 될 수 없다.
    // 계보 오버레이가 기본 꺼짐인 이유가 "늘 그리면 스파게티"였는데, 그 문제는
    // 상시 표시의 문제지 선 자체의 문제가 아니었다. 여기서 그 구분을 쓴다.
    // 고정 표시 — **짚은 것과 잡아 둔 것은 다른 상태다.** 클릭은 초점을 고정하고
    // 다시 누르면 푸는데(아래 `open`), 여태 둘이 똑같이 그려져서 마우스를 치운 뒤에도
    // 남아 있는 강조가 "안 풀린 hover" 로 읽혔다. 마크에서 떨어진 고리 하나를 둘러
    // "잡혀 있다"를 말한다 — 마크 자체는 안 건드리므로 형태=종이 그대로 유지된다.
    // 지금 짚고 있는 것과 무관하게 **언제나** 그린다: 다른 데를 훑는 동안에도 무엇이
    // 잡혀 있는지 보여야 한다.
    const pin = pinned ? posOut.get(pinned) : undefined;
    const pinRing = pin
      ? `<circle class="pl-pinring" cx="${num(pin.x)}" cy="${num(pin.y)}" r="9"/>` : "";
    if (hov) {
      const a = posOut.get(id);
      hov.innerHTML = pinRing + (!a || !kin ? "" : [...kin].map((nid) => {
        const b = posOut.get(nid);
        if (!b) return "";
        const dx = b.x - a.x, dy = b.y - a.y;
        const len = Math.hypot(dx, dy) || 1;
        // 활의 크기는 거리에 비례하되 상한을 둔다 — 먼 것끼리도 서로 겹치지 않게.
        const bow = Math.min(54, 10 + len * 0.14);
        const mx = (a.x + b.x) / 2 - (dy / len) * bow;
        const my = (a.y + b.y) / 2 + (dx / len) * bow;
        return `<path class="pl-hovline" d="M${num(a.x)} ${num(a.y)}`
          + ` Q${num(mx)} ${num(my)} ${num(b.x)} ${num(b.y)}"/>`;
      }).join(""));
    }
    for (const g of laneEls) g.classList.remove("pl-fade");   // 물러남은 점 단위로만
    for (const g of nodeEls) {
      const nid = g.dataset.id || "";
      const on = nid === id;
      g.classList.toggle("pl-hl", on);
      g.classList.toggle("pl-fade", !on && !kin?.has(nid));
    }
    for (const g of linEls) {
      const on = g.dataset.from === id || g.dataset.to === id;
      g.classList.toggle("pl-hl", on);
      g.classList.toggle("pl-fade", !on);
    }
  }
  const focus = (id: string) => paint(domOf.has(id) ? id : pinned);
  const clear = () => paint(pinned);
  const stopSize = trackSize(el, (w, h) => {
    el.innerHTML = wrap(draw(w, h)); collect();
    if (pinned) paint(pinned);           // 다시 그려도 짚어 둔 것은 남는다
  });
  const stopFocus = wireFocus(el, {
    focus, clear,
    // ⚠ 클릭이 **더 깊이 들어가지 않는다**(발주자 지시 2026-08-14). 이 관점은 이미
    // 전량을 그리므로 파고들 곳이 없고, 파고들면 방금 짚은 그림이 사라진다.
    // 그래서 클릭 = 고정(다시 누르면 해제) + 상세 레일이다.
    open: (id) => { pinned = pinned === id ? "" : id; paint(pinned); o.onOpen(id); },
    month: o.onMonth ? (m) => o.onMonth!(m) : undefined,
  });
  return {
    dispose() { stopSize(); stopFocus(); el.innerHTML = ""; },
    focus(id: string) {
      const hit = el.querySelector<SVGElement>(`.pl-node[data-id="${CSS.escape(id)}"]`);
      if (!hit) return false;
      focus(id);
      hit.scrollIntoView({ block: "center", inline: "center" });
      return true;
    },
  };
}
