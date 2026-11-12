// 표의 단축키가 층 뒤에서 쉬고, 층이 어느 길로 닫히든 다시 돈다. 진짜 components.ts(시트 · 확인 · 알림 ·
// 입력 · 우클릭 메뉴)와 keys.ts · layers.ts 를 작은 DOM 위에 띄우고 닫는 길을 하나씩 밟는다. 끝마다
// "아무것도 안 떠 있으면 표의 단축키가 전부 돈다"를 본다: 남은 잡기는 그 세션 내내 단축키를 죽인다.
// 공간 전환 · 검색 모드 메뉴와 시간 · 범례 창은 브라우저가 필요하다(진짜 DOM 의 숨김 · 상자): 그쪽은
// 이 테스트가 그 요소의 모양만 세워 물음(`openLayerIn`)이 그것을 세는지 본다.
//
//     node --experimental-strip-types app/dev/shortcut-rest-check.ts   (app/ 에서 npm ci 뒤)
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { installDom, settle, El } from '../tests/mini-dom.mjs';
import { tsUrl, COPY_STUB } from '../tests/ts-load.mjs';

const { body } = installDom();

// ── 작은 DOM 에 없는 것: 창과 문서의 듣기(잡기 · 거품), 초점, 상자 ─────────────────────────────
const listeners = [];   // { on: 'window' | 'document', type, f, capture }
const wire = (on) => ({
  add: (type, f, opt) => listeners.push({ on, type, f, capture: opt === true || !!opt?.capture }),
  remove: (type, f, opt) => {
    const capture = opt === true || !!opt?.capture;
    const i = listeners.findIndex((l) => l.on === on && l.type === type && l.f === f && l.capture === capture);
    if (i >= 0) listeners.splice(i, 1);
  },
});
document.addEventListener = wire('document').add;
document.removeEventListener = wire('document').remove;
globalThis.addEventListener = wire('window').add;
globalThis.removeEventListener = wire('window').remove;
document.activeElement = body;
El.prototype.focus = function () { document.activeElement = this; };
El.prototype.getClientRects = function () { return this.isConnected ? [{}] : []; };
globalThis.HTMLElement = El;
const realRemove = El.prototype.remove;
El.prototype.remove = function () {
  if (this.contains(document.activeElement)) document.activeElement = body;
  realRemove.call(this);
};

/** 브라우저의 순서로 이벤트를 보낸다: 창의 잡기 → 문서의 잡기 → 대상에서 위로 거품 → 문서의 거품 → 창의 거품.
 *  `stopPropagation` 은 다음 자리부터, `stopImmediatePropagation` 은 바로 다음 듣기부터 멈춘다. */
function send(type, target = document.activeElement, extra = {}) {
  let stop = false, now = false;
  const ev = { type, target, ...extra, defaultPrevented: false,
    stopPropagation() { stop = true; }, stopImmediatePropagation() { stop = true; now = true; },
    preventDefault() { this.defaultPrevented = true; } };
  const at = (fs) => { for (const f of fs) { if (now) return; f(ev); } };
  const phase = (on, capture) => listeners.filter((l) => l.on === on && l.type === type && l.capture === capture).map((l) => l.f);
  const path = [];
  for (let x = target; x && x !== body; x = x.parent) path.push(x);
  const steps = [() => phase('window', true), () => phase('document', true),
    ...path.map((x) => () => [...(x.listeners?.[type] || [])]),
    () => phase('document', false), () => phase('window', false)];
  for (const step of steps) { if (stop) break; at(step()); }
  return ev;
}
const key = (k) => send('keydown', document.activeElement, { key: k });

const keysUrl = await tsUrl('keys.ts');
const layersUrl = await tsUrl('layers.ts');
const C = await import(await tsUrl('components.ts', {
  './icons': 'export const stateDot = () => "";',
  './copy': COPY_STUB,
  './search-temporal': 'export const temporalEntries = () => [];',
  './motion': 'export const popIn = () => null; export const fadeIn = () => {}; export const popOut = async () => {}; export const fadeOut = async () => {};',
  './vinc-loaders.js': 'export const loaderTrack = () => "";',
  './keys': keysUrl,
  './layers': layersUrl,
}));
const K = await import(keysUrl);

