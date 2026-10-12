import test from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import ELK from "elkjs/lib/elk.bundled.js";
import { AvoidLib } from "libavoid-js";

const source = async (entry) => {
  const result = await build({ entryPoints: [fileURLToPath(new URL(entry, import.meta.url))],
    bundle: true, platform: "node", format: "esm", write: false });
  return import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString("base64")}`);
};
const { createLayoutService, validRequest } = await source("../src/layout-service.ts");
const { applyLayoutAnswer, slimNodes, slimEdges } = await source("../src/view-layout-wire.ts");
const { temporalLayout } = await source("../src/view-layout.ts");
const { routeAcrossTopics } = await source("../src/view-layout-avoid.ts");
await AvoidLib.load(fileURLToPath(new URL("../node_modules/libavoid-js/dist/libavoid.wasm", import.meta.url)));
const router = (ns, es, batching) => Promise.resolve(es.length ? routeAcrossTopics(AvoidLib.getInstance(), ns, es, batching) : []);

/** Three topics with chains of dated nodes, a repeated relation and two relations across topics. */
function graph(extra = 0) {
  const nodes = [], edges = [];
  for (const [t, size] of [["vinc/a", 9 + extra], ["vinc/b", 7], ["vinc/c", 5]]) {
    for (let i = 0; i < size; i++) {
      nodes.push({ id: `${t}/${i}`, label: `${t} ${i}`, kind: "concept", domain: t, degree: 1,
        ts: `2026-0${1 + (i % 9)}-1${i % 10}T00:00:00Z`, props: { secret: "never sent" } });
      if (i) edges.push({ source: `${t}/${i - 1}`, target: `${t}/${i}`, type: "derives_from" });
    }
  }
  edges.push({ source: "vinc/a/0", target: "vinc/b/0", type: "relates" }, { source: "vinc/b/3", target: "vinc/c/1", type: "relates" },
    { source: "vinc/a/0", target: "vinc/a/1", type: "derives_from" });
  return { nodes, edges };
}
const request = (g, space = "space-1") => ({ space, basis: "recorded", nodes: slimNodes(g.nodes), edges: slimEdges(g.edges) });

test("a stored answer puts the same layout on the read, however the read orders its relations", async () => {
  const g = graph(), elk = new ELK();
  const service = createLayoutService(elk, router);
  const { answer } = await service.layout(request(g));
  assert.ok(!JSON.stringify(answer).includes("never sent"), "the service never sees props or labels");
  const direct = await temporalLayout(g.nodes, g.edges, "recorded", elk, router, "temporal", 1.6);
  const shuffled = [...g.edges].reverse();
  const applied = applyLayoutAnswer(g.nodes, shuffled, answer);
  assert.ok(applied);
  const at = new Map(direct.nodes.map(n => [n.id, n]));
  for (const n of applied.nodes) for (const k of ["x", "y", "z", "x2", "y2"]) assert.equal(n[k], at.get(n.id)[k], `${n.id}.${k}`);
  assert.equal(applied.nodes.find(n => n.id === "vinc/a/0").label, "vinc/a 0", "the read's own fields stay");
  // Repeated relations keep their order among themselves, so the reversed read still matches by key.
  const routeOf = new Map();
  direct.edges.forEach(e => { const k = `${e.source}|${e.target}|${e.type}`; routeOf.set(k, [...(routeOf.get(k) || []), e.route]); });
  const seen = new Map();
  for (const e of [...applied.edges].reverse()) {
    const k = `${e.source}|${e.target}|${e.type}`, i = seen.get(k) || 0; seen.set(k, i + 1);
    assert.deepEqual(e.route, routeOf.get(k)[i], k);
  }
  assert.deepEqual(applied.topicEdges, direct.topicEdges);
});

test("after a write only the changed topic is laid out again and the other boxes stay put", async () => {
  const service = createLayoutService(new ELK(), router);
  const first = await service.layout(request(graph()));
  assert.equal(first.stats.reusedPlacement, false);
  assert.ok(first.stats.branchesLaidOut > 0);
  const same = await service.layout(request(graph()));
  assert.equal(same.stats.branchesLaidOut, 0);
  assert.deepEqual(same.answer, first.answer);
  const grown = await service.layout(request(graph(1)));
  assert.equal(grown.stats.reusedPlacement, true);
  assert.ok(grown.stats.branchesLaidOut > 0 && grown.stats.branchesLaidOut < first.stats.branchesLaidOut);
  // The 2D placement is what stays put. The 3D arrangement is composed again from all topics each time.
  const before = new Map(first.answer.nodes.map(([id, , , , x2, y2]) => [id, [x2, y2]]));
  for (const [id, , , , x2, y2] of grown.answer.nodes) if (!id.startsWith("vinc/a/")) assert.deepEqual([x2, y2], before.get(id), id);
  // Another Space never reuses this one's pieces.
  const other = await service.layout(request(graph(), "space-2"));
  assert.equal(other.stats.reusedPlacement, false);
  assert.equal(service.health().spaces, 2);
});

test("an answer that does not cover the read is refused, and malformed requests are rejected", async () => {
  const g = graph(), service = createLayoutService(new ELK(), router);
  const { answer } = await service.layout(request(g));
  assert.equal(applyLayoutAnswer([...g.nodes, { id: "new", label: "", kind: "concept", degree: 0 }], g.edges, answer), null);
  assert.equal(applyLayoutAnswer(g.nodes, [...g.edges, { source: "vinc/a/0", target: "vinc/c/0", type: "relates" }], answer), null);
  assert.ok(validRequest(request(g)));
  for (const bad of [null, {}, { ...request(g), basis: "other" }, { ...request(g), space: "" },
    { ...request(g), nodes: [{ id: 1, kind: "concept" }] }, { ...request(g), edges: [{ source: "a" }] }])
    assert.equal(validRequest(bad), false);
});

test("requests run one at a time and a failed one does not stop the next", async () => {
  let active = 0, most = 0, fail = true;
  const elk = new ELK();
  const engine = { layout: async input => {
    active++; most = Math.max(most, active);
    try { await new Promise(r => setTimeout(r, 2)); if (fail) { fail = false; throw new Error("boom"); } return await elk.layout(input); }
    finally { active--; }
  } };
  const service = createLayoutService(engine, router);
  const results = await Promise.allSettled([service.layout(request(graph())), service.layout(request(graph(), "b")), service.layout(request(graph(), "c"))]);
  assert.equal(results[0].status, "rejected");
  assert.equal(results[1].status, "fulfilled");
  assert.equal(results[2].status, "fulfilled");
  assert.equal(most, 1);
});

test("the bundled server asks for the token and answers a layout", async () => {
  const out = fs.mkdtempSync(path.join(os.tmpdir(), "vinc-layout-"));
  await new Promise((resolve, reject) => {
    const p = spawn(process.execPath, [fileURLToPath(new URL("../service/build.mjs", import.meta.url)), out], { stdio: "ignore" });
    p.on("exit", code => code === 0 ? resolve() : reject(new Error(`build exited ${code}`)));
  });
  const port = 18000 + Math.floor(Math.random() * 2000);
  const server = spawn(process.execPath, [path.join(out, "layout-server.mjs")], { env: { ...process.env, VINC_LAYOUT_TOKEN: "t0ken", PORT: String(port) }, stdio: ["ignore", "pipe", "inherit"] });
  try {
    await new Promise((resolve, reject) => {
      server.stdout.on("data", d => { if (String(d).includes("listening")) resolve(); });
      server.on("exit", code => reject(new Error(`server exited ${code}`)));
    });
    const url = `http://127.0.0.1:${port}`, body = JSON.stringify(request(graph()));
    assert.equal((await fetch(`${url}/health`)).status, 200);
    assert.equal((await fetch(`${url}/v1/layout`, { method: "POST", body })).status, 401);
    assert.equal((await fetch(`${url}/v1/layout`, { method: "POST", body, headers: { authorization: "Bearer wrong" } })).status, 401);
    assert.equal((await fetch(`${url}/v1/layout`, { method: "POST", body: "{}", headers: { authorization: "Bearer t0ken" } })).status, 400);
    const ok = await fetch(`${url}/v1/layout`, { method: "POST", body, headers: { authorization: "Bearer t0ken" } });
    assert.equal(ok.status, 200);
    const { answer } = await ok.json();
    assert.ok(applyLayoutAnswer(graph().nodes, graph().edges, answer));
  } finally { server.kill(); fs.rmSync(out, { recursive: true, force: true }); }
});
