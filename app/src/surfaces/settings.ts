import { accountMode, buildId, cfg, forgetMemberKey, remote, teamsApi, WEB, shellInvoke } from "../api";
import { accountKey, accountWho, connectAccount, forgetAccount, hasAccount, keyIsOnDisk,
         setAccountKey } from "../account-connect";
import { COPY } from "../copy";
import { esc, openSheet, type SheetHandle } from "../components";
import { loadPrefs, savePrefs, RAIL_MIN, RAIL_MAX } from "../view-prefs";
import { disposeEditorsIn } from "../doc-editor";

/** Settings — 왼쪽 분류 레일 + 본문(2026-09-22).
 *  종 레일 바닥의 설정 버튼이 예전 Settings · GRAPH.md · Tools(쓰기/가져오기/공유)
 *  를 한 상자에 담는다. 시트 머리의 ✕ 가 닫기이고, 여기 안에 닫기를 하나 더 두지 않는다.
 *  웹 Vinc 는 셸 전용 분류(AI 연결 · 고급)와 계정연결·동기화 섹션을 숨긴다. */

export type SettingsCat =
  | "general" | "reading" | "account" | "ai"
  | "data" | "graphmd" | "advanced" | "about";
export type DataTab = "create" | "import" | "share";

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
  // ⚠ `title:` 을 여는 괄호 바로 다음 줄에 둔다 — design-hub `figma_inventory.py` 가
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

