import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

const settings = readFileSync(new URL("../src/surfaces/settings.ts", import.meta.url), "utf8");
const api = readFileSync(new URL("../src/api.ts", import.meta.url), "utf8");
function slice(source, from, to) {
  const a = source.indexOf(from), b = source.indexOf(to, a);
  assert.ok(a >= 0 && b > a); return source.slice(a, b);
}
function run(source, state) {
  vm.runInContext(ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } }).outputText, state);
}
function fixture(rows, error = false, communities = []) {
  const options = [{ value: "", disabled: false }], handlers = {};
  const pick = { value: "", appendChild: o => options.push(o), addEventListener: (type, cb) => { handlers[type] = cb; },
    get selectedOptions() { return [options.find(o => o.value === this.value)]; } };
  const note = { textContent: "" }, cfg = { team: "active" }, calls = [];
  const state = { cfg, body: { querySelector: id => id === "#space-pick" ? pick : note },
    document: { createElement: () => ({ appendChild(o) { (this.children ??= []).push(o); } }) },
    U: { spaceNone: "none", spacePending: "pending", spaceSuspended: "paused", spaceActivationHint: "activate on account", spaceListFailed: "failed", spaceCommunities: "Communities", spaceCommunityReadOnly: "read only" },
    location: { reload: () => calls.push("reload") },
    // 스페이스 선택기는 떠나기 전에 결과를 잠깐 보관한 뒤 전환한다(`retainBeforeSwitch`).
    retainBeforeSwitch: async () => { calls.push("retain"); return true; },
    communitiesApi: { list: async () => communities },
    isCommunitySpace: team => Boolean(team && team.startsWith("community:")),
    teamsApi: { list: async opts => { assert.equal(opts.throwOnError, true); if (error) throw new Error("offline"); return rows; } },
  };
  vm.createContext(state);
  run(slice(settings, 'const pick = body.querySelector<HTMLSelectElement>("#space-pick")', "\n      return;"), state);
  return { pick, note, cfg, options, handlers, calls };
}
const settle = () => new Promise(resolve => setImmediate(resolve));
test("pending and suspended teams are visible but cannot become the active graph", async () => {
  const s = fixture([ { id: "active", name: "Active", status: "active" },
    { id: "pending", name: "Pending", status: "pending" }, { id: "paused", name: "Paused", status: "suspended" },
    { id: "deleted", name: "Deleted", status: "deleted" } ]);
  await settle();
  assert.deepEqual(s.options.map(o => o.value), ["", "active", "pending", "paused"]);
  assert.equal(s.options[1].selected, true); assert.equal(s.options[2].disabled, true); assert.equal(s.options[3].disabled, true);
  s.pick.value = "pending"; await s.handlers.change(); assert.equal(s.cfg.team, "active"); assert.equal(s.calls.length, 0);
  s.pick.value = ""; await s.handlers.change(); assert.equal(s.cfg.team, ""); assert.deepEqual(s.calls, ["retain", "reload"]);
  assert.equal(s.pick.disabled, true, "the picker is locked while the left Space is retained");
  assert.equal(s.note.textContent, "activate on account");
});
test("joined communities are offered as their own group and can become the active space", async () => {
  const s = fixture([], false, [{ id: "community:c-1", name: "Physics", role: "member" }]);
  await settle();
  const group = s.options.find(o => o.label === "Communities");
  assert.ok(group, "a Communities group is added");
  assert.deepEqual(group.children.map(o => [o.value, o.textContent]), [["community:c-1", "Physics"]]);
  s.pick.value = "community:c-1"; s.options.push(...group.children); await s.handlers.change();
  assert.equal(s.cfg.team, "community:c-1"); assert.deepEqual(s.calls, ["retain", "reload"]);
});
test("list failure is distinguished from successful empty membership", async () => {
  const failed = fixture([], true), empty = fixture([]); await settle();
  assert.equal(failed.note.textContent, "failed"); assert.equal(empty.note.textContent, "none");
});
test("strict settings lookup propagates failures without changing optional consumers", async () => {
  const state = { accountMode: () => true, call: async () => { throw new Error("offline"); } };
  vm.createContext(state);
  run((slice(api, "export const teamsApi =", "\n};") + "\n};").replace("export const", "var"), state);
  await assert.rejects(state.teamsApi.list({ throwOnError: true }), /offline/);
  assert.equal((await state.teamsApi.list()).length, 0);
});
test("strict lookup rejects malformed successful list responses", async () => {
  const state = { accountMode: () => true, call: async () => ({}) };
  vm.createContext(state);
  run((slice(api, "export const teamsApi =", "\n};") + "\n};").replace("export const", "var"), state);
  await assert.rejects(state.teamsApi.list({ throwOnError: true }), /team_list_unavailable/);
});
