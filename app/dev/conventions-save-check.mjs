/** VIN-357: actual GRAPH.md editor, save feedback and edits during an in-flight save.
 * VINC_TEST_MODULES=<scratch>/package.json node app/dev/conventions-save-check.mjs
 * Synthetic API fixtures; installed desktop acceptance is separate.
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
const output = await mkdtemp(join(tmpdir(), "vin357-save-"));
await esbuild.build({ stdin: { contents: 'export * from "./conventions"; export { cfg, dataMode, api } from "./api";',
  resolveDir: join(root, "src"), loader: "ts" }, bundle: true, format: "esm", platform: "browser",
  outfile: join(output, "save.mjs"), loader: { ".css": "empty", ".svg": "text" }, logLevel: "silent" });
esbuild.stop();
const dom = new JSDOM(await readFile(join(root, "index.html"), "utf8"),
  { url: "http://tauri.localhost/#view", pretendToBeVisual: true });
for (const key of ["window", "document", "location", "localStorage", "navigator", "HTMLElement", "Node",
  "Element", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent"])
  Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true });
window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
const { cfg, dataMode, api, mountConventions } = await import(pathToFileURL(join(output, "save.mjs")));
dataMode.set("account"); cfg.lang = "en";
const text = "Personal guidance\n<!-- vinc:generated start -->\nGenerated vocabulary\n<!-- vinc:generated end -->\nTail";
api.conventions = async () => ({ executed: true, doc_id: "actual-conventions-id", text });
let writes = [], result = true, finishSave, rebuilds = 0;
api.docPut = async (...args) => { writes.push(args); return result === "defer" ? new Promise(r => { finishSave = r; }) : { executed: result }; };
api.conventionsBuild = async () => { rebuilds++; return { executed: false }; };
const host = document.createElement("div"), foot = document.createElement("div");
document.body.append(host, foot);
const sheet = { body: host, foot, close() {} };
const status = () => foot.querySelector("#conv-status").textContent;
const edit = value => { const ta = host.querySelector("#conv-head"); ta.value = value; ta.dispatchEvent(new Event("input", { bubbles: true })); };
async function until(check) { for (let i = 0; i < 100 && !check(); i++) await new Promise(r => setTimeout(r, 10)); assert.ok(check()); }
await mountConventions(host, sheet);
assert.equal(status(), ""); assert.match(host.textContent, /local storage.*desktop app/);
edit("Edited guidance"); assert.equal(status(), "Unsaved changes");
foot.querySelector("#conv-save").click(); await until(() => status() === "Saved");
assert.equal(writes[0][2], "actual-conventions-id"); assert.match(writes[0][1], /Generated vocabulary/);
assert.equal(host.querySelector("#conv-head").value, "Edited guidance");
result = "defer"; edit("First revision"); foot.querySelector("#conv-save").click();
assert.equal(status(), "Saving…"); assert.ok(foot.querySelector("#conv-rebuild").disabled);
edit("Second revision"); finishSave({ executed: true });
await until(() => !foot.querySelector("#conv-save").disabled);
assert.equal(status(), "Unsaved changes"); assert.match(writes.at(-1)[1], /First revision/);
result = false; foot.querySelector("#conv-save").click(); await until(() => status() === "Could not save");
assert.equal(host.querySelector("#conv-head").value, "Second revision");
foot.querySelector("#conv-rebuild").click(); await until(() => !foot.querySelector("#conv-rebuild").disabled);
assert.equal(rebuilds, 0); assert.equal(status(), "Could not save");
assert.equal(host.querySelector("#conv-head").readOnly, false);
result = true; foot.querySelector("#conv-rebuild").click(); await until(() => rebuilds === 1 && !foot.querySelector("#conv-rebuild").disabled);
assert.equal(status(), "Saved, but could not rebuild. Try again.");
cfg.lang = "ko"; await mountConventions(host, sheet); edit("변경한 안내");
assert.equal(status(), "아직 저장하지 않은 변경이 있어요");
foot.querySelector("#conv-save").click(); await until(() => status() === "저장했어요");
console.log("PASS GRAPH.md: persistent saved/unsaved feedback, generated text preservation, concurrent edit stays dirty, failed save blocks rebuild, failed rebuild is honest, EN/KO.");
dom.window.close(); process.exit(0);
