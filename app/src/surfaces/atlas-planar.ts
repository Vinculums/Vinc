import type { LedgerRow } from "../api";
import { COPY } from "../copy";
import {
  aggGaps, bucketOthers, lodeOffsets, orderLodes, renderPlan, OTHERS_KEY,
  type Lode,
} from "./atlas-lodes";

/** Atlas 평면 뷰 (D43) — Timeline · Sankey.
 *
 *  3D/2D 맵과 같은 시각 문법을 평면으로 옮긴다: 모노크롬 잉크가 정본이고
 *  의미는 형태 · 농도 · 굵기 · 위치가 나른다(색상환은 쓰지 않는다, D41).
 *  초점색(hsl(var(--ring)))은 딱 한 곳 — hover/고정된 리니지뿐이며, 그 외의
 *  어떤 마크도 색으로 구분되지 않는다.
 *
 *  의존성 0: 순수 DOM + 인라인 SVG(캔버스도 WebGL 도 아니다). 색은 전부 토큰
 *  참조(hsl(var(--…)))라 테마 전환에 자동으로 따라간다 — hex 하드코딩 금지.
 *  레이아웃 수치는 전부 입력 순서의 결정론(Math.random · 시간 의존 없음)이라
 *  같은 데이터는 언제 그려도 같은 그림이 된다(D17 좌표 철학의 평면판).
 *
 *  스타일 규칙(.pl-*)은 app.css 의 Atlas 절에 산다 — 템플릿 문자열 안에 숨은
 *  두 번째 스타일시트를 만들지 않는다(디자인 시스템 · 모노크롬 게이트가 훑는
 *  파일이 곧 규칙이 사는 파일이어야 한다).
 *
 *  화면 문구는 전부 copy.ts 덱(COPY.ui.atlasx)에서 온다. atlas3d.ts 가 COPY 를
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
export type Grain = "month" | "year" | "decade";

/** 고유 버킷 수가 화면에 담길 만큼일 때까지 알갱이를 물린다.
 *  월 48개(4년)·연 60개(60년)가 상한 — 그 이상은 밴드가 글자보다 좁아진다. */
export function pickGrain(list: string[]): Grain {
  const uniq = (n: number) => new Set(list.map((t) => t.slice(0, n))).size;
  if (uniq(7) <= 48) return "month";
  if (uniq(4) <= 60) return "year";
  return "decade";
}

export function bucketKey(ts: string, g: Grain): string {
  if (g === "month") return ts.slice(0, 7);
  if (g === "year") return ts.slice(0, 4);
  return `${ts.slice(0, 3)}0`;      // 190x → 1900
}

/** 버킷 키 → 반개구간 [start, end). 문자열 산술뿐이다(날짜 파싱 금지 — ts 계약).
 *  끝값은 "다음 버킷의 시작"이고, 자리수를 유지해야 사전식 비교가 성립한다. */
export function bucketRange(key: string, g: Grain): [string, string] {
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
const PAD_X = 16;      // .pl-wrap 좌우 패딩
const PAD_TOP = 56;    // 상단 크롬(크럼 · 툴 행) 회피
const PAD_BOT = 16;
/** 세로 스크롤바 폭 여유 — Windows 클래식 17px 을 덮는 상한. 오버레이 스크롤바
 *  (macOS)에서는 그냥 여백이 된다. 세로 타임라인만 쓴다(항상 세로로 넘치므로). */
const SBAR = 18;

// ── 문자열 조립 헬퍼 ───────────────────────────────────────────────────────
const esc = (s: string) => s.replace(/[&<>"]/g, (c) =>
  c === "&" ? "&amp;" : c === "<" ? "&lt;" : c === ">" ? "&gt;" : "&quot;");
/** 좌표 소수 1자리 — 문자열이 흔들리지 않게(결정론 + diff 가독). */
const num = (v: number) => String(Math.round(v * 10) / 10);
const cut = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1) + "…" : s);
/** 좌→우 수평 3차 베지어(층 사이 커넥터). */
const curveH = (x1: number, y1: number, x2: number, y2: number) => {
  const mx = (x1 + x2) / 2;
  return `M${num(x1)} ${num(y1)} C${num(mx)} ${num(y1)} ${num(mx)} ${num(y2)} ${num(x2)} ${num(y2)}`;
};
/** 정육각 꼭짓점 — 형태=타입(D35): 기록물 계열은 육각, 개념은 원. */
const hexPts = (cx: number, cy: number, r: number) => {
  const p: string[] = [];
  for (let i = 0; i < 6; i++) {
    const a = (Math.PI / 180) * (60 * i - 30);
    p.push(`${num(cx + r * Math.cos(a))},${num(cy + r * Math.sin(a))}`);
  }
  return p.join(" ");
};
const wrap = (svg: string) => `<div class="pl-wrap">${svg}</div>`;

/** 라벨 디클러터 — 앵커 y 를 최소 간격까지 밀어내되 원 위치에 최대한 붙인다.
 *  아래로 한 번, 위로 한 번 쓸어 양끝 쏠림을 없앤다(밀집 구간의 겹침 실측 대응). */
