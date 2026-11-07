import test from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const result = await build({ entryPoints: [fileURLToPath(new URL("../src/view-controls.ts", import.meta.url))],
  bundle: true, platform: "node", format: "esm", write: false });
const { changedViewPrefs, changedRendererPrefs, canUpdateViewStyle, viewControlRows, viewPreferenceWork } =
  await import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString("base64")}`);
const prefs = (overrides = {}) => ({ view: "3d", layout: "temporalRoots", when: 1, timeBasis: "recorded",
  edgeOff: [], nodeScale: .7, labels: "hover", ...overrides });
const available = { map: true };

test("repeated and reordered legend filters do not trigger work", () => {
  const p = prefs({ edgeOff: ["directed", "loose"] });
  assert.deepEqual(changedViewPrefs(p, { edgeOff: ["loose", "directed"] }), []);
  assert.deepEqual(changedViewPrefs(prefs(), { edgeOff: [] }), []);
  assert.deepEqual(changedViewPrefs(p, { edgeOff: ["directed"] }), ["edgeOff"]);
});

test("time visibility followed by style changes remains a live update", () => {
  const previous = prefs();
  const next = { ...previous, when: .5, timeBasis: "content", nodeScale: .9 };
  assert.deepEqual(changedRendererPrefs(previous, next), ["nodeScale"]);
  assert.equal(canUpdateViewStyle(changedRendererPrefs(previous, next)), true);
  assert.equal(viewPreferenceWork(next, ["nodeScale"], available).mode, "style");
  assert.equal(viewPreferenceWork(next, ["edges"], available).mode, "style");
  assert.equal(viewPreferenceWork(next, ["when"], available).mode, "time");
  // 시간 기준은 가지 배치를 다시 짓는다(아래 시험).
  assert.equal(viewPreferenceWork(next, ["timeBasis"], available).mode, "remount");
});

test("structural renderer changes still require a remount", () => {
  const previous = prefs();
  for (const change of [{ edges: "always" }, { edgeOff: ["directed"] },
    { unlinked: "hide" }]) {
    const next = { ...previous, ...change };
    assert.equal(canUpdateViewStyle(changedRendererPrefs(previous, next)), false);
  }
  assert.equal(canUpdateViewStyle(changedRendererPrefs(previous, { ...previous, edgeOff: [] })), true);
});

test("time branches recompute for their time basis and preserve the current camera", () => {
  const work = viewPreferenceWork(prefs({ layout: "temporalRoots" }), ["timeBasis"], available);
  assert.equal(work.mode, "remount");
  assert.equal(work.relayout, true);
  assert.equal(work.serverLayout, false);
  assert.equal(work.reuseGraph, true);
  assert.equal(work.keepCamera, true);
});

test("the one view has no layout choice, ends with replay and keeps no retired rows", () => {
  assert.equal(viewControlRows(prefs()).at(-1), "replay");
  for (const gone of ["layout", "cone", "period", "docs", "lineage", "bundleDepth", "bundleRels"])
    assert.equal(viewControlRows(prefs()).includes(gone), false);
});

test("slider work retains the panel DOM while label chips refresh it", () => {
  for (const key of ["nodeScale", "edgeScale", "spread"]) {
    assert.equal(viewPreferenceWork(prefs(), [key], available).refreshPanel, false);
  }
  assert.equal(viewPreferenceWork(prefs(), ["labels"], available).refreshPanel, true);
  assert.equal(viewPreferenceWork(prefs(), [], available).mode, "noop");
});

test("an option changed during a pending show restarts its captured snapshot", () => {
  const p = prefs({ layout: "temporalRoots", timeBasis: "content" });
  for (const key of ["labels", "nodeScale", "when"]) {
    const work = viewPreferenceWork(p, [key], { map: true, current: false });
    assert.equal(work.mode, "remount");
    assert.equal(work.reuseGraph, true);
    assert.equal(work.keepCamera, true);
  }
});
