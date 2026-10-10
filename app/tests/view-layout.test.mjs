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
const { temporalLayout } = await source("../src/view-layout.ts");
const { routeAcrossTopics } = await source("../src/view-layout-avoid.ts");
const { layoutInWorker } = await source("../src/view-layout-client.ts");
const { movedRoute, routeLength, pointOnRoute } = await source("../src/view-route.ts");
const { RoutedLinkObject } = await source("../src/surfaces/view-routes3d.ts");
await AvoidLib.load(fileURLToPath(new URL("../node_modules/libavoid-js/dist/libavoid.wasm", import.meta.url)));
const elk = new ELK();
const route = (ns, es) => Promise.resolve(routeAcrossTopics(AvoidLib.getInstance(), ns, es));
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
    const worker = { terminate: () => terminated++, postMessage: () => {} };
    const controller = new AbortController();
    const job = layoutInWorker([n("a", "")], [], "content", controller.signal, () => worker);
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
