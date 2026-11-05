/** 절차 편집 칸 — Agent 그래프 옆 칸에서 절차를 고른 그 자리에서 고친다(다른 면으로 들어가지 않는다).
 *
 *  묶음: 무엇(이름 · 하는 일) · 먼저 읽을 것 · 제한(하지 않는 것 · 사람 승인 · 막을 도구와 능력) ·
 *  코드 작업(리포 · 경로 · 테스트 명령, 코드를 다루는 절차만이라 비어 있으면 접힌다) · 남기는 것(고른다).
 *  Agent 도 같은 틀로 고친다(kind: "agent", 코드 작업 묶음 없음). 그 아래 "실행할 때 적용되는 목록"은 Agent·이 절차·붙은 정책을 합친 결과를
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
import { canon, mountGateEditor, type GateEditor } from "./gate-editor";

type Field = { key: string; kind?: Kind; read: (p: Props) => string; write: (text: string, p: Props) => unknown; rows?: number; editor?: "gates" | "checks" };

const list = (k: string): Field => ({ key: k, read: (p) => listLines(p[k]).join("\n"), write: (t) => linesOf(t) });

/** 테스트 명령. 한 줄이면 문자열, 원래 값이 출처별(`{dev-core: "…"}`)이면 "이름: 명령" 줄로 보여 주고 같은 꼴로 되돌린다.
 *  예전에는 출처별 값이 `[object Object]` 로 보였고, 그 칸을 고치면 명령이 사라졌다. */
const testCommand: Field = {
  key: "test_command", rows: 2,
  read: (p) => { const v = p.test_command as unknown; if (!v) return ""; if (typeof v === "string") return v;
    return typeof v === "object" ? Object.entries(v as Record<string, unknown>).map(([k, c]) => `${k}: ${String(c)}`).join("\n") : String(v); },
  write: (t, p) => { const ls = linesOf(t); if (!ls.length) return null; const was = p.test_command;
    if (was && typeof was === "object" && !Array.isArray(was)) { const o: Record<string, string> = {};
      for (const l of ls) { const i = l.indexOf(": "); if (i > 0) o[l.slice(0, i).trim()] = l.slice(i + 2).trim(); else o[`${Object.keys(o).length + 1}`] = l; } return o; }
    return ls.length === 1 ? ls[0] : ls.join(" && "); },
};
/** 그래프에 남기는 종류: 글로 적지 않고 고른다. 모르는 값(다른 곳에서 넣은 것)은 그대로 남긴다. */
export const WRITE_KINDS = ["episode", "record", "decision", "concept"] as const;
/** 종류별 편집기로 고치는 칸(gate-editor.ts). 값은 JSON 글로 오간다. */
const structured = (key: "gates" | "never_checks", editor: "gates" | "checks"): Field =>
  ({ key, editor, read: (p) => canon(p[key]), write: (t) => JSON.parse(t) });
const writesGraph: Field = { key: "writes_graph", read: (p) => listLines(p.writes_graph).join("\n"), write: (t) => linesOf(t) };

/** 칸을 묶음으로 나눈다: 무엇 · 먼저 읽을 것 · 제한 · 코드 작업(코드를 다루는 절차만, 접어 둔다) · 남기는 것.
 *  Agent 는 같은 틀에서 코드 작업 묶음만 뺀다(Agent 에는 리포·경로·테스트가 없다). */
type Group = { id: "reads" | "limits" | "checks" | "code" | "writes"; fields: Field[] };
const GROUPS: Group[] = [
  { id: "reads", fields: [{ ...list("reads"), rows: 4 }] },
  { id: "limits", fields: [
    { ...list("never"), kind: "never", rows: 4 },
    { key: "person_only", kind: "person_only", rows: 3, read: (p) => listLines(p.person_only).join("\n"),
      write: (t, p) => { const f = foldPersonOnly(linesOf(t), p.person_only); return f.ok ? f.value : undefined; } },
    { ...list("disallowed_tools"), kind: "tool", rows: 3 },
    { key: "capabilities", kind: "capability", rows: 3, read: (p) => listLines((p.capabilities as any)?.deny).join("\n"),
      write: (t, p) => ({ ...((p.capabilities as object) || {}), deny: linesOf(t) }) },
    structured("never_checks", "checks"),
  ] },
  { id: "checks", fields: [structured("gates", "gates")] },
  { id: "code", fields: [{ ...list("repos"), rows: 2 }, { ...list("paths"), rows: 4 }, testCommand] },
  { id: "writes", fields: [writesGraph] },
];

