// 나침반 뷰어의 버그 고침을 잡는다: 다시 그려도 보던 자리를 잇고, 긴 목록 너머의 노드와 목록(6시)의 노드도 가운데로 열리며,
// 고정은 스페이스마다 따로이고 깨진 저장값에 무너지지 않고, 바깥이 처리한 Esc 를 다시 처리하지 않는다.
// 실제 view-compass.ts 를 mini-dom 위에서 마운트한다.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { installDom, El } from './mini-dom.mjs';
import { loadCompass, compassModules } from './helpers/compass-load.mjs';

globalThis.requestAnimationFrame = () => 0;
installDom({ hash: '' });
Object.defineProperty(El.prototype, 'isConnected', { get: () => true });
const winOn = [];
globalThis.addEventListener = (type, fn) => winOn.push([type, fn]);
globalThis.cancelAnimationFrame ??= clearTimeout;
globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} };
globalThis.matchMedia ??= () => ({ matches: false, addEventListener() {}, removeEventListener() {} });

const { mountCompass, compassDataFrom } = await loadCompass();

const node = (id, kind, domain, extra = {}) => ({ id, kind, title: id, label: id, domain, ts: '2026-11-01', degree: 1, ...extra });
function mount(nodes, edges = [], ctx = {}) {
  const host = document.createElement('div');
  document.body.append(host);
  return { h: mountCompass(host, compassDataFrom(nodes, edges), ctx), host };
}
const hubId = (host) => host.querySelector('.node.sel')?.getAttribute('data-id') || null;
const winKey = (key, extra = {}) => { const ev = { key, target: document.body, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...extra };
  for (const [t, fn] of winOn) if (t === 'keydown') fn(ev); return ev; };

test('a concept past the first twenty of its topic still opens as the centre', () => {
  const nodes = []; for (let i = 0; i < 25; i++) nodes.push(node(`concept:c${i}`, 'concept', 'root/a'));
  const { h, host } = mount(nodes);
  h.open('concept:c24');
  assert.equal(h.centre(), 'concept:c24');
  assert.equal(hubId(host), 'concept:c24', 'the drawn centre is the node that was opened, not its topic');
});

test('every subtopic is drawn, even past sixteen, and each one can be opened', () => {
  const nodes = []; for (let i = 0; i < 20; i++) nodes.push(node(`concept:t${i}`, 'concept', `root/t${i}`));
  const { h, host } = mount(nodes);
  h.open('topic:root');
  const drawn = new Set(host.querySelectorAll('.node').map((g) => g.getAttribute('data-id')));
  for (let i = 0; i < 20; i++) assert.ok(drawn.has(`topic:root/t${i}`), `topic:root/t${i} is drawn`);
  h.open('topic:root/t19');
  assert.equal(hubId(host), 'topic:root/t19');
});

test('a record from the six o\'clock list opens as the centre', () => {
  const { h, host } = mount([node('concept:a', 'concept', 'root/a'), node('record:r1', 'record', 'root/a')]);
  h.open('record:r1');
  assert.equal(hubId(host), 'record:r1');
});

test('a snapshot carries the open path and the filters into a new mount', () => {
  const nodes = []; for (const d of ['root/a', 'root/b']) for (let i = 0; i < 3; i++) nodes.push(node(`concept:${d}-${i}`, 'concept', d));
  const first = mount(nodes);
  first.h.open('concept:root/b-1');
  const snap = first.h.snapshot();
  first.h.destroy();
  assert.deepEqual(snap.path.at(-1), 'concept:root/b-1');
  const second = mount(nodes, [], { restore: snap });
  assert.equal(second.h.centre(), 'concept:root/b-1', 'the new mount opens where the old one was');
  // 전체 보기가 있던 때 저장된 자리(mode: 'all')도 하나씩 열기로 연다
  const old = mount(nodes, [], { restore: { ...snap, mode: 'all' } });
  assert.equal(old.h.centre(), 'concept:root/b-1'); assert.equal(old.h.snapshot().mode, 'path');
  // 새 데이터에 없는 노드로 된 자리는 버리고 뿌리에서 시작한다
  const third = mount([node('concept:x', 'concept', 'root/x')], [], { restore: snap });
  assert.notEqual(third.h.centre(), 'concept:root/b-1');
});

