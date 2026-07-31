import { api, cfg } from "./api";
import { COPY } from "./copy";
import { icon } from "./icons";

/** 상단 바 (DEC D56·D57) — 셸의 유일한 크롬.
 *
 *  여기 사는 것: 로고 · 렌즈 · 관점 세그 · 상시 검색 · 가져오기 · 공유 · 설정.
 *  여기 살지 않는 것: 표면 목록과 주제·문서 목록(좌측 트리가 가져갔다 — D69),
 *  크럼(폐지 — "지금 어디"는 트리가 상시로 말한다), 지표(하단 상태 표시줄에 남는다).
 *
 *  **표면과의 계약은 훅 하나뿐이다.** 공유·보기 버튼은 Atlas 만 뜻이 있으므로
 *  Atlas 가 마운트될 때 자기 동작을 등록하고(setAtlasHooks), 떠날 때 지운다.
 *  등록이 없으면 버튼은 숨는다 — 눌러도 아무 일 없는 컨트롤을 내놓지 않는다
 *  (보이는 손잡이는 반드시 무언가를 한다는 규율, VIEW_ROWS 와 같은 태도).
 *
 *  검색 드롭다운은 **노드 히트만** 싣는다: 청크(문서 조각) 히트는 Atlas 에 착지할
 *  대상이 없어 "눌렀는데 아무 데도 안 가는 줄"이 된다. 문서 전체를 훑는 일은
 *  `모든 문서` 시트가 맡는다(표면 이동 아님 — 닫으면 하던 자리로 돌아온다). */

export type AtlasHooks = {
  /** 지금 스코프를 팩으로 묶는 시트를 연다(범위는 Atlas 가 정한다). */
  share: () => void;
  /** 그 노드로 이동한다(D70) — 화면에 있으면 카메라만, 없으면 스코프를 간다.
   *  검색 착지가 이 훅을 쓰면 표면 리마운트 없이 날아간다. */
  openNode: (id: string) => void;
};

let hooks: AtlasHooks | null = null;
let wired = false;

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T | null;

/** Atlas 가 마운트/언마운트하며 자기 동작을 넘긴다. null 이면 버튼이 숨는다. */
export function setAtlasHooks(h: AtlasHooks | null) {
  hooks = h;
  refreshTopbar();
}

/** 지금 Atlas 가 살아 있는가(+그 동작). 다른 표면·시트가 "지도로 보낼 수 있는가"를
 *  판단하는 단일 지점이다 — 없으면 해시 착지로 강등한다. */
export function atlasHooks(): AtlasHooks | null {
  return hooks;
}

/** 지도 손잡이 표시 상태를 현재 훅 기준으로 다시 칠한다(관점을 바꾼 뒤 호출).
 *  Tools 는 훅과 무관하게 늘 눌린다 — 문서를 쓰고 가져오는 일은 지도가 없어도 된다. */
export function refreshTopbar() {
  // 지도 손잡이는 Atlas 소유다 — 훅이 없으면 슬롯을 비운다. 남겨 두면 검색·팩
  // 화면에서 눌리는데 아무 지도도 안 바뀌는 칩이 된다.
  if (!hooks) { const l = lensSlot(), v = viewsSlot(); if (l) l.innerHTML = ""; if (v) v.innerHTML = ""; }
}

/** 렌즈 버튼 자리(D71) — 하단 바에서 상단 바 좌측으로 올라왔다. */
export function lensSlot(): HTMLElement | null { return $("tb-lens"); }

/** 관점 세그 자리(D71). 내용은 Atlas 가 채운다(주인은 표면이다). */
export function viewsSlot(): HTMLElement | null { return $("tb-views"); }

