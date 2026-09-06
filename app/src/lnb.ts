import { api, cfg } from "./api";
import type { NavGroup, NavNode, NavTreeRes } from "./api";
import { COPY } from "./copy";
import { icon } from "./icons";

/** 좌측 문서 트리 (DEC D69) — 셸의 두 번째 크롬.
 *
 *  여기 사는 것: 표면 내비(홈·검색·팩) · 주제 트리(주제 → 그 주제의 문서 또는 노드)
 *  · 접기.
 *  D56 이 상단 바 하나로 몰아넣었던 것을 되돌린다. 되돌리는 이유는 그때와 전제가
 *  다르기 때문이다 — 이 사이드바가 싣는 것은 "표면 5개로 가는 내비"(D26 이 아이콘
 *  레일로 충분하다고 판정한 것)가 아니라 **문서 탐색기**다. Obsidian 으로 치면
 *  리본이 아니라 파일 목록 쪽이고, 그건 아이콘 레일로 대체되지 않는다.
 *
 *  **문서 정렬은 개념 계층 깊이순**이다(`/nav-tree`). 얕은 개념(선행·원본)의 문서가
 *  위, 깊은 개념(파생)의 문서가 아래로 들여쓰기된다. 최근 수정순이 아닌 이유: 목록의
 *  일이 "무엇이 새로운가"가 아니라 "이 주제가 어떻게 짜여 있는가"이기 때문이다.
 *
 *  **표면과의 계약은 훅 하나뿐이다**(상단 바와 같은 태도). View 가 마운트되면
 *  자기 `focusNode` 를 넘기고(setLnbNavigator), 떠나면 지운다. 훅이 없으면 문서
 *  클릭은 해시 착지로 강등된다 — 눌러도 아무 일 없는 줄을 만들지 않는다. */

/** 트리는 그 문서가 **어느 주제 밑에 있는지 이미 안다** — 그 정보를 함께 넘긴다.
 *  받는 쪽(합계 뷰)은 개념 마크가 없어서 그래프 노드로는 주제를 되짚을 수 없다. */
type Navigator = (nodeId: string, domain?: string, docId?: string) => void;
/** 주제를 고른다. `add` 면 지금 고른 것에 **더한다**(Ctrl·⌘ 클릭).
 *  화면이 즉시 따라간다 — 고르는 것과 보는 것이 어긋나는 중간 상태를 두지 않는다. */
type TopicPicker = (path: string, add: boolean) => void;

const KEY_OPEN = "vinc.shell.lnb";        // 열림 상태(접기는 사용자 소유)
const KEY_EXPANDED = "vinc.shell.lnb.groups";   // 펼친 주제 목록
const KEY_MODE = "vinc.shell.lnb.mode";   // 갈래: 문서 | 노드

let navigate: Navigator | null = null;
let pickTopic: TopicPicker | null = null;
let tree: NavTreeRes | null = null;
let loading = false;
let failed = false;
let active: { domain?: string; nodeId?: string } = {};
/** 지금 스코프가 걸고 있는 주제들 — 트리 줄의 선택 표시가 이걸 읽는다. */
let picked = new Set<string>();
let expanded = new Set<string>();

/** 주제 줄 **아래**에 무엇이 걸리는가. 주제 줄 자체는 두 갈래에서 같다 —
 *  이것은 서피스 내비가 아니라 이 열이 무엇을 펴는지의 선택이고, 그래서
 *  `adr-design-069`("왼쪽 열은 문서 탐색기다")를 되돌리지 않는다.
 *
 *  기억한다. 노드로 일하는 사람이 창을 열 때마다 문서 목록을 지나가지 않아야 한다. */
type Mode = "docs" | "nodes";
let mode: Mode = localStorage.getItem(KEY_MODE) === "nodes" ? "nodes" : "docs";
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

/** View 가 마운트/언마운트하며 착지 동작을 넘긴다. null 이면 해시 착지로 강등. */
export function setLnbTopicPicker(fn: TopicPicker | null) { pickTopic = fn; }

export function setLnbNavigator(fn: Navigator | null) {
  navigate = fn;
}

/** 지금 어디를 보고 있는지 트리에 표시한다(크럼이 하던 일 — D71 로 여기 왔다). */
/** 뿌리 자동 펼침을 한 번만 하기 위한 표시(paint 안 주석 참조). */
let autoExpanded = false;