test('pins are kept per space and a broken stored value does not stop the mount', () => {
  localStorage.setItem('vinc.pins:me|', '{not json');
  localStorage.setItem('vinc.pins:me|team-1', JSON.stringify(['concept:p']));
  const a = mount([node('concept:p', 'concept', 'root/a')], [], { store: 'me|' });
  assert.equal(a.h.centre(), 'topic:root', 'mounts with an empty pin list');
  const b = mount([node('concept:p', 'concept', 'root/a')], [], { store: 'me|team-1' });
  assert.ok(b.h.snapshot(), 'mounts with the team pins');
  assert.equal(localStorage.getItem('vinc.pins:me|team-1'), JSON.stringify(['concept:p']), 'the team pins are untouched');
});

test('the graph has no All button: it opens one layer at a time', () => {
  const { h } = mount([node('concept:a', 'concept', 'root/a'), node('concept:b', 'concept', 'root/b')]);
  assert.equal(h.tools.querySelector('[data-all]'), null);
  assert.equal(h.snapshot().mode, 'path');
});

test('pressing a node opens it at once: no expand menu, no drag handles, no edge markers for pins', () => {
  const { h, host } = mount([node('concept:a', 'concept', 'root/a'), node('concept:b', 'concept', 'root/b')]);
  h.open('topic:root/a');
  const g = host.querySelector('.node[data-id="concept:a"]');
  assert.ok(g, 'the concept is drawn in the open layer');
  assert.equal(host.querySelector('#cmp-menu'), null, 'no expand menu in the page');
  assert.equal(host.querySelector('.pedge'), null, 'no pin edge markers');
  h.open('concept:a'); assert.equal(h.centre(), 'concept:a');
  assert.deepEqual(h.snapshot().filt, [], 'no per-node branch filter is kept');
});

test('pressing the middle again asks the view to open its details, pressing another node only unfolds it', () => {
  const opened = [];
  const { h, host } = mount([node('concept:a', 'concept', 'root/a'), node('concept:b', 'concept', 'root/a')], [], { onOpen: (id) => opened.push(id) });
  h.open('topic:root/a');
  host.querySelector('.node[data-id="concept:a"]').click();
  assert.equal(h.centre(), 'concept:a', 'the first press unfolds the node');
  assert.deepEqual(opened, [], 'no details yet');
  host.querySelector('.node.sel').click();
  assert.deepEqual(opened, ['concept:a'], 'the second press on the middle opens its details');
  assert.equal(h.centre(), 'concept:a', 'the compass stays where it is');
  h.open('topic:root/a'); host.querySelector('.node.sel').click();
  assert.deepEqual(opened, ['concept:a', 'topic:root/a'], 'a topic in the middle opens too');
});

test('the side summary names the middle and follows a directed relation as one chain until it branches', () => {
  const ids = ['a', 'b', 'c', 'd', 'e'], nodes = ids.map((x) => node(`concept:${x}`, 'concept', 'root/x'));
  const P = (a, b) => ({ source: `concept:${a}`, target: `concept:${b}`, type: 'PREREQUISITE_OF' });
  const { h } = mount(nodes, [P('a', 'b'), P('b', 'c'), P('c', 'd'), P('c', 'e')]);
  h.open('concept:a');
  const sum = () => h.side.querySelector('#cmp-sum');
  assert.ok(sum().querySelector('.sh b'), 'the summary names the middle');
  const row = sum().querySelector('[data-follow="PREREQUISITE_OF|out"]');
  assert.ok(row, 'a row for what comes next');
  row.click();
  assert.equal(h.centre(), 'concept:c', 'followed a → b → c and stopped where it branches');
  assert.deepEqual(h.snapshot().path.slice(-3), ['concept:a', 'concept:b', 'concept:c'], 'the chain is on the open path');
  sum().querySelector('[data-follow="PREREQUISITE_OF|out"]').click();   // 갈래가 둘이면 고르게 편다
  const pick = sum().querySelector('[data-go="concept:e"][data-chain]');
  assert.ok(pick, 'both branches are listed'); pick.click();
  assert.equal(h.centre(), 'concept:e');
});

test('the side summary says what replaced a node', () => {
  const { h } = mount([node('decision:old', 'decision', 'root/x'), node('decision:new', 'decision', 'root/x')],
    [{ source: 'decision:new', target: 'decision:old', type: 'SUPERSEDES' }]);
  h.open('decision:old');
  const s = h.side.querySelector('#cmp-sum');
  assert.ok(s.querySelector('[data-go="decision:new"]'), 'the newer decision is linked from the older one');
  h.open('decision:new');
  assert.ok(h.side.querySelector('#cmp-sum [data-follow="SUPERSEDES|out"]'), 'the newer one can follow what it replaced');
});

