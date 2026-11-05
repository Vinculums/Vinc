/** 검사(gates)와 기계로 확인하는 금지(never_checks)의 종류별 편집기.
 *
 *  형식은 서버 계약 그대로다(`api/role_gate_contract.py` · `vinc_mcp/role_output_gate.py`):
 *  - 검사 한 줄 = { id, check, on_fail }. id 는 `[a-z][a-z0-9_]{0,63}`, on_fail 은 block(멈춤) · interrupt(사람에게 묻기).
 *  - check.type 별 값: command_success { command, repos } · forbidden_actions { actions } · rendered_copy { rules } ·
 *    person_only · side_effect_evidence · handoff_manifest · korean_comment_allowed(값 없음).
 *  - 구조로 된 검사 목록에는 person_only(interrupt) · side_effect_evidence(block) · handoff_manifest(block) 이
 *    있어야 한다. 그 셋은 이 편집기에서 지우거나 실패 처리를 바꿀 수 없다.
 *  never_checks 는 같은 check 만 늘어놓는다(id · on_fail 이 없다).
 *  서버가 받지 않는 값은 저장 전에 여기서 막는다: 서버는 형식이 틀리면 경고만 남기고, 실행 하네스는 그 검사를 무시한다. */
import { COPY } from "./copy";
import { esc } from "./components";

export const GATE_TYPES = ["command_success", "forbidden_actions", "rendered_copy", "person_only", "side_effect_evidence",
  "handoff_manifest", "korean_comment_allowed"] as const;
type GateType = (typeof GATE_TYPES)[number];
/** 서버가 받는 리포(command_success.repos). 서버의 허용 목록과 같아야 한다. */
export const GATE_REPOS = ["Vinc", "Vinc-homepage"] as const;
const ACTIONS = ["merge", "commit", "push", "edit", "sign_off", "deploy", "english_value", "graph_delete", "graph_merge", "migration"] as const;
const COPY_RULES = ["variable_particle", "em_dash", "second_person", "object_honorific", "english_value"] as const;
const REQUIRED: Record<string, "block" | "interrupt"> = { person_only: "interrupt", side_effect_evidence: "block", handoff_manifest: "block" };
const BARE = new Set(["person_only", "side_effect_evidence", "handoff_manifest", "korean_comment_allowed"]);
const ID_RE = /^[a-z][a-z0-9_]{0,63}$/;

type Check = { type: string; command?: string; repos?: string[]; actions?: string[]; rules?: string[] };
type Row = { id: string; check: Check; on_fail: "block" | "interrupt"; orig?: unknown };

const fixId = (id: string): string => { const s = id.toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^[^a-z]+/, "").slice(0, 64); return s || "check"; };
const checkErrors = (c: Check): string[] => {
  const G = COPY.ui.procedure.gate;
  if (!(GATE_TYPES as readonly string[]).includes(c.type)) return [G.errType];
  if (c.type === "command_success") return [...(!c.command?.trim() ? [G.errCommand] : []), ...(!c.repos?.length ? [G.errRepos] : [])];
  if (c.type === "forbidden_actions" && !c.actions?.length) return [G.errActions];
  if (c.type === "rendered_copy" && !c.rules?.length) return [G.errRules];
  return [];
};
const clean = (c: Check): Check => c.type === "command_success" ? { type: c.type, command: (c.command || "").trim(), repos: [...(c.repos || [])] }
  : c.type === "forbidden_actions" ? { type: c.type, actions: [...(c.actions || [])] }
  : c.type === "rendered_copy" ? { type: c.type, rules: [...(c.rules || [])] } : { type: c.type };

export interface GateEditor { get(): { value: unknown; errors: string[] } }

