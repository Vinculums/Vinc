/** VIN-352: Intake and existing candidate review with synthetic transport fixtures.
 * VINC_TEST_MODULES=<scratch>/package.json node app/dev/import-review-check.mjs
 * Preview domain is the A companion contract. This is not native acceptance.
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
const output = await mkdtemp(join(tmpdir(), "vin352-review-"));
globalThis.fixtureInvoke = async command => command === "secret_available" ? true
  : command === "secret_get" ? "synthetic-member-key" : null;
await esbuild.build({ stdin: { contents: 'export * from "./surfaces/intake"; export { cfg, dataMode } from "./api"; export { offerExtraction } from "./extract-list";',
  resolveDir: join(root, "src"), loader: "ts" }, bundle: true, format: "esm", platform: "browser",
  outfile: join(output, "review.mjs"), loader: { ".css": "empty", ".svg": "text" }, logLevel: "silent",
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
globalThis.getComputedStyle = dom.window.getComputedStyle.bind(dom.window);
globalThis.dispatchEvent = dom.window.dispatchEvent.bind(dom.window);
globalThis.addEventListener = dom.window.addEventListener.bind(dom.window);
window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
const { cfg, dataMode, renderIntake, offerExtraction } = await import(pathToFileURL(join(output, "review.mjs")));
cfg.token = "synthetic-session"; cfg.lang = "en"; cfg.team = "synthetic-team"; dataMode.set("account");
let topic = "", legacy = false, verified = true, rejectTopic = false;
let text = "# Alpha\n\n# Beta", title = "Review <document>", writes = [], applies = [], releasePreview;
globalThis.fetch = async (url, opts = {}) => {
  let path = new URL(url).pathname, body = opts.body ? JSON.parse(opts.body) : {};
  if (path === "/space/proxy") {
    assert.equal(body.team, "synthetic-team"); path = body.path; body = body.body || {};
  }
  if (path === "/docs") return Response.json({ executed: true, docs: [] });
  if (path === "/doc/put") {
    writes.push(body);
    if (body.topic && rejectTopic) return Response.json({ executed: false });
    if (body.topic) topic = body.topic;
    return Response.json({ executed: true, doc_id: "doc-review", created: true });
  }
  if (path === "/doc") return Response.json({ executed: true, verified, text, title,
    origin: "review.md", content_time: "2026-10-01" });
  if (path === "/doc/extract") {
    if (body.apply) { applies.push(body); return Response.json({ executed: true,
      applied: { created: body.candidates.length, matched: 0 }, mentions: 3 }); }
    const answer = () => Response.json({ executed: true,
      candidates: [{ title: "Alpha", slug: "alpha" }, { title: "Beta", slug: "beta" }],
      ...(legacy ? {} : { domain: topic }) });
    if (releasePreview === "defer") return new Promise(r => { releasePreview = () => r(answer()); });
    return answer();
  }
  if (path === "/node-context") return Response.json({ executed: false });
  if (path === "/domains") return Response.json({ executed: true, rows: [] });
  throw new Error(`Unexpected synthetic request: ${path}`);
};
async function until(check) {
  for (let i = 0; i < 120; i++) { if (check()) return; await new Promise(r => setTimeout(r, 10)); }
  assert.ok(check(), "review did not settle");
}
const el = document.createElement("div"); document.body.append(el);
const file = { name: "review.md", size: 20, text: async () => text };
function reset() { writes = []; applies = []; localStorage.removeItem("vinc.extract.seen.tenant:local.doc-review"); }
async function importAndReview() {
  reset(); renderIntake(el, { withHeader: false, files: [file] });
  await until(() => el.querySelector('[data-ex="open"]'));
  assert.equal(el.querySelector("#do-extract"), null);
  assert.equal(applies.length, 0);
  assert.equal(el.querySelector("#extract-offer h3").textContent, title);
  assert.equal(el.querySelector("#extract-offer h3 document"), null);
  el.querySelector('[data-ex="open"]').click();
  assert.equal(el.querySelectorAll(".ex-check").length, 2);
}
await importAndReview();
assert.ok(el.querySelector('[data-ex="apply"]').hidden);
assert.ok(el.querySelector(".ex-topic"));
el.querySelector(".ex-title").dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
assert.equal(applies.length, 0);
el.querySelectorAll(".ex-check")[1].checked = false;
el.querySelector(".ex-title").value = "Chosen <concept>";
text = "# Latest canonical text"; title = "Latest title";
el.querySelector(".ex-topic input").value = "qa/review";
el.querySelector(".ex-topic button").click();
await until(() => el.querySelector(".ex-done"));
assert.equal(writes.length, 2);
assert.equal(writes[1].text, text); assert.equal(writes[1].title, title);
assert.equal(writes[1].topic, "qa/review");
assert.equal(Object.hasOwn(writes[1], "origin"), false);
assert.equal(Object.hasOwn(writes[1], "content_time"), false);
assert.deepEqual(applies[0].candidates, [{ title: "Chosen <concept>", one_liner: "" }]);
assert.match(el.querySelector(".ex-done").textContent, /1/);
assert.match(el.querySelector(".ex-done").textContent, /3/);

await importAndReview(); // Companion domain permits review and selected Apply without a topic write.
assert.equal(el.querySelector(".ex-topic"), null);
assert.equal(el.querySelector('[data-ex="apply"]').hidden, false);
el.querySelectorAll(".ex-check")[1].checked = false;
el.querySelector('[data-ex="apply"]').click();
el.querySelector(".ex-title").dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
await until(() => el.querySelector(".ex-done"));
assert.equal(writes.length, 1); assert.equal(applies.length, 1); assert.equal(applies[0].candidates.length, 1);

topic = ""; legacy = true; verified = false;
await importAndReview();
el.querySelector(".ex-topic input").value = "qa/review";
el.querySelector(".ex-topic button").click();
await until(() => el.querySelector(".ex-msg .error-banner"));
assert.equal(writes.length, 1); assert.equal(applies.length, 0);
assert.equal(el.querySelector(".ex-topic button").disabled, false);
verified = true; rejectTopic = true;
el.querySelector(".ex-topic button").click();
await until(() => writes.length === 2 && !el.querySelector(".ex-topic button").disabled);
assert.equal(applies.length, 0); rejectTopic = false;

reset(); releasePreview = "defer";
renderIntake(el, { withHeader: false, files: [file] });
await until(() => typeof releasePreview === "function");
el.querySelector("#add-more").click(); releasePreview(); releasePreview = null;
await new Promise(r => setTimeout(r, 50));
assert.ok(el.querySelector("#pick")); assert.equal(el.querySelector(".ex-list"), null);
assert.equal(applies.length, 0);

const boxes = [document.createElement("div"), document.createElement("div")];
for (const [i, box] of boxes.entries()) {
  document.body.append(box);
  await offerExtraction(box, { owner: "tenant:local", docId: `unique-${i}`, requireTopic: true, setTopic: async () => true });
  box.querySelector('[data-ex="open"]').click();
}
assert.notEqual(boxes[0].querySelector("datalist").id, boxes[1].querySelector("datalist").id);
for (const box of boxes) assert.equal(box.querySelector(".ex-topic input").getAttribute("list"), box.querySelector("datalist").id);
console.log("PASS Intake review: explicit topic before Apply, chosen candidates only, canonical preservation, old-server/refused/unverified safety, stale screen and unique topic lists. Companion integration/native remain separate.");
dom.window.close(); process.exit(0);
