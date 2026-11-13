/** 나침반의 펼친 나무와 팔: 펼칠 층들을 BFS 로 놓고(layoutTree), 모든 주제를 펼친 정해진 자리에서 팔을 고르고 층을 격자로 묶으며
 *  (crossCanon), 하나씩 열기를 그 묶음 배치에 맞춘다(packPath). DOM 을 만지지 않는다. 층의 내용은 이웃 모으기(view-compass-gather.ts)
 *  에서 받고, 색 · 이름표 · 글자 폭은 마운트가 건네는 함수로 부르는 그 순간의 상태에서 읽는다. 렌더 캐시(LZ · CANONL · PLAN_*)는 읽지 않는다. */
import * as LS from "./view-compass-state";
import type { LNode, Edge } from "./view-compass-model";
import { ROWS, REL, rad, todeg, fanPlace, colPlace, sizeU, type compassGather, type Pt, type Unit, type Cp, type Placement, type LayoutIn, type Pos } from "./view-compass-gather";

/** 배치가 놓은 것 하나(노드 · 허브 · 목록 줄 · 다리 · 고정 노드). */
export interface Item { id: string; src: LNode; k: string; x: number; y: number; r: number; c: string; label: string; role: string; level: number;
  rel?: string; e?: Edge; ang?: number; comp?: string; chain?: LNode[]; parentHub?: Item; viaE?: Edge; route?: Pt[]; subs?: string[]; outside?: boolean;
  farEnds?: string[]; pinned?: boolean; link?: undefined; linkId?: undefined }
/** 층의 줄에 든 것: 놓은 것, 이미 놓인 것으로 잇는 빛살(link), 나중에 이을 빛살(linkId). */
export type Slot = Item | { link: Item; e?: Edge; linkId?: undefined; id?: undefined; comp?: undefined; role?: undefined } | { linkId: string; e?: Edge; link?: undefined; id?: undefined; comp?: undefined; role?: undefined };
export interface Kinds { k: string; name: string; all: LNode[] }
export interface ShelfCol extends Kinds { x: number; items: Item[] }
/** 놓은 층 하나. */
export interface Comp { cp: Cp; hub: Item; th12: number; slots: Map<string, { ang: number; r: number; e?: Edge }>; focus: boolean; level: number; items: Slot[]; chain: LNode[];
  parent: Comp | null; pinOnly: boolean; free: number | null; kinds?: Kinds[]; stub?: { x: number; y: number; text: string; ang: number }; far?: boolean }
/** 층 하나의 목록 자리: 버스(Q · BY)와 줄들. */
export interface ShelfSet { hub: Item; comp: Comp; shelves: ShelfCol[]; Q: { x: number; y: number; a6?: number }; BY: number; mode: string; side: number; a6: number }
/** 배치 한 번의 결과. */
export interface Layout { items: Item[]; comps: Comp[]; focus: Comp; shelves: ShelfCol[]; sets: ShelfSet[]; Q: ShelfSet["Q"] | null; BY: number; mode: string; side: number; a6: number;
  boxes?: (c: Comp) => number[][]; pinned?: Item[] }
/** 층 하나의 격자: 열 수(k) · 열 폭(cw) · 줄 높이(rh) · 전체(W · H). */
export type Grid = { k: number; cw: number[]; rh: number[]; W: number; H: number };
export type Packed = Grid & { w: number; h: number; sc: number };
/** 층(과 그 자식 격자)의 상자: 자기 층(w0 · h0)과 전체(w · h). 자식이 있으면 격자도 든다. */
export type Sized = { w: number; h: number; w0: number; h0: number } & Partial<Packed>;
/** 팔 하나: 방향(g · p) · 가로인지 · 잰 방향(o) · 층의 12시 · 상자 표 · 격자의 원점(O). */
export type Arm = { g: Pt; p: Pt; hz: boolean; o: 'v' | 'u' | 'h'; th12: number; B: Map<Comp, Sized>; O?: Pt };
export type Meas = Map<string, Partial<Record<"v" | "u" | "h", { top: number; dep: number; hw: number }>>>;
/** 정해진 자리(팔)의 결과: 주제 층의 자리와 주제마다 고른 팔. */
export interface Plan { out: Placement; arms: Map<string, number> }
/** 펼친 나무가 부르는 그 순간에 읽는 것: 노드의 색 · 이름표, 이름표 글자 하나의 폭, 종류 이름(목록 머리) */
export interface TreeLook { colv: (n: LNode) => string; titleOf: (n: LNode) => string; GW: (k: 'fan' | 'bridge') => number; KIND: Record<string, string> }

