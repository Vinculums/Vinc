// 나침반 전체 보기의 순수한 몫: 고리의 자리 수, 실제 크기 그래프에서 겹침이 없음, 글자 단계, 이름표 고르기, 맞춤 그림의 문턱과 그 이력.
// 배치는 view-compass-whole.ts 를 그대로 부르고, 2,500 노드 · 170 주제의 합성 그래프는 손 확인 판(app/dev)의 생성기를 쓴다.
// 글자 폭은 나침반과 같은 어림(상태 모듈의 textWidth)이다. 끝에서 실제 view-compass.ts 를 mini-dom 위에 세워 머리 단추 · 범례의 관계선 ·
// 보던 자리 잇기 · Esc 를 본다(상태 전이 자체는 view-compass-state.test.mjs).
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { loadTs } from './ts-load.mjs';

const W = await loadTs('surfaces/view-compass-whole.ts');
const LS = await loadTs('surfaces/view-compass-state.ts');
const { synthGraph } = await loadTs('../dev/compass-whole-data.ts');
const measure = (s, f) => LS.textWidth(0, s, f);
const fitScale = (L, w, h) => w / LS.fitView(L.bounds, { w, h }).w;

/** 합성 그래프 → 배치 입력(주제 나무). 나침반과 같은 갈래: 개념은 뜻, 다리는 다리, 결정 · 기록 · 사건은 목록 */
function treeOf(g) {
  const T = new Map(), get = (p) => { if (!T.has(p)) T.set(p, { id: `topic:${p}`, label: p ? p.split('/').pop() : 'All topics', count: 0, r: 14, mean: [], bridges: [], lists: [], subs: [], _l: {} }); return T.get(p); };
  get('');
  for (const n of g.nodes) { const segs = n.domain ? n.domain.split('/') : [];
    for (let i = 0; i <= segs.length; i++) { const p = segs.slice(0, i).join('/'), t = get(p); t.count++; if (i < segs.length) { const c = get(segs.slice(0, i + 1).join('/')); if (!t.subs.includes(c)) t.subs.push(c); } }
    const t = get(n.domain), x = { id: n.id, k: n.kind, label: n.label, prio: n.degree };
    if (n.kind === 'bridge') t.bridges.push(x); else if (n.kind === 'concept') t.mean.push(x);
    else { (t._l[n.kind] ||= []).push(x); } }
  for (const t of T.values()) { t.lists = Object.entries(t._l).map(([k, items]) => ({ k, name: k, items })); t.r = Math.max(9, Math.min(24, 4 + 1.5 * Math.sqrt(t.count))); }
  return get('');
}
const bbox = (P) => { let b = [Infinity, Infinity, -Infinity, -Infinity]; for (const [x, y] of P) b = [Math.min(b[0], x), Math.min(b[1], y), Math.max(b[2], x), Math.max(b[3], y)]; return b; };
/** 서로 다른 항목의 모양(노드 · 이름표 · 허브 이름 · 목록 머리) 사이 겹침 쌍을 센다(격자로 후보를 줄인다) */
function overlaps(L) {
  const shapes = [];
  for (const it of L.items) for (const p of W.itemPolys(it)) shapes.push({ who: it.id, p, b: bbox(p) });
  for (const h of L.heads) { const p = [[h.x, h.y - 11], [h.x + h.w, h.y - 11], [h.x + h.w, h.y + 3], [h.x, h.y + 3]]; shapes.push({ who: `head:${h.topic}:${h.text}`, p, b: bbox(p) }); }
  const C = 200, grid = new Map(); shapes.forEach((s, i) => { for (let x = Math.floor(s.b[0] / C); x <= Math.floor(s.b[2] / C); x++) for (let y = Math.floor(s.b[1] / C); y <= Math.floor(s.b[3] / C); y++) { const k = `${x},${y}`; if (!grid.has(k)) grid.set(k, []); grid.get(k).push(i); } });
  const bad = new Set(), pairs = [];
  for (const ids of grid.values()) for (let a = 0; a < ids.length; a++) for (let c = a + 1; c < ids.length; c++) {
    const A = shapes[ids[a]], B = shapes[ids[c]]; if (A.who === B.who) continue; const key = ids[a] < ids[c] ? `${ids[a]}|${ids[c]}` : `${ids[c]}|${ids[a]}`;
    if (bad.has(key)) continue; if (A.b[2] <= B.b[0] || B.b[2] <= A.b[0] || A.b[3] <= B.b[1] || B.b[3] <= A.b[1]) continue;
    if (W.polysOverlap(A.p, B.p)) { bad.add(key); if (pairs.length < 5) pairs.push([A.who, B.who]); } }
  return { n: bad.size, pairs };
}

test('a ring takes arc length over pitch, and outer rings take more', () => {
  const [c0] = W.ringCapacity(100, W.SECT_MEAN), [c1] = W.ringCapacity(300, W.SECT_MEAN);
  assert.equal(c0, Math.floor(130 * Math.PI / 180 * 100 / W.PITCH));
  assert.ok(c1 > 2.5 * c0, 'three times the radius holds about three times the nodes');
  const s = W.ringSlots(250, { r0: 80, step: 180, sectors: W.SECT_MEAN });
  assert.equal(s.length, 250, 'every node gets a slot: no cap');
  for (const ring of new Set(s.map((q) => q[2]))) {
    const on = s.filter((q) => q[2] === ring).sort((a, b) => a[0] - b[0]);
    for (let i = 1; i < on.length; i++) assert.ok((on[i][0] - on[i - 1][0]) * on[i][1] >= W.PITCH - 1e-6, 'neighbours on a ring stay a pitch apart');
    if (ring < Math.max(...s.map((q) => q[2]))) assert.equal(on.length, W.ringCapacity(on[0][1], W.SECT_MEAN)[0], 'an inner ring is full before the next one starts');
  }
  const b = W.ringSlots(9, { r0: 84, step: 150, sectors: W.SECT_BRIDGE });
  assert.ok(b.every(([a]) => Math.cos(a) > .3), 'bridges stand on the 3 o\'clock side');
  assert.ok(b.every(([a]) => Math.abs(Math.sin(a)) > Math.sin(13 * Math.PI / 180)), 'the 3 o\'clock axis stays free for the subtopic stem');
});

