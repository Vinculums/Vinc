/** 그래프 탭의 나침반 렌즈 — 가운데가 지금 보는 것(주제 또는 노드)이고, 누르면 그 자리에서 같은 나침반으로 펼친다.
 *
 *  자리는 나침반으로 정해져 있다(온 방향을 따라 돈다): 12시 = 부모(온 곳), 9시 = 뜻의 관계·이 주제의 개념, 3시 = 하위 주제와 그 사이의
 *  다리·여기서 나온 것, 6시 = 줄기로 이어진 결정·기록·사건. 주제가 가운데면 하위 주제는 둘레 전체에 고르게 선다. 지나온 층은 옅게 남는다.
 *  선은 렌즈 문법 그대로다: 뜻의 관계 = 빛살(가운데 쪽이 굵고 양끝은 노드 둘레를 띄운 원호), 방향 있는 관계 = 가운데의 표(›, ∘›, |›),
 *  붙은 것 = 줄기와 가지(꺾이는 곳은 둥글게). 다른 주제의 노드에는 가운데 주제 색의 초승달, 다리는 잇는 주제마다 한 조각인 갈래 고리.
 *  길·찾기·기간·종류·고정·읽는 법은 LNB 의 칸(side)에 선다(기간 밖은 흐리게, 끈 종류는 뺀다). 고정은 이 기기에만 저장한다.
 *  SVG 하나라 GPU 가 없어도 된다. 한 번에 그리는 것은 펼친 층의 이웃뿐이다. */
import { COPY } from "../copy";
import { esc as escHtml } from "../components";
import { tintPlan, topicRing } from "../view-tint";
import { topicJewels } from "../theme";
import * as LS from "./view-compass-state";
import type { LensState, CompassState, Box, Point } from "./view-compass-state";
import { layoutWhole, lodTier, labelShown, placeLabels, gridIndex, farArrange, farPlan, attribution, fitChoice, FONT, RING_SEP,
  type WLayout, type WTopicIn, type WItem, type FarArr, type Pt as WPt } from "./view-compass-whole";
import { shortcutsRest } from "../keys";
import type { GraphNode } from "../api";
export type { CompassState } from "./view-compass-state";

/** 나침반이 읽는 그래프 노드(compassDataFrom 의 모양). k: 종류('doc' 은 문서 미러), t: 이름, d: 주제 경로(빈 값이면 뿌리), ts · ct · vf · vu: 날짜들,
 *  g: 이음 수, m: 뜻의 관계 수 */
export interface CompassNode { id: string; k: string; t: string; d: string; ts: string; ct?: string; vf?: string; vu?: string; g: number; m: number }
export interface CompassData { nodes: CompassNode[]; edges: [string, string, string][] }
/** compassDataFrom 이 읽는 그래프 읽기의 칸(GraphNode · GraphEdge 와, 읽기에 따라 함께 오는 title · updated_at · 객체 끝). */
type GraphNodeIn = Pick<GraphNode, "id" | "kind" | "label" | "domain" | "degree" | "ts" | "content_ts" | "valid_from" | "valid_until" | "props"> & { title?: string; updated_at?: string };
type GraphEnd = string | { id: string };
type GraphEdgeIn = { source: GraphEnd; target: GraphEnd; type: string; presentation?: boolean };
/** 나침반 안의 노드: 그래프 노드(DNode)와 domain 사슬로 지은 주제(TNode, k 는 'topic'). 한쪽에만 있는 칸은 다른 쪽에서 비어 있다. */
interface DNode extends CompassNode { path?: undefined; direct?: undefined; sub?: undefined; count?: undefined }
interface TNode { id: string; k: "topic"; path: string; direct: DNode[]; sub: Set<string>; count: number; ts?: string; t?: string;
  d?: undefined; ct?: undefined; vf?: undefined; vu?: undefined; g?: undefined; m?: undefined }
type LNode = DNode | TNode;
/** 두 그래프 노드 사이의 관계 하나(a → b, 종류 t). */
interface Edge { a: DNode; b: DNode; t: string }
type Pt = [number, number];
/** 한 층에 놓을 이웃 하나: 노드와 관계 이름(rel), 그 관계(e). 다리는 잇는 하위 주제(subs)와 층 밖으로도 잇는지(outside)를 든다. */
interface Unit { n: LNode; rel?: string; e?: Edge; main?: boolean; label?: string; farEnds?: string[] }
type Shelf = { decision: LNode[]; record: LNode[]; episode: LNode[]; doc: LNode[]; [k: string]: LNode[] };
interface Gathered { up: Unit[]; left: Unit[]; right: Unit[]; bridges: { n: LNode; subs: (string | null)[]; outside: boolean }[]; shelf: Shelf }
/** 한 층(나침반 하나)의 내용과 크기(rL · rR: 부채 반지름, E: 나침반 둘레, FP: 목록까지 친 둘레). */
interface Cp { node: LNode; G: Gathered; rL: number; rR: number; shelfN: number; E: number; FP: number }
/** 배치가 놓은 것 하나(노드 · 허브 · 목록 줄 · 다리 · 고정 노드). */
interface Item { id: string; src: LNode; k: string; x: number; y: number; r: number; c: string; label: string; role: string; level: number;
  rel?: string; e?: Edge; ang?: number; comp?: string; chain?: LNode[]; parentHub?: Item; viaE?: Edge; route?: Pt[]; subs?: string[]; outside?: boolean;
  farEnds?: string[]; pinned?: boolean; link?: undefined; linkId?: undefined }
/** 층의 줄에 든 것: 놓은 것, 이미 놓인 것으로 잇는 빛살(link), 나중에 이을 빛살(linkId). */
type Slot = Item | { link: Item; e?: Edge; linkId?: undefined; id?: undefined; comp?: undefined; role?: undefined } | { linkId: string; e?: Edge; link?: undefined; id?: undefined; comp?: undefined; role?: undefined };
interface Kinds { k: string; name: string; all: LNode[] }
interface ShelfCol extends Kinds { x: number; items: Item[] }
/** 놓은 층 하나. */
interface Comp { cp: Cp; hub: Item; th12: number; slots: Map<string, { ang: number; r: number; e?: Edge }>; focus: boolean; level: number; items: Slot[]; chain: LNode[];
  parent: Comp | null; pinOnly: boolean; free: number | null; kinds?: Kinds[]; stub?: { x: number; y: number; text: string; ang: number }; far?: boolean }
/** 층 하나의 목록 자리: 버스(Q · BY)와 줄들. */
interface ShelfSet { hub: Item; comp: Comp; shelves: ShelfCol[]; Q: { x: number; y: number; a6?: number }; BY: number; mode: string; side: number; a6: number }
type Placement = Map<string, { x: number; y: number; th12: number; route?: Pt[] }>;
/** 배치 한 번의 결과. */
interface Layout { items: Item[]; comps: Comp[]; focus: Comp; shelves: ShelfCol[]; sets: ShelfSet[]; Q: ShelfSet["Q"] | null; BY: number; mode: string; side: number; a6: number;
  boxes?: (c: Comp) => number[][]; pinned?: Item[] }
/** 목록 가지의 조각: 곧은 토막(seg) · 꺾임(arc) · 모서리(fil) · 끝마감(cap). d0 · d1 은 허브에서 잰 길이(자라는 순서). */
type Prim = { t: 'seg'; a: Pt; b: Pt; d0: number; d1: number; c: string } | { t: 'arc'; cx: number; cy: number; R: number; a0: number; a1: number; d0: number; d1: number; c: string }
  | { t: 'fil'; jx: number; jy: number; wv: number; wh: number; sx: number; sy: number; F: number; d0: number; d1?: undefined; c: string } | { t: 'cap'; n: Item; d0: number; d1?: undefined; c: string };
/** DOM 의 경계: 리스너가 받는 사건(대상은 요소). */
type At<E extends Event> = E & { target: Element };
/** 창에 거는 키 리스너의 사건: 대상이 요소가 아닐 수도 있다(창 · 문서). */
type WinKey = KeyboardEvent & { target: { closest?: (sel: string) => Element | null } };
/** 층 하나의 격자: 열 수(k) · 열 폭(cw) · 줄 높이(rh) · 전체(W · H). */
type Grid = { k: number; cw: number[]; rh: number[]; W: number; H: number };
type Packed = Grid & { w: number; h: number; sc: number };
/** 층(과 그 자식 격자)의 상자: 자기 층(w0 · h0)과 전체(w · h). 자식이 있으면 격자도 든다. */
type Sized = { w: number; h: number; w0: number; h0: number } & Partial<Packed>;
/** 팔 하나: 방향(g · p) · 가로인지 · 잰 방향(o) · 층의 12시 · 상자 표 · 격자의 원점(O). */
type Arm = { g: Pt; p: Pt; hz: boolean; o: 'v' | 'u' | 'h'; th12: number; B: Map<Comp, Sized>; O?: Pt };
type Meas = Map<string, Partial<Record<"v" | "u" | "h", { top: number; dep: number; hw: number }>>>;
/** 정해진 자리(팔)의 결과: 주제 층의 자리와 주제마다 고른 팔. */
interface Plan { out: Placement; arms: Map<string, number> }
/** 배치 한 번이 읽는 것(curS · measS). */
interface LayoutIn { mode: string; path: LNode[]; place: Placement | null; dirs: Placement | null; canon: boolean; range: [string, string] | null; koff: Set<string>;
  more: [string, number][]; pins: Set<string>; pinChain: Set<string>; filt?: Map<string, unknown>; cps?: Map<string, Cp> }
export interface CompassCtx { startPath?: string[]; restore?: CompassState | null; store?: string; onChange?: () => void; onOpen?: (id: string) => void; onDestroy?: () => void;
  onTree?: (o: { reveal: string[]; reset: boolean }) => void }
export interface CompassHandle { destroy: () => void; side: HTMLElement; tools: HTMLElement; filter: (q: string) => void; open: (id: string) => void;
  tree: () => { domain: string; rows: { id: string; title: string; kind: string; depth: number }[] }[]; mark: (kind: string) => string; colour: (domain: string) => string; centre: () => string;
  snapshot: () => CompassState; retheme: () => void }

const themeMode = () => (document.documentElement.dataset.theme === "dark" ? "dark" : "light");

/** 그래프 탭의 읽기(GraphNode·GraphEdge)를 나침반이 읽는 모양으로. 주제(domain) 노드는 노드의 domain 사슬에서 다시 만든다. */
const idDate = (id: string) => { const m = /-(20\d\d-\d\d-\d\d)$/.exec(String(id)); return m ? m[1] : ""; };
export function compassDataFrom(nodes: readonly GraphNodeIn[], edges: readonly GraphEdgeIn[]): CompassData {
  const out: CompassNode[] = nodes.filter((n) => n.kind !== "domain" && n.kind !== "area").map((n) => ({
    id: n.id, k: String(n.id).startsWith("concept:doc.") ? "doc" : n.kind as string, t: n.label || n.title || n.id, d: n.domain || "",   // 주제 없는 노드는 빈 값 그대로 둔다: 어느 뿌리에 설지는 mountCompass 가 정한다
    // 날짜: 사건은 그래프 읽기의 ts 가 곧 그 일이 있었던 날이다(updated_at 은 마지막으로 고친 때라 뒤에 둔다).
    // 결정은 내용 시간 칸이 없어 props.valid_from(결정한 날)을, 그것도 없으면 id 끝의 날짜(…-2026-09-20, 이 그래프의 이름 관례)를 쓴다.
    ts: String(n.ts || n.updated_at || "").slice(0, 10), ct: String(n.content_ts || "").slice(0, 10) || idDate(n.id) || undefined,
    vf: String(n.valid_from || n.props?.valid_from || "").slice(0, 10) || undefined, vu: n.valid_until || undefined, g: n.degree || 0, m: 0 }));
  const byId = new Map(out.map((n) => [n.id, n])), M = new Set(["RELATED_TO", "PREREQUISITE_OF", "DERIVES_FROM", "INHERITS_FROM", "RETURNS_TO", "CONNECTS"]);
  const es: [string, string, string][] = [];
  for (const e of edges) { const a = typeof e.source === "object" ? e.source.id : e.source, b = typeof e.target === "object" ? e.target.id : e.target;
    if (!byId.has(a) || !byId.has(b) || e.presentation) continue; es.push([a, b, e.type]); if (M.has(e.type)) { byId.get(a)!.m++; byId.get(b)!.m++; } }
  return { nodes: out, edges: es };
}

/** 고정 핀(압정). 45° 기운 머리 · 목 · 깃 · 바늘. 채움은 currentColor 라 놓인 자리의 색을 따른다. */
const PIN_PATHS = `<g transform="rotate(45 12 12)"><rect x="7.2" y="1.6" width="9.6" height="4" rx="2" fill="currentColor"/><path d="M9.3 5.4h5.4v3.9l3.4 3.9H5.9l3.4-3.9z" fill="currentColor"/><path d="M12 13.2V21.6" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"/></g>`;
const pinIcon = (s: number = 14) => `<svg class="pin-ic" viewBox="0 0 24 24" width="${s}" height="${s}" aria-hidden="true">${PIN_PATHS}</svg>`;
const CMP_HTML = () => `<div class="cmp-stage"><svg class="g" id="cmp-g" role="group" aria-label="${escHtml(COPY.ui.compass.lens)}"></svg></div><div id="cmp-tip"></div>`;
/** LNB 맨 위의 검색 칸(topbar.ts, setSearchFilter) · 시간 · 범례의 종류가 한 거르개다: 함께 건 결과가 나침반과 LNB 트리(lnb.ts
 *  setLnbGraphSource)에 같이 나온다. LNB 에는 고정 판만 서고, 시간과 범례는 머리의 "그래프" 글자 오른쪽 버튼이 여는 창이다. */
const SIDE_HTML = `<div class="cmp-sec" id="cmp-sum"></div><div class="cmp-sec" id="cmp-dock" hidden></div>`;
/** 머리의 전체 보기 단추: 둘레에 주제 허브 넷을 단 가운데 고리. 머리가 좁으면 이름을 접고 이 표만 남는다(app.css) */
const WHOLE_ICON = `<svg class="cmp-tic" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><circle cx="8" cy="8" r="2.4" fill="none" stroke="currentColor" stroke-width="1.5"/><circle cx="2.8" cy="3" r="1.5" fill="currentColor"/><circle cx="13.2" cy="3" r="1.5" fill="currentColor"/><circle cx="2.8" cy="13" r="1.5" fill="currentColor"/><circle cx="13.2" cy="13" r="1.5" fill="currentColor"/><path d="M4.1 4.2L6.2 6.3M11.9 4.2L9.8 6.3M4.1 11.8L6.2 9.7M11.9 11.8L9.8 9.7" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>`;
const TOOLS_HTML = () => `<button class="cmp-tbtn" type="button" data-pop="time" aria-haspopup="dialog" aria-expanded="false">${escHtml(COPY.ui.compass.time)}</button><button class="cmp-tbtn" type="button" data-pop="legend" aria-haspopup="dialog" aria-expanded="false">${escHtml(COPY.ui.compass.legend)}</button>`
  + `<button class="cmp-tbtn cmp-twhole" type="button" data-whole aria-pressed="false" aria-label="${escHtml(COPY.ui.compass.whole)}" title="${escHtml(COPY.ui.compass.wholeTip)}">${WHOLE_ICON}<span class="cmp-tname">${escHtml(COPY.ui.compass.whole)}</span></button>`;

const C0 = () => COPY.ui.compass;
/** 층의 실제 크기(measureCross)를 데이터별로 한 번만 잰다. 다시 그릴 때(새로고침 · 쓰고 난 뒤 마운트) 같은 주제 나무면 그대로 쓴다. */
const MEAS_CACHE = new Map<string, Meas>();
/** 데이터의 값 하나를 처음 쓸 때 한 번 센다. */
const memo = <T,>(f: () => T): (() => T) => { let done = false, v: T; return () => { if (!done) { v = f(); done = true; } return v; }; };
/** 마운트 하나의 효과(리스너의 신호 · 이름 붙은 프레임과 타이머와 유휴 콜백 · 관찰자)를 한 곳에 든다. 이름 하나에 하나씩이고 release 가 모두 놓는다.
 *  유휴 콜백은 requestIdleCallback 이 없을 때만 setTimeout(300ms)이고, 지울 때도 그 짝(cancelIdleCallback, 없으면 clearTimeout)으로만
 *  지운다: 유휴 콜백의 번호를 clearTimeout 에 넘기면 번호가 같은 남의 타이머가 지워진다 */
type Effects = { signal: AbortSignal; frame(k: string, f: FrameRequestCallback): void; cancelFrame(k: string): void; after(k: string, ms: number, f: () => void): void;
  cancelAfter(k: string): void; idle(k: string, f: () => void): void; cancelIdle(k: string): void; observe(o: ResizeObserver, el: Element): void; release(): void };
