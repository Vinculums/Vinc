/** Agent 역할 편집 면 — 새로 만들기와 고치기를 한 면이 한다(`#home/agents/<id>` · `#home/agents/new`).
 *
 *  역할은 개념 노드 하나다: id `concept:agent-<이름>`, 주제 `agent`, 계약은 props
 *  (`mission`·`tier`·`rung`·`never`·`person_only`), 팀은 `team`·`team_role`. 일반 노드 컴포저로도
 *  만들 수는 있지만 id 를 제목에서 지어 한국어 직무명이면 역할 이름 규칙(영문 소문자·숫자·하이픈)을
 *  못 맞추고, 계약 키를 만든 뒤 속성 편집기에서 하나씩 더해야 했다. 여기서는 이름을 따로 받고
 *  계약을 한 화면에서 채운다. 서버의 역할 계약 경고(`api/role_contract.py`)는 저장 뒤 그대로
 *  보여 준다 — 거절이 아니라 알림이라 저장은 된다.
 *
 *  시트가 아니라 Agents 보드 안의 한 면이다. 옆 칸은 보드 카드가 어떻게 보일지(입력을 따라
 *  바뀐다)와 앞뒤 역할, 그리고 고치는 역할의 할 일(프롬프트 복사 · 그래프에서 보기)을 둔다.
 *
 *  고치기는 있는 props 위에 이 면의 칸만 덮어 **통째로** 보낸다(props 는 부분 전송이 아니라
 *  교체다). 이 면이 모르는 키는 그대로 남고, 연 뒤 다른 곳에서 바뀌었으면 rev 가 막는다.
 *  고치지 않은 목록 칸은 읽은 값 그대로 보내고(`role-fields.ts`), 태그는 고치기에서 보내지 않는다 —
 *  이 면은 노드의 태그를 읽지 않으므로 보내면 다른 태그를 지운다(안 보내면 서버가 보존한다).
 *  저장한 뒤에는 Agents 보드로 돌아간다 — 그래프로 보내지 않는다.
 *  근거: [[vinc.canon.agent-roles]] · [[decision:agent-roles-live-under-a-top-level-agent-topic-2026-10-02]] */
import { api, cfg, isCommunitySpace, messageOf, spaceName } from "./api";
import { COPY } from "./copy";
import { esc } from "./components";
import { icon } from "./icons";
import { setTopbarLead } from "./topbar";
import { AGENT_TOPICS, AGENTS_BOARD, buildAgentRoster, setLnbActive, showAgentRail,
         type AgentRole } from "./lnb";
import { evalCardsHtml, sharedEvalCardsHtml } from "./role-eval";
import { absoluteViewUrl } from "./view-url";
import { loadPrefs } from "./view-prefs";
import { loaderRows } from "./vinc-loaders.js";
import { BUDGET_KEYS, CAPABILITY_LISTS, OUTPUT_KINDS, actLines, budgetFields, capabilityFields,
         fieldChanged, foldActPolicy, foldBudget, foldCapabilities, foldPersonOnly, isPersonAction,
         linesOf, listLines } from "./role-fields";

/** `api/role_contract.py` 의 TIERS · RUNGS 와 같은 순서. */
export const TIERS = ["orchestrate", "execute", "verify", "deliver", "knowledge"] as const;
export const RUNGS = ["strongest", "strong", "fast", "cheapest"] as const;
/** 팀 안의 자리. 로스터 정렬(`lnb.ts teamRoleRank`)과 같은 값이다. */
export const TEAM_ROLES = ["lead", "coordinator", "member"] as const;
/** 역할 이름 규칙(`api/role_contract._SLUG`). */
export const ROLE_SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

/** 직무명 → 역할 이름 후보. 영문 소문자·숫자만 남긴다(한국어 직무명이면 빈 값이라 사람이 적는다). */
const roleSlugOf = (title: string): string =>
  title.normalize("NFKD").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48)
    .replace(/-+$/, "");
const strOf = (v: unknown): string => (typeof v === "string" ? v : "");

export type RoleSheetOpts = {
  /** 고칠 역할의 id. 없으면 새 역할을 만든다. */
  id?: string;
  /** 새 역할의 팀을 미리 채운다(보드의 "역할 추가"). */
  team?: string;
};

