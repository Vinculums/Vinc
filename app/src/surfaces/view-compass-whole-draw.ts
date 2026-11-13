/** 나침반 렌즈의 전체 보기를 SVG 로 그린다: 늘 그리는 뼈대(주제 줄기 · 목록 줄기), 보이는 곳만 다시 짓는 몸(노드 · 빛살 · 이름표),
 *  가리킴과 고름(관계선 · 고정 표). 배치와 계획은 마운트(view-compass.ts)가 지어 memo 에 넣고, 여기서는 그것을 읽어 그린 뒤
 *  그린 단계 · 배율 · 둘레를 memo 에 남긴다. 렌즈 상태는 받은 것(s)만 읽고 바꾸지 않는다. 움직임은 마운트의 fx 에 이름으로 건다. */
import type { COPY } from "../copy";
import { esc } from "../components";
import * as LS from "./view-compass-state";
import type { LensState, Box } from "./view-compass-state";
import { lodTier, labelShown, placeLabels, gridIndex, FONT, RING_SEP, type WLayout, type WItem, type FarArr, type Pt as WPt } from "./view-compass-whole";
import { when, type CompassModel, type LNode } from "./view-compass-model";
import { shape, PIN_PATHS } from "./view-compass-shapes";

/** 손 맞추기의 격자 색인이 드는 것과 그 색인 */
export type Hit = { id: string; x: number; y: number; r: number };
export type WIx = ReturnType<typeof gridIndex<Hit>>;
/** 그린 그림의 기억(렌즈 상태가 아니다) 가운데 전체 보기 그리기와 마운트가 함께 읽고 쓰는 것. 마운트가 하나 짓고(renderMemo) 둘이 같은 객체를 든다.
 *  배치(WL, 격자 색인 포함) · 먼 단계의 계획(PLANF)은 마운트가 짓고, 그린 단계(wMode · wTier) · 배율(wScale) · 둘레(wRegion) · 거르개 서명(wFilt) ·
 *  그려 둔 viewBox(wVC)는 그리기가 남긴다. panEl(손짓 겹) · live(읽기 칸의 알림)는 전체 보기에 처음 들 때 그리기가 짓는다.
 *  prevPos 는 층 보기의 지난 노드 자리다: 전체 보기를 떠날 때 그리기가 잊는다. WPERF 는 배치 · 그리기에 걸린 시간(측정용)이다 */
export interface RenderMemo { prevPos: Map<string, { x: number; y: number }>; WL: (WLayout & { ix: WIx }) | null; PLANF: { key: string; F: (FarArr & { ix: WIx }) | null } | null;
  WPERF: { layout: number; draw: number }; wMode: string; wTier: string; wScale: number; wRegion: number[] | null; wFilt: string; wVC: Box | null;
  panEl: HTMLElement | null; live: HTMLElement | null }
/** 처음의 기억: 아무것도 그리지 않은 때 */
export const renderMemo = (): RenderMemo => ({ prevPos: new Map(), WL: null, PLANF: null, WPERF: { layout: 0, draw: 0 }, wMode: '', wTier: '', wScale: 0, wRegion: null,
  wFilt: '', wVC: null, panEl: null, live: null });
/** 그리기가 거는 효과: 마운트의 fx(이름 하나에 하나, destroy 가 모두 놓는다) 가운데 쓰는 것 */
export interface WholeEffects { frame(k: string, f: FrameRequestCallback): void; cancelFrame(k: string): void; after(k: string, ms: number, f: () => void): void;
  cancelAfter(k: string): void; cancelIdle(k: string): void }
/** 그리기가 마운트에서 받는 것: 문구, 효과, 그림이 서는 요소(svg · 렌즈 · 머리 도구 · 풍선 · id 찾기), 이름표 · 색 · 섞기 · 주제 고리,
 *  찾기 맞춤, 글자 폭(지금 글꼴). 색 · 섞기 · 글자 폭은 부르는 그 순간의 상태를 읽는다 */
export interface WholeLook { C: Pick<typeof COPY.ui.compass, 'lens' | 'wholeLabel' | 'wholeHubsOnly'>; fx: WholeEffects; svg: SVGSVGElement; root: HTMLElement;
  tools: HTMLElement; tip: HTMLElement; $id: (k: string) => Element | null; titleOf: (n: LNode) => string; colv: (n: LNode) => string;
  mix: (c: string, p: number) => string; topicColour: (p: string | undefined) => string; ringS: (r: number, col: string, w: number) => string;
  matchQ: (n: LNode, q: string) => boolean; tw: (t: string, k: LS.GlyphKind) => number }

