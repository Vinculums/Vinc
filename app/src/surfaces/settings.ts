import { cfg, teamsApi, WEB, shellInvoke } from "../api";
import { COPY } from "../copy";

/** Settings — 중앙 모달 + 상단 탭(GNB). 4탭:
 *  · Account   계정 연결(디바이스 플로우) + 그래프 동기화 + Theme + Language + Lock
 *  · Connections MCP(로컬 MCP 호스트 연결·해제) — 셸 전용
 *  · Data      로컬 데이터 폴더/백업
 *  · Advanced  작업공간(owner)·엔진·시크릿·수동 동기화 키 — 셸 전용
 *  웹 Vinc(호스티드)는 셸 전용 탭(Connections/Advanced)과 계정연결·동기화 섹션을 숨긴다.
 *
 *  계정 연결(디바이스 플로우): 회원 키 복붙 대체 — [계정 연결]이 시스템 브라우저로
 *  vincs.io/connect-app 을 열고, 발급된 vinc_sk_* 가 사이드카 루프백으로 돌아오면
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
  // KO 비활성(2026-08-15): 언어 토글 UI 통째로 숨김(호출부 제거, api.ts cfg.lang 참조).
  // const langSeg = () => (["en", "ko"] as const).map((l) =>
  //   `<button class="chip${cfg.lang === l ? " active" : ""}"
  //     data-lang-opt="${l}">${l === "en" ? "English" : "한국어"}</button>`).join("");

  function wireThemeLang(body: HTMLElement) {
    // KO 비활성(2026-08-15): 언어 토글 자체를 숨겼으므로(langSeg 미사용) 배선도 죽여 둔다.
    // 재활성화 시 langSeg()/이 블록의 주석을 함께 풀 것.
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
      ? { acct: "계정", none: "로그인되어 있지 않아요.", manage: "계정 관리", signedAs: (w: string) => "로그인: " + w,
          space: "스페이스", spacePersonal: "개인",
          spaceHint: "지금 작업 중인 그래프입니다. 모든 화면이 여기를 따릅니다.",
          spaceNone: "참여 중인 활성 팀이 없습니다." }
      : { acct: "Account", none: "Not signed in.", manage: "Manage account", signedAs: (w: string) => "Signed in as " + w,
          space: "Space", spacePersonal: "Personal",
          spaceHint: "Which graph you are working in. Every surface follows this.",
          spaceNone: "You are not in an active team yet." };
    body.innerHTML = `
      ${WEB ? `
      <label>${W.acct}</label>
      <div id="web-acct" class="hint" style="font-size:.85rem">…</div>
      <label>${W.space}</label>
      <p class="hint">${W.spaceHint}</p>
      <p><select id="space-pick"><option value="">${W.spacePersonal}</option></select></p>
      <div id="space-note" class="hint" style="font-size:.8rem"></div>
      <hr class="settings-sep" />` : ""}
      ${!WEB ? `
      <!-- 저장 위치 — 상단 바에 상시로 떠 있던 "● 로컬" 칩이 여기로 왔다(발주자 지시
           2026-08-05). 바뀌지 않는 사실은 상시 점유할 자격이 없다: 궁금할 때 와서 보는
           것으로 충분하다. 데스크탑 분기 안에만 두는 것도 의도다 — 웹 Vinc 의 데이터는
           계정에 있으므로 그쪽에서 "로컬"은 애초에 참이 아니었다(칩은 양쪽에 다 떴다). -->
      <label>${U.storage}</label>
      <p class="rail-sync"><span class="sync-dot"></span><span>${COPY.ui.common.local}</span></p>
      <hr class="settings-sep" />
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
      // 스페이스 전환 — 개인 그래프와 팀 그래프는 별개 네임스페이스다. 여기서 고른
      // 것이 View·Search·Records 전부에 걸리므로, 표면 하나가 아니라 계정 설정에 둔다.
      // 정지·연체 팀은 목록에 넣지 않는다 — 계정이 서명을 멈춰 어차피 거부된다.
      const pick = body.querySelector<HTMLSelectElement>("#space-pick");
      const note = body.querySelector<HTMLElement>("#space-note");
      if (pick) {
        void teamsApi.list().then((rows) => {
          const active = rows.filter((t) => t.status === "active");
          for (const t of active) {
            const o = document.createElement("option");
            o.value = t.id; o.textContent = t.name;
            if (t.id === cfg.team) o.selected = true;
            pick.appendChild(o);
          }
          if (note && !active.length) note.textContent = W.spaceNone;
        });
        pick.addEventListener("change", () => {
          cfg.team = pick.value;
          // 스페이스는 전역이라 부분 갱신하지 않는다 — 어떤 표면이 옛 스코프의
          // 데이터를 들고 남으면 지금 어느 그래프를 보는지 알 수 없게 된다.
          location.reload();
        });
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
        const url = `https://vincs.io/connect-app/#port=${port}&state=${encodeURIComponent(state)}`;
        // Q2: 전역 __TAURI__ 대신 셸일 때만 동적 import. 셸이 아니면 새 탭.
        const invoke = await shellInvoke();
        if (invoke) await invoke("plugin:shell|open", { path: url });
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
  // 등록 가능한 호스트 — 사이드카 `vinc_mcp.install.CLIENTS` 와 짝. 여기 없는
  // 호스트도 서버는 그대로 동작한다(수동 등록 안내는 U.mcpOtherHint).
  const MCP_HOSTS = ["desktop", "code", "cursor", "codex"] as const;

  function renderConnections(body: HTMLElement) {
    // 호스트 하나 = 행 하나(2026-08-05 재설계). 여태 `.seg` 안에 연결 버튼 4개,
    // 다른 `.seg` 안에 해제 버튼 4개를 넣었다 — `.seg` 는 **여럿 중 하나를 고르는**
    // 컨트롤이므로 독립 동작 8개를 담을 그릇이 아니었고(그래서 한 덩어리로 뭉쳐
    // 보였다), 같은 호스트의 연결과 해제가 화면 위아래로 갈라져 있어 "Claude Desktop
    // 을 어떻게 하지"를 두 곳에서 찾아야 했다. 행이 되면 그 둘이 한 줄에 있다.
    const rows = MCP_HOSTS.map((h) => `
      <li class="row">
        <span class="row-name">${U.mcpHosts[h]}</span>
        <span class="row-acts">
          <button class="btn btn-quiet" id="mcp-rm-${h}"
                  aria-label="${U.mcpRemove(U.mcpHosts[h])}">${U.mcpRemoveAct}</button>
          <button class="btn" id="mcp-add-${h}"
                  aria-label="${U.mcpConnect(U.mcpHosts[h])}">${U.mcpConnectAct}</button>
        </span>
      </li>`).join("");
    body.innerHTML = `
      <label>${U.mcp}</label><p class="hint">${COPY.settings.mcp}</p>
      <ul class="rowlist">${rows}</ul>
      <p class="hint hint-sm">${U.mcpOtherHint}</p>
      <div id="mcp-result" class="hint hint-sm break-all"></div>

      <label>${U.cli}</label><p class="hint">${U.cliHint}</p>
      <ul class="rowlist">
        <li class="row">
          <span class="row-name" id="cli-state">${U.cliChecking}</span>
          <span class="row-acts">
            <button class="btn btn-quiet" id="cli-rm" hidden>${U.cliRemoveAct}</button>
            <button class="btn" id="cli-add" hidden>${U.cliInstallAct}</button>
          </span>
        </li>
      </ul>
      <div id="cli-note" class="hint hint-sm break-all"></div>`;
    const mcpResult = body.querySelector<HTMLElement>("#mcp-result")!;
    async function mcpAction(action: "install" | "uninstall", client: string) {
      const invoke = await shellInvoke();
      if (!invoke) { mcpResult.textContent = U.mcpNoShell; return; }
      mcpResult.textContent = action === "install" ? U.mcpInstalling : U.mcpRemoving;
      const host = U.mcpHosts[client] || client;
      try {
        await invoke(action === "install" ? "mcp_install" : "mcp_uninstall", { client });
        mcpResult.textContent = action === "install" ? U.mcpDone(host) : U.mcpRemoved(host);
      } catch (e: any) {
        mcpResult.textContent = `${U.mcpFail}: ${(e && e.message) || e}`;
      }
    }
    for (const h of MCP_HOSTS) {
      body.querySelector(`#mcp-add-${h}`)!.addEventListener("click", () => mcpAction("install", h));
      body.querySelector(`#mcp-rm-${h}`)!.addEventListener("click", () => mcpAction("uninstall", h));
    }

    // ── 터미널에서 `vinc` 부르기 ────────────────────────────────────────────
    // 리눅스 패키지(deb·rpm)는 이미 `/usr/bin/vinc` 를 깔아 두므로 이 행이 할 일이
    // 없고, macOS·AppImage 는 설치 훅이 없어 여기서 링크를 건다. Windows 는 설치기
    // 몫이라 `supported:false` 로 와서 행 자체를 숨긴다.
    const cliState = body.querySelector<HTMLElement>("#cli-state")!;
    const cliNote = body.querySelector<HTMLElement>("#cli-note")!;
    const cliAdd = body.querySelector<HTMLButtonElement>("#cli-add")!;
    const cliRm = body.querySelector<HTMLButtonElement>("#cli-rm")!;
    type CliStatus = { supported: boolean; linked: boolean; path: string; dir: string; on_path: boolean };
    function paintCli(st: CliStatus | null, err?: string) {
      if (err) { cliState.textContent = `${U.cliFail}: ${err}`; return; }
      if (!st || !st.supported) {
        // 이 플랫폼에서 할 일이 없으면 조용히 접는다 — 못 누르는 버튼을 남기지 않는다.
        cliState.closest("li")!.hidden = true;
        cliNote.textContent = "";
        return;
      }
      cliState.textContent = st.linked ? U.cliLinked(st.path) : U.cliMissing;
      // 안내 문장은 여기서 짓는다 — 러스트는 사실(dir·on_path)만 준다.
      cliNote.textContent = st.linked && !st.on_path ? U.cliPathWarn(st.dir) : "";
      cliAdd.hidden = st.linked;
      cliRm.hidden = !st.linked;
    }
    async function cliAction(cmd: "cli_link_status" | "cli_link_install" | "cli_link_remove") {
      const invoke = await shellInvoke();
      if (!invoke) { paintCli(null); return; }   // 웹 Vinc — 행 자체가 의미 없다
      try { paintCli(await invoke(cmd) as any); }
      catch (e: any) { paintCli(null, (e && e.message) || String(e)); }
    }
    cliAdd.addEventListener("click", () => cliAction("cli_link_install"));
    cliRm.addEventListener("click", () => cliAction("cli_link_remove"));
    void cliAction("cli_link_status");
  }

  // ── Data 탭 ───────────────────────────────────────────────────────────────
  function renderData(body: HTMLElement) {
    // Packs 표면(전체 백업·커뮤니티 게시)은 상단 바에 가져오기·공유가 올라온 뒤
    // 보이는 진입점이 없어졌다. 데이터 관리의 맥락인 여기서 열 수 있게 둔다.
    body.innerHTML = `
      <label>${U.data}</label><p class="hint">${COPY.settings.data}</p>
      <p><button class="btn" id="s-packs">${COPY.ui.packsUi.openPacks}</button></p>`;
    body.querySelector("#s-packs")!.addEventListener("click", () => {
      (document.getElementById("settings-btn") as HTMLElement).click();   // 팝오버 닫기
      location.hash = "#packs";
    });
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