function declutter(anchors: number[], minSep: number): number[] {
  const items = anchors.map((y, i) => ({ i, y, ly: y }))
    .sort((p, q) => (p.y === q.y ? p.i - q.i : p.y - q.y));
  for (let k = 1; k < items.length; k++)
    if (items[k].ly - items[k - 1].ly < minSep) items[k].ly = items[k - 1].ly + minSep;
  for (let k = items.length - 2; k >= 0; k--)
    if (items[k + 1].ly - items[k].ly < minSep) items[k].ly = items[k + 1].ly - minSep;
  const out: number[] = [];
  for (const it of items) out[it.i] = it.ly;
  return out;
}

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
export function mountTimeline(el: HTMLElement,
  nodes: (PlanarNode & { ts?: string; kind?: string })[],
  o: { docs: boolean; labels: boolean; onOpen: (id: string) => void;
       /** 칸 클릭 → 그 구간의 항목(D45-V2 4단계). 세로축이라 브러시는 가로 띠
        *  하나 = 한 버킷이다(자유 픽셀 드래그 대신 이산). 넘기는 값은 버킷 키
        *  ("2026-05" · "1905" · "1900")이고, 구간 해석은 호출부가 한다. */
       onMonth?: (bucket: string) => void;
       /** 계보 선을 그릴 방향 관계. 없으면 겹치기가 꺼진 것과 같다. */
       lineage?: { source: string; target: string; type: string }[] }): PlanarHandle {
  const pool = timelinePool(nodes, o.docs);
  // 날짜 없는 노드는 축에 올리지 않는다(빈 ts 는 정상값 — 상류가 자주 비운다).
  const dated = pool.filter((n) => !!(n.ts && n.ts.trim()))
    .slice()
    .sort((a, b) => (a.ts! < b.ts! ? -1 : a.ts! > b.ts! ? 1 : a.id < b.id ? -1 : 1));
  // **버킷 알갱이 적응(D72)** — 내용 시점 축은 범위가 세기 단위로 벌어져 월 밴드가
  // 수백 개가 된다. 고유 버킷 수를 보고 월 → 연 → 십년으로 물러난다. 자르기가 아니라
  // 접기라 항목은 하나도 빠지지 않는다. 키는 전부 ts 의 **접두 문자열**이라 파싱이
  // 없고(사전식 = 시간순 계약 유지), 십년만 넷째 자리를 0 으로 눌러 만든다.
  const grain = pickGrain(dated.map((n) => n.ts!));
  const bucketOf = (ts: string) => bucketKey(ts, grain);
  // 주제 없는 항목(브릿지 등)도 가지 하나를 갖되 이름을 붙인다 — 무명 가지는
  // "이게 뭐지"로 끝난다(U6). 정렬은 이름순이되 미분류는 늘 마지막.
  const laneKey = (n: { domain: string }) => n.domain || "";
  const lanes = [...new Set(dated.map(laneKey))]
    .sort((a, b) => (a === "" ? 1 : b === "" ? -1 : a < b ? -1 : 1));
  const laneAt = new Map(lanes.map((d, i) => [d, i]));
  const laneName = (d: string) => (d || COPY.ui.atlasx.unfiled);
  const months = [...new Set(dated.map((n) => bucketOf(n.ts!)))];   // 정렬본 → 이미 오름차순
  const monthAt = new Map(months.map((m, i) => [m, i]));
  // 같은 버킷·같은 가지 안 순번 — 세로로 흩어 겹침을 없앤다.
  const slot = new Map<string, number>();
  const per = new Map<string, number>();
  for (const n of dated) {
    const k = `${bucketOf(n.ts!)}|${laneKey(n)}`;
    const i = per.get(k) || 0;
    slot.set(n.id, i);
    per.set(k, i + 1);
  }
  const label = COPY.ui.atlasx.timelineChart(dated.length, lanes.length);
  // 계보 선(D74) — **양 끝이 다 축에 올라와 있는 관계만** 그린다. 한쪽이 날짜가
  // 없거나 문서 토글로 걸러졌으면 그 선은 갈 곳이 없다(허공으로 뻗는 선은 거짓이다).
  // 자기 자신으로 도는 관계도 뺀다 — 활이 점 하나로 눌려 아무 것도 못 말한다.
  const seen = new Map(dated.map((n) => [n.id, n]));
  const lineage = (o.lineage || [])
    .filter((e) => e.source !== e.target && seen.has(e.source) && seen.has(e.target));
  // 가지 하나의 가로 폭 = 마크 + 오른쪽 라벨. 이상폭과 최소폭 사이에서 화면에
  // 맞춰 좁아지고, 라벨 글자수가 그 실폭에서 파생된다(아래 labChars).
  const BRANCH_W = 168;       // 이상폭 — 한글 14자 라벨이 옆 줄기를 안 넘는 선
  const BRANCH_MIN = 112;     // 이보다 좁히면 마크와 라벨이 서로를 먹는다
  const GUT = 78;             // 왼쪽 시간 눈금(버킷 이름)이 앉는 자리
  const ROOT_Y = 26;          // 뿌리 머리(시작 시점 한 점)
  const STEM = 78;            // 뿌리에서 가지가 갈라져 내려앉기까지
  const MONTH_MIN = 84;       // 성긴 구간도 이만큼은 차지한다
  const SLOT_H = 30;          // 한 구간 안에서 항목 하나가 요구하는 세로 폭
  let first = true;           // 진입 안무는 최초 1회(리사이즈 재그리기는 조용히)

  function draw(w0: number, h: number): string {
    // trackSize 가 재는 것은 캔버스(el)이고 스크롤바는 그 **자식**(.pl-wrap)에 달린다
    // — el 은 스크롤바만큼 좁아지지 않으므로 w0 를 그대로 쓰면 viewBox 가 실제 내용
    // 상자보다 늘 스크롤바 폭만큼 넓어 가로 스크롤이 딸려 온다(실측 10px). 세로
    // 타임라인은 **설계상 항상** 세로로 넘치므로 그 폭을 처음부터 뺀다.
    const w = w0 - SBAR;
    const bot = 28;
    const headY = ROOT_Y + STEM;                    // 가지가 아래로 자라기 시작하는 y
    // 구간 높이는 그 구간의 **가장 붐비는 가지**가 요구하는 만큼(비례).
    // 넘치면 세로 스크롤 — 스크롤이 곧 시간이라 넘치는 것이 정상 상태다.
    const need = months.map((m) => {
      const maxPer = Math.max(1, ...lanes.map((d) => per.get(`${m}|${d}`) || 0));
      return Math.max(MONTH_MIN, maxPer * SLOT_H);
    });
    const sum = need.reduce((a, v) => a + v, 0);
    const H = Math.max(h, headY + sum + bot);
    const k = Math.max(1, (H - headY - bot) / Math.max(sum, 1));
    const rowH = need.map((v) => v * k);
    const rowY: number[] = [];
    rowH.reduce((y, v, i) => { rowY[i] = y; return y + v; }, headY);
    // 가로는 **맞춤 우선**이다: 가지가 여럿이면 이상폭(BRANCH_W)에서 최소폭까지
    // 좁혀 화면에 담고, 그래도 안 담길 때만 가로 스크롤을 낸다. 세로가 시간이므로
    // 가로 스크롤은 "시간을 보려다 실수로 옆으로 미는" 사고를 만든다 — 마지막 수단.
    const avail = Math.max(w - GUT - 40, 240);
    const colW = Math.max(BRANCH_MIN, Math.min(BRANCH_W, avail / Math.max(lanes.length, 1)));
    const W = Math.max(w, GUT + colW * lanes.length + 40);
    // 라벨 길이는 실제 칸 폭에서 나온다(고정 상수는 좁은 칸에서 옆 줄기를 넘는다).
    // 한 글자 ≈ 10px 로 잡는다 — 한글 기준으로 살짝 낙관, 라틴 기준으론 넉넉.
    const labChars = Math.max(6, Math.floor((colW - 26) / 10));
    const laneX = (d: string) => GUT + ((laneAt.get(d) || 0) + 0.5) * colW;
    // 줄기는 가지 다발의 한가운데를 지난다 — 뿌리가 한쪽으로 쏠리지 않게.
    const rootX = GUT + (colW * lanes.length) / 2;
    const yOf = (n: { id: string; ts?: string; domain: string }) => {
      const i = monthAt.get(bucketOf(n.ts!)) || 0;
      const m = `${bucketOf(n.ts!)}|${laneKey(n)}`;
      const cnt = Math.max(per.get(m) || 1, 1);
      return rowY[i] + (rowH[i] * (slot.get(n.id)! + 0.5)) / cnt;
    };
    /** 뿌리 자람 지연 — 위(과거)가 먼저, 아래(현재)가 나중. 깊이에 비례한 지연이
     *  "뻗어 내려가는" 인상을 만든다. 상한 1.1s 를 둬 마지막 마디가 하염없이
     *  기다리지 않게 한다(항목이 수백 개여도 안무가 1.5초 안에 끝난다). */
    const delay = (y: number) =>
      num(Math.min(1.1, 0.22 + ((y - headY) / Math.max(H - headY, 1)) * 1.1));

    // 시간 눈금 — 구간마다 가로 띠(홀수만 옅게, 격자선 억제) + 왼쪽에 버킷 이름.
    // 클릭 표적은 전 폭 투명 rect 라 빈 곳을 눌러도 그 구간이 열린다(점 클릭 우선 —
    // 마크가 DOM 상 뒤에 온다).
    const bands = months.map((m, i) =>
      (i % 2 === 1
        ? `<rect class="pl-band" x="${num(GUT - 12)}" y="${num(rowY[i])}"
             width="${num(W - GUT - 4)}" height="${num(rowH[i])}"/>` : "")
      + (o.onMonth
        ? `<rect class="pl-band-hit" data-month="${esc(m)}" x="${num(GUT - 12)}" y="${num(rowY[i])}"`
          + ` width="${num(W - GUT - 4)}" height="${num(rowH[i])}" fill="transparent"`
          + ` style="cursor:pointer"/>` : "")
      + `<text class="pl-cap pl-num" x="${num(GUT - 22)}" y="${num(rowY[i] + 14)}"
           text-anchor="end">${esc(m)}</text>`).join("");

    // 뿌리 머리 + 갈라지는 목. pathLength 를 100 으로 선언해 두면 dasharray/offset 이
    // 실제 길이와 무관한 100 단위가 된다 — 곡선 길이를 재지 않고도 정확히 자란다.
    const grow = first ? " pl-grow" : "";
    const neck = lanes.map((d, i) => {
      const x = laneX(d);
      return `<path class="pl-lane-line pl-neck${grow}" pathLength="100" stroke-width="1.6"`
        + ` style="--d:${num(0.04 * i)}s"`
        + ` d="M${num(rootX)} ${num(ROOT_Y)} C${num(rootX)} ${num(ROOT_Y + STEM * 0.55)}`
        + ` ${num(x)} ${num(headY - STEM * 0.55)} ${num(x)} ${num(headY)}"/>`;
    }).join("");
    const head = `<circle class="pl-root${grow}" cx="${num(rootX)}" cy="${num(ROOT_Y)}" r="4.5"/>`
      + `<text class="pl-cap pl-halo" x="${num(rootX)}" y="${num(ROOT_Y - 11)}"`
      + ` text-anchor="middle">${esc(months[0] || "")}</text>`;

    const laneGs = lanes.map((d) => {
      const list = dated.filter((n) => laneKey(n) === d);
      const x = laneX(d);
      const ys = list.map(yOf);
      // 가지 굵기 = 밀도(개수). 굵기이지 색이 아니다.
      const sw = list.length >= 8 ? 2.4 : list.length >= 4 ? 1.8 : 1.2;
      const endY = ys.length ? Math.max(...ys) + 12 : headY;
      const line = `<path class="pl-lane-line${grow}" pathLength="100" stroke-width="${sw}"`
        + ` style="--d:${num(0.2)}s"`
        + ` d="M${num(x)} ${num(headY)} L${num(x)} ${num(endY)}"/>`;
      // 가지 이름은 목이 내려앉는 자리 바로 위에 선다. 목 곡선이 글자를 관통하므로
      // 배경색 후광(.pl-halo)으로 도려낸다 — 판(plate)은 getBBox 실측이 필요한데
      // 후광은 CSS 한 줄이면 되고 결과가 같다.
      const nameY = headY - 26;
      const headTxt = `<text class="pl-sub pl-halo" x="${num(x)}" y="${num(nameY)}"
          text-anchor="middle">${esc(cut(laneName(d), 16))}</text>`
        + `<text class="pl-cap pl-num pl-halo" x="${num(x)}" y="${num(nameY + 13)}"
          text-anchor="middle">${list.length}</text>`;
      // 라벨은 마크 오른쪽에 세우고, 세로로 겹치면 밀어낸다(디클러터의 세로판).
      // ⚠ 밀도가 임계를 넘으면 **아예 달지 않는다**: 밀어내기로 풀리지 않는 밀집에서
      // 글자가 서로를 먹는다(D62 실측). 그때는 점만 남기고 이름은 hover 툴팁이 답한다.
      const gap = ys.length > 1
        ? (Math.max(...ys) - Math.min(...ys)) / (ys.length - 1) : Infinity;
      const showLabels = o.labels && gap >= 20;
      const ly = declutter(ys, 14);
      const marks = list.map((n, i) => {
        const y = ys[i], lyi = ly[i];
        const doc = DOC_KINDS.includes(n.kind || "");
        const mark = doc
          ? `<polygon class="pl-mark pl-soft" points="${hexPts(x, y, 5)}"/>`
          : `<circle class="pl-mark" cx="${num(x)}" cy="${num(y)}" r="5.2"/>`;
        const lead = showLabels && Math.abs(lyi - y) > 1.5
          ? `<path class="pl-ax" d="M${num(x + 8)} ${num(y)} L${num(x + 16)} ${num(lyi)}"/>` : "";
        const text = showLabels
          ? `<text class="pl-lab" x="${num(x + 20)}" y="${num(lyi + 4)}">${esc(cut(n.label, labChars))}</text>` : "";
        return `<g class="pl-node${grow}" style="--d:${delay(y)}s"`
          + ` data-id="${esc(n.id)}" data-tip="${esc(n.label)} ${esc(n.ts || "")}">`
          + `${mark}${lead}${text}</g>`;
      }).join("");
      return `<g class="pl-lane" data-lane="${esc(d)}">${line}${headTxt}${marks}</g>`;
    }).join("");

    // 원인 → 결과 선. **가지 아래**(DOM 먼저)에 깔아 마크의 hover·클릭을 안 가린다.
    // 두 마디가 같은 가지면 그 줄기를 따라 붙는 짧은 활, 다른 가지면 옆으로 부푸는
    // 큰 활 — 부푸는 정도가 "얼마나 멀리서 온 원인인가"를 그대로 말한다.
    const lineGs = lineage.map((e, i) => {
      const a = seen.get(e.source)!, b = seen.get(e.target)!;
      const ax = laneX(laneKey(a)), ay = yOf(a);
      const bx = laneX(laneKey(b)), by = yOf(b);
      // 시간을 거스르는 기록(원인이 결과보다 나중)은 지우지 않고 **점선으로 가른다**.
      const back = by < ay;
      const dx = bx - ax, dy = by - ay;
      // 제어점을 수직 이등분선 쪽으로 밀어 활을 만든다. 같은 가지(dx=0)면 옆으로,
      // 다른 가지면 진행 방향에 수직으로 — 어느 쪽이든 직선 두 개가 겹치지 않는다.
      const len = Math.hypot(dx, dy) || 1;
      const bow = Math.min(46, 12 + len * 0.16) * (dx === 0 ? 1 : Math.sign(dx) || 1);
      const mx = (ax + bx) / 2 - (dy / len) * bow;
      const my = (ay + by) / 2 + (dx / len) * bow;
      return `<path class="pl-lin${back ? " pl-lin-back" : ""}${grow ? " pl-grow" : ""}"`
        + ` pathLength="100" style="--d:${num(0.5 + 0.006 * i)}s"`
        + ` data-from="${esc(e.source)}" data-to="${esc(e.target)}"`
        + ` d="M${num(ax)} ${num(ay)} Q${num(mx)} ${num(my)} ${num(bx)} ${num(by)}"/>`;
    }).join("");

    return `<svg class="pl-svg pl-timeline" aria-label="${esc(label)}"`
      + ` viewBox="0 0 ${num(W)} ${num(H)}" width="${num(W)}" height="${num(H)}">`
      + `${bands}${neck}${head}`
      + (lineGs ? `<g class="pl-lins">${lineGs}</g>` : "")
      + `${laneGs}</svg>`;
  }

  const domOf = new Map(dated.map((n) => [n.id, laneKey(n)]));
  // 계보 이웃 — 한 홉짜리 원인·결과. 초점이 걸리면 이들만 남기고 나머지가 물러난다.
  const kin = new Map<string, Set<string>>();
  for (const e of lineage) {
    (kin.get(e.source) || kin.set(e.source, new Set()).get(e.source)!).add(e.target);
    (kin.get(e.target) || kin.set(e.target, new Set()).get(e.target)!).add(e.source);
  }
  let laneEls: SVGElement[] = [];
  let nodeEls: SVGElement[] = [];
  let linEls: SVGElement[] = [];
  const collect = () => {
    laneEls = [...el.querySelectorAll<SVGElement>(".pl-lane")];
    nodeEls = [...el.querySelectorAll<SVGElement>(".pl-node")];
    linEls = [...el.querySelectorAll<SVGElement>(".pl-lin")];
  };
  /** 계보를 켜면 "관련"의 뜻이 바뀐다: 같은 가지(주제)가 아니라 **원인·결과로
   *  이어진 것**이다. 그게 이 겹치기의 요점이다 — 주제가 달라도 원인이면 남는다.
   *  계보가 없으면(꺼짐·방향 관계 0) 예전대로 같은 가지가 관련이다. */
  function focus(id: string) {
    const d = domOf.get(id);
    if (d === undefined) { clear(); return; }
    const near = kin.get(id);
    if (linEls.length && near) {
      for (const g of laneEls) g.classList.remove("pl-fade");
      for (const g of nodeEls) {
        const nid = g.dataset.id || "";
        g.classList.toggle("pl-hl", nid === id);
        g.classList.toggle("pl-fade", nid !== id && !near.has(nid));
      }
      for (const g of linEls) {
        const on = g.dataset.from === id || g.dataset.to === id;
        g.classList.toggle("pl-hl", on);
        g.classList.toggle("pl-fade", !on);
      }
      return;
    }
    for (const g of laneEls) g.classList.toggle("pl-fade", g.dataset.lane !== d);
    for (const g of nodeEls) g.classList.toggle("pl-hl", g.dataset.id === id);
    for (const g of linEls) g.classList.add("pl-fade");
  }
  function clear() {
    for (const g of laneEls) g.classList.remove("pl-fade");
    for (const g of nodeEls) { g.classList.remove("pl-fade"); g.classList.remove("pl-hl"); }
    for (const g of linEls) { g.classList.remove("pl-fade"); g.classList.remove("pl-hl"); }
  }

  const stopSize = trackSize(el, (w, h) => { el.innerHTML = wrap(draw(w, h)); collect(); });
  // 첫 동기 그리기는 아직 레이아웃 전 크기로 나가고 곧 ResizeObserver 가 실제 크기로
  // 다시 그린다 — `first` 를 그리기 콜백 안에서 내리면 방금 시작한 안무가 지워진다
  // (mountTotals 에서 실측된 함정). 레이아웃이 앉는 더블 rAF 뒤에 내린다.
  const settle = requestAnimationFrame(() =>
    requestAnimationFrame(() => { first = false; }));
  const stopFocusT = wireFocus(el, { focus, clear, open: o.onOpen, month: o.onMonth });
  return {
    dispose() { cancelAnimationFrame(settle); stopFocusT(); stopSize(); },
    focus(id) {
      if (!domOf.has(id)) return false;
      focus(id);
      // 세로로 긴 차트라 강조만으로는 화면 밖에 있을 수 있다 — 그 마크를 가운데로.
      el.querySelector<SVGElement>(`.pl-node[data-id="${CSS.escape(id)}"]`)
        ?.scrollIntoView({ block: "center", inline: "nearest" });
      return true;
    },
  };
}

