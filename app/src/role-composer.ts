/** 새 Agent 역할 — Agent 레일의 `+` 가 연다.
 *
 *  역할은 개념 노드 하나다: id `concept:agent-<이름>`, 주제 `agent`, 계약은 props
 *  (`mission`·`tier`·`rung`·`never`·`person_only`). 일반 노드 컴포저로도 만들 수는 있지만
 *  id 를 제목에서 지어 한국어 직무명이면 역할 이름 규칙(영문 소문자·숫자·하이픈)을 못
 *  맞추고, 계약 키를 만든 뒤 속성 편집기에서 하나씩 더해야 했다. 여기서는 이름을 따로
 *  받고 계약을 한 화면에서 채운다. 서버의 역할 계약 경고(`api/role_contract.py`)는
 *  저장 뒤 그대로 보여 준다 — 거절이 아니라 알림이라 저장은 된다.
 *  근거: [[vinc.canon.agent-roles]] · [[decision:agent-roles-live-under-a-top-level-agent-topic-2026-10-02]] */
import { api, cfg, messageOf } from "./api";
import { COPY } from "./copy";
import { esc, openSheet, sheetActions } from "./components";
import { viewHooks } from "./topbar";
import { AGENT_TOPICS } from "./lnb";

/** `api/role_contract.py` 의 TIERS · RUNGS 와 같은 순서. */
export const TIERS = ["orchestrate", "execute", "verify", "deliver", "knowledge"] as const;
export const RUNGS = ["strongest", "strong", "fast", "cheapest"] as const;
/** 역할 이름 규칙(`api/role_contract._SLUG`). */
export const ROLE_SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

/** 직무명 → 역할 이름 후보. 영문 소문자·숫자만 남긴다(한국어 직무명이면 빈 값이라 사람이 적는다). */
const roleSlugOf = (title: string): string =>
  title.normalize("NFKD").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48)
    .replace(/-+$/, "");
/** 여러 줄 칸 → 목록. 빈 줄은 버린다. */
const linesOf = (s: string): string[] => s.split("\n").map((x) => x.trim()).filter(Boolean);

