// 나침반 배치와 표식: 꺾여 들어가도 층끼리 겹치지 않고, 전체 펼치기를 한 번 눌러도 하나씩 열기의 방향이 그대로이며,
// 목록 줄기는 한 색의 꼬이지 않은 도형이고, 주제 고리는 그래프 · 범례 · 사이드바가 같은 함수로 그리며,
// 전체 펼치기의 부모 선도 끝 표시(data-a/b)를 단다. 실제 view-compass.ts 를 mini-dom 위에서 마운트한다.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { installDom, El } from './mini-dom.mjs';
import { loadTs, tsUrl, copyStub, COMPONENTS_STUB } from './ts-load.mjs';

globalThis.requestAnimationFrame = () => 0;
installDom({ hash: '' });
Object.defineProperty(El.prototype, 'isConnected', { get: () => true });
globalThis.addEventListener = () => {};
globalThis.cancelAnimationFrame ??= clearTimeout;
globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} };
// 움직임 줄이기: 줄기를 한 번에 끝까지 그린다(조각 모양을 바로 읽을 수 있게)
globalThis.matchMedia = () => ({ matches: true, addEventListener() {}, removeEventListener() {} });

const THEME_STUB = `export const topicJewels = {
  light: [0xb3261e, 0x1a5fb4, 0x2e7d32, 0xb26a00, 0x6a3fa0],
  dark: [0xff8a80, 0x82b1ff, 0x69f0ae, 0xffd180, 0xce93d8] };
export const palette = { light: { background: 0xf2f2f0, surface: 0xe9e9e6, card: 0xfbfbfa, text: 0x17171a, ring: 0x4a4f8c },
  dark: { background: 0x0a0a0a, surface: 0x121212, card: 0x1a1a1a, text: 0xededeb, ring: 0x8f96d9 } };`;
const tintUrl = await tsUrl('view-tint.ts', { './theme': THEME_STUB, './area-key': await tsUrl('area-key.ts') });
const tint = await import(tintUrl);
const { mountCompass, compassDataFrom } = await loadTs('surfaces/view-compass.ts', {
  '../copy': copyStub("{ ui: { compass: { rootTopic: 'All topics' } } }"),
  '../components': COMPONENTS_STUB,
  '../view-tint': tintUrl,
  '../theme': THEME_STUB,
});

const node = (id, kind, domain) => ({ id, kind, title: id.split(':')[1], label: id.split(':')[1], domain, ts: '2026-11-01', degree: 1 });
function mount(nodes, edges = []) {
  const host = document.createElement('div');
  document.body.append(host);
  return { h: mountCompass(host, compassDataFrom(nodes, edges), {}), host };
}
const placed = (host) => host.querySelectorAll('#cmp-nodes .node').map((g) => {
  const m = /translate\(([-\d.e]+)px, ([-\d.e]+)px\)/.exec(g.getAttribute('style'));
  return { id: g.getAttribute('data-id'), role: g.getAttribute('data-role'), level: +g.getAttribute('data-level'), x: +m[1], y: +m[2] }; });

test('a path that turns at every step keeps each layer clear of the earlier ones', () => {
  // 개념 사슬 c0 → … → c5, 개념마다 다른 주제. 개념의 이웃은 9시 부채라 하나씩 열 때마다 방향이 꺾여, 넷째에 사각형이 닫혀
  // 앞 층 자리(주제 · c0 · c1)로 되돌아왔다
  const nodes = [], edges = [];
  for (let i = 0; i < 6; i++) { nodes.push(node(`concept:c${i}`, 'concept', `root/t${i}`));
    for (let j = 0; j < 6; j++) { nodes.push(node(`concept:l${i}x${j}`, 'concept', `root/t${i}`)); edges.push({ source: `concept:c${i}`, target: `concept:l${i}x${j}`, type: 'RELATED_TO' }); } }
  for (let i = 0; i < 5; i++) edges.push({ source: `concept:c${i}`, target: `concept:c${i + 1}`, type: 'RELATED_TO' });
  const { h, host } = mount(nodes, edges);
  h.open('topic:root/t0');
  for (let i = 0; i < 6; i++) h.open(`concept:c${i}`);
  assert.equal(h.centre(), 'concept:c5');
  const at = placed(host).filter((o) => !o.id.includes('@@'));
  // 다른 층의 두 노드가 한 자리(노드 지름 안)에 겹치면 안 된다
  for (let i = 0; i < at.length; i++) for (let j = i + 1; j < at.length; j++) {
    if (at[i].level === at[j].level) continue;
    const d = Math.hypot(at[i].x - at[j].x, at[i].y - at[j].y);
    assert.ok(d > 30, `${at[i].id} (layer ${at[i].level}) and ${at[j].id} (layer ${at[j].level}) are ${d.toFixed(0)} apart`);
  }
});