test('the summary opens the shortest path from the middle to a pinned node as one chain', () => {
  localStorage.setItem('vinc.pins:me|path', JSON.stringify(['concept:e']));
  const ids = ['a', 'b', 'c', 'd', 'e'], nodes = ids.map((x) => node(`concept:${x}`, 'concept', 'root/x'));
  const R = (a, b) => ({ source: `concept:${a}`, target: `concept:${b}`, type: 'RELATED_TO' });
  const { h } = mount(nodes, [R('a', 'b'), R('b', 'c'), R('c', 'e'), R('a', 'd'), R('d', 'b')], { store: 'me|path' });
  h.open('concept:a');
  const btn = h.side.querySelector('#cmp-sum [data-path="concept:e"]');
  assert.ok(btn, 'a path button for the pinned node'); btn.click();
  assert.equal(h.centre(), 'concept:e');
  assert.deepEqual(h.snapshot().path.slice(-4), ['concept:a', 'concept:b', 'concept:c', 'concept:e'], 'a → b → c → e, the shortest');
  // 이어지지 않으면 그렇다고 말한다
  localStorage.setItem('vinc.pins:me|nopath', JSON.stringify(['concept:z']));
  const two = mount([node('concept:y', 'concept', 'root/x'), node('concept:z', 'concept', 'root/x')], [], { store: 'me|nopath' });
  two.h.open('concept:y'); two.h.side.querySelector('#cmp-sum [data-path="concept:z"]').click();
  assert.equal(two.h.centre(), 'concept:y'); assert.ok(two.h.side.querySelector('#cmp-sum .sr'), 'says there is no path');
});

test('resting on a node grows its tip into a peek card without moving the compass', async () => {
  const R = (a, b) => ({ source: `concept:${a}`, target: `concept:${b}`, type: 'DERIVES_FROM' });
  const { h, host } = mount(['a', 'b', 'c'].map((x) => node(`concept:${x}`, 'concept', 'root/x')), [R('a', 'b'), R('b', 'c')]);
  h.open('concept:a');
  const g = host.querySelector('.node[data-id="concept:b"]'); assert.ok(g);
  g.fire('pointerover');
  const tip = host.querySelector('#cmp-tip');
  assert.ok(!String(tip.innerHTML).includes('peek'), 'the plain tip first');
  await new Promise((r) => setTimeout(r, 520));
  assert.ok(String(tip.innerHTML).includes('peek'), 'then the peek card');
  assert.equal(h.centre(), 'concept:a', 'the compass did not move');
});

test('an Escape the view already handled does not also send the compass back to the root', () => {
  winOn.length = 0;
  const { h } = mount([node('concept:a', 'concept', 'root/a')]);
  h.open('concept:a');
  winKey('Escape', { defaultPrevented: true });
  assert.equal(h.centre(), 'concept:a');
  winKey('Escape');
  assert.equal(h.centre(), 'topic:root');
});

test('cancelling the idle measure does not clear an unrelated timer with the same number', () => {
  // requestIdleCallback 과 setTimeout 은 번호를 따로 센다. 유휴 측정의 번호를 clearTimeout 에 넘기면 같은 번호의 남의 타이머가 지워진다
  const IDLE = 4242, cleared = [], cancelled = [];
  const saved = [globalThis.requestIdleCallback, globalThis.cancelIdleCallback, globalThis.clearTimeout];
  globalThis.requestIdleCallback = () => IDLE;
  globalThis.cancelIdleCallback = (id) => cancelled.push(id);
  globalThis.clearTimeout = (id) => { cleared.push(id); saved[2](id); };
  try {
    const nodes = []; for (const d of ['idle/a', 'idle/b', 'idle/c']) for (let i = 0; i < 2; i++) nodes.push(node(`concept:${d}-${i}`, 'concept', d));
    const { h, host } = mount(nodes);
    h.open('concept:idle/b-1');
    h.destroy();
    assert.ok(cancelled.includes(IDLE), 'the idle measure is cancelled through cancelIdleCallback');
    assert.ok(!cleared.includes(IDLE), 'clearTimeout never receives the idle callback number');
  } finally { [globalThis.requestIdleCallback, globalThis.cancelIdleCallback, globalThis.clearTimeout] = saved; }
});