/** `host` 안에 편집기를 그린다. kind "gates" = 검사 목록, "checks" = never_checks. 값이 없으면 빈 목록에서 시작한다. */
export function mountGateEditor(host: HTMLElement, opts: { kind: "gates" | "checks"; value: unknown; readOnly: boolean; onChange: () => void }): GateEditor {
  const G = COPY.ui.procedure.gate, isGates = opts.kind === "gates", ro = opts.readOnly;
  const src = Array.isArray(opts.value) ? opts.value : [];
  // 옛 문자열 검사(명령 한 줄)는 명령 성공 검사로 읽는다. 구조가 아닌 값은 손대지 않고 저장 때 그대로 둔다
  let rows: Row[] = src.map((g: any, i: number) => isGates
    ? (typeof g === "string" ? { id: `command_${i + 1}`, check: { type: "command_success", command: g, repos: [...GATE_REPOS] }, on_fail: "block" as const, orig: g }
      : { id: String(g?.id || `check_${i + 1}`), check: { ...(g?.check || { type: "" }) }, on_fail: g?.on_fail === "interrupt" ? "interrupt" as const : "block" as const, orig: g })
    : { id: "", check: { ...(g || { type: "" }) }, on_fail: "block" as const, orig: g });
  let touched = false;

  const chk = (name: string, value: string, on: boolean, label: string, i: number) =>
    `<label class="pe-check"><input type="checkbox" data-i="${i}" data-set="${name}" value="${esc(value)}"${on ? " checked" : ""}${ro ? " disabled" : ""}><span>${esc(label)}</span></label>`;
  const rowHtml = (r: Row, i: number) => {
    const req = isGates && r.check.type in REQUIRED, c = r.check;
    const typeSel = req || ro ? `<span class="ge-type">${esc(G.types[c.type as GateType] || c.type)}</span>`
      : `<select class="ge-type-pick" data-i="${i}" aria-label="${esc(G.typeLabel)}">${GATE_TYPES.filter((t) => !isGates || !(t in REQUIRED) || t === c.type)
        .map((t) => `<option value="${t}"${t === c.type ? " selected" : ""}>${esc(G.types[t])}</option>`).join("")}</select>`;
    let body = "";
    if (c.type === "command_success") body = `<input class="ge-cmd" data-i="${i}" value="${esc(c.command || "")}" placeholder="${esc(G.commandPh)}" aria-label="${esc(G.command)}"${ro ? " readonly" : ""}>
      <div class="ge-opts"><span class="ge-k">${esc(G.repos)}</span>${GATE_REPOS.map((x) => chk("repos", x, !!c.repos?.includes(x), x, i)).join("")}</div>`;
    else if (c.type === "forbidden_actions") body = `<div class="ge-opts">${ACTIONS.map((x) => chk("actions", x, !!c.actions?.includes(x), G.actions[x], i)).join("")}</div>`;
    else if (c.type === "rendered_copy") body = `<div class="ge-opts">${COPY_RULES.map((x) => chk("rules", x, !!c.rules?.includes(x), G.rules[x], i)).join("")}</div>`;
    const hint = G.hints[c.type as GateType] ? `<p class="ge-hint">${esc(G.hints[c.type as GateType])}</p>` : "";
    const fail = !isGates ? "" : req || ro ? `<span class="ge-fail">${esc(G.onFail[r.on_fail])}</span>`
      : `<select class="ge-fail-pick" data-i="${i}" aria-label="${esc(G.onFailLabel)}">${(["block", "interrupt"] as const).map((f) =>
        `<option value="${f}"${f === r.on_fail ? " selected" : ""}>${esc(G.onFail[f])}</option>`).join("")}</select>`;
    const badId = isGates && !ID_RE.test(r.id) ? `<p class="ge-warn">${esc(G.badId(r.id, fixId(r.id)))}</p>` : "";
    const errs = checkErrors(c);
    return `<div class="ge-row${req ? " ge-req" : ""}" data-i="${i}"><div class="ge-head">${typeSel}${fail}${req || ro ? (req ? `<span class="ge-lock">${esc(G.required)}</span>` : "")
      : `<button type="button" class="ge-x" data-del="${i}" aria-label="${esc(G.remove)}">✕</button>`}</div>${hint}${body}${badId}${errs.length && touched ? `<p class="ge-err">${esc(errs.join(" "))}</p>` : ""}</div>`;
  };
  const paint = () => {
    host.innerHTML = `<div class="ge-list">${rows.map(rowHtml).join("") || `<p class="hint">${esc(isGates ? G.emptyGates : G.emptyChecks)}</p>`}</div>`
      + (ro ? "" : `<button type="button" class="btn ge-add">+ ${esc(isGates ? G.addGate : G.addCheck)}</button>`);
  };
  paint();
  const changed = (repaint = false) => { touched = true; if (repaint) paint(); opts.onChange(); };
  host.addEventListener("click", (e) => {
    const t = e.target as HTMLElement;
    if (t.closest(".ge-add")) { const n = rows.filter((r) => r.check.type === "command_success").length + 1;
      rows.push({ id: `command_${n}`, check: { type: "command_success", command: "", repos: [GATE_REPOS[0]] }, on_fail: "block" }); changed(true);
      host.querySelector<HTMLInputElement>(".ge-row:last-child .ge-cmd")?.focus(); return; }
    const del = t.closest<HTMLElement>("[data-del]"); if (del) { rows.splice(Number(del.dataset.del), 1); changed(true); }
  });
  host.addEventListener("change", (e) => {
    const t = e.target as HTMLInputElement | HTMLSelectElement, i = Number(t.dataset.i), r = rows[i]; if (!r) return;
    if (t.classList.contains("ge-type-pick")) { r.check = { type: t.value, ...(t.value === "command_success" ? { command: "", repos: [GATE_REPOS[0]] } : {}) }; r.orig = undefined;
      if (isGates && !r.id.startsWith(t.value)) r.id = `${t.value}_${i + 1}`; changed(true); return; }
    if (t.classList.contains("ge-fail-pick")) { r.on_fail = t.value as Row["on_fail"]; r.orig = undefined; changed(); return; }
    const set = t.dataset.set as "repos" | "actions" | "rules" | undefined;
    if (set) { const cur = new Set(r.check[set] || []); (t as HTMLInputElement).checked ? cur.add(t.value) : cur.delete(t.value);
      const order: readonly string[] = set === "repos" ? GATE_REPOS : set === "actions" ? ACTIONS : COPY_RULES;
      r.check[set] = order.filter((x) => cur.has(x)); r.orig = undefined; changed(); }
  });
  host.addEventListener("input", (e) => { const t = e.target as HTMLInputElement; if (!t.classList.contains("ge-cmd")) return;
    const r = rows[Number(t.dataset.i)]; if (r) { r.check.command = t.value; r.orig = undefined; touched = true; opts.onChange(); } });

  return {
    get() {
      // 고치지 않은 줄은 읽은 값 그대로 돌려준다(키 순서까지): 손대지 않았으면 "바뀜"으로 세지 않는다
      if (!touched) return { value: opts.value ?? [], errors: [] };
      const errors = rows.flatMap((r) => checkErrors(r.check));
      if (isGates) {
        const ids = new Set<string>();
        const value = rows.map((r) => { let id = ID_RE.test(r.id) ? r.id : fixId(r.id), k = 2; while (ids.has(id)) id = `${fixId(r.id)}_${k++}`; ids.add(id);
          return { id, check: clean(r.check), on_fail: r.check.type in REQUIRED ? REQUIRED[r.check.type] : r.on_fail }; });
        // 구조로 된 목록에는 필수 셋이 있어야 한다는 계약은 역할(Agent)에만 걸린다. 절차 쪽은 하네스가 Agent 의 셋과 합친다
        return { value, errors };
      }
      return { value: rows.map((r) => clean(r.check)), errors };
    },
  };
}

/** 저장 전후 비교용: 키 순서와 상관없이 같은 값이면 같은 글. */
export function canon(v: unknown): string {
  const walk = (x: unknown): unknown => Array.isArray(x) ? x.map(walk) : x && typeof x === "object"
    ? Object.fromEntries(Object.keys(x as object).sort().map((k) => [k, walk((x as Record<string, unknown>)[k])])) : x;
  return JSON.stringify(walk(v ?? []));
}

export const isBareCheck = (t: string): boolean => BARE.has(t);
