/** Agents 보드 — Agent 레일이 여는 첫 화면(`#home/agents`).
 *
 *  역할을 팀별로 묶어 카드로 보여 준다. 카드 하나가 역할 하나다: 직무명 · 팀에서 맡는 자리 ·
 *  하는 일 · 앞뒤 역할(받음 · 넘김), 그리고 할 일(프롬프트 복사 · 고치기 · 더 보기 · 그래프에서 보기).
 *  카드를 누르거나 고치기를 누르면 역할 편집 면(`#home/agents/<id>`, role-composer.ts)으로 간다.
 *
 *  팀 꾸리기도 이 보드에서 한다: 새 팀 · 이름 바꾸기 · 해체, 카드를 다른 팀 단으로 끌어 옮기기
 *  (끌기가 어려우면 카드의 더 보기 메뉴), 팀에서 맡는 자리 정하기.
 *
 *  팀은 따로 있는 노드가 아니라 역할의 `props.team` 값이다. 그래서 팀 이름 바꾸기와 해체는 그
 *  팀 역할들의 `team` 을 한 번의 쓰기로 고치는 것이고(`props_patch` + 역할마다 `expected_rev`, 하나라도
 *  판이 다르면 아무것도 안 바뀐다), 마지막 역할이 빠지면 그 팀도 보드에서 사라진다. 아직 역할이
 *  없는 새 팀은 이 화면에만 있다(역할을 넣는 순간 저장된다).
 *  근거: [[vinc.canon.agent-roles]] */
import { api, cfg, isCommunitySpace } from "../api";
import { COPY } from "../copy";
import { confirmDestructive, esc, openContextMenu, promptText } from "../components";
import { icon } from "../icons";
import { setTopbarLead } from "../topbar";
import { buildAgentRoster, teamRank, setLnbActive, showAgentRail, type AgentRole } from "../lnb";
import { announceRolesChanged, copyRolePrompt, openRoleComposer, TEAM_ROLES } from "../role-composer";
import { loaderRows } from "../vinc-loaders.js";
import { mountAgentWrites } from "../agent-writes";
import { loadAgentMap, updateAgentMap, type MapData } from "./agent-map";

/** 팀 안의 자리 순서 — 리드 · 조율 · 구성원(레일 목록과 같다). */
const SEAT_RANK: Record<string, number> = { lead: 0, coordinator: 1 };

let offChanged: (() => void) | null = null;
/** 역할이 아직 없는 새 팀(이 화면에만 있다). 역할이 들어가면 로스터가 그 팀을 갖게 된다. */
const pendingTeams = new Set<string>();
/** 다시 그린 뒤에도 남길 한 줄 알림(저장 실패 · 판 충돌). */
let notice = "";

/** 보드를 떠날 때 부른다(레일 갱신 신호의 구독을 푼다). */
function leaveAgentsBoard(): void {
  offChanged?.();
  offChanged = null;
}

export async function renderAgentsBoard(el: HTMLElement, live: () => boolean): Promise<void> {
  const H = COPY.ui.home;
  leaveAgentsBoard();
  setTopbarLead(`<span class="tb-lead-name">${esc(H.agentsTitle)}</span>`);
  setLnbActive({ domains: [] });
  showAgentRail();
  if (!el.querySelector(".agents-board")) {
    el.innerHTML = `<div class="home agents agents-board"><div class="home-read">${loaderRows(COPY.ui.common.loading, { count: 6 })}</div></div>`;
  }
  const paint = async () => {
    let roles: AgentRole[];
    try { roles = buildAgentRoster(await api.roleRoster(cfg.owner)); } catch {
      if (!live()) return;
      el.innerHTML = `<div class="home agents agents-board"><p class="hint">${esc(H.agentsLoadFail)}</p>
        <button class="btn btn-quiet" type="button" id="agents-retry">${esc(H.retry)}</button></div>`;
      el.querySelector("#agents-retry")?.addEventListener("click", () => void paint());
      return;
    }
    if (!live()) return;
    // Agent 에 절차가 있는 스페이스는 관계 지도가 기본 면이다. 직함 역할이 남았으면 지도 아래에서 펼친다.
    // 근거: [[vinc.canon.terminology §3.2]]
    let map: MapData | null = null;
    try { map = await loadAgentMap(roles); } catch { map = null; }
    if (!live()) return;
    if (map) updateAgentMap(el, map, (host) => draw(host, map!.legacy, () => void paint()));
    else draw(el, roles, () => void paint());
  };
  // 편집 면이나 이 보드가 저장하면 레일이 다시 읽고 이 신호를 낸다 — 보드도 그때 다시 그린다.
  // 떠난 보드의 구독은 **자기 자신만** 푼다. 공용 해제(leaveAgentsBoard)를 부르면, 그 사이 새로
  // 그린 보드의 구독까지 풀려 저장한 뒤에도 보드가 옛 모습으로 남는다.
  const off = () => window.removeEventListener("vinc:agents-changed", onChanged);
  const onChanged = () => { if (live()) void paint(); else off(); };
  window.addEventListener("vinc:agents-changed", onChanged);
  offChanged = off;
  await paint();
}

