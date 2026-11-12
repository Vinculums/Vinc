// 주제 색 표 하나: 문서 트리(lnb.ts)와 나침반(view-compass.ts)이 같은 주제에 같은 색을 내는지 실제 두 표면을 mini-dom 위에 함께 세워 본다.
// 두 표면은 같은 표 모듈 인스턴스를 읽는다(같은 data: URL). 문구 · 테마 토큰 · API 는 대역이고, 색 계산과 두 표면의 코드는 실제 것이다.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { installDom, El } from './mini-dom.mjs';
import { loadTs, tsUrl, copyStub, COPY_STUB, COMPONENTS_STUB } from './ts-load.mjs';

// 창 이벤트는 실제로 전달한다: 표가 바뀌었다는 알림을 두 표면이 듣는 것까지 본다.
const bus = new EventTarget();
globalThis.addEventListener = bus.addEventListener.bind(bus);
globalThis.removeEventListener = bus.removeEventListener.bind(bus);
globalThis.dispatchEvent = bus.dispatchEvent.bind(bus);
installDom({ hash: '' });
document.getElementById = (id) => document.body.querySelector(`#${id}`);
Object.defineProperty(El.prototype, 'isConnected', { get: () => true });
globalThis.cancelAnimationFrame ??= clearTimeout;
globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} };
globalThis.matchMedia ??= () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
globalThis.CSS ??= { escape: (s) => String(s) };

const THEME_STUB = `export const topicJewels = {
  light: [0xb3261e, 0x1a5fb4, 0x2e7d32, 0xb26a00, 0x6a3fa0],
  dark: [0xff8a80, 0x82b1ff, 0x69f0ae, 0xffd180, 0xce93d8] };
export const palette = { light: { background: 0xf2f2f0, surface: 0xe9e9e6, card: 0xfbfbfa, text: 0x17171a, ring: 0x4a4f8c },
  dark: { background: 0x0a0a0a, surface: 0x121212, card: 0x1a1a1a, text: 0xededeb, ring: 0x8f96d9 } };`;
const tintUrl = await tsUrl('view-tint.ts', { './theme': THEME_STUB, './area-key': await tsUrl('area-key.ts') });
const tableUrl = await tsUrl('topic-tint-table.ts', { './view-tint': tintUrl });
const table = await import(tableUrl);
const { tintPlan } = await import(tintUrl);

const { mountCompass, compassDataFrom } = await loadTs('surfaces/view-compass.ts', {
  '../copy': copyStub("{ ui: { compass: { rootTopic: 'All topics' } } }"),
  '../components': COMPONENTS_STUB,
  '../view-tint': tintUrl,
  '../topic-tint-table': tableUrl,
  '../theme': THEME_STUB,
  './view-compass-state': await tsUrl('surfaces/view-compass-state.ts'),
  './view-compass-whole': await tsUrl('surfaces/view-compass-whole.ts'),
  '../keys': await tsUrl('keys.ts'),
});

// 문서 트리: 지금 스페이스와 받을 문서 주제를 테스트가 정한다.
const hub = { space: 'me|', docTopics: [] };
const none = 'export {};';
const lnb = await loadTs('lnb.ts', {
  './api': `export const cfg = { get owner() { return 'me'; }, get team() { return globalThis.__hub.space.split('|')[1]; } };
    export const spaceKey = () => globalThis.__hub.space;
    export const isCommunitySpace = () => false; export const remote = () => true;
    export const api = { navTree: async () => ({ executed: true, unfiled: [],
      domains: globalThis.__hub.docTopics.map((d) => ({ domain: d, concepts: 0, docs: [{ doc_id: 'doc:' + d, title: d, updated_at: '2026-11-01' }] })) }) };`,
  './copy': COPY_STUB,
  './doc-title': `export const docTitle = (t) => t || '';`,
  './icons': `export const icon = () => '';`,
  './lnb-tree': await tsUrl('lnb-tree.ts'),
  './view-tint': tintUrl,
  './topic-tint-table': tableUrl,
  './components': COMPONENTS_STUB,
  './vinc-loaders.js': `export const loaderRows = () => '';`,
  './surfaces/agent-map': none, './conventions': none, './doc-editor': none, './role-composer': none,
  './authoring': none, './surfaces/settings': none,
});
globalThis.__hub = hub;
document.body.innerHTML = '<div id="lnb-head"></div><div id="lnb-body"></div>';

