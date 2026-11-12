// 나침반 렌즈의 상태 모듈(view-compass-state.ts): 전이는 순수하다(옛 상태를 바꾸지 않는다), 앞뒤 기록, 카메라(맞춤 · 날기 · 굴리기 ·
// 끌기 · 크기 바뀜), 거르개, 고정, 창, 스냅숏 왕복과 옛 모양. DOM 없이 모듈만 적재한다.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { loadTs } from './ts-load.mjs';

const S = await loadTs('surfaces/view-compass-state.ts');
const base = () => S.initialState('dark', { w: 1000, h: 500 });
const frozen = (o) => { if (o && typeof o === 'object' && !Object.isFrozen(o)) { Object.freeze(o); for (const v of Object.values(o)) frozen(v); } return o; };
const known = (ids) => (id) => (ids.includes(id) ? id : null);

test('a transition returns a new state and leaves the old one as it was', () => {
  const s0 = frozen(base());
  const steps = [(s) => S.enter(s, ['r', 'a']), (s) => S.upOne(s), (s) => S.toRoot(s, 'r'), (s) => S.hover(s, 'x'), (s) => S.setQuery(s, 'Q'),
    (s) => S.setRange(s, ['2026-01-01', '2026-02-01']), (s) => S.toggleKind(s, 'record'), (s) => S.showMore(s, 'h|record'), (s) => S.togglePin(s, 'p'),
    (s) => S.pinsPlaced(s, [['p', { x: 1, y: 2 }]]), (s) => S.toggleSumOpen(s, 'pre:out'), (s) => S.openSum(s, 'der:in'), (s) => S.setNoPath(s, 'n'),
    (s) => S.togglePop(s, 'time'), (s) => S.closePop(s), (s) => S.armsSettled(s), (s) => S.themeChanged(s, 'light'), (s) => S.fontsSettled(s),
    (s) => S.resize(s, { w: 10, h: 10 }), (s) => S.flyTo(s, { x: 0, y: 0, w: 100, h: 100 }, { w: 1000, h: 500 }, 0, 520)];
  let s = s0;
  for (const f of steps) { const before = JSON.stringify(s); const n = f(frozen(s)); assert.equal(JSON.stringify(s), before); s = n; }
  assert.equal(JSON.stringify(s0), JSON.stringify(base()));
});

test('entering pushes onto the history and drops what was ahead', () => {
  let s = S.enter(base(), ['r']);
  s = S.enter(s, ['r', 'a']); s = S.enter(s, ['r', 'a', 'b']);
  assert.deepEqual(s.hist, [['r'], ['r', 'a'], ['r', 'a', 'b']]); assert.equal(s.hi, 2);
  s = S.historyStep(s, -1, known(['r', 'a', 'b'])); s = S.historyStep(s, -1, known(['r', 'a', 'b']));
  assert.deepEqual(s.path, ['r']); assert.equal(s.hi, 0);
  assert.equal(S.historyStep(s, -1, known(['r'])), s, 'at the first entry Alt+Left changes nothing');
  s = S.historyStep(s, 1, known(['r', 'a', 'b'])); assert.deepEqual(s.path, ['r', 'a']);
  s = S.enter(s, ['r', 'c']);
  assert.deepEqual(s.hist, [['r'], ['r', 'a'], ['r', 'c']], 'the entry ahead of the current one is dropped');
  assert.equal(S.historyStep(s, 1, known(['r', 'c'])), s, 'at the last entry Alt+Right changes nothing');
  assert.equal(S.historyCan(s, -1), true); assert.equal(S.historyCan(s, 1), false);
});

test('a history step drops ids the data does not know, and a replace does not touch the history', () => {
  let s = S.enter(S.enter(base(), ['r', 'gone', 'a']), ['r']);
  s = S.historyStep(s, -1, known(['r', 'a'])); assert.deepEqual(s.path, ['r', 'a']);
  const t = S.enter(s, ['r', 'x'], false); assert.deepEqual(t.path, ['r', 'x']); assert.equal(t.hist, s.hist); assert.equal(t.hi, s.hi);
});

test('Backspace goes one layer up, never past the root, and Escape goes to the root as a new entry', () => {
  let s = S.enter(base(), ['r', 'a', 'b']);
  s = S.upOne(s); assert.deepEqual(s.path, ['r', 'a']);
  s = S.upOne(S.upOne(s)); assert.deepEqual(s.path, ['r']); assert.equal(s.hist.length, 3, 'up at the root adds no entry');
  s = S.toRoot(S.enter(s, ['r', 'z']), 'r'); assert.deepEqual(s.path, ['r']); assert.deepEqual(s.hist.at(-1), ['r']);
  assert.equal(S.centreOf(S.enter(base(), ['r', 'q'])), 'q'); assert.equal(S.centreOf(base()), '');
});

