// 나침반 배치와 표식: 꺾여 들어가도 층끼리 겹치지 않고, 전체 펼치기를 한 번 눌러도 하나씩 열기의 방향이 그대로이며,
// 목록 줄기는 한 색의 꼬이지 않은 도형이고, 주제 고리는 그래프 · 범례 · 사이드바가 같은 함수로 그리며,
// 전체 펼치기의 부모 선도 끝 표시(data-a/b)를 단다. 실제 view-compass.ts 를 mini-dom 위에서 마운트한다.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { installDom, El } from './mini-dom.mjs';
import { loadCompass, compassModules } from './helpers/compass-load.mjs';

globalThis.requestAnimationFrame = () => 0;
installDom({ hash: '' });
Object.defineProperty(El.prototype, 'isConnected', { get: () => true });
globalThis.addEventListener = () => {};
globalThis.cancelAnimationFrame ??= clearTimeout;
globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} };
// 움직임 줄이기: 줄기를 한 번에 끝까지 그린다(조각 모양을 바로 읽을 수 있게)
globalThis.matchMedia = () => ({ matches: true, addEventListener() {}, removeEventListener() {} });

const tint = await import((await compassModules()).tint);
const { mountCompass, compassDataFrom } = await loadCompass();

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

// ── 이웃 모으기와 층 하나의 내용(view-compass-gather.ts): 모델을 받아 DOM 없이 바로 부른다 ──────────────────────────
// 나침반이 쓰는 것과 같은 모듈 인스턴스다. 관계 이름표(C.rel)는 키 이름 그대로인 대역이다.
const gm = await compassModules();
const model = await import(gm.model), G = await import(gm.gather);
const RELS = Object.fromEntries(['parentTopic', 'concept', 'subtopic', 'topic', 'attachedTo', 'replacedOld', 'replacedNew', 'source', 'derived',
  'parent', 'inherited', 'next', 'prev'].map((k) => [k, k]));
/** 모델이 읽는 노드 하나(compassDataFrom 의 모양) */
const cn = (id, k, d, x = {}) => ({ id, k, t: id.split(':')[1], d, ts: '2026-11-01', g: 1, m: 0, ...x });
const built = (nodes, edges = []) => { const M = model.compassModel({ nodes, edges }, { rootTopic: 'All topics' }); return { M, ...G.compassGather(M, { rel: RELS }) }; };
const ids = (l) => l.map((u) => u.n.id);
const rels = (l) => l.map((u) => `${u.rel} ${u.n.id}`);
const S0 = (x = {}) => ({ mode: 'path', path: [], place: null, dirs: null, canon: false, koff: new Set(), more: [], pins: new Set(), pinChain: new Set(), ...x });
const close = (a, b, msg = '') => assert.ok(Math.abs(a - b) < 1e-9, `${msg} ${a} vs ${b}`);
const units = (n, k = 'concept') => Array.from({ length: n }, (_, i) => ({ n: cn(`${k}:u${i}`, k, 'lab') }));

test('fanPlace spreads a fan evenly about its middle angle and grows the radius until it fits', () => {
  const f = G.fanPlace(units(3), { x: 10, y: 20 }, -135, -45, 150, 9, () => 7);
  assert.equal(f.R, 150, 'three fit inside the minimum radius');
  close(f.pos[1].ang, -Math.PI / 2, 'the middle one sits on the middle angle'); close(f.pos[1].x, 10); close(f.pos[1].y, 20 - 150);
  close(f.pos[0].x - 10, -(f.pos[2].x - 10), 'the ends mirror each other'); close(f.pos[0].y, f.pos[2].y);
  close(f.pos[1].ang - f.pos[0].ang, 23 / 150, 'each one takes its width plus the gap on the arc');
  const big = G.fanPlace(units(40), { x: 0, y: 0 }, 0, 90, 150, 9, () => 7);
  close(big.R, (40 * 23) / (Math.PI / 2), 'many: the radius grows until they fill the span');
  close(big.pos[0].ang, 23 / big.R / 2); close(big.pos[39].ang, Math.PI / 2 - 23 / big.R / 2);
});

