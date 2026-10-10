// 실제 앱을 격리 브라우저에서 실행한다. 모든 API는 합성 응답이며 외부 요청은 차단한다.
// VINC_TEST_MODULES=<package.json exposing playwright> npm run test:timeline-panel
// Optional: VINC_TIMELINE_LANG=en|ko VINC_TIMELINE_THEME=light|dark
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { createServer as portServer } from 'node:net';

const root = resolve(process.env.VINC_APP_ROOT || resolve(dirname(fileURLToPath(import.meta.url)), '..'));
const baseline = process.env.VINC_TIMELINE_BASELINE === '1';
const language = process.env.VINC_TIMELINE_LANG || 'en';
const theme = process.env.VINC_TIMELINE_THEME || 'light';
assert.ok(['en', 'ko'].includes(language));
assert.ok(['light', 'dark'].includes(theme));
const ui = language === 'ko' ? {
  close: '닫기', full: '크게 보기', side: '작게 보기', resize: '패널 폭 조절',
  loading: '이 기간의 문서를 불러오는 중…',
  activity: '한국어 활동 목록과 문서 패널 전환을 확인하는 합성 기록',
  doc: '합성 문서: Timeline 패널 전환 검증',
} : { close: 'Close', full: 'Full width', side: 'Side panel', resize: 'Resize panel',
  loading: 'Loading this period…', activity: 'Synthetic activity', doc: 'Synthetic document' };
const output = resolve(process.env.VINC_TIMELINE_EVIDENCE || 'timeline-evidence');
const require = createRequire(resolve(process.env.VINC_TEST_MODULES || resolve(root, 'package.json')));
const { chromium } = require('playwright');
process.chdir(root);
process.env.VINC_SIDECAR = 'http://127.0.0.1:1';
const probe = portServer();
await new Promise(r => probe.listen(0, '127.0.0.1', r));
const port = probe.address().port;
await new Promise(r => probe.close(r));
const server = await createServer({ root, configFile: resolve(root, 'vite.config.ts'),
  server: { host: '127.0.0.1', port, strictPort: true } });
await server.listen();
const origin = `http://127.0.0.1:${port}`;
const results = [], requests = [], errors = [], consoleErrors = [];
const days = ['2026-10-01', '2026-10-02', '2026-10-03', '2026-10-04', '2026-10-05'];
const nodes = days.flatMap((day, d) => Array.from({ length: d === 0 ? 36 : 2 }, (_, i) => ({
  id: `concept:synthetic-${d}-${i}`, label: `${ui.activity} ${d}-${i}`, kind: 'concept',
  domain: 'synthetic/timeline', degree: 0, ts: `${day}T12:00:00Z`, content_ts: day,
})));
const role = { id: 'concept:agent-synthetic', title: 'Agent role: Synthetic reviewer',
  domain: 'synthetic/independent-role', props: { job_title: 'Synthetic reviewer', team: 'Quality and Security' } };
let periodMode = 'normal', pending = [], holdReader = false, pendingReader = null;
const periodResponse = day => ({ executed: true, total: day === days[2] ? 0 : day === days[0] ? 36 : 2,
  items: day === days[2] ? [] : Array.from({ length: day === days[0] ? 36 : 2 }, (_, i) => ({
    id: `concept:synthetic-${days.indexOf(day)}-${i}`, kind: 'concept',
    title: `${day} ${ui.activity} ${i}`, ts: `${day}T12:00:00Z`, domain: 'synthetic/timeline',
  })) });