// ── 가운데 요약과 엿보기(view-compass-side.ts): 모델은 실제 모듈, 마운트 쪽 손잡이(문구 · 종류 이름 · 이름표 · 색 · 열린 길)는 대역으로 DOM 없이 부른다 ──
const gm = await compassModules();
const model = await import(gm.model), SD = await import(gm.side), VT = await import(gm.tint), SH = await import(gm.shapes);
const cn = (id, k, d, x = {}) => ({ id, k, t: id.split(':')[1], d, ts: '2026-11-01', g: 1, m: 0, ...x });
const SUM = { latest: (d) => `latest ${d}`, empty: 'empty', subtopics: (n) => `${n} subtopics`, mostLinked: 'Most linked', holds: (a) => `holds ${a}`,
  held: (a, b) => `held ${a}~${b}`, replacedBy: 'Replaced by', followTip: 'follow', related: (n) => `Related ${n}`, attached: 'Attached', details: 'Details',
  pathTo: 'Path to', noPath: 'No path', rel: Object.fromEntries(['pre', 'der', 'inh', 'sup', 'ret'].map((k) => [k, { in: `${k}<in`, out: `${k}>out` }])) };
const KINDS = { concept: 'Concept', doc: 'Doc', decision: 'Decision', record: 'Record', episode: 'Event', bridge: 'Bridge', topic: 'Topic' };
const short = (s, n) => (s.length > n ? s.slice(0, n - 1) + '…' : s);
function side(nodes, edges, { onOpen } = {}) {
  const M = model.compassModel({ nodes, edges }, { rootTopic: 'All topics' }), sum = { innerHTML: '' }, paths = [];
  const look = { C: { sum: SUM, rel: { topic: 'Topic' } }, KIND: KINDS, titleOf: (n) => (n.k === 'topic' ? n.path.split('/').pop() || 'All topics' : n.t), short,
    colv: (n) => `c:${n.id}`, pathOf: (s) => { paths.push(s.path); return s.path.map(M.byAny); }, ctx: onOpen ? { onOpen } : {} };
  return { M, sum, paths, ...SD.compassSide(M, sum, look) };
}
const S = (x = {}) => ({ path: [], whole: { on: false, sel: '' }, sumOpen: [], pins: [], noPath: null, ...x });
const chainIds = (l) => l.map((n) => n.id);
// 이 주제(lab/x)의 개념 다섯과 결정 둘, 다른 영역(lab/y)의 개념 둘(p 는 아무와도 잇지 않는다), 다리 하나, 붙은 기록 · 사건
const NODES = [cn('concept:a', 'concept', 'lab/x', { g: 9 }), cn('concept:b', 'concept', 'lab/x', { g: 5 }), cn('concept:c', 'concept', 'lab/x', { g: 3 }),
  cn('concept:d', 'concept', 'lab/x', { g: 2 }), cn('concept:e', 'concept', 'lab/x', { g: 1, ct: '2026-11-07' }), cn('concept:f', 'concept', 'lab/y', { g: 4 }),
  cn('concept:p', 'concept', 'lab/y'), cn('bridge:j', 'bridge', 'lab/x', { g: 3.5 }), cn('record:r1', 'record', 'lab/x', { ct: '2026-11-03' }),
  cn('record:r2', 'record', 'lab/x'), cn('episode:ep', 'episode', 'lab/x', { ts: '2026-11-05' }),
  cn('decision:new', 'decision', 'lab/x', { vf: '2026-11-01' }), cn('decision:old', 'decision', 'lab/x', { vf: '2026-09-01', vu: '2026-11-01T00:00:00Z' })];
const EDGES = [['concept:a', 'concept:b', 'PREREQUISITE_OF'], ['concept:b', 'concept:c', 'PREREQUISITE_OF'], ['concept:c', 'concept:d', 'PREREQUISITE_OF'],
  ['concept:c', 'concept:e', 'PREREQUISITE_OF'], ['concept:b', 'concept:f', 'DERIVES_FROM'], ['concept:e', 'concept:a', 'INHERITS_FROM'],
  ['concept:d', 'concept:a', 'RETURNS_TO'], ['concept:a', 'concept:f', 'RELATED_TO'], ['bridge:j', 'concept:a', 'CONNECTS'],
  ['record:r1', 'concept:a', 'ABOUT'], ['episode:ep', 'concept:a', 'ATTACHES_TO'], ['decision:new', 'concept:a', 'ABOUT'], ['concept:a', 'record:r2', 'MENTIONS'],
  ['decision:new', 'decision:old', 'SUPERSEDES']];