test('colPlace lays a vertical fan as two columns with an empty lane between them', () => {
  const c = G.colPlace(units(4), { x: 0, y: 0 }, 90, 0, () => 7);
  assert.deepEqual(c.pos.map((p) => [p.ang, p.x, p.y]), [[Math.PI, -47, 67], [Math.PI, -47, 89], [0, 47, 67], [0, 47, 89]], 'half left, half right, downward');
  assert.equal(c.R, 100);
  const up = G.colPlace(units(4), { x: 0, y: 0, r: 30 }, -90, -1, () => 12);
  assert.deepEqual(up.pos.map((p) => [p.x, p.y]), [[-52, -80], [-52, -112], [-52, -144], [-52, -176]], 'side -1: the left column only, upward, from the hub rim');
  assert.equal(up.R, 192);
  assert.ok(G.colPlace(units(3), { x: 0, y: 0 }, 90, 1, () => 7).pos.every((p) => p.ang === 0 && p.x === 47), 'side 1: the right column only');
  assert.deepEqual(G.colPlace(units(3), { x: 0, y: 0 }, 90, 0, () => 7).pos.map((p) => p.ang), [Math.PI, Math.PI, 0], 'an odd count puts the extra one on the left');
  assert.deepEqual([1, 100, 400].map((count) => G.subR({ k: 'topic', count })), [9, 19, 24], 'a subtopic grows with its count between 9 and 24');
  assert.deepEqual([cn('concept:c', 'concept', 'lab'), cn('record:r', 'record', 'lab'), cn('record:m', 'record', 'lab', { m: 2 })].map((n) => G.sizeU({ n })), [7, 5.5, 7]);
  assert.deepEqual([G.ROWS, G.REL, G.rad(180), G.todeg(Math.PI)], [18, { up: [-26, 26], left: [-135, -45], right: [45, 135] }, Math.PI, 180]);
});

/** 주제 lab/x: 개념 · 문서 · 목록 · 하위 주제 둘 · 다리 셋(하위 주제 사이 · 층 밖으로도 · 끝이 이 주제 자신) */
const topicGraph = () => built([
  cn('concept:a', 'concept', 'lab/x', { m: 2 }), cn('concept:b', 'concept', 'lab/x', { m: 1 }), cn('concept:hub', 'concept', 'lab/x', { m: 1, g: 5 }),
  cn('concept:doc.n', 'doc', 'lab/x'), cn('record:r1', 'record', 'lab/x', { ts: '2026-10-01' }), cn('record:r2', 'record', 'lab/x', { ts: '2026-11-05' }),
  cn('person:p', 'person', 'lab/x'), cn('decision:d', 'decision', 'lab/x', { ts: '2026-09-01' }), cn('episode:e', 'episode', 'lab/x'),
  cn('concept:o1', 'concept', 'lab/x/one'), cn('concept:o2', 'concept', 'lab/x/one'), cn('concept:o3', 'concept', 'lab/x/one'), cn('concept:t1', 'concept', 'lab/x/two'),
  cn('concept:y1', 'concept', 'lab/y'), cn('bridge:j', 'bridge', ''), cn('bridge:out', 'bridge', 'lab/x'), cn('bridge:self', 'bridge', 'lab/x')],
[['concept:a', 'concept:b', 'RELATED_TO'], ['bridge:j', 'concept:o1', 'CONNECTS'], ['bridge:j', 'concept:t1', 'CONNECTS'],
  ['bridge:out', 'concept:o2', 'CONNECTS'], ['bridge:out', 'concept:y1', 'CONNECTS'], ['bridge:self', 'concept:a', 'CONNECTS']]);

