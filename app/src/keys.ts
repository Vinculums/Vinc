/** 전역 단축키가 비켜설 자리 — 판정은 여기 **한 벌**만 산다.
 *
 *  `INPUT|TEXTAREA` 만 보면 안 된다: CodeMirror 6 의 편집면은
 *  `div.cm-content[contenteditable=true]` 라 그 검사에 **절대 안 걸린다.** 여태 이
 *  계약을 main.ts(전역)와 view.ts(표면)가 각자 구현했고 한쪽만 고쳐져 있어서,
 *  편집기에 포커스가 있어도 `1`·`2`·`f`·`?` 가 새어 나갔다 — `1`·`2` 는 본문에
 *  글자를 넣으면서 **동시에** 표면을 갈아엎었다(실측 2026-09-07).
 *
 *  DOM 을 안 쓴다(오리 타이핑). 그래서 `app/dev/keys-check.ts` 가 브라우저 없이
 *  이 판정을 그대로 돌린다 — 앱에는 JS 테스트 러너가 없다. */
export function isTypingTarget(target: EventTarget | null): boolean {
  const el = target as { tagName?: string; isContentEditable?: boolean } | null;
  if (!el) return false;
  return el.tagName === "INPUT" || el.tagName === "TEXTAREA"
    || el.isContentEditable === true;
}

/** 표면 단축키(1·2·f·?)를 막아야 하는가 — `isTypingTarget` 만으로는 부족하다.
 *
 *  문서 열은 View 옆에 `autoFocus:false` 로 열린다(열자마자 키보드를 뺏으면 `f`·Esc
 *  같은 조작이 글자로 들어간다). 그래서 포커스가 아직 View·트리에 남아 있는데도
 *  숫자가 `#search` 로 새어 나갔다 — 문서를 읽는 중에 화면이 통째로 갈렸다
 *  (F-DOC-KB-01, 2026-09-15 실측). 편집기가 떠 있으면 표면 단축키는 쉰다.
 *
 *  ⚠ **DOM 은 여기서 읽지 않는다.** 위 주석이 말하는 조건 — 이 파일이 DOM 을 안
 *  쓴다는 것 — 이 `app/dev/keys-check.ts` 를 성립시킨다. 처음 고칠 때 이 함수가
 *  `document.getElementById` 를 직접 불렀고, 그러면 노드에서 도는 그 검사가 첫
 *  조건에서 단락될 때만 우연히 통과한다. 문서 열이 떠 있는지는 부르는 쪽이 재서
 *  넘긴다(`doc-editor.docEditorOpen`). */
export function blocksSurfaceShortcuts(
  target: EventTarget | null,
  active: EventTarget | null,
  docEditorOpen: boolean,
): boolean {
  return docEditorOpen || isTypingTarget(target) || isTypingTarget(active);
}