test('labels too long for a ring step are cut with one ellipsis', () => {
  const r = W.fitLabel('a'.repeat(80), measure);
  assert.ok(r.w <= W.MAXW && r.text.endsWith('…'));
  assert.equal(W.fitLabel('short', measure).text, 'short');
});

for (const [n, topics] of [[2500, 170], [800, 60]]) test(`the whole layout of ${n} nodes in ${topics} topics draws every node and nothing overlaps`, () => {
  const g = synthGraph({ nodes: n, topics, seed: 7 }), root = treeOf(g), L = W.layoutWhole(root, measure);
  const nodeItems = L.items.filter((it) => it.role !== 'hub');
  assert.equal(nodeItems.length, g.nodes.length, 'every node has a place');
  assert.equal(new Set(nodeItems.map((it) => it.id)).size, g.nodes.length, 'and only one');
  const walk = (t) => 1 + t.subs.reduce((x, c) => x + walk(c), 0);
  assert.equal(L.items.filter((it) => it.role === 'hub').length, walk(root), 'every topic at every depth has its hub');
  const o = overlaps(L);
  assert.equal(o.n, 0, `no node, label or list head overlaps another: ${JSON.stringify(o.pairs)}`);
  // 형제 칸은 상자로 떨어져 있다: 한 주제의 부채가 다른 주제의 칸으로 들어가지 않는다
  for (const t of L.topics) for (const u of L.topics) if (t !== u && !t.id.startsWith(u.id === 'topic:' ? 'topic:' : u.id + '/') && !u.id.startsWith(t.id === 'topic:' ? 'topic:' : t.id + '/')) {
    const a = t.box, b = u.box; assert.ok(a[2] <= b[0] || b[2] <= a[0] || a[3] <= b[1] || b[3] <= a[1], `${t.id} and ${u.id} do not overlap`); }
  const [x0, y0, x1, y1] = L.bounds; assert.ok(L.items.every((it) => it.x >= x0 && it.x <= x1 && it.y >= y0 && it.y <= y1));
});

for (const [n, topics, seed] of [[2500, 170, 7], [2500, 170, 3], [800, 60, 7]]) test(`far tier at the fitted zoom (${n} nodes, seed ${seed}): every node is its own mark and names clear marks and lines`, () => {
  const L = W.layoutWhole(treeOf(synthGraph({ nodes: n, topics, seed })), measure);
  for (const [vw, vh] of [[1670, 1130], [1030, 730]]) {
    const s = Math.min(vw / LS.fitView(L.bounds, { w: vw, h: vh }).w, W.RING_SEP);
    const F = W.farArrange(L, s, (id) => measure(L.byId.get(id).label, 'hub') * 12 / W.FONT.hub, { hubPx: 2.8 });
    assert.equal(F.pos.size, L.items.filter((i) => i.role !== 'hub').length, 'every node has a far place');
    assert.equal(F.fused, 0, 'no node shares a place');
    // 화면 px 로: 점(지름 dotPx) · 허브(반지름 hubPx) 사이 틈이 gapPx 이상
    const marks = [...F.pos.values()].map(([x, y]) => [x * s, y * s, F.dotPx / 2]).concat(L.topics.map((t) => [t.hub.x * s, t.hub.y * s, F.hubPx]));
    const C = 16, G = new Map(); marks.forEach((m, i) => { const k = Math.floor(m[0] / C) + ',' + Math.floor(m[1] / C); (G.get(k) || G.set(k, []).get(k)).push(i); });
    let minGap = Infinity;
    marks.forEach((m, i) => { const i0 = Math.floor(m[0] / C), j0 = Math.floor(m[1] / C); for (let a = i0 - 1; a <= i0 + 1; a++) for (let b = j0 - 1; b <= j0 + 1; b++) for (const j of G.get(a + ',' + b) || []) if (j > i) minGap = Math.min(minGap, Math.hypot(marks[j][0] - m[0], marks[j][1] - m[1]) - m[2] - marks[j][2]); });
    assert.ok(minGap >= F.gapPx - 1e-6 || minGap >= 1.9, `marks stay at least ${F.gapPx}px apart at ${vw}px (min ${minGap.toFixed(2)})`);
    assert.ok(F.dotPx >= 2, 'a mark is at least 2px');
    // 이름 자리: 표식과 줄기에 닿지 않는다
    const segs = L.topics.flatMap((t) => [...t.trunk, ...t.drops.map((d) => d.pts)]);
    for (const [, b] of F.names) {
      for (const [x, y, r] of marks) assert.ok(!(x + r > b[0] * s && x - r < b[2] * s && y + r > b[1] * s && y - r < b[3] * s), 'no mark under a name');
      for (const sg of segs) for (let k = 1; k < sg.length; k++) { const [a, c] = [sg[k - 1], sg[k]];
        assert.ok(!(Math.min(a[0], c[0]) < b[2] && Math.max(a[0], c[0]) > b[0] && Math.min(a[1], c[1]) < b[3] && Math.max(a[1], c[1]) > b[1]), 'no stem through a name'); } }
    assert.ok(F.names.size >= Math.min(30, L.topics.length / 4), `names are kept for the larger topics (${F.names.size})`);
  }
});

