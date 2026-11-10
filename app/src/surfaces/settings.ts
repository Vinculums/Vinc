import { accountMode, api, buildId, cfg, forgetMemberKey, messageOf, remote, WEB, shellInvoke } from "../api";
import { accountKey, accountWho, connectAccount, forgetAccount, hasAccount, keyIsOnDisk,
         mcpKeyPresent,
         setAccountKey } from "../account-connect";
import { COPY } from "../copy";
import { esc, openSheet, type SheetHandle } from "../components";
import { loadPrefs, savePrefs, RAIL_MIN, RAIL_MAX } from "../view-prefs";
import { disposeEditorsIn } from "../doc-editor";
import { mountStorage } from "../storage-mode";
import { mountTopicConversion } from "../topic-conversion";
import { mountPushSettings } from "../web-push-ui";

/** Settings — 왼쪽 분류 레일 + 본문(2026-09-22).
 *  종 레일 바닥의 설정 버튼이 예전 Settings · GRAPH.md · Tools(쓰기/가져오기/공유)
 *  를 한 상자에 담는다. 시트 머리의 ✕ 가 닫기이고, 여기 안에 닫기를 하나 더 두지 않는다.
 *  웹 Vinc 는 셸 전용 분류(AI 연결 · 고급)와 계정연결·동기화 섹션을 숨긴다. */

type SettingsCat =
  | "general" | "reading" | "account" | "storage" | "ai"
  | "data" | "graphmd" | "advanced" | "about";
export type DataTab = "import" | "share";

type Live = {
  go: (cat: SettingsCat, dataTab?: DataTab) => void;
  close: () => void;
};
let live: Live | null = null;

/** 설정 시트를 연다. 이미 열려 있으면 `toggle` 이 참일 때만 닫고, 아니면 분류만 옮긴다. */
export function openSettings(start: SettingsCat = "general", dataTab?: DataTab, toggle = false) {
  if (live) {
    if (toggle) { live.close(); return; }
    live.go(start, dataTab);
    return;
  }
  // 시트 이름은 설정 버튼과 같은 말이다(`설정`). `settingsUi.title` 은 한국어 덱에서도
  // "Settings" 라 머리만 영어가 된다.
  // ⚠ `title:` 을 여는 괄호 바로 다음 줄에 둔다 — `scripts/design/figma_inventory.py` 가
  // 이 자리의 제목식으로 모달을 센다(사이에 주석이 끼면 제목 없는 호출로 읽는다).
  const sheet: SheetHandle = openSheet({
    title: COPY.ui.topbar.settings,
    size: "panel",
    // 닫힐 때 살아 있는 편집기(정보 › 쓰기)를 확정·해제한다. 마운트 지점만 걷어 가면
    // 디바운스 저장이 화면 없는 곳에서 터진다.
    onClose: () => { live = null; disposeEditorsIn(sheet.el); },
  });
  let queued: { cat: SettingsCat; tab?: DataTab } | null = null;
  live = {
    go: (cat, tab) => { queued = { cat, tab }; },
    close: sheet.close,
  };
  void renderSettings(sheet.body, sheet, start, dataTab).then((go) => {
    if (!live) return;
    live.go = go;
    if (queued) go(queued.cat, queued.tab);
  });
}