test('gather around a topic: parent up, its concepts left, subtopics right, bridges between subtopics, the rest in the lists', () => {
  const { M, gather } = topicGraph();
  const g = gather(M.topics.get('lab/x'));
  assert.deepEqual(g.up.map((u) => [u.n.id, u.rel, u.main]), [['topic:lab', 'parentTopic', true]]);
  assert.deepEqual(ids(g.left), ['concept:a', 'concept:hub', 'concept:b', 'concept:doc.n', 'bridge:self'], 'meaning nodes, most meaning relations first, then most links');
  assert.ok(g.left.every((u) => u.rel === 'concept'));
  assert.deepEqual(ids(g.right), ['topic:lab/x/one', 'topic:lab/x/two'], 'subtopics, the largest first');
  assert.deepEqual(g.bridges.map((b) => [b.n.id, b.subs, b.outside]), [['bridge:j', ['lab/x/one', 'lab/x/two'], false], ['bridge:out', ['lab/x/one'], true]],
    'a bridge whose ends reach a subtopic stands among the subtopics; outside says it also leaves this topic');
  assert.deepEqual(Object.fromEntries(Object.entries(g.shelf).map(([k, l]) => [k, l.map((n) => n.id)])),
    { decision: ['decision:d'], record: ['record:r2', 'person:p', 'record:r1'], episode: ['episode:e'], doc: [] }, 'newest first; an unknown kind joins the records');
  const root = gather(M.topics.get('lab'));
  assert.deepEqual(root.up, [], 'the root has no parent');
  assert.deepEqual(ids(root.right), ['topic:lab/x', 'topic:lab/y']);
});

test('gather around a node: each relation goes to its place by kind and direction', () => {
  const nodes = ['a', 'b', 'hub', 'newer', 'older', 'src', 'kid', 'par', 'heir', 'nxt', 'prv'].map((t) => cn(`concept:${t}`, 'concept', 'lab/x', { m: t === 'hub' ? 3 : 0 }));
  nodes.push(cn('record:r1', 'record', 'lab/x', { ts: '2026-10-01' }), cn('record:r2', 'record', 'lab/x', { ts: '2026-11-01' }));
  const { M, gather } = built(nodes, [['concept:a', 'concept:b', 'RELATED_TO'], ['record:r1', 'concept:a', 'ABOUT'], ['record:r2', 'concept:a', 'MENTIONS'],
    ['concept:a', 'concept:hub', 'ATTACHES_TO'], ['concept:newer', 'concept:a', 'SUPERSEDES'], ['concept:a', 'concept:older', 'SUPERSEDES'],
    ['concept:a', 'concept:src', 'DERIVES_FROM'], ['concept:kid', 'concept:a', 'DERIVES_FROM'], ['concept:a', 'concept:par', 'INHERITS_FROM'],
    ['concept:heir', 'concept:a', 'INHERITS_FROM'], ['concept:a', 'concept:nxt', 'PREREQUISITE_OF'], ['concept:prv', 'concept:a', 'PREREQUISITE_OF'],
    ['concept:hub', 'concept:a', 'RELATED_TO']]);
  const g = gather(M.byId.get('concept:a'));
  assert.deepEqual(rels(g.up), ['topic topic:lab/x', 'attachedTo concept:hub', 'replacedNew concept:newer', 'source concept:src', 'parent concept:par', 'prev concept:prv']);
  assert.equal(g.up[0].main, true, 'its topic is the main parent');
  assert.deepEqual(rels(g.right), ['replacedOld concept:older', 'derived concept:kid', 'inherited concept:heir', 'next concept:nxt']);
  assert.deepEqual(rels(g.left), ['RELATED_TO concept:hub', 'RELATED_TO concept:b'], 'meaning relations by their type, most meaning relations first');
  assert.deepEqual(g.shelf.record.map((n) => n.id), ['record:r2', 'record:r1'], 'what attaches to it is listed, newest first');
  assert.ok(g.left.every((u) => u.e && (u.e.a.id === 'concept:a' || u.e.b.id === 'concept:a')), 'each unit carries its edge');
});