function areas() {
  // 크기가 아주 다른 가지들: 어림한 크기와 잰 크기가 달라 팔을 다시 고르면 방향이 바뀐다
  const nodes = [];
  const sizes = { alpha: [40, 30], beta: [2, 0], gamma: [16, 2], delta: [2, 20], eps: [30, 0], zeta: [3, 3], eta: [12, 12], theta: [1, 1] };
  for (const [a, [c, r]] of Object.entries(sizes)) for (const s of ['one', 'two', 'three']) {
    const d = `root/${a}/${s}`;
    for (let i = 0; i < c; i++) nodes.push(node(`concept:${a}-${s}-${i}`, 'concept', d));
    for (let i = 0; i < r; i++) nodes.push(node(`record:${a}-${s}-${i}`, 'record', d));
  }
  return nodes;
}
// 다각형이 스스로 꼬이지 않았는가(이웃하지 않은 두 변이 교차하지 않는다)
const pts = (s) => s.trim().split(/\s+/).map((p) => p.split(',').map(Number));
const cross = (a, b, c, d) => { const o = (p, q, r) => Math.sign((q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]));
  return o(a, b, c) * o(a, b, d) < 0 && o(c, d, a) * o(c, d, b) < 0; };
const simple = (P) => { const n = P.length;
  for (let i = 0; i < n; i++) for (let j = i + 2; j < n; j++) { if (i === 0 && j === n - 1) continue;
    if (cross(P[i], P[(i + 1) % n], P[j], P[(j + 1) % n])) return false; } return true; };

test('the list bus is one colour and none of its pieces is twisted', () => {
  const nodes = [node('concept:a', 'concept', 'root/a')];
  for (const k of ['record', 'decision', 'episode']) for (let i = 0; i < 4; i++) nodes.push(node(`${k}:${k[0]}${i}`, k, 'root/a'));
  const { h, host } = mount(nodes);
  h.open('topic:root/a');
  const polys = host.querySelectorAll('#cmp-tree polygon');
  assert.ok(polys.length > 10, 'the bus is drawn');
  const fills = new Set(polys.map((p) => /fill:([^;"]+)/.exec(p.getAttribute('style'))[1]));
  assert.ok(fills.size <= 2, `one fill for the bus (and at most one for items of another topic), got ${fills.size}`);
  for (const p of polys) { const P = pts(p.getAttribute('points')); if (P.length > 3) assert.ok(simple(P), `twisted piece: ${p.getAttribute('points').slice(0, 80)}`); }
});

test('the topic ring is the same mark in the graph, the legend and the sidebar', () => {
  const { h, host } = mount([node('concept:a', 'concept', 'root/a')]);
  h.open('topic:root/a');
  const ringOf = (html) => { const m = /<circle[^>]*stroke-dasharray="([\d.]+) ([\d.]+)"[^>]*>/.exec(html); assert.ok(m, `a dashed ring in ${html.slice(0, 120)}`);
    return { ratio: +m[1] / (+m[1] + +m[2]), butt: /stroke-linecap="butt"/.test(m[0]) }; };
  const graph = ringOf(host.querySelector('.node[data-role="hub"]').innerHTML);
  const legend = ringOf(h.mark('topic'));
  const sidebar = ringOf(tint.topicRingIcon(11, 2));
  for (const [name, r] of Object.entries({ graph, legend, sidebar })) {
    assert.ok(r.butt, `${name} ring has square ends, so its gaps show`);
    assert.ok(Math.abs(r.ratio - .55) < .02, `${name} ring dash share ${r.ratio.toFixed(2)}`);
  }
});

test('the parent lines of an opened path carry both ends so hovering a node lights them', () => {
  const nodes = []; for (const a of ['alpha', 'beta', 'gamma']) for (const s of ['one', 'two']) nodes.push(node(`concept:${a}-${s}`, 'concept', `root/${a}/${s}`));
  const { h, host } = mount(nodes);
  h.open('topic:root/beta/two');
  const routes = host.querySelectorAll('#cmp-rays .ray.path');
  assert.ok(routes.length > 0);
  for (const p of routes) { assert.ok(p.getAttribute('data-a'), 'data-a'); assert.ok(p.getAttribute('data-b'), 'data-b'); }
});