export function mountTopbar() {
  if (wired) return;
  wired = true;
  const qEl = $<HTMLInputElement>("tb-q");
  const boxEl = $("tb-results");
  const sicon = document.querySelector<HTMLElement>(".tb-sicon");
  const kbd = $("tb-kbd");
  if (!qEl || !boxEl) return;
  const q = qEl, box = boxEl;

  q.placeholder = COPY.ui.topbar.searchPh;
  q.setAttribute("aria-label", COPY.ui.topbar.searchPh);
  if (sicon) sicon.innerHTML = icon("search", 15);
  // 표기는 플랫폼 관용구를 따른다(mac ⌘K · 그 외 Ctrl K) — 팔레트 단축키와 같은 키.
  if (kbd) kbd.textContent = /mac/i.test(navigator.platform) ? "⌘K" : "Ctrl K";

  // Tools — 표면과 무관하게 언제나 눌린다(Atlas 훅에 매이지 않는다). 공유는 이 안에서
  // 범위를 고르고, `지금 보고 있는 곳`만 Atlas 훅을 쓴다.
  const tools = $("tb-tools");
  if (tools) {
    tools.innerHTML = icon("pack-in", 15) + `<span>${COPY.ui.tools.title}</span>`;
    tools.setAttribute("data-tip", COPY.ui.tools.tip);
    tools.addEventListener("click", async () => {
      const { openTools } = await import("./tools");
      openTools();
    });
  }

  type Hit = { id: string; title: string; domain: string; mark: string; snippet: string };
  let hits: Hit[] = [];
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

  function draw(loading = false) {
    const esc = (s: string) => String(s ?? "").replace(/[&<>"]/g, (c) =>
      c === "&" ? "&amp;" : c === "<" ? "&lt;" : c === ">" ? "&gt;" : "&quot;");
    const head = `<div class="tb-rhead"><span class="hint">${COPY.ui.topbar.results}</span>
      <span class="hint">${COPY.ui.topbar.enterHint}</span></div>`;
    if (loading) {
      box.innerHTML = head + `<p class="hint tb-rnote">${COPY.search.searching}</p>`;
      open(); return;
    }
    if (hits.length === 0) {
      box.innerHTML = head
        + `<p class="hint tb-rnote">${COPY.ui.topbar.noResults}</p>`
        // 첫 질의는 색인을 만드느라 몇 초 걸린다 — 침묵 대신 이유를 말한다(V14).
        + `<p class="hint tb-rnote">${COPY.ui.topbar.firstSlow}</p>`
        + `<button class="tb-all" id="tb-all">${COPY.ui.topbar.browseAll}</button>`;
      wireAll(); open(); return;
    }
    const foot = `<button class="tb-all" id="tb-all">${COPY.ui.topbar.browseAll}</button>`;
    box.innerHTML = head + hits.map((h, i) => `
      <button class="tb-row" role="option" data-i="${i}">
        <span class="tb-mark">${h.mark}</span>
        <span class="tb-rmain">
          <span class="tb-rtitle">${esc(h.title)}</span>
          ${h.snippet ? `<span class="hint tb-rsnip">${esc(h.snippet)}</span>` : ""}
        </span>
        <span class="hint tb-rmeta">${esc(h.domain)}</span>
      </button>`).join("") + foot;
    box.querySelectorAll<HTMLElement>(".tb-row").forEach((r) =>
      r.addEventListener("mousedown", (e) => {   // blur 보다 먼저 잡는다
        e.preventDefault();
        go(hits[Number(r.dataset.i)]);
      }));
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

  function wireAll() {
    box.querySelector("#tb-all")?.addEventListener("mousedown", (e) => {
      e.preventDefault();
      void openAllDocs();
    });
  }

  /** 결과 착지 — Atlas 가 이미 떠 있으면 훅으로 **그 노드까지** 날아가고(리마운트
   *  없음, 카드·문서 패널까지 열린다), 다른 표면이면 해시로 Atlas 를 띄운다. */
  function go(h: Hit | undefined) {
    if (!h) return;
    close();
    q.blur();
    if (hooks) hooks.openNode(h.id);
    else location.hash = `#atlas/trace/${encodeURIComponent(h.id)}`;
  }

  async function run(raw: string) {
    const mine = ++seq;
    draw(true);
    try {
      const res: any = await api.search(raw);
      if (mine !== seq) return;                  // 지나간 질의 — 버린다
      hits = (res.node_hits || []).slice(0, 8).map((n: any) => ({
        id: String(n.id || ""),
        title: String(n.title || n.id || ""),
        domain: String(n.domain || ""),
        // 왜 맞았는지(D24) — 그래프 arm 은 ≈, 제목 일치는 ≡.
        mark: n.matched_in === "graph" ? "≈" : "≡",
        snippet: String(n.one_liner || n.snippet || "").slice(0, 90),
      })).filter((h: Hit) => h.id);
      draw();
    } catch {
      if (mine !== seq) return;
      hits = [];
      // 실패해도 막다른 골목을 만들지 않는다 — 목록으로 가는 길은 남긴다
      // (검색이 죽었다고 문서에 닿을 방법까지 사라지면 안 된다).
      box.innerHTML = `<p class="hint tb-rnote">${COPY.search.error}</p>`
        + `<button class="tb-all" id="tb-all">${COPY.ui.topbar.browseAll}</button>`;
      wireAll();
      open();
    }
  }

  q.addEventListener("input", () => {
    window.clearTimeout(timer);
    const raw = q.value.trim();
    if (raw.length < 2) { hits = []; close(); return; }
    timer = window.setTimeout(() => void run(raw), 250);
  });
  q.addEventListener("focus", () => { if (hits.length) open(); });
  q.addEventListener("blur", () => window.setTimeout(close, 120));
  q.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { close(); q.blur(); return; }
    if (e.key !== "Enter") return;
    e.preventDefault();
    // 결과가 있으면 첫 줄로 착지, 없으면 전체 목록을 연다(막다른 골목 금지).
    // 여기도 표면 이동이 아니라 시트다 — 드롭다운의 `모든 문서` 와 같은 자리로 간다.
    if (hits.length) go(hits[0]);
    else if (q.value.trim()) void openAllDocs();
  });

  // Ctrl/Cmd+K 는 팔레트가 이미 쓰는 키다 — 여기서는 **검색 필드 포커스**로 통일한다
  // (같은 키에 두 동작이 붙으면 어느 쪽이 뜰지 사용자가 예측할 수 없다). 팔레트는
  // main.ts 에서 이 핸들러가 먼저 잡도록 순서를 맞춘다.
  document.addEventListener("keydown", (e) => {
    if (!(e.key === "k" && (e.metaKey || e.ctrlKey))) return;
    e.preventDefault();
    q.focus();
    q.select();
  }, true);
}