test('compassOf drops the parent, filters switched-off kinds but keeps topics, the path and pins, and caps the fans', () => {
  const { M, compassOf } = topicGraph();
  const x = M.topics.get('lab/x'), r1 = M.byId.get('record:r1');
  assert.deepEqual(compassOf(x, 'topic:lab', S0()).G.up, [], 'the parent is already at 12 o\'clock');
  const off = compassOf(x, null, S0({ koff: new Set(['record', 'concept']) }));
  assert.deepEqual(off.G.shelf.record, [], 'switched-off records leave the list');
  assert.deepEqual(ids(off.G.left), ['concept:doc.n', 'bridge:self'], 'switched-off concepts leave the fan; documents and bridges stay');
  assert.deepEqual(ids(off.G.right), ['topic:lab/x/one', 'topic:lab/x/two'], 'topics are never filtered');
  assert.deepEqual(compassOf(x, null, S0({ koff: new Set(['record']), path: [r1], pins: new Set(['record:r2']) })).G.shelf.record.map((n) => n.id),
    ['record:r2', 'person:p', 'record:r1'], 'a pinned node and a node on the path stay; an unknown kind is a concept in the legend');
  assert.equal(compassOf(x, null, S0({ koff: new Set(['record']), canon: true })).G.shelf.record.length, 3, 'canon filters nothing');
  const many = Array.from({ length: 25 }, (_, i) => cn(`concept:n${i}`, 'concept', 'lab/z'));
  const big = built([cn('concept:c', 'concept', 'lab/z'), ...many], many.map((n) => ['concept:c', n.id, 'RELATED_TO']));
  const c = big.M.byId.get('concept:c');
  assert.equal(big.compassOf(c, null, S0()).G.left.length, 20, 'the left fan takes 20');
  const kept = big.compassOf(c, null, S0({ path: [big.M.byId.get('concept:n24')] })).G.left;
  assert.equal(kept.length, 20); assert.equal(kept[19].n.id, 'concept:n24', 'a node on the path past the cap stays, in its order');
});

test('compassOf measures the layer: fan radii, the compass rim and the rim with its lists', () => {
  const { M, compassOf } = topicGraph();
  const cp = compassOf(M.topics.get('lab/x'), 'topic:lab', S0()), probe = { x: 0, y: 0, r: 22 };
  assert.equal(cp.node.id, 'topic:lab/x');
  assert.equal(cp.rL, Math.max(G.fanPlace(cp.G.left, probe, 0, 90, 150, 9, G.sizeU).R, G.colPlace(cp.G.left, probe, 90, 0, G.sizeU).R));
  assert.equal(cp.rR, Math.max(G.fanPlace(cp.G.right, probe, 0, 90, 170, 40, G.sizeU).R, G.colPlace(cp.G.right, probe, 90, 0, G.sizeU).R));
  assert.equal(cp.E, Math.max(cp.rL, cp.rR, 120) + 70);
  assert.equal(cp.shelfN, 5);
  assert.equal(cp.FP, Math.max(cp.E, cp.E * .6 + 3 * 210, cp.E * .7 + 46 + 3 * 17 + 30), 'three list columns, three rows at most');
  const leaf = (n) => built(Array.from({ length: n }, (_, i) => cn(`record:r${i}`, 'record', 'lab/w')));
  const two = leaf(2), w2 = two.compassOf(two.M.topics.get('lab/w'), 'topic:lab', S0());
  assert.deepEqual([w2.rL, w2.rR, w2.E, w2.shelfN, w2.FP], [0, 0, 190, 2, 324], 'no fans: the smallest rim; one column of two rows');
  const thirty = leaf(30), w30 = thirty.compassOf(thirty.M.topics.get('lab/w'), 'topic:lab', S0());
  assert.equal(w30.FP, 190 * .7 + 46 + 18 * 17 + 30, 'a long list counts only the rows shown before it folds');
  const kids = Array.from({ length: 16 }, (_, i) => cn(`concept:k${i}`, 'concept', 'lab/v')), sibs = Array.from({ length: 20 }, (_, i) => cn(`concept:s${i}`, 'concept', 'lab/v'));
  const wide = built([cn('concept:c', 'concept', 'lab/v'), ...kids, ...sibs], [...kids.map((n) => [n.id, 'concept:c', 'DERIVES_FROM']), ...sibs.map((n) => ['concept:c', n.id, 'RELATED_TO'])]);
  const cw = wide.compassOf(wide.M.byId.get('concept:c'), null, S0());
  close(cw.rR, (16 * (2 * 7 + 40)) / (Math.PI / 2), 'a full right fan: sixteen with a 40 gap over a quarter turn');
  close(cw.rL, (20 * (2 * 7 + 9)) / (Math.PI / 2), 'a full left fan: twenty with a 9 gap over a quarter turn');
  close(cw.E, cw.rR + 70);
});

