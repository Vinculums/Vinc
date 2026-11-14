// AC-APP-11 · AC-APP-14(홈 쪽): 문서 홈 목록을 실제 home.ts 그대로 그려 본다.
// 근거: [[vinc.bdd.app]] AC-APP-11, AC-APP-14 · [[vinc.frd.app]] FR-APP-11, FR-APP-14
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { installDom, settle, El } from './mini-dom.mjs';
import { loadTs, tsUrl, srcText, COPY_STUB, COMPONENTS_STUB } from './ts-load.mjs';
import { loadReader, loadDocEditor, loadDocMidWrite, watchTimers, MID_WRITE, FAILED, GOOD } from './helpers/doc-reader-load.mjs';

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

// 표면이 셸에 거는 문(surface-binding.ts)은 실제 것을 쓰고, 그 문이 부르는 셸 쪽 setter 아홉은 받은 것을 적는다.
globalThis.__bound = [];
// 문서 홈이 뷰 머리 앞자리에 넘긴 마크업, 넘긴 차례대로.
globalThis.__lead = [];
const recorder = (names) => names.map((n) =>
  `export const ${n} = (v) => { globalThis.__bound.push(['${n}', v === null ? null : typeof v]); };`).join('\n');
const BIND_URL = await tsUrl('surface-binding.ts', {
  './topbar': recorder(['setViewHooks', 'setSearchFilter', 'setTopbarTools']),
  './lnb': recorder(['setLnbNavigator', 'setLnbTopicPicker', 'setLnbScopeClearer', 'setLnbDocOpener', 'setLnbGraphSource']),
  './live': recorder(['setLiveRefresher']),
});

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
  '../topbar': `export const setTopbarLead = (html) => { globalThis.__lead.push(html); return null; }; export const setViewHooks = () => {};`,
  '../lnb': `export const lnbTopicTint = () => ''; export const setLnbActive = () => {};
    export const setLnbDocOpener = () => {}; export const setLnbTopicPicker = () => {};
    export const setLnbScopeClearer = () => {}; export const refreshLnb = async () => {};`,
  '../agent-roster': `export const agentRoleCount = () => 0;`,
  '../doc-graph': `export const namedDecisions = () => []; export const docMirrorId = (id) => 'concept:doc.' + id;`,
  '../live': `export const setLiveRefresher = () => {};`,
  '../surface-binding': BIND_URL,
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

