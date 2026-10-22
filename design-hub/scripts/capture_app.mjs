// 앱 캡처 파이프라인. 로컬에서 도는 앱(vite dev + 사이드카)을 상태별로 PNG 로 남기고 매니페스트를
// 쓴다. Figma 의 `App · *` 보드는 전부 이 PNG 에 대고 그린다(코드가 원천).
//
// 준비(각각 다른 터미널, 리포 루트에서):
//   python -m api.cli serve --data %USERPROFILE%\.vinc-dev\db --host 127.0.0.1 --port 8787 --token devtoken
//   cd app && VINC_SIDECAR=http://127.0.0.1:8787 npx vite --port 5173 --strictPort --host 127.0.0.1
// 실행:
//   node scripts/capture_app.mjs [--only view] [--list] [--no-seed]
//   --app http://tauri.localhost:5173   --sidecar http://127.0.0.1:8787   --token devtoken   --out reports/captures/app
//
// 왜 tauri.localhost 인가: `app/src/api.ts` 의 IN_SHELL 은 호스트명이 tauri.localhost 면 참이다.
// Chromium 은 *.localhost 를 루프백으로 풀고 vite 는 그 Host 를 받으므로, 브라우저에서 데스크탑 셸의
// 분기(첫 실행 시트 · 연결 탭 · 로컬 저장 위치)가 그대로 살아난다. 창 제어(min/max/close)만 빈 손이다.
// 시드: 스토어에 physics 주제가 없으면 스타터 팩 넷을 앱의 가져오기 흐름으로 넣는다(그 자체가 캡처 대상).
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { fileURLToPath } from "node:url";

const HUB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const REPO = path.resolve(HUB, "..");
const argv = process.argv.slice(2);
const flag = (name, dflt) => { const i = argv.indexOf(name); return i >= 0 ? argv[i + 1] : dflt; };
const APP = flag("--app", "http://tauri.localhost:5173").replace(/\/$/, "");
const SIDE = flag("--sidecar", "http://127.0.0.1:8787").replace(/\/$/, "");
const TOKEN = flag("--token", "devtoken");
const OUT = path.resolve(HUB, flag("--out", "reports/captures/app"));
const ONLY = flag("--only", null);
const LIST = argv.includes("--list");
const NOSEED = argv.includes("--no-seed");
const T = new Date().toISOString();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const side = async (p) => { const r = await fetch(SIDE + p, { headers: { Authorization: `Bearer ${TOKEN}` } }); return r.ok ? r.json() : null; };
const sidePost = async (p, body) => { const r = await fetch(SIDE + p, { method: "POST", headers: { Authorization: `Bearer ${TOKEN}`, "Content-Type": "application/json" }, body: JSON.stringify(body) }); if (!r.ok) throw new Error(`${p} ${r.status}`); return r.json(); };

// ── 공통 ───────────────────────────────────────────────────────────────────
const VP = { width: 1280, height: 800 };
// vinc.sidecar.base: 셸 분기(tauri.localhost)는 api.ts 가 8787 을 기본으로 박으므로, --sidecar 로 다른 포트를 주면
// 여기서 앱에도 알려야 한다. 없으면 스크립트는 8788 을 보고 앱은 8787 을 봐서 다른 스토어를 찍는다(2026-09-07 실측).
// 0.9.4 부터 셸은 로그인이 먼저고(firstrun.ts) 볼 그래프를 모드로 정한다(api.ts decideDataMode). 캡처는
// **로그인한 로컬 모드**로 찍는다: 계정 표식(vinc.sync.linked · username)이 있으면 로그인 화면을 건너뛰고,
// vinc.mode=local 이면 사이드카 스토어를 본다. 계정 모드로 두면 모든 조회가 키 없이 계정으로 가서 빈 화면이다.
// ⚠ 모드는 사이드카의 ~/.vinc/config.json 에도 적힌다 — 캡처용 사이드카는 USERPROFILE 을 스크래치로 돌려
// 띄울 것(이 기기의 실제 데스크톱 설정을 덮지 않게).
const OWNER = "tenant:local";
const BASE_LS = { "vinc.sidecar.token": TOKEN, "vinc.sidecar.base": SIDE, "vinc.lang": "en", "vinc.theme": "dark", "vinc.firstrun.seen": "1", "vinc.view.coach.seen": "1", "vinc.owner": OWNER,
  "vinc.mode": "local", "vinc.sync.linked": "1", "vinc.sync.username": "demo" };
