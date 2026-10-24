/** Agents 보드 — Agent 레일이 여는 첫 화면(`#home/agents`).
 *
 *  역할을 팀별로 묶어 카드로 보여 준다. 카드 하나가 역할 하나다: 직무명 · 팀에서 맡는 자리 ·
 *  하는 일 · 앞뒤 역할(받음 · 넘김), 그리고 할 일 셋(프롬프트 복사 · 고치기 · 그래프에서 보기).
 *  팀 머리에서 그 팀에 역할을 더하고, 보드 머리에서 새 역할을 만든다. 고치기와 만들기는 같은
 *  역할 시트(role-composer.ts)이고, 저장해도 이 보드에 머문다.
 *
 *  팀은 따로 있는 노드가 아니라 역할의 `props.team` 값이다. 그래서 팀을 만든다는 것은 역할에
 *  새 팀 이름을 주는 것이고, 마지막 역할이 빠지면 그 팀도 보드에서 사라진다.
 *  근거: [[vinc.canon.agent-roles]] */
import { api, cfg, isCommunitySpace, spaceName } from "../api";
import { COPY } from "../copy";
import { esc } from "../components";
import { icon } from "../icons";
import { setTopbarLead } from "../topbar";
import { buildAgentRoster, teamRank, setLnbActive, showAgentRail, type AgentRole } from "../lnb";
import { absoluteViewUrl } from "../view-url";
import { loadPrefs } from "../view-prefs";
import { loaderRows } from "../vinc-loaders.js";

/** 팀 안의 자리 순서 — 리드 · 조율 · 구성원(레일 목록과 같다). */
const SEAT_RANK: Record<string, number> = { lead: 0, coordinator: 1 };

let offChanged: (() => void) | null = null;

/** 보드를 떠날 때 부른다(레일 갱신 신호의 구독을 푼다). */
export function leaveAgentsBoard(): void {
  offChanged?.();
  offChanged = null;
}

export async function renderAgentsBoard(el: HTMLElement, live: () => boolean): Promise<void> {
  const H = COPY.ui.home;
  leaveAgentsBoard();
  setTopbarLead(`<span class="tb-lead-name">${esc(H.agentsTitle)}</span>`);
  setLnbActive({ domains: [] });
  showAgentRail();
  if (!el.querySelector(".agents")) {
    el.innerHTML = `<div class="home agents"><div class="home-read">${loaderRows(COPY.ui.common.loading, { count: 6 })}</div></div>`;
  }
  const paint = async () => {
    let roles: AgentRole[];
    try { roles = buildAgentRoster(await api.roleRoster(cfg.owner)); } catch {
      if (!live()) return;
      el.innerHTML = `<div class="home agents"><p class="hint">${esc(H.agentsLoadFail)}</p>
        <button class="btn btn-quiet" type="button" id="agents-retry">${esc(H.retry)}</button></div>`;
      el.querySelector("#agents-retry")?.addEventListener("click", () => void paint());
      return;
    }
    if (!live()) return;
    draw(el, roles);
  };
  // 역할 시트가 저장하면 레일이 다시 읽고 이 신호를 낸다 — 보드도 그때 다시 그린다.
  const onChanged = () => { if (live()) void paint(); else leaveAgentsBoard(); };
  window.addEventListener("vinc:agents-changed", onChanged);
  offChanged = () => window.removeEventListener("vinc:agents-changed", onChanged);
  await paint();
}

