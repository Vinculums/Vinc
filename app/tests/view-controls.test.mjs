import test from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const result = await build({ entryPoints: [fileURLToPath(new URL("../src/view-controls.ts", import.meta.url))],
  bundle: true, platform: "node", format: "esm", write: false });
const { changedViewPrefs, changedRendererPrefs, canUpdateViewStyle, viewControlRows, viewPreferenceWork } =
  await import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString("base64")}`);
const prefs = (overrides = {}) => ({ view: "3d", layout: "hub", when: 1, timeBasis: "recorded",
  edgeOff: [], bundleOff: [], nodeScale: .7, labels: "hover", ...overrides });
const available = { map: true, bundle: false };

test("repeated and reordered legend filters do not trigger work", () => {
  const p = prefs({ edgeOff: ["directed", "loose"], bundleOff: ["ABOUT", "RELATED_TO"] });
  assert.deepEqual(changedViewPrefs(p, { edgeOff: ["loose", "directed"] }), []);
  assert.deepEqual(changedViewPrefs(p, { bundleOff: ["RELATED_TO", "ABOUT"] }), []);
  assert.deepEqual(changedViewPrefs(prefs(), { edgeOff: [], bundleOff: [] }), []);
  assert.deepEqual(changedViewPrefs(p, { edgeOff: ["directed"] }), ["edgeOff"]);
});

test("time visibility followed by style changes remains a live update in both maps", () => {
  for (const view of ["2d", "3d"]) {
    const previous = prefs({ view });
    const next = { ...previous, when: .5, timeBasis: "content", nodeScale: .9 };
    assert.deepEqual(changedRendererPrefs(previous, next), ["nodeScale"]);
    assert.equal(canUpdateViewStyle(changedRendererPrefs(previous, next)), true);
    assert.equal(viewPreferenceWork(next, ["nodeScale"], available).mode, "style");
    assert.equal(viewPreferenceWork(next, ["when"], available).mode, "time");
    assert.equal(viewPreferenceWork(next, ["timeBasis"], available).mode, "time");
  }
});

test("structural renderer changes still require a remount", () => {
  const previous = prefs();
  for (const change of [{ edges: "always" }, { edgeOff: ["directed"] },
    { unlinked: "hide" }, { layout: "temporalRoots" }, { cone: .9 }]) {
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
  const centre = viewPreferenceWork(prefs({ layout: "hub" }), ["layout"], available);
  assert.equal(centre.serverLayout, true);
  assert.equal(centre.keepCamera, false);
});

test("Timeline and Weave keep separate filter semantics and update routes", () => {
  const timeline = prefs({ view: "timeline" });
  const weave = prefs({ view: "bundle" });
  for (const key of ["period", "timelineDocs", "timelineLineage", "timeBasis"]) {
    const work = viewPreferenceWork(timeline, [key], { map: false, bundle: false });
    assert.equal(work.mode, "remount");
    assert.equal(work.reuseGraph, true);
  }
  for (const key of ["bundleDepth", "bundleOff", "labels"]) {
    assert.equal(viewPreferenceWork(weave, [key], { map: false, bundle: true }).mode, "bundle");
  }
  assert.equal(viewPreferenceWork(weave, ["when"], { map: false, bundle: true }).mode, "time");
});

test("shared panel inventory preserves viewpoint-specific controls", () => {
  assert.deepEqual(viewControlRows(prefs({ view: "timeline" })), ["period", "basis", "docs", "lineage"]);
  assert.deepEqual(viewControlRows(prefs({ view: "bundle" })), ["bundleDepth", "bundleRels", "labels", "basis", "when", "replay"]);
  assert.equal(viewControlRows(prefs()).includes("cone"), true);
  assert.equal(viewControlRows(prefs({ layout: "temporalRoots" })).includes("cone"), false);
  assert.equal(viewControlRows(prefs({ view: "2d" })).includes("cone"), false);
  assert.equal(viewControlRows(prefs({ view: "2d" })).includes("replay"), false);
});

test("slider work retains the panel DOM while label chips refresh it", () => {
  for (const key of ["nodeScale", "edgeScale", "spread"]) {
    assert.equal(viewPreferenceWork(prefs(), [key], available).refreshPanel, false);
  }
  assert.equal(viewPreferenceWork(prefs(), ["labels"], available).refreshPanel, true);
  assert.equal(viewPreferenceWork(prefs(), [], available).mode, "noop");
});

test("an option changed during a pending show restarts its captured snapshot", () => {
  for (const view of ["2d", "3d", "timeline", "bundle"]) {
    const p = prefs({ view, layout: "temporalRoots", timeBasis: "content" });
    for (const key of ["labels", "nodeScale", "when"]) {
      const work = viewPreferenceWork(p, [key], { map: true, bundle: true, current: false });
      assert.equal(work.mode, "remount");
      assert.equal(work.reuseGraph, true);
      assert.equal(work.keepCamera, true);
    }
  }
});
