// 나침반이 그래프에서 온 이름을 정본 esc 로 내는가: `<` `>` `&` `"` 가 든 제목이 이름표 · aria-label · 풍선에서
// 글자로 남고 요소나 속성 탈출이 되지 않는다. 실제 view-compass.ts 를 mini-dom 위에서 마운트한다.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { installDom, El } from './mini-dom.mjs';
import { loadTs, tsUrl, copyStub, COMPONENTS_STUB } from './ts-load.mjs';

globalThis.requestAnimationFrame = () => 0;
installDom({ hash: '' });
Object.defineProperty(El.prototype, 'isConnected', { get: () => true });
globalThis.cancelAnimationFrame ??= clearTimeout;
globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} };
globalThis.matchMedia ??= () => ({ matches: false, addEventListener() {}, removeEventListener() {} });

const THEME_STUB = `export const topicJewels = {
  light: [0xb3261e, 0x1a5fb4, 0x2e7d32, 0xb26a00, 0x6a3fa0],
  dark: [0xff8a80, 0x82b1ff, 0x69f0ae, 0xffd180, 0xce93d8] };
export const palette = { light: { background: 0xf2f2f0, surface: 0xe9e9e6, card: 0xfbfbfa, text: 0x17171a, ring: 0x4a4f8c },
  dark: { background: 0x0a0a0a, surface: 0x121212, card: 0x1a1a1a, text: 0xededeb, ring: 0x8f96d9 } };`;
const tintUrl = await tsUrl('view-tint.ts', { './theme': THEME_STUB, './area-key': await tsUrl('area-key.ts') });
const { mountCompass, compassDataFrom } = await loadTs('surfaces/view-compass.ts', {
  '../copy': copyStub("{ ui: { compass: { rootTopic: 'All topics', kinds: { concept: 'Concept', topic: 'Topic' } } } }"),
  '../components': COMPONENTS_STUB,
  '../view-tint': tintUrl,
  '../topic-tint-table': await tsUrl('topic-tint-table.ts', { './view-tint': tintUrl }),
  '../theme': THEME_STUB,
  './view-compass-state': await tsUrl('surfaces/view-compass-state.ts'),
  './view-compass-whole': await tsUrl('surfaces/view-compass-whole.ts'),
  '../keys': await tsUrl('keys.ts'),
});

const TITLE = 'a<img src=x>&"q"';
const ESCAPED = 'a&lt;img src=x&gt;&amp;&quot;q&quot;';
const node = (id, extra = {}) => ({ id, kind: 'concept', title: id, label: id, domain: 'root/a', ts: '2026-11-01', degree: 1, ...extra });

function mountHostile() {
  const host = document.createElement('div');
  document.body.append(host);
  const h = mountCompass(host, compassDataFrom([node('concept:x', { title: TITLE, label: TITLE }), node('concept:y')], []), {});
  h.open('concept:x');
  return host;
}

test('a title with < > & and a quote stays text in the label and the aria-label', () => {
  const host = mountHostile();
  const html = host.innerHTML;
  assert.equal(host.querySelectorAll('img').length, 0, 'the title made no element');
  assert.ok(!html.includes('<img'), 'no raw tag in the markup');
  assert.ok(!html.includes('src=x>'), '> is escaped too');
  const g = host.querySelector('.node[data-id="concept:x"]');
  assert.ok(g, 'the node is drawn');
  assert.ok(g.attrs.get('aria-label').endsWith(` ${ESCAPED}`), `aria-label: ${g.attrs.get('aria-label')}`);
  const label = g.querySelectorAll('text').find((t) => t.textContent.includes('img'));
  assert.equal(label?.html().match(/>([^<]*)<\/text>$/)?.[1], ESCAPED, 'the drawn label');
  assert.equal(label.textContent, TITLE, 'and reads back as the title');
  assert.ok(!html.includes('undefined'));
});

test('the hover tip escapes the same title', () => {
  const host = mountHostile();
  const g = host.querySelector('.node[data-id="concept:x"]');
  g.fire('pointerover');
  const tip = host.querySelector('#cmp-tip');
  assert.ok(tip.innerHTML.startsWith(`${ESCAPED}<small>`), tip.innerHTML);
  assert.equal(tip.querySelectorAll('img').length, 0);
  assert.ok(!tip.innerHTML.includes('undefined'), tip.innerHTML);
});
