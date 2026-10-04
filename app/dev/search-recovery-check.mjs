/** APP-SEARCH-01: actual transport and Find components with synthetic fault injection.
 * VINC_TEST_MODULES=<scratch>/package.json node app/dev/search-recovery-check.mjs
 * Requires jsdom@26.1.0 and esbuild@0.25.12 in scratch, as other component checks do.
 * This is neither native rendering nor twenty actual searches after account sign-in.
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
const output = await mkdtemp(join(tmpdir(), "vin342-components-"));
globalThis.fixtureInvoke = async (command, args) => {
  if (command === "secret_available") return true;
  if (command === "secret_get") return "synthetic-member-key";
  if (command === "plugin:log|log") nativeLogs.push(args);
  return null;
};
await esbuild.build({
  entryPoints: ["api", "topbar", "surfaces/search"].map(n => join(root, "src", `${n}.ts`)),
  bundle: true, splitting: true, format: "esm", platform: "browser", outdir: output,
  outExtension: { ".js": ".mjs" }, loader: { ".css": "empty", ".svg": "text" }, logLevel: "silent",
  plugins: [{ name: "fixture-native-ipc", setup(build) {
    build.onResolve({ filter: /^@tauri-apps\/api\/core$/ }, () => ({ path: "core", namespace: "fixture" }));
    build.onLoad({ filter: /.*/, namespace: "fixture" }, () => ({
      contents: "export const invoke = (...args) => globalThis.fixtureInvoke(...args); export class Resource {} export class Channel {}", loader: "js",
    }));
  } }],
});
const html = await readFile(join(root, "index.html"), "utf8");
const dom = new JSDOM(html, { url: "http://tauri.localhost/#view", pretendToBeVisual: true });
for (const key of ["window", "document", "location", "localStorage", "navigator", "HTMLElement",
  "HTMLInputElement", "Node", "Element", "MutationObserver", "DOMRect", "Event", "InputEvent",
  "KeyboardEvent", "CustomEvent", "MouseEvent"])
  Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true });
