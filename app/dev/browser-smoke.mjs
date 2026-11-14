// 앱 셸이 실제 브라우저에서 뜨는가: 빌드한 앱을 리포의 사이드카에 붙여 헤드리스 Chromium 으로 화면을 차례로 본다.
//
//   cd app && npm run smoke
//
// 하는 일: 빈 임베디드 스토어로 사이드카(`python -m api.cli serve`)를 띄우고 문서 둘, 개념 셋, 에이전트 역할 하나를 심는다 →
// `vite build` 로 앱을 짓고 `vite preview` 로 낸다(사이드카 경로는 vite.config.ts 의 프록시가 같은 출처로 넘긴다) →
// 셸 마운트 · 문서 홈 목록 · 문서 열기 · 그래프 나침반 · 검색 · 노드 카드와 오른쪽 면 · 문서 리더 · 공유 시트 · 설정 ·
// 승인함 · 에이전트 보드를 차례로 본다. 걸음마다 잡히지 않은 예외와 콘솔
// 오류가 하나도 없어야 한다. 실패하면 그 걸음 이름으로 끝나고 스크린숏과 Playwright 트레이스를 남긴다.
//
// 환경: VINC_PYTHON(사이드카를 띄울 파이썬, 기본은 리포의 .venv 다음 python3) · VINC_SMOKE_OUT(실패 산출물 폴더,
// 기본은 임시 폴더) · VINC_SMOKE_DIST(이미 지은 dist 를 쓴다, 없으면 이 스크립트가 짓는다) ·
// VINC_SMOKE_CHROME(Playwright 가 찾는 브라우저 대신 쓸 실행 파일) ·
// VINC_SMOKE_HEADED=1(창을 띄워 본다). 루프백 밖으로 나가는 요청은 막고 그 목록을 실패로 친다.
// 셸(Tauri)·로그인·동기화·계정 그래프는 보지 않는다: 이것은 브라우저 dev 경로(localhost, 로컬 모드)다.
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdir, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { randomBytes } from "node:crypto";

const app = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repo = resolve(app, "..");
const started = Date.now();
const scratch = await mkdtemp(join(tmpdir(), "vinc-app-smoke-"));
const out = resolve(process.env.VINC_SMOKE_OUT || join(scratch, "output"));
const token = randomBytes(18).toString("hex");
const OWNER = "tenant:local";
// 심는 것. 제목·본문·주제는 아래 걸음이 화면에서 그대로 찾는다.
const SEED = {
  title: "Smoke harbour notes",
  topic: "smoke/harbour",
  phrase: "The lighthouse keeper logs every tide in the harbour ledger.",
  query: "lighthouse",
  node: { id: "concept:smoke-lighthouse", title: "Lighthouse" },
  role: { id: "concept:agent-smoke-keeper", title: "Agent role: Lighthouse keeper", jobTitle: "Keeper" },
};
const STEP_MS = 30_000;
const say = (m) => console.log(`[smoke ${((Date.now() - started) / 1000).toFixed(1)}s] ${m}`);

let sidecar = null, server = null, browser = null, context = null;
const sidecarLog = [];

function pythonPath() {
  if (process.env.VINC_PYTHON) return process.env.VINC_PYTHON;
  for (const p of [join(repo, ".venv", "bin", "python"), join(repo, ".venv", "Scripts", "python.exe")])
    if (existsSync(p)) return p;
  return process.platform === "win32" ? "python" : "python3";
}

