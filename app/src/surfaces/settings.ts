import { cfg, teamsApi, WEB, shellInvoke } from "../api";
import { accountKey, accountWho, connectAccount, forgetAccount, hasAccount, keyIsOnDisk,
         setAccountKey } from "../account-connect";
import { COPY } from "../copy";
import { esc } from "../components";

/** Settings — 중앙 모달 + 상단 탭(GNB). 3탭:
 *  · Account   계정 연결(디바이스 플로우) + 그래프 동기화 + Theme + Language + Lock
 *  · Connections MCP(로컬 MCP 호스트 연결·해제) — 셸 전용
 *  · Advanced  작업공간(owner)·엔진·시크릿·수동 동기화 키 — 셸 전용
 *
 *  Data 탭은 없앴다(P15). 그 탭에는 `Packs 열기` 버튼 하나뿐이었고, 그 버튼은 상단
 *  바에 가져오기·공유가 올라간 뒤 고아가 된 표면에 걸어 둔 임시 구명줄이었다. 이제
 *  그 표면의 다섯 기능이 전부 Tools 에 있으므로 구명줄도 함께 걷는다.
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

  type TabId = "account" | "connections" | "advanced";
  const TABS: { id: TabId; label: string }[] = [
    { id: "account", label: U.tabAccount },
    ...(!WEB ? [{ id: "connections" as TabId, label: U.tabConnections }] : []),
    ...(!WEB ? [{ id: "advanced" as TabId, label: U.tabAdvanced }] : []),
  ];
  let activeTab: TabId = "account";

  // ── 탭바 + 몸 ─────────────────────────────────────────────────────────────
  //  ⚠ **닫기 버튼이 없다**(2026-09-09). 이 화면은 이제 시트 안에서 열리고
  //  (`openSheet`), 시트가 제목 줄에 ✕ 를 이미 갖고 있다 — 여기서 하나 더 그리면
  //  같은 상자에 닫기가 둘이 된다. Esc·스크림·✕ 세 탈출구도 시트의 것이다.
  function shell() {
    el.innerHTML = `
      <div class="settings-head">
        <div class="settings-tabs" role="tablist">
          ${TABS.map((t) => `<button class="settings-tab${t.id === activeTab ? " active" : ""}"
            data-tab="${t.id}" role="tab">${t.label}</button>`).join("")}
        </div>
      </div>
      <div class="settings-body" id="settings-body"></div>`;
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
    // 웹 Vinc: 로그인 계정을 @username(없으면 이메일)으로 표시.
    // ⚠ 이 블록의 카피는 예전에 여기 EN/KO 사전으로 인라인돼 있었다. 사유는
    // "셸 편집 충돌 회피"였는데 그 충돌은 이미 없고, 덱 밖 카피는 언어 프록시도
    // 게이트도 타지 않는다(tests/test_copy_parity.py 가 이제 막는다).
    body.innerHTML = `
      ${WEB ? `
      <label>${U.account}</label>
      <div id="web-acct" class="hint" style="font-size:.85rem">…</div>
      <label>${U.space}</label>
      <p class="hint">${U.spaceHint}</p>
      <p><select id="space-pick"><option value="">${U.spacePersonal}</option></select></p>
      <div id="space-note" class="hint" style="font-size:.8rem"></div>
      <hr class="settings-sep" />` : ""}
      ${!WEB ? `
      <!-- 저장 위치 — 상단 바에 상시로 떠 있던 "● 로컬" 칩이 여기로 왔다(발주자 지시
           2026-08-05). 바뀌지 않는 사실은 상시 점유할 자격이 없다: 궁금할 때 와서 보는
           것으로 충분하다. 데스크탑 분기 안에만 두는 것도 의도다 — 웹 Vinc 의 데이터는
           계정에 있으므로 그쪽에서 "로컬"은 애초에 참이 아니었다(칩은 양쪽에 다 떴다). -->
      <label>${U.storage}</label>
      <p class="rail-sync"><span class="sync-dot"></span><span>${
        // 로그인 여부로 갈린다. **"로컬"은 계정을 안 붙였을 때만 참이다** — 붙이면
        // 고른 주제가 계정에도 올라가고(바로 아래 syncHint 가 그 약속이다), 팀
        // 스페이스는 아예 계정에만 있다. 상시 "로컬"은 그 둘을 다 부정하는 말을
        // 화면에 고정하는 것이라, 사용자가 자기 데이터가 어디 있는지 틀리게 안다.
        // 연결·해제는 renderAccount 가 이 패널을 통째로 다시 그리므로 즉시 따라온다.
        hasAccount() ? U.storageSynced : COPY.ui.common.local
      }</span></p>
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
      <label>${U.language}</label>
      <p><span class="seg">${langSeg()}</span></p>
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
            if (!u) { box.textContent = U.webNotSignedIn; return; }
            const who = u.username ? "@" + u.username : u.email;
            box.innerHTML = `${U.webSignedAs(esc(who))} · <a href="/account/" target="_blank" rel="noopener">${U.webManage}</a>`;
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
          if (note && !active.length) note.textContent = U.spaceNone;
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

    // 계정 연결(디바이스 플로우) — 흐름은 `account-connect` 한 벌을 쓴다.
    // 여기서 하는 일은 **그리기**뿐이다: 같은 플로우를 첫 실행 카드와 원격 커넥터
    // 행도 부르므로, 폴링을 이 탭 안에 두면 세 벌이 된다.
    const acctBox = body.querySelector<HTMLElement>("#acct-box")!;
    function drawAcct() {
      // 연결 여부와 표시할 이름은 **다른 질문**이다. 수동으로 붙여넣은 키는 누구
      // 것인지 알 수 없어 이름이 없고, 이름으로 갈랐더니 로그인해도 미연결 화면이
      // 나왔다. 카피(`acctConnected`)는 빈 이름을 이미 감당한다.
      const who = accountWho();
      if (hasAccount()) {
        acctBox.innerHTML = `
          <p>${U.acctConnected(who ? esc(who) : "")}
             <button class="btn" id="acct-disc" style="margin-left:.5rem">${U.acctDisconnect}</button></p>
          <p class="hint">${U.acctDisconnectHint}</p>
          <p class="hint" id="acct-ondisk" hidden>${U.acctOnDisk}</p>`;
        // OS 저장소 유무는 물어봐야 안다(프로브가 쓰고 지운다). 약한 상태를
        // 기본으로 두지 않고 **화면에 적는다** — 안 적으면 사용자는 자기 키가
        // 어디 있는지 모른다.
        void accountKey().then(() => {
          const n = acctBox.querySelector<HTMLElement>("#acct-ondisk");
          if (n) n.hidden = !keyIsOnDisk();
        });
        acctBox.querySelector("#acct-disc")!.addEventListener("click", () => {
          forgetAccount();
          renderAccount(body);
        });
      } else {
        // 지금 상태를 먼저 말한다. 첫 실행이 선택을 요구하게 되면서(firstrun) 여기가
        // **되돌아오는 유일한 길**이 됐는데, 여태 이 자리는 연결 버튼만 있어서
        // "오프라인"이 고른 상태가 아니라 아직 안 한 일처럼 보였다.
        acctBox.innerHTML = `
          <p class="hint">${U.acctOffline}</p>
          <p><button class="btn" id="acct-connect">${U.acctConnect}</button></p>
          <p class="hint">${U.acctConnectHint}</p>
          <div id="acct-result" class="hint" style="font-size:.78rem"></div>`;
        acctBox.querySelector("#acct-connect")!.addEventListener("click", () => void startConnect());
      }
    }

    async function startConnect() {
      const acctResult = acctBox.querySelector<HTMLElement>("#acct-result")!;
      const r = await connectAccount(() => { acctResult.textContent = U.acctConnecting; });
      if (r === "connected") { renderAccount(body); return; }
      if (r === "expired") { acctResult.textContent = U.acctExpired; return; }
      if (r === "failed") acctResult.textContent = U.acctFail;
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
        // 기본 선택은 비어 있고, 비어 있으면 export 가 아무것도 안 훑는다. 그러면 계정을
        // 붙여 놓고도 올라가는 것이 없는데 화면은 그 말을 안 했다 — 사용자가 "동기화됐다"고
        // 믿을 근거는 [연결됨]뿐이고 그건 참이므로 오해가 안 풀린다.
        //
        // 대신 **골라 주지 않는다.** 안 고른 것은 이 기기에만 남는다는 것이 이 섹션의 약속
        // (`syncHint`)이고, 대신 골라 주면 그 약속을 깨는 쪽이 기본값이 된다. 물어보는 것이
        // 이 약속을 지키는 유일한 방법이다.
        const wasEmpty = !selected.size;
        const nudge = hasAccount() && wasEmpty
          ? `<p class="hint" style="margin:.2rem 0 .5rem">${U.syncNonePicked}</p>` : "";
        domBox.innerHTML = nudge + rows.map((r) => {
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
            // 안내는 "고른 것이 없다"는 **사실**이므로 그 사실이 뒤집힐 때 같이 뒤집혀야
            // 한다 — 처음 하나를 고르면 걷히고, 마지막 하나를 풀면 돌아온다. 고르는
            // 방향만 보면 후자에서 참인 문장이 화면에서 사라진 채 남는다(실측).
            // 되그리기가 반복되지 않는 이유: 다시 그린 뒤에는 `wasEmpty` 가 새 상태와
            // 같아져 조건이 거짓이 된다.
            if (!picked.length !== wasEmpty) void loadDomains();
          }));
      } catch { domBox.textContent = U.syncNoDomains; }
    }
    void loadDomains();

    body.querySelector("#sync-now")!.addEventListener("click", async () => {
      const key = await accountKey();
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
        // 문서 채널(D45 P3 배선) — 오간 게 있을 때만 한 줄 덧붙인다.
        const dUp = r?.docs_push?.sent ?? 0;
        const dDown = (r?.docs_pull?.applied ?? 0) + (r?.docs_pull?.updated ?? 0);
        syncResult.textContent = U.syncDone(up, down)
          + (dUp || dDown ? U.syncDocs(dUp, dDown) : "");
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
  // 원격 MCP 엔드포인트(허브). `vinc_mcp/http_server.py` 의 `/mcp` 와 짝이고, 같은
  // 호스트가 OAuth 디스커버리(`/.well-known/*`)도 낸다 — 커넥터는 이 주소 하나만 받는다.
  const REMOTE_MCP_URL = "https://mcp.vincs.io/mcp";

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

      <!-- 원격 호스트. 위의 넷과 **다른 물건**이라 섹션을 가른다: 저것들은 이 기기의
           루프백에 붙고 계정이 필요 없지만, 여기는 계정 그래프에 붙으므로 계정이
           있어야 한다(루프백은 구조적으로 원격에 노출할 수 없다). 여태 이 경로는
           화면에 아예 없어서, 계정이 도움이 될 사람조차 존재를 몰랐다.
           근거: [[decision:account-is-a-path-not-a-wall]] -->
      <label>${U.mcpRemote}</label><p class="hint">${U.mcpRemoteHint}</p>
      <ul class="rowlist">
        <li class="row">
          <span class="row-name">${U.mcpRemoteHosts}</span>
          <span class="row-acts">
            <button class="btn btn-quiet" id="mcp-remote-copy">${U.mcpRemoteCopyAct}</button>
            <button class="btn" id="mcp-remote-acct" hidden>${U.acctConnect}</button>
          </span>
        </li>
      </ul>
      <p class="hint hint-sm break-all"><code>${REMOTE_MCP_URL}</code></p>
      <div id="mcp-remote-state" class="hint hint-sm"></div>

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
    /** ⚠ **성공했다고 말하기 전에 결과를 읽는다.**
     *
     *  여태 이 자리는 호출이 던지지만 않으면 "등록됐어요"라고 했다. 그런데 호스트
     *  CLI 에 위임하는 경로(Claude Code · Codex)는 그 CLI 가 없어도 사이드카가
     *  **0 으로 끝난다** — 실패가 예외가 아니라 결과 JSON 의 `registered:false` 로
     *  온다(`vinc_mcp/install.py` 의 `_run_cli`, `api/cli.py` 의 `_emit`). 그래서
     *  아무것도 등록되지 않은 채 화면만 성공을 말했다. 넷이 똑같이 생긴 행이라
     *  어느 쪽이 그런지 구분할 방법도 없었다.
     *
     *  JSON 이 아니어도 안 깨진다: 못 읽으면 예전처럼 성공으로 읽되, 없는 말을
     *  지어내지는 않는다. */
    async function mcpAction(action: "install" | "uninstall", client: string) {
      const invoke = await shellInvoke();
      if (!invoke) { mcpResult.textContent = U.mcpNoShell; return; }
      mcpResult.textContent = action === "install" ? U.mcpInstalling : U.mcpRemoving;
      const host = U.mcpHosts[client] || client;
      try {
        const raw = await invoke(
          action === "install" ? "mcp_install" : "mcp_uninstall", { client }) as unknown;
        let res: any = null;
        try { res = typeof raw === "string" ? JSON.parse(raw) : raw; } catch { /* 형식 밖 */ }
        const ok = action === "install" ? res?.registered !== false : res?.removed !== false;
        if (!ok && res?.command) {
          // 사람이 손으로 이을 수 있게 **실행할 줄을 그대로** 보여 준다.
          mcpResult.textContent = `${U.mcpNeedsCli(host)} ${res.command}`;
        } else if (!ok) {
          mcpResult.textContent = action === "install"
            ? `${U.mcpFail}: ${res?.note || ""}`.trim()
            : U.mcpNothing(host);
        } else {
          mcpResult.textContent = action === "install" ? U.mcpDone(host) : U.mcpRemoved(host);
        }
      } catch (e: any) {
        mcpResult.textContent = `${U.mcpFail}: ${(e && e.message) || e}`;
      }
    }
    for (const h of MCP_HOSTS) {
      body.querySelector(`#mcp-add-${h}`)!.addEventListener("click", () => mcpAction("install", h));
      body.querySelector(`#mcp-rm-${h}`)!.addEventListener("click", () => mcpAction("uninstall", h));
    }

    // 원격 행: 여기서 할 수 있는 것은 주소 복사와 계정 연결 둘뿐이다. 커넥터 등록은
    // 호스트 쪽 화면에서 사용자가 한다(우리가 대신 써 줄 설정 파일이 없다).
    const remoteState = body.querySelector<HTMLElement>("#mcp-remote-state")!;
    const remoteAcct = body.querySelector<HTMLButtonElement>("#mcp-remote-acct")!;
    function paintRemote() {
      const who = accountWho();
      const linked = hasAccount();          // 이름 없이 연결된 경우가 있다(수동 키)
      remoteAcct.hidden = linked;
      remoteState.textContent = linked
        ? U.mcpRemoteSignedIn(who || "") : U.mcpRemoteNeedAccount;
    }
    body.querySelector("#mcp-remote-copy")!.addEventListener("click", async () => {
      // 복사가 막혀도 주소는 위에 그대로 떠 있다 — 실패를 오류로 키우지 않는다.
      try { await navigator.clipboard.writeText(REMOTE_MCP_URL); remoteState.textContent = U.mcpRemoteCopied; }
      catch { remoteState.textContent = U.mcpRemoteCopyFail; }
    });
    remoteAcct.addEventListener("click", async () => {
      remoteState.textContent = U.acctConnecting;
      const r = await connectAccount();
      if (r === "connected") paintRemote();
      else remoteState.textContent = r === "expired" ? U.acctExpired : U.acctFail;
    });
    paintRemote();

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

  // ── Advanced 탭 (셸 전용) ─────────────────────────────────────────────────
  function renderAdvanced(body: HTMLElement) {
    body.innerHTML = `
      <label for="s-owner">${U.workspace}</label>
      <p class="hint">${U.workspaceHint}</p>
      <input id="s-owner" value="${cfg.owner}" />
      <label for="s-sync-key">${U.syncKey}</label>
      <p class="hint">${U.syncKeyHint}</p>
      <p class="s-keyrow"><input id="s-sync-key" type="password" placeholder="vinc_sk_…"
         autocomplete="off" /><button class="btn" id="s-sync-key-save" type="button">${
        U.syncKeySave}</button></p>
      <p class="hint" id="s-sync-key-said" hidden></p>
      <label for="s-base">${U.engine}</label>
      <input id="s-base" value="${cfg.base}" />
      <label for="s-token">${U.secret}</label>
      <p class="hint">${U.secretHint}</p>
      <input id="s-token" type="password" placeholder="••••••"
             autocomplete="off" />`;
    body.querySelector<HTMLInputElement>("#s-base")!.addEventListener("change", (e) =>
      (cfg.base = (e.target as HTMLInputElement).value));
    // 동기화 키와 **같은 규율**(감사 M8): 값을 다시 그리지 않고, 비어 있으면
    // "안 바꿈"이다. 여태 이 칸만 `value=` 로 시크릿을 DOM 에 눕히고 있었다 —
    // 같은 탭에서 한 칸은 감추고 옆 칸은 드러내면 규율이 아니라 우연이 된다.
    const tokenInput = body.querySelector<HTMLInputElement>("#s-token")!;
    tokenInput.addEventListener("change", () => {
      const v = tokenInput.value.trim();
      if (!v) return;
      cfg.token = v;
      tokenInput.value = "";
    });
    body.querySelector<HTMLInputElement>("#s-owner")!.addEventListener("change", (e) =>
      (cfg.owner = (e.target as HTMLInputElement).value));
    // 수동 키(폴백 경로). **값을 다시 그리지 않는다** — 여기 두면 키가 DOM 에 눕고,
    // 그것을 없애자는 것이 이 변경의 목적이다(감사 M8). 비어 있으면 "안 바꿈"이다.
    const keyInput = body.querySelector<HTMLInputElement>("#s-sync-key")!;
    const keySaid = body.querySelector<HTMLElement>("#s-sync-key-said")!;
    // **저장했다고 말한다**(발주자 지적 2026-09-10: "키 입력하면 저장되는 게 아니던데").
    // 실은 저장되고 있었다 — 다만 곧바로 입력칸을 비우는데(위 M8 규칙) 아무 말도
    // 안 해서, 비워지는 것이 "버려졌다"로 읽혔다. 지우는 것을 되돌리지 않고
    // **왜 지우는지를 말한다**: 키를 화면에 남기지 않는 것이 그 규칙의 목적이므로.
    const save = () => {
      const v = keyInput.value.trim();
      if (!v) return;
      void setAccountKey(v);
      localStorage.removeItem("vinc.sync.email");   // 수동 키 = 이메일 미상
      keyInput.value = "";                          // 입력칸에도 남기지 않는다
      keySaid.textContent = U.syncKeySaved;
      keySaid.hidden = false;
    };
    // 버튼이 **정본 경로**다. `change` 만 두면 입력칸에 포커스를 둔 채 시트를 닫을 때
    // 이벤트가 아예 안 뜨고 키가 조용히 사라진다(실측 경로: Esc·딤 클릭).
    body.querySelector<HTMLElement>("#s-sync-key-save")!.addEventListener("click", save);
    keyInput.addEventListener("change", save);
    keyInput.addEventListener("keydown", (e) => { if (e.key === "Enter") save(); });
  }

  shell();
}