/** 역할 편집 면의 주소. 새 역할은 `new`(팀을 미리 채우면 `new/<팀>`), 고치기는 역할 id 다. */
export function roleHash(opts: RoleSheetOpts = {}): string {
  if (opts.id) return `${AGENTS_BOARD}/${encodeURIComponent(opts.id)}`;
  return `${AGENTS_BOARD}/new${opts.team ? `/${encodeURIComponent(opts.team)}` : ""}`;
}

/** 역할 편집 면으로 간다(예전 시트를 열던 호출부가 그대로 쓴다). */
export function openRoleComposer(opts: RoleSheetOpts = {}): void {
  location.hash = roleHash(opts);
}

/** 그래프 카드의 "이 역할로 일해 줘"와 같은 문장 · 같은 주소를 클립보드에 싣는다
 *  (surfaces/view.ts nodeAskItems). */
export function copyRolePrompt(id: string): Promise<void> {
  const url = absoluteViewUrl({ kind: "node", id }, { depth: 1, viewpoint: loadPrefs().view }, spaceName());
  return navigator.clipboard.writeText(`${COPY.ui.viewx.askRoleBody} ${url}`);
}

/** 역할이 바뀌었다고 알린다 — 레일 목록을 다시 읽는다(보드는 그 뒤의 신호로 다시 그린다). */
export function announceRolesChanged(): void {
  void import("./lnb").then((m) => m.refreshLnb({ quiet: true }));
}

