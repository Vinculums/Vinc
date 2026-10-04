/** VIN-362: render batch outcomes from the document write response, including older servers.
 * VINC_TEST_MODULES=<scratch>/package.json node app/dev/import-outcome-check.mjs
 * Synthetic transport exercises the real Intake and import pipeline; native acceptance is separate.
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
const output = await mkdtemp(join(tmpdir(), "vin362-outcome-"));
globalThis.fixtureInvoke = async command => command === "secret_available" ? true
  : command === "secret_get" ? "synthetic-member-key" : null;
await esbuild.build({ stdin: { contents: 'export * from "./surfaces/intake"; export { cfg, dataMode } from "./api";',
  resolveDir: join(root, "src"), loader: "ts" }, bundle: true, format: "esm", platform: "browser",
  outfile: join(output, "outcome.mjs"), loader: { ".css": "empty", ".svg": "text" }, logLevel: "silent",
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
const { cfg, dataMode, renderIntake } = await import(pathToFileURL(join(output, "outcome.mjs")));
cfg.token = "synthetic-session"; cfg.lang = "en"; cfg.team = "synthetic-team"; dataMode.set("account");
let outcomes = [], writes = [], listUnavailable = false;
globalThis.fetch = async (url, opts = {}) => {
  let path = new URL(url).pathname, body = opts.body ? JSON.parse(opts.body) : {};
  if (path === "/space/proxy") { assert.equal(body.team, cfg.team); path = body.path; body = body.body || {}; }
  if (path === "/docs") return Response.json(listUnavailable ? { executed: false } : { executed: true, docs: [] });
  if (path === "/doc/put" || path === "/doc/put-file") {
    writes.push(body); const result = outcomes.shift(); assert.ok(result, "unexpected write");
    return Response.json({ doc_id: `doc-${writes.length}`, ...result });
  }
  if (path === "/doc") return Response.json({ executed: false });
  if (path === "/domains") return Response.json({ executed: true, rows: [] });
  throw new Error(`Unexpected synthetic request: ${path}`);
};
const el = document.createElement("div"); document.body.append(el);
async function batch(results, names = results.map((_, i) => `file-${i}.md`)) {
  outcomes = [...results]; writes = [];
  renderIntake(el, { withHeader: false, files: names.map(name => ({ name, size: 12,
    text: async () => "document text", arrayBuffer: async () => new Uint8Array([1, 2, 3]).buffer })) });
  for (let i = 0; i < 120 && !el.querySelector(".success-stage"); i++) await new Promise(r => setTimeout(r, 10));
  assert.ok(el.querySelector(".success-stage"), "import did not finish");
  assert.equal(outcomes.length, 0); return el.querySelector("h2").textContent;
}
assert.equal(await batch([{ executed: true, created: true }, { executed: true, created: true }]), "Added 2 documents.");
assert.equal(await batch([{ executed: true, created: false, skipped: true }, { executed: true, created: false, skipped: true }]), "2 documents are unchanged.");
listUnavailable = true; // The write result still classifies a refresh without a local origin match.
assert.equal(await batch([{ executed: true, created: true }, { executed: true, created: false }]), "Added 1 document. Refreshed 1 document.");
assert.equal(await batch([{ executed: true, created: false }], ["report.pdf"]), "Refreshed 1 document.");
assert.ok(writes[0].data_b64);
assert.equal(await batch([{ executed: true }, { executed: true, skipped: true }]), "1 document is unchanged. Saved 1 document.");
assert.equal(await batch([{ executed: true, created: false }, { executed: false, error: "synthetic rejection" }]), "Refreshed 1 document.");
assert.match(el.querySelector(".success-failed").textContent, /file-1.md/);
cfg.lang = "ko";
assert.equal(await batch([{ executed: true, created: true }, { executed: true, created: false }, { executed: true, skipped: true }]),
  "문서 1개를 추가했어요. 문서 1개를 갱신했어요. 문서 1개는 내용이 같아 그대로 두었어요.");
console.log("PASS Intake outcomes: new, repeated unchanged, mixed refresh, missing origin index, binary, older server, partial rejection and EN/KO.");
dom.window.close(); process.exit(0);