const surfaceRests = () => K.shortcutsRest('surface', document.activeElement, document.activeElement);
const commandRests = () => K.shortcutsRest('command', document.activeElement, document.activeElement);
/** 아무것도 안 떠 있다: 딤 · 메뉴가 없고, 1·2·3·? 와 ⌘/Ctrl 키가 쉬지 않는다. */
async function nothingOpen(label) {
  await settle();
  assert.equal(body.querySelectorAll('.sheet-scrim').length, 0, `${label}: 딤이 남았다`);
  assert.equal(body.querySelectorAll('.ctx-menu').length, 0, `${label}: 우클릭 메뉴가 남았다`);
  document.activeElement = opener;
  assert.equal(surfaceRests(), false, `${label}: 1·2·3·? 이 죽었다`);
  assert.equal(commandRests(), false, `${label}: ⌘/Ctrl 키가 죽었다`);
}
function resting(label) {
  assert.equal(surfaceRests(), true, `${label}: 1·2·3·? 이 돈다`);
  assert.equal(commandRests(), true, `${label}: ⌘/Ctrl 키가 돈다`);
}

// 층을 연 자리(시트를 연 단추). 닫히면 초점이 여기로 돌아와야 한다.
const opener = new El('button');
body.append(opener);
const sheets = () => body.querySelectorAll('.sheet-scrim').length;
const open = (title = 'S', more = {}) => { document.activeElement = opener; return C.openSheet({ title, ...more }); };

test('시트: Esc · ✕ · 딤 누르기 · 두 번 닫기 · 닫기 없이 지움, 어느 길로 닫아도 단축키가 돌아온다', async () => {
  await nothingOpen('처음');
  const a = open('esc');
  resting('시트가 떠 있음');
  key('Escape');
  await nothingOpen('Esc');
  assert.equal(document.activeElement, opener, 'Esc 뒤 초점이 연 자리로 돌아온다');

  const b = open('x');
  b.el.querySelector('.sheet-x').click();
  await nothingOpen('✕');

  const c = open('scrim');
  Object.assign(c.el, { clientWidth: 100, clientHeight: 100 });
  c.el.dispatchEvent({ type: 'pointerdown', target: c.el, clientX: 5, clientY: 5 });
  c.el.dispatchEvent({ type: 'click', target: c.el });
  await nothingOpen('딤 누르기');

  const d = open('twice');
  d.close(); d.close();
  await nothingOpen('두 번 닫기');

  const e = open('removed');
  e.el.remove();                       // 다른 화면이 닫기를 거치지 않고 걷어 감
  await nothingOpen('닫기 없이 지움');
  e.close();                           // 늦게 온 닫기
  await nothingOpen('늦은 닫기');
});

test('시트 위의 시트 · 확인 · 알림 · 입력: Esc 한 번에 맨 위 하나만 닫는다', async () => {
  const s1 = open('lower');
  const s2 = open('upper');
  key('Escape');
  await settle();
  assert.equal(sheets(), 1, '맨 위 시트만 닫힌다');
  assert.equal(s2.closed, true); assert.equal(s1.closed, false);
  resting('아래 시트가 남음');
  key('Escape');
  await nothingOpen('시트 위의 시트');

  const under = open('settings');
  const confirmed = C.confirmDestructive('Delete?', 'Delete', 'Cancel');
  resting('확인이 떠 있음');
  key('Escape');
  assert.equal(await confirmed, false);
  await settle();
  assert.equal(sheets(), 1, 'Esc 는 확인만 닫고 그 아래 설정은 남긴다');
  assert.equal(under.closed, false);
  const noticed = C.showNotice('Done', 'OK');
  key('Escape');
  await noticed;
  const asked = C.promptText('Name', 'x', 'OK', 'Cancel');
  resting('입력이 떠 있음');
  key('Escape');
  assert.equal(await asked, null);
  await settle();
  assert.equal(sheets(), 1);
  key('Escape');
  await nothingOpen('설정 위의 확인 · 알림 · 입력');
});

test('고르기를 요구하는 시트(첫 실행)는 Esc 로 닫히지 않고, 발의 문으로 닫히면 단축키가 돌아온다', async () => {
  const s = open('first run', { mustChoose: true });
  key('Escape');
  await settle();
  assert.equal(s.closed, false);
  resting('첫 실행 시트');
  s.close();
  await nothingOpen('첫 실행 시트를 고름');
});

