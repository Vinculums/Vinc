// 나침반의 한 배치: 하나씩 열기와 전체 펼치기가 같은 묶음 배치를 쓴다. 주제마다 방향이 두 모드에서 같고, 꺾여 들어가거나
// 다리를 거쳐 들어가도 그려진 노드 · 이름표가 다른 층과 닿지 않으며, 층의 크기를 재는 동안 화면 상태(모드 · 길 · 거르기)를
// 바꾸지 않고, 층 사이 거리가 하나씩 열기에서 읽을 만한 범위에 있다. 실제 view-compass.ts 를 mini-dom 위에서 마운트하고,
// 배치 결과(LZ)를 읽으려고 그 한 줄만 전역으로 내보낸다.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { installDom, El } from './mini-dom.mjs';
import { tsUrl, srcText, transpile, dataUrl, copyStub, COMPONENTS_STUB } from './ts-load.mjs';

globalThis.requestAnimationFrame = () => 0;
installDom({ hash: '' });
Object.defineProperty(El.prototype, 'isConnected', { get: () => true });
globalThis.addEventListener = () => {};
globalThis.cancelAnimationFrame ??= clearTimeout;
globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} };
globalThis.matchMedia ??= () => ({ matches: false, addEventListener() {}, removeEventListener() {} });

const THEME_STUB = `export const topicJewels = {
  light: [0xb3261e, 0x1a5fb4, 0x2e7d32, 0xb26a00, 0x6a3fa0],
  dark: [0xff8a80, 0x82b1ff, 0x69f0ae, 0xffd180, 0xce93d8] };`;
const tintUrl = await tsUrl('view-tint.ts', { './theme': THEME_STUB, './area-key': await tsUrl('area-key.ts') });
let js = transpile(await srcText('surfaces/view-compass.ts'));
const HOOK = 'LZ = { hub: L.focus.hub, ...L };';
assert.ok(js.includes(HOOK), 'render still keeps the last layout in LZ');
js = js.replace(HOOK, 'LZ = globalThis.__cmpLZ = { hub: L.focus.hub, ...L };');
for (const [spec, code] of Object.entries({ '../copy': copyStub("{ ui: { compass: { rootTopic: 'All topics' } } }"), '../components': COMPONENTS_STUB, '../view-tint': tintUrl, '../theme': THEME_STUB }))
  js = js.split(JSON.stringify(spec)).join(JSON.stringify(code.startsWith('data:') ? code : dataUrl(code)));
const { mountCompass, compassDataFrom } = await import(dataUrl(js));
const LZ = () => globalThis.__cmpLZ;
const idle = () => new Promise((r) => setTimeout(r, 400));   // 마운트 뒤 한가할 때 팔을 재는 것(requestIdleCallback 이 없으면 300ms)을 기다린다

// 작은 그래프: 영역 다섯 · 하위 주제 셋, 이름 길이가 여러 가지인 개념, 목록, 개념 사이 관계, 영역을 건너 잇는 다리
function smallGraph(seed) {
  let s = seed; const rnd = () => (s = (s * 1664525 + 1013904223) % 4294967296) / 4294967296;
  const words = ['ledger', 'review', 'gate', 'policy', 'harness', 'routing', 'quota', 'cache', 'replica', 'token', 'audit', 'session'];
  const nodes = [], edges = [], con = [];
  for (const a of ['alpha', 'beta', 'gamma', 'delta', 'eps']) for (const t of ['one', 'two', 'three']) {
    const d = `root/${a}/${t}`;
    for (let i = 0; i < 9; i++) { const w = Array.from({ length: 2 + Math.floor(rnd() * 4) }, () => words[Math.floor(rnd() * words.length)]).join(' ');
      const id = `concept:${a}-${t}-${i}`; nodes.push({ id, kind: 'concept', title: `${w} ${i}`, label: `${w} ${i}`, domain: d, ts: '2026-11-01', degree: 1 }); con.push(id); }
    for (const [k, n] of [['record', 5], ['decision', 3], ['episode', 2]]) for (let i = 0; i < n; i++)
      nodes.push({ id: `${k}:${a}-${t}-${i}`, kind: k, title: `${a} ${t} ${k} about the ${words[i % words.length]} ${i}`, label: `${k} ${i}`, domain: d, ts: '2026-11-01', degree: 1 });
  }
  for (let i = 0; i < 160; i++) { const x = con[Math.floor(rnd() * con.length)], y = con[Math.floor(rnd() * con.length)]; if (x !== y) edges.push({ source: x, target: y, type: 'RELATED_TO' }); }
  for (let i = 0; i < 60; i++) { const r = nodes.filter((n) => n.kind !== 'concept')[Math.floor(rnd() * 150)], c = con[Math.floor(rnd() * con.length)]; if (r) edges.push({ source: r.id, target: c, type: 'ABOUT' }); }
  for (let b = 0; b < 4; b++) { const id = `bridge:link-${b}`; nodes.push({ id, kind: 'bridge', title: `Link ${b} junction`, label: `Link ${b}`, domain: '', ts: '2026-11-01', degree: 1 });
    for (let k = 0; k < 3; k++) edges.push({ source: id, target: con[Math.floor(rnd() * con.length)], type: 'CONNECTS' }); }
  return { nodes, edges };
}
function mount(seed, ctx = {}) {
  const host = document.createElement('div'); document.body.append(host);
  const G = smallGraph(seed); return { h: mountCompass(host, compassDataFrom(G.nodes, G.edges), ctx), host };
}