// ── 3. Totals — 지식은 어디서 와서 어디에 쌓이고 어떤 상태인가 ─────────────
/** 3열 생키(정본 [[vinc.d.graph-views]] §2.3 복귀, D47).
 *
 *  열: **유입원 → 주제 → 연결 상태**. 구 구현은 한 축만 고르는 누적 막대라
 *  "대화에서 온 개념이 어느 주제에 쌓였고 그중 몇이 고아인가"를 경로로 추적할
 *  수 없었다 — 생키의 존재 이유가 그 경로다.
 *
 *  **보존 불변**: 사이드카 폴드가 출처 3분할·연결 3분할을 각각 배타로 세므로
 *  (query.py `ledger`) 두 합이 모두 정확히 `concepts` 다. 따라서 1열 합 = 2열 합
 *  = 3열 합이 항상 성립하고, 어긋나면 그건 집계 결함이라 화면이 먼저 드러낸다
 *  (노드 높이를 두 합의 큰 쪽으로 잡아 리본이 노드를 넘치지 않게만 방어한다).
 *
 *  **고아 필터를 여기서는 받지 않는다**: 고아를 숨기려면 (출처 × 상태) 교차표가
 *  필요한데 원장은 주변합만 준다 — 주변합으로 고아를 걷어내면 1열과 3열의 합이
 *  달라져 보존이 깨진다. 게다가 고아 리본은 D32 고아 최소화 동선의 진입점이라
 *  숨기는 것이 이 뷰의 목적과 반대다. 교차표가 필요해지면 `/ledger` 확장(별건).
 *
 *  색은 여전히 단일 잉크다 — bklit 의 소스→타깃 그라디언트는 모노크롬 규율
 *  (정본 §5 검증기 FAIL)에 걸려 채택하지 않고, 위계는 굵기와 농도만 나른다. */