test('gather and compassOf leave the model as it was', () => {
  const { M, gather, compassOf } = topicGraph();
  const shot = () => JSON.stringify([[...M.topics.values()].map((t) => [t.id, t.count, t.ts, [...t.sub], t.direct.map((n) => n.id)]),
    M.nodes.map((n) => [n.id, n.d, n.m, n.g]), [...M.adj].map(([k, l]) => [k, l.map((e) => [e.a.id, e.b.id, e.t])])]);
  const before = shot();
  for (const n of [...M.topics.values(), ...M.nodes]) { gather(n); compassOf(n, null, S0({ koff: new Set(['record']) })); }
  assert.equal(shot(), before);
});

// ── 펼친 나무와 팔(view-compass-tree.ts): 모델 · 이웃 모으기와 그리기 쪽 손잡이(색 · 이름표 · 글자 폭 · 종류 이름)의 대역으로 DOM 없이 부른다 ──
// 자리 하나하나는 view-compass-unify.test.mjs 의 기준 자리(golden)가 지킨다. 여기서는 손잡이를 부르는 방식과 배치의 규칙을 본다.
const TR = await import(gm.tree);
const LOOK = { colv: (n) => `c:${n.id}`, titleOf: (n) => (n.k === 'topic' ? n.path.split('/').pop() || 'All topics' : n.t), GW: () => 7,
  KIND: { decision: 'Decisions', record: 'Records', episode: 'Events', doc: 'Docs' } };
const treeOf = (b, look = LOOK) => ({ ...b, ...TR.compassTree(b, look) });
const allOpen = (M) => new Map([...M.topics.values()].map((t) => [t.id, new Set([...t.sub].map((s) => `topic:${s}`))]));

test('extentPts: the points a node takes on screen, with its label, by role', () => {
  const { extentPts } = TR.compassTree({ compassOf: () => null }, LOOK);
  const it = (role, x = {}) => ({ id: 'i', role, k: 'concept', x: 100, y: 50, r: 10, label: 'abcdef', ang: 0, ...x });
  assert.deepEqual(extentPts(it('shelf')), [[70, 10], [300, 62]]);
  assert.deepEqual(extentPts(it('left', { pinned: true })), [[40, 0], [280, 100]], 'a pinned node takes its label too');
  assert.deepEqual(extentPts(it('hub', { k: 'topic' })), [[60, 30], [140, 100]], 'a topic hub: the ring and the name under it');
  assert.deepEqual(extentPts(it('hub')), [[-80, 20], [130, 90]], 'a node hub: the name to its lower left');
  assert.deepEqual(extentPts(it('right', { k: 'topic', ang: Math.PI })), [[90, 36], [110, 64], [100 - (10 + 12 + 7 * 10), 50]], 'a subtopic name runs flat, to the left when it faces left');
  assert.deepEqual(extentPts(it('left')), [[90, 40], [110, 60], [100 + 10 + 12 + 7 * 6, 50]], 'a fan name runs along its angle as long as the name');
});