type Patch = { id: string; patch: Record<string, unknown> };

/** 역할 여럿의 props 를 한 번에 고친다. 역할마다 지금 판(rev)을 읽어 `expected_rev` 로 싣고,
 *  보낸 키만 바꾸는 `props_patch` 로 보낸다(나머지 계약 키는 그대로 남는다). */
async function patchRoles(changes: Patch[]): Promise<"ok" | "stale" | "fail"> {
  if (!changes.length) return "ok";
  let revs: (number | undefined)[];
  try {
    revs = await Promise.all(changes.map((c) => api.nodeContext(cfg.owner, c.id, 1)
      .then((r: any) => (r?.executed && typeof r.rev === "number" ? r.rev as number : undefined))));
  } catch { return "fail"; }
  if (revs.some((r) => r === undefined)) return "fail";
  let res: any;
  try {
    res = await api.ingestFragment({ nodes: changes.map((c, i) => ({
      id: c.id, type: "concept", props_patch: c.patch, expected_rev: revs[i] })), edges: [] });
  } catch (e: any) { res = { executed: false, error: String(e?.message || e) }; }
  if (res?.executed === false) return /rev|revision|conflict/i.test(String(res.error || "")) ? "stale" : "fail";
  return (res?.merged?.nodes?.matched || 0) >= changes.length ? "ok" : "fail";
}

