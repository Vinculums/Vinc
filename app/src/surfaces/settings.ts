import { cfg, WEB } from "../api";
import { COPY } from "../copy";

/** Settings popover (spec: vinc.d.settings v0.1) — exactly three items: Lock (D7
 *  opt-in), MCP observation, Data. Plus dev-scaffold sidecar fields; developer
 *  config (embedding model, driver) stays env-only by design. */
export async function renderSettings(el: HTMLElement) {
  const U = COPY.ui.settingsUi;
  el.innerHTML = `
    <h2>Settings</h2>
    <label>${U.lock}</label><p class="hint">${COPY.settings.lock}</p>
    ${WEB ? "" : `
    <label>${U.mcp}</label><p class="hint">${COPY.settings.mcp}</p>
    <p><span class="seg">
      <button class="btn" id="mcp-desktop">${U.mcpDesktop}</button>
      <button class="btn" id="mcp-code">${U.mcpCode}</button>
    </span></p>
    <div id="mcp-result" class="hint" style="font-size:.78rem;word-break:break-all"></div>
    <label>${U.sync}</label><p class="hint">${U.syncHint}</p>
    <label for="s-sync-key">${U.syncKey}</label>
    <p class="hint">${U.syncKeyHint}</p>
    <input id="s-sync-key" type="password" placeholder="vinc_sk_…"
           value="${(localStorage.getItem("vinc.sync.key") || "").replace(/"/g, "&quot;")}" />
    <p class="hint" style="margin-top:.5rem">${U.syncDomains}</p>
    <div id="sync-domains" class="hint" style="font-size:.8rem">…</div>
    <p><button class="btn" id="sync-now">${U.syncNow}</button></p>
    <div id="sync-result" class="hint" style="font-size:.78rem"></div>`}
    <label>${U.data}</label><p class="hint">${COPY.settings.data}</p>
    ${WEB ? "" : `
    <label for="s-owner">${U.workspace}</label>
    <p class="hint">${U.workspaceHint}</p>
    <input id="s-owner" value="${cfg.owner}" />`}
    <label>${U.theme}</label>
    <p><span class="seg">
      ${["dark", "light", "auto"].map((t) => `
        <button class="chip${(localStorage.getItem("vinc.theme") || "dark") === t ? " active" : ""}"
                data-theme-opt="${t}">${t}</button>`).join("")}
    </span></p>
    <label>${U.language}</label>
    <p><span class="seg">
      ${(["en", "ko"] as const).map((l) => `
        <button class="chip${cfg.lang === l ? " active" : ""}"
                data-lang-opt="${l}">${l === "en" ? "English" : "한국어"}</button>`).join("")}
    </span></p>
    ${WEB ? "" : `
    <details style="margin-top:var(--space-3,.75rem)">
      <summary class="hint" style="cursor:pointer">${U.advanced}</summary>
      <label for="s-base">${U.engine}</label>
      <input id="s-base" value="${cfg.base}" />
      <label for="s-token">${U.secret}</label>
      <p class="hint">${U.secretHint}</p>
      <input id="s-token" value="${cfg.token}" />
    </details>`}
  `;
  // D42 — 언어 전환: 전 표면 재렌더가 필요하므로 reload(팔레트·타이틀 포함)
  el.querySelectorAll<HTMLElement>("[data-lang-opt]").forEach((b) =>
    b.addEventListener("click", () => {
      cfg.lang = b.dataset.langOpt as "en" | "ko";
      location.reload();
    }));
  el.querySelectorAll<HTMLElement>("[data-theme-opt]").forEach((b) =>
    b.addEventListener("click", async () => {
      localStorage.setItem("vinc.theme", b.dataset.themeOpt!);
      const { applyTheme } = await import("../main");
      applyTheme();
      renderSettings(el);
    }));
  // 웹 Vinc(호스티드): 아래는 전부 셸/로컬 전용(사이드카 base·token·owner, MCP install,
  // 계정 키 동기화). 웹은 owner 를 세션이 강제하고 로컬 사이드카가 없어 이 UI 자체를
  // 렌더하지 않았으므로(위 HTML 가드), 여기서 배선도 건너뛴다.
  if (WEB) return;

  el.querySelector<HTMLInputElement>("#s-base")!.addEventListener("change", (e) =>
    (cfg.base = (e.target as HTMLInputElement).value));
  el.querySelector<HTMLInputElement>("#s-token")!.addEventListener("change", (e) =>
    (cfg.token = (e.target as HTMLInputElement).value));
  el.querySelector<HTMLInputElement>("#s-owner")!.addEventListener("change", (e) =>
    (cfg.owner = (e.target as HTMLInputElement).value));

  // MCP — 이 설치본을 로컬 Claude 클라이언트(Desktop/Code)에 등록. main.rs 의
  // mcp_install 커맨드가 번들 사이드카 `mcp install` 을 실행한다(셸에서만 동작).
  const mcpResult = el.querySelector<HTMLElement>("#mcp-result")!;
  async function connectMcp(client: "desktop" | "code") {
    const invoke = (window as any).__TAURI__?.core?.invoke;
    if (typeof invoke !== "function") { mcpResult.textContent = U.mcpNoShell; return; }
    mcpResult.textContent = U.mcpInstalling;
    try {
      await invoke("mcp_install", { client });
      mcpResult.textContent = U.mcpDone(client);
    } catch (e: any) {
      mcpResult.textContent = `${U.mcpFail}: ${(e && e.message) || e}`;
    }
  }
  el.querySelector("#mcp-desktop")!.addEventListener("click", () => connectMcp("desktop"));
  el.querySelector("#mcp-code")!.addEventListener("click", () => connectMcp("code"));

  // ── 계정 그래프 동기화(S5) ────────────────────────────────────────────────
  // 회원 키는 이 기기 localStorage 에만(민감정보 — cfg/서버 저장 안 함). 도메인 선택은
  // 사이드카 정본(_SyncMeta)에 저장 — 오프라인 우선 약속의 UI 각인.
  const keyInput = el.querySelector<HTMLInputElement>("#s-sync-key")!;
  keyInput.addEventListener("change", () =>
    localStorage.setItem("vinc.sync.key", keyInput.value.trim()));

  const domBox = el.querySelector<HTMLElement>("#sync-domains")!;
  const syncResult = el.querySelector<HTMLElement>("#sync-result")!;
  const { api, pollJob } = await import("../api");

  async function loadDomains() {
    try {
      const res = await api.syncDomains(cfg.owner);
      const rows: Array<{ domain: string }> = res.domains || [];
      const selected = new Set<string>(res.selected || []);
      if (!rows.length) { domBox.textContent = U.syncNoDomains; return; }
      domBox.innerHTML = rows.map((r) => {
        const d = r.domain;
        const on = selected.has(d) ? " checked" : "";
        const safe = d.replace(/"/g, "&quot;");
        return `<label style="display:block;font-weight:400;margin:.15rem 0">
          <input type="checkbox" data-sync-dom="${safe}"${on} /> ${safe}</label>`;
      }).join("");
      domBox.querySelectorAll<HTMLInputElement>("[data-sync-dom]").forEach((cb) =>
        cb.addEventListener("change", async () => {
          const picked = [...domBox.querySelectorAll<HTMLInputElement>("[data-sync-dom]:checked")]
            .map((c) => c.dataset.syncDom!);
          try { await api.setSyncDomains(picked); } catch { /* 사이드카 미가동 등 무시 */ }
        }));
    } catch {
      domBox.textContent = U.syncNoDomains;
    }
  }
  void loadDomains();

  el.querySelector("#sync-now")!.addEventListener("click", async () => {
    const key = (localStorage.getItem("vinc.sync.key") || "").trim();
    if (!key) { syncResult.textContent = U.syncNeedKey; return; }
    syncResult.textContent = U.syncing;
    try {
      const job = await api.syncNow(key, cfg.owner);
      const done = await pollJob(job.job_id, (s) => {
        if (s.status === "running") syncResult.textContent = U.syncing;
      });
      const r = done.result || {};
      const up = r?.push?.sent ?? 0;
      const down = (r?.pull?.applied ?? 0) + (r?.pull?.merged ?? 0);
      syncResult.textContent = U.syncDone(up, down);
      void loadDomains();
    } catch (e: any) {
      syncResult.textContent = `${U.syncFail}: ${(e && e.message) || e}`;
    }
  });
}