test('layoutTree: the root sits at the origin facing 12 o\'clock and asks the handles for colour, label and glyph width as it places', () => {
  const calls = [], look = { ...LOOK, colv: (n) => { calls.push(['colv', n.id]); return LOOK.colv(n); }, titleOf: (n) => { calls.push(['titleOf', n.id]); return LOOK.titleOf(n); } };
  const t = treeOf(topicGraph(), look), root = t.M.topics.get('lab');
  const L = t.layoutTree(root, new Map(), root.id, S0({ path: [root] }));
  assert.equal(L.comps.length, 1); assert.equal(L.focus.hub.id, 'topic:lab');
  assert.deepEqual([L.focus.hub.x, L.focus.hub.y, L.focus.hub.r, L.focus.hub.role, L.focus.hub.level, L.focus.th12], [0, 0, 24, 'hub', 0, -90]);
  assert.equal(L.focus.hub.c, 'c:topic:lab'); assert.equal(L.focus.hub.label, 'lab');
  assert.deepEqual(L.items.map((o) => [o.id, o.role]), [['topic:lab', 'hub'], ['topic:lab/x', 'right'], ['topic:lab/y', 'right']], 'the subtopics stand at 3 o\'clock');
  assert.deepEqual(calls, L.items.flatMap((o) => [['colv', o.id], ['titleOf', o.id]]), 'each placed node asks its colour, then its label, in placing order');
});

test('layoutTree: opened layers go out breadth first, each from its slot in the parent, with the path as its chain', () => {
  const t = treeOf(topicGraph()), root = t.M.topics.get('lab'), x = t.M.topics.get('lab/x'), one = t.M.topics.get('lab/x/one');
  const L = t.layoutTree(root, new Map([['topic:lab', new Set(['topic:lab/x'])], ['topic:lab/x', new Set(['topic:lab/x/one'])]]), one.id, S0({ path: [root, x, one] }));
  assert.deepEqual(L.comps.map((c) => [c.hub.id, c.level, c.parent && c.parent.hub.id, c.focus]), [['topic:lab', 0, null, false], ['topic:lab/x', 1, 'topic:lab', false], ['topic:lab/x/one', 2, 'topic:lab/x', true]]);
  assert.deepEqual(L.comps[2].chain.map((n) => n.id), ['topic:lab', 'topic:lab/x', 'topic:lab/x/one']);
  for (const c of L.comps.slice(1)) assert.equal(((c.th12 % 90) + 90) % 90, 0, `${c.hub.id} stands on one of four directions`);
  assert.ok(L.comps[0].items.some((o) => o.link && o.link.id === 'topic:lab/x') === false && L.comps[0].slots.has('topic:lab/x'), 'an opened child leaves a slot, not a leaf');
  assert.equal(L.items.filter((o) => o.id === 'topic:lab/x').length, 1, 'a node is placed once');
  assert.equal(L.sets.length, 1, 'only the current layer lays out its lists'); assert.equal(L.sets[0].comp, L.focus);
  assert.ok(L.comps[1].stub && /Records 3/.test(L.comps[1].stub.text), 'a passed layer folds its lists into a count');
  const lx = t.layoutTree(x, new Map(), x.id, S0({ path: [x] })), lists = lx.boxes(lx.focus).at(-1), w = Math.max(lx.focus.cp.E, 3 * 105 + 60);
  close(lists[0], -w, 'the current layer reserves its three list columns below it when pushing others away'); close(lists[2], w);
});

test('layoutTree: canon places every topic without filters, and a layer cache in S.cps is reused', () => {
  const t = treeOf(topicGraph()), root = t.M.topics.get('lab'), cps = new Map();
  const S = S0({ mode: 'all', path: [root], canon: true, koff: new Set(['record']), cps });
  const A = t.layoutTree(root, allOpen(t.M), null, S);
  assert.deepEqual(A.comps.map((c) => c.hub.id), ['topic:lab', 'topic:lab/x', 'topic:lab/y', 'topic:lab/x/one', 'topic:lab/x/two']);
  assert.equal(A.focus, A.comps.at(-1), 'no focus id: the last layer placed is the current one'); assert.equal(A.sets[0].comp, A.focus);
  assert.deepEqual(A.sets.slice(1).map((st) => st.comp.hub.id), A.comps.filter((c) => c !== A.focus && c.kinds.length).map((c) => c.hub.id),
    'all mode also lays out the lists of every other layer that has them');
  assert.ok(A.items.some((o) => o.id === 'record:r1'), 'canon does not filter switched-off kinds');
  assert.deepEqual([...cps.keys()], A.comps.map((c) => `${c.hub.id}|${c.parent ? c.parent.hub.id : ''}`), 'one cached layer per placed layer, keyed by its parent');
  const first = new Map(cps); t.layoutTree(root, allOpen(t.M), null, S);
  assert.ok([...first].every(([k, v]) => cps.get(k) === v), 'the second layout reuses each layer from the cache');
});