const SHELL_INIT = () => {
  // main.ts 는 Tauri 창 핸들을 얻은 뒤에만 dataset.shell 을 켠다. 브라우저에는 창이 없으니 여기서 켠다.
  document.addEventListener("DOMContentLoaded", () => { document.documentElement.dataset.shell = ""; });
};
const sheet = (p) => p.locator(".sheet[role=dialog]").last();
const foot = (p, re) => sheet(p).locator(".sheet-foot button").filter({ hasText: re });
// 관점 세그는 보기 카드가 열렸을 때만 보인다(렌즈 패널과 함께). 열고 고른 뒤 닫는다.
const viewpoint = async (p, name) => {
  const chip = p.locator("#tb-viewpoints button", { hasText: new RegExp(`^${name}$`) });
  if (!(await chip.isVisible().catch(() => false))) { await p.locator("#tb-lens").click(); await chip.waitFor({ state: "visible" }); }
  await chip.click(); await sleep(400);
  if (await p.locator("#view-lens-panel").isVisible().catch(() => false)) await p.keyboard.press("Escape");
  await sleep(1600);
};
const settled = async (p) => { await p.waitForSelector("#view-metrics", { state: "attached" }).catch(() => {}); await sleep(2200); };
// 설정 · 쓰기/가져오기/공유 · GRAPH.md 는 2026-09-22 부터 레일 바닥 설정 단추가 여는 **시트 하나**다
// (surfaces/settings.ts). 옛 상단 바 단추(#tb-tools · #settings-btn · #tb-conv)는 숨어서 눌리지 않는다.
const settingsSheet = async (p, cat, tab) => {
  await p.locator("#rail-settings").click(); await sheet(p).waitFor({ state: "visible" }); await sleep(300);
  if (cat) { await sheet(p).locator(`.settings-cat[data-cat="${cat}"]`).click(); await sleep(400); }
  if (tab) { await sheet(p).locator(`[data-tab="${tab}"]`).click(); await sleep(400); }
};
// 누른 뒤 커서를 타이틀바 빈 곳으로 옮긴다: 단추 위에 남으면 "View and legend" 툴팁이 창 제어 단추를 덮은 채 찍힌다.
const openLens = async (p) => { await p.locator("#tb-lens").click(); await p.waitForSelector("#view-lens-panel", { state: "visible" }); await p.mouse.move(640, 14); await sleep(400); };
// 검색은 접힌 알약으로 시작한다(문서 우선 셸). 입력 칸 · 모드 단추를 쓰기 전에 편다.
const findOpen = async (p) => { const b = p.locator("#tb-find-open"); if (await b.isVisible().catch(() => false)) { await b.click(); await sleep(300); } };
const firstTopic = (p) => p.locator(".lnb-group .lnb-gname").first();
// 주제 줄을 펼친다 — 닫혀 있을 때만 누른다(줄 클릭은 여닫기라 열린 줄을 누르면 닫힌다, #436).
const openGroup = async (p, name) => {
  const g = name ? p.locator(".lnb-group").filter({ has: p.locator(".lnb-gname", { hasText: new RegExp(`^${name}$`) }) }).first() : p.locator(".lnb-group").first();
  if ((await g.getAttribute("aria-expanded")) !== "true") { await g.click(); await sleep(400); }
};
const firstDoc = async (p) => {
  await openGroup(p);
  const doc = p.locator(".lnb-doc:not(.lnb-node):visible").first();
  // 주제 바로 밑에 문서가 없고 하위 주제(.lnb-fold)만 있으면 첫 하위 주제를 편다.
  if (!(await doc.count())) { await p.locator(".lnb-fold:visible").first().click(); await sleep(400); }
  return doc;
};   // 개념 줄(.lnb-node) 아래 문서가 온다(lnb.ts groupRows) — 문서 줄만 고른다
const openFloat = async (p) => {
  // 노드 하나를 주소로 지목한다(#view/trace/<id>): 캔버스 좌표 없이도 float card 가 뜬다.
  const id = await pickNodeId(); if (!id) throw new Error("no node id from the sidecar search");
  await p.goto(`${APP}/#view/trace/${encodeURIComponent(id)}`, { waitUntil: "load" }); await settled(p);
  await p.waitForSelector(".float-card", { state: "visible", timeout: 15000 }); await sleep(600);
};
let NODE_ID = null;
async function pickNodeId() {
  // nav-tree 가 문서마다 node_id 를 준다. physics 의 뿌리 개념 하나를 고른다.
  if (NODE_ID) return NODE_ID;
  const r = await side("/nav-tree?owner=tenant:local");
  const doms = r?.domains || [];
  const pick = (d) => (d?.docs || []).find((x) => x.node_id && x.depth === 0)?.node_id;
  NODE_ID = pick(doms.find((d) => d.domain === "physics")) || doms.map(pick).find(Boolean) || null;
  return NODE_ID;
}
async function pickNodeIds(n = 10) {
  const r = await side("/nav-tree?owner=tenant:local");
  return (r?.domains || []).flatMap((d) => (d.docs || []).filter((x) => x.node_id && x.depth === 0).map((x) => x.node_id)).slice(0, n);
}