export function setLnbActive(sel: { domain?: string; domains?: string[]; nodeId?: string }) {
  // 주제는 이제 **여럿**일 수 있다(스코프가 목록이 됐다). 첫 번째를 대표로 두고
  // 나머지는 선택 표시로만 쓴다 — 조상 펼치기는 대표 하나면 충분하다.
  const doms = sel.domains?.length ? sel.domains : sel.domain ? [sel.domain] : [];
  active = { ...sel, domain: doms[0] };
  picked = new Set(doms);
  if (doms[0]) {
    // 지금 있는 주제는 접혀 있으면 안 된다 — 그리고 중첩이 생겼으므로 **조상까지**
    // 함께 편다. 자기만 펴면 부모가 닫힌 채라 화면에 나타나지 않는다.
    const segs = doms[0].split("/").filter(Boolean);
    let grew = false;
    segs.forEach((_s, i) => {
      const path = segs.slice(0, i + 1).join("/");
      if (!expanded.has(path)) { expanded.add(path); grew = true; }
    });
    if (grew) saveExpanded();
  }
  paint();
}

/** 이 응답이 노드 갈래를 그릴 수 있는가. 서버는 `nodes=1` 로 물었을 때만 싣는다. */
const hasNodes = (t: NavTreeRes | null): boolean =>
  !!t?.domains?.length && t.domains.some((g) => Array.isArray(g.nodes));

/** 문서가 늘거나 주제가 바뀐 뒤 다시 읽는다(가져오기·추출·이름 변경 후). */
export async function refreshLnb(): Promise<void> {
  loading = true; failed = false;
  paint();
  try {
    // 노드 갈래일 때만 제목까지 받는다. 노드 응답은 문서까지 함께 실어 오므로
    // 한 번 받아 두면 갈래를 도로 넘겨도 다시 부르지 않는다.
    tree = await api.navTree(cfg.owner, { nodes: mode === "nodes" });
    failed = !tree?.executed;
  } catch {
    tree = null; failed = true;
  }
  loading = false;
  paint();
}

/** 갈래 전환. 노드 목록이 아직 없을 때만 다시 읽는다 — 되돌아오는 길은 공짜다. */
function setMode(next: Mode): void {
  if (mode === next) return;
  mode = next;
  localStorage.setItem(KEY_MODE, next);
  if (next === "nodes" && !hasNodes(tree)) { void refreshLnb(); return; }
  paint();
}

