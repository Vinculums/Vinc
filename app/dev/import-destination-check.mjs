/** VIN-351: real Intake and proxy routing with synthetic file/network fixtures.
 * VINC_TEST_MODULES=<scratch>/package.json node app/dev/import-destination-check.mjs
 * Scratch jsdom/esbuild only. Native rendering and actual graph writes are separate.
 */
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, resolve, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(resolve(process.env.VINC_TEST_MODULES || join(root, "package.json")));
const { JSDOM } = require("jsdom"); const esbuild = require("esbuild");
const output = await mkdtemp(join(tmpdir(), "vin351-import-"));
globalThis.fixtureInvoke = async command => command === "secret_available" ? true
  : command === "secret_get" ? "synthetic-member-key" : null;
// 번들 안의 import.meta.env 는 빈 객체로 둔다: vite 밖에서도 빌드 상수(VITE_*)를 읽는 모듈이 떠야 한다.
await esbuild.build({ define: { "import.meta.env": "{}" }, stdin: { contents: 'export * from "./surfaces/intake"; export { cfg, dataMode } from "./api";',
    resolveDir: join(root, "src"), loader: "ts" },
  bundle: true, format: "esm", platform: "browser", outfile: join(output, "intake.mjs"),
  loader: { ".css": "empty", ".svg": "text" }, logLevel: "silent",
  plugins: [{ name: "fixture-native-ipc", setup(build) {
    build.onResolve({ filter: /^@tauri-apps\/api\/core$/ }, () => ({ path: "core", namespace: "fixture" }));
    build.onLoad({ filter: /.*/, namespace: "fixture" }, () => ({
      contents: "export const invoke = (...a) => globalThis.fixtureInvoke(...a); export class Resource {} export class Channel {}", loader: "js" }));
  } }] });
esbuild.stop();
const dom = new JSDOM(await readFile(join(root, "index.html"), "utf8"),
  { url: "http://tauri.localhost/#view", pretendToBeVisual: true });
for (const key of ["window", "document", "location", "localStorage", "navigator", "HTMLElement", "Node",
  "Element", "Event", "CustomEvent", "MouseEvent"])
  Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true });
globalThis.getComputedStyle = dom.window.getComputedStyle.bind(dom.window);
globalThis.dispatchEvent = dom.window.dispatchEvent.bind(dom.window);
globalThis.addEventListener = dom.window.addEventListener.bind(dom.window);
window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
const { cfg, dataMode, renderIntake } = await import(pathToFileURL(join(output, "intake.mjs")));
cfg.token = "synthetic-session"; cfg.lang = "en";
const team = "synthetic-team", name = 'QA <Team>&"space"';
let missingName = false, releaseSpaces, releaseWrite, requests = [];
globalThis.fetch = async (url, opts = {}) => {
  let path = new URL(url).pathname, body;
  if (path === "/space/proxy") { body = JSON.parse(opts.body); path = body.path; }
  if (path === "/spaces") {
    const answer = () => Response.json({ teams: missingName ? [] : [{ id: team, name, status: "active" }] });
    if (releaseSpaces === "defer") return new Promise(r => { releaseSpaces = () => r(answer()); });
    return answer();
  }
  if (path === "/ingest/fragment") {
    requests.push({ path, remote: !!body, team: body?.team || "" });
    return new Promise(r => { releaseWrite = () => r(Response.json({ executed: true,
      merged: { nodes: { created: 1 }, edges: { created: 0 } } })); });
  }
  throw new Error(`unexpected fixture request ${path}`);
};
const el = document.createElement("div"); document.body.appendChild(el);
const file = { name: "fixture.json", arrayBuffer: async () => new TextEncoder().encode(JSON.stringify({
  nodes: [{ id: "concept:fixture", type: "concept", title: "Fixture" }], edges: [] })).buffer };
async function until(check) {
  for (let n = 0; n < 100; n++) { if (check()) return; await new Promise(r => setTimeout(r, 10)); }
  assert.ok(check(), "import destination did not settle");
}
for (const [mode, space, label] of [["account", team, name], ["account", "", "personal space"],
  ["local", team, "On your device only"]]) {
  dataMode.set(mode); cfg.team = space; requests = []; releaseWrite = null;
  renderIntake(el, { withHeader: false, withNewDoc: false });
  await until(() => el.querySelector(".import-destination").textContent.includes(label));
  if (mode === "account") assert.doesNotMatch(el.textContent, /on your device only|this is your account graph/i);
  assert.equal(el.querySelector(".import-destination Team"), null);
  renderIntake(el, { withHeader: false, files: [file] });
  await until(() => typeof releaseWrite === "function" && el.querySelector(".import-destination").textContent.includes(label));
  assert.equal(requests[0].remote, mode === "account");
  assert.equal(requests[0].team, mode === "account" ? space : "");
  if (mode === "account") assert.doesNotMatch(el.textContent, /on your device only|your account graph/i);
  releaseWrite();
  await until(() => el.querySelector("#add-more") || el.querySelector("#retry"));
  assert.ok(el.querySelector("#add-more"), el.textContent);
}
dataMode.set("account"); cfg.team = team; missingName = true;
renderIntake(el, { withHeader: false });
await new Promise(r => setTimeout(r, 30));
assert.match(el.querySelector(".import-destination").textContent, /Selected team space/);
assert.doesNotMatch(el.textContent, /personal space|on your device only/i);
missingName = false; releaseSpaces = "defer";
renderIntake(el, { withHeader: false });
await until(() => typeof releaseSpaces === "function");
cfg.team = ""; renderIntake(el, { withHeader: false });
releaseSpaces(); await new Promise(r => setTimeout(r, 30));
assert.match(el.querySelector(".import-destination").textContent, /personal space/);
cfg.lang = "ko"; cfg.team = team;
renderIntake(el, { withHeader: false });
await until(() => el.querySelector(".import-destination").textContent.includes(name));
assert.match(el.textContent, /팀 구성원과 공유해요/);
dom.window.close();
console.log("PASS: personal/team/local idle and importing labels match actual proxy scope; safe team names, unavailable lookup and stale lookup, isolated Korean fixture.");
process.exit(0);
