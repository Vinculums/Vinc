/** 공유 시트의 원문 선택 자체 검사 — 브라우저 없이 돈다.
 *
 *     node --experimental-strip-types app/dev/share-docs-check.ts
 *
 *  `share-docs.ts` 가 import 0 인 것이 성립 조건이다. */
import assert from "node:assert/strict";
import { keptDocs } from "../src/share-docs.ts";

const doc = (id: string) => ({ doc_id: id, title: id, text: "x" });
const docs = [doc("d1"), doc("d2"), doc("d3")];
// d1 은 a·b 가 언급, d2 는 b 만, d3 는 미러 행 하나로만 실렸다.
const refs = { d1: ["concept:a", "concept:b"], d2: ["concept:b"], d3: ["concept:doc.d3"] };
const ids = ["concept:a", "concept:b", "concept:doc.d3"];
const pick = (off: string[], r: Record<string, string[]> | null = refs) => {
  const excluded = new Set(off);
  const kept = new Set(ids.filter((i) => !excluded.has(i)));
  return keptDocs(docs, excluded, r ?? undefined, kept).map((d) => d.doc_id);
};

// 아무것도 안 끄면 전부 나간다.
assert.deepEqual(pick([]), ["d1", "d2", "d3"]);
// 행을 전부 끄면 원문도 0 이다 — 미러 행이 없는 문서(d1·d2)도 마찬가지다.
assert.deepEqual(pick(ids), []);
// 개념 하나를 꺼도 다른 남은 노드가 언급하는 원문은 남는다(d1 은 a 가 붙잡는다).
assert.deepEqual(pick(["concept:b"]), ["d1", "d3"]);
assert.deepEqual(pick(["concept:a"]), ["d1", "d2", "d3"]);
// 미러 행을 끄면 그 문서가 빠진다. 다른 노드가 언급해도 미러를 끈 것이 이긴다.
assert.deepEqual(pick(["concept:doc.d3"]), ["d1", "d2"]);
assert.deepEqual(
  keptDocs(docs, new Set(["concept:doc.d1"]), refs, new Set(["concept:a", "concept:b"]))
    .map((d) => d.doc_id), ["d2"]);
// 호스티드의 물리 키(owner + U+001F + id)도 미러로 읽는다.
assert.deepEqual(
  keptDocs(docs, new Set(["user:x\u001fconcept:doc.d2"]), undefined, undefined)
    .map((d) => d.doc_id), ["d1", "d3"]);
// 대응표가 없는 응답(구 허브)은 미러 규칙만 쓴다: 전부 꺼도 미러 없는 원문은 남는다.
assert.deepEqual(pick(ids, null), ["d1", "d2"]);
// 대응표에 없는 원문은 붙잡는 노드가 없는 것이다.
assert.deepEqual(keptDocs([doc("d9")], new Set(), refs, new Set(ids)), []);

console.log("share-docs-check: ok");