test('the camera intent is used once, keep first, then chain, then wide', () => {
  let s = S.pickFromTree(base(), ['r', 'a']);
  assert.deepEqual(S.cameraPlan(s), { kind: 'wide' }); s = S.cameraFramed(s, S.cameraPlan(s)); assert.deepEqual(S.cameraPlan(s), { kind: 'fit' });
  s = S.followChain(base(), ['r', 'a', 'b'], ['a', 'b']);
  assert.deepEqual(S.cameraPlan(s), { kind: 'chain', ids: ['a', 'b'] });
  s = S.keepCamera(S.pickFromTree(s, ['r']));
  assert.equal(S.cameraPlan(s).kind, 'keep');
  s = S.cameraFramed(s, S.cameraPlan(s)); assert.equal(S.cameraPlan(s).kind, 'chain', 'keep clears only itself');
  s = S.cameraFramed(s, S.cameraPlan(s)); assert.equal(S.cameraPlan(s).kind, 'fit', 'chain also clears wide');
  assert.equal(S.cameraPlan(S.togglePin(base(), 'p')).kind, 'keep', 'a pin keeps the picture where it is');
});

test('a fitted box takes the aspect of the graph area and is never smaller than 420 by 320', () => {
  const b = S.fitBox({ x: 0, y: 0, w: 100, h: 100 }, { w: 1000, h: 500 });
  assert.deepEqual(b, { x: -110, y: 0, w: 640, h: 320 });
  const tall = S.fitBox({ x: 0, y: 0, w: 2000, h: 400 }, { w: 400, h: 400 });
  assert.equal(tall.w, 2000); assert.equal(tall.h, 2000); assert.equal(tall.y, -800);
  assert.deepEqual(S.fitBox({ x: 0, y: 0, w: 1400, h: 900 }, { w: 0, h: 0 }), { x: 0, y: 0, w: 1400, h: 900 }, 'a hidden area counts as 1400 by 900');
});

test('a flight eases from the current view to the fitted box and stops there', () => {
  let s = S.setView(base(), { x: 0, y: 0, w: 1000, h: 500 }, 1000);
  s = S.flyTo(s, { x: 1000, y: 0, w: 1000, h: 500 }, { w: 1000, h: 500 }, 100, 520);
  assert.ok(S.flying(s));
  const mid = S.flyStep(s, 100 + 260, 1000);
  assert.ok(mid.camera.view.x > 500 && mid.camera.view.x < 1000, 'half the time covers more than half the way');
  const end = S.flyStep(mid, 100 + 600, 1000);
  assert.deepEqual(end.camera.view, { x: 1000, y: 0, w: 1000, h: 500 }); assert.equal(S.flying(end), false);
  const first = S.flyStep(S.flyTo(base(), { x: 5, y: 5, w: 1000, h: 500 }, { w: 1000, h: 500 }, 0, 0), 0, 1000);
  assert.deepEqual(first.camera.view, { x: 5, y: 5, w: 1000, h: 500 }, 'with no view yet and no motion it lands at once');
});

test('the wheel zooms about the pointer and stops a flight; dragging moves the view by screen pixels', () => {
  let s = S.setView(base(), { x: 0, y: 0, w: 1000, h: 500 }, 1000);
  s = S.flyTo(s, { x: 9, y: 9, w: 9, h: 9 }, { w: 1000, h: 500 }, 0, 520);
  const z = S.zoomAt(s, .25, .5, Math.log(2) / .0015, 1000);
  assert.equal(z.camera.fly, null);
  assert.ok(Math.abs(z.camera.view.w - 2000) < 1e-9 && Math.abs(z.camera.view.h - 1000) < 1e-9);
  assert.ok(Math.abs(z.camera.view.x + 250) < 1e-9 && Math.abs(z.camera.view.y + 250) < 1e-9, 'the point under the pointer stays put');
  let p = S.panStart(S.setView(base(), { x: 0, y: 0, w: 2000, h: 1000 }, 1000), 100, 100);
  p = S.panMove(p, 150, 90, 1000, 1000); assert.deepEqual(p.camera.view, { x: -100, y: 20, w: 2000, h: 1000 });
  p = S.panEnd(p); assert.equal(p.pan, null); assert.equal(S.panMove(p, 0, 0, 1000, 1000), p, 'moving without a press changes nothing');
});

