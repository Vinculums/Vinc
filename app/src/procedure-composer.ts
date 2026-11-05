/** 절차 편집 면(`#home/agents/procedure/<id>`) — Agent 가 과제에 맞춰 읽는 일의 방법 하나를 고친다.
 *
 *  왼쪽은 이 절차의 칸(설명, 먼저 읽을 문서, 리포와 경로, 이 절차만의 금지와 사람 승인, 막을 도구와 능력,
 *  테스트 명령, 그래프에 쓸 수 있는 종류), 오른쪽은 실행할 때 적용되는 목록이다: Agent·이 절차·붙은 정책을
 *  합친 결과를 항목마다 온 곳과 함께 보여 준다. Agent 에 이미 있는 항목을 이 절차에 적으면 그 자리에서 알린다
 *  (되풀이는 고칠 곳을 둘로 만든다). 정책 붙이기는 지도 면에서 한다.
 *
 *  저장은 고친 칸만 `props_patch` 로, 읽은 판(rev)을 `expected_rev` 로 보낸다. 그 사이 다른 곳에서 바뀌었으면
 *  서버가 거절하고 이 면은 다시 읽으라고 말한다. 근거: [[vinc.canon.terminology §3.2]] */
import { api, cfg, isCommunitySpace, isWriteConflict, messageOf } from "./api";
import { COPY } from "./copy";
import { esc } from "./components";
import { setTopbarLead } from "./topbar";
import { AGENTS_BOARD, setLnbActive, showAgentRail } from "./lnb";
import { compose, policySlug, repeatedInField, type Item, type Kind, type PolicyRule, type Props } from "./agent-ontology";
import { fieldChanged, foldPersonOnly, linesOf, listLines } from "./role-fields";
import { announceRolesChanged } from "./role-composer";

type Field = { key: string; kind?: Kind; read: (p: Props) => string; write: (text: string, p: Props) => unknown };

const list = (k: string): Field => ({ key: k, read: (p) => listLines(p[k]).join("\n"), write: (t) => linesOf(t) });

/** 화면 칸 ↔ props 키. `kind` 가 있는 칸은 Agent 와 되풀이를 비교한다. */
const FIELDS: Field[] = [
  list("reads"), list("repos"), list("paths"),
  { ...list("never"), kind: "never" },
  { key: "person_only", kind: "person_only", read: (p) => listLines(p.person_only).join("\n"),
    write: (t, p) => { const f = foldPersonOnly(linesOf(t), p.person_only); return f.ok ? f.value : undefined; } },
  { ...list("disallowed_tools"), kind: "tool" },
  { key: "capabilities", kind: "capability", read: (p) => listLines((p.capabilities as any)?.deny).join("\n"),
    write: (t, p) => ({ ...((p.capabilities as object) || {}), deny: linesOf(t) }) },
  { key: "test_command", read: (p) => String(p.test_command || ""), write: (t) => t.trim() || null },
  list("writes_graph"),
];

