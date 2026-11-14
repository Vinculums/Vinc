import { cfg, isCommunitySpace } from "./api";
import type { NavGroup } from "./api";
import { esc } from "./components";
import { COPY } from "./copy";
import { docTitle } from "./doc-title";
import { bridgeKey, foldsOwnRows, ownKey } from "./lnb-tree";
import type { LnbGraphRow } from "./lnb-graph";
import { $, exp, st, tintStyle } from "./lnb-state";
import { topicRingIcon } from "./view-tint";

/** 좌측 트리의 줄을 그린다: 문서 줄(`docRow`) · 주제 마디(`nestGroups` · `groupRows`) · 머리(`paintHead`) · 고른 주제 칩(`scopeChip`).
 *  그래프 트리가 같은 마디와 접기 규칙을 쓰므로 그래프의 노드 줄(`gnodeRow`)도 여기 선다 — 문서 트리와 그래프 트리는 한 그림이다.
 *  이 파일은 다시 그리기를 부르지 않는다: 줄의 글을 돌려주기만 하고, 언제 그릴지는 입구(`lnb.ts`)가 쥔다.
 *  근거: [[decision:documents-and-graph-share-one-lnb-tree-2026-11-06]] */

export function docRow(d: { doc_id: string; title: string; node_id?: string; depth?: number },
                domain = ""): string {
  const depth = Math.min(Math.max(Number(d.depth ?? 0), 0), 8);   // 들여쓰기 상한 — 8단 넘으면 글자가 사라진다
  const on = st.active.docId ? st.active.docId === d.doc_id : !!d.node_id && st.active.nodeId === d.node_id;
  // 깊이 툴팁은 착지 개념이 있을 때만 뜻이 있다(개념 계층 안의 자리다).
  const tip = d.node_id ? COPY.ui.lnb.depthTip(Number(d.depth ?? 0)) : "";
  // 깊이는 세 갈래로 말한다. 여태 들여쓰기 하나뿐이라 1단이 10.88px 밖에 안 벌어져
  // 종속이 거의 안 보였고, 보조기술에는 8단 트리가 평평한 목록으로 들렸다.
  //   --lvl  : 들여쓰기(폭이 232px 라 단차를 무한정 키울 수 없다)
  //   --ink  : 잉크 3단(0=본문 · 1=흐림 · 2+=더 흐림). 색은 2단까지만 — 계속 흐리면
  //            AA 하한에 먼저 닿는다. 그 아래는 들여쓰기와 도트가 받는다.
  //   aria-level : 시각 밖 통로. 주제가 1단이므로 문서는 depth+2 다.
  // 그 위에 --tint 가 하나 더 얹히는데 그것은 깊이가 아니라 **주제**를 말하고, 글자가
  // 아니라 도트에만 앉는다 — 잉크 3단과 자리를 다투지 않게 하려는 것이다.
  const ink = Math.min(depth, 2);
  return `<button class="lnb-doc${on ? " on" : ""}" role="treeitem"
    aria-level="${depth + 2}"${on ? ' aria-selected="true"' : ""} data-doc="${esc(d.doc_id)}"
    data-node="${esc(d.node_id || "")}" data-dom="${esc(domain)}"
    style="--lvl:${depth};--ink:${ink}${tintStyle(domain)}" data-tip="${esc(tip)}">
    <span class="lnb-dot" aria-hidden="true"></span><span class="lnb-dtitle">${esc(docTitle(d.title))}</span>
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
export function nestGroups(groups: NavGroup[]): DomNode[] {
  const root: DomNode = { seg: "", path: "", kids: [] };
  let loose: DomNode | null = null;
  for (const g of groups) {
    const segs = g.domain.split("/").filter(Boolean);
    // 그래프: 주제 없는 노드는 이름 없는 가상 뿌리에 직접 선다(최상위 주제가 여럿일 때). 줄은 "주제 없음" 하나로 맨 끝에 낸다.
    // 경로가 빈 문자열이라 누르면 접고 펴기만 한다(문서 트리의 미분류 줄과 같다). 문서 트리는 예전 그대로 이 묶음을 내지 않는다.
    if (!segs.length) { if (st.gsrc) loose = { seg: COPY.ui.lnb.unfiled, path: "", group: g, kids: [] }; continue; }
    let cur = root;
    segs.forEach((seg, i) => {
      const path = segs.slice(0, i + 1).join("/");
      let next = cur.kids.find((k) => k.path === path);
      if (!next) { next = { seg, path, kids: [] }; cur.kids.push(next); }
      cur = next;
    });
    cur.group = g;
  }
  if (loose) root.kids.push(loose);
  return root.kids;
}
const subDocs = (n: DomNode): number =>
  (n.group?.docs.length || 0) + n.kids.reduce((s, k) => s + subDocs(k), 0);
/** 주제 줄 끝의 숫자 = 그 아래 펼쳐질 **노드** 줄 수. 서버가 `count` 로 준다
 *  (`kind` 로 물었을 때만 오는 열이고, 그것이 곧 그 갈래의 줄 수라는 계약이다).
 *  문서는 노드 아래 매달리므로 이 숫자에 안 들어간다 — 세는 것과 그리는 것이
 *  같아야 한다는 규율은 **노드 줄에 대한 것**이다. */

export function groupRows(n: DomNode, level = 1): string {
  const open = exp().has(n.path);
  const on = st.gsrc ? st.gActive === `topic:${n.path}` : st.picked.has(n.path);
  // 주제 행도 treeitem 이다. 자식 group 은 **항목의 형제가 아니라 안쪽**에 와야 한다 —
  // 형제로 두면 보조기술이 계층으로 읽지 않는다.
  // 줄은 **접고 펴기만** 한다. 지금 표면을 이 주제로 좁히는 것은 줄 끝의 버튼이다 — 줄
  // 클릭이 화면까지 바꾸면 "안을 보려고" 누른 사람의 화면이 통째로 바뀐다.
  // 버튼은 줄의 형제다(버튼 안에 버튼을 둘 수 없다). 골라져 있음(aria-pressed)도 버튼이 든다.
  const docsHere = subDocs(n);
  const head = `<div class="lnb-grow${on ? " on" : ""}">
    <button class="lnb-group${on ? " on" : ""}" role="treeitem" aria-level="${level}" style="${tintStyle(n.path).slice(1)}"
      ${on ? 'aria-selected="true" ' : ""}data-group="${esc(n.path)}"
      aria-expanded="${open}"${st.gsrc ? "" : ` data-tip="${esc(COPY.ui.home.count(docsHere))}"`}>
      <span class="lnb-caret${open ? " open" : ""}" aria-hidden="true">›</span>
      <span class="lnb-gring" aria-hidden="true">${topicRingIcon(11, 2)}</span><span class="lnb-gname">${esc(n.seg)}</span>
      <span class="lnb-gcount">${docsHere || "·"}</span>
    </button>
  </div>`;
  if (!open) return head;
  // 자식 가지가 먼저, 그다음 이 주제 자신의 문서. 순서를 뒤집으면 문서 목록 끝에
  // 가지가 붙어 "문서인 줄 알았던 것"이 사실 폴더인 상황이 된다.
  const kids = n.kids.map((k) => groupRows(k, level + 1)).join("");
  // 이 주제의 문서. 들여쓰기는 착지 개념의 계층 깊이다(얕은 개념의 문서가 위).
  const all = n.group?.docs || [];
  // 그래프: 다리(bridge)는 하위 주제들을 잇는 것이라 그 주제의 **맨 위**(하위 주제보다 앞)에 늘 펼쳐 둔다.
  // 줄은 묶음 단위로 가른다: depth 0 줄과 그 뒤에 붙은 depth 1 줄들이 한 묶음이다(다리에 붙은 것도 다리와 함께 간다).
  const kindOf = (d: unknown) => (d as { kind?: string }).kind || "";
  const bridgeRows: typeof all = [], docs: typeof all = [];
  if (st.gsrc) { let into = docs; for (const d of all) { if (Number(d.depth ?? 0) === 0) into = kindOf(d) === "bridge" ? bridgeRows : docs; into.push(d); } }
  else docs.push(...all);
  const gRow = (d: (typeof all)[number]) => gnodeRow({ id: d.doc_id, title: d.title, kind: kindOf(d), depth: Number(d.depth ?? 0) }, n.path);
  // 다리는 한 묶음("다리 N개")으로 묶어 맨 위에 둔다. 처음엔 접혀 있고 누르면 펼친다.
  const nBridges = bridgeRows.filter((d) => Number(d.depth ?? 0) === 0).length;
  let bridgesHtml = "";
  if (nBridges) {
    const bOpen = exp().has(bridgeKey(n.path));
    bridgesHtml = `<button class="lnb-fold lnb-bfold" type="button" role="treeitem" aria-level="${level + 1}"
        data-bfold="${esc(n.path)}" aria-expanded="${bOpen}">
        <span class="lnb-caret${bOpen ? " open" : ""}" aria-hidden="true">›</span>
        <span class="lnb-gmark" aria-hidden="true" style="${tintStyle(n.path).slice(1)}">${st.gsrc ? st.gsrc.mark("bridge", n.path) : ""}</span>
        <span class="lnb-fname">${esc(COPY.ui.lnb.bridgeItems(nBridges))}</span></button>`
      + (bOpen ? `<div class="lnb-docs" role="group">${bridgeRows.map((d) => gRow({ ...d, depth: Number(d.depth ?? 0) + 1 })).join("")}</div>` : "");
  }
  const own = docs.map((d) => st.gsrc ? gRow(d) : docRow(d, n.path)).join("")
    // 문서가 없는 주제 — 그렇다고 목록에서 지우면 이름을 바꾸거나 문서를 붙일
    // 진입점이 사라진다. 빈 채로 두되 빈 이유를 말한다.
    // (자식 가지가 있으면 비어 보이지 않으므로 그 문구를 내지 않는다.)
    || (n.kids.length || bridgeRows.length ? "" : `<p class="lnb-none hint">${esc(COPY.ui.home.topicEmpty)}</p>`);
  // 하위 주제가 있는 줄은 자기 문서를 **한 줄로 접어 둔다**: 기본 펼침이 주제를
  // 여는데, 그 줄들의 문서까지 쏟으면 주제의 짜임이 문서 더미에 묻힌다. 하위 주제가 없는
  // 줄은 펴면 곧장 문서다.
  let ownHtml = own;
  if (foldsOwnRows(n) && docs.length) {
    const ownOpen = exp().has(ownKey(n.path));
    ownHtml = `<button class="lnb-fold" type="button" role="treeitem" aria-level="${level + 1}"
        data-fold="${esc(n.path)}" aria-expanded="${ownOpen}">
        <span class="lnb-caret${ownOpen ? " open" : ""}" aria-hidden="true">›</span>
        <span class="lnb-fname">${esc(COPY.ui.lnb.ownItems(docs.length))}</span></button>`
      + (ownOpen ? `<div class="lnb-docs" role="group">${own}</div>` : "");
  }
  return head + `<div class="lnb-docs" role="group">${bridgesHtml}${kids}${ownHtml}</div>`;
}

/** 스크롤 밖의 머리 — 지금 몇 주제를 보고 있는지. 인자가 없으면 비운다(아직
 *  못 세었거나 셀 것이 없는 상태에서 `0` 을 내면 "주제가 없다"로 읽힌다). */
/** 머리 + 의 이름. Agent 레일에서는 새 역할 양식을 연다. */
const addTip = (): string => (st.railKey === "agent" ? COPY.author.role.newTip : COPY.author.addTip);
export function paintHead(n?: number, label?: string): void {
  const h = $("lnb-head");
  if (!h) return;
  // `+` — 트리 머리의 만들기(문서 쓰기 · 노드 만들기 · 파일 가져오기). 지금 고른 주제가
  // 있으면 그 주제로 채운다(authoring.openCreateMenu).
  h.innerHTML = n === undefined ? ""
    : `<div class="lnb-sec"><span class="lnb-sec-label">${esc(label || COPY.ui.lnb.documents)}</span>`
      + `<span class="lnb-sec-end${isCommunitySpace(cfg.team) || st.railKey === "agent" ? " no-add" : ""}"><span class="lnb-gcount">${n || "·"}</span>`
      // 커뮤니티 스페이스는 읽기 전용이라 만들기 `+` 를 두지 않는다.
      + (isCommunitySpace(cfg.team) || st.railKey === "agent" ? "" : `<button class="rail-x lnb-add" type="button" aria-haspopup="${st.railKey === "agent" ? "dialog" : "menu"}"
           aria-label="${esc(addTip())}" data-tip="${esc(addTip())}">+</button>`) + `</span></div>`;
}

/** 주제 스코프 칩 — 고른 주제가 없으면 빈 문자열. */
export function scopeChip(): string {
  if (!st.picked.size) return "";
  return `<div class="lnb-scope">
      <span>${esc(COPY.ui.lnb.scoped(st.picked.size))}</span>
      <button class="lnb-scope-clear" type="button">${esc(COPY.ui.lnb.scopedClear)}</button>
    </div>`;
}

export function gnodeRow(d: LnbGraphRow, domain: string): string {
  const depth = Math.min(Math.max(d.depth, 0), 8), ink = Math.min(depth, 2), on = st.gActive === d.id;
  return `<button class="lnb-doc lnb-gnode${on ? " on" : ""}" role="treeitem" aria-level="${depth + 2}"
    data-gid="${esc(d.id)}" data-dom="${esc(domain)}" title="${esc(d.title)}"
    style="--lvl:${depth};--ink:${ink}${tintStyle(domain)}">
    <span class="lnb-gmark" aria-hidden="true">${st.gsrc ? st.gsrc.mark(d.kind, domain) : ""}</span><span class="lnb-dtitle">${esc(d.title)}</span>
  </button>`;
}
