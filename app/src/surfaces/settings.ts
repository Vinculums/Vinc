import { accountMode, api, buildId, cfg, forgetMemberKey, messageOf, remote, selfHostedSeats, selfHostedSignOut, WEB, shellInvoke } from "../api";
import { mcpUrl, selfHosted } from "../self-hosted";
import { accountKey, accountWho, connectAccount, forgetAccount, hasAccount, keyIsOnDisk,
         mcpKeyPresent,
         setAccountKey } from "../account-connect";
import { COPY } from "../copy";
import { esc, openSheet, type SheetHandle } from "../components";
import { loadPrefs, savePrefs, RAIL_MIN, RAIL_MAX } from "../view-prefs";
import { DOC_CHOICES, DOC_DEFAULTS, currentDocPrefs, setDocPrefs, type DocPrefs } from "../doc-options";
import { disposeEditorsIn } from "../doc-editor";
import { mountStorage } from "../storage-mode";
import { mountTopicConversion } from "../topic-conversion";
import { mountPushSettings } from "../web-push-ui";
import { shortcutList } from "../shortcuts";

/** Settings — 왼쪽 분류 레일 + 본문(2026-09-22).
 *  레일 바닥의 설정 버튼이 예전 Settings · GRAPH.md · Tools(가져오기/공유)를 한 상자에 담는다.
 *  분류는 다섯 묶음(앱 · 계정 · 연결 · 가져오기·공유 · 시스템) 아래에 서고, 보일 분류가 없는 묶음은
 *  머리째 그리지 않는다. 시트 머리의 ✕ 가 닫기이고, 여기 안에 닫기를 하나 더 두지 않는다.
 *  모든 본문은 아래 두 도우미(`settingsCard` · `settingsRow`)로 짓는다. */

type SettingsCat =
  | "general" | "appearance" | "reading" | "shortcuts" | "notifications"
  | "account" | "spaces" | "storage"
  | "apps" | "assistants" | "terminal"
  | "import" | "share" | "graphmd"
  | "advanced" | "about";
/** 묶음 머리가 생기기 전의 분류 id. 호출부(main · lnb · home · authoring · tools · view)와 `#intake`·`#packs`
 *  별칭이 아직 이 이름으로 연다. `data` 는 탭 인자로 가져오기·공유를 고른다.
 *  제거 조건: 위 호출부가 전부 새 분류 id(apps · import · share)로 옮겨진 뒤. */
type LegacyCat = "ai" | "data";
export type DataTab = "import" | "share";

function resolveCat(cat: SettingsCat | LegacyCat, tab?: DataTab): SettingsCat {
  if (cat === "data") return tab ?? "import";
  if (cat === "ai") return "apps";
  return cat;
}

type Live = {
  go: (cat: SettingsCat | LegacyCat, dataTab?: DataTab) => void;
  close: () => void;
};
let live: Live | null = null;

/** 설정 시트를 연다. 이미 열려 있으면 `toggle` 이 참일 때만 닫고, 아니면 분류만 옮긴다. */
export function openSettings(start: SettingsCat | LegacyCat = "general", dataTab?: DataTab, toggle = false) {
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
  let queued: { cat: SettingsCat | LegacyCat; tab?: DataTab } | null = null;
  live = {
    go: (cat, tab) => { queued = { cat, tab }; },
    close: sheet.close,
  };
  void renderSettings(sheet.body, sheet, resolveCat(start, dataTab)).then((go) => {
    if (!live) return;
    live.go = go;
    if (queued) go(queued.cat, queued.tab);
  });
}

// ── 본문을 짓는 두 도우미 ────────────────────────────────────────────────────
// 카드 하나에 줄 여럿. 줄은 왼쪽에 제목과 설명, 오른쪽에 컨트롤이다. 넓은 컨트롤(글 상자 ·
// 드롭존 · 호스트 목록 같은 것)은 줄 글 아래 전폭(`wide`)에 선다.
// ⚠ 제목과 설명은 **일반 글**이다(여기서 이스케이프한다). 마크업이 필요한 상태 칸은 `extra` 에
// 호출부가 이스케이프해서 넣는다. 입력·선택이 하나인 줄은 `labelFor` 로 제목을 그 이름표로 묶는다.
type Row = {
  title?: string;
  desc?: string;
  extra?: string;
  control?: string;
  wide?: string;
  labelFor?: string;
  /** 제목 요소의 id. 묶음 컨트롤(세그)이 `aria-labelledby` 로 가리킨다. */
  titleId?: string;
  /** 줄 자체의 속성(`id="…" hidden` 처럼). */
  attrs?: string;
};

export function settingsRow(r: Row): string {
  const id = r.titleId ? ` id="${r.titleId}"` : "";
  const title = r.title === undefined ? ""
    : r.labelFor ? `<label class="set-row-title" for="${r.labelFor}"${id}>${esc(r.title)}</label>`
    : `<span class="set-row-title"${id}>${esc(r.title)}</span>`;
  const text = title || r.desc || r.extra
    ? `<div class="set-row-text">${title}${r.desc ? `<p class="set-row-desc">${esc(r.desc)}</p>` : ""}${r.extra ?? ""}</div>`
    : "";
  return `<div class="set-row${r.wide !== undefined ? " set-row--wide" : ""}"${r.attrs ? ` ${r.attrs}` : ""}>${text}${
    r.control ? `<div class="set-row-ctl">${r.control}</div>` : ""}${
    r.wide !== undefined ? `<div class="set-row-wide">${r.wide}</div>` : ""}</div>`;
}