test('attribution decides between dots and hubs only: dots where a node reads as its topic, hubs first where it would not', () => {
  const nameW = (L) => (id) => measure(L.byId.get(id).label, 'hub') * 12 / W.FONT.hub;
  const L = W.layoutWhole(treeOf(synthGraph({ nodes: 2500, topics: 170, seed: 7 })), measure);
  // 넓은 그래프 자리: 맞춤 배율의 격자가 시험을 통과한다
  const sWide = fitScale(L, 1670, 1130), Fw = W.farArrange(L, sWide, nameW(L), { hubPx: 2.8 }), aw = W.attribution(L, Fw);
  assert.equal(aw.inOtherBlock, 0, 'no node sits in another topic\'s block');
  assert.ok(W.attributable(aw), `wide: attributable (${(aw.nearerOther / aw.n * 100).toFixed(1)}% nearer another hub, ${(aw.knnForeign * 100).toFixed(1)}% foreign neighbours)`);
  // 좁은 그래프 자리(960x600 창): 맞춤 배율의 격자는 섞이므로 허브만, 점은 더 다가간 배율부터
  const sNarrow = fitScale(L, 700, 530), P = W.farPlan(L, sNarrow, nameW(L), { hubPx: 2.8 });
  assert.equal(P.tried[0].ok, false, 'narrow: the fitted lattice fails the test');
  assert.ok(P.tried[0].a.nearerOther > W.ATTR_IN.nearerOther * P.tried[0].a.n || P.tried[0].a.knnForeign > W.ATTR_IN.knnForeign);
  assert.ok(P.sDots && P.sDots > sNarrow && P.sDots < W.RING_SEP, 'dots come back at a closer scale below the ring tier');
  const a = W.attribution(L, P.F); assert.ok(W.attributable(a) && a.inOtherBlock === 0 && a.fused === 0);
  for (const q of P.tried) assert.equal(q.a.inOtherBlock, 0, 'overflow never enters another topic\'s block');
  // 문턱은 측정 사이: 읽히는 그림(18~24%, 2~4%)과 섞이는 그림(63%+, 9~19%) 사이. 들어가는 문턱이 나가는 문턱보다 엄하다
  for (const T of [W.ATTR_IN, W.ATTR_OUT]) assert.ok(T.nearerOther > .24 && T.nearerOther < .63 && T.knnForeign > .04 && T.knnForeign < .09);
  assert.ok(W.ATTR_IN.nearerOther < W.ATTR_OUT.nearerOther && W.ATTR_IN.knnForeign < W.ATTR_OUT.knnForeign);
});

test('the fitted choice between dots and hubs has hysteresis: a slow resize across the boundary changes it once each way', () => {
  const L = W.layoutWhole(treeOf(synthGraph({ nodes: 2500, topics: 170, seed: 7 })), measure);
  const nameW = (id) => LS.textWidth(0, L.byId.get(id).label, 'name') + 2;
  // 900 높이에서 칸 폭 810~840 px(1px 씩): 맞춤 배율의 격자가 문턱 둘레를 오가며 이웃의 다른 주제 몫이 흔들리는 폭
  const widths = []; for (let w = 810; w <= 840; w += 1) widths.push(w);
  const at = new Map(widths.map((w) => [w, W.attribution(L, W.farArrange(L, fitScale(L, w, 900), nameW, { hubPx: 2.8 }))]));
  const one = (a) => W.attributable(a, W.ATTR_IN);
  let raw = 0; for (let i = 1; i < widths.length; i++) if (one(at.get(widths[i])) !== one(at.get(widths[i - 1]))) raw++;
  assert.ok(raw >= 2, `with one threshold the choice flips more than once over this range (${raw})`);
  for (const dir of [1, -1]) { const ws = dir > 0 ? widths : [...widths].reverse(); let prev = null, flips = 0;
    for (const w of ws) { const c = W.fitChoice(prev, at.get(w)); if (prev && c !== prev) flips++; prev = c; }
    assert.ok(flips <= 1, `${dir > 0 ? 'widening' : 'narrowing'} changes the fitted choice at most once (${flips})`); }
  assert.equal(W.fitChoice(null, at.get(810)), 'hubs'); assert.equal(W.fitChoice(null, at.get(840)), 'dots');
  assert.equal(W.fitChoice('dots', at.get(810)), 'dots', 'once dots, a picture inside the band stays dots');
});

test('a stem from a parent to a subtopic crosses no node or label', () => {
  const g = synthGraph({ nodes: 800, topics: 60, seed: 3 }), L = W.layoutWhole(treeOf(g), measure);
  const shapes = L.items.flatMap((it) => W.itemPolys(it).map((p) => ({ it, p })));
  const seg = (a, b) => { const h = .6, dx = b[0] - a[0], dy = b[1] - a[1], d = Math.hypot(dx, dy) || 1, nx = -dy / d * h, ny = dx / d * h;
    return [[a[0] + nx, a[1] + ny], [b[0] + nx, b[1] + ny], [b[0] - nx, b[1] - ny], [a[0] - nx, a[1] - ny]]; };
  let hits = 0;
  for (const t of L.topics) for (const path of [...t.trunk, ...t.drops.map((d) => d.pts)]) for (let i = 1; i < path.length; i++) {
    const s = seg(path[i - 1], path[i]); for (const { it, p } of shapes) if (it.role !== 'hub' && W.polysOverlap(s, p)) hits++; }
  assert.equal(hits, 0);
});

