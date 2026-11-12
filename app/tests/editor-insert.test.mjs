// AC-APP-15: 삽입 목록(우클릭 · + 버튼 · 슬래시)과 `[[` 노드 찾기. 진짜 cm-insert · cm-assist 를
// @codemirror/state 의 상태 위에서 돌린다(EditorView 는 화면이 있어야 서므로 dispatch 만 하는 대역).
// 근거: [[vinc.bdd.app]] AC-APP-15 · [[vinc.frd.app]] FR-APP-15
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { EditorState, EditorSelection } from '@codemirror/state';
import { installDom, settle } from './mini-dom.mjs';
import { loadTs, tsUrl, srcText, copyStub, copyBlock, COMPONENTS_STUB } from './ts-load.mjs';

installDom();
// 메뉴가 여닫힐 때 층의 쌓임에 오르내린다. 이 테스트는 그 쌓임을 보지 않는다(dev/shortcut-rest-check.ts 가 본다).
const KEYS_STUB = 'export const pushLayer = () => () => {};';
const cmInsertUrl = await tsUrl('cm-insert.ts', { './components': COMPONENTS_STUB, './keys': KEYS_STUB });
const cmi = await import(cmInsertUrl);
const assist = await loadTs('cm-assist.ts', { './components': COMPONENTS_STUB, './keys': KEYS_STUB });
const COPY = copyStub(`{ ui: { viewx: { slash: ${await copyBlock('slash')}, ins: ${await copyBlock('ins')} } } }`);
const editor = await loadTs('doc-editor.ts', {
  './api': `export const api = {}; export const cfg = { owner: 'u' }; export const docMidWrite = () => false;
    export const storeDownReason = () => ''; export const shellInvoke = async () => null;`,
  './copy': COPY,
  './doc-title': `export const docTitle = (t) => (t || '').trim() || 'Untitled';`,
  './rich-blocks': `export const hydrateRich = () => {};`,
  './components': COMPONENTS_STUB,
  './vinc-loaders.js': `export const loaderRows = () => '';`,
  './cm-insert': cmInsertUrl,
  './codemirror-editor': `export const mountEditor = () => { throw new Error('no editor in tests'); };`,
  './authoring': `export const findNodes = async () => ({ hits: [], note: '' });`,
  './extract-list': `export const offerExtraction = async () => {};`,
  './node-composer': `export const openComposer = async () => null;`,
  './doc-delete': `export const deleteDocument = async () => 'unknown';`,
  './lnb': `export const refreshLnb = async () => {};`,
});
const { blocks, labels } = editor.insertOpts();

/** 글자와 커서만 가진 편집기 대역. `|` 가 커서, `{` `}` 가 고른 범위다. */
function viewOf(marked) {
  const a = marked.indexOf('{'), b = marked.indexOf('}'), c = marked.indexOf('|');
  const doc = marked.replace(/[{}|]/g, '');
  const sel = c >= 0 ? EditorSelection.single(c) : EditorSelection.single(a, b - 1);
  const v = {
    state: EditorState.create({ doc, selection: sel }),
    dispatch(spec) { v.state = v.state.update(spec).state; },
    focus() {},
    coordsAtPos: () => ({ left: 0, right: 0, top: 0, bottom: 0 }),
    dom: { isConnected: true },
  };
  return v;
}
const text = (v) => v.state.doc.toString();
const caret = (v) => v.state.selection.main.head;
const block = (k) => blocks.find((b) => b.key === k);

