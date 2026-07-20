import { cfg, WEB } from "../api";
import { COPY } from "../copy";

/** Settings — 중앙 모달 + 상단 탭(GNB). 4탭:
 *  · Account   계정 연결(디바이스 플로우) + 그래프 동기화 + Theme + Language + Lock
 *  · Connections MCP(로컬 Claude Desktop/Code 연결·해제) — 셸 전용
 *  · Data      로컬 데이터 폴더/백업
 *  · Advanced  작업공간(owner)·엔진·시크릿·수동 동기화 키 — 셸 전용
 *  웹 Vinc(호스티드)는 셸 전용 탭(Connections/Advanced)과 계정연결·동기화 섹션을 숨긴다.
 *
 *  계정 연결(디바이스 플로우): 회원 키 복붙 대체 — [계정 연결]이 시스템 브라우저로
 *  vincs.app/connect-app 을 열고, 발급된 vinc_sk_* 가 사이드카 루프백으로 돌아오면
 *  폴링으로 1회 수령해 localStorage 에 저장한다(B1 유지 — 앱은 계정 API 미호출). */
export async function renderSettings(el: HTMLElement) {
  const U = COPY.ui.settingsUi;
  // 셸이면 계정/MCP/동기화용 API 를 미리 로드(웹은 불요).
  const mod = WEB ? null : await import("../api");
  const api = mod?.api;
  const pollJob = mod?.pollJob;

  type TabId = "account" | "connections" | "data" | "advanced";
  const TABS: { id: TabId; label: string }[] = [
    { id: "account", label: U.tabAccount },
    ...(!WEB ? [{ id: "connections" as TabId, label: U.tabConnections }] : []),
    { id: "data", label: U.tabData },
    ...(!WEB ? [{ id: "advanced" as TabId, label: U.tabAdvanced }] : []),
  ];
  let activeTab: TabId = "account";
  let connectAbort = false;   // 재렌더/해제 시 폴링 중단

  // ── 모달 크롬(탭바 + 닫기) ────────────────────────────────────────────────
  function shell() {
    el.innerHTML = `
      <div class="settings-head">
        <div class="settings-tabs" role="tablist">
          ${TABS.map((t) => `<button class="settings-tab${t.id === activeTab ? " active" : ""}"
            data-tab="${t.id}" role="tab">${t.label}</button>`).join("")}
        </div>
        <button class="settings-close" id="settings-x" aria-label="${U.close}">✕</button>
      </div>
      <div class="settings-body" id="settings-body"></div>`;
    // 닫기 = Esc 디스패치(닫힘 로직은 main.ts 소유 — 결합 최소화).
    el.querySelector("#settings-x")!.addEventListener("click", () =>
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })));
    el.querySelectorAll<HTMLElement>(".settings-tab").forEach((b) =>
      b.addEventListener("click", () => {
        activeTab = b.dataset.tab as TabId;
        shell();
      }));
    renderBody();
  }

  function renderBody() {
    const body = el.querySelector<HTMLElement>("#settings-body")!;
    if (activeTab === "account") renderAccount(body);
    else if (activeTab === "connections") renderConnections(body);
    else if (activeTab === "data") renderData(body);
    else renderAdvanced(body);
  }

  // 세그먼트 빌더(테마·언어) — 활성 표시.
  const themeSeg = () => ["dark", "light", "auto"].map((t) =>
    `<button class="chip${(localStorage.getItem("vinc.theme") || "dark") === t ? " active" : ""}"
      data-theme-opt="${t}">${t}</button>`).join("");
  const langSeg = () => (["en", "ko"] as const).map((l) =>
    `<button class="chip${cfg.lang === l ? " active" : ""}"
      data-lang-opt="${l}">${l === "en" ? "English" : "한국어"}</button>`).join("");

  function wireThemeLang(body: HTMLElement) {
    body.querySelectorAll<HTMLElement>("[data-lang-opt]").forEach((b) =>
      b.addEventListener("click", () => {   // D42 — 언어 전환은 전 표면 재렌더 필요 → reload
        cfg.lang = b.dataset.langOpt as "en" | "ko";
        location.reload();
      }));
    body.querySelectorAll<HTMLElement>("[data-theme-opt]").forEach((b) =>
      b.addEventListener("click", async () => {
        localStorage.setItem("vinc.theme", b.dataset.themeOpt!);
        const { applyTheme } = await import("../main");
        applyTheme();
        renderAccount(body);   // 활성 칩 갱신
      }));
  }

  // ── Account 탭 ────────────────────────────────────────────────────────────
  function renderAccount(body: HTMLElement) {
    // 웹 Vinc: 로그인 계정을 @username(없으면 이메일)으로 표시. 카피는 셸 편집 충돌 회피 위해 인라인.
    const ko = cfg.lang === "ko";
    const W = ko
      ? { acct: "계정", none: "로그인되어 있지 않아요.", manage: "계정 관리", signedAs: (w: string) => "로그인: " + w }
      : { acct: "Account", none: "Not signed in.", manage: "Manage account", signedAs: (w: string) => "Signed in as " + w };
    body.innerHTML = `
      ${WEB ? `
      <label>${W.acct}</label>
      <div id="web-acct" class="hint" style="font-size:.85rem">…</div>
      <hr class="settings-sep" />` : ""}
      ${!WEB ? `
      <label>${U.account}</label>
      <p class="hint">${U.syncHint}</p>
      <div id="acct-box"></div>
      <p class="hint" style="margin-top:.5rem">${U.syncDomains}</p>
      <div id="sync-domains" class="hint" style="font-size:.8rem">…</div>
      <p><button class="btn" id="sync-now">${U.syncNow}</button></p>
      <div id="sync-result" class="hint" style="font-size:.78rem"></div>
      <hr class="settings-sep" />` : ""}
      <label>${U.appearance}</label>
      <label>${U.theme}</label>
      <p><span class="seg">${themeSeg()}</span></p>
      <label>${U.language}</label>
      <p><span class="seg">${langSeg()}</span></p>
      <hr class="settings-sep" />
      <label>${U.lock}</label><p class="hint">${COPY.settings.lock}</p>`;
    wireThemeLang(body);
    if (WEB) {
      // 웹모드: /api/me 로 로그인 계정 표시(@username 우선). 연결/동기화 섹션은 없음(셸 전용).
      const box = body.querySelector<HTMLElement>("#web-acct");
      if (box) {
        fetch("/api/me", { credentials: "include" })
          .then((r) => (r.ok ? r.json() : null))
          .then((d) => {
            const u = d && d.user;
            if (!u) { box.textContent = W.none; return; }
            const esc = (s: string) =>
              String(s).replace(/[<>&"]/g, (m) => (({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;" }) as Record<string, string>)[m]!);
            const who = u.username ? "@" + u.username : u.email;
            box.innerHTML = `${W.signedAs(esc(who))} · <a href="/account/" target="_blank" rel="noopener">${W.manage}</a>`;
          })
          .catch(() => { if (box) box.textContent = ""; });
      }
      return;
    }
    if (!api || !pollJob) return;

    // 계정 연결(디바이스 플로우) — 키는 이 기기 localStorage 에만.
    const acctBox = body.querySelector<HTMLElement>("#acct-box")!;
    function drawAcct() {
      const key = (localStorage.getItem("vinc.sync.key") || "").trim();
      if (key) {
        // 공개 핸들(009) 우선 표시, 없으면 이메일, 그마저 없으면 키 앞부분.
        const username = localStorage.getItem("vinc.sync.username") || "";
        const email = localStorage.getItem("vinc.sync.email") || "";
        const who = username ? `@${username}` : (email || key.slice(0, 12) + "…");
        acctBox.innerHTML = `
          <p>${U.acctConnected(who)}
             <button class="btn" id="acct-disc" style="margin-left:.5rem">${U.acctDisconnect}</button></p>
          <p class="hint">${U.acctDisconnectHint}</p>`;
        acctBox.querySelector("#acct-disc")!.addEventListener("click", () => {
          connectAbort = true;
          localStorage.removeItem("vinc.sync.key");
          localStorage.removeItem("vinc.sync.email");
          localStorage.removeItem("vinc.sync.username");
          renderAccount(body);
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
        const { state } = await api!.connectStart();
        const port = new URL(cfg.base).port || "8787";
        const url = `https://vincs.app/connect-app/#port=${port}&state=${encodeURIComponent(state)}`;
        const invoke = (window as any).__TAURI__?.core?.invoke;
        if (typeof invoke === "function") await invoke("plugin:shell|open", { path: url });
        else window.open(url, "_blank");
        acctResult.textContent = U.acctConnecting;
        connectAbort = false;
        for (let i = 0; i < 300 && !connectAbort; i++) {   // 2s × 300 = TTL(10분)
          await new Promise((r) => setTimeout(r, 2000));
          const st = await api!.connectStatus(state).catch(() => null);
          if (!st) continue;
          if (st.status === "delivered") {
            localStorage.setItem("vinc.sync.key", st.key);
            if (st.email) localStorage.setItem("vinc.sync.email", st.email);
            if (st.username) localStorage.setItem("vinc.sync.username", st.username);
            else localStorage.removeItem("vinc.sync.username"); // 핸들 미설정 계정 → 이전 값 잔존 방지
            renderAccount(body);
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

    // 그래프 동기화(S5) — 도메인 선택 + 지금 동기화.
    const domBox = body.querySelector<HTMLElement>("#sync-domains")!;
    const syncResult = body.querySelector<HTMLElement>("#sync-result")!;
    async function loadDomains() {
      try {
        const res = await api!.syncDomains(cfg.owner);
        const rows: Array<{ domain: string }> = res.domains || [];
        const selected = new Set<string>(res.selected || []);
        if (!rows.length) { domBox.textContent = U.syncNoDomains; return; }
        domBox.innerHTML = rows.map((r) => {
          const on = selected.has(r.domain) ? " checked" : "";
          const safe = r.domain.replace(/"/g, "&quot;");
          return `<label style="display:block;font-weight:400;margin:.15rem 0">
            <input type="checkbox" data-sync-dom="${safe}"${on} /> ${safe}</label>`;
        }).join("");
        domBox.querySelectorAll<HTMLInputElement>("[data-sync-dom]").forEach((cb) =>
          cb.addEventListener("change", async () => {
            const picked = [...domBox.querySelectorAll<HTMLInputElement>("[data-sync-dom]:checked")]
              .map((c) => c.dataset.syncDom!);
            try { await api!.setSyncDomains(picked); } catch { /* 사이드카 미가동 무시 */ }
          }));
      } catch { domBox.textContent = U.syncNoDomains; }
    }
    void loadDomains();

    body.querySelector("#sync-now")!.addEventListener("click", async () => {
      const key = (localStorage.getItem("vinc.sync.key") || "").trim();
      if (!key) { syncResult.textContent = U.syncNeedKey; return; }
      syncResult.textContent = U.syncing;
      try {
        const job = await api!.syncNow(key, cfg.owner);
        const done = await pollJob!(job.job_id, (s) => {
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

  // ── Connections 탭 (MCP, 셸 전용) ─────────────────────────────────────────
  function renderConnections(body: HTMLElement) {
    body.innerHTML = `
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
      <div id="mcp-result" class="hint" style="font-size:.78rem;word-break:break-all"></div>`;
    const mcpResult = body.querySelector<HTMLElement>("#mcp-result")!;
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
    body.querySelector("#mcp-desktop")!.addEventListener("click", () => mcpAction("install", "desktop"));
    body.querySelector("#mcp-code")!.addEventListener("click", () => mcpAction("install", "code"));
    body.querySelector("#mcp-rm-desktop")!.addEventListener("click", () => mcpAction("uninstall", "desktop"));
    body.querySelector("#mcp-rm-code")!.addEventListener("click", () => mcpAction("uninstall", "code"));
  }

  // ── Data 탭 ───────────────────────────────────────────────────────────────
  function renderData(body: HTMLElement) {
    body.innerHTML = `
      <label>${U.data}</label><p class="hint">${COPY.settings.data}</p>`;
  }

  // ── Advanced 탭 (셸 전용) ─────────────────────────────────────────────────
  function renderAdvanced(body: HTMLElement) {
    body.innerHTML = `
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
      <input id="s-token" value="${cfg.token}" />`;
    body.querySelector<HTMLInputElement>("#s-base")!.addEventListener("change", (e) =>
      (cfg.base = (e.target as HTMLInputElement).value));
    body.querySelector<HTMLInputElement>("#s-token")!.addEventListener("change", (e) =>
      (cfg.token = (e.target as HTMLInputElement).value));
    body.querySelector<HTMLInputElement>("#s-owner")!.addEventListener("change", (e) =>
      (cfg.owner = (e.target as HTMLInputElement).value));
    // 수동 키: localStorage 만 갱신(Account 탭이 다시 열릴 때 최신 상태를 읽음).
    const keyInput = body.querySelector<HTMLInputElement>("#s-sync-key")!;
    keyInput.addEventListener("change", () => {
      localStorage.setItem("vinc.sync.key", keyInput.value.trim());
      localStorage.removeItem("vinc.sync.email");   // 수동 키 = 이메일 미상
    });
  }

  shell();
}