test('labels show only when they would be at least the readable size on screen', () => {
  assert.equal(W.LABEL_MIN_PX, 8);
  assert.equal(W.labelShown(W.FONT.leaf, .72), false);
  assert.equal(W.labelShown(W.FONT.leaf, .73), true);
  assert.equal(W.lodTier(.1), 'far'); assert.equal(W.lodTier(.4), 'mid'); assert.equal(W.lodTier(1), 'near');
  assert.equal(W.lodTier(8 / W.FONT.leaf), 'near', 'the threshold itself shows names');
});

test('label placement: the higher priority keeps its spot, the other takes its next candidate or none', () => {
  const box = (x, y, w = 40, h = 10) => [[x, y], [x + w, y], [x + w, y + h], [x, y + h]];
  const r = W.placeLabels([{ id: 'a', prio: 5, at: [box(0, 0)] }, { id: 'b', prio: 1, at: [box(10, 2), box(10, 30)] }, { id: 'c', prio: 0, at: [box(5, 5)] }]);
  assert.deepEqual([...r].sort(), [['a', 0], ['b', 1], ['c', -1]]);
  assert.equal(W.placeLabels([{ id: 'x', prio: 1, at: [box(0, 0)] }], [box(20, 0)]).get('x'), -1, 'a blocking mark wins over a label');
  assert.equal(W.polysOverlap(box(0, 0), box(40, 0)), false, 'touching is not overlapping');
});

test('a pointer finds the nearest item within reach', () => {
  const its = [{ id: 'a', x: 0, y: 0, r: 5 }, { id: 'b', x: 300, y: 10, r: 5 }];
  const ix = W.gridIndex(its);
  assert.equal(ix.near(296, 12, 6).id, 'b');
  assert.equal(ix.near(150, 0, 6), null);
});

test('the fitted camera holds the whole box at the screen aspect', () => {
  const v = LS.fitView([-100, -50, 900, 450], { w: 1600, h: 1000 }, 24);
  assert.ok(v.x <= -100 && v.y <= -50 && v.x + v.w >= 900 && v.y + v.h >= 450);
  assert.ok(Math.abs(v.w / v.h - 1.6) < 1e-9);
});

// ── 마운트: 실제 view-compass.ts 를 mini-dom 위에 세워 머리 단추 · 보던 자리 잇기 · 사이드바 고르기 · Esc 를 본다 ──
import { installDom, El } from './mini-dom.mjs';
import { loadCompass, compassModules } from './helpers/compass-load.mjs';
installDom({ hash: '' });
Object.defineProperty(El.prototype, 'isConnected', { get: () => true });
const winOn = [];
globalThis.addEventListener = (type, fn) => winOn.push([type, fn]);
globalThis.cancelAnimationFrame ??= clearTimeout;
globalThis.requestAnimationFrame ??= (f) => setTimeout(() => f(0), 0);
globalThis.matchMedia ??= () => ({ matches: true, addEventListener() {}, removeEventListener() {} });
const { mountCompass, compassDataFrom } = await loadCompass({ copy: "{ ui: { compass: { rootTopic: 'All topics', whole: 'Whole graph', rels: 'Relations', wholeLabel: (n, t) => `whole ${n} ${t}` } } }" });
const SPEC = [['work/alpha', 'a'], ['work/alpha', 'a2'], ['work/zebra', 'b'], ['research', 'c'], ['research/deep', 'd', 'record']];
function mountW(ctx = {}) {
  const nodes = SPEC.map(([domain, t, kind = 'concept']) => ({ id: `${kind}:${t}`, kind, title: t, label: t, domain, ts: '2026-11-01', degree: 1 }));
  const host = document.createElement('div'); document.body.append(host);
  const h = mountCompass(host, compassDataFrom(nodes, [{ source: 'concept:a', target: 'concept:c', type: 'RELATED_TO' }]), ctx);
  return { h, host, btn: h.tools.querySelector('[data-whole]'), legend: h.tools.querySelector('[data-pop="legend"]') };
}
const key = (k) => { for (const [t, f] of winOn) if (t === 'keydown') f({ key: k, target: { closest: () => null } }); };

test('the header control switches to the whole view, says so, and every node is drawn there', () => {
  winOn.length = 0;
  const { h, host, btn, legend } = mountW();
  assert.equal(btn.getAttribute('aria-pressed'), 'false', 'the layer view is still the default');
  assert.equal(h.tools.querySelectorAll('button').length, 3, 'the header gains one control: time, legend, whole graph');
  legend.click(); assert.equal(document.querySelectorAll('[data-rels]').length, 0, 'the relations control is not in the layer view legend'); legend.click();
  btn.click();
  assert.equal(btn.getAttribute('aria-pressed'), 'true');
  legend.click(); const rels = document.querySelectorAll('[data-rels]');
  assert.equal(rels.length, 1, 'the whole view legend carries the relations control'); assert.equal(rels[0].getAttribute('aria-pressed'), 'false');
  rels[0].click(); assert.ok(h.snapshot().whole.rels, 'pressing it draws the topic pair lines'); legend.click();
  assert.equal(h.snapshot().mode, 'whole');
  assert.ok(host.querySelector('#cmp-g').classList.contains('whole'));
  for (const [, t, k = 'concept'] of SPEC) assert.ok(host.innerHTML.includes(`data-id="${k}:${t}"`), `${k}:${t} is drawn in the whole view`);
  for (const p of ['topic:', 'topic:work', 'topic:work/alpha', 'topic:work/zebra', 'topic:research', 'topic:research/deep']) assert.ok(host.innerHTML.includes(`data-id="${p}"`), `${p} has its hub`);
  key('Escape');
  assert.equal(h.snapshot().mode, 'whole', 'Escape in the whole view does not leave it');
  btn.click();
  const snap = h.snapshot();
  assert.equal(snap.mode, 'path', 'pressing again returns to the layer view');
  assert.ok(snap.whole && snap.whole.cam, 'the whole camera is kept for the way back');
  h.destroy();
});