function draw(el: HTMLElement, roles: AgentRole[], repaint: () => void): void {
  const H = COPY.ui.home;
  const L = COPY.ui.lnb;
  const R = COPY.author.role;
  const readOnly = isCommunitySpace(cfg.team);
  const teams = new Map<string, AgentRole[]>();
  for (const r of roles) {
    const k = r.team || "";
    if (!teams.has(k)) teams.set(k, []);
    teams.get(k)!.push(r);
  }
  for (const t of [...pendingTeams]) {
    if (teams.has(t)) pendingTeams.delete(t);   // 역할이 들어가 저장된 팀이 됐다
    else teams.set(t, []);
  }
  // 팀 순서: 정한 순서의 팀 → 나머지 팀 이름순 → 팀 없는 역할은 끝.
  const order = [...teams.keys()].filter(Boolean).sort((a, b) =>
    teamRank(a) - teamRank(b) || a.localeCompare(b));
  const named = order.filter((t) => teams.get(t)!.length);
  const loose = teams.get("") || [];
  const rel = (label: string, names: string[]) => names.length
    ? `<p class="agent-rel"><span class="agent-rel-k">${esc(label)}</span> ${esc(names.join(" · "))}</p>` : "";
  const card = (r: AgentRole) => `
    <article class="agent-card" data-role="${esc(r.id)}"${readOnly ? "" : ` draggable="true"`}>
      <header class="agent-card-top">
        <h3 class="agent-name">${esc(r.title)}</h3>
        ${r.teamRole && r.team ? `<span class="agent-seat agent-seat-${esc(r.teamRole)}">${
          esc(R.teamRoles[r.teamRole] || r.teamRole)}</span>` : ""}
      </header>
      <p class="agent-mission">${esc(r.mission || H.agentsNoMission)}</p>
      ${r.rel.from.length || r.rel.then.length || r.rel.beside.length ? `<div class="agent-rels">${
        rel(L.agentFrom, r.rel.from)}${rel(L.agentThen, r.rel.then)}${rel(L.agentBeside, r.rel.beside)}</div>` : ""}
      <footer class="agent-card-acts">
        <button class="btn btn-quiet agent-act" type="button" data-act="copy"><span>${esc(H.agentsCopy)}</span></button>
        ${readOnly ? "" : `<button class="btn btn-quiet agent-act" type="button" data-act="edit"><span>${esc(H.agentsEdit)}</span></button>
        <button class="btn btn-quiet agent-act agent-act-icon agent-act-more" type="button" data-act="more"
                aria-label="${esc(H.agentsMore)}" data-tip="${esc(H.agentsMore)}" aria-haspopup="menu">${icon("more", 14)}</button>`}
        <button class="btn btn-quiet agent-act agent-act-icon" type="button" data-act="graph"
                aria-label="${esc(H.agentsGraph)}" data-tip="${esc(H.agentsGraph)}">${icon("graph", 14)}</button>
      </footer>
    </article>`;
  const section = (name: string) => {
    const list = (teams.get(name) || []).slice().sort((a, b) =>
      (SEAT_RANK[a.teamRole] ?? 2) - (SEAT_RANK[b.teamRole] ?? 2) || a.title.localeCompare(b.title));
    const acts = readOnly ? "" : `<div class="agents-team-acts">
        <button class="btn btn-quiet agent-act" type="button" data-act="add" data-team="${esc(name)}">${esc(H.agentsAddToTeam)}</button>
        ${name ? `<button class="btn btn-quiet agent-act agent-act-icon" type="button" data-act="team-more" data-team="${esc(name)}"
                aria-label="${esc(H.agentsTeamMenu(name))}" data-tip="${esc(H.agentsTeamMenu(name))}" aria-haspopup="menu">${icon("more", 14)}</button>` : ""}
      </div>`;
    return `<section class="agents-team${list.length ? "" : " agents-team-empty"}" data-team="${esc(name)}">
      <header class="agents-team-head">
        <h2>${esc(name || H.agentsNoTeam)}</h2>
        <span class="home-count">${esc(H.agentsTeamCount(list.length))}</span>
        ${acts}
      </header>
      ${list.length ? `<div class="agents-grid">${list.map(card).join("")}</div>`
        : `<p class="agents-drop-hint hint">${esc(name ? H.agentsTeamEmpty : H.agentsDropOut)}</p>`}
    </section>`;
  };
  // 팀 없는 단은 역할이 없어도 둔다(끌어 놓아 팀에서 빼는 자리). 비어 있으면 끌 때만 보인다.
  const body = roles.length || pendingTeams.size
    ? order.map(section).join("") + section("")
    : `<div class="home-empty"><p>${esc(H.agentsEmpty)}</p>
        ${readOnly ? "" : `<div class="home-acts"><button class="btn btn-primary" type="button" data-act="new">${esc(H.agentsNewRole)}</button></div>`}</div>`;

  el.innerHTML = `<div class="home agents agents-board${loose.length ? "" : " agents-no-loose"}">
    <header class="home-head">
      <h1>${esc(H.agentsTitle)} <span class="home-count">${esc(H.agentsCount(roles.length, named.length))}</span></h1>
      <div class="home-acts">
        ${readOnly ? "" : `<button class="btn" type="button" data-act="new-team"><span>${esc(H.agentsNewTeam)}</span></button>
        <button class="btn btn-primary" type="button" data-act="new"><span>${esc(H.agentsNewRole)}</span></button>`}
      </div>
    </header>
    <p class="hint agents-lead">${esc(readOnly ? H.agentsLeadRead : H.agentsLead)}</p>
    <p class="agents-status" role="status">${esc(notice)}</p>
    ${body}
    <section class="agent-writes" id="agent-writes"></section>
  </div>`;
  notice = "";
  void mountAgentWrites(el.querySelector<HTMLElement>("#agent-writes")!,
    () => el.isConnected && !!el.querySelector("#agent-writes"));

  const board = el.querySelector<HTMLElement>(".agents-board")!;
  const status = (s: string) => { board.querySelector(".agents-status")!.textContent = s; };
  const run = async (changes: Patch[]) => {
    status(H.agentsSaving);
    board.classList.add("agents-busy");
    const out = await patchRoles(changes);
    board.classList.remove("agents-busy");
    if (out === "ok") { status(""); announceRolesChanged(); return; }
    notice = out === "stale" ? H.agentsChanged : H.agentsMoveFail;
    repaint();
  };
  const roleById = new Map(roles.map((r) => [r.id, r]));
  const teamTaken = (name: string) => teams.has(name);

  /** 역할 하나를 다른 팀으로(빈 이름 = 팀에서 빼기). 자리는 새 팀에서 다시 정하므로 지운다. */
  const moveRole = (id: string, to: string) => {
    const r = roleById.get(id);
    if (!r || r.team === to) return;
    pendingTeams.delete(to);
    void run([{ id, patch: { team: to || null, team_role: null } }]);
  };
  const askTeamName = async (initial = ""): Promise<string | null> => {
    const name = (await promptText(H.agentsTeamName, initial, H.agentsTeamCreate, COPY.ui.common.cancel))?.trim();
    if (!name) return null;
    if (teamTaken(name)) { status(H.agentsTeamTaken); return null; }
    return name;
  };
  const renameTeam = async (from: string) => {
    const name = (await promptText(H.agentsRenameTitle(from), from, H.agentsRename, COPY.ui.common.cancel))?.trim();
    if (!name || name === from) return;
    if (teamTaken(name)) { status(H.agentsTeamTaken); return; }
    const list = teams.get(from) || [];
    if (!list.length) { pendingTeams.delete(from); pendingTeams.add(name); repaint(); return; }
    void run(list.map((r) => ({ id: r.id, patch: { team: name } })));
  };
  const dissolveTeam = async (name: string) => {
    const list = teams.get(name) || [];
    if (!list.length) { pendingTeams.delete(name); repaint(); return; }
    const ok = await confirmDestructive(esc(H.agentsDissolveAsk(name, list.length)), esc(H.agentsDissolve),
                                        esc(COPY.ui.common.cancel));
    if (ok) void run(list.map((r) => ({ id: r.id, patch: { team: null, team_role: null } })));
  };
  const roleMenu = (b: HTMLElement, id: string) => {
    const r = roleById.get(id);
    if (!r) return;
    const rect = b.getBoundingClientRect();
    const items: { label: string; run: () => void; danger?: boolean }[] = [];
    if (r.team) for (const seat of TEAM_ROLES) if (seat !== (r.teamRole || "member")) {
      items.push({ label: H.agentsSeatAs(R.teamRoles[seat]),
                   run: () => void run([{ id, patch: { team_role: seat === "member" ? null : seat } }]) });
    }
    for (const t of order) if (t !== r.team) items.push({ label: H.agentsMoveTo(t), run: () => moveRole(id, t) });
    items.push({ label: H.agentsMoveToNew, run: () => void askTeamName().then((t) => { if (t) moveRole(id, t); }) });
    if (r.team) items.push({ label: H.agentsMoveOut, run: () => moveRole(id, "") });
    openContextMenu(rect.left, rect.bottom + 4, items);
  };
  const teamMenu = (b: HTMLElement, name: string) => {
    const rect = b.getBoundingClientRect();
    openContextMenu(rect.left, rect.bottom + 4, [
      { label: H.agentsRename, run: () => void renameTeam(name) },
      { label: H.agentsDissolve, run: () => void dissolveTeam(name), danger: true },
    ]);
  };

  board.querySelectorAll<HTMLElement>("[data-act]").forEach((b) => b.addEventListener("click", (e) => {
    const act = b.dataset.act;
    const id = b.closest<HTMLElement>("[data-role]")?.dataset.role || "";
    e.stopPropagation();
    if (act === "new") openRoleComposer({});
    else if (act === "new-team") void askTeamName().then((t) => { if (t) { pendingTeams.add(t); repaint(); } });
    else if (act === "add") openRoleComposer({ team: b.dataset.team || "" });
    else if (act === "team-more") teamMenu(b, b.dataset.team || "");
    else if (act === "edit" && id) openRoleComposer({ id });
    else if (act === "more" && id) roleMenu(b, id);
    else if (act === "graph" && id) location.hash = `#view/read/${encodeURIComponent(id)}`;
    else if (act === "copy" && id) {
      void copyRolePrompt(id).then(() => {
        const label = b.querySelector("span:last-child");
        if (!label) return;
        label.textContent = H.agentsCopied;
        setTimeout(() => { if (label.isConnected) label.textContent = H.agentsCopy; }, 1600);
      }).catch(() => {});
    }
  }));
  if (readOnly) return;
  // 카드 본문을 누르면 고치기와 같다(할 일 단추 위의 누름은 위에서 처리한다).
  board.querySelectorAll<HTMLElement>(".agent-card").forEach((c) => c.addEventListener("click", (e) => {
    if ((e.target as HTMLElement).closest("button, a")) return;
    openRoleComposer({ id: c.dataset.role || "" });
  }));

  // 끌어 옮기기: 카드를 다른 팀 단에 놓으면 그 팀으로 간다. 팀 없는 단에 놓으면 팀에서 뺀다.
  let dragId = "";
  board.addEventListener("dragstart", (e) => {
    const c = (e.target as HTMLElement).closest<HTMLElement>(".agent-card");
    if (!c || !e.dataTransfer) return;
    dragId = c.dataset.role || "";
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", dragId);
    c.classList.add("agent-dragging");
    board.classList.add("agents-dragging");
  });
  board.addEventListener("dragend", () => {
    dragId = "";
    board.classList.remove("agents-dragging");
    board.querySelectorAll(".agent-dragging, .agents-drop-on").forEach((x) =>
      x.classList.remove("agent-dragging", "agents-drop-on"));
  });
  board.querySelectorAll<HTMLElement>(".agents-team").forEach((sec) => {
    const team = sec.dataset.team || "";
    sec.addEventListener("dragover", (e) => {
      if (!dragId || roleById.get(dragId)?.team === team) return;
      e.preventDefault();
      if (e.dataTransfer) e.dataTransfer.dropEffect = "move";
      sec.classList.add("agents-drop-on");
    });
    sec.addEventListener("dragleave", (e) => {
      if (!sec.contains(e.relatedTarget as Node | null)) sec.classList.remove("agents-drop-on");
    });
    sec.addEventListener("drop", (e) => {
      e.preventDefault();
      sec.classList.remove("agents-drop-on");
      const id = dragId || e.dataTransfer?.getData("text/plain") || "";
      if (id) moveRole(id, team);
    });
  });
}
