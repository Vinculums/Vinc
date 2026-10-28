// AC-APP-19: 개념 후보는 반영할 때만 노드가 된다. 편집기의 목록(extract-list.ts)은 체크한 것만
// 보내고 살펴본 후보를 다시 권하지 않으며, 파일 하나 가져오기 시트는 한 단추로 전부 반영한다.
// 근거: [[vinc.bdd.app]] AC-APP-19 · [[vinc.frd.app]] FR-APP-19
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { installDom, settle, El } from './mini-dom.mjs';
import { loadTs, srcText, COPY_STUB, COMPONENTS_STUB } from './ts-load.mjs';

const { body } = installDom();
const S = globalThis.__ex = { calls: [], reply: null, exists: new Set(), preview: [] };
const API = `export const api = {
    docExtract: async (...a) => { globalThis.__ex.calls.push(a); const [, , apply] = a;
      return apply ? globalThis.__ex.reply : { executed: true, domain: 'vinc/core', candidates: globalThis.__ex.preview }; },
    nodeContext: async (_o, id) => ({ executed: globalThis.__ex.exists.has(id) }) };
  export const cfg = { owner: 'u' };
  export const remote = () => false;
  export const messageOf = (e, d) => String(e?.message || d);`;
const list = await loadTs('extract-list.ts', {
  './api': API, './copy': COPY_STUB, './components': COMPONENTS_STUB,
  './authoring': `export const fillTopicList = async () => {}; export const forgetTopics = () => {};`,
  './lnb': `export const refreshLnb = async () => {};`,
});
const docImport = await loadTs('doc-import.ts', { './api': API, './copy': COPY_STUB });

const CANDS = [
  { candidate_id: 'c-alpha', slug: 'alpha', title: 'Alpha', one_liner: 'first', source: 'heading' },
  { candidate_id: 'c-beta', slug: 'beta', title: 'Beta', source: 'heading' },
  { candidate_id: 'c-gamma', slug: 'gamma', title: 'Gamma', source: 'body', spans: [{ text: 'gamma', char_start: 4, char_end: 9 }] },
];
let docN = 0;
async function offer(docId = `doc-${++docN}`) {
  S.calls.length = 0;
  const box = new El('div');
  body.append(box);
  await list.offerExtraction(box, { owner: 'u', docId, setTopic: async () => true });
  return { box, docId };
}
const open = async (box) => { box.querySelector('[data-ex="open"]').click(); await settle(); };
const applyCalls = () => S.calls.filter((c) => c[2] === true);

test('only ticked candidates are sent, with the edited title, and existing or body ones start unticked', async () => {
  S.preview = CANDS; S.exists = new Set(['concept:beta']);
  S.reply = { executed: true, applied: { created: 1, matched: 0 }, mentions: 2 };
  const { box } = await offer();
  assert.match(box.textContent, /exFound\(3\)/);
  assert.equal(applyCalls().length, 0, 'nothing is applied before a person asks');
  await open(box);
  const checks = box.querySelectorAll('.ex-check');
  assert.deepEqual(checks.map((c) => c.checked), [true, false, false]);
  assert.ok(box.querySelector('.ex-badge'), 'the existing concept is marked');
  // 감마를 체크하고 알파의 제목을 고친다.
  checks[2].checked = true; checks[2].fire('change');
  assert.match(box.querySelector('[data-ex="apply"]').textContent, /exApply\(2\)/);
  box.querySelector('.ex-title[data-i="0"]').value = 'Alpha store ';
  box.querySelector('[data-ex="apply"]').click();
  await settle();
  const [call] = applyCalls();
  assert.deepEqual(call[3].map((c) => c.title), ['Alpha store', 'Gamma']);
  // 본문에서 찾은 후보가 끼면 후보 id 를 함께 보낸다.
  assert.deepEqual(call[4], ['c-alpha', 'c-gamma']);
  assert.match(box.textContent, /exDone\(1,0,2\)/);
});

test('applied and unticked candidates are not offered again for that document', async () => {
  S.preview = CANDS; S.exists = new Set();
  S.reply = { executed: true, applied: { created: 2, matched: 0 }, mentions: 0 };
  const { box, docId } = await offer();
  await open(box);
  box.querySelector('[data-ex="apply"]').click();
  await settle();
  const again = await offer(docId);
  assert.equal(again.box.innerHTML, '');
  // 새 후보 하나가 생기면 그것만 권한다.
  S.preview = [...CANDS, { candidate_id: 'c-delta', slug: 'delta', title: 'Delta', source: 'heading' }];
  const more = await offer(docId);
  assert.match(more.box.textContent, /exFound\(1\)/);
});

test('Skip all dismisses every candidate for that document', async () => {
  S.preview = CANDS;
  const { box, docId } = await offer();
  await open(box);
  box.querySelector('[data-ex="skip"]').click();
  assert.equal(box.innerHTML, '');
  assert.equal(applyCalls().length, 0);
  assert.equal((await offer(docId)).box.innerHTML, '');
});

test('nothing ticked sends nothing, and a topicless refusal asks for a topic', async () => {
  S.preview = CANDS; S.exists = new Set();
  const { box } = await offer();
  await open(box);
  box.querySelectorAll('.ex-check').forEach((c) => { c.checked = false; });
  box.querySelector('[data-ex="apply"]').click();
  await settle();
  assert.equal(applyCalls().length, 0);
  assert.match(box.querySelector('.ex-msg').textContent, /exNothingChecked/);
  box.querySelector('.ex-check').checked = true;
  S.reply = { executed: false, error: 'no topic on this document' };
  box.querySelector('[data-ex="apply"]').click();
  await settle();
  assert.ok(box.querySelector('.ex-topic'), 'the list asks for a topic instead of failing');
});

test('the dismissed memory keeps the last 400 per document', async () => {
  const docId = 'doc-full';
  localStorage.setItem(`vinc.extract.seen.u.${docId}`, JSON.stringify(Array.from({ length: 400 }, (_, i) => `old-${i}`)));
  S.preview = CANDS;
  const { box } = await offer(docId);
  await open(box);
  box.querySelector('[data-ex="skip"]').click();
  const kept = JSON.parse(localStorage.getItem(`vinc.extract.seen.u.${docId}`));
  assert.equal(kept.length, 400);
  assert.deepEqual(kept.slice(-3), ['c-alpha', 'c-beta', 'c-gamma']);
  assert.ok(!kept.includes('old-0'));
});

test('the single-file import sheet applies every candidate with one button', async () => {
  S.calls.length = 0;
  S.reply = { executed: true, applied: { created: 3 }, mentions: 5 };
  assert.deepEqual(await docImport.applyExtract('u', ['d1']), { created: 3, mentions: 5, refused: 0 });
  // 고른 목록 없이 부른다: 서버가 미리보기의 후보 전부를 반영한다.
  assert.deepEqual(S.calls, [['u', 'd1', true]]);
  S.reply = { executed: false, error: 'no topic' };
  assert.deepEqual(await docImport.applyExtract('u', ['d1', '', 'd2']), { created: 0, mentions: 0, refused: 2 });
  // 시트의 단추가 그 함수를 문서 하나로 부른다(정적 대조).
  const sheets = await srcText('sheets.ts');
  const at = sheets.indexOf('export async function openDocImportSheet');
  assert.match(sheets.slice(at), /#sheet-extract[\s\S]*?dm\.applyExtract\(cfg\.owner, \[r\.docId\]\)/);
  assert.match(sheets.slice(at), /ap\.refused \? esc\(COPY\.author\.exRefusedDocs\(ap\.refused\)\)/);
});
