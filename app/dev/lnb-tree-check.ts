/** 좌측 트리 펼침 규칙 자체 검사 — 브라우저 없이 돈다.
 *
 *     node --experimental-strip-types app/dev/lnb-tree-check.ts
 *
 *  `lnb-tree.ts` 가 import 0 인 것이 성립 조건이다. */
import assert from "node:assert/strict";
import { applyDefaults, defaultOpenPaths, foldsOwnRows, ownKey, type PathNode } from "../src/lnb-tree.ts";

const n = (path: string, ...kids: PathNode[]): PathNode => ({ path, kids });
// 계정 그래프 모양: vinc → design → {surfaces, view}, engineering → mcp-server → directory
const tops = [
  n("vinc",
    n("vinc/design", n("vinc/design/surfaces"), n("vinc/design/view")),
    n("vinc/engineering", n("vinc/engineering/mcp-server", n("vinc/engineering/mcp-server/directory"))),
    n("vinc/agent")),               // 하위 주제 없음
  n("art"),                          // 1단계 잎 주제
];

// 영역까지만 보이게: 1단계 중 하위 주제가 있는 것만 연다. 잎 주제와 2단계 이하는 닫힌다.
assert.deepEqual(defaultOpenPaths(tops), ["vinc"]);
// 깊이를 넘기면 그만큼 더 연다(예전 기본은 2였다).
assert.deepEqual(defaultOpenPaths(tops, 2), ["vinc", "vinc/design", "vinc/engineering"]);

// 처음 적용하면 맨 위 주제 하나가 펴진다.
const expanded = new Set<string>();
const seen = new Set<string>();
assert.equal(applyDefaults(tops, expanded, seen), 1);
assert.deepEqual([...expanded].sort(), ["vinc"]);

// 사용자가 vinc 를 접었다 → 다시 그려도 기본이 되살리지 않는다.
expanded.delete("vinc");
assert.equal(applyDefaults(tops, expanded, seen), 0);
assert.ok(!expanded.has("vinc"));

// 새 주제가 나중에 생기면(종 레일을 바꾸거나 적재 후) 그 주제만 기본을 받는다.
const later = [...tops, n("physics", n("physics/quantum"))];
assert.equal(applyDefaults(later, expanded, seen), 1);
assert.ok(expanded.has("physics"));

// 하위 주제가 있는 줄만 자기 항목을 한 줄로 접는다.
assert.equal(foldsOwnRows(tops[0]), true);
assert.equal(foldsOwnRows(n("art")), false);
assert.equal(ownKey("vinc/design"), "own:vinc/design");

console.log("lnb-tree-check: ok");
