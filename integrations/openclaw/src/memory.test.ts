// memory 슬롯 단위 테스트 — 플러그인 API 와 허브를 모킹한다. 라이브 게이트웨이 e2e 는 사람 단계다.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { hubConnection } from "./supplement.ts";
import {
  PROVIDER_ID,
  TOOL_NAMES,
  buildVincMemoryPrompt,
  createVincMemoryCapability,
  createVincMemoryTools,
  recall,
  type MemoryCallerAuthority,
} from "./memory.ts";
import { register, ownsMemorySlot, type VincPluginApi } from "./index.ts";

/** 슬롯이 부를 수 있는 허브 도구. 쓰기 도구는 하나도 없다. */
const READ_TOOLS = new Set(["vinc_search", "vinc_brief", "vinc_doc_get", "vinc_graph", "vinc_whoami"]);

function hub(handler: (name: string, args: Record<string, unknown>) => unknown) {
  const calls: Array<{ name: string; args: Record<string, unknown> }> = [];
  const fetchImpl = (async (_url: unknown, init?: RequestInit) => {
    const rpc = JSON.parse(String(init?.body));
    calls.push({ name: rpc.params.name, args: rpc.params.arguments });
    let payload: unknown;
    try {
      payload = handler(rpc.params.name, rpc.params.arguments);
    } catch (err) {
      return new Response(
        JSON.stringify({ jsonrpc: "2.0", id: rpc.id, error: { code: -32000, message: String(err) } }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    }
    return new Response(
      JSON.stringify({ jsonrpc: "2.0", id: rpc.id, result: { content: [{ type: "text", text: JSON.stringify(payload) }] } }),
      { status: 200, headers: { "content-type": "application/json" } },
    );
  }) as typeof fetch;
  const o = hubConnection({ url: "https://hub.test/mcp", apiKey: "vinc_ro_test", fetchImpl });
  return { o, calls };
}

const BRIEF = {
  status: "resolved",
  node: { id: "concept:release-train", cite_id: "concept:release-train", title: "Release train", kind: "concept" },
  one_liner: "Releases leave every second Tuesday.",
  quotes: [{ text: "The train leaves on Tuesday.", doc_id: "vinc.ops.release" }],
};
const SEARCH = {
  rows: [{ id: "vinc.ops.release#0", doc_id: "vinc.ops.release", score: 0.8, snippet: "The train leaves on Tuesday." }],
  node_hits: [
    { id: "concept:release-train", title: "Release train", kind: "concept", rrf_score: 0.5 },
    { id: "record:missed-train", title: "Missed train", kind: "record", rrf_score: 0.3 },
  ],
};

function handler(name: string): unknown {
  if (name === "vinc_brief") return BRIEF;
  if (name === "vinc_search") return SEARCH;
  if (name === "vinc_doc_get") return { text: "line one\nline two\nline three", title: "Release" };
  if (name === "vinc_graph") return { found: false };
  if (name === "vinc_whoami") return { owner: "user:x" };
  throw new Error(`unexpected tool ${name}`);
}

const owner: MemoryCallerAuthority = {
  kind: "session",
  sessionKey: "s1",
  sandboxed: false,
  audience: { kind: "owner-private", agentId: "main" },
};

function open(o: ReturnType<typeof hub>["o"], authority: MemoryCallerAuthority, agentId = "main") {
  const capability = createVincMemoryCapability(o);
  return capability.providerRuntime.open({ agentId, context: { authority, assertCurrent() {} } });
}

test("recall puts the brief node first and drops its duplicate from search", async () => {
  const { o } = hub(handler);
  const hits = await recall(o, "when do releases leave", 8);
  assert.deepEqual(
    hits.map((h) => [h.path, h.via]),
    [
      ["vinc://node/concept:release-train", "brief"],
      ["vinc://doc/vinc.ops.release", "search"],
      ["vinc://node/record:missed-train", "search"],
    ],
  );
  assert.equal(hits[0].score, undefined);
  assert.match(hits[0].snippet, /every second Tuesday/);
  assert.match(hits[0].snippet, /"The train leaves on Tuesday\." \(vinc\.ops\.release\)/);
});

test("recall reads a trimmed brief: a node without cite_id cites its id, and a slim temporal block passes through", async () => {
  const slim = {
    kind: "brief", executed: true, status: "resolved",
    node: { id: "record:train-moved", title: "The train moved to Wednesday", kind: "record",
      validity: "current", temporal: { valid_from: "2026-10-01", updated_at: "2026-10-02T00:00:00Z" } },
    one_liner: "Releases leave on Wednesday.",
    quotes: [{ text: "The train leaves on Wednesday.", doc_id: "vinc.ops.release" }],
    grounds: ["record:train-moved"],
    omitted: "Omitted: empty, default, derived, duplicate, diagnostic, created_at. …=cut title. No cite_id: id. No validity: unknown if interval_invalid or no valid_from/until, else current. raw:true=all",
  };
  const { o } = hub((name) => (name === "vinc_brief" ? slim : { rows: [], node_hits: [] }));
  const [first] = await recall(o, "when do releases leave", 8);
  assert.equal(first.path, "vinc://node/record:train-moved");
  assert.equal(first.citation, "record:train-moved");
  assert.deepEqual(first.temporal, { valid_from: "2026-10-01", updated_at: "2026-10-02T00:00:00Z" });
  assert.match(first.snippet, /Wednesday/);
});

test("recall survives a failed brief but not a failed search", async () => {
  const warned: string[] = [];
  const briefDown = hub((name) => {
    if (name === "vinc_brief") throw new Error("brief down");
    return handler(name);
  });
  const hits = await recall(briefDown.o, "q", 8, { warn: (m) => warned.push(m) });
  assert.equal(hits[0].via, "search");
  assert.match(warned[0], /brief failed/);
  const searchDown = hub((name) => {
    if (name === "vinc_search") throw new Error("search down");
    return handler(name);
  });
  await assert.rejects(recall(searchDown.o, "q", 8), /search down/);
});

test("the slot reads for the owner's private session and refuses other audiences", async () => {
  const { o } = hub(handler);
  const { provider } = await open(o, owner);
  const page = await provider!.search({ query: "release", maxResults: 2 });
  assert.equal(page.hits.length, 2);
  assert.deepEqual(page.hits[0].reference, { providerId: PROVIDER_ID, id: "vinc://node/concept:release-train" });
  assert.equal(page.hits[0].citations?.[0].label, "concept:release-train");

  const group: MemoryCallerAuthority = {
    kind: "session",
    sessionKey: "g1",
    sandboxed: false,
    audience: { kind: "conversation", agentId: "main", sessionKey: "g1", sessionId: "x" },
  };
  const denied = (await open(o, group)).provider!;
  await assert.rejects(denied.search({ query: "release" }), /readable only/);
  await assert.rejects(denied.get({ reference: { providerId: PROVIDER_ID, id: "vinc://doc/a" } }), /readable only/);
  const otherAgent = (await open(o, owner, "helper")).provider!;
  await assert.rejects(otherAgent.search({ query: "release" }), /readable only/);

  const host = (await open(o, { kind: "host", operation: "status" })).provider!;
  assert.deepEqual(await host.health(), { status: "ready" });
  await assert.rejects(host.search({ query: "release" }), /readable only/);

  const operator = (await open(o, { kind: "operator", scopes: [] })).provider!;
  assert.ok((await operator.search({ query: "release" })).hits.length > 0);
});

test("a score floor drops the unscored brief hit; get honours the provider and the lines", async () => {
  const { o } = hub(handler);
  const { provider } = await open(o, owner);
  const floored = await provider!.search({ query: "release", minScore: 0.4 });
  assert.deepEqual(
    floored.hits.map((h) => h.reference.id),
    ["vinc://doc/vinc.ops.release"],
  );
  assert.deepEqual(await provider!.get({ reference: { providerId: "other", id: "vinc://doc/x" } }), { status: "not_found" });
  const got = await provider!.get({ reference: { providerId: PROVIDER_ID, id: "vinc://doc/vinc.ops.release" }, from: 2, lines: 1 });
  assert.equal(got.status, "ok");
  assert.equal(got.status === "ok" && got.text, "line two");
  assert.deepEqual(await provider!.get({ reference: { providerId: PROVIDER_ID, id: "concept:gone" } }), { status: "not_found" });
});

test("health reports an unreachable hub without content", async () => {
  const down = hub(() => {
    throw new Error("401 bad key");
  });
  const { provider } = await open(down.o, { kind: "host", operation: "status" });
  const health = await provider!.health();
  assert.equal(health.status, "unavailable");
  assert.doesNotMatch(health.message ?? "", /401/);
});

test("the flush writes nothing and no flow reaches a write tool", async () => {
  const { o, calls } = hub(handler);
  const capability = createVincMemoryCapability(o);
  assert.equal(capability.providerFlushPlanResolver(), null);
  assert.deepEqual(capability.recallToolNames, ["memory_search", "memory_get"]);
  const { provider } = await capability.providerRuntime.open({ agentId: "main", context: { authority: owner, assertCurrent() {} } });
  await provider!.search({ query: "release" });
  await provider!.get({ reference: { providerId: PROVIDER_ID, id: "vinc://doc/vinc.ops.release" } });
  await provider!.health();
  const [search, get] = createVincMemoryTools(o);
  await search.execute("t1", { query: "release" });
  await get.execute("t2", { path: "concept:gone" });
  assert.ok(calls.length >= 6);
  for (const call of calls) assert.ok(READ_TOOLS.has(call.name), `slot called ${call.name}`);
});

test("the tools render citations and refuse empty input", async () => {
  const { o } = hub(handler);
  const [search, get] = createVincMemoryTools(o, { maxResults: 2 });
  assert.deepEqual([search.name, get.name], [...TOOL_NAMES]);
  const found = await search.execute("t1", { query: "release" });
  assert.match(found.content[0].text, /^1\. Release train \[vinc:\/\/node\/concept:release-train\] best match \(brief\)/);
  assert.match(found.content[0].text, /citation: vinc\.ops\.release/);
  assert.equal((found.details as { hits: unknown[] }).hits.length, 2);
  await assert.rejects(search.execute("t2", { query: "  " }), /needs a query/);
  const missing = await get.execute("t3", { path: "concept:gone" });
  assert.match(missing.content[0].text, /^Not found in Vinc/);
  const doc = await get.execute("t4", { path: "vinc://doc/vinc.ops.release", from: 3 });
  assert.equal(doc.content[0].text, "line three");
});

test("the prompt section appears only with the tools and says nothing is saved on its own", () => {
  assert.deepEqual(buildVincMemoryPrompt({ availableTools: new Set() }), []);
  const lines = buildVincMemoryPrompt({ availableTools: new Set(["memory_search"]) });
  assert.equal(lines[0], "## Memory (Vinc)");
  assert.ok(lines.some((l) => /not instructions/.test(l)));
  assert.ok(lines.some((l) => /Nothing from this conversation is saved/.test(l)));
});

function fakeApi(config: unknown, agents?: string[]) {
  const got = { supplements: 0, capabilities: [] as unknown[], tools: [] as Array<string[] | undefined>,
    factories: [] as Array<(ctx: unknown) => unknown> };
  const api: VincPluginApi = {
    config,
    pluginConfig: { apiKey: "vinc_ro_test", ...(agents ? { agents } : {}) },
    registerMemoryCorpusSupplement: () => {
      got.supplements += 1;
    },
    registerMemoryCapability: (c) => {
      got.capabilities.push(c);
    },
    registerTool: (factory, opts) => {
      got.tools.push(opts?.names);
      got.factories.push(factory);
    },
  };
  return { api, got };
}

test("the slot registers only when the gateway gives Vinc the memory slot", () => {
  const unselected = fakeApi({ plugins: { slots: { memory: "memory-core" } } });
  register(unselected.api);
  assert.deepEqual(unselected.got, { supplements: 1, capabilities: [], tools: [], factories: [] });

  const selected = fakeApi({ plugins: { slots: { memory: "vinc" } } });
  register(selected.api);
  assert.equal(selected.got.supplements, 1);
  assert.equal(selected.got.capabilities.length, 1);
  assert.deepEqual(selected.got.tools, [["memory_search", "memory_get"]]);

  const older = fakeApi({ plugins: { slots: { memory: "vinc" } } });
  delete older.api.registerMemoryCapability;
  delete older.api.registerTool;
  register(older.api);
  assert.equal(older.got.supplements, 1);
  assert.equal(ownsMemorySlot(undefined), false);
});

test("the manifest declares the memory kind, the tools and the config the code reads", () => {
  const root = new URL("..", import.meta.url);
  const manifest = JSON.parse(readFileSync(new URL("openclaw.plugin.json", root), "utf8"));
  assert.equal(manifest.id, "vinc");
  assert.equal(manifest.kind, "memory");
  assert.deepEqual(manifest.contracts.tools, [...TOOL_NAMES]);
  assert.deepEqual(Object.keys(manifest.configSchema.properties).sort(), ["agents", "apiKey", "maxResults", "timeoutMs", "url"]);
  assert.deepEqual(manifest.configContracts.secretInputs.paths, [{ path: "apiKey", expected: "string" }]);
  const pkg = JSON.parse(readFileSync(new URL("package.json", root), "utf8"));
  for (const entry of pkg.openclaw.extensions) assert.ok(existsSync(new URL(entry, root)), entry);
  for (const file of pkg.files) assert.ok(existsSync(new URL(file, root)), file);
});

test("the agent tools go only to the agents the config names", () => {
  const listed = fakeApi({ plugins: { slots: { memory: "vinc" } } }, ["me"]);
  register(listed.api);
  const factory = listed.got.factories[0];
  const tools = factory({ agentId: "me", sessionKey: "s" }) as Array<{ name: string }>;
  assert.deepEqual(tools.map((t) => t.name), ["memory_search", "memory_get"]);
  assert.equal(factory({ agentId: "group-helper", sessionKey: "g" }), null);
  assert.equal(factory({}), null);
  assert.equal(factory(undefined), null);

  const warned: string[] = [];
  const unlisted = fakeApi({ plugins: { slots: { memory: "vinc" } } });
  unlisted.api.logger = { warn: (m) => warned.push(m) };
  register(unlisted.api);
  assert.equal(unlisted.got.factories[0]({ agentId: "me" }), null);
  assert.ok(warned.some((m) => /config\.agents/.test(m)));
});
