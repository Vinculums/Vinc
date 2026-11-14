/** 역할 편집 면의 목록 칸(`never` · `person_only`)을 줄로 펴고 다시 접는다.
 *
 *  `person_only` 는 `{action, reason}` 객체 목록이다(옛 팩은 문자열 목록). 칸에는 한 줄에 하나씩
 *  `action: reason` 으로 펴고, 저장할 때 같은 꼴로 접는다. 문자열과 객체를 한 목록에 섞지 않는다 —
 *  섞인 목록은 서버가 경고하고 사람 확인 게이트가 그 행동을 못 알아본다.
 *  칸을 고치지 않았으면 읽은 값을 그대로 다시 보낸다: 폈다 접은 값이 원래와 같다는 보장이 없다.
 *  근거: [[vinc.canon.agent-roles §3]] */

/** 행동 키 규칙(`api/role_contract._ACTION`). */
const ACTION_KEY = /^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$/;

export type PersonAction = { action: string; reason: string };

export const isPersonAction = (v: unknown): v is PersonAction =>
  !!v && typeof v === "object" && !Array.isArray(v)
  && typeof (v as PersonAction).action === "string" && typeof (v as PersonAction).reason === "string";

/** 저장된 목록 → 칸의 줄. 객체는 `action: reason`, 문자열은 그대로. 둘 다 아닌 값은 JSON 으로 보인다
 *  (숨기면 사람이 고친 뒤 저장할 때 조용히 빠진다). */
export function listLines(v: unknown): string[] {
  if (!Array.isArray(v)) return [];
  return v.map((x) => typeof x === "string" ? x : isPersonAction(x) ? `${x.action}: ${x.reason}` : JSON.stringify(x));
}

/** 여러 줄 칸 → 목록. 앞뒤 공백을 걷고 빈 줄은 버린다. */
export const linesOf = (s: string): string[] => s.split("\n").map((x) => x.trim()).filter(Boolean);

/** 칸이 읽은 값에서 바뀌었는가. 공백만 다른 것은 바뀐 것이 아니다. */
export const fieldChanged = (text: string, stored: unknown): boolean =>
  linesOf(text).join("\n") !== listLines(stored).map((x) => x.trim()).filter(Boolean).join("\n");

export type FoldedPersonOnly = { ok: true; value: Array<string | PersonAction> } | { ok: false; bad: string[] };

/** 칸의 줄 → 저장할 `person_only`. 한 목록 단위로 정한다.
 *  - 모든 줄이 `행동_키: 이유` 꼴이고 키가 겹치지 않으면 객체 목록.
 *  - 아니면, 읽은 값에 객체가 하나라도 있었으면(구조화된 계약) 꼴이 틀리거나 겹친 줄을 돌려주고
 *    저장을 막는다. 객체가 없었으면(옛 팩 · 새 역할) 적은 그대로 문자열 목록이다. */
export function foldPersonOnly(lines: string[], stored: unknown): FoldedPersonOnly {
  const seen = new Set<string>();
  const parsed = lines.map((line) => {
    const m = /^([^\s:]+)\s*:\s*(\S.*)$/.exec(line);
    if (!m || !ACTION_KEY.test(m[1]) || seen.has(m[1])) return null;
    seen.add(m[1]);
    return { action: m[1], reason: m[2].trim() };
  });
  if (parsed.every((p) => p !== null)) return { ok: true, value: parsed as PersonAction[] };
  const structured = Array.isArray(stored) && stored.some(isPersonAction);
  if (structured) return { ok: false, bad: lines.filter((_, i) => parsed[i] === null) };
  return { ok: true, value: lines };
}

/* ── 실행 정책 칸 넷(output_kind · act_policy · budget · capabilities) ─────────────
 *  어휘는 `api/role_policy_contract.py` 와 같다. 칸을 고치지 않았으면 읽은 값을 그대로 보내고,
 *  비우면 키째 뺀다(빈 객체를 정책으로 남기지 않는다). */

export const OUTPUT_KINDS = ["prose", "code", "verdict", "write_proposal", "none"] as const;
const ACT_MODES = ["act", "act_and_report", "ask_first", "never"] as const;
const BUDGET_INTS = ["tool_calls", "model_calls", "tokens"] as const;
const BUDGET_NUMBERS = ["cost_usd", "wall_seconds"] as const;
export const BUDGET_KEYS = [...BUDGET_INTS, ...BUDGET_NUMBERS] as const;
export const CAPABILITY_LISTS = ["allow", "approve", "deny"] as const;
const CAPABILITY_ID = /^[a-z][a-z0-9_-]*(?:\.[a-z0-9_-]+)*(?:\.\*)?$/;

