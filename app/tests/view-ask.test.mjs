// Ask 메뉴 배선(view-ask.ts)이 문서 클릭 리스너를 남기지 않는가.
// 문서 리스너는 메뉴가 열린 동안 하나뿐이고, 닫히거나 표면이 사라지거나 다음 줄이 매달리면 0 으로 돌아와야 한다.
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

const { wireAsk } = await loadTs('surfaces/view-ask.ts',
  { '../copy': COPY_STUB, '../components': COMPONENTS_STUB, '../keys': KEYS_STUB });

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
