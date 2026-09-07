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