/** 전체 보기를 그리는 함수들을 돌려준다. 모델은 읽기만 한다 */
export function compassWholeDraw(M: CompassModel, memo: RenderMemo, look: WholeLook) {
  const { ATTACH, RD, edges, adj, fresh, bridgeEnds, byAny } = M, { C, fx, svg, root, tools, tip, $id, titleOf, colv, mix, topicColour, ringS, matchQ, tw } = look;
  // 그리기만 쓰는 지난 몸의 기억: 이름 · 표식 상자(가리킨 이름이 피한다), 노드 자리(단계가 바뀔 때 옮겨 간다), 전체 보기에 처음 들었는가와 허브만 그릴 때의 말
  let wNameBoxes: WPt[][] = [], wMarkBoxes: WPt[][] = [], wLast = new Map<string, WPt>(), wLastDots = false;
  let wholeDom = false, note: HTMLElement | null = null;
  /** 그 배율에서 그리는 것: hubs(허브만) · dots(격자 점) · mid(고리 모양) · near(이름표까지) */
  const modeAt = (s: LensState, sc: number) => { if (sc >= RING_SEP) return lodTier(sc); const P = s.whole.plan; return P && memo.PLANF && memo.PLANF.F && P.sDots != null && sc >= P.sDots * .999 ? 'dots' : 'hubs'; };
  /** 지금 그림에서 그 노드가 그려진 자리(격자 점 단계는 격자 자리, 그 밖은 고리 자리)와 그려진 반지름(그림 단위) */
  const posOf = (id: string): (Hit & { topic: string; role: string; label: string }) | null => { const it = memo.WL && memo.WL.byId.get(id); if (!it) return null; const F = memo.PLANF && memo.PLANF.F;
    if (memo.wMode === 'dots' && F && it.role !== 'hub') { const q = F.pos.get(id); if (q) return { x: q[0], y: q[1], r: F.dotPx / 2 / memo.wScale, topic: it.topic, role: it.role, label: it.label, id }; }
    if ((memo.wMode === 'dots' || memo.wMode === 'hubs') && it.role === 'hub') return { ...it, r: (memo.wMode === 'hubs' ? 4 : 2.8) / memo.wScale };
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
    if (!memo.panEl && typeof (svg as Element & { before?: unknown }).before === 'function') { memo.panEl = document.createElement('div'); memo.panEl.className = 'cmp-pan'; svg.before(memo.panEl); memo.panEl.append(svg); }
    if (!memo.live) { memo.live = document.createElement('div'); memo.live.className = 'cmp-live'; memo.live.setAttribute('aria-live', 'polite'); root.append(memo.live); }
    if (!note) { note = document.createElement('div'); note.className = 'cmp-note'; note.setAttribute('role', 'status'); note.hidden = true; root.append(note); } }
  /** 층 보기로 돌아왔다: 전체 보기의 일(유휴 계획 · 손짓 마무리 · 크기 뒤 다시 짓기 · 가리킴 · 옮김)을 지우고 svg 를 층 보기의 모습으로 */
  function quitWhole() { if (!wholeDom) return; wholeDom = false;
    fx.cancelIdle('wplan'); fx.cancelAfter('wcommit'); fx.cancelAfter('wresize'); fx.cancelFrame('whover'); fx.cancelFrame('wcross'); fx.cancelAfter('wcross'); fx.cancelAfter('peek');
    if (memo.panEl) memo.panEl.style.transform = ''; memo.wVC = null; memo.wMode = ''; memo.wTier = ''; memo.wRegion = null; memo.wFilt = ''; wLast = new Map(); memo.prevPos = new Map();
    svg.classList.remove('whole', 'far'); svg.removeAttribute('tabindex'); svg.setAttribute('aria-label', C.lens); svg.style.removeProperty?.('--wk'); svg.style.cursor = '';
    tip.style.display = 'none'; if (memo.live) memo.live.textContent = ''; if (note) note.hidden = true;
    tools.querySelector('[data-whole]')?.setAttribute('aria-pressed', 'false'); }
  function syncWholeUi(s: LensState) { tools.querySelector('[data-whole]')?.setAttribute('aria-pressed', String(s.whole.on));
    if (memo.WL) { svg.setAttribute('tabindex', '0'); svg.setAttribute('aria-label', C.wholeLabel(memo.WL.items.filter((q) => q.role !== 'hub').length, memo.WL.topics.length)); } }
  /** 카메라를 SVG 에 놓는다(손짓 겹의 변환은 지운다): viewBox 는 늘 지금 칸의 비율이라 화면 자리와 그림 자리가 맞는다 */
  function showWholeCamera(s: LensState) { const v = LS.wholeView(s); if (!v) return; fx.cancelAfter('wcommit'); memo.wVC = { ...v }; if (memo.panEl) memo.panEl.style.transform = ''; svg.setAttribute('viewBox', LS.viewBoxAttr(v)); }
  function drawWhole(s: LensState) {
    fx.cancelFrame('fly'); fx.cancelFrame('flip'); fx.cancelAfter('flip'); fx.cancelFrame('grow');   // 층 보기의 카메라 · 옮김 · 줄기 움직임이 남아 덮지 않게
    enterWholeDom(); const L = memo.WL!;
    svg.classList.add('whole'); svg.classList.remove('lod1', 'lod2', 'moving', 'hot');
    svg.innerHTML = `<defs></defs><g id="cmp-wstat">${wholeStatic(L)}</g><g id="cmp-wstem">${wholeStems(L)}</g><g id="cmp-wbun"></g><g id="cmp-wsp"></g><g id="cmp-nodes"></g><g id="cmp-wlab"></g><g id="cmp-whl"></g>`;
    syncWholeUi(s); showWholeCamera(s); wLast = new Map(); wholeBody(s); }
  /** 몸: 화면 둘레(1.5배)의 노드 · 빛살 · 이름표만 짓는다. 그리는 것(modeAt): hubs = 허브 · 줄기 · 이름과 수만(점 없음), dots = 격자 점,
   *  mid = 고리 자리의 모양과 빛살, near = 이름표까지. 화면 크기 이름은 노드 표식 · 줄기 · 다른 이름과 닿지 않고 그래프 칸 안에 다 드는 자리에만(큰 주제부터) */
  function wholeBody(s: LensState) {
    const V = LS.wholeView(s); if (!s.whole.on || !memo.WL || !V) return; const L = memo.WL, t0 = performance.now();
    const sc = LS.wholeScale(s), tier = lodTier(sc), mode = modeAt(s, sc), far = tier === 'far', dots = mode === 'dots', hubsOnly = mode === 'hubs', m = .5, F = dots ? memo.PLANF!.F! : null;
    // 먼 단계는 모든 노드를 그린다(맞춤 화면이 곧 전체다): 끌어도 둘레를 벗어나지 않아 다시 짓지 않는다
    const R = far ? [-Infinity, -Infinity, Infinity, Infinity] : [V.x - V.w * m, V.y - V.h * m, V.x + V.w * (1 + m), V.y + V.h * (1 + m)];
    const crossing = dots !== wLastDots && !hubsOnly && wLast.size > 0 && !matchMedia('(prefers-reduced-motion: reduce)').matches;
    memo.wRegion = R; memo.wTier = tier; memo.wMode = mode; memo.wScale = sc; memo.wFilt = `${s.tsel}|${s.query}`;
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
    wholeOverlay(s); memo.WPERF.draw = Math.round(performance.now() - t0); }
  /** 가리킴과 고름: 그 노드의 관계를 그래프 어디에 있든 끝까지 잇고, 끝 노드에 고리와 이름(작게 보일 때는 화면 크기로)을 단다 */
  function wholeOverlay(s: LensState) {
    const g = $id('whl'), V = LS.wholeView(s); if (!g || !memo.WL || !V || !s.whole.on) return; const sc = LS.wholeScale(s), near = lodTier(sc) === 'near'; let h = ''; const names: (Hit & { label: string })[] = [];
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
        h += `<text class="wname" x="${P[0][0].toFixed(1)}" y="${(P[0][1] + f * .9).toFixed(1)}" style="font-size:${f.toFixed(2)}px;stroke-width:${(3 / sc).toFixed(2)}px">${esc(memo.WL.byId.get(c.id)?.label || '')}</text>`; } }   // fs-allow: 화면 11px 이름(배율로 나눈 그림 단위)
    // 찾기에 맞은 노드: 흐린 것들 사이에서 보이게 고리를 단다(많으면 300 개까지, 화면 둘레 안만)
    if (s.query && memo.wRegion) { let k = 0; const R = memo.wRegion; for (const it0 of memo.WL.items) { if (k > 300) break; if (it0.role === 'hub') continue; const it = posOf(it0.id)!; if (it.x < R[0] || it.x > R[2] || it.y < R[1] || it.y > R[3]) continue; const n = byAny(it.id);
      if (n && matchQ(n, s.query)) { k++; h += `<circle class="wpart" cx="${it.x.toFixed(1)}" cy="${it.y.toFixed(1)}" r="${Math.max(it.r + 2.5, 3.5 / sc).toFixed(2)}"/>`; } } }
    for (const id of s.pins) { const it = posOf(id); if (!it) continue; const k = Math.max(1, 1.2 / sc);
      h += `<g class="pinmark self" transform="translate(${(it.x + it.r * .8 + 3 * k).toFixed(1)} ${(it.y - it.r * .8 - 3 * k).toFixed(1)}) scale(${k.toFixed(2)})"><circle r="6"/><svg x="-4.4" y="-4.4" width="8.8" height="8.8" viewBox="0 0 24 24">${PIN_PATHS}</svg></g>`; }
    g.innerHTML = h; }
  return { modeAt, posOf, quitWhole, showWholeCamera, drawWhole, wholeBody, wholeOverlay };
}