// ── 시드 ───────────────────────────────────────────────────────────────────
async function seedEvidenceEdge() {
  // 스타터 팩의 관계에는 기록된 근거가 없다. 엣지 상세 화면(근거 있는 관계 태그 → docpane)을 찍으려면
  // 근거가 붙은 관계가 하나는 있어야 하므로, physics 뿌리 개념 둘 사이에 관계 하나를 근거와 함께 둔다.
  const r = await side("/nav-tree?owner=tenant:local");
  const roots = (d) => (d?.docs || []).filter((x) => x.node_id && x.depth === 0).map((x) => x.node_id);
  const doms = r?.domains || [];
  const ids = roots(doms.find((d) => d.domain === "physics")).length >= 2
    ? roots(doms.find((d) => d.domain === "physics")) : roots(doms.find((d) => roots(d).length >= 2));
  if (ids.length < 2) { console.log("seed: no topic with two roots for the evidence edge"); return; }
  const [a, b] = ids;
  const res = await fetch(`${SIDE}/ingest/fragment`, { method: "POST", headers: { Authorization: `Bearer ${TOKEN}`, "Content-Type": "application/json" },
    body: JSON.stringify({ owner: "tenant:local", nodes: [{ id: a, type: "concept" }, { id: b, type: "concept" }],
      edges: [{ from: a, to: b, type: "relates_to", confidence: 0.82, evidence: "Both are read in the same design review; the second is introduced as the tool the first needs." }] }) });
  console.log("seed: evidence edge", a, "→", b, res.status);
  EV_NODES = [b, a];   // 카드는 관계를 몇 개만 펼쳐 보이므로, 관계가 적은 쪽(b)에서 먼저 찾는다
}
let EV_NODES = [];
async function seed(browser) {
  const d = await side("/domains");
  const have = (d?.rows || []).some((r) => /physics|math|economics|art/.test(r.domain));
  if (have) { console.log("seed: starter packs already present"); await seedEvidenceEdge(); return; }
  console.log("seed: importing the four starter packs through Settings › Data › Import");
  const ctx = await browser.newContext({ viewport: VP, colorScheme: "dark" });
  await ctx.addInitScript((kv) => { for (const [k, v] of Object.entries(kv)) localStorage.setItem(k, v); }, BASE_LS);
  await ctx.addInitScript(SHELL_INIT);
  const p = await ctx.newPage();
  await p.goto(`${APP}/#view`, { waitUntil: "load" }); await settled(p);
  for (const f of ["physics", "math", "economics", "art"]) {
    await settingsSheet(p, "data", "import");
    const [chooser] = await Promise.all([p.waitForEvent("filechooser"), sheet(p).locator("button", { hasText: /file/i }).first().click()]);
    await chooser.setFiles(path.join(REPO, "starter-packs", `vinc-pack-${f}.vincpack`));
    await sleep(1200);
    await foot(p, /import/i).last().click().catch(() => {});
    await p.waitForFunction(() => { const s = [...document.querySelectorAll(".sheet[role=dialog]")].pop(); return s && /new|already|imported/i.test(s.innerText); }, null, { timeout: 120000 }).catch(() => {});
    console.log("seed:", f, "→", (await sheet(p).innerText().catch(() => "")).split("\n").slice(-3).join(" | ").slice(0, 160));
    await p.keyboard.press("Escape"); await sleep(400); await p.keyboard.press("Escape"); await sleep(400);
  }
  await ctx.close();
  await seedEvidenceEdge();
}

// ── 상태 목록 ───────────────────────────────────────────────────────────────
// { id, url, trigger, ls, api:[[method, pathRegex, "abort"|"hang"|{status,json}]], scheme, full, before(page) }
const S = [];
const add = (o) => S.push({ url: "#view", ls: {}, api: [], full: false, ...o });
const V = (id, trigger, before, extra = {}) => add({ id: `view__${id}`, trigger, before: async (p) => { await settled(p); if (before) await before(p); }, ...extra });

