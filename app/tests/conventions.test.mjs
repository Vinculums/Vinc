import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

// conventions.ts 를 그대로 싣고 api · copy · components 만 갈아 끼운다. 화면은 아주 작은 가짜 DOM 이
// 받는다: innerHTML 을 받으면 id · class · data-act 를 단 태그만 요소로 세우고, textarea 와 pre 의
// 본문을 값으로 둔다. 근거: [[vinc.frd.app]] FR-APP-39
const url = (js) => `data:text/javascript;base64,${Buffer.from(js).toString('base64')}`;
let js = ts.transpileModule(await readFile(new URL('../src/conventions.ts', import.meta.url), 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const stubs = {
  './api': `export const cfg = { owner: 'o' };
    export const remote = () => globalThis.__conv.remote;
    export const api = new Proxy({}, { get: (o, k) => (...a) => globalThis.__conv.call(k, a) });`,
  './copy': `export const COPY = { ui: { conv: new Proxy({}, { get: (o, k) => k === 'pending' ? (n) => 'pending:' + n : k }) } };`,
  './components': `export const esc = (s) => String(s);
    export const sheetActions = (...b) => b.map((x) => '<button id="' + x.id + '">' + x.label + '</button>').join('');`,
};
for (const [from, to] of Object.entries(stubs)) js = js.replace(JSON.stringify(from), JSON.stringify(url(to)));
const { mountConventions, refreshBadge } = await import(url(js));

/** innerHTML 을 받으면 자식을 다시 세우는 가짜 요소. 셀렉터는 `#id` · `.class` · `[data-act]` 와 쉼표 묶음만 안다. */
function el(tag = 'div', attrs = {}, text = '') {
  const node = { tag, attrs, value: text, textContent: text, children: [], listeners: {}, isConnected: true,
    disabled: false, readOnly: false, hidden: false,
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
    async click() { await Promise.all((this.listeners.click || []).map((fn) => fn())); },
    setAttribute(k, v) { this.attrs[k] = v; }, removeAttribute(k) { delete this.attrs[k]; },
    get dataset() { return { act: this.attrs['data-act'], name: this.attrs['data-name'] }; },
    querySelectorAll(sel) { return this.children.filter((c) => sel.split(',').some((s) => matches(c, s.trim()))); },
    querySelector(sel) { return this.querySelectorAll(sel)[0] ?? null; },
    get innerHTML() { return this._html; },
    set innerHTML(html) {
      this._html = html;
      this.children = [];
      const re = /<(textarea|pre)([^>]*)>([\s\S]*?)<\/\1>|<(\w+)([^>]*)>/g;
      for (const m of html.matchAll(re)) {
        const raw = m[2] ?? m[5];
        const a = Object.fromEntries([...raw.matchAll(/([\w-]+)="([^"]*)"/g)].map((x) => [x[1], x[2]]));
        if (a.id || a.class || a['data-act']) this.children.push(el(m[1] ?? m[4], a, m[3] ?? ''));
      }
    },
  };
  node._html = '';
  return node;
}
function matches(node, sel) {
  if (sel.startsWith('#')) return node.attrs.id === sel.slice(1);
  if (sel.startsWith('.')) return (node.attrs.class || '').split(/\s+/).includes(sel.slice(1));
  if (sel === '[data-act]') return 'data-act' in node.attrs;
  return false;
}

const GEN = '<!-- vinc:generated start v1 -->\n- concept: things\n<!-- vinc:generated end -->';
const DOC = `# My conventions\n\nprose before\n${GEN}\nprose after`;

async function mount({ text = DOC, remote = false, types = [], putOk = true } = {}) {
  const calls = [];
  globalThis.__conv = { remote, call: async (op, args) => {
    calls.push([op, args]);
    if (op === 'conventions') return { text, doc_id: 'doc:graph-md' };
    if (op === 'docPut') return { executed: putOk };
    if (op === 'conventionsBuild') return { executed: true, text: `rebuilt\n${GEN}\n` };
    if (op === 'themes') return { active_theme_id: 'theme:1' };
    if (op === 'types') return { types };
    return {};
  } };
  const host = el(), sheet = { body: el(), foot: el(), close() {} };
  await mountConventions(host, sheet);
  return { host, sheet, calls, ops: () => calls.map((c) => c[0]) };
}

test('the generated block is shown read only and the prose around it is editable', async () => {
  const { host } = await mount();
  const head = host.querySelector('#conv-head');
  assert.equal(head.tag, 'textarea');
  assert.match(head.value, /prose before/);
  assert.doesNotMatch(head.value, /vinc:generated/);
  const gen = host.querySelector('.conv-gen');
  assert.equal(gen.tag, 'pre');
  assert.equal(gen.attrs['aria-readonly'], 'true');
  assert.match(gen.textContent, /concept: things/);
  assert.match(host.querySelector('#conv-tail').value, /prose after/);
  assert.equal(host.querySelectorAll('.conv-ta').length, 2);
});

test('a broken marker pair edits the whole file in one box', async () => {
  for (const text of [`a\n<!-- vinc:generated start -->\nb`, `${GEN}\n${GEN}`, `<!-- vinc:generated end -->\n<!-- vinc:generated start -->`]) {
    const { host } = await mount({ text });
    assert.equal(host.querySelector('.conv-gen'), null);
    assert.equal(host.querySelector('#conv-head').value, text);
    assert.ok(host.querySelector('.conv-warn'));
  }
});

test('rebuild saves the edited prose before it asks for a rebuild', async () => {
  const s = await mount();
  s.host.querySelector('#conv-head').value = '# My conventions\n\nnew sentence';
  await s.sheet.foot.querySelector('#conv-rebuild').click();
  const ops = s.ops().filter((o) => o === 'docPut' || o === 'conventionsBuild');
  assert.deepEqual(ops, ['docPut', 'conventionsBuild']);
  const put = s.calls.find((c) => c[0] === 'docPut')[1];
  assert.match(put[1], /new sentence/);
  assert.match(put[1], /vinc:generated start/);
  assert.equal(put[2], 'doc:graph-md');
});

test('rebuild does not run when the save fails', async () => {
  const s = await mount({ putOk: false });
  await s.sheet.foot.querySelector('#conv-rebuild').click();
  assert.ok(s.ops().includes('docPut'));
  assert.ok(!s.ops().includes('conventionsBuild'));
  assert.equal(s.sheet.foot.querySelector('#conv-status').textContent, 'saveFailed');
});

test('vocabulary approval is offered only where the store is on this device', async () => {
  const types = [{ name: 'Proposed', kind: 'node', provenance: 'ingest_proposed' },
    { name: 'Seeded', kind: 'node', provenance: 'system_draft' }];
  const web = await mount({ remote: true, types });
  assert.match(web.host.querySelector('#conv-ratify').innerHTML, /webOnly/);
  assert.ok(!web.ops().includes('themes'));
  const local = await mount({ remote: false, types });
  const ratify = local.host.querySelector('#conv-ratify');
  assert.match(ratify.innerHTML, /pending:1/);
  assert.deepEqual(ratify.querySelectorAll('[data-act]').map((b) => [b.dataset.act, b.dataset.name]),
    [['rename', 'Proposed'], ['reject', 'Proposed'], ['ratify', 'Proposed']]);
});

test('the badge counts only what an assistant proposed, and is empty on remote graphs', async () => {
  const badges = { 'tb-conv-badge': el(), 'rail-settings-badge': el(), 'settings-graphmd-badge': el() };
  globalThis.document = { getElementById: (id) => badges[id] ?? null };
  const types = [{ provenance: 'ingest_proposed' }, { provenance: 'ingest_proposed' }, { provenance: 'system_draft' }];
  const settle = () => new Promise((r) => setTimeout(r, 0));
  await mount({ remote: false, types });
  refreshBadge(); await settle();
  assert.deepEqual(Object.values(badges).map((b) => [b.textContent, b.hidden]), [['2', false], ['2', false], ['2', false]]);
  globalThis.__conv.remote = true;
  refreshBadge(); await settle();
  assert.deepEqual(Object.values(badges).map((b) => [b.textContent, b.hidden]), [['', true], ['', true], ['', true]]);
});