// 고정 그림: 두 표면이 함께 갖는 주제와 한쪽에만 있는 주제.
const DOC_TOPICS = ['vinc/design/tokens', 'vinc/eng/app', 'vinc/eng/docs-only'];
const GRAPH_TOPICS = ['vinc/design/tokens', 'vinc/eng/app', 'vinc/eng/api', 'vinc/research'];
const withAncestors = (ds) => [...new Set(ds.flatMap((d) => d.split('/').map((_, i, s) => s.slice(0, i + 1).join('/'))))];
const ALL = withAncestors([...DOC_TOPICS, ...GRAPH_TOPICS]);

/** 사이드바 문서 트리를 다시 읽어 그린다. */
async function paintTree(topics) { hub.docTopics = topics; await lnb.refreshLnb({ quiet: true }); }
/** 나침반을 지금 스페이스로 세운다. */
function mountGraph(topics) {
  const nodes = topics.map((d, i) => ({ id: `concept:n${i}`, kind: 'concept', title: `n${i}`, label: `n${i}`, domain: d, ts: '2026-11-01', degree: 1 }));
  const host = document.createElement('div');
  document.body.append(host);
  return { h: mountCompass(host, compassDataFrom(nodes, []), { store: hub.space }), host };
}
/** 트리에 그려진 주제 줄의 색(줄의 style 에 실린 --tint). */
function treeRows() {
  const out = new Map();
  for (const b of document.body.querySelectorAll('.lnb-group[data-group]')) {
    const m = /--tint:(#[0-9a-f]{6})/.exec(b.attrs.get('style') || '');
    out.set(b.dataset.group, m ? m[1] : '');
  }
  return out;
}
/** 두 표면이 모든 주제에 같은 색을 내는지 본다. 트리에 줄이 있는 주제는 그 줄의 색으로 본다. */
function agree(h) {
  const rows = treeRows();
  assert.ok(rows.size >= 3, `the tree drew topic rows (${rows.size})`);
  for (const [t, c] of rows) assert.equal(c, h.colour(t), `tree row and graph disagree on ${t}`);
  const seen = {};
  for (const t of ALL) { assert.equal(lnb.lnbTopicTint(t), h.colour(t), `tree and graph disagree on ${t}`); seen[t] = h.colour(t); }
  return seen;
}

test('before: each surface built its own table, so a shared topic took two colours', () => {
  // 고치기 전 두 빌더가 하던 일: 자기 주제(조상 포함)만으로 tintPlan 을 돌린다.
  const docs = tintPlan(withAncestors(DOC_TOPICS), 'light').topics, graph = tintPlan(withAncestors(GRAPH_TOPICS), 'light').topics;
  const split = DOC_TOPICS.filter((t) => GRAPH_TOPICS.includes(t) && docs.get(t) !== graph.get(t));
  assert.deepEqual(split, ['vinc/eng/app'], 'the fixture reproduces the two-colour topic');
});

let docsFirst;
test('documents known first, then the graph: the tree and the compass give every topic one colour', async () => {
  hub.space = 'me|order-a';
  await paintTree(DOC_TOPICS);
  const { h } = mountGraph(GRAPH_TOPICS);
  docsFirst = agree(h);
  // 한 표는 두 표면의 주제 전부로 지은 것이다.
  const union = tintPlan(ALL, 'light').topics;
  for (const t of ALL.filter((x) => x !== 'vinc')) assert.equal(docsFirst[t], union.get(t), `${t} wears the all-topics colour`);
  assert.equal(docsFirst['vinc'], '', 'the single top-level topic is the root and wears no colour');
  assert.notEqual(docsFirst['vinc/eng/docs-only'], '', 'a documents-only topic has a colour in the graph too');
  assert.notEqual(lnb.lnbTopicTint('vinc/eng/api'), '', 'a graph-only topic has a colour in the tree too');
});

test('the graph known first, then documents: the same colours as the other order', async () => {
  hub.space = 'me|order-b';
  const { h } = mountGraph(GRAPH_TOPICS);
  await paintTree(DOC_TOPICS);
  assert.deepEqual(agree(h), docsFirst);
});

test('a topic added later: both surfaces repaint from the grown table and still agree', async () => {
  hub.space = 'me|later';
  await paintTree(DOC_TOPICS);
  const { h, host } = mountGraph(GRAPH_TOPICS);
  const before = agree(h);
  let told = 0;
  addEventListener(table.TOPIC_TINTS_EVENT, () => told++);
  await paintTree([...DOC_TOPICS, 'vinc/eng/added']);
  assert.ok(told >= 1, 'the table said it changed');
  const after = agree(h);
  assert.notEqual(after['vinc/eng/added'], '');
  // 같은 영역에서 새 주제 뒤로 정렬되는 주제는 색이 옮겨 간다(tintPlan 은 정렬 순위로 칠한다). 다른 영역은 그대로다.
  const moved = Object.keys(before).filter((t) => before[t] !== after[t]).sort();
  assert.deepEqual(moved, ['vinc/eng/api', 'vinc/eng/app', 'vinc/eng/docs-only'], 'only the topics sorted after the new one in its area moved');
  // 새 영역이 앞에 서면 뒤 영역의 보석이 바뀐다. 나침반이 그린 영역 고리도 새 색으로 다시 그려진다.
  const drawn = host.innerHTML;
  assert.ok(drawn.includes(after['vinc/design']), 'the compass draws the design area');
  await paintTree([...DOC_TOPICS, 'vinc/eng/added', 'vinc/arch']);
  const third = agree(h);
  assert.notEqual(third['vinc/design'], after['vinc/design'], 'a new area sorted first moves the next areas');
  assert.ok(host.innerHTML.includes(third['vinc/design']) && !host.innerHTML.includes(after['vinc/design']), 'the compass repainted with the new table');
});

test('a space switch starts a new table: one space\'s topics do not reach another\'s colours', async () => {
  hub.space = 'me|';
  await paintTree(DOC_TOPICS);
  mountGraph(GRAPH_TOPICS);
  assert.notEqual(lnb.lnbTopicTint('vinc/eng/app'), '');
  hub.space = 'me|team-1';
  const { h } = mountGraph(['work/alpha', 'work/zebra', 'research']);
  await paintTree(['work/alpha']);
  assert.equal(lnb.lnbTopicTint('vinc/eng/app'), '', 'the personal graph\'s topic is gone from the team\'s table');
  const own = tintPlan(withAncestors(['work/alpha', 'work/zebra', 'research']), 'light').topics;
  for (const t of ['work', 'work/alpha', 'work/zebra', 'research']) {
    assert.equal(h.colour(t), own.get(t), `${t} is coloured from the team's topics alone`);
    assert.equal(lnb.lnbTopicTint(t), own.get(t));
  }
  assert.equal(h.colour(''), '', 'several top-level topics: the nameless root wears nothing');
});

test('the root rule is one function: one top-level topic is the root, otherwise a nameless root', () => {
  assert.equal(table.rootTopicOf(['vinc/a', 'vinc/b/c', '']), 'vinc');
  assert.equal(table.rootTopicOf(['vinc/a', 'agent/maker']), '');
  assert.equal(table.rootTopicOf([]), '');
});
