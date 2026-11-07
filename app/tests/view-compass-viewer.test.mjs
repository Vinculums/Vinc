// 나침반 뷰어의 버그 고침을 잡는다: 다시 그려도 보던 자리를 잇고, 긴 목록 너머의 노드와 목록(6시)의 노드도 가운데로 열리며,
// 고정은 스페이스마다 따로이고 깨진 저장값에 무너지지 않고, 바깥이 처리한 Esc 를 다시 처리하지 않는다.
// 실제 view-compass.ts 를 mini-dom 위에서 마운트한다.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { installDom, El } from './mini-dom.mjs';
import { loadTs, tsUrl, copyStub, COMPONENTS_STUB } from './ts-load.mjs';

globalThis.requestAnimationFrame = () => 0;
installDom({ hash: '' });
Object.defineProperty(El.prototype, 'isConnected', { get: () => true });
const winOn = [];
globalThis.addEventListener = (type, fn) => winOn.push([type, fn]);
globalThis.cancelAnimationFrame ??= clearTimeout;
globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} };
globalThis.matchMedia ??= () => ({ matches: false, addEventListener() {}, removeEventListener() {} });

const THEME_STUB = `export const topicJewels = {
  light: [0xb3261e, 0x1a5fb4, 0x2e7d32, 0xb26a00, 0x6a3fa0],
  dark: [0xff8a80, 0x82b1ff, 0x69f0ae, 0xffd180, 0xce93d8] };`;
const tintUrl = await tsUrl('view-tint.ts', { './theme': THEME_STUB, './area-key': await tsUrl('area-key.ts') });
const { mountCompass, compassDataFrom } = await loadTs('surfaces/view-compass.ts', {
  '../copy': copyStub("{ ui: { compass: { rootTopic: 'All topics' } } }"),
  '../components': COMPONENTS_STUB,
  '../view-tint': tintUrl,
  '../theme': THEME_STUB,
});

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
