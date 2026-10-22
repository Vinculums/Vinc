import { api, cfg } from "./api";
import { COPY } from "./copy";
import { partitionSearchResults } from "./search-relevance";
import { docTitle } from "./doc-title";
import { icon } from "./icons";
import { esc, armsNotice, type ArmsNotice } from "./components";
import { dayOf } from "./view-prefs";
import { renderSearchError } from "./search-error";

/** 상단 바 (DEC D56·D57) — 내용 칸 오른쪽 위에 뜬 손잡이 묶음.
 *
 *  두 줄이다: 위는 검색, 아래는 그래프 전환 + 보기 카드(관점 · 옵션 · 범례).
 *  검색은 평소 알약 하나로 접혀 있고, 누르거나 ⌘/Ctrl K 를 치면 길게 펴진다 — 문서를
 *  읽고 쓰는 면 위에 긴 입력창이 늘 떠 있을 이유가 없다.
 *  근거: [[decision:the-desktop-shell-is-documents-first]]
 *  여기 살지 않는 것: 로고(타이틀바가 든다 — 웹에선 공홈 GNB),
 *  표면 목록과 주제·문서 목록(좌측 트리가 가져갔다 — D69),
 *  크럼(폐지 — "지금 어디"는 트리가 상시로 말한다), 지표(하단 상태 표시줄에 남는다).
 *
 *  **표면과의 계약은 훅 하나뿐이다.** 공유·보기 버튼은 View 만 뜻이 있으므로
 *  View 가 마운트될 때 자기 동작을 등록하고(setViewHooks), 떠날 때 지운다.
 *  등록이 없으면 버튼은 숨는다 — 눌러도 아무 일 없는 컨트롤을 내놓지 않는다
 *  (보이는 손잡이는 반드시 무언가를 한다는 규율, VIEW_ROWS 와 같은 태도).
 *
 *  검색 드롭다운은 **노드 히트만** 싣는다: 청크(문서 조각) 히트는 View 에 착지할
 *  대상이 없어 "눌렀는데 아무 데도 안 가는 줄"이 된다. 문서 전체를 훑는 일은
 *  `모든 문서` 시트가 맡는다(표면 이동 아님 — 닫으면 하던 자리로 돌아온다). */

type ViewHooks = {
  /** 지금 스코프를 팩으로 묶는 시트를 연다(범위는 View 가 정한다). */
  share: () => void;
  /** 그 노드로 이동한다(D70) — 화면에 있으면 카메라만, 없으면 스코프를 넓혀
   *  데려온다. 검색 착지가 이 훅을 쓰면 표면 리마운트 없이 날아간다.
   *  `domain` 을 아는 자리(검색 히트·트리 행)는 함께 준다: 알면 그 주제 하나로,
   *  모르면 전량으로 넓힌다. */
  openNode: (id: string, domain?: string) => void;
  /** 문서를 연다 — **지도 착지와 본문 열기를 한 클릭에** 한다. 여태 문서 행을
   *  눌러도 문서가 안 열리고 노드로만 날아갔다(D70 은 지도에 대해 옳았고, 본문을
   *  가져갔다). `nodeId` 가 없는 문서(개념 언급 0)는 지도에 자리가 없으므로 본문만
   *  연다. `chunkIndex` 는 검색의 청크 히트가 그 대목에 앉는 자리다. */
  openDoc: (docId: string, nodeId?: string, domain?: string, chunkIndex?: number) => void;
  // ── 작업하는 자리에서 만들기(authoring.ts). View 만 뜻이 있어서 여기 싣는다. ──
  /** 오른쪽 문서 열에 새 문서를 연다. 주제 칩은 `topic` 으로 채운다. */
  newDoc?: (topic?: string) => void;
  /** 방금 만든(또는 고친) 노드를 지도에 세우고 그 자리로 간다. 열려 있는 문서 열은 닫지 않는다.
   *  `label` 을 주면, 지도에 자리가 없는 노드(연결 없는 기록·결정은 전체 지도에 안 그려진다)를
   *  노드 상세로 대신 연다 — 만들었는데 아무 데도 안 보이는 일을 만들지 않는다. */
  reveal?: (id: string, domain?: string, label?: string) => Promise<void>;
  /** 만든 노드를 문서 열의 노드 상세로 연다(컴포저의 Ctrl/Cmd+Enter). */
  openNodePane?: (id: string, domain?: string, label?: string) => Promise<void>;
  /** 지금 보고 있는 자리 — 새로 만드는 것의 주제를 채우는 데 쓴다. */
  context?: () => { topic: string; nodeId: string };
  /** 잇기(흐름 F): 지도에서 다음에 고른 노드를 `cb` 로 넘긴다. null 이면 푼다. */
  pickNode?: (cb: ((id: string, title: string) => void) | null) => void;
};