export function openRoleComposer(): void {
  const R = COPY.author.role;
  const A = COPY.author;
  const topic = AGENT_TOPICS[0];
  const sheet = openSheet({ title: R.title, subtitle: R.sub });
  let tier = "";
  let rung = "";
  let slugTouched = false;
  let busy = false;

  const chips = (name: string, keys: readonly string[], labels: Record<string, string>) =>
    `<div class="seg rc-${name}" role="radiogroup" aria-labelledby="rc-${name}-l">${keys.map((k) =>
      `<button type="button" class="chip" role="radio" aria-checked="false" data-${name}="${k}">${
        esc(labels[k])}</button>`).join("")}</div>`;
  sheet.body.innerHTML = `
    <div class="nc rc">
      <label class="ei-field"><span>${esc(R.fJob)}</span>
        <input id="rc-job" type="text" autocomplete="off" placeholder="${esc(R.jobPh)}"/></label>
      <label class="ei-field"><span>${esc(R.fSlug)}</span>
        <input id="rc-slug" type="text" autocomplete="off" spellcheck="false" placeholder="release-notes"/></label>
      <p class="hint nc-id"><span id="rc-id"></span> · ${esc(R.topicNote(topic))}</p>
      <label class="ei-field"><span>${esc(R.fMission)}</span>
        <textarea id="rc-mission" rows="2" placeholder="${esc(R.missionPh)}"></textarea></label>
      <div class="ei-field"><span id="rc-tier-l">${esc(R.fTier)}</span>${chips("tier", TIERS, R.tiers)}
        <p class="hint" id="rc-tier-hint">${esc(R.tierHint)}</p></div>
      <div class="ei-field"><span id="rc-rung-l">${esc(R.fRung)}</span>${chips("rung", RUNGS, R.rungs)}
        <p class="hint">${esc(R.rungHint)}</p></div>
      <label class="ei-field"><span>${esc(R.fNever)}</span>
        <textarea id="rc-never" rows="2" placeholder="${esc(R.neverPh)}"></textarea></label>
      <label class="ei-field"><span>${esc(R.fPersonOnly)}</span>
        <textarea id="rc-person" rows="2" placeholder="${esc(R.personPh)}"></textarea></label>
      <p class="hint">${esc(R.linesHint)}</p>
      <label class="ei-field"><span>${esc(R.fTeam)}</span>
        <input id="rc-team" type="text" autocomplete="off"/></label>
    </div>`;
  sheet.foot.innerHTML = `<div id="rc-msg" class="nc-msg" role="status"></div>`
    + sheetActions({ id: "rc-create", label: A.create });
  const $ = <T extends HTMLElement>(s: string) => sheet.el.querySelector<T>(s)!;
  const jobEl = $<HTMLInputElement>("#rc-job");
  const slugEl = $<HTMLInputElement>("#rc-slug");
  const msg = (html: string) => { $("#rc-msg").innerHTML = html; };
  const idOf = () => `concept:agent-${slugEl.value.trim()}`;
  const paintId = () => {
    const s = slugEl.value.trim();
    $("#rc-id").textContent = s ? A.idPreview(idOf()) + (ROLE_SLUG.test(s) ? "" : ` · ${R.slugBad}`) : "";
  };

  jobEl.addEventListener("input", () => {
    // 이름을 손으로 고치기 전까지는 직무명을 따라간다.
    if (!slugTouched) slugEl.value = roleSlugOf(jobEl.value);
    paintId();
  });
  slugEl.addEventListener("input", () => { slugTouched = true; paintId(); });

  // 앞서 낸 "이것을 채워 주세요" 는 사람이 무엇이든 고치면 지운다 — 고친 뒤에도 남아 있으면 아직 틀린 줄 안다.
  sheet.body.addEventListener("input", () => msg(""));
  const pick = (name: "tier" | "rung", value: string) => {
    msg("");
    sheet.el.querySelectorAll<HTMLElement>(`.rc-${name} .chip`).forEach((b) => {
      const on = b.dataset[name] === value;
      b.classList.toggle("active", on);
      b.setAttribute("aria-checked", String(on));
    });
    if (name === "tier") { tier = value; $("#rc-tier-hint").textContent = R.tierHints[value] || R.tierHint; }
    else rung = value;
  };
  sheet.el.querySelector(".rc-tier")!.addEventListener("click", (e) => {
    const b = (e.target as HTMLElement).closest<HTMLElement>("[data-tier]");
    if (b) pick("tier", b.dataset.tier!);
  });
  sheet.el.querySelector(".rc-rung")!.addEventListener("click", (e) => {
    const b = (e.target as HTMLElement).closest<HTMLElement>("[data-rung]");
    if (b) pick("rung", b.dataset.rung!);
  });

  async function create() {
    if (busy) return;
    const title = jobEl.value.trim();
    const slug = slugEl.value.trim();
    const mission = $<HTMLTextAreaElement>("#rc-mission").value.trim().replace(/\s+/g, " ");
    if (!title) { msg(esc(R.needJob)); jobEl.focus(); return; }
    if (!ROLE_SLUG.test(slug)) { msg(esc(R.slugBad)); slugEl.focus(); return; }
    if (!mission) { msg(esc(R.needMission)); $("#rc-mission").focus(); return; }
    if (!tier) { msg(esc(R.needTier)); return; }
    if (!rung) { msg(esc(R.needRung)); return; }
    const id = idOf();
    busy = true;
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
      msg(state === "exists" ? esc(R.nameTaken(id)) : `<span class="error-banner">${esc(A.idCheckFail)}</span>`);
      return;
    }
    const team = $<HTMLInputElement>("#rc-team").value.trim();
    const props: Record<string, unknown> = {
      mission, tier, rung,
      never: linesOf($<HTMLTextAreaElement>("#rc-never").value),
      person_only: linesOf($<HTMLTextAreaElement>("#rc-person").value),
      ...(team ? { team } : {}),
    };
    const node = { id, type: "concept", title, domain: topic, tags: ["agent", tier, "role"],
                   one_liner: mission, props };
    const btn = sheet.foot.querySelector<HTMLButtonElement>("#rc-create")!;
    btn.disabled = true;
    msg(esc(A.creating));
    let res: any;
    try { res = await api.ingestFragment({ nodes: [node], edges: [] }); }
    catch (e: any) { res = { executed: false, error: messageOf(e, "") }; }
    busy = false;
    btn.disabled = false;
    if (res?.executed === false) {
      msg(`<span class="error-banner">${esc(A.createFail)}</span>`
        + (res.error ? ` <span class="hint">${esc(String(res.error))}</span>` : ""));
      return;
    }
    if ((res?.id_conflicts || []).includes(id)) { msg(esc(A.conflict(id))); return; }
    if (!res?.merged?.nodes?.created) { msg(`<span class="error-banner">${esc(A.createFail)}</span>`); return; }
    void import("./lnb").then((m) => m.refreshLnb({ quiet: true }));
    // 역할 계약 경고는 숨기지 않는다 — 저장은 됐지만 호스트가 받을 프롬프트가 비거나
    // 목록에서 빠질 수 있다는 뜻이다. 경고가 있으면 시트를 열어 둔 채 보여 준다.
    const warns: string[] = (res?.warnings || []).map(String).filter((w: string) => w.includes(id));
    if (warns.length) {
      msg(`${esc(R.created(title))}<br><span class="hint">${esc(R.warned)}</span><br>`
        + warns.map((w) => `<span class="hint">· ${esc(w)}</span>`).join("<br>"));
      return;
    }
    sheet.close();
    const h = viewHooks();
    if (h?.openNodePane) void h.openNodePane(id, topic, title);
    else location.hash = `#view/read/${encodeURIComponent(id)}`;
  }
  sheet.foot.querySelector("#rc-create")!.addEventListener("click", () => void create());
  sheet.el.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || e.isComposing || e.shiftKey) return;
    const t = e.target as HTMLElement;
    if (t instanceof HTMLTextAreaElement && !(e.ctrlKey || e.metaKey)) return;
    if (t.closest("button, summary, select")) return;
    e.preventDefault();
    void create();
  });
  jobEl.focus();
}