test('crossCanon: the root keeps its place, every subtopic gets one of four arms, and given arms are kept', () => {
  const t = treeOf(built(['a', 'b', 'c', 'd', 'e'].flatMap((a) => [cn(`concept:${a}1`, 'concept', `lab/${a}`), cn(`concept:${a}2`, 'concept', `lab/${a}/in`)]))), root = t.M.topics.get('lab');
  const C0 = t.layoutTree(root, allOpen(t.M), null, S0({ mode: 'all', path: [root], canon: true }));
  const P = TR.crossCanon(C0);
  assert.deepEqual(P.out.get('topic:lab'), { x: 0, y: 0, th12: -90 });
  assert.deepEqual([...P.arms.keys()].sort(), ['topic:lab/a', 'topic:lab/b', 'topic:lab/c', 'topic:lab/d', 'topic:lab/e']);
  assert.ok([...P.arms.values()].every((i) => i >= 0 && i <= 3));
  for (const id of P.arms.keys()) { const o = P.out.get(id); assert.deepEqual(o.route[0], [0, 0], `${id} starts from the root`); assert.deepEqual(o.route.at(-1), [o.x, o.y]);
    assert.equal(o.th12, [-90, 180, 90, 0][P.arms.get(id)], `${id} faces its arm`); }
  assert.ok(P.out.has('topic:lab/a/in'), 'deeper topics are placed too');
  const fixed = new Map([...P.arms].map(([k]) => [k, 2]));
  assert.ok([...TR.crossCanon(C0, null, fixed).arms.values()].every((i) => i === 2), 'given arms are kept as they are');
  const lone = treeOf(built([cn('concept:z', 'concept', 'lab')])), r = lone.M.topics.get('lab');
  const P1 = TR.crossCanon(lone.layoutTree(r, allOpen(lone.M), null, S0({ mode: 'all', path: [r], canon: true })));
  assert.deepEqual([[...P1.out], [...P1.arms]], [[['topic:lab', { x: 0, y: 0, th12: -90 }]], []], 'no subtopics: only the root');
});

test('packPath: nothing to pack without arms or without a topic under a topic; otherwise every open topic layer gets its packed place', () => {
  const t = treeOf(topicGraph()), root = t.M.topics.get('lab'), x = t.M.topics.get('lab/x'), one = t.M.topics.get('lab/x/one');
  const P = TR.crossCanon(t.layoutTree(root, allOpen(t.M), null, S0({ mode: 'all', path: [root], canon: true })));
  const L1 = t.layoutTree(root, new Map([['topic:lab', new Set(['topic:lab/x'])]]), x.id, S0({ path: [root, x], dirs: P.out }));
  assert.equal(t.packPath(L1, null), null, 'no arms');
  assert.deepEqual([...t.packPath(L1, P.arms).keys()], ['topic:lab', 'topic:lab/x'], 'a topic layer under a topic (the root too) is packed');
  const a = t.M.byId.get('concept:a'), Ln = t.layoutTree(a, new Map([['concept:a', new Set(['concept:b'])]]), 'concept:b', S0({ path: [a, t.M.byId.get('concept:b')] }));
  assert.equal(t.packPath(Ln, P.arms), null, 'no topic layer under a topic: parent-relative placement stays');
  const ex = new Map([['topic:lab', new Set(['topic:lab/x'])], ['topic:lab/x', new Set(['topic:lab/x/one'])]]);
  const L2 = t.layoutTree(root, ex, one.id, S0({ path: [root, x, one], dirs: P.out }));
  const place = t.packPath(L2, P.arms);
  assert.deepEqual([...place.keys()], ['topic:lab', 'topic:lab/x', 'topic:lab/x/one']);
  assert.equal(place.get('topic:lab/x').th12, P.out.get('topic:lab/x').th12, 'a packed layer faces the way its arm does');
});