V("3d", "load: 3D viewpoint, all topics");
V("2d", "viewpoint 2D", (p) => viewpoint(p, "2D"));
V("timeline", "viewpoint Timeline", (p) => viewpoint(p, "Timeline"));
V("weave", "viewpoint Weave", (p) => viewpoint(p, "Weave"));
// 2026-09-26(#436): 주제 줄 클릭은 접기만 한다. View 를 그 주제로 좁히는 것은 줄 끝의 범위 단추다.
V("topic-scope", "LNB: the topic's scope button narrows View, scope chip appears", async (p) => { const g = p.locator(".lnb-grow").first(); await g.hover(); await g.locator(".lnb-gscope").click(); await sleep(1800); });
V("lnb-collapsed", "LNB: collapse", async (p) => { await p.locator("#tb-shell-toggle").click(); await sleep(600); });
V("lens-options-3d", "lens button: Options tab (3D rows)", openLens);
V("lens-options-2d", "lens: Options for 2D", async (p) => { await viewpoint(p, "2D"); await openLens(p); });
V("lens-options-timeline", "lens: Options for Timeline", async (p) => { await viewpoint(p, "Timeline"); await openLens(p); });
V("lens-options-weave", "lens: Options for Weave", async (p) => { await viewpoint(p, "Weave"); await openLens(p); });
V("lens-legend", "lens: Legend tab", async (p) => { await openLens(p); await p.locator("#view-lens-panel button", { hasText: /legend/i }).first().click(); await sleep(400); });
V("lens-help-open", "lens: '?' explanations expanded", async (p) => { await openLens(p); await p.locator("#view-lens-panel button[aria-label], #view-lens-panel button", { hasText: /\?/ }).first().click().catch(() => {}); await sleep(300); });
V("node-float", "node addressed: float card with causal cone", openFloat);
V("node-float-links", "float card: Links row expanded", async (p) => { await openFloat(p); await p.locator("#c-links").click(); await sleep(900); });
V("node-ask-menu", "float card: Ask the assistant menu", async (p) => { await openFloat(p); await p.locator(".float-card button", { hasText: /copy prompt/i }).first().click(); await sleep(500); });
V("node-detail", "float card: Detail and source: docpane node detail", async (p) => { await openFloat(p); await p.locator("#c-open").click(); await p.waitForSelector("#docpane", { state: "visible" }); await sleep(1000); });
V("node-delete-confirm", "float card: Delete: destructive confirm (not confirmed)", async (p) => { await openFloat(p); await p.locator("#c-del").click(); await p.waitForSelector("[role=alertdialog]", { state: "visible" }); await sleep(300); });
V("edge-detail", "float card link tag with evidence: edge detail in the docpane", async (p) => { // 근거가 기록된 관계는 노드마다 있지는 않다. 후보 노드를 차례로 열어 `.link-tag--ev` 가 있는 첫 노드에서 찍는다.
  for (const id of [...EV_NODES, ...(await pickNodeIds(12))]) {
    await p.goto(`${APP}/#view/trace/${encodeURIComponent(id)}`, { waitUntil: "load" }); await settled(p);
    if (!(await p.locator(".float-card").count())) continue;
    await p.locator("#c-links").click(); await sleep(900);
    const tag = p.locator("#c-rels .link-tag--ev").first();
    if (await tag.count()) { await tag.click(); await p.waitForSelector("#docpane", { state: "visible" }); await sleep(1000); return; }
  }
  throw new Error("none of the first 12 root nodes has a link with recorded evidence"); });
V("reader", "LNB: click a document: reader in the docpane", async (p) => { const d = await firstDoc(p); await d.click(); await p.waitForSelector("#docpane", { state: "visible" }); await sleep(1200); });
// 드릴다운(상태바 'not linked' 셀)은 고아 노드가 있을 때만 버튼이 된다. 스타터 팩 넷은 전부 연결돼 있어
// 시드 스토어에는 고아가 없다. 이 상태의 캡처는 고아가 있는 스토어에서 따로 뜬다(매트릭스에 남긴 갭).
V("topic-context-menu", "LNB: right-click a topic", async (p) => { await firstTopic(p).click({ button: "right" }); await sleep(300); });
V("topic-rename-prompt", "LNB: right-click a topic, Rename: text prompt", async (p) => { await firstTopic(p).click({ button: "right" }); await sleep(300); await p.locator("[role=menu] button, .ctx button, .menu button").filter({ hasText: /rename/i }).first().click(); await sleep(300); });
V("coachmark", "first visit: coachmark", undefined, { ls: { "vinc.view.coach.seen": "" } });
// 계정 표식을 지우면 셸이 로그인부터 한다 — 2026-09-26 부터 창 전체 화면(이메일 · 또는 · Google).
V("first-run", "first launch in the shell: the full-window sign-in (VIN-128)", undefined, { ls: { "vinc.sync.linked": "", "vinc.sync.username": "" } });
V("light", "theme light", undefined, { ls: { "vinc.theme": "light" }, scheme: "light" });
V("light-lens", "theme light, lens Options", openLens, { ls: { "vinc.theme": "light" }, scheme: "light" });
V("empty-invite", "empty store: empty invite on the canvas, LNB empty", undefined, { ls: { "vinc.owner": "tenant:capture-empty" } });
V("new-node-sheet", "empty invite: Create a node sheet", async (p) => { await p.locator("#surface button, #view-msg button").filter({ hasText: /node/i }).first().click(); await sheet(p).waitFor({ state: "visible" }); await sleep(300); }, { ls: { "vinc.owner": "tenant:capture-empty" } });
V("error", "sidecar unreachable for the map: error box", undefined, { api: [["GET", /\/(view|graph)(\?|$)/, "abort"]] });
V("lnb-failed", "nav-tree 500: LNB failed state with Retry", undefined, { api: [["GET", /\/nav-tree/, { status: 500, json: { error: "boom" } }]] });
V("lnb-loading", "nav-tree pending: LNB loading", undefined, { api: [["GET", /\/nav-tree/, "hang"]] });

