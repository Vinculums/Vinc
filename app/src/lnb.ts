import { api, cfg } from "./api";
import type { NavGroup, NavTreeRes } from "./api";
import { COPY } from "./copy";
import { icon } from "./icons";

/** 좌측 문서 트리 (DEC D69) — 셸의 두 번째 크롬.
 *
 *  여기 사는 것: 표면 내비(홈·검색·팩) · 주제 트리(주제 → 그 주제의 문서) · 접기.
 *  D56 이 상단 바 하나로 몰아넣었던 것을 되돌린다. 되돌리는 이유는 그때와 전제가
 *  다르기 때문이다 — 이 사이드바가 싣는 것은 "표면 5개로 가는 내비"(D26 이 아이콘
 *  레일로 충분하다고 판정한 것)가 아니라 **문서 탐색기**다. Obsidian 으로 치면
 *  리본이 아니라 파일 목록 쪽이고, 그건 아이콘 레일로 대체되지 않는다.
 *
 *  **문서 정렬은 개념 계층 깊이순**이다(`/nav-tree`). 얕은 개념(선행·원본)의 문서가
 *  위, 깊은 개념(파생)의 문서가 아래로 들여쓰기된다. 최근 수정순이 아닌 이유: 목록의
 *  일이 "무엇이 새로운가"가 아니라 "이 주제가 어떻게 짜여 있는가"이기 때문이다.
 *
 *  **표면과의 계약은 훅 하나뿐이다**(상단 바와 같은 태도). Atlas 가 마운트되면
 *  자기 `focusNode` 를 넘기고(setLnbNavigator), 떠나면 지운다. 훅이 없으면 문서
 *  클릭은 해시 착지로 강등된다 — 눌러도 아무 일 없는 줄을 만들지 않는다. */

/** 트리는 그 문서가 **어느 주제 밑에 있는지 이미 안다** — 그 정보를 함께 넘긴다.
 *  받는 쪽(합계 뷰)은 개념 마크가 없어서 그래프 노드로는 주제를 되짚을 수 없다. */
type Navigator = (nodeId: string, domain?: string) => void;

const KEY_OPEN = "vinc.shell.lnb";        // 열림 상태(접기는 사용자 소유)
const KEY_EXPANDED = "vinc.shell.lnb.groups";   // 펼친 주제 목록

let navigate: Navigator | null = null;
let tree: NavTreeRes | null = null;
let loading = false;
let failed = false;
let active: { domain?: string; nodeId?: string } = {};
let expanded = new Set<string>();
let wired = false;

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T | null;
const esc = (s: string) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  c === "&" ? "&amp;" : c === "<" ? "&lt;" : c === ">" ? "&gt;" : "&quot;");

function isOpen(): boolean {
  return localStorage.getItem(KEY_OPEN) !== "closed";
}

function setOpen(open: boolean) {
  localStorage.setItem(KEY_OPEN, open ? "open" : "closed");
  document.getElementById("shell")?.setAttribute("data-lnb", open ? "open" : "closed");
  const t = $("lnb-toggle");
  if (t) {
    t.setAttribute("aria-expanded", String(open));
    const label = open ? COPY.ui.lnb.close : COPY.ui.lnb.open;
    t.setAttribute("aria-label", label);
    t.setAttribute("data-tip", label);
  }
}

function loadExpanded(): Set<string> {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY_EXPANDED) || "[]");
    return new Set(Array.isArray(raw) ? raw.filter((x) => typeof x === "string") : []);
  } catch { return new Set(); }
}

function saveExpanded() {
  try { localStorage.setItem(KEY_EXPANDED, JSON.stringify([...expanded])); } catch { /* 용량 초과 무시 */ }
}

/** Atlas 가 마운트/언마운트하며 착지 동작을 넘긴다. null 이면 해시 착지로 강등. */
export function setLnbNavigator(fn: Navigator | null) {
  navigate = fn;
}

/** 지금 어디를 보고 있는지 트리에 표시한다(크럼이 하던 일 — D71 로 여기 왔다). */
export function setLnbActive(sel: { domain?: string; nodeId?: string }) {
  active = { ...sel };
  if (sel.domain && !expanded.has(sel.domain)) {
    expanded.add(sel.domain);     // 지금 있는 주제는 접혀 있으면 안 된다
    saveExpanded();
  }
  paint();
}

