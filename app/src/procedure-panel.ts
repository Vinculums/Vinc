/** 절차 편집 칸 — Agent 그래프 옆 칸에서 절차를 고른 그 자리에서 고친다(다른 면으로 들어가지 않는다).
 *
 *  칸: 설명, 먼저 읽을 문서, 리포와 경로, 이 절차만의 금지와 사람 승인, 막을 도구와 능력, 테스트 명령,
 *  그래프에 쓸 수 있는 종류. 그 아래 "실행할 때 적용되는 목록"은 Agent·이 절차·붙은 정책을 합친 결과를
 *  항목마다 온 곳과 함께 보여 주고, 칸을 고치는 대로 바뀐다. Agent 에 이미 있는 항목을 적으면 그 자리에서
 *  알린다(되풀이는 고칠 곳을 둘로 만든다).
 *
 *  저장은 고친 칸만 `props_patch` 로, 읽은 판(rev)을 `expected_rev` 로 보낸다. 그 사이 다른 곳에서 바뀌었으면
 *  서버가 거절하고 이 칸은 다시 읽으라고 말한다. 저장한 뒤에는 이 절차 하나만 다시 읽어 칸을 새로 채운다
 *  (그래프 전체를 다시 그리지 않는다). 근거: [[vinc.canon.terminology §3.2]] */
import { api, cfg, isWriteConflict, messageOf } from "./api";
import { COPY } from "./copy";
import { esc } from "./components";
import { compose, policySlug, repeatedInField, type Item, type Kind, type PolicyRule, type Props } from "./agent-ontology";
import { fieldChanged, foldPersonOnly, linesOf, listLines } from "./role-fields";

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

export interface ProcedureCtx {
  id: string; title: string; oneLiner: string; props: Props; rev: number;
  agentTitle: string; agentProps: Props; policies: PolicyRule[]; policyNames: Map<string, string>;
  readOnly: boolean;
  /** 저장한 뒤 다시 읽은 값. 호출측이 자기 자료를 고친다(다시 고를 때 옛 값을 보이지 않게). */
  onSaved: (fresh: { props: Props; oneLiner: string; rev: number }) => void;
}

