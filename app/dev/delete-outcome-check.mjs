/** Bounded APP-DELETE-01 fault injection with real API and editor modules.
 * npm install --prefix <scratch> jsdom@26.1.0 esbuild@0.25.12
 * VINC_TEST_MODULES=<scratch>/package.json node app/dev/delete-outcome-check.mjs
 * Component behavior only; no browser layout or installed-shell verdict.
 */
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, resolve, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(resolve(process.env.VINC_TEST_MODULES || join(root, "package.json")));
const { JSDOM } = require("jsdom");
const esbuild = require("esbuild");
const output = await mkdtemp(join(tmpdir(), "vin348-components-"));
await esbuild.build({
  entryPoints: ["doc-editor", "doc-delete", "api"].map(n => join(root, "src", `${n}.ts`)),
  bundle: true, splitting: true, format: "esm", platform: "browser", outdir: output,
  outExtension: { ".js": ".mjs" }, loader: { ".css": "empty", ".svg": "text" }, logLevel: "silent",
});
const dom = new JSDOM("<body></body>", { url: "https://vincs.io/app/", pretendToBeVisual: true });
for (const key of ["window", "document", "location", "localStorage", "navigator", "HTMLElement",
  "HTMLInputElement", "HTMLTextAreaElement", "Node", "Element", "MutationObserver", "DOMRect",
  "Event", "InputEvent", "KeyboardEvent", "CustomEvent", "MouseEvent", "Range", "Window"])
  Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true });
globalThis.getComputedStyle = dom.window.getComputedStyle.bind(dom.window);
globalThis.requestAnimationFrame = dom.window.requestAnimationFrame.bind(dom.window);
globalThis.cancelAnimationFrame = dom.window.cancelAnimationFrame.bind(dom.window);
globalThis.dispatchEvent = dom.window.dispatchEvent.bind(dom.window);
Range.prototype.getClientRects = () => [];
Range.prototype.getBoundingClientRect = () => new DOMRect();
window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
// jsdom has no animation engine; motion has no bearing on these outcome assertions.
HTMLElement.prototype.animate = () => ({ finished: Promise.resolve(), cancel() {} });
HTMLElement.prototype.getAnimations = () => [];
const pause = (ms = 20) => new Promise(r => setTimeout(r, ms));
async function until(check) {
  for (let n = 0; n < 150; n++) { if (check()) return; await pause(); }
  assert.ok(check(), "component did not reach expected state");
}
let scenario = "success", deleted = false, reads = 0, navReads = 0, deletes = 0, puts = 0;
const warnings = [];
const requests = [];
let client;
console.warn = message => warnings.push(message);
globalThis.fetch = async (url, opts = {}) => {
  const u = new URL(url);
  requests.push({ path: u.pathname, team: u.searchParams.get("team") });
  u.pathname = u.pathname.replace(/^\/api\/graph/, "");
  if (u.pathname === "/doc/delete") {
    deletes++;
    assert.equal(JSON.parse(opts.body).id, "synthetic-doc");
    if (scenario === "success") { deleted = true; return Response.json({ executed: true }); }
    if (scenario === "landed" || scenario === "scope") deleted = true;
    if (scenario === "scope") client.cfg.team = "other-team";
    if (scenario === "timeout") { deleted = true; throw new TypeError("fixture transport failure"); }
    return Response.json({ error: { code: "graph_upstream_error", message: "never log this body" } }, { status: 502 });
  }
  if (u.pathname === "/doc") {
    reads++;
    if (scenario === "unknown" && deletes) return Response.json({ error: { code: "graph_upstream_error" } }, { status: 502 });
    return Response.json(deleted ? { executed: false, error: "unknown doc_id" } : {
      executed: true, verified: true, text: "Fixture body", title: "Fixture", content_hash: "fixture-hash",
    });
  }
  if (u.pathname === "/doc/put") { puts++; return Response.json({ executed: true }); }
  if (u.pathname === "/nav-tree") navReads++;
  return Response.json({ executed: true, domains: [], unfiled: [], concepts: [], nodes: [], edges: [], counts: { kinds: {} } });
};
const editor = await import(pathToFileURL(join(output, "doc-editor.mjs")));
const deletion = await import(pathToFileURL(join(output, "doc-delete.mjs")));
client = await import(pathToFileURL(join(output, "api.mjs")));
let saved = 0;
async function open(next) {
  scenario = next; deleted = false; reads = 0; navReads = 0; deletes = 0; puts = 0; saved = 0;
  await editor.openDocSheet({ owner: "tenant:local", docId: "synthetic-doc", onSaved: () => saved++ });
  assert.ok(document.querySelector(".cm-content"), "real CodeMirror must mount");
}
async function confirm() {
  document.querySelector("#ed-del").click();
  await until(() => document.querySelector("#cd-yes"));
  document.querySelector("#cd-yes").click();
}
for (const next of ["success", "landed", "timeout"]) {
  await open(next);
  const body = document.querySelector(".cm-content");
  body.querySelector(".cm-line").textContent = "Pending edit must not resurrect deleted document";
  body.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: "edit" }));
  await pause();
  await confirm();
  await until(() => saved === 1 && navReads > 0);
  await until(() => !document.querySelector("#ed-del"));
  assert.equal(deletes, 1);
  assert.equal(puts, 0, "closing after deletion must discard queued autosave");
  if (next !== "success") assert.ok(reads >= 2, "uncertain write must read exact document");
}
await open("present");
await confirm();
await until(() => navReads > 0);
assert.match(document.querySelector("#ed-del").textContent, /still here/);
assert.equal(saved, 0);
assert.equal(document.querySelector(".sheet-body").inert, false);
document.querySelector("#ed-close").click();
await until(() => !document.querySelector("#ed-del"));

await open("unknown");
await confirm();
await until(() => navReads > 0);
assert.match(document.querySelector("#ed-del").textContent, /Couldn't confirm/);
assert.equal(saved, 0);
scenario = "landed"; deleted = true;
window.dispatchEvent(new CustomEvent("vinc:account-reachable"));
await until(() => saved === 1 && navReads >= 2);
assert.equal(deletes, 1, "recovery reads, never repeats the write");
await until(() => !document.querySelector("#ed-del"));
assert.ok(warnings.some(w => w.includes("status=502 code=graph_upstream_error")));
assert.ok(warnings.every(w => !w.includes("never log this body") && !w.includes("synthetic-doc")));

scenario = "unknown"; deletes = 1;
assert.equal(await deletion.checkDocumentDeletion("tenant:local", "synthetic-doc", { team: null }), "unknown");
scenario = "scope"; deleted = false; client.cfg.team = "original-team";
const beforeScope = requests.length;
assert.equal(await deletion.deleteDocument("tenant:local", "synthetic-doc"), "deleted");
assert.ok(requests.slice(beforeScope).every(r => r.team === "original-team"),
  "an uncertain write must read from its original team even after the UI changes team");
console.log("PASS: success, applied 502/timeout, still present, unknown then recovery, list refresh, redacted status/code logs, no late autosave, original team scope");
dom.window.close();
