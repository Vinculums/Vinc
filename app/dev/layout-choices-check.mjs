/** Actual View renderers with intercepted synthetic graph responses.
 * VINC_TEST_MODULES=<package exposing playwright> node dev/layout-choices-check.mjs
 */
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const output = resolve(process.env.VINC_LAYOUT_EVIDENCE || resolve(root, "../output/layout-choices"));
const require = createRequire(resolve(process.env.VINC_TEST_MODULES || resolve(root, "package.json")));
const { chromium } = require("playwright");
process.env.VINC_SIDECAR = "http://127.0.0.1:1";
const server = await createServer({ root, configFile: resolve(root, "vite.config.ts"),
  server: { host: "127.0.0.1", port: 0 } });
await server.listen();
const origin = server.resolvedUrls.local[0].replace(/\/$/, "");
const nodes = Array.from({ length: 12 }, (_, i) => ({ id: `concept:fixture-${i}`, label: `Fixture ${i}`,
  kind: "concept", domain: i < 6 ? "vinc/fixture/one" : "vinc/fixture/two", degree: 2,
  ts: `2020-${String(i + 1).padStart(2, "0")}-01`, content_ts: `2021-${String(12 - i).padStart(2, "0")}-01` }));
const edges = nodes.slice(1).map((n, i) => ({ id: `edge-${i}`, source: nodes[i].id, target: n.id,
  type: "PREREQUISITE_OF", confidence: .8, evidence: "Fixture evidence", cross_domain: i === 5 }));
// Deliberately distinct server coordinates make accidental worker replacement observable.
const graphFor = (layout, cone) => ({ executed: true, nodes: nodes.map((n, i) => ({ ...n,
  x: (layout === "rank" ? i : Math.cos(i)) * cone, y: Math.sin(i), z: i / 12,
  x2: layout === "rank" ? i : Math.cos(i), y2: Math.sin(i) })), edges });