// 문서 홈(#home) — 2026-10-22 부터 셸의 첫 화면이다. 그래프는 오른쪽 위 Graph 버튼이 연다.
const HM = (id, trigger, before, extra = {}) => add({ id: `home__${id}`, url: "#home", trigger, before: async (p) => { await sleep(1500); if (before) await before(p); }, ...extra });
const rail = async (p, key) => { await p.locator(`.rail-btn[data-rail="${key}"]`).click(); await sleep(1500); };
HM("list", "load: document home, all documents, recent first");
HM("topic", "LNB: a topic's filter button narrows the list (breadcrumb and chips)", async (p) => { const g = p.locator(".lnb-grow").first(); await g.hover(); await g.locator(".lnb-gscope").click(); await sleep(800); });
HM("filter-empty", "title filter with no match", async (p) => { await p.fill("#home-q", "zzqxv"); await sleep(500); });
HM("doc", "open a document: reading face with the side panel", async (p) => { await p.locator(".home-row").first().click(); await p.waitForSelector(".home-doc", { state: "visible" }); await sleep(1200); });
HM("new", "New document: the writer", async (p) => { await p.locator('[data-act="new"]').first().click(); await sleep(1200); });
HM("agent", "rail Agent: role roster, list narrowed to the role topics", (p) => rail(p, "agent"));
HM("agent-empty", "rail Agent with no roles: New Agent role in the list and the LNB", (p) => rail(p, "agent"),
  { api: [["GET", /^\/roles(\?|$)/, { status: 200, json: { executed: true, roles: [], relations: [], next_offset: null } }]] });
HM("lnb-collapsed", "LNB: collapse to the icon strip", async (p) => { await p.locator("#tb-shell-toggle").click(); await sleep(600); });
HM("empty", "empty store: write or import", undefined, { ls: { "vinc.owner": "tenant:capture-empty" } });

