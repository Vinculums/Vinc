import { agentRoster } from "./agent-roster";
import { esc } from "./components";
import { COPY } from "./copy";
import { st, tintStyle } from "./lnb-state";
import { ownKey } from "./lnb-tree";

/** Agent 레일의 트리. 보드(agent-map)와 같은 구조다: Agent(Maker · Checker) → 그 Agent 가 읽는 절차, 그리고 정책 묶음.
 *  물러난 직함 역할은 싣지 않는다. Agent 가 없는 스페이스(절차가 붙은 역할이 없다)는 역할을 한 줄씩 나열한다.
 *  줄을 누르면 보드가 그것을 고른다(절차는 보드 옆 칸에서 바로 고친다). 근거: [[vinc.canon.terminology §3.2]] */
export type AgentMapLite = { agents: { id: string; title: string; domain: string }[];
  procedures: { id: string; title: string; agent: string; domain: string }[]; policies: { id: string; title: string; domain: string }[] };
export const AGENT_POL = "agent-policies";
export function paintAgentTree(): string {
  const L = COPY.ui.lnb;
  if (!st.agentMap) {
    const roles = agentRoster || [];
    if (!roles.length) return `<div class="lnb-agent-empty"><p class="lnb-none hint">${esc(L.agentsEmpty)}</p>
      <button class="btn btn-primary lnb-new-role" type="button">${esc(COPY.author.role.title)}</button></div>`;
    return `<div class="lnb-tree" role="tree">${roles.map((r) => { const on = st.active.nodeId === r.id;
      return `<button class="lnb-doc lnb-node lnb-agent-role${on ? " on" : ""}" role="treeitem" aria-level="1" data-nodeid="${esc(r.id)}" data-dom="${esc(r.domain)}"
        style="--lvl:0;--ink:0${tintStyle(r.domain)}"><span class="lnb-dot" aria-hidden="true"></span><span class="lnb-dtitle">${esc(r.title)}</span></button>`; }).join("")}</div>`;
  }
  const tintA = (agentId: string) => { const c = agentId === AGENT_POL ? st.polCol : st.agentCol.get(agentId); return c ? `;--tint:${c}` : ""; };
  const row = (kind: string, id: string, title: string, agentId: string) => { const on = st.agentPick === id;
    return `<button class="lnb-doc lnb-arow${on ? " on" : ""}" role="treeitem" aria-level="2" data-apick="${kind}" data-id="${esc(id)}" title="${esc(title)}"
      style="--lvl:1;--ink:0${tintA(agentId)}"><span class="${kind === "policy" ? "lnb-pdot" : "lnb-dot"}" aria-hidden="true"></span><span class="lnb-dtitle">${esc(title)}</span></button>`; };
  let h = `<div class="lnb-tree" role="tree">`;
  // 만드는 쪽이 먼저(보드도 Maker 가 왼쪽이다). 그 밖의 Agent 는 이름순
  const rank = (a: { title: string }) => { const i = ["maker", "checker"].indexOf(a.title.toLowerCase()); return i < 0 ? 9 : i; };
  for (const a of [...st.agentMap.agents].sort((x, y) => rank(x) - rank(y) || x.title.localeCompare(y.title))) {
    const key = `agent:${a.id}`, open = st.expanded.has(key), on = st.agentPick === a.id, procs = st.agentMap.procedures.filter((p) => p.agent === a.id);
    h += `<div class="lnb-grow${on ? " on" : ""}"><button class="lnb-group${on ? " on" : ""}" role="treeitem" aria-level="1" data-group="${esc(key)}" data-apick="agent" data-id="${esc(a.id)}"
        aria-expanded="${open}" style="${tintA(a.id).slice(1)}"><span class="lnb-caret${open ? " open" : ""}" aria-hidden="true">›</span>
        <span class="lnb-adot" aria-hidden="true"></span><span class="lnb-gname">${esc(a.title)}</span><span class="lnb-gcount">${procs.length}</span></button></div>`;
    if (open) h += `<div class="lnb-docs" role="group">${procs.map((p) => row("procedure", p.id, p.title, a.id)).join("")}</div>`;
  }
  if (st.agentMap.policies.length) {
    const open = st.expanded.has(ownKey(AGENT_POL));
    h += `<div class="lnb-grow"><button class="lnb-group" type="button" role="treeitem" aria-level="1" data-fold="${AGENT_POL}" aria-expanded="${open}" style="${tintA(AGENT_POL).slice(1)}">
        <span class="lnb-caret${open ? " open" : ""}" aria-hidden="true">›</span><span class="lnb-pdot lnb-pmark" aria-hidden="true"></span><span class="lnb-gname">${esc(L.agentPolicies)}</span><span class="lnb-gcount">${st.agentMap.policies.length}</span></button></div>`
      + (open ? `<div class="lnb-docs" role="group">${st.agentMap.policies.map((q) => row("policy", q.id, q.title.replace(/^Policy:\s*/, ""), AGENT_POL)).join("")}</div>` : "");
  }
  return h + `</div>`;
}
