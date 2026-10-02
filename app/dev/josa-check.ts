/** 조사 고르기(`j`) — 브라우저 없이 돈다.
 *
 *     node --experimental-strip-types app/dev/josa-check.ts
 *
 *  노드 이름을 문장에 끼우는 자리마다 이 함수가 조사를 고른다(VIN-259). 한글 끝은 받침으로
 *  정확히, 숫자는 읽는 소리로, 영문은 끝 글자로 어림한다. 어림이 틀리는 꼴은 아래 마지막
 *  묶음에 적어 둔다(알고 있는 한계다). */
import assert from "node:assert/strict";
import { j } from "../src/josa.ts";

let n = 0;
const ok = (label: string) => { n += 1; console.log("  ok", label); };

// 한글 — 받침 없음 · 있음 · ㄹ 받침(으로만 갈린다)
assert.equal(j("문서", "이"), "가");
assert.equal(j("토큰", "이"), "이");
assert.equal(j("파일", "으로"), "로");
assert.equal(j("책", "으로"), "으로");
assert.equal(j("모드", "과"), "와");
assert.equal(j("'토큰'", "을"), "을");          // 따옴표는 건너뛴다
ok("한글 끝은 받침으로 고른다(ㄹ 받침은 '로')");

// 숫자 — 읽는 소리(일·칠·팔은 ㄹ, 영·삼·육은 받침)
assert.equal(j("v0.9.6", "은"), "은");
assert.equal(j("2", "을"), "를");
assert.equal(j("7", "으로"), "로");
ok("숫자는 읽는 소리로 고른다");

// 영문 — 약어는 글자 이름, 낱말은 끝 글자
assert.equal(j("MCP", "을"), "를");             // 엠씨피
assert.equal(j("URL", "으로"), "로");           // 유알엘
assert.equal(j("PDF", "은"), "는");
assert.equal(j("Design token", "과"), "과");    // 토큰
assert.equal(j("Dark mode", "을"), "를");       // 모드
assert.equal(j("Semantic layer", "은"), "는");  // 레이어
assert.equal(j("Table", "을"), "을");           // 테이블
assert.equal(j("report.pdf", "을"), "를");
assert.equal(j("Graph steward", "이"), "가");   // 스튜어드
ok("영문은 약어면 글자 이름, 낱말이면 끝 글자로 어림한다");

// 알려진 한계 — 받침으로 읽히는 파열음 끝(챗·맵)은 어림이 틀린다
assert.equal(j("chat", "이"), "가");            // 바른 것은 "이"(챗이)
ok("파열음 끝 영단어는 틀린다(ponytail 주석의 한계)");

console.log(`josa-check: ${n} passed`);
