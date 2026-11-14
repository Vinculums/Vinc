// AC-APP-11 · AC-APP-14(홈 쪽): 문서 홈 목록을 실제 home.ts 그대로 그려 본다.
// 근거: [[vinc.bdd.app]] AC-APP-11, AC-APP-14 · [[vinc.frd.app]] FR-APP-11, FR-APP-14
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { installDom, settle, El } from './mini-dom.mjs';
import { loadTs, srcText, COPY_STUB, COMPONENTS_STUB } from './ts-load.mjs';

const { body } = installDom({ hash: '#home' });

const TOPICS = ['vinc/design', 'vinc/engineering', 'vinc/engineering/store', 'research'];
// 100 문서: 주제 넷을 돌려 가며, 수정 시각은 번호가 클수록 최근이다.
const DOCS = Array.from({ length: 100 }, (_, i) => ({
  doc_id: `doc-${String(i).padStart(3, '0')}`,
  title: `Doc ${String(i).padStart(3, '0')}`,
  topic: TOPICS[i % TOPICS.length],
  updated_at: `2026-10-${String(1 + (i % 28)).padStart(2, '0')}T00:00:${String(i % 60).padStart(2, '0')}Z`,
}));
const calls = { docList: 0, mounted: [] };
globalThis.__home = {
  docList: async () => { calls.docList++; return { executed: true, docs: DOCS }; },
  mount: (host, o) => { calls.mounted.push(o); host.innerHTML = '<div class="ed">body</div>';
    return { getText: () => '# A\n\n# B\n', dispose: async () => {}, scrollToOffset() {}, pauseSaving: async () => {} }; },
};

const stubs = {
  '../api': `export const api = { docList: (...a) => globalThis.__home.docList(...a),
      docContext: async () => ({ executed: false }), nodeContext: async () => ({ executed: false }),
      ingestFragment: async () => ({ executed: false }) };
    export const cfg = { owner: 'u', team: '' };
    export const canWriteHere = async () => true;
    export const isCommunitySpace = (t) => !!t && t.startsWith('community:');`,
  '../copy': COPY_STUB,
  '../doc-title': `export const docTitle = (t) => (t || '').trim() || 'Untitled';`,
  '../components': COMPONENTS_STUB + `\nexport const confirmDestructive = async () => false;`,
  '../icons': `export const icon = () => '';`,
  '../view-prefs': `export const dayOf = (ts) => ts.slice(0, 10);`,
  '../topbar': `export const setTopbarLead = () => null; export const setViewHooks = () => {};`,
  '../lnb': `export const agentRoleCount = () => 0; export const lnbTopicTint = () => ''; export const setLnbActive = () => {};
    export const setLnbDocOpener = () => {}; export const setLnbTopicPicker = () => {};
    export const setLnbScopeClearer = () => {}; export const refreshLnb = async () => {};`,
  '../doc-graph': `export const namedDecisions = () => []; export const docMirrorId = (id) => 'concept:doc.' + id;`,
  '../live': `export const setLiveRefresher = () => {};`,
  '../vinc-loaders.js': `export const loaderRows = () => '';`,
  '../doc-editor': `export const mountDocEditor = (...a) => globalThis.__home.mount(...a);`,
  '../authoring': `export const fillTopicList = async () => {}; export const forgetTopics = () => {};`,
  './settings': `export const openSettings = () => {};`,
  '../role-composer': `export const openRoleComposer = () => {}; export const renderRoleEditor = async () => {};`,
  './agents-board': `export const renderAgentsBoard = async () => {};`,
  '../doc-delete': `export const deleteDocument = async () => 'unknown';`,
};

async function mountHome() {
  const home = await loadTs('surfaces/home.ts', stubs);
  const el = new El('div');
  body.innerHTML = '';
  body.append(el);
  location.hash = '#home';
  await home.renderHome(el);
  await settle();
  return { home, el };
}
const rows = (el) => el.querySelectorAll('.home-row');
const act = (el, a) => el.querySelector(`[data-act="${a}"]`);

test('the list shows 40 rows and Show more adds 40 at a time', async () => {
  const { el } = await mountHome();
  assert.equal(rows(el).length, 40);
  assert.match(act(el, 'more').textContent, /more\(40\)/);
  act(el, 'more').click(); await settle();
  assert.equal(rows(el).length, 80);
  assert.match(act(el, 'more').textContent, /more\(20\)/);
  act(el, 'more').click(); await settle();
  assert.equal(rows(el).length, 100);
  assert.equal(act(el, 'more'), null);
  // 목록은 한 번 읽어 두고 더 보기에 다시 묻지 않는다.
  assert.equal(calls.docList, 1);
});

test('the default sort is recently updated, newest first', async () => {
  localStorage.removeItem('vinc.home.sort');
  const { el } = await mountHome();
  const first = rows(el)[0].querySelector('.home-rtitle').textContent;
  const newest = [...DOCS].sort((a, b) => b.updated_at.localeCompare(a.updated_at)
    || a.title.localeCompare(b.title))[0].title;
  assert.equal(first, newest);
});

