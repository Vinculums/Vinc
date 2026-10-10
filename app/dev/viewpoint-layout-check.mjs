/** Actual app DOM and CSS with synthetic, intercepted API responses only.
 * VINC_TEST_MODULES=<package.json exposing playwright> node dev/viewpoint-layout-check.mjs
 * Uses an isolated browser profile and an OS-assigned port; requires installed Edge.
 */
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { createServer as createPortServer } from "node:net";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const output = resolve(process.env.VINC_LAYOUT_EVIDENCE || "viewpoint-evidence");
const require = createRequire(resolve(process.env.VINC_TEST_MODULES || resolve(root, "package.json")));
const { chromium } = require("playwright");
process.chdir(root);
process.env.VINC_SIDECAR = "http://127.0.0.1:1";
const portProbe = createPortServer();
await new Promise(r => portProbe.listen(0, "127.0.0.1", r));
const port = portProbe.address().port;
await new Promise(r => portProbe.close(r));
const server = await createServer({ root, configFile: resolve(root, "vite.config.ts"),
  server: { host: "127.0.0.1", port, strictPort: true } });
await server.listen();
const origin = `http://127.0.0.1:${port}`;
const results = [], requests = [], errors = [];
let browser;
try {
  await mkdir(output, { recursive: true });
  browser = await chromium.launch({ channel: "msedge", headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await context.addInitScript(() => {
    localStorage.setItem("vinc.mode", "local");
    localStorage.setItem("vinc.lang", "en");
    localStorage.setItem("vinc.owner", "synthetic-layout");
    localStorage.setItem("vinc.theme", "light");
  });
  await context.route("**/*", async route => {
    const req = route.request(), url = new URL(req.url());
    if (url.origin !== origin) {
      errors.push(`External request blocked: ${url.origin}`);
      return route.abort();
    }
    if (!["fetch", "xhr"].includes(req.resourceType())) return route.continue();
    requests.push({ path: url.pathname, method: req.method() });
    await route.fulfill({ json: {
      executed: true, nodes: [], edges: [], rows: [], domains: [], docs: [], items: [], unfiled: [],
      counts: { domains: 0, docs: 0, unfiled: 0, kinds: {} }, marker: "synthetic", arms: "fts",
      node_hits: [{ id: "concept:synthetic-layout", title: "Synthetic search result", matched_in: "title" }],
    } });
  });
  const page = await context.newPage();
  page.on("pageerror", e => errors.push(e.message));
  await page.goto(`${origin}/#view`);
  await page.locator("#view-lens").waitFor();
  await page.waitForTimeout(500);
  await page.evaluate(() => {
    window.layoutFrames = [];
    window.originalSearchInput = document.getElementById("tb-q");
    const sample = () => {
      const find = document.getElementById("tb-find").getBoundingClientRect();
      const card = document.getElementById("tb-viewctl").getBoundingClientRect();
      const input = document.getElementById("tb-q").getBoundingClientRect();
      const lens = document.getElementById("view-lens").getBoundingClientRect();
      const overlap = Math.max(0, Math.min(find.right, card.right) - Math.max(find.left, card.left))
        * Math.max(0, Math.min(find.bottom, card.bottom) - Math.max(find.top, card.top));
      window.layoutFrames.push({ overlap, search: find.width, card: card.width, input: input.width,
        gap: card.left - find.right, sameRow: Math.abs(find.top - card.top) < 1,
        lensInside: lens.right <= card.right - 1,
        lensRight: lens.right, cardRight: card.right, lensWidth: lens.width, viewport: innerWidth,
        open: document.getElementById("tb-viewctl").dataset.open === "true" });
      requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
  });
  const measure = label => page.evaluate(label => {
    const rect = id => {
      const el = document.getElementById(id), r = el.getBoundingClientRect();
      return { x: r.x, y: r.y, width: r.width, height: r.height, right: r.right, bottom: r.bottom };
    };
    const find = rect("tb-find"), card = rect("tb-viewctl");
    return { label, viewport: innerWidth, bar: rect("topbar"), find, search: rect("tb-search"),
      input: rect("tb-q"), card, lens: rect("view-lens"), ctlHeight: parseFloat(getComputedStyle(document.getElementById("view-lens")).height),
      sameInput: document.getElementById("tb-q") === window.originalSearchInput,
      sameRow: Math.abs(find.y - card.y) < 1,
      gap: card.x - find.right, value: document.getElementById("tb-q").value,
      focus: document.activeElement?.id, panelHidden: document.getElementById("view-lens-panel").hidden,
      open: document.getElementById("tb-viewctl").dataset.open === "true",
      scrollWidth: document.getElementById("tb-viewpoints").scrollWidth,
      clientWidth: document.getElementById("tb-viewpoints").clientWidth };
  }, label);
  const settle = () => page.waitForTimeout(450);
  async function capture(label, screenshot = false) {
    const m = await measure(label);
    if (m.sameRow) assert.ok(m.gap >= 7.5, `${label}: card overlap or lost gap`);
    assert.ok(m.card.right <= m.bar.right + 1, `${label}: card exceeds bar`);
    assert.ok(m.input.width >= 90, `${label}: search input unusable`);
    assert.ok(m.lens.right <= m.card.right - 12.5, `${label}: lens exceeds card padding`);
    if (m.open && m.viewport > 900) assert.ok(m.lens.width >= m.ctlHeight - .5, `${label}: lens button shrank`);
    assert.equal(m.sameInput, true, `${label}: search input must not be replaced`);
    results.push(m);
    if (screenshot) await page.screenshot({ path: resolve(output, `${label}.png`) });
    return m;
  }
  const closed = await capture("after-closed", true);
  await page.locator("#tb-q").fill("synthetic");
  await page.locator("#tb-results").getByText("Synthetic search result", { exact: true }).waitFor();
  await page.locator("#view-lens").click();
  await settle();
  const open = await capture("after-open", true);
  assert.ok(Math.abs((closed.find.width - open.find.width) - (open.card.width - closed.card.width)) < 1);
  assert.equal(open.value, "synthetic");
  await page.locator("#tb-viewpoints [data-view='2d']").click();
  await settle();
  assert.equal(await page.locator("#tb-viewpoints [data-view='2d']").getAttribute("aria-pressed"), "true");
  assert.equal(await page.locator("#view-lens").getAttribute("aria-expanded"), "true");
  for (const viewpoint of ["timeline", "bundle", "2d"]) {
    await page.locator(`#tb-viewpoints [data-view='${viewpoint}']`).click(); await settle();
    assert.equal(await page.locator(`#tb-viewpoints [data-view='${viewpoint}']`).getAttribute("aria-pressed"), "true");
    assert.equal(await page.locator("#view-lens").getAttribute("aria-expanded"), "true");
  }
  await page.locator("#tb-viewpoints [data-view='3d']").focus();
  await page.keyboard.press("Enter");
  await settle();
  assert.equal(await page.locator("#tb-viewpoints [data-view='3d']").getAttribute("aria-pressed"), "true");
  await page.keyboard.press("Escape");
  await settle();
  assert.equal(await page.locator("#view-lens").getAttribute("aria-expanded"), "false");
  const restored = await capture("after-restored", true);
  assert.ok(Math.abs(restored.find.width - closed.find.width) < 1, "closed search width must restore");
  assert.equal(restored.value, "synthetic");
  assert.equal(restored.focus, "view-lens");
  await page.keyboard.press("Control+k");
  assert.equal(await page.locator("#tb-q").evaluate(e => document.activeElement === e), true);
  await page.locator("#tb-q").evaluate(e => e.setSelectionRange(2, 6));
  await page.setViewportSize({ width: 1100, height: 900 }); await settle();
  assert.deepEqual(await page.locator("#tb-q").evaluate(e => [e.selectionStart, e.selectionEnd, document.activeElement === e]), [2, 6, true]);
  await page.setViewportSize({ width: 1440, height: 900 }); await settle();
  for (let i = 0; i < 5; i++) {
    await page.locator("#view-lens").click(); await settle();
    await page.locator("#tb-q").click(); await settle();
    assert.equal(await page.locator("#tb-q").inputValue(), "synthetic");
    assert.equal(await page.locator("#tb-q").evaluate(e => document.activeElement === e), true);
    assert.equal((await measure("cycle")).open, false);
  }
  await page.locator("#view-lens").focus();
  await page.keyboard.press("Space"); await settle();
  assert.equal((await measure("keyboard-open")).open, true);
  await page.locator("[data-lensclose]").click(); await settle();
  assert.equal((await measure("button-close")).open, false);

  // Reopen during the panel's exit, then during the card's own closing animation.
  await page.locator("#view-lens").click(); await settle();
  await page.evaluate(() => document.querySelector("#view-lens").click());
  await page.waitForTimeout(35);
  await page.evaluate(() => document.querySelector("#view-lens").click());
  await settle();
  assert.equal((await capture("rapid-reopen")).open, true);
  assert.equal(await page.locator("#view-lens").getAttribute("aria-expanded"), "true");
  await page.keyboard.press("Escape"); await settle();
  await page.locator("#view-lens").click(); await settle();
  await page.evaluate(() => document.querySelector("#view-lens").click());
  await page.waitForFunction(() => {
    const c = document.getElementById("tb-viewctl");
    return document.getElementById("view-lens-panel").hidden && c.dataset.open === "true" && c.getAnimations().length > 0;
  });
  await page.evaluate(() => document.querySelector("#view-lens").click());
  await settle();
  assert.equal((await capture("card-animation-reopen")).open, true);
  assert.equal(await page.locator("#view-lens").getAttribute("aria-expanded"), "true");
  await page.keyboard.press("Escape"); await settle();

  for (const width of [1100, 1000, 920, 901, 900, 812, 700, 600, 430, 390, 320]) {
    await page.setViewportSize({ width, height: 900 }); await settle();
    const c = await capture(`closed-${width}`);
    await page.locator("#view-lens").click(); await settle();
    const o = await capture(`open-${width}`, [1000, 920, 390].includes(width));
    if (width > 900) assert.ok(o.find.width < c.find.width, `width ${width}: opening must allocate space`);
    else assert.ok(Math.abs(o.find.width - c.find.width) < 1, "sheet-head viewpoint must not consume bar width");
    await page.keyboard.press("Escape"); await settle();
    assert.ok(Math.abs((await measure("restored")).find.width - c.find.width) < 1);
  }
  await page.setViewportSize({ width: 920, height: 900 }); await settle();
  await page.locator("#view-lens").click(); await settle();
  await page.evaluate(() => {
    document.querySelector("#tb-viewpoints [data-view='bundle']").textContent = "Synthetic viewpoint with a deliberately very long label";
  });
  await page.locator("#tb-viewpoints [data-view='bundle']").focus();
  await settle();
  const long = await capture("long-label", true);
  assert.ok(long.scrollWidth > long.clientWidth, "long label must scroll inside its own region");
  const focusVisible = await page.locator("#tb-viewpoints [data-view='bundle']").evaluate(e => {
    const a=e.getBoundingClientRect(),b=e.closest("#tb-viewpoints").getBoundingClientRect();
    return Math.min(a.right,b.right)-Math.max(a.left,b.left)>20;
  });
  assert.ok(focusVisible, "keyboard focus must reveal an overflowed viewpoint");
  await page.keyboard.press("Escape"); await settle();
  await page.evaluate(() => {
    document.querySelector("#view-lens .lens-vp").textContent = "Synthetic closed viewpoint with a deliberately very long label";
  });
  await capture("long-closed-label");
  await page.setViewportSize({ width: 1440, height: 900 }); await settle();
  await page.locator("#view-lens").click({ force: true });
  await page.setViewportSize({ width: 901, height: 700 });
  await page.setViewportSize({ width: 390, height: 700 }); await settle();
  await capture("resize-during-open");
  await page.keyboard.press("Escape");
  await page.setViewportSize({ width: 1100, height: 900 }); await settle();
  await capture("resize-during-close");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.locator("#view-lens").click(); await settle();
  await capture("reduced-motion-open");
  await page.keyboard.press("Escape"); await settle();
  await capture("reduced-motion-closed");
  const frames = await page.evaluate(() => window.layoutFrames);
  await writeFile(resolve(output, "frame-boundaries.json"), JSON.stringify({
    results, violations: frames.filter(f => f.overlap > .1 || !f.lensInside || (f.sameRow && f.gap < 7.5)),
  }, null, 2));
  assert.ok(frames.length > 100, "transition checks need rendered frames");
  assert.equal(frames.filter(f => f.overlap > .1).length, 0, "cards must not overlap during animations");
  assert.equal(frames.filter(f => f.sameRow && f.gap < 7.5).length, 0);
  assert.equal(frames.filter(f => !f.lensInside).length, 0, "lens button must remain inside its card during motion");
  assert.deepEqual(errors, []);
  const report = { origin, results, frameCount: frames.length, maxOverlapArea: Math.max(...frames.map(f => f.overlap)),
    minSameRowGap: Math.min(...frames.filter(f=>f.sameRow).map(f=>f.gap)), errors, requests };
  await writeFile(resolve(output, "measurements.json"), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ passed: true, states: results.length, frames: report.frameCount,
    maxOverlapArea: report.maxOverlapArea, minSameRowGap: report.minSameRowGap, output }));
} finally {
  await browser?.close();
  await server.close();
}