test('딤 밖의 모달 화면(로그인)이 시트 위에 뜨면 Esc 는 아래 시트를 닫지 않고, 화면이 빠지면 다시 닫는다', async () => {
  const s = open('settings');
  const screen = new El('section');
  screen.setAttribute('aria-modal', 'true');
  body.append(screen);
  K.pushLayer({ alive: () => screen.isConnected });     // firstrun.ts · device-graph.ts 와 같은 배선(Esc 를 삼킨다)
  key('Escape');
  await settle();
  assert.equal(s.closed, false, '보이지 않는 시트가 Esc 를 가져가지 않는다');
  resting('로그인 화면');
  screen.remove();
  resting('시트만 남음');
  key('Escape');
  await nothingOpen('로그인 화면이 빠진 뒤');
  const alone = new El('section');
  alone.setAttribute('aria-modal', 'true');
  body.append(alone);
  resting('시트 없이 로그인 화면만');
  alone.remove();
  await nothingOpen('로그인 화면만 빠짐');
});

test('우클릭 메뉴: Esc · 바깥 누르기 · 고르기 · 새 메뉴로 갈아 끼우기, 어느 길로 닫아도 단축키가 돌아온다', async () => {
  const ran = [];
  const items = [{ label: 'One', run: () => ran.push('one') }, { label: 'Two', run: () => ran.push('two') }];
  document.activeElement = opener;
  C.openContextMenu(10, 10, items);
  resting('메뉴가 떠 있음');
  key('Escape');
  await nothingOpen('Esc');
  assert.equal(document.activeElement, opener, 'Esc 뒤 초점이 연 자리로 돌아온다');

  C.openContextMenu(10, 10, items);
  send('mousedown', opener);
  await nothingOpen('바깥 누르기');

  document.activeElement = opener;
  C.openContextMenu(10, 10, items);
  body.querySelectorAll('.ctx-item')[1].click();
  assert.deepEqual(ran, ['two']);
  await nothingOpen('고르기');

  C.openContextMenu(10, 10, items);
  C.openContextMenu(20, 20, items);
  assert.equal(body.querySelectorAll('.ctx-menu').length, 1, '메뉴는 하나만 뜬다');
  key('Escape');
  await nothingOpen('갈아 끼운 뒤 Esc');
  assert.equal(listeners.filter((l) => l.type === 'mousedown').length, 0, '앞 메뉴의 듣기가 남지 않는다');

  C.openContextMenu(10, 10, items);
  body.querySelector('.ctx-menu').remove();   // 메뉴만 지워짐(다른 화면이 다시 그림)
  await nothingOpen('메뉴 요소만 지워짐');
  send('mousedown', opener);                  // 남은 듣기는 다음 누르기에 스스로 풀린다
  assert.equal(listeners.filter((l) => l.type === 'mousedown').length, 0);
});

test('메뉴가 떠 있을 때 시트가 열리면 메뉴가 먼저 닫히고, 시트 위에 뜬 메뉴는 Esc 를 먼저 받는다', async () => {
  document.activeElement = opener;
  C.openContextMenu(10, 10, [{ label: 'A', run() {} }]);
  const s = open('over a menu');
  assert.equal(body.querySelectorAll('.ctx-menu').length, 0, '시트가 열리면 우클릭 메뉴가 닫힌다');
  key('Escape');
  await nothingOpen('메뉴 위에 연 시트');
  assert.equal(s.closed, true);

  const under = open('settings');
  C.openContextMenu(10, 10, [{ label: 'A', run() {} }]);
  key('Escape');
  await settle();
  assert.equal(body.querySelectorAll('.ctx-menu').length, 0, 'Esc 한 번이 메뉴를 닫는다');
  assert.equal(under.closed, false, '같은 Esc 가 아래 시트까지 닫지 않는다');
  key('Escape');
  await nothingOpen('시트 위의 메뉴');

  // 공간 전환 메뉴: 연 단추를 한 번 더 누르는 것이 그 메뉴의 닫기다.
  const sw = new El('button');
  sw.className = 'space-switch';
  sw.setAttribute('aria-expanded', 'true');
  const menu = new El('div');
  menu.className = 'space-menu';
  sw.addEventListener('click', () => { menu.remove(); sw.setAttribute('aria-expanded', 'false'); });
  body.append(sw); body.append(menu);
  resting('공간 전환 메뉴');
  const t = open('over the space menu');
  assert.equal(menu.isConnected, false, '시트가 열리면 공간 전환 메뉴가 자기 단추로 닫힌다');
  t.close();
  await nothingOpen('공간 전환 메뉴 위에 연 시트');
  sw.remove();
});