test('a size change keeps the camera where it was and the detail level until the camera moves', () => {
  let s = S.setView(base(), { x: -1250, y: -600, w: 2500, h: 1200 }, 1000);
  assert.deepEqual(S.lodOf(s.camera), { lod1: true, lod2: false });
  const r = S.resize(s, { w: 1200, h: 500 });
  assert.deepEqual(r.size, { w: 1200, h: 500 }); assert.deepEqual(r.camera, s.camera, 'the same picture range stays in view');
  assert.deepEqual(S.lodOf(r.camera), { lod1: true, lod2: false }, 'the level is measured again only when the camera moves');
  assert.deepEqual(S.lodOf(S.setView(r, r.camera.view, 1200).camera), { lod1: false, lod2: false });
  assert.equal(S.resize(r, { w: 1200, h: 500 }), r, 'the same size is no change');
  assert.deepEqual(S.lodOf(base().camera), { lod1: false, lod2: false });
  assert.deepEqual(S.lodOf(S.setView(base(), { x: 0, y: 0, w: 6000, h: 1 }, 1000).camera), { lod1: true, lod2: true });
  assert.equal(S.viewBoxAttr({ x: -1.25, y: 2, w: 3.333, h: 4.05 }), '-1.3 2.0 3.3 4.0');
});

test('theme and fonts are state; the glyph width is the fixed estimate the layout has always used', () => {
  let s = S.themeChanged(base(), 'light'); assert.equal(s.theme, 'light');
  s = S.fontsSettled(S.fontsSettled(s)); assert.equal(s.fontKey, 2);
  assert.equal(S.glyphWidth(0, 'fan'), 6.6); assert.equal(S.glyphWidth(s.fontKey, 'fan'), 6.6); assert.equal(S.glyphWidth(s.fontKey, 'bridge'), 7);
});

test('filters: query is lower case, kinds toggle in order, more grows by twenty, a range keeps undated nodes', () => {
  let s = S.setQuery(base(), 'Gate'); assert.equal(s.query, 'gate'); assert.equal(S.setQuery(s, '').query, '');
  s = S.toggleKind(S.toggleKind(S.toggleKind(s, 'record'), 'episode'), 'record'); assert.deepEqual(s.koff, ['episode']);
  s = S.toggleKind(s, 'record'); assert.deepEqual(s.koff, ['episode', 'record']);
  s = S.showMore(S.showMore(S.showMore(s, 'a|record'), 'b|doc'), 'a|record');
  assert.deepEqual(s.more, [['a|record', 40], ['b|doc', 20]]); assert.equal(S.moreOf(s, 'a|record'), 40); assert.equal(S.moreOf(s, 'x'), 0);
  assert.equal(S.inRange('2026-03-01', ['2026-02-01', '2026-03-01']), true); assert.equal(S.inRange('2026-03-02', ['2026-02-01', '2026-03-01']), false);
  assert.equal(S.inRange('', ['2026-02-01', '2026-03-01']), true); assert.equal(S.inRange('2020-01-01', null), true);
});

test('the time bars: the first column is everything before, a drag spans both ends, a preset counts back from the last day', () => {
  const days = ['before', '2026-01-01', '2026-01-02', '2026-01-03', '2026-01-04'];
  let s = S.barStart(base(), days, 3); assert.deepEqual(s.tsel, ['2026-01-03', '2026-01-03']);
  s = S.barMove(s, days, 1); assert.deepEqual(s.tsel, ['2026-01-01', '2026-01-03']);
  s = S.barMove(s, days, 0); assert.deepEqual(s.tsel, ['0000-01-01', '2026-01-03']);
  s = S.barEnd(s); assert.equal(s.bar, null); assert.equal(S.barMove(s, days, 4), s, 'no drag, no change');
  assert.deepEqual(S.rangeOf(days, '2'), ['2026-01-03', '2026-01-04']); assert.deepEqual(S.rangeOf(days, '30'), ['2026-01-01', '2026-01-04']);
  assert.equal(S.rangeOf(days, 'all'), null);
});

test('pins: toggling keeps the order, unpinning forgets the last place, stored values are cleaned', () => {
  let s = S.togglePin(S.togglePin(base(), 'a'), 'b');
  assert.deepEqual(s.pins, ['a', 'b']);
  s = S.pinsPlaced(s, [['a', { x: 1, y: 2 }], ['b', { x: 3, y: 4 }]]); s = S.pinsPlaced(s, [['a', { x: 5, y: 6 }]]);
  assert.deepEqual(s.lastPin, [['a', { x: 5, y: 6 }], ['b', { x: 3, y: 4 }]]);
  s = S.togglePin(s, 'a'); assert.deepEqual(s.pins, ['b']); assert.deepEqual(s.lastPin, [['b', { x: 3, y: 4 }]]);
  assert.equal(S.pinsPlaced(s, []), s);
  assert.deepEqual(S.pinsFrom(['a', 3, 'zz', 'a', null, 'b'], (id) => id !== 'zz'), ['a', 'b']);
  assert.deepEqual(S.loadPins(base(), ['x', 'y']).pins, ['x', 'y']);
  assert.deepEqual(S.pinsFrom({ a: 1 }, () => true), []); assert.deepEqual(S.pinsFrom(null, () => true), []);
});

