// 나침반 뷰어의 버그 고침을 잡는다: 다시 그려도 보던 자리를 잇고, 긴 목록 너머의 노드와 목록(6시)의 노드도 가운데로 열리며,
// 고정은 스페이스마다 따로이고 깨진 저장값에 무너지지 않고, 바깥이 처리한 Esc 를 다시 처리하지 않는다.
// 실제 view-compass.ts 를 mini-dom 위에서 마운트한다.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { installDom, El } from './mini-dom.mjs';
import { loadCompass } from './helpers/compass-load.mjs';

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
