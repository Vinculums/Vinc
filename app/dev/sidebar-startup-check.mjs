/** VIN-360: fresh sidebar instances and the main startup barrier.
 * VINC_TEST_MODULES=<scratch>/package.json node app/dev/sidebar-startup-check.mjs
 * Requires scratch jsdom/esbuild. These simulated starts are not native cold starts.
 */
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, resolve, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(resolve(process.env.VINC_TEST_MODULES || join(root, "package.json")));
const { JSDOM } = require("jsdom");
const esbuild = require("esbuild");
const output = await mkdtemp(join(tmpdir(), "vin360-sidebar-"));
await esbuild.build({
  stdin: { contents: 'export * from "./lnb"; export { api, cfg, dataMode } from "./api";',
    resolveDir: join(root, "src"), loader: "ts" },
  bundle: true, format: "esm", platform: "browser", outfile: join(output, "sidebar.mjs"),
  loader: { ".css": "empty", ".svg": "text" }, logLevel: "silent",
});
const html = await readFile(join(root, "index.html"), "utf8");
const main = await readFile(join(root, "src/main.ts"), "utf8");
esbuild.stop();
assert.ok(main.includes("mountLnb({ deferLoad: true })"));
const firstRead = main.indexOf("void refreshLnb();");
for (const barrier of ["await sidecar;", "await decideDataMode()", "await ensureWebAuth()",
  "await adoptSpaceFromAddress()"])
  assert.ok(main.indexOf(barrier) < firstRead && main.indexOf(barrier) >= 0, barrier);
assert.ok(firstRead < main.indexOf("mountLiveRefresh();"));

let instance = 0;
async function fresh(mode) {
  const dom = new JSDOM(html, { url: "http://localhost/#view", pretendToBeVisual: true });
  for (const key of ["window", "document", "location", "localStorage", "navigator", "HTMLElement",
    "HTMLInputElement", "Node", "Element", "Event", "CustomEvent", "MouseEvent"])
    Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true });
  globalThis.getComputedStyle = dom.window.getComputedStyle.bind(dom.window);
  window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  localStorage.setItem("vinc.lang", "en");
  const m = await import(pathToFileURL(join(output, "sidebar.mjs")) + `?start=${++instance}`);
  m.dataMode.set(mode);
  let calls = 0, ready = false, fail = false;
  m.api.navTree = async () => {
    calls++;
    assert.ok(ready, "sidebar queried before startup completed");
    if (fail) throw new Error("synthetic real failure");
    return { executed: true, domains: [], unfiled: [] };
  };
  return { m, dom, calls: () => calls, ready: () => { ready = true; }, fail: () => { fail = true; } };
}
for (const mode of ["account", "local"]) {
  for (let n = 0; n < 10; n++) {
    const f = await fresh(mode);
    f.m.mountLnb({ deferLoad: true });
    await new Promise(r => setTimeout(r, 10));
    assert.equal(f.calls(), 0);
    assert.match(document.getElementById("lnb-body").textContent, /loading/i);
    assert.equal(document.getElementById("lnb-retry"), null);
    f.ready();
    await f.m.refreshLnb();
    assert.equal(f.calls(), 1);
    assert.equal(document.getElementById("lnb-retry"), null);
    assert.match(document.getElementById("lnb-body").textContent, /no documents/i);
    f.dom.window.close();
  }
}
const failure = await fresh("local");
failure.m.mountLnb({ deferLoad: true });
failure.ready(); failure.fail();
await failure.m.refreshLnb();
assert.ok(document.getElementById("lnb-retry"));
document.getElementById("lnb-retry").click();
await new Promise(r => setTimeout(r, 10));
assert.equal(failure.calls(), 2);
assert.ok(document.getElementById("lnb-retry"));
failure.dom.window.close();
console.log("PASS: startup barrier, 10 fresh fixtures per mode, real failure and Retry. Native cold starts pending.");
// 번들에 포함된 UI 모듈의 브라우저 타이머는 이 Node fixture가 소유하지 않는다.
process.exit(0);
