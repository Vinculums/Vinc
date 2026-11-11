import { api, cfg } from "./api";
import { COPY } from "./copy";
import { partitionSearchResults } from "./search-relevance";
import { docTitle } from "./doc-title";
import { icon } from "./icons";
import { esc, armsNotice, type ArmsNotice } from "./components";
import { dayOf } from "./view-prefs";
import { renderSearchError } from "./search-error";
import { loaderInline } from "./vinc-loaders.js";
import { documentsHome } from "./lnb";
import { mountDocOptions } from "./doc-options";

/** 뷰 머리 (DEC D56·D57) — 내용 카드의 윗줄. 떠 있지 않다.
 *
 *  맨 앞(`#tb-space`)은 계정 그래프의 공간 전환이다(space-switcher.ts 가 채운다). 앞(`#tb-lead`)은
 *  지금 자리다: 문서 면은 경로를 표면이 채우고(`setTopbarLead`), 그래프는 범위를 여기서 칠한다.
 *  뒤는 검색, 그래프의 시간 · 범례(`#tb-graph-tools`, `setTopbarTools`), 문서 | 그래프 전환, 맨 끝에
 *  문서 면의 읽기 설정(doc-options.ts). 검색은 평소 칸 하나로 접혀 있고 누르거나 ⌘/Ctrl K 를 치면
 *  왼쪽으로 길게 펴진다.
 *  근거: [[decision:the-graph-is-one-2d-compass-lens-2026-11-06]]
 *  근거: [[decision:documents-and-graph-share-one-lnb-tree-2026-11-06]]
 *  여기 살지 않는 것: 로고(타이틀바가 든다 — 웹에선 공홈 GNB),
 *  표면 목록과 주제·문서 목록(좌측 트리가 가져갔다 — D69), 지표(하단 상태 표시줄에 남는다).
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
  // 지도 손잡이는 View 소유다 — 훅이 없으면 시간 · 범례 자리를 비운다. 남겨 두면 다른
  // 화면에서 눌리는데 아무 지도도 안 바뀌는 단추가 된다.
  if (!hooks) setTopbarTools(null);
}

/** 표면이 거는 검색 거르개. 걸려 있으면 검색 칸에 치는 글자가 드롭다운 대신 이리로 간다(그래프의 나침반).
 *  Enter 는 그대로 전문 검색(드롭다운). 표면을 떠날 때 null 로 푼다. */
let searchFilter: ((q: string) => void) | null = null;
export function setSearchFilter(fn: ((q: string) => void) | null) {
  searchFilter = fn;
  const q = document.getElementById("tb-q") as HTMLInputElement | null;
  if (fn && q?.value.trim()) fn(q.value.trim());
}


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

/** 본문 스크롤바 폭을 머리 오른쪽에 같이 비운다(`--sb-w`). 머리와 본문이 한 칼럼을 나눠
 *  쓰는데 본문만 스크롤바만큼 좁아지면, 가운데 정렬된 두 칼럼이 그 절반만큼 어긋난다
 *  (경로와 제목, 검색과 읽기 칸의 세로선이 갈라진다). 스크롤바는 문서 길이에 따라 생기고
 *  사라지므로 표면의 안쪽 폭을 지켜보고 잰다. */
function trackGutter() {
  const surface = $("surface");
  const shell = $("shell");
  if (!surface || !shell || typeof ResizeObserver === "undefined") return;
  const apply = () => {
    const cs = getComputedStyle(surface);
    const borders = (parseFloat(cs.borderLeftWidth) || 0) + (parseFloat(cs.borderRightWidth) || 0);
    const sb = Math.max(0, surface.offsetWidth - surface.clientWidth - borders);
    shell.style.setProperty("--sb-w", `${sb}px`);
  };
  new ResizeObserver(apply).observe(surface);
  apply();
}

const onGraph = () => /^#view(\/|$)/.test(location.hash);
const onDocs = () => /^#home(\/|$)/.test(location.hash) || location.hash === "" || location.hash === "#";

/** 문서 | 그래프 전환을 지금 주소에 맞춰 칠한다. 상태의 정본은 주소다(따로 저장하지
 *  않는다). 둘 다 아닌 표면(검색 · 설정 …)에서는 어느 쪽도 눌려 있지 않다. */
function paintSwitch() {
  const docs = $("tb-to-docs"), graph = $("tb-to-graph");
  if (!docs || !graph) return;
  docs.setAttribute("aria-pressed", String(onDocs()));
  graph.setAttribute("aria-pressed", String(onGraph()));
}

