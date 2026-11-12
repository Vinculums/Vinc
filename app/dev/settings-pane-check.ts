/** 설정 본문의 늦은 그리기 — 브라우저 없이 돈다.
 *
 *     node --experimental-strip-types app/dev/settings-pane-check.ts
 *
 *  설정의 본문 칸(`#settings-body`)은 시트만큼 산다. 그래서 늦게 끝나는 일(계정 연결 · 키 확인)이
 *  끝났을 때 사람이 이미 다른 분류로 옮겼다면, 그 일은 아무것도 그리지 않아야 한다 — 그리면 레일은
 *  읽기를 가리키는데 본문은 계정이 된다. `surfaces/settings.ts` 의 실제 소스(표 `paneToken` 과 계정
 *  분류의 연결 · 키 확인)를 잘라 가짜 요소 위에서 돌린다. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

const settings = readFileSync(new URL("../src/surfaces/settings.ts", import.meta.url), "utf8");
const js = (src: string) => ts.transpileModule(src, { compilerOptions: {
  target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
const slice = (from: string, to: string) => {
  const i = settings.indexOf(from), j = settings.indexOf(to, i);
  assert.ok(i >= 0 && j > i, `${from} … ${to}`);
  return settings.slice(i, j);
};
const settle = () => new Promise((r) => setImmediate(r));

test("표는 분류를 바꾸면(본문을 다시 그리면) 꺼지고, 시트가 닫혀도 꺼진다", () => {
  const sheet = { closed: false };
  const ctx = vm.createContext({ sheet, out: {} as any });
  vm.runInContext(js(slice("let paneGen = 0;", "\n\n")) + `
    out.token = paneToken; out.bump = () => { paneGen += 1; };`, ctx);
  const { token, bump } = (ctx as any).out;
  bump();
  const a = token();
  assert.equal(a(), true);
  bump();                       // renderBody: 다른 분류(또는 같은 분류를 다시) 그린다
  assert.equal(a(), false);
  const b = token();
  assert.equal(b(), true);
  sheet.closed = true;
  assert.equal(b(), false);
});

/** 계정 분류(로컬 모드 · 미연결)의 연결 흐름. 연결이 끝나기 전에 다른 분류로 옮긴다. */
function connectFixture() {
  let finish: (r: string) => void = () => {}, live = true, redraws = 0;
  const handlers: Record<string, () => void> = {};
  const result = { textContent: "" };
  const box = { innerHTML: "", querySelector: (id: string) => ({
    "#acct-connect": { addEventListener: (_n: string, fn: () => void) => { handlers.connect = fn; } },
    "#acct-result": result,
  } as any)[id] };
  const state: any = {
    body: { querySelector: () => box }, accountWho: () => "", hasAccount: () => false, esc: (x: string) => x,
    U: { acctOffline: "off", acctConnectHint: "hint", acctConnect: "connect", acctConnecting: "waiting",
         acctExpired: "expired", acctFail: "failed" },
    connectAccount: (onWait?: () => void) => { onWait?.(); return new Promise((r) => { finish = r; }); },
    renderBody: () => { redraws += 1; }, alive: () => live, exports: {},
  };
  vm.createContext(state);
  vm.runInContext(js(slice("type Row = {", "function paneHead")), state);
  vm.runInContext(js(slice('const acctBox = body.querySelector<HTMLElement>("#acct-box")', "\n    drawAcct();\n  }")
    + "\n    drawAcct();"), state);
  return { handlers, result, leave: () => { live = false; }, finish: (r: string) => finish(r), redraws: () => redraws };
}

test("연결이 다른 분류로 옮긴 뒤 끝나면 계정 본문을 다시 그리지 않는다", async () => {
  const s = connectFixture();
  s.handlers.connect(); await settle();
  assert.equal(s.result.textContent, "waiting");
  s.leave();                    // 사람이 읽기 분류를 눌렀다
  s.finish("connected"); await settle();
  assert.equal(s.redraws(), 0, "다른 분류 위에 계정 본문을 그렸다");
});

test("같은 분류에 머물면 연결이 끝난 뒤 계정 본문을 다시 그린다(되돌림 검사)", async () => {
  const s = connectFixture();
  s.handlers.connect(); await settle();
  s.finish("connected"); await settle();
  assert.equal(s.redraws(), 1);
});

test("만료 문구도 떠난 뒤에는 쓰지 않는다", async () => {
  const s = connectFixture();
  s.handlers.connect(); await settle();
  s.leave(); s.finish("expired"); await settle();
  assert.equal(s.result.textContent, "waiting");
});