const chain = (n, from = 0) => Array.from({ length: n }, (_, i) => cn(`concept:q${from + i}`, 'concept', 'lab/z'));
const links = (n, type = 'PREREQUISITE_OF') => Array.from({ length: n - 1 }, (_, i) => [`concept:q${i}`, `concept:q${i + 1}`, type]);

test('side: following a relation goes one way while it does not branch, and stops at a branch, a loop or twelve steps', () => {
  const t = side([...NODES, ...chain(15), cn('concept:l1', 'concept', 'lab/z'), cn('concept:l2', 'concept', 'lab/z'), cn('concept:l3', 'concept', 'lab/z')],
    [...EDGES, ...links(15), ['concept:l1', 'concept:l2', 'PREREQUISITE_OF'], ['concept:l2', 'concept:l3', 'PREREQUISITE_OF'], ['concept:l3', 'concept:l1', 'PREREQUISITE_OF']]);
  const n = (id) => t.M.byAny(id);
  assert.deepEqual(chainIds(t.chainFrom(n('concept:a'), 'PREREQUISITE_OF', 'out', n('concept:b'))), ['concept:b', 'concept:c'], 'stops where c branches to d and e');
  assert.deepEqual(chainIds(t.chainFrom(n('concept:c'), 'PREREQUISITE_OF', 'in', n('concept:b'))), ['concept:b', 'concept:a'], 'the other way round');
  assert.deepEqual(chainIds(t.chainFrom(n('concept:l1'), 'PREREQUISITE_OF', 'out', n('concept:l2'))), ['concept:l2', 'concept:l3'], 'stops before it comes back');
  assert.deepEqual(chainIds(t.chainFrom(n('concept:q0'), 'PREREQUISITE_OF', 'out', n('concept:q1'))), chain(12, 1).map((x) => x.id), 'twelve at most');
  assert.deepEqual(chainIds(t.chainFrom(n('concept:a'), 'DERIVES_FROM', 'out', null)), [], 'nothing to follow');
  assert.deepEqual(t.RELT, [['PREREQUISITE_OF', 'pre'], ['DERIVES_FROM', 'der'], ['INHERITS_FROM', 'inh'], ['SUPERSEDES', 'sup'], ['RETURNS_TO', 'ret']]);
});

test('side: the shortest path takes the fewest steps along any relation either way, and gives up past ten steps or when apart', () => {
  const t = side([...NODES, ...chain(15)], [...EDGES, ...links(15, 'RELATED_TO')]), n = (id) => t.M.byAny(id);
  assert.deepEqual(chainIds(t.shortest(n('concept:d'), n('concept:f'))), ['concept:d', 'concept:a', 'concept:f'], 'd returns to a, a is related to f');
  assert.deepEqual(chainIds(t.shortest(n('concept:f'), n('concept:c'))), ['concept:f', 'concept:b', 'concept:c'], 'against the arrows too');
  assert.equal(t.shortest(n('concept:a'), n('concept:p')), null, 'p is not linked');
  assert.equal(t.shortest(n('concept:q0'), n('concept:q10')).length, 11, 'ten steps is still a path');
  assert.equal(t.shortest(n('concept:q0'), n('concept:q11')), null, 'eleven is not');
  assert.equal(t.shortest(n('concept:q0'), n('concept:q3'), 2), null, 'the limit is a parameter');
});