/** 옆 칸의 편집 부분을 `host` 안에 그리고 묶는다. */
export function mountProcedurePanel(host: HTMLElement, ctx: ProcedureCtx): void {
  const M = COPY.ui.procedure, A = COPY.ui.agentMap;
  const props = ctx.props;
  const ro = ctx.readOnly ? " readonly" : "";
  const fieldHtml = (f: Field) => `<div class="pe-field"><label for="pe-${f.key}">${esc(M.fields[f.key as keyof typeof M.fields] || f.key)}</label>
    ${f.key === "test_command" ? `<input id="pe-${f.key}" value="${esc(f.read(props))}"${ro}>`
      : `<textarea id="pe-${f.key}" rows="${Math.min(6, Math.max(2, f.read(props).split("\n").length + 1))}"${ro}>${esc(f.read(props))}</textarea>`}
    ${f.kind ? `<p class="pe-repeat" data-repeat="${f.key}" hidden></p>` : ""}</div>`;
  host.innerHTML = `<form class="pe-form" onsubmit="return false">
      <div class="pe-field"><label for="pe-desc">${esc(M.fields.description)}</label>
        <textarea id="pe-desc" rows="3"${ro}>${esc(ctx.oneLiner || String(props.description || ""))}</textarea></div>
      ${FIELDS.map(fieldHtml).join("")}
    </form>
    <div class="pe-eff" aria-live="polite"></div>
    ${ctx.readOnly ? "" : `<div class="pe-save-row"><span class="pe-status" role="status"></span>
      <button class="btn btn-primary" type="button" id="pe-save">${esc(M.save)}</button></div>`}`;

  const val = (k: string) => (host.querySelector<HTMLInputElement | HTMLTextAreaElement>(`#pe-${k}`)?.value ?? "");
  /** 칸의 지금 값으로 만든 props(미리보기와 저장에 쓴다). 고치지 않은 칸은 읽은 값 그대로다. */
  const draft = (): Props => {
    const out: Props = { ...props };
    for (const f of FIELDS) {
      const text = val(f.key);
      const changed = f.key === "test_command" ? text.trim() !== String(props.test_command || "")
        : fieldChanged(text, f.key === "capabilities" ? (props.capabilities as any)?.deny : props[f.key]);
      if (changed) { const v = f.write(text, props); if (v !== undefined) out[f.key] = v; }
    }
    return out;
  };
  const originLabel = (o: string) => o === "agent" ? A.originAgent : o === "procedure" ? A.originProcedure : A.originPolicy(policySlug(o));
  const paint = () => {
    const eff: Item[] = compose(ctx.agentProps, draft(), ctx.policies);
    const group = (kinds: Kind[], title: string) => {
      const rows = eff.filter((x) => kinds.includes(x.kind));
      return rows.length ? `<h3>${esc(title)}</h3><ul class="pe-eff-list">${rows.map((x) =>
        `<li><code>${esc(x.item)}</code><span title="${esc(x.origin.startsWith("decision:") ? ctx.policyNames.get(x.origin) || x.origin : "")}" class="pe-origin pe-origin-${
          x.origin === "agent" || x.origin === "procedure" ? x.origin : "policy"}">${esc(originLabel(x.origin))}</span></li>`).join("")}</ul>` : "";
    };
    host.querySelector(".pe-eff")!.innerHTML = `<h3 class="pe-eff-h">${esc(A.effective)}</h3><p class="hint">${esc(A.effectiveHint)}</p>
      ${group(["tool", "capability"], M.effBlocked)}${group(["person_only"], M.effAsk)}${group(["never"], M.effNever)}`;
    for (const f of FIELDS) {
      if (!f.kind) continue;
      const box = host.querySelector<HTMLElement>(`[data-repeat="${f.key}"]`);
      const values = f.kind === "person_only" ? linesOf(val(f.key)).map((l) => l.split(":")[0]) : linesOf(val(f.key));
      const dup = repeatedInField(ctx.agentProps, f.kind, values);
      if (box) { box.hidden = !dup.length; box.textContent = dup.length ? M.repeat(dup.join(", "), ctx.agentTitle || "Agent") : ""; }
    }
  };
  paint();
  host.querySelector(".pe-form")!.addEventListener("input", paint);

  const status = (s: string) => { const x = host.querySelector(".pe-status"); if (x) x.textContent = s; };
  host.querySelector("#pe-save")?.addEventListener("click", async () => {
    const next = draft();
    const pf = foldPersonOnly(linesOf(val("person_only")), props.person_only);
    if (!pf.ok) { status(M.badPersonOnly(pf.bad.join(", "))); return; }
    const patch: Props = {};
    for (const f of FIELDS) if (JSON.stringify(next[f.key]) !== JSON.stringify(props[f.key])) patch[f.key] = next[f.key];
    const desc = val("desc").trim();
    const descChanged = desc !== (ctx.oneLiner || String(props.description || ""));
    if (descChanged) patch.description = desc;
    if (!Object.keys(patch).length) { status(M.noChange); return; }
    status(A.saving);
    let res: any;
    try { res = await api.editNode({ id: ctx.id, type: "concept", ...(descChanged ? { one_liner: desc } : {}), props_patch: patch }, ctx.rev); }
    catch (e) { res = { executed: false, error: messageOf(e, "") }; }
    if (isWriteConflict(res) || /rev|revision|conflict/i.test(String(res?.error || ""))) { status(M.stale); return; }
    if (res?.executed === false || (res?.warnings || []).length) { status(M.saveFail); return; }
    let fresh: any = null;
    try { fresh = await api.nodeContext(cfg.owner, ctx.id, 1); } catch { fresh = null; }
    if (!fresh?.executed) { status(M.saved); return; }
    ctx.onSaved({ props: (fresh.props || {}) as Props, oneLiner: String(fresh.one_liner || ""), rev: Number(fresh.rev) });
    mountProcedurePanel(host, { ...ctx, props: (fresh.props || {}) as Props, oneLiner: String(fresh.one_liner || ""), rev: Number(fresh.rev) });
    const s = host.querySelector(".pe-status"); if (s) s.textContent = M.saved;
  });
}
