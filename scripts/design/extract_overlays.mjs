// 오버레이를 **살아 있는 DOM** 에서 재서 JSON 으로 남긴다(Figma 재작도용, `figma_build_overlay.js` 의 입력).
// capture_app.mjs 의 상태 정의·준비(before)를 그대로 쓰므로 캡처와 같은 화면을 잰다.
//
//   node scripts/design/extract_overlays.mjs <out dir> --app http://tauri.localhost:5199 --sidecar http://127.0.0.1:8787 --token devtoken
//
// 한 줄 JSON 하나가 상태 하나다: t(tag) c(class) i(id) b[x,y,w,h] bg bd[w,color] bb(아래선) r o ov s f x(글자) F[family,size,weight,color,align,lh,italic] svg col k(children).
// 스크롤 영역은 펼쳐서 잰다(EXPAND_*) — 바닥에 반쯤 잘린 줄까지 온전히 얻고, vh(펼치기 전 높이)+40 아래의 자식은 버린다.
// 빌더가 루트를 vh 로 자르므로 그 아래는 어차피 안 보이고, 긴 목록(문서 123건)이 use_figma 코드 한도(50K자)를 넘지 않게 된다.
// generate_figma_design(html-to-design) 이 세션에 없을 때의 대체 경로다(2026-09-10).
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";
import { S, APP, SIDE, TOKEN, VP, BASE_LS, SHELL_INIT, side, seedEvidenceEdge, sleep } from "./capture_app.mjs";
const OUT = process.argv[2];
fs.mkdirSync(OUT, { recursive: true });
const EXPAND_LENS = "#view-lens-panel{max-height:none!important;height:auto!important} .lens-scroll{max-height:none!important;overflow:visible!important}";
const EXPAND_POP = "#tb-results,#tb-chat{max-height:none!important;overflow:visible!important}";
const EXPAND_SHEET = ".sheet{max-height:none!important} .sheet-body{max-height:none!important;overflow:visible!important}";
const T = [
  ["view__lens-options-3d", "#view-lens-panel", EXPAND_LENS],
  ["view__lens-legend", "#view-lens-panel", EXPAND_LENS], ["view__lens-help-open", "#view-lens-panel", EXPAND_LENS],
  ["topbar__settings-account", ".sheet[role=dialog]", EXPAND_SHEET], ["topbar__settings-connections", ".sheet[role=dialog]", EXPAND_SHEET],
  ["topbar__settings-advanced", ".sheet[role=dialog]", EXPAND_SHEET], ["topbar__graphmd", ".sheet[role=dialog]", EXPAND_SHEET],
  ["view__node-float", ".float-card", ""], ["view__node-float-links", ".float-card", ""], ["view__node-ask-menu", ".float-card", "", "[role=menu]:not([hidden])"],
  ["view__reader", "#docpane", ""], ["view__node-detail", "#docpane", ""], ["view__edge-detail", "#docpane", ""],
  ["view__3d", "#lnb", ""],
  // ── 2026-09-10 둘째 묶음: 남은 Overlay / * 전부. "last:" 는 겹친 시트 중 맨 위(마지막) 를 고른다.
  ["topbar__mode-menu", "#tb-modemenu", ""],
  ["topbar__find-results", "#tb-results", EXPAND_POP], ["topbar__find-empty", "#tb-results", ""], ["topbar__find-loading", "#tb-results", ""], ["topbar__find-error", "#tb-results", ""],
  ["topbar__ask-intro", "#tb-chat", EXPAND_POP], ["topbar__ask-answer", "#tb-chat", EXPAND_POP], ["topbar__ask-none", "#tb-chat", EXPAND_POP],
  ["topbar__ask-history", "last:.sheet[role=dialog]", EXPAND_SHEET],
  ["topbar__palette", ".palette-card", ""], ["topbar__palette-filtered", ".palette-card", ""],
  ["topbar__cheatsheet", "last:.sheet[role=dialog]", EXPAND_SHEET],
  ["topbar__tools-create", "last:.sheet[role=dialog]", EXPAND_SHEET], ["topbar__tools-import", "last:.sheet[role=dialog]", EXPAND_SHEET], ["topbar__tools-share", "last:.sheet[role=dialog]", EXPAND_SHEET],
  ["topbar__share-sheet", "last:.sheet[role=dialog]", EXPAND_SHEET], ["topbar__import-pack-sheet", "last:.sheet[role=dialog]", EXPAND_SHEET], ["topbar__import-doc-sheet", "last:.sheet[role=dialog]", EXPAND_SHEET],
  ["topbar__browse-sheet", "last:.sheet[role=dialog]", EXPAND_SHEET], ["topbar__browse-sheet-docs", "last:.sheet[role=dialog]", EXPAND_SHEET],
  ["view__new-node-sheet", "last:.sheet[role=dialog]", EXPAND_SHEET], ["view__first-run", "#signin-screen", ""],
  ["view__coachmark", ".coach", ""],
  ["view__topic-context-menu", ".ctx-menu", ""], ["view__node-context-menu", ".ctx-menu", ""],
  ["view__topic-rename-prompt", "last:.sheet[role=dialog]", ""],
  ["view__node-delete-confirm", ".sheet[role=alertdialog]", ""],
  ["view__tooltip-short", ".tip", ""], ["view__tooltip-long", ".tip", ""],
  // 웹 전용 단계(목적지 고르기). 아래 EXTRA 의 web 상태로 잰다.
  ["topbar__share-destination", "last:.sheet[role=dialog]", EXPAND_SHEET],
];
// 캡처 목록에 없는 상태 셋 — 보드는 없고 Figma 컴포넌트만 있는 것들(노드 컨텍스트 메뉴 · 툴팁 둘).
// 노드 메뉴는 WebGL 캔버스 우클릭 대신 앱 모듈을 그대로 불러 같은 항목으로 연다(카피는 COPY 에서, 정본).
const settled = async (p) => { await p.waitForSelector("#view-metrics", { state: "attached" }).catch(() => {}); await sleep(2200); };
const hoverTip = async (p, pick) => { await p.evaluate((pick) => { const els = [...document.querySelectorAll("#lnb [data-tip], #rail [data-tip], #topbar [data-tip]")]; els.sort((a, b) => a.dataset.tip.length - b.dataset.tip.length); const el = pick === "long" ? els[els.length - 1] : els[0]; el.setAttribute("data-ext-hover", "1"); }, pick); await p.hover("[data-ext-hover]"); await sleep(1200); };
const EXTRA = [
  { id: "view__node-context-menu", url: "#view", ls: {}, api: [], before: async (p) => { await settled(p); await p.evaluate(async () => { const [{ openContextMenu }, { COPY }] = await Promise.all([import("/src/components.ts"), import("/src/copy.ts")]); openContextMenu(520, 320, [{ label: COPY.ui.menu.open, run() {} }, { label: COPY.ui.menu.shareHere, run() {} }, { label: COPY.ui.viewx.del, danger: true, run() {} }]); }); await sleep(300); } },
  { id: "view__tooltip-short", url: "#view", ls: {}, api: [], before: async (p) => { await settled(p); await hoverTip(p, "short"); } },
  { id: "view__tooltip-long", url: "#view", ls: {}, api: [], before: async (p) => { await settled(p); await hoverTip(p, "long"); } },
];
// ── 웹 모드(WEB=true) 상태 ──────────────────────────────────────────────────
// api.ts: WEB 은 호스트명으로 갈린다(localhost·127.0.0.1·tauri.localhost 가 아니면 웹). 크로미엄은 *.localhost 를
// 루프백으로 풀므로 web.localhost:5199 로 같은 vite 를 열면 웹 분기가 선다. 그러면 ① 부팅이 __Host-csrf 쿠키와
// /api/me 를 요구하고(쿠키는 https 전용 접두라 document.cookie 게터를 덮어 흉내낸다) ② 데이터가 /api/graph/* 로
// 가는데 vite 프록시 목록엔 없으므로 여기서 사이드카로 넘긴다("sidecar" 라우트) ③ /api/teams 는 빈 목록.
// 스킨은 desktop 으로 고정한다 — Figma 05 · Overlays 의 다른 변형이 전부 desktop 스킨이라 세트가 섞이지 않게.
const WEB_APP = (process.env.VINC_WEB_APP || APP).replace("tauri.localhost", "web.localhost");
const WEB_COOKIE = () => { Object.defineProperty(Document.prototype, "cookie", { get: () => "__Host-csrf=capture", set: () => {}, configurable: true }); };
const WEB_API = [["GET", /^\/api\/me(\?|$)/, { status: 200, json: { sub: "capture" } }], ["GET", /^\/api\/teams(\?|$)/, { status: 200, json: { teams: [] } }], ["*", /^\/api\/graph\//, "sidecar"]];
const WEB_LS = { "vinc.sidecar.base": "", "vinc.sidecar.token": "", "vinc.skin": "desktop" };
const sheetL = (p) => p.locator(".sheet[role=dialog]").last();
// 공유 시트의 행선지 고르기까지만 잰다. 커뮤니티 행선지는 앱 안 폼이 아니라 진행 표시 뒤 사이트 /community/share/ 로 넘어가므로 잴 오버레이가 없다.
const webShare = async (p) => {
  await settled(p);
  await p.locator("#tb-tools").click(); await sheetL(p).waitFor({ state: "visible" });
  await sheetL(p).locator("button", { hasText: /^Share$/ }).first().click();
  const go = sheetL(p).locator(".sheet-foot button").filter({ hasText: /continue/i }).first(); await go.waitFor({ state: "visible", timeout: 30000 }); await go.click();
  await p.waitForSelector(".sheet[role=dialog] #sheet-save", { timeout: 30000 }); await sleep(800);
  await p.locator("#sheet-save").click();
  await p.waitForSelector(".dest-pick", { timeout: 15000 }); await sleep(300);
};
EXTRA.push(
  { id: "topbar__share-destination", url: "#view", web: true, app: WEB_APP, ls: WEB_LS, api: WEB_API, before: webShare },
);
const SKIP_EXISTING = process.argv.includes("--skip-existing");
const EXT = String.raw`window.__ext = (root) => {
  const R = root.getBoundingClientRect(); const n1 = (v) => Math.round(v * 10) / 10;
  const isInline = (k) => k.nodeType === 1 && /^(span|b|i|em|strong|kbd|code|a|u|small)$/i.test(k.tagName) && !k.querySelector('svg,button,input,div') && getComputedStyle(k).display.startsWith('inline');
  const font = (cs) => [cs.fontFamily.split(',')[0].replace(/["']/g,''), n1(parseFloat(cs.fontSize)), cs.fontWeight, cs.color, cs.textAlign, n1(parseFloat(cs.lineHeight)) || 0, cs.fontStyle === 'italic' ? 1 : 0];
  const walk = (el, depth) => {
    if (!(el instanceof Element) || /^(script|style)$/i.test(el.tagName)) return null;
    const cs = getComputedStyle(el); if (cs.display === 'none' || cs.visibility === 'hidden') return null;
    const b = el.getBoundingClientRect(); if (b.width < 0.5 || b.height < 0.5) return null;
    const n = { t: el.tagName.toLowerCase(), c: [...el.classList].slice(0, 3).join('.') || undefined, i: el.id || undefined, b: [n1(b.x - R.x), n1(b.y - R.y), n1(b.width), n1(b.height)] };
    if (cs.backgroundColor !== 'rgba(0, 0, 0, 0)') n.bg = cs.backgroundColor;
    if (parseFloat(cs.borderTopWidth) > 0 && cs.borderTopStyle !== 'none') n.bd = [n1(parseFloat(cs.borderTopWidth)), cs.borderTopColor];
    if (parseFloat(cs.borderBottomWidth) > 0 && parseFloat(cs.borderTopWidth) === 0 && cs.borderBottomStyle !== 'none') n.bb = [n1(parseFloat(cs.borderBottomWidth)), cs.borderBottomColor];
    if (parseFloat(cs.borderTopLeftRadius) > 0) n.r = n1(parseFloat(cs.borderTopLeftRadius));
    if (cs.opacity !== '1') n.o = parseFloat(cs.opacity);
    if (cs.overflow !== 'visible' || cs.overflowY !== 'visible') n.ov = 1;
    if (cs.boxShadow !== 'none') n.s = cs.boxShadow.slice(0, 90);
    if (cs.backdropFilter !== 'none') n.f = cs.backdropFilter;
    if (el.tagName.toLowerCase() === 'svg') { n.svg = el.outerHTML.slice(0, 3000); n.col = cs.color; return n; }
    if (/^input$/i.test(el.tagName) && /^(checkbox|radio)$/.test(el.type)) { n.t = 'cb'; n.kind = el.type; n.on = el.checked ? 1 : 0; return n; }   /* 체크박스·라디오는 값이 아니라 상태다 */
    if (/^(input|textarea|select)$/i.test(el.tagName)) { n.x = el.value || el.placeholder || ''; n.ph = el.value ? 0 : 1; n.F = font(cs); if (n.ph) n.F[3] = 'rgb(110, 113, 137)'; return n; }
    const kids = [...el.childNodes];
    const textOnly = kids.length && kids.every(k => k.nodeType === 3 || k.nodeType === 8 || isInline(k));
    if (textOnly) { const t = kids.map(k => k.nodeType === 3 ? k.textContent : (k.nodeType === 1 ? ' ' + k.textContent + ' ' : '')).join('').replace(/\s+/g, ' ').trim();   /* 인라인 요소 경계에 공백 — 한 줄 스크립트라 줄 주석은 뒤를 다 삼킨다 */ if (t) { n.x = t.slice(0, 400); n.F = font(cs); } return n; }
    const ch = [];
    for (const k of kids) {
      if (k.nodeType === 3) { const t = k.textContent.replace(/\s+/g, ' ').trim(); if (!t) continue; const rg = document.createRange(); rg.selectNodeContents(k); const rb = rg.getBoundingClientRect(); if (rb.width < 0.5) continue; ch.push({ t: '#text', b: [n1(rb.x - R.x), n1(rb.y - R.y), n1(rb.width), n1(rb.height)], x: t.slice(0, 400), F: font(cs) }); }
      else if (k.nodeType === 1 && depth < 16) { const c = walk(k, depth + 1); if (c) ch.push(c); }
    }
    if (ch.length) n.k = ch;
    return n;
  };
  return walk(root, 0);
};`;
if (!(await side("/health"))) { console.error("sidecar not reachable"); process.exit(2); }
const browser = await chromium.launch({ channel: "chromium" });
await seedEvidenceEdge();   // edge-detail 상태가 근거 있는 관계를 요구한다
for (const [id, root, expand, root2] of T) {
  const s = [...S, ...EXTRA].find((x) => x.id === id); if (!s) { console.log("skip", id); continue; }
  if (SKIP_EXISTING && fs.existsSync(path.join(OUT, id + ".json"))) continue;
  const ctx = await browser.newContext({ viewport: s.vp ?? VP, colorScheme: s.scheme ?? "dark", reducedMotion: "reduce", locale: "en-US", deviceScaleFactor: 1 });
  try {
    await ctx.addInitScript((kv) => { for (const [k, v] of Object.entries(kv)) { if (v === "") localStorage.removeItem(k); else localStorage.setItem(k, v); } }, { ...BASE_LS, ...s.ls, ...(s.scheme === "light" ? { "vinc.theme": "light" } : {}) });
    await ctx.addInitScript(s.web ? WEB_COOKIE : SHELL_INIT);
    const page = await ctx.newPage();
    for (const [m, re, r] of s.api) await page.route((u) => re.test(new URL(u).pathname + new URL(u).search), async (route) => { if (m !== "*" && route.request().method() !== m) return route.continue(); if (r === "abort") return route.abort("connectionrefused"); if (r === "hang") return; if (r === "sidecar") { const u0 = new URL(route.request().url()); const h = route.request().headers(); const resp = await route.fetch({ url: SIDE + u0.pathname.replace(/^\/api\/graph/, "") + u0.search, headers: { accept: h.accept || "*/*", ...(h["content-type"] ? { "content-type": h["content-type"] } : {}), authorization: `Bearer ${TOKEN}` } }); return route.fulfill({ response: resp }); } return route.fulfill({ status: r.status, contentType: "application/json", body: JSON.stringify(r.json) }); });
    await page.goto(`${s.app || APP}/${s.url}`, { waitUntil: "load", timeout: 30000 });
    if (s.before) await s.before(page);
    const pickEl = String.raw`(sel) => sel.startsWith("last:") ? [...document.querySelectorAll(sel.slice(5))].pop() : document.querySelector(sel)`;
    const vh = await page.evaluate(([pk, sel]) => { const el = (0, eval)(pk)(sel); return el ? Math.round(el.getBoundingClientRect().height * 10) / 10 : null; }, [pickEl, root]);
    await page.addStyleTag({ content: expand + " *{transition:none!important;animation:none!important}" });
    await sleep(300);
    const data = await page.evaluate(([ext, sel, sel2, pk]) => { (0, eval)(ext); const el = (0, eval)(pk)(sel); const d = el ? window.__ext(el) : null; if (d && sel2) { const e2 = document.querySelector(sel2); if (e2) { const R = el.getBoundingClientRect(), B = e2.getBoundingClientRect(); const m = window.__ext(e2); if (m) { m.b[0] = Math.round((B.x - R.x) * 10) / 10; m.b[1] = Math.round((B.y - R.y) * 10) / 10; m.detached = 1; (d.k = d.k || []).push(m); } } } return d; }, [EXT, root, root2 || null, pickEl]);
    if (data) { data.vh = vh; if (vh) { const prune = (n) => { if (!n.k) return; n.k = n.k.filter((c) => c.b[1] < vh + 40); n.k.forEach(prune); }; prune(data); } }
    const f = path.join(OUT, id + ".json"); fs.writeFileSync(f, JSON.stringify(data));
    console.log(data ? "ok  " : "NULL", id, data ? `${Math.round(fs.statSync(f).size / 1024)}KB ${data.b[2]}x${data.b[3]}` : "");
  } catch (e) { console.log("FAIL", id, String(e.message || e).split("\n")[0].slice(0, 160)); }
  finally { await ctx.close(); }
}
await browser.close();