async function renderSettings(
  el: HTMLElement,
  sheet: SheetHandle,
  start: SettingsCat = "general",
  dataTab?: DataTab,
): Promise<(cat: SettingsCat, tab?: DataTab) => void> {
  const U = COPY.ui.settingsUi;

  const ALL_CATS: { id: SettingsCat; label: string; desktop?: boolean }[] = [
    { id: "general", label: U.navGeneral },
    { id: "reading", label: U.navReading },
    { id: "account", label: U.navAccount },
    { id: "storage", label: U.navStorage, desktop: true },
    { id: "ai", label: U.navAi, desktop: true },
    { id: "data", label: U.navData },
    { id: "graphmd", label: U.navGraphmd },
    { id: "advanced", label: U.navAdvanced, desktop: true },
    { id: "about", label: U.navAbout },
  ];
  const CATS = ALL_CATS.filter((c) => !c.desktop || !WEB);

  let active: SettingsCat = CATS.some((c) => c.id === start) ? start : "general";
  let toolsTab: DataTab = dataTab ?? "import";
  let cleanup: (() => void) | null = null;

  function shell() {
    cleanup?.();
    cleanup = null;
    sheet.foot.innerHTML = "";
    el.innerHTML = `
      <div class="settings">
        <nav class="settings-cats" aria-label="${esc(U.title)}">
          ${CATS.map((c) => `<button type="button" class="settings-cat${c.id === active ? " on" : ""}"
            data-cat="${c.id}" role="tab" aria-selected="${c.id === active}">${esc(c.label)}${
              c.id === "graphmd" ? `<span id="settings-graphmd-badge" class="settings-cat-badge" hidden></span>` : ""
            }</button>`).join("")}
          <span class="settings-cat-space"></span>
          <div class="settings-ver hint">Vinc ${esc(buildId)}</div>
        </nav>
        <div class="settings-main settings-body" id="settings-body"></div>
      </div>`;
    el.querySelectorAll<HTMLElement>(".settings-cat").forEach((b) =>
      b.addEventListener("click", () => {
        const next = b.dataset.cat as SettingsCat;
        if (next === active) return;
        active = next;
        shell();
      }));
    void import("../conventions").then((m) => m.refreshBadge());
    renderBody();
  }

  function renderBody() {
    cleanup?.();
    cleanup = null;
    sheet.foot.innerHTML = "";
    const body = el.querySelector<HTMLElement>("#settings-body")!;
    if (active === "general") renderGeneral(body);
    else if (active === "reading") renderReading(body);
    else if (active === "account") renderAccount(body);
    else if (active === "storage") renderStorage(body);
    else if (active === "ai") renderConnections(body);
    else if (active === "data") renderData(body);
    else if (active === "graphmd") renderGraphmd(body);
    else if (active === "advanced") renderAdvanced(body);
    else renderAbout(body);
  }

  const THEME_OPTS = [
    { id: "light", label: U.themeLight },
    { id: "dark", label: U.themeDark },
    { id: "auto", label: U.themeSystem },
  ] as const;
  const themeSeg = () => THEME_OPTS.map((t) =>
    `<button class="chip${(localStorage.getItem("vinc.theme") || "dark") === t.id ? " active" : ""}"
      data-theme-opt="${t.id}">${esc(t.label)}</button>`).join("");

  function wireThemeLang(body: HTMLElement) {
    body.querySelector<HTMLSelectElement>("#s-lang")?.addEventListener("change", (e) => {
      cfg.lang = (e.target as HTMLSelectElement).value as "en" | "ko";
      location.reload();
    });
    body.querySelectorAll<HTMLElement>("[data-theme-opt]").forEach((b) =>
      b.addEventListener("click", async () => {
        localStorage.setItem("vinc.theme", b.dataset.themeOpt!);
        const { applyTheme } = await import("../main");
        applyTheme();
        renderGeneral(body);
      }));
  }

  function renderGeneral(body: HTMLElement) {
    const lang = cfg.lang === "en" ? "en" : "ko";
    // 언어 이름은 덱에 넣지 않는다: 각 언어가 자기 이름으로 선다(영어 화면에서 "Korean"
    // 이 되면 영어를 못 읽는 사람이 자기 언어를 못 찾는다). `data-lang-opt` 가 카피
    // 인벤토리(tests/design/test_copy_parity.py)의 그 면제 표식이다.
    body.innerHTML = `
      <h2>${esc(U.navGeneral)}</h2>
      <p class="hint">${esc(U.generalIntro)}</p>
      <div class="settings-row">
        <div class="settings-row-copy">
          <strong>${esc(U.theme)}</strong>
          <p class="hint">${esc(U.themeHint)}</p>
        </div>
        <span class="seg">${themeSeg()}</span>
      </div>
      <hr class="settings-sep" />
      <div class="settings-row">
        <div class="settings-row-copy">
          <strong>${esc(U.language)}</strong>
          <p class="hint">${esc(U.languageHint)}</p>
        </div>
        <select id="s-lang" class="settings-select">
          <option data-lang-opt="ko" value="ko"${lang === "ko" ? " selected" : ""}>한국어</option>
          <option data-lang-opt="en" value="en"${lang === "en" ? " selected" : ""}>English</option>
        </select>
      </div>
      <p class="hint">${esc(U.languageReload)}</p>`;
    wireThemeLang(body);
  }

  function renderReading(body: HTMLElement) {
    const rem = loadPrefs().railW;
    const px = Math.round(rem * 16);
    const minPx = Math.round(RAIL_MIN * 16);
    const maxPx = Math.round(RAIL_MAX * 16);
    body.innerHTML = `
      <h2>${esc(U.navReading)}</h2>
      <p class="hint">${esc(U.readingIntro)}</p>
      <div class="settings-row">
        <div class="settings-row-copy">
          <strong>${esc(U.readingPane)}</strong>
          <p class="hint">${esc(U.readingPaneHint)}</p>
        </div>
        <label class="settings-px">
          <input id="s-rail-w" type="number" min="${minPx}" max="${maxPx}" step="16" value="${px}" />
          <span>px</span>
        </label>
      </div>
`;   // 그래프의 '보기 옵션 열기' 줄은 걷었다: 옵션·범례 단추가 없어지고 그 손잡이는 LNB 의 나침반 칸으로 갔다
    body.querySelector<HTMLInputElement>("#s-rail-w")!.addEventListener("change", (e) => {
      const n = Number((e.target as HTMLInputElement).value);
      const next = Math.min(RAIL_MAX, Math.max(RAIL_MIN, n / 16));
      savePrefs({ ...loadPrefs(), railW: next });
      document.getElementById("shell")?.style.setProperty("--rail-size", `${next}rem`);
      (e.target as HTMLInputElement).value = String(Math.round(next * 16));
    });
  }

  function renderAbout(body: HTMLElement) {
    body.innerHTML = `
      <h2>${esc(U.navAbout)}</h2>
      <p class="hint">${esc(U.aboutIntro)}</p>
      <div class="settings-about-id">
        <span class="brand-logo" aria-hidden="true"></span>
        <p class="settings-build">Vinc ${esc(buildId)}</p>
      </div>
      <p class="hint">${esc(remote() ? COPY.sovereigntyWeb : COPY.sovereignty)}</p>`;
  }

  function renderData(body: HTMLElement) {
    body.innerHTML = `
      <h2>${esc(U.navData)}</h2>
      <p class="hint">${esc(U.dataIntro)}</p>
      <div id="settings-tools"></div>`;
    const pane = body.querySelector<HTMLElement>("#settings-tools")!;
    // 분류들은 시트의 발(`sheet.foot`)을 함께 쓴다. 모듈이 오는 사이 다른 분류로
    // 옮겼으면 이 판은 이미 떨어져 나갔으므로 아무것도 안 그린다 — 그리면 그 발
    // 버튼이 지금 분류 밑에 선다. 판 안의 늦은 쓰기도 같은 규칙이다(tools.ts ·
    // conventions.ts 가 `isConnected` 로 본다).
    void import("../tools").then((m) => {
      if (!pane.isConnected) return;
      cleanup = m.mountTools(
        { el: sheet.el, body: pane, foot: sheet.foot, close: sheet.close }, toolsTab);
    });
  }

  function renderGraphmd(body: HTMLElement) {
    body.innerHTML = `
      <h2>${esc(U.navGraphmd)}</h2>
      <p class="hint">${esc(U.graphmdIntro)}</p>
      <div id="settings-conv"></div>`;
    const pane = body.querySelector<HTMLElement>("#settings-conv")!;
    void import("../conventions").then((m) => {
      if (!pane.isConnected) return;       // 위 renderData 와 같은 이유
      void m.mountConventions(pane, sheet);
    });
  }

  // ── Account 탭 ────────────────────────────────────────────────────────────
  function renderAccount(body: HTMLElement) {
    // 웹 Vinc: 로그인 계정을 @username(없으면 이메일)으로 표시.
    // ⚠ 이 블록의 카피는 예전에 여기 EN/KO 사전으로 인라인돼 있었다. 사유는
    // "셸 편집 충돌 회피"였는데 그 충돌은 이미 없고, 덱 밖 카피는 언어 프록시도
    // 게이트도 타지 않는다(tests/design/test_copy_parity.py 가 이제 막는다).
    body.innerHTML = `
      <h2>${esc(U.navAccount)}</h2>
      <p class="hint">${esc(U.accountIntro)}</p>
      ${remote() ? `
      <label>${U.account}</label>
      <div id="web-acct" class="hint" style="font-size:.85rem">…</div>
      <label>${U.space}</label>
      <p class="hint">${esc(U.spaceHint)}</p>
      ${!cfg.team ? '<div id="topic-conversion"></div>' : ""}
      ${WEB ? `<label>${COPY.push.title}</label><div id="push-box"></div>` : ""}` : ""}
      ${!remote() ? `
      <label>${U.account}</label>
      <div id="acct-box"></div>` : ""}
      ${!WEB ? `
      <hr class="settings-sep" />
      <div class="settings-row">
        <div class="settings-row-copy">
          <strong>${esc(U.storage)}</strong>
          <p class="hint">${esc(accountMode() ? U.storageAccount : U.storageDevice)}</p>
        </div>
        <button class="btn" type="button" id="acct-storage-go">${esc(U.storageOpen)}</button>
      </div>` : ""}`;
    body.querySelector("#acct-storage-go")?.addEventListener("click", () => go("storage"));
    if (remote()) {
      const conversion = body.querySelector<HTMLElement>("#topic-conversion");
      if (conversion) cleanup = mountTopicConversion(conversion);
      // 이 기기의 알림(웹 푸시). 웹에서만 그린다: 데스크톱 셸은 서비스 워커를 쓰지 않는다.
      const pushBox = body.querySelector<HTMLElement>("#push-box");
      if (pushBox) {
        const before = cleanup;
        const stop = mountPushSettings(pushBox);
        cleanup = () => { before?.(); stop(); };
      }
      // 웹모드: /api/me 로 로그인 계정 표시(@username 우선). 연결/동기화 섹션은 없음(셸 전용).
      // 셸 계정 모드: 쿠키가 없어 /api/me 를 못 부른다. 기기 플로우가 남긴 이름을 쓰고,
      // 로그아웃을 여기 둔다(로그아웃하면 로그인 시트로 돌아간다, VIN-128).
      const box = body.querySelector<HTMLElement>("#web-acct");
      if (box && accountMode()) {
        const who = accountWho();
        box.innerHTML = `${who ? U.webSignedAs(esc(who)) : U.acctSignedIn}
          <p><button class="btn" id="acct-signout" type="button">${U.acctSignOut}</button></p>
          <p class="hint">${U.acctSignOutHint}</p>`;
        box.querySelector("#acct-signout")!.addEventListener("click", () => {
          forgetAccount();
          forgetMemberKey();
          // 화면 전체가 계정 그래프를 들고 있다. 부분 갱신하면 옛 계정의 데이터가 남는다.
          location.reload();
        });
      } else if (box) {
        api.me()
          .then((r) => (r.ok ? r.json() : null))
          .then((d) => {
            const u = d && d.user;
            if (!u) { box.textContent = U.webNotSignedIn; return; }
            const who = u.username ? "@" + u.username : u.email;
            box.innerHTML = `${U.webSignedAs(esc(who))} · <a href="/account/" target="_blank" rel="noopener">${U.webManage}</a>`;
          })
          .catch(() => { if (box) box.textContent = ""; });
      }
      // 공간 전환은 사이드바 머리에 있다(`space-switcher.ts`). 여기는 그 자리를 한 줄로 알리고,
      // 개인 공간이면 주제를 새 팀 공간으로 옮기는 칸을 둔다.
      return;
    }
    if (WEB) return;

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
          <p class="hint" id="acct-ondisk" hidden>${U.acctOnDisk}</p>
          <p class="hint" id="acct-key-failure" role="status" aria-live="polite" hidden>
            ${U.acctKeyFailed} <button class="btn" id="acct-key-retry">${U.acctKeyRetry}</button></p>`;
        // OS 저장소 유무는 물어봐야 안다(프로브가 쓰고 지운다). 약한 상태를
        // 기본으로 두지 않고 **화면에 적는다** — 안 적으면 사용자는 자기 키가
        // 어디 있는지 모른다.
        const onDisk = acctBox.querySelector<HTMLElement>("#acct-ondisk")!;
        const failure = acctBox.querySelector<HTMLElement>("#acct-key-failure")!;
        const retry = acctBox.querySelector<HTMLButtonElement>("#acct-key-retry")!;
        let checkingKey = false;
        async function checkKey() {
          if (checkingKey) return;
          checkingKey = true;
          retry.disabled = true;
          failure.hidden = true;
          try {
            await accountKey();
            onDisk.hidden = !keyIsOnDisk();
          } catch {
            // 이행 저장 실패도 여기로 온다. 연결이나 기존 키를 지우지 않고 재시도를 열어 둔다.
            onDisk.hidden = true;
            failure.hidden = false;
          } finally {
            checkingKey = false;
            retry.disabled = false;
          }
        }
        void checkKey();
        retry.addEventListener("click", () => void checkKey());
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

  }

  // ── Storage 탭 (셸 전용) ─────────────────────────────────────────────────
  // 이 기기가 그래프를 두는 곳과 옮기기(`storage-mode.ts`). 웹 Vinc 의 데이터는 늘 계정에 있어
  // 이 탭이 없다. 옮기기에 로그인이 필요하면 계정 탭으로 보낸다.
  function renderStorage(body: HTMLElement) {
    body.innerHTML = `
      <h2>${esc(U.navStorage)}</h2>
      <p class="hint">${esc(U.storageIntro)}</p>
      <div id="st-box"></div>`;
    mountStorage(body.querySelector<HTMLElement>("#st-box")!, { openAccount: () => go("account") });
  }

  // ── Connections 탭 (MCP, 셸 전용) ─────────────────────────────────────────
  // 등록 가능한 호스트 — 사이드카 `vinc_mcp.install.CLIENTS` 와 짝. 여기 없는
  // 호스트도 서버는 그대로 동작한다(수동 등록 안내는 U.mcpOtherHint).
  const MCP_HOSTS = ["desktop", "code", "cursor", "codex"] as const;
  // 원격 MCP 엔드포인트(허브). `vinc_mcp/http_server.py` 의 `/mcp` 와 짝이고, 같은
  // 호스트가 OAuth 디스커버리(`/.well-known/*`)도 낸다 — 커넥터는 이 주소 하나만 받는다.
  const REMOTE_MCP_URL = "https://mcp.vincs.io/mcp";
  // 이 기기에서 연결에 성공한 호스트만 기억한다(해제하면 지운다). 호스트 설정 파일을 읽지 않으므로
  // 다른 길로 등록한 것은 모른다. 기억한 호스트는 상태와 해제만, 나머지는 연결만 보인다.
  const MCP_KEY = "vinc.mcp.connected";
  const registeredHosts = new Set<string>(
    (() => { try { const v = JSON.parse(localStorage.getItem(MCP_KEY) || "[]"); return Array.isArray(v) ? v.filter((x) => typeof x === "string") : []; } catch { return []; } })());
  const keepHosts = () => { try { localStorage.setItem(MCP_KEY, JSON.stringify([...registeredHosts])); } catch { /* 기억만 못 한다 */ } };
  const busyHosts = new Set<string>();
  const hostResults = new Map<string, string>();
  let paintConnections = () => {};

  function renderConnections(body: HTMLElement) {
    // 호스트 하나 = 행 하나(2026-08-05 재설계). 여태 `.seg` 안에 연결 버튼 4개,
    // 다른 `.seg` 안에 해제 버튼 4개를 넣었다 — `.seg` 는 **여럿 중 하나를 고르는**
    // 컨트롤이므로 독립 동작 8개를 담을 그릇이 아니었고(그래서 한 덩어리로 뭉쳐
    // 보였다), 같은 호스트의 연결과 해제가 화면 위아래로 갈라져 있어 "Claude Desktop
    // 을 어떻게 하지"를 두 곳에서 찾아야 했다. 행이 되면 그 둘이 한 줄에 있다.
    const rows = MCP_HOSTS.map((h) => `
      <li class="row mcp-row">
        <span class="row-name">${U.mcpHosts[h]}</span>
        <span class="row-acts">
          <button class="btn btn-quiet" id="mcp-rm-${h}"
                  aria-label="${U.mcpRemove(U.mcpHosts[h])}">${U.mcpRemoveAct}</button>
          <button class="btn" id="mcp-add-${h}"
                  disabled
                  aria-label="${U.mcpConnect(U.mcpHosts[h])}">${U.mcpConnectAct}</button>
        </span>
        <span id="mcp-result-${h}" class="hint hint-sm break-all mcp-result"
              role="status" aria-live="polite"></span>
      </li>`).join("");
    body.innerHTML = `
      <h2>${esc(U.navAi)}</h2>
      <p class="hint">${esc(U.aiIntro)}</p>
      <label>${U.mcp}</label><p class="hint">${COPY.settings.mcp}</p>
      <ul class="rowlist">${rows}</ul>
      <p class="hint hint-sm" id="mcp-mode"></p>
      <p class="hint hint-sm">${U.mcpOtherHint}</p>

      <!-- 원격 호스트. 위의 넷은 앱 모드를 따르지만 여기는 계정 그래프에 붙으므로 계정이
           있어야 한다(루프백은 구조적으로 원격에 노출할 수 없다). 여태 이 경로는
           화면에 아예 없어서, 계정이 도움이 될 사람조차 존재를 몰랐다.
           근거: [[decision:the-desktop-signs-in-and-shows-the-account-graph-live-and-local-mode-is-a-setting]] -->
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
    let keyReady = !accountMode();
    function paintHost(client: string) {
      const add = body.querySelector<HTMLButtonElement>(`#mcp-add-${client}`)!;
      const remove = body.querySelector<HTMLButtonElement>(`#mcp-rm-${client}`)!;
      if (!add || !remove) return;
      const linked = registeredHosts.has(client);
      add.hidden = linked; remove.hidden = !linked;
      add.disabled = busyHosts.has(client) || !keyReady;
      remove.disabled = busyHosts.has(client);
      const result = rowResult(client);
      result.textContent = !keyReady ? U.mcpNoKey : hostResults.get(client) || (linked ? U.mcpConnectedHere : "");
    }
    function rowResult(client: string): HTMLElement {
      return body.querySelector<HTMLElement>(`#mcp-result-${client}`)!;
    }
    paintConnections = () => { for (const h of MCP_HOSTS) paintHost(h); };
    // 등록된 항목은 앱 모드를 따른다(VIN-132): 계정 모드면 계정 그래프, 로컬 모드면 이
    // 컴퓨터의 그래프. 모드는 호스트가 서버를 띄울 때 한 번 읽으므로 바꾼 뒤엔 다시 켜야 한다.
    const mcpMode = body.querySelector<HTMLElement>("#mcp-mode")!;
    mcpMode.textContent = accountMode() ? U.mcpModeAccount : U.mcpModeLocal;
    if (accountMode()) {
      void mcpKeyPresent().then((ok) => {
        if (!mcpMode.isConnected) return;
        keyReady = !accountMode() || ok;
        paintConnections();
      });
    } else paintConnections();
    /** ⚠ **성공했다고 말하기 전에 결과를 읽는다.**
     *
     *  여태 이 자리는 호출이 던지지만 않으면 "등록됐어요"라고 했다. 그런데 호스트
     *  CLI 에 위임하는 경로(Claude Code · Codex)는 그 CLI 가 없어도 사이드카가
     *  **0 으로 끝난다** — 실패가 예외가 아니라 결과 JSON 의 `registered:false` 로
     *  온다(`vinc_mcp/install.py` 의 `_run_cli`, `api/cli.py` 의 `_emit`). 그래서
     *  아무것도 등록되지 않은 채 화면만 성공을 말했다. 넷이 똑같이 생긴 행이라
     *  어느 쪽이 그런지 구분할 방법도 없었다.
     *
     *  결과가 JSON 이 아니거나 성공 필드가 없으면 등록됐다고 말하지 않는다. */
    async function mcpAction(action: "install" | "uninstall", client: string) {
      if (busyHosts.has(client) || (action === "install" && registeredHosts.has(client))) return;
      busyHosts.add(client); paintHost(client);
      const mcpResult = rowResult(client);
      const host = U.mcpHosts[client] || client;
      const say = (text: string) => {
        hostResults.set(client, text);
        mcpResult.textContent = text;
      };
      try {
        // 화면을 연 뒤 키가 지워졌을 수도 있다. 등록 직전에 다시 확인한다.
        if (action === "install" && accountMode() && !(await mcpKeyPresent())) {
          keyReady = false; say(U.mcpNoKey); return;
        }
        const invoke = await shellInvoke();
        if (!invoke) { say(U.mcpNoShell); return; }
        say(action === "install" ? U.mcpInstalling : U.mcpRemoving);
        const raw = await invoke(
          action === "install" ? "mcp_install" : "mcp_uninstall", { client }) as unknown;
        let res: any = null;
        try { res = typeof raw === "string" ? JSON.parse(raw) : raw; } catch { /* 형식 밖 */ }
        const ok = action === "install" ? res?.registered === true : res?.removed === true;
        if (!ok && res?.command) {
          // 사람이 손으로 이을 수 있게 **실행할 줄을 그대로** 보여 준다.
          say(`${U.mcpNeedsCli(host)} ${res.command}`);
        } else if (!ok) {
          const nothing = action === "uninstall" && res?.removed === false;
          // 지울 것이 없었다면 연결된 것이 아니다 — 기억도 지운다.
          if (nothing) { registeredHosts.delete(client); keepHosts(); }
          say(nothing ? U.mcpNothing(host) : `${U.mcpFail}: ${res?.note || ""}`.trim());
        } else {
          if (action === "install") registeredHosts.add(client);
          else registeredHosts.delete(client);
          keepHosts();
          say(action === "install" ? U.mcpDone(host) : U.mcpRemoved(host));
        }
      } catch (e: any) {
        say(`${U.mcpFail}: ${messageOf(e)}`);
      } finally {
        busyHosts.delete(client);
        paintConnections();
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
      catch (e: any) { paintCli(null, messageOf(e)); }
    }
    cliAdd.addEventListener("click", () => cliAction("cli_link_install"));
    cliRm.addEventListener("click", () => cliAction("cli_link_remove"));
    void cliAction("cli_link_status");
  }

  // ── Advanced 탭 (셸 전용) ─────────────────────────────────────────────────
  function renderAdvanced(body: HTMLElement) {
    body.innerHTML = `
      <h2>${esc(U.navAdvanced)}</h2>
      <p class="hint">${esc(U.advancedIntro)}</p>
      <!-- 작업 공간(owner)은 로컬 모드에서만 뜻이 있다. 계정 모드에서는 허브가 키로 소유자를
           정하므로 바꿔도 아무 일도 안 일어나고, 힌트가 말하는 샘플도 계정 모드에는 없다. -->
      ${accountMode() ? "" : `
      <label for="s-owner">${U.workspace}</label>
      <p class="hint">${U.workspaceHint}</p>
      <input id="s-owner" value="${esc(cfg.owner)}" />`}
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
    body.querySelector<HTMLInputElement>("#s-owner")?.addEventListener("change", (e) =>
      (cfg.owner = (e.target as HTMLInputElement).value));
    // 수동 키(폴백 경로). **값을 다시 그리지 않는다** — 여기 두면 키가 DOM 에 눕고,
    // 그것을 없애자는 것이 이 변경의 목적이다(감사 M8). 비어 있으면 "안 바꿈"이다.
    const keyInput = body.querySelector<HTMLInputElement>("#s-sync-key")!;
    const keySaid = body.querySelector<HTMLElement>("#s-sync-key-said")!;
    // **저장했다고 말한다**(발주자 지적 2026-09-10: "키 입력하면 저장되는 게 아니던데").
    // 실은 저장되고 있었다 — 다만 곧바로 입력칸을 비우는데(위 M8 규칙) 아무 말도
    // 안 해서, 비워지는 것이 "버려졌다"로 읽혔다. 지우는 것을 되돌리지 않고
    // **왜 지우는지를 말한다**: 키를 화면에 남기지 않는 것이 그 규칙의 목적이므로.
    let saving = false;
    const save = async () => {
      const v = keyInput.value.trim();
      if (!v || saving) return;
      saving = true;
      try {
        await setAccountKey(v);
        localStorage.removeItem("vinc.sync.email"); // 수동 키 = 이메일 미상
        // 저장을 기다리는 동안 새로 적은 값은 지우지 않는다.
        if (keyInput.value.trim() === v) keyInput.value = "";
        keySaid.textContent = U.syncKeySaved;
      } catch {
        keySaid.textContent = U.syncKeySaveFailed;
      } finally {
        keySaid.hidden = false;
        saving = false;
      }
    };
    // 버튼이 **정본 경로**다. `change` 만 두면 입력칸에 포커스를 둔 채 시트를 닫을 때
    // 이벤트가 아예 안 뜨고 키가 조용히 사라진다(실측 경로: Esc·딤 클릭).
    body.querySelector<HTMLElement>("#s-sync-key-save")!.addEventListener("click", save);
    keyInput.addEventListener("change", save);
    keyInput.addEventListener("keydown", (e) => { if (e.key === "Enter") save(); });
  }

  function go(cat: SettingsCat, tab?: DataTab) {
    if (tab) toolsTab = tab;
    active = CATS.some((c) => c.id === cat) ? cat : "general";
    shell();
  }
  shell();
  return go;
}