export async function renderProcedureEditor(el: HTMLElement, live: () => boolean, id: string): Promise<void> {
  const M = COPY.ui.procedure;
  setLnbActive({ domains: [] });
  showAgentRail();
  setTopbarLead(`<a class="tb-lead-name" href="${AGENTS_BOARD}">${esc(COPY.ui.agentMap.title)}</a>`);
  el.innerHTML = `<div class="home agents procedure-editor"><p class="hint">${esc(COPY.ui.common.loading)}</p></div>`;

  let node: any, agent: any = null, policies: PolicyRule[] = [], names = new Map<string, string>();
  try {
    node = await api.nodeContext(cfg.owner, id, 1);
    if (!node?.executed) throw new Error(node?.note || "not found");
    const agentId = String((node.props || {}).agent || "");
    if (agentId) {
      const got = await api.role(cfg.owner, agentId);
      if (got?.executed) {
        agent = got.node || null;
        const mine = (got.procedures || []).find((p: any) => p.id === id);
        const byId = new Map<string, any>((got.policies || []).map((p: any) => [p.id, p]));
        policies = (mine?.policies || []).filter((p: string) => byId.has(p)).map((p: string) => ({ id: p, rule: byId.get(p).rule || {} }));
        names = new Map((got.policies || []).map((p: any) => [p.id, p.title || policySlug(p.id)]));
      }
    }
  } catch (e) {
    if (!live()) return;
    el.innerHTML = `<div class="home agents procedure-editor"><p class="hint">${esc(M.loadFail)} ${esc(messageOf(e, ""))}</p>
      <a class="btn" href="${AGENTS_BOARD}">${esc(M.back)}</a></div>`;
    return;
  }
  if (!live()) return;
  const readOnly = isCommunitySpace(cfg.team);
  const props: Props = { ...(node.props || {}) };
  const agentProps: Props = (agent?.props || {}) as Props;
  const rev: number = node.rev;

  const fieldHtml = (f: Field) => `<div class="pe-field"><label for="pe-${f.key}">${esc(M.fields[f.key as keyof typeof M.fields] || f.key)}</label>
    ${f.key === "test_command" ? `<input id="pe-${f.key}" value="${esc(f.read(props))}"${readOnly ? " readonly" : ""}>`
      : `<textarea id="pe-${f.key}" rows="${Math.min(8, Math.max(2, f.read(props).split("\n").length + 1))}"${readOnly ? " readonly" : ""}>${esc(f.read(props))}</textarea>`}
    ${f.kind ? `<p class="pe-repeat" data-repeat="${f.key}" hidden></p>` : ""}</div>`;

  el.innerHTML = `<div class="home agents procedure-editor">
    <header class="home-head"><h1>${esc(String(node.title || id).replace(/ \((Maker|Checker) procedure\)$/, ""))}</h1>
      <div class="home-acts"><a class="btn btn-quiet" href="${AGENTS_BOARD}">${esc(M.back)}</a>
      ${readOnly ? "" : `<button class="btn btn-primary" type="button" id="pe-save">${esc(M.save)}</button>`}</div></header>
    <p class="hint">${esc(M.lead(agent?.title || String(props.agent || "")))}</p>
    <p class="agents-status" role="status"></p>
    <div class="pe-grid">
      <form class="pe-form" onsubmit="return false">
        <div class="pe-field"><label for="pe-desc">${esc(M.fields.description)}</label>
          <textarea id="pe-desc" rows="3"${readOnly ? " readonly" : ""}>${esc(String(node.one_liner || props.description || ""))}</textarea></div>
        ${FIELDS.map(fieldHtml).join("")}
      </form>
      <aside class="pe-eff" aria-live="polite"></aside>
    </div></div>`;

  const root = el.querySelector<HTMLElement>(".procedure-editor")!;
  const val = (k: string) => (root.querySelector<HTMLInputElement | HTMLTextAreaElement>(`#pe-${k}`)?.value ?? "");
  /** 칸의 지금 값으로 만든 props(저장 전 미리보기용). 고치지 않은 칸은 읽은 값 그대로다. */
  const draft = (): Props => {
    const out: Props = { ...props };
    for (const f of FIELDS) {
      const text = val(f.key);
      if (f.key === "test_command" ? text.trim() !== String(props.test_command || "") : fieldChanged(text, f.key === "capabilities" ? (props.capabilities as any)?.deny : props[f.key])) {
        const v = f.write(text, props);
        if (v !== undefined) out[f.key] = v;
      }
    }
    return out;
  };
  const originLabel = (o: string) => o === "agent" ? COPY.ui.agentMap.originAgent : o === "procedure"
    ? COPY.ui.agentMap.originProcedure : COPY.ui.agentMap.originPolicy(policySlug(o));
  const paintEff = () => {
    const eff: Item[] = compose(agentProps, draft(), policies);
    const group = (kinds: Kind[], title: string) => {
      const rows = eff.filter((x) => kinds.includes(x.kind));
      return rows.length ? `<h3>${esc(title)}</h3><ul class="pe-eff-list">${rows.map((x) =>
        `<li><code>${esc(x.item)}</code><span title="${esc(x.origin.startsWith("decision:") ? names.get(x.origin) || x.origin : "")}" class="pe-origin pe-origin-${x.origin === "agent" || x.origin === "procedure" ? x.origin : "policy"}">${esc(originLabel(x.origin))}</span></li>`).join("")}</ul>` : "";
    };
    root.querySelector(".pe-eff")!.innerHTML = `<h2>${esc(COPY.ui.agentMap.effective)}</h2><p class="hint">${esc(COPY.ui.agentMap.effectiveHint)}</p>
      ${group(["tool", "capability"], M.effBlocked)}${group(["person_only"], M.effAsk)}${group(["never"], M.effNever)}`;
    for (const f of FIELDS) {
      if (!f.kind) continue;
      const box = root.querySelector<HTMLElement>(`[data-repeat="${f.key}"]`);
      const values = f.kind === "person_only" ? linesOf(val(f.key)).map((l) => l.split(":")[0]) : linesOf(val(f.key));
      const dup = repeatedInField(agentProps, f.kind, values);
      if (box) { box.hidden = !dup.length; box.textContent = dup.length ? M.repeat(dup.join(", "), agent?.title || "Agent") : ""; }
    }
  };
  paintEff();
  root.querySelector(".pe-form")!.addEventListener("input", paintEff);

  const status = (s: string) => { root.querySelector(".agents-status")!.textContent = s; };
  root.querySelector("#pe-save")?.addEventListener("click", async () => {
    const next = draft();
    const pf = foldPersonOnly(linesOf(val("person_only")), props.person_only);
    if (!pf.ok) { status(M.badPersonOnly(pf.bad.join(", "))); return; }
    const patch: Props = {};
    for (const f of FIELDS) if (JSON.stringify(next[f.key]) !== JSON.stringify(props[f.key])) patch[f.key] = next[f.key];
    const desc = val("desc").trim();
    const descChanged = desc !== String(node.one_liner || props.description || "");
    if (descChanged) patch.description = desc;
    if (!Object.keys(patch).length) { location.hash = AGENTS_BOARD; return; }
    status(COPY.ui.agentMap.saving);
    let res: any;
    try { res = await api.editNode({ id, type: "concept", ...(descChanged ? { one_liner: desc } : {}), props_patch: patch }, rev); }
    catch (e) { res = { executed: false, error: messageOf(e, "") }; }
    if (isWriteConflict(res) || /rev|revision|conflict/i.test(String(res?.error || ""))) { status(M.stale); return; }
    if (res?.executed === false || (res?.warnings || []).length) { status(M.saveFail); return; }
    announceRolesChanged();
    location.hash = AGENTS_BOARD;
  });
}
