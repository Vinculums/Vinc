// 단위 테스트 — 플러그인 API 와 허브를 전부 모킹한다(라이브 게이트웨이·허브 불필요).
// 라이브 OpenClaw e2e 는 사람 게이트 뒤에 있다.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createVincCorpusSupplement, renderNode } from "./supplement.ts";
import entry, { register, DEFAULT_HUB_URL, type VincPluginApi } from "./index.ts";

test("search preserves temporal metadata and explicit temporal arguments", async () => {
  const temporal = { created_at: null, valid_from: null, valid_until: "2001", validity: "historical", superseded_by: "record:new" };
  const { supplement, hub } = makeSupplement(() => ({
    rows: [{ id: "synthetic#0", doc_id: "synthetic", snippet: "Original body", temporal }],
    node_hits: [{ id: "record:old", title: "Old", temporal }],
  }));
  const result = await supplement.search({ query: "Marker", mode: "historical", as_of: "2000", known_at: "2026" });
  assert.deepEqual(result[0].temporal, temporal);
  assert.deepEqual(result[1].temporal, temporal);
  assert.equal(result[0].snippet, "Original body");
  assert.equal(hub.calls[0].args.mode, "historical");
  assert.equal(hub.calls[0].args.as_of, "2000");
  assert.equal(hub.calls[0].args.known_at, "2026");
});

test("search reads the trimmed MCP default text: a slim temporal block, no page and an omitted note", async () => {
  // 허브의 MCP 기본 본문 그대로다(빈 칸·기본값·건강한 진단이 빠지고 omitted 한 줄이 붙는다).
  const { supplement } = makeSupplement(() => ({
    kind: "search", executed: true,
    rows: [
      { id: "doc.a#00000", score: 4.1, snippet: "chunk text", truncated: true, doc_id: "doc.a",
        temporal: { valid_from: "2026-01", updated_at: "2026-02-01T00:00:00Z" }, doc_title: "Doc A", cite_id: "concept:doc.doc.a" },
      { id: "doc.b#00001", score: 2.0, snippet: "undated", truncated: true, doc_id: "doc.b", doc_title: "Doc B",
        temporal: { updated_at: "2026-03-01T00:00:00Z" } },
      { id: "doc.c#00002", score: 1.0, snippet: "no block", truncated: true, doc_id: "doc.c", doc_title: "Doc C" },
    ],
    node_hits: [{ id: "concept:a", title: "A", kind: "concept", domain: "vinc/x", rrf_score: 0.03,
      temporal: { valid_from: "2026-01", updated_at: "2026-02-01T00:00:00Z" } }],
    arms: "fts+vector", total_count: 2, has_more: false, next_cursor: null, validity_filtered: 3,
    omitted: "Omitted: empty, default, derived, duplicate, diagnostic, created_at. …=cut title. No cite_id: id. No validity: unknown if interval_invalid or no valid_from/until, else current. raw:true=all",
  }));
  const out = await supplement.search({ query: "Marker" });
  assert.deepEqual(out.map((r) => [r.path, r.score]), [
    ["vinc://doc/doc.a", 4.1], ["vinc://doc/doc.b", 2.0], ["vinc://doc/doc.c", 1.0], ["vinc://node/concept:a", 0.03]]);
  assert.deepEqual(out[0].temporal, { valid_from: "2026-01", updated_at: "2026-02-01T00:00:00Z" });
  assert.deepEqual(out[1].temporal, { updated_at: "2026-03-01T00:00:00Z" });
  assert.equal("temporal" in out[2], false);
  assert.deepEqual(out[3].temporal, { valid_from: "2026-01", updated_at: "2026-02-01T00:00:00Z" });
  assert.equal(out[3].snippet, "A (concept, vinc/x)");
});

test("document get preserves temporal metadata without changing canonical content", async () => {
  const temporal = { created_at: null, validity: "unknown" };
  const { supplement } = makeSupplement(() => ({ text: "Original body", temporal }));
  const result = await supplement.get({ lookup: "vinc://doc/synthetic" });
  assert.equal(result?.content, "Original body");
  assert.deepEqual(result?.temporal, temporal);
});