const errors = [], requests = [], stages = [], results = [];
let browser;
try {
  await mkdir(output, { recursive: true });
  browser = await chromium.launch({ channel: "msedge", headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  await context.addInitScript(() => {
    localStorage.setItem("vinc.mode", "local"); localStorage.setItem("vinc.owner", "fixture");
    localStorage.setItem("vinc.lang", "en"); localStorage.setItem("vinc.theme", "light");
    const NativeWorker = window.Worker;
    window.layoutWorkers = 0;
    window.Worker = class extends NativeWorker { constructor(...args) { super(...args); window.layoutWorkers++; } };
  });
  await context.route("**/*", async route => {
    const req = route.request(), url = new URL(req.url());
    if (url.origin !== origin) return route.abort();
    if (!["fetch", "xhr"].includes(req.resourceType()) || /\.(wasm|[cm]?js|ts)$/.test(url.pathname)
      || /^\/(src|node_modules|@)/.test(url.pathname)) return route.continue();
    if (url.pathname.endsWith("/graph")) {
      const layout = url.searchParams.get("layout"), cone = Number(url.searchParams.get("cone") || 1);
      requests.push({ layout, cone, domains: url.searchParams.get("domains") });
      return route.fulfill({ json: graphFor(layout, cone) });
    }
    return route.fulfill({ json: { executed: true, nodes: [], edges: [], rows: [], domains: [], docs: [], items: [],
      unfiled: [], counts: { domains: 0, docs: 0, unfiled: 0, kinds: {} } } });
  });
  const page = await context.newPage();
  page.on("pageerror", e => errors.push(e.message));
  page.on("console", m => { if (m.text().startsWith("View layout stages")) stages.push(m.text()); });
  await page.goto(`${origin}/#view`);
  const handle = view => page.evaluate(v => v === "2d" ? !!window.__s2?.getGraph()?.order : !!window.__g3?.graphData()?.nodes.length, view);
  await page.waitForFunction(() => window.__g3?.graphData()?.nodes.length === 12);
  assert.equal(await page.evaluate(() => window.layoutWorkers), 0, "default hub must not allocate a layout worker");
  await page.locator("#view-lens").click();
  await page.locator("[data-pref='layout'][data-val='hub']").waitFor();
  for (const view of ["3d", "2d"]) {
    if (view === "2d") await page.locator("#tb-viewpoints [data-view='2d']").click();
    await page.waitForFunction(v => v === "2d" ? window.__s2?.getGraph()?.order === 12 : window.__g3?.graphData()?.nodes.length === 12, view);
    assert.equal(await page.locator("[data-pref='layout']").count(), 4);
    for (const layout of ["hub", "rank", "temporalRoots", "rankedRoots", "hub"]) {
      const beforeWorkers = await page.evaluate(() => window.layoutWorkers);
      await page.evaluate(v => { window.previousLayoutHandle = v === "2d" ? window.__s2 : window.__g3; }, view);
      const already = await page.locator(`[data-pref='layout'][data-val='${layout}']`).getAttribute("aria-checked") === "true";
      await page.locator(`[data-pref='layout'][data-val='${layout}']`).click();
      if (!already) await page.waitForFunction(v => (v === "2d" ? window.__s2 : window.__g3) !== window.previousLayoutHandle, view);
      await page.waitForTimeout(600);
      assert.equal(await page.locator(`[data-pref='layout'][data-val='${layout}']`).getAttribute("aria-checked"), "true");
      assert.equal(await handle(view), true);
      const roots = layout.endsWith("Roots"), workerCount = await page.evaluate(() => window.layoutWorkers);
      if (!roots) assert.equal(workerCount, beforeWorkers, "existing layout allocated a worker");
      else assert.ok(workerCount > beforeWorkers, "new roots layout did not use its worker");
      const received = await page.evaluate(v => v === "2d"
        ? window.__s2.getGraph().nodes().map(id => ({ id, x: window.__s2.getGraph().getNodeAttribute(id, "x"), y: window.__s2.getGraph().getNodeAttribute(id, "y") }))
        : window.__g3.graphData().nodes.map(n => ({ id: n.id, x: n.fx / 140, y: n.fy / 140, z: n.fz / 140 })), view);
      const last = requests.at(-1), expected = graphFor(last.layout, last.cone).nodes;
      if (!roots) for (const n of expected) {
        const got = received.find(x => x.id === n.id);
        assert.ok(Math.abs(got.x - (view === "2d" ? n.x2 : n.x)) < 1e-8, `${view} ${layout}: changed server x`);
        assert.ok(Math.abs(got.y - (view === "2d" ? n.y2 : n.y)) < 1e-8, `${view} ${layout}: changed server y`);
      }
      else assert.ok(received.some(n => !expected.some(e => e.id === n.id && Math.abs(n.x - e.x) < 1e-8)), "roots used original coordinates");
      assert.equal(await page.locator("#pref-cone").count(), view === "3d" && !roots ? 1 : 0);
      if (view === "3d") {
        const rendered = await page.evaluate(() => window.__g3.graphData().links.filter(e => !e.presentation)
          .map(e => ({ id: e.id, confidence: e.confidence, evidence: e.evidence,
            source: typeof e.source === "object" ? e.source.id : e.source,
            target: typeof e.target === "object" ? e.target.id : e.target })));
        assert.deepEqual(rendered, edges.map(({ id, confidence, evidence, source, target }) => ({ id, confidence, evidence, source, target })));
      }
      const beforeReads = requests.length;
      const currentBasis = await page.locator("[data-pref='timeBasis'][aria-checked='true']").getAttribute("data-val");
      await page.locator(`[data-pref='timeBasis'][data-val='${currentBasis === "recorded" ? "content" : "recorded"}']`).click();
      if (roots) await page.waitForFunction(n => window.layoutWorkers > n, workerCount);
      await page.waitForTimeout(500);
      assert.equal(requests.length, beforeReads, "time basis must reuse the same received graph");
      assert.equal(await page.evaluate(() => window.layoutWorkers), roots ? workerCount + 1 : workerCount);
      results.push({ view, layout, nodes: received.length, workers: workerCount, lastRequest: last });
      if (layout !== "hub" || results.length < 3) await page.screenshot({ path: resolve(output, `${view}-${layout}.png`) });
    }
  }
  await page.locator("#tb-viewpoints [data-view='3d']").click();
  await page.locator("#pref-cone").waitFor();
  const beforeConeReads = requests.length;
  await page.locator("#pref-cone").evaluate(e => { e.value = "1.2"; e.dispatchEvent(new Event("change", { bubbles: true })); });
  await page.waitForFunction(() => JSON.parse(localStorage.getItem("vinc.view.prefs")).cone === 1.2);
  await page.waitForTimeout(700);
  assert.equal(requests.length, beforeConeReads + 1, "restored density must request new server coordinates");
  assert.equal(requests.at(-1).cone, 1.2);
  // A restored original layout must survive reload with no ELK job.
  await page.reload(); await page.waitForFunction(() => window.__g3?.graphData()?.nodes.length === 12);
  assert.equal(await page.evaluate(() => window.layoutWorkers), 0);
  assert.deepEqual(errors, []);
  await writeFile(resolve(output, "results.json"), JSON.stringify({ synthetic: true, results, requests, stages, errors }, null, 2));
  console.log(`layout-choices-check: ${results.length} renderer/layout cases passed; default and reload allocated no worker`);
} finally { await browser?.close(); await server.close(); }