test('one list of twelve blocks backs Insert, right click and slash', async () => {
  assert.deepEqual(blocks.map((b) => b.key),
    ['h1', 'h2', 'h3', 'bullet', 'number', 'task', 'quote', 'table', 'code', 'math', 'diagram', 'hr']);
  assert.deepEqual(blocks.map((b) => b.label), ['Heading', 'Subheading', 'Small heading', 'Bulleted list',
    'Numbered list', 'Checklist', 'Quote', 'Table', 'Code block', 'Math', 'Diagram', 'Divider']);
  // 문 셋이 같은 목록을 받는다: 편집기는 `insert` 하나로 슬래시와 삽입 메뉴를 세우고, 우클릭과
  // + 버튼은 같은 삽입 메뉴를 연다(우클릭만 잘라내기 · 복사 · 붙여넣기를 더한다).
  const cm = await srcText('codemirror-editor.ts');
  assert.match(cm, /slash = new SlashMenu\(view, opts\.insert\.blocks\)/);
  assert.match(cm, /inserter = new InsertMenu\(view, opts\.insert\)/);
  assert.match(cm, /contextmenu: \(e, v\) => \{[\s\S]*?inserter\.show\(\{ x: e\.clientX, y: e\.clientY \}, \{ clipboard: true \}\)/);
  assert.match(cm, /openInsert: \(at\) => \{[\s\S]*?inserter\.show\(at, \{ clipboard: false \}\)/);
  assert.match(cm, /this\.items = filterBlocks\(this\.list, query\)/);
  assert.match(cm, /insertBlock\(this\.view, it, \[3, 3\], true\)/);
  const ed = await srcText('doc-editor.ts');
  assert.match(ed, /insert: insertOpts\(\)/);
  assert.match(ed, /cm\?\.openInsert\(/);
});

test('the insert menu lists every block, formats only with a selection and the clipboard only on right click', () => {
  const plain = viewOf('Some text|');
  const menu = new cmi.InsertMenu(plain, { blocks, labels });
  menu.show({ x: 10, y: 10 }, { clipboard: false });
  const dom = document.body.children.at(-1);
  assert.equal(dom.querySelectorAll('.cm-ins-row[data-i]').length, 12);
  assert.equal(dom.querySelectorAll('[data-fmt]').length, 0);
  assert.equal(dom.querySelectorAll('[data-clip]').length, 0);
  menu.hide();

  const sel = viewOf('Some {text}');
  const ctx = new cmi.InsertMenu(sel, { blocks, labels });
  ctx.show({ x: 10, y: 10 }, { clipboard: true });
  const dom2 = document.body.children.at(-1);
  assert.equal(dom2.querySelectorAll('.cm-ins-row[data-i]').length, 12);
  assert.deepEqual(dom2.querySelectorAll('[data-fmt]').map((b) => b.dataset.fmt), ['bold', 'italic', 'strike', 'code', 'link']);
  assert.deepEqual(dom2.querySelectorAll('[data-clip]').map((b) => b.dataset.clip), ['cut', 'copy', 'paste']);
  // 서식은 마크다운 기호로 씌우고, 같은 것을 다시 누르면 벗긴다.
  dom2.querySelector('[data-fmt="bold"]').click();
  assert.equal(text(sel), 'Some **text**');
  cmi.applyFormat(sel, 'bold');
  assert.equal(text(sel), 'Some text');
});

test('choosing a block from the menu inserts markdown source', () => {
  const v = viewOf('Intro\n|');
  const menu = new cmi.InsertMenu(v, { blocks, labels });
  menu.show({ x: 0, y: 0 }, { clipboard: false });
  const dom = document.body.children.at(-1);
  dom.querySelector(`.cm-ins-row[data-i="${blocks.indexOf(block('math'))}"]`).click();
  assert.equal(text(v), 'Intro\n\n$$\n\n$$\n');
  assert.equal(caret(v), 'Intro\n\n$$\n'.length);
});

test('on an empty line each block puts in its markdown', () => {
  const want = { h1: '# ', h2: '## ', h3: '### ', bullet: '- ', number: '1. ', task: '- [ ] ', quote: '> ',
    code: '```\n\n```\n', math: '$$\n\n$$\n', diagram: '```mermaid\ngraph TD\n  A --> B\n```\n', hr: '---\n' };
  for (const [k, out] of Object.entries(want)) {
    const v = viewOf('|');
    cmi.insertBlock(v, block(k));
    assert.equal(text(v), out, k);
  }
  const t = viewOf('|');
  cmi.insertBlock(t, block('table'), [2, 3]);
  assert.equal(text(t), '| Column 1 | Column 2 | Column 3 |\n| --- | --- | --- |\n|   |   |   |\n');
  // 커서는 첫 본문 칸 안에 선다.
  assert.equal(text(t).slice(caret(t) - 2, caret(t)), '| ');
  assert.ok(caret(t) > text(t).indexOf('---'));
});

test('a line marker on a written line changes the line kind and keeps the words', () => {
  const v = viewOf('- buy milk|');
  cmi.insertBlock(v, block('h2'));
  assert.equal(text(v), '## buy milk');
  cmi.insertBlock(v, block('task'));
  assert.equal(text(v), '- [ ] buy milk');
  assert.equal(caret(v), text(v).length);
});

test('a divider after a paragraph gets a blank line so it cannot turn the paragraph into a heading', () => {
  const v = viewOf('A paragraph|\nNext line');
  cmi.insertBlock(v, block('hr'));
  // 앞뒤로 빈 줄이 선다. 아랫줄이 글이면 그 앞에는 빈 줄이 **둘** 남는다(지금 동작: 뒤 띄움
  // "\n\n" 에 원래 줄바꿈이 더해진다). 렌더는 같으므로 그대로 고정해 두고 보고한다.
  assert.equal(text(v), 'A paragraph\n\n---\n\n\nNext line');
  const w = viewOf('A paragraph\n|\nNext line');
  cmi.insertBlock(w, block('hr'));
  assert.equal(text(w), 'A paragraph\n\n---\n\n\nNext line');
  assert.doesNotMatch(text(w), /A paragraph\n---/, 'the divider never sits right under the paragraph');
});

test('slash filters the same blocks and replaces the slash line', () => {
  assert.equal(cmi.filterBlocks(blocks, '').length, 12);
  assert.equal(cmi.filterBlocks(blocks, 'ma')[0].key, 'math');
  // 낱말 머리가 맞는 것(목록 둘)이 속에서 맞는 것(Checklist)보다 먼저 선다.
  assert.deepEqual(cmi.filterBlocks(blocks, 'list').map((b) => b.key), ['bullet', 'number', 'task']);
  assert.equal(cmi.filterBlocks(blocks, 'mermaid')[0].key, 'diagram');
  assert.deepEqual(cmi.filterBlocks(blocks, 'zzz'), []);
  const v = viewOf('Intro\n/tab|');
  cmi.insertBlock(v, cmi.filterBlocks(blocks, 'tab')[0], [3, 3], true);
  assert.ok(text(v).startsWith('Intro\n\n| Column 1 | Column 2 | Column 3 |\n| --- | --- | --- |\n'), text(v));
  assert.ok(!text(v).includes('/tab'));
});

test('two open brackets find nodes and put in the picked title', async () => {
  const { MentionMenu, MENTION_RE } = assist;
  assert.equal(MENTION_RE.exec('see [[Gra')[1], 'Gra');
  assert.equal(MENTION_RE.exec('see [[Graph]] and'), null);
  assert.equal(MENTION_RE.exec(`[[${'x'.repeat(61)}`), null);
  const searched = [];
  const o = {
    search: async (q) => { searched.push(q); return { hits: [{ id: 'concept:graph', title: 'Graph store', domain: 'vinc/core' }], note: '' }; },
    create: async (q) => `Made ${q}`,
    createLabel: (q) => `Create concept: ${q}`, noneLabel: 'No match',
  };
  const v = viewOf('see [[Gra|');
  const m = new MentionMenu(v, o);
  m.update(4, 'Gra');
  await settle(200);
  assert.deepEqual(searched, ['Gra']);
  // 행 = 찾은 노드 + 마지막 줄 "개념 만들기".
  const rowsEl = m.dom.querySelectorAll('.cm-assist-row');
  assert.deepEqual(rowsEl.map((r) => r.querySelector('.cm-assist-name').textContent), ['Graph store', 'Create concept: Gra']);
  await m.choose(0);
  assert.equal(text(v), 'see Graph store');

  const w = viewOf('x [[New idea|');
  const n = new MentionMenu(w, o);
  n.update(2, 'New idea');
  await settle(200);
  await n.choose(1);
  assert.equal(text(w), 'x Made New idea');
});

test('a created concept is not put in when the text moved on meanwhile', async () => {
  let release;
  const o = { search: async () => ({ hits: [], note: '' }), create: () => new Promise((r) => { release = r; }),
    createLabel: (q) => q, noneLabel: '' };
  const v = viewOf('[[Idea|');
  const m = new assist.MentionMenu(v, o);
  m.update(0, 'Idea');
  const done = m.choose(0);
  v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: 'rewritten' } });
  release('Idea');
  await done;
  assert.equal(text(v), 'rewritten');
});