const ORIGINS = ["episodic", "record", "unsourced"] as const;
const STATES = ["connected", "gateway", "orphan"] as const;
const val = (r: LedgerRow, k: string) => Math.max(0, Number((r as unknown as Record<string, unknown>)[k]) || 0);

/** 경로 보존 튜플(D63) — 개념 하나의 (출처, 주제, 상태) 조합과 그 조합의 개념 수.
 *  인접 교차 리본은 2열에서 출처 정보를 섞어 버려 "대화에서 온 X 주제 개념이
 *  고아인가"를 추적할 수 없었다 — 이 튜플이 리본을 끝까지 가른다. */
export type TotalsPath = { origin: string; domain: string; state: string; n: number };

/** 개념 알갱이 원장 행(by=concept, 마크 0/1)을 주제 알갱이 행 + 경로 튜플로 접는다.
 *  행 접기는 사이드카의 by=domain 롤업과 같은 규칙(주제별 합산, concepts 내림차순 ·
 *  이름 오름차순) — 노드 값은 종전과 같고 리본만 교차표를 얻는다. 각 축에서 마크가
 *  정확히 하나 1 인 배타 분할(query._concept_in_mark)이 전제라, 접은 행의 축 합과
 *  튜플 합이 모두 개념 수로 보존된다(보존 불변 유지). */
export function foldLedgerPaths(rows: LedgerRow[]): { rows: LedgerRow[]; paths: TotalsPath[] } {
  const agg = new Map<string, LedgerRow>();
  const tup = new Map<string, TotalsPath>();
  for (const r of rows) {
    const topic = r.topic || "";
    if (!topic) continue;                    // 주제 없는 행은 롤업 키가 없다(백엔드 동형)
    const origin = ORIGINS.find((k) => val(r, k) > 0) || "unsourced";
    const state = STATES.find((k) => val(r, k) > 0) || "orphan";
    const a = agg.get(topic) || {
      domain: topic, concepts: 0, episodic: 0, record: 0, unsourced: 0,
      connected: 0, gateway: 0, orphan: 0,
    } as LedgerRow;
    a.concepts += 1;
    (a as unknown as Record<string, number>)[origin] += 1;
    (a as unknown as Record<string, number>)[state] += 1;
    agg.set(topic, a);
    const key = `${origin}|${topic}|${state}`;
    const t = tup.get(key) || { origin, domain: topic, state, n: 0 };
    t.n += 1;
    tup.set(key, t);
  }
  const folded = [...agg.values()]
    .sort((a, b) => (b.concepts - a.concepts) || (a.domain < b.domain ? -1 : 1));
  return { rows: folded, paths: [...tup.values()] };
}
/** 생키 치수 — bklit 규격(nodeWidth 16 · nodePadding 24 · lineCap 4)을 그대로 쓴다. */
const NODE_W = 16, NODE_PAD = 24, NODE_CAP = 4;
/** 가장 작은 노드도 이만큼은 보인다(스케일 하한 → 넘치면 H 가 자라고 스크롤). */
const NODE_MIN = 5;
/** 가장 얇은 가닥도 이만큼은 보인다(D68) — 노드 하한과 같은 방식으로 H 를 키워
 *  얻는다(스케일이 전역 선형이라 비례·보존이 유지된다). 단 절대 캡 안에서만:
 *  캡을 넘기는 요구는 renderPlan 이 갭 집계로 강등해 해소하고, 그래도 남는
 *  초과는 종전 1px clamp 가 받친다(현행과 같은 ≤1px 초과 묘화). */
const RIB_MIN = 2;
/** RIB_MIN 이 요구할 수 있는 캔버스 높이의 절대 상한(px). 컨테이너 높이의
 *  배수로 잡으면 강등 판정이 리사이즈의 함수가 되어 창을 늘릴 때 모드가 튄다 —
 *  절대값이라 데이터만이 모드를 정한다. */
const H_RIB_CAP = 2200;
/** 열 머리글·총계 띠 높이 — w·h 와 무관해 mount 시점 계산(needNode·needRib)도
 *  쓴다. draw 안에 두면 배치 파이프라인이 값을 못 본다. */
const TOP = 48, BOT = 34;

