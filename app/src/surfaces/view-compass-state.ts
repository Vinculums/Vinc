/** 나침반 렌즈의 상태 한 벌과 그 상태를 바꾸는 순수한 전이들. DOM 을 읽지도 쓰지도 않는다.
 *
 *  상태를 바꾸는 길은 이 파일의 전이뿐이다: `(state, …) → state` 로 새 상태를 돌려주고 받은 상태는 건드리지 않는다.
 *  나침반(view-compass.ts)은 이벤트를 전이로 바꾸고, 그 이벤트에 맞는 그리기 한 번을 부른다. 측정이 필요한 값(그래프 칸의
 *  크기, 지금 시각, 포인터 자리)은 이벤트가 재서 인자로 넘긴다. 파생 값(맞춘 카메라, 확대 단계, 기간 안인지)은 상태의
 *  함수이고 따로 저장하지 않는다. 스냅숏은 `snapshotOf` 한 모양으로 나가고 `restoreFrom` 이 앞선 모양까지 받아들인다. */

export type ThemeMode = "dark" | "light";
export interface Box { x: number; y: number; w: number; h: number }
export interface Size { w: number; h: number }
export interface Point { x: number; y: number }
/** 배치의 상자 [x0, y0, x1, y1](그림 단위). */
export type Bounds = [number, number, number, number];
/** 카메라가 날아가는 중일 때의 출발 · 도착 · 시작 시각 · 길이(ms, 0 이면 한 번에). */
export interface Fly { from: Box; to: Box; t0: number; ms: number }
export interface Camera {
  /** 지금 viewBox(반올림 전 값). 첫 그림 전에는 없다. */
  view: Box | null;
  /** 확대 단계를 마지막으로 정할 때 잰 그래프 칸 폭(px). 카메라가 움직일 때만 다시 잰다(크기만 바뀌면 그대로다). */
  lodW: number;
  fly: Fly | null;
  /** 다음 그리기의 카메라 뜻. keep: 그대로 둔다, chain: 이 허브들과 지금 층만 담는다, wide: 고른 노드를 가운데 두고 펼친 길 전체.
   *  셋 다 없으면 지금 층에 맞춘다. 그리기가 하나를 쓰면 `cameraFramed` 가 지운다(먼저 오는 것: keep, chain, wide). */
  keep: boolean; chain: string[] | null; wide: boolean;
}
/** 전체 보기의 카메라. fit 이면 지금 칸에 맞춘 그림이고(viewBox 는 배치 상자와 칸 크기에서 그때그때 나온다), 아니면 view 가
 *  at(그 viewBox 를 정한 때의 칸 크기)에 맞춘 viewBox 다. 칸 크기가 바뀌면 `resize` 가 가운데와 배율을 지켜 새 크기로 옮긴다. */
export interface WholeCam { fit: boolean; view: Box | null; at: Size }
/** 전체 보기의 계획: 이 배치(key = 배치 서명 | 칸 크기)의 맞춤 배율(sFit), 맞춤이 먼 단계인지(far), 맞춤에서 그리는 것(fit: 점 · 허브만),
 *  점을 그리기 시작하는 배율(sDots, 없으면 null), 그 배율을 한가할 때 찾는 중인지(pending). 격자 자체는 나침반이 key 로 들고 있다 */
export interface WholePlan { key: string; sFit: number; far: boolean; fit: "dots" | "hubs"; sDots: number | null; pending: boolean }
/** 전체 보기의 상태. on: 보이는가, cam: 그 카메라(떠나 있어도 남는다), rels: 주제 쌍의 관계선, sel · hov: 고른 노드 · 가리킨 노드,
 *  bounds: 지금 배치의 상자(카메라 맞춤에 쓴다), plan: 지금 칸의 계획, last: 맞춤에서 마지막으로 고른 것(문턱의 이력, `fitChoice`) */
export interface WholeState { on: boolean; cam: WholeCam; rels: boolean; sel: string; hov: string; bounds: Bounds | null; plan: WholePlan | null; last: "dots" | "hubs" | null }
/** 앞뒤 기록에서 전체 보기를 뜻하는 한 칸. */
export const WHOLE_ENTRY = "@whole";
/** 전체 보기의 맞춤 여백(px). */
export const WHOLE_PAD = 48;

