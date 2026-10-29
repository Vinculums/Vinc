// 앱 상태를 Figma 로 **편집 가능한 레이어**로 옮긴다(html-to-design). capture_app.mjs 의 상태 정의·시드·셸 주입을
// 그대로 쓰고, PNG 대신 Figma capture.js 의 내부 함수(직렬화 ar · 제출 Tm)로 DOM 을 제출한다. 씨앗 captureId
// 하나가 nextCaptureId 로 이어지므로 체인 수만큼만 generate_figma_design 을 부른다(capture_site_figma.mjs 와 같다).
//
//   node scripts/design/capture_app_figma.mjs <job.json>
//   job.json = { "seeds": ["<captureId>", ...], "states": ["<state id>", ...] }
//
// 씨앗을 발급할 때 nodeId 로 준 페이지 아래에 프레임이 생긴다. 서피스·오버레이·상태를 페이지별로 나누려면 페이지마다
// 따로 job 을 만든다(04 · Surfaces, 05 · Overlays, 06 · States). 프레임 이름은 문서 제목 = 상태 id 다.
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";
import { S, APP, VP, BASE_LS, SHELL_INIT, side, seed, seedEvidenceEdge, sleep } from "./capture_app.mjs";

const jobPath = process.argv[2];
if (!jobPath) { console.error("usage: capture_app_figma.mjs <job.json>"); process.exit(2); }
const job = JSON.parse(fs.readFileSync(jobPath, "utf8"));
const resultPath = jobPath.replace(/\.json$/, "") + ".result.json";
const results = fs.existsSync(resultPath) ? JSON.parse(fs.readFileSync(resultPath, "utf8")) : {};
const save = () => fs.writeFileSync(resultPath, JSON.stringify(results, null, 1));
const STEP_TIMEOUT_MS = 6 * 60 * 1000;
const submitted = new Set();   // 제출 단계에 들어간 captureId (실패해도 재사용 불가)

const raw = await (await fetch("https://mcp.figma.com/mcp/html-to-design/capture.js")).text();
// 내부 함수 이름은 capture.js 를 다시 빌드할 때마다 바뀐다. 이름을 박지 않고 파일 모양에서 찾는다(capture_site_figma.mjs 와 같다).
const hookM = /startMultiCaptureSelectionFlow:(\w+)\}\)\);\}\)\(\);/.exec(raw);
const serM = /let m=yield (\w+)\(p,i\)/.exec(raw);
const subM = /let w=yield (\w+)\(m,a,t,b\)/.exec(raw);
if (!hookM || !serM || !subM) { console.error("capture.js changed shape: cannot find the serialize and submit functions"); process.exit(3); }
const captureJs = raw.replace(hookM[0], `startMultiCaptureSelectionFlow:${hookM[1]}}));window.__vincCap={ar:${serM[1]},Tm:${subM[1]}};})();`);

if (!(await side("/health"))) { console.error("sidecar not reachable"); process.exit(2); }
const browser = await chromium.launch({ channel: "chromium" });
if (!job.noSeed) await seed(browser); else await seedEvidenceEdge();   // 엣지 상세 상태가 근거 있는 관계의 노드를 알아야 한다
const queue = job.states.filter((id) => !results[id]?.ok);
console.log(`${queue.length} states over ${job.seeds.length} chains`);

async function captureOne(id, captureId) {
  const s = S.find((x) => x.id === id);
  if (!s) throw new Error("unknown state " + id);
  const t0 = Date.now();
  const ctx = await browser.newContext({ viewport: s.vp ?? VP, colorScheme: s.scheme ?? "dark", reducedMotion: "reduce", locale: "en-US", deviceScaleFactor: 1 });
  try {
    await ctx.addInitScript((kv) => { for (const [k, v] of Object.entries(kv)) { if (v === "") localStorage.removeItem(k); else localStorage.setItem(k, v); } }, { ...BASE_LS, ...s.ls, ...(s.scheme === "light" ? { "vinc.theme": "light" } : {}) });
    await ctx.addInitScript(SHELL_INIT);
    const page = await ctx.newPage();
    for (const [m, re, r] of s.api) {
      await page.route((u) => re.test(new URL(u).pathname + new URL(u).search), async (route) => {
        if (m !== "*" && route.request().method() !== m) return route.continue();
        if (r === "abort") return route.abort("connectionrefused");
        if (r === "hang") return;
        return route.fulfill({ status: r.status, contentType: "application/json", body: JSON.stringify(r.json) });
      });
    }
    await page.goto(`${APP}/${s.url}`, { waitUntil: "load", timeout: 30000 });
    if (s.before) await s.before(page);
    submitted.add(captureId);          // 여기부터는 제출이 나갔을 수 있어 같은 id 를 다시 쓸 수 없다
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

async function chain(seedId, n) {
  let cid = seedId;
  while (queue.length && cid) {
    const id = queue.shift();
    try {
      const r = await captureOne(id, cid);
      results[id] = { ok: true, ...r }; save();
      console.log(`ok   [${n}] ${id} (${Math.round(r.ms / 1000)}s)`);
      cid = r.nextCaptureId;
      if (!cid) console.log(`chain ${n}: no nextCaptureId returned, chain ends`);
    } catch (e) {
      const msg = String(e.message || e).split("\n")[0].slice(0, 200);
      results[id] = { ok: false, captureId: cid, error: msg }; save();
      console.log(`FAIL [${n}] ${id} ${msg}`);
      // 상태 준비(before) 중에 죽었으면 id 는 아직 쓰인 적이 없다: 다음 상태로 이어 쓴다. 제출 뒤에 죽었으면 끝.
      if (submitted.has(cid)) { console.log(`chain ${n}: id may be spent, chain ends`); cid = null; }
    }
  }
}
await Promise.all(job.seeds.map((s, i) => chain(s, i + 1)));
await browser.close();
const ok = Object.values(results).filter((r) => r.ok).length;
console.log(`\n${ok} captured, ${job.states.length - ok} not → ${path.basename(resultPath)}${queue.length ? ` (${queue.length} states left: chains ran out of ids)` : ""}`);
