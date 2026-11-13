// Ask 메뉴 배선(view-ask.ts)이 문서 클릭 리스너를 남기지 않는가.
// 문서 리스너는 메뉴가 열린 동안 하나뿐이고, 닫히거나 표면이 사라지거나 다음 줄이 매달리면 0 으로 돌아와야 한다.
// 같은 모듈의 Ask 줄 마크업과 노드 · 문서의 프롬프트 셋(칸의 순서 · 부제의 수치 · 주소의 반경)도 본다.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { installDom, El } from './mini-dom.mjs';
import { loadTs, COPY_STUB, COMPONENTS_STUB } from './ts-load.mjs';

const { body } = installDom();

// 진짜 DOM 처럼 { signal } 을 따르는 리스너 등록: 신호가 끊기면 그 리스너가 빠진다.
const withSignal = (add) => function (t, f, opts) {
  const sig = opts && typeof opts === 'object' ? opts.signal : undefined;
  if (sig?.aborted) return;
  add.call(this, t, f);
  sig?.addEventListener('abort', () => this.removeEventListener(t, f), { once: true });
};
El.prototype.addEventListener = withSignal(El.prototype.addEventListener);

// 문서 수준 리스너 표: 세는 것이 이 테스트의 과녁이다.
const docListeners = [];
document.addEventListener = withSignal(function (t, f) { docListeners.push({ t, f }); });
document.removeEventListener = function (t, f) {
  const i = docListeners.findIndex((r) => r.t === t && r.f === f);
  if (i >= 0) docListeners.splice(i, 1);
};
document.getElementById = (id) => body.querySelector(`#${id}`);
document.createTextNode = (s) => s;
const docClicks = () => docListeners.filter((r) => r.t === 'click').length;

// 클릭은 요소에서 body 까지 오르고, 그다음 문서로 간다(진짜 DOM 의 거품 순서).
const click = (el) => {
  const ev = { type: 'click' };
  el.dispatchEvent(ev);
  for (const r of [...docListeners]) if (r.t === 'click') r.f(ev);
};

Object.defineProperty(globalThis, 'navigator',
  { value: { clipboard: { writeText: async () => {} } }, configurable: true });

const layers = [];
const KEYS_STUB = `
export const pushLayer = (o) => { globalThis.__layers.push(o); return () => { const i = globalThis.__layers.indexOf(o); if (i >= 0) globalThis.__layers.splice(i, 1); }; };`;
globalThis.__layers = layers;

// 줄과 프롬프트 셋이 쓰는 이름들. 주소 대역은 받은 인자를 글로 돌려, 무엇을 넘겼는지 읽게 한다.
const ROW_STUBS = {
  '../api': `export const spaceName = () => 'personal';`,
  '../icons': `export const icon = (name, size) => '<i data-icon="' + name + ':' + size + '"></i>';`,
  '../lnb': `export const isAgentRoleId = (id) => id.startsWith('concept:agent-');`,
  '../view-url': `export const absoluteViewUrl = (sel, opts, space) => JSON.stringify([sel, opts, space]);`,
};

const { wireAsk, askRowHTML, nodeAskItems, docAskItems, chev, SHARE_DEPTH } = await loadTs('surfaces/view-ask.ts',
  { '../copy': COPY_STUB, '../components': COMPONENTS_STUB, '../keys': KEYS_STUB, ...ROW_STUBS });

const items = () => [
  { name: 'A', sub: 'a', body: 'ba', url: 'u1' },
  { name: 'B', sub: 'b', body: 'bb', url: 'u2' },
];

/** 카드 하나를 body 에 세우고 그 Ask 줄을 배선한다. */
function mountCard() {
  const card = new El('div');
  card.innerHTML = `<div class="ask-row"><button type="button" class="js-ask" aria-expanded="false">Ask</button></div>`;
  body.append(card);
  const set = wireAsk(card, () => card.isConnected, items);
  set(0, 0);
  return { card, trigger: card.querySelector('.js-ask') };
}

const menuEl = () => body.querySelector('#ask-menu-live');
const outside = new El('div');
body.append(outside);

test('열고 바깥을 누르면 문서 리스너가 처음 수로 돌아온다', () => {
  const start = docClicks();
  const { card, trigger } = mountCard();
  assert.equal(docClicks(), start, '배선만으로는 문서 리스너를 붙이지 않는다');
  click(trigger);
  assert.equal(menuEl().hidden, false);
  assert.equal(docClicks(), start + 1, '열린 동안 하나');
  click(outside);
  assert.equal(menuEl().hidden, true);
  assert.equal(docClicks(), start);
  card.remove();
});

