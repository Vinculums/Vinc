/** 전역 단축키가 비켜설 자리 — 브라우저도 프레임워크도 없이 돈다.
 *
 *     node --experimental-strip-types app/dev/keys-check.ts
 *
 *  ⚠ **왜 이게 있어야 하나**: 이 판정은 여태 거울이 둘이었다 — `main.ts` 의 전역
 *  핸들러와 `view.ts` 의 표면 핸들러가 각자 구현했고, 한쪽만 `contenteditable` 을
 *  보게 고쳐져 있었다. CodeMirror 6 의 편집면이 `div.cm-content[contenteditable]`
 *  이라 나머지 한쪽은 절대 비켜서지 않았고, 편집기에 포커스가 있어도 `1`·`2`·`f`·`?`
 *  가 새어 나갔다(2026-09-07 실측 — `1`·`2` 는 본문에 글자를 넣으면서 동시에 표면을
 *  갈아엎었다). 지금은 `src/keys.ts` 한 벌이고, 그것이 **실제로 무엇을 막는지**를
 *  여기서 돌려 본다. 소스에 그 이름이 적혀 있는지가 아니라 판정 자체를 건다.
 *
 *  `keys.ts` 가 DOM 을 안 쓰는 것(오리 타이핑)이 이 검사가 성립하는 조건이다. */
import assert from "node:assert/strict";
import { isTypingTarget } from "../src/keys.ts";

let n = 0;
const ok = (label: string) => { n += 1; console.log("  ok", label); };

// ── 1. 이 결함 자체 — CM6 의 편집면은 DIV 다 ────────────────────────────────
assert.equal(isTypingTarget({ tagName: "DIV", isContentEditable: true }), true);
ok("contenteditable 인 DIV 는 타이핑이다(CM6 편집면)");

// 여태의 판정(`tagName === "INPUT" || "TEXTAREA"`)이 살아 있으면 여기서 떨어진다.
assert.notEqual(isTypingTarget({ tagName: "DIV", isContentEditable: true }), false);
ok("tagName 만 보던 판정으로 되돌아가면 떨어진다");

// ── 2. 원래 막던 것들은 계속 막는다 ────────────────────────────────────────
for (const tag of ["INPUT", "TEXTAREA"]) {
  assert.equal(isTypingTarget({ tagName: tag }), true, tag);
}
ok("INPUT·TEXTAREA 는 그대로 타이핑이다");

// ── 3. 지도 위의 마크·캔버스는 단축키가 살아 있어야 한다 ────────────────────
for (const el of [{ tagName: "DIV" }, { tagName: "CANVAS" }, { tagName: "BUTTON" },
                  { tagName: "DIV", isContentEditable: false }]) {
  assert.equal(isTypingTarget(el), false, el.tagName);
}
ok("보통 요소는 단축키를 막지 않는다");

// ── 4. 대상이 없을 수 있다(document 로 오는 키) ────────────────────────────
assert.equal(isTypingTarget(null), false);
assert.equal(isTypingTarget({} as EventTarget), false);
ok("target 이 없거나 요소가 아니면 단축키가 산다");

console.log(`\nkeys: ${n}개 검사 통과`);
