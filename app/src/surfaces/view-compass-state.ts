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
}

/** 다시 그릴 때 이어 받는 나침반의 자리(`CompassHandle.snapshot`). 이 모양 그대로 나가고 그대로 받는다. */
export interface CompassState { path: string[]; hist: string[][]; hi: number; mode: string; filt: [string, string[]][]; more: [string, number][];
  koff: string[]; tsel: [string, string] | null; view: Box | null; query: string }
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
    armsMeasured: false };
}

// ── 다니기 ────────────────────────────────────────────────────────────────────────
/** 층으로 들어간다(가운데를 고른다). push 면 앞뒤 기록에 남긴다: 지금 자리 뒤의 기록은 버린다. */
export function enter(s: LensState, path: string[], push = true): LensState {
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
/** Backspace: 한 층 위로(뿌리에서는 그대로). */
export const upOne = (s: LensState): LensState => (s.path.length > 1 ? enter(s, s.path.slice(0, -1)) : s);
/** Alt+← · Alt+→: 기록을 한 칸 옮긴다. `known` 은 이 데이터에 있는 id 를 정본 id 로, 없으면 null. 끝이면 그대로 돌려준다. */
export function historyStep(s: LensState, dir: -1 | 1, known: (id: string) => string | null): LensState {
  const hi = s.hi + dir; if (dir < 0 ? s.hi <= 0 : s.hi >= s.hist.length - 1) return s;
  return { ...s, hi, path: s.hist[hi].map(known).filter((x): x is string => !!x) };
}
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
/** 굴려서 확대: 포인터 자리(칸 안의 비율 fx, fy)를 고정점으로 deltaY 만큼. 날던 카메라는 멈춘다. */
export function zoomAt(s: LensState, fx: number, fy: number, deltaY: number, width: number): LensState {
  const v = s.camera.view as Box, f = Math.exp(deltaY * .0015);
  const px = v.x + fx * v.w, py = v.y + fy * v.h;
  return withCamera(s, { fly: null, view: { x: px - (px - v.x) * f, y: py - (py - v.y) * f, w: v.w * f, h: v.h * f }, lodW: width });
}
export const panStart = (s: LensState, x: number, y: number): LensState => ({ ...s, pan: { x, y, v: { ...(s.camera.view as Box) } } });
/** 끄는 중이면 누른 자리에서 움직인 만큼 viewBox 를 민다. rw 는 칸의 그려진 폭(화면 px 를 그림 단위로 바꾼다),
 *  width 는 칸의 정수 폭(확대 단계에 쓴다). */
export function panMove(s: LensState, x: number, y: number, rw: number, width: number): LensState {
  const p = s.pan; if (!p) return s; const k = p.v.w / rw;
  return withCamera(s, { view: { ...p.v, x: p.v.x - (x - p.x) * k, y: p.v.y - (y - p.y) * k }, lodW: width });
}
export const panEnd = (s: LensState): LensState => ({ ...s, pan: null });
/** 확대 단계: 멀리서 볼수록 덜 그린다. 1단은 이름 · 목록 글자를, 2단은 목록 줄과 잎 노드를 숨긴다. */
export function lodOf(c: Camera): { lod1: boolean; lod2: boolean } {
  const z = c.view ? c.view.w / (c.lodW || 1) : 1; return { lod1: z > 2.4, lod2: z > 5 };
}
export const viewBoxAttr = (v: Box): string => `${v.x.toFixed(1)} ${v.y.toFixed(1)} ${v.w.toFixed(1)} ${v.h.toFixed(1)}`;

// ── 크기 · 테마 · 글꼴 ────────────────────────────────────────────────────────────
/** 그래프 칸의 크기가 바뀌었다. 카메라는 뜻을 지킨다: viewBox 는 같은 그림 범위를 그대로 담고(SVG 가 칸에 맞춰 늘고 준다),
 *  확대 단계는 다음에 카메라가 움직일 때 다시 잰다. */
export const resize = (s: LensState, size: Size): LensState => (s.size.w === size.w && s.size.h === size.h ? s : { ...s, size });
export const themeChanged = (s: LensState, theme: ThemeMode): LensState => ({ ...s, theme });
export const fontsSettled = (s: LensState): LensState => ({ ...s, fontKey: s.fontKey + 1 });
/** 이름표 글자 하나의 어림 폭(px). 글꼴과 상관없는 고정값이다(배치는 이 값으로 이름표 자리를 잰다).
 *  실제 글꼴로 재려면 여기서 fontKey 마다 잰 값을 돌려준다: 지금 그림이 바뀌므로 그 일은 따로 한다. */
export function glyphWidth(_fontKey: number, kind: "fan" | "bridge"): number { return kind === "fan" ? 6.6 : 7; }

// ── 고르기 · 거르기 ──────────────────────────────────────────────────────────────
export const hover = (s: LensState, id: string | null): LensState => (s.hover === id ? s : { ...s, hover: id });
export const setQuery = (s: LensState, q: string): LensState => ({ ...s, query: String(q || "").toLowerCase() });
export const setRange = (s: LensState, tsel: [string, string] | null): LensState => ({ ...s, tsel });
export const toggleKind = (s: LensState, k: string): LensState => ({ ...s, koff: s.koff.includes(k) ? without(s.koff, k) : [...s.koff, k] });
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
/** 다시 그릴 때 넘기는 모양. mode 는 늘 'path', filt 는 늘 비어 있다(받는 쪽이 읽지 않는다). */
export const snapshotOf = (s: LensState): CompassState => ({ path: s.path.slice(), hist: s.hist, hi: s.hi, mode: "path", filt: [], more: s.more.map(([k, v]) => [k, v]),
  koff: s.koff.slice(), tsel: s.tsel, view: s.camera.view, query: s.query });
/** 저장된 자리로 되돌린다. `known` 은 이 데이터의 정본 id(없으면 null). 길에 아는 노드가 하나도 없으면 null 을 돌려준다
 *  (받은 쪽은 시작 길에서 연다). 앞선 모양의 mode · filt 는 읽지 않는다: 저장된 'all' 도 한 층씩 연다.
 *  기록이 있으면 그 기록을 그대로 두고(지금 자리는 hi, 끝을 넘지 않게), 없으면 이 길이 첫 기록이다. viewBox 가 있으면
 *  카메라는 그 자리를 지킨다(`view` 는 그린 뒤에 `setView` 로 놓는다). */
export function restoreFrom(s: LensState, R: SavedLens, known: (id: string) => string | null): LensState | null {
  const rp = R.path.map(known).filter((x): x is string => !!x); if (!rp.length) return null;
  let more = s.more; for (const [k, v] of R.more || []) more = entrySet(more, k, v);
  let koff = s.koff; for (const k of R.koff || []) koff = withAdded(koff, k);
  const hist = (R.hist || []).map((h) => h.filter((id) => !!known(id))).filter((h) => h.length);
  const hi = Math.min(R.hi ?? hist.length - 1, hist.length - 1);
  const next: LensState = { ...s, more, koff, tsel: R.tsel || null, query: R.query || "", hist, hi };
  return enter(R.view ? keepCamera(next) : next, rp, !hist.length);
}
