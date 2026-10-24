/** 역할 편집 면의 목록 칸(`never` · `person_only`)을 줄로 펴고 다시 접는다.
 *
 *  `person_only` 는 `{action, reason}` 객체 목록이다(옛 팩은 문자열 목록). 칸에는 한 줄에 하나씩
 *  `action: reason` 으로 펴고, 저장할 때 같은 꼴로 접는다. 문자열과 객체를 한 목록에 섞지 않는다 —
 *  섞인 목록은 서버가 경고하고 사람 확인 게이트가 그 행동을 못 알아본다.
 *  칸을 고치지 않았으면 읽은 값을 그대로 다시 보낸다: 폈다 접은 값이 원래와 같다는 보장이 없다.
 *  근거: [[vinc.canon.agent-roles §3]] */

/** 행동 키 규칙(`api/role_contract._ACTION`). */
export const ACTION_KEY = /^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$/;

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
