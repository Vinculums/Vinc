// 나침반 지도의 뿌리: 최상위 주제가 'vinc' 여야만 열리던 가정을 잡는다(VIN-927).
// 근거: [[episode:compass-map-assumed-vinc-as-the-only-root-topic-2026-11-06]]
// 실제 view-compass.ts 를 mini-dom 위에서 그대로 마운트한다. 문구·색 표·테마는 대역이고 레이아웃은 실제 코드다.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { installDom, El } from './mini-dom.mjs';
import { loadTs, tsUrl, copyStub, COMPONENTS_STUB } from './ts-load.mjs';

installDom({ hash: '' });
Object.defineProperty(El.prototype, 'isConnected', { get: () => true });
const winOn = [];
globalThis.addEventListener = (type, fn) => winOn.push([type, fn]);
globalThis.cancelAnimationFrame ??= clearTimeout;
globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} };
globalThis.matchMedia ??= () => ({ matches: false, addEventListener() {}, removeEventListener() {} });

const THEME_STUB = `export const topicJewels = {
  light: [0xb3261e, 0x1a5fb4, 0x2e7d32, 0xb26a00, 0x6a3fa0],
  dark: [0xff8a80, 0x82b1ff, 0x69f0ae, 0xffd180, 0xce93d8] };
export const palette = { light: { background: 0xf2f2f0, surface: 0xe9e9e6, card: 0xfbfbfa, text: 0x17171a, ring: 0x4a4f8c },
  dark: { background: 0x0a0a0a, surface: 0x121212, card: 0x1a1a1a, text: 0xededeb, ring: 0x8f96d9 } };`;
const tintUrl = await tsUrl('view-tint.ts', { './theme': THEME_STUB, './area-key': await tsUrl('area-key.ts'), './view-tint-floor': await tsUrl('view-tint-floor.ts') });
const { mountCompass, compassDataFrom } = await loadTs('surfaces/view-compass.ts', {
  '../copy': copyStub("{ ui: { compass: { rootTopic: 'All topics' } } }"),
  '../components': COMPONENTS_STUB,
  '../view-tint': tintUrl,
  '../theme': THEME_STUB,
});

/** [domain, 제목, 종류?] 목록 → 마운트. 허브가 주는 모양(kind·domain·ts·degree)으로 만든다. */
function mount(spec, edges = [], ctx = {}) {
  const nodes = spec.map(([domain, t, kind = 'concept']) => ({ id: `${kind}:${t}`, kind, title: t, label: t, domain, ts: '2026-11-01', degree: 1 }));
  const host = document.createElement('div');
  document.body.append(host);
  const h = mountCompass(host, compassDataFrom(nodes, edges), ctx);
  return { h, host, drawn: (w) => host.innerHTML.includes(`>${w}<`) };
}
const escape = () => { for (const [t, f] of winOn) if (t === 'keydown') f({ key: 'Escape', target: { closest: () => null } }); };

test('every domain under vinc: the map still starts at vinc', () => {
  const { h, drawn } = mount([['vinc/design', 'a'], ['vinc/eng', 'b'], ['vinc/design/tokens', 'c']]);
  assert.equal(h.centre(), 'topic:vinc');
  for (const w of ['vinc', 'design', 'eng']) assert.ok(drawn(w), `${w} is drawn`);
  assert.equal(h.colour('vinc'), '', 'the root wears no area colour');
  assert.notEqual(h.colour('vinc/design'), '', 'an area below the root does');
});

test('a single top-level topic with another name is the root', () => {
  const { h, drawn } = mount([['notes', 'a'], ['notes/daily', 'b']]);
  assert.equal(h.centre(), 'topic:notes');
  assert.ok(drawn('notes') && drawn('daily'));
  assert.equal(h.colour('notes'), '');
});