test('단추로 닫아도, 항목을 골라도, Esc 로 닫아도 리스너가 빠진다', () => {
  const start = docClicks();
  const { card, trigger } = mountCard();
  click(trigger); click(trigger);
  assert.equal(docClicks(), start, '단추로 닫기');
  click(trigger);
  click(menuEl().querySelector('.menu-item'));
  assert.equal(menuEl().hidden, true);
  assert.equal(docClicks(), start, '항목 선택');
  click(trigger);
  layers[layers.length - 1].escape();
  assert.equal(docClicks(), start, 'Esc');
  assert.equal(layers.length, 0, '메뉴 층도 내려온다');
  card.remove();
});

test('되풀이해 배선해도 리스너가 쌓이지 않고 메뉴는 하나다', () => {
  const start = docClicks();
  const cards = [];
  for (let i = 0; i < 20; i++) {
    const c = mountCard();
    click(c.trigger);
    click(outside);
    cards.push(c);
  }
  assert.equal(docClicks(), start);
  assert.equal(body.querySelectorAll('#ask-menu-live').length, 1);
  // 앞 카드의 단추는 거둔 메뉴를 다시 열지 않는다.
  click(cards[0].trigger);
  assert.equal(docClicks(), start);
  for (const c of cards) c.card.remove();
});

test('열린 채로 다음 줄이 매달리면 앞 메뉴와 그 리스너를 거둔다', () => {
  const start = docClicks();
  const first = mountCard();
  click(first.trigger);
  assert.equal(docClicks(), start + 1);
  const second = mountCard();
  assert.equal(docClicks(), start, '앞 리스너가 빠진다');
  assert.equal(body.querySelectorAll('#ask-menu-live').length, 1);
  assert.equal(layers.length, 0, '앞 메뉴 층도 내려온다');
  first.card.remove(); second.card.remove();
});

test('열린 채로 카드가 사라지면 다음 클릭에 메뉴와 리스너를 거둔다', () => {
  const start = docClicks();
  const { card, trigger } = mountCard();
  click(trigger);
  card.remove();
  click(outside);
  assert.equal(docClicks(), start);
  assert.equal(menuEl(), null, '메뉴도 body 에서 빠진다');
});

const names = (its) => its.map((it) => String(it.name));

test('Ask 줄: 단추가 메뉴를 연다고 말하고 아래로 펼치는 캐럿을 단다', () => {
  const row = askRowHTML();
  assert.match(row, /^<div class="card-row card-row--static ask-row">/);
  assert.match(row, /class="card-ctl js-ask" aria-expanded="false"\s+aria-haspopup="menu">COPY\.ui\.viewx\.cardAskCopy/);
  assert.ok(row.includes(chev('down')), '아래 캐럿');
  assert.equal(chev('right'),
    '<span class="row-chev row-chev--right" aria-hidden="true"><i data-icon="chevron-left:12"></i></span>');
});

test('노드의 셋: 설명 · 비교 · 문서 순이고, 비교만 건네는 반경으로 연다', () => {
  const its = nodeAskItems({ id: 'concept:x', label: 'X' }, 5, 2);
  assert.deepEqual(names(its), ['COPY.ui.viewx.askExplain', 'COPY.ui.viewx.askCompare', 'COPY.ui.viewx.askDocs']);
  assert.deepEqual(its.map((it) => String(it.sub)),
    ['COPY.ui.viewx.askExplainSub', 'COPY.ui.viewx.askCompareSub(5)', 'COPY.ui.viewx.askDocsSub(2)']);
  assert.equal(SHARE_DEPTH, 2);
  assert.deepEqual(its.map((it) => JSON.parse(it.url)[1].depth), [1, SHARE_DEPTH, 1]);
  assert.deepEqual(JSON.parse(its[0].url), [{ kind: 'node', id: 'concept:x' }, { depth: 1 }, 'personal']);
});

test('에이전트 역할이면 역할을 맡기는 칸이 맨 앞에 붙는다(주제가 아니라 id 로 가린다)', () => {
  assert.deepEqual(names(nodeAskItems({ id: 'concept:agent-x', label: 'X', domain: 'vinc/agent' }, 0, 0)),
    ['COPY.ui.viewx.askRole', 'COPY.ui.viewx.askExplain', 'COPY.ui.viewx.askCompare', 'COPY.ui.viewx.askDocs']);
  assert.equal(nodeAskItems({ id: 'concept:x', label: 'X', domain: 'vinc/agent' }, 0, 0).length, 3);
});

test('문서의 셋: 요약 · 개념 · 대조 순이고, 셋 다 그 문서의 주소다', () => {
  const its = docAskItems('d1');
  assert.deepEqual(names(its), ['COPY.ui.viewx.askDocSum', 'COPY.ui.viewx.askDocConcepts', 'COPY.ui.viewx.askDocCheck']);
  for (const it of its) assert.deepEqual(JSON.parse(it.url), [{ kind: 'doc', id: 'd1' }, {}, 'personal']);
});