test('the side summary and the popups', () => {
  let s = S.toggleSumOpen(base(), 'pre:out'); assert.deepEqual(s.sumOpen, ['pre:out']);
  s = S.openSum(S.openSum(s, 'der:in'), 'der:in'); assert.deepEqual(s.sumOpen, ['pre:out', 'der:in']);
  s = S.toggleSumOpen(s, 'pre:out'); assert.deepEqual(s.sumOpen, ['der:in']);
  assert.equal(S.setNoPath(s, 'n').noPath, 'n');
  s = S.togglePop(s, 'time'); assert.equal(s.pop, 'time'); s = S.togglePop(s, 'legend'); assert.equal(s.pop, 'legend');
  s = S.togglePop(s, 'legend'); assert.equal(s.pop, ''); assert.equal(S.closePop(s), s);
  assert.equal(S.hover(S.hover(base(), 'x'), 'x').hover, 'x');
  const m = S.armsSettled(base()); assert.equal(m.armsMeasured, true); assert.equal(S.armsSettled(m), m);
});

test('a snapshot comes back through restore as the same place, filters and camera', () => {
  let s = S.enter(S.enter(base(), ['r']), ['r', 'a']);
  s = S.setQuery(S.setRange(S.toggleKind(S.showMore(s, 'a|record'), 'episode'), ['2026-01-01', '2026-02-01']), 'gate');
  s = S.setView(s, { x: 1, y: 2, w: 3, h: 4 }, 1000);
  const snap = S.snapshotOf(s);
  assert.deepEqual(snap, { path: ['r', 'a'], hist: [['r'], ['r', 'a']], hi: 1, mode: 'path', filt: [], more: [['a|record', 20]], koff: ['episode'],
    tsel: ['2026-01-01', '2026-02-01'], view: { x: 1, y: 2, w: 3, h: 4 }, query: 'gate' });
  const back = S.restoreFrom(base(), JSON.parse(JSON.stringify(snap)), known(['r', 'a']));
  assert.deepEqual(S.snapshotOf(S.setView(back, snap.view, 1000)), snap);
  assert.equal(S.cameraPlan(back).kind, 'keep', 'a saved view is kept, not refitted');
});

test('older saved shapes are still read: path only, the All mode with its expansions, unknown ids, no history', () => {
  const k = known(['r', 'a', 'b', 'c']);
  const only = S.restoreFrom(base(), { path: ['r', 'a'] }, k);
  assert.deepEqual(only.path, ['r', 'a']); assert.deepEqual(only.hist, [['r', 'a']]); assert.equal(only.hi, 0, 'no history: this path is the first entry');
  assert.equal(S.cameraPlan(only).kind, 'fit'); assert.deepEqual([only.more, only.koff, only.tsel, only.query], [[], [], null, '']);
  const all = S.restoreFrom(base(), { path: ['r', 'a', 'b'], hist: [['r'], ['r', 'a', 'b']], hi: 1, mode: 'all', filt: [['a', ['b']]], more: [], koff: ['episode'], tsel: null, view: null, query: '' }, k);
  assert.deepEqual(all.path, ['r', 'a', 'b'], 'a saved All view opens one layer at a time'); assert.deepEqual(all.hist, [['r'], ['r', 'a', 'b']]); assert.equal(all.hi, 1);
  assert.equal('mode' in all || 'filt' in all, false, 'mode and filt are not carried');
  const odd = S.restoreFrom(base(), { path: ['r', 'gone', 'c'], hist: [['gone'], ['r', 'gone', 'c']], hi: 9 }, k);
  assert.deepEqual(odd.path, ['r', 'c']); assert.deepEqual(odd.hist, [['r', 'c']]); assert.equal(odd.hi, 0, 'hi stays inside the history');
  assert.equal(S.restoreFrom(base(), { path: ['gone'] }, k), null, 'nothing known: the caller opens its start path');
  const dup = S.restoreFrom(S.toggleKind(base(), 'record'), { path: ['r'], koff: ['record', 'doc'], more: [['x', 20], ['x', 40]] }, k);
  assert.deepEqual(dup.koff, ['record', 'doc']); assert.deepEqual(dup.more, [['x', 40]]);
});
