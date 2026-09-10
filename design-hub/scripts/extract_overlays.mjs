// 오버레이를 **살아 있는 DOM** 에서 재서 JSON 으로 남긴다(Figma 재작도용, `figma_build_overlay.js` 의 입력).
// capture_app.mjs 의 상태 정의·준비(before)를 그대로 쓰므로 캡처와 같은 화면을 잰다.
//
//   node scripts/extract_overlays.mjs <out dir> --app http://tauri.localhost:5199 --sidecar http://127.0.0.1:8787 --token devtoken
//
// 한 줄 JSON 하나가 상태 하나다: t(tag) c(class) i(id) b[x,y,w,h] bg bd[w,color] bb(아래선) r o ov s f x(글자) F[family,size,weight,color,align,lh,italic] svg col k(children).
// 스크롤 영역은 펼쳐서 잰다(EXPAND_*) — 잘린 줄 없이 전체 높이를 얻고, Figma 쪽에서 clipsContent 로 앱 높이로 되돌린다.
// generate_figma_design(html-to-design) 이 세션에 없을 때의 대체 경로다(2026-09-10).
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";
import { S, APP, VP, BASE_LS, SHELL_INIT, side, seedEvidenceEdge, sleep } from "./capture_app.mjs";
const OUT = process.argv[2];
fs.mkdirSync(OUT, { recursive: true });
const EXPAND_LENS = "#view-lens-panel{max-height:none!important;height:auto!important} .lens-scroll{max-height:none!important;overflow:visible!important}";
const EXPAND_SHEET = ".sheet{max-height:none!important} .sheet-body{max-height:none!important;overflow:visible!important}";
const T = [
  ["view__lens-options-3d", "#view-lens-panel", EXPAND_LENS], ["view__lens-options-2d", "#view-lens-panel", EXPAND_LENS],
  ["view__lens-options-timeline", "#view-lens-panel", EXPAND_LENS], ["view__lens-options-weave", "#view-lens-panel", EXPAND_LENS],
  ["view__lens-legend", "#view-lens-panel", EXPAND_LENS], ["view__lens-help-open", "#view-lens-panel", EXPAND_LENS],
  ["topbar__settings-account", ".sheet[role=dialog]", EXPAND_SHEET], ["topbar__settings-connections", ".sheet[role=dialog]", EXPAND_SHEET],
  ["topbar__settings-advanced", ".sheet[role=dialog]", EXPAND_SHEET], ["topbar__graphmd", ".sheet[role=dialog]", EXPAND_SHEET],
  ["view__node-float", ".float-card", ""], ["view__node-float-links", ".float-card", ""], ["view__node-ask-menu", ".float-card", "", "[role=menu]:not([hidden])"],
  ["view__reader", "#docpane", ""], ["view__node-detail", "#docpane", ""], ["view__edge-detail", "#docpane", ""],
  ["view__3d", "#lnb", ""],
];
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
  const s = S.find((x) => x.id === id); if (!s) { console.log("skip", id); continue; }
  const ctx = await browser.newContext({ viewport: s.vp ?? VP, colorScheme: s.scheme ?? "dark", reducedMotion: "reduce", locale: "en-US", deviceScaleFactor: 1 });
  try {
    await ctx.addInitScript((kv) => { for (const [k, v] of Object.entries(kv)) { if (v === "") localStorage.removeItem(k); else localStorage.setItem(k, v); } }, { ...BASE_LS, ...s.ls, ...(s.scheme === "light" ? { "vinc.theme": "light" } : {}) });
    await ctx.addInitScript(SHELL_INIT);
    const page = await ctx.newPage();
    for (const [m, re, r] of s.api) await page.route((u) => re.test(new URL(u).pathname + new URL(u).search), async (route) => { if (m !== "*" && route.request().method() !== m) return route.continue(); if (r === "abort") return route.abort("connectionrefused"); if (r === "hang") return; return route.fulfill({ status: r.status, contentType: "application/json", body: JSON.stringify(r.json) }); });
    await page.goto(`${APP}/${s.url}`, { waitUntil: "load", timeout: 30000 });
    if (s.before) await s.before(page);
    await page.addStyleTag({ content: expand + " *{transition:none!important;animation:none!important}" });
    await sleep(300);
    const data = await page.evaluate(([ext, sel, sel2]) => { (0, eval)(ext); const el = document.querySelector(sel); const d = el ? window.__ext(el) : null; if (d && sel2) { const e2 = document.querySelector(sel2); if (e2) { const R = el.getBoundingClientRect(), B = e2.getBoundingClientRect(); const m = window.__ext(e2); if (m) { m.b[0] = Math.round((B.x - R.x) * 10) / 10; m.b[1] = Math.round((B.y - R.y) * 10) / 10; m.detached = 1; (d.k = d.k || []).push(m); } } } return d; }, [EXT, root, root2 || null]);
    const f = path.join(OUT, id + ".json"); fs.writeFileSync(f, JSON.stringify(data));
    console.log(data ? "ok  " : "NULL", id, data ? `${Math.round(fs.statSync(f).size / 1024)}KB ${data.b[2]}x${data.b[3]}` : "");
  } catch (e) { console.log("FAIL", id, String(e.message || e).split("\n")[0].slice(0, 160)); }
  finally { await ctx.close(); }
}
await browser.close();
