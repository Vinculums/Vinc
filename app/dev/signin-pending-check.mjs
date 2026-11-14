/** VIN-341: actual sign-in screen and device flow with synthetic transport/IPC.
 * VINC_TEST_MODULES=<scratch>/package.json node app/dev/signin-pending-check.mjs
 * Exercises cancellation and late responses; native browser acceptance is separate.
 */
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, resolve, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(resolve(process.env.VINC_TEST_MODULES || join(root, "package.json")));
const { JSDOM } = require("jsdom"), esbuild = require("esbuild");
const output = await mkdtemp(join(tmpdir(), "vin341-signin-"));
// 번들 안의 import.meta.env 는 빈 객체로 둔다: vite 밖에서도 빌드 상수(VITE_*)를 읽는 모듈이 떠야 한다.
await esbuild.build({ define: { "import.meta.env": "{}" }, stdin: { contents: 'export * from "./firstrun"; export { forgetAccount, hasAccount } from "./account-connect"; export { cfg, api } from "./api";',
  resolveDir: join(root, "src"), loader: "ts" }, bundle: true, format: "esm", platform: "browser",
  outfile: join(output, "signin.mjs"), loader: { ".css": "empty", ".svg": "text" }, logLevel: "silent",
  plugins: [{ name: "fixture-native-ipc", setup(build) {
    build.onResolve({ filter: /^@tauri-apps\/api\/core$/ }, () => ({ path: "core", namespace: "fixture" }));
    build.onLoad({ filter: /.*/, namespace: "fixture" }, () => ({ contents:
      "export const invoke = (...a) => globalThis.fixtureInvoke(...a); export class Resource {} export class Channel {}", loader: "js" }));
  } }] });
esbuild.stop();
const dom = new JSDOM(await readFile(join(root, "index.html"), "utf8"),
  { url: "http://tauri.localhost/#view", pretendToBeVisual: true });
for (const key of ["window", "document", "location", "localStorage", "navigator", "HTMLElement", "Node",
  "Element", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent"])
  Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true });
globalThis.dispatchEvent = dom.window.dispatchEvent.bind(dom.window);
window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
const realTimeout = globalThis.setTimeout;
globalThis.setTimeout = (fn, ms, ...args) => realTimeout(fn, ms === 2000 ? 5 : ms, ...args);
const opens = []; let openFails = false, copied = "";
globalThis.fixtureInvoke = async (command, args) => {
  if (command === "plugin:shell|open") { opens.push(args.path); if (openFails) throw Error("fixture browser unavailable"); }
  if (command === "secret_available") return false;
  return null;
};
Object.defineProperty(navigator, "clipboard", { value: { writeText: async text => { copied = text; } }, configurable: true });
globalThis.fetch = async url => { assert.equal(new URL(url).pathname, "/health"); return Response.json({ status: "ok" }); };
const { requireSignIn, cfg, api, forgetAccount, hasAccount } = await import(pathToFileURL(join(output, "signin.mjs")));
const $ = id => document.getElementById(id);
const wait = ms => new Promise(r => realTimeout(r, ms));
async function until(check) { for (let i = 0; i < 100 && !check(); i++) await wait(10); assert.ok(check(), "state did not settle"); }
for (const lang of ["en", "ko"]) {
  cfg.lang = lang; copied = ""; forgetAccount();
  let startDone, staleDone, currentDone, starts = 0;
  api.connectStart = async () => ++starts === 1 ? new Promise(r => { startDone = r; }) : { state: `fixture-state-${starts}` };
  api.connectStatus = async state => new Promise(r => {
    if (state.endsWith("2")) staleDone = r; else currentDone = r;
  });
  const finished = requireSignIn();
  assert.equal($("shell").getAttribute("inert"), "");
  const legal = [...document.querySelectorAll("#signin-screen .si-note a")];
  assert.equal(legal.length, 2); assert.match(legal[0].textContent, lang === "en" ? /Terms/ : /이용약관/);
  for (const link of legal) link.click();
  await until(() => opens.includes("https://vincs.io/terms/") && opens.includes("https://vincs.io/privacy/"));
  $("fr-signin").click(); await until(() => startDone);
  assert.equal($("fr-progress").hidden, false); assert.equal($("fr-copy").disabled, true);
  $("fr-cancel").click(); assert.equal($("fr-actions").hidden, true);
  assert.equal($("fr-signin").disabled, false); assert.equal(document.activeElement.id, "fr-signin");
  const count = opens.length; startDone({ state: "canceled-before-url" }); await wait(25);
  assert.equal(opens.length, count); assert.equal(hasAccount(), false);
  openFails = true; $("fr-signin").click(); await until(() => staleDone);
  assert.equal($("fr-progress").hidden, false); assert.equal($("fr-reopen").disabled, false);
  assert.match($("fr-state").textContent, lang === "en" ? /Waiting/ : /기다리고/);
  $("fr-copy").click(); await until(() => copied.includes("fixture-state-2") && /copied|복사했/.test($("fr-state").textContent));
  assert.match($("fr-state").textContent, lang === "en" ? /copied/ : /복사했/);
  openFails = false; $("fr-reopen").click(); await until(() => opens.length === count + 2);
  $("fr-cancel").click(); $("fr-google").click(); await until(() => currentDone);
  assert.match(opens.at(-1), /via=google/);
  staleDone({ status: "delivered", key: "synthetic-stale-key" }); await wait(25);
  assert.equal(hasAccount(), false); assert.equal(localStorage.getItem("vinc.sync.key"), null);
  assert.equal($("fr-signin").disabled, true); // The old result cannot unlock the new attempt.
  currentDone({ status: "delivered", key: "synthetic-current-key", username: "fixture-user" });
  await finished; assert.equal($("signin-screen"), null); assert.equal($("shell").hasAttribute("inert"), false);
  assert.equal(localStorage.getItem("vinc.sync.key"), "synthetic-current-key");
}
forgetAccount(); cfg.lang = "en";
api.connectStart = async () => ({ state: "fixture-expired" });
api.connectStatus = async () => ({ status: "expired" });
const finished = requireSignIn(); $("fr-signin").click(); await until(() => /expired/.test($("fr-state").textContent));
assert.equal($("fr-signin").disabled, false); assert.equal($("fr-progress").hidden, true);
api.connectStatus = async () => ({ status: "delivered", key: "synthetic-retry-key" });
$("fr-signin").click(); await finished; forgetAccount();
requireSignIn("first", Promise.reject(Error("fixture startup failed"))); $("fr-signin").click();
await until(() => /could not start/.test($("fr-state").textContent));
assert.equal($("fr-actions").hidden, true); assert.equal($("fr-signin").disabled, false);
console.log("PASS sign-in: EN/KO legal browser links, visible waiting, cancel before URL and during poll, reopen/copy after browser failure, late delivery discarded, Google retry, expiry, startup failure and inert shell.");
dom.window.close(); process.exit(0);
