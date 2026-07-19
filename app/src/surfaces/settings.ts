import { cfg, WEB } from "../api";
import { COPY } from "../copy";

/** Settings popover (spec: vinc.d.settings v0.1) — exactly three items: Lock (D7
 *  opt-in), MCP observation, Data. Plus dev-scaffold sidecar fields; developer
 *  config (embedding model, driver) stays env-only by design.
 *
 *  계정 연결(디바이스 플로우): 회원 키 복붙을 대체한다 — [계정 연결] 이 시스템
 *  브라우저로 vincs.app/connect-app 을 열고(로그인·승인·발급은 전부 계정 오리진),
 *  발급된 vinc_sk_* 가 사이드카 루프백 메일박스로 돌아오면 폴링으로 1회 수령해
 *  localStorage 에 저장한다. B1 유지 — 앱은 계정 API 를 호출하지 않는다.
 *  수동 키 붙여넣기·작업 공간(owner)·엔진 주소는 고급(Advanced)으로 강등. */
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
    <p class="hint" style="margin-top:.35rem">${U.mcpRemoveHint}</p>
    <p><span class="seg">
      <button class="btn" id="mcp-rm-desktop">${U.mcpRemoveDesktop}</button>
      <button class="btn" id="mcp-rm-code">${U.mcpRemoveCode}</button>
    </span></p>
    <div id="mcp-result" class="hint" style="font-size:.78rem;word-break:break-all"></div>
    <label>${U.sync}</label><p class="hint">${U.syncHint}</p>
    <div id="acct-box"></div>
    <p class="hint" style="margin-top:.5rem">${U.syncDomains}</p>
    <div id="sync-domains" class="hint" style="font-size:.8rem">…</div>
    <p><button class="btn" id="sync-now">${U.syncNow}</button></p>
    <div id="sync-result" class="hint" style="font-size:.78rem"></div>`}
    <label>${U.data}</label><p class="hint">${COPY.settings.data}</p>
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
      <label for="s-owner">${U.workspace}</label>
      <p class="hint">${U.workspaceHint}</p>
      <input id="s-owner" value="${cfg.owner}" />
      <label for="s-sync-key">${U.syncKey}</label>
      <p class="hint">${U.syncKeyHint}</p>
      <input id="s-sync-key" type="password" placeholder="vinc_sk_…"
             value="${(localStorage.getItem("vinc.sync.key") || "").replace(/"/g, "&quot;")}" />
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
  // 계정 연결·동기화). 웹은 owner 를 세션이 강제하고 로컬 사이드카가 없어 이 UI 자체를
  // 렌더하지 않았으므로(위 HTML 가드), 여기서 배선도 건너뛴다.
  if (WEB) return;

  el.querySelector<HTMLInputElement>("#s-base")!.addEventListener("change", (e) =>
    (cfg.base = (e.target as HTMLInputElement).value));
  el.querySelector<HTMLInputElement>("#s-token")!.addEventListener("change", (e) =>
    (cfg.token = (e.target as HTMLInputElement).value));
  el.querySelector<HTMLInputElement>("#s-owner")!.addEventListener("change", (e) =>
    (cfg.owner = (e.target as HTMLInputElement).value));

  // MCP — 이 설치본을 로컬 Claude 클라이언트(Desktop/Code)에 등록/해제. main.rs 의
  // mcp_install / mcp_uninstall 이 번들 사이드카 `mcp install|uninstall` 을 실행한다(셸 전용).
  const mcpResult = el.querySelector<HTMLElement>("#mcp-result")!;
  async function mcpAction(action: "install" | "uninstall", client: "desktop" | "code") {
    const invoke = (window as any).__TAURI__?.core?.invoke;
    if (typeof invoke !== "function") { mcpResult.textContent = U.mcpNoShell; return; }
    mcpResult.textContent = action === "install" ? U.mcpInstalling : U.mcpRemoving;
    try {
      await invoke(action === "install" ? "mcp_install" : "mcp_uninstall", { client });
      mcpResult.textContent = action === "install" ? U.mcpDone(client) : U.mcpRemoved(client);
    } catch (e: any) {
      mcpResult.textContent = `${U.mcpFail}: ${(e && e.message) || e}`;
    }
  }
  el.querySelector("#mcp-desktop")!.addEventListener("click", () => mcpAction("install", "desktop"));
  el.querySelector("#mcp-code")!.addEventListener("click", () => mcpAction("install", "code"));
  el.querySelector("#mcp-rm-desktop")!.addEventListener("click", () => mcpAction("uninstall", "desktop"));
  el.querySelector("#mcp-rm-code")!.addEventListener("click", () => mcpAction("uninstall", "code"));

  const { api, pollJob } = await import("../api");

  // ── 계정 연결(디바이스 플로우) ────────────────────────────────────────────
  // 키는 이 기기 localStorage 에만(민감정보 — cfg/서버 저장 안 함, 현행 유지).
  const acctBox = el.querySelector<HTMLElement>("#acct-box")!;
  let connectAbort = false;   // 재렌더/해제 시 폴링 중단

  function drawAcct() {
    const key = (localStorage.getItem("vinc.sync.key") || "").trim();
    if (key) {
      const who = localStorage.getItem("vinc.sync.email")
        || key.slice(0, 12) + "…";
      acctBox.innerHTML = `
        <p>${U.acctConnected(who)}
           <button class="btn" id="acct-disc" style="margin-left:.5rem">${U.acctDisconnect}</button></p>
        <p class="hint">${U.acctDisconnectHint}</p>`;
      acctBox.querySelector("#acct-disc")!.addEventListener("click", () => {
        connectAbort = true;
        localStorage.removeItem("vinc.sync.key");
        localStorage.removeItem("vinc.sync.email");
        renderSettings(el);
      });
    } else {
      acctBox.innerHTML = `
        <p><button class="btn" id="acct-connect">${U.acctConnect}</button></p>
        <p class="hint">${U.acctConnectHint}</p>
        <div id="acct-result" class="hint" style="font-size:.78rem"></div>`;
      acctBox.querySelector("#acct-connect")!.addEventListener("click", () => void startConnect());
    }
  }

  async function startConnect() {
    const acctResult = acctBox.querySelector<HTMLElement>("#acct-result")!;
    try {
      const { state } = await api.connectStart();
      // 콜백 포트 = 사이드카(셸: 실포트 주입 완료) 또는 dev 프록시(vite → 사이드카).
      const port = new URL(cfg.base).port || "8787";
      const url = `https://vincs.app/connect-app/#port=${port}&state=${encodeURIComponent(state)}`;
      const invoke = (window as any).__TAURI__?.core?.invoke;
      if (typeof invoke === "function") await invoke("plugin:shell|open", { path: url });
      else window.open(url, "_blank");            // 브라우저 dev 폴백
      acctResult.textContent = U.acctConnecting;
      connectAbort = false;
      for (let i = 0; i < 300 && !connectAbort; i++) {   // 2s × 300 = TTL(10분)
        await new Promise((r) => setTimeout(r, 2000));
        const st = await api.connectStatus(state).catch(() => null);
        if (!st) continue;
        if (st.status === "delivered") {
          localStorage.setItem("vinc.sync.key", st.key);
          if (st.email) localStorage.setItem("vinc.sync.email", st.email);
          renderSettings(el);
          return;
        }
        if (st.status === "expired") { acctResult.textContent = U.acctExpired; return; }
      }
      if (!connectAbort) acctResult.textContent = U.acctFail;
    } catch (e: any) {
      acctResult.textContent = `${U.acctFail}: ${(e && e.message) || e}`;
    }
  }
  drawAcct();

  // ── 계정 그래프 동기화(S5) ────────────────────────────────────────────────
  // 도메인 선택은 사이드카 정본(_SyncMeta)에 저장 — 오프라인 우선 약속의 UI 각인.
  const keyInput = el.querySelector<HTMLInputElement>("#s-sync-key")!;
  keyInput.addEventListener("change", () => {
    localStorage.setItem("vinc.sync.key", keyInput.value.trim());
    localStorage.removeItem("vinc.sync.email");   // 수동 키 = 이메일 미상
    drawAcct();
  });

  const domBox = el.querySelector<HTMLElement>("#sync-domains")!;
  const syncResult = el.querySelector<HTMLElement>("#sync-result")!;

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