// 전체 배치(네 팔). 맨 위 주제의 상자를 가운데 두고, 그 하위 주제 가지를 아래·오른쪽·위·왼쪽 팔에 나눈다. 팔은 바람개비처럼
// 맨 위 상자의 한 모서리에서 시계 방향으로 뻗어(위 팔은 오른쪽으로, 오른쪽 팔은 아래로 …) 서로의 자리에 들어가지 않는다.
// 가지 하나는 상자 하나다: 자기 층(폭 2·HW, 깊이 TOP + DEP) 아래에 자식 상자들을 열을 맞춘 격자로 둔다. 격자의 열 사이와
// 줄 사이는 비어 있는 통로이고, 부모에서 자식으로 가는 선(route)은 그 통로만 따라 꺾는다. 열 수는 상자가 화면 비율에 가깝게 고른다.
// 좌표: u 는 팔 방향(부모에서 멀어지는 쪽), v 는 그 3시 쪽. 가로 팔에서는 이름표가 u 쪽으로 길어 앞뒤 여유를 더 준다
export function crossCanon(L: Layout, meas: Meas | null = null, armsOf: Map<string, number> | null = null, straight = false): Plan {
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

/** 이웃 모으기(compassOf)와 그리기 쪽의 손잡이(look)를 받아 펼친 나무 · 하나씩 열기의 묶음 배치 · 노드가 차지하는 점들을 짓는다.
 *  층의 나침반 캐시는 배치 입력(S.cps)으로 받은 것만 쓴다. 모델과 렌즈 상태는 바꾸지 않는다. */
export function compassTree(G: ReturnType<typeof compassGather>, look: TreeLook) {
  const { compassOf } = G, { colv, titleOf, GW, KIND } = look;
  // 노드 하나가 화면에서 차지하는 점들(이름표까지). 카메라와 전체 배치의 층 크기 재기가 같이 쓴다
  const extentPts = (o: Item): Pt[] => { if (o.role === 'shelf') return [[o.x - 30, o.y - 40], [o.x + 200, o.y + 12]];
      if (o.pinned) return [[o.x - 60, o.y - 50], [o.x + 180, o.y + 50]];   // 고정 노드: 이름표까지 들어오게
      // 이름표가 실제로 차지하는 만큼만 잡는다: 가로로 쓰는 고리 주제는 옆으로 약 110px·위아래로 30px, 비스듬한 부채 이름은 이름 길이만큼
      if (o.role === 'hub') return o.k === 'topic' ? [[o.x - o.r - 30, o.y - o.r - 10], [o.x + o.r + 30, o.y + o.r + 40]] : [[o.x - o.r - 170, o.y - o.r - 20], [o.x + o.r + 20, o.y + o.r + 30]];
      const a = o.ang ?? 0;
      // 하위 주제 이름은 기울이지 않고 가로로 쓴다(수 배지까지): 부채 방향이 아니라 왼쪽이나 오른쪽으로 길어진다
      if (o.role === 'right' && o.k === 'topic') { const s = Math.cos(a) < 0 ? -1 : 1, L = o.r + 12 + GW('fan') * (Math.min(18, o.label.length) + 4); return [[o.x - o.r, o.y - o.r - 4], [o.x + o.r, o.y + o.r + 4], [o.x + s * L, o.y]]; }
      const L = o.r + 12 + GW('fan') * Math.min(24, o.label.length); return [[o.x - o.r, o.y - o.r], [o.x + o.r, o.y + o.r], [o.x + Math.cos(a) * L, o.y + Math.sin(a) * L]]; };
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
  return { layoutTree, packPath, extentPts };
}
