// 공홈 상태를 Figma 로 **편집 가능한 레이어**로 옮긴다(html-to-design). PNG 참조(capture_site.mjs)와 같은 상태
// 정의·픽스처를 쓰되, 스크린샷 대신 Figma 의 capture.js 로 DOM 을 직렬화해 제출한다.
//
//   node scripts/capture_site_figma.mjs <job.json>
//   job.json = { "seeds": ["<captureId>", ...], "states": ["<state id>", ...] }
//
// 왜 seeds 인가. generate_figma_design 이 발급하는 captureId 는 1회용이지만, 제출 응답이 **다음 captureId**
// (nextCaptureId) 를 돌려준다. 그래서 씨앗 하나로 상태를 줄줄이 이어 찍을 수 있다(씨앗 수 = 병렬 체인 수).
// capture.js 의 공개 진입점(captureForDesign)은 툴바를 띄우고 사람의 다음 행동을 기다리는 프로미스라
// 헤드리스에서는 절대 풀리지 않는다. 그래서 스크립트를 주입할 때 내부 함수 둘(직렬화 ar · 제출 Tm)을
// window.__vincCap 으로 노출시켜 직접 부른다. 제출 응답의 claimUrl 에 생성된 노드 id 가 있어 폴링도 필요 없다.
//
// 결과는 <job>.result.json 에 상태별 {captureId, nodeId, claimUrl, ms} 로 남는다. 프레임 이름은 문서 제목이 되므로
// 상태 id 를 제목에 두고, 이름 정리·배치는 뒤따르는 use_figma 단계가 한다.
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";
import { S, install, BASE, sleep } from "./capture_site.mjs";

const jobPath = process.argv[2];
if (!jobPath) { console.error("usage: capture_site_figma.mjs <job.json>"); process.exit(2); }
const job = JSON.parse(fs.readFileSync(jobPath, "utf8"));
const resultPath = jobPath.replace(/\.json$/, "") + ".result.json";
const results = fs.existsSync(resultPath) ? JSON.parse(fs.readFileSync(resultPath, "utf8")) : {};
const save = () => fs.writeFileSync(resultPath, JSON.stringify(results, null, 1));
const STEP_TIMEOUT_MS = 6 * 60 * 1000;

const raw = await (await fetch("https://mcp.figma.com/mcp/html-to-design/capture.js")).text();
const HOOK = "startMultiCaptureSelectionFlow:dk}));})();";
if (!raw.includes(HOOK)) { console.error("capture.js changed shape: cannot expose ar/Tm"); process.exit(3); }
const captureJs = raw.replace(HOOK, "startMultiCaptureSelectionFlow:dk}));window.__vincCap={ar:ar,Tm:Tm};})();");

const browser = await chromium.launch({ channel: "chromium" });
const queue = job.states.filter((id) => !results[id]?.ok);
console.log(`${queue.length} states over ${job.seeds.length} chains`);

async function captureOne(id, captureId) {
  const s = S.find((x) => x.id === id);
  if (!s) throw new Error("unknown state " + id);
  const t0 = Date.now();
  const vp = s.vp ?? { width: 1440, height: 900 };
  const ctx = await browser.newContext({ viewport: vp, colorScheme: s.scheme ?? "dark", reducedMotion: "reduce", locale: "en-US", userAgent: s.ua, deviceScaleFactor: 1 });
  try {
    await ctx.addCookies([{ name: "__Host-csrf", value: "capture", url: BASE }]).catch(() => {});
    const ls = { "vinc.site.theme": s.scheme ?? "dark", "vinc.site.lang": "en", ...s.ls };
    await ctx.addInitScript((kv) => { try { for (const [k, v] of Object.entries(kv)) localStorage.setItem(k, v); } catch (e) {} }, ls);
    const page = await ctx.newPage();
    page.on("dialog", async (d) => { await (page.acceptDialogs ? d.accept() : d.dismiss()).catch(() => {}); });
    await page.route("**/*", async (route) => {
      if (route.request().resourceType() !== "document") return route.continue();
      const response = await route.fetch(); const headers = { ...response.headers() };
      delete headers["content-security-policy"]; delete headers["content-security-policy-report-only"];
      await route.fulfill({ response, headers });
    });
    await install(page, s.api);
    await page.goto(BASE + s.url, { waitUntil: "networkidle", timeout: 45000 }).catch(async () => page.goto(BASE + s.url, { waitUntil: "load", timeout: 45000 }));
    await sleep(700);
    if (s.before) await s.before(page);
    await page.evaluate((t) => { document.title = t; }, id);
    await page.evaluate((src) => { const el = document.createElement("script"); el.textContent = src; document.head.appendChild(el); }, captureJs);
    await page.waitForFunction(() => window.__vincCap && window.__vincCap.ar && window.__vincCap.Tm, null, { timeout: 15000 });
    const endpoint = `https://mcp.figma.com/mcp/capture/${captureId}/submit?bindVariables=true`;
    const out = await Promise.race([
      page.evaluate(async (a) => { const payload = await window.__vincCap.ar("body", false); return await window.__vincCap.Tm(payload, a.captureId, a.endpoint, 0); }, { captureId, endpoint }),
      new Promise((_, rej) => setTimeout(() => rej(new Error("step timeout")), STEP_TIMEOUT_MS)),
    ]);
    const m = /node-id=(\d+)-(\d+)/.exec(out?.claimUrl || "");
    return { captureId, nodeId: m ? `${m[1]}:${m[2]}` : null, claimUrl: out?.claimUrl || null, nextCaptureId: out?.nextCaptureId || null, ms: Date.now() - t0 };
  } finally { await ctx.close(); }
}

async function chain(seed, n) {
  let cid = seed;
  while (queue.length && cid) {
    const id = queue.shift();
    try {
      const r = await captureOne(id, cid);
      results[id] = { ok: true, ...r }; save();
      console.log(`ok   [${n}] ${id} → ${r.nodeId} (${Math.round(r.ms / 1000)}s)`);
      cid = r.nextCaptureId;
      if (!cid) console.log(`chain ${n}: no nextCaptureId returned, chain ends`);
    } catch (e) {
      const msg = String(e.message || e).split("\n")[0].slice(0, 200);
      results[id] = { ok: false, captureId: cid, error: msg }; save();
      console.log(`FAIL [${n}] ${id} ${msg}`);
      // 제출이 나갔는지 모르면 같은 id 로 다시 못 쓴다(1회용). 체인은 여기서 끝난다.
      if (!/unknown state|Timeout|locator|waitFor/i.test(msg)) cid = null;
    }
  }
}
await Promise.all(job.seeds.map((seed, i) => chain(seed, i + 1)));
await browser.close();
const ok = Object.values(results).filter((r) => r.ok).length;
console.log(`\n${ok} captured, ${job.states.length - ok} not → ${path.basename(resultPath)}${queue.length ? ` (${queue.length} states left: chains ran out of ids)` : ""}`);