export async function renderSettings(
  el: HTMLElement,
  sheet: SheetHandle,
  start: SettingsCat = "general",
  dataTab?: DataTab,
): Promise<(cat: SettingsCat, tab?: DataTab) => void> {
  const U = COPY.ui.settingsUi;
  // 셸이면 계정/MCP/동기화용 API 를 미리 로드(웹은 불요).
  const mod = WEB ? null : await import("../api");
  const api = mod?.api;
  const pollJob = mod?.pollJob;

  const ALL_CATS: { id: SettingsCat; label: string; desktop?: boolean }[] = [
    { id: "general", label: U.navGeneral },
    { id: "reading", label: U.navReading },
    { id: "account", label: U.navAccount },
    { id: "ai", label: U.navAi, desktop: true },
    { id: "data", label: U.navData },
    { id: "graphmd", label: U.navGraphmd },
    { id: "advanced", label: U.navAdvanced, desktop: true },
    { id: "about", label: U.navAbout },
  ];
  const CATS = ALL_CATS.filter((c) => !c.desktop || !WEB);

  let active: SettingsCat = CATS.some((c) => c.id === start) ? start : "general";
  let toolsTab: DataTab = dataTab ?? "create";
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
    // 인벤토리(tests/test_copy_parity.py)의 그 면제 표식이다.
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
      <hr class="settings-sep" />
      <div class="settings-row">
        <div class="settings-row-copy">
          <strong>${esc(U.readingOpenOpts)}</strong>
          <p class="hint">${esc(U.readingOpenOptsHint)}</p>
        </div>
        <button class="btn" type="button" id="s-open-lens">${esc(U.readingOpenOpts)}</button>
      </div>
      <p class="hint" id="s-lens-note" hidden></p>`;
    body.querySelector<HTMLInputElement>("#s-rail-w")!.addEventListener("change", (e) => {
      const n = Number((e.target as HTMLInputElement).value);
      const next = Math.min(RAIL_MAX, Math.max(RAIL_MIN, n / 16));
      savePrefs({ ...loadPrefs(), railW: next });
      document.getElementById("shell")?.style.setProperty("--rail-size", `${next}rem`);
      (e.target as HTMLInputElement).value = String(Math.round(next * 16));
    });
    body.querySelector("#s-open-lens")!.addEventListener("click", () => {
      const lens = document.getElementById("view-lens");
      if (!lens) {
        const note = body.querySelector<HTMLElement>("#s-lens-note")!;
        note.hidden = false;
        note.textContent = U.readingOpenOptsNeedView;
        return;
      }
      sheet.close();
      lens.click();
    });
  }

  function renderAbout(body: HTMLElement) {
    body.innerHTML = `
      <h2>${esc(U.navAbout)}</h2>
      <p class="hint">${esc(U.aboutIntro)}</p>
      <p class="settings-build">Vinc ${esc(buildId)}</p>
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
    // 게이트도 타지 않는다(tests/test_copy_parity.py 가 이제 막는다).
    body.innerHTML = `
      <h2>${esc(U.navAccount)}</h2>
      <p class="hint">${esc(U.accountIntro)}</p>
      ${accountMode() ? `
      <!-- 셸 계정 모드(VIN-128): 그래프는 계정에 있고 이 기기에는 사본이 없다. 로컬
           분기의 저장 위치 줄과 같은 자리에서 그 사실을 말한다. -->
      <label>${U.storage}</label>
      <p class="rail-sync"><span class="sync-dot"></span><span>${U.storageAccount}</span></p>
      <hr class="settings-sep" />` : ""}
      ${remote() ? `
      <label>${U.account}</label>
      <div id="web-acct" class="hint" style="font-size:.85rem">…</div>
      <label>${U.space}</label>
      <p class="hint">${U.spaceHint}</p>
      <p><select id="space-pick"><option value="">${U.spacePersonal}</option></select></p>
      <div id="space-note" class="hint" style="font-size:.8rem"></div>` : ""}
      ${!remote() ? `
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
      <div class="syncdom-head">
        <p class="hint" style="margin:.5rem 0 0">${U.syncDomains}</p>
        <span class="syncdom-acts">
          <button class="btn quiet" id="sync-all" type="button">${U.syncAll}</button>
          <button class="btn quiet" id="sync-clear" type="button">${U.syncClear}</button>
        </span>
      </div>
      <input id="sync-filter" class="syncdom-filter" type="search" autocomplete="off"
             spellcheck="false" placeholder="${U.syncFilter}" hidden />
      <div id="sync-domains" class="syncdom">…</div>
      <p class="hint" id="sync-picked" style="font-size:.78rem;margin:.35rem 0 0"></p>
      <p><button class="btn" id="sync-now">${U.syncNow}</button></p>
      <div id="sync-result" class="hint" style="font-size:.78rem"></div>` : ""}`;
    if (remote()) {
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
    //
    // 주제는 **나무다**. 슬래시가 계층을 만들고(`vinc/design/surfaces`), 그 나무가
    // 이 앱의 다른 곳에서는 이미 나무로 보인다(LNB 트리). 여기만 평면 목록이었다 —
    // 주제가 예순 개면 스물다섯 번 스크롤해서 하나씩 짚는 화면이다.
    //
    // ⚠ 평면 목록이 감추고 있던 것이 하나 더 있다. export 의 매칭은 **정확 일치**다
    // (`api/sync.py`: `c.domain IN $domains`). 그래서 `vinc/design` 만 고르면
    // `vinc/design/surfaces` 는 안 올라간다. 목록에서는 둘이 나란한 줄이라 그 차이가
    // 안 보였고, 고른 사람은 아래가 딸려 간다고 믿을 이유가 충분했다. 나무로 그리고
    // 상위를 고르면 아래가 함께 골라지게 하면, 화면이 하는 말과 실제가 같아진다.
    // 계정으로 가는 것은 여전히 **실재하는 도메인 문자열 목록**이다(중간 마디는 화면의
    // 셈일 뿐 보내지 않는다).
    const domBox = body.querySelector<HTMLElement>("#sync-domains")!;
    const syncResult = body.querySelector<HTMLElement>("#sync-result")!;
    const pickedLine = body.querySelector<HTMLElement>("#sync-picked")!;
    const filterBox = body.querySelector<HTMLInputElement>("#sync-filter")!;

    type DomRow = { domain: string; total?: number };
    type DomNode = { seg: string; path: string; row?: DomRow; kids: DomNode[] };

    let domRows: DomRow[] = [];
    let domTree: DomNode[] = [];
    let picked = new Set<string>();
    //: 펼친 마디. 기본은 1단만 펼친다 — 예순 줄을 한 번에 펴면 목록으로 돌아간다.
    const open = new Set<string>();
    let filter = "";

    function buildTree(rows: DomRow[]): DomNode[] {
      const root: DomNode = { seg: "", path: "", kids: [] };
      const at = new Map<string, DomNode>([["", root]]);
      for (const r of rows) {
        let cur = root, path = "";
        for (const seg of r.domain.split("/")) {
          path = path ? path + "/" + seg : seg;
          let n = at.get(path);
          if (!n) { n = { seg, path, kids: [] }; at.set(path, n); cur.kids.push(n); }
          cur = n;
        }
        cur.row = r;              // 실재하는 도메인만 행을 갖는다
      }
      return root.kids;
    }
    //: 이 마디 아래의 **실재 도메인** 전부(자기 포함). 계정으로 보내는 단위다.
    function leavesOf(n: DomNode): string[] {
      const out = n.row ? [n.row.domain] : [];
      for (const k of n.kids) out.push(...leavesOf(k));
      return out;
    }
    function totalOf(n: DomNode): number {
      return (Number(n.row?.total) || 0) + n.kids.reduce((a, k) => a + totalOf(k), 0);
    }
    //: 거르개에 걸리는가 — 자기 경로나 자손 중 하나라도 맞으면 남는다.
    function matches(n: DomNode): boolean {
      if (!filter) return true;
      if (n.path.toLowerCase().includes(filter)) return true;
      return n.kids.some(matches);
    }

    function rowHtml(n: DomNode, depth: number): string {
      const kids = n.kids.filter(matches);
      const mine = leavesOf(n);
      const on = mine.filter((d) => picked.has(d)).length;
      const state = on === 0 ? "" : on === mine.length ? " checked" : " data-part=\"1\"";
      // 거르개가 켜져 있으면 맞는 가지는 펴서 보여 준다 — 거르고도 접혀 있으면
      // 거른 보람이 없다.
      const isOpen = kids.length > 0 && (open.has(n.path) || !!filter);
      const label = n.path === "(none)" ? U.syncNoTopic : n.seg;
      const caret = kids.length
        ? `<button class="syncdom-caret" type="button" data-open="${n.path}"
             aria-expanded="${isOpen}">${isOpen ? "▾" : "▸"}</button>`
        : `<span class="syncdom-caret"></span>`;
      const head = `<div class="syncdom-row" style="padding-left:${depth * 1.1}rem">
        ${caret}
        <label class="syncdom-lab">
          <input type="checkbox" data-dom="${n.path}"${state} />
          <span class="syncdom-name">${label}</span>
        </label>
        <span class="syncdom-n">${totalOf(n)}</span>
      </div>`;
      return head + (isOpen ? kids.map((k) => rowHtml(k, depth + 1)).join("") : "");
    }

    async function save() {
      try { await api!.setSyncDomains([...picked]); } catch { /* 사이드카 미가동 무시 */ }
    }

    function draw() {
      const top = domTree.filter(matches);
      if (!domRows.length) { domBox.textContent = U.syncNoDomains; pickedLine.textContent = ""; return; }
      // 거르개는 목록이 길 때만 나온다. 여덟 줄짜리 화면에 검색칸을 두는 것은
      // 자리를 쓰면서 아무 것도 덜어 주지 않는다.
      filterBox.hidden = domRows.length < 12;
      // 안내는 "고른 것이 없다"는 **사실**이라 그 사실이 뒤집힐 때 함께 뒤집힌다.
      const nudge = hasAccount() && !picked.size
        ? `<p class="hint" style="margin:.2rem 0 .5rem">${U.syncNonePicked}</p>` : "";
      domBox.innerHTML = nudge + (top.length
        ? top.map((n) => rowHtml(n, 0)).join("")
        : `<p class="hint" style="margin:.2rem 0">${U.syncNoMatch}</p>`);
      // 반선택은 속성으로 못 준다 — DOM 프로퍼티다.
      domBox.querySelectorAll<HTMLInputElement>("[data-part]").forEach((cb) => { cb.indeterminate = true; });
      const nodes = domRows.reduce((a, r) => a + (Number(r.total) || 0), 0);
      const picking = domRows.filter((r) => picked.has(r.domain));
      pickedLine.textContent = U.syncPicked(picking.length, domRows.length,
        picking.reduce((a, r) => a + (Number(r.total) || 0), 0));
      // 셈이 0 이면 위 문장이 "노드 0개"라 오히려 시끄럽다 — 아무것도 안 골랐을 때는
      // 위의 nudge 가 이미 그 말을 한다.
      if (!picking.length) pickedLine.textContent = "";
      void nodes;
    }

    function nodeAt(path: string): DomNode | null {
      let list = domTree, found: DomNode | null = null;
      for (const seg of path.split("/")) {
        found = list.find((n) => n.seg === seg) || null;
        if (!found) return null;
        list = found.kids;
      }
      return found;
    }

    domBox.addEventListener("change", (e) => {
      const cb = (e.target as HTMLElement).closest<HTMLInputElement>("[data-dom]");
      if (!cb) return;
      const n = nodeAt(cb.dataset.dom!);
      if (!n) return;
      // 상위를 고르면 그 아래가 함께 간다. 정확 일치로 내보내므로 여기서 펴 두지
      // 않으면 화면이 한 말과 올라가는 것이 갈린다.
      for (const d of leavesOf(n)) { if (cb.checked) picked.add(d); else picked.delete(d); }
      void save();
      draw();
    });
    domBox.addEventListener("click", (e) => {
      const b = (e.target as HTMLElement).closest<HTMLElement>("[data-open]");
      if (!b) return;
      const p = b.dataset.open!;
      if (open.has(p)) open.delete(p); else open.add(p);
      draw();
    });
    filterBox.addEventListener("input", () => { filter = filterBox.value.trim().toLowerCase(); draw(); });
    body.querySelector("#sync-all")!.addEventListener("click", () => {
      picked = new Set(domRows.map((r) => r.domain)); void save(); draw();
    });
    body.querySelector("#sync-clear")!.addEventListener("click", () => {
      picked = new Set(); void save(); draw();
    });

    async function loadDomains() {
      try {
        const res = await api!.syncDomains(cfg.owner);
        domRows = (res.domains || []) as DomRow[];
        picked = new Set<string>(res.selected || []);
        domTree = buildTree(domRows);
        open.clear();
        for (const n of domTree) if (n.kids.length) open.add(n.path);   // 1단만 편다
        draw();
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
      <h2>${esc(U.navAi)}</h2>
      <p class="hint">${esc(U.aiIntro)}</p>
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
      <h2>${esc(U.navAdvanced)}</h2>
      <p class="hint">${esc(U.advancedIntro)}</p>
      <label>${U.lock}</label><p class="hint">${COPY.settings.lock}</p>
      <hr class="settings-sep" />
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

  function go(cat: SettingsCat, tab?: DataTab) {
    if (tab) toolsTab = tab;
    active = CATS.some((c) => c.id === cat) ? cat : "general";
    shell();
  }
  shell();
  return go;
}