let hooks: ViewHooks | null = null;
let wired = false;

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T | null;

/** View 가 마운트/언마운트하며 자기 동작을 넘긴다. null 이면 버튼이 숨는다. */
export function setViewHooks(h: ViewHooks | null) {
  hooks = h;
  refreshTopbar();
}

/** 지금 View 가 살아 있는가(+그 동작). 다른 표면·시트가 "지도로 보낼 수 있는가"를
 *  판단하는 단일 지점이다 — 없으면 해시 착지로 강등한다. */
export function viewHooks(): ViewHooks | null {
  return hooks;
}

/** 지도 손잡이 표시 상태를 현재 훅 기준으로 다시 칠한다(관점을 바꾼 뒤 호출).
 *  Tools 는 훅과 무관하게 늘 눌린다 — 문서를 쓰고 가져오는 일은 지도가 없어도 된다. */
export function refreshTopbar() {
  // 지도 손잡이는 View 소유다 — 훅이 없으면 슬롯을 비운다. 남겨 두면 검색·팩
  // 화면에서 눌리는데 아무 지도도 안 바뀌는 칩이 된다.
  // 열린 보기 카드도 함께 접는다 — 패널은 표면과 함께 사라졌는데 카드만 펼친 폭으로
  // 남으면, 돌아왔을 때 닫힌 패널 위에 관점 세그가 떠 있다.
  if (!hooks) {
    const l = lensSlot(), v = viewpointsSlot();
    if (l) l.innerHTML = "";
    if (v) v.innerHTML = "";
    $("tb-viewctl")?.removeAttribute("data-open");
  }
}

/** 렌즈 버튼 자리(D71) — 하단 바에서 상단 바 좌측으로 올라왔다. */
export function lensSlot(): HTMLElement | null { return $("tb-lens"); }

/** 관점 세그 자리(D71). 내용은 View 가 채운다(주인은 표면이다). */
export function viewpointsSlot(): HTMLElement | null { return $("tb-viewpoints"); }

/** 검색바 모드(D73). 바의 `data-mode` 가 화면·동작의 단일 원천이다.
 *    find = 기존 동작(드롭다운 결과 → 착지)
 *    ask  = 무LLM 대화(질문 우측 · 답변 좌측 · 이력 보존, ask.ts)
 *  아이콘은 **기존 것을 그대로** 쓴다: 찾기=search, 묻기=decide(마주 보는 두 표식).
 *  sparkle 은 쓰지 않는다 — AI 를 함의해 "생성하지 않는다"는 이 표면의 약속과
 *  정면으로 어긋난다([[vinc.research.no-llm-chat]] §5a.6). */
type Mode = "find" | "ask";
const MODE_KEY = "vinc.search.mode";
const MODE_ICON: Record<Mode, string> = { find: "search", ask: "decide" };

function storedMode(): Mode {
  return localStorage.getItem(MODE_KEY) === "ask" ? "ask" : "find";
}

/** 바 아래 크롬의 천장(`--chrome-top`)을 **바의 실측 바닥**에서 뗀다.
 *
 *  CSS 기본값은 "바 한 줄 높이 + 여백 두 칸"이다. 좁은 창(≤720, 예전엔 ≤520)에서는 바가 두 줄이 돼
 *  그 값이 모자라다 — 그 천장을 쓰는 문서 열 머리(크기 전환 · ✕)가 바 둘째 줄 뒤에
 *  가려졌다(실측 2026-09-26, 창 390: 바 16~114, 문서 열 천장 76). 두 줄 높이를 상수로
 *  다시 적으면 바의 구성이 바뀌는 날 조용히 어긋나므로 바의 크기를 지켜보고 잰다.
 *  값의 뜻은 CSS 와 같다: 지도 칸 윗변에서 바 바닥까지 + 바 아래 여백.
 *  근거: [[concept:app-narrow-window-contract]] */