/** 렌즈 상태. 길(path)은 노드 id 의 사슬이다(첫 칸이 뿌리 주제, 끝이 지금 가운데). */
export interface LensState {
  theme: ThemeMode;
  /** 그래프 칸의 크기(관찰자가 잰 값). 크기가 바뀌어도 카메라의 viewBox 는 그대로다. */
  size: Size;
  /** 글꼴이 자리 잡을 때마다 하나 는다. 글자 폭을 재는 자리(`glyphWidth`)가 이 값으로 다시 잰다. */
  fontKey: number;
  path: string[];
  hist: string[][];
  hi: number;
  camera: Camera;
  /** 포인터 아래의 노드(엿보기 · P 고정의 대상). */
  hover: string | null;
  /** 끌어서 옮기는 중: 누른 자리와 누를 때의 viewBox. */
  pan: { x: number; y: number; v: Box } | null;
  /** 찾기 글(소문자로 고른 것). */
  query: string;
  /** 시간 막대에서 고른 기간 [시작, 끝]. 없으면 전체. */
  tsel: [string, string] | null;
  /** 범례에서 끈 종류(넣은 순서). */
  koff: string[];
  /** '허브|종류' → 목록에 더 보인 줄 수(넣은 순서). */
  more: [string, number][];
  /** 고정한 노드(넣은 순서). 이 기기에만 저장한다. */
  pins: string[];
  /** 이번 마운트에서 고정 노드가 마지막으로 놓였던 자리(배치 좌표). */
  lastPin: [string, Point][];
  /** 사이드바 요약에서 펼친 관계 줄(`종류:방향`). */
  sumOpen: string[];
  /** 고정 노드까지 길이 없다고 말하는 가운데. */
  noPath: string | null;
  /** 열린 창: 시간 · 범례. */
  pop: "" | "time" | "legend";
  /** 시간 막대를 끄는 중이면 누른 칸. */
  bar: number | null;
  /** 층의 실제 크기를 재서 팔을 골랐다(그 뒤로 팔은 바뀌지 않는다). */
  armsMeasured: boolean;
  /** 전체 보기(층 보기와 나란한 두 번째 그림). */
  whole: WholeState;
}

/** 다시 그릴 때 이어 받는 나침반의 자리(`CompassHandle.snapshot`). 이 모양 그대로 나가고 그대로 받는다. */
export interface CompassState { path: string[]; hist: string[][]; hi: number; mode: string; filt: [string, string[]][]; more: [string, number][];
  koff: string[]; tsel: [string, string] | null; view: Box | null; query: string;
  /** 전체 보기를 한 번이라도 연 뒤에만 선다: 그 카메라(fit · view · at)와 관계선. mode 가 'whole' 이면 전체 보기로 다시 연다 */
  whole?: { cam: WholeCam; rels: boolean } }
/** `restoreFrom` 이 받아들이는 모양: 지금 모양과, 칸이 빠졌거나 `mode: 'all'` · `filt` 를 가진 앞선 모양. 모르는 칸은 읽지 않는다. */
export type SavedLens = Partial<CompassState> & { path: string[] };

// ── 작은 도움: 넣은 순서를 지키는 집합과 표(배열로 들고 다닌다) ───────────────────────
const without = <T,>(a: T[], x: T): T[] => a.filter((y) => y !== x);
const withAdded = <T,>(a: T[], x: T): T[] => (a.includes(x) ? a : [...a, x]);
/** 표에 값을 넣는다. 이미 있는 열쇠는 제자리에서 값만 바뀐다(Map.set 과 같다). */
export function entrySet<V>(a: [string, V][], k: string, v: V): [string, V][] {
  const i = a.findIndex(([x]) => x === k); if (i < 0) return [...a, [k, v]];
  const o = a.slice(); o[i] = [k, v]; return o;
}
export const entryGet = <V,>(a: [string, V][], k: string): V | undefined => { for (const [x, v] of a) if (x === k) return v; return undefined; };

export function initialState(theme: ThemeMode, size: Size = { w: 0, h: 0 }): LensState {
  return { theme, size, fontKey: 0, path: [], hist: [], hi: -1,
    camera: { view: null, lodW: 0, fly: null, keep: false, chain: null, wide: false },
    hover: null, pan: null, query: "", tsel: null, koff: [], more: [], pins: [], lastPin: [], sumOpen: [], noPath: null, pop: "", bar: null,
    armsMeasured: false, whole: initialWhole() };
}
const initialWhole = (): WholeState => ({ on: false, cam: { fit: true, view: null, at: { w: 0, h: 0 } }, rels: false, sel: "", hov: "", bounds: null, plan: null, last: null });

// ── 다니기 ────────────────────────────────────────────────────────────────────────
/** 전체 보기를 떠난다: 고름 · 가리킴은 풀고 카메라는 남긴다. 층의 카메라는 viewBox 없이(날기 · 그대로 두기도 없이) 새 층에
 *  바로 맞춘다: 두 그림은 좌표가 달라 전체 보기의 viewBox 에서 날아오면 엉뚱한 자리를 지난다. */
