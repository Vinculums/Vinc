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
const { LAYOUTS, normalizeLayout, layoutsFor, serverLayoutFor, isRootsLayout, loadPrefs, savePrefs } = await source("../src/view-prefs.ts");

test("two layouts remain and retired saved values fold into time branches", () => {
  const saved = new Map();
  globalThis.localStorage = { getItem: k => saved.get(k) ?? null, setItem: (k, v) => saved.set(k, v) };
  try {
    assert.equal(loadPrefs().layout, "hub");
    assert.deepEqual(LAYOUTS, ["hub", "temporalRoots"]);
    for (const layout of LAYOUTS) {
      savePrefs({ ...loadPrefs(), layout });
      assert.equal(loadPrefs().layout, layout);
      assert.equal(normalizeLayout(layout), layout);
      assert.equal(serverLayoutFor(layout), "hub");
      assert.equal(isRootsLayout(layout), layout === "temporalRoots");
    }
    for (const view of ["2d", "3d", "timeline", "bundle"]) assert.deepEqual(layoutsFor(view), LAYOUTS);
    for (const retired of ["rank", "rankedRoots"]) {
      assert.equal(normalizeLayout(retired), "temporalRoots");
      saved.set("vinc.view.prefs", JSON.stringify({ layout: retired }));
      assert.equal(loadPrefs().layout, "temporalRoots");
    }
    assert.equal(normalizeLayout("radial"), "hub");
    assert.equal(normalizeLayout("unknown"), "hub");
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

test("connected groups stay in one topic wing and disconnected roots surround the topic", async () => {
  const result = await temporalLayout([n("a", "2020"), n("b", "2021"), n("c", "2020"), n("d", "2021")],
    [edge("a", "b"), edge("c", "d")], "content", elk, route);
  const map = new Map(result.nodes.map(n => [n.id, n]));
  assert.ok(map.get("a").x * map.get("b").x > 0);
  assert.ok(map.get("c").x * map.get("d").x > 0);
  assert.ok(map.get("a").x * map.get("c").x < 0);
  assert.equal(map.get("domain:topic").x, 0);
});

test("a small Topic with one connected chain keeps its Topic at the centre", async () => {
  const nodes = Array.from({ length: 6 }, (_, i) => n(`c${i}`, `202${i}`));
  const edges = nodes.slice(1).map((x, i) => edge(nodes[i].id, x.id));
  const result = await temporalLayout(nodes, edges, "content", elk, route);
  check(result, edges);
  const xs = result.nodes.filter(n => n.kind !== "domain").map(n => n.x);
  assert.equal(result.nodes.find(n => n.kind === "domain").x, 0);
  assert.ok(Math.min(...xs) < 0 && Math.max(...xs) > 0, "every branch sat on one side of its Topic");
});

test("ranked follows relationship stages when dates disagree, retaining cycles and original direction", async () => {
  const nodes = [n("root", "2022"), n("middle", "2021"), n("leaf", "2020")];
  const edges = [edge("root", "middle"), edge("leaf", "middle", "DERIVES_FROM")];
  const temporal = await temporalLayout(nodes, edges, "content", elk, route);
  const ranked = await temporalLayout(nodes, edges, "content", elk, route, "ranked");
  check(temporal, edges); check(ranked, edges);
  assert.ok(temporal.nodes[0].x > temporal.nodes[1].x && temporal.nodes[1].x > temporal.nodes[2].x);
  assert.ok(ranked.nodes[0].x < ranked.nodes[1].x && ranked.nodes[1].x < ranked.nodes[2].x);
  const cycles = [...edges, edge("leaf", "root"), edge("middle", "middle"), edge("root", "leaf", "RELATED_TO")];
  check(await temporalLayout([...nodes, n("unknown", "")], cycles, "content", elk, route, "ranked"), cycles);
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
    "temporal", () => { throw new Error("must not create a worker"); }), { nodes: [], edges: [], topicEdges: [] });
  const result = await temporalLayout([n("a", "")], [], "content", elk, route);
  assert.ok(Number.isFinite(result.nodes[0].x)); assert.equal(result.topicEdges.length, 1);
});

test("layout jobs terminate on success, abort, worker error and invalid messages", async () => {
  for (const outcome of ["success", "abort", "error", "messageerror"]) {
    let terminated = 0;
    let posted;
    const worker = { terminate: () => terminated++, postMessage: value => { posted = value; } };
    const controller = new AbortController();
    const job = layoutInWorker([n("a", "")], [], "content", controller.signal, "ranked", () => worker);
    assert.equal(posted.ordering, "ranked");
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
  const job = layoutInWorker([n("a", "")], [], "content", new AbortController().signal, "temporal", () => worker);
  job.then(() => { settled = true; });
  worker.onmessage({ data: { progress: { phase: "elk", callsDone: 4, activeNodes: 12, activeEdges: 18, elapsedMs: 5000 } } });
  worker.onmessage({ data: { progress: { phase: "routing", callsDone: 5, activeNodes: 30, activeEdges: 4, elapsedMs: 10000 } } });
  await Promise.resolve();
  assert.equal(settled, false); assert.equal(terminated, 0);
  const graph = { nodes: [], edges: [], topicEdges: [] };
  worker.onmessage({ data: { graph } });
  assert.deepEqual(await job, graph); assert.equal(terminated, 1);
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

test("a connected timeline with unique instants branches around its Topic without losing continuation edges", async () => {
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
  const result = await temporalLayout(nodes, edges, "content", observedElk, route, "temporal", 1.6);
  check(result, edges);
  const topic = result.nodes.find(n => n.kind === "domain");
  assert.ok(result.nodes.some(n => n.x < topic.x) && result.nodes.some(n => n.x > topic.x));
  assert.ok(result.nodes.some(n => n.y < topic.y) && result.nodes.some(n => n.y > topic.y));
  assert.deepEqual(new Set(branches.map(b => b.direction)), new Set(["RIGHT", "LEFT", "UP", "DOWN"]));
  const width = Math.max(...result.nodes.map(n => n.x)) - Math.min(...result.nodes.map(n => n.x));
  const height = Math.max(...result.nodes.map(n => n.y)) - Math.min(...result.nodes.map(n => n.y));
  assert.ok(width / height > .6 && width / height < 4, "connected timeline is still an elongated list");
  assert.ok(result.edges.some(e => e.route.engine === "libavoid"), "continuation edges between branches were discarded");
  for (const e of result.edges.filter(e => e.route.engine === "elk" && e.type === "PREREQUISITE_OF")) {
    const a = result.nodes.find(n => n.id === e.source), b = result.nodes.find(n => n.id === e.target);
    const direction = branches.find(branch => branch.ids.has(a.id) && branch.ids.has(b.id)).direction;
    const outward = { RIGHT: b.x - a.x, LEFT: a.x - b.x, DOWN: b.y - a.y, UP: a.y - b.y }[direction];
    assert.ok(outward > 0, `within-branch chronology does not point ${direction} outward`);
  }
});

test("four Topic arms share a regular grid without rotating marks or cutting through their labels", async () => {
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
  const landscape = await temporalLayout(nodes, [], "content", elk, route, "temporal", 2.4);
  const portrait = await temporalLayout(nodes, [], "content", elk, route, "temporal", .6);
  const extent = graph => {
    const ts = graph.nodes.filter(n => n.kind === "domain");
    assert.ok(new Set(ts.map(n => n.x)).size > 1 && new Set(ts.map(n => n.y)).size > 1);
    return (Math.max(...ts.map(n => n.x)) - Math.min(...ts.map(n => n.x))) /
      (Math.max(...ts.map(n => n.y)) - Math.min(...ts.map(n => n.y)));
  };
  assert.ok(extent(landscape) > extent(portrait));
  for (const graph of [landscape, portrait]) check(graph, []);
});

test("spatial Topics have six outward arms, distinct depth and clear original directed routes", async () => {
  for (const ordering of ["temporal", "ranked"]) {
    const nodes = Array.from({ length: 36 }, (_, i) => n(`space${i}`,
      new Date(Date.UTC(2020, 0, i + 1)).toISOString()));
    const edges = nodes.slice(1).map((node, i) => edge(nodes[i].id, node.id));
    edges.push(edge("space31", "space2", "RELATED_TO"), edge("space0", "space0"),
      edge("space8", "space7", "DERIVES_FROM"));
    const graph = await spatialTemporalLayout(nodes, edges, "content", elk, route, ordering);
    check(graph, edges);
    const topic = graph.nodes.find(n => n.kind === "domain"), axes = ["x", "y", "z"];
    const arm = node => { const axis = axes.toSorted((a, b) =>
      Math.abs(node[b] - topic[b]) - Math.abs(node[a] - topic[a]))[0];
      return `${axis}:${Math.sign(node[axis] - topic[axis])}`; };
    assert.equal(new Set(graph.nodes.filter(n => n.kind !== "domain").map(arm)).size, 6);
    for (const node of graph.nodes) for (const axis of axes)
      assert.ok(Math.abs((node[axis] - topic[axis]) / .33 - Math.round((node[axis] - topic[axis]) / .33)) < 1e-7);
    for (const e of graph.edges) {
      axisAligned(e.route.points2d);
      const a = graph.nodes.find(n => n.id === e.source), b = graph.nodes.find(n => n.id === e.target);
      assert.deepEqual(e.route.points2d[0], { x: a.x2, y: -a.y2, z: e.route.points2d[0].z });
      if (e.source !== e.target && e.type === "PREREQUISITE_OF" && arm(a) === arm(b) &&
          axes.filter(axis => axis !== arm(a)[0]).every(axis => Math.abs(a[axis] - b[axis]) < 1e-8)) {
        const axis = arm(a)[0], sign = Math.sign(a[axis] - topic[axis]);
        assert.ok(sign * (b[axis] - a[axis]) > 0, `spatial branch reverses ${ordering} ${e.source}->${e.target} ${JSON.stringify([a,b])}`);
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

test("Topic centres fill depth as well as both planar axes without colliding", async () => {
  const nodes = Array.from({ length: 24 }, (_, i) => n(`volume${i}`, "2020", `topic/${i}`));
  const graph = await spatialTemporalLayout(nodes, [], "content", elk, route);
  const topics = graph.nodes.filter(n => n.kind === "domain");
  for (const axis of ["x", "y", "z"]) assert.ok(new Set(topics.map(n => n[axis])).size > 1);
  assert.equal(new Set(graph.nodes.map(n => [n.x, n.y, n.z].join(":"))).size, graph.nodes.length);
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