test('a remount restores the whole view, and a sidebar pick selects without leaving it', () => {
  winOn.length = 0;
  const a = mountW(); a.btn.click(); const snap = a.h.snapshot(); a.h.destroy();
  winOn.length = 0;
  const b = mountW({ restore: snap });
  assert.equal(b.h.snapshot().mode, 'whole', 'the saved view reopens as the whole view');
  assert.equal(b.btn.getAttribute('aria-pressed'), 'true');
  b.h.open('concept:c');
  assert.equal(b.h.centre(), 'concept:c', 'the picked node is the selection (the sidebar highlights it)');
  assert.equal(b.h.snapshot().mode, 'whole');
  b.h.destroy();
  winOn.length = 0;
  const old = mountW({ restore: { ...snap, mode: 'all' } });
  assert.equal(old.h.snapshot().mode, 'path', "a view saved with the old 'all' still opens one layer at a time");
  old.h.destroy();
});

test('Escape returns to the whole view only from the layer entered from it, and repeated toggles do not grow history', () => {
  winOn.length = 0;
  const { h, btn } = mountW();
  btn.click(); btn.click();                                   // 전체 → 층(전체에서 바로 들어온 층)
  key('Escape');
  assert.equal(h.snapshot().mode, 'whole', 'Escape from the layer entered from the whole view returns to it');
  btn.click(); h.open('concept:c');                           // 층에서 한 걸음 더 들어간다
  key('Escape');
  assert.equal(h.snapshot().mode, 'path', 'one step further in, Escape goes to the root layer as before');
  assert.equal(h.centre(), 'topic:');
  const n0 = h.snapshot().hist.length;
  for (let i = 0; i < 30; i++) btn.click();
  assert.ok(h.snapshot().hist.length <= n0 + 2, `30 presses add at most two history entries (${h.snapshot().hist.length - n0})`);
  h.destroy();
});

test('search filters the tree but does not dim the layer view drawing; the whole view dims non-matches', () => {
  winOn.length = 0;
  const { h, host, btn } = mountW();
  h.filter('zzz-nothing');
  assert.equal(host.querySelectorAll('#cmp-nodes .node.out').length, 0, 'layer view: nothing dims on search');
  btn.click();
  assert.ok(host.querySelectorAll('#cmp-nodes .node.out').length > 0, 'whole view: non-matching nodes dim');
  h.destroy();
});

// ── 전체 보기 그리기(view-compass-whole-draw.ts): 실제 모델과 배치로 기억(memo)을 채우고, 마운트 쪽 손잡이(효과 · 색 · 섞기 · 글자 폭)는 대역으로 부른다 ──
const gm = await compassModules();
const WD = await import(gm.wholeDraw), MD = await import(gm.model), SH = await import(gm.shapes);
const DRAW_C = { lens: 'lens', wholeLabel: (n, t) => `whole ${n} ${t}`, wholeHubsOnly: 'hubs only' };
/** 그리기 하나: 모델(SPEC + 관계), 그 배치를 든 기억, 손잡이 대역이 부른 효과의 기록, 렌즈 · 머리 도구 · 풍선 요소 */
function drawKit({ spec = SPEC, edges = [['concept:a', 'concept:c', 'RELATED_TO']], size = { w: 800, h: 600 } } = {}) {
  const nodes = spec.map(([d, t, k = 'concept', x = {}]) => ({ id: `${k}:${t}`, k, t, d, ts: '2026-11-01', g: 1, m: 0, ...x }));
  const M = MD.compassModel({ nodes, edges }, { rootTopic: 'All topics' });
  const tree = (tp) => ({ id: tp.id, label: tp.path ? tp.path.split('/').pop() : 'All topics', count: tp.count, r: 14,
    mean: tp.direct.filter((n) => n.k === 'concept').map((n) => ({ id: n.id, k: n.k, label: n.t, prio: 0 })), bridges: [],
    lists: tp.direct.some((n) => n.k === 'record') ? [{ k: 'record', name: 'Records', items: tp.direct.filter((n) => n.k === 'record').map((n) => ({ id: n.id, k: n.k, label: n.t, prio: 0 })) }] : [],
    subs: [...tp.sub].sort().map((p) => tree(M.topics.get(p))) });
  const L = W.layoutWhole(tree(M.topics.get(M.ROOT)), measure);
  const memo = WD.renderMemo(); memo.WL = { ...L, ix: W.gridIndex(L.items) };
  const host = document.createElement('div');
  host.innerHTML = '<div class="cmp"><div class="cmp-stage"><svg class="g lod1 moving hot" id="cmp-g" aria-label="lens"></svg></div><div id="cmp-tip"></div></div><span class="cmp-tools"><button data-whole aria-pressed="false"></button></span>';
  const root = host.querySelector('.cmp'), svg = host.querySelector('#cmp-g'), tip = host.querySelector('#cmp-tip'), tools = host.querySelector('.cmp-tools');
  const fxLog = [], fx = { frame: (k, f) => fxLog.push(['frame', k, f]), cancelFrame: (k) => fxLog.push(['cancelFrame', k]), after: (k, ms, f) => fxLog.push(['after', k, ms, f]),
    cancelAfter: (k) => fxLog.push(['cancelAfter', k]), cancelIdle: (k) => fxLog.push(['cancelIdle', k]) };
  const look = { C: DRAW_C, fx, svg, root, tools, tip, $id: (k) => root.querySelector(k === 'g' ? '#cmp-g' : `#cmp-${k.replace(/^cmp-/, '')}`), titleOf: (n) => n.t || n.path || 'All topics',
    colv: (n) => `c(${n.id})`, mix: (c, p) => `${c}/${p}`, topicColour: (p) => `t(${p})`, ringS: (r, c, w) => `<ring r="${r}" c="${c}" w="${w}"/>`,
    matchQ: (n, q) => !q || String(n.t || n.path || n.id).toLowerCase().includes(q) || n.id.includes(q), tw: (t, k) => measure(t, k) };
  let s = LS.wholeLaidOut(LS.toggleWhole(LS.initialState('dark', size)), L.bounds);
  return { M, L, memo, host, root, svg, tip, tools, fxLog, look, s, ...WD.compassWholeDraw(M, memo, look) };
}
const fxNames = (log) => log.map(([t, k]) => `${t}:${k}`);
const plan = (sc, x = {}) => ({ key: 'k', sFit: sc, far: true, fit: 'dots', sDots: sc, pending: false, ...x });