function offWhole(s: LensState): LensState {
  if (!s.whole.on) return s;
  // 한가할 때 찾던 계획(pending)은 버린다: 나침반이 같은 걸음에 그 유휴 일을 지운다. 다시 들어오면 새로 세운다
  const plan = s.whole.plan && s.whole.plan.pending ? null : s.whole.plan;
  return { ...s, pan: null, whole: { ...s.whole, on: false, sel: "", hov: "", plan }, camera: { ...s.camera, view: null, fly: null, keep: false } };
}
export const isWholeEntry = (h: string[] | undefined): boolean => !!h && h.length === 1 && h[0] === WHOLE_ENTRY;
/** 층으로 들어간다(가운데를 고른다). push 면 앞뒤 기록에 남긴다: 지금 자리 뒤의 기록은 버린다. 전체 보기에서 부르면 그 보기를 떠난다. */
export function enter(s0: LensState, path: string[], push = true): LensState {
  const s = offWhole(s0);
  if (!push) return { ...s, path };
  const hist = [...s.hist.slice(0, s.hi + 1), path.slice()];
  return { ...s, path, hist, hi: hist.length - 1 };
}
/** 목록 · 트리에서 고른 노드로: 고른 노드를 가운데 두고 펼친 길 전체가 보이게 물러난다. */
export const pickFromTree = (s: LensState, path: string[]): LensState => enter(withCamera(s, { wide: true }), path);
/** 관계를 따라 연 사슬: 그 사슬의 허브와 지금 층만 화면에 담는다. */
export const followChain = (s: LensState, path: string[], chain: string[]): LensState => enter(withCamera(s, { chain }), path);
/** Escape: 뿌리 층으로(기록에 남긴다). */
export const toRoot = (s: LensState, rootId: string): LensState => enter(s, [rootId]);
/** Backspace: 한 층 위로(뿌리에서는 그대로). 전체 보기에서는 아무것도 하지 않는다. */
export const upOne = (s: LensState): LensState => (!s.whole.on && s.path.length > 1 ? enter(s, s.path.slice(0, -1)) : s);
/** Alt+← · Alt+→: 기록을 한 칸 옮긴다. `known` 은 이 데이터에 있는 id 를 정본 id 로, 없으면 null. 끝이면 그대로 돌려준다.
 *  전체 보기의 칸이면 전체 보기로(층의 길은 그대로), 층의 칸이면 전체 보기를 떠나 그 층으로. */
export function historyStep(s: LensState, dir: -1 | 1, known: (id: string) => string | null): LensState {
  const hi = s.hi + dir; if (dir < 0 ? s.hi <= 0 : s.hi >= s.hist.length - 1) return s;
  if (isWholeEntry(s.hist[hi])) return { ...toWhole(s, false), hi };
  return { ...offWhole(s), hi, path: s.hist[hi].map(known).filter((x): x is string => !!x) };
}

// ── 전체 보기로 오가기 ─────────────────────────────────────────────────────────────
/** 전체 보기로. push 면 앞뒤 기록에 전체 보기 한 칸을 남긴다. 카메라는 떠날 때 남긴 것(처음이면 맞춤)이다. */
export function toWhole(s: LensState, push = true): LensState {
  if (s.whole.on) return s;
  const hist = push ? [...s.hist.slice(0, s.hi + 1), [WHOLE_ENTRY]] : s.hist;
  return { ...s, hist, hi: push ? hist.length - 1 : s.hi, hover: null, pan: null, whole: { ...s.whole, on: true, hov: "" } };
}
/** 지금 층이 전체 보기에서 바로 들어온 층인가(기록의 바로 앞 칸이 전체 보기). */
export const cameFromWhole = (s: LensState): boolean => !s.whole.on && s.hi > 0 && isWholeEntry(s.hist[s.hi - 1]);
/** 머리 단추. 거푸 눌러도 기록이 자라지 않게, 바로 앞 · 바로 뒤 칸이 갈 자리면 그 칸으로 옮긴다. */
export function toggleWhole(s: LensState): LensState {
  if (s.whole.on) {
    const next = s.hist[s.hi + 1];
    if (isWholeEntry(s.hist[s.hi]) && next && !isWholeEntry(next) && next.join("|") === s.path.join("|")) return { ...offWhole(s), hi: s.hi + 1 };
    return enter(s, s.path);
  }
  if (cameFromWhole(s)) return { ...toWhole(s, false), hi: s.hi - 1 };
  return toWhole(s, true);
}
/** Escape. 전체 보기에서는 고른 노드만 푼다(보기를 떠나지 않는다). 전체 보기에서 바로 들어온 층이면 전체 보기로(같은 카메라),
 *  그 밖에서는 뿌리 층으로. */