test('숨겨 닫는 메뉴와 팝오버 · 팔레트: 보이는 동안만 쉬고, 숨거나 빠지면 다음 키에서 돈다', async () => {
  const mode = new El('div'); mode.setAttribute('id', 'tb-modemenu'); mode.hidden = true;
  const pop = new El('div'); pop.className = 'cmp-side cmp-pop'; pop.hidden = true;
  const pal = new El('div'); pal.setAttribute('id', 'palette'); pal.hidden = true;
  const drawer = new El('div'); drawer.append(mode);
  body.append(drawer); body.append(pop); body.append(pal);
  await nothingOpen('모두 숨김');
  for (const el of [mode, pop, pal]) {
    el.hidden = false;
    resting(el.id || el.className);
    el.hidden = true;
    await nothingOpen(`${el.id || el.className} 숨김`);
  }
  mode.hidden = false; drawer.hidden = true;                 // 메뉴는 열린 채 그 칸이 접힘
  await nothingOpen('숨은 칸 안의 열린 메뉴');
  drawer.hidden = false; resting('칸이 다시 펴짐');
  drawer.remove();                                           // 다시 그리기가 메뉴를 걷어 감
  await nothingOpen('메뉴가 든 칸이 빠짐');
  pop.hidden = false; pop.remove();                          // 그래프를 떠나며 창이 빠짐
  await nothingOpen('열린 창이 빠짐');
  pal.remove();
});

// ── 한 번의 Esc 는 맨 위 층 하나만 닫고, 그 뒤의 누구도 그 키를 듣지 않는다 ─────────────────────────────
// 뒤의 처리기(View 의 레일 닫기 · 나침반의 뿌리 · 툴팁 · 검색 칸)를 대신하는 듣기를 층을 열기 **전에** 문서와 창의
// 거품 단계에 건다. Esc 를 받은 층이 있으면 그 듣기는 불리지 않아야 한다.
const heard = [];
const behind = (e) => { if (e.key === 'Escape') heard.push(e.target?.id || e.target?.tagName || 'body'); };
document.addEventListener('keydown', behind);
globalThis.addEventListener('keydown', behind);
/** 팔레트처럼 components.ts 밖에서 쌓임에 오르는 층(main.ts 의 팔레트, 편집기의 메뉴, 모드 메뉴와 같은 배선). */
function stackLayer(name, menu = false) {
  const el = new El('div'); el.setAttribute('id', name); body.append(el);
  let release = K.pushLayer({ menu, alive: () => el.isConnected && !el.hidden, escape: () => { el.hidden = true; release(); } });
  return el;
}

test('맨 위 층마다: Esc 는 그 층만 닫고 뒤의 듣기에 닿지 않는다(시트 · 확인 · 알림 · 입력 · 우클릭 메뉴 · 팔레트 · 편집기 메뉴)', async () => {
  const kinds = {
    sheet: () => { const s = open('sheet'); return () => s.closed; },
    confirm: () => { let done = false; document.activeElement = opener; void C.confirmDestructive('?', 'y', 'n').then(() => { done = true; }); return () => done; },
    notice: () => { let done = false; document.activeElement = opener; void C.showNotice('!', 'ok').then(() => { done = true; }); return () => done; },
    prompt: () => { let done = false; document.activeElement = opener; void C.promptText('?', 'x', 'ok', 'no').then(() => { done = true; }); return () => done; },
    ctxmenu: () => { document.activeElement = opener; C.openContextMenu(1, 1, [{ label: 'a', run() {} }]); return () => !body.querySelector('.ctx-menu'); },
    palette: () => { const el = stackLayer('palette'); return () => el.hidden; },
    editorMenu: () => { const el = stackLayer('mention', true); return () => el.hidden; },
  };
  for (const [name, openIt] of Object.entries(kinds)) {
    heard.length = 0;
    const closed = openIt();
    const ev = key('Escape');
    await settle(); await settle();
    assert.equal(closed(), true, `${name}: Esc 로 닫히지 않았다`);
    assert.deepEqual(heard, [], `${name}: 뒤의 듣기가 Esc 를 들었다`);
    assert.equal(ev.defaultPrevented, true, `${name}: Esc 의 기본 동작이 남았다`);
    body.querySelector('#palette')?.remove(); body.querySelector('#mention')?.remove();
    await nothingOpen(name);
  }
  heard.length = 0;
  key('Escape');
  assert.deepEqual(heard, ['BUTTON', 'BUTTON'], '아무것도 안 떠 있으면 Esc 는 main 과 같이 뒤의 처리기에 간다');
});

