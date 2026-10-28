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

const { sayings, statedStart, decisionSteps } = await source("../src/item-page.ts");

test("the item page quotes stored sentences only, ruling first, and skips run-id evidence", () => {
  const props = { why: "Because the owner said so on the day.", ruling: "Use the infra layout as it comes down.",
    code: "vinc:app/src/view3d.ts, vinc:app/src/x.ts", status: "adopted", date: "2026-10-28", tag: "x" };
  const links = [
    { id: "decision:a", title: "A", type: "SUPERSEDES", dir: "out", evidence_text: "The owner replaced A with this on 28 Oct." },
    { id: "record:b", title: "B", type: "ABOUT", dir: "in", evidence_text: "run:7f3a" },
  ];
  const got = sayings(props, links);
  assert.deepEqual(got.map((s) => s.from === "prop" ? s.key : s.id), ["ruling", "why", "decision:a"]);
});

test("a missing start date is filled only from a date the item itself states", () => {
  assert.equal(statedStart("", { date: "2026-10-11" }), "2026-10-11");
  assert.equal(statedStart("2026-10-01", { date: "2026-10-11" }), "", "already set");
  assert.equal(statedStart("", { decided_at: "2026-10" }), "2026-10");
  assert.equal(statedStart("", { date: "last week" }), "", "not a date");
  assert.equal(statedStart("", {}), "");
});

test("how it was decided lists the item, what it replaced and what replaced it, by date", () => {
  const steps = decisionSteps({ id: "decision:now", title: "Now", validFrom: "2026-10-11" }, [
    { id: "decision:old", title: "Old", type: "SUPERSEDES", dir: "out", valid_from: "2026-08-01", valid_until: "2026-10-11" },
    { id: "decision:next", title: "Next", type: "SUPERSEDES", dir: "in", temporal: { valid_from: "2026-10-22" } },
    { id: "concept:c", title: "C", type: "ABOUT", dir: "out" },
    { id: "decision:side", title: "Side", type: "RELATED_TO", dir: "out" },
  ]);
  assert.deepEqual(steps.map((s) => [s.id, s.rel]), [["decision:old", "replaces"], ["decision:now", "self"],
    ["decision:next", "replaced_by"], ["decision:side", "related"]]);
  assert.equal(steps[0].ended, "2026-10-11");
});

const { namedDecisions, docMirrorId } = await source("../src/doc-graph.ts");

test("a document names decisions by id, once each, with the line it stands on", () => {
  const text = "# Audit\nSee [[decision:view-offers-two-layouts-2026-10-11]] and decision:x-1.\n"
    + "Again decision:x-1 here.\nNot decision: a label.";
  assert.deepEqual(namedDecisions(text), [
    { id: "decision:view-offers-two-layouts-2026-10-11", line: "See [[decision:view-offers-two-layouts-2026-10-11]] and decision:x-1." },
    { id: "decision:x-1", line: "See [[decision:view-offers-two-layouts-2026-10-11]] and decision:x-1." },
  ]);
  assert.equal(docMirrorId("vinc.audit.x"), "concept:doc.vinc.audit.x");
});