function trackChromeTop() {
  const bar = $("topbar");
  const shell = $("shell");
  if (!bar || !shell || typeof ResizeObserver === "undefined") return;
  const apply = () => {
    const b = bar.getBoundingClientRect();
    if (!b.height) return;                 // 숨은 바(임베드)는 CSS 값 그대로
    const s = shell.getBoundingClientRect();
    const pad = parseFloat(getComputedStyle(shell).paddingTop) || 0;
    const gap = parseFloat(getComputedStyle(bar).marginBottom) || 0;
    shell.style.setProperty("--chrome-top", `${Math.round(b.bottom - s.top - pad + gap)}px`);
  };
  new ResizeObserver(apply).observe(bar);
  apply();
}

/** 그래프 전환 버튼을 지금 표면에 맞춰 칠한다. 그래프(`#view…`)에 있으면 눌림이고
 *  누르면 문서 홈으로, 아니면 그래프로 간다. 상태의 정본은 주소다(따로 저장하지 않는다). */
function paintGraphToggle() {
  const b = $("tb-graph");
  if (!b) return;
  const on = /^#view(\/|$)/.test(location.hash);
  const tip = on ? COPY.ui.topbar.graphOffTip : COPY.ui.topbar.graphTip;
  b.setAttribute("aria-pressed", String(on));
  b.setAttribute("aria-label", tip);
  b.setAttribute("data-tip", tip);
  b.classList.toggle("on", on);
}