export function escapeStep(s: LensState, rootId: string): LensState {
  if (s.whole.on) return s.whole.sel ? wholeSelect(s, "") : s;
  return cameFromWhole(s) ? toWhole(s, true) : toRoot(s, rootId);
}
export const wholeSelect = (s: LensState, id: string): LensState => (s.whole.sel === id ? s : { ...s, whole: { ...s.whole, sel: id } });
export const wholeHover = (s: LensState, id: string): LensState => (s.whole.hov === id ? s : { ...s, whole: { ...s.whole, hov: id } });
export const toggleRels = (s: LensState): LensState => ({ ...s, whole: { ...s.whole, rels: !s.whole.rels } });
/** 전체 보기의 배치를 새로 지었다(종류를 끄고 켜면 바뀐다): 카메라 맞춤이 읽는 상자. */
export const wholeLaidOut = (s: LensState, bounds: Bounds): LensState => (sameBounds(s.whole.bounds, bounds) ? s : { ...s, whole: { ...s.whole, bounds } });
/** 지금 칸의 계획이 섰다(또는 한가할 때 점의 배율을 찾았다). 맞춤에서 고른 것을 문턱의 이력으로 남긴다. */
export const wholePlanned = (s: LensState, plan: WholePlan): LensState => ({ ...s, whole: { ...s.whole, plan, last: plan.far ? plan.fit : s.whole.last } });
const sameBounds = (a: Bounds | null, b: Bounds) => !!a && a.every((v, i) => v === b[i]);
export const historyCan = (s: LensState, dir: -1 | 1): boolean => (dir < 0 ? s.hi > 0 : s.hi < s.hist.length - 1);
export const centreOf = (s: LensState): string => (s.path.length ? s.path[s.path.length - 1] : "");

// ── 카메라 ────────────────────────────────────────────────────────────────────────
const withCamera = (s: LensState, c: Partial<Camera>): LensState => ({ ...s, camera: { ...s.camera, ...c } });
/** 다음 그리기가 카메라를 어떻게 다룰지(main 의 순서 그대로: keep 이 먼저, 그다음 chain, wide, 맞춤). */
export type CameraPlan = { kind: "keep" } | { kind: "chain"; ids: string[] } | { kind: "wide" } | { kind: "fit" };
export function cameraPlan(s: LensState): CameraPlan {
  const c = s.camera;
  return c.keep ? { kind: "keep" } : c.chain ? { kind: "chain", ids: c.chain } : c.wide ? { kind: "wide" } : { kind: "fit" };
}
/** 그리기가 카메라 뜻을 하나 썼다. keep 은 자기만 지운다. chain 은 chain 과 wide 를, wide 는 wide 를 지운다. */
export function cameraFramed(s: LensState, plan: CameraPlan): LensState {
  if (plan.kind === "keep") return withCamera(s, { keep: false });
  if (plan.kind === "chain") return withCamera(s, { chain: null, wide: false });
  if (plan.kind === "wide") return withCamera(s, { wide: false });
  return s;
}
/** 지금 그림을 그대로 둔 채 다음 그리기를 부른다(고정을 바꿀 때). */
export const keepCamera = (s: LensState): LensState => withCamera(s, { keep: true });
/** 상자를 그래프 칸의 비율로 넓힌다. 너무 작은 층만 과하게 확대되지 않게 420 × 320 아래로는 줄이지 않는다.
 *  area 는 그 순간의 칸 크기(0 이면 1400 × 900 으로 본다). */
export function fitBox(b: Box, area: Size): Box {
  const W = area.w || 1400, H = area.h || 900, ar = W / H; let { x, y, w, h } = b; w = Math.max(w, 420); h = Math.max(h, 320);
  if (w / h > ar) { const nh = w / ar; y -= (nh - h) / 2; h = nh; } else { const nw = h * ar; x -= (nw - w) / 2; w = nw; }
  return { x, y, w, h };
}
/** 상자로 날아가기 시작한다. ms 가 0 이면 다음 걸음에서 바로 닿는다. */
export function flyTo(s: LensState, b: Box, area: Size, now: number, ms: number): LensState {
  const to = fitBox(b, area);
  return withCamera(s, { fly: { from: s.camera.view || to, to, t0: now, ms } });
}
/** 날아가는 한 걸음(세제곱 감속). 닿으면 날기를 끝낸다. width 는 그 순간의 칸 폭(확대 단계에 쓴다). */
export function flyStep(s: LensState, now: number, width: number): LensState {
  const f = s.camera.fly; if (!f) return s;
  const u = f.ms ? Math.min(1, (now - f.t0) / f.ms) : 1, e = 1 - (1 - u) ** 3;
  const v = { x: f.from.x + (f.to.x - f.from.x) * e, y: f.from.y + (f.to.y - f.from.y) * e, w: f.from.w + (f.to.w - f.from.w) * e, h: f.from.h + (f.to.h - f.from.h) * e };
  return withCamera(s, { view: v, lodW: width, fly: u < 1 ? f : null });
}
export const flying = (s: LensState): boolean => !!s.camera.fly;
/** viewBox 를 곧바로 정한다(끌기 · 복원). 날기는 건드리지 않는다. */
export const setView = (s: LensState, v: Box, width: number): LensState => withCamera(s, { view: v, lodW: width });
/** 굴려서 확대: 포인터 자리(칸 안의 비율 fx, fy)를 고정점으로 deltaY 만큼. 날던 카메라는 멈춘다. 전체 보기에서는 그 카메라를 민다. */
export function zoomAt(s: LensState, fx: number, fy: number, deltaY: number, width: number): LensState {
  const v = (s.whole.on ? wholeView(s) : s.camera.view) as Box, f = Math.exp(deltaY * .0015);
  const px = v.x + fx * v.w, py = v.y + fy * v.h, next = { x: px - (px - v.x) * f, y: py - (py - v.y) * f, w: v.w * f, h: v.h * f };
  if (s.whole.on) return wholeSetView(s, next, deltaY > 0);
  return withCamera(s, { fly: null, view: next, lodW: width });
}
export const panStart = (s: LensState, x: number, y: number): LensState => ({ ...s, pan: { x, y, v: { ...((s.whole.on ? wholeView(s) : s.camera.view) as Box) } } });
/** 끄는 중이면 누른 자리에서 움직인 만큼 viewBox 를 민다. rw 는 칸의 그려진 폭(화면 px 를 그림 단위로 바꾼다),
 *  width 는 칸의 정수 폭(확대 단계에 쓴다). */
