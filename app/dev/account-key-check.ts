import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

// 실제 모듈을 실행하되 셸과 저장소만 합성 값으로 바꾼다. 실제 OS 키를 읽거나 쓰지 않는다.
const source = readFileSync(new URL("../src/account-connect.ts", import.meta.url), "utf8")
  .replace(/^import .* from "\.\/api";\r?\n/m, "");
const settings = readFileSync(new URL("../src/surfaces/settings.ts", import.meta.url), "utf8");
function run(source, state) {
  vm.runInContext(ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS,
  } }).outputText, state);
}
function fixture({ available = true, fail = false, legacy = "fixture-prior", held = "" } = {}) {
  const values = new Map(legacy ? [["vinc.sync.key", legacy]] : []);
  const calls = [], events = [];
  let osKey = held, forgotten = 0, shouldFail = fail;
  const localStorage = { getItem: k => values.get(k) ?? null,
    setItem: (k, v) => { values.set(k, v); }, removeItem: k => { values.delete(k); } };
  const state = { exports: {}, localStorage, cfg: {},
    shellInvoke: async () => available ? async (command, args) => {
      calls.push(command);
      if (command === "secret_available") return true;
      if (command === "secret_get") return osKey;
      if (command === "secret_set") {
        if (shouldFail) throw new Error("fixture_store_unavailable");
        osKey = args.value; return null; // Tauri 저장 성공은 void/null이다.
      }
      throw new Error("unexpected fixture command");
    } : null,
    forgetMemberKey: () => { forgotten++; },
    CustomEvent: class { constructor(type) { this.type = type; } },
    dispatchEvent: e => { events.push(e.type); },
  };
  vm.createContext(state); run(source, state);
  return { api: state.exports, values, localStorage, calls, events,
    get forgotten() { return forgotten; }, get osKey() { return osKey; },
    retry: () => { shouldFail = false; } };
}

test("OS 저장 실패는 이전 키·연결 표식·캐시를 보존하고 성공 이벤트를 보내지 않는다", async () => {
  const s = fixture({ fail: true, held: "fixture-os-prior" });
  await assert.rejects(s.api.setAccountKey("fixture-new"), /fixture_store_unavailable/);
  assert.equal(s.values.get("vinc.sync.key"), "fixture-prior");
  assert.equal(s.osKey, "fixture-os-prior");
  assert.equal(s.values.has("vinc.sync.linked"), false);
  assert.equal(s.forgotten, 0); assert.deepEqual(s.events, []);
  s.retry(); await s.api.setAccountKey("fixture-new");
  assert.equal(s.osKey, "fixture-new"); assert.equal(s.values.has("vinc.sync.key"), false);
  assert.equal(s.values.get("vinc.sync.linked"), "1");
  assert.equal(s.forgotten, 1); assert.deepEqual(s.events, ["vinc:linked"]);
});
test("이미 연결된 계정의 저장 실패도 연결 표식을 그대로 둔다", async () => {
  const s = fixture({ fail: true }); s.values.set("vinc.sync.linked", "1");
  await assert.rejects(s.api.setAccountKey("fixture-new"));
  assert.equal(s.values.get("vinc.sync.linked"), "1"); assert.equal(s.forgotten, 0);
});
test("레거시 이행 실패는 키를 보존하고 다음 읽기로 다시 이행한다", async () => {
  const s = fixture({ fail: true });
  await assert.rejects(s.api.accountKey(), /fixture_store_unavailable/);
  assert.equal(s.values.get("vinc.sync.key"), "fixture-prior");
  s.retry(); assert.equal(await s.api.accountKey(), "fixture-prior");
  assert.equal(s.osKey, "fixture-prior"); assert.equal(s.values.has("vinc.sync.key"), false);
});
test("저장된 OS 키를 확인한 경우에만 레거시 잔재를 지운다", async () => {
  const s = fixture({ held: "fixture-held" });
  assert.equal(await s.api.accountKey(), "fixture-held");
  assert.equal(s.values.has("vinc.sync.key"), false); assert.equal(s.calls.includes("secret_set"), false);
});
test("셸 저장소가 없는 프로파일 경로도 저장 이후에 성공을 알린다", async () => {
  const s = fixture({ available: false }); await s.api.setAccountKey("fixture-new");
  assert.equal(await s.api.accountKey(), "fixture-new");
  assert.equal(s.values.get("vinc.sync.linked"), "1"); assert.deepEqual(s.events, ["vinc:linked"]);
});

function manualFixture(setAccountKey) {
  const handlers = {}, input = { value: "fixture-new", addEventListener: (name, fn) => { handlers[name] = fn; } };
  const note = { textContent: "", hidden: true }, values = new Map([["vinc.sync.email", "fixture@fixture.invalid"]]);
  const state = { body: { querySelector: id => id === "#s-sync-key-said" ? note : { addEventListener: (_name, fn) => { handlers.click = fn; } } },
    keyInput: input, U: { syncKeySaved: "saved", syncKeySaveFailed: "failed" }, setAccountKey,
    localStorage: { removeItem: k => values.delete(k) } };
  vm.createContext(state);
  const from = settings.indexOf('const keySaid = body.querySelector<HTMLElement>("#s-sync-key-said")');
  const to = settings.indexOf("\n  }", from);
  assert.ok(from >= 0 && to > from); run(settings.slice(from, to), state);
  return { handlers, input, note, values };
}
test("수동 입력은 저장 실패를 표시하고 입력값과 이전 계정 표시를 보존한다", async () => {
  const s = manualFixture(async () => { throw new Error("fixture_failure"); });
  await s.handlers.click(); assert.equal(s.note.textContent, "failed"); assert.equal(s.note.hidden, false);
  assert.equal(s.input.value, "fixture-new"); assert.equal(s.values.has("vinc.sync.email"), true);
});
test("수동 입력의 중복 이벤트를 막고 저장 완료 뒤에만 성공을 표시한다", async () => {
  let done, writes = 0;
  const s = manualFixture(() => { writes++; return new Promise(resolve => { done = resolve; }); });
  const pending = s.handlers.click(); await s.handlers.change();
  assert.equal(writes, 1); assert.equal(s.note.textContent, ""); assert.equal(s.input.value, "fixture-new");
  done(); await pending;
  assert.equal(s.note.textContent, "saved"); assert.equal(s.input.value, ""); assert.equal(s.values.has("vinc.sync.email"), false);
});
test("저장을 기다리며 새로 입력한 키는 이전 저장 완료가 지우지 않는다", async () => {
  let done;
  const s = manualFixture(() => new Promise(resolve => { done = resolve; }));
  const pending = s.handlers.click(); s.input.value = "fixture-next"; done(); await pending;
  assert.equal(s.input.value, "fixture-next");
});
