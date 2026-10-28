import test from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import ELK from "elkjs/lib/elk.bundled.js";
import { AvoidLib } from "libavoid-js";

const source = async (entry) => {
  const result = await build({ entryPoints: [fileURLToPath(new URL(entry, import.meta.url))],
    bundle: true, platform: "node", format: "esm", write: false });
  return import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString("base64")}`);
};
const { temporalLayout: spatialTemporalLayout } = await source("../src/view-layout.ts");
// Retain the full planar regression suite, and validate the independently
// composed spatial result before projecting its saved 2D contract for it.
const temporalLayout = async (...args) => {
  const graph = await spatialTemporalLayout(...args);
  check(graph, args[1]);
  const zs = new Map();
  for (const e of [...graph.edges, ...graph.topicEdges]) if (e.route?.points2d) {
    zs.set(e.source, e.route.points2d[0].z); zs.set(e.target, e.route.points2d.at(-1).z);
  }
  return { nodes: graph.nodes.map(n => ({ ...n, x: n.x2, y: -n.y2, z: zs.get(n.id) ?? 0 })),
    edges: graph.edges.map(e => ({ ...e, route: e.route && { ...e.route, points: e.route.points2d, engine: e.route.engine2d } })),
    topicEdges: graph.topicEdges.map(e => ({ ...e, route: { ...e.route, points: e.route.points2d, engine: e.route.engine2d } })) };
};
const { routeAcrossTopics } = await source("../src/view-layout-avoid.ts");
const { layoutInWorker } = await source("../src/view-layout-client.ts");
const { LAYOUTS, normalizeLayout, serverLayoutFor, isRootsLayout, loadPrefs, savePrefs } = await source("../src/view-prefs.ts");

test("one layout remains and every saved layout folds into time branches", () => {
  const saved = new Map();
  globalThis.localStorage = { getItem: k => saved.get(k) ?? null, setItem: (k, v) => saved.set(k, v) };
  try {
    // 첫 그림(판 4): 3D · 시간순 가지 · 선 상시 · 범례는 관계 선만.
    assert.deepEqual([loadPrefs().view, loadPrefs().layout, loadPrefs().edges, loadPrefs().edgeOff],
      ["3d", "temporalRoots", "always", ["directed", "cross", "bridge"]]);
    assert.deepEqual(LAYOUTS, ["temporalRoots"]);
    assert.equal(serverLayoutFor("temporalRoots"), "hub");
    assert.equal(isRootsLayout(loadPrefs().layout), true);
    // 보기는 3D 하나다 — 저장된 옛 관점은 3D 로 접힌다. 2D 전용이던 선 `focus` 는 `none` 이다.
    for (const view of ["2d", "timeline", "bundle"]) {
      saved.set("vinc.view.prefs", JSON.stringify({ view, edges: "focus", pv: 4 }));
      assert.deepEqual([loadPrefs().view, loadPrefs().edges], ["3d", "none"]);
    }
    // 저장된 배치는 무엇이든(걷은 `hub`, 0.9.7 의 `rank`·`rankedRoots`, 그 전 이름, 모르는 값) 시간순 가지다.
    for (const retired of ["hub", "rank", "rankedRoots", "radial", "dendro", "toString", "unknown"]) {
      assert.equal(normalizeLayout(retired), "temporalRoots");
      for (const pv of [undefined, 2, 4]) {
        saved.set("vinc.view.prefs", JSON.stringify({ layout: retired, pv }));
        assert.equal(loadPrefs().layout, "temporalRoots");
      }
    }
    // 판을 올려도 판 2 사용자가 고른 크기 1 은 기본값으로 되돌아가지 않는다.
    saved.set("vinc.view.prefs", JSON.stringify({ nodeScale: 1, cone: 1, pv: 2 }));
    assert.equal(loadPrefs().nodeScale, 1); assert.equal(loadPrefs().cone, 1);
    saved.set("vinc.view.prefs", JSON.stringify({ nodeScale: 1, cone: 1 }));
    assert.equal(loadPrefs().nodeScale, 0.7); assert.equal(loadPrefs().cone, 0.7);
    // 판 4 이전 저장값은 첫 그림 넷만 한 번 새 기본으로 옮기고, 나머지 고른 값은 둔다.
    saved.set("vinc.view.prefs", JSON.stringify({ view: "2d", layout: "hub", edges: "none", edgeOff: [],
      nodeScale: 1.4, pv: 3 }));
    assert.deepEqual([loadPrefs().view, loadPrefs().layout, loadPrefs().edges, loadPrefs().edgeOff, loadPrefs().nodeScale],
      ["3d", "temporalRoots", "always", ["directed", "cross", "bridge"], 1.4]);
    savePrefs({ ...loadPrefs(), edges: "particles", edgeOff: [] });
    assert.equal(JSON.parse(saved.get("vinc.view.prefs")).pv, 4);
    assert.deepEqual([loadPrefs().edges, loadPrefs().edgeOff], ["particles", []], "a choice made after the move stays");
  } finally { delete globalThis.localStorage; }
});

const { movedRoute, routeLength, pointOnRoute, simplifyRoute } = await source("../src/view-route.ts");
const { RoutedLinkObject } = await source("../src/surfaces/view-routes3d.ts");
const { shortenPlanarRoutes } = await source("../src/view-route-shortcuts.ts");
await AvoidLib.load(fileURLToPath(new URL("../node_modules/libavoid-js/dist/libavoid.wasm", import.meta.url)));
const elk = new ELK();
const route = (ns, es, batching) => Promise.resolve(routeAcrossTopics(AvoidLib.getInstance(), ns, es, batching));
const n = (id, date, domain = "topic", recorded = date) => ({ id, label: id, kind: "concept", domain,
  degree: 2, content_ts: date, ts: recorded });
const edge = (source, target, type = "PREREQUISITE_OF", id = `${source}-${target}-${type}`) =>
  ({ id, source, target, type, cross_domain: false, confidence: .8, evidence: "Authored evidence" });
const axisAligned = (points) => {
  assert.ok(points.length >= 2);
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1], b = points[i];
    assert.ok([a.x - b.x, a.y - b.y, a.z - b.z].filter(v => Math.abs(v) > 1e-8).length <= 1,
      `diagonal section ${JSON.stringify([a, b])}`);
  }
};
const check = (graph, edges) => {
  const map = new Map(graph.nodes.map(n => [n.id, n]));
  assert.equal(graph.edges.length, edges.length);
  graph.edges.forEach((e, i) => {
    const { route, ...metadata } = e;
    assert.deepEqual(metadata, edges[i]);
    axisAligned(route.points);
    for (const [at, id] of [[route.points[0], e.source], [route.points.at(-1), e.target]]) {
      const node = map.get(id);
      assert.deepEqual(at, { x: node.x, y: node.y, z: node.z });
    }
  });
  graph.topicEdges.forEach(e => { assert.equal(e.presentation, true); assert.ok(["IN_DOMAIN", "PARENT_OF"].includes(e.type)); axisAligned(e.route.points); });
};

test("temporal partitions preserve backwards edges, cycles, parallels, self loops, missing dates and metadata", async () => {
  const nodes = [n("a", "2020"), n("b", "2021"), n("c", "2022"), n("unknown", "")];
  const edges = [edge("a", "b"), edge("b", "c"), edge("c", "a"), edge("b", "a", "DERIVES_FROM"),
    edge("a", "b", "RELATED_TO"), edge("c", "c"), edge("unknown", "a", "CONNECTS")];
  const snapshot = structuredClone({ nodes, edges });
  const result = await temporalLayout(nodes, edges, "content", elk, route);
  check(result, edges);
  assert.deepEqual({ nodes, edges }, snapshot);
  const byId = new Map(result.nodes.map(n => [n.id, n]));
  assert.ok(byId.get("a").x < byId.get("b").x && byId.get("b").x < byId.get("c").x);
  assert.equal(byId.get("unknown").content_ts, "");
  assert.equal(result.nodes.filter(n => n.kind === "domain").length, 1);
});

test("undated items stand in their own column left of the first date", async () => {
  const nodes = [n("early", "2020"), n("late", "2021"), n("nodate", "")];
  const edges = [edge("early", "late"), edge("nodate", "late", "RELATED_TO")];
  const result = await temporalLayout(nodes, edges, "content", elk, route);
  check(result, edges);
  const at = id => result.nodes.find(r => r.id === id).x;
  assert.ok(at("nodate") < at("early"), "an undated item shared the first date's column");
  assert.ok(at("early") < at("late"));
});

test("chronological backwards relations use facing ports without a gratuitous loop", async () => {
  for (const type of ["DERIVES_FROM", "INHERITS_FROM", "PREREQUISITE_OF", "RELATED_TO"]) {
    const nodes = [n("a", "2020"), n("b", "2021")];
    const edges = [edge(type === "DERIVES_FROM" || type === "INHERITS_FROM" ? "a" : "b",
      type === "DERIVES_FROM" || type === "INHERITS_FROM" ? "b" : "a", type)];
    const result = await temporalLayout(nodes, edges, "content", elk, route);
    check(result, edges);
    assert.equal(result.edges[0].route.points.length, 2, type);
  }
});

test("recorded and content bases change ordering without inventing dates", async () => {
  const nodes = [n("a", "2020", "topic", "2022"), n("b", "2022", "topic", "2020")], edges = [edge("a", "b")];
  const content = await temporalLayout(nodes, edges, "content", elk, route);
  const recorded = await temporalLayout(nodes, edges, "recorded", elk, route);
  assert.ok(content.nodes[0].x < content.nodes[1].x);
  assert.ok(recorded.nodes[0].x > recorded.nodes[1].x);
  check(recorded, edges);
});

test("disconnected groups stack as separate branches to the right of their topic", async () => {
  const result = await temporalLayout([n("a", "2020"), n("b", "2021"), n("c", "2020"), n("d", "2021")],
    [edge("a", "b"), edge("c", "d")], "content", elk, route);
  const map = new Map(result.nodes.map(n => [n.id, n]));
  const topic = map.get("domain:topic");
  for (const id of ["a", "b", "c", "d"]) assert.ok(map.get(id).x > topic.x, `${id} sits left of its topic`);
  assert.ok(map.get("a").x < map.get("b").x && map.get("c").x < map.get("d").x, "dates run left to right");
  assert.notEqual(map.get("a").y, map.get("c").y, "the two branches share a lane");
});

test("a small Topic with one connected chain starts at its Topic and grows right", async () => {
  const nodes = Array.from({ length: 6 }, (_, i) => n(`c${i}`, `202${i}`));
  const edges = nodes.slice(1).map((x, i) => edge(nodes[i].id, x.id));
  const result = await temporalLayout(nodes, edges, "content", elk, route);
  check(result, edges);
  const topic = result.nodes.find(n => n.kind === "domain");
  const xs = nodes.map(m => result.nodes.find(r => r.id === m.id).x);
  assert.ok(xs.every(x => x > topic.x), "a branch sat left of its Topic");
  assert.deepEqual(xs, xs.toSorted((a, b) => a - b), "the chain does not read left to right");
});

test("time branches follow dates when relations disagree, retaining cycles and original direction", async () => {
  const nodes = [n("root", "2022"), n("middle", "2021"), n("leaf", "2020")];
  const edges = [edge("root", "middle"), edge("leaf", "middle", "DERIVES_FROM")];
  const temporal = await temporalLayout(nodes, edges, "content", elk, route);
  check(temporal, edges);
  assert.ok(temporal.nodes[0].x > temporal.nodes[1].x && temporal.nodes[1].x > temporal.nodes[2].x);
  const cycles = [...edges, edge("leaf", "root"), edge("middle", "middle"), edge("root", "leaf", "RELATED_TO")];
  check(await temporalLayout([...nodes, n("unknown", "")], cycles, "content", elk, route), cycles);
});

test("explicit timestamp offsets compare as instants while invalid dates stay unknown", async () => {
  const nodes = [n("a", "2020-01-01T23:00:00+09:00"), n("b", "2020-01-01T20:00:00Z"), n("invalid", "2020-02-31")];
  const edges = [edge("a", "b"), edge("invalid", "a", "RELATED_TO")];
  const result = await temporalLayout(nodes, edges, "content", elk, route);
  check(result, edges);
  assert.ok(result.nodes[0].x < result.nodes[1].x);
  assert.equal(result.nodes[2].content_ts, "2020-02-31", "source metadata must not be rewritten");
});

test("cross topic routing preserves source direction and moves depth orthogonally", async () => {
  const nodes = [n("a", "2020", "topic/a"), n("b", "2021", "topic/b"),
    { id: "domain:topic", label: "topic", kind: "domain", domain: "", degree: 2 }];
  const edges = [edge("b", "a", "DERIVES_FROM")];
  const result = await temporalLayout(nodes, edges, "content", elk, route);
  check(result, edges);
  assert.equal(result.edges[0].route.engine, "libavoid");
  assert.ok(result.topicEdges.some(e => e.type === "PARENT_OF"));
  assert.ok(new Set(result.edges[0].route.points.map(p => p.z)).size > 1);
});

test("vertically aligned topics use facing vertical ports instead of a side detour", () => {
  const nodes = [{ ...n("a", "2020"), x: 0, y: 0, z: 0 }, { ...n("b", "2021"), x: 0, y: 1, z: .26 }];
  const e = edge("a", "b", "RELATED_TO");
  const [result] = routeAcrossTopics(AvoidLib.getInstance(), nodes, [e]);
  axisAligned(result.route.points);
  assert.equal(simplifyRoute(result.route.points.map(p => ({ ...p, z: 0 }))).length, 2);
  const { route: ignored, ...metadata } = result;
  assert.deepEqual(metadata, e);
});

test("centre pin routes avoid intervening node and label rectangles and preserve self loops", () => {
  const nodes = [{ ...n("a", "2020"), x: 0, y: 0, z: 0 },
    { ...n("b", "2021"), x: 0, y: 1, z: 0 }, { ...n("obstacle", ""), x: 0, y: .5, z: 0 }];
  const edges = [edge("a", "b", "RELATED_TO"), edge("a", "a", "RELATED_TO")];
  const result = routeAcrossTopics(AvoidLib.getInstance(), nodes, edges);
  result.forEach(e => axisAligned(e.route.points));
  assert.ok(routeLength(result[1].route.points) > .2, "self relation collapsed at the centre pin");
  for (let i = 1; i < result[0].route.points.length; i++) {
    const a = result[0].route.points[i - 1], b = result[0].route.points[i];
    const horizontal = Math.abs(a.y - b.y) < 1e-8;
    const crosses = horizontal
      ? a.y > .476 && a.y < .566 && Math.max(a.x, b.x) > -.11 && Math.min(a.x, b.x) < .11
      : a.x > -.11 && a.x < .11 && Math.max(a.y, b.y) > .476 && Math.min(a.y, b.y) < .566;
    assert.ok(!crosses, "route cuts through the intervening label rectangle");
  }
});

test("topic-pair routing adds distant obstacles reached by detours and restores input edge order", () => {
  const positioned = (id, x, y, domain) => ({ ...n(id, "2020", domain), x, y, z: 0 });
  const nodes = [positioned("a", 0, 0, "a"), positioned("b", 0, 1, "b"),
    ...[-.55, -.33, -.11, .11, .33, .55].map((x, i) => positioned(`wall${i}`, x, .5, "wall")),
    positioned("c", 3, 0, "c"), positioned("d", 3, 1, "d")];
  const edges = [edge("a", "b", "RELATED_TO"), edge("c", "d"), edge("b", "a", "DERIVES_FROM")];
  const routed = routeAcrossTopics(AvoidLib.getInstance(), nodes, edges);
  routed.forEach((e, i) => {
    const { route, ...metadata } = e; assert.deepEqual(metadata, edges[i]); axisAligned(route.points);
    for (const obstacle of nodes.filter(n => n.id.startsWith("wall"))) {
      for (let j = 1; j < route.points.length; j++) {
        const a = route.points[j - 1], b = route.points[j];
        assert.ok(!(Math.max(a.x, b.x) > obstacle.x - .114 + 1e-8 && Math.min(a.x, b.x) < obstacle.x + .114 - 1e-8 &&
          Math.max(a.y, b.y) > obstacle.y - .028 + 1e-8 && Math.min(a.y, b.y) < obstacle.y + .070 - 1e-8),
          `detour crossed ${obstacle.id}`);
      }
    }
  });
});

test("hundreds of repeated access legs route once without losing direction or evidence", () => {
  const nodes = [{ ...n("a", "2020"), x: 0, y: 0, z: 0 },
    { ...n("b", "2021"), x: 0, y: 1, z: .26 }, { ...n("wall", ""), x: 0, y: .5, z: 0 }];
  const edges = Array.from({ length: 640 }, (_, i) => ({ ...edge(i % 2 ? "b" : "a", i % 2 ? "a" : "b", "RELATED_TO"),
    id: `repeated${i}`, confidence: i / 640, evidence: `access evidence${i}` }));
  const at = performance.now(), routed = routeAcrossTopics(AvoidLib.getInstance(), nodes, edges);
  check({ nodes, edges: routed, topicEdges: [] }, edges);
  assert.ok(performance.now() - at < 3000, "duplicate connector geometry makes access-leg routing unbounded");
  assert.deepEqual(routed[0].route.points, [...routed[1].route.points].reverse());
  assert.ok(routed[0].route.points.some(p => Math.abs(p.x) >= .114), "shared route cuts through the intervening label");
});

test("many connectors in a modest Topic graph remain bounded and avoid every label", t => {
  const nodes = Array.from({ length: 96 }, (_, i) => ({ ...n(`dense${i}`, "2020"),
    x: i % 12 * .33, y: Math.floor(i / 12) * .33, z: 0 }));
  const edges = nodes.slice(1).map(node => edge(nodes[0].id, node.id, "RELATED_TO"));
  edges.push(edge(nodes[0].id, nodes[95].id, "PREREQUISITE_OF", "dense-parallel"));
  const at = performance.now(), routed = routeAcrossTopics(AvoidLib.getInstance(), nodes, edges);
  const elapsedMs = Math.round(performance.now() - at);
  check({ nodes, edges: routed, topicEdges: [] }, edges);
  for (const e of routed) for (const node of nodes) {
    if (node.id === e.source || node.id === e.target) continue;
    for (let i = 1; i < e.route.points.length; i++) {
      const a = e.route.points[i - 1], b = e.route.points[i];
      assert.ok(!(Math.max(a.x, b.x) > node.x - .11 + 1e-8 && Math.min(a.x, b.x) < node.x + .11 - 1e-8 &&
        Math.max(a.y, b.y) > node.y - .024 + 1e-8 && Math.min(a.y, b.y) < node.y + .066 - 1e-8),
        `dense routing cuts through ${node.id}`);
    }
  }
  assert.ok(elapsedMs < 60000, "a modest Topic exhausted the layout deadline");
  t.diagnostic(JSON.stringify({ nodes: 96, edges: 96, routingMs: elapsedMs, synthetic: true }));
});

test("empty graph avoids worker allocation; singleton remains navigable", async () => {
  assert.deepEqual(await temporalLayout([], [], "content", elk, route), { nodes: [], edges: [], topicEdges: [] });
  assert.deepEqual(await layoutInWorker([], [], "content", new AbortController().signal,
    () => { throw new Error("must not create a worker"); }), { nodes: [], edges: [], topicEdges: [] });
  const result = await temporalLayout([n("a", "")], [], "content", elk, route);
  assert.ok(Number.isFinite(result.nodes[0].x)); assert.equal(result.topicEdges.length, 1);
});

test("layout jobs terminate on success, abort, worker error and invalid messages", async () => {
  for (const outcome of ["success", "abort", "error", "messageerror"]) {
    let terminated = 0;
    let posted;
    const worker = { terminate: () => terminated++, postMessage: value => { posted = value; } };
    const controller = new AbortController();
    const job = layoutInWorker([n("a", "")], [], "content", controller.signal, () => worker);
    assert.equal(posted.basis, "content");
    if (outcome === "success") worker.onmessage({ data: { graph: { nodes: [], edges: [], topicEdges: [] } } });
    if (outcome === "abort") controller.abort();
    if (outcome === "error") worker.onerror({ message: "test failure" });
    if (outcome === "messageerror") worker.onmessageerror();
    if (outcome === "success") await job; else await assert.rejects(job);
    assert.equal(terminated, 1);
    worker.onmessage({ data: { graph: { nodes: [], edges: [], topicEdges: [] } } });
    assert.equal(terminated, 1, "late completion cannot terminate or settle again");
  }
});

test("content-free stage progress leaves the job alive until its actual graph arrives", async () => {
  let terminated = 0, settled = false;
  const worker = { terminate: () => terminated++, postMessage: () => {} };
  const job = layoutInWorker([n("a", "")], [], "content", new AbortController().signal, () => worker);
  job.then(() => { settled = true; });
  worker.onmessage({ data: { progress: { phase: "elk", callsDone: 4, activeNodes: 12, activeEdges: 18, elapsedMs: 5000 } } });
  worker.onmessage({ data: { progress: { phase: "routing", callsDone: 5, activeNodes: 30, activeEdges: 4, elapsedMs: 10000 } } });
  await Promise.resolve();
  assert.equal(settled, false); assert.equal(terminated, 0);
  const graph = { nodes: [], edges: [], topicEdges: [] };
  worker.onmessage({ data: { graph } });
  assert.deepEqual(await job, graph); assert.equal(terminated, 1);
});

test("the layout deadline follows progress and still has an overall cap", async () => {
  const run = (limits, beats) => {
    let terminated = 0;
    const worker = { terminate: () => terminated++, postMessage: () => {} };
    const job = layoutInWorker([n("a", "")], [], "content", new AbortController().signal, () => worker, 1.6, limits);
    const timer = setInterval(() => { if (beats-- > 0) worker.onmessage({ data: { progress: { phase: "elk", callsDone: 1,
      activeNodes: 1, activeEdges: 0, elapsedMs: 1 } } }); }, 20);
    return { job: job.finally(() => clearInterval(timer)), terminated: () => terminated };
  };
  const stalled = run({ idleMs: 60, capMs: 5000 }, 0);
  await assert.rejects(stalled.job, /no progress/); assert.equal(stalled.terminated(), 1);
  const reporting = run({ idleMs: 60, capMs: 300 }, 1000);
  const started = Date.now();
  await assert.rejects(reporting.job, /over the cap/);
  assert.ok(Date.now() - started >= 250, "progress kept the job alive past the idle limit");
  assert.equal(reporting.terminated(), 1);
});

test("drag translations and independent endpoint movement retain orthogonal sections", () => {
  const points = [{ x: 0, y: 0, z: 0 }, { x: 1, y: 0, z: 0 }, { x: 1, y: 1, z: 0 }, { x: 2, y: 1, z: 0 }];
  const translate = movedRoute(points, { x: 3, y: 2, z: 1 }, { x: 5, y: 3, z: 1 });
  assert.equal(translate.length, points.length); axisAligned(translate);
  const drag = movedRoute(points, { x: -.5, y: .4, z: .5 }, { x: 4, y: 2, z: -1 });
  axisAligned(drag); assert.deepEqual(drag[0], { x: -.5, y: .4, z: .5 }); assert.deepEqual(drag.at(-1), { x: 4, y: 2, z: -1 });
  assert.deepEqual(pointOnRoute(points, routeLength(points) - .5), { x: 1.5, y: 1, z: 0 });
});

test("3d arrows and photons follow the routed geometry and visibility", () => {
  const points = [{ x: 0, y: 0, z: 0 }, { x: 1, y: 0, z: 0 }, { x: 1, y: 1, z: 0 }, { x: 2, y: 1, z: 0 }];
  const object = new RoutedLinkObject({ points, engine: "elk" });
  const paint = { color: "#ffffff", width: 0, arrow: true, particles: true,
    particleColor: "#ffffff", visible: true, targetRadius: 1 };
  object.update(points[0], { x: 20, y: 10, z: 0 }, 10, paint, 1000);
  const [line, arrow, ...photons] = object.children;
  const pos = line.geometry.attributes.position;
  axisAligned(Array.from({ length: pos.count }, (_, i) => ({ x: pos.getX(i), y: pos.getY(i), z: pos.getZ(i) })));
  assert.equal(arrow.visible, true);
  for (const p of photons) assert.ok(p.position.y === 0 || p.position.x === 10 || p.position.y === 10);
  object.update(points[0], { x: 20, y: 10, z: 0 }, 10, { ...paint, visible: false }, 2000);
  assert.equal(object.visible, false);
  object.update(points[0], { x: 20, y: 10, z: 0 }, 10, { ...paint, color: "#ffffff00", particles: false }, 3000);
  assert.equal(line.visible, false); assert.equal(arrow.visible, false);
  assert.ok(photons.every(p => !p.visible));
  // Overview detail changes ink, never relation geometry or particle direction.
  object.update(points[0], { x: 20, y: 10, z: 0 }, 10, { ...paint, detail: .1 }, 4000);
  assert.equal(line.material.opacity, .085);
  assert.equal(arrow.material.opacity, .085);
  assert.ok(photons.every(p => p.material.opacity === .1));
  object.update(points[0], { x: 20, y: 10, z: 0 }, 10, paint, 4000);
  assert.equal(line.material.opacity, .85);
  assert.ok(photons.every(p => p.material.opacity === 1));
});

test("larger graph retains every authored relation and finishes within the worker deadline", async (t) => {
  const nodes = Array.from({ length: 1000 }, (_, i) => n(`n${i}`, `2020-${String(i % 12 + 1).padStart(2, "0")}`, `topic/${i % 4}`));
  const edges = nodes.slice(4).map((node, i) => edge(nodes[i].id, node.id));
  for (let i = 0; i < 40; i++) edges.push(edge(nodes[i].id, nodes[i + 1].id, "RELATED_TO"));
  const start = performance.now();
  const result = await temporalLayout(nodes, edges, "content", elk, route);
  check(result, edges);
  const elapsedMs = Math.round(performance.now() - start);
  assert.ok(elapsedMs < 60000, "layout exceeds the production worker deadline");
  t.diagnostic(JSON.stringify({ nodes: 1000, authoredEdges: edges.length, elapsedMs, synthetic: true }));
});

test("many-topic graph keeps every cross relation within the worker deadline", async (t) => {
  const nodes = Array.from({ length: 4000 }, (_, i) => n(`n${i}`, `2020-${String(i % 12 + 1).padStart(2, "0")}`, `topic/${i % 79}`));
  const edges = nodes.slice(79).map((node, i) => edge(nodes[i].id, node.id));
  for (let i = 0; i < 300; i++) edges.push(edge(nodes[i].id, nodes[i + 1].id, "RELATED_TO"));
  const start = performance.now();
  const result = await temporalLayout(nodes, edges, "content", elk, route);
  check(result, edges);
  const elapsedMs = Math.round(performance.now() - start);
  assert.ok(elapsedMs < 60000, "many-topic layout exceeds the production worker deadline");
  for (const e of result.edges.slice(-300).filter((_, i) => i % 10 === 0)) {
    for (const obstacle of result.nodes) {
      if (obstacle.id === e.source || obstacle.id === e.target) continue;
      for (let j = 1; j < e.route.points.length; j++) {
        const a = e.route.points[j - 1], b = e.route.points[j];
        assert.ok(!(Math.max(a.x, b.x) > obstacle.x - .11 + 1e-8 && Math.min(a.x, b.x) < obstacle.x + .11 - 1e-8 &&
          Math.max(a.y, b.y) > obstacle.y - .024 + 1e-8 && Math.min(a.y, b.y) < obstacle.y + .066 - 1e-8),
          `hierarchical cross-topic route ${e.id} cuts through ${obstacle.id}: ${JSON.stringify([a, b])} at ${JSON.stringify([obstacle.x, obstacle.y])}`);
      }
    }
  }
  t.diagnostic(JSON.stringify({ nodes: 4000, topics: 79, authoredEdges: edges.length, elapsedMs, synthetic: true }));
});

test("large maps share Topic corridors while preserving parallel and reversed relation metadata", async () => {
  const nodes = Array.from({ length: 600 }, (_, i) => n(`shared${i}`, "2020", `topic/${i % 2}`));
  const edges = Array.from({ length: 48 }, (_, i) => ({ ...edge(`shared${i * 2}`, `shared${i * 2 + 1}`, "RELATED_TO"),
    id: `original${i}`, confidence: i / 48, evidence: `evidence${i}` }));
  edges.push({ ...edges[0], id: "parallel", confidence: .7, evidence: "parallel evidence" },
    { ...edges[0], id: "reversed", source: edges[0].target, target: edges[0].source, evidence: "reverse evidence" });
  let corridorCount = 0;
  const graph = await temporalLayout(nodes, edges, "content", elk, (ns, es, batching) => {
    if (batching === "connector") corridorCount += es.length;
    return route(ns, es, batching);
  });
  check(graph, edges);
  assert.ok(corridorCount > 0 && corridorCount < edges.length, "external corridor geometry was recomputed per original relation");
  for (const e of graph.edges) for (const node of graph.nodes) {
    if (node.id === e.source || node.id === e.target) continue;
    for (let i = 1; i < e.route.points.length; i++) {
      const a = e.route.points[i - 1], b = e.route.points[i];
      assert.ok(!(Math.max(a.x, b.x) > node.x - .11 + 1e-8 && Math.min(a.x, b.x) < node.x + .11 - 1e-8 &&
        Math.max(a.y, b.y) > node.y - .024 + 1e-8 && Math.min(a.y, b.y) < node.y + .066 - 1e-8),
        `shared corridor ${e.id} cuts through ${node.id}`);
    }
  }
});

test("a connected timeline with unique instants stacks branches right of its Topic without losing continuation edges", async () => {
  const nodes = Array.from({ length: 180 }, (_, i) => n(`instant${i}`,
    new Date(Date.UTC(2020, 0, 1, 0, 0, i)).toISOString()));
  const edges = nodes.slice(1).map((node, i) => edge(nodes[i].id, node.id));
  edges.push(edge(nodes[170].id, nodes[2].id, "RELATED_TO"));
  const branches = [];
  const observedElk = { layout: graph => {
    if (graph.layoutOptions["elk.algorithm"] === "layered") branches.push({
      ids: new Set(graph.children.map(n => n.id)), direction: graph.layoutOptions["elk.direction"] });
    return elk.layout(graph);
  } };
  const result = await temporalLayout(nodes, edges, "content", observedElk, route, 1.6);
  check(result, edges);
  const topic = result.nodes.find(n => n.kind === "domain");
  assert.ok(result.nodes.every(n => n === topic || n.x > topic.x), "a branch grew left of its Topic");
  assert.ok(result.nodes.some(n => n.y < topic.y) && result.nodes.some(n => n.y > topic.y),
    "the Topic is not at the vertical middle of its stacked branches");
  assert.deepEqual(new Set(branches.map(b => b.direction)), new Set(["RIGHT"]));
  const width = Math.max(...result.nodes.map(n => n.x)) - Math.min(...result.nodes.map(n => n.x));
  const height = Math.max(...result.nodes.map(n => n.y)) - Math.min(...result.nodes.map(n => n.y));
  assert.ok(width > height, "a connected timeline does not read left to right");
  assert.ok(result.edges.some(e => e.route.engine === "libavoid"), "continuation edges between branches were discarded");
  for (const e of result.edges.filter(e => e.route.engine === "elk" && e.type === "PREREQUISITE_OF")) {
    const a = result.nodes.find(n => n.id === e.source), b = result.nodes.find(n => n.id === e.target);
    const direction = branches.find(branch => branch.ids.has(a.id) && branch.ids.has(b.id)).direction;
    const outward = { RIGHT: b.x - a.x, LEFT: a.x - b.x, DOWN: b.y - a.y, UP: a.y - b.y }[direction];
    assert.ok(outward > 0, `within-branch chronology does not point ${direction} outward`);
  }
});

test("stacked branches share a regular grid without rotating marks or cutting through their labels", async () => {
  const nodes = Array.from({ length: 16 }, (_, i) => n(`grid${i}`, `2020-${String(i + 1).padStart(2, "0")}-01`));
  const edges = nodes.slice(1).map((node, i) => edge(nodes[i].id, node.id));
  edges.push(edge("grid14", "grid1", "RELATED_TO"));
  const graph = await temporalLayout(nodes, edges, "content", elk, route);
  check(graph, edges);
  const topic = graph.nodes.find(n => n.kind === "domain");
  for (const node of graph.nodes) for (const axis of ["x", "y"]) {
    const cell = (node[axis] - topic[axis]) / .33;
    assert.ok(Math.abs(cell - Math.round(cell)) < 1e-7, `${node.id} is off the ${axis} grid`);
  }
  for (const a of graph.nodes) for (const b of graph.nodes) if (a !== b)
    assert.ok(Math.abs(a.x - b.x) >= .22 || Math.abs(a.y - b.y) >= .09, "mark/label rectangles overlap");
  for (const edge of [...graph.edges, ...graph.topicEdges]) for (const node of graph.nodes) {
    if (node.id === edge.source || node.id === edge.target) continue;
    for (let i = 1; i < edge.route.points.length; i++) {
      const a = edge.route.points[i - 1], b = edge.route.points[i];
      assert.ok(!(Math.max(a.x, b.x) > node.x - .11 + 1e-8 && Math.min(a.x, b.x) < node.x + .11 - 1e-8 &&
        Math.max(a.y, b.y) > node.y - .024 + 1e-8 && Math.min(a.y, b.y) < node.y + .066 - 1e-8),
        `${edge.id || edge.type} cuts through ${node.id}`);
    }
  }
});

test("Topic composition uses both axes and responds to landscape and portrait aspect ratios", async () => {
  const nodes = Array.from({ length: 24 }, (_, i) => n(`map${i}`, "2020", `topic/${i}`));
  const landscape = await temporalLayout(nodes, [], "content", elk, route, 2.4);
  const portrait = await temporalLayout(nodes, [], "content", elk, route, .6);
  const extent = graph => {
    const ts = graph.nodes.filter(n => n.kind === "domain");
    assert.ok(new Set(ts.map(n => n.x)).size > 1 && new Set(ts.map(n => n.y)).size > 1);
    return (Math.max(...ts.map(n => n.x)) - Math.min(...ts.map(n => n.x))) /
      (Math.max(...ts.map(n => n.y)) - Math.min(...ts.map(n => n.y)));
  };
  assert.ok(extent(landscape) > extent(portrait));
  for (const graph of [landscape, portrait]) check(graph, []);
});

test("a spatial Topic grows one arm along +x with lanes across and in depth, and clear original directed routes", async () => {
  {
    const nodes = Array.from({ length: 36 }, (_, i) => n(`space${i}`,
      new Date(Date.UTC(2020, 0, i + 1)).toISOString()));
    const edges = nodes.slice(1).map((node, i) => edge(nodes[i].id, node.id));
    edges.push(edge("space31", "space2", "RELATED_TO"), edge("space0", "space0"),
      edge("space8", "space7", "DERIVES_FROM"));
    const graph = await spatialTemporalLayout(nodes, edges, "content", elk, route);
    check(graph, edges);
    const topic = graph.nodes.find(n => n.kind === "domain"), axes = ["x", "y", "z"];
    // 팔은 하나(+x)다. 레인은 y 로 나란히 서고 깊이(z)로 물러난다.
    const arm = () => "x:1";
    const members = graph.nodes.filter(n => n.kind !== "domain");
    assert.ok(members.every(m => m.x > topic.x), "a member sits left of its Topic");
    assert.ok(new Set(members.map(n => n.y)).size > 1, "lanes do not sit side by side");
    for (const node of graph.nodes) for (const axis of axes)
      assert.ok(Math.abs((node[axis] - topic[axis]) / .33 - Math.round((node[axis] - topic[axis]) / .33)) < 1e-7);
    for (const e of graph.edges) {
      axisAligned(e.route.points2d);
      const a = graph.nodes.find(n => n.id === e.source), b = graph.nodes.find(n => n.id === e.target);
      assert.deepEqual(e.route.points2d[0], { x: a.x2, y: -a.y2, z: e.route.points2d[0].z });
      if (e.source !== e.target && e.type === "PREREQUISITE_OF" && arm(a) === arm(b) &&
          axes.filter(axis => axis !== arm(a)[0]).every(axis => Math.abs(a[axis] - b[axis]) < 1e-8)) {
        const axis = arm(a)[0], sign = Math.sign(a[axis] - topic[axis]);
        assert.ok(sign * (b[axis] - a[axis]) > 0, `spatial branch reverses ${e.source}->${e.target} ${JSON.stringify([a,b])}`);
      }
    }
    for (const e of [...graph.edges, ...graph.topicEdges]) for (const node of graph.nodes) {
      if (node.id === e.source || node.id === e.target) continue;
      for (let i = 1; i < e.route.points.length; i++) {
        const a = e.route.points[i - 1], b = e.route.points[i];
        assert.ok(!axes.every(axis => Math.max(a[axis], b[axis]) > node[axis] - .12 + 1e-8 &&
          Math.min(a[axis], b[axis]) < node[axis] + .12 - 1e-8), "spatial route intersects another mark");
      }
    }
  }
});

test("Topic cells flow left to right in time, main topic first, in 2D and 3D alike", async () => {
  // 24 주제, 날짜를 이름과 어긋나게 준다. vinc 는 하위 주제를 거느린 메인 토픽이다.
  const nodes = Array.from({ length: 24 }, (_, i) =>
    n(`volume${i}`, `20${String(40 - i).padStart(2, "0")}`, `vinc/t${String(i).padStart(2, "0")}`));
  nodes.push(n("root", "2090", "vinc"));
  const spatial = await spatialTemporalLayout(structuredClone(nodes), [], "content", elk, route);
  const planar = await temporalLayout(structuredClone(nodes), [], "content", elk, route);
  for (const graph of [spatial, planar]) {
    const topics = graph.nodes.filter(n => n.kind === "domain" && n.id !== "domain:vinc");
    const main = graph.nodes.find(n => n.id === "domain:vinc");
    assert.ok(topics.every(t => main.x <= t.x), "the main topic is not leftmost");
    // 가장 이른 주제(t23, 2017)가 가장 늦은 주제(t00, 2040)보다 왼쪽이다.
    const at = id => graph.nodes.find(n => n.id === `domain:vinc/${id}`).x;
    assert.ok(at("t23") < at("t00"), "earlier topics do not come first from the left");
    assert.ok(new Set(topics.map(n => n.x)).size > 1 && new Set(topics.map(n => n.y)).size > 1);
  }
  assert.equal(new Set(spatial.nodes.map(n => [n.x, n.y, n.z].join(":"))).size, spatial.nodes.length);
});

test("shortcuts remove empty detours but preserve required obstacle detours and self loops", () => {
  const nodes = [{ ...n("a", ""), x: 0, y: 0, z: 0 }, { ...n("b", ""), x: 2, y: 0, z: 0 }];
  const detour = [{ x: 0, y: 0, z: 0 }, { x: 0, y: 1, z: 0 },
    { x: 2, y: 1, z: 0 }, { x: 2, y: 0, z: 0 }];
  const e = { ...edge("a", "b"), route: { engine: "libavoid", points: detour } };
  shortenPlanarRoutes(nodes, [e]);
  assert.equal(routeLength(e.route.points), 2); assert.equal(e.route.points.length, 2);
  const blocked = { ...edge("a", "b"), route: { engine: "libavoid", points: detour } };
  shortenPlanarRoutes([...nodes, { ...n("obstacle", ""), x: 1, y: 0, z: 0 }], [blocked]);
  assert.ok(routeLength(blocked.route.points) > 2);
  const loop = { ...edge("a", "a"), route: { engine: "libavoid", points: [...detour, detour[0]] } };
  const snapshot = structuredClone(loop);
  shortenPlanarRoutes(nodes, [loop]); assert.deepEqual(loop, snapshot);
});

test("stored topic pieces reproduce a full layout and only a changed topic is laid out again", async () => {
  const nodes = [];
  for (let t = 0; t < 4; t++) for (let i = 0; i < 9; i++) nodes.push(n(`t${t}n${i}`, `2025-${String(i % 12 + 1).padStart(2, "0")}`, `vinc/t${t}`));
  const edges = [];
  for (let t = 0; t < 4; t++) for (let i = 1; i < 9; i++) edges.push(edge(`t${t}n${i - 1}`, `t${t}n${i}`));
  edges.push(edge("t0n2", "t1n3", "RELATED_TO"), edge("t2n4", "t3n1", "DERIVES_FROM"));
  const calls = [];
  const counted = { layout: input => { calls.push(input.id); return elk.layout(input); } };
  const run = (ns, es, pieces) => spatialTemporalLayout(structuredClone(ns), structuredClone(es), "content", counted, route,
    1.6, pieces ? { pieces } : {});
  const store = new Map();
  const fresh = await run(nodes, edges);
  calls.length = 0;
  const cold = await run(nodes, edges, store);
  assert.deepEqual(cold, fresh, "a cold cache gives the full layout");
  const coldTopicCalls = calls.filter(id => id.startsWith("topic:")).length;
  assert.ok(coldTopicCalls >= 4 && store.size === 4);
  calls.length = 0;
  const warm = await run(nodes, edges, store);
  assert.deepEqual(warm, fresh, "stored pieces give the same layout");
  assert.deepEqual(calls.filter(id => id.startsWith("topic:")), [], "no topic is laid out again");
  // One new node and relation in topic t2 only.
  const changedNodes = [...nodes, n("t2n9", "2025-10", "vinc/t2")], changedEdges = [...edges, edge("t2n8", "t2n9")];
  calls.length = 0;
  const incremental = await run(changedNodes, changedEdges, store);
  const laidOut = new Set(calls.filter(id => id.startsWith("topic:")).map(id => id.split(":").slice(1, -2).join(":")));
  assert.deepEqual([...laidOut], ["domain:vinc/t2"], "only the changed topic is laid out");
  assert.deepEqual(incremental, await run(changedNodes, changedEdges), "the incremental result equals a full layout");
  // A stored piece is not changed by using it.
  const snapshot = structuredClone([...store.values()]);
  await run(changedNodes, changedEdges, store);
  assert.deepEqual(structuredClone([...store.values()]).slice(0, snapshot.length), snapshot);
});

test("a topic piece does not depend on how large the rest of the graph is", async () => {
  const topic = Array.from({ length: 6 }, (_, i) => n(`a${i}`, `2025-0${i + 1}`, "vinc/a"));
  const inner = topic.slice(1).map((node, i) => edge(topic[i].id, node.id));
  // Past 500 nodes the whole-graph rule used to lower ELK thoroughness for every topic.
  const crowd = Array.from({ length: 520 }, (_, i) => n(`b${i}`, `2025-0${i % 9 + 1}`, `vinc/b${i % 20}`));
  const calls = [];
  const counted = { layout: input => { calls.push(input.id); return elk.layout(input); } };
  const store = new Map();
  await spatialTemporalLayout(structuredClone(topic), structuredClone(inner), "content", counted, route, 1.6, { pieces: store });
  calls.length = 0;
  await spatialTemporalLayout(structuredClone([...topic, ...crowd]), structuredClone(inner), "content", counted, route,
    1.6, { pieces: store });
  assert.equal(calls.filter(id => id.startsWith("topic:domain:vinc/a:")).length, 0, "the small topic is reused from the store");
});

test("a previous placement keeps unchanged topic boxes and their corridors, and a change reroutes only what it touches", async (t) => {
  // More than 500 marks so routing goes through Topic boxes, portals and corridors.
  const nodes = [], edges = [];
  for (let k = 0; k < 8; k++) for (let i = 0; i < 70; i++)
    nodes.push(n(`k${k}n${i}`, `2025-${String(i % 12 + 1).padStart(2, "0")}`, `vinc/k${k}`));
  for (let k = 0; k < 8; k++) for (let i = 1; i < 70; i++) edges.push(edge(`k${k}n${i - 1}`, `k${k}n${i}`));
  for (let k = 0; k < 8; k++) for (let j = 0; j < 4; j++) edges.push(edge(`k${k}n${j * 9}`, `k${(k + j + 1) % 8}n${j * 7 + 3}`, "RELATED_TO"));
  const elkCalls = [], routerCalls = [];
  const engine = { layout: input => { elkCalls.push(input.id); return elk.layout(input); } };
  const router = (ns, es, b) => { routerCalls.push({ edges: es.length, batching: b || "" }); return route(ns, es, b); };
  const pieces = new Map(), routes = new Map();
  const run = async (ns, es, previous) => {
    const next = new Map();
    const graph = await spatialTemporalLayout(structuredClone(ns), structuredClone(es), "content", engine, router, 1.6,
      { pieces, routes, placement: { previous, next } });
    return { graph, next };
  };
  const fresh = await spatialTemporalLayout(structuredClone(nodes), structuredClone(edges), "content", elk, route, 1.6);
  const first = await run(nodes, edges);
  assert.deepEqual(first.graph, fresh, "the first run equals a layout without any store");
  assert.equal(first.next.size, 8);

  elkCalls.length = 0; routerCalls.length = 0;
  const again = await run(nodes, edges, first.next);
  assert.deepEqual(again.graph, first.graph, "the same inputs with the previous placement give the same layout");
  assert.deepEqual(elkCalls, [], "no topic and no packing is laid out again");
  assert.deepEqual(routerCalls, [], "no corridor or leg is routed again");

  // k3 grows by one mark and one relation.
  const grownNodes = [...nodes, n("k3n70", "2025-11", "vinc/k3")], grownEdges = [...edges, edge("k3n69", "k3n70")];
  elkCalls.length = 0; routerCalls.length = 0;
  const grown = await run(grownNodes, grownEdges, first.next);
  check(grown.graph, grownEdges);
  assert.deepEqual([...new Set(elkCalls.filter(id => id.startsWith("topic:")).map(id => id.split(":").slice(1, -2).join(":")))],
    ["domain:vinc/k3"], "only the grown topic is laid out");
  assert.ok(!elkCalls.includes("view-topics"), "the boxes are not packed again");
  for (const [id, q] of first.next) if (id !== "domain:vinc/k3") assert.deepEqual(grown.next.get(id), q, `${id} stays in place`);
  const corridorEdges = routerCalls.filter(c => c.batching === "connector").reduce((s, c) => s + c.edges, 0);
  const firstCorridors = [...routes.keys()].filter(k => k.startsWith("corridor:")).length;
  assert.ok(corridorEdges < firstCorridors, `only corridors touching the changed box are routed (${corridorEdges} of ${firstCorridors})`);
  // Every route still clears every mark it does not end at, on the 2D projection the map draws.
  const marks = grown.graph.nodes.map(m => ({ ...m, x: m.x2, y: -m.y2 }));
  for (const e of grown.graph.edges) for (const mark of marks) {
    const points = e.route?.points2d;
    if (mark.id === e.source || mark.id === e.target || mark.kind === "domain" || !points) continue;
    for (let j = 1; j < points.length; j++) {
      const a = points[j - 1], b = points[j];
      assert.ok(!(Math.max(a.x, b.x) > mark.x - .11 + 1e-8 && Math.min(a.x, b.x) < mark.x + .11 - 1e-8 &&
        Math.max(a.y, b.y) > mark.y - .024 + 1e-8 && Math.min(a.y, b.y) < mark.y + .066 - 1e-8),
        `route ${e.id} cuts through ${mark.id}`);
    }
  }
  t.diagnostic(JSON.stringify({ corridorsRouted: corridorEdges, corridorsStored: firstCorridors }));
});

test("many topics fill the 3D cross section instead of standing on one ring", async () => {
  // 주제가 많으면 고리 하나의 반지름이 주제 수에 비례해 시간 축보다 길어진다(계정 그래프 174개).
  const nodes = [];
  for (let t = 0; t < 120; t++) for (let i = 0; i < 2; i++)
    nodes.push(n(`t${t}n${i}`, `2026-${String(i + 1).padStart(2, "0")}`, `vinc/a${t % 6}/t${t}`));
  const graph = await spatialTemporalLayout(nodes, [], "content", elk, route);
  const topics = graph.nodes.filter(n => n.kind === "domain");
  const radii = topics.map(n => Math.hypot(n.y, n.z));
  const xs = graph.nodes.map(n => n.x);
  assert.ok(Math.max(...radii) <= Math.max(...xs) - Math.min(...xs), "the topics stand on one wide ring");
  // 안쪽도 찬다: 가장 바깥 반지름의 절반 안에 선 주제가 있다.
  assert.ok(radii.filter(r => r > 0 && r < Math.max(...radii) / 2).length >= 6, "the cross section is hollow");
});

test("3D topic cells group by area and still start left to right in time across areas", async () => {
  // 영역 셋, 영역마다 주제 여덟. 날짜는 영역을 가로질러 엇갈리게 준다(영역 순서와 시간 순서가 다르다).
  const nodes = [];
  const areas = ["design", "engineering", "ai"];
  for (let t = 0; t < 24; t++) for (let i = 0; i < 3; i++)
    nodes.push(n(`t${t}n${i}`, `20${String(10 + t).padStart(2, "0")}-0${i + 1}`, `vinc/${areas[t % 3]}/t${t}`));
  const graph = await spatialTemporalLayout(nodes, [], "content", elk, route);
  const topics = graph.nodes.filter(n => n.kind === "domain");
  const areaOf = t => t.domain.split("/")[1];
  const groups = new Map();
  for (const t of topics) { if (!groups.has(areaOf(t))) groups.set(areaOf(t), []); groups.get(areaOf(t)).push(t); }
  assert.equal(groups.size, 3);
  const centre = list => ({ y: list.reduce((s, t) => s + t.y, 0) / list.length, z: list.reduce((s, t) => s + t.z, 0) / list.length });
  const centres = new Map([...groups].map(([a, list]) => [a, centre(list)]));
  // 모든 주제는 다른 영역의 중심보다 제 영역의 중심에 더 가깝다(영역마다 한 덩어리).
  for (const t of topics) {
    const d = c => Math.hypot(t.y - c.y, t.z - c.z), own = d(centres.get(areaOf(t)));
    for (const [a, c] of centres) if (a !== areaOf(t)) assert.ok(own < d(c), `${t.id} sits nearer ${a} than its own area`);
  }
  // x 는 영역과 무관하게 시간 순서다: 가장 이른 주제(t0)가 가장 늦은 주제(t23)보다 왼쪽이다.
  const at = id => topics.find(t => t.id.endsWith(`/${id}`)).x;
  assert.ok(at("t0") < at("t23"), "time no longer runs left to right across areas");
  assert.equal(new Set(graph.nodes.map(n => [n.x, n.y, n.z].join(":"))).size, graph.nodes.length);
  // 그래서 영역 부피를 그린다: view3d 의 areaBox 와 같은 셈으로 상자를 지으면 서로 떨어져 있다.
  // 반지름은 렌더 배율(140) 아래 가장 큰 노드(12)다.
  const { areasApart } = await source("../src/area-key.ts");
  const r = 12 / 140, shells = [];
  for (const area of areas) {
    const pts = graph.nodes.filter(n => (n.domain || "").split("/")[1] === area);
    let x0 = Infinity, x1 = -Infinity, cy = 0, cz = 0;
    for (const p of pts) { x0 = Math.min(x0, p.x - r); x1 = Math.max(x1, p.x + r); cy += p.y; cz += p.z; }
    cy /= pts.length; cz /= pts.length;
    let rc = 0;
    for (const p of pts) rc = Math.max(rc, Math.hypot(p.y - cy, p.z - cz) + r);
    shells.push({ cx: (x0 + x1) / 2, cy, cz, hx: Math.max((x1 - x0) / 2 * 1.1, rc), rc: rc * 1.18 });
  }
  assert.equal(areasApart(shells), true, "grouped areas must keep their volumes");
});

test("3D topic cells group by area when topic nodes carry their parent path, as the hub sends them", async () => {
  // 허브의 주제 노드는 `domain` 열에 **부모** 경로를 싣는다(api/query.py 의 주제 마크:
  // `domain:vinc/design/t0` 는 domain "vinc/design", `domain:vinc` 는 domain ""). 영역 열쇠를 그 열에서
  // 읽으면 공통 접두가 비어 모든 주제가 "vinc" 한 영역이 되고, 칸이 영역별로 묶이지 않았다.
  const areas = ["design", "engineering", "ai"];
  const topicMark = (path) => ({ id: `domain:${path}`, label: path.split("/").at(-1), kind: "domain",
    domain: path.split("/").slice(0, -1).join("/"), degree: 1 });
  const nodes = [topicMark("vinc"), ...areas.map(a => topicMark(`vinc/${a}`))];
  for (let t = 0; t < 24; t++) {
    nodes.push(topicMark(`vinc/${areas[t % 3]}/t${t}`));
    for (let i = 0; i < 3; i++) nodes.push(n(`t${t}n${i}`, `20${String(10 + t).padStart(2, "0")}-0${i + 1}`, `vinc/${areas[t % 3]}/t${t}`));
  }
  const graph = await spatialTemporalLayout(nodes, [], "content", elk, route);
  const leaves = graph.nodes.filter(x => x.kind === "domain" && /\/t\d+$/.test(x.id));
  const areaOf = t => t.id.split("/")[1];
  const centres = new Map(areas.map(a => {
    const list = leaves.filter(t => areaOf(t) === a);
    return [a, { y: list.reduce((s, t) => s + t.y, 0) / list.length, z: list.reduce((s, t) => s + t.z, 0) / list.length }];
  }));
  for (const t of leaves) {
    const d = c => Math.hypot(t.y - c.y, t.z - c.z), own = d(centres.get(areaOf(t)));
    for (const [a, c] of centres) if (a !== areaOf(t)) assert.ok(own < d(c), `${t.id} sits nearer ${a} than its own area`);
  }
});

test("area keys strip the common domain prefix and take the next step", async () => {
  const { areaKeys } = await source("../src/view-layout-spatial.ts");
  assert.deepEqual(areaKeys(["vinc/design/tokens", "vinc/ai", "vinc", "vinc/design"]), ["design", "ai", "", "design"]);
  assert.deepEqual(areaKeys(["vinc/design/a", "vinc/design/b/c"]), ["a", "b"]);
  assert.deepEqual(areaKeys(["", "notes"]), ["", "notes"]);
  assert.deepEqual(areaKeys([]), []);
});