function draw(el: HTMLElement, roles: AgentRole[]): void {
  const H = COPY.ui.home;
  const L = COPY.ui.lnb;
  const readOnly = isCommunitySpace(cfg.team);
  const teams = new Map<string, AgentRole[]>();
  for (const r of roles) {
    const k = r.team || "";
    if (!teams.has(k)) teams.set(k, []);
    teams.get(k)!.push(r);
  }
  // 팀 순서: 정한 순서의 팀 → 나머지 팀 이름순 → 팀 없는 역할은 끝.
  const order = [...teams.keys()].sort((a, b) => {
    if (!a || !b) return a ? -1 : b ? 1 : 0;
    return teamRank(a) - teamRank(b) || a.localeCompare(b);
  });
  const named = order.filter(Boolean);
  const rel = (label: string, names: string[]) => names.length
    ? `<p class="agent-rel"><span class="agent-rel-k">${esc(label)}</span> ${esc(names.join(" · "))}</p>` : "";
  const card = (r: AgentRole) => `
    <article class="agent-card" data-role="${esc(r.id)}">
      <header class="agent-card-top">
        <h3 class="agent-name">${esc(r.title)}</h3>
        ${r.teamRole && r.team ? `<span class="agent-seat agent-seat-${esc(r.teamRole)}">${
          esc(COPY.author.role.teamRoles[r.teamRole] || r.teamRole)}</span>` : ""}
      </header>
      <p class="agent-mission">${esc(r.mission || H.agentsNoMission)}</p>
      ${r.rel.from.length || r.rel.then.length || r.rel.beside.length ? `<div class="agent-rels">${
        rel(L.agentFrom, r.rel.from)}${rel(L.agentThen, r.rel.then)}${rel(L.agentBeside, r.rel.beside)}</div>` : ""}
      <footer class="agent-card-acts">
        <button class="btn btn-quiet agent-act" type="button" data-act="copy"><span>${esc(H.agentsCopy)}</span></button>
        ${readOnly ? "" : `<button class="btn btn-quiet agent-act" type="button" data-act="edit"><span>${esc(H.agentsEdit)}</span></button>`}
        <button class="btn btn-quiet agent-act agent-act-icon" type="button" data-act="graph"
                aria-label="${esc(H.agentsGraph)}" data-tip="${esc(H.agentsGraph)}">${icon("graph", 14)}</button>
      </footer>
    </article>`;
  const team = (name: string) => {
    const list = teams.get(name)!.slice().sort((a, b) =>
      (SEAT_RANK[a.teamRole] ?? 2) - (SEAT_RANK[b.teamRole] ?? 2) || a.title.localeCompare(b.title));
    return `<section class="agents-team" data-team="${esc(name)}">
      <header class="agents-team-head">
        <h2>${esc(name || H.agentsNoTeam)}</h2>
        <span class="home-count">${esc(H.agentsTeamCount(list.length))}</span>
        ${readOnly ? "" : `<button class="btn btn-quiet agent-act agents-add" type="button" data-act="add" data-team="${esc(name)}">${
          esc(H.agentsAddToTeam)}</button>`}
      </header>
      <div class="agents-grid">${list.map(card).join("")}</div>
    </section>`;
  };
  const body = roles.length
    ? order.map(team).join("")
    : `<div class="home-empty"><p>${esc(H.agentsEmpty)}</p>
        ${readOnly ? "" : `<div class="home-acts"><button class="btn btn-primary" type="button" data-act="new">${esc(H.agentsNewRole)}</button></div>`}</div>`;

  el.innerHTML = `<div class="home agents">
    <header class="home-head">
      <h1>${esc(H.agentsTitle)} <span class="home-count">${esc(H.agentsCount(roles.length, named.length))}</span></h1>
      <div class="home-acts">
        ${readOnly ? "" : `<button class="btn btn-primary" type="button" data-act="new"><span>${esc(H.agentsNewRole)}</span></button>`}
      </div>
    </header>
    <p class="hint agents-lead">${esc(H.agentsLead)}</p>
    ${body}
  </div>`;

  const openRole = (o: { id?: string; team?: string }) =>
    void import("../role-composer").then((m) => m.openRoleComposer({ ...o, teams: named }));
  el.querySelectorAll<HTMLElement>("[data-act]").forEach((b) => b.addEventListener("click", (e) => {
    const act = b.dataset.act;
    const id = b.closest<HTMLElement>("[data-role]")?.dataset.role || "";
    if (act === "new") openRole({});
    else if (act === "add") openRole({ team: b.dataset.team || "" });
    else if (act === "edit" && id) openRole({ id });
    else if (act === "graph" && id) location.hash = `#view/read/${encodeURIComponent(id)}`;
    else if (act === "copy" && id) {
      e.stopPropagation();
      // 그래프 카드의 "이 역할로 일해 줘"와 같은 문장 · 같은 주소다(surfaces/view.ts nodeAskItems).
      const url = absoluteViewUrl({ kind: "node", id }, { depth: 1, viewpoint: loadPrefs().view }, spaceName());
      void navigator.clipboard.writeText(`${COPY.ui.viewx.askRoleBody} ${url}`).then(() => {
        const label = b.querySelector("span:last-child");
        if (!label) return;
        label.textContent = H.agentsCopied;
        setTimeout(() => { if (label.isConnected) label.textContent = H.agentsCopy; }, 1600);
      }).catch(() => {});
    }
  }));
  // 카드 본문을 누르면 고치기와 같다(할 일 단추 위의 누름은 위에서 처리한다).
  if (!readOnly) el.querySelectorAll<HTMLElement>(".agent-card").forEach((c) => c.addEventListener("click", (e) => {
    if ((e.target as HTMLElement).closest("button")) return;
    openRole({ id: c.dataset.role || "" });
  }));
}