test('side: the peek card counts each directed relation under its mark, the related and the attached, and names the four most linked', () => {
  const t = side(NODES, EDGES), n = (id) => t.M.byAny(id);
  assert.equal(t.peekHTML(n('concept:a')), '<div class="peek"><div class="pr">› 1 · |› 1 · ↩ 1 · Related 3 · Attached 3</div>'
    + '<span class="pn">b</span><span class="pn">f</span><span class="pn">j</span><span class="pn">d</span></div>');
  assert.equal(t.peekHTML(n('concept:b')), '<div class="peek"><div class="pr">› 2 · ∘› 1</div><span class="pn">a</span><span class="pn">f</span><span class="pn">c</span></div>');
  assert.equal(t.peekHTML(n('decision:new')), '<div class="peek"><div class="pr">⇢ 1 · Related 1</div><span class="pn">a</span></div>', 'what it is about counts as related');
  assert.equal(t.peekHTML(n('concept:p')), '<div class="peek"></div>', 'nothing to count');
  assert.equal(t.peekHTML(t.M.topics.get('lab/x')), '<div class="peek"><div class="pr">Concept 5 · Bridge 1 · Record 2 · Event 1 · Decision 2</div></div>');
  assert.equal(t.peekHTML(t.M.topics.get('lab')), '<div class="peek"><div class="pr">Concept 7 · Bridge 1 · Record 2 · Event 1 · Decision 2 · 2 subtopics</div></div>', 'a topic counts below it too');
  assert.equal(t.peekHTML(null), '');
});

test('side: the icon is the topic ring or the kind\'s shape in the node\'s colour, as an outline when the node is closed', () => {
  const t = side(NODES, EDGES), n = (id) => t.M.byAny(id);
  assert.equal(t.ico(t.M.topics.get('lab/x')), `<svg viewBox="-7 -7 14 14">${VT.topicRing(5, 'c:topic:lab/x', 1.8)}</svg>`);
  assert.equal(t.ico(n('decision:new')), `<svg viewBox="-7 -7 14 14">${SH.shape('decision', 0, 0, 5, 'c:decision:new', false)}</svg>`);
  assert.equal(t.ico(n('decision:old')), `<svg viewBox="-7 -7 14 14">${SH.shape('decision', 0, 0, 5, 'c:decision:old', true)}</svg>`, 'closed: an outline');
});

test('side: the summary describes the middle of the open path, and in the whole view the picked node or the root', () => {
  const t = side(NODES, EDGES);
  assert.equal(t.sumNode(S({ path: ['topic:lab', 'concept:a'] })).id, 'concept:a');
  assert.deepEqual(chainIds(t.sumPath(S({ path: ['topic:lab', 'concept:a'] }))), ['topic:lab', 'concept:a']);
  assert.deepEqual(t.paths, [['topic:lab', 'concept:a']], 'the open path comes from the mount');
  assert.equal(t.sumNode(S({ path: ['topic:lab', 'concept:a'], whole: { on: true, sel: '' } })).id, 'topic:lab', 'the whole view with nothing picked: the root');
  const w = S({ path: ['topic:lab'], whole: { on: true, sel: 'concept:a' } });
  assert.equal(t.sumNode(w).id, 'concept:a');
  assert.deepEqual(chainIds(t.sumPath(w)), ['topic:lab', 'topic:lab/x', 'concept:a'], 'the whole view: the picked node\'s topic chain');
  assert.equal(t.paths.length, 1, 'not the open path');
});

test('side: a topic summary names it, counts its kinds, gives the latest date and the five most linked', () => {
  const t = side(NODES, EDGES);
  t.drawSum(S({ path: ['topic:lab', 'topic:lab/x'] }));
  const nb = (id, title) => `<button class="sn" data-go="${id}" title="${title}" >${t.ico(t.M.byAny(id))}<span>${title}</span></button>`;
  assert.equal(t.sum.innerHTML, `<div class="sh">${t.ico(t.M.topics.get('lab/x'))}<b>x</b></div><div class="sk">Topic · lab/x · latest 2026-11-07</div>`
    + '<div class="sr">Concept 5 · Bridge 1 · Record 2 · Event 1 · Decision 2</div><div class="sl">Most linked</div>'
    + nb('concept:a', 'a') + nb('concept:b', 'b') + nb('bridge:j', 'j') + nb('concept:c', 'c') + nb('concept:d', 'd'));
  t.drawSum(S({ path: ['topic:lab'] }));
  assert.match(t.sum.innerHTML, /<b>lab<\/b><\/div><div class="sk">Topic · lab · latest 2026-11-07<\/div><div class="sr">Concept 7 · Bridge 1 · Record 2 · Event 1 · Decision 2 · 2 subtopics<\/div>/);
});