test('a picked sort is kept in vinc.home.sort and used on the next launch', async () => {
  localStorage.removeItem('vinc.home.sort');
  const { el } = await mountHome();
  const sel = el.querySelector('#home-sort');
  sel.value = 'name';
  sel.fire('change', { target: sel });
  await settle();
  assert.equal(localStorage.getItem('vinc.home.sort'), 'name');
  assert.equal(rows(el)[0].querySelector('.home-rtitle').textContent, 'Doc 000');
  // 새로 띄운 모듈(앱을 다시 연 것)이 저장된 정렬로 연다.
  const again = await mountHome();
  assert.equal(again.el.querySelector('#home-sort option[selected]').value, 'name');
  assert.equal(rows(again.el)[0].querySelector('.home-rtitle').textContent, 'Doc 000');
  localStorage.setItem('vinc.home.sort', 'topic');
  const grouped = await mountHome();
  assert.ok(grouped.el.querySelectorAll('.home-group').length >= 2, 'the topic sort groups rows under topic headings');
  localStorage.setItem('vinc.home.sort', 'bogus');
  const fallback = await mountHome();
  assert.equal(fallback.el.querySelector('#home-sort option[selected]').value, 'recent');
  localStorage.removeItem('vinc.home.sort');
});

test('topic chips and the title filter narrow the list and stay out of the address', async () => {
  const { el } = await mountHome();
  // 맨 위 단은 하위가 여럿(vinc · research)이라 칩이 둘이다.
  const chips = el.querySelectorAll('.home-chip').map((c) => c.dataset.topic);
  assert.deepEqual(chips.sort(), ['research', 'vinc']);
  el.querySelector('.home-chip[data-topic="vinc"]').click(); await settle();
  assert.equal(location.hash, '#home');
  assert.match(el.querySelector('.home-count').textContent, /count\(75\)/);
  // vinc 아래 칩은 design · engineering 둘. 하나만 남는 단은 건너뛴다(engineering → store).
  el.querySelector('.home-chip[data-topic="vinc/engineering"]').click(); await settle();
  assert.match(el.querySelector('.home-count').textContent, /count\(50\)/);
  assert.deepEqual(el.querySelectorAll('.home-chip').map((c) => c.dataset.topic), ['vinc/engineering/store']);
  const q = el.querySelector('#home-q');
  q.value = 'Doc 00';
  q.fire('input', { target: q });
  await settle();
  // vinc/engineering 아래(i%4 가 1 또는 2)이면서 제목이 Doc 00x 인 것: 001 002 005 006 009.
  assert.deepEqual(rows(el).map((r) => r.querySelector('.home-rtitle').textContent).sort(),
    ['Doc 001', 'Doc 002', 'Doc 005', 'Doc 006', 'Doc 009']);
  assert.equal(location.hash, '#home');
  el.querySelector('.home-crumb[data-topic=""]').click(); await settle();
  assert.equal(location.hash, '#home');
  // 거르기를 풀어도 제목 거르기는 남는다(세션 안의 상태).
  assert.match(el.querySelector('.home-count').textContent, /count\(10\)/);
});

test('the home shows no unverified badge and never reads verified (AC-APP-14, home side)', async () => {
  const { el } = await mountHome();
  location.hash = '#home/doc/doc-001';
  const home = await loadTs('surfaces/home.ts', stubs);
  await home.renderHome(el);
  await settle();
  assert.equal(calls.mounted.at(-1).docId, 'doc-001');
  assert.ok(el.querySelector('#home-ed'));
  assert.doesNotMatch(el.innerHTML, /readerUnverified|not verified/i);
  // 정적 대조: 홈과 편집기는 `verified` 를 읽지 않는다. 배지는 그래프의 리더 레일에만 있다.
  for (const f of ['surfaces/home.ts', 'doc-editor.ts']) {
    const src = (await srcText(f)).replace(/\/\/.*$|\/\*[\s\S]*?\*\//gm, '');
    assert.doesNotMatch(src, /\.verified\b|readerUnverified/, f);
  }
});

test('the reader rail shows Reconstructed (not verified) only on verified false (AC-APP-14, static)', async () => {
  // 리더 레일은 view-reader.ts 의 닫힌 함수 안이라 그려 볼 수 없다: 원문으로 대조한다.
  const view = await srcText('surfaces/view-reader.ts');
  const m = /const badge = doc\.verified\s*\?\s*""\s*:\s*`<span class="hint">\$\{COPY\.ui\.viewx\.readerUnverified\}<\/span>`;/.exec(view);
  assert.ok(m, 'the badge is empty when verified and the hint otherwise');
  const after = view.slice(m.index, m.index + 6000);
  assert.match(after, /detail\.innerHTML = `[\s\S]*\$\{badge\}/, 'the badge is drawn into the reader rail');
  const copy = await srcText('copy.ts');
  assert.match(copy, /readerUnverified: "Reconstructed \(not verified\)"/);
});
