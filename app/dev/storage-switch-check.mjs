/** VIN-361: actual storage controls, successful and rejected mode writes.
 * VINC_TEST_MODULES=<scratch>/package.json node app/dev/storage-switch-check.mjs
 * Uses scratch jsdom/esbuild. Installed desktop rendering remains separate.
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
const output = await mkdtemp(join(tmpdir(), "vin361-storage-"));
await esbuild.build({ stdin: { contents: 'export * from "./storage-mode"; export { storage, dataMode } from "./api";',
    resolveDir: join(root, "src"), loader: "ts" },
  bundle: true, format: "esm", platform: "browser", outfile: join(output, "storage.mjs"),
  loader: { ".css": "empty", ".svg": "text" }, logLevel: "silent" });
esbuild.stop();
const dom = new JSDOM(await readFile(join(root, "index.html"), "utf8"),
  { url: "http://tauri.localhost/#view", pretendToBeVisual: true });
for (const key of ["window", "document", "localStorage", "navigator", "HTMLElement", "Node", "Element",
  "Event", "CustomEvent", "MouseEvent"])
  Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true });
let reloads = 0;
Object.defineProperty(globalThis, "location", { value: { protocol: "http:", hostname: "tauri.localhost",
  origin: "http://tauri.localhost", hash: "#view", reload: () => reloads++ }, configurable: true });
globalThis.getComputedStyle = dom.window.getComputedStyle.bind(dom.window);
window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
localStorage.setItem("vinc.lang", "en");
const { storage, dataMode, mountStorage } = await import(pathToFileURL(join(output, "storage.mjs")));
const conf = { mode: "account", data_path: "", default_folder: "/fixture/default",
  suggested_folder: "/fixture/empty", pinned: false };
storage.config = async () => conf;
storage.inspect = async () => ({ ok: true, has_store: false, store_bytes: 0, risks: [] });
let reject = false;
storage.setConfig = async body => {
  if (body.mode && reject) return { status: 400, error: "synthetic_rejection" };
  Object.assign(conf, body);
  return { restart_needed: false };
};
const box = document.createElement("div"); document.body.appendChild(box);
async function until(check) {
  for (let n = 0; n < 100; n++) { if (check()) return; await new Promise(r => setTimeout(r, 10)); }
  assert.ok(check(), "storage control did not settle");
}
async function draw(mode) {
  dataMode.set(mode); mountStorage(box);
  await until(() => box.querySelector("#st-toggle"));
}
async function turnOn(fail = false) {
  reject = fail;
  await draw("account");
  box.querySelector("#st-toggle").click();
  await until(() => box.querySelector("#st-check")?.textContent);
  const keep = box.querySelector('input[value="keep"]'); keep.checked = true;
  box.querySelector("#st-go").click();
  await until(() => fail ? box.querySelector("#st-result").textContent : dataMode.get() === "local");
}
await turnOn(true);
assert.equal(dataMode.get(), "account"); assert.equal(reloads, 0);
await turnOn();
assert.equal(reloads, 1); assert.equal(conf.mode, "local");
await draw("local");
assert.match(box.textContent, /this device only/i);
assert.match(box.querySelector("#st-toggle").textContent, /turn off/i);
const folder = conf.data_path;
box.querySelector("#st-toggle").click();
reject = true; box.querySelector("#st-keep").click();
await until(() => !box.querySelector("#st-keep").disabled);
assert.equal(dataMode.get(), "local"); assert.equal(reloads, 1);
reject = false; box.querySelector("#st-keep").click();
await until(() => dataMode.get() === "account");
assert.equal(reloads, 2); assert.equal(conf.data_path, folder);
await draw("account");
assert.match(box.textContent, /your account/i);
assert.match(box.querySelector("#st-toggle").textContent, /turn on/i);
dom.window.close();
console.log("PASS: Turn on and Switch without moving reload automatically only after successful persistence; mode controls and folder preservation.");
process.exit(0);
