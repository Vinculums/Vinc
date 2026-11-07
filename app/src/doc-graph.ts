/** 문서 옆 칸의 "그래프에서"(A1) — 본문이 이름으로 부른 결정을 찾는다.
 *
 *  문서는 문서 화면 그대로가 페이지다. 옆 칸은 이 문서에 관한 결정과, 본문이 id 로 부르는데
 *  관계가 없는 결정을 보인다. 이을 때 근거는 그 이름이 선 **본문의 그 줄**이다 — 만든
 *  문장이 아니라 저장된 문장을 근거로 단다(묻기와 같은 규율).
 *  근거: [[vinc.research.graph-pages-and-marks-2026-10-25]] §5 A1 */

/** 결정 id 의 모양 — `decision:` 다음 소문자·숫자·마침표·하이픈. `[[…]]` 인용도 같은 id 다. */
const DECISION_ID = /\bdecision:[a-z0-9][a-z0-9.-]*[a-z0-9]/g;
/** 근거로 달 줄의 상한(글자). 긴 문단은 앞부분만 싣는다. */
const EVIDENCE_MAX = 300;

/** 본문이 부른 결정들 — 처음 나온 순서, 한 번씩. `line` 은 처음 나온 줄(앞뒤 공백 없이). */
export function namedDecisions(text: string): { id: string; line: string }[] {
  const out: { id: string; line: string }[] = [];
  const seen = new Set<string>();
  for (const line of text.split("\n")) {
    for (const m of line.matchAll(DECISION_ID)) {
      if (seen.has(m[0])) continue;
      seen.add(m[0]);
      const t = line.trim();
      out.push({ id: m[0], line: t.length > EVIDENCE_MAX ? t.slice(0, EVIDENCE_MAX - 1) + "…" : t });
    }
  }
  return out;
}

/** 문서의 미러 개념 — 문서에 관한 관계는 이 노드에 매달린다(문서 자체는 노드가 아니다). */
export const docMirrorId = (docId: string): string => `concept:doc.${docId}`;