// 그려진 모양: 노드 원과 이름표를 캡슐(선분 + 두께)로. 부채 이름표는 실제 글자 방향(o.ang)으로, 하위 주제 이름 · 목록 · 다리 · 허브 이름표는
// 그려지는 자리(가로, 아래 가운데, 허브 왼쪽 아래)로 잡는다. 글자 폭 6.6, 글자 높이의 절반 6
const W = 6.6, TH = 6;
function shapes(o) {
  const c = [[o.x, o.y, o.x, o.y, o.r + 3]], n = (o.label || '').length, along = (a, l0, l1) => [o.x + Math.cos(a) * l0, o.y + Math.sin(a) * l0, o.x + Math.cos(a) * l1, o.y + Math.sin(a) * l1, TH];
  if (o.role === 'hub') {
    if (o.k === 'topic') { const hw = W / 2 * Math.min(28, n); c.push([o.x - hw, o.y + o.r + 16, o.x + hw, o.y + o.r + 16, TH]); }
    else { const x1 = o.x - o.r * .7 - 8; c.push([x1 - W * Math.min(28, n), o.y + o.r + 12, x1, o.y + o.r + 12, TH]); }
  } else if (o.role === 'bridge') { const hw = W / 2 * Math.min(20, n); c.push([o.x - hw, o.y + o.r + 9, o.x + hw, o.y + o.r + 9, TH]); }
  else if (o.role === 'right' && o.k === 'topic') { const s = Math.cos(o.ang ?? 0) < 0 ? -1 : 1; c.push([o.x + s * (o.r + 7), o.y, o.x + s * (o.r + 7 + W * (Math.min(18, n) + 4)), o.y, TH]); }
  else if (['up', 'left', 'right'].includes(o.role)) c.push(along(o.ang ?? 0, o.r + 7, o.r + 7 + W * Math.min(o.farEnds ? 40 : 24, n)));
  else c.push([o.x + o.r + 7, o.y, o.x + o.r + 7 + W * Math.min(24, n), o.y, TH]);
  return c;
}
function segDist([a0, a1, a2, a3], [b0, b1, b2, b3]) {
  const pd = (px, py, x0, y0, x1, y1) => { const vx = x1 - x0, vy = y1 - y0, L = vx * vx + vy * vy, t = L ? Math.max(0, Math.min(1, ((px - x0) * vx + (py - y0) * vy) / L)) : 0; return Math.hypot(px - x0 - vx * t, py - y0 - vy * t); };
  const cr = (ax, ay, bx, by, cx, cy) => (bx - ax) * (cy - ay) - (by - ay) * (cx - ax);
  const d1 = cr(a0, a1, a2, a3, b0, b1), d2 = cr(a0, a1, a2, a3, b2, b3), d3 = cr(b0, b1, b2, b3, a0, a1), d4 = cr(b0, b1, b2, b3, a2, a3);
  if (d1 * d2 < 0 && d3 * d4 < 0) return 0;
  return Math.min(pd(a0, a1, b0, b1, b2, b3), pd(a2, a3, b0, b1, b2, b3), pd(b0, b1, a0, a1, a2, a3), pd(b2, b3, a0, a1, a2, a3)); }
// 층이 다른 두 노드의 모양이 닿는 쌍
function touching(L) {
  const lv = new Map(L.comps.map((c, i) => [c.hub.id, i])), it = L.items.map((o) => ({ o, l: o.role === 'hub' ? lv.get(o.id) : lv.get(o.comp), S: shapes(o) })).filter((q) => q.l != null), out = [];
  for (let i = 0; i < it.length; i++) for (let j = i + 1; j < it.length; j++) { const A = it[i], B = it[j];
    if (A.l !== B.l && A.S.some((a) => B.S.some((b) => segDist(a, b) < a[4] + b[4] - 1))) out.push(`${A.o.id} ~ ${B.o.id}`); }
  return out; }
