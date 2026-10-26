import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

// role-eval imports copy and components (the DOM); load it with the real English copy block and a plain esc.
const compile = async (name) => ts.transpileModule(await readFile(new URL(`../src/${name}`, import.meta.url), 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const url = (js) => `data:text/javascript;base64,${Buffer.from(js).toString('base64')}`;
const copySrc = await readFile(new URL('../src/copy.ts', import.meta.url), 'utf8');
const roleBlock = (lang) => {
  const at = copySrc.indexOf('evalTitle:', lang === 'en' ? 0 : copySrc.indexOf('evalTitle:') + 1);
  const start = copySrc.lastIndexOf('role: {', at);
  let depth = 0, i = copySrc.indexOf('{', start);
  const from = i;
  for (; i < copySrc.length; i++) {
    if (copySrc[i] === '{') depth++;
    else if (copySrc[i] === '}' && --depth === 0) break;
  }
  return ts.transpileModule(`export default (${copySrc.slice(from, i + 1)});`,
    { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
};
const role = (await import(url(roleBlock('en')))).default;
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const copyUrl = url(`export const COPY = { author: { role: ${JSON.stringify(Object.fromEntries(
  Object.entries(role).filter(([, v]) => typeof v !== 'function')))} } };
  const r = COPY.author.role;
  r.evalCell = (a, m) => a + ' · ' + m; r.evalSuite = (i, t) => i + ' · ' + t + ' tasks';
  r.evalReplaced = (n) => n + ' older'; r.evalAlways = (n) => 'all ' + n;
  r.evalSkipped = (n) => n + ' skipped'; r.evalUngraded = (n) => n + ' ungraded';`);
const compsUrl = url(`export const esc = ${esc.toString()};`);
const js = (await compile('role-eval.ts')).replace('"./copy"', JSON.stringify(copyUrl)).replace('"./components"', JSON.stringify(compsUrl));
const { evalCardsHtml } = await import(url(js));

const cell = { adapter: 'langgraph', model_family: 'claude', runs_per_task: 3,
  pass_1: { k: 90, n: 120, rate: 0.75 }, pass_k: { k: 21, n: 40, rate: 0.525 },
  violation: null, fabrication: { k: 1, n: 24, rate: 0.0417 }, cost_per_success_usd: 0.0412 };
const card = (extra = {}) => ({ id: 'record:eval-card-x', date: '2026-10-26', current: true,
  suite: { id: 'agent-tasks-test', tasks: 40 }, results: [cell], ...extra });

test('a card shows each measured rate as a percent and leaves the unmeasured ones out', () => {
  const html = evalCardsHtml([card()], 'concept:agent-dev-core');
  assert.match(html, /agent-tasks-test · 40 tasks · 2026-10-26/);
  assert.match(html, /langgraph · claude/);
  assert.match(html, /Success rate<\/dt><dd title="90\/120">75%/);
  assert.match(html, /53%/);          // 0.525 rounds to 53, the tooltip keeps 21/40
  assert.match(html, /\$0\.041/);
  assert.doesNotMatch(html, /Rule breaks/);
});

test('no card says how to make one, with the short role name', () => {
  const html = evalCardsHtml([], 'concept:agent-dev-core');
  assert.match(html, /No evaluation card yet/);
  assert.match(html, /vinc eval run dev-core --suite FILE/);
});

test('replaced cards are counted, not shown, and skipped or ungraded runs are said', () => {
  const html = evalCardsHtml([card({ results: [{ ...cell, skipped: 2, ungraded: 1 }] }),
    card({ id: 'record:eval-card-old', current: false })], 'concept:agent-dev-core');
  assert.equal((html.match(/class="eval-card"/g) || []).length, 1);
  assert.match(html, /1 older/);
  assert.match(html, /2 skipped/);
  assert.match(html, /1 ungraded/);
});

test('text from a card is escaped', () => {
  const html = evalCardsHtml([card({ suite: { id: '<b>x</b>', tasks: 1 }, results: [] })], 'concept:agent-r');
  assert.doesNotMatch(html, /<b>x/);
});