test('whole draw: the memo starts empty, and what is drawn at a scale follows the tier or the far plan', () => {
  assert.deepEqual(WD.renderMemo(), { prevPos: new Map(), WL: null, PLANF: null, WPERF: { layout: 0, draw: 0 }, wMode: '', wTier: '', wScale: 0, wRegion: null, wFilt: '', wVC: null, panEl: null, live: null });
  assert.notEqual(WD.renderMemo().prevPos, WD.renderMemo().prevPos, 'a fresh memo per mount');
  const k = drawKit();
  for (const sc of [W.RING_SEP, W.RING_SEP * 2, 50]) assert.equal(k.modeAt(k.s, sc), W.lodTier(sc), `at ${sc} the tier decides`);
  const far = W.RING_SEP / 2;
  assert.equal(k.modeAt(k.s, far), 'hubs', 'far without a plan: hubs only');
  const sp = LS.wholePlanned(k.s, plan(far));
  assert.equal(k.modeAt(sp, far), 'hubs', 'a plan with dots but no grid yet: hubs');
  k.memo.PLANF = { key: 'k', F: { pos: new Map(), dotPx: 3 } };
  assert.equal(k.modeAt(sp, far), 'dots', 'the plan\'s dot scale reached: dots');
  assert.equal(k.modeAt(sp, far * .9995), 'dots', 'within the rounding margin');
  assert.equal(k.modeAt(sp, far * .998), 'hubs', 'just past it: hubs');
  assert.equal(k.modeAt(LS.wholePlanned(k.s, plan(far, { sDots: null })), far), 'hubs');
});

test('whole draw: where a node is drawn depends on the drawn mode: its ring, its grid dot, or its hub at screen size', () => {
  const k = drawKit(), a = k.memo.WL.byId.get('concept:a'), hub = k.memo.WL.byId.get('topic:work');
  assert.equal(k.posOf('concept:nowhere'), null);
  assert.equal(k.posOf('concept:a'), a, 'no mode yet: the ring position itself');
  k.memo.wMode = 'mid'; k.memo.wScale = 2;
  assert.equal(k.posOf('topic:work'), hub, 'mid: hubs as laid out');
  k.memo.wMode = 'hubs'; assert.deepEqual(k.posOf('topic:work'), { ...hub, r: 2 }, 'hubs: a hub is 4 px on screen');
  k.memo.PLANF = { key: 'k', F: { pos: new Map([['concept:a', [7, 9]]]), dotPx: 3 } };
  k.memo.wMode = 'dots';
  assert.deepEqual(k.posOf('topic:work'), { ...hub, r: 1.4 }, 'dots: a hub is 2.8 px on screen');
  assert.deepEqual(k.posOf('concept:a'), { x: 7, y: 9, r: .75, topic: a.topic, role: a.role, label: a.label, id: 'concept:a' }, 'dots: the grid place, half the dot on screen');
  assert.equal(k.posOf('concept:c'), k.memo.WL.byId.get('concept:c'), 'a node without a grid place keeps its ring position');
});