test('side: a node summary gives kind, topic and date, a row per directed relation, the related, the attached and a details button', () => {
  const opened = [], t = side(NODES, EDGES, { onOpen: (id) => opened.push(id) }), html = () => t.sum.innerHTML;
  t.drawSum(S({ path: ['topic:lab', 'topic:lab/x', 'concept:c'] }));
  assert.match(html(), /^<div class="sh">.*<b>c<\/b><\/div><div class="sk">Concept · lab\/x<\/div>/, 'a concept without a date: kind and topic');
  t.drawSum(S({ path: ['concept:e'] }));
  assert.match(html(), /<div class="sk">Concept · lab\/x · 2026-11-07<\/div>/, 'with its date');
  t.drawSum(S({ path: ['topic:lab', 'topic:lab/x', 'concept:c'] }));
  assert.match(html(), /<div class="sf"><button class="sfr" data-follow="PREREQUISITE_OF\|in" title="follow"><i class="sg sg-pre"><\/i><span>pre&lt;in<\/span><b>1<\/b><em>›<\/em><\/button><\/div><div class="sfl"><button class="sn" data-go="concept:b"[^>]* data-chain="PREREQUISITE_OF\|in">/,
    'one before it: the row and its one node, ready to follow');
  assert.match(html(), /<div class="sf"><button class="sfr" data-follow="PREREQUISITE_OF\|out" [^>]*>.*<b>2<\/b><em>›<\/em><\/button><button class="sfx" data-sx="pre:out" aria-expanded="false">▸<\/button><\/div>(?!<div class="sfl">)/,
    'two after it: the row and a fold button, the list folded');
  assert.match(html(), /<button class="btn btn-sm sdet" data-sopen="1">Details<\/button>$/);
  t.drawSum(S({ path: ['concept:c'], sumOpen: ['pre:out'] }));
  assert.match(html(), /data-sx="pre:out" aria-expanded="true">▾<\/button><\/div><div class="sfl"><button class="sn" data-go="concept:d"[^>]*data-chain="PREREQUISITE_OF\|out">.*data-go="concept:e"/, 'unfolded: both listed');
  t.drawSum(S({ path: ['concept:a'] }));
  assert.match(html(), /<div class="sl">Related 3<\/div><button class="sn" data-go="concept:f".*data-go="bridge:j".*data-go="record:r2"/, 'related, most linked first');
  assert.match(html(), /<div class="sr">Attached Decision 1 · Record 1 · Event 1<\/div>/);
  assert.match(html(), /data-follow="INHERITS_FROM\|in".*data-follow="RETURNS_TO\|in"/, 'rows in the order of the relation table');
  const noDetails = side(NODES, EDGES); noDetails.drawSum(S({ path: ['concept:a'] }));
  assert.ok(!noDetails.sum.innerHTML.includes('data-sopen'), 'no details button when the view cannot open details');
  assert.deepEqual(opened, []);
});

test('side: a decision says how long it held and what replaced it; the newer one can follow what it replaced', () => {
  const t = side(NODES, EDGES), html = () => t.sum.innerHTML;
  t.drawSum(S({ path: ['decision:old'] }));
  assert.match(html(), /<div class="sk">Decision · lab\/x · 2026-09-01<\/div><div class="sv closed">held 2026-09-01~2026-11-01<\/div><div class="sv closed">Replaced by <button class="sn" data-go="decision:new"/);
  assert.ok(!html().includes('data-follow="SUPERSEDES|in"'), 'what replaced it is a name, not a row to follow');
  t.drawSum(S({ path: ['decision:new'] }));
  assert.match(html(), /<div class="sv">holds 2026-11-01<\/div><div class="sf"><button class="sfr" data-follow="SUPERSEDES\|out"/);
});

test('side: paths to up to three pins other than the middle, a no-path line only for this middle, and nothing to draw without a place', () => {
  const t = side(NODES, EDGES), html = () => t.sum.innerHTML;
  t.drawSum(S({ path: ['concept:a'], pins: ['concept:a', 'concept:p', 'concept:f', 'concept:d', 'concept:e'] }));
  assert.match(html(), /<div class="sl">Path to<\/div><button class="sn" data-path="concept:p">.*<span>p<\/span><\/button><button class="sn" data-path="concept:f">.*<button class="sn" data-path="concept:d">.*<span>d<\/span><\/button>$/);
  assert.ok(!html().includes('data-path="concept:e"') && !html().includes('data-path="concept:a"'), 'three at most, not the middle itself');
  t.drawSum(S({ path: ['concept:a'], pins: ['concept:p'], noPath: 'concept:a' }));
  assert.match(html(), /<div class="sr">No path<\/div>$/);
  t.drawSum(S({ path: ['concept:a'], pins: ['concept:p'], noPath: 'concept:b' }));
  assert.ok(!html().includes('No path'), 'a no-path from another middle is not shown');
  const before = html(); t.drawSum(S({ path: ['concept:nowhere'] }));
  assert.equal(html(), before, 'no node: the summary is left as it was');
  const none = SD.compassSide(t.M, null, { C: { sum: SUM, rel: { topic: 'Topic' } }, KIND: KINDS, titleOf: (n) => n.t, short, colv: () => '', pathOf: () => [], ctx: {} });
  assert.doesNotThrow(() => none.drawSum(S({ path: ['concept:a'] })), 'no summary place: nothing to draw');
});