/** 머리 앞자리. 표면이 자기 자리(경로)를 준다 — 문자열은 이미 이스케이프된 마크업이다.
 *  null 이면 비운다. 표면이 바뀌면 topbar 가 먼저 비우고, 새 표면이 다시 채운다. */
export function setTopbarLead(html: string | null): HTMLElement | null {
  const lead = $("tb-lead");
  if (!lead) return null;
  lead.innerHTML = html ?? "";
  return lead;
}

/** 그래프의 뒤 묶음 단추(나침반의 시간 · 범례). 전환 바로 왼쪽 자리(`#tb-graph-tools`)에 요소를 옮겨
 *  붙이므로 배선이 남는다. null 이면 비운다(나침반이 떠날 때 · View 의 훅이 풀릴 때). */
let graphTools: HTMLElement | null = null;
export function setTopbarTools(el: HTMLElement | null) {
  if (graphTools && graphTools !== el) graphTools.remove();
  graphTools = el;
  const slot = $("tb-graph-tools");
  if (slot && el && el.parentElement !== slot) slot.append(el);
}

/** 그래프의 앞자리 — 지금 범위(LNB 가 고른 주제). 고른 것이 없으면 "그래프" 한 낱말. */
let graphScope = "";
function paintGraphLead() {
  if (!onGraph()) return;
  const segs = graphScope.split("/").filter(Boolean);
  setTopbarLead(`<span class="tb-lead-name">${esc(COPY.ui.topbar.graph)}</span>${
    segs.length ? `<span class="tb-lead-sep" aria-hidden="true">·</span><span class="tb-lead-scope">${
      esc(segs.join(" / "))}</span>` : ""}`);
}