function effectsOwner(): Effects {
  const ac = new AbortController(), frames = new Map<string, number>(), timers = new Map<string, ReturnType<typeof setTimeout>>(), idles = new Map<string, number>(), observers: ResizeObserver[] = [];
  const cancelIdle = (k: string) => (globalThis.cancelIdleCallback || clearTimeout)(idles.get(k) || 0);
  return {
    signal: ac.signal,
    frame(k, f) { frames.set(k, requestAnimationFrame(f)); },
    cancelFrame(k) { cancelAnimationFrame(frames.get(k) || 0); },
    after(k, ms, f) { timers.set(k, setTimeout(f, ms)); },
    cancelAfter(k) { clearTimeout(timers.get(k) || 0); },
    idle(k, f) { idles.set(k, (globalThis.requestIdleCallback || ((g: () => void) => setTimeout(g, 300)))(f)); },
    cancelIdle,
    observe(o, el) { o.observe(el); observers.push(o); },
    release() { ac.abort(); for (const k of idles.keys()) cancelIdle(k); for (const id of frames.values()) cancelAnimationFrame(id); for (const id of timers.values()) clearTimeout(id); for (const o of observers) o.disconnect(); },
  };
}
export function mountCompass(host: HTMLElement, data: CompassData, ctx: CompassCtx = {}): CompassHandle {
  const C = C0();
  // 리스너 · 프레임 · 타이머 · 유휴 콜백 · 관찰자는 이 한 곳(fx)이 들고, destroy 가 모두 놓는다
  const fx = effectsOwner();
  const onWin = (t: string, f: (e: never) => void, o?: boolean | AddEventListenerOptions) => window.addEventListener(t, f as EventListener, { ...(typeof o === "object" ? o : { capture: !!o }), signal: fx.signal });
  const on = (el: EventTarget, t: string, f: (e: never) => void, o: AddEventListenerOptions = {}) => el.addEventListener(t, f as EventListener, { ...o, signal: fx.signal });
  // 렌즈의 상태는 이 한 칸(st)이다. 상태 모듈의 전이가 만든 새 상태로만 갈아 끼운다(set). 그리기는 읽기만 한다
  let st: LensState = LS.initialState(themeMode());
  const set = (next: LensState) => { st = next; };
  host.innerHTML = `<div class="cmp">${CMP_HTML()}</div>`;
  const root = host.querySelector(".cmp") as HTMLElement;
  const side = document.createElement("div"); side.className = "cmp-side"; side.innerHTML = SIDE_HTML;
  const tools = document.createElement("span"); tools.className = "cmp-tools"; tools.innerHTML = TOOLS_HTML();
  const pop = document.createElement("div"); pop.className = "cmp-side cmp-pop"; pop.setAttribute("role", "dialog"); pop.hidden = true; document.body.append(pop);
  const $id = (k: string) => { const sel = k === "g" ? "#cmp-g" : `#cmp-${k.replace(/^cmp-/, "")}`; return root.querySelector(sel) || side.querySelector(sel) || pop.querySelector(sel); };

  const W0 = data;
  const ATTACH = new Set(['ABOUT', 'ATTACHES_TO', 'MENTIONS']);
  // 뿌리: 이 그래프의 최상위 주제가 하나뿐이면 그 주제가 뿌리다('vinc' 만 쓰는 그래프는 예전과 같다). 둘 이상이거나 하나도 없으면
  // 이름 없는 가상 뿌리('')가 모든 최상위 주제를 품는다. 주제 없는 노드는 뿌리에 직접 서고, 이름을 지어 붙이지 않는다.
  // RD 는 뿌리가 경로에서 차지하는 칸 수다(영역 = 뿌리 다음 칸을 자를 때 쓴다). ⚠ 코드 어디에도 최상위 이름을 박지 말 것.
  const tops = new Set(W0.nodes.map((n) => String(n.d || '').split('/')[0]).filter(Boolean));
  const ROOT = tops.size === 1 ? [...tops][0] : '', RD = ROOT ? 1 : 0;
  const nodes: DNode[] = W0.nodes.map((n) => (n.d ? n : { ...n, d: ROOT })), byId = new Map(nodes.map((n) => [n.id, n]));
  const edges = W0.edges.map(([a, b, t]) => ({ a: byId.get(a), b: byId.get(b), t })).filter((e) => e.a && e.b) as Edge[];
  const adj = new Map<string, Edge[]>(nodes.map((n) => [n.id, []])); for (const e of edges) { adj.get(e.a.id)!.push(e); adj.get(e.b.id)!.push(e); }
  // 다리는 주제를 갖지 않는다(여러 주제 사이에 있다). 그래서 다리를 끝들이 만나는 주제, 곧 끝 노드들 주제의 가장 가까운 공통 조상에 둔다.
  // 같은 영역 안의 끝들을 잇는 다리는 그 영역 층에 서고, 영역을 넘는 다리만 뿌리에 남는다. 끝마다의 주제(BENDS)는 끝 층에서 다리를 보일 때 쓴다
  const BENDS = new Map<string, string[]>();
  for (const b of nodes) { if (b.k !== 'bridge') continue; const raw = W0.nodes.find((n) => n.id === b.id);
    const ds = [...new Set(adj.get(b.id)!.filter((e) => e.t === 'CONNECTS').map((e) => (e.a === b ? e.b : e.a).d).filter((d) => d && d !== ROOT))];
    BENDS.set(b.id, ds); if (raw && raw.d || !ds.length) continue;
    const seg = ds.map((d) => d.split('/')); let k = 0; while (seg.every((x) => k < x.length && x[k] === seg[0][k])) k++;
    if (k > RD) b.d = seg[0].slice(0, k).join('/'); }
  const fresh = (n: LNode) => { const w = when(n); return !!w && (Date.parse(newestW()) - Date.parse(w)) / 864e5 <= 7; };   // '최근' = 내용 시간으로 7일 안
  const newestW = memo(() => nodes.map(when).filter(Boolean).sort().pop() || '');
  // 주제: 노드의 domain 사슬에서 만든다. 'vinc/agent/roles' → vinc, vinc/agent, vinc/agent/roles (가상 뿌리면 '' 가 맨 앞에 붙는다)
  const topics = new Map<string, TNode>();
  const tget = (p: string): TNode => { if (!topics.has(p)) topics.set(p, { id: `topic:${p}`, path: p, k: 'topic', direct: [], sub: new Set(), count: 0 }); return topics.get(p)!; };
  const chainOf = (d: string | undefined) => { const o: string[] = ROOT ? [] : [''], s = d ? d.split('/') : []; for (let i = 1; i <= s.length; i++) o.push(s.slice(0, i).join('/')); return o; };
  const rootTopic = tget(ROOT); if (!ROOT) rootTopic.t = C.rootTopic;   // 뿌리는 노드가 없어도 늘 있다. 가상 뿌리만 이름표(t)를 갖는다
  for (const n of nodes) { const ch = chainOf(n.d);
    ch.forEach((p, i) => { const t = tget(p); t.count++; if ((n.ts || '') > (t.ts || '')) t.ts = n.ts; if (i < ch.length - 1) t.sub.add(ch[i + 1]); });
    tget(ch[ch.length - 1]).direct.push(n); }
  // ── 주제 색: 그래프 보기와 같은 규칙(tintPlan)이다. 영역마다 보석색 하나, 그 안의 주제는 색상각을 조금씩 돌린다 ─────────────
  // 주제 색 표는 테마의 함수다(tintPlan). 테마마다 한 번 짓고, 그리기는 지금 상태의 테마로 읽는다
  const TINTS = new Map<string, ReturnType<typeof tintPlan>>();
  const tints = () => { let t = TINTS.get(st.theme); if (!t) { t = tintPlan([...topics.keys()], st.theme); TINTS.set(st.theme, t); } return t; };
  const topicColour = (p: string | undefined) => { if (!p || p === ROOT) return 'hsl(var(--muted-foreground))';   // 뿌리는 어느 영역도 아니라 중립색
    const s = p.split('/'); for (let i = s.length; i >= 1; i--) { const c = tints().topics.get(s.slice(0, i).join('/')); if (c) return c; } return 'hsl(var(--muted-foreground))'; };
  const topicOf = (n: LNode) => (n.k === 'topic' ? n.path : n.d);
  // 섞기: 주제 색을 카드색 쪽으로 바랜다. 라이트는 카드가 거의 흰색이고 보석 토큰의 대비 바닥도 3:1(다크 5.5:1)이라
  // 같은 비율로 바래면 빛살 끝이 배경 대비 1.3 안팎으로 사라진다. 그래서 라이트에서는 바래는 몫을 45%만 쓴다(30% → 69%, 55% → 80%)
  const colv = (n: LNode) => topicColour(topicOf(n)), mix = (c: string, p: number) => `color-mix(in srgb, ${c} ${st.theme === 'light' ? Math.round(100 - (100 - p) * .45) : p}%, var(--c-card))`;
  const isMean = (n: LNode) => ['concept', 'doc', 'bridge'].includes(n.k) || n.m! > 0;
  const titleOf = (n: LNode): string => { if (n.k === 'topic') return n.path ? n.path.split('/').pop()! : C.rootTopic; const s = (n.t || n.id).replace(/\s*\((?:\d{4}-\d\d-\d\d|pstack principle|Anthropic)[^)]*\)/g, '');
    return ['concept', 'doc', 'bridge'].includes(n.k) ? s.replace(/^([^:]{3,40}):.*$/, '$1') : s; };
  const short = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1) + '…' : s);
  const GW = (k: 'fan' | 'bridge') => LS.glyphWidth(st.fontKey, k);   // 이름표 글자 하나의 폭: 배치는 이 한 곳에서 읽는다
  const esc = (s: unknown) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  const KIND: Record<string, string> = COPY.ui.compass.kinds;

  // ── 모양·빛살·표(문법) ─────────────────────────────────────────────────────────────────────
  const GAP = 4, P = (pts: Pt[]) => pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  const P2 = (pts: Pt[]) => pts.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(' ');   // 줄기 조각: 0.1 단위로 자르면 확대했을 때 호가 각진다
  const arcPts = (cx0: number, cy0: number, r: number, mid: number, half: number, n = 6) => { const o: Pt[] = []; for (let q = 0; q <= n; q++) { const a = mid - half + (2 * half * q) / n; o.push([cx0 + Math.cos(a) * r, cy0 + Math.sin(a) * r]); } return o; };
  function shape(kind: string, x: number, y: number, r: number, fill: string, hollow?: boolean) {
    const st = hollow ? `fill="none" stroke="${fill}" stroke-width="1.6"` : `fill="${fill}"`;
    if (kind === 'doc') return `<g class="shape"><circle cx="${x}" cy="${y}" r="${r}" ${st}/><rect x="${x - r * .42}" y="${y - r * .12}" width="${r * .84}" height="${r * .24}" rx="${r * .1}" fill="var(--c-card)"/></g>`;
    if (kind === 'decision') { const d = r * 1.25; return `<path class="shape" d="M${x} ${y - d}L${x + d} ${y}L${x} ${y + d}L${x - d} ${y}Z" ${st}/>`; }
    if (kind === 'record') { const s = r * .9; return `<rect class="shape" x="${x - s}" y="${y - s}" width="${2 * s}" height="${2 * s}" rx="${r * .25}" ${st}/>`; }
    if (kind === 'episode') { const h = r * 1.2; return `<path class="shape" d="M${x} ${y - h}L${x + h * 1.05} ${y + h * .75}L${x - h * 1.05} ${y + h * .75}Z" ${st} stroke-linejoin="round"/>`; }
    // 다리는 범례와 같은 이음 표식(위아래 호 둘과 가운데 점)이다. 끝 주제 색을 아는 자리는 `junction` 이 그린다
    if (kind === 'bridge') { const k = r * .7, w = Math.max(1.6, r * .36).toFixed(2);
      return `<g class="shape"><path d="M${x - k} ${y - k}A${r} ${r} 0 0 1 ${x + k} ${y - k}M${x + k} ${y + k}A${r} ${r} 0 0 1 ${x - k} ${y + k}" fill="none" stroke="${fill}" stroke-width="${w}" stroke-linecap="round"/><circle cx="${x}" cy="${y}" r="${(r * .33).toFixed(2)}" fill="${fill}"/></g>`; }
    return `<circle class="shape" cx="${x}" cy="${y}" r="${r}" ${st}/>`;
  }
  // 빛살: 가운데 쪽이 굵고 바깥으로 가늘다. 양끝은 노드와 같은 중심의 원호로, 틈만큼 떨어져 잘린다
  type End = { id: string; x: number; y: number; r: number; c: string };
  function ray(a: End, b: End, i: number, w0 = 3, w1 = .5) {
    if (st.theme === 'light') { w0 *= 1.45; w1 = Math.max(w1 * 1.6, 1); }   // 밝은 바탕에서는 가는 띠가 묻힌다: 라이트만 굵게, 바깥 끝도 1 아래로 가늘어지지 않게
    const th = Math.atan2(b.y - a.y, b.x - a.x), ra = a.r + GAP, rb = b.r + GAP;
    const pts = [...arcPts(a.x, a.y, ra, th, Math.asin(Math.min(.95, w0 / ra))), ...arcPts(b.x, b.y, rb, th + Math.PI, Math.asin(Math.min(.95, w1 / rb))).reverse()];
    const x0 = a.x + Math.cos(th) * ra, y0 = a.y + Math.sin(th) * ra, x1 = b.x - Math.cos(th) * rb, y1 = b.y - Math.sin(th) * rb;
    return { def: `<linearGradient id="r${i}" gradientUnits="userSpaceOnUse" x1="${x0}" y1="${y0}" x2="${x1}" y2="${y1}"><stop offset="0" style="stop-color:${mix(a.c, 85)}"/><stop offset="1" style="stop-color:${mix(b.c, 30)}"/></linearGradient>`,
             svg: `<polygon class="ray" data-a="${a.id}" data-b="${b.id}" points="${P(pts)}" style="fill:url(#r${i})"/>`, x0, y0, x1, y1 };
  }
  function bead(x: number, y: number, th: number, c: string, kind: string) {
    const p = (dx: number, dy: number): Pt => [x + Math.cos(th) * dx - Math.sin(th) * dy, y + Math.sin(th) * dx + Math.cos(th) * dy], f = (q: Pt) => `${q[0].toFixed(1)} ${q[1].toFixed(1)}`;
    let d = `M${f(p(-1.5, -4))}L${f(p(2.5, 0))}L${f(p(-1.5, 4))}`, extra = '';
    if (kind === 'INHERITS_FROM') d += `M${f(p(-6, -4))}L${f(p(-6, 4))}`;
    if (kind === 'DERIVES_FROM') { const q = p(-6.5, 0); extra = `<circle cx="${q[0].toFixed(1)}" cy="${q[1].toFixed(1)}" r="2.2" fill="var(--c-card)" stroke="${c}" stroke-width="1.4"/>`; }
    return `<path class="ray" d="${d}" fill="none" stroke="${c}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>${extra}`;
  }

  // ── 이웃 모으기: 가운데가 노드일 때와 주제일 때 ──────────────────────────────────────────────
  // 나침반의 네 자리에 무엇이 가는가(가운데가 주제든 노드든 같은 규칙):
  //   12시 = 어디서 왔나(위 주제는 정확히 위, 원천·부모·앞선 것·붙은 곳·새것은 그 좌우로)
  //    9시 = 뜻의 관계(노드) / 이 주제에 직접 속한 개념(주제)
  //    3시 = 그 아래 구성(하위 주제와 그 사이를 잇는 다리) / 여기서 나온 것·물려받은 것·뒤의 것·옛것(노드)
  //    6시 = 줄기가 곧게 내려가 붙은 결정·기록·사건·문서의 목록으로(최신순)
  const bridgeEnds = (b: LNode) => adj.get(b.id)!.filter((e) => e.t === 'CONNECTS').map((e) => (e.a === b ? e.b : e.a));
  function gather(c: LNode): Gathered {
    const up: Unit[] = [], left: Unit[] = [], right: Unit[] = [], bridges: Gathered['bridges'] = [], shelf: Shelf = { decision: [], record: [], episode: [], doc: [] };
    if (c.k === 'topic') {
      // 뿌리 아래 첫 칸 주제의 부모는 가상 뿌리('')다. 뿌리 자신은 부모가 없다
      const parent = c.path === ROOT ? null : topics.get(c.path!.includes('/') ? c.path!.split('/').slice(0, -1).join('/') : '');
      if (parent) up.push({ n: parent, rel: C.rel.parentTopic, main: true });
      const childOf = (d: string): string | null => (!d || d === c.path ? null : !c.path ? d.split('/')[0] : d.startsWith(c.path + '/') ? `${c.path}/${d.slice(c.path.length + 1).split('/')[0]}` : null);
      // 다리: 잇는 끝이 이 주제의 하위 주제 둘 이상이면 오른쪽 하위 주제 사이로, 아니면 왼쪽 개념 부채에
      for (const n of c.direct!.filter(isMean)) {
        if (n.k === 'bridge') { const subs = [...new Set(bridgeEnds(n).map((m) => childOf(m.d)).filter(Boolean))]; if (subs.length >= 1) { bridges.push({ n, subs, outside: bridgeEnds(n).some((m) => !childOf(m.d)) }); continue; } }
        left.push({ n, rel: C.rel.concept });
      }
      left.sort((a, b) => b.n.m! - a.n.m! || b.n.g! - a.n.g!);
      for (const n of c.direct!.filter((x) => !isMean(x))) (shelf[n.k] || shelf.record).push(n);
      for (const p of [...c.sub!].sort((a, b) => topics.get(b)!.count - topics.get(a)!.count)) right.push({ n: topics.get(p)!, rel: C.rel.subtopic });
    } else {
      up.push({ n: topics.get(c.d!)!, rel: C.rel.topic, main: true });
      for (const e of adj.get(c.id)!) {
        const o = e.a === c ? e.b : e.a, out = e.a === c;
        if (ATTACH.has(e.t)) { if (out) up.push({ n: o, rel: C.rel.attachedTo, e }); else (shelf[o.k] || shelf.record).push(o); continue; }
        if (e.t === 'SUPERSEDES') { (out ? right : up).push({ n: o, rel: out ? C.rel.replacedOld : C.rel.replacedNew, e }); continue; }
        if (e.t === 'DERIVES_FROM' || e.t === 'INHERITS_FROM') { (out ? up : right).push({ n: o, rel: e.t === 'DERIVES_FROM' ? (out ? C.rel.source : C.rel.derived) : (out ? C.rel.parent : C.rel.inherited), e }); continue; }
        if (e.t === 'PREREQUISITE_OF') { (out ? right : up).push({ n: o, rel: out ? C.rel.next : C.rel.prev, e }); continue; }
        left.push({ n: o, rel: e.t, e });
      }
      left.sort((a, b) => (b.n.m || 0) - (a.n.m || 0));
    }
    for (const k of Object.keys(shelf)) shelf[k].sort((a, b) => (b.ts || '').localeCompare(a.ts || ''));
    // 자르지 않고 다 돌려준다: 부채 크기는 compassOf 가 기간 · 종류로 거른 **뒤에** 자른다(지나온 길의 노드는 자르지 않는다).
    return { up, left, right, bridges, shelf };
  }

  // ── 펼침: 네 방향 십자 ─────────────────────────────────────────────────────────────────────
  // 층마다 12시는 부모(온 곳) 쪽, 3시는 하위 주제, 6시는 바깥(목록), 9시는 개념이다. 층은 화면에서 위·아래·왼쪽·오른쪽
  // 넷 중 한 방향으로만 서서(0·90·180·270°) 호나 나선이 생기지 않는다. 맨 위 주제의 하위 주제는 네 팔로 나뉘고, 펼친 층은
  // 부모에서 그 팔 방향으로 곧게 나간다. 위·아래로 서는 부채는 가로 이름표의 두 줄 목록으로 편다.
  const svg = $id('g') as SVGSVGElement;
  const ROWS = 18;
  const REL: Record<'up' | 'left' | 'right', [number, number]> = { up: [-26, 26], left: [-135, -45], right: [45, 135] };   // 12시 방향(θ12)에서 잰 각도(도)
  const rad = (d: number) => d * Math.PI / 180, todeg = (r: number) => r * 180 / Math.PI;
  // 그린 그림의 기억(렌즈 상태가 아니다): 마지막 배치(LZ, 손잡이들이 화면에 놓인 자리를 읽는다)와 지난 그림의 노드 자리(prevPos, 옮겨 가는 움직임)
  let prevPos = new Map<string, { x: number; y: number }>(), LZ: (Layout & { hub: Item }) | null = null;
  // 정해진 자리(팔): 마운트 때 어림한 것(PLAN_EST)과 층을 재서 고른 것(PLAN_MEAS). 둘 다 데이터에서 한 번 짓는 값이고, 어느 쪽을 쓰는지는
  // 상태(armsMeasured)가 정한다. 잰 뒤로는 팔을 바꾸지 않는다
  let CANONL: Layout | null = null, PLAN_EST: Plan | null = null, PLAN_MEAS: Plan | null = null;
  const armsOf = (s: LensState): Plan => (s.armsMeasured && PLAN_MEAS) || PLAN_EST!;
  const pathOf = (s: LensState) => s.path.map(byAny) as LNode[];
  // 배치 한 번이 읽는 것(S). 그리기는 상태에서 만든 S(curS)로, 재기 · 정해진 자리 짓기는 따로 만든 S 로 부른다.
  // place: 주제 층의 허브를 이 표의 자리에 둔다(하나씩 열기의 묶음 배치). dirs: 부모 기준으로 놓는 층의 방향(th12)을 읽는 표.
  // canon: 정해진 자리를 처음 짓는 중(거르기 · 밀어내기를 하지 않는다). range 는 늘 비어 있다(기간은 흐리기만 한다).
  // koff · more · pins: 범례에서 끈 종류 · 더 연 목록 · 고정. pinChain: 고정 노드로 가는 길에만 있는 층
  const curS = (s: LensState, pinChain: Set<string>): LayoutIn => ({ mode: 'path', path: pathOf(s), place: null, dirs: armsOf(s).out, canon: false, range: null, koff: new Set(s.koff), more: s.more, pins: new Set(s.pins), pinChain });
  const kindKey = (n: LNode) => (n.k === 'decision' || n.k === 'record' || n.k === 'episode' || n.k === 'bridge' ? n.k : n.k === 'doc' ? 'doc' : 'concept');   // 범례의 종류 키(끄고 켜는 단위)
  const fullDate = (s: string) => (!s ? '' : s.length === 4 ? s + '-01-01' : s.length === 7 ? s + '-01' : s.slice(0, 10));   // '2026' · '2026-03' 같은 덜 적힌 날짜는 그 첫날로
  // 날짜 = 그 일이 있었던 날: 내용 시간 → 유효 시작(→ 사건은 그날). 마지막으로 고친 때만 있는 노드는 날짜가 없는 것으로 본다(막대에 넣지 않고 흐리지도 않는다).
  const when = (n: LNode) => fullDate(n.ct || n.vf || (n.k === 'episode' ? n.ts : '') || '');
  const inRange = (n: LNode, rg: [string, string] | null = null) => { const w = when(n); return !rg || !w || (w >= rg[0] && w <= rg[1]); };
  type Pos = { u: Unit; ang: number; x: number; y: number };
  function fanPlace(list: Unit[], center: { x: number; y: number }, a0: number, a1: number, rMin: number, gap: number, size: (u: Unit) => number): { R: number; pos: Pos[] } {
    const need = list.reduce((s, u) => s + 2 * size(u) + gap, 0), span = rad(a1 - a0), R = Math.max(rMin, need / span);
    let acc = rad(a0) + (span - need / R) / 2;
    return { R, pos: list.map((u) => { const w = (2 * size(u) + gap) / R, mid = acc + w / 2; acc += w; return { u, ang: mid, x: center.x + Math.cos(mid) * R, y: center.y + Math.sin(mid) * R }; }) };
  }
  const subR = (n: LNode) => Math.max(9, Math.min(24, 4 + 1.5 * Math.sqrt(n.count || 1)));
  const sizeU = (u: Unit) => (u.n.k === 'topic' ? subR(u.n) : isMean(u.n) ? 7 : 5.5);
  // 위·아래로 서는 부채(가운데 각이 거의 수직)는 두 줄 목록으로 편다. 부채 그대로면 이름표가 세로로 서서 읽기 어렵다.
  // side: -1 왼쪽 줄만, 1 오른쪽 줄만, 0 반씩 양쪽. 두 줄 사이는 비워 둔다(부모·자식 선이 그 사이로 지나간다)
  function colPlace(list: Unit[], center: { x: number; y: number; r?: number }, cDeg: number, side: number, size: (u: Unit) => number): { R: number; pos: Pos[] } {
    const s = Math.sin(rad(cDeg)) > 0 ? 1 : -1, n = list.length, nl = side === 0 ? Math.ceil(n / 2) : side < 0 ? n : 0, r0 = (center.r || 22) + 34, acc = [r0, r0];
    const pos = list.map((u, i) => { const L = i < nl ? 0 : 1, h = Math.max(22, 2 * size(u) + 8), d = acc[L] + h / 2; acc[L] += h;
      return { u, ang: L === 0 ? Math.PI : 0, x: center.x + (L === 0 ? -1 : 1) * (40 + size(u)), y: center.y + s * d }; });
    return { R: Math.max(acc[0], acc[1]), pos };
  }
  // 한 층(나침반 하나)의 내용. 부모는 이미 12시에 있으므로 뺀다
  function compassOf(node: LNode, parentId: string | null, S: LayoutIn): Cp {
    const G = gather(node), drop = (l: Unit[]) => l.filter((u) => u.n.id !== parentId);
    G.up = drop(G.up); G.left = drop(G.left); G.right = drop(G.right);
    // 기간: 시간 막대에서 고른 구간 밖의 노드는 뺀다(주제와 지나온 길의 노드는 남긴다)
    const keep = (n: LNode) => S.canon || n.k === 'topic' || S.path.some((q) => q.id === n.id) || S.pins.has(n.id) || (inRange(n, S.range) && !S.koff.has(kindKey(n)));
    G.up = G.up.filter((u) => keep(u.n)); G.left = G.left.filter((u) => keep(u.n)); G.right = G.right.filter((u) => keep(u.n));
    for (const k of Object.keys(G.shelf)) G.shelf[k] = G.shelf[k].filter(keep);
    G.bridges = G.bridges.filter((b) => keep(b.n));
    // 부채가 받는 수: 위 11 · 왼쪽 20 · 오른쪽 16. 지나온 길의 노드와 하위 주제는 넘쳐도 남긴다(잘리면 그 노드를 열 수 없고,
    // 정해진 자리를 잴 때 주제가 빠진다). 순서는 그대로다
    const onPathU = (u: Unit) => S.path.some((q) => q.id === u.n.id);
    const cap = (l: Unit[], n: number, must: (u: Unit) => boolean) => { if (l.length <= n) return l; let room = n - l.slice(n).filter(must).length; return l.filter((u) => must(u) || room-- > 0); };
    G.up = cap(G.up, 11, onPathU); G.left = cap(G.left, 20, onPathU); G.right = cap(G.right, 16, (u) => onPathU(u) || u.n.k === 'topic');
    const probe = { x: 0, y: 0, r: 22 };
    const rL = G.left.length ? Math.max(fanPlace(G.left, probe, 0, REL.left[1] - REL.left[0], 150, 9, sizeU).R, colPlace(G.left, probe, 90, 0, sizeU).R) : 0;
    const shelfN = Object.values(G.shelf).reduce((x, l) => x + l.length, 0);
    const rR = G.right.length ? Math.max(fanPlace(G.right, probe, 0, REL.right[1] - REL.right[0], 170, 40, sizeU).R, colPlace(G.right, probe, 90, 0, sizeU).R) : 0;
    const E = Math.max(rL, rR, 120) + 70;
    // 이 층이 차지하는 둘레: 나침반(E)과 목록(6시 쪽으로 늘어지거나 옆으로 퍼진다). 정해진 자리를 잴 때 층끼리 이만큼 띄운다
    const ncol = ['decision', 'record', 'episode', 'doc'].filter((k) => G.shelf[k].length).length, rows = Math.min(ROWS, Math.max(0, ...Object.values(G.shelf).map((l) => l.length)));
    const FP = ncol ? Math.max(E, E * .6 + ncol * 210, E * .7 + 46 + rows * 17 + 30) : E;
    return { node, G, rL, rR, shelfN, E, FP };
  }
  // 펼친 나무: 펼칠 노드들(expand: 부모 id → 펼칠 자식 id 들)을 BFS 로 놓는다. 펼친 노드는 부모 부채의 자기 자리에서 바깥으로 빠져나와
  // 같은 나침반을 편다. 부모 기준으로 놓은 층은 앞서 놓인 층의 항목(이름표까지)과 겹치지 않을 때까지 자기 방향으로 더 나간다(settle). 목록은 focus 층만 펴고 나머지는 수로 접는다.

  function layoutTree(root: LNode, expand: Map<string, Set<string>>, focusId: string | null, S: LayoutIn): Layout {
    const items: Item[] = [], comps: Comp[] = [], placed = new Map<string, Item>(), onPath = new Set(S.path.map((n) => n.id)), queued = new Set([root.id]);
    const mk = (n: LNode, x: number, y: number, r: number, extra: Partial<Item> & { role: string; level: number }): Item => { const o: Item = { id: n.id, src: n, k: n.k, x, y, r, c: colv(n), label: titleOf(n), ...extra }; items.push(o); placed.set(n.id, o); return o; };
    const queue: { node: LNode; parent: Comp | null }[] = [{ node: root, parent: null }];
    // 겹침 풀기: 부모 기준으로 놓은 층이 앞서 놓인 어느 층(부모 · 형제 · 지나온 층 · 고정 층)과 겹치면, 자기 방향으로 한 칸씩 밀어낸다.
    // 상자는 항목마다 따로 잰다(노드와 이름표 끝까지, extentPts). 층 전체를 상자 하나로 보면 비스듬한 부채의 빈 모서리까지 막혀
    // 이미 떨어진 층도 밀려난다. 지금 층(focus)은 6시 목록이 설 자리까지 센다
    const own = (c: Comp): Item[] => [c.hub, ...c.items.filter((o) => o.id && !o.link && o.comp === c.hub.id) as Item[]];
    const boxOf = (o: Item) => { let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      for (const [x, y] of extentPts(o)) { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); } return [x0, y0, x1, y1]; };
    const boxes = (c: Comp) => { const out = own(c).map(boxOf);
      if (c.stub) out.push([c.stub.x - 20, c.stub.y - 14, c.stub.x + 170, c.stub.y + 14]);
      if (c.focus && S.mode !== 'all' && c.kinds && c.kinds.length) { const a = rad(c.th12 + 180), ux = Math.cos(a), uy = Math.sin(a), d = Math.max(c.cp.FP, c.hub.r + 80), w = Math.max(c.cp.E, c.kinds.length * 105 + 60);
        const xs = [-1, 1].map((k) => c.hub.x + ux * d - uy * w * k), ys = [-1, 1].map((k) => c.hub.y + uy * d + ux * w * k), x0 = c.hub.x + ux * (c.hub.r + 30), y0 = c.hub.y + uy * (c.hub.r + 30);
        out.push([Math.min(x0, ...xs), Math.min(y0, ...ys), Math.max(x0, ...xs), Math.max(y0, ...ys)]); }
      return out; };
    const hit = (A: number[][], B: number[][], m = 12) => A.some((a) => B.some((b) => a[0] < b[2] + m && b[0] < a[2] + m && a[1] < b[3] + m && b[1] < a[3] + m));
    const shift = (c: Comp, dx: number, dy: number) => { for (const o of own(c)) { o.x += dx; o.y += dy; } if (c.stub) { c.stub.x += dx; c.stub.y += dy; } };
    const settle = (c: Comp, dir: number) => { const ux = Math.cos(dir), uy = Math.sin(dir); let mine = boxes(c); const others = comps.map(boxes);
      for (let k = 0; k < 80 && others.some((B) => hit(mine, B)); k++) { shift(c, ux * 60, uy * 60); mine = boxes(c); }
      if (c.hub.route) c.hub.route[c.hub.route.length - 1] = [c.hub.x, c.hub.y]; };   // 마지막 토막은 팔 방향이라 끝점만 옮기면 곧게 남는다
    while (queue.length) {
      const { node, parent } = queue.shift()!; const i = comps.length, kids = expand.get(node.id) || new Set<string>();
      const ck = S.cps && `${node.id}|${parent ? parent.hub.id : ''}`, cp = (ck && S.cps!.get(ck)) || compassOf(node, parent ? parent.hub.id : null, S), focus = node.id === focusId;
      if (ck) S.cps!.set(ck, cp);
      // 고정 노드로 가는 길에만 있는 층(지금 길 밖): 다음 사슬 노드와 고정 노드만 남긴다(길만 잇는다)
      const pinOnly = S.mode !== 'all' && !onPath.has(node.id) && S.pinChain.has(node.id);
      if (pinOnly) { const ok = (u: Unit) => kids.has(u.n.id) || S.pins.has(u.n.id); cp.G.up = cp.G.up.filter(ok); cp.G.left = cp.G.left.filter(ok); cp.G.right = cp.G.right.filter(ok); cp.G.bridges = [];
        for (const k of Object.keys(cp.G.shelf)) cp.G.shelf[k] = cp.G.shelf[k].filter((n) => S.pins.has(n.id)); cp.shelfN = 0; }
      let hub: Item, th12: number, free: number | null = null;   // free: 정해진 자리 없이 부모 기준으로 놓은 층이면 밀어낼 방향(라디안)
      const T0 = !S.canon && S.place && parent ? S.place.get(node.id) : null;
      if (!parent) { hub = mk(node, 0, 0, node.k === 'topic' ? 24 : 15, { role: 'hub', level: 0 }); th12 = -90; }
      else if (T0) { const slot = parent.slots.get(node.id);   // 정해진 자리(어떤 길로 와도 같다)
        hub = mk(node, T0.x, T0.y, 22, { role: 'hub', level: i, parentHub: parent.hub, viaE: slot && slot.e, route: T0.route && [...T0.route] }); th12 = T0.th12;
        if (S.mode !== 'all') free = rad(th12 + 180); }   // 묶음이 모르는 층(주제가 아닌 노드로 들어간 층)과 겹치면 팔 방향으로 비킨다
      else {
        const slot = parent.slots.get(node.id) || { ang: rad(parent.th12 + 180), r: parent.cp.E - 70 };
        const Tc = S.dirs && S.dirs.get(node.id), Tp = S.dirs && S.dirs.get(parent.hub.id), extra = { role: 'hub', level: i, parentHub: parent.hub, viaE: slot.e };
        if (parent.slots.size > 1 && Tc && Tp) {
          // 한 층에서 여럿을 펼치면(고정한 노드로 가는 길이 갈라질 때) 전체 배치에서의 상대 자리를 그대로 쓴다: 형제가 한 자리에 겹치지 않는다
          hub = mk(node, parent.hub.x + Tc.x - Tp.x, parent.hub.y + Tc.y - Tp.y, 22, extra); th12 = Tc.th12;
          free = Math.atan2(hub.y - parent.hub.y, hub.x - parent.hub.x);
        } else {
          // 펼친 층은 부모에서 팔 방향(부모의 6시 쪽)으로 곧게 나간다. 주제가 아닌 노드는 부모 부채에서의 자리를 가까운 네 방향으로 맞춘다.
          // 다만 온 쪽(부모의 12시)으로는 열지 않는다(앞 층 위로 되돌아가 겹친다). 다리도 하위 주제 부채 한가운데가 아니라 앞(6시 쪽)으로 연다.
          // 층 사이: 위·아래로 나가면 두 나침반 반지름의 3/4, 가로로 나가면 6할에 이름표 폭만큼 더. 그래도 겹치면 아래 settle 이 밀어낸다
          let ang = Tc ? rad(Tc.th12 + 180) : Math.round(slot.ang / (Math.PI / 2)) * (Math.PI / 2);
          const back = rad(parent.th12), near = (a: number, b: number) => Math.abs(Math.atan2(Math.sin(a - b), Math.cos(a - b))) < Math.PI / 4;
          if (!Tc && (node.k === 'bridge' || near(ang, back))) ang = rad(parent.th12 + 180);
          const dist = Math.abs(Math.cos(ang)) > .5 ? (parent.cp.E + cp.E) * .62 + 320 : (parent.cp.E + cp.E) * .75 + 40;
          hub = mk(node, parent.hub.x + Math.cos(ang) * dist, parent.hub.y + Math.sin(ang) * dist, node.k === 'topic' ? 22 : 14, extra);
          th12 = todeg(ang) + 180; free = ang;
        }
      }
      const chain = parent ? [...parent.chain, node] : [node];
      hub.chain = chain;
      const comp: Comp = { cp, hub, th12, slots: new Map(), focus, level: i, items: [], chain, parent, pinOnly, free };
      const put = (p: Pos, role: string, extra: Partial<Item> = {}) => { const r = Math.hypot(p.x - hub.x, p.y - hub.y);
        // 지나온 길의 노드는 반드시 자기 층(허브)으로 펼쳐져야 한다. 앞 층의 부채에 먼저 나와도 여기서 그리지 않고, 나중에 그 허브로 빛살만 잇는다.
        // (이걸 안 하면 길의 노드가 앞 층의 잎으로 먼저 놓여 펼쳐지지 못하고, 지금 층을 잃어 맨 위 vinc 로 돌아간 것처럼 보였다)
        if (onPath.has(p.u.n.id) && !kids.has(p.u.n.id)) { comp.items.push({ linkId: p.u.n.id, e: p.u.e }); return; }
        if (kids.has(p.u.n.id) && queued.has(p.u.n.id)) { comp.items.push({ linkId: p.u.n.id, e: p.u.e }); return; }   // 같은 노드가 두 관계로 두 번 나오면 한 번만 편다
        if (kids.has(p.u.n.id) && !placed.has(p.u.n.id)) { queued.add(p.u.n.id); comp.slots.set(p.u.n.id, { ang: Math.atan2(p.y - hub.y, p.x - hub.x), r, e: p.u.e }); queue.push({ node: p.u.n, parent: comp }); return; }
        if (placed.has(p.u.n.id)) { comp.items.push({ link: placed.get(p.u.n.id)!, e: p.u.e }); return; }
        comp.items.push(mk(p.u.n, p.x, p.y, sizeU(p.u), { role, rel: p.u.rel, e: p.u.e, ang: p.ang, level: i, comp: hub.id, chain, ...(p.u.label ? { label: p.u.label, farEnds: p.u.farEnds } : {}), ...extra })); };
      const take = (list: Unit[], a0: number, a1: number, rMin: number, gap: number, role: string) => { if (!list.length) return; const c = th12 + (a0 + a1) / 2, cc = Math.cos(rad(c));
        const P = Math.abs(Math.sin(rad(c))) > .7 ? colPlace(list, hub, c, Math.abs(cc) < .05 ? 0 : cc < 0 ? -1 : 1, sizeU) : fanPlace(list, hub, th12 + a0, th12 + a1, rMin, gap, sizeU);
        P.pos.forEach((p) => put(p, role)); };
      const G = cp.G;
      const ups = G.up; take(ups.slice(0, Math.ceil(ups.length / 2)), REL.up[0], -8, 190, 60, 'up'); take(ups.slice(Math.ceil(ups.length / 2)), 8, REL.up[1], 190, 60, 'up');
      take(G.left, ...REL.left, cp.rL || 150, 9, 'left');
      take(G.right, ...REL.right, cp.rR || 170, 40, 'right');
      // 다리: 이 층의 하위 주제들 사이(펼쳐진 하위 주제는 그 자리 각도로 센다)
      const angOf = new Map<string, number>([...comp.items.filter((o): o is Item => o.role === 'right').map((o): [string, number] => [o.id, Math.atan2(o.y - hub.y, o.x - hub.x)]), ...[...comp.slots].map(([id, s]): [string, number] => [id, s.ang])]);
      const posOf = new Map<string, Pt>([...comp.items.filter((o): o is Item => o.role === 'right').map((o): [string, Pt] => [o.id, [o.x, o.y]]), ...[...comp.slots].map(([id, s]): [string, Pt] => [id, [hub.x + Math.cos(s.ang) * s.r, hub.y + Math.sin(s.ang) * s.r]])]);
      for (const b of G.bridges) { const angs = b.subs.map((s) => angOf.get(`topic:${s}`)).filter((a): a is number => a != null); if (!angs.length || placed.has(b.n.id)) continue;
        if (onPath.has(b.n.id) && !kids.has(b.n.id)) continue;
        let a = Math.atan2(angs.reduce((s, x) => s + Math.sin(x), 0), angs.reduce((s, x) => s + Math.cos(x), 0)), r = Math.max(hub.r + 70, (cp.rR || 170) - 90);
        // 3시가 위·아래로 서서 하위 주제가 두 줄 목록이면, 다리는 두 줄 사이 빈 길(3시 축) 위, 잇는 하위 주제들의 가운데 거리에 선다
        const c3 = rad(th12 + 90); if (Math.abs(Math.sin(c3)) > .7) { const ds = (b.subs.map((x) => posOf.get(`topic:${x}`)).filter(Boolean) as Pt[]).map(([x, y]) => (x - hub.x) * Math.cos(c3) + (y - hub.y) * Math.sin(c3));
          if (ds.length) { a = c3; r = ds.reduce((s, x) => s + x, 0) / ds.length; } }
        // 다리끼리 이름표가 겹치지 않게: 겹치면 안쪽으로 한 칸, 더 들어갈 수 없으면 옆으로 조금 돈다
        const bw = (o: Item) => GW('bridge') * Math.min(20, (o.label || '').replace(/ junction$/i, '').length) + 16, myW = GW('bridge') * Math.min(20, titleOf(b.n).replace(/ junction$/i, '').length) + 16;
        const busy = () => { const x = hub.x + Math.cos(a) * r, y = hub.y + Math.sin(a) * r;
          return items.some((o) => o.role === 'bridge' && Math.abs(o.x - x) < (bw(o) + myW) / 2 && Math.abs(o.y - y) < 30) || items.some((o) => o.role === 'right' && o.comp === hub.id && Math.hypot(o.x - x, o.y - y) < o.r + 26); };
        const lane = Math.abs(Math.sin(c3)) > .7 && a === c3;
        for (let k = 0; k < 16 && busy(); k++) { if (lane) r += 26; else if (r - 26 > hub.r + 60) r -= 26; else a += .14; }
        if (kids.has(b.n.id)) { if (queued.has(b.n.id)) continue; queued.add(b.n.id); comp.slots.set(b.n.id, { ang: a, r }); queue.push({ node: b.n, parent: comp }); continue; }   // 다리를 눌렀으면 그 자리에서 다리의 나침반을 편다
        comp.items.push(mk(b.n, hub.x + Math.cos(a) * r, hub.y + Math.sin(a) * r, 8, { role: 'bridge', subs: b.subs.map((s) => `topic:${s}`), outside: b.outside, ang: a, level: i, comp: hub.id, chain })); }
      // 다음 층이 6시 목록(결정 · 기록 · 사건 · 문서)에 있으면 6시 쪽으로 층을 연다. 부채에서만 찾으면 목록의 노드는 가운데로 오지 못했다
      for (const k of Object.keys(G.shelf)) G.shelf[k] = G.shelf[k].filter((n) => {
        if (!kids.has(n.id) || queued.has(n.id) || placed.has(n.id)) return true;
        queued.add(n.id); comp.slots.set(n.id, { ang: rad(th12 + 180), r: hub.r + 46 }); queue.push({ node: n, parent: comp }); return false; });
      const kinds = [['decision', KIND.decision], ['record', KIND.record], ['episode', KIND.episode], ['doc', KIND.doc]].filter(([k]) => G.shelf[k].length);
      comp.kinds = kinds.map(([k, name]) => ({ k, name, all: G.shelf[k] }));
      if (!focus && kinds.length && S.mode !== 'all') { const a = rad(th12 + 180 + (comp.slots.size ? 22 : 0)), r = hub.r + 46;   // 펼친 층으로 가는 선(6시)이 있으면 조금 비켜 둔다
        comp.stub = { x: hub.x + Math.cos(a) * r, y: hub.y + Math.sin(a) * r, text: kinds.map(([k, name]) => `${name} ${G.shelf[k].length}`).join(' · '), ang: a }; }
      if (free != null && !S.canon) settle(comp, free);
      comps.push(comp);
    }
    // 미뤄 둔 빛살을 실제 허브에 잇는다
    for (const c of comps) c.items = c.items.map((o) => (o.linkId ? (placed.has(o.linkId) && placed.get(o.linkId) !== c.hub ? { link: placed.get(o.linkId)!, e: o.e } : null) : o)).filter(Boolean) as Slot[];
    const F = comps.find((c) => c.focus) || comps[comps.length - 1];
    if (!F) return { items, comps, focus: comps[0], shelves: [], sets: [], Q: null, BY: 0, mode: 'none', side: 1, a6: 0 };
    // 층마다의 목록(결정·기록·사건·문서). 지금 층에만 단다(정해진 자리를 잴 때는 목록이 있는 모든 층)
    const setFor = (F: Comp): ShelfSet => {
    // 지금 층의 목록: 6시가 아래쪽이면 아래에 좌우 대칭으로, 옆쪽이면 그쪽으로 가로로 늘어놓는다(글자는 늘 바로 선다)
    let a6 = rad(F.th12 + 180); const down = Math.sin(a6) > .94; let side = Math.cos(a6) >= 0 ? 1 : -1;   // 거의 곧게 아래일 때만 아래 버스, 아니면 6시 방향으로 비스듬히 나가 옆 버스
    // 목록이 부채의 이름표와 겹치지 않게: 이름표가 닿는 끝(노드 바깥 약 160px)까지 재서 그 바깥에 둔다
    const reach = (o: Item): Pt => { const a = o.ang ?? 0, L = o.r + (o.role === 'right' && o.k === 'topic' ? 120 : 160); return [o.x + Math.cos(a) * L, o.y + Math.sin(a) * L]; };
    const ends = F.items.filter((o): o is Item => !o.link).flatMap((o): Pt[] => [[o.x, o.y], reach(o)]);
    const ext = down ? Math.max(F.hub.r, ...ends.map(([, y]) => y - F.hub.y)) + 50 : Math.max(F.hub.r, ...ends.map(([x]) => side * (x - F.hub.x))) + 50;
    const COLW = 210, ROW = 17; const shelves: ShelfCol[] = [];
    let mode: string, Q: ShelfSet['Q'];
    if (down) { mode = 'down'; Q = { x: F.hub.x, y: F.hub.y + ext }; const x0 = Q.x - (F.kinds!.length - 1) * COLW / 2;
      F.kinds!.forEach((s, i) => shelves.push({ ...s, x: x0 + i * COLW } as ShelfCol)); }
    else { mode = 'side';
      // 6시 방향(두 부채 사이의 빈 틈)으로 곧게 나가 부채의 이름표 바깥에서 꺾는다. 그 점이 버스의 시작이다
      const along = Math.max(F.hub.r + 60, ...ends.map(([x, y]) => (x - F.hub.x) * Math.cos(a6) + (y - F.hub.y) * Math.sin(a6))) + 40;
      Q = { x: F.hub.x + Math.cos(a6) * along, y: F.hub.y + Math.sin(a6) * along, a6 };
      // 목록은 버스(Q 높이)에서 아래로 늘어진다. 그 높이 안에 걸친 부채 이름표가 있으면 그 끝보다 더 옆에서 시작한다.
      // 6시가 거의 위·아래로 서면(옆이 정해지지 않으면) 걸리는 것이 적은 쪽으로 보낸다.
      const rows = Math.max(1, ...F.kinds!.map((k) => Math.min(k.all.length, ROWS + (LS.entryGet(S.more, `${F.hub.id}|${k.k}`) || 0)))), yTop = Q.y - 12, yBot = Q.y + 46 + rows * ROW + 12;
      const hubLab: Pt[] = [[F.hub.x - F.hub.r - 60, F.hub.y + F.hub.r + 24], [F.hub.x + F.hub.r + 60, F.hub.y + F.hub.r + 24], [F.hub.x - F.hub.r - 10, F.hub.y], [F.hub.x + F.hub.r + 10, F.hub.y]];   // 허브 자신과 그 이름표
      const inBand = ends.concat(F.items.filter((o): o is Item => !o.link).map((o): Pt => [o.x, o.y]), hubLab).filter(([, y]) => y >= yTop && y <= yBot);
      const reachOn = (sd: number) => Math.max(0, ...inBand.map(([x]) => sd * (x - Q.x)));
      // 6시가 거의 위·아래로 서면 옆을 정할 수 없다: 9시(개념) 쪽으로 꺾는다. 3시 쪽은 하위 주제로 가는 선이 지나간다
      let sd = side; if (Math.abs(Math.cos(a6)) < .35) sd = Math.cos(rad(F.th12 - 90)) >= 0 ? 1 : -1;
      side = sd;   // 그리기(buildTree)가 같은 쪽으로 버스를 긋는다
      // 거의 선 6시는 곧게 세운다: 비스듬히 안쪽으로 올라갔다가 바깥으로 되돌아 꺾는 군더더기를 없앤다
      if (Math.abs(Math.cos(a6)) < .35) { a6 = Math.sin(a6) < 0 ? -Math.PI / 2 : Math.PI / 2; Q = { x: F.hub.x, y: F.hub.y + Math.sin(a6) * along, a6 }; }
      const off = Math.max(40, reachOn(sd) + 36);
      F.kinds!.forEach((s, i) => shelves.push({ ...s, x: sd > 0 ? Q.x + off + i * COLW : Q.x - off - (F.kinds!.length - i) * COLW + 20 } as ShelfCol)); }
    const BY = Q.y, TOP = BY + 46;
      for (const s of shelves) s.items = s.all.filter((n) => !placed.has(n.id)).slice(0, ROWS + (LS.entryGet(S.more, `${F.hub.id}|${s.k}`) || 0)).map((n, j) => mk(n, s.x + 26, TOP + j * ROW, 5, { role: 'shelf', level: F.level, comp: F.hub.id, chain: F.chain })).filter(Boolean);
      return { hub: F.hub, comp: F, shelves, Q, BY, mode, side, a6 };
    };
    const fset = setFor(F), sets = [fset];
    // 지금 층의 목록은 층을 다 놓은 뒤에 선다. 그 목록이 앞서 놓인 층에 닿으면 지금 층을 목록째 자기 방향으로 더 민다
    if (S.mode !== 'all' && !S.canon && F.free != null) {
      const ux = Math.cos(F.free) * 60, uy = Math.sin(F.free) * 60, list = () => fset.shelves.flatMap((sh) => sh.items), others = comps.filter((c) => c !== F).map(boxes);
      for (let k = 0; k < 80 && others.some((B) => hit([...boxes(F), ...list().map(boxOf)], B)); k++) { shift(F, ux, uy); for (const o of list()) { o.x += ux; o.y += uy; }
        for (const sh of fset.shelves) sh.x += ux; fset.Q = { ...fset.Q, x: fset.Q.x + ux, y: fset.Q.y + uy }; fset.BY += uy; }
      if (F.hub.route) F.hub.route[F.hub.route.length - 1] = [F.hub.x, F.hub.y]; }
    if (S.mode === 'all') for (const c of comps) if (c !== F && !c.pinOnly && c.kinds!.length) sets.push(setFor(c));
    return { items, comps, focus: F, shelves: fset.shelves, sets, Q: fset.Q, BY: fset.BY, mode: fset.mode, side: fset.side, a6: fset.a6, boxes };
  }

  // ── 그리기 ──────────────────────────────────────────────────────────────────────────────
  const ringS = (r: number, col: string, w: number) => topicRing(r, col, w, 'shape');   // 주제 고리: 범례 · 고정 칩 · 사이드바와 같은 함수(view-tint.ts)
  // 펼칠 것: path = 지나온 길만, all = 모든 주제(+ 지나온 길)
  function expandMap(s: LensState) { const m = new Map<string, Set<string>>(), add = (a: string, b: string) => { if (!m.has(a)) m.set(a, new Set()); m.get(a)!.add(b); };
    for (let i = 1; i < s.path.length; i++) add(s.path[i - 1], s.path[i]);
    // 고정한 노드까지의 길: 그 노드의 주제 사슬을 펼친다(고정 노드 자신은 잎으로 남는다). 그 길에만 있는 층(pinChain)은 길만 잇는다
    const pinChain = new Set<string>();
    for (const id of s.pins) { const n = byAny(id); if (!n) continue; const ch = chainTo(n);
      for (let i = 1; i < ch.length - 1; i++) { add(ch[i - 1].id, ch[i].id); pinChain.add(ch[i].id); } }
    return { m, pinChain }; }
  // 노드 하나가 화면에서 차지하는 점들(이름표까지). 카메라와 전체 배치의 층 크기 재기가 같이 쓴다
  const extentPts = (o: Item): Pt[] => { if (o.role === 'shelf') return [[o.x - 30, o.y - 40], [o.x + 200, o.y + 12]];
      if (o.pinned) return [[o.x - 60, o.y - 50], [o.x + 180, o.y + 50]];   // 고정 노드: 이름표까지 들어오게
      // 이름표가 실제로 차지하는 만큼만 잡는다: 가로로 쓰는 고리 주제는 옆으로 약 110px·위아래로 30px, 비스듬한 부채 이름은 이름 길이만큼
      if (o.role === 'hub') return o.k === 'topic' ? [[o.x - o.r - 30, o.y - o.r - 10], [o.x + o.r + 30, o.y + o.r + 40]] : [[o.x - o.r - 170, o.y - o.r - 20], [o.x + o.r + 20, o.y + o.r + 30]];
      const a = o.ang ?? 0;
      // 하위 주제 이름은 기울이지 않고 가로로 쓴다(수 배지까지): 부채 방향이 아니라 왼쪽이나 오른쪽으로 길어진다
      if (o.role === 'right' && o.k === 'topic') { const s = Math.cos(a) < 0 ? -1 : 1, L = o.r + 12 + GW('fan') * (Math.min(18, o.label.length) + 4); return [[o.x - o.r, o.y - o.r - 4], [o.x + o.r, o.y + o.r + 4], [o.x + s * L, o.y]]; }
      const L = o.r + 12 + GW('fan') * Math.min(24, o.label.length); return [[o.x - o.r, o.y - o.r], [o.x + o.r, o.y + o.r], [o.x + Math.cos(a) * L, o.y + Math.sin(a) * L]]; };
  // 다시 그리기 한 번: 새 상태로 갈아 끼우고 그림을 짓는다. 그림이 쓴 카메라 뜻과 고정 노드가 놓인 자리는 전이로 상태에 돌려 넣고,
  // 카메라를 옮기고, 옮겨 가는 움직임을 건 뒤 곁의 칸(흐리기 · 고정 판 · 요약 · 창)을 칠한다. flip 이 거짓이면 지난 그림의 자리를 잊는다(움직임 없이)
  function redraw(next: LensState, flip = true) {
    set(next); if (!flip) prevPos = new Map();
    if (st.whole.on) { prepareWhole(); drawWhole(st); ctx.onChange?.(); drawDock(st); drawSum(st); paintSide(st); return; }   // 전체 보기는 drawLayers 의 짝(drawWhole)이 그린다
    quitWhole();
    const fr = drawLayers(st);
    set(LS.pinsPlaced(LS.cameraFramed(st, fr.plan), fr.pinsAt));
    if (fr.box) flyView(fr.box);
    animateIn(fr.L, fr.at);
    dimLens(st); ctx.onChange?.(); drawDock(st); markPins(st); drawSum(st); paintSide(st);
  }
  // 상태 하나로 층들을 SVG 로 짓는다(상태는 읽기만 한다). 옮겨 가는 움직임(FLIP)의 프레임과 끝난 뒤 가지를 키우는 타이머(fx 의 'flip')를
  // 먼저 지운다: 늦게 온 타이머가 새 배치를 지난 배치의 가지로 덮지 않게
  function drawLayers(s: LensState) {
    fx.cancelFrame('flip'); fx.cancelAfter('flip');
    const { m: ex, pinChain } = expandMap(s), S0 = curS(s, pinChain), root0 = byAny(s.path[0])!, fid = s.path[s.path.length - 1]; let L = layoutTree(root0, ex, fid, S0);
    { const place = packPath(L, armsOf(s).arms); if (place) L = layoutTree(root0, ex, fid, { ...S0, place }); }
    const pl = pinLayout(L, s); L = pl.L;
    const F = L.focus;
    for (const c of L.comps) c.far = c !== F; LZ = { hub: L.focus.hub, ...L }; let defs = '', rays = '', nodesS = '', deco = '', i0 = 0;
    const drawRay = (inner: End, outer: End, e: Edge | null | undefined, w0: number, w1 = .5, cls = '') => { const r = ray(inner, outer, i0++, w0, w1); defs += r.def; rays += cls ? r.svg.replace('class="ray"', `class="ray ${cls}"`) : r.svg;
      if (e && ['PREREQUISITE_OF', 'DERIVES_FROM', 'INHERITS_FROM', 'RETURNS_TO'].includes(e.t)) {
        const A = e.a.id === inner.id ? inner : outer, B = A === inner ? outer : inner, src = e.t === 'PREREQUISITE_OF' || e.t === 'RETURNS_TO' ? A : B, dst = src === A ? B : A;
        rays += bead((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2, Math.atan2(dst.y - src.y, dst.x - src.x), mix(src.c, 90), e.t).replace(/class="ray"/, `class="ray ${cls}" data-a="${A.id}" data-b="${B.id}"`); } };
    const at = new Map(L.items.map((o) => [o.id, o]));
    // 묶음 배치의 부모 선: 상자 사이의 빈 길(줄기·통로)을 따라가되, 꺾는 곳마다 양옆 구간의 절반까지 굴려 곧은 토막 없이
    // 한 줄기 곡선으로 그린다. 지나가는 자리는 통로 안이라 다른 층의 상자를 거의 지나지 않는다. 양끝은 두 허브의 둘레에서 자른다
    const routeS = (pts: Pt[], h: Item, p: Item, cls = 'path') => { const q = pts.map(([x, y]): Pt => [x, y]), n = q.length, f = (a: Pt) => `${a[0].toFixed(1)} ${a[1].toFixed(1)}`;
      const unit = (a: Pt, b: Pt): [number, number, number] => { const d = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1; return [(b[0] - a[0]) / d, (b[1] - a[1]) / d, d]; };
      const u0 = unit(q[0], q[1]), u1 = unit(q[n - 1], q[n - 2]);
      q[0] = [q[0][0] + u0[0] * (p.r + 5), q[0][1] + u0[1] * (p.r + 5)]; q[n - 1] = [q[n - 1][0] + u1[0] * (h.r + 5), q[n - 1][1] + u1[1] * (h.r + 5)];
      let d = `M${f(q[0])}`;
      for (let i = 1; i < n - 1; i++) { const a = q[i - 1], b = q[i], c = q[i + 1], [ax, ay, l1] = unit(b, a), [cx, cy, l2] = unit(b, c);
        const r1 = i === 1 ? l1 : l1 / 2, r2 = i === n - 2 ? l2 : l2 / 2;   // 끝 구간은 통째로, 가운데 구간은 이웃 모서리와 반씩 나눈다
        d += `L${f([b[0] + ax * r1, b[1] + ay * r1])}Q${f(b)} ${f([b[0] + cx * r2, b[1] + cy * r2])}`; }
      return `<path class="ray ${cls}" data-a="${esc(p.id)}" data-b="${esc(h.id)}" d="${d}L${f(q[n - 1])}" fill="none" style="stroke:${mix(h.c, 80)}" stroke-width="${st.theme === 'light' ? 2.6 : 2}" stroke-linejoin="round" stroke-linecap="round"/>`; };
    for (const comp of L.comps) {
      const far = comp.far ? 'far' : '', hub = comp.hub;
      // 부모 선의 모양: 묶음 배치가 상자를 격자로 놓은 주제 층(hub.route 가 있다)은 두 모드 모두 상자 사이 통로를 따라 굴린 선(routeS),
      // 부모 기준으로 놓은 층(주제가 아닌 노드로 들어간 층, 그 아래의 주제)은 곧은 빛살(ray)로 잇는다
      if (hub.parentHub && hub.route) rays += routeS(hub.route, hub, hub.parentHub, comp.pinOnly ? 'pinpath' : 'path');
      else if (hub.parentHub) drawRay(hub.parentHub, hub, hub.viaE, comp.pinOnly ? 2.2 : 3.4, comp.pinOnly ? .8 : 1.2, comp.pinOnly ? 'pinpath' : 'path');          // 펼쳐 들어온 길: 굵게(고정 노드로 가는 길은 조금 가늘게)
      for (const o of comp.items) { if (o.link) { drawRay(hub, o.link, o.e, 1.4, .5, `${far} link`); continue; }
        if (o.role === 'bridge') { for (const s of o.subs!) { const t = at.get(s); if (t) drawRay({ ...o, c: mix(o.c, 70) }, { ...t, c: mix(t.c, 60) }, null, 1.2, 1, far); } continue; }
        drawRay(hub, o as Item, o.e, o.role === 'left' ? 3 : 2.4, .5, comp.pinOnly ? 'pinpath' : far); }
      if (comp.stub) { const s = comp.stub; deco += `<g class="stub ${far}" data-id="${esc(hub.id)}" data-chain="${esc(comp.chain.map((n) => n.id).join('|'))}"><line x1="${(hub.x + Math.cos(s.ang) * (hub.r + 5)).toFixed(1)}" y1="${(hub.y + Math.sin(s.ang) * (hub.r + 5)).toFixed(1)}" x2="${s.x.toFixed(1)}" y2="${s.y.toFixed(1)}" style="stroke:${mix(hub.c, 55)}" stroke-width="2.2" stroke-linecap="round"/><text class="badge" x="${s.x.toFixed(1)}" y="${(s.y + 4).toFixed(1)}" text-anchor="${Math.cos(s.ang) < -.3 ? 'end' : Math.cos(s.ang) > .3 ? 'start' : 'middle'}" dx="${Math.cos(s.ang) < -.3 ? -6 : Math.cos(s.ang) > .3 ? 6 : 0}" dy="${Math.sin(s.ang) > .3 ? 10 : Math.sin(s.ang) < -.3 ? -6 : 0}">${esc(s.text)}</text></g>`; }
    }
    // 고정한 노드(그래프 안의 제자리에 박힌 것): 지금 펼친 노드 가운데 이어진 것으로 빛살을 긋는다
    for (const pn of L.pinned || []) { if (pn.role !== 'pin') continue;
      for (const e of adj.get(pn.id) || []) { const t = at.get((e.a.id === pn.id ? e.b : e.a).id); if (t && t !== pn) drawRay(pn, t, e, 1.6, .6, 'pinray'); } }
    for (const st of L.sets) for (const s of st.shelves) deco += `<text class="head" x="${s.x + 14}" y="${st.BY + 30}">${s.name} ${s.all.length}${s.all.length > s.items.length ? ` <tspan class="badge">${esc(C.recent(s.items.length))}</tspan>` : ''}</text>` + (s.all.length > s.items.length ? `<text class="more-btn" data-more="${esc(st.hub.id)}|${s.k}" x="${s.x + 26}" y="${st.BY + 46 + s.items.length * 17 + 14}">${esc(C.more(s.all.length - s.items.length))}</text>` : '');
    const rot = (o: Item, pad: number, cls: string, n: number) => { const dg = todeg(o.ang!), flip = Math.cos(o.ang!) < 0, rr = flip ? dg + 180 : dg, ox = Math.cos(o.ang!) * (o.r + pad), oy = Math.sin(o.ang!) * (o.r + pad);
      return `<text class="${cls}" x="${ox.toFixed(1)}" y="${oy.toFixed(1)}" dy="4" text-anchor="${flip ? 'end' : 'start'}" transform="rotate(${rr.toFixed(1)} ${ox.toFixed(1)} ${oy.toFixed(1)})">${esc(short(o.label, n))}</text>`; };
    const lbl = (o: Item) => {
      if (o.role === 'hub') {
        return o.k === 'topic' ? `<text class="hub" x="0" y="${o.r + 20}" text-anchor="middle">${esc(short(o.label, 28))}</text><text class="badge" x="0" y="${o.r + 34}" text-anchor="middle">${esc(C.topicCount(o.src.count!))}</text>` : `<text class="hub" x="${(-o.r * .7 - 8)}" y="${o.r + 16}" text-anchor="end">${esc(short(o.k === 'bridge' ? o.label.replace(/ junction$/i, '') : o.label, 28))}${o.k === 'topic' ? `<tspan class="badge" dx="6">${esc(C.topicCount(o.src.count!))}</tspan>` : ''}</text>`; }
      if (o.role === 'right' && o.k === 'topic') { const flip = Math.cos(o.ang!) < 0; return `<text class="l1" x="${flip ? -(o.r + 7) : o.r + 7}" y="4" text-anchor="${flip ? 'end' : 'start'}">${esc(short(o.label, 18))}<tspan class="badge" dx="5">${o.src.count}</tspan></text>`; }
      if (o.role === 'up' || o.role === 'left' || o.role === 'right') return rot(o, 7, 'l1', o.farEnds ? 40 : 24);
      if (o.role === 'bridge') return `<text class="l2" x="0" y="${o.r + 13}" text-anchor="middle">${esc(short(o.label.replace(/ junction$/i, ''), 20))}</text>`;
      return `<text x="${o.r + 7}" y="4">${esc(short(o.label, 24))}</text>`; };
    // 초승달: 그 층 허브와 다른 주제의 노드에, 그 층 허브 주제의 색으로
    const compHub = new Map(L.comps.map((c) => [c.hub.id, c.hub]));
    const crescent = (o: Item) => { const h = compHub.get(o.comp!); if (!h || o.k === 'topic' || o.k === 'bridge' || o.role === 'hub') return ''; const ht = topicOf(h.src), d = topicOf(o.src);
      const cross = h.k === 'topic' ? !(d === ht || d!.startsWith(ht + '/')) : d !== ht; if (!cross) return '';
      const a = o.role === 'shelf' ? Math.PI : Math.atan2(h.y - o.y, h.x - o.x), dx = Math.cos(a) * o.r * .5, dy = Math.sin(a) * o.r * .5;
      return `<g class="cres">${shape(o.k, +dx.toFixed(2), +dy.toFixed(2), o.r, topicColour(ht))}${shape(o.k, 0, 0, o.r + 1.3, 'var(--c-bg)')}</g>`; };
    // 다리 = 갈래 고리. 잇는 주제마다 고리 한 조각을 그 주제 쪽으로 향하게 두고, 그 주제의 색으로 칠한다.
    // 조각 수가 곧 몇 주제를 잇는가이고, 조각의 방향이 곧 빛살이 나가는 방향이다. 이 층 밖으로도 이어지면 회색 조각이 하나 더 붙는다.
    const junction = (o: Item) => { const ts = o.farEnds ? o.farEnds.map((d, i, l) => ({ a: -Math.PI / 2 + i * 2 * Math.PI / l.length, c: topicColour(d) }))
        : ((o.subs || []).map((s) => at.get(s)).filter(Boolean) as Item[]).map((q) => ({ a: Math.atan2(q.y - o.y, q.x - o.x), c: q.c }));
      if (o.outside) { const avg = Math.atan2(ts.reduce((s, q) => s + Math.sin(q.a), 0), ts.reduce((s, q) => s + Math.cos(q.a), 0)); ts.push({ a: avg + Math.PI, c: 'var(--c-mute)' }); }
      if (!ts.length) return shape('concept', 0, 0, o.r, o.c, true);
      ts.sort((x, y) => x.a - y.a); const n = ts.length, R = o.r, w = 2.6;
      const half = Math.min(Math.PI / n - .32, 1.1);
      const arc = (a: number, c: string) => { const a0 = a - half, a1 = a + half, P = (q: number) => `${(Math.cos(q) * R).toFixed(2)} ${(Math.sin(q) * R).toFixed(2)}`;
        return `<path class="shape" d="M${P(a0)}A${R} ${R} 0 0 1 ${P(a1)}" fill="none" style="stroke:${c}" stroke-width="${w}" stroke-linecap="round"/>`; };
      return ts.map((q) => arc(q.a, q.c)).join('') + `<circle class="shape" r="${(R * .32).toFixed(2)}" style="fill:${mix(o.c, 80)}"/>`; };
    // 다리는 어느 자리에서나 이음 표식이다. 층 안의 다리(`role: bridge`)는 이미 끝을 알고,
    // 눌러 가운데(허브)가 된 다리는 CONNECTS 끝들의 주제 색으로 호를 그린다(알약 모양이 남지 않게)
    const bridgeBody = (o: Item) => o.role === 'bridge' || o.farEnds ? junction(o)
      : junction({ ...o, farEnds: [...new Set(bridgeEnds(o.src).map((m) => m.d).filter(Boolean))] });
    const body = (o: Item) => o.k === 'bridge' ? bridgeBody(o) : crescent(o) + (o.k === 'topic' ? ringS(o.r, o.role === 'hub' || fresh(o.src) ? o.c : mix(o.c, 70), o.role === 'hub' ? 2.6 : 2)
      : shape(o.k, 0, 0, o.r, o.src.vu ? o.c : fresh(o.src) ? o.c : mix(o.c, 55), !!o.src.vu)) + '';
    const compOf = new Map(L.comps.map((c) => [c.hub.id, c]));
    for (const o of L.items) { const oc = o.role === 'hub' ? compOf.get(o.id) : compOf.get(o.comp!), far = oc && oc.far && o.role !== 'hub' ? ' far' : '', cur = o.role === 'hub' && oc === F ? ' sel' : o.role === 'hub' && s.path.includes(o.id) ? ' onpath' : '';
      nodesS += `<g class="node${far}${cur}" data-id="${esc(o.id)}" data-role="${o.role}" data-chain="${esc((o.role === 'hub' ? (compOf.get(o.id)?.chain || []) : (o.chain || [])).map((n) => n.id).join('|'))}" data-level="${o.level}" tabindex="0" role="button" aria-label="${esc((KIND[o.k] || o.k) + ' ' + o.label)}" style="transform: translate(${o.x}px, ${o.y}px)"><circle class="ring" r="${o.r + 4.5}"/><circle class="hit" r="${o.r + 5}" fill="transparent"/>${body(o)}${lbl(o)}</g>`; }
    svg.innerHTML = `<defs>${defs}</defs><g id="cmp-deco">${deco}</g><g id="cmp-rays">${rays}</g><g id="cmp-tree"></g><g id="cmp-nodes">${nodesS}</g>`;
    // 카메라: 지금 층(허브·부채·목록)과 바로 위 허브가 들어오게
    const inFocus = (o: Item) => { const c = o.role === 'hub' ? compOf.get(o.id) : compOf.get(o.comp!); return o.role === 'shelf' || (!!c && c === F); };
    const box = L.items.filter((o) => inFocus(o) || nearPin(o, F) || (o.role === 'hub' && F.parent && o.id === F.parent.hub.id));
    const pts = box.flatMap(extentPts);
    const xs = pts.map((q) => q[0]), ys = pts.map((q) => q[1]);
    // 넓게 보기: 지나온 허브들 + 지금 층만 담는다(옆 갈래의 잎까지 담으면 너무 멀어진다)
    const plan = LS.cameraPlan(s); let camBox = null;
    if (plan.kind === 'chain') { const ids = new Set(plan.ids);
      const pts = L.items.filter((o) => (o.role === 'hub' && ids.has(o.id)) || o.comp === F.hub.id).flatMap(extentPts), qx = pts.map((q) => q[0]), qy = pts.map((q) => q[1]);
      camBox = { x: Math.min(...qx) - 40, y: Math.min(...qy) - 40, w: Math.max(...qx) - Math.min(...qx) + 80, h: Math.max(...qy) - Math.min(...qy) + 80 }; }
    else if (plan.kind === 'wide') { const pinHub = new Set(L.comps.filter((c) => c.pinOnly).map((c) => c.hub.id)), all = L.items.filter((o) => (o.role === 'hub' && !pinHub.has(o.id)) || o.comp === F.hub.id || o.role === 'shelf' || nearPin(o, F)), ax = all.flatMap((o) => [o.x - 160, o.x + 200]), ay = all.flatMap((o) => [o.y - 40, o.y + 40]), c = F.hub;
      const hw = Math.max(c.x - Math.min(...ax), Math.max(...ax) - c.x), hh = Math.max(c.y - Math.min(...ay), Math.max(...ay) - c.y);
      camBox = { x: c.x - hw, y: c.y - hh, w: 2 * hw, h: 2 * hh };   // 고른 노드를 가운데 두고, 펼친 길 전체가 보이게 물러난다
    } else if (plan.kind === 'fit') camBox = { x: Math.min(...xs) - 20, y: Math.min(...ys) - 20, w: Math.max(...xs) - Math.min(...xs) + 40, h: Math.max(...ys) - Math.min(...ys) + 40 };
    return { L, at, plan, box: camBox, pinsAt: pl.at };   // keep: 카메라를 그대로 둔다
  }
  // 남는 노드는 지난 그림의 자리에서 새 자리로(FLIP). 움직임이 끝나면 지금 층의 목록 가지를 키운다
  function animateIn(L: Layout, at: Map<string, Item>) {
    const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (!still && prevPos.size) { svg.classList.add('moving');
      for (const g of svg.querySelectorAll<SVGGElement>('#cmp-nodes .node')) { const was = prevPos.get(g.dataset.id!); g.style.transition = 'none'; if (was) g.style.transform = `translate(${was.x}px, ${was.y}px)`; else g.classList.add('enter'); }
      svg.getBoundingClientRect();
      fx.frame('flip', () => { for (const g of svg.querySelectorAll<SVGGElement>('#cmp-nodes .node')) { const o = at.get(g.dataset.id!); if (!o) continue; g.style.transition = ''; g.style.transform = `translate(${o.x}px, ${o.y}px)`; g.classList.remove('enter'); }
        fx.after('flip', 400, () => { svg.classList.remove('moving'); grow(L.sets.flatMap(buildTree)); }); }); }
    else grow(L.sets.flatMap(buildTree));
    prevPos = new Map(L.items.map((o) => [o.id, { x: o.x, y: o.y }]));
  }
  // 카메라: viewBox 를 부드럽게 옮긴다(화면 비율에 맞춰 넓힌다, LS.fitBox). 걸음마다 상태를 한 걸음 옮기고 그 카메라를 SVG 에 놓는다
  function flyView(b: Box) {
    set(LS.flyTo(st, b, { w: svg.clientWidth, h: svg.clientHeight }, performance.now(), matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 520));
    fx.cancelFrame('fly'); flyFrame();
  }
  function flyFrame() { set(LS.flyStep(st, performance.now(), svg.clientWidth)); showCamera(st); if (LS.flying(st)) fx.frame('fly', flyFrame); }
  // 카메라를 SVG 에 놓는다: viewBox 와 확대 단계(lod1 · lod2, LS.lodOf)
  function showCamera(s: LensState) { const v = s.camera.view; if (!v) return; svg.setAttribute('viewBox', LS.viewBoxAttr(v)); const l = LS.lodOf(s.camera); svg.classList.toggle('lod1', l.lod1); svg.classList.toggle('lod2', l.lod2); }

  // ── 줄기와 가지(지금 층의 목록): 허브에서 6시 쪽으로 곧게 나가 버스에 닿고, 목록마다 세로 줄기, 항목마다 가지 ─────────
  const Wd = (d: number) => Math.max(.8, 2.8 - d * .0016);   // 폭은 허브에서 멀수록 가늘어진다
  function buildTree(L: ShelfSet): Prim[] {
    const prims: Prim[] = [], hub = L.hub, hc = hub.c, Rc = 12, Rb = 7, F = 4.5, BY = L.BY;
    const cols = L.shelves.filter((s) => s.items.length); if (!cols.length) return prims;
    // 곧은 토막은 양 끝을 반 픽셀씩 늘려 이웃 조각(꺾임 · 모서리 · 끝마감) 안으로 들인다: 맞닿기만 하면 그 자리에 실금이 비친다
    const seg = (a: Pt, b: Pt, d0: number, c: string) => { const len = Math.hypot(b[0] - a[0], b[1] - a[1]), ux = (b[0] - a[0]) / (len || 1), uy = (b[1] - a[1]) / (len || 1), o = .5;
      prims.push({ t: 'seg', a: [a[0] - ux * o, a[1] - uy * o], b: [b[0] + ux * o, b[1] + uy * o], d0, d1: d0 + len, c }); return d0 + len; };
    const elbow = (x: number, y: number, R: number, a0: number, a1: number, d0: number, c: string) => { const len = R * Math.abs(a1 - a0); prims.push({ t: 'arc', cx: x, cy: y, R, a0, a1, d0, d1: d0 + len, c }); return d0 + len; };
    const fillet = (jx: number, jy: number, w: number, sx: number, sy: number, dd: number, c: string) => prims.push({ t: 'fil', jx, jy, wv: w, wh: w, sx, sy, F, d0: dd, c });
    const spine = (s: ShelfCol, ds: number, sy0: number) => { const lastY = s.items[s.items.length - 1].y, sEnd = seg([s.x, sy0], [s.x, lastY - Rb], ds, hc);
      s.items.forEach((n, q) => { const isLast = q === s.items.length - 1, dy = ds + (n.y - sy0), endX = n.x - (n.r + GAP + 3); let db: number;
        if (isLast) db = elbow(s.x + Rb, lastY - Rb, Rb, Math.PI, Math.PI / 2, sEnd, n.c); else { fillet(s.x, n.y, Wd(dy), 1, -1, dy, hc); fillet(s.x, n.y, Wd(dy), 1, 1, dy, hc); db = dy; }
        const de = seg([isLast ? s.x + Rb : s.x, n.y], [endX, n.y], db, n.c); prims.push({ t: 'cap', n, d0: de, c: n.c }); }); };
    if (L.mode === 'down') {   // 6시가 아래: 곧게 내려가 버스, 좌우로 갈라진다
      const CX = hub.x, xs = cols.map((s) => s.x), xL = Math.min(...xs), xR = Math.max(...xs);
      if (cols.length === 1 && Math.abs(cols[0].x - CX) < 1) { spine(cols[0], seg([CX, hub.y + hub.r + GAP], [CX, BY], 0, hc), BY); return prims; }
      const L0 = seg([CX, hub.y + hub.r + GAP], [CX, BY], 0, hc); fillet(CX, BY, Wd(L0), -1, -1, L0, hc); fillet(CX, BY, Wd(L0), 1, -1, L0, hc);
      const bL = xL < CX - 1 ? seg([CX, BY], [xL + Rc, BY], L0, hc) : L0, bR = xR > CX + 1 ? seg([CX, BY], [xR - Rc, BY], L0, hc) : L0;
      for (const s of cols) { const dj = L0 + Math.abs(s.x - CX);
        if (Math.abs(s.x - xL) < 1 && xL < CX - 1) { spine(s, elbow(xL + Rc, BY + Rc, Rc, -Math.PI / 2, -Math.PI, bL, hc), BY + Rc); continue; }
        if (Math.abs(s.x - xR) < 1 && xR > CX + 1) { spine(s, elbow(xR - Rc, BY + Rc, Rc, -Math.PI / 2, 0, bR, hc), BY + Rc); continue; }
        fillet(s.x, BY, Wd(dj), -1, 1, dj, hc); fillet(s.x, BY, Wd(dj), 1, 1, dj, hc); spine(s, dj, BY); }
      return prims; }
    // 6시가 옆: 허브에서 그쪽으로 곧게 나가 버스가 되고, 목록은 버스에서 아래로 내려간다(맨 끝은 둥글게 꺾인다)
    // 6시 방향으로 비스듬히 나가(빛살 사이의 빈 틈), Q 에서 둥글게 꺾여 가로 버스가 된다
    const sd = L.side, a6 = L.a6, ux = Math.cos(a6), uy = Math.sin(a6), Q = L.Q, ordered = [...cols].sort((a, b) => sd * (a.x - b.x)), end = ordered[ordered.length - 1];
    const vx = sd, vy = 0, turn = Math.acos(Math.max(-1, Math.min(1, ux * vx + uy * vy))), R = 16, dt = R * Math.tan(turn / 2);
    const P1: Pt = [Q!.x - ux * dt, Q!.y - uy * dt], P2: Pt = [Q!.x + vx * dt, Q!.y];
    let d0 = seg([hub.x + ux * (hub.r + GAP), hub.y + uy * (hub.r + GAP)], P1, 0, hc);
    if (turn > .02) { const cr = ux * vy - uy * vx, nx = cr > 0 ? -uy : uy, ny = cr > 0 ? ux : -ux, C0: Pt = [P1[0] + nx * R, P1[1] + ny * R];
      let b0 = Math.atan2(P1[1] - C0[1], P1[0] - C0[0]), b1 = Math.atan2(P2[1] - C0[1], P2[0] - C0[0]); if (b1 - b0 > Math.PI) b1 -= 2 * Math.PI; if (b0 - b1 > Math.PI) b1 += 2 * Math.PI;
      d0 = elbow(C0[0], C0[1], R, b0, b1, d0, hc); }
    const x0 = P2[0], busEnd = seg([x0, BY], [end.x - sd * Rc, BY], d0, hc);
    for (const s of ordered) { const dj = d0 + Math.abs(s.x - x0);
      if (s === end) { spine(s, elbow(end.x - sd * Rc, BY + Rc, Rc, -Math.PI / 2, sd > 0 ? 0 : -Math.PI, busEnd, hc), BY + Rc); continue; }
      fillet(s.x, BY, Wd(dj), -1, 1, dj, hc); fillet(s.x, BY, Wd(dj), 1, 1, dj, hc); spine(s, dj, BY); }
    return prims;
  }

  function primPoly(p: Prim, D: number): string {
    if (p.t === 'seg') { const q = Math.max(0, Math.min(1, (D - p.d0) / (p.d1 - p.d0))); if (q <= 0) return ''; const [ax, ay] = p.a, bx = ax + (p.b[0] - ax) * q, by = ay + (p.b[1] - ay) * q, len = Math.hypot(bx - ax, by - ay) || 1;
      const nx = -(by - ay) / len, ny = (bx - ax) / len, wa = Wd(p.d0), wb = Wd(p.d0 + (p.d1 - p.d0) * q); return P2([[ax + nx * wa, ay + ny * wa], [bx + nx * wb, by + ny * wb], [bx - nx * wb, by - ny * wb], [ax - nx * wa, ay - ny * wa]]); }
    if (p.t === 'arc') { const q = Math.max(0, Math.min(1, (D - p.d0) / (p.d1 - p.d0))); if (q <= 0) return ''; const A: Pt[] = [], B: Pt[] = [];
      for (let s = 0; s <= 12; s++) { const u = s / 12 * q, a = p.a0 + (p.a1 - p.a0) * u, w = Wd(p.d0 + (p.d1 - p.d0) * u); A.push([p.cx + Math.cos(a) * (p.R + w), p.cy + Math.sin(a) * (p.R + w)]); B.push([p.cx + Math.cos(a) * (p.R - w), p.cy + Math.sin(a) * (p.R - w)]); }
      return P2([...A, ...B.reverse()]); }
    if (p.t === 'fil') { if (D < p.d0) return ''; const { jx, jy, wv, wh, sx, sy, F } = p, ccx = jx + sx * (wv + F), ccy = jy + sy * (wh + F), a0 = sx > 0 ? Math.PI : 0, a1 = sy > 0 ? -Math.PI / 2 : Math.PI / 2;
      let da = a1 - a0; if (da > Math.PI) da -= 2 * Math.PI; if (da < -Math.PI) da += 2 * Math.PI; const arc: Pt[] = []; for (let s = 0; s <= 8; s++) { const a = a0 + da * s / 8; arc.push([ccx + Math.cos(a) * F, ccy + Math.sin(a) * F]); }
      // 모서리 조각은 줄기와 가지 안으로 0.6 만큼 파고든다(맞닿은 변에 실금이 서지 않게)
      const b = .6; arc[0] = [arc[0][0] - sx * b, arc[0][1]]; arc[arc.length - 1] = [arc[arc.length - 1][0], arc[arc.length - 1][1] - sy * b];
      return P2([...arc, [jx + sx * (wv - b), jy + sy * (wh - b)]]); }
    // 끝마감: arcPts 는 아래(y + w1)에서 시작해 위로 돈다. 다각형도 아래에서 시작해야 꼬이지 않는다(위에서 시작하면 두 변이 엇갈려 모래시계가 된다)
    if (p.t === 'cap') { if (D < p.d0) return ''; const n = p.n, rr = n.r + GAP + 3, w1 = Wd(p.d0), c = arcPts(n.x, n.y, rr, Math.PI, Math.asin(Math.min(.95, w1 / rr))); return P2([[n.x - rr - .8, n.y + w1], ...c, [n.x - rr - .8, n.y - w1]]); }
    return '';
  }
  function grow(prims: Prim[]) {
    fx.cancelFrame('grow'); const g = $id('cmp-tree')!, still = matchMedia('(prefers-reduced-motion: reduce)').matches;
    // 한 줄기는 한 색이다. 조각마다 거리로 다른 색을 칠하면 맞닿는 곳에 계단과 덧칠한 블록이 보였다. 허브와 다른 주제 색인 가지는
    // 줄기보다 먼저(아래에) 그려 그 시작이 줄기 안에 묻히게 한다
    const hc = LZ!.hub.c; prims = [...prims.filter((p) => p.c !== hc), ...prims.filter((p) => p.c === hc)];
    g.innerHTML = prims.map((p) => `<polygon data-bus="${p.c === hc ? 'hub' : 'item'}" style="fill:${mix(p.c, p.c === hc ? 82 : 66)}"/>`).join('');
    const els = [...g.children], maxD = Math.max(0, ...prims.map((p) => p.d1 ?? p.d0)), t0 = performance.now();
    const step = () => { const D = still ? 1e9 : (performance.now() - t0) * 2.2; prims.forEach((p, i) => els[i].setAttribute('points', primPoly(p, D))); if (D < maxD) fx.frame('grow', step); }; step();
  }

  // ── 다니기: 누르면 그 자리에서 펼친다. 지나온 층의 노드를 누르면 그 층으로 접고 거기서 새로 펼친다 ─────────────────
  const byAny = (id: string): LNode | null | undefined => byId.get(id) || byId.get(String(id).split('@@')[0]) || (id.startsWith('topic:') ? topics.get(id.slice(6)) : null);   // 'id@@…' 별칭은 앞의 id 로 찾는다
  const ids = (ns: LNode[]) => ns.map((n) => n.id);
  const canon = (id: string) => { const n = byAny(id); return n ? n.id : null; };   // 이 데이터의 정본 id(없으면 null)
  // 길(노드의 사슬)을 바꾸고 다시 그린다. push 면 앞뒤 기록에 남긴다
  function setPath(P: LNode[], push = true) { redraw(LS.enter(st, ids(P), push)); }
  function act(g: SVGElement | null, _e: { clientX: number; clientY: number }) { if (!g) return; const chain = (g.dataset.chain || '').split('|').filter(Boolean).map(byAny) as LNode[], n = byAny(g.dataset.id!)!;
    const isFocus = g.classList.contains('sel');
    if (g.dataset.role === 'hub' && !isFocus) { setPath(chain.length ? chain : [n]); return; }   // 지나온 층의 허브: 그 층으로 접는다
    if (isFocus) { ctx.onOpen?.(String(g.dataset.id).split('@@')[0]); return; }   // 가운데를 다시 누르면 자세히(상세 레일)
    setPath([...chain, n]); }
  on(svg, 'click', (e: At<MouseEvent>) => { if (swallow) { swallow = false; return; }   // 전체 보기에서 끈 뒤에 뗀 자리를 누른 것으로 치지 않는다
    if (st.whole.on) { wholeClick(e); return; }
    const mb = e.target.closest<SVGElement>('[data-more]');
    // 더 보기: 그 목록의 줄 수를 늘리고 옮겨 가는 움직임 없이 다시 그린다(카메라는 지금 층에 다시 맞는다)
    if (mb) { redraw(LS.showMore(st, mb.dataset.more!), false); return; }
    const sb = e.target.closest<SVGElement>('.stub'); if (sb) { setPath(sb.dataset.chain!.split('|').map(byAny) as LNode[]); return; } act(e.target.closest<SVGElement>('.node'), e); });
  on(svg, 'keydown', (e: At<KeyboardEvent>) => { if (st.whole.on) { wholeKey(e); return; } if (e.key !== 'Enter' && e.key !== ' ') return; const g = e.target.closest<SVGElement>('.node'); if (!g) return; e.preventDefault(); const r = g.getBoundingClientRect(); act(g, { clientX: r.right, clientY: r.top }); });
  const tip = $id('tip') as HTMLElement;
  // 엿보기: 노드 위에 잠깐(PEEK_MS) 머물면 풍선이 그 노드의 관계 수 · 붙은 것 · 가까운 이웃을 담은 카드로 커진다(fx 의 'peek').
  // 포인터 아래의 노드는 상태(hover)다: P 고정도 그것을 읽는다
  const PEEK_MS = 450;
  on(svg, 'pointerover', (e: At<PointerEvent>) => { if (st.whole.on) return; const g = e.target.closest<SVGElement>('.node'); if (g && g.dataset.id === st.hover) return; svg.classList.toggle('hot', !!g);
    svg.querySelectorAll<SVGElement>('.ray').forEach((r) => r.classList.toggle('near', !!g && (r.dataset.a === g?.dataset.id || r.dataset.b === g?.dataset.id)));
    fx.cancelAfter('peek'); set(LS.hover(st, g ? g.dataset.id! : null));
    if (g) { const n = byAny(g.dataset.id!)!, o = LZ!.items.find((q) => q.id === g.dataset.id); tip.style.display = 'block';
      tip.innerHTML = `${esc(n.t || n.path)}<small>${KIND[n.k] || n.k}${o?.rel ? ' · ' + esc(o.rel) : ''}${n.d ? ' · ' + esc(n.d) : ''}${n.ts ? ' · ' + n.ts : ''}</small>${g.classList.contains('sel') && ctx.onOpen ? `<small class="hint">${esc(C.detailHint)}</small>` : ''}`;  const gid = g.dataset.id!; fx.after('peek', PEEK_MS, () => { if (st.hover === gid && tip.style.display === 'block') tip.insertAdjacentHTML('beforeend', peekHTML(byAny(gid))); }); } else tip.style.display = 'none'; });
  on(svg, 'pointermove', (e: PointerEvent) => { tip.style.left = `${e.clientX + 14}px`; tip.style.top = `${e.clientY + 12}px`; });
  on(svg, 'pointerleave', () => { tip.style.display = 'none'; svg.classList.remove('hot'); fx.cancelAfter('peek'); set(LS.hover(st, null)); if (st.whole.on) { fx.cancelFrame('whover'); set(LS.wholeHover(st, '')); wholeOverlay(st); } });
  // 창 전체의 키가 렌즈에 닿는가: 입력 칸(sel) 안이 아니고, 렌즈가 문서에 붙어 보이고, 그 위에 층(시트 · 메뉴 · 팔레트 · 시간과 범례 창)이
  // 떠 있지 않을 때(keys.shortcutsRest 의 쌓임). 층 뒤에서 Backspace · Alt+← · → 가 렌즈를 움직이면 안 된다
  const lensHasKey = (e: WinKey, sel: string) => !e.target.closest?.(sel) && root.isConnected && !root.closest('[hidden]') && !shortcutsRest('surface', e.target as EventTarget, document.activeElement);
  onWin('keydown', (e: WinKey) => { if (e.defaultPrevented || !lensHasKey(e, 'input,textarea,[contenteditable],dialog')) return; if (e.altKey && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) e.preventDefault(); if (e.altKey && e.key === 'ArrowLeft') { if (LS.historyCan(st, -1)) redraw(LS.historyStep(st, -1, canon)); } else if (e.altKey && e.key === 'ArrowRight') { if (LS.historyCan(st, 1)) redraw(LS.historyStep(st, 1, canon)); }
    // Esc: 전체 보기에서는 고른 노드만 푼다. 전체 보기에서 바로 들어온 층이면 전체 보기로, 그 밖은 뿌리 층으로(LS.escapeStep)
    else if (e.key === 'Escape') { if (st.whole.on) { if (st.whole.sel) { set(LS.escapeStep(st, topics.get(ROOT)!.id)); wholeSelChanged(); } } else redraw(LS.escapeStep(st, topics.get(ROOT)!.id)); }
    else if (e.key === 'Backspace' && !st.whole.on && st.path.length > 1) redraw(LS.upOne(st)); });
  // 찾기: 주제 사슬을 따라 펼친 다음 그 노드를 편다
  const chainTo = (n: LNode): LNode[] => { const out: LNode[] = chainOf(topicOf(n)).map((p) => topics.get(p)!); if (n.k !== 'topic') out.push(n); return out; };
  const norm = (s: unknown) => String(s || '').toLowerCase();
  const matchQ = (n: LNode, q: string) => !q || norm(n.t || n.path || n.id).includes(q) || norm(n.id).includes(q);
  // 찾기는 사이드바 트리와 창의 수를 거른다. 층 보기의 그림은 흐리지 않고, 전체 보기는 맞지 않는 노드를 흐린다
  const setQuery = (q: string) => { const was = st.query; set(LS.setQuery(st, q)); if (st.whole.on) wholeBody(st); paintSide(st); treeChanged(st, !st.query && !!was); };
  // 굴려서 확대·끌어서 이동: viewBox 를 직접 옮긴다(펼친 그림이 화면보다 클 때)
  // 멀리서 볼수록 덜 그린다: 1단(노드 이름·목록 줄 글자 숨김), 2단(목록 줄과 잎 노드 숨김, 주제와 선만)
  // 확대 단계는 카메라가 움직일 때 그 순간의 칸 폭으로 다시 잰다(LS.lodOf). 굴리면 포인터 자리를 고정점으로 확대하고 날던 카메라는 멈춘다
  // 전체 보기는 손짓 동안 svg 를 감싼 겹을 CSS 로 옮기므로 svg 자신의 상자가 움직인다: 손이 닿는 자리는 무대(.cmp-stage) 기준으로 잰다
  const stageEl = root.querySelector('.cmp-stage') as HTMLElement;
  const rectFor = () => (st.whole.on ? stageEl.getBoundingClientRect() : svg.getBoundingClientRect());
  on(svg, 'wheel', (e: WheelEvent) => { e.preventDefault(); fx.cancelFrame('fly'); const r = rectFor();
    set(LS.zoomAt(st, (e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height, e.deltaY, svg.clientWidth)); if (st.whole.on) wMove(); else showCamera(st); }, { passive: false });
  // 끌기. 층 보기는 노드 · 목록 표 · 더 보기 위에서 시작하지 않는다(누름이 그 요소에 가야 한다: 붙잡으면 누름이 svg 로 간다).
  // 전체 보기는 노드 위에서도 끈다: 4px 넘게 움직여야 끌기이고, 끈 뒤의 누름은 삼킨다(swallow)
  let swallow = false, panFrom: { x: number; y: number; moved: boolean } | null = null;
  on(svg, 'pointerdown', (e: At<PointerEvent>) => { swallow = false;
    if (st.whole.on) { if (e.button > 0) return; panFrom = { x: e.clientX, y: e.clientY, moved: false }; set(LS.panStart(st, e.clientX, e.clientY)); svg.setPointerCapture?.(e.pointerId); return; }
    if (e.target.closest('.node, .stub, [data-more]')) return; set(LS.panStart(st, e.clientX, e.clientY)); svg.setPointerCapture(e.pointerId); svg.style.cursor = 'grabbing'; });
  on(svg, 'pointermove', (e: PointerEvent) => { if (!st.pan) { if (st.whole.on) wholePointer(e); return; }
    if (st.whole.on && panFrom) { if (!panFrom.moved && Math.hypot(e.clientX - panFrom.x, e.clientY - panFrom.y) < 4) return; if (!panFrom.moved) { panFrom.moved = true; svg.style.cursor = 'grabbing'; } }
    const r = rectFor(); set(LS.panMove(st, e.clientX, e.clientY, r.width, svg.clientWidth)); if (st.whole.on) wMove(); else showCamera(st); });
  on(svg, 'pointerup', () => { set(LS.panEnd(st)); svg.style.cursor = ''; if (panFrom) { swallow = panFrom.moved; panFrom = null; } });

  // ── 기간과 종류(옵션·범례가 다룬다): 기간 밖의 노드는 흐리게, 범례에서 끈 종류는 렌즈에서 뺀다 ─────────────────────
  const dated = nodes.filter((n) => when(n));
  const allDays: string[] = (() => { const ds = dated.map(when).sort(); if (!ds.length) return [C.before]; const lo = ds[Math.floor(ds.length * .005)], out = [C.before];
    for (let x = Date.parse(lo); x <= Date.parse(ds[ds.length - 1]); x += 864e5) out.push(new Date(x).toISOString().slice(0, 10)); return out; })();
  const dIdx0 = new Map(allDays.map((d, i) => [d, i])), dIdx = { get: (d: string) => dIdx0.get(d) ?? (d && d < allDays[1] ? 0 : undefined) };
  function dimLens(s: LensState) { if (s.whole.on) { if (wFilt !== `${s.tsel}|${s.query}`) wholeBody(s); return; } const sel = s.tsel; for (const g of svg.querySelectorAll<SVGGElement>('#cmp-nodes .node')) { const n = byAny(g.dataset.id!), w = n && n.k !== 'topic' ? when(n) : '';
      g.classList.toggle('out', !!sel && !!w && g.dataset.role !== 'hub' && (w < sel[0] || w > sel[1])); } }

  // ── 고정: 고른 노드를 다른 층으로 가도 왼쪽 판에 남긴다(Neo4j·Palantir 의 pin). 판의 노드를 짚으면 지금 렌즈에서 그와 이어진 노드가 진해지고,
  // 누르면 그리로 간다. 지금 렌즈에 고정한 노드와 이어진 노드가 있으면 그 노드에 작은 핀 표가 붙는다. 이 기기에만 저장한다(localStorage).
  // 고정은 이 기기에 스페이스마다 따로 둔다(`ctx.store`, 없으면 예전 열쇠). 다른 스페이스의 고정을 덮어쓰지 않고, 깨진 값은 빈 것으로 읽는다
  const PKEY = ctx.store ? `vinc.pins:${ctx.store}` : 'vinc.pins';
  const readJSON = (k: string, d: unknown): unknown => { try { const v = JSON.parse(localStorage.getItem(k) || ''); return v && typeof v === 'object' ? v : d; } catch { return d; } };
  set(LS.loadPins(st, LS.pinsFrom(readJSON(PKEY, []), (id) => !!byAny(id))));
  const dock = $id('dock') as HTMLElement;
  // ── 고정: 고른 노드를 **그래프 안의 그 자리**(배치 좌표)에 박아 둔다. 가운데를 옮겨 다시 배치해도 그 노드는 같은 자리에 남고,
  // 새로 펼친 노드 가운데 이어진 것으로 빛살이 간다. 겹치는 새 노드는 비켜 선다. 이 기기에만 저장한다.
  localStorage.removeItem('vinc.pins.at');   // 앞선 시안의 화면 좌표 자리는 쓰지 않는다
  function savePins(s: LensState) { localStorage.setItem(PKEY, JSON.stringify(s.pins)); localStorage.removeItem(`${PKEY}.w`); }   // .w: 끌어 옮긴 자리(걷어냄)
  // 고정 노드를 배치에 박는다. 놓인 고정 노드의 자리(at)를 돌려주면 상태가 그것을 기억한다(LS.pinsPlaced, 이번 마운트에서 마지막으로 놓였던 자리)
  function pinLayout(L: Layout, s: LensState): { L: Layout; at: [string, Point][] } {
    if (!s.pins.length) return { L, at: [] };
    const at = new Map(L.items.map((o) => [o.id, o])), pinned: Item[] = [], placedAt: [string, Point][] = [];
    for (const id of s.pins) { const n = byAny(id); if (!n) continue; let o = at.get(id);
      if (o) { placedAt.push([id, { x: o.x, y: o.y }]); o.pinned = true; pinned.push(o); continue; }
      const w2 = LS.entryGet(s.lastPin, id); if (!w2) continue;   // 길로 못 잇는 경우(배치가 그 노드를 못 놓을 때)는 마지막으로 놓였던 자리에
      const chain = chainTo(n).slice(0, -1);
      o = { id, src: n, k: n.k, x: w2.x, y: w2.y, r: n.k === 'topic' ? 13 : 6, c: colv(n), label: titleOf(n), role: 'pin', chain, comp: '', level: chain.length, pinned: true };
      L.items.push(o); pinned.push(o); }
    // 비키기: 고정한 자리에 겹친 새 노드를 그 자리 바깥으로 민다(허브와 목록 줄은 자리를 지킨다)
    for (const p of pinned) for (const o of L.items) { if (o.pinned || o.role === 'hub' || o.role === 'shelf') continue;
      const dx = o.x - p.x, dy = o.y - p.y, d = Math.hypot(dx, dy), need = o.r + p.r + 18; if (d >= need) continue;
      const ux = d ? dx / d : 1, uy = d ? dy / d : 0; o.x = p.x + ux * need; o.y = p.y + uy * need; }
    L.pinned = pinned; return { L, at: placedAt }; }
  // 카메라: 지금 층에서 너무 멀지 않은 고정 노드는 함께 담는다(멀면 무대 가장자리에 방향 표만 둔다)
  const nearPin = (o: Item, F: Comp) => !!o.pinned && Math.hypot(o.x - F.hub.x, o.y - F.hub.y) < 520;
  function togglePin(id: string) { const next = LS.togglePin(st, id); savePins(next); redraw(next); }
  const linkedTo = (id: string) => { const s = new Set<string>(); for (const e of adj.get(id) || []) s.add((e.a.id === id ? e.b : e.a).id);
    const n = byAny(id); if (n && n.k === 'topic') for (const m of nodes) if ((m.d || '') === n.path) s.add(m.id); return s; };
  function drawDock(s: LensState) {
    dock.hidden = !s.pins.length; if (!s.pins.length) return;
    const here = s.whole.on ? null : new Set(LZ ? LZ.items.map((o) => o.id) : []);   // 전체 보기는 모든 노드가 그림에 있다
    dock.innerHTML = `<div class="cmp-sec-h cmp-pinh">${pinIcon(14)}${esc(C.pinned(s.pins.length))}</div>` + s.pins.map((id) => { const n = byAny(id)!, ln = [...linkedTo(id)].filter((x) => !here || here.has(x)).length;
      return `<div class="pi" data-id="${esc(id)}"><button class="pg" data-id="${esc(id)}" title="${esc(n.t || n.path || id)}">${ico(n)}<span>${esc(short(titleOf(n), 34))}</span>${ln ? `<em>${ln}</em>` : ''}</button><button class="px" data-x="${esc(id)}" aria-label="${esc(C.unpin)}">✕</button></div>`; }).join('');
  }
  // 고정 표식. 노드마다 LZ.items 를 다시 훑지 않는다(큰 그래프에서 렌더마다 n² 이었다): id 표를 한 번 만든다
  // 전체 보기의 고정 표는 wholeOverlay 가 단다
  function markPins(s: LensState) { if (!LZ || s.whole.on) return; const lk = new Map<string, number>(); for (const id of s.pins) for (const x of linkedTo(id)) lk.set(x, (lk.get(x) || 0) + 1);
    const nodesEl = svg.querySelectorAll<SVGGElement>('#cmp-nodes .node');
    if (!s.pins.length) { for (const g of nodesEl) { g.querySelector('.pinmark')?.remove(); g.classList.remove('pinned'); } return; }
    const byItem = new Map(LZ.items.map((q) => [q.id, q]));
    for (const g of nodesEl) { g.querySelector('.pinmark')?.remove(); g.classList.toggle('pinned', s.pins.includes(g.dataset.id!)); const o = byItem.get(g.dataset.id!); if (!o) continue;
      const mine = s.pins.includes(g.dataset.id!), k = lk.get(g.dataset.id!); if (!mine && !k) continue;
      g.insertAdjacentHTML('beforeend', `<g class="pinmark${mine ? ' self' : ''}" transform="translate(${(o.r * .8 + 3).toFixed(1)} ${(-o.r * .8 - 3).toFixed(1)})"><circle r="${mine ? 6 : 3.2}"/>${mine ? `<svg x="-4.4" y="-4.4" width="8.8" height="8.8" viewBox="0 0 24 24">${PIN_PATHS}</svg>` : ''}</g>`); } }
  on(dock, 'click', (e: At<MouseEvent>) => { const x = e.target.closest<HTMLElement>('[data-x]'); if (x) { togglePin(x.dataset.x!); return; }
    const g = e.target.closest<HTMLElement>('.pg'); if (!g) return; const n = byAny(g.dataset.id!)!, o = LZ && LZ.items.find((q) => q.id === n.id && q.chain);
    if (o) setPath(o.role === 'hub' ? o.chain! : [...o.chain!, n]); else setPath(chainTo(n)); });
  on(dock, 'pointerover', (e: At<PointerEvent>) => { const pi = e.target.closest<HTMLElement>('.pi'); const on = pi ? new Set([pi.dataset.id!, ...linkedTo(pi.dataset.id!)]) : null;
    svg.classList.toggle('hot', !!on); svg.querySelectorAll<SVGGElement>('#cmp-nodes .node').forEach((g) => g.classList.toggle('pick', !!on && on.has(g.dataset.id!))); });
  on(dock, 'pointerleave', () => { svg.classList.remove('hot'); svg.querySelectorAll('#cmp-nodes .node.pick').forEach((g) => g.classList.remove('pick')); });
  // P 하나만(⌘/Ctrl+P 는 인쇄다). 입력 칸 · 숨은 면에서는 듣지 않는다
  onWin('keydown', (e: WinKey) => { if (e.key.toLowerCase() !== 'p' || e.ctrlKey || e.metaKey || e.altKey || !st.hover || !lensHasKey(e, 'input,textarea,[contenteditable]')) return; togglePin(st.hover); });
  // ── 가운데 요약: 사이드바 맨 위에 지금 가운데의 종류 · 주제 · 날짜 · 유효 기간과 대체 · 관계 종류별 수 · 많이 이어진 이웃.
  // 방향 있는 관계(선행 · 파생 · 상속 · 대체 · 되돌림)는 줄을 누르면 그 관계를 따라 사슬을 곧게 연다(갈래가 갈리면 그 자리에서 멈춘다)
  const sum = $id('sum'), SC = () => C.sum;
  const RELT: [string, 'pre' | 'der' | 'inh' | 'sup' | 'ret'][] = [['PREREQUISITE_OF', 'pre'], ['DERIVES_FROM', 'der'], ['INHERITS_FROM', 'inh'], ['SUPERSEDES', 'sup'], ['RETURNS_TO', 'ret']];
  const relOf = (n: LNode) => { const out = new Map<string, LNode[]>(), rel: LNode[] = [], att: Record<string, number> = { decision: 0, record: 0, episode: 0, doc: 0 };
    for (const e of adj.get(n.id) || []) { const o = e.a === n ? e.b : e.a; if (!o || o === n) continue;
      if (ATTACH.has(e.t)) { if (e.b === n && att[o.k] != null) att[o.k]++; else if (e.a === n) rel.push(o); continue; }
      const r = RELT.find(([t]) => t === e.t); if (!r) { if (e.t === 'RELATED_TO' || e.t === 'CONNECTS') rel.push(o); continue; }
      const k = `${r[1]}:${e.a === n ? 'out' : 'in'}`; if (!out.has(k)) out.set(k, []); if (!out.get(k)!.includes(o)) out.get(k)!.push(o); }
    return { out, rel: [...new Set(rel)], att }; };
  // 따라가기: 같은 종류 · 같은 방향의 이웃이 하나뿐인 동안 잇는다. 갈래(둘 이상) · 되돌아옴 · 12칸이면 멈춘다
  function chainFrom(n: LNode, type: string, dir: string, first: LNode | null | undefined) { const out: LNode[] = [], seen = new Set([n.id]); let cur: LNode | null | undefined = first;
    while (cur && !seen.has(cur.id) && out.length < 12) { out.push(cur); seen.add(cur.id);
      const nx = (adj.get(cur.id) || []).filter((e) => e.t === type && (dir === 'out' ? e.a === cur : e.b === cur)).map((e) => (dir === 'out' ? e.b : e.a));
      cur = nx.length === 1 ? nx[0] : null; }
    return out; }
  // 두 노드 사이 가장 짧은 길(관계의 방향은 보지 않는다). 주제는 이음에 없으므로 노드끼리만 걷는다. 10칸 넘게 걸리면 없는 것으로 본다
  function shortest(a: LNode, b: LNode, max = 10) { const prev = new Map<string, LNode | null>([[a.id, null]]), dist = new Map([[a.id, 0]]), q: LNode[] = [a];
    while (q.length) { const x = q.shift()!; if (x === b) break; if (dist.get(x.id)! >= max) continue;
      for (const e of adj.get(x.id) || []) { const y = e.a === x ? e.b : e.a; if (!y || prev.has(y.id)) continue; prev.set(y.id, x); dist.set(y.id, dist.get(x.id)! + 1); q.push(y); } }
    if (!prev.has(b.id)) return null; const out: LNode[] = []; for (let x: LNode | null | undefined = b; x; x = prev.get(x.id)) out.unshift(x); return out; }
  // 엿보기 카드: 관계 종류별 수(나침반 선의 표) · 관련 · 붙은 것 · 많이 이어진 이웃 넷. 주제면 종류별 수와 하위 주제 수
  function peekHTML(n: LNode | null | undefined) { if (!n) return ''; const S = SC(); let h = '<div class="peek">';
    if (n.k === 'topic') { const c: Record<string, number> = {}; for (const m of nodes) if (m.d === n.path || (n.path && m.d.startsWith(n.path + '/'))) c[m.k] = (c[m.k] || 0) + 1;
      h += `<div class="pr">${Object.entries(c).map(([k, v]) => `${esc(KIND[k] || k)} ${v}`).join(' · ')}${n.sub && n.sub.size ? ' · ' + esc(S.subtopics(n.sub.size)) : ''}</div>`; }
    else { const R = relOf(n), G = { pre: '›', der: '∘›', inh: '|›', sup: '⇢', ret: '↩' }, parts: string[] = [];
      for (const [, key] of RELT) { const k = (R.out.get(`${key}:in`) || []).length + (R.out.get(`${key}:out`) || []).length; if (k) parts.push(`${G[key]} ${k}`); }
      if (R.rel.length) parts.push(S.related(R.rel.length)); const at = Object.values(R.att).reduce((x, y) => x + y, 0); if (at) parts.push(`${S.attached} ${at}`);
      if (parts.length) h += `<div class="pr">${parts.map(esc).join(' · ')}</div>`;
      const nb4 = [...new Set([...(adj.get(n.id) || [])].map((e) => (e.a === n ? e.b : e.a)).filter((o) => o && isMean(o)))].sort((a, b) => (b.g || 0) - (a.g || 0)).slice(0, 4);
      for (const o of nb4) h += `<span class="pn">${esc(short(titleOf(o), 40))}</span>`; }
    return h + '</div>'; }
  const ico = (n: LNode) => `<svg viewBox="-7 -7 14 14">${n.k === 'topic' ? topicRing(5, colv(n), 1.8) : shape(n.k, 0, 0, 5, colv(n), !!n.vu)}</svg>`;
  const nb = (o: LNode, extra = '') => `<button class="sn" data-go="${esc(o.id)}" title="${esc(o.t || o.path || o.id)}" ${extra}>${ico(o)}<span>${esc(short(titleOf(o), 34))}</span></button>`;
  // 요약의 가운데: 층 보기는 지금 가운데, 전체 보기는 고른 노드(없으면 뿌리). 요약에서 이어 여는 길은 전체 보기면 그 노드의 주제 사슬이다
  const sumNode = (s: LensState) => (s.whole.on ? byAny(s.whole.sel) || topics.get(ROOT)! : byAny(LS.centreOf(s)));
  const sumPath = (s: LensState) => (s.whole.on ? chainTo(sumNode(s)!) : pathOf(s));
  function drawSum(s: LensState) {
    const n = sumNode(s); if (!sum || !n) return; const S = SC(); let h = '';
    h += `<div class="sh">${ico(n)}<b>${esc(short(titleOf(n), 60))}</b></div>`;
    if (n.k === 'topic') {
      const ns = nodes.filter((m) => m.d === n.path || (n.path && m.d.startsWith(n.path + '/')) || (!n.path)), c: Record<string, number> = {}; for (const m of ns) c[m.k] = (c[m.k] || 0) + 1;
      const last = ns.map(when).filter(Boolean).sort().pop();
      h += `<div class="sk">${esc(C.rel.topic)}${n.path ? ' · ' + esc(n.path) : ''}${last ? ' · ' + esc(S.latest(last)) : ''}</div>`;
      h += `<div class="sr">${Object.entries(c).map(([k, v]) => `${esc(KIND[k] || k)} ${v}`).join(' · ') || esc(S.empty)}${n.sub && n.sub.size ? ` · ${esc(S.subtopics(n.sub.size))}` : ''}</div>`;
      const top = ns.filter(isMean).sort((a, b) => (b.g || 0) - (a.g || 0)).slice(0, 5);
      if (top.length) h += `<div class="sl">${esc(S.mostLinked)}</div>${top.map((o) => nb(o)).join('')}`;
    } else {
      const d = when(n);
      h += `<div class="sk">${esc(KIND[n.k] || n.k)}${n.d ? ' · ' + esc(n.d) : ''}${d ? ' · ' + esc(d) : ''}</div>`;
      const R = relOf(n), sup = R.out.get('sup:in') || [];
      if (n.vf || n.vu) h += `<div class="sv${n.vu ? ' closed' : ''}">${esc(n.vu ? S.held(n.vf || '', String(n.vu).slice(0, 10)) : S.holds(n.vf))}</div>`;
      if (sup.length) h += `<div class="sv closed">${esc(S.replacedBy)} ${sup.map((o) => nb(o)).join('')}</div>`;
      for (const [type, key] of RELT) for (const dir of ['in', 'out'] as const) { const l = R.out.get(`${key}:${dir}`); if (!l || !l.length || (type === 'SUPERSEDES' && dir === 'in')) continue;
        const id = `${key}:${dir}`, open = s.sumOpen.includes(id);
        h += `<div class="sf"><button class="sfr" data-follow="${type}|${dir}" title="${esc(S.followTip)}"><i class="sg sg-${key}"></i><span>${esc(S.rel[key][dir])}</span><b>${l.length}</b><em>›</em></button>`;
        if (l.length > 1) h += `<button class="sfx" data-sx="${id}" aria-expanded="${open}">${open ? '▾' : '▸'}</button>`;
        h += `</div>`;
        if (open || l.length === 1) h += `<div class="sfl">${l.slice(0, 12).map((o) => nb(o, `data-chain="${type}|${dir}"`)).join('')}</div>`; }
      if (R.rel.length) h += `<div class="sl">${esc(S.related(R.rel.length))}</div>${R.rel.sort((a, b) => (b.g || 0) - (a.g || 0)).slice(0, 5).map((o) => nb(o)).join('')}`;
      const at = Object.entries(R.att).filter(([, v]) => v); if (at.length) h += `<div class="sr">${esc(S.attached)} ${at.map(([k, v]) => `${esc(KIND[k] || k)} ${v}`).join(' · ')}</div>`;
    }
    const pinsTo = (s.pins.filter((id) => id !== n.id).map(byAny).filter(Boolean) as LNode[]).slice(0, 3);
    if (pinsTo.length) { h += `<div class="sl">${esc(S.pathTo)}</div>` + pinsTo.map((o) => `<button class="sn" data-path="${esc(o.id)}">${ico(o)}<span>${esc(short(titleOf(o), 34))}</span></button>`).join('');
      if (s.noPath === n.id) h += `<div class="sr">${esc(S.noPath)}</div>`; }
    if (ctx.onOpen) h += `<button class="btn btn-sm sdet" data-sopen="1">${esc(S.details)}</button>`;
    sum.innerHTML = h; }
  sum && on(sum, 'click', (e: At<MouseEvent>) => { const n = sumNode(st); if (!n) return;
    const x = e.target.closest<HTMLElement>('[data-sx]'); if (x) { set(LS.toggleSumOpen(st, x.dataset.sx!)); drawSum(st); return; }
    if (e.target.closest('[data-sopen]')) { ctx.onOpen?.(n.id); return; }
    const pp = e.target.closest<HTMLElement>('[data-path]');
    if (pp) { const o = byAny(pp.dataset.path!); if (!o) return;
      if (o.k === 'topic' || n.k === 'topic') { goTo(o.id); return; }   // 주제는 노드 이음에 없다: 주제 사슬로 간다
      const r = shortest(n, o); if (!r) { set(LS.setNoPath(st, n.id)); drawSum(st); return; }
      redraw(LS.followChain(LS.setNoPath(st, null), ids([...sumPath(st), ...r.slice(1)]), ids(r))); return; }
    const f = e.target.closest<HTMLElement>('[data-follow]'), g = e.target.closest<HTMLElement>('[data-go]');
    if (f || (g && g.dataset.chain)) { const [type, dir] = (f ? f.dataset.follow : g!.dataset.chain)!.split('|');
      const firsts = (adj.get(n.id) || []).filter((q) => q.t === type && (dir === 'out' ? q.a === n : q.b === n)).map((q) => (dir === 'out' ? q.b : q.a));
      const first = g ? byAny(g.dataset.go!) : firsts.length === 1 ? firsts[0] : null;
      if (!first) { const id = `${RELT.find(([t]) => t === type)![1]}:${dir}`; set(LS.openSum(st, id)); drawSum(st); return; }   // 갈래가 여럿이면 먼저 고르게 편다
      const ch = chainFrom(n, type, dir, first); redraw(LS.followChain(st, ids([...sumPath(st), ...ch]), [n.id, ...ids(ch)])); return; }
    if (g) { const o = byAny(g.dataset.go!); if (!o) return; const inLayer = !st.whole.on && LZ && LZ.items.some((q) => q.id === o.id && q.comp === n.id);
      if (inLayer) setPath([...pathOf(st), o]); else goTo(o.id); } });
  // 전체 배치(네 팔). 맨 위 주제의 상자를 가운데 두고, 그 하위 주제 가지를 아래·오른쪽·위·왼쪽 팔에 나눈다. 팔은 바람개비처럼
  // 맨 위 상자의 한 모서리에서 시계 방향으로 뻗어(위 팔은 오른쪽으로, 오른쪽 팔은 아래로 …) 서로의 자리에 들어가지 않는다.
  // 가지 하나는 상자 하나다: 자기 층(폭 2·HW, 깊이 TOP + DEP) 아래에 자식 상자들을 열을 맞춘 격자로 둔다. 격자의 열 사이와
  // 줄 사이는 비어 있는 통로이고, 부모에서 자식으로 가는 선(route)은 그 통로만 따라 꺾는다. 열 수는 상자가 화면 비율에 가깝게 고른다.
  // 좌표: u 는 팔 방향(부모에서 멀어지는 쪽), v 는 그 3시 쪽. 가로 팔에서는 이름표가 u 쪽으로 길어 앞뒤 여유를 더 준다
  function crossCanon(L: Layout, meas: Meas | null = null, armsOf: Map<string, number> | null = null, straight = false): Plan {
    const GAP = 80, ASP = 1.6, OUT: Placement = new Map(), KO = new Map<Comp, Comp[]>(), q = (c: Comp, o: Arm['o']) => meas && meas.get(c.hub.id) && meas.get(c.hub.id)![o];
    for (const c of L.comps) if (c.parent && c.hub.k === 'topic') { if (!KO.has(c.parent)) KO.set(c.parent, []); KO.get(c.parent)!.push(c); }
    const rowsOf = (c: Comp) => Math.min(ROWS, Math.max(0, ...Object.values(c.cp.G.shelf).map((l) => l.length)));
    // 잰 크기가 있으면 그것에 여유를 더하고, 없으면(처음 열 때) 나침반 반지름과 목록 수로 어림한다
    // o: 층을 세운 방향. v 는 아래 팔, u 는 위 팔(목록이 부모 쪽으로 늘어지므로 따로 잰다), h 는 가로 팔
    const HW = (c: Comp, o: Arm['o'], hz = o === 'h') => (q(c, o) ? q(c, o)!.hw + 40 : Math.max(c.cp.E + 90, (c.cp.FP > c.cp.E ? c.cp.FP - c.cp.E * .6 : 0) / 2 + 150, hz ? 46 + rowsOf(c) * 17 + 70 : 0));
    const TOP = (c: Comp, o: Arm['o'], hz = o === 'h') => (q(c, o) ? q(c, o)!.top + 24 : c.cp.E * .75 + (hz ? 170 : 0)), DEP = (c: Comp, o: Arm['o'], hz = o === 'h') => (q(c, o) ? q(c, o)!.dep + 24 : c.cp.FP + (hz ? 120 : 0));
    const grid = (ks: Comp[], k: number, B: Map<Comp, Sized>): Grid => { const rows = Math.ceil(ks.length / k), cw: number[] = Array(k).fill(0), rh: number[] = Array(rows).fill(0);
      ks.forEach((c, i) => { const b = B.get(c)!; cw[i % k] = Math.max(cw[i % k], b.w); rh[Math.floor(i / k)] = Math.max(rh[Math.floor(i / k)], b.h); });
      return { k, cw, rh, W: cw.reduce((x, w) => x + w, 0) + GAP * (k - 1), H: rh.reduce((x, h) => x + h, 0) + GAP * (rows - 1) }; };
    const best = (ks: Comp[], B: Map<Comp, Sized>, asp: number, w0: number, h0: number) => { let b: Packed | null = null;
      for (let k = 1; k <= ks.length; k++) { const g = grid(ks, k, B), w = Math.max(w0, g.W), h = h0 + (h0 ? GAP : 0) + g.H, sc = w * h * (1 + Math.abs(Math.log(w / h / asp)));
        if (!b || sc < b.sc) b = { ...g, w, h, sc }; }
      return b; };
    const measure = (c: Comp, o: Arm['o'], B: Map<Comp, Sized>) => { const ks = KO.get(c) || []; ks.forEach((k) => measure(k, o, B)); const w0 = 2 * HW(c, o), h0 = TOP(c, o) + DEP(c, o);
      B.set(c, ks.length ? { ...best(ks, B, o === 'h' ? 1 / ASP : ASP, w0, h0)!, w0, h0 } : { w: w0, h: h0, w0, h0 }); };
    const S = (A: Arm, v: number, u: number): Pt => [A.O![0] + A.g[0] * u + A.p[0] * v, A.O![1] + A.g[1] * u + A.p[1] * v];
    // 격자 하나를 놓는다. pre 는 부모 허브에서 이 격자 위 통로(bus0)까지 온 길이다
    // straight(하나씩 열기): 부모 바로 앞(팔 축 위)에 홀로 선 층은 통로를 돌지 않고 부모에서 곧게 잇는다
    const lay = (ks: Comp[], b: Grid, x0: number, g0: number, A: Arm, pre: Pt[], vp = 0) => {
      const colL: number[] = []; for (let j = 0, x = x0; j < b.k; j++) { colL.push(x); x += b.cw[j] + GAP; }
      const rowT: number[] = []; for (let r = 0, y = g0; r < b.rh.length; r++) { rowT.push(y); y += b.rh[r] + GAP; }
      const bus0 = g0 - GAP / 2;
      ks.forEach((c, i) => { const j = i % b.k, r = Math.floor(i / b.k), vc = colL[j] + b.cw[j] / 2, uT = rowT[r];
        // 둘째 줄부터는 열 사이 통로로 내려간다. 마지막 열은 왼쪽 통로를 쓰고, 열이 하나면 상자 오른쪽 바깥(부모 통로와 겹치지 않게 1/3 지점)으로
        const vk = j < b.k - 1 ? colL[j] + b.cw[j] + GAP / 2 : b.k > 1 ? colL[j] - GAP / 2 : colL[j] + b.cw[j] + GAP / 3;
        put(c, vc, uT, A, r === 0 && straight && b.k === 1 && Math.abs(vc - vp) < .5 ? [pre[0]] : r === 0 ? [...pre, S(A, vc, bus0)] : [...pre, S(A, vk, bus0), S(A, vk, uT - GAP / 2), S(A, vc, uT - GAP / 2)]); }); };
    const put = (c: Comp, vc: number, uT: number, A: Arm, route: Pt[]) => { const b = A.B.get(c) as Sized & Grid, hu = uT + TOP(c, A.o), hub = S(A, vc, hu);
      OUT.set(c.hub.id, { x: hub[0], y: hub[1], th12: A.th12, route: [...route, hub] });
      const ks = KO.get(c) || []; if (!ks.length) return;
      const g0 = uT + b.h0 + GAP, port = vc + b.w0 / 2 - 12;   // 자기 층의 3시 쪽 가장자리로 나가 그 줄을 따라 자식 격자 위 통로로
      lay(ks, b, vc - b.W / 2, g0, A, [hub, S(A, port, hu), S(A, port, g0 - GAP / 2)], vc); };
    const R = L.comps[0], kids = KO.get(R) || [];
    OUT.set(R.hub.id, { x: R.hub.x, y: R.hub.y, th12: -90 });
    if (!kids.length) return { out: OUT, arms: new Map(armsOf || []) };
    const Bv = new Map<Comp, Sized>(), Bu = new Map<Comp, Sized>(), Bh = new Map<Comp, Sized>(); for (const k of kids) { measure(k, 'v', Bv); measure(k, 'u', Bu); measure(k, 'h', Bh); }
    const hwR = HW(R, 'v'), Ht = TOP(R, 'v') + 16, Hb = DEP(R, 'v') + 16;   // 맨 위 주제의 상자: 개념은 9시, 목록은 6시
    const ARMS: Arm[] = [{ g: [0, 1], p: [1, 0], hz: false, o: 'v', th12: -90, B: Bv }, { g: [1, 0], p: [0, -1], hz: true, o: 'h', th12: 180, B: Bh },
                  { g: [0, -1], p: [-1, 0], hz: false, o: 'u', th12: 90, B: Bu }, { g: [-1, 0], p: [0, 1], hz: true, o: 'h', th12: 0, B: Bh }];
    // 팔의 격자(폭 W = 팔에 수직인 길이, 깊이 H)에서 팔이 차지하는 화면 상자와 격자의 시작 v
    // 팔 격자는 바람개비처럼 맨 위 주제 상자의 한쪽 모서리에 맞춘다(이웃 팔과 엇갈리게). solo: 쓰는 팔이 하나뿐이면 가운데에 둔다
    const span = (_A: Arm, P: Packed | null, i: number, solo = false) => { if (!P) return null; const { W, H } = P, hw = solo ? Infinity : hwR;
      if (i === 0) { const x1 = Math.min(hw, W / 2); return { box: [x1 - W, Hb + GAP, x1, Hb + GAP + H], O: [0, Hb + GAP], v0: x1 - W }; }
      if (i === 1) { const y0 = Math.max(solo ? -Infinity : -Ht, -W / 2); return { box: [hwR + GAP, y0, hwR + GAP + H, y0 + W], O: [hwR + GAP, 0], v0: -(y0 + W) }; }
      if (i === 2) { const x0 = Math.max(-hw, -W / 2); return { box: [x0, -Ht - GAP - H, x0 + W, -Ht - GAP], O: [0, -Ht - GAP], v0: -(x0 + W) }; }
      const y1 = Math.min(solo ? Infinity : Hb, W / 2); return { box: [-hwR - GAP - H, y1 - W, -hwR - GAP, y1], O: [-hwR - GAP, 0], v0: y1 - W }; };
    const order = new Map(kids.map((k, i) => [k, i])), members = ARMS.map((): Comp[] => []);
    const packArm = (i: number, ks: Comp[]) => (ks.length ? best(ks, ARMS[i].B, ARMS[i].hz ? 1 / ASP : ASP, 0, 0) : null);
    const score = (packs: (Packed | null)[]) => { let x0 = -hwR, y0 = -Ht, x1 = hwR, y1 = Hb;
      packs.forEach((P, i) => { const sp = span(ARMS[i], P, i); if (sp) { x0 = Math.min(x0, sp.box[0]); y0 = Math.min(y0, sp.box[1]); x1 = Math.max(x1, sp.box[2]); y1 = Math.max(y1, sp.box[3]); } });
      const w = x1 - x0, h = y1 - y0; return w * h * (1 + Math.abs(Math.log(w / h / ASP))); };
    // 큰 가지부터, 전체 상자가 가장 작고 화면 비율에 가까워지는 팔에 넣는다. 팔은 마운트 때 어림한 크기로 한 번만 고르고,
    // 잰 크기로 다시 놓거나 하나씩 열기를 놓을 때(armsOf)는 그대로 두어 주제마다 방향이 늘 같다
    const area = (k: Comp) => Bv.get(k)!.w * Bv.get(k)!.h;
    if (armsOf) for (const k of kids) { const i = armsOf.get(k.hub.id); if (i != null) members[i].push(k); }
    for (const k of [...kids].filter((k) => !armsOf || armsOf.get(k.hub.id) == null).sort((a, b) => area(b) - area(a))) { let bi = 0, bs = Infinity;
      for (let i = 0; i < 4; i++) { const packs = members.map((m, j) => packArm(j, (j === i ? [...m, k] : m).sort((a, b) => order.get(a)! - order.get(b)!))), sc = score(packs); if (sc < bs) { bs = sc; bi = i; } }
      members[bi].push(k); members[bi].sort((a, b) => order.get(a)! - order.get(b)!); }
    const arms = new Map<string, number>([...(armsOf || []), ...members.flatMap((m, i) => m.map((k): [string, number] => [k.hub.id, i]))]);
    // 맨 위 주제에서 나가는 길: 3시 쪽(하위 주제 자리, 비어 있다)으로 나가 상자 둘레의 통로를 돌아 그 팔의 첫 통로로
    const E1: Pt = [hwR + GAP / 2, 0], hub0: Pt = [R.hub.x, R.hub.y], port0: Pt = [hwR - 12, 0];
    const lead: Pt[][] = [[hub0, port0, E1], [hub0, port0, E1], [hub0, port0, E1, [hwR + GAP / 2, -Ht - GAP / 2]], [hub0, port0, E1, [hwR + GAP / 2, Hb + GAP / 2], [-hwR - GAP / 2, Hb + GAP / 2]]];
    lead[0].push([hwR + GAP / 2, Hb + GAP / 2]);
    const solo = straight && members.filter((m) => m.length).length === 1;
    ARMS.forEach((A, i) => { const ks = members[i]; if (!ks.length) return; const P = packArm(i, ks), sp = span(A, P, i, solo)!; A.O = sp.O as Pt; lay(ks, P!, sp.v0, 0, A, lead[i]); });
    return { out: OUT, arms };
  }
  // 하나씩 열기의 배치: 모든 주제를 펼친 정해진 자리와 같은 묶음 배치(crossCanon)에 지금 펼친 층들(지나온 길과 고정 노드로 가는 길)만 넘긴다.
  // 층의 크기는 방금 부모 기준으로 놓아 본 배치(L)에서 층마다 자기 방향으로 잰다(접힌 목록 표 · 지금 층의 목록 자리까지).
  // 팔은 정해진 자리에서 고른 것(armsOf)을 그대로 쓰므로 주제마다 방향(th12)이 어떤 길로 와도 같다. 주제 층만 묶음에 들어가고,
  // 주제가 아닌 노드로 들어간 층과 그 아래는 부모 기준 자리(settle)에 남는다
  function packPath(L: Layout, arms: Map<string, number> | null): Placement | null {
    if (!arms || !L.comps.some((c) => c.parent && c.hub.k === 'topic' && c.parent.hub.k === 'topic')) return null;
    const meas: Meas = new Map(), shelfOf = new Map(L.sets.map((st): [Comp, Item[]] => [st.comp, st.shelves.flatMap((s) => s.items)]));
    for (const c of L.comps) { if (c.hub.k !== 'topic') continue;
      const a = rad(c.th12 + 180), g: Pt = [Math.cos(a), Math.sin(a)], pv: Pt = [g[1], -g[0]]; let u0 = 0, u1 = 0, v0 = 0, v1 = 0;
      const pts: Pt[] = [...L.boxes!(c).flatMap(([x0, y0, x1, y1]): Pt[] => [[x0, y0], [x1, y0], [x0, y1], [x1, y1]]), ...(shelfOf.get(c) || []).flatMap(extentPts)];
      for (const [x, y] of pts) { const dx = x - c.hub.x, dy = y - c.hub.y, u = dx * g[0] + dy * g[1], v = dx * pv[0] + dy * pv[1];
        u0 = Math.min(u0, u); u1 = Math.max(u1, u); v0 = Math.min(v0, v); v1 = Math.max(v1, v); }
      const m = { top: -u0, dep: u1, hw: Math.max(-v0, v1) }; meas.set(c.hub.id, { v: m, u: m, h: m }); }
    // 층 사이는 적어도 부모 기준으로 놓던 거리만큼 둔다(세로 팔은 두 반지름 합의 3/4, 가로 팔은 6할에 이름표 폭): 작은 층끼리 붙지 않게
    for (const c of L.comps) { const p = c.parent, m = meas.get(c.hub.id); if (!p || !m || !meas.has(p.hub.id)) continue;
      const hz = Math.abs(Math.cos(rad(c.th12 + 180))) > .5, D0 = hz ? (p.cp.E + c.cp.E) * .62 + 320 : (p.cp.E + c.cp.E) * .75 + 40;
      m.v!.top = Math.max(m.v!.top, D0 - meas.get(p.hub.id)!.v!.dep - (p === L.comps[0] ? 40 : 24) - 80 - 24); }
    return crossCanon(L, meas, arms, true).out;
  }
  // 정해진 자리: 모든 주제를 지금과 같은 규칙으로 한꺼번에 펼쳐(목록 자리까지 비워) 한 번 잰다. 하나씩 들어가든 전체를 펼치든 주제는 이 자리에 선다
  const allExpand = () => { const m = new Map<string, Set<string>>(); for (const tp of topics.values()) { const set = new Set<string>(); for (const s2 of tp.sub) set.add(`topic:${s2}`); m.set(tp.id, set); } return m; };
  // 재기 · 정해진 자리 짓기의 상태: 전부 펼치고 거르기 없이(지금 화면의 상태는 읽지도 바꾸지도 않는다)
  // cps: 층마다의 나침반(compassOf). 거르기가 없으니 정해진 자리 짓기와 세 방향 재기가 같은 것을 한 번만 모은다
  const MCPS = new Map<string, Cp>(), measS = (place: Placement | null, canon = false): LayoutIn => ({ mode: 'all', path: [topics.get(ROOT)!], place, dirs: place, canon, range: null, koff: new Set(), filt: new Map(), cps: MCPS, more: st.more, pins: new Set(st.pins), pinChain: new Set() });
  { CANONL = layoutTree(topics.get(ROOT)!, allExpand(), null, measS(null, true)); PLAN_EST = crossCanon(CANONL); }
  // 팔 정하기: 층의 실제 크기를 재서 팔을 한 번 고른다. 어림한 크기로 고르면 배치가 3할 넘게 넓어지고, 뒤늦게 다시
  // 고르면 그 뒤 하나씩 열기의 방향이 바뀐다. 그래서 마운트 직후 한가할 때 잰다(같은 주제 나무는 MEAS_CACHE).
  // 이미 깊이 들어가 있으면 한 번 다시 그려 방향을 맞춘다
  const measSig = `${ROOT}|${topics.size}|${[...topics.values()].reduce((h, t) => (h * 31 + (t.count || 0) + t.path.length) % 1000000007, 7)}`;
  function settleArms() {
    if (st.armsMeasured || !CANONL) return;
    fx.cancelIdle('arms');
    let meas = MEAS_CACHE.get(measSig);
    if (!meas) { meas = measureCross(); MEAS_CACHE.set(measSig, meas); if (MEAS_CACHE.size > 8) MEAS_CACHE.delete(MEAS_CACHE.keys().next().value!); }
    PLAN_MEAS = crossCanon(CANONL, meas); set(LS.armsSettled(st));
  }
  if (MEAS_CACHE.has(measSig)) settleArms();
  else fx.idle('arms', () => { if (st.armsMeasured || !root.isConnected) return;
    settleArms(); if (st.path.length > 1) redraw(st); });
  // 층의 실제 크기: 모든 층을 세 방향(아래 팔, 위 팔, 가로 팔)으로 세워 그려 보고, 층마다 허브에서 잰 범위를 남긴다.
  // 기간·종류 거르기 없이 잰다(거른 뒤에 다시 켜도 상자가 모자라지 않게)
  function measureCross(): Meas {
    const out: Meas = new Map(), rootId = topics.get(ROOT)!.id;
    for (const [o, t12] of [['v', -90], ['u', 90], ['h', 180]] as const) {
        const T: Placement = new Map([...armsOf(st).out].map(([id, t]) => [id, { x: t.x, y: t.y, th12: id === rootId ? -90 : t12 }])), a = rad(t12 + 180), g: Pt = [Math.cos(a), Math.sin(a)];
        const L = layoutTree(topics.get(ROOT)!, allExpand(), null, measS(T)), own = new Map(L.comps.map((c): [string, Item[]] => [c.hub.id, [c.hub, ...c.items.filter((o) => o.id && !o.link) as Item[]]]));
        for (const st of L.sets) for (const sh of st.shelves) own.get(st.hub.id)?.push(...sh.items);
        for (const [id, list] of own) { const h = list[0]; let u0 = 0, u1 = 0, v0 = 0, v1 = 0;
          for (const [x, y] of list.flatMap(extentPts)) { const dx = x - h.x, dy = y - h.y, u = dx * g[0] + dy * g[1], v = dx * g[1] - dy * g[0];
            u0 = Math.min(u0, u); u1 = Math.max(u1, u); v0 = Math.min(v0, v); v1 = Math.max(v1, v); }
          if (!out.has(id)) out.set(id, {}); out.get(id)![o] = { top: -u0, dep: u1, hw: Math.max(-v0, v1) }; } }
    return out; }

  // ── 전체 보기: 모든 깊이의 주제를 한꺼번에 펴고 노드를 하나도 빼지 않는다(배치는 view-compass-whole.ts, 상태는 LS 의 whole) ─────────
  // drawWhole 은 drawLayers 의 짝이다: redraw 가 상태를 갈아 끼운 뒤 둘 가운데 하나를 부른다. 그림은 세 겹이다. 늘 그리는 뼈대(주제 줄기 ·
  // 목록 줄기), 보이는 곳만 다시 짓는 몸(노드 · 빛살 · 이름표, 화면의 1.5배 둘레), 가리킴과 고름(관계선 · 고정 표). 손 맞추기는 DOM 이
  // 아니라 격자 색인으로 한다(먼 단계에는 노드 요소가 없다). 배치 · 격자 · 색인은 데이터와 상태에서 다시 셀 수 있는 값이라 상태에 넣지 않고
  // 서명(WLsig · plan.key)과 함께 든다. 타이머 · 프레임 · 유휴 일은 모두 fx 의 이름(wcommit · wresize · whover · wcross · wplan)으로 건다
  type Hit = { id: string; x: number; y: number; r: number };
  type WIx = ReturnType<typeof gridIndex<Hit>>;
  let WL: (WLayout & { ix: WIx }) | null = null, WLsig = '', HUBIX: { L: WLayout; ix: WIx } | null = null;
  let PLANF: { key: string; F: (FarArr & { ix: WIx }) | null } | null = null, PLANTRIED: { s: number; ok: boolean; nearer: number; knn: number }[] = [];
  // 지난 몸의 기억(렌즈 상태가 아니다): 그린 단계 · 배율 · 둘레, 이름 · 표식 상자(가리킨 이름이 피한다), 노드 자리(단계가 바뀔 때 옮겨 간다)
  let wMode = '', wTier = '', wScale = 0, wRegion: number[] | null = null, wFilt = '', wNameBoxes: WPt[][] = [], wMarkBoxes: WPt[][] = [], wLast = new Map<string, WPt>(), wLastDots = false;
  let wVC: Box | null = null, wholeDom = false, panEl: HTMLElement | null = null, live: HTMLElement | null = null, note: HTMLElement | null = null;
  const WPERF = { layout: 0, draw: 0 };
  // 글자 폭: 상태 모듈의 어림(LS.textWidth, 글꼴과 상관없다)을 fontKey 와 함께 기억한다. 그래서 웹 글꼴이 들어오기 전후의 배치가 같다
  const TW = new Map<string, number>();
  const tw = (t: string, k: LS.GlyphKind) => { const key = `${st.fontKey}|${k}|${t}`; let w = TW.get(key); if (w == null) { w = LS.textWidth(st.fontKey, t, k); if (TW.size > 20000) TW.clear(); TW.set(key, w); } return w; };
  /** 글자 폭의 서명: 이 값이 바뀔 때만 글꼴 때문에 다시 그린다(restyle) */
  function metric(s: LensState) { return `${LS.glyphWidth(s.fontKey, 'fan')}|${LS.glyphWidth(s.fontKey, 'bridge')}|${(['leaf', 'hub', 'head', 'name', 'count', 'hover'] as const).map((k) => LS.textWidth(s.fontKey, 'Ag\uac001', k)).join(',')}`; }
  const wNewest = (a: LNode, b: LNode) => (when(b) || '').localeCompare(when(a) || '') || titleOf(a).localeCompare(titleOf(b));
  /** 배치 입력: 층 보기와 같은 갈래(isMean = 9시 부채, 다리 = 3시, 나머지 = 6시 목록). 범례에서 끈 종류는 뺀다 */
  function wholeModel(s: LensState): WTopicIn {
    const koff = new Set(s.koff), node = (n: LNode) => ({ id: n.id, k: n.k, label: titleOf(n), prio: (n.m || 0) * 1000 + (n.g || 0) });
    const mk = (t: TNode): WTopicIn => { const own = t.direct.filter((n) => !koff.has(kindKey(n))), L: Record<string, DNode[]> = { decision: [], record: [], episode: [] };
      for (const n of own) if (!isMean(n)) (L[n.k] || L.record).push(n);
      return { id: t.id, label: titleOf(t), count: t.count, r: t.path === ROOT ? 22 : subR(t), mean: own.filter((n) => isMean(n) && n.k !== 'bridge').map(node),
        bridges: own.filter((n) => n.k === 'bridge').map(node), lists: Object.entries(L).filter(([, l]) => l.length).map(([k, l]) => ({ k, name: KIND[k], items: l.sort(wNewest).map(node) })),
        subs: [...t.sub].sort((a, b) => topics.get(b)!.count - topics.get(a)!.count || (a < b ? -1 : 1)).map((p) => mk(topics.get(p)!)) }; };
    return mk(topics.get(ROOT)!); }
  function ensureWL(s: LensState): WLayout & { ix: WIx } {
    const sig = `${[...s.koff].sort().join(',')}|${metric(s)}`; if (WL && WLsig === sig) return WL; const t0 = performance.now();
    const L = layoutWhole(wholeModel(s), (t, f) => tw(t, f)); WL = { ...L, ix: gridIndex<Hit>(L.items) }; WLsig = sig; HUBIX = null; WPERF.layout = Math.round(performance.now() - t0); return WL; }
  const hubIx = (): WIx => (HUBIX && HUBIX.L === WL ? HUBIX.ix : (HUBIX = { L: WL!, ix: gridIndex<Hit>(WL!.items.filter((i) => i.role === 'hub')) }).ix);
  const nameWpx = (id: string) => tw(WL!.byId.get(id)!.label, 'name') + 2;   // 화면 12px 굵은 이름의 폭(px)
  const withIx = (F: FarArr): FarArr & { ix: WIx } => ({ ...F, ix: gridIndex<Hit>([...WL!.items.filter((i) => i.role === 'hub'), ...[...F.pos].map(([id, [x, y]]) => ({ id, x, y, r: 0 }))]) });
  const planKey = (s: LensState) => { const A = LS.areaOf(s.size); return `${WLsig}|${Math.round(A.w)}x${Math.round(A.h)}`; };
  /** 그리기 전에: 칸 크기를 잰 값으로 맞추고(재기 전의 첫 그림 · 숨었다 나온 칸), 배치를 짓고(그 상자를 상태에 넣는다), 이 칸의 계획을 세운다 */
  function prepareWhole() {
    const r = stageEl.getBoundingClientRect();
    if (r.width > 0 && r.height > 0 && (Math.abs(r.width - st.size.w) > .5 || Math.abs(r.height - st.size.h) > .5)) set(LS.resize(st, { w: r.width, h: r.height }));
    set(LS.wholeLaidOut(st, ensureWL(st).bounds)); ensurePlan(); }
  /** 먼 단계의 계획: 칸 크기(와 배치)마다 한 번. 맞춤 배율의 격자가 귀속 시험(fitChoice, 앞의 선택에 따라 문턱이 다르다)을 지나면 맞춤부터
   *  점을 그리고, 아니면 맞춤 그림은 허브만 그린다. 점을 그릴 배율은 첫 그림 다음 한가할 때(fx 'wplan') 찾는다. 전체 보기를 떠나면 그 일은 지워진다 */
  function ensurePlan(): LS.WholePlan {
    const key = planKey(st), have = st.whole.plan;
    if (have && have.key === key && PLANF && PLANF.key === key) return have;
    fx.cancelIdle('wplan'); const L = WL!, sFit = LS.wholeFitScale(st); let plan: LS.WholePlan;
    if (sFit >= RING_SEP) { plan = { key, sFit, far: false, fit: st.whole.last || 'dots', sDots: null, pending: false }; PLANF = { key, F: null }; PLANTRIED = []; }
    else {
      const F0 = farArrange(L, sFit, nameWpx, { hubPx: 2.8 }), a0 = attribution(L, F0), fit = fitChoice(st.whole.last, a0);
      PLANTRIED = [{ s: sFit, ok: fit === 'dots', nearer: a0.nearerOther / a0.n, knn: a0.knnForeign }];
      if (fit === 'dots') { plan = { key, sFit, far: true, fit, sDots: sFit, pending: false }; PLANF = { key, F: withIx(F0) }; }
      else { plan = { key, sFit, far: true, fit, sDots: null, pending: true }; PLANF = { key, F: null };
        fx.idle('wplan', () => { if (!st.whole.on || !st.whole.plan || st.whole.plan.key !== key || !root.isConnected) return;
          const P = farPlan(L, sFit * 1.35, nameWpx, { hubPx: 2.8 });
          PLANTRIED.push(...P.tried.map((q) => ({ s: q.s, ok: q.ok, nearer: q.a.nearerOther / q.a.n, knn: q.a.knnForeign })));
          PLANF = { key, F: P.F && withIx(P.F) }; set(LS.wholePlanned(st, { ...st.whole.plan, sDots: P.sDots, pending: false })); wholeBody(st); }); }
    }
    set(LS.wholePlanned(st, plan)); return plan; }
  /** 그 배율에서 그리는 것: hubs(허브만) · dots(격자 점) · mid(고리 모양) · near(이름표까지) */
  const modeAt = (s: LensState, sc: number) => { if (sc >= RING_SEP) return lodTier(sc); const P = s.whole.plan; return P && PLANF && PLANF.F && P.sDots != null && sc >= P.sDots * .999 ? 'dots' : 'hubs'; };
  /** 지금 그림에서 그 노드가 그려진 자리(격자 점 단계는 격자 자리, 그 밖은 고리 자리)와 그려진 반지름(그림 단위) */
  const posOf = (id: string): (Hit & { topic: string; role: string; label: string }) | null => { const it = WL && WL.byId.get(id); if (!it) return null; const F = PLANF && PLANF.F;
    if (wMode === 'dots' && F && it.role !== 'hub') { const q = F.pos.get(id); if (q) return { x: q[0], y: q[1], r: F.dotPx / 2 / wScale, topic: it.topic, role: it.role, label: it.label, id }; }
    if ((wMode === 'dots' || wMode === 'hubs') && it.role === 'hub') return { ...it, r: (wMode === 'hubs' ? 4 : 2.8) / wScale };
    return it; };
  const tcol = (topicId: string) => topicColour(String(topicId).slice(6));
  const fillOf = (n: LNode, c: string) => (n.vu || fresh(n) ? c : mix(c, 55));
  const pp = (p: WPt) => `${p[0].toFixed(1)} ${p[1].toFixed(1)}`;
  const polyD = (segs: WPt[][]) => segs.map((q) => `M${q.map(pp).join('L')}`).join('');
  /** 다리 표식(전체 보기): 잇는 주제마다 고리 한 조각을 그 주제 색으로. 조각 수 = 몇 주제를 잇는가 */
  function wJunction(it: WItem, n: LNode) { const ds = [...new Set(bridgeEnds(n).map((m) => m.d).filter(Boolean))], R = it.r, k = ds.length || 1, half = Math.min(Math.PI / k - .32, 1.1);
    if (!ds.length) return shape('concept', 0, 0, R, colv(n), true);
    return ds.map((d, i) => { const a = -Math.PI / 2 + i * 2 * Math.PI / k, P = (q: number) => `${(Math.cos(q) * R).toFixed(2)} ${(Math.sin(q) * R).toFixed(2)}`;
      return `<path class="shape" d="M${P(a - half)}A${R} ${R} 0 0 1 ${P(a + half)}" fill="none" style="stroke:${topicColour(d)}" stroke-width="2.4" stroke-linecap="round"/>`; }).join('')
      + `<circle class="shape" r="${(R * .32).toFixed(2)}" style="fill:${mix(colv(n), 80)}"/>`; }
  /** 뼈대: 주제 나무의 줄기(부모 색 버스 + 자식 색 마지막 토막). 다리에서 끝 노드로 가는 선은 가리키거나 고를 때만 긋는다
   *  (늘 그으면 뿌리의 다리마다 그래프를 가로지르는 긴 선이 서서 털뭉치가 된다) */
  function wholeStatic(L: WLayout) { let s = '';
    for (const t of L.topics) { const c = tcol(t.id);
      if (t.trunk.length || t.drops.length) s += `<path class="wtrunk" d="${polyD(t.trunk)}${t.drops.map((q) => `M${pp(q.pts[0])}L${pp(q.pts[1])}`).join('')}" style="stroke:${mix(c, 75)}"/>`;
      for (const q of t.drops) s += `<path class="wtrunk" d="M${pp(q.pts[1])}L${pp(q.pts[2])}" style="stroke:${mix(tcol(q.to), 85)}"/>`; }
    return s; }
  /** 목록 줄기(6시): 고리 배치의 목록 열에 맞춘 것이라 먼 단계(격자 배치)에서는 감춘다(.far) */
  const wholeStems = (L: WLayout) => L.topics.filter((t) => t.stems.length).map((t) => `<path class="wstem" d="${polyD(t.stems)}" style="stroke:${mix(tcol(t.id), 70)}"/>`).join('');
  /** 관계를 크게 보기: 주제가 다른 두 노드 사이의 뜻의 관계를 두 주제 쌍으로 끌어올려 쌍마다 곡선 하나를 긋고, 굵기는 수의 로그다(화면 px 고정).
   *  멀리서는 영역(뿌리 아래 첫 칸) 쌍으로, 가까이서는 지금 보이는 주제가 낀 쌍 가운데 많은 것 BUNDLE_TOP 개만. 붙은 것 · 다리 선은 뺀다 */
  const BUNDLE_TOP: Record<string, number> = { far: 24, mid: 40, near: 40 };
  function wholeBundles(L: WLayout, tier: string, R: number[], sc: number) {
    const areaOf = (tid: string) => { const p = String(tid).slice(6), seg = p ? p.split('/') : []; return `topic:${seg.slice(0, RD + 1).join('/')}`; };
    const pairs = new Map<string, number>();
    for (const e of edges) { if (ATTACH.has(e.t) || e.t === 'CONNECTS') continue; const A = L.byId.get(e.a.id), B = L.byId.get(e.b.id); if (!A || !B) continue;
      let ta = A.topic, tb = B.topic; if (tier === 'far') { ta = areaOf(ta); tb = areaOf(tb); } if (ta === tb || !L.byId.has(ta) || !L.byId.has(tb)) continue;
      const k = ta < tb ? `${ta}\u0000${tb}` : `${tb}\u0000${ta}`; pairs.set(k, (pairs.get(k) || 0) + 1); }
    const inR = (h: WItem) => h.x >= R[0] && h.x <= R[2] && h.y >= R[1] && h.y <= R[3];
    let list = [...pairs].map(([k, n]) => { const [a, b] = k.split('\u0000'); return { a: L.byId.get(a)!, b: L.byId.get(b)!, n }; });
    if (tier !== 'far') list = list.filter((q) => inR(q.a) || inR(q.b));
    list = list.sort((x, y) => y.n - x.n).slice(0, BUNDLE_TOP[tier]);
    return list.map(({ a, b, n }) => { const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2, dx = b.x - a.x, dy = b.y - a.y, k = .18;   // 곧은 줄기와 겹치지 않게 옆으로 휜다
      return `<path class="wbun" d="M${pp([a.x, a.y])}Q${pp([mx - dy * k, my + dx * k])} ${pp([b.x, b.y])}" style="stroke:${mix(tcol(a.id), 70)};stroke-width:${((1 + Math.log2(n)) / sc).toFixed(2)}"><title>${esc(`${titleOf(byAny(a.id)!)} · ${titleOf(byAny(b.id)!)} · ${n}`)}</title></path>`; }).join(''); }
  /** 전체 보기에 처음 들 때만 짓는 요소: 손짓 동안 그림을 통째로 옮기는 겹(cmp-pan, svg 자신을 변환하면 벡터를 다시 굽는다), 읽기 칸의 알림, 허브만 그릴 때의 말.
   *  층 보기만 쓰는 렌즈의 DOM 은 그대로다 */
  function enterWholeDom() { if (wholeDom) return; wholeDom = true;
    if (!panEl && typeof (svg as Element & { before?: unknown }).before === 'function') { panEl = document.createElement('div'); panEl.className = 'cmp-pan'; svg.before(panEl); panEl.append(svg); }
    if (!live) { live = document.createElement('div'); live.className = 'cmp-live'; live.setAttribute('aria-live', 'polite'); root.append(live); }
    if (!note) { note = document.createElement('div'); note.className = 'cmp-note'; note.setAttribute('role', 'status'); note.hidden = true; root.append(note); } }
  /** 층 보기로 돌아왔다: 전체 보기의 일(유휴 계획 · 손짓 마무리 · 크기 뒤 다시 짓기 · 가리킴 · 옮김)을 지우고 svg 를 층 보기의 모습으로 */
  function quitWhole() { if (!wholeDom) return; wholeDom = false;
    fx.cancelIdle('wplan'); fx.cancelAfter('wcommit'); fx.cancelAfter('wresize'); fx.cancelFrame('whover'); fx.cancelFrame('wcross'); fx.cancelAfter('wcross'); fx.cancelAfter('peek');
    if (panEl) panEl.style.transform = ''; wVC = null; wMode = ''; wTier = ''; wRegion = null; wFilt = ''; wLast = new Map(); prevPos = new Map();
    svg.classList.remove('whole', 'far'); svg.removeAttribute('tabindex'); svg.setAttribute('aria-label', C.lens); svg.style.removeProperty?.('--wk'); svg.style.cursor = '';
    tip.style.display = 'none'; if (live) live.textContent = ''; if (note) note.hidden = true;
    tools.querySelector('[data-whole]')?.setAttribute('aria-pressed', 'false'); }
  function syncWholeUi(s: LensState) { tools.querySelector('[data-whole]')?.setAttribute('aria-pressed', String(s.whole.on));
    if (WL) { svg.setAttribute('tabindex', '0'); svg.setAttribute('aria-label', C.wholeLabel(WL.items.filter((q) => q.role !== 'hub').length, WL.topics.length)); } }
  /** 카메라를 SVG 에 놓는다(손짓 겹의 변환은 지운다): viewBox 는 늘 지금 칸의 비율이라 화면 자리와 그림 자리가 맞는다 */
  function showWholeCamera(s: LensState) { const v = LS.wholeView(s); if (!v) return; fx.cancelAfter('wcommit'); wVC = { ...v }; if (panEl) panEl.style.transform = ''; svg.setAttribute('viewBox', LS.viewBoxAttr(v)); }
  function drawWhole(s: LensState) {
    fx.cancelFrame('fly'); fx.cancelFrame('flip'); fx.cancelAfter('flip'); fx.cancelFrame('grow');   // 층 보기의 카메라 · 옮김 · 줄기 움직임이 남아 덮지 않게
    enterWholeDom(); const L = WL!;
    svg.classList.add('whole'); svg.classList.remove('lod1', 'lod2', 'moving', 'hot');
    svg.innerHTML = `<defs></defs><g id="cmp-wstat">${wholeStatic(L)}</g><g id="cmp-wstem">${wholeStems(L)}</g><g id="cmp-wbun"></g><g id="cmp-wsp"></g><g id="cmp-nodes"></g><g id="cmp-wlab"></g><g id="cmp-whl"></g>`;
    syncWholeUi(s); showWholeCamera(s); wLast = new Map(); wholeBody(s); }
  /** 몸: 화면 둘레(1.5배)의 노드 · 빛살 · 이름표만 짓는다. 그리는 것(modeAt): hubs = 허브 · 줄기 · 이름과 수만(점 없음), dots = 격자 점,
   *  mid = 고리 자리의 모양과 빛살, near = 이름표까지. 화면 크기 이름은 노드 표식 · 줄기 · 다른 이름과 닿지 않고 그래프 칸 안에 다 드는 자리에만(큰 주제부터) */
  function wholeBody(s: LensState) {
    const V = LS.wholeView(s); if (!s.whole.on || !WL || !V) return; const L = WL, t0 = performance.now();
    const sc = LS.wholeScale(s), tier = lodTier(sc), mode = modeAt(s, sc), far = tier === 'far', dots = mode === 'dots', hubsOnly = mode === 'hubs', m = .5, F = dots ? PLANF!.F! : null;
    // 먼 단계는 모든 노드를 그린다(맞춤 화면이 곧 전체다): 끌어도 둘레를 벗어나지 않아 다시 짓지 않는다
    const R = far ? [-Infinity, -Infinity, Infinity, Infinity] : [V.x - V.w * m, V.y - V.h * m, V.x + V.w * (1 + m), V.y + V.h * (1 + m)];
    const crossing = dots !== wLastDots && !hubsOnly && wLast.size > 0 && !matchMedia('(prefers-reduced-motion: reduce)').matches;
    wRegion = R; wTier = tier; wMode = mode; wScale = sc; wFilt = `${s.tsel}|${s.query}`;
    svg.style.setProperty?.('--wk', (1 / sc).toFixed(4));   // 선 굵기 = 화면 px × --wk(그림 단위). non-scaling-stroke 는 쓰지 않는다: 프레임마다 다시 재서 확대 · 끌기가 무겁다
    svg.classList.toggle('far', far);
    const dim = (n: LNode) => { const w = when(n); return (!!s.tsel && !!w && (w < s.tsel[0] || w > s.tsel[1])) || (!!s.query && !matchQ(n, s.query)); };
    const inR = (x: number, y: number) => x >= R[0] && x <= R[2] && y >= R[1] && y <= R[3];
    let sp = '', nd = '', lb = ''; const spokes = new Map<string, string>(), drawn = new Map<string, WPt>(), marks: WPt[][] = [];
    const sq = (x: number, y: number, r: number): WPt[] => [[x - r, y - r], [x + r, y - r], [x + r, y + r], [x - r, y + r]];
    if (!hubsOnly) for (const it of L.items) { if (it.role === 'hub') continue; const q: WPt | undefined = dots ? F!.pos.get(it.id) : [it.x, it.y]; if (!q || !inR(q[0], q[1])) continue;
      const n = byAny(it.id); if (!n) continue; const c = tcol(it.topic), out = dim(n); drawn.set(it.id, q);
      if (dots) { // 격자 점: 화면 지름 3px(반지름은 CSS 가 --wk 로 맞춘다: 확대해도 다시 짓지 않는다). 격자 간격이 지름 + 틈이라 점끼리 붙지 않는다
        marks.push(sq(q[0], q[1], (F!.dotPx / 2 + 1) / sc));
        nd += `<g class="node wf${out ? ' out' : ''}" data-id="${esc(it.id)}" data-role="${it.role}" transform="translate(${q[0].toFixed(1)} ${q[1].toFixed(1)})"><circle class="shape" style="fill:${mix(c, 88)}"/></g>`; continue; }
      if (it.role === 'mean' || it.role === 'bridge') { const h = L.byId.get(it.topic)!; spokes.set(it.topic, (spokes.get(it.topic) || '') + `M${pp([h.x, h.y])}L${pp([it.x, it.y])}`); }
      marks.push(sq(it.x, it.y, it.r * 1.3));
      let lab = '';
      if (tier === 'near') { if (it.role === 'list') lab = `<text x="${it.r + 6}" y="4">${esc(it.label)}</text>`;
        else { const a = it.ang ?? 0, fl = Math.cos(a) < 0, ox = Math.cos(a) * (it.r + 6), oy = Math.sin(a) * (it.r + 6), dg = a * 180 / Math.PI + (fl ? 180 : 0);
          lab = `<text x="${ox.toFixed(1)}" y="${oy.toFixed(1)}" dy="3.8" text-anchor="${fl ? 'end' : 'start'}" transform="rotate(${dg.toFixed(1)} ${ox.toFixed(1)} ${oy.toFixed(1)})">${esc(it.label)}</text>`; } }
      nd += `<g class="node${out ? ' out' : ''}" data-id="${esc(it.id)}" data-role="${it.role}" transform="translate(${it.x.toFixed(1)} ${it.y.toFixed(1)})">${it.k === 'bridge' ? wJunction(it, n) : shape(it.k, 0, 0, it.r, fillOf(n, c), !!n.vu)}${lab}</g>`; }
    if (!far) for (const [t, d] of spokes) sp += `<path class="wsp" d="${d}" style="stroke:${mix(tcol(t), 55)}"/>`;
    if (tier === 'near') for (const h of L.heads) if (inR(h.x, h.y)) lb += `<text class="whead" x="${h.x.toFixed(1)}" y="${h.y.toFixed(1)}">${esc(h.text)}</text>`;
    // 허브: 늘 요소로 그린다. 먼 단계는 화면 크기의 고리(허브만 그릴 때는 조금 크게)
    const hubs = L.items.filter((it) => it.role === 'hub' && it.x >= R[0] - 400 && it.x <= R[2] + 400 && it.y >= R[1] - 400 && it.y <= R[3] + 400), natural = labelShown(FONT.hub, sc);
    // 먼 단계의 허브 고리: 가장 가까운 다른 허브와 2px 넘게 떨어지도록 반지름을 줄인다(작은 주제의 허브가 몇 px 안에 붙어 설 수 있다)
    const hubPx = new Map<string, number>(); if (far) { const all = L.items.filter((i) => i.role === 'hub'), Cc = 40 / sc, G = new Map<string, WItem[]>();
      all.forEach((h) => { const k = Math.floor(h.x / Cc) + ',' + Math.floor(h.y / Cc); if (!G.has(k)) G.set(k, []); G.get(k)!.push(h); });
      for (const h of hubs) { let d = Infinity; const gx = Math.floor(h.x / Cc), gy = Math.floor(h.y / Cc);
        for (let i = gx - 1; i <= gx + 1; i++) for (let j = gy - 1; j <= gy + 1; j++) for (const o of G.get(i + ',' + j) || []) if (o !== h) d = Math.min(d, Math.hypot(o.x - h.x, o.y - h.y) * sc);
        const big = hubsOnly ? 4 : 2.75; hubPx.set(h.id, Math.max(1, Math.min(big, (d - 2) / 2))); } }
    const hw = (it: WItem) => (far ? Math.min(hubsOnly ? 1.2 : .9, hubPx.get(it.id)! * .5) / sc : Math.max(2.2, 2 / sc)), hr = (it: WItem) => (far ? hubPx.get(it.id)! / sc - hw(it) / 2 : Math.max(it.r, 2.5 / sc));
    for (const it of hubs) marks.push(sq(it.x, it.y, hr(it) + hw(it) / 2 + .5 / sc));
    const f = 12 / sc, gap = 4 / sc, cnt = (it: WItem) => (hubsOnly ? String(byAny(it.id)?.count ?? '') : ''), nameW = (it: WItem) => (tw(it.label, 'name') + 2 + (hubsOnly ? 6 + tw(cnt(it), 'count') : 0)) / sc;
    const names = new Map<string, [number, number, string]>();   // id → [x, y, 기준]
    const nameBoxes: WPt[][] = [], box = (x0: number, y0: number, w: number): WPt[] => [[x0, y0], [x0 + w, y0], [x0 + w, y0 + f * 1.25], [x0, y0 + f * 1.25]];
    // 그래프 칸 안에 다 드는가(맞춤 그림에서 이름이 가장자리에 잘리지 않게)
    const inView = (P: WPt[]) => P.every(([x, y]) => x >= V.x + 2 / sc && x <= V.x + V.w - 2 / sc && y >= V.y + 2 / sc && y <= V.y + V.h - 2 / sc);
    if (natural) for (const it of hubs) nameBoxes.push(box(it.x + it.r + 8, it.y - 21, it.lw));
    else {
      const blocks: WPt[][] = [...marks], th = 1.5 / sc;
      const segPoly = (a: WPt, c: WPt): WPt[] => [[Math.min(a[0], c[0]) - th, Math.min(a[1], c[1]) - th], [Math.max(a[0], c[0]) + th, Math.min(a[1], c[1]) - th], [Math.max(a[0], c[0]) + th, Math.max(a[1], c[1]) + th], [Math.min(a[0], c[0]) - th, Math.max(a[1], c[1]) + th]];
      for (const t of L.topics) for (const sg of [...t.trunk, ...t.drops.map((d) => d.pts), ...(far ? [] : t.stems)]) for (let k = 1; k < sg.length; k++) if (inR(sg[k][0], sg[k][1]) || inR(sg[k - 1][0], sg[k - 1][1])) blocks.push(segPoly(sg[k - 1], sg[k]));
      // 빛살(허브 → 노드)은 비스듬하므로 짧은 토막 여럿의 상자로 막는다. 이름 후보는 허브에서 화면 200px 안이라 그 안의 토막만
      if (!far) for (const t of L.topics) { const h = t.hub; if (!inR(h.x, h.y)) continue; for (const id of t.spokes) { const it = L.byId.get(id); if (!it) continue;
        const len = Math.hypot(it.x - h.x, it.y - h.y), cut = Math.min(1, 200 / sc / (len || 1)), n = Math.max(1, Math.ceil(len * cut * sc / 12));
        for (let k = 0; k < n; k++) blocks.push(segPoly([h.x + (it.x - h.x) * cut * k / n, h.y + (it.y - h.y) * cut * k / n], [h.x + (it.x - h.x) * cut * (k + 1) / n, h.y + (it.y - h.y) * cut * (k + 1) / n])); } }
      // 점 단계: 맞춤 배율에서 격자가 비워 둔 이름 자리를 쓴다(그래프 칸 안에 다 들 때만). 허브만 · 고리 단계: 표식 · 줄기 · 이름을 피해 놓는다
      if (dots) for (const it of hubs) { const b = F!.names.get(it.id); if (!b) continue; const P4: WPt[] = [[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]];
        if (inView(P4)) { names.set(it.id, [b[0] - it.x, (b[1] + b[3]) / 2 - it.y, 'start']); nameBoxes.push(P4); } }
      const rest = dots ? [] : hubs;
      const NEVER: WPt[] = [[Infinity, Infinity], [Infinity, Infinity], [Infinity, Infinity], [Infinity, Infinity]];
      const cands = rest.map((it) => { const w = nameW(it), r = hr(it) + hw(it) / 2;
        const at = [box(it.x + r + gap, it.y - f * 1.25 - gap, w), box(it.x + r + gap, it.y + gap, w), box(it.x - r - gap - w, it.y - f * .62, w), box(it.x - w / 2, it.y + r + gap, w), box(it.x - w / 2, it.y - r - gap - f * 1.25, w), box(it.x - w / 2, it.y - 45 / sc - f * 1.25, w)];   // 마지막: 다리 고리 너머 위(빛살이 끝난 자리)
        return { id: it.id, prio: byAny(it.id)?.count || 0, at: at.map((q) => (inView(q) ? q : NEVER)) }; });
      const pick = placeLabels(cands, blocks);
      for (const c of cands) { const it = L.byId.get(c.id)!, i = pick.get(c.id)!, r = hr(it) + hw(it) / 2; if (i < 0) continue; nameBoxes.push(c.at[i]);
        names.set(c.id, ([[r + gap, -gap - f * .3, 'start'], [r + gap, gap + f * .95, 'start'], [-r - gap, f * .35, 'end'], [0, r + gap + f * .95, 'middle'], [0, -r - gap - f * .3, 'middle'], [0, -45 / sc - f * .3, 'middle']] as [number, number, string][])[i]); } }
    for (const it of hubs) { const t = byAny(it.id), c = tcol(it.id); let lab = '';
      if (natural) lab = `<text class="whub" x="${it.r + 8}" y="-6">${esc(it.label)}<tspan class="badge" dx="6">${t?.count ?? ''}</tspan></text>`;
      else if (dots && names.has(it.id)) { const [x, y] = names.get(it.id)!; lab = `<text class="whub wfname" x="${x.toFixed(1)}" y="${y.toFixed(1)}">${esc(it.label)}</text>`; }   // 크기 · 기준선은 CSS(.wfname)
      else if (names.has(it.id)) { const [x, y, a] = names.get(it.id)!;
        lab = `<text class="whub" x="${x.toFixed(1)}" y="${y.toFixed(1)}" text-anchor="${a}" style="font-size:${f.toFixed(2)}px;stroke-width:${(3 / sc).toFixed(2)}px">${esc(it.label)}${hubsOnly ? `<tspan class="badge" dx="${(6 / sc).toFixed(2)}" style="font-size:${(11 / sc).toFixed(2)}px">${esc(cnt(it))}</tspan>` : ''}</text>`; }   // fs-allow: 화면 12px 이름 · 11px 수(배율로 나눈 그림 단위)
      nd += `<g class="node" data-id="${esc(it.id)}" data-role="hub" transform="translate(${it.x.toFixed(1)} ${it.y.toFixed(1)})">${far ? `<circle class="wfhub" style="stroke:${c};r:calc(${(hubPx.get(it.id)! - hw(it) * sc / 2).toFixed(2)}px * var(--wk, 1));stroke-width:calc(${(hw(it) * sc).toFixed(2)}px * var(--wk, 1))"/>` : ringS(hr(it), c, hw(it))}${lab}</g>`; }
    wNameBoxes = nameBoxes; wMarkBoxes = marks;
    const put = (k: string, h: string) => { const g = $id(k); if (g) g.innerHTML = h; };
    put('wsp', sp); put('wbun', s.whole.rels ? wholeBundles(L, tier, R, sc) : ''); put('wlab', lb); const ng = svg.querySelector('#cmp-nodes'); if (ng) ng.innerHTML = nd;
    // 격자 점 ↔ 고리 단계가 바뀌면 노드가 지난 자리에서 새 자리로 옮겨 간다(같은 노드 · 같은 색, 뛰지 않는다)
    if (crossing) { const els = [...svg.querySelectorAll<SVGGElement>('#cmp-nodes .node[data-id]')].filter((g) => g.dataset.role !== 'hub' && wLast.has(g.dataset.id!));
      for (const g of els) { const [x, y] = wLast.get(g.dataset.id!)!; g.style.transition = 'none'; g.style.transform = `translate(${x}px, ${y}px)`; }
      svg.getBoundingClientRect();
      fx.cancelFrame('wcross'); fx.cancelAfter('wcross');
      fx.frame('wcross', () => { for (const g of els) { const q = drawn.get(g.dataset.id!); if (!q) continue; g.style.transition = ''; g.classList.add('wmove'); g.style.transform = `translate(${q[0]}px, ${q[1]}px)`; }
        fx.after('wcross', 170, () => { for (const g of els) { g.classList.remove('wmove'); g.style.transform = ''; } }); }); }
    wLast = drawn; wLastDots = dots;
    // 허브만 그리는 맞춤 그림이면 그렇다고 말한다(확대하면 노드가 보인다)
    if (note) { note.hidden = !hubsOnly; note.textContent = hubsOnly ? C.wholeHubsOnly : ''; }
    wholeOverlay(s); WPERF.draw = Math.round(performance.now() - t0); }
  /** 가리킴과 고름: 그 노드의 관계를 그래프 어디에 있든 끝까지 잇고, 끝 노드에 고리와 이름(작게 보일 때는 화면 크기로)을 단다 */
  function wholeOverlay(s: LensState) {
    const g = $id('whl'), V = LS.wholeView(s); if (!g || !WL || !V || !s.whole.on) return; const sc = LS.wholeScale(s), near = lodTier(sc) === 'near'; let h = ''; const names: (Hit & { label: string })[] = [];
    for (const id of new Set([s.whole.sel, s.whole.hov].filter(Boolean))) { const it = posOf(id); if (!it) continue;
      if (it.role !== 'hub') for (const e of adj.get(id) || []) { const o = e.a.id === id ? e.b : e.a, q = posOf(o.id); if (!q) continue;
        const cross = q.topic !== it.topic, c = e.t === 'CONNECTS' ? tcol(q.topic) : cross ? '' : tcol(it.topic);
        h += `<line class="wl${e.t === 'CONNECTS' ? ' br' : cross ? ' cross' : ''}" x1="${it.x.toFixed(1)}" y1="${it.y.toFixed(1)}" x2="${q.x.toFixed(1)}" y2="${q.y.toFixed(1)}"${c ? ` style="stroke:${mix(c, 90)}"` : ''}/>`
          + `<circle class="wpart" cx="${q.x.toFixed(1)}" cy="${q.y.toFixed(1)}" r="${Math.max(q.r + 3, 4 / sc).toFixed(2)}"/>`;
        if (!near) names.push(q); }
      h += `<circle class="wsel${id === s.whole.sel ? ' on' : ''}" cx="${it.x.toFixed(1)}" cy="${it.y.toFixed(1)}" r="${Math.max(it.r + 4.5, 6 / sc).toFixed(2)}"/>`;
      if (!near && it.role !== 'hub') names.unshift(it); }
    // 끝 노드 이름: 화면 11px, 주제 이름 · 노드 표식 · 서로와 닿지 않고 그래프 칸 안에 다 드는 자리(오른쪽 · 왼쪽 · 위 · 아래)에만.
    // 자리가 없으면 그리지 않는다(풍선과 읽기 칸이 답한다). 가리킨 노드 → 많이 이어진 것부터
    if (names.length) { const f = 11 / sc, gap = 4 / sc, inV = (P: WPt[]) => P.every(([x, y]) => x >= V.x && x <= V.x + V.w && y >= V.y && y <= V.y + V.h);
      const NEVER: WPt[] = [[Infinity, Infinity], [Infinity, Infinity], [Infinity, Infinity], [Infinity, Infinity]], B = (x0: number, y0: number, w: number): WPt[] => [[x0, y0], [x0 + w, y0], [x0 + w, y0 + f * 1.2], [x0, y0 + f * 1.2]];
      const cands = names.map((q, i) => { const w = tw(q.label, 'hover') / sc, r = Math.max(q.r, 3 / sc) + gap;
        const at = [B(q.x + r, q.y - f * .6, w), B(q.x - r - w, q.y - f * .6, w), B(q.x - w / 2, q.y - r - f * 1.2, w), B(q.x - w / 2, q.y + r, w)];
        return { id: q.id, prio: i === 0 ? 1e9 : byAny(q.id)?.g || 0, at: at.map((P) => (inV(P) ? P : NEVER)) }; });
      const pick = placeLabels(cands, [...wNameBoxes, ...wMarkBoxes]);
      for (const c of cands) { const i = pick.get(c.id)!; if (i < 0) continue; const P = c.at[i];
        h += `<text class="wname" x="${P[0][0].toFixed(1)}" y="${(P[0][1] + f * .9).toFixed(1)}" style="font-size:${f.toFixed(2)}px;stroke-width:${(3 / sc).toFixed(2)}px">${esc(WL.byId.get(c.id)?.label || '')}</text>`; } }   // fs-allow: 화면 11px 이름(배율로 나눈 그림 단위)
    // 찾기에 맞은 노드: 흐린 것들 사이에서 보이게 고리를 단다(많으면 300 개까지, 화면 둘레 안만)
    if (s.query && wRegion) { let k = 0; const R = wRegion; for (const it0 of WL.items) { if (k > 300) break; if (it0.role === 'hub') continue; const it = posOf(it0.id)!; if (it.x < R[0] || it.x > R[2] || it.y < R[1] || it.y > R[3]) continue; const n = byAny(it.id);
      if (n && matchQ(n, s.query)) { k++; h += `<circle class="wpart" cx="${it.x.toFixed(1)}" cy="${it.y.toFixed(1)}" r="${Math.max(it.r + 2.5, 3.5 / sc).toFixed(2)}"/>`; } } }
    for (const id of s.pins) { const it = posOf(id); if (!it) continue; const k = Math.max(1, 1.2 / sc);
      h += `<g class="pinmark self" transform="translate(${(it.x + it.r * .8 + 3 * k).toFixed(1)} ${(it.y - it.r * .8 - 3 * k).toFixed(1)}) scale(${k.toFixed(2)})"><circle r="6"/><svg x="-4.4" y="-4.4" width="8.8" height="8.8" viewBox="0 0 24 24">${PIN_PATHS}</svg></g>`; }
    g.innerHTML = h; }
  /** 카메라가 움직이는 동안(바퀴 · 끌기)은 svg 를 그려 둔 viewBox(wVC)에서 지금 카메라로 CSS 변환만 한다: 벡터를 다시 굽지 않는다.
   *  손이 멈추면(120ms, fx 'wcommit') viewBox 를 지금 카메라로 바꾸고 변환을 지운 뒤, 둘레를 벗어났거나 단계 · 배율이 바뀌었으면 몸을 다시 짓는다 */
  function wMove() { if (!st.whole.on) return; const v = LS.wholeView(st); if (!v) return; if (!wVC || !panEl) { wCommit(); return; }
    const W = stageEl.clientWidth || 1, H = stageEl.clientHeight || 1, k = wVC.w / v.w;
    panEl.style.transform = `translate(${((wVC.x - v.x) / v.w * W).toFixed(2)}px, ${((wVC.y - v.y) / v.h * H).toFixed(2)}px) scale(${k.toFixed(5)})`;
    fx.cancelAfter('wcommit'); fx.after('wcommit', 120, wCommit); }
  function wCommit() { fx.cancelAfter('wcommit'); if (!st.whole.on) return; const v = LS.wholeView(st); if (!v) return;
    showWholeCamera(st); const sc = LS.wholeScale(st), R = wRegion, outside = !R || v.x < R[0] || v.y < R[1] || v.x + v.w > R[2] || v.y + v.h > R[3], t = lodTier(sc), mode = modeAt(st, sc), sized = st.whole.plan?.key === planKey(st);
    svg.style.setProperty?.('--wk', (1 / sc).toFixed(4));
    // 격자 점 단계 안에서 확대 · 끌기는 다시 짓지 않는다(점 · 고리 · 이름 크기는 --wk 를 따른다). 그 밖은 크기 · 단계 · 그리는 것 · 배율이 바뀌면 다시 짓는다
    if (sized && mode === 'dots' && wMode === 'dots') { wScale = sc; wholeOverlay(st); return; }
    if (!sized) prepareWhole();
    if (!sized || outside || t !== wTier || mode !== wMode || Math.abs(Math.log(sc / wScale)) > (mode === 'hubs' ? .05 : .25)) wholeBody(st); else wholeOverlay(st); }
  /** 칸의 크기가 바뀌었다(창 · 사이드바 · 서랍): 카메라는 상태(LS.resize)가 이미 옮겼다. viewBox 를 바로 놓아 손 맞추기가 맞게 하고, 계획과 몸은 150ms 뒤에 */
  function wholeResized() { showWholeCamera(st); fx.cancelAfter('wresize'); fx.after('wresize', 150, () => { if (!st.whole.on) return; prepareWhole(); wholeBody(st); }); }
  function wHit(cx: number, cy: number): Hit | null { const v = LS.wholeView(st); if (!WL || !v) return null; const r = stageEl.getBoundingClientRect(); if (!r.width || !r.height) return null;
    const x = v.x + (cx - r.left) / r.width * v.w, y = v.y + (cy - r.top) / r.height * v.h;
    return (wMode === 'dots' && PLANF && PLANF.F ? PLANF.F.ix : wMode === 'hubs' ? hubIx() : WL.ix).near(x, y, 7 / LS.wholeScale(st)); }
  /** 가리킴: 프레임마다 한 번 격자 색인으로 찾는다. 풍선 · 엿보기 · 관계선이 그 노드를 따른다 */
  function wholePointer(e: PointerEvent) { const cx = e.clientX, cy = e.clientY; fx.cancelFrame('whover');
    fx.frame('whover', () => { if (!st.whole.on || st.pan) return; const it = wHit(cx, cy), id = it ? it.id : '';
      svg.style.cursor = it ? 'pointer' : ''; if (id === st.whole.hov) return; set(LS.wholeHover(LS.hover(st, id || null), id)); fx.cancelAfter('peek');
      if (it) { const n = byAny(id)!; tip.style.display = 'block'; tip.innerHTML = `${esc(n.t || n.path || C.rootTopic)}<small>${KIND[n.k] || n.k}${n.d ? ' · ' + esc(n.d) : ''}${n.ts ? ' · ' + n.ts : ''}</small>`;
        fx.after('peek', PEEK_MS, () => { if (st.whole.hov === id && tip.style.display === 'block') tip.insertAdjacentHTML('beforeend', peekHTML(n)); }); } else tip.style.display = 'none';
      wholeOverlay(st); }); }
  function wholeSelChanged() { wholeOverlay(st); drawSum(st); ctx.onChange?.(); }
  /** 누름: 노드 · 주제를 누르면 그 자리의 층으로 들어간다(전체 카메라는 남는다). 빈 곳을 누르면 고름을 푼다 */
  function wholeClick(e: MouseEvent) { const it = wHit(e.clientX, e.clientY); if (!it) { if (st.whole.sel) { set(LS.wholeSelect(st, '')); wholeSelChanged(); } return; }
    const n = byAny(it.id); if (!n) return; tip.style.display = 'none'; redraw(LS.enter(st, ids(chainTo(n)))); }
  /** 고르기(사이드바 트리 · 건반 · 요약): 관계가 서고, frame 이면 그 노드로 간다(이름이 읽힐 만큼 다가간다) */
  function wholePick(id: string, frame: boolean) { set(LS.wholeSelect(st, id)); const it = WL && WL.byId.get(id), q = posOf(id), v = LS.wholeView(st);
    if (it && q && v && frame) { const out = q.x < v.x || q.x > v.x + v.w || q.y < v.y || q.y > v.y + v.h;
      if (out || lodTier(LS.wholeScale(st)) !== 'near') { set(LS.wholeFrame(st, it.x, it.y, 1.05)); showWholeCamera(st); wholeBody(st); } }
    const n = byAny(id); if (n && live) live.textContent = `${KIND[n.k] || n.k} ${titleOf(n)}${n.d ? ' · ' + n.d : ''}`;
    wholeSelChanged(); }
  // 건반: 화살표 = 읽는 순서(주제 허브 → 그 노드들 → 다음 주제)의 다음 · 앞, Page = 다음 · 앞 주제, Home · End, Enter = 들어가기
  function wholeKey(e: KeyboardEvent) { if (!WL || e.altKey || e.ctrlKey || e.metaKey) return; const its = WL.items, cur = WL.byId.get(st.whole.sel), i = cur ? cur.order : -1;
    const hubAt = (from: number, dir: number) => { for (let j = from + dir; j >= 0 && j < its.length; j += dir) if (its[j].role === 'hub') return j; return from; };
    let j: number | null = null;
    if (e.key === 'ArrowDown' || e.key === 'ArrowRight') j = Math.min(its.length - 1, i + 1); else if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') j = Math.max(0, i - 1);
    else if (e.key === 'PageDown') j = hubAt(Math.max(i, 0), 1); else if (e.key === 'PageUp') j = i <= 0 ? 0 : hubAt(i, -1); else if (e.key === 'Home') j = 0; else if (e.key === 'End') j = its.length - 1;
    else if ((e.key === 'Enter' || e.key === ' ') && cur) { e.preventDefault(); const n = byAny(cur.id); if (n) { tip.style.display = 'none'; redraw(LS.enter(st, ids(chainTo(n)))); } return; }
    if (j == null) return; e.preventDefault(); wholePick(its[j].id, true); }

  // 다시 그릴 때 받은 자리(앞선 모양도 받는다, LS.restoreFrom)로 연다. 이 데이터에 그 길이 없으면 시작 길(없으면 뿌리)에서 연다
  { const R = ctx.restore, next = R ? LS.restoreFrom(st, R, canon) : null;
    if (next) { redraw(next); if (R!.view && !st.whole.on) { set(LS.setView(st, R!.view, svg.clientWidth)); showCamera(st); } }   // 전체 보기로 다시 열면 그 카메라(LS.wholeView)가 viewBox 다
    else {
      const sp = ctx.startPath ? ctx.startPath.map(byAny).filter(Boolean) as LNode[] : [];   // 시작 길이 이 데이터에 없는 주제를 가리키면 뿌리에서 시작한다
      setPath(sp.length ? sp : [topics.get(ROOT)!]);
    } }
  // ── 렌즈 패널: 옵션(기간)과 범례(종류는 누르면 끄고 켠다 = 범례가 곧 필터) ────────────────────────────────
  const mark = (k: string) => `<svg class="cmp-mk" viewBox="-8 -8 16 16" aria-hidden="true">${k === "topic" ? topicRing(6, "currentColor", 1.8) : k === "bridge" ? `<path d="M-4.2 -4.2A6 6 0 0 1 4.2 -4.2M4.2 4.2A6 6 0 0 1 -4.2 4.2" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/><circle r="2" fill="currentColor"/>` : shape(k, 0, 0, 5.5, "currentColor")}</svg>`;
  const kindsHTML = (s: LensState) => { const TSEL = s.tsel;
    const c: Record<string, number> = {}; for (const n of nodes) { if (!matchQ(n, s.query) || (TSEL && (!when(n) || when(n) < TSEL[0] || when(n) > TSEL[1]))) continue; const k = kindKey(n); c[k] = (c[k] || 0) + 1; }
    const kinds = ["concept", "doc", "decision", "record", "episode", "bridge"].map((k) => `<button class="cmp-lk${s.koff.includes(k) ? " off" : ""}" type="button" data-cmp-k="${k}" aria-pressed="${!s.koff.includes(k)}">${mark(k)}<span>${KIND[k]}</span><em>${c[k] || 0}</em></button>`).join("");
    return `<div class="cmp-kinds">${kinds}</div>`; };
  const legendHTML2 = () => {
    const areas = [...tints().areas].map(([a, col]) => `<span class="cmp-sw"><i style="background:${col}"></i>${a.split("/").pop()}</span>`).join("");
    const row: (art: string, name: string, extra?: string) => string = (art, name) => `<div class="cmp-lr"><svg viewBox="0 0 44 16" aria-hidden="true">${art}</svg><span>${name}</span></div>`;
    return `${row(mark("topic").replace('class="cmp-mk"', 'x="14" width="16"'), esc(KIND.topic), "")}
      ${row(`<path d="M2 6.6L42 7.6L42 8.4L2 9.4Z" fill="currentColor"/>`, esc(C.legendRows.meaning), "")}
      ${row(`<path d="M2 7.4H42" stroke="currentColor" stroke-width="1.4"/><path d="M19 4L23 8L19 12" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>`, esc(C.legendRows.order), "")}
      ${row(`<path d="M4 2V10Q4 14 8 14H40" fill="none" stroke="currentColor" stroke-width="2"/>`, esc(C.legendRows.attached), "")}
      ${row(`<circle cx="22" cy="8" r="5" fill="currentColor"/><path d="M15.6 4.2A6.4 6.4 0 0 0 15.6 11.8" fill="none" stroke="currentColor" stroke-width="2"/>`, esc(C.legendRows.otherTopic), "")}
      ${row(`<path d="M17.8 3.8A6 6 0 0 1 26.2 3.8M26.2 12.2A6 6 0 0 1 17.8 12.2" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/><circle cx="22" cy="8" r="2" fill="currentColor"/>`, esc(KIND.bridge), "")}
      <div class="cmp-sws">${areas}</div>`; };
  // ── 시간(시안의 시간 모달을 LNB 로): 날마다의 수(종류별로 쌓음, 끌어서 기간)와 기간 버튼 ─────
  const TSCOPE: string = 'all';   // 시간 막대가 세는 범위: 늘 전체다(지금 층만 세는 갈래는 쓰이지 않는다)
  const jewel = (i: number) => `#${topicJewels[st.theme][i].toString(16).padStart(6, '0')}`;   // 토큰 값을 읽어 쓴다(글자로 박지 않는다)
  const TK = (): [string, string, string][] => [['decision', KIND.decision, jewel(3)], ['record', KIND.record, jewel(1)], ['episode', KIND.episode, jewel(0)], ['concept', C.conceptAndDoc, jewel(2)]];
  const tKind = (n: LNode) => (n.k === 'decision' || n.k === 'record' || n.k === 'episode' ? n.k : 'concept');
  function scopeNodes(): LNode[] { if (TSCOPE === 'all') return dated; const s = new Set<LNode>(); if (LZ) for (const o of LZ.items) if (o.src && o.src.k !== 'topic' && o.role !== 'hub') s.add(o.src);
    if (LZ && LZ.focus) { const G = gather(LZ.focus.hub.src); for (const l of Object.values(G.shelf)) for (const n of l) s.add(n); } return [...s].filter((n) => when(n)); }
  function timeHTML(s: LensState) { const TSEL = s.tsel;
    const ns = scopeNodes().filter((n) => matchQ(n, s.query) && !s.koff.includes(kindKey(n))), W = Math.max(300, Math.round((pop.clientWidth || 520) - 34)), H = 80, padB = 14, bw = W / allDays.length, K = TK();
    const stack = allDays.map((): Record<string, number> => ({ decision: 0, record: 0, episode: 0, concept: 0 })); for (const n of ns) { const i = dIdx.get(when(n)); if (i != null) stack[i][tKind(n)]++; }
    const tot = stack.map((q) => q.decision + q.record + q.episode + q.concept), maxC = Math.max(1, ...tot), sc = (c: number) => (H - padB - 4) * c / maxC;
    const i0 = TSEL ? (dIdx.get(TSEL[0]) ?? 0) : -1, i1 = TSEL ? (dIdx.get(TSEL[1]) ?? allDays.length - 1) : -1; let bars = '';
    allDays.forEach((d, i) => { let y = H - padB; const inR = !TSEL || (i >= i0 && i <= i1);
      for (const [k, , c] of K) { const h = sc(stack[i][k]); if (!h) continue; y -= h; bars += `<rect x="${(i * bw).toFixed(1)}" y="${y.toFixed(1)}" width="${Math.max(1, bw - .4).toFixed(1)}" height="${h.toFixed(1)}" style="fill:${c};opacity:${inR ? .9 : .22}"/>`; }
      if (i === 0) bars += `<text x="0" y="${H - 2}">${esc(C.before)}</text>`; else if (d.endsWith('-01') && i * bw > 22) bars += `<text x="${(i * bw + 1).toFixed(1)}" y="${H - 2}">${d.slice(5)}</text>`; });
    if (TSEL) bars += `<rect class="sel" x="${(i0 * bw).toFixed(1)}" y="1" width="${((i1 - i0 + 1) * bw).toFixed(1)}" height="${H - padB - 1}" rx="3"/>`;
    const rows = ns.filter((n) => (!TSEL || (when(n) >= TSEL[0] && when(n) <= TSEL[1])) && matchQ(n, s.query) && !s.koff.includes(kindKey(n)));
    const undated = nodes.filter((n) => !when(n)).length;
    // 날짜가 있는 노드가 하나도 없으면 칸은 '이전' 하나뿐이다: 범위(allDays[1] ~ …)도 기간 단추도 말할 것이 없다.
    // 이 경우 범위 자리에는 noDates 문장을 쓰고, 기간 단추는 끄고 막대(끌어 고르기)는 그리지 않는다. 범위 줄에 undefined 가 나오면 안 된다
    const noDays = allDays.length < 2;
    const lab = noDays ? esc(C.noDates) : TSEL ? `${TSEL[0] === '0000-01-01' ? C.start : TSEL[0]} ~ ${TSEL[1]}` : `${allDays[1]} ~ ${allDays[allDays.length - 1]}`;
    return `      <div class="cmp-seg">${[['7', C.days(7)], ['30', C.days(30)], ['all', C.all]].map(([k, l]) => `<button class="btn btn-sm" data-cmp-r="${k}"${noDays && k !== 'all' ? ' disabled' : ''}>${l}</button>`).join('')}</div>
      <p class="cmp-range">${lab}</p>
      <div class="cmp-tleg">${K.map(([k, name, c]) => `<span><i style="background:${c}"></i>${name} ${rows.filter((n) => tKind(n) === k).length}</span>`).join('')}${undated ? `<span title="${esc(C.undatedTip)}">${esc(C.undated(undated))}</span>` : ''}</div>
      ${noDays ? '' : `<svg class="cmp-bars" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" data-bars>${bars}</svg>`}`;
  }
  // 붙은 곳: 기록·사건·결정이 붙은 뜻의 노드(개념·문서). LNB 트리가 그 아래로 들여 쓴다
  const hostOf = (n: LNode) => { if (isMean(n)) return null; for (const e of adj.get(n.id) || []) if (ATTACH.has(e.t) && e.a === n && isMean(e.b)) return e.b; return null; };
  // ── 시간 · 범례 창: 머리 뒤 묶음의 버튼 바로 아래. 창의 제 폭(CSS)이 단추 오른쪽 끝에서 내용 카드(`#surface`)
  //    안쪽 왼쪽까지에 들어가면 단추 오른쪽 끝에 맞춘다. 안 들어가면 카드 안쪽 왼쪽에서 열고, 카드가 창보다 좁으면
  //    화면 안(가장자리 8px)에 둔다 — 어느 쪽이든 창의 폭을 깎지 않아 안의 단추가 잘리지 않는다. 높이는 카드
  //    아래에서 멈춘다. 밖을 누르거나 Esc 면 닫힌다 ─────────────
  const btnOf = (k: string) => tools.querySelector(`[data-pop="${k}"]`);
  function placePop() { const b = st.pop && btnOf(st.pop); if (!b) return; const r = b.getBoundingClientRect();
    const card = (document.querySelector('#surface') || document.body).getBoundingClientRect(), gap = 8, top = Math.round(r.bottom + 6);
    pop.style.maxWidth = 'none'; pop.style.left = '0px'; pop.style.right = 'auto';
    const want = pop.offsetWidth || 0, room = r.right - card.left - gap, vw = window.innerWidth;
    if (want <= room) { pop.style.left = 'auto'; pop.style.right = `${Math.round(vw - r.right)}px`; pop.style.maxWidth = `${Math.max(0, Math.round(room))}px`; }
    else { const w = Math.min(want, vw - gap * 2); pop.style.left = `${Math.round(Math.max(gap, Math.min(card.left + gap, vw - gap - w)))}px`; pop.style.maxWidth = `${Math.round(w)}px`; }
    pop.style.top = `${top}px`; pop.style.maxHeight = `min(80dvh, 46rem, ${Math.max(0, Math.round(card.bottom - gap - top))}px)`; }
  function paintSide(s: LensState) { if (!s.pop) return;
    pop.innerHTML = s.pop === 'time' ? `<div class="cmp-sec cmp-time">${timeHTML(s)}</div>` : `<div class="cmp-sec">${wholeFolded() ? wholeRowHTML(s) : ''}${s.whole.on ? relsHTML(s) : ''}${kindsHTML(s)}<div class="cmp-legend">${legendHTML2()}</div></div>`; }
  // 머리가 아주 좁으면(app.css 의 topbar 480 접기) 전체 보기 단추가 머리에서 빠지고 범례 맨 위에 선다: 공간 이름과 전환이 자리를 지킨다
  const wholeFolded = () => { const b = tools.querySelector<HTMLElement>('[data-whole]'); return !!b && b.isConnected && getComputedStyle(b).display === 'none'; };
  const wholeRowHTML = (s: LensState) => `<button class="cmp-lk cmp-rels" type="button" data-whole aria-pressed="${s.whole.on}" title="${esc(C.wholeTip)}"><span class="cmp-mk">${WHOLE_ICON}</span><span>${esc(C.whole)}</span></button>`;
  // 전체 보기의 범례 맨 위: 주제 쌍의 관계선을 켜고 끈다(머리가 좁아도 단추가 하나만 늘도록 머리가 아니라 범례에 둔다)
  const relsHTML = (s: LensState) => `<button class="cmp-lk cmp-rels" type="button" data-rels aria-pressed="${s.whole.rels}" title="${esc(C.relsTip)}"><svg class="cmp-mk" viewBox="-8 -8 16 16" aria-hidden="true"><path d="M-6 4Q0 -7 6 4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg><span>${esc(C.rels)}</span></button>`;
  function openPop(k: 'time' | 'legend') { if (st.pop === k) { closePop(); return; } set(LS.togglePop(st, k)); pop.dataset.pop = k; pop.hidden = false;
    tools.querySelectorAll<HTMLElement>('[data-pop]').forEach((b) => b.setAttribute('aria-expanded', String(b.dataset.pop === k))); placePop(); paintSide(st); if (k === 'time') paintSide(st); }
  function closePop() { if (!st.pop) return; set(LS.closePop(st)); pop.hidden = true; pop.innerHTML = ''; tools.querySelectorAll('[data-pop]').forEach((b) => b.setAttribute('aria-expanded', 'false')); highlight(null); }
  on(tools, 'click', (e: At<MouseEvent>) => { const b = e.target.closest<HTMLElement>('[data-pop]'); if (b) openPop(b.dataset.pop as 'time' | 'legend');
    if (e.target.closest('[data-whole]')) { tip.style.display = 'none'; redraw(LS.toggleWhole(st)); }
  });
  onWin('pointerdown', (e: PointerEvent & { target: Node }) => { if (st.pop && !pop.contains(e.target) && !tools.contains(e.target)) closePop(); }, true);
  onWin('keydown', (e: KeyboardEvent) => { if (e.key === 'Escape' && st.pop) { e.stopImmediatePropagation(); closePop(); } }, true);
  onWin('resize', () => { if (st.pop) { placePop(); paintSide(st); } });
  // ── LNB 트리의 줄: 거르개(검색 ∧ 기간 ∧ 종류)를 지난 노드를 주제별로. 뜻의 층(depth 0) 아래에 붙은 것(depth 1) ──────
  const passes = (n: LNode, s: LensState) => n.k !== 'topic' && matchQ(n, s.query) && !s.koff.includes(kindKey(n)) && (!s.tsel || (!!when(n) && when(n) >= s.tsel[0] && when(n) <= s.tsel[1]));
  type TreeRow = { id: string; title: string; kind: string; depth: number };
  function treeGroups(s: LensState) {
    const by = new Map<string, DNode[]>(); for (const n of nodes) { if (!passes(n, s)) continue; const d = n.d; if (!by.has(d)) by.set(d, []); by.get(d)!.push(n); }
    const newestFirst = (a: LNode, b: LNode) => (when(b) || '').localeCompare(when(a) || '') || titleOf(a).localeCompare(titleOf(b));
    const out: { domain: string; rows: TreeRow[] }[] = [];
    for (const d of [...by.keys()].sort()) { const its = by.get(d)!, inList = new Set(its.map((n) => n.id)), kids = new Map<string, DNode[]>(), loose: DNode[] = [], rows: TreeRow[] = [];
      for (const n of its) { if (isMean(n)) continue; const h = hostOf(n); if (h && inList.has(h.id)) { if (!kids.has(h.id)) kids.set(h.id, []); kids.get(h.id)!.push(n); } else loose.push(n); }
      const R = (n: LNode, depth: number) => rows.push({ id: n.id, title: titleOf(n), kind: n.k, depth });
      for (const m of its.filter(isMean).sort(newestFirst)) { R(m, 0); for (const c of (kids.get(m.id) || []).sort(newestFirst)) R(c, 1); }
      for (const n of loose.sort(newestFirst)) R(n, 0);
      out.push({ domain: d, rows }); }
    return out; }
  // 거르개가 걸려 있고 남은 것이 적으면 그 주제들을 펴서 보인다(많으면 기본 펼침 그대로: 수천 줄을 쏟지 않는다)
  function treeChanged(s: LensState, reset = false) { const g = treeGroups(s), n = g.reduce((x, q) => x + q.rows.length, 0), narrowed = !!s.query || !!s.tsel || s.koff.length > 0;
    ctx.onTree?.({ reveal: narrowed && n <= 300 ? g.map((q) => q.domain) : [], reset }); }
  const treeMark = (k: string) => mark(k);
  const highlight = (ids: Set<string> | null) => { svg.classList.toggle('hot', !!ids && ids.size > 0); svg.querySelectorAll<SVGGElement>('#cmp-nodes .node').forEach((g) => g.classList.toggle('pick', !!ids && ids.has(g.dataset.id!))); };
  on(pop, "click", (e: At<MouseEvent>) => {
    const kb = e.target.closest<HTMLElement>("[data-cmp-k]"); if (kb) { redraw(LS.toggleKind(st, kb.dataset.cmpK!), false); treeChanged(st); return; }
    if (e.target.closest("[data-rels]")) { set(LS.toggleRels(st)); wholeBody(st); paintSide(st); return; }
    if (e.target.closest("[data-whole]")) { tip.style.display = "none"; redraw(LS.toggleWhole(st)); return; }
    const rb = e.target.closest<HTMLElement>("[data-cmp-r]"); if (rb) { set(LS.setRange(st, LS.rangeOf(allDays, rb.dataset.cmpR!))); dimLens(st); paintSide(st); treeChanged(st); return; }
  });
  const barsEl = () => pop.querySelector("[data-bars]")!;
  const day = (e: PointerEvent) => { const r = barsEl().getBoundingClientRect(); return Math.max(0, Math.min(allDays.length - 1, Math.floor((e.clientX - r.left) / (r.width / allDays.length)))); };
  on(pop, "pointerdown", (e: At<PointerEvent>) => { if (!e.target.closest("[data-bars]")) return; const i = day(e); pop.setPointerCapture(e.pointerId); set(LS.barStart(st, allDays, i)); dimLens(st); });
  on(pop, "pointermove", (e: PointerEvent) => { if (st.bar == null) return; set(LS.barMove(st, allDays, day(e))); dimLens(st); });
  on(pop, "pointerup", () => { if (st.bar == null) return; set(LS.barEnd(st)); paintSide(st); treeChanged(st); });
  // 크기의 길: 그래프 칸(SVG)을 관찰해 크기를 상태에 넣고(LS.resize: 층의 카메라는 같은 그림 범위를, 전체 보기의 카메라는 보이든 떠나 있든
  // 그 뜻을 지킨다) 그 카메라를 다시 놓는다. 층의 그림은 그대로다. 전체 보기는 viewBox 를 바로 놓고, 계획과 몸은 손이 멈추면(150ms) 다시 짓는다
  if (typeof ResizeObserver !== 'undefined') fx.observe(new ResizeObserver((es) => { const r = es[es.length - 1].contentRect; set(LS.resize(st, { w: r.width, h: r.height })); if (st.whole.on) wholeResized(); else showCamera(st); }), svg);
  // 테마 · 글꼴의 길: 상태를 바꾸고 다시 그린다. 테마는 늘 다시 그린다. 글꼴은 글자 폭(LS.glyphWidth)이 바뀔 때만 다시 그린다
  // (지금 폭은 글꼴과 상관없는 고정값이라 그림이 그대로다: 실제 글꼴로 재는 일은 glyphWidth 한 곳에서 한다)
  function restyle(next: LensState, always: boolean) { if (always || metric(next) !== metric(st)) redraw(next); else set(next); }
  const fonts: Partial<EventTarget> | undefined = document.fonts;
  if (fonts && fonts.addEventListener) on(fonts as EventTarget, 'loadingdone', () => restyle(LS.fontsSettled(st), false));
  const destroy = () => { fx.release(); host.innerHTML = ""; side.remove(); tools.remove(); pop.remove(); ctx.onDestroy?.(); };
  if (import.meta.env?.DEV) (window as unknown as { __compass?: unknown }).__compass = { open: (xs: string[]) => setPath(xs.map(byAny) as LNode[]), lz: () => LZ, pin: togglePin, range: (r: [string, string] | null) => { set(LS.setRange(st, r)); dimLens(st); },
    // 손 확인 판 · 측정 스크립트만 쓴다: 전체 보기의 카메라 · 계획 · 그린 자리 · 상태
    cam: (v?: Box) => { if (v && st.whole.on) { set(LS.wholeSetView(st, v)); wCommit(); } return st.whole.on ? LS.wholeView(st) : st.camera.view; }, plan: () => st.whole.plan && { ...st.whole.plan, mode: wMode, last: st.whole.last, tried: PLANTRIED },
    wl: () => (WL && wMode === 'dots' && PLANF?.F ? (() => { const items = WL.items.map((i) => { const q = posOf(i.id)!; return { ...i, x: q.x, y: q.y }; }); return { ...WL, items, byId: new Map(items.map((i) => [i.id, i])) }; })() : WL),
    st: () => JSON.parse(JSON.stringify({ ...st, whole: st.whole })), perf: () => ({ ...WPERF }) };
  // 목록에서 고른 노드로: 지금 렌즈에 이미 사슬이 있으면 그 사슬로(맥락 유지), 없으면 주제 사슬을 따라 편다
  function goTo(id: string) { const n = byAny(id); if (!n) return; if (st.whole.on) { wholePick(n.id, true); return; }   // 전체 보기: 그 자리로 가서 고른다(관계가 선다)
    const o = LZ && LZ.items.find((q) => q.id === n.id && q.chain);
    redraw(LS.pickFromTree(st, ids(o ? (o.role === 'hub' ? o.chain! : [...o.chain!, n]) : chainTo(n)))); }
  const centre = () => (st.whole.on ? st.whole.sel : LS.centreOf(st));
  // 같은 범위를 다시 그릴 때(변경 감지 새로고침 · 쓰고 난 뒤) 보던 자리를 그대로 잇는다: 길 · 앞뒤 기록 ·
  // 더 연 목록 · 끈 종류 · 기간 · 카메라 · 찾기. 새 데이터에 없는 노드는 버린다
  const snapshot = () => LS.snapshotOf(st);
  // 밝기 테마가 바뀌면 그 테마의 주제 색 표로 다시 그린다(글자 폭도 다시 잰다)
  const retheme = () => restyle(LS.fontsSettled(LS.themeChanged(st, themeMode())), true);
  return { destroy, side, tools, filter: setQuery, open: goTo, tree: () => treeGroups(st), mark: treeMark, colour: (p: string) => (!p || p === ROOT ? '' : topicColour(p)), centre, snapshot, retheme };   // 뿌리는 색을 입지 않는다(LNB 줄은 잉크 그대로)
}