test('several top-level topics, none of them vinc: a nameless root holds them all', () => {
  const { h, drawn } = mount([['work/alpha', 'a'], ['work/zebra', 'b'], ['research', 'c']]);
  assert.equal(h.centre(), 'topic:', 'the nameless root is the centre');
  for (const w of ['All topics', 'work', 'research']) assert.ok(drawn(w), `${w} is drawn from the start`);
  assert.equal(h.colour(''), '');
  assert.notEqual(h.colour('work'), '', 'a top-level topic is an area and wears a colour');
  assert.notEqual(h.colour('research'), '');
  assert.deepEqual(h.tree().map((g) => g.domain), ['research', 'work/alpha', 'work/zebra']);
});

test('vinc beside another top-level topic (the default Agent roles live under agent)', () => {
  const { h, drawn } = mount([['vinc/design', 'a'], ['agent/maker', 'b']]);
  assert.equal(h.centre(), 'topic:');
  assert.ok(drawn('vinc') && drawn('agent'), 'both reach the start screen');
  h.open('topic:vinc');
  assert.equal(h.centre(), 'topic:vinc');
  escape();
  assert.equal(h.centre(), 'topic:', 'Escape goes back to the root');
});

test('a node without a domain stands at the root and is not given a made-up topic', () => {
  const { h, drawn } = mount([['work/alpha', 'a'], ['research', 'b'], ['', 'loose']]);
  assert.equal(h.centre(), 'topic:');
  assert.ok(!drawn('vinc'), 'no vinc topic appears');
  assert.ok(drawn('work') && drawn('research'));
  assert.ok(h.tree().some((g) => g.domain === '' && g.rows.some((r) => r.id === 'concept:loose')), 'the sidebar keeps the node');
});

test('with one top-level topic a node without a domain joins it, as it did under vinc', () => {
  const { h, host } = mount([['notes/daily', 'a'], ['', 'loose']]);
  assert.equal(h.centre(), 'topic:notes');
  assert.ok(h.tree().some((g) => g.domain === 'notes' && g.rows.some((r) => r.id === 'concept:loose')));
  assert.ok(!host.innerHTML.includes('>vinc<'));
});

test('no domain anywhere: the nameless root holds every node', () => {
  const { h, drawn } = mount([['', 'a'], ['', 'b']]);
  assert.equal(h.centre(), 'topic:');
  assert.ok(drawn('All topics'));
});

test('walking down from the nameless root and opening a node works at every depth', () => {
  const { h } = mount([['work/alpha/deep', 'a'], ['research', 'b']]);
  h.open('topic:work');
  assert.equal(h.centre(), 'topic:work');
  h.open('topic:work/alpha/deep');
  assert.equal(h.centre(), 'topic:work/alpha/deep');
  h.open('concept:a');
  assert.equal(h.centre(), 'concept:a');
  h.open('concept:b');
  assert.equal(h.centre(), 'concept:b');
});

test('a start path that names a topic the data lacks falls back to the root', () => {
  const { h } = mount([['work/alpha', 'a'], ['research', 'b']], [], { startPath: ['topic:vinc', 'topic:vinc/design'] });
  assert.equal(h.centre(), 'topic:');
});

test('a start path inside the data is honoured', () => {
  const { h } = mount([['work/alpha', 'a'], ['research', 'b']], [], { startPath: ['topic:work', 'topic:work/alpha'] });
  assert.equal(h.centre(), 'topic:work/alpha');
});

test('the time popup keeps the period and the bars, and invents no vinc', () => {
  const D = '-2026-11-01';
  for (const spec of [[['work/alpha', 'a' + D], ['research', 'b' + D], ['', 'loose' + D]], [['notes/daily', 'a' + D], ['notes/weekly', 'b' + D]]]) {
    const { h } = mount(spec);
    h.tools.querySelector('[data-pop="time"]').click();
    const pop = document.body.querySelectorAll('.cmp-pop').pop();
    assert.ok(pop.querySelector('[data-cmp-r="7"]') && pop.querySelector('[data-bars]'), 'period buttons and bars');
    assert.equal(pop.querySelector('[data-cell]'), null, 'no area by time grid');
    assert.ok(!pop.innerHTML.includes('vinc'), 'no vinc is invented');
    h.destroy();
  }
});