export function panMove(s: LensState, x: number, y: number, rw: number, width: number): LensState {
  const p = s.pan; if (!p) return s; const k = p.v.w / rw, v = { ...p.v, x: p.v.x - (x - p.x) * k, y: p.v.y - (y - p.y) * k };
  if (s.whole.on) return wholeSetView(s, v, false);
  return withCamera(s, { view: v, lodW: width });
}

// ── 전체 보기의 카메라 ──────────────────────────────────────────────────────────────
/** 칸 크기(0 이면 1400 × 900 으로 본다: 숨은 칸). */
export const areaOf = (z: Size): Size => (z.w > 0 && z.h > 0 ? z : { w: 1400, h: 900 });
/** 상자를 칸(area px)에 맞추는 viewBox: 칸의 비율로 넓히고 가장자리를 pad(px)만큼 둔다. */
export function fitView(b: Bounds, area: Size, pad = WHOLE_PAD): Box {
  const A = areaOf(area), bw = Math.max(1, b[2] - b[0]), bh = Math.max(1, b[3] - b[1]);
  const k = Math.max(1e-9, Math.min((A.w - 2 * pad) / bw, (A.h - 2 * pad) / bh)), vw = A.w / k, vh = A.h / k;
  return { x: (b[0] + b[2]) / 2 - vw / 2, y: (b[1] + b[3]) / 2 - vh / 2, w: vw, h: vh };
}
/** viewBox 하나를 다른 칸 크기로 옮긴다: 가운데와 배율(화면 px / 그림 단위)을 지킨다. 옛 크기를 모르면(0) 옛 viewBox 가 새 칸에
 *  다 들어오는 배율로 본다. 결과의 비율은 늘 새 칸의 비율이다(그래서 화면 자리와 그림 자리가 어긋나지 않는다). */
export function rebaseView(v: Box, at: Size, area: Size): Box {
  const A = areaOf(area); if (at.w === A.w && at.h === A.h) return v;
  const k = at.w > 0 && at.h > 0 ? at.w / v.w : Math.min(A.w / v.w, A.h / v.h), cx = v.x + v.w / 2, cy = v.y + v.h / 2, w = A.w / k, h = A.h / k;
  return { x: cx - w / 2, y: cy - h / 2, w, h };
}
/** 맞춤보다 멀리 물러나지 않는다(그보다 멀면 점이 붙는다). 가운데를 지키고 넓이만 줄인다. */
function notFarther(v: Box, fit: Box): Box {
  if (v.w <= fit.w * (1 + 1e-9)) return v;
  const k = fit.w / v.w, cx = v.x + v.w / 2, cy = v.y + v.h / 2;
  return { x: cx - v.w * k / 2, y: cy - v.h * k / 2, w: v.w * k, h: v.h * k };
}
/** 지금 그릴 전체 보기의 viewBox(상태의 함수): 맞춤이면 배치 상자와 지금 칸 크기에서, 아니면 남긴 viewBox 를 지금 칸으로 옮겨
 *  맞춤보다 멀지 않게. 배치를 아직 짓지 않았으면 null. */
