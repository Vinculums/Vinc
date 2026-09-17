/** 팩 조각내기 자체 검사 — 브라우저 없이 돈다.
 *
 *     node --experimental-strip-types app/dev/packchunks-check.ts
 *
 *  `packchunks.ts` 가 import 0 인 것이 성립 조건이다. */
import assert from "node:assert/strict";
import { chunkPack } from "../src/packchunks.ts";

const node = (i: number) => ({ id: `concept:n${i}`, type: "concept", title: "가".repeat(40) });
const edge = (i: number) => ({ from: `concept:n${i}`, to: `concept:n${i + 1}`, type: "relates_to" });
const doc = (i: number, len = 50) => ({ doc_id: `d${i}`, title: `d${i}`, text: "x".repeat(len) });
const pack = {
  vinc_pack: 1, manifest: { counts: { concepts: 30 } },
  fragment: { nodes: Array.from({ length: 30 }, (_, i) => node(i)),
              edges: Array.from({ length: 29 }, (_, i) => edge(i)) },
  documents: [doc(0), doc(1, 5000), doc(2)],
};

const LIMIT = 1000;
const { chunks, tooBig } = chunkPack(pack, LIMIT);

// 순서: 노드 조각이 전부 끝난 뒤에 엣지, 그 뒤에 문서.
const order = chunks.map((c) => c.stage);
assert.deepEqual(order, [...order].sort((a, b) =>
  ["nodes", "edges", "documents"].indexOf(a) - ["nodes", "edges", "documents"].indexOf(b)));
assert.ok(order.filter((s) => s === "nodes").length > 1, "노드가 여러 조각으로 나뉘어야 한다");

// 빠지는 것도 겹치는 것도 없다(혼자 상한을 넘는 문서 하나만 빠진다).
const all = (stage: string, key: "nodes" | "edges" | "documents") => chunks
  .filter((c) => c.stage === stage)
  .flatMap((c: any) => key === "documents" ? c.pack.documents : c.pack.fragment[key]);
assert.deepEqual(all("nodes", "nodes"), pack.fragment.nodes);
assert.deepEqual(all("edges", "edges"), pack.fragment.edges);
assert.deepEqual(all("documents", "documents").map((d: any) => d.doc_id), ["d0", "d2"]);
assert.equal(tooBig, 1);

// 조각마다 그대로 보낼 수 있는 팩이다: 판 번호가 있고, 다른 단계의 항목은 비어 있다.
for (const c of chunks as any[]) {
  assert.equal(c.pack.vinc_pack, 1);
  assert.equal(c.count, c.stage === "documents" ? c.pack.documents.length
                        : c.pack.fragment[c.stage].length);
  if (c.stage !== "nodes") assert.equal(c.pack.fragment.nodes.length, 0);
  if (c.stage !== "edges") assert.equal(c.pack.fragment.edges.length, 0);
  // 항목 몫이 상한 안이다(매니페스트·감싸는 키는 상한 밖의 여유로 받는다).
  const items = c.stage === "documents" ? c.pack.documents : c.pack.fragment[c.stage];
  assert.ok(Buffer.byteLength(JSON.stringify(items)) <= LIMIT + 2);
}

// 빈 팩은 조각이 없다.
assert.deepEqual(chunkPack({ vinc_pack: 1 }).chunks, []);

console.log(`ok · ${chunks.length} chunks, ${tooBig} too big`);