function surfaceHash(): string {
  return location.hash.replace(/^#/, "").split("/")[0] || "view";
}

function navRows(): string {
  // **홈 하나뿐이다**(발주자 지시 2026-07-31). 검색은 상단 바가 상시로 들고 있고,
  // 가져오기·공유는 Tools 로 갔다(D71) — 같은 문을 두 번 그리지 않는다. 남은 하나는
  // "전체 지도로 돌아가기"라서, 크럼이 지고 간 자리를 실제로 메우는 유일한 행이다.
  // (Search·Packs 표면 자체는 살아 있고 해시·팔레트·단축키 1~3 으로 간다.)
  const here = surfaceHash();
  const on = here === "view";
  return `<nav class="lnb-nav" aria-label="${esc(COPY.ui.topbar.home)}">
      <a class="lnb-navrow${on ? " on" : ""}" href="#view"${on ? ' aria-current="page"' : ""}>
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

/** 노드 한 줄. 문서 줄과 **같은 깊이 표현**을 쓴다(들여쓰기 · 잉크 · aria-level) —
 *  두 갈래가 같은 계층을 그리는데 다르게 생기면 같은 트리로 안 읽힌다. 다른 것은
 *  하나뿐이다: 열 문서가 없으므로 누르면 지도에서 그 노드로 간다. */
function nodeRow(nd: NavNode, domain = ""): string {
  const depth = Math.min(Math.max(Number(nd.depth ?? 0), 0), 8);
  const on = active.nodeId === nd.id;
  const ink = Math.min(depth, 2);
  return `<button class="lnb-doc lnb-node${on ? " on" : ""}" role="treeitem"
    aria-level="${depth + 2}"${on ? ' aria-selected="true"' : ""} data-nodeid="${esc(nd.id)}"
    data-dom="${esc(domain)}" style="--lvl:${depth};--ink:${ink}"
    data-tip="${esc(COPY.ui.lnb.nodeTip)}">
    <span class="lnb-dot" aria-hidden="true"></span><span class="lnb-dtitle">${esc(nd.title || nd.id)}</span>
  </button>`;
}

/** 주제 트리의 한 마디. `path` 가 `/nav-tree` 의 주제 문자열 통짜와 같은 값이라
 *  선택·활성 키는 그대로다 — 중첩은 **화면의 일**이고 계약은 안 바뀐다. */
type DomNode = { seg: string; path: string; group?: NavGroup; kids: DomNode[] };

/** 슬래시가 곧 계층이다(GRAPH.md: `vinc/design` 은 `vinc` 아래에 선다). 저장소는
 *  그 계층을 이미 노드로 갖고 있는데(`domain:vinc` -PARENT_OF-> `domain:vinc/design`)
 *  좌측 트리만 문자열을 통짜로 묶어 평평하게 보여 주고 있었다. 서버가 주는 것은
 *  그대로 두고 여기서 쪼갠다 — 계약을 안 건드리는 것이 이 방식의 값이다.
 *
 *  중간 마디(`vinc`)는 그 자체로 주제일 수도 있고 아닐 수도 있다. 아니면 `group`
 *  이 없어 문서를 안 갖고 자식만 갖는다 — 그래도 행은 낸다(그 이름이 계층이다). */
function nestGroups(groups: NavGroup[]): DomNode[] {
  const root: DomNode = { seg: "", path: "", kids: [] };
  for (const g of groups) {
    const segs = g.domain.split("/").filter(Boolean);
    let cur = root;
    segs.forEach((seg, i) => {
      const path = segs.slice(0, i + 1).join("/");
      let next = cur.kids.find((k) => k.path === path);
      if (!next) { next = { seg, path, kids: [] }; cur.kids.push(next); }
      cur = next;
    });
    cur.group = g;
  }
  return root.kids;
}
const subDocs = (n: DomNode): number =>
  (n.group?.docs.length || 0) + n.kids.reduce((s, k) => s + subDocs(k), 0);
/** 갈래에 맞는 수. 주제 줄 끝의 숫자가 그 아래 펼쳐질 줄 수와 같아야 한다. */
const subCount = (n: DomNode): number => (mode === "nodes" ? subConcepts(n) : subDocs(n));
const subConcepts = (n: DomNode): number =>
  (n.group?.concepts || 0) + n.kids.reduce((s, k) => s + subConcepts(k), 0);

function groupRows(n: DomNode, level = 1): string {
  const open = expanded.has(n.path);
  const on = picked.has(n.path);
  // 주제 행도 treeitem 이다(여태 role 이 없어 트리에서 빠져 있었다). 그리고 자식
  // group 은 **항목의 형제가 아니라 안쪽**에 와야 한다 — 형제로 두면 보조기술이
  // 계층으로 읽지 않는다.
  const head = `<button class="lnb-group${on ? " on" : ""}" role="treeitem" aria-level="${level}"
      ${on ? 'aria-selected="true" ' : ""}data-group="${esc(n.path)}"
      aria-expanded="${open}" aria-pressed="${on}"
      data-tip="${esc(COPY.ui.lnb.conceptCount(subConcepts(n)))} · ${esc(COPY.ui.lnb.topicTip)}">
      <span class="lnb-caret${open ? " open" : ""}" aria-hidden="true">›</span>
      <span class="lnb-gname">${esc(n.seg)}</span>
      <span class="lnb-gcount">${subCount(n) || "·"}</span>
    </button>`;
  if (!open) return head;
  // 자식 가지가 먼저, 그다음 이 주제 자신의 문서. 순서를 뒤집으면 문서 목록 끝에
  // 가지가 붙어 "문서인 줄 알았던 것"이 사실 폴더인 상황이 된다.
  const kids = n.kids.map((k) => groupRows(k, level + 1)).join("");
  const rows = mode === "nodes"
    ? (n.group?.nodes || []).map((nd) => nodeRow(nd, n.path)).join("")
    : (n.group?.docs || []).map((d) => docRow(d, n.path)).join("");
  const own = rows
    // 개념만 있고 문서가 없는 주제 — 그렇다고 목록에서 지우면 이름을 바꾸거나
    // 문서를 붙일 진입점이 사라진다. 빈 채로 두되 빈 이유를 말한다.
    // (자식 가지가 있으면 비어 보이지 않으므로 그 문구를 내지 않는다.)
    || (n.kids.length ? "" : `<p class="lnb-none hint">${
      esc(mode === "nodes" ? COPY.ui.lnb.noNodes : COPY.ui.lnb.empty)}</p>`);
  return head + `<div class="lnb-docs" role="group">${kids}${own}</div>`;
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
  const topicCount = (tree?.domains || []).length;
  let html = navRows();
  html += `<div class="lnb-sec"><span class="lnb-sec-label">${esc(COPY.ui.lnb.documents)}</span>
    <span class="hint">${topicCount || "·"}</span></div>`;
  // 갈래 칩 — 주제 줄은 그대로이고 그 아래 무엇이 걸리는지만 바뀐다. `radiogroup`
  // 인 이유: 둘 중 하나이지 둘 다 켤 수 있는 것이 아니다.
  html += `<div class="lnb-mode" role="radiogroup" aria-label="${esc(COPY.ui.lnb.documents)}">${
    ([["docs", COPY.ui.lnb.modeDocs, COPY.ui.lnb.modeDocsTip],
      ["nodes", COPY.ui.lnb.modeNodes, COPY.ui.lnb.modeNodesTip]] as const)
      .map(([key, label, tip]) => `<button class="lnb-modechip${mode === key ? " on" : ""}"
        type="button" role="radio" aria-checked="${mode === key}" data-mode="${key}"
        data-tip="${esc(tip)}">${esc(label)}</button>`).join("")}</div>`;
  // ③ 고른 것이 있으면 **말한다**. 켜진 배경만으로는 몇 개인지도, 어떻게 푸는지도
  //    안 보인다 — 홈 행이 전체로 돌아가는 길이지만 그렇게 읽히지 않는다.
  if (picked.size) {
    html += `<div class="lnb-scope">
      <span>${esc(COPY.ui.lnb.scoped(picked.size))}</span>
      <button class="lnb-scope-clear" type="button">${esc(COPY.ui.lnb.scopedClear)}</button>
    </div>`;
  }
  // 노드 갈래를 골랐는데 응답이 노드를 안 실어 왔다. 데스크탑에서는 사이드카가 앱과
  // 한 벌이라 날 수 없지만 **웹모드는 다르다** — 사이트가 배포한 앱과 허브의 사이드카가
  // 따로 올라가므로 앱이 먼저 새것일 수 있다. 그때 빈 트리를 그리면 "내 개념이
  // 사라졌다"로 읽힌다. 갈래는 사용자가 고른 대로 두고, 못 받았다고 말한다.
  // ⚠ 주제가 하나도 없을 때는 여기 오면 안 된다 — 그건 빈 그래프이지 못 받은 것이
  // 아니다. 아래 빈 상태 CTA 가 그 경우를 맡는다.
  if (mode === "nodes" && groups.length > 0 && !hasNodes(tree)) {
    body.innerHTML = html
      + `<p class="lnb-none hint">${esc(COPY.ui.lnb.failed)}</p>`
      + `<button class="btn btn-quiet lnb-retry" id="lnb-retry">${esc(COPY.ui.lnb.retry)}</button>`;
    return;
  }
  if (!groups.length && !unfiled.length) {
    html += `<p class="lnb-none hint">${esc(COPY.ui.lnb.empty)}</p>`
      + `<button class="btn btn-quiet lnb-retry" id="lnb-add">${esc(COPY.ui.lnb.emptyCta)}</button>`;
  } else {
    const tops = nestGroups(groups);
    // 뿌리가 하나뿐이면(지금은 전부 `vinc/*` 다) 한 번은 펼쳐 둔다 — 안 그러면
    // 중첩이 정보를 더하는 대신 모든 것을 클릭 한 번 뒤로 밀기만 한다. 최초 1회만
    // 이라 접어 둔 사용자의 선택과 싸우지 않는다.
    if (!autoExpanded && tops.length === 1) { autoExpanded = true; expanded.add(tops[0].path); }
    html += `<div class="lnb-tree" role="tree">${tops.map((n) => groupRows(n)).join("")}`;
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

/** 캐럿을 **실제로 돌린다.** paint() 가 트리를 통째로 다시 그리므로 새 캐럿은 최종 각도로
 *  태어나고 전이는 시작점이 없다 — `.lnb-caret` 의 transition 이 여태 한 번도 돌지 않은
 *  이유. 방금 그린 캐럿을 이전 각도에 세워 한 프레임 확정한 뒤 놓아 준다(FLIP). */
function spinCaret(key: string, open: boolean): void {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const caret = $("lnb-body")?.querySelector<HTMLElement>(`.lnb-group[data-group="${CSS.escape(key)}"] > .lnb-caret`);
  if (!caret) return;
  caret.style.transition = "none";
  caret.style.transform = open ? "rotate(0deg)" : "rotate(90deg)";
  void caret.offsetWidth;                  // 시작 상태 확정 — 없으면 한 프레임에 건너뛴다
  caret.style.transition = "";
  caret.style.transform = "";
}

/** 문서 클릭 — **지도로 가고 문서를 연다**(둘 다, 한 클릭에). 여태 노드로만
 *  날아가서, 목록에서 문서를 골라도 본문은 두 번째 클릭이 있어야 나왔고 그 두 번째
 *  클릭조차 "그 노드의 발췌가 마침 이 문서일 때"만 성립했다. 착지 노드가 없는
 *  문서는 지도에 자리가 없으므로 본문만 연다. */
async function openDoc(docId: string, nodeId: string, domain?: string) {
  if (nodeId) {
    if (navigate) navigate(nodeId, domain, docId);
    else location.hash = `#view/trace/${encodeURIComponent(nodeId)}`;
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
      <!-- 패널 제목은 비워 둔다. 바로 아래 절 제목이 같은 말을 하고 있었고(Home 을
           사이에 두고 두 번), 이 줄의 일은 접기 버튼을 담는 것이다. 버튼은 자기
           aria-label 을 가지므로 접근성에서 잃는 것이 없다. -->
      <span class="lnb-title" aria-hidden="true"></span>
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
    const chip = t.closest<HTMLElement>(".lnb-modechip");
    if (chip) { setMode(chip.dataset.mode === "nodes" ? "nodes" : "docs"); return; }
    // 노드 줄이 문서 줄의 class 를 함께 쓰므로(같은 생김새) **먼저** 가른다.
    const nd = t.closest<HTMLElement>(".lnb-node");
    if (nd) {
      const id = nd.dataset.nodeid || "";
      if (!id) return;
      if (navigate) navigate(id, nd.dataset.dom || "");
      else location.hash = `#view/trace/${encodeURIComponent(id)}`;
      active = { domain: nd.dataset.dom || "", nodeId: id };
      paint();
      return;
    }
    const doc = t.closest<HTMLElement>(".lnb-doc");
    if (doc) {
      void openDoc(doc.dataset.doc || "", doc.dataset.node || "", doc.dataset.dom || "");
      return;
    }
    const group = t.closest<HTMLElement>(".lnb-group");
    if (group) {
      const key = group.dataset.group ?? "";
      // 캐럿은 **접고 펴기**, 이름은 **주제 고르기**다. 여태 어디를 눌러도 접고
      // 펴기뿐이라, 주제 트리가 지도를 좁힐 것처럼 생겨서는 아무것도 안 했다.
      // 미분류 묶음(빈 key)은 실재하는 주제가 아니라 고를 수 없다 — 접고 펴기만.
      const onCaret = !!t.closest(".lnb-caret");
      if (!onCaret && key && pickTopic) {
        pickTopic(key, e.ctrlKey || e.metaKey);
        return;
      }
      if (expanded.has(key)) expanded.delete(key); else expanded.add(key);
      saveExpanded();
      paint();
      spinCaret(key, expanded.has(key));
      return;
    }
    if (t.closest(".lnb-scope-clear")) {
      // 고른 것을 전부 뺀다 = 전체(L0). `pickTopic` 에 같은 경로를 다시 넘기면
      // 토글이라 하나씩 꺼야 하므로, 첫 번째를 비-add 로 두 번 눌러 비우는 대신
      // 홈 해시로 간다 — 그것이 이 앱에서 "전체"의 정본 주소다.
      location.hash = "#view";
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
        COPY.ui.domains.rename, COPY.ui.viewx.delCancel, COPY.ui.domains.unassign);
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