// topbar: search, ask, palette, cheatsheet, tools, GRAPH.md, settings
const TB = (id, trigger, before, extra = {}) => add({ id: `topbar__${id}`, trigger, before: async (p) => { await settled(p); await before(p); }, ...extra });
TB("find-open", "search pill: expanded to the left, Graph and view card stay put", async (p) => { await findOpen(p); await p.mouse.move(640, 400); await sleep(300); });
TB("mode-menu", "search mode dropdown", async (p) => { await findOpen(p); await p.locator("#tb-mode").click(); await sleep(300); });
TB("find-results", "type: results listbox", async (p) => { await findOpen(p); await p.fill("#tb-q", "rate"); await sleep(1200); });
TB("find-empty", "type a query with no hits", async (p) => { await findOpen(p); await p.fill("#tb-q", "zzqxv"); await sleep(1200); });
TB("find-loading", "search pending", async (p) => { await findOpen(p); await p.fill("#tb-q", "rate"); await sleep(600); }, { api: [["GET", /^\/search(\?|$)/,"hang"]] });
TB("find-error", "search 500", async (p) => { await findOpen(p); await p.fill("#tb-q", "rate"); await sleep(1200); }, { api: [["GET", /^\/search(\?|$)/,{ status: 500, json: { error: "boom" } }]] });
TB("browse-sheet", "results footer: All documents sheet, topic layer", async (p) => { await findOpen(p); await p.fill("#tb-q", "rate"); await sleep(1000); await p.locator("#tb-results button", { hasText: /all documents/i }).click(); await sheet(p).waitFor({ state: "visible" }); await sleep(600); });
TB("browse-sheet-docs", "All documents: inside a topic", async (p) => { await findOpen(p); await p.fill("#tb-q", "rate"); await sleep(1000); await p.locator("#tb-results button", { hasText: /all documents/i }).click(); await sheet(p).waitFor({ state: "visible" }); await sleep(500); await sheet(p).locator(".sheet-body [role=button], .sheet-body button, .sheet-body [role=treeitem], .sheet-body li, .sheet-body .row").first().click(); await sleep(600); });
const askMode = async (p) => { await findOpen(p); await p.locator("#tb-mode").click(); await sleep(200); await p.locator("#tb-modemenu [role=menuitemradio], #tb-modemenu button").filter({ hasText: /ask/i }).first().click(); await sleep(300); };
TB("ask-intro", "mode Ask: chat panel intro", async (p) => { await askMode(p); await p.locator("#tb-q").click(); await sleep(400); });
TB("ask-answer", "ask a question that resolves", async (p) => { await askMode(p); await p.fill("#tb-q", "How do we rate limit the public API"); await p.press("#tb-q", "Enter"); await sleep(3500); });
TB("ask-none", "ask something the graph has nothing on", async (p) => { await askMode(p); await p.fill("#tb-q", "zzqxv plorf"); await p.press("#tb-q", "Enter"); await sleep(3000); });
TB("ask-history", "chat head: History sheet", async (p) => { await askMode(p); await p.fill("#tb-q", "How do we rate limit the public API"); await p.press("#tb-q", "Enter"); await sleep(2500); await p.locator("#tb-chat button", { hasText: /history/i }).click(); await sleep(500); });
TB("palette", "Ctrl+Shift+K: command palette", async (p) => { await p.keyboard.press("Control+Shift+K"); await sleep(400); });
TB("palette-filtered", "palette with a filter typed", async (p) => { await p.keyboard.press("Control+Shift+K"); await sleep(300); await p.keyboard.type("sh"); await sleep(300); });
TB("cheatsheet", "?: shortcuts sheet", async (p) => { await p.keyboard.press("Shift+Slash"); await sleep(500); });
// 상태 id(= 보드 이름)는 그대로 두고 여는 길만 바꿨다 — 쓰기 · 가져오기 · 공유는 설정 시트의 Data 분류 탭이다.
TB("tools-create", "Settings › Data: Create tab", async (p) => { await settingsSheet(p, "data", "create"); await sleep(300); });
TB("tools-import", "Settings › Data: Import tab", (p) => settingsSheet(p, "data", "import"));
TB("tools-share", "Settings › Data: Share tab", (p) => settingsSheet(p, "data", "share"));
// ⚠ 300ms 고정 대기로는 못 잡는다. Tools 의 draw() 가 foot 을 비우고 renderShare 가
// teamsApi.list() 와 api.domains() 를 **기다린 뒤에야** 버튼을 다시 단다(app/src/tools.ts:72,180).
// 그래서 Continue 가 나타날 때까지 기다린다. 그 빈 창 자체는 별건이다 —
// [[record:the-tools-share-pane-has-no-action-buttons-until-two-network-calls-return-2026-09-07]].
const shareSheet = async (p) => { await settingsSheet(p, "data", "share"); const go = foot(p, /continue/i).first(); await go.waitFor({ state: "visible", timeout: 30000 }); await go.click(); await sleep(2500); };
TB("share-sheet", "Share › Continue: share as a pack, spec grid and node list", shareSheet);
// ⚠ `share-result` 를 걷었다(2026-09-07). 데스크탑 셸에는 목적지가 `file` 하나뿐이라
// `chooseDestination` 의 `dests.length === 1` 분기가 **저장하고 시트를 닫는다** — 결과
// 화면이 존재하지 않는다. 실측: [공유] 클릭 39ms 뒤 저장이 돌고 800ms 안에 시트가 사라진다.
// 그래서 이 상태는 여태 시트가 닫힌 **뒤의 지도**를 찍고 "ok" 를 답했다. 보드는 도는 코드의
// 캡처인데 그 보드는 아무 코드도 안 그리고 있었다. 목적지 고르기 화면은 스페이스가 둘 이상일
// 때만 뜨므로 웹 세션이 필요하고, 그 픽스처는 이 스크립트에 없다.
// 팩도 고르는 즉시 가져온다(확인 시트 없음). 적재 호출을 붙잡아 '가져오는 중'을 찍고 아무것도 쓰지 않는다.
// 파일은 실무 이름의 사본이다: 화면에 보이는 것은 이름뿐이고, 내용은 적재되지 않는다.
const PACK_SHOWN = path.join(OUT, "api-design-notes.vincpack");
TB("import-pack-sheet", "Import › pick a .vincpack: the pack is being added", async (p) => { fs.copyFileSync(path.join(REPO, "starter-packs", "vinc-pack-math.vincpack"), PACK_SHOWN); await settingsSheet(p, "data", "import"); const [ch] = await Promise.all([p.waitForEvent("filechooser"), sheet(p).locator("button", { hasText: /file/i }).first().click()]); await ch.setFiles(PACK_SHOWN); await sleep(1500); },
  { api: [["POST", /^\/ingest\/pack/, "hang"]], after: async () => fs.rmSync(PACK_SHOWN, { force: true }) });