/** 카드. 머리(`heading`)는 줄 여럿을 한 이름으로 묶을 때만 단다. `foot` 은 카드 아래의 짧은
 *  안내(마크업, 호출부가 이스케이프)다. `bare` 는 자기 틀을 가진 모듈(드롭존 · 저장 위치)을 담을
 *  때 카드의 틀을 걷는다 — 틀 안에 틀이 서지 않게. */
export function settingsCard(rows: string[], opts: { heading?: string; foot?: string; bare?: boolean } = {}): string {
  return `<section class="set-card-wrap">${opts.heading ? `<h3 class="set-card-head">${esc(opts.heading)}</h3>` : ""}
    <div class="set-card${opts.bare ? " set-card--bare" : ""}">${rows.join("")}</div>${opts.foot ?? ""}</section>`;
}

/** 카드 아래 안내 한 줄. */
const cardNote = (text: string, attrs = "") => `<p class="set-card-note"${attrs ? ` ${attrs}` : ""}>${esc(text)}</p>`;
/** 줄 글 아래의 상태 한 줄(비어 있으면 CSS 가 접는다). */
const rowLine = (text: string, attrs = "") => `<p class="set-row-desc"${attrs ? ` ${attrs}` : ""}>${esc(text)}</p>`;

function paneHead(title: string, intro?: string): string {
  return `<h2>${esc(title)}</h2>${intro ? `<p class="hint">${esc(intro)}</p>` : ""}`;
}

