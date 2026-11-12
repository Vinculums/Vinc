// 나침반 렌즈(view-compass.ts)의 행동이 바뀌지 않았는지 두 dev 서버를 나란히 돌려 잰다. CI 는 돌리지 않는다(*-check.ts 가 아니다).
//
// 쓰는 법(앱 폴더에서). 기준(손대기 전 트리)과 고친 트리를 각각 dev 서버로 띄운다. 실제 앱 세션은 사이드카도 있어야 한다:
//   (기준 트리) VINC_SIDECAR=http://127.0.0.1:8797 npx vite --port 5210 --strictPort --host 127.0.0.1
//   (고친 트리) VINC_SIDECAR=http://127.0.0.1:8797 npx vite --port 5209 --strictPort --host 127.0.0.1
//   PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs node dev/compass-compare.mjs record http://127.0.0.1:5210 /tmp/cmp/main
//   PLAYWRIGHT_MODULE=… node dev/compass-compare.mjs record http://127.0.0.1:5209 /tmp/cmp/branch
//   PLAYWRIGHT_MODULE=… node dev/compass-compare.mjs compare /tmp/cmp/main /tmp/cmp/branch [/tmp/cmp/report.json]
// 기준 트리에도 dev/compass-lens.html · compass-lens.ts 가 있어야 한다(없으면 이 두 파일을 그 트리의 dev/ 에 복사한다).
// 세션 일부만: SESSIONS=h01,h02 · 실제 앱 세션을 빼려면 APP=0 · 앱 세션의 사이드카는 SIDECAR(기본 http://127.0.0.1:8797), 토큰은 SIDECAR_TOKEN(기본 devtoken).
//
// 걸음마다 남기는 것: 그래프 칸 스크린숏, 직렬화한 SVG, viewBox, 스냅숏(판에서만), 해시, 살아 있는 영역의 글, 사이드바 ·
// 시간/범례 창 · 풍선의 HTML, 나침반 모듈이 만든 리스너 · 타이머 · rAF · 유휴 콜백 · 관찰자 수(스택의 모듈 주소로 가린다).
// 기다림: 글꼴(document.fonts.ready), 그리고 그래프 안에서 도는 애니메이션이 없고 SVG · viewBox 가 두 번 연달아 같을 때까지.
import { mkdir, writeFile, readFile, readdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';

const [, , cmd, a1, a2, a3] = process.argv;
const pw = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ── 셈: 나침반 모듈(주소에 view-compass)이 만든 것만 센다 ─────────────────────────────
const COUNTER = () => {
  const mine = () => /view-compass/.test(new Error().stack || '');
  const reg = [], tmo = new Map(), raf = new Set(), idle = new Set(), obs = new Set();
  const add = EventTarget.prototype.addEventListener, rem = EventTarget.prototype.removeEventListener;
  EventTarget.prototype.addEventListener = function (type, fn, o) {
    if (fn && mine()) { const rec = { t: this, type, fn, cap: typeof o === 'boolean' ? o : !!(o && o.capture), dead: false }; reg.push(rec);
      const sig = o && typeof o === 'object' && o.signal; if (sig) { if (sig.aborted) rec.dead = true; else add.call(sig, 'abort', () => { rec.dead = true; }); } }
    return add.call(this, type, fn, o); };
  EventTarget.prototype.removeEventListener = function (type, fn, o) { const cap = typeof o === 'boolean' ? o : !!(o && o.capture);
    for (const r of reg) if (!r.dead && r.t === this && r.type === type && r.fn === fn && r.cap === cap) r.dead = true;
    return rem.call(this, type, fn, o); };
  const sT = window.setTimeout, cT = window.clearTimeout, rA = window.requestAnimationFrame, cA = window.cancelAnimationFrame;
  window.setTimeout = function (f, ms, ...x) { const m = mine(); let id = 0; id = sT.call(window, (...y) => { tmo.delete(id); if (typeof f === 'function') f(...y); }, ms, ...x); if (m) tmo.set(id, ms); return id; };
  window.clearTimeout = function (id) { tmo.delete(id); return cT.call(window, id); };
  window.requestAnimationFrame = function (f) { const m = mine(); let id = 0; id = rA.call(window, (t) => { raf.delete(id); f(t); }); if (m) raf.add(id); return id; };
  window.cancelAnimationFrame = function (id) { raf.delete(id); return cA.call(window, id); };
  if (window.requestIdleCallback) { const rI = window.requestIdleCallback, cI = window.cancelIdleCallback;
    window.requestIdleCallback = function (f, o) { const m = mine(); let id = 0; id = rI.call(window, (d) => { idle.delete(id); f(d); }, o); if (m) idle.add(id); return id; };
    window.cancelIdleCallback = function (id) { idle.delete(id); return cI.call(window, id); }; }
  for (const K of ['ResizeObserver', 'MutationObserver', 'IntersectionObserver']) { const B = window[K]; if (!B) continue;
    window[K] = class extends B { constructor(cb) { super(cb); this.__m = mine(); this.__n = 0; }
      observe(...x) { if (this.__m) { this.__n++; obs.add(this); } return super.observe(...x); }
      disconnect() { obs.delete(this); return super.disconnect(); } }; }
  window.__cnt = () => { const live = reg.filter((r) => !r.dead && (!(r.t instanceof Node) || r.t.isConnected));
    const by = {}; for (const r of live) { const w = r.t === window ? 'window' : r.t === document ? 'document' : r.t.id ? `#${r.t.id}` : (r.t.className && String(r.t.className.baseVal ?? r.t.className)) || r.t.nodeName;
      const k = `${w}:${r.type}`; by[k] = (by[k] || 0) + 1; }
    return { listeners: live.length, timeouts: tmo.size, raf: raf.size, idle: idle.size, observers: obs.size, by }; };
};

// ── 기다림과 담기 ─────────────────────────────────────────────────────────────────
async function settle(p, min = 700) {
  await sleep(min); let last = null;
  for (let i = 0; i < 80; i++) {
    const s = await p.evaluate(() => { const g = document.querySelector('#cmp-g');
      const run = document.getAnimations().filter((a) => a.playState === 'running' && a.effect && a.effect.target && a.effect.target.closest && a.effect.target.closest('.cmp, #view-canvas, .cmp-pop')).length;
      return JSON.stringify([run, g ? g.getAttribute('viewBox') : null, g ? g.innerHTML : null]); });
    if (s === last && JSON.parse(s)[0] === 0) return; last = s; await sleep(220);
  }
}
const neutral = (p) => p.mouse.move(3, 3);
async function capture(p, dir, idx, label) {
  const sel = (await p.locator('#host').count()) ? '#host' : (await p.locator('#view-canvas').count()) ? '#view-canvas' : null;
  const png = sel ? await p.locator(sel).screenshot() : await p.screenshot();
  const data = await p.evaluate(() => { const g = document.querySelector('#cmp-g'), pop = document.querySelector('.cmp-pop'), tip = document.querySelector('#cmp-tip');
    return { svg: g ? g.outerHTML : null, viewBox: g ? g.getAttribute('viewBox') : null, cls: g ? g.getAttribute('class') : null,
      snap: window.__snap ? window.__snap() : null, hash: location.hash,
      live: [...document.querySelectorAll('[aria-live], [role=status]')].map((e) => e.innerText).join('\n--\n'),
      side: document.querySelector('.cmp-side') ? document.querySelector('.cmp-side').innerHTML : null,
      pop: pop ? { hidden: pop.hidden, html: pop.innerHTML, style: pop.getAttribute('style') } : null,
      tip: tip ? { display: tip.style.display, html: tip.innerHTML, left: tip.style.left, top: tip.style.top } : null,
      tools: document.querySelector('.cmp-tools') ? document.querySelector('.cmp-tools').outerHTML : null,
      log: document.querySelector('#log') ? `${document.querySelector('#log').textContent}|${document.querySelector('#log').dataset.centre}` : null,
      cnt: window.__cnt ? window.__cnt() : null }; });
  const base = `${dir}/${String(idx).padStart(2, '0')}`;
  await writeFile(`${base}.png`, png); await writeFile(`${base}.json`, JSON.stringify({ label, ...data }, null, 1));
}

// ── 몸짓 ───────────────────────────────────────────────────────────────────────────
const nodeBox = async (p, id, nth = 0) => { const l = p.locator(`#cmp-nodes .node[data-id="${id}"] .hit`).nth(nth); await l.waitFor({ state: 'attached', timeout: 5000 }); return l.boundingBox(); };
const clickNode = async (p, id) => { const b = await nodeBox(p, id); await p.mouse.move(b.x + b.width / 2, b.y + b.height / 2); await sleep(120); await p.mouse.down(); await p.mouse.up(); };
const clickRole = async (p, role, nth = 0) => { const id = await p.locator(`#cmp-nodes .node[data-role="${role}"]`).nth(nth).getAttribute('data-id'); await clickNode(p, id); return id; };
const hover = async (p, id) => { const b = await nodeBox(p, id); await p.mouse.move(b.x + b.width / 2 - 2, b.y + b.height / 2 - 2, { steps: 4 }); await sleep(650); };
const hoverRole = async (p, role, nth = 0) => { const id = await p.locator(`#cmp-nodes .node[data-role="${role}"]`).nth(nth).getAttribute('data-id'); await hover(p, id); return id; };
const stageBox = (p) => p.locator('#cmp-g').boundingBox();
const wheel = async (p, dy, fx = .5, fy = .5) => { const b = await stageBox(p); await p.mouse.move(b.x + b.width * fx, b.y + b.height * fy); await p.mouse.wheel(0, dy); await sleep(80); await p.mouse.wheel(0, dy); };
const drag = async (p, dx, dy) => { const b = await stageBox(p); const x = b.x + 12, y = b.y + b.height - 14; await p.mouse.move(x, y); await p.mouse.down();
  for (let i = 1; i <= 8; i++) await p.mouse.move(x + dx * i / 8, y + dy * i / 8); await p.mouse.up(); };
const pop = (p, k) => p.locator(`.cmp-tools [data-pop="${k}"]`).click();
const H = (p, fn, ...x) => p.evaluate(fn, ...x);

// ── 세션: 판(h..)과 실제 앱(a..) ────────────────────────────────────────────────────
const HS = (name, params, steps, vp = { width: 1280, height: 800 }, opts = {}) => ({ name, kind: 'harness', params, steps, vp, ...opts });
const enter = (p, id) => clickNode(p, id);
const SESS = [
  HS('h01-open-root', 'seed=7', [['open', async () => {}]]),
  HS('h02-enter-topic-subtopic-back', 'seed=7', [['enter area', (p) => enter(p, 'topic:lab/ledger')], ['enter subtopic', (p) => enter(p, 'topic:lab/ledger/core')],
    ['enter deep', (p) => enter(p, 'topic:lab/ledger/core/alpha')], ['back (Backspace)', (p) => p.keyboard.press('Backspace')], ['Escape to root', (p) => p.keyboard.press('Escape')],
    ['Alt+Left', (p) => p.keyboard.press('Alt+ArrowLeft')], ['Alt+Left again', (p) => p.keyboard.press('Alt+ArrowLeft')], ['Alt+Right', (p) => p.keyboard.press('Alt+ArrowRight')]]),
  HS('h03-open-concept-then-details', 'seed=7', [['enter area', (p) => enter(p, 'topic:lab/review')], ['open a concept (left fan)', (p) => clickRole(p, 'left', 0)],
    ['press the centre again (details)', (p) => p.locator('#cmp-nodes .node.sel .hit').first().click()], ['open another node from this layer', (p) => clickRole(p, 'left', 1)],
    ['fold to an earlier hub', (p) => clickNode(p, 'topic:lab/review')]]),
  HS('h04-tree-pick', 'seed=7', [['type in filter', (p) => p.locator('#q').pressSequentially('drift', { delay: 20 })], ['pick first tree row', (p) => p.locator('#tree button:not(.g)').first().click()],
    ['pick a topic group', (p) => p.locator('#tree button.g').nth(5).click()], ['clear filter', (p) => p.locator('#q').fill('')], ['pick a group again', (p) => p.locator('#tree button.g').nth(9).click()]]),
  HS('h05-select-deselect-hover', 'seed=7', [['enter area', (p) => enter(p, 'topic:lab/gate')], ['hover a subtopic', (p) => hoverRole(p, 'right', 0)],
    ['hover a concept (peek)', (p) => hoverRole(p, 'left', 0)], ['hover the hub', (p) => p.locator('#cmp-nodes .node.sel .hit').first().hover().then(() => sleep(650))], ['leave', (p) => neutral(p)]], undefined, { keepMouse: true }),
  HS('h06-wheel-zoom', 'seed=7', [['wheel in', (p) => wheel(p, -240)], ['wheel in more', (p) => wheel(p, -400, .3, .4)], ['wheel out', (p) => wheel(p, 900, .6, .6)],
    ['wheel far out (lod)', (p) => wheel(p, 1600)], ['enter topic while zoomed out', (p) => enter(p, 'topic:lab/policy')]]),
  HS('h07-drag-pan', 'seed=7', [['enter area', (p) => enter(p, 'topic:lab/harness')], ['drag pan', (p) => drag(p, 220, -140)], ['drag pan back', (p) => drag(p, -90, 60)], ['click after pan', (p) => clickRole(p, 'right', 0)]]),
  HS('h08-more-control', 'seed=7', [['enter area', (p) => enter(p, 'topic:lab/ledger')], ['enter big topic', (p) => enter(p, 'topic:lab/ledger/core')],
    ['press more', (p) => p.locator('#cmp-g [data-more]').first().click()], ['redraw by entering a list item', (p) => clickRole(p, 'shelf', 0)], ['back', (p) => p.keyboard.press('Backspace')]]),
  HS('h09-time-popover', 'seed=7', [['open time', (p) => pop(p, 'time')], ['last 7 days', (p) => p.locator('[data-cmp-r="7"]').click()], ['last 30', (p) => p.locator('[data-cmp-r="30"]').click()],
    ['drag bars', async (p) => { const b = await p.locator('[data-bars]').boundingBox(); await p.mouse.move(b.x + b.width * .4, b.y + 20); await p.mouse.down(); await p.mouse.move(b.x + b.width * .7, b.y + 20, { steps: 5 }); await p.mouse.up(); }],
    ['enter topic with range', (p) => enter(p, 'topic:lab/ledger')], ['reopen time, all', async (p) => { await pop(p, 'time'); await p.locator('[data-cmp-r="all"]').click(); }], ['Escape closes', (p) => p.keyboard.press('Escape')]]),
  HS('h10-legend-kinds', 'seed=7', [['enter area', (p) => enter(p, 'topic:lab/ledger')], ['open legend', (p) => pop(p, 'legend')],
    ...['record', 'decision', 'episode', 'concept', 'doc', 'bridge'].map((k) => [`toggle ${k}`, (p) => p.locator(`[data-cmp-k="${k}"]`).click()]),
    ['toggle record back', (p) => p.locator('[data-cmp-k="record"]').click()], ['outside press closes', (p) => p.mouse.click(5, 300)]]),
  HS('h11-search-filter', 'seed=7', [['type query', (p) => p.locator('#q').pressSequentially('gate', { delay: 20 })], ['open legend with query', (p) => pop(p, 'legend')],
    ['open time with query', (p) => pop(p, 'time')], ['clear query', (p) => p.locator('#q').fill('')]]),
  HS('h12-pin-unpin', 'seed=7', [['enter area', (p) => enter(p, 'topic:lab/review')], ['pin hovered concept (P)', async (p) => { await hoverRole(p, 'left', 0); await p.keyboard.press('p'); }],
    ['go elsewhere', (p) => { return neutral(p).then(() => p.keyboard.press('Escape')).then(() => sleep(900)).then(() => enter(p, 'topic:lab/gate')); }], ['hover dock entry', (p) => p.locator('#cmp-dock .pg').first().hover()],
    ['press dock entry', (p) => p.locator('#cmp-dock .pg').first().click()], ['unpin from dock', (p) => p.locator('#cmp-dock [data-x]').first().click()]]),
  HS('h13-bridge', 'seed=7', [['enter area', (p) => enter(p, 'topic:lab/ledger')], ['open bridge', (p) => clickRole(p, 'bridge', 0)], ['back', (p) => p.keyboard.press('Backspace')],
    ['root bridge', (p) => p.keyboard.press('Escape').then(() => sleep(900)).then(() => clickRole(p, 'bridge', 0))]]),
  HS('h14-keyboard', 'seed=7', [['focus a node + Enter', async (p) => { await p.locator('#cmp-nodes .node[data-role="right"]').first().focus(); await p.keyboard.press('Enter'); }],
    ['arrows', async (p) => { for (const k of ['ArrowDown', 'ArrowRight', 'ArrowUp', 'ArrowLeft']) await p.keyboard.press(k); }], ['page keys', async (p) => { await p.keyboard.press('PageDown'); await p.keyboard.press('PageUp'); }],
    ['Space on focused', async (p) => { await p.locator('#cmp-nodes .node[data-role="right"]').first().focus(); await p.keyboard.press(' '); }], ['Tab + Enter', async (p) => { await p.keyboard.press('Tab'); await p.keyboard.press('Enter'); }]]),
  HS('h15-resize-root', 'seed=7', [['wider', (p) => p.setViewportSize({ width: 1600, height: 900 })], ['narrower', (p) => p.setViewportSize({ width: 1000, height: 700 })],
    ['across 900', (p) => p.setViewportSize({ width: 860, height: 700 })], ['back over 900', (p) => p.setViewportSize({ width: 1280, height: 800 })], ['enter after resizes', (p) => enter(p, 'topic:lab/gate')]]),
  HS('h16-resize-in-topic', 'seed=7', [['enter area', (p) => enter(p, 'topic:lab/policy')], ['enter sub', (p) => enter(p, 'topic:lab/policy/core')], ['wider', (p) => p.setViewportSize({ width: 1700, height: 1000 })],
    ['across 900', (p) => p.setViewportSize({ width: 880, height: 640 })], ['back', (p) => p.setViewportSize({ width: 1280, height: 800 })], ['Backspace after resize', (p) => p.keyboard.press('Backspace')]]),
  HS('h17-resize-zoomed', 'seed=7', [['enter area', (p) => enter(p, 'topic:lab/ledger')], ['zoom far out', (p) => wheel(p, 1200)], ['narrower', (p) => p.setViewportSize({ width: 1000, height: 760 })],
    ['across 900', (p) => p.setViewportSize({ width: 820, height: 760 })], ['wheel after resize', (p) => wheel(p, -200)], ['wider', (p) => p.setViewportSize({ width: 1500, height: 800 })]]),
  HS('h18-sidebar-toggle', 'seed=7', [['enter area', (p) => enter(p, 'topic:lab/review')], ['collapse sidebar', (p) => H(p, () => window.__side(false))], ['expand sidebar', (p) => H(p, () => window.__side(true))],
    ['collapse + zoom', async (p) => { await H(p, () => window.__side(false)); await wheel(p, -300); }], ['expand + enter', async (p) => { await H(p, () => window.__side(true)); await clickRole(p, 'right', 0); }]]),
  HS('h19-theme-switch', 'seed=7', [['enter area', (p) => enter(p, 'topic:lab/gate')], ['zoom', (p) => wheel(p, -300)], ['to light', (p) => H(p, () => window.__retheme('light'))],
    ['open legend in light', (p) => pop(p, 'legend')], ['to dark', (p) => H(p, () => window.__retheme('dark'))]]),
  HS('h20-documents-and-back', 'seed=7', [['enter area', (p) => enter(p, 'topic:lab/ledger')], ['open time', (p) => pop(p, 'time')], ['leave (destroy)', (p) => H(p, () => window.__destroy())],
    ['come back (mount)', (p) => H(p, () => window.__mount())], ['leave again', (p) => H(p, () => window.__destroy())], ['back again', (p) => H(p, () => window.__mount())]]),
  HS('h21-reload-saved-pins', 'seed=7', [['enter + pin', async (p) => { await enter(p, 'topic:lab/harness'); await settle(p); await hoverRole(p, 'left', 0); await p.keyboard.press('p'); await neutral(p); }],
    ['reload', (p) => p.reload()], ['enter where pinned', (p) => enter(p, 'topic:lab/harness')], ['path to pinned from summary', (p) => p.locator('#cmp-sum [data-path]').first().click()]]),
  HS('h22-snapshot-roundtrip', 'seed=7', [['enter two levels', async (p) => { await enter(p, 'topic:lab/gate'); await settle(p); await enter(p, 'topic:lab/gate/core'); }],
    ['set filters', async (p) => { await pop(p, 'legend'); await p.locator('[data-cmp-k="record"]').click(); await p.keyboard.press('Escape'); await pop(p, 'time'); await p.locator('[data-cmp-r="30"]').click(); await p.keyboard.press('Escape'); await p.locator('#q').pressSequentially('cache'); }],
    ['zoom', (p) => wheel(p, -200)], ['remount with snapshot', (p) => H(p, () => window.__remount(window.__snap()))], ['Alt+Left after restore', (p) => p.keyboard.press('Alt+ArrowLeft')]]),
  HS('h23-old-shapes', 'seed=7', [
    ['shape: path only', (p) => H(p, () => window.__remount({ path: ['topic:lab', 'topic:lab/review'] }))],
    ['shape: All mode with filt', (p) => H(p, () => window.__remount({ path: ['topic:lab', 'topic:lab/gate', 'topic:lab/gate/core'], hist: [['topic:lab'], ['topic:lab', 'topic:lab/gate'], ['topic:lab', 'topic:lab/gate', 'topic:lab/gate/core']], hi: 2, mode: 'all', filt: [['topic:lab/gate', ['topic:lab/gate/core']]], more: [], koff: ['episode'], tsel: null, view: null, query: '' }))],
    ['shape: path mode with filt and view', (p) => H(p, () => window.__remount({ path: ['topic:lab', 'topic:lab/ledger'], hist: [['topic:lab'], ['topic:lab', 'topic:lab/ledger']], hi: 1, mode: 'path', filt: [['topic:lab', ['x']]], more: [['topic:lab/ledger|record', 20]], koff: [], tsel: ['2026-08-01', '2026-10-01'], view: { x: -900, y: -700, w: 1800, h: 1200 }, query: 'gate' }))],
    ['shape: unknown ids dropped', (p) => H(p, () => window.__remount({ path: ['topic:lab', 'topic:nope', 'topic:lab/policy'], hist: [['topic:gone'], ['topic:lab', 'topic:lab/policy']], hi: 5 }))],
    ['shape: empty path falls back to start', (p) => H(p, () => window.__remount({ path: ['topic:nope'] }))],
    ['shape: current', (p) => H(p, () => window.__remount({ path: ['topic:lab', 'topic:lab/harness'], hist: [['topic:lab', 'topic:lab/harness']], hi: 0, mode: 'path', filt: [], more: [], koff: [], tsel: null, view: null, query: '' }))]]),
  HS('h24-ko-deck', 'seed=7&lang=ko', [['enter area', (p) => enter(p, 'topic:lab/ledger')], ['open concept', (p) => clickRole(p, 'left', 0)], ['time', (p) => pop(p, 'time')], ['legend', (p) => pop(p, 'legend')]]),
  HS('h25-web-skin', 'seed=7&skin=web', [['enter area', (p) => enter(p, 'topic:lab/review')], ['enter sub', (p) => clickRole(p, 'right', 0)], ['legend', (p) => pop(p, 'legend')]]),
  HS('h26-light-start', 'seed=7&theme=light', [['enter area', (p) => enter(p, 'topic:lab/policy')], ['open list item', (p) => clickRole(p, 'shelf', 0)], ['to dark', (p) => H(p, () => window.__retheme('dark'))]]),
  HS('h27-summary-follow', 'seed=7&start=lab/review/core', [['open concept', (p) => clickRole(p, 'left', 0)], ['follow first relation', (p) => p.locator('#cmp-sum [data-follow]').first().click()],
    ['expand a relation list', (p) => p.locator('#cmp-sum [data-sx]').first().click().catch(() => {})], ['go to a neighbour', (p) => p.locator('#cmp-sum [data-go]').first().click()], ['details button', (p) => p.locator('#cmp-sum [data-sopen]').click()]]),
  HS('h28-start-path', 'seed=7&start=lab/gate/core/alpha', [['open shelf item', (p) => clickRole(p, 'shelf', 0).catch(() => clickRole(p, 'left', 0))], ['Escape', (p) => p.keyboard.press('Escape')]]),
  HS('h29-small-window', 'seed=7', [['enter area', (p) => enter(p, 'topic:lab/ledger')], ['enter big topic', (p) => enter(p, 'topic:lab/ledger/core')], ['time', (p) => pop(p, 'time')]], { width: 960, height: 600 }),
  HS('h30-other-seed', 'seed=23', [['enter first right', (p) => clickRole(p, 'right', 0)], ['enter next', (p) => clickRole(p, 'right', 0)], ['open concept', (p) => clickRole(p, 'left', 0)], ['wheel', (p) => wheel(p, 500)]]),
  HS('h31-pin-path-through-layers', 'seed=7', [['pin deep node', async (p) => { await enter(p, 'topic:lab/gate'); await settle(p); await enter(p, 'topic:lab/gate/core'); await settle(p); await hoverRole(p, 'left', 0); await p.keyboard.press('p'); await neutral(p); }],
    ['escape to root (pin chain stays)', (p) => p.keyboard.press('Escape')], ['enter another area', (p) => enter(p, 'topic:lab/policy')], ['hover pinned mark', (p) => hoverRole(p, 'left', 0)]], undefined, { keepMouse: true }),
  HS('h32-reduced-motion', 'seed=7', [['enter area', (p) => enter(p, 'topic:lab/ledger')], ['enter sub', (p) => enter(p, 'topic:lab/ledger/edge')], ['wheel', (p) => wheel(p, -300)]], undefined, { reduced: true }),
];

// 실제 앱(사이드카 8797): 셸 · LNB · 머리 · 문서 탭과 함께
const AS = (name, ls, steps, vp = { width: 1280, height: 800 }, q = '') => ({ name, kind: 'app', ls, steps, vp, q });
const lnbGroup = (p, n) => p.locator('.lnb-group').nth(n).click();
const APPS = [
  AS('a01-app-open-enter-back', {}, [['enter first subtopic', (p) => clickRole(p, 'right', 0)], ['open concept', (p) => clickRole(p, 'left', 0)], ['Backspace', (p) => p.keyboard.press('Backspace')],
    ['Escape', (p) => p.keyboard.press('Escape')], ['Alt+Left', (p) => p.keyboard.press('Alt+ArrowLeft')], ['wheel', (p) => wheel(p, -300)], ['drag', (p) => drag(p, 160, -80)]]),
  AS('a02-app-lnb-and-search', {}, [['open lnb group', (p) => lnbGroup(p, 2)], ['pick lnb row', (p) => p.locator('.lnb-doc:visible').first().click()],
    ['type search', (p) => p.locator('#tb-q').click().then(() => p.keyboard.type('agent', { delay: 30 }))], ['clear search', (p) => p.locator('#tb-q').fill('')]]),
  AS('a03-app-resize-sidebar', {}, [['enter first subtopic', (p) => clickRole(p, 'right', 0)], ['wider', (p) => p.setViewportSize({ width: 1600, height: 900 })], ['across 900', (p) => p.setViewportSize({ width: 860, height: 700 })],
    ['back', (p) => p.setViewportSize({ width: 1280, height: 800 })], ['sidebar collapse', (p) => p.locator('#tb-shell-toggle').click()], ['sidebar expand', (p) => p.locator('#tb-shell-toggle').click()]]),
  AS('a04-app-theme-docs-back', {}, [['enter first subtopic', (p) => clickRole(p, 'right', 0)], ['theme light', (p) => H(p, () => { localStorage.setItem('vinc.theme', 'light'); window.dispatchEvent(new Event('vinc:theme')); })],
    ['popover time', (p) => pop(p, 'time')], ['to Documents', (p) => H(p, () => { location.hash = '#home'; })], ['back to Graph', (p) => H(p, () => { location.hash = '#view'; })], ['theme dark', (p) => H(p, () => { localStorage.setItem('vinc.theme', 'dark'); window.dispatchEvent(new Event('vinc:theme')); })]]),
  AS('a05-app-ko-light-web', { 'vinc.lang': 'ko', 'vinc.theme': 'light' }, [['enter first subtopic', (p) => clickRole(p, 'right', 0)], ['legend', (p) => pop(p, 'legend')], ['toggle record', (p) => p.locator('[data-cmp-k="record"]').click()]], undefined, '?skin=web'),
  AS('a06-app-reload-pins', {}, [['pin a concept', async (p) => { await clickRole(p, 'right', 0); await settle(p); await hoverRole(p, 'left', 0); await p.keyboard.press('p'); await neutral(p); }], ['reload', (p) => p.reload()], ['Escape', (p) => p.keyboard.press('Escape')]]),
];

async function runSession(b, base, s, out) {
  const dir = `${out}/${s.name}`; await mkdir(dir, { recursive: true });
  const ctx = await b.newContext({ viewport: s.vp, reducedMotion: s.reduced ? 'reduce' : 'no-preference', deviceScaleFactor: 1 });
  await ctx.addInitScript(COUNTER);
  if (s.kind === 'app') {
    const tok = process.env.SIDECAR_TOKEN || 'devtoken';
    const LS = { 'vinc.sidecar.token': tok, 'vinc.sidecar.base': process.env.SIDECAR || 'http://127.0.0.1:8797', 'vinc.lang': 'en', 'vinc.theme': 'dark', 'vinc.firstrun.seen': '1', 'vinc.view.coach.seen': '1', 'vinc.owner': 'tenant:local', 'vinc.mode': 'local', 'vinc.sync.linked': '1', 'vinc.sync.username': 'demo', ...s.ls };
    await ctx.addInitScript((kv) => { if (!sessionStorage.getItem('__seeded')) { for (const [k, v] of Object.entries(kv)) localStorage.setItem(k, v); sessionStorage.setItem('__seeded', '1'); } }, LS);
  } else await ctx.addInitScript(() => { if (!sessionStorage.getItem('__seeded')) { localStorage.clear(); sessionStorage.setItem('__seeded', '1'); } });
  const p = await ctx.newPage(); const errs = []; p.on('pageerror', (e) => errs.push(String(e.message).split('\n')[0]));
  const url = s.kind === 'app' ? base.replace('127.0.0.1', 'tauri.localhost') + `/${s.q}#view` : `${base}/dev/compass-lens.html?${s.params}`;
  await p.goto(url); await p.evaluate(() => document.fonts.ready);
  if (s.kind === 'app') await p.waitForSelector('#cmp-g #cmp-nodes .node', { timeout: 60000, state: 'attached' });
  await settle(p, 1500); await neutral(p); await settle(p, 200);
  await capture(p, dir, 0, 'mounted');
  let i = 1;
  for (const [label, fn] of s.steps) {
    const before = errs.length;
    try { await fn(p); } catch (e) { errs.push(`step "${label}": ${String(e.message).split('\n')[0]}`); }
    await p.evaluate(() => document.fonts.ready).catch(() => {});
    await settle(p); if (!s.keepMouse && !/hover/.test(label)) { await neutral(p); await settle(p, 150); }
    await capture(p, dir, i++, label + (errs.length > before ? ` [errors: ${errs.slice(before).join(' | ')}]` : ''));
  }
  await writeFile(`${dir}/errors.json`, JSON.stringify(errs, null, 1));
  await ctx.close(); return errs;
}

async function record(base, out) {
  const b = await pw.chromium.launch(); await mkdir(out, { recursive: true });
  const only = process.env.SESSIONS ? process.env.SESSIONS.split(',') : null;
  const all = [...SESS, ...(process.env.APP === '0' ? [] : APPS)].filter((s) => !only || only.includes(s.name));
  for (const s of all) { const t0 = Date.now(); let errs; try { errs = await runSession(b, base, s, out); } catch (e) { errs = [`session: ${e.message}`]; }
    console.log(`${s.name} ${((Date.now() - t0) / 1000).toFixed(1)}s${errs.length ? ' errors: ' + errs.join(' | ') : ''}`); }
  await b.close();
}

// ── 대조: 스크린숏은 픽셀로(브라우저 캔버스에서 푼다), 나머지는 글자 그대로 ───────────────────────
async function compare(A, B, outFile) {
  const b = await pw.chromium.launch(), p = await b.newPage();
  const px = (x, y) => p.evaluate(async ([a, c]) => { const load = (s) => new Promise((r) => { const i = new Image(); i.onload = () => r(i); i.src = `data:image/png;base64,${s}`; });
    const [ia, ib] = await Promise.all([load(a), load(c)]); if (ia.width !== ib.width || ia.height !== ib.height) return { size: [ia.width, ia.height, ib.width, ib.height], diff: -1 };
    const cv = (i) => { const c2 = document.createElement('canvas'); c2.width = i.width; c2.height = i.height; const g = c2.getContext('2d'); g.drawImage(i, 0, 0); return g.getImageData(0, 0, i.width, i.height).data; };
    const da = cv(ia), db = cv(ib); let diff = 0, maxd = 0; for (let k = 0; k < da.length; k += 4) { const d = Math.max(Math.abs(da[k] - db[k]), Math.abs(da[k + 1] - db[k + 1]), Math.abs(da[k + 2] - db[k + 2])); if (d) { diff++; if (d > maxd) maxd = d; } }
    return { diff, maxd, w: ia.width, h: ia.height }; }, [x.toString('base64'), y.toString('base64')]);
  const rep = { a: A, b: B, sessions: [], steps: 0, differing: 0 };
  for (const s of (await readdir(A)).sort()) { if (!existsSync(`${B}/${s}`)) { rep.sessions.push({ s, missing: true }); continue; }
    const files = (await readdir(`${A}/${s}`)).filter((f) => f.endsWith('.json') && f !== 'errors.json').sort(), ses = { s, steps: [] };
    const ea = JSON.parse(await readFile(`${A}/${s}/errors.json`, 'utf8')), eb = existsSync(`${B}/${s}/errors.json`) ? JSON.parse(await readFile(`${B}/${s}/errors.json`, 'utf8')) : null;
    if (JSON.stringify(ea) !== JSON.stringify(eb)) ses.errors = { a: ea, b: eb };
    for (const f of files) { const n = f.slice(0, -5); rep.steps++;
      if (!existsSync(`${B}/${s}/${f}`)) { ses.steps.push({ n, missing: true }); rep.differing++; continue; }
      const ja = JSON.parse(await readFile(`${A}/${s}/${f}`, 'utf8')), jb = JSON.parse(await readFile(`${B}/${s}/${f}`, 'utf8'));
      const keys = ['svg', 'viewBox', 'cls', 'snap', 'hash', 'live', 'side', 'pop', 'tip', 'tools', 'log', 'cnt'].filter((k) => JSON.stringify(ja[k]) !== JSON.stringify(jb[k]));
      const pix = await px(await readFile(`${A}/${s}/${n}.png`), await readFile(`${B}/${s}/${n}.png`));
      const bad = keys.length || pix.diff !== 0;
      if (bad) rep.differing++;
      ses.steps.push({ n, label: ja.label, ok: !bad, keys, pix: pix.diff ? pix : 0, ...(keys.includes('cnt') ? { cnt: [ja.cnt, jb.cnt] } : {}), ...(keys.includes('viewBox') ? { vb: [ja.viewBox, jb.viewBox] } : {}) }); }
    rep.sessions.push(ses); }
  await b.close();
  const txt = JSON.stringify(rep, null, 1); if (outFile) await writeFile(outFile, txt);
  console.log(`sessions ${rep.sessions.length} · steps ${rep.steps} · differing ${rep.differing}`);
  for (const s of rep.sessions) { const bad = (s.steps || []).filter((x) => !x.ok); if (bad.length || s.missing || s.errors) console.log(`  ${s.s}: ${s.missing ? 'missing' : bad.map((x) => `${x.n}(${x.keys.join('+')}${x.pix ? ` px ${x.pix.diff}/${x.pix.maxd}` : ''})`).join(' ')}${s.errors ? ' errors differ' : ''}`); }
}

if (cmd === 'record') await record(a1, a2);
else if (cmd === 'compare') await compare(a1, a2, a3);
else console.log('usage: record <base> <out> | compare <dirA> <dirB> [report.json]');