// 사이드카를 띄우고 첫 줄 배너(JSON, 포트가 실린다)를 기다린다. 데스크톱이 띄우는 것과 같은 진입점이다.
async function startSidecar() {
  const env = { ...process.env, HOME: scratch, USERPROFILE: scratch, VINC_TARGET: "local" };
  // 밖의 설정이 이 스토어를 다른 곳으로 돌리지 않게 지운다(빈 임시 스토어만 쓴다).
  for (const k of ["PYTHONHOME", "PYTHONPATH", "MEMORY_DATA_DIR", "VINC_SIDECAR_TOKEN", "VINC_STORE_SNAPSHOT",
    "VINC_DURABLE_ACK", "VINC_DATABASE_URI", "VINC_DATABASE_NAME", "VINC_DATABASE_USER", "VINC_DATABASE_SECRET",
    "VINC_DATABASE_SECRET_FILE", "VINC_EMBED_ENCODE"]) delete env[k];
  // 리눅스에서는 리포에 들어 있는 엔진 확장을 쓴다(ci.yml 의 core 잡과 같다). 네트워크로 받지 않는다.
  const bundled = join(repo, "packaging", "lbug_extensions");
  if (!env.VINC_LBUG_EXT_DIR && process.platform === "linux" && existsSync(bundled)) env.VINC_LBUG_EXT_DIR = bundled;
  const py = pythonPath();
  sidecar = spawn(py, ["-m", "api.cli", "serve", "--port", "0", "--token", token, "--data", join(scratch, "db")],
    { cwd: repo, env, stdio: ["ignore", "pipe", "pipe"] });
  sidecar.stderr.setEncoding("utf8").on("data", (d) => sidecarLog.push(d));
  const banner = await new Promise((ok, fail) => {
    let buf = "";
    const timer = setTimeout(() => fail(new Error("no startup banner within 90 s")), 90_000);
    sidecar.stdout.setEncoding("utf8").on("data", (d) => {
      buf += d;
      const nl = buf.indexOf("\n");
      if (nl < 0) return;
      clearTimeout(timer);
      try { ok(JSON.parse(buf.slice(0, nl))); } catch (e) { fail(new Error(`unreadable banner: ${buf.slice(0, nl)}`)); }
    });
    sidecar.on("error", (e) => { clearTimeout(timer); fail(new Error(`cannot start ${py}: ${e.message}`)); });
    sidecar.on("exit", (code) => { clearTimeout(timer); fail(new Error(`sidecar exited with ${code}`)); });
  });
  return `http://127.0.0.1:${banner.port}`;
}

