/** 항목 페이지(A2) — 노드 상세를 넓게 열었을 때 그 노드가 무엇이고 어떻게 정해졌는지.
 *
 *  **문장을 만들지 않는다**(묻기와 같은 규율, D73): 여기 서는 글은 전부 스토어에 저장된 것을
 *  그대로 인용한 것이다 — 노드의 속성 문장과 관계에 저장된 근거 문장. 이 모듈은 무엇을
 *  어느 순서로 보일지만 고른다. 근거: [[vinc.research.graph-pages-and-marks-2026-10-25]] §5 A2
 *
 *  순수 함수만 둔다(DOM 없음) — 무엇을 인용하고 어떤 순서로 세우는지가 테스트로 잠긴다. */

/** 관계 한 줄 — node-context 의 `links.items`(evidence=1 일 때 문장까지). */
export type ItemLink = {
  id: string; title: string; domain?: string; type: string; dir: string;
  evidence?: boolean; evidence_text?: string;
  valid_from?: string; valid_until?: string;
  temporal?: { created_at?: string | null; valid_from?: string | null; valid_until?: string | null;
               superseded_by?: string | null } | null;
};

/** 인용할 문장 한 줄. `key` 는 속성 이름, 관계면 그 관계와 상대. */
export type Saying =
  | { from: "prop"; key: string; text: string }
  | { from: "relation"; type: string; dir: string; id: string; title: string; text: string };

/** 문장이 아닌 속성 — 기계가 읽는 열쇠라 인용하지 않는다(경로·식별자·날짜·상태). */
const NOT_SAID = new Set(["code", "subject_path", "figma", "measured_at", "measured_ref", "superseded_by",
  "valid_from", "valid_until", "date", "decided_at", "decided_by", "status", "values", "owner_words",
  "run_id", "role", "outcome", "trace_ref", "doc_id", "entity", "predicate", "scope", "value"]);
/** 먼저 세울 속성 — 결정이 무엇을 골랐고 왜인지가 첫 줄이다. 나머지는 이름순. */
const FIRST = ["ruling", "chosen", "why", "note", "summary", "description", "correction"];

/** 문장처럼 보이는가 — 띄어쓰기가 있고 짧은 꼬리표가 아니다. 270 글자 배열 같은 망가진
 *  속성(낱글자 배열)은 문자열이 아니라 여기서 걸러진다. */
const sentence = (v: unknown): v is string =>
  typeof v === "string" && v.trim().length > 12 && /\s/.test(v.trim());

export function sayings(props: Record<string, unknown> | null | undefined, links: ItemLink[]): Saying[] {
  const p = props || {};
  const keys = Object.keys(p).filter((k) => !NOT_SAID.has(k) && sentence(p[k]));
  keys.sort((a, b) => {
    const ia = FIRST.indexOf(a), ib = FIRST.indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b);
  });
  const out: Saying[] = keys.map((k) => ({ from: "prop" as const, key: k, text: (p[k] as string).trim() }));
  for (const l of links) {
    const text = (l.evidence_text || "").trim();
    // 근거가 실행 id 하나뿐인 관계(예: "run:abc")는 문장이 아니라 인용하지 않는다 — 그 관계는
    // "관련"으로만 남는다(A5 검토가 그것을 사람에게 돌린다).
    if (!sentence(text)) continue;
    out.push({ from: "relation", type: l.type, dir: l.dir, id: l.id, title: l.title, text });
  }
  return out;
}

/** 효력 시작일이 비어 있고 노드가 스스로 말하는 날이 있으면 그 날 — 한 번에 채울 값이다.
 *  추측하지 않는다: 결정이 적어 둔 `date`·`decided_at` 만 쓴다(기록 시각은 효력의 시작이 아니다). */
export function statedStart(validFrom: string | null | undefined,
                            props: Record<string, unknown> | null | undefined): string {
  if (validFrom) return "";
  const p = props || {};
  for (const k of ["date", "decided_at"]) {
    const v = p[k];
    if (typeof v === "string" && /^\d{4}(-\d{2}(-\d{2})?)?$/.test(v.trim())) return v.trim();
  }
  return "";
}

/** 어떻게 정해졌나의 한 걸음. `rel` 은 이 항목과의 관계(자기 자신은 "self"). */
export type Step = { id: string; title: string; date: string; ended: string;
  rel: "self" | "replaces" | "replaced_by" | "related" };

const dayOf = (s: string | null | undefined) => (s || "").slice(0, 10);

/** 같은 물음 위의 걸음들 — 이 항목, 이것이 대체한 것과 이것을 대체한 것(SUPERSEDES), 이것과
 *  관계를 맺은 다른 결정. 날짜순(효력 시작일, 없으면 기록 시각)이고 날짜가 없으면 맨 뒤다. */
export function decisionSteps(self: { id: string; title: string; validFrom?: string | null;
  validUntil?: string | null; createdAt?: string | null }, links: ItemLink[]): Step[] {
  const steps = new Map<string, Step>();
  steps.set(self.id, { id: self.id, title: self.title, date: dayOf(self.validFrom || self.createdAt),
    ended: dayOf(self.validUntil), rel: "self" });
  for (const l of links) {
    const isDecision = l.id.startsWith("decision:");
    const supersede = l.type === "SUPERSEDES";
    if (!supersede && !isDecision) continue;
    if (steps.has(l.id)) continue;
    const t = l.temporal || {};
    // `out` = 이 항목이 상대를 대체했다(새 것 → 옛 것 방향으로 쓴다, GRAPH.md).
    const rel: Step["rel"] = supersede ? (l.dir === "out" ? "replaces" : "replaced_by") : "related";
    steps.set(l.id, { id: l.id, title: l.title,
      date: dayOf(l.valid_from || t.valid_from || t.created_at),
      ended: dayOf(l.valid_until || t.valid_until), rel });
  }
  return [...steps.values()].sort((a, b) =>
    (a.date ? 0 : 1) - (b.date ? 0 : 1) || a.date.localeCompare(b.date) || a.id.localeCompare(b.id));
}