/** 문서가 늘거나 주제가 바뀐 뒤 다시 읽는다(가져오기·추출·이름 변경 후). */
export async function refreshLnb(): Promise<void> {
  loading = true; failed = false;
  paint();
  try {
    tree = await api.navTree(cfg.owner);
    failed = !tree?.executed;
  } catch {
    tree = null; failed = true;
  }
  loading = false;
  paint();
}

function surfaceHash(): string {
  return location.hash.replace(/^#/, "").split("/")[0] || "atlas";
}

function navRows(): string {
  // **홈 하나뿐이다**(발주자 지시 2026-07-31). 검색은 상단 바가 상시로 들고 있고,
  // 가져오기·공유는 Tools 로 갔다(D71) — 같은 문을 두 번 그리지 않는다. 남은 하나는
  // "전체 지도로 돌아가기"라서, 크럼이 지고 간 자리를 실제로 메우는 유일한 행이다.
  // (Search·Packs 표면 자체는 살아 있고 해시·팔레트·단축키 1~3 으로 간다.)
  const here = surfaceHash();
  const on = here === "atlas";
  return `<nav class="lnb-nav" aria-label="${esc(COPY.ui.topbar.home)}">
      <a class="lnb-navrow${on ? " on" : ""}" href="#atlas"${on ? ' aria-current="page"' : ""}>
        <span class="lnb-ico" aria-hidden="true">${icon("graph", 15)}</span>
        <span>${esc(COPY.ui.topbar.home)}</span></a>
    </nav>`;
}

function docRow(d: { doc_id: string; title: string; node_id?: string; depth?: number },
                domain = ""): string {
  const depth = Math.min(Math.max(Number(d.depth ?? 0), 0), 8);   // 들여쓰기 상한 — 8단 넘으면 글자가 사라진다
  const on = d.node_id && active.nodeId === d.node_id;
  const tip = d.node_id ? COPY.ui.lnb.depthTip(Number(d.depth ?? 0)) : COPY.ui.lnb.editOnly;
  // 깊이는 세 갈래로 말한다. 여태 들여쓰기 하나뿐이라 1단이 10.88px 밖에 안 벌어져
  // 종속이 거의 안 보였고, 보조기술에는 8단 트리가 평평한 목록으로 들렸다.
  //   --lvl  : 들여쓰기(폭이 232px 라 단차를 무한정 키울 수 없다)
  //   --ink  : 잉크 3단(0=본문 · 1=흐림 · 2+=더 흐림). 색은 2단까지만 — 계속 흐리면
  //            AA 하한에 먼저 닿는다. 그 아래는 들여쓰기와 도트가 받는다.
  //   aria-level : 시각 밖 통로. 주제가 1단이므로 문서는 depth+2 다.
  const ink = Math.min(depth, 2);
  return `<button class="lnb-doc${on ? " on" : ""}" role="treeitem"
    aria-level="${depth + 2}"${on ? ' aria-selected="true"' : ""} data-doc="${esc(d.doc_id)}"
    data-node="${esc(d.node_id || "")}" data-dom="${esc(domain)}"
    style="--lvl:${depth};--ink:${ink}" data-tip="${esc(tip)}">
    <span class="lnb-dot" aria-hidden="true"></span><span class="lnb-dtitle">${esc(d.title || d.doc_id)}</span>
  </button>`;
}

function groupRows(g: NavGroup): string {
  const open = expanded.has(g.domain);
  const on = active.domain === g.domain;
  // 주제 행도 treeitem 이다(여태 role 이 없어 트리에서 빠져 있었다). 그리고 자식
  // group 은 **항목의 형제가 아니라 안쪽**에 와야 한다 — 형제로 두면 보조기술이
  // 계층으로 읽지 않는다.
  const head = `<button class="lnb-group${on ? " on" : ""}" role="treeitem" aria-level="1"
      ${on ? 'aria-selected="true" ' : ""}data-group="${esc(g.domain)}"
      aria-expanded="${open}" data-tip="${esc(COPY.ui.lnb.conceptCount(g.concepts))}">
      <span class="lnb-caret${open ? " open" : ""}" aria-hidden="true">›</span>
      <span class="lnb-gname">${esc(g.domain)}</span>
      <span class="lnb-gcount">${g.docs.length || "·"}</span>
    </button>`;
  if (!open) return head;
  const body = g.docs.length
    ? g.docs.map((d) => docRow(d, g.domain)).join("")
    // 개념만 있고 문서가 없는 주제 — 그렇다고 목록에서 지우면 이름을 바꾸거나
    // 문서를 붙일 진입점이 사라진다. 빈 채로 두되 빈 이유를 말한다.
    : `<p class="lnb-none hint">${esc(COPY.ui.lnb.empty)}</p>`;
  return head + `<div class="lnb-docs" role="group">${body}</div>`;
}

function paint() {
  const body = $("lnb-body");
  if (!body) return;
  if (loading && !tree) {
    body.innerHTML = navRows() + `<p class="lnb-none hint">${esc(COPY.ui.lnb.loading)}</p>`;
    return;
  }
  if (failed) {
    body.innerHTML = navRows()
      + `<p class="lnb-none hint">${esc(COPY.ui.lnb.failed)}</p>`
      + `<button class="btn btn-quiet lnb-retry" id="lnb-retry">${esc(COPY.ui.lnb.retry)}</button>`;
    return;
  }
  const groups = tree?.domains || [];
  const unfiled = tree?.unfiled || [];
  const docs = tree?.counts?.docs ?? 0;
  let html = navRows();
  html += `<div class="lnb-sec"><span class="lnb-sec-label">${esc(COPY.ui.lnb.documents)}</span>
    <span class="hint">${docs ? esc(COPY.ui.lnb.docCount(docs)) : "·"}</span></div>`;
  if (!groups.length && !unfiled.length) {
    html += `<p class="lnb-none hint">${esc(COPY.ui.lnb.empty)}</p>`
      + `<button class="btn btn-quiet lnb-retry" id="lnb-add">${esc(COPY.ui.lnb.emptyCta)}</button>`;
  } else {
    html += `<div class="lnb-tree" role="tree">${groups.map(groupRows).join("")}`;
    if (unfiled.length) {
      const open = expanded.has("");
      html += `<button class="lnb-group" data-group="" aria-expanded="${open}"
          data-tip="${esc(COPY.ui.lnb.unfiledTip)}">
          <span class="lnb-caret${open ? " open" : ""}" aria-hidden="true">›</span>
          <span class="lnb-gname lnb-unfiled">${esc(COPY.ui.lnb.unfiled)}</span>
          <span class="lnb-gcount">${unfiled.length}</span></button>`;
      // 미분류는 주제가 없다 — 빈 문자열을 명시해 map 의 index 가 두 번째 인자로
      // 새어 들어가지 않게 한다(주제 자리에 0·1·2 가 박히는 고전적 함정).
      if (open) html += `<div class="lnb-docs" role="group">${
        unfiled.map((d) => docRow(d, "")).join("")}</div>`;
    }
    html += `</div>`;
  }
  body.innerHTML = html;
}

/** 문서 클릭 — 착지 노드가 있으면 지도로, 없으면 편집기로. */
async function openDoc(docId: string, nodeId: string, domain?: string) {
  if (nodeId) {
    if (navigate) navigate(nodeId, domain);
    else location.hash = `#atlas/trace/${encodeURIComponent(nodeId)}`;
    return;
  }
  // 개념과 아직 이어지지 않은 문서는 지도에 자리가 없다 — 없는 자리를 지어내는 대신
  // 원문을 연다(거기서 개념을 뽑으면 다음부터 트리의 제자리로 들어온다).
  const { openDocSheet } = await import("./doc-editor");
  openDocSheet({ owner: cfg.owner, docId, onSaved: () => void refreshLnb() });
}

export function mountLnb() {
  if (wired) return;
  wired = true;
  const root = $("lnb");
  if (!root) return;
  expanded = loadExpanded();

  root.innerHTML = `
    <div class="lnb-head">
      <span class="lnb-title">${esc(COPY.ui.lnb.documents)}</span>
      <button class="tb-icon lnb-x" id="lnb-toggle" type="button" aria-controls="lnb-body">
        ${icon("chevron-left", 16)}
      </button>
    </div>
    <div class="lnb-body" id="lnb-body"></div>`;
  setOpen(isOpen());

  $("lnb-toggle")!.addEventListener("click", () => setOpen(!isOpen()));
  // 접힌 상태에서 다시 여는 손잡이. 접기 버튼과 같은 일을 하지만 사이드바 **밖**에
  // 살아야 한다 — 사이드바가 폭 0 이 되면 그 안의 버튼도 함께 사라지기 때문이다.
  const reopen = $("lnb-reopen");
  if (reopen) {
    reopen.innerHTML = icon("chevron-left", 16);
    reopen.setAttribute("aria-label", COPY.ui.lnb.open);
    reopen.setAttribute("data-tip", COPY.ui.lnb.open);
    reopen.addEventListener("click", () => setOpen(true));
  }

  const body = $("lnb-body")!;
  body.addEventListener("click", (e) => {
    const t = e.target as HTMLElement;
    const doc = t.closest<HTMLElement>(".lnb-doc");
    if (doc) {
      void openDoc(doc.dataset.doc || "", doc.dataset.node || "", doc.dataset.dom || "");
      return;
    }
    const group = t.closest<HTMLElement>(".lnb-group");
    if (group) {
      const key = group.dataset.group ?? "";
      if (expanded.has(key)) expanded.delete(key); else expanded.add(key);
      saveExpanded();
      paint();
      return;
    }
    if (t.closest("#lnb-retry")) { void refreshLnb(); return; }
    // 트리가 비었을 때의 CTA — 쓸지 가져올지 아직 모르므로 Tools 를 연다(D71).
    if (t.closest("#lnb-add")) { void import("./tools").then((m) => m.openTools()); return; }
  });
  // 주제 우클릭 = 이름 변경(S2). L0 주제 표의 `⋯` 가 하던 일이 여기로 왔다 —
  // 표가 사라졌으므로 이 경로가 없으면 이름을 바꿀 길이 없어진다.
  body.addEventListener("contextmenu", (e) => {
    const group = (e.target as HTMLElement).closest<HTMLElement>(".lnb-group");
    const domain = group?.dataset.group;
    if (!domain) return;      // 미분류 묶음은 실재하는 주제가 아니라 이름이 없다
    e.preventDefault();
    void domainMenu(domain, e as MouseEvent);
  });

  // 표면이 바뀌면 내비 활성 줄도 따라간다(트리는 그대로 — 목록은 표면과 무관하다).
  window.addEventListener("hashchange", paint);
  void refreshLnb();
}

/** 주제 이름 변경 / 미분류로 내리기(S2). 폐지된 L0 주제 표의 `⋯` 가 하던 일 그대로 —
 *  전역 Domain 노드는 건드리지 않고 내 노드의 domain 문자열만 바뀐다. */
async function domainMenu(domain: string, ev: MouseEvent) {
  const { openContextMenu, promptText } = await import("./components");
  openContextMenu(ev.clientX, ev.clientY, [{
    label: COPY.ui.domains.rename,
    run: () => void (async () => {
      const next = await promptText(COPY.ui.domains.renameAsk(domain), domain,
        COPY.ui.domains.rename, COPY.ui.atlasx.delCancel, COPY.ui.domains.unassign);
      if (next === null || next === domain) return;
      try {
        const r: any = await api.domainRename(cfg.owner, domain, next);
        if (!r.executed) throw new Error(r.error || "rename failed");
      } catch { return; }     // 실패는 조용히 — 목록은 그대로다(바뀐 것 없음)
      if (active.domain === domain) active.domain = next || undefined;
      await refreshLnb();
      // 열려 있는 지도의 주제 라벨도 같이 갈아야 한다. 표면에게 다시 그리라고
      // 말할 훅이 없으므로(내비게이터는 착지 전용) 해시 이벤트로 깨운다.
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    })(),
  }]);
}
