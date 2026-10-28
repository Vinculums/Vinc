import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

// ask.ts 를 그대로 싣고, 가져오는 모듈(api · copy · components · topbar · loaders)만 갈아 끼운다.
// 화면은 그리지 않는다: 패널은 innerHTML 만 받는 빈 상자이고, 판정은 저장된 턴(`vinc.ask.log`)에서 읽는다.
// 근거: [[vinc.frd.app]] FR-APP-31 · FR-APP-32
const url = (js) => `data:text/javascript;base64,${Buffer.from(js).toString('base64')}`;
const src = ts.transpileModule(await readFile(new URL('../src/ask.ts', import.meta.url), 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const stubs = {
  './api': `export const cfg = { owner: 'o' };
    export const api = { search: (q) => globalThis.__ask.search(q),
      nodeContext: (owner, id, n) => globalThis.__ask.context(id, n) };`,
  './copy': `export const COPY = { ui: { ask: new Proxy({}, { get: (o, k) => typeof k === 'string'
    ? (k === 'relTag' ? {} : (...a) => k + a.join(' ')) : undefined }) } };`,
  './components': `export const esc = (s) => String(s);
    export const openSheet = () => ({ body: {}, close() {} });
    export const armsNotice = () => null;`,
  './topbar': 'export const viewHooks = () => null;',
  './vinc-loaders.js': 'export const loaderInline = (s) => s;',
};

const store = new Map();
globalThis.localStorage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, String(v)) };
globalThis.requestAnimationFrame = () => 0;

let fresh = 0;
/** 모듈 상태(턴 목록)가 테스트끼리 섞이지 않게 매번 새 판을 싣는다. */
async function loadAsk() {
  let js = src;
  for (const [from, to] of Object.entries(stubs)) js = js.replace(JSON.stringify(from), JSON.stringify(url(to)));
  const mod = await import(url(`${js}\n// ${fresh++}`));
  mod.initAsk({ hidden: true, innerHTML: '', querySelector: () => null, querySelectorAll: () => [] }, () => {});
  return mod;
}
/** 질문 하나를 보내고 Vinc 의 마지막 카드를 돌려준다. */
async function ask(q, hits, contexts = {}) {
  store.clear();
  const seen = [];
  globalThis.__ask = {
    search: async () => ({ node_hits: hits }),
    context: async (id) => { seen.push(id); return contexts[id] ?? { title: id }; },
  };
  const mod = await loadAsk();
  await mod.askSubmit(q);
  const turns = JSON.parse(store.get('vinc.ask.log'));
  return { card: turns.at(-1).card, seen };
}
const hit = (id, title, hit_tokens = 0, title_tokens = 0) => ({ id, title, domain: 'd', hit_tokens, title_tokens });

test('an exact title answers even when other hits score higher', async () => {
  const { card } = await ask('Payment failure', [hit('a', 'Idempotency key', 3, 3), hit('b', 'payment failure', 1, 1)]);
  assert.equal(card.kind, 'answer');
  assert.equal(card.id, 'b');
});

test('a lone hit answers', async () => {
  const { card } = await ask('anything', [hit('only', 'Only one', 0, 0)]);
  assert.equal(card.kind, 'answer');
  assert.equal(card.id, 'only');
});

test('a first hit that beats the second on matched or title tokens answers', async () => {
  const byTokens = await ask('what about payment', [hit('a', 'A', 1, 0), hit('b', 'B', 2, 0)]);
  assert.equal(byTokens.card.kind, 'answer');
  assert.equal(byTokens.card.id, 'b');
  const byTitle = await ask('what about payment', [hit('a', 'A', 2, 0), hit('b', 'B', 2, 1)]);
  assert.equal(byTitle.card.kind, 'answer');
  assert.equal(byTitle.card.id, 'b');
});

test('a tie asks back with at most three candidates and opens none', async () => {
  const hits = ['a', 'b', 'c', 'd', 'e'].map((id) => hit(id, id.toUpperCase(), 2, 1));
  const { card, seen } = await ask('which', hits);
  assert.equal(card.kind, 'choose');
  assert.deepEqual(card.options.map((o) => o.id), ['a', 'b', 'c']);
  assert.deepEqual(seen, []);
});

test('hits with no matched token never resolve, however they are ordered', async () => {
  const { card, seen } = await ask('how does it work', [hit('a', 'A', 0, 0), hit('b', 'B', 0, 0)]);
  assert.equal(card.kind, 'choose');
  assert.deepEqual(seen, []);
});

test('no node hits says nothing matched', async () => {
  const { card } = await ask('nothing here', []);
  assert.deepEqual(card, { kind: 'none', query: 'nothing here' });
});

test('with no one-liner the body is quoted from props, named keys first, then the longest', async () => {
  const named = await ask('x', [hit('n', 'N')], { n: { title: 'N', one_liner: '', props: { other: 'a much longer sentence than the note', note: 'the note' } } });
  assert.equal(named.card.body, 'the note');
  assert.equal(named.card.bodySrc, 'props');
  const longest = await ask('x', [hit('n', 'N')], { n: { title: 'N', props: { a: 'short', b: 'twelve chars', c: 'this one is the longest value' } } });
  assert.equal(longest.card.body, 'this one is the longest value');
  const oneLiner = await ask('x', [hit('n', 'N')], { n: { title: 'N', one_liner: 'stated', props: { note: 'the note' } } });
  assert.equal(oneLiner.card.bodySrc, 'one_liner');
  const excerpt = await ask('x', [hit('n', 'N')], { n: { title: 'N', props: { a: 'too short' },
    excerpts: [{ excerpt: 'from the doc', doc_title: 'Doc', section: '2' }] } });
  assert.deepEqual([excerpt.card.body, excerpt.card.bodySrc, excerpt.card.bodyCite], ['from the doc', 'doc', 'Doc · 2']);
});

test('related nodes stop at six and the total is kept', async () => {
  const items = Array.from({ length: 9 }, (_, i) => ({ id: `r${i}`, title: `R${i}`, confidence: i / 10 }));
  const { card } = await ask('x', [hit('n', 'N')], { n: { title: 'N', links: { items } } });
  assert.equal(card.related.length, 6);
  assert.equal(card.relatedTotal, 9);
  assert.equal(card.related[0].id, 'r8');
});

test('the log keeps the last 60 turns', async () => {
  store.clear();
  const old = Array.from({ length: 70 }, (_, i) => ({ role: 'you', at: i, text: `q${i}` }));
  store.set('vinc.ask.log', JSON.stringify(old));
  globalThis.__ask = { search: async () => ({ node_hits: [] }), context: async () => ({}) };
  const mod = await loadAsk();
  await mod.askSubmit('one more');
  const kept = JSON.parse(store.get('vinc.ask.log'));
  assert.equal(kept.length, 60);
  assert.equal(kept.at(-1).card.kind, 'none');
  assert.equal(kept.at(-2).text, 'one more');
  assert.equal(kept[0].text, 'q12');
});
