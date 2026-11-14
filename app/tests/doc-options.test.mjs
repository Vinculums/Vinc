// 읽기 설정: 저장된 값을 읽어 `<html>` 의 `data-doc-*` 로 얹는다. 모르는 값은 그 칸만 기본값이고,
// 기본값인 칸은 속성을 지워 CSS 의 기본이 그대로 살게 한다(doc-options.ts).
// 아래 "뷰 머리" 절은 앱의 진짜 머리 마크업(index.html 의 `#topbar`)을 mini-dom 에 세우고 topbar.ts 를 돌려 본다:
// 칸의 순서와 자리, 앞자리와 그래프 도구 칸 채우기, 본문 스크롤바 폭 비우기, 읽기 설정 단추 칸.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import { installDom, El } from './mini-dom.mjs';
import { tsUrl, loadTs, COPY_STUB, COMPONENTS_STUB } from './ts-load.mjs';

const DOC_OPTIONS_STUBS = {
  './copy': COPY_STUB,
  './icons': 'export const icon = () => "";',
  './motion': 'export const popIn = () => null; export const popOut = async () => {};',
  './components': COMPONENTS_STUB + '\nexport const wireOutsideDismiss = () => () => {};',
  './float-panel': 'export const floatCeiling = () => 0;',
};
const DOC_OPTIONS_URL = await tsUrl('doc-options.ts', DOC_OPTIONS_STUBS);
const t = await import(DOC_OPTIONS_URL);

const fakeRoot = () => {
  const a = new Map();
  return { a, setAttribute: (k, v) => a.set(k, v), removeAttribute: (k) => a.delete(k) };
};

test('nothing stored reads as the defaults', () => {
  assert.deepEqual(t.readDocPrefs(null), t.DOC_DEFAULTS);
  assert.deepEqual(t.readDocPrefs('not json'), t.DOC_DEFAULTS);
});

test('an unknown value falls back for that field only', () => {
  const p = t.readDocPrefs(JSON.stringify({ text: 'huge', width: 'wide', lines: 'relaxed' }));
  assert.deepEqual(p, { text: 'medium', width: 'wide', lines: 'relaxed' });
});

test('defaults clear the attributes and choices set them', () => {
  const r = fakeRoot();
  t.applyDocPrefs({ text: 'large', width: 'standard', lines: 'compact' }, r);
  assert.deepEqual([...r.a], [['data-doc-text', 'large'], ['data-doc-lines', 'compact']]);
  t.applyDocPrefs(t.DOC_DEFAULTS, r);
  assert.equal(r.a.size, 0);
});

// ── 뷰 머리 ──────────────────────────────────────────────────────────────────────────────────────────────────────
const { body } = installDom();
document.getElementById = (id) => body.querySelector(`#${id}`);
document.documentElement.setAttribute = function (k, v) { (this.attrs ||= new Map()).set(k, String(v)); };
document.documentElement.removeAttribute = function (k) { this.attrs?.delete(k); };
document.documentElement.getAttribute = function (k) { return this.attrs?.get(k) ?? null; };
document.documentElement.style = { setProperty() {} };
// 본문 스크롤바 폭을 재는 관찰자: 만든 것의 콜백을 잡아 두고 테스트가 부른다.
const observers = [];
globalThis.ResizeObserver = class { constructor(cb) { observers.push(cb); } observe() {} disconnect() {} };

const indexHtml = await readFile(new URL('../index.html', import.meta.url), 'utf8');
body.innerHTML = indexHtml.slice(indexHtml.indexOf('<body'), indexHtml.indexOf('</body>'));
const shell = body.querySelector('#shell');
shell.style.setProperty = (k, v) => { shell.style[k] = v; };
const header = body.querySelector('#topbar');
const treeOrder = (id) => [...body.walk()].findIndex((e) => e.id === id);

const topbar = await loadTs('topbar.ts', {
  './api': 'export const api = {}; export const cfg = { owner: "u", team: "" };',
  './copy': COPY_STUB,
  './search-relevance': 'export const partitionSearchResults = () => ({});',
  './doc-title': 'export const docTitle = (t) => t || "";',
  './icons': 'export const icon = () => "";',
  './components': COMPONENTS_STUB + '\nexport const armsNotice = () => "";',
  './keys': 'export const pushLayer = () => () => {}; export const shortcutsRest = () => false;',
  './view-prefs': 'export const dayOf = (ts) => ts.slice(0, 10);',
  './search-error': 'export const renderSearchError = () => "";',
  './vinc-loaders.js': 'export const loaderInline = (s) => String(s);',
  './lnb': 'export const documentsHome = () => "#home";',
  './doc-options': DOC_OPTIONS_URL,
  './ask': 'export const initAsk = () => {}; export const askClose = () => {};', './tools': 'export {};', './surfaces/records': 'export {};', './doc-editor': 'export {};',
});