// 설정 시트는 .md 를 고르는 즉시 가져온다(확인 시트 없음). 찍고 나면 그 문서를 지운다: 남겨 두면 뒤의 상태들
// LNB 에 "No topic yet" 이 끼고, 다음 실행은 "이미 있음" 화면을 찍는다.
TB("import-doc-sheet", "Import › pick a .md: the document is added", async (p) => { await settingsSheet(p, "data", "import"); const [ch] = await Promise.all([p.waitForEvent("filechooser"), sheet(p).locator("button", { hasText: /file/i }).first().click()]); await ch.setFiles(path.join(REPO, "starter-packs", "README.md")); await sleep(1500); },
  { after: async () => { const d = await side(`/docs?owner=${OWNER}&limit=500`); for (const doc of (d?.docs ?? []).filter((x) => x.origin === "README.md")) await sidePost("/doc/delete", { id: doc.doc_id, owner: OWNER }); } });
TB("graphmd", "Settings › GRAPH.md", async (p) => { await settingsSheet(p, "graphmd"); await sleep(400); });
// 설정 시트의 분류(surfaces/settings.ts ALL_CATS). 옛 탭 셋은 분류로 옮겨 갔다: Account → account,
// Connections(외부 어시스턴트 연결) → ai, Advanced → advanced. 라이트는 첫 분류(General)다.
TB("settings-account", "Settings › Account (signed in, local mode)", (p) => settingsSheet(p, "account"));
TB("settings-connections", "Settings › AI assistants", (p) => settingsSheet(p, "ai"));
TB("settings-advanced", "Settings › Advanced", (p) => settingsSheet(p, "advanced"));
TB("settings-light", "Settings › General, light theme", (p) => settingsSheet(p), { ls: { "vinc.theme": "light" }, scheme: "light" });

// search surface
const SS = (id, trigger, before, extra = {}) => add({ id: `search__${id}`, url: "#search", trigger, before: async (p) => { await sleep(1500); if (before) await before(p); }, ...extra });
SS("default", "load: recent queries and the browse list");
SS("results", "type: grouped results", async (p) => { await p.locator("#surface input").first().fill("pagination"); await sleep(1500); });
SS("empty", "no hits", async (p) => { await p.locator("#surface input").first().fill("zzqxv"); await sleep(1500); });
SS("error", "search 500: banner", async (p) => { await p.locator("#surface input").first().fill("pagination"); await sleep(1500); }, { api: [["GET", /^\/search(\?|$)/,{ status: 500, json: { error: "boom" } }]] });


// ── 다른 스크립트가 같은 상태 목록을 쓴다 ───────────────────────────────────
export { S, APP, SIDE, TOKEN, VP, BASE_LS, SHELL_INIT, side, seed, seedEvidenceEdge, sleep };
const isMain = process.argv[1] && import.meta.url === new URL(`file:///${process.argv[1].replace(/\\/g, "/")}`).href;