export function wholeView(s: LensState): Box | null {
  const w = s.whole; if (!w.bounds) return null; const fit = fitView(w.bounds, s.size);
  return w.cam.fit || !w.cam.view ? fit : notFarther(rebaseView(w.cam.view, w.cam.at, s.size), fit);
}
/** 지금 배율(화면 px / 그림 단위)과 맞춤 배율. */
export const wholeScale = (s: LensState): number => { const v = wholeView(s); return v ? areaOf(s.size).w / v.w : 1; };
export const wholeFitScale = (s: LensState): number => (s.whole.bounds ? areaOf(s.size).w / fitView(s.whole.bounds, s.size).w : 1);
/** 전체 보기의 카메라를 정한다. 맞춤 배율에 닿았고 out(물러나는 몸짓)이면 맞춤으로 돌아간다(가운데도). */
export function wholeSetView(s: LensState, v: Box, out = false): LensState {
  const A = areaOf(s.size), fit = s.whole.bounds ? fitView(s.whole.bounds, s.size) : null;
  const atFit = !!fit && v.w >= fit.w * (1 - 1e-3);
  const cam: WholeCam = out && atFit ? { fit: true, view: null, at: A } : { fit: false, view: fit ? notFarther(v, fit) : v, at: A };
  return { ...s, whole: { ...s.whole, cam } };
}
/** 노드 하나를 이름이 읽히는 배율(scale 이상) 가운데로. */
export function wholeFrame(s: LensState, x: number, y: number, scale: number): LensState {
  const A = areaOf(s.size), k = Math.max(scale, wholeScale(s));
  return wholeSetView(s, { x: x - A.w / k / 2, y: y - A.h / k / 2, w: A.w / k, h: A.h / k });
}
export const wholeFit = (s: LensState): LensState => ({ ...s, whole: { ...s.whole, cam: { fit: true, view: null, at: areaOf(s.size) } } });
export const panEnd = (s: LensState): LensState => ({ ...s, pan: null });
/** 확대 단계: 멀리서 볼수록 덜 그린다. 1단은 이름 · 목록 글자를, 2단은 목록 줄과 잎 노드를 숨긴다. */
export function lodOf(c: Camera): { lod1: boolean; lod2: boolean } {
  const z = c.view ? c.view.w / (c.lodW || 1) : 1; return { lod1: z > 2.4, lod2: z > 5 };
}
export const viewBoxAttr = (v: Box): string => `${v.x.toFixed(1)} ${v.y.toFixed(1)} ${v.w.toFixed(1)} ${v.h.toFixed(1)}`;

// ── 크기 · 테마 · 글꼴 ────────────────────────────────────────────────────────────
/** 그래프 칸의 크기가 바뀌었다. 카메라는 뜻을 지킨다. 층 보기: viewBox 는 같은 그림 범위를 그대로 담고(SVG 가 칸에 맞춰 늘고 준다),
 *  확대 단계는 다음에 카메라가 움직일 때 다시 잰다. 전체 보기의 카메라(보이든 떠나 있든): 맞춘 그림은 맞춘 그림으로 남고(새 칸에 다시
 *  맞는다), 다가간 그림은 가운데와 배율을 지킨 채 새 칸의 비율로 옮기되 새 맞춤보다 멀리 물러나지 않는다. 숨은 칸(0)은 옮기지 않는다. */
export function resize(s: LensState, size: Size): LensState {
  if (s.size.w === size.w && s.size.h === size.h) return s;
  const c = s.whole.cam; let cam = c;
  if (!c.fit && c.view && size.w > 0 && size.h > 0) {
    const v = rebaseView(c.view, c.at, size), fit = s.whole.bounds ? fitView(s.whole.bounds, size) : null;
    cam = { fit: false, view: fit ? notFarther(v, fit) : v, at: { ...size } };
  }
  return { ...s, size, whole: cam === c ? s.whole : { ...s.whole, cam } };
}
export const themeChanged = (s: LensState, theme: ThemeMode): LensState => ({ ...s, theme });
export const fontsSettled = (s: LensState): LensState => ({ ...s, fontKey: s.fontKey + 1 });
/** 글자 폭을 재는 곳: 층 보기의 이름표(fan · bridge)와 전체 보기의 글자들(leaf: 노드 이름 11, hub: 주제 이름 굵게 15,
 *  head: 목록 머리 굵게 11, name: 화면 크기 주제 이름 굵게 12, count: 수 11, hover: 가리킨 끝 이름 600 11). */
export type GlyphKind = "fan" | "bridge" | "leaf" | "hub" | "head" | "name" | "count" | "hover";
const GLYPH_PX: Record<Exclude<GlyphKind, "fan" | "bridge">, [number, number]> = { leaf: [11, 1], hub: [15, 1.07], head: [11, 1.07], name: [12, 1.07], count: [11, 1], hover: [11, 1.04] };
/** 글자 하나가 차지하는 몫(em). 웹 글꼴(Pretendard)과 대신 서는 시스템 글꼴 가운데 넓은 쪽에 맞춘 어림이라, 글꼴이 들어오기 전후에
 *  배치가 같다. 한글 · 한자 · 전각은 1em. 좁게 잡으면 이름표가 이웃에 닿는다: 줄이지 말 것 */