test('맨 위가 아닌 층은 Esc 로 닫히지 않는다: 팔레트 위의 알림 · 팔레트 위의 시트 · 설정 위의 확인 · 시트 위의 메뉴 · 로그인 화면', async () => {
  heard.length = 0;
  const pal = stackLayer('palette');
  document.activeElement = pal;
  const shown = C.showNotice('arrived', 'ok');
  key('Escape'); await shown; await settle();
  assert.equal(pal.hidden, false, '알림 아래 팔레트가 첫 Esc 에 닫혔다');
  assert.equal(document.activeElement, pal, '알림이 닫히면 초점이 팔레트로 돌아온다');
  key('Escape');
  assert.equal(pal.hidden, true);
  pal.remove();

  const pal2 = stackLayer('palette');
  const s = C.openSheet({ title: 'async' });
  key('Escape'); await settle();
  assert.equal(s.closed, true); assert.equal(pal2.hidden, false, '시트 아래 팔레트가 같은 Esc 에 닫혔다');
  key('Escape'); assert.equal(pal2.hidden, true);
  pal2.remove();

  const settings = open('settings');
  const confirmed = C.confirmDestructive('?', 'y', 'n');
  key('Escape'); assert.equal(await confirmed, false); await settle();
  assert.equal(settings.closed, false, '확인 아래 설정이 같은 Esc 에 닫혔다');
  key('Escape'); await settle(); assert.equal(settings.closed, true);

  const under = open('composer');
  const menu = stackLayer('mention', true);
  key('Escape'); assert.equal(menu.hidden, true); assert.equal(under.closed, false, '메뉴 아래 시트가 같은 Esc 에 닫혔다');
  key('Escape'); await settle(); assert.equal(under.closed, true);
  menu.remove();

  const sheet = open('settings');
  const screen = new El('section'); body.append(screen);
  const down = K.pushLayer({ alive: () => screen.isConnected });   // 로그인 화면: Esc 를 삼킨다
  key('Escape'); await settle();
  assert.equal(sheet.closed, false, '로그인 화면 아래 시트가 닫혔다');
  screen.remove(); void down;
  key('Escape'); await settle(); assert.equal(sheet.closed, true);
  assert.deepEqual(heard, [], '어느 Esc 도 뒤의 듣기에 닿지 않았다');
  await nothingOpen('맨 위가 아닌 층');
});

test('빠른 여닫기: 시트 · 메뉴 · 다이얼로그를 200번 섞어 열고 닫아도 끝에 단축키가 돈다', async () => {
  for (let i = 0; i < 200; i++) {
    const s = open(`r${i}`);
    if (i % 3 === 0) C.openContextMenu(1, 1, [{ label: 'x', run() {} }]);
    if (i % 4 === 0) key('Escape');                          // 메뉴가 있으면 메뉴만, 없으면 시트를 닫는다
    if (i % 5 === 0) void C.confirmDestructive('?', 'y', 'n');
    if (i % 2) s.el.remove(); else s.close();
    key('Escape'); key('Escape');
    if (i % 7 === 0) s.close();
  }
  await settle(); await settle();
  // 이 순서에서 다이얼로그는 Esc 로 닫혔거나 아래 시트가 지워지며 남았다: 남은 것은 Esc 로 닫는다.
  for (let i = 0; i < 5 && sheets(); i++) { key('Escape'); await settle(); }
  await nothingOpen('200번 섞어 여닫기');
});