// ── 실행 ───────────────────────────────────────────────────────────────────
const onlyList = ONLY ? ONLY.split(",") : null;
if (!isMain) { /* imported */ } else {
const states = onlyList ? S.filter((s) => onlyList.some((o) => s.id.includes(o))) : S;
if (LIST) { for (const s of states) console.log(s.id.padEnd(34), s.trigger); console.log(states.length, "states"); process.exit(0); }
if (!(await side("/health"))) { console.error("sidecar not reachable at", SIDE); process.exit(2); }
fs.mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch({ channel: "chromium" });
if (!NOSEED) await seed(browser); else await seedEvidenceEdge();
// 캡처가 어느 코드를 그렸는지 남긴다. figma_app_copy.py 가 이 리비전 뒤에 app/src 가 바뀌었는지로 보드의 신선도를 본다.
let sourceRev = null; try { sourceRev = (await import("node:child_process")).execSync("git rev-parse HEAD", { cwd: REPO, encoding: "utf8" }).trim(); } catch {}
// 신선도의 **판정 근거**. 커밋이 아니라 이 트리 지문을 게이트가 본다 — 스쿼시 머지는 커밋을
// 바꾸지만 트리는 그대로 옮기므로, 브랜치에서 캡처해도 머지된 main 에서 통과한다.
// (커밋 계보로 재던 시절에는 그 길이 없었다: figma_app_copy.py 머리 참조.)
let sourceTree = null; try { sourceTree = (await import("node:child_process")).execSync("git rev-parse HEAD:app/src", { cwd: REPO, encoding: "utf8" }).trim(); } catch {}
// 설명 두 줄은 스크립트가 쓴다. 손으로 붙여 두면 다음 캡처가 조용히 지운다(실측 2026-09-06).
const manifest = { app: APP, capturedAt: T, tool: "design-hub/scripts/capture_app.mjs", node: NODE_ID,
  sourceRev, sourceRevNote: "Vinc commit whose app/src the captures rendered",
  sourceTree, sourceTreeNote: "git rev-parse <sourceRev>:app/src — the gate compares this with the current tree, not commit history (squash merge keeps the tree, not the commit)",
  states: [] };
let fails = 0;
for (const s of states) {
  const ctx = await browser.newContext({ viewport: s.vp ?? VP, colorScheme: s.scheme ?? "dark", reducedMotion: "reduce", locale: "en-US", deviceScaleFactor: 1 });
  await ctx.addInitScript((kv) => { for (const [k, v] of Object.entries(kv)) { if (v === "") localStorage.removeItem(k); else localStorage.setItem(k, v); } }, { ...BASE_LS, ...s.ls, ...(s.scheme === "light" ? { "vinc.theme": "light" } : {}) });
  await ctx.addInitScript(SHELL_INIT);
  const page = await ctx.newPage();
  // 저장 설정은 이 PC 공용 파일(~/.vinc/config.json)이라 스크립트가 쓰지 않는다. 대신 읽기 응답만
  // '고른 로컬 모드'로 바꿔, 로그인 뒤 알림(device-graph)이 모든 상태를 가리지 않게 한다.
  await page.route((u) => new URL(u).pathname === "/config", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const res = await route.fetch(); const j = await res.json().catch(() => ({}));
    return route.fulfill({ response: res, json: { ...j, mode: "local", mode_chosen: true } });
  });
  for (const [m, re, r] of s.api) {
    await page.route((u) => re.test(new URL(u).pathname + new URL(u).search), async (route) => {
      if (m !== "*" && route.request().method() !== m) return route.continue();
      if (r === "abort") return route.abort("connectionrefused");
      if (r === "hang") return;
      return route.fulfill({ status: r.status, contentType: "application/json", body: JSON.stringify(r.json) });
    });
  }
  const row = { id: s.id, surface: s.url, state: s.id.split("__")[1], trigger: s.trigger, viewport: s.vp ?? VP, scheme: s.scheme ?? "dark", file: `${s.id}.png` };
  try {
    await page.goto(`${APP}/${s.url}`, { waitUntil: "load", timeout: 30000 });
    if (s.before) await s.before(page);
    await page.screenshot({ path: path.join(OUT, row.file), fullPage: s.full });
    if (s.after) await s.after(page);   // 상태가 스토어에 쓴 것을 되돌린다(뒤 상태와 다음 실행이 같은 데이터를 보게)
    row.sha1 = crypto.createHash("sha1").update(fs.readFileSync(path.join(OUT, row.file))).digest("hex");
    console.log("ok  ", s.id);
  } catch (e) { fails++; row.error = String(e.message || e).split("\n")[0].slice(0, 200); console.log("FAIL", s.id, row.error); }
  manifest.states.push(row);
  await ctx.close();
}
await browser.close();
// --only 재실행은 기존 매니페스트에 행을 갈아 끼운다(부분 실행이 전체 매니페스트를 지우지 않게).
const mpath = path.join(OUT, "manifest.json");
if (onlyList && fs.existsSync(mpath)) {
  const known = new Set(S.map((s) => s.id));   // 스크립트에서 빠진 상태의 옛 행은 버린다
  const old = JSON.parse(fs.readFileSync(mpath, "utf8")); const byId = new Map(old.states.filter((r) => known.has(r.id)).map((r) => [r.id, r]));
  for (const r of manifest.states) byId.set(r.id, r);
  manifest.states = [...byId.values()]; manifest.capturedAt = old.capturedAt; manifest.updatedAt = T;
}
fs.writeFileSync(mpath, JSON.stringify(manifest, null, 1));
console.log(`\n${manifest.states.length - fails} captured, ${fails} failed → ${path.relative(HUB, OUT)}`);
process.exit(fails ? 1 : 0);
}
