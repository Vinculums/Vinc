/** VIN-361 · VIN-950: the storage location card, successful and rejected mode writes, and your database.
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
// 번들 안의 import.meta.env 는 빈 객체로 둔다: vite 밖에서도 빌드 상수(VITE_*)를 읽는 모듈이 떠야 한다.
await esbuild.build({ define: { "import.meta.env": "{}" }, stdin: { contents: 'export * from "./storage-mode"; export { storage, dataMode } from "./api";',
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
// 셸 흉내: 키링(secret_*)과 사이드카 다시 띄우기. 비밀은 여기 기록만 한다.
const secrets = new Map([["vinc.sync.key", "member-key"]]);
const shellCalls = [];
window.__TAURI_INTERNALS__ = {
  invoke: async (cmd, args) => {
    shellCalls.push(cmd);
    if (cmd === "secret_available") return true;
    if (cmd === "secret_get") return secrets.get(args.name) ?? null;
    if (cmd === "secret_set") { secrets.set(args.name, args.value); return null; }
    if (cmd === "sidecar_restart") return null;
    throw new Error(`unexpected shell command ${cmd}`);
  },
  transformCallback: () => 0,
};
globalThis.fetch = async () => ({ ok: true, json: async () => ({ status: "ok" }) });
Object.defineProperty(globalThis, "location", { value: { protocol: "http:", hostname: "tauri.localhost",
  origin: "http://tauri.localhost", hash: "#view", reload: () => reloads++ }, configurable: true });
globalThis.getComputedStyle = dom.window.getComputedStyle.bind(dom.window);
window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
localStorage.setItem("vinc.lang", "en");
const { storage, dataMode, mountStorage } = await import(pathToFileURL(join(output, "storage.mjs")));
const conf = { mode: "account", data_path: "", default_folder: "/fixture/default",
  suggested_folder: "/fixture/empty", pinned: false, location: null, location_error: null,
  store_open: true, engine: "embedded" };
storage.config = async () => conf;
storage.inspect = async () => ({ ok: true, has_store: false, store_bytes: 0, risks: [] });
let reject = false;
const configWrites = [];
storage.setConfig = async body => {
  configWrites.push(body);
  if ((body.mode || body.location) && reject) return { status: 400, error: "synthetic_rejection" };
  if (body.location) conf.location = { ...body.location };
  else Object.assign(conf, body);
  return { restart_needed: false };
};
const box = document.createElement("div"); document.body.appendChild(box);
async function until(check) {
  for (let n = 0; n < 200; n++) { if (check()) return; await new Promise(r => setTimeout(r, 10)); }
  assert.ok(check(), "storage control did not settle");
}
async function draw(mode, opts) {
  dataMode.set(mode); box.innerHTML = ""; mountStorage(box, opts);
  await until(() => box.querySelector(".sl-loc"));
}
async function choose(loc) {
  box.querySelector("#st-change").click();
  assert.equal(box.querySelector("#st-choose").hidden, false);
  box.querySelector(`[data-pick="${loc}"]`).click();
  assert.equal(box.querySelector(`[data-pick="${loc}"]`).getAttribute("aria-checked"), "true");
}
async function turnOn(fail = false) {
  reject = fail;
  await draw("account");
  await choose("folder");
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
assert.match(box.querySelector(".sl-loc").textContent, /a folder on this device/i);
// 지금 위치인 폴더 카드는 고를 수 있고(다른 폴더로 바꾸기), 계정 카드에서 끈다.
assert.equal(box.querySelector('[data-pick="folder"]').disabled, false);
const folder = conf.data_path;
await choose("account");
reject = true; box.querySelector("#st-keep").click();
await until(() => !box.querySelector("#st-keep").disabled);
assert.equal(dataMode.get(), "local"); assert.equal(reloads, 1);
reject = false; box.querySelector("#st-keep").click();
await until(() => dataMode.get() === "account");
assert.equal(reloads, 2); assert.equal(conf.data_path, folder);
await draw("account");
assert.match(box.querySelector(".sl-loc").textContent, /this account/i);
assert.equal(box.querySelector('[data-pick="account"]').disabled, true);
// 취소는 고르기를 닫고 "위치 바꾸기"를 되살린다.
await choose("folder");
await until(() => box.querySelector("#st-cancel"));
box.querySelector("#st-cancel").click();
assert.equal(box.querySelector("#st-choose").hidden, true);
assert.equal(box.querySelector("#st-change").disabled, false);
// 계정 키 없이 옮기려 하면 그렇다고 말하고, 저장 위치 탭에서 계정 탭으로 가는 버튼을 준다.
let opened = 0;
secrets.delete("vinc.sync.key");
await draw("local", { openAccount: () => opened++ });
await choose("account");
await until(() => box.querySelector("#st-go"));
box.querySelector("#st-go").click();
await until(() => box.querySelector("#st-result button"));
assert.match(box.querySelector("#st-result").textContent, /sign in first/i);
assert.equal(dataMode.get(), "local");
box.querySelector("#st-result button").click();
assert.equal(opened, 1);
secrets.set("vinc.sync.key", "member-key");

// ── 내 데이터베이스 ────────────────────────────────────────────────────────
const PASSWORD = "fixture-db-password";
const check = (over = {}) => ({ ok: true, problem: null, detail: null, reachable: true, auth_ok: true,
  server_version: "2026.01.4", edition: "community", version_supported: true, write_permission: true,
  latency_ms: 2, vinc_schema_version: null, vinc_counts: { nodes: 0, docs: 0 },
  foreign_labels: { count: 0, names: [], nodes: 0 }, writer_lease: null, risks: [], ...over });
let nextCheck = check();
const inspected = [];
storage.inspectDatabase = async body => { inspected.push(body); return nextCheck; };
const moves = [];
let moveResult = { switched: true, copy: { missing: 0, after: { nodes: 3, docs: 1 } } };
storage.move = async body => { moves.push(body); return { job_id: "job-1", status: "queued" }; };
storage.job = async () => ({ status: "done", result: moveResult });
async function connect(uri) {
  await choose("database");
  await until(() => box.querySelector("#sl-uri"));
  const set = (id, v) => { const i = box.querySelector(id); i.value = v; i.dispatchEvent(new window.Event("input")); };
  set("#sl-uri", uri); set("#sl-password", PASSWORD);
  box.querySelector('[data-act="test"]').click();
  await until(() => box.querySelector(".sl-check"));
}
// 막힌 확인: 폼이 남고 바꾸기 버튼은 없다.
nextCheck = check({ server_version: "5.26.2", version_supported: false, risks: ["version_below_floor"] });
await draw("account");
await connect("neo4j+s://legacy.example.com:7687");
assert.ok(box.querySelector("#sl-uri"), "a blocked check keeps the form");
assert.equal(box.querySelector('[data-act="switch"]'), null);
assert.match(box.querySelector(".sl-check").textContent, /2026\.01 or later/);
// 평문 연결: 경고를 받아들여야 바꾼다. 빈 그래프로 시작하면 위치만 적는다.
nextCheck = check({ risks: ["unencrypted_transport"] });
conf.location = { kind: "account" };
await draw("account");
await connect("bolt://db.example.com:7687");
const go = box.querySelector('[data-act="switch"]');
assert.equal(go.disabled, true);
box.querySelector("#sl-accept").click();
assert.equal(go.disabled, false);
box.querySelector('input[name="sl-start"][value="empty"]').click();
assert.match(box.querySelector("#sl-flow-to").textContent, /empty graph/i);
assert.ok(!box.innerHTML.includes(PASSWORD), "the password is not written into the page");
configWrites.length = 0;
go.click();
await until(() => box.querySelector('[data-act="done"]'));
assert.equal(secrets.get("vinc-database:db.example.com/neo4j"), PASSWORD);
const wrote = configWrites.at(-1);
assert.deepEqual(wrote.location, { kind: "database", engine: "neo4j", uri: "bolt://db.example.com:7687",
  database: "neo4j", user: "neo4j", secret_ref: "vinc-database:db.example.com/neo4j" });
assert.equal(wrote.accept_risk, true);
assert.ok(shellCalls.includes("sidecar_restart"), "the sidecar restarts right after the switch");
assert.equal(dataMode.get(), "local");
assert.match(box.textContent, /started an empty graph/i);
box.querySelector('[data-act="done"]').click();
assert.equal(reloads, 3);
// 계정 → 데이터베이스 복사: 계정 키로 받고, 다 왔을 때만 바뀐다.
nextCheck = check();
conf.location = { kind: "account" };
await draw("account");
await connect("neo4j+s://graph.example.com");
box.querySelector('[data-act="switch"]').click();
await until(() => box.querySelector('[data-act="done"]'));
assert.equal(moves.at(-1).from, "account");
assert.equal(moves.at(-1).token, "member-key");
assert.match(box.textContent, /copied 3 nodes and 1 document/i);
// 빠진 것이 있으면 바꾸지 않았다고 말하고 다시 시도를 준다.
moveResult = { switched: false, copy: { missing: 2 } };
conf.location = { kind: "folder", path: "/fixture/folder" };
await draw("local");
await connect("neo4j+s://graph.example.com");
box.querySelector('[data-act="switch"]').click();
await until(() => box.querySelector('[data-act="retry"]'));
assert.equal(moves.at(-1).from, undefined);
assert.match(box.textContent, /not switched/i);
assert.match(box.textContent, /2 items did not arrive/i);
// 데이터베이스 → 폴더: 고르면 옮기고(`/config/move` 의 폴더), 그래프가 있는 폴더에는 붓지 않는다.
conf.location = { kind: "database", engine: "neo4j", uri: "neo4j+s://graph.example.com", database: "vinc",
  user: "vinc_app", secret_ref: "vinc-database:graph.example.com/vinc" };
moveResult = { switched: true, copy: { missing: 0 } };
storage.inspect = async () => ({ ok: true, has_store: true, store_bytes: 4096, risks: [] });
await draw("local");
assert.equal(box.querySelector('[data-pick="database"]').disabled, true);
await choose("folder");
await until(() => box.querySelector("#st-check")?.textContent);
assert.equal(box.querySelector('input[name="st-start"][value="copy"]').disabled, true);
storage.inspect = async () => ({ ok: true, has_store: false, store_bytes: 0, risks: [] });
box.querySelector("#st-path").dispatchEvent(new window.Event("change"));
await until(() => !box.querySelector('input[name="st-start"][value="copy"]').disabled);
box.querySelector('input[name="st-start"][value="copy"]').checked = true;
const before = reloads;
box.querySelector("#st-go").click();
await until(() => reloads > before);
assert.deepEqual(moves.at(-1).to, { kind: "folder", path: "/fixture/empty" });
// 시작할 때 데이터베이스가 안 닿으면 카드가 응답 없음과 다시 시도를 보인다.
conf.location = { kind: "database", engine: "neo4j", uri: "neo4j+s://graph.example.com", database: "vinc",
  user: "vinc_app", secret_ref: "vinc-database:graph.example.com/vinc" };
conf.store_open = false;
await draw("local");
assert.ok(box.querySelector(".sl-loc .sync-dot.down"));
assert.ok(box.querySelector("#st-retry"));
assert.match(box.querySelector("#st-down").textContent, /could not reach graph\.example\.com/i);
dom.window.close();
console.log("PASS: location card, folder and account panels, and your database: blocked, risky, empty, copy, not switched and not answering.");
process.exit(0);
