// AC-APP-16 · AC-APP-17: 새 노드와 새 문서의 id. 컴포저는 진짜 node-composer.ts 를 작은 DOM 위에
// 띄워 만들기를 누르고, 새 문서는 authoring.ts 의 freeDocId 를 직접 부른다.
// 근거: [[vinc.bdd.app]] AC-APP-16, AC-APP-17 · [[vinc.frd.app]] FR-APP-16, FR-APP-17
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { installDom, settle, El } from './mini-dom.mjs';
import { loadTs, tsUrl, COPY_STUB, COMPONENTS_STUB } from './ts-load.mjs';

installDom();
const S = globalThis.__auth = {
  nodeContext: async () => ({ executed: false, note: 'unknown node' }),
  docGet: async () => ({ executed: false }),
  ingest: [], ingestReply: { executed: true, merged: { nodes: { created: 1 } } },
  opened: [], revealed: [],
};
const API = `export const api = {
    nodeContext: (...a) => globalThis.__auth.nodeContext(...a),
    docGet: (...a) => globalThis.__auth.docGet(...a),
    ingestFragment: async (f) => { globalThis.__auth.ingest.push(f); return globalThis.__auth.ingestReply; },
    search: async () => ({}), domains: async () => ({ rows: [] }) };
  export const cfg = { owner: 'u' };
  export const messageOf = (e, d) => String(e?.message || d);
  export const docMidWrite = (d) => d.executed === false && d.chunk_count !== undefined;`;
const TOPBAR = `export const viewHooks = () => ({
  openNodePane: async (id) => { globalThis.__auth.opened.push(id); },
  reveal: async (id) => { globalThis.__auth.revealed.push(id); } });`;
const SHEET = COMPONENTS_STUB + `
export const armsNotice = () => null;
export const openContextMenu = () => {};
export const showNotice = async () => {};
export const layerWhileShown = () => {};
export const sheetActions = (...acts) => acts.filter(Boolean).map((a) => '<button id="' + a.id + '">' + a.label + '</button>').join('');
export function openSheet(o) {
  const el = document.createElement('div');
  el.innerHTML = '<div class="sheet"><div class="sheet-body"></div><div class="sheet-foot"></div></div>';
  document.body.appendChild(el);
  const sheet = { el, body: el.querySelector('.sheet-body'), foot: el.querySelector('.sheet-foot'),
    close: () => { el.remove(); o.onClose?.(); } };
  globalThis.__auth.sheet = sheet;
  return sheet;
}`;
const authoringUrl = await tsUrl('authoring.ts', {
  './api': API, './copy': COPY_STUB, './components': SHEET, './topbar': TOPBAR,
  './node-composer': `export const openComposer = async () => null;`,
  './surfaces/settings': `export const openSettings = () => {};`,
  './doc-editor': `export const mountDocEditor = async () => ({});`,
  './keys': `export const shortcutsRest = () => false;`,
});
const authoring = await import(authoringUrl);
const composer = await loadTs('node-composer.ts', {
  './api': API, './copy': COPY_STUB, './components': SHEET, './topbar': TOPBAR, './authoring': authoringUrl,
  './lnb': `export const refreshLnb = async () => {};`,
});

/** 컴포저를 열고 제목 · 주제 · 한 줄 설명을 채운다. */
async function compose({ kind = 'concept', title, topic = 'vinc/core', ol = '' }) {
  S.ingest.length = 0;
  void composer.openComposer({ kind });
  const sheet = S.sheet;
  const $ = (s) => sheet.el.querySelector(s);
  $('#nc-title').value = title;
  $('#nc-topic').value = topic;
  $('#nc-ol').value = ol;
  return { sheet, $, create: async () => { $('#nc-create').click(); await settle(); await settle(); } };
}

test('slugs keep letters and digits of any script, cap at 60 and are empty for symbols only', () => {
  assert.equal(authoring.slugOf('Graph Store!'), 'graph-store');
  assert.equal(authoring.slugOf('지식 그래프'), '지식-그래프');
  assert.equal(authoring.slugOf('知識グラフ'), '知識グラフ');
  assert.equal(authoring.slugOf('Граф знаний'), 'граф-знаний');
  assert.equal(authoring.slugOf('!!! ???'), '');
  assert.equal(authoring.slugOf('a'.repeat(59) + ' bcd').length, 59);   // 60자에서 잘리고 끝의 - 는 떨어진다
  assert.match(authoring.stampSlug(), /^note-[0-9a-z]+$/);
  assert.deepEqual([...authoring.NODE_KINDS], ['concept', 'record', 'decision', 'episode']);
});

