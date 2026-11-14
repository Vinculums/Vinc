import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile, readdir } from 'node:fs/promises';
import ts from 'typescript';

// 앱이 기기 밖으로 무엇을 보내는가(FR-APP-43)와 웹 빌드가 데스크톱과 무엇이 다른가(FR-APP-46).
// 두 모듈(api.ts · settings.ts · sheets.ts)은 DOM 과 부팅 상태를 통째로 끌고 와서 싣지 않는다.
// 대신 판정이 들어 있는 조각을 소스에서 잘라 그대로 돌리고, 나머지는 소스를 읽어 대조한다.
// 근거: [[vinc.frd.app]] FR-APP-43 · FR-APP-46
const read = (p) => readFile(new URL(`../${p}`, import.meta.url), 'utf8');
const url = (js) => `data:text/javascript;base64,${Buffer.from(js).toString('base64')}`;
const run = async (code) => (await import(url(ts.transpileModule(code,
  { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText))).default;
/** `start` 로 시작해 `end` 앞에서 끝나는 소스 조각. 못 찾으면 테스트가 깨진다(조용히 빈 조각이 되지 않게). */
function slice(src, start, end) {
  const a = src.indexOf(start);
  assert.ok(a >= 0, `missing: ${start}`);
  const b = src.indexOf(end, a + start.length);
  assert.ok(b > a, `missing: ${end}`);
  return src.slice(a, b);
}
async function sources(dir = 'src') {
  const out = [];
  for (const e of await readdir(new URL(`../${dir}`, import.meta.url), { withFileTypes: true })) {
    const p = `${dir}/${e.name}`;
    if (e.isDirectory()) out.push(...await sources(p));
    else if (/\.(ts|js)$/.test(e.name)) out.push([p, await read(p)]);
  }
  return out;
}

const api = await read('src/api.ts');

test('a failed search is logged with status and duration only', async () => {
  const fn = slice(api, 'async function logSearchFailure', '\n}\n') + '\n}\n';
  const logSearchFailure = await run(`const sent = []; const warned = [];
    const console = { warn: (m) => warned.push(m) };
    async function shellInvoke() { return async (cmd, args) => { sent.push([cmd, args]); }; }
    ${fn}
    export default async (s, d) => { await logSearchFailure(s, d); return { sent, warned }; };`);
  const { sent, warned } = await logSearchFailure(503, 12.4);
  assert.deepEqual(sent, [['plugin:log|log', { level: 4, message: 'GET /api/graph/search failed status=503 duration_ms=12', location: 'search' }]]);
  assert.deepEqual(warned, ['[search] GET /api/graph/search failed status=503 duration_ms=12']);
  // 부르는 자리마다 넘기는 것은 상태와 걸린 시간뿐이다(질의 문자열이 인자로 들어갈 자리가 없다).
  const args = [...api.matchAll(/await logSearchFailure\(([^;]*)\);/g)].map((m) => m[1]);
  assert.ok(args.length >= 3);
  for (const a of args) assert.match(a, /^(0|200|response\.status), performance\.now\(\) - started$/);
});

test('the app sends no usage, crash or analytics data', async () => {
  const files = await sources();
  const offenders = [];
  for (const [p, s] of files) {
    if (/sendBeacon|new WebSocket|XMLHttpRequest|new EventSource|sentry|posthog|gtag\(|mixpanel|amplitude|telemetry/i.test(s.replace(/\/\/.*$|\/\*[\s\S]*?\*\//gm, '')))
      offenders.push(p);
  }
  assert.deepEqual(offenders, []);
  const pkg = JSON.parse(await read('package.json'));
  for (const dep of Object.keys({ ...pkg.dependencies, ...pkg.devDependencies }))
    assert.doesNotMatch(dep, /sentry|posthog|analytics|mixpanel|amplitude|segment|datadog|bugsnag|rollbar/i);
  const cargo = await read('src-tauri/Cargo.toml');
  assert.doesNotMatch(cargo, /sentry|analytics|telemetry|bugsnag|rollbar/i);
});

test('outbound addresses are the graph and the site pages only', async () => {
  const allowed = [/^https:\/\/vincs\.io\/(terms|privacy|connect-app|account|app)\//, /^https:\/\/mcp\.vincs\.io\/mcp$/,
    /^http:\/\/127\.0\.0\.1:/, /^http:\/\/tauri\.localhost$/, /^http:\/\/www\.w3\.org\/2000\/svg$/,
    /^https:\/\/modelcontextprotocol\.io\/favicon\.svg$/];
  const found = [];
  for (const [p, s] of await sources()) {
    for (const m of s.matchAll(/https?:\/\/[-\w.:/${}?=&%#]+/g)) {
      const u = m[0].replace(/\$\{.*$/, '').replace(/[`'"]$/, '');
      if (!allowed.some((re) => re.test(u))) found.push(`${p}: ${u}`);
    }
  }
  assert.deepEqual(found, []);
  // fetch 는 사이드카(`cfg.base`) · 같은 오리진 · 같은 오리진 상대 경로로만 간다.
  let fetches = 0;
  for (const [p, s] of await sources()) {
    for (const m of s.matchAll(/\bfetch\((new URL\([^)]*\)|"[^"]*"|[\w.]+)/g)) {
      // `new URL(path, location.origin)` 은 계정 쿠키 라우트의 문(api.ts `webFetch`) 하나다. 그 경로는 아래에서 본다.
      assert.match(m[1], /^(new URL\("[^"]+", (cfg\.base|location\.origin)\)|new URL\(path, location\.origin\)|"\/api\/[^"]+"|url)$/, `${p}: fetch(${m[1]})`);
      fetches++;
    }
  }
  assert.ok(fetches >= 5);
  // 계정 쿠키 라우트는 `webFetch` 로 같은 오리진에 간다. 경로는 부르는 자리의 글자 그대로다(같은 오리진의 절대 경로).
  const doors = [...api.matchAll(/\bwebFetch\(([^)]*?)[,)]/g)].map((m) => m[1]).filter((a) => !a.startsWith('path: string'));
  assert.ok(doors.length >= 9 && doors.every((a) => /^"\/[^"]+"$/.test(a)), doors.join(' | '));
  assert.match(api, /const url = new URL\(WEB \? "\/api\/graph" \+ path : path, cfg\.base\);/);
});

test('the web build has no Storage, connections or Advanced in Settings', async () => {
  const settings = await read('src/surfaces/settings.ts');
  const cats = slice(settings, 'const ALL_CATS', '\n  let active');
  // 분류는 다섯 묶음 아래에 선다. 알림은 웹에서만, 공간은 계정 그래프(웹 · 셸 계정 모드)에서만.
  const ids = await run(`export default (WEB, REMOTE, SELF = false) => { const U = new Proxy({}, { get: (o, k) => k });
    const COPY = { ui: { tools: { import: "import" } } }; const remote = () => REMOTE; const selfHosted = () => SELF;
    ${cats} return { ids: CATS.map((c) => c.id), groups: GROUPS.map((g) => g.label) }; };`);
  assert.deepEqual(ids(true, true).ids, ['general', 'appearance', 'reading', 'shortcuts', 'notifications',
    'account', 'spaces', 'import', 'share', 'graphmd', 'about']);
  // 보일 분류가 없는 묶음(연결)은 머리째 빠진다.
  assert.deepEqual(ids(true, true).groups, ['groupApp', 'groupAccount', 'groupData', 'groupSystem']);
  assert.deepEqual(ids(false, false).ids, ['general', 'appearance', 'reading', 'shortcuts',
    'account', 'storage', 'apps', 'assistants', 'terminal', 'import', 'share', 'graphmd', 'advanced', 'about']);
  assert.deepEqual(ids(false, true).ids, ['general', 'appearance', 'reading', 'shortcuts',
    'account', 'spaces', 'storage', 'apps', 'assistants', 'terminal', 'import', 'share', 'graphmd', 'advanced', 'about']);
  // 셀프호스트 허브: 알림·공간이 없다.
  assert.deepEqual(ids(true, true, true).ids, ['general', 'appearance', 'reading', 'shortcuts',
    'account', 'import', 'share', 'graphmd', 'about']);
});

test('Share offers Community on the web only', async () => {
  const sheets = await read('src/sheets.ts');
  const dests = slice(sheets, 'const dests: Dest[] = [];', '  dests.push({\n    key: "file"');
  const keys = await run(`export default (WEB, cfg) => { const U = new Proxy({}, { get: (o, k) => k }); ${dests} return dests.map((d) => d.key); };`);
  assert.deepEqual(keys(true, { team: '' }), ['community']);
  assert.deepEqual(keys(true, { team: 't' }), ['personal', 'community']);
  assert.deepEqual(keys(false, { team: '' }), []);
  assert.deepEqual(keys(false, { team: 't' }), ['personal']);
});

test('the web is the hosted origin, not the shell or local dev', async () => {
  const flags = slice(api, 'export const IN_SHELL', '\n\n// ──');
  const decide = await run(`export default (location, SELF_HOSTED_BUILD = false) => { ${flags.replace(/export /g, '').replace(/const DEFAULT_BASE[^\n]*\n/, '')} return { IN_SHELL, WEB }; };`);
  assert.deepEqual(decide({ protocol: 'https:', hostname: 'vincs.io' }), { IN_SHELL: false, WEB: true });
  assert.deepEqual(decide({ protocol: 'tauri:', hostname: 'tauri.localhost' }), { IN_SHELL: true, WEB: false });
  assert.deepEqual(decide({ protocol: 'http:', hostname: 'localhost' }), { IN_SHELL: false, WEB: false });
  assert.deepEqual(decide({ protocol: 'http:', hostname: '127.0.0.1' }), { IN_SHELL: false, WEB: false });
  // 셀프호스트 허브의 번들은 localhost 로 열어도 웹 Vinc 다(허브가 같은 출처의 /login·/api/* 를 답한다).
  assert.deepEqual(decide({ protocol: 'http:', hostname: 'localhost' }, true), { IN_SHELL: false, WEB: true });
  assert.deepEqual(decide({ protocol: 'https:', hostname: 'vinc.example.com' }, true), { IN_SHELL: false, WEB: true });
  assert.deepEqual(decide({ protocol: 'tauri:', hostname: 'tauri.localhost' }, true), { IN_SHELL: true, WEB: false });
});

test('spaces are switched from the view header, on the account graph only', async () => {
  const main = await read('src/main.ts');
  // 계정 그래프(웹 · 셸의 계정 모드)일 때만 머리를 세운다. 로컬 모드에는 고를 공간이 없다.
  // 셀프호스트 허브에는 개인 그래프 하나뿐이라 머리를 세우지 않는다(`self-hosted.ts`).
  assert.match(main, /\n  if \(remote\(\) && !selfHosted\(\)\) mountSpaceSwitcher\(\);\n/);
  // 머리는 모드와 스페이스가 정해진 뒤에 선다(주소의 `?space=` 를 받은 뒤).
  assert.ok(main.indexOf('mountSpaceSwitcher();') > main.indexOf('await adoptSpaceFromAddress()'));
  const settings = await read('src/surfaces/settings.ts');
  const spaces = slice(settings, 'function renderSpaces', '\n  }\n');
  assert.doesNotMatch(settings, /space-picker|mountSpacePicker|space-pick/);
  // 공간 분류에는 그 자리를 알리는 한 줄과(개인 공간이면) 주제 옮기기만 남는다.
  assert.match(spaces, /settingsRow\(\{ title: U\.space, desc: U\.spaceHint \}\)/);
  assert.match(spaces, /!cfg\.team \? settingsCard\(\[settingsRow\(\{ wide: `<div id="topic-conversion"><\/div>` \}\)\]\) : ""/);
  assert.match(spaces, /mountTopicConversion\(conversion\)/);
  const switcher = await read('src/space-switcher.ts');
  assert.match(switcher, /createSpacePicker\(deps\)/, 'the switching rules stay in createSpacePicker');
});

test('the space name is kept next to the space id and never names another space', async () => {
  const block = slice(api, 'export const cfg = {', '\n};\n') + '\n};\n';
  const make = await run(`const store = new Map();
    const localStorage = { getItem: (k) => store.has(k) ? store.get(k) : null, setItem: (k, v) => store.set(k, String(v)), removeItem: (k) => store.delete(k) };
    let REMOTE = true; const remote = () => REMOTE; const viewSessionCache = { clear() {} };
    const BASE_KEY = "b", TOKEN_KEY = "t", OWNER_KEY = "o", LANG_KEY = "l", DEFAULT_BASE = "";
    ${api.match(/const TEAM_KEY = [^\n]*\n/)[0]}${api.match(/const TEAM_NAME_KEY = [^\n]*\n/)[0]}
    ${block.replace('export const cfg', 'const cfg')}
    export default () => ({ cfg, store, local: () => { REMOTE = false; } });`);
  const { cfg, local } = make();
  cfg.team = 't-1'; cfg.teamName = 'Acme';
  assert.equal(cfg.teamName, 'Acme');
  cfg.team = 't-2';
  assert.equal(cfg.teamName, '', 'another space does not inherit the name');
  cfg.teamName = 'Field'; cfg.team = 't-2';
  assert.equal(cfg.teamName, 'Field', 'setting the same space keeps its name');
  cfg.team = '';
  assert.equal(cfg.teamName, '', 'personal has no stored name');
  cfg.team = 'community:c'; cfg.teamName = 'Physics';
  local();
  assert.equal(cfg.team, ''); assert.equal(cfg.teamName, '', 'local mode names no account space');
});

// 계정 쿠키 라우트의 문(api.ts `webFetch`): 쿠키 · CSRF · JSON 을 거기 한 곳에서 싣는다. 함수를 소스에서 잘라 가짜 fetch 로 돌린다.
test('the account-cookie routes go through one door: the cookie always, the CSRF mark on writes, JSON when a body is given', async () => {
  const fn = slice(api, 'function webFetch(', '\n}\n') + '\n}\n';
  const webFetch = await run(`const sent = []; const location = { origin: 'https://vincs.io' };
    const fetch = async (url, init) => { sent.push([String(url), init]); return { ok: true }; };
    const csrfToken = () => 'tok';
    ${fn}
    export default async (...a) => { sent.length = 0; await webFetch(...a); return sent[0]; };`);
  const signal = new AbortController().signal;
  assert.deepEqual(await webFetch('/api/teams'), ['https://vincs.io/api/teams', { credentials: 'include' }]);
  assert.deepEqual(await webFetch('/api/me', {}), ['https://vincs.io/api/me', { credentials: 'include' }]);
  // 부르는 쪽의 옵션은 받은 그대로(값이 undefined 여도 열쇠째) 넘긴다.
  assert.deepEqual(await webFetch('/api/me', { cache: undefined }), ['https://vincs.io/api/me', { credentials: 'include', cache: undefined }]);
  assert.deepEqual(await webFetch('/api/community/agents/destinations', { cache: 'no-store', signal }),
    ['https://vincs.io/api/community/agents/destinations', { credentials: 'include', cache: 'no-store', signal }]);
  assert.deepEqual(await webFetch('/api/push/test', { method: 'POST', cache: 'no-store', json: { a: 1 } }),
    ['https://vincs.io/api/push/test', { method: 'POST', credentials: 'include', cache: 'no-store',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': 'tok' }, body: '{"a":1}' }]);
  assert.deepEqual(await webFetch('/api/push/subscribe', { method: 'POST', cache: 'no-store', json: undefined }),
    ['https://vincs.io/api/push/subscribe', { method: 'POST', credentials: 'include', cache: 'no-store',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': 'tok' }, body: undefined }]);
  assert.deepEqual(await webFetch('/logout', { method: 'POST' }),
    ['https://vincs.io/logout', { method: 'POST', credentials: 'include', headers: { 'X-CSRF-Token': 'tok' } }]);
});

test('api.ts sends the account cookie only through webFetch and the graph transport', () => {
  // `credentials: "include"` 는 두 자리뿐이다: 계정 쿠키 라우트의 문과, 웹의 그래프 평면(localCall).
  assert.equal([...api.matchAll(/credentials: "include"/g)].length, 2);
  for (const route of ['/api/me', '/api/teams', '/api/push/devices', '/api/push/subscribe', '/api/push/unsubscribe',
    '/api/push/test', '/api/community/agents/destinations', '/logout', '/api/selfhost/seats'])
    assert.match(api, new RegExp(`webFetch\\("${route.replace(/\//g, '\\/')}"`), route);
  assert.doesNotMatch(api, /fetch\(new URL\("\/api\/(?!graph)/, 'no raw fetch to an account route');
});