let browser, page;
try {
  await mkdir(output, { recursive: true });
  browser = await chromium.launch({ channel: 'msedge', headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC',
    locale: language === 'ko' ? 'ko-KR' : 'en-US', colorScheme: theme });
  await context.addInitScript(({ language, theme }) => {
    localStorage.setItem('vinc.mode', 'local');
    localStorage.setItem('vinc.lang', language);
    localStorage.setItem('vinc.owner', 'synthetic-timeline');
    localStorage.setItem('vinc.theme', theme);
    if (!localStorage.getItem('vinc.view.prefs')) localStorage.setItem('vinc.view.prefs',
      JSON.stringify({ pv: 2, view: 'timeline', timeBasis: 'recorded', period: 'all', railW: 20 }));
  }, { language, theme });
  await context.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url());
    if (url.origin !== origin) { errors.push(`Blocked external request ${url.origin}`); return route.abort(); }
    if (!['fetch', 'xhr'].includes(req.resourceType())) return route.continue();
    requests.push({ path: url.pathname, query: url.search, method: req.method() });
    assert.equal(req.method(), 'GET', 'UI validation must not write graph data');
    const common = { executed: true, nodes: [], edges: [], rows: [], domains: [], docs: [],
      items: [], unfiled: [], counts: { domains: 0, docs: 1, unfiled: 1, kinds: { concept: nodes.length } },
      marker: 'synthetic', arms: 'fts' };
    if (url.pathname === '/docs-in-range') {
      const day = url.searchParams.get('start').slice(0, 10);
      if (periodMode === 'hold') { pending.push({ route, day }); return; }
      if (day === days[3]) return route.fulfill({ json: { executed: false, error: 'synthetic outage' } });
      if (day === days[4]) return route.fulfill({ status: 500, json: { error: 'synthetic transport failure' } });
      return route.fulfill({ json: periodResponse(day) });
    }
    let json = common;
    if (url.pathname === '/graph') json = { ...common, nodes };
    if (url.pathname === '/roles') json = { executed: true, roles: [role], relations: [], next_offset: null, count: 1 };
    if (url.pathname === '/nav-tree') json = { ...common, unfiled: [
      { doc_id: 'synthetic-document', title: ui.doc, node_id: nodes[0].id, depth: 0 } ] };
    if (url.pathname === '/doc') json = { executed: true, title: ui.doc,
      text: `# ${ui.doc}\n\n${ui.activity}.`, verified: true, chunk_count: 1 };
    if (url.pathname === '/doc' && holdReader) { pendingReader = { route, json }; return; }
    if (url.pathname === '/node-context') json = { executed: true, node_kind: 'concept', rev: 1,
      title: 'Synthetic activity', props: {}, excerpts: [] };
    if (url.pathname === '/search') json = { ...common,
      node_hits: [{ id: nodes[0].id, title: 'Synthetic search result', matched_in: 'title' }] };
    await route.fulfill({ json });
  });
  page = await context.newPage();
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text()); });
  page.setDefaultTimeout(60000);
  await page.goto(`${origin}/#view`, { waitUntil: 'domcontentloaded' });
  await page.locator('.pl-band-hit').first().waitFor();
  assert.equal(await page.locator('html').getAttribute('lang'), language);
  assert.equal(await page.locator('html').getAttribute('data-theme'), theme);
  await page.locator('.lnb-group[data-group=""]').click();
  const band = day => page.locator(`.pl-band-hit[data-month='${day}']`);
  const pane = page.locator('#docpane');
  const detail = page.locator('#view-detail');
  const grip = page.locator('#rail-grip');
  const close = page.locator('#rail-close');
  const size = page.locator('#rail-size');
  const header = detail.locator(':scope > .rail-head');
  const settle = () => page.waitForTimeout(250);
  const width = async () => (await pane.boundingBox()).width;
  async function controls() {
    assert.equal(await close.isVisible(), true);
    assert.equal(await size.isVisible(), true);
    assert.equal(await close.getAttribute('aria-label'), ui.close);
    assert.equal(await header.getByRole('button', { name: ui.close, exact: true }).count(), 1);
    const full = await size.getAttribute('aria-pressed') === 'true';
    assert.equal((await size.textContent()).trim(), full ? ui.side : ui.full);
    assert.equal(await header.getByRole('button', { name: full ? ui.side : ui.full, exact: true }).count(), 1);
    if (await grip.isVisible()) assert.equal(await grip.getAttribute('aria-label'), ui.resize);
    assert.equal(await page.locator('#rail-close').count(), 1);
    assert.equal(await page.locator('#rail-size').count(), 1);
  }
  async function open(day = days[0], keyboard = false) {
    if (keyboard) { await band(day).focus(); await page.keyboard.press('Enter'); }
    else await band(day).click({ position: { x: 4, y: 160 } });
    await controls();
  }
  async function capture(label) {
    await controls();
    const metrics = await page.evaluate(() => {
      const rect = id => { const r = document.getElementById(id).getBoundingClientRect();
        return { x: r.x, y: r.y, width: r.width, right: r.right, bottom: r.bottom }; };
      const labels = ['.rail-head > .hint', '#rail-size', '#rail-close', '#period-body h2']
        .flatMap(selector => {
          const el = document.querySelector(selector);
          if (!el) return [];
          const box = el.getBoundingClientRect(), range = document.createRange();
          range.selectNodeContents(el);
          const lines = [...range.getClientRects()].filter(r => r.width > 0 && r.height > 0);
          return [{ selector, text: el.textContent.trim(), x: box.x, right: box.right,
            clipped: lines.some(r => r.left < box.left - 1 || r.right > box.right + 1
              || r.top < box.top - 1 || r.bottom > box.bottom + 1) }];
        });
      const buttons = ['rail-size', 'rail-close'].map(id => {
        const el = document.getElementById(id), box = el.getBoundingClientRect();
        const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
        return { id, ...rect(id), hit: hit?.closest('button')?.id };
      });
      const heading = document.querySelector('#period-body h2');
      const count = heading?.textContent.match(/\d+개$/)?.[0];
      let countLines = 0;
      if (count) {
        const text = heading.firstChild, range = document.createRange();
        range.setStart(text, text.textContent.length - count.length);
        range.setEnd(text, text.textContent.length);
        countLines = [...range.getClientRects()].filter(r => r.width > 0 && r.height > 0).length;
      }
      return { viewport: innerWidth, rootSize: parseFloat(getComputedStyle(document.documentElement).fontSize),
        pane: rect('docpane'), slot: rect('docpane-slot'),
        bar: rect('topbar'), find: rect('tb-find'), card: rect('tb-viewctl'),
        detailScroll: document.getElementById('view-detail').scrollWidth,
        detailClient: document.getElementById('view-detail').clientWidth,
        size: document.getElementById('shell').dataset.docSize,
        language: document.documentElement.lang, theme: document.documentElement.dataset.theme,
        labels, buttons, countLines, colors: { header: getComputedStyle(document.querySelector('.rail-head')).backgroundColor,
          button: getComputedStyle(document.getElementById('rail-size')).color },
        focus: document.activeElement?.id,
        grip: { min: document.getElementById('rail-grip').getAttribute('aria-valuemin'),
          max: document.getElementById('rail-grip').getAttribute('aria-valuemax'),
          now: document.getElementById('rail-grip').getAttribute('aria-valuenow') } };
    });
    assert.ok(metrics.pane.x >= metrics.slot.x - .5);
    assert.ok(metrics.pane.right <= metrics.slot.right + .5);
    assert.ok(metrics.detailScroll <= metrics.detailClient + 1, `${label}: panel content overflows horizontally`);
    assert.equal(metrics.language, language);
    assert.equal(metrics.theme, theme);
    for (const text of metrics.labels) assert.equal(text.clipped, false, `${label}: clipped ${text.selector}`);
    if (language === 'ko' && metrics.labels.some(t => t.selector === '#period-body h2'))
      assert.equal(metrics.countLines, 1, `${label}: count and Korean unit must stay on the same line`);
    const [sizeButton, closeButton] = metrics.buttons;
    assert.ok(sizeButton.right <= closeButton.x + 1, `${label}: header buttons overlap`);
    assert.ok(metrics.labels[0].right <= sizeButton.x + 1, `${label}: caption overlaps actions`);
    for (const button of metrics.buttons) {
      assert.equal(button.hit, button.id, `${label}: button is covered`);
      assert.ok(button.x >= metrics.pane.x && button.right <= metrics.pane.right,
        `${label}: button outside panel`);
    }
    if (await grip.isVisible()) {
      const max = Math.min(120, (metrics.slot.width - 2 * (metrics.slot.right - metrics.pane.right)) / metrics.rootSize);
      assert.ok(Math.abs(Number(metrics.grip.max) - max) < .1, `${label}: resize accessibility range must follow available width`);
      assert.ok(Math.abs(Number(metrics.grip.now) - metrics.pane.width / metrics.rootSize) < .1);
    }
    results.push({ case: label, passed: true, ...metrics });
    await page.screenshot({ path: resolve(output, `${label}.png`) });
  }
  async function release(index = 0) {
    const p = pending.splice(index, 1)[0];
    await p.route.fulfill({ json: periodResponse(p.day) });
    await settle();
  }
  const pendingCount = n => page.waitForFunction(() => true).then(async () => {
    const until = Date.now() + 5000;
    while (pending.length < n && Date.now() < until) await page.waitForTimeout(20);
    assert.equal(pending.length, n);
  });
  async function openSyntheticDoc() {
    if (await page.locator('#shell').getAttribute('data-lnb') !== 'open')
      await page.locator('#tb-shell-toggle').click();
    const group = page.locator('.lnb-group[data-group=""]');
    if (await group.getAttribute('aria-expanded') !== 'true') await group.click();
    await page.locator('.lnb-doc[data-doc="synthetic-document"]').click();
  }

  if (baseline) {
    await band(days[0]).click({ position: { x: 4, y: 160 } });
    await page.locator('.period-i').first().waitFor();
    assert.equal(await close.count(), 0);
    assert.equal(await size.count(), 0);
    results.push({ case: 'baseline-controls-missing', passed: true,
      closeCount: await close.count(), sizeCount: await size.count(), gripVisible: await grip.isVisible() });
    await page.screenshot({ path: resolve(output, 'baseline-opened.png') });
  } else {
  periodMode = 'hold';
  await open(days[0], true);
  await pendingCount(1);
  assert.equal(await close.evaluate(e => e === document.activeElement), true);
  await capture('loading-keyboard');
  await release();
  assert.equal(await close.evaluate(e => e === document.activeElement), true, 'loading response must preserve keyboard focus');
  await page.locator('.period-i').first().waitFor();
  await capture('opened');
  await size.focus(); await page.keyboard.press('Tab');
  assert.equal(await close.evaluate(e => e === document.activeElement), true);
  assert.ok(await close.evaluate(e => e.matches(':focus-visible') && getComputedStyle(e).outlineStyle !== 'none'));
  await page.keyboard.press('Shift+Tab');
  assert.equal(await size.evaluate(e => e === document.activeElement), true);
  await capture('keyboard-header-controls');
  await close.focus(); await page.keyboard.press('Enter');
  assert.equal(await pane.isVisible(), false);
  assert.equal(await band(days[0]).evaluate(e => e === document.activeElement), true);
  periodMode = 'normal';
  await band(days[0]).focus(); await page.keyboard.press('Space');
  await page.locator('.period-i').first().waitFor();
  await controls();
  assert.equal(await page.locator('.period-i').count(), 36);
  await open(days[1]);
  await page.waitForFunction(day => document.querySelector('#period-body h2')?.textContent.includes(day), days[1]);
  assert.equal(await page.locator('.period-i').count(), 2);
  await capture('date-switch');

  await size.click();
  assert.equal(await size.getAttribute('aria-pressed'), 'true');
  assert.equal(await grip.isVisible(), false);
  await capture('full-width');
  await size.focus(); await page.keyboard.press('Space');
  assert.equal(await size.getAttribute('aria-pressed'), 'false');
  assert.equal(await grip.isVisible(), true);
  await grip.focus();
  const initial = await width();
  await page.keyboard.press('ArrowLeft');
  assert.ok(Math.abs(await width() - initial - 16) < 1);
  await page.keyboard.press('ArrowRight');
  assert.ok(Math.abs(await width() - initial) < 1);
  await page.keyboard.press('Home');
  assert.ok(Math.abs(await width() - 256) < 1);
  await page.keyboard.press('ArrowRight');
  assert.ok(Math.abs(await width() - 256) < 1);
  await capture('keyboard-minimum');
  await page.keyboard.press('End');
  await settle();
  assert.ok(Math.abs(Number(await grip.getAttribute('aria-valuenow')) - Number(await grip.getAttribute('aria-valuemax'))) < .1);
  await capture('keyboard-maximum');
  await page.keyboard.press('Home');
  let g = await grip.boundingBox();
  await page.mouse.move(g.x + 4, g.y + 120);
  await page.mouse.down(); await page.mouse.move(0, g.y + 120, { steps: 10 }); await page.mouse.up();
  await capture('drag-maximum');
  g = await grip.boundingBox();
  await page.mouse.move(g.x + 4, g.y + 120);
  await page.mouse.down(); await page.mouse.move(1438, g.y + 120, { steps: 10 }); await page.mouse.up();
  assert.ok(Math.abs(await width() - 256) < 1);
  await capture('drag-minimum');
  await grip.focus(); await page.keyboard.press('Shift+ArrowLeft');
  await page.keyboard.press('ArrowLeft');
  const savedWidth = await width();
  await close.click(); await open(days[0]);
  assert.ok(Math.abs(await width() - savedWidth) < 1, 'width persists across close/reopen');
  await page.reload(); await band(days[0]).waitFor(); await open(days[0]);
  assert.ok(Math.abs(await width() - savedWidth) < 1, 'width persists across reload');
  await detail.evaluate(e => { e.scrollTop = e.scrollHeight; });
  const c = await close.boundingBox(), p = await pane.boundingBox();
  assert.ok(c.y >= p.y && c.y < p.y + 90, 'sticky close remains visible after scrolling');
  await capture('scrolled-controls');
  await close.click();

  periodMode = 'hold';
  await open(days[0]); await pendingCount(1);
  await open(days[1]); await pendingCount(2);
  await release(1); await release();
  assert.ok((await page.locator('#period-body h2').textContent()).includes(days[1]), 'older date response must not replace the selected date');
  await capture('reversed-responses');
  await close.click();
  await open(days[0]); await pendingCount(1);
  await close.click(); await open(days[1]); await pendingCount(2);
  await release(1); await release();
  assert.ok((await page.locator('#period-body h2').textContent()).includes(days[1]), 'closed response must not replace a reopened panel');
  await close.click();
  await open(days[0]); await pendingCount(1);
  await close.click(); await release();
  assert.equal(await pane.isVisible(), false, 'late response must not reopen a closed panel');
  results.push({ case: 'close-reopen-races', passed: true });
  periodMode = 'normal';
  for (const day of [days[2], days[3], days[4]]) {
    await open(day);
    await page.waitForFunction(loading => document.querySelector('#period-body [role="status"]')?.textContent !== loading, ui.loading);
    await capture(day === days[2] ? 'empty' : day === days[3] ? 'failure' : 'transport-failure');
    await close.click();
  }

  await open(days[0]); await page.locator('.period-i').first().waitFor();
  await page.locator('.period-i').first().focus(); await page.keyboard.press('Enter');
  await page.locator('#node-source').waitFor();
  assert.equal(await close.evaluate(e => e === document.activeElement), true, 'keyboard activity navigation must retain a focus target');
  await controls();
  await capture('node-panel');
  await page.keyboard.press('Escape');
  if (await pane.isVisible()) await page.keyboard.press('Escape');
  assert.equal(await pane.isVisible(), false);
  periodMode = 'hold';
  await open(days[0]); await pendingCount(1);
  await openSyntheticDoc();
  await page.locator('#rdr-host .cm-editor').waitFor();
  await release();
  await controls();
  assert.equal(await page.locator('#period-body').count(), 0, 'date response must not replace reader');
  await capture('document-panel');
  await size.click();
  await open(days[1]); await pendingCount(1); await release();
  await capture('document-to-timeline');
  await close.click();
  periodMode = 'normal';
  holdReader = true;
  await openSyntheticDoc();
  const readerUntil = Date.now() + 5000;
  while (!pendingReader && Date.now() < readerUntil) await page.waitForTimeout(20);
  assert.ok(pendingReader, 'reader request must be held');
  // LNB 문서는 전폭으로 열리므로 현재 날짜 줄을 키보드로 활성화한다.
  await open(days[1], true);
  await page.locator('.period-i').first().waitFor();
  await pendingReader.route.fulfill({ json: pendingReader.json });
  pendingReader = null; holdReader = false;
  await settle();
  assert.ok((await page.locator('#period-body h2').textContent()).includes(days[1]), 'late reader response must not replace timeline');
  await capture('reader-to-timeline-race');
  await close.click();

  await page.locator('[data-rail="agent"]').click();
  await page.locator('.lnb-agent-role').waitFor();
  assert.equal(await page.locator('.lnb-agent-role').count(), 1);
  assert.ok((await page.locator('.lnb-agent-role').textContent()).includes('Synthetic reviewer'));
  await page.locator('.lnb-agent-role').click();
  await page.locator('#node-source').waitFor(); await controls();
  await capture('agents-panel');
  await close.click();
  await page.locator('[data-rail="concept"]').click();
  await band(days[0]).waitFor();

  await open(days[0]);
  for (const viewport of [920, 900, 700, 430, 390, 320]) {
    await page.setViewportSize({ width: viewport, height: 900 }); await settle();
    await capture(`narrow-${viewport}`);
    await grip.focus(); await page.keyboard.press('End');
    await capture(`narrow-max-${viewport}`);
    await size.focus(); await page.keyboard.press('Enter');
    assert.equal(await size.getAttribute('aria-pressed'), 'true');
    await capture(`narrow-full-${viewport}`);
    await page.keyboard.press('Space');
    assert.equal(await size.getAttribute('aria-pressed'), 'false');
    await close.focus(); await page.keyboard.press('Escape');
    assert.equal(await pane.isVisible(), false);
    assert.equal(await band(days[0]).evaluate(e => e === document.activeElement), true,
      'close after viewport redraw restores the current date band');
    await band(days[0]).focus(); await page.keyboard.press('Enter'); await controls();
  }
  for (const viewport of [390, 320]) {
    await page.setViewportSize({ width: viewport, height: 900 }); await settle();
    await openSyntheticDoc(); await page.locator('#rdr-host .cm-editor').waitFor(); await settle();
    await capture(`narrow-document-full-${viewport}`);
    await size.focus(); await page.keyboard.press('Space');
    await grip.focus(); await page.keyboard.press('Home');
    await capture(`narrow-document-side-${viewport}`);
    await open(days[0], true); await page.locator('.period-i').first().waitFor();
    await capture(`narrow-document-to-timeline-${viewport}`);
    await close.focus(); await page.keyboard.press('Space');
    assert.equal(await pane.isVisible(), false);
    await open(days[0], true); await page.locator('.period-i').first().waitFor();
  }
  await page.setViewportSize({ width: 1440, height: 900 }); await settle();
  await close.click();
  await page.locator('#tb-q').fill('synthetic');
  await page.locator('#tb-results').getByText('Synthetic search result', { exact: true }).waitFor();
  const searchBefore = await page.locator('#tb-find').boundingBox();
  await page.locator('#view-lens').click(); await settle();
  const searchOpen = await page.locator('#tb-find').boundingBox(), card = await page.locator('#tb-viewctl').boundingBox();
  assert.ok(searchOpen.width < searchBefore.width);
  assert.ok(card.x >= searchOpen.x + searchOpen.width + 7);
  await page.keyboard.press('Escape');
  await page.waitForFunction(expected => Math.abs(document.getElementById('tb-find').getBoundingClientRect().width - expected) < 1, searchBefore.width);
  assert.ok(Math.abs((await page.locator('#tb-find').boundingBox()).width - searchBefore.width) < 1);
  assert.equal(await page.locator('#tb-q').inputValue(), 'synthetic');
  results.push({ case: 'viewpoint-search-layout', passed: true });
  }
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ completed: true, language, theme, cases: results.length, output }));
} catch (e) {
  errors.push(e.stack || String(e));
  if (page) await page.screenshot({ path: resolve(output, 'assertion-failure.png') }).catch(() => {});
  throw e;
} finally {
  for (const p of pending) await p.route.abort().catch(() => {});
  if (pendingReader) await pendingReader.route.abort().catch(() => {});
  await writeFile(resolve(output, 'results.json'), JSON.stringify({ completed: errors.length === 0,
    environment: { language, theme, timezone: 'UTC', browser: 'isolated Edge Chromium', synthetic: true },
    results, requests, errors, consoleErrors }, null, 2));
  await browser?.close();
  await server.close();
}