test('a title whose id exists offers the existing node and writes nothing', async () => {
  S.nodeContext = async () => ({ executed: true });
  const { $, create } = await compose({ title: 'Graph Store' });
  await create();
  assert.equal(S.ingest.length, 0);
  assert.match($('#nc-msg').textContent, /idTaken\(concept:graph-store\)/);
  assert.equal($('#nc-open-existing').hidden, false);
  $('#nc-open-existing').click();
  assert.equal(S.opened.at(-1), 'concept:graph-store');
});

test('a failed check writes nothing and says the check failed', async () => {
  for (const fail of [async () => { throw new Error('offline'); },
                      async () => ({ executed: false, note: 'store unavailable' })]) {
    S.nodeContext = fail;
    const { $, create } = await compose({ title: 'Fresh idea' });
    await create();
    assert.equal(S.ingest.length, 0);
    assert.match($('#nc-msg').innerHTML, /idCheckFail/);
    S.sheet.close();
  }
});

test('a symbols only title is written as note-<time> once the check says new', async () => {
  S.nodeContext = async () => ({ executed: false, note: 'unknown node: concept:x' });
  const { create } = await compose({ title: '?!' });
  await create();
  assert.equal(S.ingest.length, 1);
  const node = S.ingest[0].nodes[0];
  assert.match(node.id, /^concept:note-[0-9a-z]+$/);
  assert.equal(node.title, '?!');
  assert.equal(node.domain, 'vinc/core');
});

test('the one-liner goes where the kind keeps it and matched is a warning, not a success', async () => {
  S.nodeContext = async () => ({ executed: false, note: 'unknown node' });
  const sent = {};
  for (const kind of ['concept', 'record', 'decision', 'episode']) {
    const { create } = await compose({ kind, title: `A ${kind}`, ol: 'One line' });
    await create();
    sent[kind] = S.ingest[0].nodes[0];
  }
  assert.equal(sent.concept.one_liner, 'One line');
  assert.equal(sent.episode.summary, 'One line');
  assert.equal(sent.record.props.note, 'One line');
  assert.equal(sent.decision.props.note, 'One line');
  assert.equal(sent.record.one_liner, undefined);

  S.ingestReply = { executed: true, merged: { nodes: { matched: 1 } } };
  const { $, create } = await compose({ title: 'Raced' });
  await create();
  assert.match($('#nc-msg').innerHTML, /matched\(Raced\)/);
  assert.ok($('#nc-msg').innerHTML.includes('error-banner'));
  S.ingestReply = { executed: true, merged: { nodes: { created: 1 } } };
});

test('a title and a topic are both required before anything is checked or written', async () => {
  let asked = 0;
  S.nodeContext = async () => { asked++; return { executed: false, note: 'unknown node' }; };
  const a = await compose({ title: '', topic: 'vinc' });
  asked = 0; await a.create();
  assert.match(a.$('#nc-msg').innerHTML, /needTitle/);
  const b = await compose({ title: 'Titled', topic: '' });
  await b.create();
  assert.match(b.$('#nc-msg').innerHTML, /needTopic/);
  assert.equal(S.ingest.length, 0);
  assert.equal(asked, 0);
});

test('a new document takes the next free -n id and skips ids it cannot read', async () => {
  const docs = {
    'my-notes': { executed: true },                                  // 내 문서가 쓴다
    'my-notes-2': 'throw',                                           // 읽기 실패: 비었는지 모른다
    'my-notes-3': { executed: false, chunk_count: 2 },               // 쓰는 중인 문서
  };
  S.docGet = async (_owner, id) => {
    const d = docs[id];
    if (d === 'throw') throw new Error('503');
    return d || { executed: false };
  };
  assert.equal(await authoring.freeDocId('My notes', 'note-x', []), 'my-notes-4');
  // 쓰기에서 거절당한 후보(남의 스페이스가 가진 id)는 건너뛴다.
  assert.equal(await authoring.freeDocId('My notes', 'note-x', ['my-notes-4']), 'my-notes-5');
  assert.equal(await authoring.freeDocId('Fresh', 'note-x', []), 'fresh');
  // 제목에서 조각이 안 나오면 시각 조각을 쓴다.
  assert.equal(await authoring.freeDocId('???', 'note-abc', []), 'note-abc');
});

test('with no confirmed free id among 20 the save fails rather than guessing', async () => {
  S.docGet = async () => ({ executed: true });
  await assert.rejects(authoring.freeDocId('Taken', 'note-x', []), /no free document id/);
  S.docGet = async () => { throw new Error('offline'); };
  await assert.rejects(authoring.freeDocId('Unknown', 'note-x', []), /no free document id/);
});