// tools/call 요청을 받아 도구별 응답을 돌려주는 가짜 허브.
function hubFetch(handler: (name: string, args: Record<string, unknown>) => unknown) {
  const calls: Array<{ url: string; headers: Record<string, string>; name: string; args: Record<string, unknown> }> = [];
  const impl = (async (url: unknown, init?: RequestInit) => {
    const rpc = JSON.parse(String(init?.body));
    calls.push({
      url: String(url),
      headers: (init?.headers ?? {}) as Record<string, string>,
      name: rpc.params.name,
      args: rpc.params.arguments,
    });
    const payload = handler(rpc.params.name, rpc.params.arguments);
    return new Response(
      JSON.stringify({
        jsonrpc: "2.0",
        id: rpc.id,
        result: { content: [{ type: "text", text: JSON.stringify(payload) }] },
      }),
      { status: 200, headers: { "content-type": "application/json" } },
    );
  }) as typeof fetch;
  return { impl, calls };
}

function makeSupplement(handler: (name: string, args: Record<string, unknown>) => unknown) {
  const hub = hubFetch(handler);
  const warnings: string[] = [];
  const supplement = createVincCorpusSupplement({
    url: "https://hub.test/mcp",
    apiKey: "vinc_sk_test",
    fetchImpl: hub.impl,
    log: { warn: (m) => warnings.push(m) },
  });
  return { supplement, hub, warnings };
}

test("search: 문서 청크와 노드 히트를 corpus 결과로 매핑하고 인용을 단다", async () => {
  const { supplement, hub } = makeSupplement((name) => {
    assert.equal(name, "vinc_search");
    return {
      rows: [{ id: "d123#00002", doc_id: "d123", score: 3.2, snippet: "chunk text" }],
      node_hits: [
        { id: "concept:x", title: "X thing", kind: "concept", domain: "vinc/agent", rrf_score: 0.03 },
      ],
    };
  });
  const out = await supplement.search({ query: "openclaw", maxResults: 5 });
  assert.equal(out.length, 2);
  assert.deepEqual(out[0], {
    corpus: "vinc",
    path: "vinc://doc/d123",
    kind: "document",
    score: 3.2,
    snippet: "chunk text",
    id: "d123#00002",
    citation: "d123",
    provenanceLabel: "vinc:doc",
    sourceType: "vinc-doc",
  });
  assert.equal(out[1].path, "vinc://node/concept:x");
  assert.equal(out[1].citation, "concept:x");
  assert.equal(out[1].snippet, "X thing (concept, vinc/agent)");
  // 허브 호출 형태: 인증 헤더 + limit 전달.
  assert.equal(hub.calls[0].headers.authorization, "Bearer vinc_sk_test");
  assert.equal(hub.calls[0].args.limit, 5);
});

test("search: JSON-RPC 오류는 HTTP 200 으로 와도 빈 결과 + 경고로 강등된다", async () => {
  const impl = (async () =>
    new Response(
      JSON.stringify({ jsonrpc: "2.0", id: 1, error: { code: -32000, message: "bad key" } }),
      { status: 200, headers: { "content-type": "application/json" } },
    )) as typeof fetch;
  const warnings: string[] = [];
  const supplement = createVincCorpusSupplement({
    url: "https://hub.test/mcp",
    apiKey: "k",
    fetchImpl: impl,
    log: { warn: (m) => warnings.push(m) },
  });
  const out = await supplement.search({ query: "q" });
  assert.deepEqual(out, []);
  assert.equal(warnings.length, 1);
  assert.match(warnings[0], /bad key/);
});

test("search: SSE 응답(text/event-stream)도 해석한다", async () => {
  const payload = { rows: [], node_hits: [{ id: "record:r", title: "R", kind: "record", rrf_score: 1 }] };
  const rpc = JSON.stringify({
    jsonrpc: "2.0",
    id: 1,
    result: { content: [{ type: "text", text: JSON.stringify(payload) }] },
  });
  const impl = (async () =>
    new Response(`event: message\ndata: ${rpc}\n\n`, {
      status: 200,
      headers: { "content-type": "text/event-stream" },
    })) as typeof fetch;
  const supplement = createVincCorpusSupplement({ url: "u", apiKey: "k", fetchImpl: impl });
  const out = await supplement.search({ query: "q" });
  assert.equal(out.length, 1);
  assert.equal(out[0].id, "record:r");
});