export interface ProcedureCtx {
  id: string; title: string; oneLiner: string; props: Props; rev: number;
  agentTitle: string; agentProps: Props; policies: PolicyRule[]; policyNames: Map<string, string>;
  readOnly: boolean;
  /** "agent" 면 Agent 를 고친다: 하는 일은 props.mission, 코드 작업 묶음과 "실행할 때 적용되는 목록"이 없다. */
  kind?: "procedure" | "agent";
  /** 저장한 뒤 다시 읽은 값. 호출측이 자기 자료를 고친다(다시 고를 때 옛 값을 보이지 않게). */
  onSaved: (fresh: { props: Props; oneLiner: string; rev: number; title: string }) => void;
}

/** 옆 칸의 편집 부분을 `host` 안에 그리고 묶는다. */
export function mountProcedurePanel(host: HTMLElement, ctx: ProcedureCtx): void {
  const M = COPY.ui.procedure, A = COPY.ui.agentMap;
  const props = ctx.props, isAgent = ctx.kind === "agent";
  const ro = ctx.readOnly ? " readonly" : "", dis = ctx.readOnly ? " disabled" : "";
  const groups = GROUPS.filter((g) => !(isAgent && g.id === "code"));
  const FIELDS = groups.flatMap((g) => g.fields);
  const descOf = () => isAgent ? String(props.mission || "") : (ctx.oneLiner || String(props.description || ""));
  const rowsFor = (f: Field, text: string) => Math.min(12, Math.max(f.rows || 3, text.split("\n").length + 1));
  const fieldHtml = (f: Field) => {
    const text = f.read(props), label = esc(isAgent && f.key === "never" ? M.fields.neverAgent : M.fields[f.key as keyof typeof M.fields] || f.key);
    if (f.editor) return `<div class="pe-field"><span class="pe-label">${label}</span><div class="ge" id="pe-${f.key}"></div></div>`;
    if (f.key === "writes_graph") { const on = new Set(linesOf(text)), extra = [...on].filter((k) => !(WRITE_KINDS as readonly string[]).includes(k));
      return `<fieldset class="pe-field pe-checks" id="pe-writes_graph" data-extra="${esc(extra.join("\n"))}"><legend>${label}</legend>${WRITE_KINDS.map((k) =>
        `<label class="pe-check"><input type="checkbox" value="${k}"${on.has(k) ? " checked" : ""}${dis}><span>${esc(M.kinds[k])}</span></label>`).join("")}</fieldset>`; }
    return `<div class="pe-field"><label for="pe-${f.key}">${label}</label>
      <textarea id="pe-${f.key}" rows="${rowsFor(f, text)}"${ro}>${esc(text)}</textarea>
      ${f.kind ? `<p class="pe-repeat" data-repeat="${f.key}" hidden></p>` : ""}</div>`; };
  // 코드 작업: 값이 하나라도 있으면 펼친 채, 없으면 접어 둔다(문서·조사·검토 같은 절차에는 필요 없다)
  const codeOpen = GROUPS.find((g) => g.id === "code")!.fields.some((f) => f.read(props).trim());
  const groupHtml = (g: Group) => g.id === "code"
    ? `<details class="pe-group pe-code"${codeOpen ? " open" : ""}><summary class="pe-group-h">${esc(M.groups.code)}</summary><p class="hint">${esc(M.codeHint)}</p>${g.fields.map(fieldHtml).join("")}</details>`
    : `<section class="pe-group"><h3 class="pe-group-h">${esc(M.groups[g.id])}</h3>${g.fields.map(fieldHtml).join("")}</section>`;
  host.innerHTML = `<form class="pe-form" onsubmit="return false">
      <section class="pe-group"><h3 class="pe-group-h">${esc(M.groups.what)}</h3>
        <div class="pe-field"><label for="pe-title">${esc(M.fields.title)}</label><input id="pe-title" value="${esc(ctx.title)}"${ro}></div>
        <div class="pe-field"><label for="pe-desc">${esc(isAgent ? M.fields.mission : M.fields.description)}</label>
          <textarea id="pe-desc" rows="${Math.min(10, Math.max(4, descOf().length / 48 + 1 | 0))}"${ro}>${esc(descOf())}</textarea></div></section>
      ${groups.map(groupHtml).join("")}
    </form>
    ${isAgent ? "" : `<div class="pe-eff" aria-live="polite"></div>`}
    ${ctx.readOnly ? "" : `<div class="pe-save-row"><span class="pe-status" role="status"></span>
      <button class="btn btn-primary" type="button" id="pe-save">${esc(M.save)}</button></div>`}`;

  // 종류별 편집기(검사 · 기계 검사)
  const editors = new Map<string, GateEditor>();
  for (const f of FIELDS) if (f.editor) { const el = host.querySelector<HTMLElement>(`#pe-${f.key}`);
    if (el) editors.set(f.key, mountGateEditor(el, { kind: f.editor, value: props[f.key], readOnly: ctx.readOnly, onChange: () => paint() })); }
  const val = (k: string) => {
    const ed = editors.get(k); if (ed) return canon(ed.get().value);
    if (k === "writes_graph") { const fs = host.querySelector<HTMLElement>("#pe-writes_graph"); if (!fs) return "";
      return [...fs.querySelectorAll<HTMLInputElement>("input:checked")].map((x) => x.value).concat(linesOf(fs.dataset.extra || "")).join("\n"); }
    return host.querySelector<HTMLInputElement | HTMLTextAreaElement>(`#pe-${k}`)?.value ?? ""; };
  /** 칸의 지금 값으로 만든 props(미리보기와 저장에 쓴다). 고치지 않은 칸은 읽은 값 그대로다. */
  const draft = (): Props => {
    const out: Props = { ...props };
    for (const f of FIELDS) {
      const text = val(f.key);
      const changed = f.editor ? text !== f.read(props) : f.key === "test_command" ? text.trim() !== f.read(props).trim()
        : f.key === "writes_graph" ? JSON.stringify(linesOf(text).sort()) !== JSON.stringify(listLines(props.writes_graph).slice().sort())
        : fieldChanged(text, f.key === "capabilities" ? (props.capabilities as any)?.deny : props[f.key]);
      if (changed) { const v = f.write(text, props); if (v !== undefined) out[f.key] = v; }
    }
    return out;
  };
  const originLabel = (o: string) => o === "agent" ? A.originAgent : o === "procedure" ? A.originProcedure : A.originPolicy(policySlug(o));
  const paint = () => {
    if (isAgent) { for (const f of FIELDS) { const box = host.querySelector<HTMLElement>(`[data-repeat="${f.key}"]`); if (box) box.hidden = true; } return; }
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
  host.querySelector(".pe-form")!.addEventListener("change", paint);

  const status = (s: string) => { const x = host.querySelector(".pe-status"); if (x) x.textContent = s; };
  host.querySelector("#pe-save")?.addEventListener("click", async () => {
    const gateErr = [...editors.values()].flatMap((e) => e.get().errors);
    if (gateErr.length) { status(M.gate.saveBlocked); return; }
    const next = draft();
    const pf = foldPersonOnly(linesOf(val("person_only")), props.person_only);
    if (!pf.ok) { status(M.badPersonOnly(pf.bad.join(", "))); return; }
    const patch: Props = {};
    for (const f of FIELDS) if (JSON.stringify(next[f.key]) !== JSON.stringify(props[f.key])) patch[f.key] = next[f.key];
    const desc = val("desc").trim(), title = val("title").trim();
    const descChanged = desc !== descOf(), titleChanged = !!title && title !== ctx.title;
    if (descChanged) { if (isAgent) patch.mission = desc; else patch.description = desc; }
    if (!Object.keys(patch).length && !titleChanged) { status(M.noChange); return; }
    status(A.saving);
    let res: any;
    // 하는 일은 한 줄 요약(one_liner)에도 싣는다: 로스터 · 프롬프트 목록이 그것을 먼저 읽는다(api/roles.py)
    try { res = await api.editNode({ id: ctx.id, type: "concept", ...(titleChanged ? { title } : {}), ...(descChanged ? { one_liner: desc } : {}),
      ...(Object.keys(patch).length ? { props_patch: patch } : {}) }, ctx.rev); }
    catch (e) { res = { executed: false, error: messageOf(e, "") }; }
    if (isWriteConflict(res) || /rev|revision|conflict/i.test(String(res?.error || ""))) { status(M.stale); return; }
    if (res?.executed === false || (res?.warnings || []).length) { status(M.saveFail); return; }
    let fresh: any = null;
    try { fresh = await api.nodeContext(cfg.owner, ctx.id, 1); } catch { fresh = null; }
    if (!fresh?.executed) { status(M.saved); return; }
    const nt = String(fresh.title || title || ctx.title);
    ctx.onSaved({ props: (fresh.props || {}) as Props, oneLiner: String(fresh.one_liner || ""), rev: Number(fresh.rev), title: nt });
    mountProcedurePanel(host, { ...ctx, title: nt, props: (fresh.props || {}) as Props, oneLiner: String(fresh.one_liner || ""), rev: Number(fresh.rev) });
    const s = host.querySelector(".pe-status"); if (s) s.textContent = M.saved;
  });
}