test('the documents home puts its place name in the header lead, and the reading page puts a back link there', async () => {
  globalThis.__lead.length = 0;
  const { el } = await mountHome();
  assert.match(globalThis.__lead.at(-1), /class="tb-lead-name">COPY\.ui\.home\.back</, 'the list names the place');
  globalThis.__lead.length = 0;
  location.hash = '#home/doc/doc-001';
  const home = await loadTs('surfaces/home.ts', stubs);
  await home.renderHome(el);
  await settle();
  assert.match(globalThis.__lead.join(''), /class="home-back" href="#home">‹ COPY\.ui\.home\.back</, 'the reading page names the way back');
  location.hash = '#home';
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

// 라우터(main.ts route)는 표면을 갈아 끼울 때마다 `unbindSurface` 한 번으로 표면이 건 손잡이를 전부 푼다.
test('the documents home binds its shell hooks through one door, and a route change clears each of them', async () => {
  const bind = await import(BIND_URL);
  bind.unbindSurface(); globalThis.__bound.length = 0;
  await mountHome();
  assert.deepEqual(globalThis.__bound.map(([n, v]) => `${n}:${v}`),
    ['setViewHooks:object', 'setLnbDocOpener:function', 'setLnbTopicPicker:function', 'setLiveRefresher:function']);
  assert.deepEqual(bind.boundSurfaceKeys(), ['viewHooks', 'liveRefresher', 'lnbDocOpener', 'lnbTopicPicker']);
  globalThis.__bound.length = 0;
  bind.unbindSurface();
  assert.deepEqual(globalThis.__bound,
    [['setViewHooks', null], ['setLiveRefresher', null], ['setLnbDocOpener', null], ['setLnbTopicPicker', null]]);
  assert.deepEqual(bind.boundSurfaceKeys(), []);
});

test('a route change clears every hook the graph binds, its topic picker and its all button included', async () => {
  const bind = await import(BIND_URL);
  bind.unbindSurface(); globalThis.__bound.length = 0;
  // 그래프(view.ts)가 거는 것, 건 순서대로: View 동작 · 착지 · "전체" · 주제 고르기 · 갱신, 나침반의 트리와 단추, 검색 거르개.
  bind.bindSurface({ viewHooks: {} });
  bind.bindSurface({ lnbNavigator: () => {} });
  bind.bindSurface({ lnbScopeClearer: () => false });
  bind.bindSurface({ lnbTopicPicker: () => {} });
  bind.bindSurface({ liveRefresher: async () => {} });
  bind.bindSurface({ lnbGraphSource: {}, topbarTools: new El('span') });
  bind.bindSurface({ searchFilter: () => {} });
  globalThis.__bound.length = 0;
  bind.unbindSurface();
  assert.deepEqual(globalThis.__bound.map(([n]) => n), ['setViewHooks', 'setLiveRefresher', 'setLnbNavigator',
    'setLnbTopicPicker', 'setLnbScopeClearer', 'setLnbGraphSource', 'setTopbarTools', 'setSearchFilter']);
  assert.ok(globalThis.__bound.every(([, v]) => v === null));
  assert.deepEqual(bind.boundSurfaceKeys(), []);
  globalThis.__bound.length = 0;
  bind.unbindSurface();
  assert.deepEqual(globalThis.__bound, [], 'nothing bound, nothing cleared');
});

test('a part unbinds only what it bound and leaves a newer binding of the same hook', async () => {
  const bind = await import(BIND_URL);
  bind.unbindSurface(); globalThis.__bound.length = 0;
  const offGraph = bind.bindSurface({ lnbGraphSource: {}, topbarTools: new El('span') });
  const offA = bind.bindSurface({ searchFilter: () => 'a' });
  offGraph();
  assert.deepEqual(globalThis.__bound.slice(-2), [['setLnbGraphSource', null], ['setTopbarTools', null]]);
  const offB = bind.bindSurface({ searchFilter: () => 'b' });
  globalThis.__bound.length = 0;
  offA();
  assert.deepEqual(globalThis.__bound, [], 'the newer filter stays');
  assert.deepEqual(bind.boundSurfaceKeys(), ['searchFilter']);
  offB(); offB();
  assert.deepEqual(globalThis.__bound, [['setSearchFilter', null]]);
  assert.deepEqual(bind.boundSurfaceKeys(), []);
});

// ── 쓰기 창에 걸린 문서 ─────────────────────────────────────────────────────────────────────────────────────────────
// 서버는 청크가 덜 쓰인 문서를 `executed:false` 에 청크 수를 달아 거절한다. 문서는 멀쩡히 있고 지금 이 순간만 반쪽이다.
// 그 상태를 두 리더(그래프의 열 view-reader.ts, 문서 편집기 doc-editor.ts)가 "못 열었다"와 다른 말로 하고,
// 사람이 누를 때만 다시 읽고, 편집기는 세우지 않는다. 편집기를 세우면 상시 편집이라 키 하나에 그 반쪽이 정본이 된다.
// 실제 코드: 판별기(api.ts) · 그래프 리더 · 열의 머리와 닫기(view-rail.ts) · 편집기 마운트(doc-editor.ts).
// 가짜: 서버 호출(`__doc.docGet`)과 CM6 편집기(`__doc.mountCm`).
globalThis.ResizeObserver ??= class { observe() {} disconnect() {} };
globalThis.__doc = {};

/** `docGet` 이 부른 차례대로 답을 준다(함수면 부른 때의 Promise). 마지막 답은 되풀이한다. */
function fakeDocs(...replies) {
  const calls = [];
  globalThis.__doc.docGet = (...a) => {
    calls.push(a);
    const r = replies[Math.min(calls.length - 1, replies.length - 1)];
    return typeof r === 'function' ? r() : Promise.resolve(r);
  };
  return calls;
}
/** 그래프 리더가 편집기를 세운 횟수. */
function fakeReaderMount() {
  const seen = { mounted: 0 };
  globalThis.__doc.mountEditor = async () => {
    seen.mounted++;
    return { dispose: async () => {}, getText: () => '', scrollToOffset() {}, pauseSaving: async () => {} };
  };
  return seen;
}
/** CM6 편집기 대역: 세운 수 · 부순 수 · 스크롤을 센다. */
function fakeCm() {
  const seen = { mounted: 0, destroyed: 0, scrolled: [] };
  globalThis.__doc.mountCm = (_host, o) => {
    seen.mounted++;
    return { getValue: () => o.initial, setValue() {}, focus() {}, destroy: () => { seen.destroyed++; },
      setHighlights() {}, scrollToHighlight() {}, scrollToOffset: (p) => seen.scrolled.push(p), lineTop: () => 0, openInsert() {} };
  };
  return seen;
}
const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };
const hintOf = (root) => root.querySelector('p.hint')?.textContent;
const openReaderOn = async () => {
  body.innerHTML = '';
  const r = await loadReader(body);
  return { ...r, open: (id = 'd1') => r.reader.openReader(id) };
};

test('docMidWrite splits a half-written document from a failed read by the chunk count, never by the message', async () => {
  const mid = await loadDocMidWrite();
  assert.equal(mid(MID_WRITE), true);
  assert.equal(mid({ executed: false, chunk_count: 0 }), true, 'a count of zero is still a count');
  assert.equal(mid(FAILED), false, 'an unknown id carries an error and no count');
  assert.equal(mid({ executed: false, error: 'document is incomplete: 1 of 3 chunks are present' }), false,
    'the message alone must not read as a write in progress');
  assert.equal(mid({ executed: true, chunk_count: 27 }), false, 'a read that worked is never "being written"');
});

test('the graph reader says "being written" for a half-written document, offers a retry and a way out, and builds no editor', async () => {
  const calls = fakeDocs(MID_WRITE);
  const seen = fakeReaderMount();
  const { open, detail } = await openReaderOn();
  const timers = watchTimers();
  await open();
  const armed = timers.stop();
  await settle(30);
  assert.equal(hintOf(detail), 'COPY.ui.viewx.readerMidWrite', 'the half-written wording, not the failed one');
  assert.equal(detail.querySelector('#rdr-retry').textContent, 'COPY.ui.viewx.readerRetry');
  assert.ok(detail.querySelector('#rail-close'), 'the rail head stays, so the mouse has a way out besides Esc');
  assert.equal(seen.mounted, 0, 'no editor over a half-written document');
  assert.deepEqual(armed, [], 'no timer is armed by this state');
  assert.equal(calls.length, 1, 'nothing reads again on its own');
});

test('the graph reader says "could not open" for a failed read, with no retry button', async () => {
  fakeDocs(FAILED);
  const seen = fakeReaderMount();
  const { open, detail } = await openReaderOn();
  await open();
  assert.equal(hintOf(detail), 'COPY.ui.viewx.readerFail');
  assert.equal(detail.querySelector('#rdr-retry'), null);
  assert.ok(detail.querySelector('#rail-close'));
  assert.equal(seen.mounted, 0);
});

test('the graph reader reads again only when the person presses retry, and then opens the document', async () => {
  const calls = fakeDocs(MID_WRITE, GOOD);
  const seen = fakeReaderMount();
  const { open, detail } = await openReaderOn();
  await open();
  assert.equal(calls.length, 1);
  detail.querySelector('#rdr-retry').click();
  await settle(30);
  assert.equal(calls.length, 2);
  assert.equal(seen.mounted, 1, 'the second read worked, so the editor is built now');
  assert.equal(detail.querySelector('#rdr-retry'), null);
  assert.ok(detail.querySelector('#rdr-host'));
});

test('the rail close button works while the reader shows a half-written document', async () => {
  fakeDocs(MID_WRITE);
  fakeReaderMount();
  const { open, detail } = await openReaderOn();
  await open();
  detail.querySelector('#rail-close').click();
  assert.equal(detail.hidden, true);
  assert.equal(detail.innerHTML, '');
});

test('the document editor says "being written", offers a retry, and builds no editor over a half-written document', async () => {
  const calls = fakeDocs(MID_WRITE);
  const cm = fakeCm();
  const ed = await loadDocEditor();
  const host = new El('div'); body.append(host);
  const timers = watchTimers();
  await ed.mountDocEditor(host, { owner: 'u', docId: 'd1' });
  const armed = timers.stop();
  await settle(30);
  assert.equal(hintOf(host), 'COPY.ui.viewx.readerMidWrite');
  assert.equal(host.querySelector('#ed-retry').textContent, 'COPY.ui.viewx.readerRetry');
  assert.equal(host.querySelector('#ed-src'), null, 'no textarea to type into');
  assert.equal(cm.mounted, 0);
  assert.deepEqual(armed, [], 'no timer is armed by this state');
  assert.equal(calls.length, 1);
});

test('the document editor says "could not open" for a failed read, with no retry button', async () => {
  fakeDocs(FAILED);
  const cm = fakeCm();
  const ed = await loadDocEditor();
  const host = new El('div'); body.append(host);
  await ed.mountDocEditor(host, { owner: 'u', docId: 'd1' });
  assert.equal(hintOf(host), 'COPY.ui.viewx.readerFail');
  assert.equal(host.querySelector('#ed-retry'), null);
  assert.equal(cm.mounted, 0);
});

test('after a retry the stub the caller holds drives the editor the retry built', async () => {
  const calls = fakeDocs(MID_WRITE, GOOD);
  const cm = fakeCm();
  const ed = await loadDocEditor();
  const host = new El('div'); body.append(host);
  const stub = await ed.mountDocEditor(host, { owner: 'u', docId: 'd1' });
  assert.equal(stub.getText(), '', 'nothing to copy while the document is unread');
  host.querySelector('#ed-retry').click();
  await settle(30);
  assert.equal(calls.length, 2);
  assert.equal(cm.mounted, 1);
  assert.equal(stub.getText(), GOOD.text);
  stub.scrollToOffset(7, 'start');
  assert.deepEqual(cm.scrolled, [7]);
  await stub.dispose();
  assert.equal(cm.destroyed, 1, 'closing the stub closes the editor behind it');
});

test('closing during the retry round trip throws away the editor that arrives', async () => {
  const late = deferred();
  fakeDocs(MID_WRITE, () => late.promise);
  const cm = fakeCm();
  const ed = await loadDocEditor();
  const host = new El('div'); body.append(host);
  const stub = await ed.mountDocEditor(host, { owner: 'u', docId: 'd1' });
  host.querySelector('#ed-retry').click();
  await stub.dispose();
  late.resolve(GOOD);
  await settle(30);
  assert.equal(cm.mounted, 1, 'the read finished and built an editor');
  assert.equal(cm.destroyed, 1, 'it is dropped at once: nobody holds it any more');
});