test("get: 문서 lookup 은 vinc_doc_get 으로 가고 fromLine/lineCount 를 지킨다", async () => {
  const { supplement, hub } = makeSupplement((name) => {
    assert.equal(name, "vinc_doc_get");
    return { text: "l1\nl2\nl3\nl4", title: "Doc T", updated_at: "2026-08-29T00:00:00Z" };
  });
  const out = await supplement.get({ lookup: "vinc://doc/dabc", fromLine: 2, lineCount: 2 });
  assert.ok(out);
  assert.equal(hub.calls[0].args.doc_id, "dabc");
  assert.equal(out.content, "l2\nl3");
  assert.equal(out.fromLine, 2);
  assert.equal(out.lineCount, 2);
  assert.equal(out.title, "Doc T");
  assert.equal(out.updatedAt, "2026-08-29T00:00:00Z");
});

test("get: 노드 lookup 은 vinc_graph 로 가고, 근거 없는 관계는 unsourced 로 라벨된다", async () => {
  const { supplement } = makeSupplement((name, args) => {
    assert.equal(name, "vinc_graph");
    assert.equal(args.seed_id, "concept:x");
    return {
      found: true,
      nodes: [
        { id: "concept:x", label: "X", kind: "concept", domain: "vinc/agent", one_liner: "X is a thing.", ts: "2026-08-29T00:00:00Z" },
        { id: "concept:y", label: "Y", kind: "concept" },
        { id: "record:r", label: "R", kind: "record" },
      ],
      edges: [
        { source: "concept:x", target: "concept:y", type: "RELATES_TO", evidence: "X cites Y." },
        { source: "record:r", target: "concept:x", type: "ATTACHES_TO" },
      ],
    };
  });
  const out = await supplement.get({ lookup: "concept:x" });
  assert.ok(out);
  // 인용 게이트 시맨틱: 근거 있는 관계는 인용, 없는 관계는 거부 대신 라벨.
  assert.match(out.content, /RELATES_TO -> concept:y "Y" \[evidence: "X cites Y\."\]/);
  assert.match(out.content, /ATTACHES_TO <- record:r "R" \[unsourced\]/);
  assert.match(out.content, /citation: concept:x \(updated 2026-08-29T00:00:00Z\)/);
  assert.equal(out.kind, "concept");
});

test("get: 없는 노드는 null", async () => {
  const { supplement, warnings } = makeSupplement(() => ({ found: false }));
  const out = await supplement.get({ lookup: "concept:missing" });
  assert.equal(out, null);
  assert.deepEqual(warnings, []); // 부재는 오류가 아니다
});

test("renderNode: 시드만 있고 관계가 없어도 인용 줄은 남는다", () => {
  const text = renderNode("concept:solo", {
    nodes: [{ id: "concept:solo", label: "Solo", kind: "concept" }],
    edges: [],
  });
  assert.match(text, /^Solo \(concept\)/);
  assert.match(text, /citation: concept:solo/);
});

test("register: apiKey 가 있으면 supplement 를 등록한다", () => {
  let registered: unknown = null;
  const api: VincPluginApi = {
    pluginConfig: { apiKey: "vinc_sk_test" },
    registerMemoryCorpusSupplement: (s) => {
      registered = s;
    },
  };
  register(api);
  assert.ok(registered);
  assert.equal(typeof (registered as { search: unknown }).search, "function");
});

test("register: apiKey 가 없으면 등록하지 않고 경고한다", () => {
  const prev = process.env.VINC_REMOTE_MCP_TOKEN;
  delete process.env.VINC_REMOTE_MCP_TOKEN;
  try {
    const warnings: string[] = [];
    let registered = false;
    const api: VincPluginApi = {
      pluginConfig: {},
      logger: { warn: (m) => warnings.push(m) },
      registerMemoryCorpusSupplement: () => {
        registered = true;
      },
    };
    register(api);
    assert.equal(registered, false);
    assert.equal(warnings.length, 1);
  } finally {
    if (prev !== undefined) process.env.VINC_REMOTE_MCP_TOKEN = prev;
  }
});

test("entry: 플러그인 엔트리 모양(id/name/register)", () => {
  assert.equal(entry.id, "vinc");
  assert.equal(typeof entry.register, "function");
  assert.equal(DEFAULT_HUB_URL, "https://mcp.vincs.io/mcp");
});