/** `rest` 는 `#home/agents/` 뒤의 조각들이다: `["new"]` · `["new", 팀]` · `[역할 id]`. */
export async function renderRoleEditor(el: HTMLElement, live: () => boolean, rest: string[]): Promise<void> {
  const R = COPY.author.role;
  const A = COPY.author;
  const H = COPY.ui.home;
  const editing = rest[0] !== "new";
  const editId = editing ? rest[0] : "";
  const topic = AGENT_TOPICS[0];
  const readOnly = isCommunitySpace(cfg.team);
  setLnbActive({ domains: [] });
  showAgentRail();
  setTopbarLead(`<nav class="home-crumbs"><a class="home-back" href="${AGENTS_BOARD}">‹ ${esc(H.agentsTitle)}</a></nav>`);
  el.innerHTML = `<div class="home agents role-page">${loaderRows(COPY.ui.common.loading, { count: 6 })}</div>`;

  // 팀 칸의 후보와 앞뒤 역할은 보드와 같은 로스터에서 온다. 못 읽어도 편집은 된다.
  const rosterP = api.roleRoster(cfg.owner).then(buildAgentRoster).catch(() => [] as AgentRole[]);
  let baseProps: Record<string, unknown> = {};
  let baseRev: number | undefined;
  let baseDomain: string = topic;
  let baseTitle = "";
  if (editing) {
    try {
      const r = await api.nodeContext(cfg.owner, editId, 1) as {
        executed: boolean; title?: string; domain?: string; props?: Record<string, unknown>; rev?: number;
      };
      if (!r.executed) throw new Error("not found");
      baseProps = { ...(r.props || {}) };
      baseRev = typeof r.rev === "number" ? r.rev : undefined;
      baseDomain = r.domain || topic;
      baseTitle = r.title || "";
    } catch {
      if (!live()) return;
      el.innerHTML = `<div class="home agents role-page"><p class="error-banner">${esc(R.loadFail)}</p>
        <div class="home-acts"><a class="btn btn-quiet" href="${AGENTS_BOARD}">${esc(R.backToBoard)}</a></div></div>`;
      return;
    }
  }
  const roster = await rosterP;
  if (!live()) return;
  const self = roster.find((r) => r.id === editId);
  const teamList = [...new Set(roster.map((r) => r.team).filter(Boolean))].sort((a, b) => a.localeCompare(b));

  const chips = (name: string, keys: readonly string[], labels: Record<string, string>) =>
    `<div class="seg rc-${name}" role="radiogroup" aria-labelledby="rc-${name}-l">${keys.map((k) =>
      `<button type="button" class="chip" role="radio" aria-checked="false" data-${name}="${k}">${
        esc(labels[k])}</button>`).join("")}</div>`;
  const rel = (label: string, names: string[]) => names.length
    ? `<p class="agent-rel"><span class="agent-rel-k">${esc(label)}</span> ${esc(names.join(" · "))}</p>` : "";
  const L = COPY.ui.lnb;
  const rels = self && (self.rel.from.length || self.rel.then.length || self.rel.beside.length)
    ? `<div class="agent-rels">${rel(L.agentFrom, self.rel.from)}${rel(L.agentThen, self.rel.then)}${
      rel(L.agentBeside, self.rel.beside)}</div>` : `<p class="hint">${esc(R.noRels)}</p>`;

  // 커뮤니티에서 가져간 역할은 공유 버전에 실려 온 카드를 켜기 전부터 보인다.
  const shared = editing ? sharedEvalCardsHtml(baseProps) : "";
  el.innerHTML = `<div class="home agents role-page">
    <header class="home-head">
      <h1>${esc(editing ? (baseTitle || R.editTitle) : R.title)}</h1>
    </header>
    <p class="hint agents-lead">${esc(editing ? R.editSub : R.sub)}</p>
    <div class="home-read-grid role-grid">
      <form class="nc rc role-form" novalidate>
        <section class="role-sec">
          <h2>${esc(R.secRole)}</h2>
          <label class="ei-field"><span>${esc(R.fJob)}</span>
            <input id="rc-job" type="text" autocomplete="off" placeholder="${esc(R.jobPh)}"/></label>
          <label class="ei-field"><span>${esc(R.fSlug)}</span>
            <input id="rc-slug" type="text" autocomplete="off" spellcheck="false" placeholder="release-notes"${editing ? " readonly" : ""}/></label>
          <p class="hint nc-id"><span id="rc-id"></span>${esc(R.topicNote(topic))}</p>
          <label class="ei-field"><span>${esc(R.fMission)}</span>
            <textarea id="rc-mission" rows="3" placeholder="${esc(R.missionPh)}"></textarea></label>
        </section>
        <section class="role-sec">
          <h2>${esc(R.secTeam)}</h2>
          <div class="rc-row">
            <label class="ei-field"><span>${esc(R.fTeam)}</span>
              <input id="rc-team" type="text" autocomplete="off" list="rc-teams" placeholder="${esc(R.teamPh)}"/>
              <datalist id="rc-teams">${teamList.map((t) => `<option value="${esc(t)}"></option>`).join("")}</datalist></label>
            <div class="ei-field"><span id="rc-trole-l">${esc(R.fTeamRole)}</span>${chips("trole", TEAM_ROLES, R.teamRoles)}</div>
          </div>
        </section>
        <section class="role-sec">
          <h2>${esc(R.secWork)}</h2>
          <div class="ei-field"><span id="rc-tier-l">${esc(R.fTier)}</span>${chips("tier", TIERS, R.tiers)}
            <p class="hint" id="rc-tier-hint">${esc(R.tierHint)}</p></div>
          <div class="ei-field"><span id="rc-rung-l">${esc(R.fRung)}</span>${chips("rung", RUNGS, R.rungs)}
            <p class="hint">${esc(R.rungHint)}</p></div>
        </section>
        <section class="role-sec">
          <h2>${esc(R.secLimits)}</h2>
          <label class="ei-field"><span>${esc(R.fNever)}</span>
            <textarea id="rc-never" rows="3" placeholder="${esc(R.neverPh)}"></textarea></label>
          <label class="ei-field"><span>${esc(R.fPersonOnly)}</span>
            <textarea id="rc-person" rows="3" placeholder="${esc(R.personPh)}"></textarea></label>
          <p class="hint">${esc(R.linesHint)}</p>
        </section>
        <section class="role-sec">
          <h2>${esc(R.secRun)}</h2>
          <div class="ei-field"><span id="rc-okind-l">${esc(R.fOutput)}</span>${chips("okind", ["unset", ...OUTPUT_KINDS], R.outputKinds)}</div>
          <label class="ei-field"><span>${esc(R.fAct)}</span>
            <textarea id="rc-act" rows="3" spellcheck="false" placeholder="${esc(R.actPh)}"></textarea></label>
          <p class="hint">${esc(R.actHint)}</p>
          <fieldset class="ei-field rc-set"><legend>${esc(R.fBudget)}</legend>
            <div class="rc-grid">${BUDGET_KEYS.map((k) => `<label class="ei-field"><span>${esc(R.budgetKeys[k])}</span>
              <input id="rc-b-${k}" type="text" inputmode="decimal" autocomplete="off" spellcheck="false"/></label>`).join("")}</div></fieldset>
          <fieldset class="ei-field rc-set"><legend>${esc(R.fCaps)}</legend>
            ${CAPABILITY_LISTS.map((k) => `<label class="ei-field"><span>${esc(R.capLists[k])}</span>
              <input id="rc-c-${k}" type="text" autocomplete="off" spellcheck="false" placeholder="${esc(R.capsPh)}"/></label>`).join("")}</fieldset>
          <p class="hint">${esc(R.capsHint)}</p>
        </section>
        <footer class="role-foot">
          <div id="rc-msg" class="nc-msg" role="status"></div>
          <a class="btn" href="${AGENTS_BOARD}" id="rc-cancel">${esc(COPY.ui.common.cancel)}</a>
          ${readOnly ? "" : `<button class="btn btn-primary" type="submit" id="rc-save">${esc(editing ? R.save : A.create)}</button>`}
        </footer>
      </form>
      <aside class="role-aside">
        <p class="role-aside-k">${esc(R.preview)}</p>
        <article class="agent-card agent-card-preview" aria-live="polite">
          <header class="agent-card-top">
            <h3 class="agent-name" id="rc-pv-name"></h3>
            <span class="agent-seat" id="rc-pv-seat" hidden></span>
          </header>
          <p class="agent-pv-team hint" id="rc-pv-team"></p>
          <p class="agent-mission" id="rc-pv-mission"></p>
        </article>
        ${editing ? `<p class="role-aside-k">${esc(R.relsTitle)}</p>${rels}
        <div class="role-aside-acts">
          <button class="btn btn-quiet agent-act" type="button" data-act="copy"><span>${esc(H.agentsCopy)}</span></button>
          <button class="btn btn-quiet agent-act" type="button" data-act="graph">${icon("graph", 14)}<span>${esc(H.agentsGraph)}</span></button>
        </div>
        <p class="role-aside-k">${esc(R.evalTitle)}</p>
        ${shared ? `<div class="role-eval">${shared}</div>` : ""}
        <div class="role-eval" id="rc-eval">${loaderRows(COPY.ui.common.loading, { count: 2 })}</div>` : ""}
      </aside>
    </div>
  </div>`;

  const $ = <T extends HTMLElement>(s: string) => el.querySelector<T>(s)!;
  // 평가 카드는 읽기 전용이고 편집과 무관하다. 늦거나 실패해도 편집은 막지 않는다.
  if (editing) {
    void api.evalCards(cfg.owner, editId).then((r: any) => {
      if (!live()) return;
      if (!r?.executed || !Array.isArray(r.cards)) throw new Error(r?.error || "eval cards unavailable");
      $("#rc-eval").innerHTML = evalCardsHtml(r.cards, editId);
    }).catch(() => {
      if (live()) $("#rc-eval").innerHTML = `<p class="eval-note">${esc(R.evalFail)}</p>`;
    });
  }
  const form = $<HTMLFormElement>(".role-form");
  const jobEl = $<HTMLInputElement>("#rc-job");
  const slugEl = $<HTMLInputElement>("#rc-slug");
  const teamEl = $<HTMLInputElement>("#rc-team");
  const missionEl = $<HTMLTextAreaElement>("#rc-mission");
  const btn = el.querySelector<HTMLButtonElement>("#rc-save");
  const msg = (html: string) => { $("#rc-msg").innerHTML = html; };
  const idOf = () => editing ? editId : `concept:agent-${slugEl.value.trim()}`;
  let tier = "";
  let rung = "";
  let teamRole = "";
  // 돌려주는 것은 사람이 고를 때만 보낸다. 읽은 값이 어휘 밖이어도 손대지 않으면 그대로 남는다.
  let outputKind = "";
  let outputTouched = false;
  let slugTouched = editing;
  let busy = false;
  let done = false;

  const paintId = () => {
    const s = slugEl.value.trim();
    $("#rc-id").textContent = s ? A.idPreview(idOf()) + (ROLE_SLUG.test(s) ? "" : ` · ${R.slugBad}`) + " · " : "";
  };
  // 옆 칸의 카드는 보드 카드와 같은 생김새로 입력을 따라간다.
  const paintPreview = () => {
    const team = teamEl.value.trim();
    const name = $("#rc-pv-name");
    name.textContent = jobEl.value.trim() || R.jobPh;
    name.classList.toggle("agent-pv-empty", !jobEl.value.trim());
    $("#rc-pv-mission").textContent = missionEl.value.trim() || H.agentsNoMission;
    $("#rc-pv-team").textContent = team || H.agentsNoTeam;
    const seat = $("#rc-pv-seat");
    seat.hidden = !(team && teamRole);
    seat.className = `agent-seat agent-seat-${teamRole}`;
    seat.textContent = teamRole ? R.teamRoles[teamRole] || teamRole : "";
  };
  const pick = (name: "tier" | "rung" | "trole" | "okind", value: string) => {
    msg("");
    el.querySelectorAll<HTMLElement>(`.rc-${name} .chip`).forEach((b) => {
      const on = b.dataset[name] === value;
      b.classList.toggle("active", on);
      b.setAttribute("aria-checked", String(on));
    });
    if (name === "tier") { tier = value; $("#rc-tier-hint").textContent = R.tierHints[value] || R.tierHint; }
    else if (name === "rung") rung = value;
    else if (name === "okind") outputKind = value === "unset" ? "" : value;
    else teamRole = value;
    paintPreview();
  };
  for (const name of ["tier", "rung", "trole", "okind"] as const) {
    el.querySelector(`.rc-${name}`)!.addEventListener("click", (e) => {
      const b = (e.target as HTMLElement).closest<HTMLElement>(`[data-${name}]`);
      if (!b) return;
      if (name === "okind") outputTouched = true;
      pick(name, b.dataset[name]!);
    });
  }
  const fieldsOf = (prefix: string, keys: readonly string[]) =>
    Object.fromEntries(keys.map((k) => [k, $<HTMLInputElement>(`#rc-${prefix}-${k}`).value]));
  const fillFields = (prefix: string, values: Record<string, string>) => {
    for (const [k, v] of Object.entries(values)) $<HTMLInputElement>(`#rc-${prefix}-${k}`).value = v;
  };
  // 칸마다 읽은 값의 모양을 기억해 두고, 같으면 저장 때 읽은 값을 그대로 보낸다.
  const sameFields = (a: Record<string, string>, b: Record<string, string>) =>
    Object.keys(b).every((k) => (a[k] || "").split(",").map((x) => x.trim()).filter(Boolean).join(",")
      === (b[k] || "").split(",").map((x) => x.trim()).filter(Boolean).join(","));

  if (editing) {
    jobEl.value = baseTitle;
    slugEl.value = editId.replace(/^concept:agent-/, "");
    missionEl.value = strOf(baseProps.mission);
    teamEl.value = strOf(baseProps.team);
    $<HTMLTextAreaElement>("#rc-never").value = listLines(baseProps.never).join("\n");
    $<HTMLTextAreaElement>("#rc-person").value = listLines(baseProps.person_only).join("\n");
    if (TIERS.includes(strOf(baseProps.tier) as never)) pick("tier", strOf(baseProps.tier));
    if (RUNGS.includes(strOf(baseProps.rung) as never)) pick("rung", strOf(baseProps.rung));
    if (TEAM_ROLES.includes(strOf(baseProps.team_role) as never)) pick("trole", strOf(baseProps.team_role));
    $<HTMLTextAreaElement>("#rc-act").value = actLines(baseProps.act_policy).join("\n");
    fillFields("b", budgetFields(baseProps.budget));
    fillFields("c", capabilityFields(baseProps.capabilities));
  } else if (rest[1]) teamEl.value = rest[1];
  pick("okind", OUTPUT_KINDS.includes(strOf(baseProps.output_kind) as never) ? strOf(baseProps.output_kind) : "unset");
  paintId();
  paintPreview();
  if (readOnly) form.querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLButtonElement>(
    "input, textarea, .chip").forEach((x) => { x.disabled = true; });

  jobEl.addEventListener("input", () => {
    // 이름을 손으로 고치기 전까지는 직무명을 따라간다(고치기에서는 id 가 고정이다).
    if (!slugTouched) slugEl.value = roleSlugOf(jobEl.value);
    paintId();
  });
  slugEl.addEventListener("input", () => { slugTouched = true; paintId(); });
  // 앞서 낸 "이것을 채워 주세요" 는 사람이 무엇이든 고치면 지운다 — 고친 뒤에도 남아 있으면 아직 틀린 줄 안다.
  form.addEventListener("input", () => { if (!done) msg(""); paintPreview(); });
  el.querySelector<HTMLElement>('[data-act="graph"]')?.addEventListener("click", () => {
    location.hash = `#view/read/${encodeURIComponent(editId)}`;
  });
  el.querySelector<HTMLElement>('[data-act="copy"]')?.addEventListener("click", (e) => {
    const b = e.currentTarget as HTMLElement;
    void copyRolePrompt(editId).then(() => {
      const label = b.querySelector("span");
      if (!label) return;
      label.textContent = H.agentsCopied;
      setTimeout(() => { if (label.isConnected) label.textContent = H.agentsCopy; }, 1600);
    }).catch(() => {});
  });

  async function save() {
    if (busy || done || readOnly || !btn) return;
    const title = jobEl.value.trim();
    const slug = slugEl.value.trim();
    const mission = missionEl.value.trim().replace(/\s+/g, " ");
    if (!title) { msg(esc(R.needJob)); jobEl.focus(); return; }
    if (!ROLE_SLUG.test(slug)) { msg(esc(R.slugBad)); slugEl.focus(); return; }
    if (!mission) { msg(esc(R.needMission)); missionEl.focus(); return; }
    if (!tier) { msg(esc(R.needTier)); return; }
    if (!rung) { msg(esc(R.needRung)); return; }
    const neverText = $<HTMLTextAreaElement>("#rc-never").value;
    const personText = $<HTMLTextAreaElement>("#rc-person").value;
    const neverList = editing && !fieldChanged(neverText, baseProps.never) ? baseProps.never : linesOf(neverText);
    let personOnly: unknown = baseProps.person_only;
    if (!editing || fieldChanged(personText, baseProps.person_only)) {
      const folded = foldPersonOnly(linesOf(personText), baseProps.person_only);
      if (!folded.ok) { msg(esc(R.personBad)); $<HTMLTextAreaElement>("#rc-person").focus(); return; }
      personOnly = folded.value;
    }
    // 실행 정책 칸 넷. 고친 칸만 접어서 확인하고, 비운 칸은 키째 뺀다.
    const policy: Record<string, unknown> = {};
    const actText = $<HTMLTextAreaElement>("#rc-act").value;
    if (!editing || fieldChanged(actText, actLines(baseProps.act_policy))) {
      const persons = Array.isArray(personOnly) ? personOnly.filter(isPersonAction).map((p) => p.action) : [];
      const act = foldActPolicy(linesOf(actText), persons);
      if (!act.ok) { msg(esc(R.actBad)); $<HTMLTextAreaElement>("#rc-act").focus(); return; }
      policy.act_policy = act.value;
    }
    const budgetNow = fieldsOf("b", BUDGET_KEYS);
    if (!editing || !sameFields(budgetNow, budgetFields(baseProps.budget))) {
      const budget = foldBudget(budgetNow);
      if (!budget.ok) { msg(esc(R.budgetBad)); $<HTMLInputElement>(`#rc-b-${budget.bad[0]}`).focus(); return; }
      policy.budget = budget.value;
    }
    const capsNow = fieldsOf("c", CAPABILITY_LISTS);
    if (!editing || !sameFields(capsNow, capabilityFields(baseProps.capabilities))) {
      const caps = foldCapabilities(capsNow);
      if (!caps.ok) { msg(esc(R.capsBad)); return; }
      policy.capabilities = caps.value;
    }
    if (!editing || outputTouched) policy.output_kind = outputKind || undefined;
    const id = idOf();
    busy = true;
    btn.disabled = true;
    if (!editing) {
      msg(esc(A.idChecking));
      // 쓰기 직전에 그 id 를 묻는다. 확실히 없을 때만 쓴다 — 있는 역할에 쓰면 props 가 통째로
      // 갈린다(노드 컴포저와 같은 규칙).
      let state: "new" | "exists" | "error" = "error";
      try {
        const r = await api.nodeContext(cfg.owner, id, 1) as { executed: boolean; note?: string };
        state = r.executed ? "exists" : /^unknown node/.test(String(r.note || "")) ? "new" : "error";
      } catch { /* 못 물었다 = 멈춘다 */ }
      if (state !== "new") {
        busy = false;
        btn.disabled = false;
        msg(state === "exists" ? esc(R.nameTaken(id)) : `<span class="error-banner">${esc(A.idCheckFail)}</span>`);
        return;
      }
    }
    const team = teamEl.value.trim();
    // 이 면이 다루는 키만 덮는다. 비운 팀은 키째 뺀다(빈 문자열을 팀 이름으로 남기지 않는다).
    const props: Record<string, unknown> = {
      ...baseProps,
      mission, tier, rung, never: neverList, person_only: personOnly,
    };
    delete props.team; delete props.team_role;
    if (team) { props.team = team; if (teamRole) props.team_role = teamRole; }
    for (const [key, value] of Object.entries(policy)) {
      if (value === undefined) delete props[key];
      else props[key] = value;
    }
    const node: Record<string, unknown> = { id, type: "concept", title, domain: editing ? baseDomain : topic,
                                            one_liner: mission, props };
    if (!editing) node.tags = ["agent", tier, "role"];
    if (editing && baseRev !== undefined) node.expected_rev = baseRev;
    msg(esc(editing ? R.saving : A.creating));
    let res: any;
    try { res = await api.ingestFragment({ nodes: [node], edges: [] }); }
    catch (e: any) { res = { executed: false, error: messageOf(e, "") }; }
    busy = false;
    btn.disabled = false;
    if (!live()) return;
    if (res?.executed === false) {
      const stale = /rev|revision|conflict/i.test(String(res.error || ""));
      msg(`<span class="error-banner">${esc(stale ? R.changedElsewhere : editing ? R.saveFail : A.createFail)}</span>`
        + (res.error && !stale ? ` <span class="hint">${esc(String(res.error))}</span>` : ""));
      return;
    }
    if ((res?.id_conflicts || []).includes(id)) { msg(esc(A.conflict(id))); return; }
    const merged = res?.merged?.nodes || {};
    if (!(editing ? (merged.matched || merged.created) : merged.created)) {
      msg(`<span class="error-banner">${esc(editing ? R.saveFail : A.createFail)}</span>`);
      return;
    }
    announceRolesChanged();
    // 역할 계약 경고는 숨기지 않는다 — 저장은 됐지만 호스트가 받을 프롬프트가 비거나
    // 목록에서 빠질 수 있다는 뜻이다. 경고가 있으면 이 면에 머문 채 보여 준다.
    const warns: string[] = (res?.warnings || []).map(String).filter((w: string) => w.includes(id));
    if (warns.length) {
      done = true;
      btn.disabled = true;
      msg(`${esc(editing ? R.saved(title) : R.created(title))}<br><span class="hint">${esc(R.warned)}</span><br>`
        + warns.map((w) => `<span class="hint">· ${esc(w)}</span>`).join("<br>")
        + `<br><a class="home-back" href="${roleHash({ id })}">${esc(R.editAgain)}</a>`);
      $("#rc-cancel").textContent = R.backToBoard;
      return;
    }
    location.hash = AGENTS_BOARD;
  }
  form.addEventListener("submit", (e) => { e.preventDefault(); void save(); });
  form.addEventListener("keydown", (e) => {
    // 여러 줄 칸에서는 Enter 가 줄바꿈이다. Ctrl/⌘+Enter 로 저장한다.
    if (e.key !== "Enter" || e.isComposing || e.shiftKey) return;
    const t = e.target as HTMLElement;
    if (t instanceof HTMLTextAreaElement && !(e.ctrlKey || e.metaKey)) return;
    if (t.closest("button, a, summary, select")) return;
    e.preventDefault();
    void save();
  });
  (editing ? missionEl : jobEl).focus({ preventScroll: true });
}