test('whole draw: drawWhole lays the three layers into the lens, frames the camera and names the view', () => {
  const k = drawKit();
  k.svg.before = (el) => { const sib = k.svg.parent.children; sib.splice(sib.indexOf(k.svg), 0, el); el.parent = k.svg.parent; };
  k.drawWhole(k.s);
  assert.deepEqual(fxNames(k.fxLog), ['cancelFrame:fly', 'cancelFrame:flip', 'cancelAfter:flip', 'cancelFrame:grow', 'cancelAfter:wcommit'], 'the layer view\'s camera, motion and stems stop first');
  assert.equal(k.svg.getAttribute('class'), 'g whole');
  assert.deepEqual(k.svg.children.map((c) => c.getAttribute('id') || c.tagName), ['DEFS', 'cmp-wstat', 'cmp-wstem', 'cmp-wbun', 'cmp-wsp', 'cmp-nodes', 'cmp-wlab', 'cmp-whl']);
  const V = LS.wholeView(k.s), sc = LS.wholeScale(k.s);
  assert.equal(k.svg.getAttribute('viewBox'), LS.viewBoxAttr(V)); assert.deepEqual(k.memo.wVC, V);
  assert.equal(k.svg.getAttribute('tabindex'), '0'); assert.equal(k.svg.getAttribute('aria-label'), 'whole 5 6', 'the label counts nodes and topics');
  assert.equal(k.tools.querySelector('[data-whole]').getAttribute('aria-pressed'), 'true');
  assert.equal(k.memo.panEl.getAttribute('class'), 'cmp-pan'); assert.equal(k.svg.parent, k.memo.panEl, 'the gesture layer wraps the drawing');
  assert.equal(k.memo.live.getAttribute('aria-live'), 'polite'); assert.equal(k.root.querySelector('.cmp-note').getAttribute('role'), 'status');
  assert.deepEqual([k.memo.wTier, k.memo.wMode, k.memo.wScale, k.memo.wFilt], [W.lodTier(sc), W.lodTier(sc), sc, 'null|']);
  assert.deepEqual(k.memo.wRegion, [V.x - V.w * .5, V.y - V.h * .5, V.x + V.w * 1.5, V.y + V.h * 1.5], 'the body covers half a screen around the view');
  const drawn = k.svg.querySelectorAll('#cmp-nodes .node').map((g) => g.getAttribute('data-id'));
  for (const id of ['concept:a', 'concept:a2', 'concept:b', 'concept:c', 'record:d', 'topic:', 'topic:work', 'topic:work/alpha', 'topic:research/deep']) assert.ok(drawn.includes(id), `${id} is drawn`);
  assert.match(k.svg.querySelector('#cmp-nodes .node[data-id="concept:a"]').innerHTML, /fill="t\(work\/alpha\)\/55"/, 'a node takes its topic colour, faded when not fresh');
  k.drawWhole(k.s);
  assert.equal(k.root.querySelectorAll('.cmp-live').length, 1, 'the whole view\'s own elements are made once');
});

test('whole draw: the body dims what is outside the period or the search, draws the topic pair lines on request, and says when only hubs are drawn', () => {
  const k = drawKit({ spec: [['work/alpha', 'a', 'concept', { ct: '2026-10-01' }], ['work/alpha', 'a2'], ['work/zebra', 'b', 'concept', { ct: '2026-11-05' }], ['research', 'c']],
    edges: [['concept:a', 'concept:c', 'RELATED_TO'], ['concept:b', 'concept:c', 'CONNECTS'], ['concept:a2', 'concept:c', 'ABOUT']] });
  const out = () => k.svg.querySelectorAll('#cmp-nodes .node.out').map((g) => g.getAttribute('data-id')).sort();
  k.drawWhole(LS.setRange(k.s, ['2026-11-01', '2026-11-30']));
  assert.deepEqual(out(), ['concept:a'], 'a dated node outside the period dims; undated ones do not');
  assert.equal(k.memo.wFilt, '2026-11-01,2026-11-30|');
  k.wholeBody(LS.setQuery(k.s, 'b'));
  assert.deepEqual(out(), ['concept:a', 'concept:a2', 'concept:c'], 'the search dims what does not match');
  assert.equal(k.svg.querySelector('#cmp-wbun').innerHTML, '', 'no pair lines unless asked');
  k.wholeBody(LS.toggleRels(k.s));
  assert.match(k.svg.querySelector('#cmp-wbun').innerHTML, new RegExp(`^<path class="wbun" d="M[^"]+" style="stroke:t\\(research\\)/70;stroke-width:${(1 / LS.wholeScale(k.s)).toFixed(2)}"><title>research · work/alpha · 1</title></path>$`),
    'one line for the one meaning relation across topics (bridges and attachments are not pair lines), one screen px wide for one relation');
  const kf = drawKit({ size: { w: 90, h: 60 } });   // 아주 작은 칸: 맞춤 배율이 고리를 가를 수 없을 만큼 작다
  kf.drawWhole(kf.s);
  assert.equal(kf.memo.wMode, 'hubs'); assert.equal(kf.memo.wTier, 'far');
  assert.deepEqual(kf.svg.querySelectorAll('#cmp-nodes .node').map((g) => g.getAttribute('data-role')).filter((r) => r !== 'hub'), [], 'hubs only');
  const note = kf.root.querySelector('.cmp-note'); assert.equal(note.getAttribute('hidden'), null); assert.equal(note.textContent, 'hubs only');
  assert.ok(kf.svg.classList.contains('far'));
  kf.wholeBody({ ...kf.s, size: { w: 800, h: 600 } }); assert.ok(!kf.svg.classList.contains('far')); assert.equal(note.textContent, ''); assert.equal(note.getAttribute('hidden'), '');
  assert.equal(typeof k.memo.WPERF.draw, 'number');
  k.wholeBody(LS.toggleWhole(k.s)); assert.equal(k.memo.wTier, W.lodTier(LS.wholeScale(k.s)), 'out of the whole view the body is not drawn');
});