test('the header keeps its slots in reading order, with search, graph tools, switch and options in the trail', () => {
  const ids = ['tb-space', 'tb-lead', 'tb-find', 'tb-graph-tools', 'tb-switch', 'tb-docopts'];
  const at = ids.map(treeOrder);
  assert.ok(at.every((i) => i >= 0), `every slot exists: ${at}`);
  assert.deepEqual([...at].sort((a, b) => a - b), at, 'space, lead, search, graph tools, switch, options');
  const trail = header.querySelector('.tb-trail');
  for (const id of ['tb-find', 'tb-graph-tools', 'tb-switch', 'tb-docopts']) assert.ok(trail.contains(body.querySelector(`#${id}`)), `${id} is in the trail`);
  for (const id of ['tb-space', 'tb-lead']) assert.ok(!trail.contains(body.querySelector(`#${id}`)), `${id} is before the trail`);
});

test('the document | graph switch has exactly two segments', () => {
  assert.deepEqual(header.querySelectorAll('#tb-switch > button').map((b) => b.id), ['tb-to-docs', 'tb-to-graph']);
});

test('no view card, viewpoint segment or lens button is in the shell markup', () => {
  const gone = /^tb-(viewctl|viewpoints|lens|card)\b/;
  const found = [...body.walk()].filter((e) => gone.test(e.id) || e.classList.list().some((c) => gone.test(c)));
  assert.deepEqual(found.map((e) => e.id || e.className), []);
});

test('the surface fills the lead slot, and null empties it', () => {
  const lead = topbar.setTopbarLead('<span class="tb-lead-name">Docs</span>');
  assert.equal(lead, body.querySelector('#tb-lead'));
  assert.equal(body.querySelector('#tb-lead .tb-lead-name').textContent, 'Docs');
  assert.equal(body.querySelector('#tb-graph-tools').childElementCount, 0, 'the lead never lands in the tools slot');
  topbar.setTopbarLead(null);
  assert.equal(body.querySelector('#tb-lead').innerHTML, '');
});

test('the graph tools stand in their own slot beside the switch, not in the lead, and the last set leaves when the next arrives', () => {
  const a = new El('span');
  const b = new El('span');
  topbar.setTopbarTools(a);
  const slot = body.querySelector('#tb-graph-tools');
  assert.equal(a.parent, slot);
  assert.ok(treeOrder('tb-graph-tools') < treeOrder('tb-switch'));
  assert.equal(body.querySelector('#tb-lead').childElementCount, 0);
  topbar.setTopbarTools(b);
  assert.equal(a.parent, null, 'the older tools are taken out');
  assert.equal(b.parent, slot);
  topbar.setTopbarTools(null);
  assert.equal(slot.childElementCount, 0, 'an empty slot (CSS hides it)');
});

// 머리를 한 번 세운다(topbar.ts 는 두 번째 호출을 무시한다). 본문은 스크롤바가 15px 인 표면이고, 저장된 읽기 값이 하나 있다.
Object.assign(body.querySelector('#surface'), { offsetWidth: 1000, clientWidth: 985 });
localStorage.setItem('vinc.doc.prefs', JSON.stringify({ text: 'large' }));
let mounted = false;
const mountHeader = () => { if (!mounted) { mounted = true; topbar.mountTopbar(); } };

test('mounting the header leaves room for the body scrollbar and follows its width', () => {
  mountHeader();
  assert.equal(shell.style['--sb-w'], '15px');
  Object.assign(body.querySelector('#surface'), { clientWidth: 1000 });
  for (const cb of observers) cb();
  assert.equal(shell.style['--sb-w'], '0px', 'the scrollbar went away');
});

test('mounting the header applies the stored reading choices, and the options button slot stays hidden and empty', () => {
  mountHeader();
  assert.equal(document.documentElement.getAttribute('data-doc-text'), 'large');
  assert.equal(document.documentElement.getAttribute('data-doc-width'), null, 'a default choice leaves no attribute');
  const slot = body.querySelector('#tb-docopts');
  assert.equal(slot.hidden, true);
  assert.equal(slot.innerHTML, '');
});
