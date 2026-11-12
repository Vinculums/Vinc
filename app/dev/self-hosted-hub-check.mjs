/** VIN-962: 셀프호스트 허브에서 웹 Vinc 가 돈다(계획 vinc.plan.self-hosted-hub-web-2026-11-11 S3).
 *
 *  허브를 셀프호스트로 띄운 뒤(VINC_SELF_HOSTED=on, 번들은 VITE_BASE=/app/ VITE_SELF_HOSTED=1 빌드):
 *    VINC_PLAYWRIGHT=<playwright 모듈> VINC_CHECK_BASE=http://127.0.0.1:18790 \
 *    VINC_CHECK_PASSWORD=<로그인 비밀번호> node app/dev/self-hosted-hub-check.mjs
 *
 *  보는 것: /app/ 이 로그인으로 보낸다 → 로그인 → 사이드바와 그래프 화면 → 문서를 쓰고 고친 뒤 화면에서
 *  읽는다 → 계정 전용 칸(사이트 머리·공간 전환·팀으로 옮기기·알림·계정 관리 링크)이 없다 → 로그아웃.
 *  허브 밖 호스트로 나간 요청이 하나라도 있으면 실패한다. */
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VINC_PLAYWRIGHT || "playwright");
const base = process.env.VINC_CHECK_BASE || "http://127.0.0.1:18790";
const password = process.env.VINC_CHECK_PASSWORD;
assert.ok(password, "VINC_CHECK_PASSWORD is required");
const hub = new URL(base).host;

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
const requests = [];
const foreign = [];
page.on("request", (r) => {
  const u = new URL(r.url());
  if (u.protocol === "data:" || u.protocol === "blob:") return;
  requests.push(`${r.method()} ${u.pathname}`);
  if (u.host !== hub) foreign.push(r.url());
});
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
const steps = [];
const step = (s) => { steps.push(s); console.log("ok", s); };

await page.goto(`${base}/app/`);
await page.waitForURL(/\/login/, { timeout: 15000 });
step("unsigned /app/ goes to /login");

await page.fill("#pw", password);
await Promise.all([page.waitForURL(/\/app\//, { timeout: 15000 }), page.click("button[type=submit]")]);
await page.waitForSelector("#lnb", { state: "visible", timeout: 30000 });
step("signed in, sidebar visible");

await page.goto(`${base}/app/#view`);
await page.waitForSelector("#lnb-tree, #lnb .lnb-sec, #lnb [data-rail]", { timeout: 30000 }).catch(() => {});
await page.waitForTimeout(1500);
const viewOk = await page.evaluate(() => location.hash.startsWith("#view") && !!document.querySelector("#shell, main, #app"));
assert.ok(viewOk, "view surface did not open");
step("graph view open");

// 문서 왕복: 앱과 같은 길(같은 출처, 쿠키 세션, CSRF 더블서브밋)로 쓰고 고친 뒤, 화면이 연다.
const id = `vin962.check.${Date.now()}`;
const put = (text) => page.evaluate(async ([id, text]) => {
  const m = document.cookie.match(/(?:^|;\s*)(?:__Host-csrf|vinc-csrf)=([^;]+)/);
  const r = await fetch("/api/graph/doc/put", { method: "POST", credentials: "include",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": m ? decodeURIComponent(m[1]) : "" },
    body: JSON.stringify({ id, text, title: "Self-hosted check" }) });
  return { status: r.status, body: await r.json().catch(() => null) };
}, [id, text]);
let out = await put("# Self-hosted check\n\nfirst line on the hub");
assert.equal(out.status, 200, JSON.stringify(out));
out = await put("# Self-hosted check\n\nedited line on the hub");
assert.equal(out.status, 200, JSON.stringify(out));
const read = await page.evaluate(async (id) => {
  const r = await fetch(`/api/graph/doc?id=${encodeURIComponent(id)}`, { credentials: "include" });
  return { status: r.status, body: await r.json().catch(() => null) };
}, id);
assert.equal(read.status, 200, JSON.stringify(read));
assert.match(String(read.body?.text || ""), /edited line on the hub/);
await page.goto(`${base}/app/#view/doc/${encodeURIComponent(id)}`);
await page.waitForFunction(() => document.body.innerText.includes("edited line on the hub"), null, { timeout: 30000 });
step("document written, edited and shown");

// 계정 전용 칸이 없다.
const shown = await page.evaluate(() => ({
  gnb: !!document.querySelector("#site-gnb, script[src$='/assets/gnb.js']"),
  spaceHead: !!document.querySelector("#tb-space .space-switch"),
}));
assert.deepEqual(shown, { gnb: false, spaceHead: false });
await page.click("#rail-settings");
await page.click('.settings-cat[data-cat="account"]');
await page.waitForSelector("#sh-signout", { timeout: 10000 });
const account = await page.evaluate(() => ({
  conversion: !!document.getElementById("topic-conversion"),
  push: !!document.getElementById("push-box"),
  manage: !!document.querySelector('a[href="/account/"]'),
}));
assert.deepEqual(account, { conversion: false, push: false, manage: false });
step("no account-only controls");

await Promise.all([page.waitForURL(/\/login/, { timeout: 15000 }), page.click("#sh-signout")]);
// 로그인 화면의 CSP 는 fetch 를 막으므로 같은 쿠키 상자의 요청으로 본다.
const after = (await page.context().request.get(`${base}/api/me`)).status();
assert.equal(after, 401);
step("signed out");

await browser.close();
const hosts = [...new Set(requests.map((r) => r.split(" ")[1].split("/")[1] || "/"))].sort();
console.log(JSON.stringify({ steps, requests: requests.length, top_paths: hosts, foreign, page_errors: errors }, null, 1));
assert.deepEqual(foreign, [], `requests left the hub: ${foreign.join(", ")}`);