globalThis.dispatchEvent = dom.window.dispatchEvent.bind(dom.window);
globalThis.addEventListener = dom.window.addEventListener.bind(dom.window);
globalThis.requestAnimationFrame = dom.window.requestAnimationFrame.bind(dom.window);
globalThis.getComputedStyle = dom.window.getComputedStyle.bind(dom.window);
window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
const pause = (ms = 20) => new Promise(r => setTimeout(r, ms));
async function until(check) {
  for (let n = 0; n < 100; n++) { if (check()) return; await pause(); }
  assert.ok(check(), "component did not reach expected state");
}
let searchResponses = [], probeStatus = 200, searches = 0, probes = 0, writes = 0;
let deferredProbe, deferredSearch;
let unreachable = 0;
const nativeLogs = [], warnings = [], routes = [];
console.warn = message => warnings.push(message);
window.addEventListener("vinc:account-unreachable", () => unreachable++);
globalThis.fetch = async (url, opts = {}) => {
  let path = new URL(url).pathname;
  let request;
  if (path === "/space/proxy") {
    request = JSON.parse(opts.body); path = request.path;
    assert.equal(request.token, "synthetic-member-key");
    assert.equal(opts.headers.Authorization, "Bearer synthetic-session-token");
    routes.push({ path, team: request.team || "" });
  }
  if (path === "/search") {
    searches++;
    if (deferredSearch) return new Promise(r => { deferredSearch.release = r; });
    const next = searchResponses.shift() ?? 200;
    if (next === "network") throw new TypeError("synthetic transport failure");
    if (next === "logical") return Response.json({ error: "synthetic body must not be logged" });
    return Response.json(next === 200 ? { rows: [], node_hits: [
      { id: "concept:fixture", title: "Fixture result", matched_in: "title" }], arms: "fts" } :
      { error: next === 502 ? "account_unreachable" : { code: "graph_upstream_error", message: "private response" } },
      { status: next });
  }
  if (path === "/changes") {
    probes++;
    if (deferredProbe) return new Promise(r => { deferredProbe.release = r; });
    return Response.json({ marker: "fixture" }, { status: probeStatus });
  }
  if (request?.method === "POST") { writes++; return Response.json({ error: "account_unreachable" }, { status: 502 }); }
  return Response.json({ docs: [], rows: [], concepts: [], domains: [], unfiled: [], marker: "fixture" });
};
const client = await import(pathToFileURL(join(output, "api.mjs")));
client.dataMode.set("account"); client.cfg.token = "synthetic-session-token";
client.cfg.team = "synthetic-team";
function reset(responses, status = 200) {
  searchResponses = responses.slice(); probeStatus = status;
  searches = 0; probes = 0; unreachable = 0; routes.length = 0;
}
for (const status of [500, 502, 503, 504]) {
  reset([status, 200]);
  const result = await client.api.search("private search text");
  assert.equal(result.node_hits.length, 1);
  assert.equal(searches, 2); assert.equal(probes, 0); assert.equal(unreachable, 0);
}
reset([502, 502]);
await assert.rejects(client.api.search("private search text"), e => e.status === 502);
assert.equal(searches, 2); assert.equal(probes, 1); assert.equal(unreachable, 0);
assert.ok(routes.every(r => r.team === "synthetic-team"));
reset([502, 502], 503);
await assert.rejects(client.api.search("private search text"));
assert.equal(unreachable, 1);
for (const status of [400, 401, 403, 429]) {
  reset([status]); await assert.rejects(client.api.search("fixture"), e => e.status === status);
  assert.equal(searches, 1); assert.equal(probes, 0);
}
reset(["network"]);
await assert.rejects(client.api.search("fixture"));
assert.equal(searches, 1); assert.equal(unreachable, 0);
reset(["logical"]);
await assert.rejects(client.api.search("fixture"), e => e.status === 200);
assert.equal(searches, 1);
assert.ok(warnings.every(s => !/synthetic-member-key|synthetic-session-token|private search text|private response|synthetic body/.test(s)));
assert.ok(nativeLogs.length > 0);
assert.ok(nativeLogs.every(s => s.level === 4 && /status=\d+ duration_ms=\d+/.test(s.message)));
assert.equal(warnings.length, nativeLogs.length);
// 다른 읽기가 성공하면 늦게 도착한 접속 실패로 배너를 띄우지 않는다.
reset([502, 502]); deferredProbe = {};
const pending = client.api.search("fixture");
await until(() => deferredProbe.release);
await client.api.domains(client.cfg.owner);
deferredProbe.release(Response.json({}, { status: 503 })); deferredProbe = null;
await assert.rejects(pending); assert.equal(unreachable, 0);
reset([502, 502]); deferredProbe = {};
const switched = client.api.search("fixture");
await until(() => deferredProbe.release);
client.cfg.team = "another-team";
deferredProbe.release(Response.json({}, { status: 503 })); deferredProbe = null;
await assert.rejects(switched); assert.equal(unreachable, 0);
client.cfg.team = "synthetic-team";
reset([]); writes = 0;
await assert.rejects(client.api.docDelete(client.cfg.owner, "synthetic-doc"));
assert.equal(writes, 1, "writes must never acquire the search retry");
client.dataMode.set("local");
reset([503, 200]); await client.api.search("fixture");
assert.equal(searches, 2); assert.equal(probes, 0);
client.dataMode.set("account");
const topbar = await import(pathToFileURL(join(output, "topbar.mjs")));
topbar.mountTopbar();
const q = document.querySelector("#tb-q"), box = document.querySelector("#tb-results");
reset([502, 502]); q.value = "fixture"; q.focus();
q.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
await until(() => box.querySelector(".search-retry"));
assert.equal(box.hidden, false); assert.ok(box.textContent.includes("HTTP 502"));
assert.equal(box.querySelector("[data-search-status]").dataset.searchStatus, "502");
assert.equal(document.querySelector(".sheet-scrim"), null, "Enter must leave the failure visible");
reset([200]); box.querySelector(".search-retry").click();
await until(() => box.textContent.includes("Fixture result")); assert.equal(searches, 1);
// 입력을 지운 뒤 도착한 오류가 검색창을 다시 열지 않는다.
reset([]); deferredSearch = {}; q.value = "old query";
q.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
await until(() => deferredSearch.release);
q.value = ""; q.dispatchEvent(new InputEvent("input", { bubbles: true }));
deferredSearch.release(Response.json({}, { status: 400 })); deferredSearch = null;
await pause(50); assert.equal(box.hidden, true);
const surface = document.createElement("div"); document.body.appendChild(surface);
const search = await import(pathToFileURL(join(output, "surfaces/search.mjs")));
location.hash = "#search";
search.renderSearch(surface);
reset([503, 503]); surface.querySelector("#q").value = "fixture";
surface.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
await until(() => surface.querySelector(".search-retry"));
assert.equal(surface.querySelector("[data-search-status]").dataset.searchStatus, "503");
reset([200]); surface.querySelector(".search-retry").click();
await until(() => surface.textContent.includes("Fixture result")); assert.equal(searches, 1);
client.cfg.lang = "ko";
reset([400]); surface.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
await until(() => surface.querySelector(".search-retry"));
assert.equal(surface.querySelector(".search-retry").textContent, "다시 시도하기");
// 합성 응답 20회는 로그인 직후 실제 계정 검색 기준과 별개다.
reset(Array(20).fill(200));
for (let i = 0; i < 20; i++) await client.api.search(`synthetic-${i}`);
assert.equal(searches, 20); assert.equal(unreachable, 0);
console.log("PASS: 5xx one retry, 4xx/network no retry, connectivity guard, scope/stale guards, write safety, both Retry controls, EN/KO, log redaction, 20 synthetic searches");
dom.window.close();