export function mountTotals(el: HTMLElement, rows: LedgerRow[],
  o: { current?: string;
       onOpen: (domain: string) => void;
       /** 리본 클릭 → 그 (도메인, mark) 의 개념 목록 드릴다운(D45-V2 3단계).
        *  구 누적 막대의 구간 클릭과 계약이 같다 — 리본이 곧 그 구간이다.
        *  경로 보존 가닥도 data-dom·data-seg 를 그대로 실어 계약이 안 바뀐다. */
       onSeg?: (domain: string, mark: string) => void;
       /** 행의 알갱이(D52·D59) — "domain" 이면 한 행이 주제, "concept" 이면 개념
        *  하나다. 캡션의 단위가 이 값으로 갈린다(개념 40개를 "주제 40개"라 부르던
        *  단위 거짓말을 끝낸다, U5). */
       grain?: "domain" | "concept";
       /** 경로 보존 튜플(D63, :func:`foldLedgerPaths`) — 있으면 리본을 인접 교차
        *  대신 (출처×주제×상태) 조합별 가닥으로 그린다. 없으면(구 사이드카 강등 ·
        *  개념 알갱이) 종전 인접 교차 그대로. */
       paths?: TotalsPath[];
       /** 지금 가리키는 주제(D70) — `기타` 병합에서 빼고 초점 표시를 준다. 좌측
        *  트리·검색으로 짚어 온 주제가 화면에서 삼켜져 있으면 안 되기 때문이다. */
       pin?: string }): PlanarHandle {
  // 주제(2열) — 두 축 합이 다르면(구버전·집계 결함) 큰 쪽을 노드 높이로 삼아
  // 리본이 노드를 넘치지 않게만 방어한다. 정상 데이터에선 둘이 같다.
  let data = rows.map((r) => {
    const origin = ORIGINS.map((k) => val(r, k));
    const state = STATES.map((k) => val(r, k));
    const oSum = origin.reduce((a, b) => a + b, 0);
    const sSum = state.reduce((a, b) => a + b, 0);
    // D52 — 개념 알갱이에서는 `domain` 이 개념 id 이고 화면 라벨은 `label`(제목)이다.
    // 키와 라벨을 분리해 둬야 클릭 표적(id)과 읽는 글자(제목)가 어긋나지 않는다.
    return { domain: r.domain, label: r.label || r.domain,
             origin, state, total: Math.max(oSum, sSum) };
  }).filter((r) => r.total > 0);
  // 개념 알갱이 흐름 정렬(D68) — 행마다 개념 하나(lode 각 1개)라 stratum 안
  // 재정렬이 무의미하고, 행 순서 자체가 갭 교차를 전부 결정한다. 종전 알파벳순은
  // 갭 양끝(출처·상태) 순서와 무관해 교차를 양산했다 — (출처, 상태, 제목)으로
  // 묶으면 같은 흐름끼리 모여 교차가 실제로 준다(발주자 채택 2026-07-28).
  // 이름 탐색은 검색·목록 표면의 몫이라 여기서 잃는 것이 없다.
  if (o.grain === "concept") {
    const mark = (xs: number[]) => { const i = xs.findIndex((v) => v > 0); return i < 0 ? xs.length : i; };
    const low = (s: string) => s.toLowerCase();
    data = [...data].sort((a, b) => mark(a.origin) - mark(b.origin)
      || mark(a.state) - mark(b.state)
      || (low(a.label) < low(b.label) ? -1 : low(a.label) > low(b.label) ? 1 : 0)
      || (a.domain < b.domain ? -1 : a.domain > b.domain ? 1 : 0));
  }
  const topicCount = data.length;   // 기타 병합 전 — aria 라벨은 실제 주제 수를 밝힌다
  const grand = data.reduce((a, r) => a + r.total, 0);
  // 경로 보존 튜플(D63) — 화면에 없는 주제(total 0 필터)로 가는 튜플은 버린다.
  // 인덱스로 바꿔 두면 정렬·오프셋 누적이 전부 정수 비교다.
  const dAt = new Map(data.map((r, i) => [r.domain, i]));
  let tuples: Lode[] = (o.paths || [])
    .map((p) => ({ oi: ORIGINS.indexOf(p.origin as typeof ORIGINS[number]),
                   di: dAt.get(p.domain) ?? -1,
                   si: STATES.indexOf(p.state as typeof STATES[number]), n: p.n }))
    .filter((t) => t.oi >= 0 && t.di >= 0 && t.si >= 0 && t.n > 0);
  // 기타 병합(D68 Stage 1) — 작은 주제를 말미 stratum 하나로 통병합한다. 주제
  // 알갱이에서만: 개념 알갱이는 전 행이 크기 1 이라 몫 임계가 무의미하고, 병합은
  // 개념 드릴다운의 존재 이유를 부순다. 주제 단위 통병합이라 주변합·보존 불변은
  // 저절로 유지된다(bucketOthers 계약).
  let othersMerged = 0;
  if (o.grain !== "concept") {
    // pin(D70) — 지금 가리키고 있는 주제는 `기타` 에 삼켜지지 않는다.
    const b = bucketOthers(data, tuples, grand,
      o.pin ? { keep: new Set([o.pin]) } : {});
    if (b.mergedTopics) {
      data = b.data;
      tuples = b.tuples;
      othersMerged = b.mergedTopics;
      data[data.length - 1].label = COPY.ui.atlasx.othersLabel(b.mergedTopics);
    }
  }
  // 1열·3열 노드 값 = 주제를 가로지른 합(주변합). 기타 병합 뒤에 세지만 값은
  // 병합 전과 같다(주제 통병합은 주변합을 바꾸지 않는다).
  const originTot = ORIGINS.map((_, i) => data.reduce((a, r) => a + r.origin[i], 0));
  const stateTot = STATES.map((_, i) => data.reduce((a, r) => a + r.state[i], 0));
  const label = (o.grain === "concept"
    ? COPY.ui.atlasx.totalsChartConcepts : COPY.ui.atlasx.totalsChart)(topicCount, grand);
  const segName = (k: string) => COPY.ui.atlasx.seg[k as keyof typeof COPY.ui.atlasx.seg];
  const segTip = (k: string, v: number) => COPY.ui.atlasx.segTip(segName(k), v);

  // ── 배치 파이프라인(D68) — 전부 데이터의 순수 함수라 mount 에서 1회만 계산하고
  // draw(w,h) 는 소비만 한다. 픽셀(h)에 묶으면 리사이즈가 순서·모드를 재셔플한다.
  const counts = [ORIGINS.length, data.length, STATES.length];
  const maxCount = Math.max(...counts);
  const pads = (maxCount - 1) * NODE_PAD;
  const vals = [...originTot, ...data.map((r) => r.total), ...stateTot].filter((v) => v > 0);
  const minVal = vals.length ? Math.min(...vals) : 1;
  const minTup = tuples.length ? Math.min(...tuples.map((t) => t.n)) : 1;
  // 강등 판정(D68 Stage 2) — 경로 가닥이 상한(가닥 수 40)을 넘거나 절대 캡 안에서
  // 전 가닥 RIB_MIN 이 불가능하면 갭 집계로 물러난다. 판정 입력이 전부 데이터라
  // 결과도 데이터의 함수다.
  const plan = tuples.length
    ? renderPlan(tuples.length, TOP + BOT + pads + (grand * RIB_MIN) / minTup, H_RIB_CAP)
    : { mode: "path" as const, reason: "" as const };
  const gaps = plan.mode === "agg"
    ? aggGaps(tuples)
    : { left: [] as { oi: number; di: number; n: number }[],
        right: [] as { di: number; si: number; n: number }[] };
  // lode 배치(경로 모드) — 정렬이 곧 레이아웃이므로 자리는 여기서 확정된다.
  const ord = plan.mode === "path" && tuples.length
    ? orderLodes(tuples, ORIGINS.length, data.length, STATES.length)
    : null;
  const offs = ord ? lodeOffsets(tuples, ord) : null;
  /** 방출 순서 — 종전 의미 정렬 그대로(왼쪽 (출처,주제,상태) · 오른쪽 (주제,출처,
   *  상태)). y 는 배치표가 정하고 방출은 data-e·--i(진입 안무 지연)·DOM 겹침
   *  순서만 정한다 — 배치가 바뀌어도 안무·겹침의 diff 가 나지 않는다. */
  const emitL = tuples.map((_, i) => i).sort((a, b) =>
    tuples[a].oi - tuples[b].oi || tuples[a].di - tuples[b].di || tuples[a].si - tuples[b].si);
  const emitR = tuples.map((_, i) => i).sort((a, b) =>
    tuples[a].di - tuples[b].di || tuples[a].oi - tuples[b].oi || tuples[a].si - tuples[b].si);
  /** H 하한 둘 — 노드 하한(종전 그대로)과 가닥 하한(D68, 캡 안에서만). 실제 그릴
   *  가닥의 최솟값이 분모다: 경로=튜플 · 집계=갭 합 · 종전=주변 셀. */
  const cellMin = (xs: number[]) => {
    const p = xs.filter((v) => v > 0);
    return p.length ? Math.min(...p) : 1;
  };
  const minRib = plan.mode === "agg"
    ? cellMin([...gaps.left.map((g) => g.n), ...gaps.right.map((g) => g.n)])
    : tuples.length ? minTup
    : cellMin(data.flatMap((r) => [...r.origin, ...r.state]));
  const bot = BOT + (plan.mode === "agg" ? 14 : 0);   // 강등 캡션 한 줄 자리
  const needNode = TOP + bot + pads + (grand * NODE_MIN) / minVal;
  const needRib = TOP + bot + pads + (grand * RIB_MIN) / minRib;
  // 집계 hover 조인(D68) — 갭 키 ↔ 튜플로 닿는 반대편 갭 키. 집계 리본은 정적
  // 정체성을 잃는 대신 이 표가 hover 때 끝에서 끝을 복원한다.
  const gkJoin = new Map<string, Set<string>>();
  if (plan.mode === "agg") for (const t of tuples) {
    const l = `0:${t.oi}:${t.di}`, r = `1:${t.di}:${t.si}`;
    if (!gkJoin.has(l)) gkJoin.set(l, new Set());
    if (!gkJoin.has(r)) gkJoin.set(r, new Set());
    gkJoin.get(l)!.add(r);
    gkJoin.get(r)!.add(l);
  }
  /** 첫 그림에만 진입 안무를 얹는다 — 리사이즈 재그리기마다 다시 자라면 소음이다. */
  let first = true;

  function draw(w: number, h: number): string {
    const W = Math.max(w, 620);
    const labW = 136;                       // 좌우 라벨 여백(bklit margin 180 의 축약)
    // 높이 — 가장 작은 노드가 NODE_MIN, 가장 얇은 가닥이 RIB_MIN(절대 캡 안에서)은
    // 되게 키운다(넘치면 .pl-wrap 이 세로 스크롤). needNode·needRib 는 mount 의
    // 배치 파이프라인에서 왔다 — 캡이 눌러 둔 초과는 ribbon 의 1px clamp 가 받친다.
    const H = Math.max(h, needNode, Math.min(needRib, H_RIB_CAP));
    const availH = H - TOP - bot;
    const scale = (availH - pads) / Math.max(grand, 1);
    const xs = [labW, (W - NODE_W) / 2, W - labW - NODE_W];

    /** 열 하나를 세로 중앙에 쌓는다 — 값 순서는 입력 순서 그대로(결정론). */
    const stack = (values: number[]) => {
      const hsum = values.reduce((a, v) => a + v * scale, 0);
      const gaps = (values.length - 1) * NODE_PAD;
      let y = TOP + (availH - hsum - gaps) / 2;
      return values.map((v) => { const top = y; y += v * scale + NODE_PAD; return { y: top, h: v * scale }; });
    };
    const oBox = stack(originTot);
    const dBox = stack(data.map((r) => r.total));
    const sBox = stack(stateTot);

    // 리본 쌓기 오프셋 — 집계·종전 모드가 방출 순서대로 누적한다. 경로 보존
    // 모드는 쓰지 않는다: 자리가 mount 의 배치표(offs)에서 온다(D68).
    const oOff = originTot.map(() => 0);
    const dInOff = data.map(() => 0);
    const dOutOff = data.map(() => 0);
    const sOff = stateTot.map(() => 0);
    const links: string[] = [];
    let li = 0;
    // 농도 정규화 기준은 **리본 최댓값**이다. 예전엔 노드 최솟값×4 로 나눴는데,
    // 그 분모가 실제 리본 값 범위보다 커서 값이 6배 달라져도 농도가 0.32~0.50
    // 안에서만 움직였다 — 농도 채널이 사실상 아무것도 나르지 않았다(실측).
    // 경로 보존 모드에선 가닥이 더 잘게 쪼개지므로 기준도 튜플 최댓값이다.
    const ribMax = Math.max(1, ...(plan.mode === "agg"
      ? [...gaps.left.map((g) => g.n), ...gaps.right.map((g) => g.n)]
      : tuples.length
      ? tuples.map((t) => t.n)
      : data.flatMap((r) => [...r.origin, ...r.state])));
    const opOf = (v: number) => 0.16 + 0.5 * (v / ribMax);
    /** 채움 밴드 — 소스 상단 → 타깃 상단으로 흐르고 두께 t 만큼 되돌아와 닫는다.
     *  선(stroke) 리본이 아니라 면(fill)이라 **테두리를 줄 수 있다** — 겹친 리본이
     *  한 덩어리로 뭉개지지 않게 하는 유일한 방법(모노크롬에선 색으로 못 가른다). */
    const band = (x1: number, yTop1: number, x2: number, yTop2: number, t: number) => {
      const mx = (x1 + x2) / 2;
      const y1b = yTop1 + t, y2b = yTop2 + t;
      return `M${num(x1)} ${num(yTop1)} C${num(mx)} ${num(yTop1)} ${num(mx)} ${num(yTop2)} ${num(x2)} ${num(yTop2)}`
        + ` L${num(x2)} ${num(y2b)} C${num(mx)} ${num(y2b)} ${num(mx)} ${num(y1b)} ${num(x1)} ${num(y1b)} Z`;
    };
    const ribbon = (x1: number, yTop1: number, x2: number, yTop2: number, v: number,
                    dom: string, mark: string, tip: string, path = "", gk = "") => {
      const t = Math.max(v * scale, 1);
      // 값 비례 농도 — 색조는 건드리지 않는다. 굵기가 값의 정본이고 농도는 읽기
      // 보조다(정본 §5: 변별은 위치·두께·형태·직접 라벨).
      const op = opOf(v).toFixed(3);
      // 테두리는 밴드가 충분히 두꺼울 때만 — 1~2px 리본에 0.75px 테두리를 두르면
      // 밴드가 테두리에 먹혀 사라진다.
      const ol = t >= 3 ? 0.75 : 0;
      // data-dom·data-seg 는 **항상** 싣는다 — 드릴다운의 표적이자 hover 초점
      // 계산의 열쇠라, onSeg 유무로 빠지면 hover 문법까지 같이 죽는다.
      // data-path(D63) 는 경로 보존 가닥의 튜플 키 — 같은 키의 좌·우 가닥이 hover 에서
      // 한 물줄기로 밝아진다. data-gk(D68) 는 집계 가닥의 갭 키 — gkJoin 조인이
      // hover 때 반대편 갭까지 잇는다. 기타(OTHERS_KEY) 리본은 드릴다운 표적이
      // 없어 pl-drill(포인터 커서)을 받지 않는다.
      const drill = o.onSeg && dom !== OTHERS_KEY;
      const cls = `pl-ribbon${drill ? " pl-drill" : ""}${first ? " pl-in" : ""}`;
      links.push(`<g class="${cls}" data-e="${li}" data-dom="${esc(dom)}"`
        + ` data-seg="${esc(mark)}"${path ? ` data-path="${esc(path)}"` : ""}`
        + `${gk ? ` data-gk="${esc(gk)}"` : ""} style="--i:${li}">`
        + `<path class="pl-flow" d="${band(x1, yTop1, x2, yTop2, t)}"`
        + ` fill-opacity="${op}" stroke-width="${ol}"`
        + ` data-tip="${esc(tip)}"></path></g>`);
      li++;
    };

    if (ord && offs) {
      // ── 경로 보존(D63 → 배치 D68, alluvial) — 리본은 (출처×주제×상태) 튜플별
      // 가닥으로 끝까지 갈리고, 자리는 orderLodes 의 배치표가 정한다. 급소였던
      // **주제 노드 안 띠의 정합**(들어오는 쪽과 나가는 쪽이 같은 높이 띠)은 이제
      // 정렬 규칙 둘의 우연한 일치가 아니라 자료구조가 보증한다: 밴드 오프셋
      // (offs.offB)이 튜플당 하나뿐이라 좌·우 가닥이 같은 띠를 지날 수밖에 없다.
      // 교차 총수는 어떤 배치로도 변하지 않는다(atlas-lodes 머리 정리) — 배치가
      // 바꾸는 것은 교차의 양 갭 분산과 리본 기울기(방랑)다. 전부 입력 결정론.
      const pk = (t: Lode) => `${t.oi}|${t.di}|${t.si}`;
      const tipOf = (t: Lode) =>
        COPY.ui.atlasx.pathTip(segName(ORIGINS[t.oi]), data[t.di].label,
                               segName(STATES[t.si]), t.n);
      for (const i of emitL) {
        const t = tuples[i];
        ribbon(xs[0] + NODE_W, oBox[t.oi].y + offs.offL[i] * scale,
               xs[1], dBox[t.di].y + offs.offB[i] * scale, t.n,
               data[t.di].domain, ORIGINS[t.oi], tipOf(t), pk(t));
      }
      for (const i of emitR) {
        const t = tuples[i];
        ribbon(xs[1] + NODE_W, dBox[t.di].y + offs.offB[i] * scale,
               xs[2], sBox[t.si].y + offs.offR[i] * scale, t.n,
               data[t.di].domain, STATES[t.si], tipOf(t), pk(t));
      }
    } else if (plan.mode === "agg") {
      // ── 갭 집계(D68 Stage 2) — 경로 가닥이 상한(가닥 수·높이 캡)을 넘는 규모.
      // 리본을 갭별 (출처×주제)·(주제×상태) 합으로 접어 정적 가닥 수를 곱이 아니라
      // 합으로 억제한다. 이 방출·쌓기 순서는 갭별로 이미 교차 최적이다(양끝
      // stratum 순서와 일치 — 모드 B 와 같은 논증). 잃은 끝-끝 정체성은 data-gk
      // 조인이 hover 때 복원한다.
      for (const l of gaps.left) {
        const y1 = oBox[l.oi].y + oOff[l.oi];
        const y2 = dBox[l.di].y + dInOff[l.di];
        oOff[l.oi] += l.n * scale; dInOff[l.di] += l.n * scale;
        ribbon(xs[0] + NODE_W, y1, xs[1], y2, l.n, data[l.di].domain, ORIGINS[l.oi],
               COPY.ui.atlasx.ribbonTip(segName(ORIGINS[l.oi]), data[l.di].label, l.n),
               "", `0:${l.oi}:${l.di}`);
      }
      for (const r of gaps.right) {
        const y1 = dBox[r.di].y + dOutOff[r.di];
        const y2 = sBox[r.si].y + sOff[r.si];
        dOutOff[r.di] += r.n * scale; sOff[r.si] += r.n * scale;
        ribbon(xs[1] + NODE_W, y1, xs[2], y2, r.n, data[r.di].domain, STATES[r.si],
               COPY.ui.atlasx.ribbonTip(data[r.di].label, segName(STATES[r.si]), r.n),
               "", `1:${r.di}:${r.si}`);
      }
    } else {
      // ── 인접 교차(종전) — 튜플이 없을 때(구 사이드카 강등 · 개념 알갱이).
      // 개념 알갱이에선 행 하나가 개념 하나라 가닥이 이미 튜플이다 — 행 id 를
      // 경로 키로 실어 좌·우 가닥이 hover 에서 함께 밝아지게 한다(D63).
      const rowPk = (r: { domain: string }) => (o.grain === "concept" ? r.domain : "");
      ORIGINS.forEach((k, oi) => {
        data.forEach((r, di) => {
          const v = r.origin[oi];
          if (v <= 0) return;
          const y1 = oBox[oi].y + oOff[oi];
          const y2 = dBox[di].y + dInOff[di];
          oOff[oi] += v * scale; dInOff[di] += v * scale;
          ribbon(xs[0] + NODE_W, y1, xs[1], y2, v, r.domain, k,
                 COPY.ui.atlasx.ribbonTip(segName(k), r.label, v), rowPk(r));
        });
      });
      data.forEach((r, di) => {
        STATES.forEach((k, si) => {
          const v = r.state[si];
          if (v <= 0) return;
          const y1 = dBox[di].y + dOutOff[di];
          const y2 = sBox[si].y + sOff[si];
          dOutOff[di] += v * scale; sOff[si] += v * scale;
          ribbon(xs[1] + NODE_W, y1, xs[2], y2, v, r.domain, k,
                 COPY.ui.atlasx.ribbonTip(r.label, segName(k), v), rowPk(r));
        });
      });
    }

    /** 노드 사각 + 바깥쪽 직접 라벨(1·3열). 판(plate)은 2열에만 필요하다. */
    const sideNode = (id: string, x: number, box: { y: number; h: number },
                      key: string, v: number, right: boolean, i: number) => {
      if (v <= 0) return "";
      const ty = box.y + box.h / 2;
      const lx = right ? x + NODE_W + 10 : x - 10;
      return `<g class="pl-node pl-static${first ? " pl-in" : ""}" data-id="${esc(id)}" style="--i:${i}">`
        + `<rect class="pl-snode" x="${num(x)}" y="${num(box.y)}" width="${NODE_W}"`
        + ` height="${num(Math.max(box.h, 1))}" rx="${NODE_CAP}"`
        + ` data-tip="${esc(segTip(key, v))}"></rect>`
        + `<text class="pl-lab" x="${num(lx)}" y="${num(ty - 1)}"`
        + ` text-anchor="${right ? "start" : "end"}">${esc(cut(segName(key), 20))}</text>`
        + `<text class="pl-cap pl-num" x="${num(lx)}" y="${num(ty + 12)}"`
        + ` text-anchor="${right ? "start" : "end"}">${v}</text></g>`;
    };
    const originNodes = ORIGINS.map((k, i) =>
      sideNode(`org:${k}`, xs[0], oBox[i], k, originTot[i], false, i)).join("");
    const stateNodes = STATES.map((k, i) =>
      sideNode(`st:${k}`, xs[2], sBox[i], k, stateTot[i], true, i)).join("");

    // 2열(주제) — 라벨이 리본 위를 지나므로 판을 깔고 얹는다. 판 폭은 추정하지
    // 않는다: 여기선 자리만 잡고 마운트 후 getBBox() 실측으로 맞춘다(정본 §6
    // 실패 사례 1). 이미 그 주제를 보고 있는 행은 눌러도 갈 곳이 없어 정적이다.
    const domainNodes = data.map((r, i) => {
      // 기타(D68)는 클릭 표적이 아니다 — 병합 stratum 이라 열 곳도 드릴다운도
      // 없다. current 와 같은 pl-static 문법(정적·비클릭)을 재사용한다.
      const others = r.domain === OTHERS_KEY;
      const dead = r.domain === o.current || others;
      const tip = others
        ? COPY.ui.atlasx.othersTip(othersMerged, r.total)
        : COPY.ui.atlasx.segTip(r.label, r.total);
      const ty = dBox[i].y + dBox[i].h / 2;
      const lx = xs[1] + NODE_W + 9;
      return `<g class="pl-node${dead ? " pl-static" : ""}${first ? " pl-in" : ""}"`
        + ` data-id="${esc(r.domain)}"${dead ? "" : ` data-open="${esc(r.domain)}"`} style="--i:${i}">`
        + `<rect class="pl-snode" x="${num(xs[1])}" y="${num(dBox[i].y)}" width="${NODE_W}"`
        + ` height="${num(Math.max(dBox[i].h, 1))}" rx="${NODE_CAP}"`
        + ` data-tip="${esc(tip)}"></rect>`
        + `<g class="pl-plate-g">`
        + `<rect class="pl-plate" x="${num(lx - 5)}" y="${num(ty - 10)}" width="10" height="20" rx="3"/>`
        + `<text class="pl-lab" x="${num(lx)}" y="${num(ty + 4)}">${esc(cut(r.label, 18))}`
        + ` <tspan class="pl-sub pl-num">${r.total}</tspan></text></g></g>`;
    }).join("");

    const head = (x: number, anchor: string, t: string) =>
      `<text class="pl-cap" x="${num(x)}" y="26" text-anchor="${anchor}">${esc(t)}</text>`;
    const heads = head(xs[0] + NODE_W, "end", COPY.ui.atlasx.colOrigin)
      + head(xs[1] + NODE_W / 2, "middle", COPY.ui.atlasx.colTopic)
      + head(xs[2], "start", COPY.ui.atlasx.colState);

    // 총계 — 세 열의 합이 같음을 눈으로 확인시킨다(보존 불변의 화면판).
    // 집계 강등(D68) 때는 표현이 바뀐 사실을 한 줄로 밝힌다 — 조용한 표현 변경을
    // 만들지 않는다(capped 와 같은 규율). bot 여백이 이 줄 자리를 미리 벌려 뒀다.
    const grouped = plan.mode === "agg"
      ? `<text class="pl-cap" x="${num(W / 2)}" y="${num(H - 26)}"`
        + ` text-anchor="middle">${esc(COPY.ui.atlasx.totalsGrouped)}</text>`
      : "";
    const foot = `<text class="pl-sub pl-num" x="${num(W / 2)}" y="${num(H - 12)}"`
      + ` text-anchor="middle">${esc((o.grain === "concept"
        ? COPY.ui.atlasx.totalsFootConcepts : COPY.ui.atlasx.totalsFoot)(grand))}</text>`;

    return `<svg class="pl-svg pl-sankey" aria-label="${esc(label)}"`
      + ` viewBox="0 0 ${num(W)} ${num(H)}" width="${num(W)}" height="${num(H)}">`
      // 리본은 한 겹으로 묶는다 — 겹침 블렌딩(multiply/screen)이 **리본끼리만**
      // 일어나고 배경·노드로 새지 않게 하는 격리 경계(isolation: isolate).
      + `${heads}<g class="pl-ribbons">${links.join("")}</g>`
      + `${originNodes}${domainNodes}${stateNodes}${grouped}${foot}</svg>`;
  }

  let nodeEls: SVGElement[] = [];
  let ribEls: SVGElement[] = [];
  /** 판 폭 실측 — 숨은 채로 그리면 getBBox() 가 0 을 준다(정본 §6 실패 1).
   *  0 이 나오면 다음 프레임에 다시 잰다(탭이 보이는 순간 맞춰진다). */
  function fitPlates(retry = true) {
    let blind = false;
    for (const g of el.querySelectorAll<SVGGElement>(".pl-plate-g")) {
      const t = g.querySelector("text");
      const p = g.querySelector<SVGRectElement>(".pl-plate");
      if (!t || !p) continue;
      const b = (t as SVGTextElement).getBBox();
      if (!b.width) { blind = true; continue; }
      p.setAttribute("x", String(Math.round((b.x - 6) * 10) / 10));
      p.setAttribute("y", String(Math.round((b.y - 3) * 10) / 10));
      p.setAttribute("width", String(Math.round((b.width + 12) * 10) / 10));
      p.setAttribute("height", String(Math.round((b.height + 6) * 10) / 10));
    }
    if (blind && retry) requestAnimationFrame(() => fitPlates(false));
  }
  const collect = () => {
    nodeEls = [...el.querySelectorAll<SVGElement>(".pl-node")];
    ribEls = [...el.querySelectorAll<SVGElement>(".pl-ribbon")];
    fitPlates();
  };

  /** D32-a — hover 한 노드·리본과 그에 닿은 것만 남고 나머지는 물러난다.
   *  생키의 "닿음"은 리본 하나가 잇는 (주제 · 구간) 쌍이다. */
  function paint(keepNode: Set<string>, keepRib: Set<Element>) {
    for (const g of nodeEls) {
      const on = keepNode.has(g.dataset.id || "");
      g.classList.toggle("pl-fade", !on);
      g.classList.toggle("pl-hl", on);
    }
    for (const g of ribEls) {
      const on = keepRib.has(g);
      g.classList.toggle("pl-fade", !on);
      g.classList.toggle("pl-hl", on);
    }
  }
  /** 구간 키가 1열인지 3열인지 — 두 사전은 서로 겹치지 않는다(배타 6종). */
  const sideId = (mark: string) =>
    (ORIGINS as readonly string[]).includes(mark) ? `org:${mark}` : `st:${mark}`;

  function focusNode(id: string) {
    const keepN = new Set([id]);
    const keepR = new Set<Element>();
    const paths = new Set<string>();
    const gks = new Set<string>();
    for (const g of ribEls) {
      const dom = g.dataset.dom || "";
      const mark = g.dataset.seg || "";
      // 주제 노드는 자기 리본 전부, 구간 노드(org:/st:)는 그 구간의 리본 전부.
      if (id !== dom && id !== sideId(mark)) continue;
      keepR.add(g);
      if (g.dataset.path) paths.add(g.dataset.path);
      if (g.dataset.gk) gks.add(g.dataset.gk);
      keepN.add(dom);
      keepN.add(sideId(mark));
    }
    // D63 — 경로 키가 있으면 닿은 튜플의 **반대편 가닥까지** 끝에서 끝으로 잇는다.
    // 출처 노드에 올리면 그 출처의 개념들이 어느 상태로 끝났는지가 통째로 밝아진다.
    if (paths.size) for (const g of ribEls) {
      const p = g.dataset.path;
      if (!p || !paths.has(p) || keepR.has(g)) continue;
      keepR.add(g);
      keepN.add(g.dataset.dom || "");
      keepN.add(sideId(g.dataset.seg || ""));
    }
    // D68 — 집계 모드도 같은 문법: 닿은 갭 리본과 튜플을 공유하는 반대편 갭
    // 리본까지 조인 표로 잇는다. 반대편 리본은 여러 출처(또는 상태)의 합이라
    // 부분이 아니라 통째로 밝는다 — 툴팁 수치는 리본 자신의 정본값이라 거짓은
    // 없고, 이 한계는 정본 문서에 기록돼 있다.
    if (gks.size) {
      const want = new Set<string>();
      for (const k of gks) for (const j of gkJoin.get(k) || []) want.add(j);
      for (const g of ribEls) {
        const k = g.dataset.gk;
        if (!k || !want.has(k) || keepR.has(g)) continue;
        keepR.add(g);
        keepN.add(g.dataset.dom || "");
        keepN.add(sideId(g.dataset.seg || ""));
      }
    }
    paint(keepN, keepR);
  }
  function focusRibbon(g: SVGElement) {
    // D63 — 경로 가닥이면 같은 튜플의 좌·우 가닥을 한 물줄기로 밝힌다.
    const p = g.dataset.path;
    if (p) {
      const keepN = new Set<string>();
      const keepR = new Set<Element>();
      for (const r of ribEls) {
        if (r.dataset.path !== p) continue;
        keepR.add(r);
        keepN.add(r.dataset.dom || "");
        keepN.add(sideId(r.dataset.seg || ""));
      }
      paint(keepN, keepR);
      return;
    }
    // D68 — 집계 가닥이면 조인 표로 반대편 갭의 리본까지 밝힌다(경로 모드와
    // 같은 hover 문법을 집계 규모에서도 유지한다).
    const k = g.dataset.gk;
    if (k) {
      const keepN = new Set<string>([g.dataset.dom || "", sideId(g.dataset.seg || "")]);
      const keepR = new Set<Element>([g]);
      const want = gkJoin.get(k) || new Set<string>();
      for (const r of ribEls) {
        const rk = r.dataset.gk;
        if (!rk || !want.has(rk) || keepR.has(r)) continue;
        keepR.add(r);
        keepN.add(r.dataset.dom || "");
        keepN.add(sideId(r.dataset.seg || ""));
      }
      paint(keepN, keepR);
      return;
    }
    paint(new Set([g.dataset.dom || "", sideId(g.dataset.seg || "")]), new Set([g]));
  }
  function clear() {
    for (const g of [...nodeEls, ...ribEls]) g.classList.remove("pl-fade", "pl-hl");
  }

  // 위임 3개 — 마크마다 리스너를 달지 않는다(다시 그려도 재배선 불필요).
  // 리본이 노드보다 먼저 검사되면 안 된다: 노드는 DOM 상 뒤(위)라 겹친 자리의
  // 포인터를 이미 가져간다. hover 는 노드 우선, 그다음 리본.
  const over = (e: Event) => {
    const t = e.target as Element;
    const n = t.closest?.(".pl-node") as SVGElement | null;
    if (n) { focusNode(n.dataset.id || ""); return; }
    const r = t.closest?.(".pl-ribbon") as SVGElement | null;
    if (r) { focusRibbon(r); return; }
    clear();
  };
  const leave = () => clear();
  const click = (e: Event) => {
    const t = e.target as Element;
    // 리본 클릭 = 구간 드릴다운(주제 열기보다 우선 — 더 구체적인 표적).
    // 기타(D68) 리본은 제외 — 병합 stratum 이라 드릴다운 API 에 실체가 없다.
    const seg = t.closest?.("[data-seg]") as SVGElement | null;
    if (seg && o.onSeg && seg.dataset.dom !== OTHERS_KEY) {
      o.onSeg(seg.dataset.dom!, seg.dataset.seg!);
      return;
    }
    const open = t.closest?.("[data-open]") as SVGElement | null;
    if (open) o.onOpen(open.dataset.open!);
  };
  el.addEventListener("mouseover", over);
  el.addEventListener("mouseleave", leave);
  el.addEventListener("click", click);

  const stopSize = trackSize(el, (w, h) => {
    el.innerHTML = wrap(draw(w, h));
    collect();
  });
  // 진입 안무는 최초 1회 — 그런데 `first` 를 그리기 콜백 안에서 내리면 **애니메이션이
  // 사실상 안 보인다**: 첫 동기 그리기는 아직 레이아웃 전 크기(0 일 수 있다)로 나가고,
  // 곧바로 ResizeObserver 가 실제 크기로 다시 그리면서 방금 시작한 안무를 지운다
  // (실측: 화면의 리본에 pl-in 이 없었다). 레이아웃이 앉을 때까지(더블 rAF) 기다렸다
  // 내려야 "사용자가 처음 보는 그림"에서 한 번 재생된다. 그 뒤 리사이즈는 조용하다.
  const settle = requestAnimationFrame(() =>
    requestAnimationFrame(() => { first = false; }));
  return {
    dispose() {
      cancelAnimationFrame(settle);
      el.removeEventListener("mouseover", over);
      el.removeEventListener("mouseleave", leave);
      el.removeEventListener("click", click);
      stopSize();
    },
    /** 밖에서 거는 초점(D70). 합계는 **개념을 그리지 않는다** — 가운데 열은 주제다
     *  (개념 알갱이 화면일 때만 개념이 행이다). 그래서 개념 id 를 받으면 그 개념이
     *  속한 주제를 짚고, 못 찾으면 false 를 돌려준다. 화면은 주제를, 옆 카드는
     *  개념을 가리키게 되는데 그건 거짓이 아니라 알갱이가 다를 뿐이고, 둘을
     *  억지로 맞추려면 없는 개념 마크를 지어내야 한다. */
    focus(id) {
      const el2 = el.querySelector<SVGElement>(`.pl-node[data-id="${CSS.escape(id)}"]`);
      if (!el2) return false;
      focusNode(id);
      el2.scrollIntoView({ block: "nearest", inline: "center" });
      return true;
    },
  };
}