test('whole draw: the overlay draws the picked node\'s relations to wherever their ends are, the hovered one, pins and search hits', () => {
  const k = drawKit();
  k.drawWhole(k.s);
  const whl = () => k.svg.querySelector('#cmp-whl').innerHTML, a = k.posOf('concept:a'), c = k.posOf('concept:c');
  k.wholeOverlay(LS.wholeSelect(k.s, 'concept:a'));
  assert.ok(whl().startsWith(`<line class="wl cross" x1="${a.x.toFixed(1)}" y1="${a.y.toFixed(1)}" x2="${c.x.toFixed(1)}" y2="${c.y.toFixed(1)}"></line><circle class="wpart" cx="${c.x.toFixed(1)}"`), 'a line to the other end, across topics');
  assert.match(whl(), /<circle class="wsel on" [^>]*><\/circle>$/, 'a ring on the pick');
  k.wholeOverlay(LS.wholeHover(k.s, 'concept:c'));
  assert.match(whl(), /<circle class="wsel" /, 'a hovered node gets a ring that is not the pick');
  k.wholeOverlay({ ...k.s, pins: ['concept:b'] });
  assert.ok(whl().includes('<g class="pinmark self"') && whl().includes(SH.PIN_PATHS.slice(0, 40)), 'a pin mark at the pinned node');
  k.wholeOverlay(LS.setQuery(k.s, 'a2'));
  const a2 = k.posOf('concept:a2');
  assert.match(whl(), new RegExp(`^<circle class="wpart" cx="${a2.x.toFixed(1)}" cy="${a2.y.toFixed(1)}"`), 'a search hit gets a ring');
  const before = whl(); k.wholeOverlay(LS.toggleWhole(k.s)); assert.equal(whl(), before, 'not in the whole view: left as it was');
});

test('whole draw: leaving forgets what the whole view drew and puts the lens back as the layer view; the camera can be placed again', () => {
  const k = drawKit();
  k.quitWhole();
  assert.deepEqual(k.fxLog, [], 'never entered: nothing to undo');
  k.svg.before = (el) => { const sib = k.svg.parent.children; sib.splice(sib.indexOf(k.svg), 0, el); el.parent = k.svg.parent; };
  k.drawWhole(k.s); k.memo.prevPos = new Map([['concept:a', { x: 1, y: 2 }]]); k.memo.panEl.style.transform = 'scale(2)'; k.memo.live.textContent = 'Concept a';
  k.tip.style.display = 'block'; k.svg.classList.add('far'); k.svg.style.cursor = 'grab'; k.fxLog.length = 0;
  k.quitWhole();
  assert.deepEqual(fxNames(k.fxLog), ['cancelIdle:wplan', 'cancelAfter:wcommit', 'cancelAfter:wresize', 'cancelFrame:whover', 'cancelFrame:wcross', 'cancelAfter:wcross', 'cancelAfter:peek']);
  assert.deepEqual([k.memo.wVC, k.memo.wMode, k.memo.wTier, k.memo.wRegion, k.memo.wFilt, k.memo.prevPos.size], [null, '', '', null, '', 0]);
  assert.equal(k.memo.panEl.style.transform, '');
  assert.equal(k.svg.getAttribute('class'), 'g'); assert.equal(k.svg.getAttribute('tabindex'), null); assert.equal(k.svg.getAttribute('aria-label'), 'lens'); assert.equal(k.svg.style.cursor, '');
  assert.equal(k.tip.style.display, 'none'); assert.equal(k.memo.live.textContent, ''); assert.equal(k.root.querySelector('.cmp-note').getAttribute('hidden'), '');
  assert.equal(k.tools.querySelector('[data-whole]').getAttribute('aria-pressed'), 'false');
  k.fxLog.length = 0; k.quitWhole(); assert.deepEqual(k.fxLog, [], 'once left, leaving again does nothing');
  k.memo.panEl.style.transform = 'scale(3)';
  const moved = LS.wholeFrame(k.s, 10, 20, 2); k.showWholeCamera(moved);
  assert.deepEqual(fxNames(k.fxLog), ['cancelAfter:wcommit']); assert.deepEqual(k.memo.wVC, LS.wholeView(moved)); assert.equal(k.memo.panEl.style.transform, '');
  assert.equal(k.svg.getAttribute('viewBox'), LS.viewBoxAttr(LS.wholeView(moved)));
  k.fxLog.length = 0; k.showWholeCamera(LS.initialState('dark')); assert.deepEqual(k.fxLog, [], 'no whole camera: nothing');
});

test('a period picked in the whole view dims the nodes outside it at once', () => {
  winOn.length = 0;
  const nodes = [['work/alpha', 'a', '2026-10-01'], ['work/zebra', 'b', '2026-11-05'], ['research', 'c', '2026-11-04']].map(([domain, t, ct]) =>
    ({ id: `concept:${t}`, kind: 'concept', title: t, label: t, domain, ts: '2026-11-01', content_ts: ct, degree: 1 }));
  const host = document.createElement('div'); document.body.append(host);
  const h = mountCompass(host, compassDataFrom(nodes, []), {});
  h.tools.querySelector('[data-whole]').click();
  const out = () => host.querySelectorAll('#cmp-nodes .node.out').map((g) => g.getAttribute('data-id'));
  assert.deepEqual(out(), []);
  h.tools.querySelector('[data-pop="time"]').click();
  document.querySelector('[data-cmp-r="7"]').click();
  assert.deepEqual(out(), ['concept:a'], 'the last seven days leave a out');
  h.tools.querySelector('[data-pop="time"]').click();
  h.destroy();
});
