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
 *  문서 열은 지도 옆에 `autoFocus:false` 로 열린다(열자마자 키보드를 뺏으면 `f`·Esc
 *  같은 지도 조작이 글자로 들어간다). 포커스가 지도·트리에 남아 있어도 숫자가
 *  `#search` 로 새어 나갔다(F-DOC-KB-01, 2026-09-15). 편집기가 보이면 표면 단축키는
 *  쉰다 — 사용자가 타이핑을 시작하면 그때 포커스를 에디터로 옮긴다(main.ts). */
export function blocksSurfaceShortcuts(target: EventTarget | null): boolean {
  if (isTypingTarget(target) || isTypingTarget(document.activeElement)) return true;
  const pane = document.getElementById("docpane");
  if (!pane || pane.hidden) return false;
  // 읽기 전용도 `contenteditable="false"` — 속성 값으로 거르면 빠진다(F-DOC-KB-01).
  return !!pane.querySelector(".cm-content");
}