export function mountTopbar() {
  if (wired) return;
  wired = true;
  trackChromeTop();
  const graphBtn = $("tb-graph");
  if (graphBtn) {
    graphBtn.innerHTML = `${icon("graph", 16)}<span class="tb-graph-name">${COPY.ui.topbar.graph}</span>`;
    graphBtn.addEventListener("click", () => {
      location.hash = /^#view(\/|$)/.test(location.hash) ? "#home" : "#view";
    });
    paintGraphToggle();
    window.addEventListener("hashchange", paintGraphToggle);
  }
  const qEl = $<HTMLInputElement>("tb-q");
  const boxEl = $("tb-results");
  const sicon = document.querySelector<HTMLElement>(".tb-sicon");
  const kbd = $("tb-kbd");
  if (!qEl || !boxEl) return;
  const q = qEl, box = boxEl;
  const bar = $("tb-search");
  const modeBtn = $<HTMLButtonElement>("tb-mode");
  const modeLabel = $("tb-mode-label");
  const modeMenu = $("tb-modemenu");
  const caret = document.querySelector<HTMLElement>(".tb-caret");
  const chatEl = $("tb-chat");
  const findCard = $("tb-find");
  const findOpen = $("tb-find-open");

  // ── 접힌 알약 ↔ 펼친 검색 ────────────────────────────────────────────────
  // 펼침은 카드의 `data-expanded` 하나가 말한다(CSS 가 그 하나만 본다). 접는 것은 초점이
  // 카드 밖으로 나가고 대화 패널이 닫혀 있을 때다. 친 글자는 남는다 — 다시 펴면 그대로다.
  const expand = (focus = true) => {
    findCard?.setAttribute("data-expanded", "true");
    findOpen?.setAttribute("aria-expanded", "true");
    if (focus) { qEl.focus(); qEl.select(); }
  };
  const collapse = () => {
    if (chatEl && !chatEl.hidden) return;          // 묻기 답을 읽는 중이다
    findCard?.removeAttribute("data-expanded");
    findOpen?.setAttribute("aria-expanded", "false");
  };
  if (findOpen) {
    const fi = findOpen.querySelector<HTMLElement>(".tb-find-icon");
    if (fi) fi.innerHTML = icon("search", 15);
    const fl = $("tb-find-label");
    if (fl) fl.textContent = COPY.ui.topbar.findOpen;
    const fk = $("tb-find-kbd");
    if (fk) fk.textContent = /mac/i.test(navigator.platform) ? "⌘K" : "Ctrl K";
    findOpen.setAttribute("aria-label", COPY.ui.topbar.searchPh);
    findOpen.setAttribute("aria-expanded", "false");
    findOpen.addEventListener("click", () => expand());
  }
  findCard?.addEventListener("focusout", () => window.setTimeout(() => {
    if (!findCard.contains(document.activeElement)) collapse();
  }, 140));

  q.setAttribute("aria-label", COPY.ui.topbar.searchPh);
  if (caret) caret.innerHTML = icon("chevron-left", 12);
  // 표기는 플랫폼 관용구를 따른다(mac ⌘K · 그 외 Ctrl K) — 팔레트 단축키와 같은 키.
  if (kbd) kbd.textContent = /mac/i.test(navigator.platform) ? "⌘K" : "Ctrl K";

  let mode: Mode = storedMode();

  /** 모드를 화면에 반영한다. 아이콘·라벨·플레이스홀더·열려 있던 패널까지 한 자리에서
   *  처리해야 모드와 화면이 어긋나지 않는다(반쪽 전환이 가장 흔한 사고다). */
  function applyMode(m: Mode, persist = true) {
    mode = m;
    if (persist) localStorage.setItem(MODE_KEY, m);
    bar?.setAttribute("data-mode", m);
    if (sicon) sicon.innerHTML = icon(MODE_ICON[m], 15);
    if (modeLabel)
      modeLabel.textContent = m === "ask" ? COPY.ui.topbar.modeAsk : COPY.ui.topbar.modeFind;
    modeBtn?.setAttribute("aria-label",
      `${COPY.ui.topbar.modeLabel}: ${modeLabel?.textContent || ""}`);
    q.placeholder = m === "ask" ? COPY.ui.topbar.askPh : COPY.ui.topbar.searchPh;
    // 모드를 바꾸면 다른 모드의 패널은 반드시 닫는다 — 둘이 같은 앵커에 겹쳐 뜬다.
    close();
    if (m === "find") void closeChat();
    else if (chatEl) void openChatIfAny();
  }

  // ── 모드 메뉴 ────────────────────────────────────────────────────────────
  function drawModeMenu() {
    if (!modeMenu) return;
    const rows: { m: Mode; name: string; hint: string }[] = [
      { m: "find", name: COPY.ui.topbar.modeFind, hint: COPY.ui.topbar.modeFindHint },
      { m: "ask", name: COPY.ui.topbar.modeAsk, hint: COPY.ui.topbar.modeAskHint },
    ];
    modeMenu.innerHTML = rows.map((r) => `
      <button class="tb-mrow" role="menuitemradio" data-m="${r.m}"
              aria-checked="${r.m === mode}">
        ${icon(MODE_ICON[r.m], 15)}
        <span><span class="tb-mname">${r.name}</span>
          <span class="tb-mhint">${r.hint}</span></span>
        <span class="tb-mtick">${r.m === mode ? "✓" : ""}</span>
      </button>`).join("");
    modeMenu.querySelectorAll<HTMLElement>("[data-m]").forEach((b) =>
      b.addEventListener("click", () => {
        applyMode(b.dataset.m as Mode);
        closeModeMenu();
        q.focus();
      }));
  }
  const closeModeMenu = () => {
    if (modeMenu) modeMenu.hidden = true;
    modeBtn?.setAttribute("aria-expanded", "false");
  };
  modeBtn?.addEventListener("click", (e) => {
    e.preventDefault();
    if (!modeMenu) return;
    const willOpen = modeMenu.hidden;
    close();                              // 결과 드롭다운과 겹치지 않게
    drawModeMenu();
    modeMenu.hidden = !willOpen;
    modeBtn.setAttribute("aria-expanded", String(willOpen));
  });
  // 바깥 클릭으로 닫는다(메뉴 자신과 트리거 위 클릭은 제외).
  document.addEventListener("mousedown", (e) => {
    if (modeMenu?.hidden) return;
    const t = e.target as Node;
    if (modeMenu?.contains(t) || modeBtn?.contains(t)) return;
    closeModeMenu();
  });

  // ── 대화 패널(묻기) ──────────────────────────────────────────────────────
  // ask.ts 는 **눌릴 때 지연 로드**한다 — 찾기만 쓰는 사용자에게 대화 코드를
  // 초기 번들로 지우지 않는다(관례 시트·Tools 와 같은 태도).
  let askMod: typeof import("./ask") | null = null;
  async function ask() {
    if (!askMod && chatEl) {
      askMod = await import("./ask");
      askMod.initAsk(chatEl, () => { chatEl.hidden = true; });
    }
    return askMod;
  }
  async function openChatIfAny() {
    const a = await ask();
    if (a?.askHasTurns()) a.askOpen();     // 이력이 있으면 이어서 보인다
  }
  async function closeChat() { (await ask())?.askClose(); }

  // Tools — 표면과 무관하게 언제나 눌린다(View 훅에 매이지 않는다). 공유는 이 안에서
  // 범위를 고르고, `지금 보고 있는 곳`만 View 훅을 쓴다.
  const tools = $("tb-tools");
  if (tools) {
    // 아이콘만 — 옆의 설정·GRAPH.md 와 같은 형태여야 세 손잡이가 한 리듬으로 읽힌다
    // (발주자 지시 2026-08-05). 이름은 툴팁과 보조기술이 든다.
    tools.innerHTML = icon("pack-in", 18);
    tools.setAttribute("aria-label", COPY.ui.tools.title);
    tools.setAttribute("data-tip", COPY.ui.tools.tip);
    tools.addEventListener("click", async () => {
      const { openTools } = await import("./tools");
      openTools();
    });
  }

  type Hit = { id: string; title: string; domain: string; mark: string; snippet: string;
               docId?: string; temporal?: string };
  let hits: Hit[] = [];
  let weakHits: Hit[] = [];
  /** 마지막 질의에서 벡터 팔이 빠졌는가. 정상이면 null 이라 드롭다운은 그대로다. */
  let notice: ArmsNotice | null = null;
  let timer: number | undefined;
  let seq = 0;                 // 늦게 온 응답이 새 질의를 덮지 않게

  const close = () => {
    box.hidden = true;
    q.setAttribute("aria-expanded", "false");
  };
  const open = () => {
    box.hidden = false;
    q.setAttribute("aria-expanded", "true");
  };

  // 초기 모드 반영 — `close` 를 쓰므로 그 선언 **뒤에** 있어야 한다(TDZ).
  applyMode(mode, false);

  function draw(loading = false) {
    const head = `<div class="tb-rhead"><span class="hint">${COPY.ui.topbar.results}</span>
      <span class="hint">${COPY.ui.topbar.enterHint}</span></div>`
      // 벡터 팔이 빠졌으면 결과 위에 그 사실을 적는다. 특히 "결과 없음"과 겹칠 때
      // 필요하다 — 반쪽 검색의 0건을 진짜 0건으로 읽게 두면 안 된다.
      + (notice && !loading ? `<p class="hint tb-rnote">${notice.text}</p>` : "");
    if (loading) {
      box.innerHTML = head + `<p class="hint tb-rnote">${COPY.search.searching}</p>`;
      open(); return;
    }
    if (hits.length === 0 && weakHits.length === 0) {
      box.innerHTML = head
        + `<p class="hint tb-rnote">${COPY.ui.topbar.noResults}</p>`
        // 첫 질의는 색인을 만드느라 몇 초 걸린다 — 침묵 대신 이유를 말한다(V14).
        + `<p class="hint tb-rnote">${COPY.ui.topbar.firstSlow}</p>`
        + foot();
      wireAll(); open(); return;
    }
    const hitRow = (h: Hit, i: number, weak = false) => `
      <button class="tb-row" role="option" data-i="${i}"${weak ? ' data-weak="true"' : ""}>
        <span class="tb-mark">${h.mark}</span>
        <span class="tb-rmain">
          <span class="tb-rtitle">${esc(h.title)}</span>
          ${h.snippet ? `<span class="hint tb-rsnip">${esc(h.snippet)}</span>` : ""}
          ${h.temporal ? `<span class="hint">${esc(h.temporal)}</span>` : ""}
        </span>
        <span class="hint tb-rmeta">${esc(h.domain)}</span>
      </button>`;
    box.innerHTML = head + (hits.length ? hits.map((h, i) => hitRow(h, i)).join("") :
      `<p class="hint tb-rnote">${COPY.ui.topbar.noResults}</p>`) +
      (weakHits.length ? `<details class="search-weak"><summary>${COPY.search.weakMatches(weakHits.length)}</summary>
        <p class="hint tb-rnote">${COPY.search.weakHint}</p>${weakHits.map((h, i) => hitRow(h, i, true)).join("")}</details>` : "") + foot();
    box.querySelectorAll<HTMLElement>(".tb-row").forEach((r) => {
      r.addEventListener("mousedown", (e) => e.preventDefault());
      r.addEventListener("click", () =>
        go((r.dataset.weak ? weakHits : hits)[Number(r.dataset.i)]));
    });
    wireAll();
    open();
  }

  /** 전체 문서 목록 — **시트로 연다**(표면 이동 아님).
   *
   *  검색 중에 목록을 보려고 화면을 통째로 갈아타면 보고 있던 지도와 스코프를
   *  잃는다(돌아오려면 크럼을 다시 밟아야 한다). 목록은 "지금 하던 일을 잠깐 멈추고
   *  훑는 것"이라 시트가 맞다 — 닫으면 있던 자리로 그대로 돌아온다.
   *
   *  두 플랫폼 공통 경로다: 웹앱은 별도 OS 창을 못 열고, 데스크탑도 지금 셸은
   *  단일 창(tauri.conf `windows:[main]`, capability `windows:["main"]`)이라
   *  창 분리는 권한 추가가 선행돼야 한다 — 그건 데스크탑 릴리스에 실리는 별건. */
  async function openAllDocs() {
    close(); q.blur();
    const [{ openSheet }, { renderBrowse }] = await Promise.all([
      import("./components"), import("./surfaces/records"),
    ]);
    const sheet = openSheet({ title: COPY.ui.topbar.browseAll, size: "browse" });
    // 문서를 눌러 지도로 날아가면 목록은 스스로 닫힌다(D70) — 방금 간 곳을 자기가
    // 가리고 서 있지 않게. 편집기로 여는 경우(착지 노드 없음)는 열린 채로 둔다.
    void renderBrowse(sheet.body, { close: sheet.close });
  }

  /** 드롭다운 아래 두 길: 전체 문서 목록(시트)과 Search 화면(문서 본문 검색).
   *  드롭다운은 노드만 보여 주므로, 마우스만 쓰는 사람에게 본문 검색으로 가는 길은
   *  여기 하나다(VIN-257). 친 검색어를 주소에 실어 그 화면이 바로 찾게 한다. */
  function foot(): string {
    return `<div class="tb-foot"><button class="tb-all" id="tb-all">${COPY.ui.topbar.browseAll}</button>`
      + `<button class="tb-all" id="tb-inside">${COPY.ui.topbar.searchInside}</button></div>`;
  }

  function wireAll() {
    box.querySelectorAll(".search-weak summary").forEach((summary) =>
      summary.addEventListener("mousedown", (e) => e.preventDefault()));
    box.querySelector("#tb-all")?.addEventListener("mousedown", (e) => {
      e.preventDefault();
      void openAllDocs();
    });
    box.querySelector("#tb-inside")?.addEventListener("mousedown", (e) => {
      e.preventDefault();
      const raw = q.value.trim();
      close(); q.blur();
      location.hash = raw ? `#search/${encodeURIComponent(raw)}` : "#search";
    });
  }

  /** 결과 착지 — View 가 이미 떠 있으면 훅으로 **그 노드까지** 날아가고(리마운트
   *  없음, 카드·문서 패널까지 열린다), 다른 표면이면 해시로 View 를 띄운다. */
  function go(h: Hit | undefined) {
    if (!h) return;
    close();
    q.blur();
    if (h.docId) {
      if (hooks) hooks.openDoc(h.docId);
      else void import("./doc-editor").then((m) => m.openDocSheet({ owner: cfg.owner, docId: h.docId }));
      return;
    }
    if (hooks) hooks.openNode(h.id);
    else location.hash = `#view/trace/${encodeURIComponent(h.id)}`;
  }

  async function run(raw: string): Promise<boolean> {
    const mine = ++seq;
    draw(true);
    try {
      const [res, docs] = await Promise.all([
        api.search(raw), api.docTitleMatches(raw).catch(() => null),
      ]);
      if (mine !== seq) return false;            // 지나간 질의 — 버린다
      notice = armsNotice(res);
      // 드롭다운은 훑는 자리라 시간 정보는 **수정한 날 하나**만 싣는다. 유효구간·대체 관계까지
      // 펼친 전체 줄은 Search 화면의 몫이다(searchTemporalText) — 여기 그대로 두면 결과 한 줄이
      // 네 줄로 불어나 제목이 묻혔다.
      const dates = (row: any): string => {
        const day = dayOf(String(row?.updated_at || row?.temporal?.updated_at || ""));
        return day ? COPY.ui.home.updated(day) : "";
      };
      const exact: Hit[] = (docs?.docs || []).map((d) => ({
        temporal: dates(d), id: d.doc_id, docId: d.doc_id, title: docTitle(d.title), domain: "", mark: "≡", snippet: "",
      }));
      const classified = partitionSearchResults<any>(res.node_hits || []);
      const toHit = (n: any): Hit => ({
        temporal: dates(n),
        id: String(n.id || ""),
        title: String(n.title || n.id || ""),
        domain: String(n.domain || ""),
        // 의미 일치와 이전 응답의 graph 표시는 ≈, 제목·속성 일치는 ≡.
        mark: ["graph", "semantic"].includes(n.matched_in) ? "≈" : "≡",
        snippet: String(n.one_liner || n.snippet || "").slice(0, 90),
      });
      hits = [...exact, ...classified.primary.map(toHit).filter((h: Hit) => h.id)].slice(0, 8);
      weakHits = classified.weak.map(toHit).filter((h: Hit) => h.id).slice(0, 8);
      draw();
      return true;
    } catch (failure) {
      if (mine !== seq) return false;
      hits = [];
      weakHits = [];
      notice = null;              // 아예 못 물어봤다 — 팔 이야기를 할 자리가 아니다
      // 실패해도 막다른 골목을 만들지 않는다 — 목록으로 가는 길은 남긴다
      // (검색이 죽었다고 문서에 닿을 방법까지 사라지면 안 된다).
      renderSearchError(box, failure, () => void run(raw), true);
      box.insertAdjacentHTML("beforeend", foot());
      wireAll();
      open();
      return false;
    }
  }

  q.addEventListener("input", () => {
    ++seq;                     // debounce 중에도 지난 검색의 성공·오류를 버린다
    window.clearTimeout(timer);
    // 묻기는 **타자마다 묻지 않는다** — 한 턴은 사용자가 Enter 로 끝맺는 단위다
    // (라이브 검색을 그대로 두면 미완성 문장이 대화 로그에 쌓인다).
    if (mode === "ask") return;
    const raw = q.value.trim();
    if (raw.length < 2) { hits = []; weakHits = []; close(); return; }
    timer = window.setTimeout(() => void run(raw), 250);
  });
  q.addEventListener("focus", () => { if (mode === "find" && (hits.length || weakHits.length)) open(); });
  // 묻기 패널은 blur 로 닫지 않는다 — 답을 읽으려면 입력창에서 손을 떼야 하는데,
  // 그때 사라지면 읽을 수가 없다. 닫기는 명시적으로만(패널의 `닫기` · Esc).
  q.addEventListener("blur", () => window.setTimeout(() => {
    if (mode === "find" && !box.contains(document.activeElement)) close();
  }, 120));
  q.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      close(); closeModeMenu();
      if (mode === "ask") void closeChat();
      q.blur(); collapse(); return;
    }
    if (e.key !== "Enter") return;
    e.preventDefault();
    const raw = q.value.trim();
    if (mode === "ask") {
      if (!raw) return;
      q.value = "";                       // 보낸 말은 입력창을 떠난다(대화 관용구)
      void (async () => { (await ask())?.askSubmit(raw); })();
      return;
    }
    // Enter 는 질의를 **끝맺는다** — debounce 가 돌기 전에 누르면 hits 가 비어 있어
    // 곧바로 전체 목록 시트가 뜨는 사고가 있었다(2026-09-15). 타이머를 먼저 끊고
    // run 을 기다린 뒤에 착지/목록을 고른다.
    void (async () => {
      window.clearTimeout(timer);
      timer = undefined;
      if (raw.length >= 2) {
        if (!await run(raw)) return;
        if (hits.length) { go(hits[0]); return; }
        if (weakHits.length) return; // 약한 후보는 사용자가 펼쳐서 고른다. Enter로 자동 착지하지 않는다.
      }
      // 두 글자가 안 되면 debounce 가 돈 적이 없어 `hits` 는 **이전 질의의 것**이다.
      // 그것으로 착지하면 친 글자와 다른 곳에 떨어지므로 쓰지 않는다. 그렇다고 아무
      // 일도 안 하면 막다른 골목이라(이 자리의 원래 규칙) 전체 목록을 연다.
      if (raw) void openAllDocs();
    })();
  });

  // Ctrl/Cmd+K 는 팔레트가 이미 쓰는 키다 — 여기서는 **검색 필드 포커스**로 통일한다
  // (같은 키에 두 동작이 붙으면 어느 쪽이 뜰지 사용자가 예측할 수 없다). 팔레트는
  // main.ts 에서 이 핸들러가 먼저 잡도록 순서를 맞춘다.
  document.addEventListener("keydown", (e) => {
    if (!(e.key === "k" && (e.metaKey || e.ctrlKey))) return;
    e.preventDefault();
    expand();
  }, true);
}