/** 키를 지운다는 뜻의 값. 저장할 때 props 에서 그 키를 뺀다. */
export const ABSENT = undefined;
export type Folded<T> = { ok: true; value: T | undefined } | { ok: false; bad: string[] };

const isRecord = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v);

/** 저장된 act_policy → 칸의 줄(`action: mode`). 객체가 아니면 빈 칸이다. */
export const actLines = (v: unknown): string[] =>
  isRecord(v) ? Object.entries(v).map(([k, m]) => `${k}: ${m}`) : [];

/** 칸의 줄 → act_policy. 사람만 하는 행동은 ask_first 나 never 만 받는다(허브가 경고하고 런이 거절한다). */
export function foldActPolicy(lines: string[], personActions: string[]): Folded<Record<string, string>> {
  const value: Record<string, string> = {};
  const bad: string[] = [];
  for (const line of lines) {
    const m = /^([^\s:]+)\s*:\s*(\S+)$/.exec(line);
    const ok = m && ACTION_KEY.test(m[1]) && !(m[1] in value)
      && (ACT_MODES as readonly string[]).includes(m[2])
      && !(personActions.includes(m[1]) && (m[2] === "act" || m[2] === "act_and_report"));
    if (!ok) bad.push(line);
    else value[m![1]] = m![2];
  }
  if (bad.length) return { ok: false, bad };
  return { ok: true, value: lines.length ? value : ABSENT };
}

/** 저장된 budget → 칸마다의 글자. 없는 칸은 빈 글자다. */
export const budgetFields = (v: unknown): Record<string, string> =>
  Object.fromEntries(BUDGET_KEYS.map((k) => [k, isRecord(v) && typeof v[k] === "number" ? String(v[k]) : ""]));

/** 칸의 글자 → budget. 앞의 셋은 0 이상 정수, 뒤의 둘은 0 이상 수. 전부 비면 키를 뺀다. */
export function foldBudget(fields: Record<string, string>): Folded<Record<string, number>> {
  const value: Record<string, number> = {};
  const bad: string[] = [];
  for (const key of BUDGET_KEYS) {
    const text = (fields[key] || "").trim();
    if (!text) continue;
    const n = Number(text);
    const whole = (BUDGET_INTS as readonly string[]).includes(key);
    if (!/^\d+(\.\d+)?$/.test(text) || !Number.isFinite(n) || n < 0 || (whole && !Number.isInteger(n))) bad.push(key);
    else value[key] = n;
  }
  if (bad.length) return { ok: false, bad };
  return { ok: true, value: Object.keys(value).length ? value : ABSENT };
}

/** 저장된 capabilities → 목록마다 쉼표로 이은 글자. */
export const capabilityFields = (v: unknown): Record<string, string> =>
  Object.fromEntries(CAPABILITY_LISTS.map((k) => [k, isRecord(v) && Array.isArray(v[k])
    ? (v[k] as unknown[]).map(String).join(", ") : ""]));

/** 칸의 글자 → capabilities. 같은 id 가 두 목록에 있으면 막는다. 전부 비면 키를 뺀다. */
export function foldCapabilities(fields: Record<string, string>): Folded<Record<string, string[]>> {
  const value: Record<string, string[]> = {};
  const bad: string[] = [];
  const seen = new Map<string, string>();
  for (const key of CAPABILITY_LISTS) {
    const ids = (fields[key] || "").split(",").map((x) => x.trim()).filter(Boolean);
    for (const id of ids) {
      if (!CAPABILITY_ID.test(id) || (seen.has(id) && seen.get(id) !== key)) bad.push(id);
      seen.set(id, key);
    }
    if (ids.length) value[key] = [...new Set(ids)];
  }
  if (bad.length) return { ok: false, bad };
  return { ok: true, value: Object.keys(value).length ? value : ABSENT };
}

/** 두 정책 값이 같은가(키 순서 무관). 고치지 않은 칸을 가려 읽은 값을 그대로 보내는 데 쓴다. */
export function samePolicy(a: unknown, b: unknown): boolean {
  if (isRecord(a) && isRecord(b)) {
    const ka = Object.keys(a), kb = Object.keys(b);
    return ka.length === kb.length && ka.every((k) => samePolicy(a[k], b[k]));
  }
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((x, i) => samePolicy(x, b[i]));
  return a === b;
}