const th12 = () => new Map(LZ().comps.filter((c) => c.hub.k === 'topic').map((c) => [c.hub.id, ((c.th12 % 360) + 360) % 360]));
const step = (h, ids) => { h.open('topic:root'); for (const id of ids) { h.open(id); assert.equal(h.centre(), id, `${id} opens`); } };

test('a topic faces the same way opened one at a time and in All, and again after toggling All', async () => {
  const { h, host } = mount(3); await idle();
  const ids = ['topic:root/alpha/one', 'topic:root/beta/two', 'topic:root/gamma/three', 'topic:root/delta/one', 'topic:root/eps/two'];
  const seen = new Map();
  for (const id of ids) { h.open(id); for (const [k, v] of th12()) seen.set(k, v); }
  h.tools.querySelector('[data-all]').click();
  const all = th12();
  for (const [k, v] of seen) assert.equal(v, all.get(k), `${k} faces ${v} one at a time and ${all.get(k)} in All`);
  h.tools.querySelector('[data-all]').click();
  for (const id of ids) { h.open(id); for (const [k, v] of th12()) assert.equal(v, all.get(k), `${k} after toggling All`); }
});

test('a path through a bridge draws no node or label over another layer', async () => {
  const { h } = mount(4); await idle();
  // 다리에서 개념으로, 다시 다른 영역의 개념으로 꺾어 들어간다. 지금 층의 목록이 맨 위 주제의 부채에 닿던 자리다
  step(h, ['bridge:link-2', 'concept:gamma-three-8', 'concept:gamma-one-5']);
  assert.deepEqual(touching(LZ()), []);
});

test('a path that turns draws no node or label over another layer', async () => {
  const { h } = mount(4); await idle();
  // 주제 둘을 내려간 뒤 개념으로 꺾고, 그 개념의 이웃으로 한 번 더 꺾는다
  step(h, ['topic:root/alpha', 'topic:root/alpha/one', 'concept:alpha-one-3']);
  const next = LZ().items.find((o) => (o.role === 'up' || o.role === 'left') && o.comp === 'concept:alpha-one-3' && !o.id.includes('@@'));
  assert.ok(next, 'the concept layer has a neighbour to open'); h.open(next.id);
  const hubs = LZ().comps.map((c) => c.hub), turns = new Set(hubs.slice(1).map((o, i) => (Math.abs(o.x - hubs[i].x) > Math.abs(o.y - hubs[i].y) ? 'h' : 'v')));
  assert.equal(turns.size, 2, 'the path turns');
  assert.deepEqual(touching(LZ()), [], 'turned path');
});

test('measuring layer sizes leaves the mode, the open path and the filters as they were', async () => {
  const { h: first } = mount(5);
  first.open('concept:beta-two-4');
  const restore = { ...first.snapshot(), filt: [['topic:root/beta/two', ['left']]], koff: ['episode'] };
  // 처음 한 번: 한가할 때 잴 틈 없이 전체 펼치기를 눌러 그 자리에서 잰다
  const { h } = mount(5, { restore }); const before = h.snapshot();
  const keep = (s) => ({ path: s.path, filt: s.filt, koff: s.koff, tsel: s.tsel, query: s.query });
  const allBtn = h.tools.querySelector('[data-all]');
  allBtn.click(); assert.equal(h.snapshot().mode, 'all');
  assert.deepEqual(keep(h.snapshot()), keep(before), 'measuring on the first All press');
  allBtn.click(); assert.equal(h.snapshot().mode, 'path');
  assert.deepEqual(keep(h.snapshot()), keep(before), 'back to one at a time');
  // 둘째: 마운트 뒤 한가할 때 재기
  const { h: h2 } = mount(5, { restore }); const b2 = h2.snapshot(); await idle();
  assert.deepEqual(h2.snapshot(), b2, 'the idle measure changes nothing the snapshot keeps');
});

test('layers opened one at a time sit at a readable distance from their parent', async () => {
  const { h } = mount(3); await idle();
  for (const seq of [['topic:root/beta', 'topic:root/beta/two'], ['topic:root/delta', 'topic:root/delta/one', 'concept:delta-one-2'], ['bridge:link-1']]) {
    step(h, seq);
    for (const c of LZ().comps.slice(1)) { const p = c.parent.hub, d = Math.hypot(c.hub.x - p.x, c.hub.y - p.y);
      assert.ok(d >= 300 && d <= 1600, `${c.hub.id} is ${Math.round(d)} from ${p.id}`); }
  }
});