function glyphEm(ch: string): number {
  const c = ch.codePointAt(0) || 0;
  if ((c >= 0x1100 && c <= 0x11ff) || (c >= 0x2e80 && c <= 0xa4cf) || (c >= 0xac00 && c <= 0xd7af) || (c >= 0xf900 && c <= 0xfaff) || (c >= 0xfe30 && c <= 0xfe4f) || (c >= 0xff00 && c <= 0xffef)) return 1;
  if ("ilj|!.,:;'\"`".includes(ch)) return .3;
  if (" frt()[]{}/\\-".includes(ch)) return .4;
  if (ch === "m" || ch === "w") return .86;
  if (ch === "M" || ch === "W") return .95;
  if (ch >= "A" && ch <= "Z") return .7;
  if (ch >= "0" && ch <= "9") return .6;
  if (ch >= "a" && ch <= "z") return .58;
  return .64;
}
/** 이름표 글자 하나의 어림 폭(px). 글꼴과 상관없는 값이다(배치는 이 값으로 이름표 자리를 잰다). 층 보기(fan · bridge)는 글자와
 *  상관없는 고정값이고, 전체 보기는 글자의 갈래(ch)마다 다르다. 실제 글꼴로 재려면 여기서 fontKey 마다 잰 값을 돌려준다:
 *  나침반은 이 값이 바뀔 때만 다시 그린다(`fontsSettled`). */
export function glyphWidth(_fontKey: number, kind: GlyphKind, ch = "n"): number {
  if (kind === "fan") return 6.6; if (kind === "bridge") return 7;
  const [px, bold] = GLYPH_PX[kind]; return px * bold * glyphEm(ch);
}
/** 글 한 줄의 어림 폭(px): 글자마다 `glyphWidth` 를 더한다. */
export function textWidth(fontKey: number, text: string, kind: GlyphKind): number {
  let w = 0; for (const ch of text) w += glyphWidth(fontKey, kind, ch); return w;
}

// ── 고르기 · 거르기 ──────────────────────────────────────────────────────────────
export const hover = (s: LensState, id: string | null): LensState => (s.hover === id ? s : { ...s, hover: id });
export const setQuery = (s: LensState, q: string): LensState => ({ ...s, query: String(q || "").toLowerCase() });
export const setRange = (s: LensState, tsel: [string, string] | null): LensState => ({ ...s, tsel });
/** 범례의 종류를 끄고 켠다. 전체 보기가 맞춤 배율에 있으면(맞춘 그림이든, 그 배율에서 끌어 옮긴 그림이든) 같은 걸음에 맞춤으로
 *  둔다: 배치 상자가 바뀌므로 새 배치에 다시 맞는다. */
export function toggleKind(s: LensState, k: string): LensState {
  const koff = s.koff.includes(k) ? without(s.koff, k) : [...s.koff, k];
  const atFit = s.whole.on && (s.whole.cam.fit || wholeScale(s) <= wholeFitScale(s) * 1.005);
  return atFit ? wholeFit({ ...s, koff }) : { ...s, koff };
}
/** 긴 목록을 20줄 더 연다. */
export const showMore = (s: LensState, key: string): LensState => ({ ...s, more: entrySet(s.more, key, (entryGet(s.more, key) || 0) + 20) });
export const moreOf = (s: LensState, key: string): number => entryGet(s.more, key) || 0;
/** 날짜가 기간 안인가(날짜가 없으면 안으로 친다). */
export const inRange = (w: string, rg: [string, string] | null): boolean => !rg || !w || (w >= rg[0] && w <= rg[1]);
/** 시간 막대의 칸 i 가 뜻하는 날. 첫 칸은 '이전'이라 시작이면 0000-01-01, 끝이면 그다음 칸의 날이다. */
export const barDay = (days: string[], i: number, end: boolean): string => (i === 0 ? (end ? days[1] : "0000-01-01") : days[i]);
/** 기간 단추: 마지막 날에서 k 일, 'all' 이면 전체. */
export const rangeOf = (days: string[], k: string): [string, string] | null => (k === "all" ? null : [days[Math.max(1, days.length - +k)], days[days.length - 1]]);
export const barStart = (s: LensState, days: string[], i: number): LensState => ({ ...s, bar: i, tsel: [barDay(days, i, false), barDay(days, i, true)] });
export function barMove(s: LensState, days: string[], i: number): LensState {
  if (s.bar == null) return s; const a = Math.min(s.bar, i), b = Math.max(s.bar, i);
  return { ...s, tsel: [barDay(days, a, false), barDay(days, b, true)] };
}
export const barEnd = (s: LensState): LensState => (s.bar == null ? s : { ...s, bar: null });

// ── 고정 ────────────────────────────────────────────────────────────────────────
/** 고정을 켜고 끈다. 끄면 마지막 자리도 잊는다. 지금 그림을 그대로 둔 채 다시 그린다. */
export function togglePin(s: LensState, id: string): LensState {
  const on = s.pins.includes(id);
  return keepCamera({ ...s, pins: on ? without(s.pins, id) : withAdded(s.pins, id), lastPin: on ? s.lastPin.filter(([x]) => x !== id) : s.lastPin });
}
/** 이 기기에 저장된 고정 목록으로 시작한다(`pinsFrom` 으로 고른 것). */
export const loadPins = (s: LensState, pins: string[]): LensState => ({ ...s, pins: pins.slice() });
/** 그린 배치에 고정 노드가 놓인 자리를 기억한다(다음 배치가 그 노드를 못 놓으면 이 자리에 둔다). */
export function pinsPlaced(s: LensState, at: [string, Point][]): LensState {
  if (!at.length) return s; let lastPin = s.lastPin; for (const [id, p] of at) lastPin = entrySet(lastPin, id, { x: p.x, y: p.y });
  return { ...s, lastPin };
}
/** 저장된 고정 목록을 읽는다: 배열이 아니거나 깨졌으면 빈 것, 이 데이터에 없는 id 와 글이 아닌 값은 버린다. */
export function pinsFrom(raw: unknown, known: (id: string) => boolean): string[] {
  const out: string[] = []; if (!Array.isArray(raw)) return out;
  for (const id of raw) if (typeof id === "string" && known(id) && !out.includes(id)) out.push(id);
  return out;
}