async function renderSettings(
  el: HTMLElement,
  sheet: SheetHandle,
  start: SettingsCat = "general",
): Promise<(cat: SettingsCat | LegacyCat, tab?: DataTab) => void> {
  const U = COPY.ui.settingsUi;

  type Cat = { id: SettingsCat; label: string; desktop?: true; when?: () => boolean };
  // 셸 전용(`desktop`)은 웹에서 숨긴다. `when` 은 그 밖의 조건이다: 알림은 웹에서만(셸은 서비스
  // 워커를 쓰지 않는다), 공간은 계정 그래프일 때만(로컬 모드와 셀프호스트 허브에는 고를 공간이 없다).
  const ALL_CATS: { label: string; cats: Cat[] }[] = [
    { label: U.groupApp, cats: [
      { id: "general", label: U.navGeneral },
      { id: "appearance", label: U.navAppearance },
      { id: "reading", label: U.navReading },
      // 그래프 분류는 없다: view-prefs 의 값 중 지금 나침반 그림을 바꾸는 것이 없다(view.ts 는 문서 열 폭만 읽는다).
      { id: "shortcuts", label: U.navShortcuts },
      { id: "notifications", label: U.navNotifications, when: () => WEB && !selfHosted() },
    ] },
    { label: U.groupAccount, cats: [
      { id: "account", label: U.navAccount },
      { id: "spaces", label: U.navSpaces, when: () => remote() && !selfHosted() },
      { id: "storage", label: U.navStorage, desktop: true },
    ] },
    { label: U.groupConnections, cats: [
      { id: "apps", label: U.navApps, desktop: true },
      { id: "assistants", label: U.mcpRemote, desktop: true },
      { id: "terminal", label: U.navTerminal, desktop: true },
    ] },
    { label: U.groupData, cats: [
      { id: "import", label: COPY.ui.tools.import },
      { id: "share", label: U.navShare },
      { id: "graphmd", label: U.navGraphmd },
    ] },
    { label: U.groupSystem, cats: [
      { id: "advanced", label: U.navAdvanced, desktop: true },
      { id: "about", label: U.navAbout },
    ] },
  ];
  const GROUPS = ALL_CATS
    .map((g) => ({ ...g, cats: g.cats.filter((c) => (!c.desktop || !WEB) && (!c.when || c.when())) }))
    .filter((g) => g.cats.length > 0);
  const CATS = GROUPS.flatMap((g) => g.cats);

  let active: SettingsCat = CATS.some((c) => c.id === start) ? start : "general";
  let toolsTab: DataTab = active === "share" ? "share" : "import";
  let cleanup: (() => void) | null = null;
  let foot: HTMLElement = sheet.foot;
  // ⚠ 본문 칸(`#settings-body`)은 시트만큼 산다. 그래서 `body.isConnected` 로는 "아직 그 분류의
  // 본문인가"를 못 가른다. 늦게 끝나는 일(계정 연결 · 키 확인 · 등록 결과 · 모듈 불러오기)은 그린
  // 때의 표(`paneToken`)를 들고 있다가, 분류가 바뀌었거나 시트가 닫혔으면 아무것도 그리지 않는다.
  let paneGen = 0;
  const paneToken = () => { const g = paneGen; return () => g === paneGen && !sheet.closed; };

  // 레일은 한 번만 그린다. 분류를 바꿀 때 레일까지 다시 그리면 방금 누른 버튼이 사라져
  // 초점이 body 로 떨어진다. 바뀌는 것은 고른 표시와 본문뿐이다.
  // 발(`#settings-foot`)은 본문 칸 안에 산다: 시트의 발을 쓰면 저장·공유 버튼 줄이 레일 밑까지
  // 번져 레일이 짧아지고 버전 줄이 뛰었다.
  function shell() {
    cleanup?.();
    cleanup = null;
    el.innerHTML = `
      <div class="settings">
        <nav class="settings-cats" aria-label="${esc(U.title)}">
          ${GROUPS.map((g, gi) => `<div class="settings-group" role="group" aria-labelledby="settings-group-${gi}">
            <p class="settings-group-head" id="settings-group-${gi}">${esc(g.label)}</p>
            ${g.cats.map((c) => `<button type="button" class="settings-cat${c.id === active ? " on" : ""}"
              data-cat="${c.id}"${c.id === active ? ' aria-current="page"' : ""}>${esc(c.label)}${
                c.id === "graphmd" ? `<span id="settings-graphmd-badge" class="settings-cat-badge" hidden></span>` : ""
              }</button>`).join("")}
          </div>`).join("")}
          <span class="settings-cat-space"></span>
          <div class="settings-ver hint">Vinc ${esc(buildId)}</div>
        </nav>
        <div class="settings-pane">
          <div class="settings-main settings-body" id="settings-body"></div>
          <div class="sheet-foot settings-foot" id="settings-foot"></div>
        </div>
      </div>`;
    foot = el.querySelector<HTMLElement>("#settings-foot")!;
    el.querySelectorAll<HTMLElement>(".settings-cat").forEach((b) =>
      b.addEventListener("click", () => pick(b.dataset.cat as SettingsCat)));
    void import("../conventions").then((m) => m.refreshBadge());
    renderBody();
    el.querySelector<HTMLElement>(".settings-cat.on")?.focus({ preventScroll: true });
    revealActive();
  }

  /** 고른 분류가 레일 안에 보이게 레일만 굴린다(낮은 창에서는 레일이 상자보다 길다).
   *  `scrollIntoView` 를 쓰지 않는다: 그것은 딤까지 굴리고, 시트가 커지는 동안(변형 중)의 상자로
   *  잰다. 레일이 기준 상자(`position: relative`)라 `offsetTop` 이 레일 안의 자리다. */
  function revealActive() {
    const nav = el.querySelector<HTMLElement>(".settings-cats");
    const on = nav?.querySelector<HTMLElement>(".settings-cat.on");
    if (!nav || !on) return;
    const pad = on.offsetHeight;
    if (nav.scrollHeight > nav.clientHeight) {
      const top = on.offsetTop, bottom = top + on.offsetHeight;
      // 마지막 묶음이면 레일 끝까지 내린다: 그 아래의 버전 줄도 함께 보인다.
      const lastGroup = !on.closest(".settings-group")?.nextElementSibling?.matches(".settings-group");
      if (lastGroup) nav.scrollTop = nav.scrollHeight;
      else if (top - pad < nav.scrollTop) nav.scrollTop = Math.max(0, top - pad);
      else if (bottom + pad > nav.scrollTop + nav.clientHeight) nav.scrollTop = bottom + pad - nav.clientHeight;
    }
    if (nav.scrollWidth > nav.clientWidth) {          // 좁은 창: 레일이 가로 한 줄이다
      const left = on.offsetLeft, right = left + on.offsetWidth;
      if (left < nav.scrollLeft) nav.scrollLeft = left;
      else if (right > nav.scrollLeft + nav.clientWidth) nav.scrollLeft = right - nav.clientWidth;
    }
  }

  function renderBody() {
    paneGen += 1;
    cleanup?.();
    cleanup = null;
    foot.innerHTML = "";
    const body = el.querySelector<HTMLElement>("#settings-body")!;
    body.scrollTop = 0;
    if (active === "general") renderGeneral(body);
    else if (active === "appearance") renderAppearance(body);
    else if (active === "reading") renderReading(body);
    else if (active === "shortcuts") renderShortcuts(body);
    else if (active === "notifications") renderNotifications(body);
    else if (active === "account") renderAccount(body);
    else if (active === "spaces") renderSpaces(body);
    else if (active === "storage") renderStorage(body);
    else if (active === "apps") renderApps(body);
    else if (active === "assistants") renderAssistants(body);
    else if (active === "terminal") renderTerminal(body);
    else if (active === "import" || active === "share") renderData(body);
    else if (active === "graphmd") renderGraphmd(body);
    else if (active === "advanced") renderAdvanced(body);
    else renderAbout(body);
  }

  function pick(next: SettingsCat) {
    if (next === "import" || next === "share") toolsTab = next;
    if (next === active) return;
    active = next;
    el.querySelectorAll<HTMLElement>(".settings-cat").forEach((b) => {
      const on = b.dataset.cat === active;
      b.classList.toggle("on", on);
      if (on) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
    });
    renderBody();
    revealActive();
  }

  // ── 앱 ────────────────────────────────────────────────────────────────────
  function renderGeneral(body: HTMLElement) {
    const lang = cfg.lang === "en" ? "en" : "ko";
    // 언어 이름은 덱에 넣지 않는다: 각 언어가 자기 이름으로 선다(영어 화면에서 "Korean"
    // 이 되면 영어를 못 읽는 사람이 자기 언어를 못 찾는다). `data-lang-opt` 가 카피
    // 인벤토리(tests/design/test_copy_parity.py)의 그 면제 표식이다.
    body.innerHTML = paneHead(U.navGeneral, U.generalIntro) + settingsCard([
      settingsRow({ title: U.language, desc: U.languageHint, labelFor: "s-lang", control: `
        <select id="s-lang" class="settings-select">
          <option data-lang-opt="ko" value="ko"${lang === "ko" ? " selected" : ""}>한국어</option>
          <option data-lang-opt="en" value="en"${lang === "en" ? " selected" : ""}>English</option>
        </select>` }),
    ], { foot: cardNote(U.languageReload) });
    body.querySelector<HTMLSelectElement>("#s-lang")!.addEventListener("change", (e) => {
      cfg.lang = (e.target as HTMLSelectElement).value as "en" | "ko";
      location.reload();
    });
  }

  const THEME_OPTS = [
    { id: "light", label: U.themeLight },
    { id: "dark", label: U.themeDark },
    { id: "auto", label: U.themeSystem },
  ] as const;

  function renderAppearance(body: HTMLElement) {
    const now = localStorage.getItem("vinc.theme") || "dark";
    body.innerHTML = paneHead(U.navAppearance, U.appearanceIntro) + settingsCard([
      settingsRow({ title: U.theme, desc: U.themeHint, titleId: "s-theme-t", control: `
        <span class="seg" role="group" aria-labelledby="s-theme-t">${THEME_OPTS.map((t) =>
          `<button type="button" class="chip${now === t.id ? " active" : ""}" data-theme-opt="${t.id}"
            aria-pressed="${now === t.id}">${esc(t.label)}</button>`).join("")}</span>` }),
    ]);
    const opts = [...body.querySelectorAll<HTMLElement>("[data-theme-opt]")];
    opts.forEach((b) => b.addEventListener("click", async () => {
      localStorage.setItem("vinc.theme", b.dataset.themeOpt!);
      // 다시 그리지 않고 눌림 표시만 옮긴다 — 다시 그리면 누른 버튼의 초점이 사라진다.
      opts.forEach((o) => {
        const on = o === b;
        o.classList.toggle("active", on);
        o.setAttribute("aria-pressed", String(on));
      });
      const { applyTheme } = await import("../main");
      applyTheme();
    }));
  }

  function renderReading(body: HTMLElement) {
    const D = COPY.ui.docOpts;
    let doc = currentDocPrefs();
    const isDefault = () => (Object.keys(DOC_DEFAULTS) as (keyof DocPrefs)[]).every((k) => doc[k] === DOC_DEFAULTS[k]);
    // 글자 크기 · 읽기 폭 · 줄 간격은 doc-options 가 저장하고 얹는다(머리 단추는 걷혔다).
    // 고르는 즉시 `<html>` 의 `data-doc-*` 가 바뀌어 열린 문서가 그 자리에서 다시 흐른다.
    const docRow = (k: keyof DocPrefs, label: string) => settingsRow({
      title: label, titleId: `s-doc-${k}-t`, control: `
        <span class="seg" role="group" aria-labelledby="s-doc-${k}-t">${(DOC_CHOICES[k] as readonly string[]).map((v) =>
          `<button type="button" class="chip${doc[k] === v ? " active" : ""}" data-dk="${k}" data-dv="${v}"
            aria-pressed="${doc[k] === v}">${esc(D.choice[v as keyof typeof D.choice])}</button>`).join("")}</span>` });
    const rem = loadPrefs().railW;
    const px = Math.round(rem * 16);
    const minPx = Math.round(RAIL_MIN * 16);
    const maxPx = Math.round(RAIL_MAX * 16);
    body.innerHTML = paneHead(U.navReading, U.readingIntro)
      + settingsCard([
        docRow("text", D.text), docRow("width", D.width), docRow("lines", D.lines),
        settingsRow({ title: U.readingDefaults, control:
          `<button class="btn" type="button" id="s-doc-reset"${isDefault() ? " disabled" : ""}>${esc(D.reset)}</button>` }),
      ], { foot: cardNote(D.note) })
      + settingsCard([
        settingsRow({ title: U.readingPane, desc: U.readingPaneHint, labelFor: "s-rail-w", control: `
          <span class="set-num"><input id="s-rail-w" type="number" min="${minPx}" max="${maxPx}" step="16" value="${px}" />
          <span aria-hidden="true">px</span></span>` }),
      ]);
    // 그래프의 '보기 옵션 열기' 줄은 걷었다: 옵션·범례 단추가 없어지고 그 손잡이는 LNB 의 나침반 칸으로 갔다
    const reset = body.querySelector<HTMLButtonElement>("#s-doc-reset")!;
    const paint = () => {
      body.querySelectorAll<HTMLElement>("[data-dk]").forEach((b) => {
        const on = doc[b.dataset.dk as keyof DocPrefs] === b.dataset.dv;
        b.classList.toggle("active", on);
        b.setAttribute("aria-pressed", String(on));
      });
      reset.disabled = isDefault();
    };
    body.querySelectorAll<HTMLElement>("[data-dk]").forEach((b) => b.addEventListener("click", () => {
      doc = { ...doc, [b.dataset.dk!]: b.dataset.dv } as DocPrefs;
      setDocPrefs(doc);
      paint();
    }));
    reset.addEventListener("click", () => {
      doc = { ...DOC_DEFAULTS };
      setDocPrefs(doc);
      paint();
      // 누른 버튼이 꺼지면 초점이 body 로 떨어진다. 같은 카드의 첫 고르기로 옮긴다.
      body.querySelector<HTMLElement>("[data-dk]")?.focus();
    });
    body.querySelector<HTMLInputElement>("#s-rail-w")!.addEventListener("change", (e) => {
      const n = Number((e.target as HTMLInputElement).value);
      const next = Math.min(RAIL_MAX, Math.max(RAIL_MIN, n / 16));
      savePrefs({ ...loadPrefs(), railW: next });
      document.getElementById("shell")?.style.setProperty("--rail-size", `${next}rem`);
      (e.target as HTMLInputElement).value = String(Math.round(next * 16));
    });
  }

  function renderShortcuts(body: HTMLElement) {
    // `?` 시트와 같은 표다(shortcuts.ts). 여기서 줄을 따로 적지 않는다.
    body.innerHTML = paneHead(U.navShortcuts, U.shortcutsIntro) + settingsCard(
      shortcutList().map((s) => settingsRow({ title: s.label, control: `<kbd class="set-kbd">${esc(s.keys)}</kbd>` })));
  }

  function renderNotifications(body: HTMLElement) {
    // 이 기기의 알림(웹 푸시). 웹에서만 그린다: 데스크톱 셸은 서비스 워커를 쓰지 않는다.
    body.innerHTML = paneHead(U.navNotifications) + settingsCard([
      settingsRow({ title: COPY.push.title, wide: `<div id="push-box"></div>` }),
    ]);
    cleanup = mountPushSettings(body.querySelector<HTMLElement>("#push-box")!);
  }

  function renderAbout(body: HTMLElement) {
    body.innerHTML = paneHead(U.navAbout, U.aboutIntro) + settingsCard([
      settingsRow({ wide: `
        <div class="settings-about-id">
          <span class="brand-logo" aria-hidden="true"></span>
          <p class="settings-build">Vinc ${esc(buildId)}</p>
        </div>` }),
    ], { foot: cardNote(remote() ? COPY.sovereigntyWeb : COPY.sovereignty) });
  }

  // ── 데이터 ────────────────────────────────────────────────────────────────
  function renderData(body: HTMLElement) {
    const T = COPY.ui.tools;
    const share = toolsTab === "share";
    body.innerHTML = paneHead(share ? U.navShare : T.import, share ? T.shareSub : T.importSub)
      + settingsCard([settingsRow({ wide: `<div id="settings-tools"></div>` })], { bare: !share });
    const pane = body.querySelector<HTMLElement>("#settings-tools")!;
    const alive = paneToken();
    // 분류들은 본문 칸의 발(`foot`)을 함께 쓴다. 모듈이 오는 사이 다른 분류로
    // 옮겼으면 이 판은 이미 떨어져 나갔으므로 아무것도 안 그린다 — 그리면 그 발
    // 버튼이 지금 분류 밑에 선다. 판 안의 늦은 쓰기도 같은 규칙이다(tools.ts ·
    // conventions.ts 가 `isConnected` 로 본다).
    void import("../tools").then((m) => {
      if (!alive() || !pane.isConnected) return;
      cleanup = m.mountTools(
        { el: sheet.el, body: pane, foot, close: sheet.close }, toolsTab);
    });
  }

  function renderGraphmd(body: HTMLElement) {
    body.innerHTML = paneHead(U.navGraphmd, U.graphmdIntro)
      + settingsCard([settingsRow({ wide: `<div id="settings-conv"></div>` })]);
    const pane = body.querySelector<HTMLElement>("#settings-conv")!;
    const alive = paneToken();
    void import("../conventions").then((m) => {
      if (!alive() || !pane.isConnected) return;       // 위 renderData 와 같은 이유
      void m.mountConventions(pane, { body: pane, foot, close: sheet.close });
    });
  }

  // ── 계정 ──────────────────────────────────────────────────────────────────
  function renderAccount(body: HTMLElement) {
    const alive = paneToken();
    // ⚠ 이 블록의 카피는 예전에 여기 EN/KO 사전으로 인라인돼 있었다. 덱 밖 카피는 언어
    // 프록시도 게이트도 타지 않는다(tests/design/test_copy_parity.py 가 이제 막는다).
    // 셀프호스트 허브: 계정·공간·팀으로 옮기기·알림이 없다. 이 허브의 로그인과 로그아웃만 둔다.
    if (selfHosted()) {
      body.innerHTML = paneHead(U.navAccount, U.selfHostedIntro) + settingsCard([
        settingsRow({ title: U.account, desc: U.selfHostedSignedIn,
          control: `<button class="btn" id="sh-signout" type="button">${esc(U.acctSignOut)}</button>` }),
        settingsRow({ title: U.selfHostedSeats, extra: `<p class="set-row-desc" id="sh-seats" role="status"></p>` }),
      ]);
      body.querySelector("#sh-signout")!.addEventListener("click", () => { void selfHostedSignOut(); });
      // 좌석·마지막 확인·유예 상태(S8). 허브가 하루 한 번 계정에 묻고 30일 동안 마지막 답을 쓴다.
      void selfHostedSeats().then((s) => {
        const el = alive() ? body.querySelector<HTMLElement>("#sh-seats") : null;
        if (!el || !s) return;
        const day = (sec: unknown) => typeof sec === "number" ? new Date(sec * 1000).toLocaleDateString() : "";
        const used = Number(s.users_used) || 1, allowed = Number(s.users_allowed) || 1;
        el.textContent = s.revoked ? U.selfHostedSeatsRevoked
          : s.state === "paid" ? U.selfHostedSeatsPaid(used, allowed, day(s.last_check))
          : s.state === "grace" ? U.selfHostedSeatsGrace(used, allowed, day(s.last_check), day(s.expires_at))
          : U.selfHostedSeatsFree;
      });
      return;
    }
    if (remote()) {
      // 셸 계정 모드: 쿠키가 없어 /api/me 를 못 부른다. 기기 플로우가 남긴 이름을 쓰고,
      // 로그아웃을 여기 둔다(로그아웃하면 로그인 시트로 돌아간다, VIN-128).
      if (accountMode()) {
        const who = accountWho();
        body.innerHTML = paneHead(U.navAccount, U.accountIntro) + settingsCard([
          settingsRow({ title: U.account, desc: who ? U.webSignedAs(who) : U.acctSignedIn,
            extra: rowLine(U.acctSignOutHint),
            control: `<button class="btn" id="acct-signout" type="button">${esc(U.acctSignOut)}</button>` }),
        ]);
        body.querySelector("#acct-signout")!.addEventListener("click", () => {
          forgetAccount();
          forgetMemberKey();
          // 화면 전체가 계정 그래프를 들고 있다. 부분 갱신하면 옛 계정의 데이터가 남는다.
          location.reload();
        });
        return;
      }
      // 웹모드: /api/me 로 로그인 계정 표시(@username 우선). 연결 섹션은 없다(셸 전용).
      body.innerHTML = paneHead(U.navAccount, U.accountIntro) + settingsCard([
        settingsRow({ title: U.account, extra: `<p class="set-row-desc" id="web-acct">…</p>` }),
      ]);
      const box = body.querySelector<HTMLElement>("#web-acct")!;
      api.me()
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => {
          if (!alive()) return;
          const u = d && d.user;
          if (!u) { box.textContent = U.webNotSignedIn; return; }
          const who = u.username ? "@" + u.username : u.email;
          box.innerHTML = `${esc(U.webSignedAs(who))} · <a href="/account/" target="_blank" rel="noopener">${esc(U.webManage)}</a>`;
        })
        .catch(() => { if (alive()) box.textContent = ""; });
      return;
    }

    // 계정 연결(디바이스 플로우) — 흐름은 `account-connect` 한 벌을 쓴다.
    // 여기서 하는 일은 **그리기**뿐이다: 같은 플로우를 첫 실행 카드와 원격 커넥터
    // 행도 부르므로, 폴링을 이 탭 안에 두면 세 벌이 된다.
    body.innerHTML = paneHead(U.navAccount, U.accountIntro) + `<div id="acct-box"></div>`;
    const acctBox = body.querySelector<HTMLElement>("#acct-box")!;
    function drawAcct() {
      // 연결 여부와 표시할 이름은 **다른 질문**이다. 수동으로 붙여넣은 키는 누구
      // 것인지 알 수 없어 이름이 없고, 이름으로 갈랐더니 로그인해도 미연결 화면이
      // 나왔다. 카피(`acctConnected`)는 빈 이름을 이미 감당한다.
      const who = accountWho();
      if (hasAccount()) {
        acctBox.innerHTML = settingsCard([
          settingsRow({ title: U.account, desc: U.acctConnected(who || ""),
            extra: rowLine(U.acctDisconnectHint)
              + rowLine(U.acctOnDisk, 'id="acct-ondisk" hidden')
              + `<p class="set-row-desc" id="acct-key-failure" role="status" aria-live="polite" hidden>${
                  esc(U.acctKeyFailed)} <button class="btn" id="acct-key-retry" type="button">${esc(U.acctKeyRetry)}</button></p>`,
            control: `<button class="btn" id="acct-disc" type="button">${esc(U.acctDisconnect)}</button>` }),
        ]);
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
            if (!alive()) return;
            onDisk.hidden = !keyIsOnDisk();
          } catch {
            if (!alive()) return;
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
          renderBody();
        });
      } else {
        // 지금 상태를 먼저 말한다. 첫 실행이 선택을 요구하게 되면서(firstrun) 여기가
        // **되돌아오는 유일한 길**이 됐는데, 여태 이 자리는 연결 버튼만 있어서
        // "오프라인"이 고른 상태가 아니라 아직 안 한 일처럼 보였다.
        acctBox.innerHTML = settingsCard([
          settingsRow({ title: U.account, desc: U.acctOffline,
            extra: rowLine(U.acctConnectHint) + `<p class="set-row-desc" id="acct-result" role="status" aria-live="polite"></p>`,
            control: `<button class="btn" id="acct-connect" type="button">${esc(U.acctConnect)}</button>` }),
        ]);
        acctBox.querySelector("#acct-connect")!.addEventListener("click", () => void startConnect());
      }
    }

    async function startConnect() {
      const acctResult = acctBox.querySelector<HTMLElement>("#acct-result")!;
      const r = await connectAccount(() => { if (alive()) acctResult.textContent = U.acctConnecting; });
      // 기다리는 사이 다른 분류로 옮겼거나 시트를 닫았으면 그리지 않는다(연결 자체는 끝났다).
      if (!alive()) return;
      if (r === "connected") { renderBody(); return; }
      if (r === "expired") { acctResult.textContent = U.acctExpired; return; }
      if (r === "failed") acctResult.textContent = U.acctFail;
    }
    drawAcct();
  }

  // 공간 전환은 사이드바 머리에 있다(`space-switcher.ts`). 여기는 그 자리를 한 줄로 알리고,
  // 개인 공간이면 주제를 새 팀 공간으로 옮기는 칸을 둔다.
  function renderSpaces(body: HTMLElement) {
    body.innerHTML = paneHead(U.navSpaces, U.spacesIntro)
      + settingsCard([settingsRow({ title: U.space, desc: U.spaceHint })])
      + (!cfg.team ? settingsCard([settingsRow({ wide: `<div id="topic-conversion"></div>` })]) : "");
    const conversion = body.querySelector<HTMLElement>("#topic-conversion");
    if (conversion) cleanup = mountTopicConversion(conversion);
  }

  // ── Storage 탭 (셸 전용) ─────────────────────────────────────────────────
  // 이 기기가 그래프를 두는 곳과 옮기기(`storage-mode.ts`). 웹 Vinc 의 데이터는 늘 계정에 있어
  // 이 탭이 없다. 옮기기에 로그인이 필요하면 계정 탭으로 보낸다.
  function renderStorage(body: HTMLElement) {
    body.innerHTML = paneHead(U.navStorage, U.storageIntro)
      + settingsCard([settingsRow({ wide: `<div id="st-box"></div>` })], { bare: true });
    mountStorage(body.querySelector<HTMLElement>("#st-box")!, { openAccount: () => go("account") });
  }

  // ── 연결 (MCP, 셸 전용) ───────────────────────────────────────────────────
  // 등록 가능한 호스트 — 사이드카 `vinc_mcp.install.CLIENTS` 와 짝. 여기 없는
  // 호스트도 서버는 그대로 동작한다(수동 등록 안내는 U.mcpOtherHint).
  const MCP_HOSTS = ["desktop", "code", "cursor", "codex"] as const;
  // 원격 MCP 엔드포인트(허브). `vinc_mcp/http_server.py` 의 `/mcp` 와 짝이고, 같은
  // 호스트가 OAuth 디스커버리(`/.well-known/*`)도 낸다 — 커넥터는 이 주소 하나만 받는다.
  // 셀프호스트 허브에서는 그 허브 자신의 주소다(`self-hosted.ts`).
  const REMOTE_MCP_URL = mcpUrl();
  // 이 기기에서 연결에 성공한 호스트만 기억한다(해제하면 지운다). 호스트 설정 파일을 읽지 않으므로
  // 다른 길로 등록한 것은 모른다. 기억한 호스트는 상태와 해제만, 나머지는 연결만 보인다.
  const MCP_KEY = "vinc.mcp.connected";
  const registeredHosts = new Set<string>(
    (() => { try { const v = JSON.parse(localStorage.getItem(MCP_KEY) || "[]"); return Array.isArray(v) ? v.filter((x) => typeof x === "string") : []; } catch { return []; } })());
  const keepHosts = () => { try { localStorage.setItem(MCP_KEY, JSON.stringify([...registeredHosts])); } catch { /* 기억만 못 한다 */ } };
  const busyHosts = new Set<string>();
  const hostResults = new Map<string, string>();
  let paintConnections = () => {};

  function renderApps(body: HTMLElement) {
    const alive = paneToken();
    // 호스트 하나 = 줄 하나(2026-08-05 재설계). `.seg` 는 **여럿 중 하나를 고르는** 컨트롤이라
    // 독립 동작 여덟 개를 담을 그릇이 아니다. 같은 호스트의 연결과 해제가 한 줄에 있다.
    const rows = MCP_HOSTS.map((h) => settingsRow({
      title: U.mcpHosts[h],
      extra: `<p id="mcp-result-${h}" class="set-row-desc break-all" role="status" aria-live="polite"></p>`,
      control: `
        <button class="btn btn-quiet" id="mcp-rm-${h}" type="button"
                aria-label="${esc(U.mcpRemove(U.mcpHosts[h]))}">${esc(U.mcpRemoveAct)}</button>
        <button class="btn" id="mcp-add-${h}" type="button" disabled
                aria-label="${esc(U.mcpConnect(U.mcpHosts[h]))}">${esc(U.mcpConnectAct)}</button>`,
    }));
    body.innerHTML = paneHead(U.navApps, COPY.settings.mcp)
      + settingsCard(rows, { foot: cardNote("", 'id="mcp-mode"') + cardNote(U.mcpOtherHint) });
    let keyReady = !accountMode();
    function paintHost(client: string) {
      const add = body.querySelector<HTMLButtonElement>(`#mcp-add-${client}`);
      const remove = body.querySelector<HTMLButtonElement>(`#mcp-rm-${client}`);
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
        if (!alive()) return;
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
      const host = U.mcpHosts[client] || client;
      const say = (text: string) => {
        hostResults.set(client, text);
        // 결과는 기억해 두고, 지금 화면이 데스크톱 앱 분류일 때만 그 줄에 쓴다(다시 돌아와 그린
        // 분류여도 같은 줄이다). 다른 분류나 닫힌 시트에는 쓰지 않는다.
        const line = active === "apps" && !sheet.closed ? body.querySelector<HTMLElement>(`#mcp-result-${client}`) : null;
        if (line) line.textContent = text;
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
        // `paintConnections` 는 가장 최근에 그린 데스크톱 앱 분류를 칠한다.
        if (active === "apps" && !sheet.closed) paintConnections();
      }
    }
    for (const h of MCP_HOSTS) {
      body.querySelector(`#mcp-add-${h}`)!.addEventListener("click", () => mcpAction("install", h));
      body.querySelector(`#mcp-rm-${h}`)!.addEventListener("click", () => mcpAction("uninstall", h));
    }
  }

  // 원격 호스트(claude.ai · ChatGPT). 데스크톱 앱 호스트는 앱 모드를 따르지만 여기는 계정
  // 그래프에 붙으므로 계정이 있어야 한다(루프백은 구조적으로 원격에 노출할 수 없다).
  // 근거: [[decision:the-desktop-signs-in-and-shows-the-account-graph-live-and-local-mode-is-a-setting]]
  function renderAssistants(body: HTMLElement) {
    const alive = paneToken();
    body.innerHTML = paneHead(U.mcpRemote, U.mcpRemoteHint) + settingsCard([
      settingsRow({ title: U.mcpRemoteHosts,
        extra: `<p class="set-row-desc break-all"><code>${esc(REMOTE_MCP_URL)}</code></p>
          <p class="set-row-desc" id="mcp-remote-state" role="status" aria-live="polite"></p>`,
        control: `
          <button class="btn btn-quiet" id="mcp-remote-copy" type="button">${esc(U.mcpRemoteCopyAct)}</button>
          <button class="btn" id="mcp-remote-acct" type="button" hidden>${esc(U.acctConnect)}</button>` }),
    ]);
    // 여기서 할 수 있는 것은 주소 복사와 계정 연결 둘뿐이다. 커넥터 등록은
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
      let copied = true;
      try { await navigator.clipboard.writeText(REMOTE_MCP_URL); } catch { copied = false; }
      if (alive()) remoteState.textContent = copied ? U.mcpRemoteCopied : U.mcpRemoteCopyFail;
    });
    remoteAcct.addEventListener("click", async () => {
      remoteState.textContent = U.acctConnecting;
      const r = await connectAccount();
      if (!alive()) return;
      if (r === "connected") paintRemote();
      else remoteState.textContent = r === "expired" ? U.acctExpired : U.acctFail;
    });
    paintRemote();
  }

  // ── 터미널에서 `vinc` 부르기 ────────────────────────────────────────────────
  // 리눅스 패키지(deb·rpm)는 이미 `/usr/bin/vinc` 를 깔아 두므로 이 줄이 할 일이
  // 없고, macOS·AppImage 는 설치 훅이 없어 여기서 링크를 건다. 이 플랫폼이 `supported:false`
  // 를 주면 빈 목록 대신 한 문장으로 그렇다고 말한다.
  function renderTerminal(body: HTMLElement) {
    body.innerHTML = paneHead(U.navTerminal, U.cliHint) + `<div id="cli-box">${settingsCard([
      settingsRow({ title: U.cli,
        extra: rowLine(U.cliChecking, 'id="cli-state" role="status" aria-live="polite"')
          + `<p class="set-row-desc break-all" id="cli-note"></p>`,
        control: `
          <button class="btn btn-quiet" id="cli-rm" type="button" hidden>${esc(U.cliRemoveAct)}</button>
          <button class="btn" id="cli-add" type="button" hidden>${esc(U.cliInstallAct)}</button>` }),
    ])}</div>`;
    const box = body.querySelector<HTMLElement>("#cli-box")!;
    const alive = paneToken();
    const cliState = body.querySelector<HTMLElement>("#cli-state")!;
    const cliNote = body.querySelector<HTMLElement>("#cli-note")!;
    const cliAdd = body.querySelector<HTMLButtonElement>("#cli-add")!;
    const cliRm = body.querySelector<HTMLButtonElement>("#cli-rm")!;
    type CliStatus = { supported: boolean; linked: boolean; path: string; dir: string; on_path: boolean };
    function paintCli(st: CliStatus | null, err?: string) {
      if (!alive()) return;
      if (err) { cliState.textContent = `${U.cliFail}: ${err}`; return; }
      if (!st || !st.supported) {
        // 못 누르는 버튼을 남기지 않는다. 대신 왜 할 일이 없는지 한 문장으로 말한다.
        box.innerHTML = `<p class="set-lone">${esc(st ? U.cliUnsupported : U.mcpNoShell)}</p>`;
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
      if (!invoke) { paintCli(null); return; }   // 웹 Vinc · 브라우저 dev — 셸이 없다
      try { paintCli(await invoke(cmd) as any); }
      catch (e: any) { paintCli(null, messageOf(e)); }
    }
    cliAdd.addEventListener("click", () => cliAction("cli_link_install"));
    cliRm.addEventListener("click", () => cliAction("cli_link_remove"));
    void cliAction("cli_link_status");
  }

  // ── 시스템 ────────────────────────────────────────────────────────────────
  function renderAdvanced(body: HTMLElement) {
    // 작업 공간(owner)은 로컬 모드에서만 뜻이 있다. 계정 모드에서는 허브가 키로 소유자를
    // 정하므로 바꿔도 아무 일도 안 일어나고, 힌트가 말하는 샘플도 계정 모드에는 없다.
    body.innerHTML = paneHead(U.navAdvanced, U.advancedIntro)
      + settingsCard([
        settingsRow({ title: U.engine, labelFor: "s-base",
          control: `<input id="s-base" class="set-input" value="${esc(cfg.base)}" />` }),
        settingsRow({ title: U.secret, desc: U.secretHint, labelFor: "s-token",
          control: `<input id="s-token" class="set-input" type="password" placeholder="••••••" autocomplete="off" />` }),
      ], { heading: U.advEngine })
      + settingsCard([
        settingsRow({ title: U.syncKey, desc: U.syncKeyHint, labelFor: "s-sync-key",
          extra: rowLine("", 'id="s-sync-key-said" role="status" hidden'),
          wide: `<p class="s-keyrow"><input id="s-sync-key" class="set-input" type="password" placeholder="vinc_sk_…"
            autocomplete="off" /><button class="btn" id="s-sync-key-save" type="button">${esc(U.syncKeySave)}</button></p>` }),
      ])
      + (accountMode() ? "" : settingsCard([
        settingsRow({ title: U.workspace, desc: U.workspaceHint, labelFor: "s-owner",
          control: `<input id="s-owner" class="set-input" value="${esc(cfg.owner)}" />` }),
      ]));
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

  /** 밖에서 분류를 옮긴다(이미 열린 시트에 `openSettings` 가 다시 왔을 때 · 저장 위치의 계정 버튼). */
  function go(cat: SettingsCat | LegacyCat, tab?: DataTab) {
    const next = resolveCat(cat, tab);
    pick(CATS.some((c) => c.id === next) ? next : "general");
  }
  shell();
  return go;
}
