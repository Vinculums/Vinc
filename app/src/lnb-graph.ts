import type { NavGroup } from "./api";
import { COPY } from "./copy";
import { esc } from "./components";
import { groupRows, nestGroups, paintHead } from "./lnb-docs";
import { $, st } from "./lnb-state";
import { applyDefaults } from "./lnb-tree";

/** 그래프 탭의 트리 원천. 문서 트리와 **같은 그림**(주제 마디 · 접기 규칙 · 줄 모양)으로 그래프의 노드를 나열한다:
 *  주제 아래에 그 주제의 개념·문서·결정(뜻의 층, depth 0)과 거기 붙은 기록·사건·결정(depth 1).
 *  거르개(LNB 검색 · 시간 · 범례의 종류)는 나침반이 쥐고, 바뀌면 `repaintLnbGraph` 로 다시 그리게 한다.
 *  펼침 상태는 문서 트리와 따로 둔다(한쪽에서 접은 것이 다른 쪽을 접지 않게). 그래프를 떠나면 null. */
export type LnbGraphRow = { id: string; title: string; kind: string; depth: number };
export type LnbGraphSource = {
  groups: () => { domain: string; rows: LnbGraphRow[] }[];
  open: (id: string) => void;
  mark: (kind: string, domain: string) => string;
  dock?: HTMLElement;
};

/** 나침반의 가운데가 바뀔 때. 다시 그리지 않고 켜진 줄만 옮긴다(트리가 크면 다시 그리기가 비싸다). */
export function setLnbGraphActive(id: string) {
  st.gActive = id;
  const body = $("lnb-body");
  if (!body || !st.gsrc) return;
  body.querySelectorAll(".lnb-gnode.on, .lnb-group.on").forEach((b) => b.classList.remove("on"));
  if (id.startsWith("topic:")) body.querySelector(`.lnb-group[data-group="${CSS.escape(id.slice(6))}"]`)?.classList.add("on");
  else body.querySelector(`.lnb-gnode[data-gid="${CSS.escape(id)}"]`)?.classList.add("on");
}

/** 그래프 노드 트리. 문서 트리의 `nestGroups` · `groupRows` 를 그대로 쓴다(그래서 생김새와 접기 규칙이 같다).
 *  줄만 다르다: 문서 줄 대신 노드 줄(`gnodeRow`, 도트 자리에 종류 모양). 고정(핀) 판이 있으면 트리 위에 세운다. */
export function paintGraphTree(body: HTMLElement): void {
  const src = st.gsrc!;
  const gs = src.groups();
  const groups: NavGroup[] = gs.map((g) => ({ domain: g.domain,
    docs: g.rows.map((r) => ({ doc_id: r.id, title: r.title, node_id: r.id, depth: r.depth, kind: r.kind })) } as unknown as NavGroup));
  paintHead(groups.length, COPY.ui.lnb.documents);
  const tops = nestGroups(groups);
  applyDefaults(tops, st.gExpanded, st.gSeen);   // 문서 트리와 같은 기본: 맨 위 주제 아래 영역(가장 상위 도메인)까지만 보인다
  const tree = document.createElement("div");
  tree.className = "lnb-tree";
  tree.setAttribute("role", "tree");
  tree.innerHTML = tops.length ? tops.map((n) => groupRows(n)).join("") : `<p class="lnb-none hint">${esc(COPY.ui.lnb.empty)}</p>`;
  const kids: HTMLElement[] = [];
  if (src.dock) kids.push(src.dock);
  kids.push(tree);
  body.replaceChildren(...kids);
}
