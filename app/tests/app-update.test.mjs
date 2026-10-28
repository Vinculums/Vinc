import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

// update.ts 를 그대로 싣고 copy · loaders 와 두 updater 플러그인만 갈아 끼운다. 셸인지는 모듈이 실릴 때
// `location` 으로 정하므로 경우마다 새 판을 싣는다. 서명 검증은 updater 플러그인이 tauri.conf.json 의
// pubkey 로 하므로 여기서는 그 설정이 있는지만 본다. 근거: [[vinc.frd.app]] FR-APP-42
const url = (js) => `data:text/javascript;base64,${Buffer.from(js).toString('base64')}`;
const src = ts.transpileModule(await readFile(new URL('../src/update.ts', import.meta.url), 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const stubs = {
  './copy': `export const COPY = { ui: { update: { available: (v) => 'ready ' + v, later: 'LATER', install: 'INSTALL',
    downloading: 'downloading', pct: (p) => 'pct ' + p, installing: 'installing', failed: 'failed' } } };`,
  './vinc-loaders.js': 'export const loaderInline = (s) => s;',
  '@tauri-apps/plugin-updater': 'export const check = () => globalThis.__upd.check();',
  '@tauri-apps/plugin-process': 'export const relaunch = () => globalThis.__upd.relaunch();',
};

/** 토스트가 쓰는 만큼만 흉내 낸 요소. 셀렉터는 `.class` 와 `[data-act="..."]` 만 안다. */
function el() {
  const node = { children: [], listeners: {}, attrs: {}, removed: false, textContent: '', className: '',
    setAttribute(k, v) { this.attrs[k] = v; }, remove() { this.removed = true; },
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
    async click() { await Promise.all((this.listeners.click || []).map((fn) => fn())); },
    querySelector(sel) { return this.children.find((c) => c.sel.includes(sel)) ?? null; },
    get innerHTML() { return this._html; },
    set innerHTML(html) {
      this._html = html;
      this.children = [...html.matchAll(/<(\w+) class="([^"]*)"(?: data-act="([^"]*)")?/g)].map((m) => {
        const c = el();
        c.sel = [`.${m[2].split(' ')[0]}`, ...(m[3] ? [`[data-act="${m[3]}"]`] : [])];
        return c;
      });
    },
  };
  node._html = '';
  return node;
}

let fresh = 0;
async function boot({ shell = true, update = null, checkFails = false } = {}) {
  const body = el(), made = [], log = [];
  globalThis.location = shell ? { protocol: 'tauri:', hostname: 'tauri.localhost' } : { protocol: 'https:', hostname: 'vincs.io' };
  globalThis.document = { body: { appendChild: (n) => body.children.push(n) }, createElement: () => { const n = el(); made.push(n); return n; } };
  globalThis.window = { matchMedia: () => ({ matches: true }) };
  globalThis.__upd = {
    check: async () => { log.push('check'); if (checkFails) throw new Error('offline'); return update; },
    relaunch: async () => { log.push('relaunch'); },
  };
  let js = src;
  for (const [from, to] of Object.entries(stubs)) js = js.replaceAll(JSON.stringify(from), JSON.stringify(url(to)));
  const { checkForUpdate } = await import(url(`${js}\n// ${fresh++}`));
  await checkForUpdate();
  return { toast: made[0] ?? null, shown: body.children, log };
}
const release = (log, events = []) => ({ version: '9.9.9',
  downloadAndInstall: async (cb) => { log.push('install'); for (const e of events) cb(e); } });

test('the web never checks for an update', async () => {
  const s = await boot({ shell: false, update: release([]) });
  assert.deepEqual(s.log, []);
  assert.equal(s.toast, null);
});

test('a failed check and an up to date app stay silent', async () => {
  assert.equal((await boot({ checkFails: true })).toast, null);
  assert.equal((await boot({ update: null })).toast, null);
});

test('a newer release offers Later and Install & restart and installs nothing until pressed', async () => {
  const installs = [];
  const s = await boot({ update: release(installs) });
  assert.match(s.toast.innerHTML, /ready 9\.9\.9/);
  assert.match(s.toast.innerHTML, /data-act="later">LATER</);
  assert.match(s.toast.innerHTML, /data-act="install">INSTALL</);
  assert.deepEqual(installs, []);
  await s.toast.querySelector('[data-act="later"]').click();
  assert.equal(s.toast.removed, true);
  assert.deepEqual(installs, []);
});

test('install shows percent progress and then restarts', async () => {
  const log = [];
  const s = await boot({ update: release(log, [{ event: 'Started', data: { contentLength: 200 } },
    { event: 'Progress', data: { chunkLength: 50 } }]) });
  const msg = s.toast.querySelector('.update-msg');
  const seen = [];
  Object.defineProperty(msg, 'innerHTML', { set(v) { seen.push(v); }, get() { return seen.at(-1); } });
  await s.toast.querySelector('[data-act="install"]').click();
  assert.ok(seen.includes('pct 25'));
  assert.deepEqual(log, ['install']);
  assert.deepEqual(s.log, ['check', 'relaunch']);
});

test('the English toast says Later and Install & restart', async () => {
  const copy = await readFile(new URL('../src/copy.ts', import.meta.url), 'utf8');
  const block = copy.slice(copy.indexOf('update: {'), copy.indexOf('},', copy.indexOf('update: {')));
  assert.match(block, /install: "Install & restart"/);
  assert.match(block, /later: "Later"/);
});

test('the updater only accepts releases signed for the configured key', async () => {
  const conf = JSON.parse(await readFile(new URL('../src-tauri/tauri.conf.json', import.meta.url), 'utf8'));
  const updater = conf.plugins?.updater;
  assert.ok(updater, 'plugins.updater is configured');
  assert.deepEqual(updater.endpoints, ['https://github.com/Vinculums/Vincs-app/releases/latest/download/latest.json']);
  const key = Buffer.from(updater.pubkey, 'base64').toString('utf8');
  assert.match(key, /^untrusted comment: minisign public key: [0-9A-F]{16}\n\S{40,}\n?$/);
});
