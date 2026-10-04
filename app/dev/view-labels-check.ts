/** VIN-337: degree/id-prioritised screen-space overlap regression.
 * node --experimental-strip-types app/dev/view-labels-check.ts
 * Actual renderer projection is also checked in Chromium acceptance fixtures.
 */
import assert from "node:assert/strict";
import { nonOverlappingLabels, type LabelBox } from "../src/view-labels.ts";
const box = (id: string, x: number, y: number, w = 120, h = 18): LabelBox =>
  ({ id, left: x, right: x + w, top: y, bottom: y + h });
const cluster = [box("parent", 100, 100), box("reviewer", 110, 100),
  box("researcher", 105, 108), box("curator", 120, 101), box("steward", 110, 105),
  box("engineering", 400, 100), box("demo", 400, 160)];
assert.deepEqual([...nonOverlappingLabels(cluster)], ["parent", "engineering", "demo"]);
assert.deepEqual([...nonOverlappingLabels([box("a", 0, 0), box("b", 121, 0), box("c", 123, 0)])], ["a", "c"]);
assert.deepEqual([...nonOverlappingLabels([box("a", 0, 0), box("b", 0, 20)])], ["a", "b"]);
assert.equal(nonOverlappingLabels([box("invalid", NaN, 0), box("zero", 0, 0, 0)]).size, 0);
assert.deepEqual([...nonOverlappingLabels(cluster)], [...nonOverlappingLabels(cluster)]);
console.log("PASS View label overlap: clustered siblings, priority, margin, separated labels, invalid projections and deterministic output.");
