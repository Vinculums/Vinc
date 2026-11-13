/** 나침반 한 층의 내용: 가운데(주제 또는 노드)의 이웃을 네 자리(12 · 9 · 3 · 6시)로 모으고(gather), 범례에서 끈 종류를 거르고
 *  부채가 받는 수로 자른 뒤 부채와 목록의 크기를 잰다(compassOf). DOM 을 만지지 않는다. 데이터는 모델(view-compass-model.ts)에서
 *  받고, 이 결과로 층을 놓는 것은 view-compass.ts 의 펼친 나무(layoutTree)다. */
import type { COPY } from "../copy";
import { isMean, kindKey, type CompassModel, type LNode, type Edge } from "./view-compass-model";

export type Pt = [number, number];
/** 한 층에 놓을 이웃 하나: 노드와 관계 이름(rel), 그 관계(e). 다리는 잇는 하위 주제(subs)와 층 밖으로도 잇는지(outside)를 든다. */
export interface Unit { n: LNode; rel?: string; e?: Edge; main?: boolean; label?: string; farEnds?: string[] }
export type Shelf = { decision: LNode[]; record: LNode[]; episode: LNode[]; doc: LNode[]; [k: string]: LNode[] };
export interface Gathered { up: Unit[]; left: Unit[]; right: Unit[]; bridges: { n: LNode; subs: (string | null)[]; outside: boolean }[]; shelf: Shelf }
/** 한 층(나침반 하나)의 내용과 크기(rL · rR: 부채 반지름, E: 나침반 둘레, FP: 목록까지 친 둘레). */
export interface Cp { node: LNode; G: Gathered; rL: number; rR: number; shelfN: number; E: number; FP: number }
export type Placement = Map<string, { x: number; y: number; th12: number; route?: Pt[] }>;
/** 배치 한 번이 읽는 것(curS · measS). */
export interface LayoutIn { mode: string; path: LNode[]; place: Placement | null; dirs: Placement | null; canon: boolean; koff: Set<string>;
  more: [string, number][]; pins: Set<string>; pinChain: Set<string>; filt?: Map<string, unknown>; cps?: Map<string, Cp> }

// 부채의 자: 목록이 접히기 전의 줄 수 · 세 자리의 각 · 도와 라디안. 펼친 나무도 같은 값을 읽는다
export const ROWS = 18;
export const REL: Record<'up' | 'left' | 'right', [number, number]> = { up: [-26, 26], left: [-135, -45], right: [45, 135] };   // 12시 방향(θ12)에서 잰 각도(도)
export const rad = (d: number) => d * Math.PI / 180, todeg = (r: number) => r * 180 / Math.PI;
export type Pos = { u: Unit; ang: number; x: number; y: number };
// 부채: 가운데에서 a0..a1 도 사이에 이웃을 크기만큼 나눠 세운다. 다 들어가도록 반지름(R)을 키운다
export function fanPlace(list: Unit[], center: { x: number; y: number }, a0: number, a1: number, rMin: number, gap: number, size: (u: Unit) => number): { R: number; pos: Pos[] } {
  const need = list.reduce((s, u) => s + 2 * size(u) + gap, 0), span = rad(a1 - a0), R = Math.max(rMin, need / span);
  let acc = rad(a0) + (span - need / R) / 2;
  return { R, pos: list.map((u) => { const w = (2 * size(u) + gap) / R, mid = acc + w / 2; acc += w; return { u, ang: mid, x: center.x + Math.cos(mid) * R, y: center.y + Math.sin(mid) * R }; }) };
}
export const subR = (n: LNode) => Math.max(9, Math.min(24, 4 + 1.5 * Math.sqrt(n.count || 1)));
export const sizeU = (u: Unit) => (u.n.k === 'topic' ? subR(u.n) : isMean(u.n) ? 7 : 5.5);
// 위·아래로 서는 부채(가운데 각이 거의 수직)는 두 줄 목록으로 편다. 부채 그대로면 이름표가 세로로 서서 읽기 어렵다.
// side: -1 왼쪽 줄만, 1 오른쪽 줄만, 0 반씩 양쪽. 두 줄 사이는 비워 둔다(부모·자식 선이 그 사이로 지나간다)
export function colPlace(list: Unit[], center: { x: number; y: number; r?: number }, cDeg: number, side: number, size: (u: Unit) => number): { R: number; pos: Pos[] } {
  const s = Math.sin(rad(cDeg)) > 0 ? 1 : -1, n = list.length, nl = side === 0 ? Math.ceil(n / 2) : side < 0 ? n : 0, r0 = (center.r || 22) + 34, acc = [r0, r0];
  const pos = list.map((u, i) => { const L = i < nl ? 0 : 1, h = Math.max(22, 2 * size(u) + 8), d = acc[L] + h / 2; acc[L] += h;
    return { u, ang: L === 0 ? Math.PI : 0, x: center.x + (L === 0 ? -1 : 1) * (40 + size(u)), y: center.y + s * d }; });
  return { R: Math.max(acc[0], acc[1]), pos };
}

/** 모델을 받아 이웃 모으기(gather)와 층 하나의 내용(compassOf)을 짓는다. `C.rel` 은 관계의 이름표다. 모델과 데이터는 바꾸지 않는다. */
export function compassGather(M: CompassModel, C: Pick<typeof COPY.ui.compass, "rel">) {
  const { ATTACH, ROOT, topics, adj, bridgeEnds } = M;
  // ── 이웃 모으기: 가운데가 노드일 때와 주제일 때 ──────────────────────────────────────────────
  // 나침반의 네 자리에 무엇이 가는가(가운데가 주제든 노드든 같은 규칙):
  //   12시 = 어디서 왔나(위 주제는 정확히 위, 원천·부모·앞선 것·붙은 곳·새것은 그 좌우로)
  //    9시 = 뜻의 관계(노드) / 이 주제에 직접 속한 개념(주제)
  //    3시 = 그 아래 구성(하위 주제와 그 사이를 잇는 다리) / 여기서 나온 것·물려받은 것·뒤의 것·옛것(노드)
  //    6시 = 줄기가 곧게 내려가 붙은 결정·기록·사건·문서의 목록으로(최신순)
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
  // 한 층(나침반 하나)의 내용. 부모는 이미 12시에 있으므로 뺀다
  function compassOf(node: LNode, parentId: string | null, S: LayoutIn): Cp {
    const G = gather(node), drop = (l: Unit[]) => l.filter((u) => u.n.id !== parentId);
    G.up = drop(G.up); G.left = drop(G.left); G.right = drop(G.right);
    // 범례에서 끈 종류의 노드는 뺀다(주제 · 지나온 길 · 고정 노드는 남긴다). 기간은 빼지 않고 흐리기만 한다
    const keep = (n: LNode) => S.canon || n.k === 'topic' || S.path.some((q) => q.id === n.id) || S.pins.has(n.id) || !S.koff.has(kindKey(n));
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
  return { gather, compassOf };
}