test('the mounted summary reads the theme for its icons and offers details only when the view can open them', () => {
  const before = document.documentElement.dataset.theme;
  document.documentElement.dataset.theme = 'dark';
  const opened = [], { h } = mount([node('concept:a', 'concept', 'root/x'), node('concept:b', 'concept', 'root/x')], [{ source: 'concept:a', target: 'concept:b', type: 'RELATED_TO' }],
    { onOpen: (id) => opened.push(id) });
  h.open('concept:a');
  const sum = () => h.side.querySelector('#cmp-sum'), icon = () => sum().querySelector('.sh svg').innerHTML;
  const dark = icon();
  assert.ok(dark.includes(h.colour('root/x')), `the icon wears the topic colour: ${dark}`);
  document.documentElement.dataset.theme = 'light'; h.retheme();
  assert.notEqual(icon(), dark, 'a theme switch repaints the icon');
  assert.ok(icon().includes(h.colour('root/x')), 'in the light colour');
  sum().querySelector('[data-sopen]').click();
  assert.deepEqual(opened, ['concept:a'], 'details for the middle');
  const plain = mount([node('concept:a', 'concept', 'root/x')]);
  plain.h.open('concept:a');
  assert.equal(plain.h.side.querySelector('#cmp-sum [data-sopen]'), null);
  if (before === undefined) delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = before;
  h.destroy(); plain.h.destroy();
});

test('side: long names are cut: 60 in the heading, 34 on a button, 40 in the peek card; related come most linked first', () => {
  const long = (k) => `${k}`.padEnd(70, 'x');
  const t = side([cn('concept:m', 'concept', 'lab/x', { t: long('m') }), cn('concept:y1', 'concept', 'lab/x', { t: long('y1'), g: 1 }),
    cn('concept:y2', 'concept', 'lab/x', { t: long('y2'), g: 5 }), cn('concept:y3', 'concept', 'lab/x', { t: long('y3'), g: 3 })],
  [['concept:m', 'concept:y1', 'RELATED_TO'], ['concept:m', 'concept:y2', 'RELATED_TO'], ['concept:m', 'concept:y3', 'RELATED_TO']]);
  t.drawSum(S({ path: ['concept:m'], pins: ['concept:y1'] }));
  const html = t.sum.innerHTML, names = [...html.matchAll(/<span>([^<]*)<\/span><\/button>/g)].map((m) => m[1]);
  assert.match(html, new RegExp(`<b>${short(long('m'), 60)}</b>`));
  assert.deepEqual(names, [short(long('y2'), 34), short(long('y3'), 34), short(long('y1'), 34), short(long('y1'), 34)], 'related by links, then the path row');
  assert.ok(names.every((s) => s.length === 34 && s.endsWith('…')));
  assert.deepEqual([...t.peekHTML(t.M.byAny('concept:m')).matchAll(/<span class="pn">([^<]*)<\/span>/g)].map((m) => m[1]),
    [short(long('y2'), 40), short(long('y3'), 40), short(long('y1'), 40)]);
});

test('side: with no single top topic the summary of the virtual root counts every node', () => {
  const t = side([cn('concept:a', 'concept', 'one'), cn('record:r', 'record', 'two/x', { ct: '2026-10-02' })], []);
  const root = t.M.topics.get(t.M.ROOT);
  assert.equal(root.path, '', 'a virtual root');
  t.drawSum(S({ path: [root.id] }));
  assert.match(t.sum.innerHTML, /<b>All topics<\/b><\/div><div class="sk">Topic · latest 2026-10-02<\/div><div class="sr">Concept 1 · Record 1 · 2 subtopics<\/div>/);
});
