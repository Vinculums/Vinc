/** Component regression with real CodeMirror and API serialization in jsdom.
 * npm install --prefix <scratch> jsdom@26.1.0 esbuild@0.25.12
 * VINC_TEST_MODULES=<scratch>/package.json node app/dev/create-form-check.mjs
 * This checks behavior, not browser layout or installed desktop builds.
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
const output = await mkdtemp(join(tmpdir(), "vin346-components-"));
await esbuild.build({
  entryPoints: ["tools", "api", "topbar", "doc-editor"].map((n) => join(root, "src", `${n}.ts`)),
  bundle: true, splitting: true, format: "esm", platform: "browser", outdir: output,
  outExtension: { ".js": ".mjs" }, loader: { ".css": "empty", ".svg": "text" }, logLevel: "silent",
});
const dom = new JSDOM("<body></body>", { url: "http://localhost:5346", pretendToBeVisual: true });
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
const pause = (ms = 20) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(check) {
  for (let n = 0; n < 150; n++) { if (check()) return; await pause(); }
  assert.ok(check(), "component did not reach the expected state");
}
const puts = [];
let release;
let fail = false;
globalThis.fetch = async (url, opts = {}) => {
  const u = new URL(url);
  if (u.pathname === "/doc/put") {
    puts.push(JSON.parse(opts.body));
    await new Promise((resolve) => { release = resolve; });
    return Response.json(fail ? { executed: false, error: "test rejection" } : {
      executed: true, created: true, doc_id: "test-doc", content_hash: "saved-hash", needs_embed: false,
    });
  }
  if (u.pathname === "/doc") return Response.json({ executed: true, text: "Typed body  \n", content_hash: "saved-hash" });
  if (u.pathname === "/docs") return Response.json({ executed: true,
    docs: u.searchParams.has("title") ? [{ doc_id: "test-doc", title: "QA-TEST first save" }] : [] });
  if (u.pathname === "/search") return Response.json({ executed: true, rows: [], node_hits: [
    { id: "concept:unrelated", title: "Unrelated node", matched_in: "graph" }], arms: "fts" });
  return Response.json({ executed: true, domains: [], unfiled: [], candidates: [], nodes: [], edges: [] });
};
const tools = await import(pathToFileURL(join(output, "tools.mjs")));
const topbar = await import(pathToFileURL(join(output, "topbar.mjs")));
const editorModule = await import(pathToFileURL(join(output, "doc-editor.mjs")));
document.body.innerHTML = '<main id="host"><div id="body"></div><footer id="foot"></footer></main>';
let closed = false;
const sheet = { el: document.querySelector("#host"), body: document.querySelector("#body"),
  foot: document.querySelector("#foot"), close: () => { closed = true; } };
const cleanup = tools.mountTools(sheet);
await until(() => document.querySelector("#tl-done"));
const title = document.querySelector("#ed-title");
const body = document.querySelector(".cm-content");
assert.ok(body, "real CodeMirror must be mounted");
document.querySelector("#tl-done").click();
await until(() => !document.querySelector("#ed-validation").hidden);
assert.equal(puts.length, 0);
assert.equal(closed, false);
title.value = "   ";
title.dispatchEvent(new Event("input", { bubbles: true }));
document.querySelector("#tl-done").click();
await pause();
assert.equal(puts.length, 0);
assert.equal(closed, false);
title.value = "  QA-TEST first save  ";
title.dispatchEvent(new Event("input", { bubbles: true }));
body.querySelector(".cm-line").textContent = "   ";
body.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: "   " }));
document.querySelector("#tl-done").click();
await pause();
assert.equal(puts.length, 0, "whitespace body must not be submitted");
assert.equal(closed, false);
body.querySelector(".cm-line").textContent = "Typed body  ";
body.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: "Typed body  " }));
document.querySelector("#tl-done").click();
document.querySelector("#tl-done").click();
await until(() => puts.length === 1);
assert.equal(document.querySelector("#tl-done").disabled, true);
assert.equal(puts[0].title, "QA-TEST first save");
assert.equal(puts[0].text, "Typed body  ");
fail = true;
release();
await until(() => !document.querySelector("#tl-done").disabled);
assert.equal(closed, false);
assert.ok(document.querySelector("#ed-status").textContent.includes("Couldn't save"));
assert.equal(body.textContent, "Typed body  ");
fail = false;
document.querySelector("#tl-done").click();
await until(() => puts.length === 2);
release();
await until(() => document.querySelector("#tl-open"));
assert.ok(document.querySelector("#tl-result").textContent.includes("Saved: QA-TEST first save"));
assert.equal(closed, false);
let opened = "";
topbar.setViewHooks({ openDoc: (id) => { opened = id; } });
document.querySelector("#tl-open").click();
assert.equal(closed, true);
assert.equal(opened, "test-doc");
cleanup();
await editorModule.disposeEditorsIn(sheet.body);

document.body.innerHTML += '<input id="tb-q"><div id="tb-results"></div>';
topbar.mountTopbar();
const q = document.querySelector("#tb-q");
q.value = "QA-TEST first save";
q.dispatchEvent(new Event("input", { bubbles: true }));
await until(() => document.querySelector(".tb-row"));
assert.ok(document.querySelector(".tb-row").textContent.includes("QA-TEST first save"));
opened = "";
document.querySelector(".tb-row").dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
assert.equal(opened, "test-doc");
// VIN-349: 빈 제목을 명시해서 지우는 것과 제목을 아예 보내지 않는 것은 다르다.
const { api } = await import(pathToFileURL(join(output, "api.mjs")));
globalThis.fetch = async (_url, opts = {}) => {
  puts.push(JSON.parse(opts.body));
  return Response.json({ executed: true, doc_id: "test-doc" });
};
await api.docPut("tenant:local", "body", "test-doc", "  Revised title  ");
assert.equal(puts.at(-1).title, "Revised title");
await api.docPut("tenant:local", "body", "test-doc", "   ");
assert.equal(puts.at(-1).title, "");
await api.docPut("tenant:local", "body", "test-doc");
assert.equal(Object.hasOwn(puts.at(-1), "title"), false);
api.docGet = async () => ({ executed: true, text: "Original body", title: "  Existing title  ", content_hash: "existing-hash" });
const existing = document.createElement("div"); document.body.append(existing);
const existingEditor = await editorModule.mountDocEditor(existing, {
  owner: "tenant:local", docId: "test-doc", initial: "Original body",
  title: "  Existing title  ", contentHash: "existing-hash", autoFocus: false,
});
const existingTitle = existing.querySelector("#ed-title");
assert.equal(existingTitle.value, "Existing title");
existingTitle.value = "  Revised existing title  ";
existingTitle.dispatchEvent(new Event("input", { bubbles: true }));
assert.equal(await existingEditor.saveNow(), true);
assert.equal(puts.at(-1).title, "Revised existing title");
assert.equal(existingTitle.value, "Revised existing title");
await existingEditor.dispose();
dom.window.close();
console.log("create-form-check: blank/whitespace, first body, single-flight, retained failure, success/open, exact-title Find, trimmed and explicitly empty title serialization passed");