// ── 사이드바 · 창 ────────────────────────────────────────────────────────────────
export const toggleSumOpen = (s: LensState, id: string): LensState => ({ ...s, sumOpen: s.sumOpen.includes(id) ? without(s.sumOpen, id) : [...s.sumOpen, id] });
export const openSum = (s: LensState, id: string): LensState => ({ ...s, sumOpen: withAdded(s.sumOpen, id) });
export const setNoPath = (s: LensState, id: string | null): LensState => ({ ...s, noPath: id });
/** 같은 창을 다시 누르면 닫는다. */
export const togglePop = (s: LensState, k: "time" | "legend"): LensState => ({ ...s, pop: s.pop === k ? "" : k });
export const closePop = (s: LensState): LensState => (s.pop ? { ...s, pop: "" } : s);
export const armsSettled = (s: LensState): LensState => (s.armsMeasured ? s : { ...s, armsMeasured: true });

// ── 스냅숏과 복원 ───────────────────────────────────────────────────────────────
/** 다시 그릴 때 넘기는 모양. mode 는 전체 보기가 보이면 'whole', 아니면 'path'. filt 는 늘 비어 있다(받는 쪽이 읽지 않는다).
 *  view 는 층 보기의 카메라다. whole 은 전체 보기를 한 번이라도 연 뒤에만 선다(열지 않은 렌즈의 스냅숏은 앞선 모양 그대로다). */
export function snapshotOf(s: LensState): CompassState {
  const out: CompassState = { path: s.path.slice(), hist: s.hist, hi: s.hi, mode: s.whole.on ? "whole" : "path", filt: [], more: s.more.map(([k, v]) => [k, v]),
    koff: s.koff.slice(), tsel: s.tsel, view: s.camera.view, query: s.query };
  const w = s.whole; if (w.on || w.rels || w.cam.view || s.hist.some(isWholeEntry)) out.whole = { cam: { ...w.cam }, rels: w.rels };
  return out;
}
/** 저장된 자리로 되돌린다. `known` 은 이 데이터의 정본 id(없으면 null). 길에 아는 노드가 하나도 없으면 null 을 돌려준다
 *  (받은 쪽은 시작 길에서 연다). 앞선 모양의 mode · filt 는 읽지 않는다: 저장된 'all' 도 한 층씩 연다.
 *  기록이 있으면 그 기록을 그대로 두고(지금 자리는 hi, 끝을 넘지 않게), 없으면 이 길이 첫 기록이다. viewBox 가 있으면
 *  카메라는 그 자리를 지킨다(`view` 는 그린 뒤에 `setView` 로 놓는다). */
export function restoreFrom(s: LensState, R: SavedLens, known: (id: string) => string | null): LensState | null {
  const rp = R.path.map(known).filter((x): x is string => !!x); if (!rp.length) return null;
  let more = s.more; for (const [k, v] of R.more || []) more = entrySet(more, k, v);
  let koff = s.koff; for (const k of R.koff || []) koff = withAdded(koff, k);
  const hist = (R.hist || []).map((h) => h.filter((id) => id === WHOLE_ENTRY || !!known(id))).filter((h) => h.length);
  const hi = Math.min(R.hi ?? hist.length - 1, hist.length - 1);
  const W = R.whole, cam = W && W.cam ? { fit: !!W.cam.fit || !W.cam.view, view: W.cam.view || null, at: W.cam.at || { w: 0, h: 0 } } : s.whole.cam;
  const next: LensState = { ...s, more, koff, tsel: R.tsel || null, query: R.query || "", hist, hi, whole: { ...s.whole, cam, rels: !!(W && W.rels) } };
  // 전체 보기로 남긴 자리: 층의 길은 받은 길 그대로 두고 전체 보기로 연다(지금 기록 칸이 전체 보기가 아니면 한 칸 남긴다)
  if (R.mode === "whole") { const base = { ...next, path: rp, hist: hist.length ? hist : [rp], hi: hist.length ? hi : 0 }; return toWhole(base, !isWholeEntry(base.hist[base.hi])); }
  return enter(R.view ? keepCamera(next) : next, rp, !hist.length);
}
