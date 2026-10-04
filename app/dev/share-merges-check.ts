import assert from "node:assert/strict";
import { shareMerges } from "../src/share-merges.ts";

const nodes = [{ id: "concept:a", title: "Incoming" }, { id: "record:b", title: "Record" }];
let reads = 0;
assert.deepEqual(await shareMerges([...nodes, nodes[0]!], async (id) => {
  reads++;
  return id === "concept:a" ? { executed: true, title: "Existing" }
    : { executed: false, note: `unknown node: ${id}` };
}), [{ id: "concept:a", title: "Existing" }]);
assert.equal(reads, 2);
assert.deepEqual(await shareMerges([], async () => { throw new Error("not called"); }), []);
await assert.rejects(shareMerges(nodes, async () => { throw new Error("503"); }));
await assert.rejects(shareMerges(nodes, async () => ({ executed: false, note: "store unavailable" })));
await assert.rejects(shareMerges(nodes, async () => ({ executed: false })));
assert.deepEqual(await shareMerges(nodes.slice(1), async () => ({ executed: true })),
  [{ id: "record:b", title: "Record" }]);
let active = 0, peak = 0;
const many = Array.from({ length: 9 }, (_, i) => ({ id: `concept:${i}` }));
const matches = await shareMerges(many, async () => {
  active++;
  peak = Math.max(peak, active);
  await new Promise((resolve) => setTimeout(resolve, 1));
  active--;
  return { executed: true };
});
assert.equal(peak, 4);
assert.deepEqual(matches.map((n) => n.id), many.map((n) => n.id));
console.log("share merge inspection: PASS");