export function mountTopbar() {
  if (wired) return;
  wired = true;
  trackGutter();
  mountDocOptions();
  const T = COPY.ui.topbar;
  const docsBtn = $("tb-to-docs"), graphBtn = $("tb-to-graph");
  if (docsBtn && graphBtn) {
    docsBtn.innerHTML = `${icon("doc", 16)}<span class="tb-seg-name">${esc(T.docs)}</span>`;
    graphBtn.innerHTML = `${icon("graph", 16)}<span class="tb-seg-name">${esc(T.graph)}</span>`;
    docsBtn.setAttribute("data-tip", T.graphOffTip);
    graphBtn.setAttribute("data-tip", T.graphTip);
    docsBtn.setAttribute("aria-label", T.docs);
    graphBtn.setAttribute("aria-label", T.graph);
    $("tb-switch")?.setAttribute("aria-label", T.switchLabel);
    // 문서 쪽 첫 자리는 맨 위 줄이 정한다: Agent 면 Agents 보드, 아니면 문서 홈(lnb `documentsHome`).
    docsBtn.addEventListener("click", () => { if (!onDocs()) location.hash = documentsHome(); });
    graphBtn.addEventListener("click", () => { if (!onGraph()) location.hash = "#view"; });
    paintSwitch();
  }
  // 표면이 바뀌면 앞자리를 먼저 비운다 — 새 표면이 다시 채운다(문서 면은 home.ts, 그래프는 여기).
  // 이 듣기가 라우터보다 먼저 걸리므로 비우기가 채우기보다 앞선다.
  let lastSurface = location.hash.split("/")[0];
  window.addEventListener("hashchange", () => {
    paintSwitch();
    const s = location.hash.split("/")[0];
    if (s !== lastSurface) { lastSurface = s; setTopbarLead(null); }
    paintGraphLead();
  });
  addEventListener("vinc:lnb-active", (e) => {
    graphScope = String((e as CustomEvent<{ domain?: string }>).detail?.domain || "");
    paintGraphLead();
  });
  paintGraphLead();
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
  // 검색 칸은 LNB 맨 위에 산다(문서·그래프 같은 자리). 머리에는 제목과 문서 | 그래프 전환만 남는다.
  // 칸은 늘 펼쳐져 있다. 결과 드롭다운은 칸 아래로, 묻기 답은 LNB 오른쪽으로 뜬다(app.css #lnb-search).
  const lnbTop = $("lnb-top");
  if (findCard && lnbTop) {
    let slot = $("lnb-search");
    if (!slot) { slot = document.createElement("div"); slot.id = "lnb-search"; slot.className = "lnb-search"; lnbTop.after(slot); }
    slot.appendChild(findCard);
    findCard.setAttribute("data-expanded", "true");
  }
  const docked = !!findCard?.closest("#lnb-search");
  // LNB 는 넘치는 것을 자른다. 그래서 결과와 묻기 답은 화면에 고정해 LNB 오른쪽에 띄운다: 그 자리(위·왼쪽)를 CSS 변수로 준다
  const placeFloats = () => { if (!docked) return; const lnb = $("lnb")?.getBoundingClientRect(), s = $("lnb-search")?.getBoundingClientRect(); if (!lnb || !s) return;
    const root = document.documentElement.style, room = innerWidth - lnb.right - 24;
    // LNB 오른쪽에 자리가 모자라면(좁은 창의 서랍) 칸 바로 아래, 화면 안쪽에 띄운다
    if (room >= 320) { root.setProperty("--lnbs-top", `${Math.round(s.top)}px`); root.setProperty("--lnbs-left", `${Math.round(lnb.right + 12)}px`); root.setProperty("--lnbs-w", `min(34rem, ${room}px)`); }
    else { root.setProperty("--lnbs-top", `${Math.round(s.bottom + 6)}px`); root.setProperty("--lnbs-left", `${Math.max(8, Math.round(s.left))}px`); root.setProperty("--lnbs-w", `${Math.round(innerWidth - Math.max(8, s.left) - 8)}px`); } };
  if (docked) { window.addEventListener("resize", placeFloats); const l = $("lnb"); if (l) new ResizeObserver(placeFloats).observe(l); placeFloats(); }

  // ── 접힌 칸 ↔ 펼친 검색 ────────────────────────────────────────────────
  // 펼침은 칸의 `data-expanded` 하나가 말한다(CSS 가 그 하나만 본다). 펼친 검색은 오른쪽
  // 끝을 축으로 왼쪽으로 자라 전환과 경로를 덮는다(절대 배치라 줄의 폭은 그대로다). 접는
  // 것은 초점이 칸 밖으로 나가고 대화 패널이 닫혀 있을 때다. 친 글자는 남는다.
  const expand = (focus = true) => {
    findCard?.setAttribute("data-expanded", "true");
    findOpen?.setAttribute("aria-expanded", "true");
    if (focus) { qEl.focus(); qEl.select(); }
  };
  const collapse = () => {
    if (docked) return;                            // LNB 에 놓인 칸은 접지 않는다
    if (chatEl && !chatEl.hidden) return;          // 묻기 답을 읽는 중이다
    findCard?.removeAttribute("data-expanded");
    findOpen?.setAttribute("aria-expanded", "false");
  };
  if (findOpen) {
    const fi = findOpen.querySelector<HTMLElement>(".tb-find-icon");
    if (fi) fi.innerHTML = icon("search", 16);
    const fl = $("tb-find-label");
    if (fl) fl.textContent = COPY.ui.topbar.findOpen;
    const fk = $("tb-find-kbd");
    if (fk) fk.textContent = /mac/i.test(navigator.platform) ? "⌘K" : "Ctrl K";
    findOpen.setAttribute("aria-label", COPY.ui.topbar.searchPh);
    findOpen.setAttribute("aria-expanded", "false");
    // 접힌 LNB 띠에서는 이 단추(돋보기)만 보인다: 누르면 LNB 를 펴고 칸에 초점을 준다
    findOpen.addEventListener("click", () => { if (docked && $("shell")?.dataset.lnb !== "open") $("tb-shell-toggle")?.click(); window.setTimeout(() => expand(), 0); });
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
    if (sicon) sicon.innerHTML = icon(MODE_ICON[m], 16);
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
    placeFloats();
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
      box.innerHTML = head + `<p class="hint tb-rnote">${loaderInline(COPY.search.searching)}</p>`;
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
    // 표면이 거르개를 걸었으면(그래프) 치는 대로 그 표면을 거르고 드롭다운은 띄우지 않는다. 전문 검색은 Enter 로.
    if (searchFilter) { searchFilter(raw); close(); return; }
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
      if (searchFilter && q.value) { q.value = ""; searchFilter(""); }
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
        if (searchFilter) { open(); return; }   // 그래프: Enter 는 전문 결과를 칸 아래에 펼친다(고르는 것은 사람이)
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
    // LNB 가 접혀 있으면 먼저 연다(칸이 거기 산다)
    if (docked && $("shell")?.dataset.lnb !== "open") $("tb-shell-toggle")?.click();
    expand();
  }, true);
}
