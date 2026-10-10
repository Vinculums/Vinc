// 실제 Vite 앱과 합성 사이드카를 검증한 뒤 오류 상태 응답만 따로 주입한다.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VINC_PLAYWRIGHT || 'playwright');
const base = process.env.VINC_CHECK_BASE || 'http://127.0.0.1:5193';
const owner = 'user:synthetic-ui';
const token = 'synthetic-local-test-token';
const output = process.env.VINC_CHECK_OUTPUT || 'agent-discovery-check.json';
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const requests = [];
page.on('request', r => { if (new URL(r.url()).pathname === '/roles') requests.push(r.url()); });
await page.addInitScript(({ owner, token }) => {
  localStorage.setItem('vinc.owner', owner);
  localStorage.setItem('vinc.sidecar.token', token);
  localStorage.setItem('vinc.shell.lnb.kind', 'agent');
  localStorage.setItem('vinc.lang', 'en');
}, { owner, token });
const results = [];
try {
  await page.goto(base + '/#view', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => document.querySelectorAll('.lnb-agent-role').length === 108,
                             { timeout: 60000 });
  const rows = page.locator('.lnb-agent-role');
  const ids = await rows.evaluateAll(xs => xs.map(x => x.dataset.nodeid));
  assert.equal(new Set(ids).size, 108);
  assert(ids.includes('concept:agent-english'));
  assert(ids.includes('concept:agent-legacy') && ids.includes('concept:agent-old'));
  assert(!ids.some(id => /ordinary|disabled|foreign|orchestration/.test(id)));
  assert(requests.some(url => new URL(url).searchParams.get('offset') === '200'));
  await page.waitForFunction(() => decodeURIComponent(location.hash).includes('ai/english/voice/girlfriend'));
  results.push({ case: 'real app /roles through Vite proxy + real synthetic embedded store',
                 passed: true, count: ids.length, unique: new Set(ids).size, roleRequests: requests });
  await page.locator('[data-rail="agent"]').click();
  await page.locator('[data-rail="concept"]').click();
  await page.waitForFunction(() => location.hash === '#view');
  await page.locator('[data-rail="agent"]').click();
  await page.waitForFunction(() => document.querySelectorAll('.lnb-agent-role').length === 108);
  await page.waitForFunction(() => decodeURIComponent(location.hash).includes('ai/english/voice/girlfriend'));
  await page.locator('[data-nodeid="concept:agent-english"]').click();
  await page.waitForFunction(() => location.hash.includes('agent-english'));
  results.push({ case: 'real topic selection and exact role navigation', passed: true,
                 hash: await page.evaluate(() => location.hash) });
  await page.screenshot({ path: output.replace(/\.json$/, '.png'), fullPage: true });
  const { executed, found, node } = await page.evaluate(async () => {
    const { api, cfg } = await import('/src/api.ts');
    const res = await fetch('/roles?owner=' + encodeURIComponent(cfg.owner) + '&role=agent-english',
                            { headers: { Authorization: 'Bearer ' + cfg.token } });
    return res.json();
  });
  assert(executed && found && node.domain === 'ai/english/voice/girlfriend');
  results.push({ case: 'real exact role HTTP lookup', passed: true });
  // 여기부터 빈 목록과 오류 상태만 /roles 응답을 바꾼다. 앱 코드는 그대로 실행한다.
  await page.route('**/roles?*', route => route.fulfill({ json: {
    executed: true, roles: [], relations: [], next_offset: null, count: 0 } }));
  await page.evaluate(async () => { const { refreshLnb } = await import('/src/lnb.ts'); await refreshLnb(); });
  assert.equal(await page.locator('.lnb-agent-role').count(), 0);
  assert.equal(await page.locator('#lnb-retry').count(), 0);
  results.push({ case: 'mock /roles empty state', passed: true });
  await page.unroute('**/roles?*');
  await page.route('**/roles?*', route => route.fulfill({ json: { executed: false, error: 'synthetic outage' } }));
  await page.evaluate(async () => { const { refreshLnb } = await import('/src/lnb.ts'); await refreshLnb(); });
  assert.equal(await page.locator('#lnb-retry').count(), 1);
  results.push({ case: 'mock /roles error state', passed: true });
  await page.unroute('**/roles?*');
  await page.locator('#lnb-retry').click();
  await page.waitForFunction(() => document.querySelectorAll('.lnb-agent-role').length === 108);
  results.push({ case: 'real retry after intercepted failure', passed: true });
} finally {
  await fs.writeFile(output, JSON.stringify({ results, completed: results.length === 6 }, null, 2));
  await browser.close();
}
console.log(JSON.stringify({ results, completed: true }));
