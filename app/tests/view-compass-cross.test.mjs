// 나침반의 네 방향 십자: 층은 위·아래·왼쪽·오른쪽 넷 중 한 방향으로만 서고, 전체 펼치기의 부모 선은 꺾여 가며,
// 부채 이름표가 세로로 서지 않는다. 실제 view-compass.ts 를 mini-dom 위에서 마운트한다.
// 근거: [[episode:compass-four-direction-cross-layout-in-the-harness-2026-11-07]]
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { installDom, El } from './mini-dom.mjs';
import { loadTs, tsUrl, copyStub, COMPONENTS_STUB } from './ts-load.mjs';

// 펼친 뒤의 움직임(FLIP)과 목록 줄기 그리기는 다음 프레임에 돈다. mini-dom 은 그 SVG 경로를 그리지 못하므로 프레임을 돌리지 않는다
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
const { mountCompass, compassDataFrom } = await loadTs('surfaces/view-compass.ts', {
  '../copy': copyStub("{ ui: { compass: { rootTopic: 'All topics' } } }"),
  '../components': COMPONENTS_STUB,
  '../view-tint': tintUrl,
  '../theme': THEME_STUB,
});

// 맨 위 주제 하나 아래 여섯 영역, 영역마다 하위 주제 셋, 주제마다 개념 열둘과 기록 몇 개
function graph() {
  const nodes = [];
  for (const a of ['alpha', 'beta', 'gamma', 'delta', 'eps', 'zeta']) for (const s of ['one', 'two', 'three']) {
    const d = `root/${a}/${s}`;
    for (let i = 0; i < 12; i++) nodes.push({ id: `concept:${a}-${s}-${i}`, kind: 'concept', title: `${a} ${s} idea ${i}`, label: `${a} ${s} idea ${i}`, domain: d, ts: '2026-11-01', degree: 1 });
    for (let i = 0; i < 4; i++) nodes.push({ id: `record:${a}-${s}-${i}`, kind: 'record', title: `${a} ${s} note ${i}`, label: `${a} ${s} note ${i}`, domain: d, ts: '2026-11-01', degree: 1 });
  }
  const host = document.createElement('div');
  document.body.append(host);
  return { h: mountCompass(host, compassDataFrom(nodes, []), {}), host };
}
const hubs = (host) => new Map(host.querySelectorAll('.node[data-role="hub"]').map((g) => {
  const m = /translate\(([-\d.e]+)px, ([-\d.e]+)px\)/.exec(g.getAttribute('style')); return [g.getAttribute('data-id'), [+m[1], +m[2]]]; }));
const rotations = (host) => host.querySelectorAll('text').map((t) => /rotate\(([-\d.]+)/.exec(t.getAttribute('transform') || '')).filter(Boolean).map((m) => ((+m[1] % 360) + 360) % 360);

test('an opened path runs straight along one of the four directions', () => {
  const { h, host } = graph();
  h.open('topic:root/beta/two');
  const at = hubs(host), chain = ['topic:root', 'topic:root/beta', 'topic:root/beta/two'];
  for (let i = 1; i < chain.length; i++) {
    const [x0, y0] = at.get(chain[i - 1]), [x1, y1] = at.get(chain[i]);
    assert.ok(Math.abs(x1 - x0) < .5 || Math.abs(y1 - y0) < .5, `${chain[i]} sits on an axis from its parent`);
  }
});

test('no fan label stands vertical', () => {
  for (const id of ['topic:root/alpha/one', 'topic:root/beta/two', 'topic:root/gamma/three', 'topic:root/delta/one', 'topic:root/eps/two']) {
    const { h, host } = graph();
    h.open(id);
    for (const r of rotations(host)) assert.ok(!(r > 55 && r < 125) && !(r > 235 && r < 305), `${id}: a label is turned ${r}°`);
  }
});

test('all topics: every topic is drawn and every parent line bends only at right angles', () => {
  const { h, host } = graph();
  h.tools.querySelector('[data-all]').click();
  const at = hubs(host);
  assert.equal(at.size, 1 + 6 + 18, 'the root, six areas and eighteen topics');
  const routes = host.querySelectorAll('#cmp-rays path.ray.path').map((p) => p.getAttribute('d'));
  assert.equal(routes.length, 24, 'one routed line per topic below the root');
  for (const d of routes) {
    const pts = d.slice(1).split('L').map((q) => q.trim().split(/\s+/).map(Number));
    for (let i = 1; i < pts.length; i++) assert.ok(Math.abs(pts[i][0] - pts[i - 1][0]) < .5 || Math.abs(pts[i][1] - pts[i - 1][1]) < .5, `segment ${i} of ${d.slice(0, 40)} is not straight`);
  }
  // 층 허브끼리는 서로 떨어져 있다(같은 자리에 겹쳐 쌓이지 않는다)
  const xy = [...at.values()];
  for (let i = 0; i < xy.length; i++) for (let j = i + 1; j < xy.length; j++) assert.ok(Math.hypot(xy[i][0] - xy[j][0], xy[i][1] - xy[j][1]) > 100, 'two hubs share a spot');
});
