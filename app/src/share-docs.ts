/** 공유 시트가 행을 끈 뒤 어떤 원문이 팩에 남는지 정한다.
 *
 *  ⚠ 이 파일은 import 0 이어야 한다 — `app/dev/share-docs-check.ts` 가 브라우저 없이
 *  plain node 로 이 규칙을 실제로 돌린다(`packchunks.ts` 와 같은 자리). */

/** 문서 미러 개념의 id 접두 — 서버의 `api/docstore._DOC_MIRROR_PREFIX` 와 같은 값. */
export const DOC_MIRROR = "concept:doc.";

/**
 * 제외 제스처를 **원문에도** 적용한다. 원문은 두 규칙을 모두 통과해야 남는다.
 *
 * ① 미러 행(`concept:doc.<doc_id>`)은 이 화면에서 그 문서 자체다. 끄면 원문도 빠진다.
 * ② 원문은 **자기를 끌어온 노드가 하나라도 남아 있을 때만** 나간다(`refs` = 서버의
 *    `doc_refs`, 서버가 원문을 고른 기준과 같다). 그래서 미러가 아닌 개념 하나를 꺼도
 *    남은 노드가 그 문서를 언급하면 원문은 남고, 행을 전부 끄면 원문도 0 이다.
 *    미러 행이 없는 스토어(원문이 전부 본문 언급으로 실린 경우)에서는 ② 가 유일한 길이다.
 *
 * `refs` 가 없는 응답(대응표를 모르는 구 허브)은 ① 만 쓴다.
 * 근거: [[decision:share-documents-follow-the-nodes-that-carry-them]]
 *
 * 접두를 `startsWith` 로 보지 않는다: 호스티드는 노드 PK 에 owner 를 합성하므로
 * 물리 키가 `owner` + U+001F + `concept:doc.<id>` 다(`store/pkns.py`). 자리를 찾아
 * 뒤만 취하면 앱이 그 규약을 몰라도 된다 — `documents[].doc_id` 는 서버가 이미
 * 벗겨서 보낸다.
 */
export function keptDocs(documents: any[] | undefined, excluded: Set<string>,
                         refs?: Record<string, string[]>, kept?: Set<string>): any[] {
  const dropped = new Set<string>();
  for (const id of excluded) {
    const at = String(id).indexOf(DOC_MIRROR);
    if (at >= 0) dropped.add(String(id).slice(at + DOC_MIRROR.length));
  }
  const carried = (d: any) => !refs || !kept
    || (refs[d?.doc_id] || []).some((id) => kept.has(id));
  return (documents || []).filter((d) => !dropped.has(d?.doc_id) && carried(d));
}
