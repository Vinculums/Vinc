import test from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const source = async (entry) => {
  const result = await build({ entryPoints: [fileURLToPath(new URL(entry, import.meta.url))],
    bundle: true, platform: "node", format: "esm", write: false, loader: { ".json": "json" } });
  return import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString("base64")}`);
};
const { tintPlan } = await source("../src/view-tint.ts");
const { isHistorical } = await source("../src/node-validity.ts");
const { topicJewels } = await source("../src/theme.ts");
const hex = (n) => `#${n.toString(16).padStart(6, "0")}`;

test("each area wears one jewel and its topics wear small turns of it", () => {
  const domains = ["vinc/design/a", "vinc/design/b", "vinc/design/c", "vinc/ai/x", "vinc/ai/y"];
  const plan = tintPlan(domains, "dark");
  assert.deepEqual([...plan.areas.keys()], ["ai", "design"]);
  assert.equal(plan.areas.get("ai"), hex(topicJewels.dark[0]));
  assert.equal(plan.areas.get("design"), hex(topicJewels.dark[1]));
  // The first topic of an area is the area jewel itself; the next ones differ but stay close.
  assert.equal(plan.topics.get("vinc/design/a"), plan.areas.get("design"));
  assert.notEqual(plan.topics.get("vinc/design/b"), plan.topics.get("vinc/design/a"));
  assert.equal(plan.areaOf.get("vinc/ai/y"), "ai");
  const rgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const far = (a, b) => Math.max(...rgb(a).map((v, i) => Math.abs(v - rgb(b)[i])));
  assert.ok(far(plan.topics.get("vinc/design/c"), plan.areas.get("design")) < 40, "a topic left its area's family");
});

test("one area on screen gives its topics whole jewels", () => {
  const plan = tintPlan(["vinc/design/a", "vinc/design/b"], "light");
  assert.equal(plan.topics.get("vinc/design/a"), hex(topicJewels.light[0]));
  assert.equal(plan.topics.get("vinc/design/b"), hex(topicJewels.light[1]));
});

test("a claim is historical once its interval closes or it has a successor", () => {
  const today = "2026-10-28";
  assert.equal(isHistorical({}, today), false);
  assert.equal(isHistorical({ valid_until: "2026-11-01" }, today), false);
  assert.equal(isHistorical({ valid_until: "2026-10-28" }, today), true, "half-open: the end day is already past");
  assert.equal(isHistorical({ props: { valid_until: "2026-10" } }, today), true);
  assert.equal(isHistorical({ props: { superseded_by: "decision:next" } }, today), true);
  assert.equal(isHistorical({ props: { superseded_by: "" } }, today), false);
});