async function call(base, path, body) {
  const r = await fetch(base + path, {
    method: body ? "POST" : "GET",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const json = await r.json().catch(() => null);
  if (!r.ok || !json || json.executed === false) throw new Error(`${path} answered ${r.status}: ${JSON.stringify(json)}`);
  return json;
}

// 문서 하나(주제를 주면 미러 노드가 생겨 나침반에 선다)와 개념 둘, 관계 하나, 에이전트 역할 하나.
async function seed(base) {
  const doc = await call(base, "/doc/put", { owner: OWNER, title: SEED.title, topic: SEED.topic,
    text: `# ${SEED.title}\n\n${SEED.phrase}\n` });
  await call(base, "/ingest", { owner: OWNER,
    nodes: [
      { id: SEED.node.id, type: "concept", title: SEED.node.title, domain: SEED.topic },
      { id: "concept:smoke-tide", type: "concept", title: "Tide", domain: SEED.topic },
      { id: SEED.role.id, type: "concept", title: SEED.role.title, domain: "vinc/agent",
        props: { team: "Design", team_role: "lead", job_title: SEED.role.jobTitle } },
    ],
    edges: [{ from: "concept:smoke-lighthouse", to: "concept:smoke-tide", type: "RELATES_TO" }] });
  return doc.doc_id;
}

// 앱을 짓고 낸다. 프록시 대상은 설정을 읽을 때 정해지므로 먼저 환경에 둔다.
async function serveApp(sidecarBase) {
  process.env.VINC_SIDECAR = sidecarBase;
  process.chdir(app);
  const { build, preview } = await import("vite");
  const configFile = join(app, "vite.config.ts");
  let outDir = process.env.VINC_SMOKE_DIST && resolve(process.env.VINC_SMOKE_DIST);
  if (!outDir) {
    outDir = join(scratch, "dist");
    await build({ root: app, configFile, logLevel: "error", build: { outDir, emptyOutDir: true } });
  }
  server = await preview({ root: app, configFile, logLevel: "warn", build: { outDir },
    preview: { host: "127.0.0.1", port: 0, strictPort: false, open: false } });
  const url = server.resolvedUrls?.local?.[0];
  if (!url) throw new Error("vite preview did not report its address");
  return url.replace(/\/$/, "");
}

const problems = [];      // 잡히지 않은 예외 · 콘솔 오류 · 막은 바깥 요청
let seen = 0;
const timings = [];

async function step(name, page, body) {
  const t0 = Date.now();
  try {
    await body();
    if (problems.length > seen) throw new Error(problems.slice(seen).join("\n  "));
  } catch (e) {
    await mkdir(out, { recursive: true });
    const shot = join(out, `${name.replace(/[^a-z0-9]+/gi, "-")}.png`);
    await page.screenshot({ path: shot, fullPage: true }).catch(() => {});
    await context?.tracing.stop({ path: join(out, "trace.zip") }).catch(() => {});
    context = null;
    const err = new Error(`smoke step "${name}" failed: ${e?.message || e}\n  screenshot: ${shot}\n  trace: ${join(out, "trace.zip")}`);
    err.reported = true;
    throw err;
  }
  seen = problems.length;
  timings.push([name, Date.now() - t0]);
  say(`ok  ${name} (${Date.now() - t0} ms)`);
}

async function main() {
  const { chromium } = await import("playwright");
  say("starting the sidecar on an empty store");
  const sidecarBase = await startSidecar();
  const docId = await seed(sidecarBase);
  say(`seeded document ${docId} at ${sidecarBase}`);
  const base = await serveApp(sidecarBase);
  say(`app served at ${base}`);

  browser = await chromium.launch({ headless: process.env.VINC_SMOKE_HEADED !== "1",
    ...(process.env.VINC_SMOKE_CHROME ? { executablePath: process.env.VINC_SMOKE_CHROME } : {}) });
  context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await context.tracing.start({ screenshots: true, snapshots: true });
  // 앱이 브라우저 dev 에서 읽는 접속 정보: 같은 출처(프록시)와 이 사이드카의 토큰. 언어는 영어로 고정한다.
  await context.addInitScript(({ token }) => {
    localStorage.setItem("vinc.sidecar.token", token);
    localStorage.setItem("vinc.lang", "en");
  }, { token });
  // 루프백 밖으로는 나가지 않는다. 나가려 한 요청은 막고 문제로 적는다.
  await context.route("**/*", (route) => {
    const u = new URL(route.request().url());
    if (["127.0.0.1", "localhost"].includes(u.hostname) || u.protocol === "data:" || u.protocol === "blob:")
      return route.continue();
    problems.push(`request outside loopback: ${u.href}`);
    return route.abort();
  });
  const page = await context.newPage();
  page.on("pageerror", (e) => problems.push(`uncaught: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") problems.push(`console error: ${m.text()}`); });
  page.setDefaultTimeout(STEP_MS);

  await step("shell mounts", page, async () => {
    await page.goto(`${base}/#home`);
    await page.locator("#lnb").waitFor({ state: "visible" });
    await page.locator("#surface .home").first().waitFor();
    await page.locator("#tb-to-graph").waitFor({ state: "visible" });
  });

  const row = page.locator(".home-row", { hasText: SEED.title });
  await step("home lists the seeded document", page, async () => {
    await row.first().waitFor({ state: "visible" });
  });

  await step("document opens with its text", page, async () => {
    await row.first().click();
    await page.waitForFunction((id) => location.hash.startsWith(`#home/doc/${id}`), docId);
    await page.locator("#home-ed", { hasText: SEED.phrase }).waitFor({ state: "visible" });
  });

  await step("graph draws the compass", page, async () => {
    await page.locator("#tb-to-graph").click();
    await page.waitForFunction(() => /^#view(\/|$)/.test(location.hash));
    await page.locator("#cmp-g #cmp-nodes .node").first().waitFor({ state: "visible" });
  });

  await step("search returns a row", page, async () => {
    await page.evaluate(() => { location.hash = "#search"; });
    const q = page.locator("#search-form #q");
    await q.waitFor({ state: "visible" });
    await q.fill(SEED.query);
    await q.press("Enter");
    await page.locator("#search-body .list-row").first().waitFor({ state: "visible" });
  });

  // 해시로 화면을 바꾼다. 셸은 한 번 떠 있으므로 페이지를 다시 열지 않는다.
  const go = (hash) => page.evaluate((h) => { location.hash = h; }, hash);
  const card = page.locator(".float-card");

  await step("node card opens its rail", page, async () => {
    await go(`#view/trace/${encodeURIComponent(SEED.node.id)}`);
    await card.locator("#c-open").waitFor({ state: "visible" });
    await card.locator("#c-open").click();
    await page.locator("#view-detail:not([hidden])", { hasText: SEED.node.title }).waitFor({ state: "visible" });
  });

  await step("reader shows the document text", page, async () => {
    await go(`#view/doc/${encodeURIComponent(docId)}`);
    await page.locator("#view-detail #rdr-host .cm-editor").waitFor({ state: "visible" });
    await page.locator("#view-detail #rdr-host", { hasText: SEED.phrase }).waitFor({ state: "visible" });
  });

  await step("share sheet opens and closes", page, async () => {
    await go(`#view/trace/${encodeURIComponent(SEED.node.id)}`);
    await card.locator("#c-share").waitFor({ state: "visible" });
    await card.locator("#c-share").click();
    await page.locator(".sheet-scrim").first().waitFor({ state: "visible" });
    await page.keyboard.press("Escape");
    await page.locator(".sheet-scrim").first().waitFor({ state: "detached" });
  });

  await step("settings opens its appearance pane", page, async () => {
    await go("#home");
    await page.locator("#rail-settings").click();
    const appearance = page.locator('[data-cat="appearance"]');
    await appearance.waitFor({ state: "visible" });
    await appearance.click();
    await page.locator("[data-theme-opt]").first().waitFor({ state: "visible" });
    await page.keyboard.press("Escape");
    await page.locator(".sheet-scrim").first().waitFor({ state: "detached" });
  });

  await step("approvals page renders", page, async () => {
    await go("#approvals");
    await page.locator("#surface .approvals .hint").first().waitFor({ state: "visible" });
  });

  await step("agents board lists the seeded role", page, async () => {
    await go("#home");
    await page.locator('#rail-kinds [data-rail="agent"]').click();
    await page.locator(".agent-card", { hasText: SEED.role.jobTitle }).first().waitFor({ state: "visible" });
  });

  // 마지막 걸음 뒤에 들어온 오류도 놓치지 않는다: 열린 화면의 비동기 작업이 끝날 때까지 기다린 뒤 다시 본다.
  await step("no late errors", page, async () => {
    await page.waitForLoadState("networkidle");
  });
  await context.tracing.stop();
  context = null;
}

let failed = false;
try {
  await main();
  say(`passed: ${timings.map(([n, ms]) => `${n} ${ms} ms`).join(" · ")}`);
} catch (e) {
  failed = true;
  console.error(e?.reported ? e.message : `smoke setup failed: ${e?.stack || e}`);
  if (problems.length) console.error(`problems seen:\n  ${problems.join("\n  ")}`);
  const tail = sidecarLog.join("").split("\n").slice(-30).join("\n");
  if (tail.trim()) console.error(`sidecar stderr (last lines):\n${tail}`);
} finally {
  await context?.tracing.stop().catch(() => {});
  await browser?.close().catch(() => {});
  await server?.close().catch(() => {});
  if (sidecar && sidecar.exitCode === null) {
    sidecar.kill("SIGTERM");
    await new Promise((r) => { const t = setTimeout(() => { sidecar.kill("SIGKILL"); r(); }, 10_000); sidecar.once("exit", () => { clearTimeout(t); r(); }); });
  }
  // 임시 폴더는 지운다. 실패 산출물이 그 안에 있으면 산출물 폴더만 남긴다(사람이 열어 봐야 한다).
  const keepOut = failed && out === join(scratch, "output");
  for (const name of keepOut ? ["db", "dist"] : [""])
    await rm(join(scratch, name), { recursive: true, force: true }).catch(() => {});
  say(`${failed ? "FAILED" : "done"} in ${((Date.now() - started) / 1000).toFixed(1)} s`);
  process.exit(failed ? 1 : 0);
}
