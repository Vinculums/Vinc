// Vinc corpus supplement: OpenClaw 의 memory_search/memory_get 이 Vinc 계정 그래프에
// 도달하게 하는 비배타 코퍼스 구현체. **읽기 전용이다** — 이 플러그인에는 쓰기 경로가
// 아예 없다. 커밋(쓰기)은 사람이 명시적으로 부르는 Vinc MCP 쓰기 표면에만 있고,
// 대화에서 사실을 자동 추출해 저장하는 경로는 만들지 않는다(확정 금지선).
// 경로 선택 근거: `decision:openclaw-integration-corpus-supplement` (계정 그래프)

// ── OpenClaw SDK 계약 (검증본 미러) ──────────────────────────────────────────
// 아래 타입은 openclaw/openclaw main `src/plugins/registry-contribution-types.ts` 의
// MemoryCorpusSupplement / MemoryCorpusSearchResult / MemoryCorpusGetResult 를 그대로
// 미러한 것이다. SDK 패키지를 의존성으로 끌지 않기 위해서다(엔진 오프라인 원칙과
// 같은 사유로, 이 플러그인의 테스트도 SDK 없이 돈다).
// 검증: 2026-08-29, docs.openclaw.ai /plugins/sdk-overview + 업스트림 소스 대조.
// 2026-08 마이그레이션이 제거한 것은 배타 슬롯 구 API(registerMemoryPromptSection 등
// 3종을 registerMemoryCapability 로 통합)와 메모리 전용 임베딩 레지스트리이고,
// additive 계약(registerMemoryCorpusSupplement)은 "not affected" 로 명시됐다.
// 검증 기록: `record:openclaw-sdk-corpus-supplement-verified-2026-08-29` (계정 그래프)

export type MemoryCorpusSearchResult = {
  corpus: string;
  path: string;
  title?: string;
  kind?: string;
  score: number;
  snippet: string;
  id?: string;
  startLine?: number;
  endLine?: number;
  citation?: string;
  source?: string;
  provenanceLabel?: string;
  sourceType?: string;
  sourcePath?: string;
  updatedAt?: string;
};

export type MemoryCorpusGetResult = {
  corpus: string;
  path: string;
  title?: string;
  kind?: string;
  content: string;
  fromLine: number;
  lineCount: number;
  id?: string;
  provenanceLabel?: string;
  sourceType?: string;
  sourcePath?: string;
  updatedAt?: string;
};

export type MemoryCorpusSupplement = {
  search(params: {
    query: string;
    maxResults?: number;
    agentId?: string;
    agentSessionKey?: string;
    sandboxed?: boolean;
  }): Promise<MemoryCorpusSearchResult[]>;
  get(params: {
    lookup: string;
    fromLine?: number;
    lineCount?: number;
    agentId?: string;
    agentSessionKey?: string;
    sandboxed?: boolean;
  }): Promise<MemoryCorpusGetResult | null>;
};

// ── 설정 ─────────────────────────────────────────────────────────────────────

export type VincSupplementOptions = {
  /** Vinc 허브 MCP 엔드포인트. */
  url: string;
  /** vinc_sk 키. ⚠ 스코프 키(W18) 출시 전에는 전권(읽기+쓰기) 키라는 점에 주의. */
  apiKey: string;
  /** search 기본 상한 (기본 8, 허브 상한 50). */
  maxResults?: number;
  /** HTTP 타임아웃 ms (기본 10000). */
  timeoutMs?: number;
  /** 테스트 주입용 fetch. */
  fetchImpl?: typeof fetch;
  log?: { warn(msg: string): void };
};

const CORPUS = "vinc";
const DOC_PREFIX = "vinc://doc/";
const NODE_PREFIX = "vinc://node/";
const HUB_SEARCH_LIMIT_MAX = 50;

// ── 허브 JSON-RPC 클라이언트 ─────────────────────────────────────────────────
// MCP tools/call 을 단발 HTTP POST 로 부른다. 주의 둘:
// 1) 허브의 JSON-RPC 오류는 HTTP 200 으로 온다 — body.error 를 반드시 읽는다.
// 2) streamable HTTP 서버는 단발 POST 에도 text/event-stream 으로 답할 수 있다.

type RpcBody = {
  error?: { code?: number; message?: string };
  result?: {
    isError?: boolean;
    structuredContent?: unknown;
    content?: Array<{ type?: string; text?: string }>;
  };
};

function parseSse(raw: string): RpcBody {
  // 이벤트 단위로 나눠 result 나 error 를 담은 첫 JSON-RPC 응답을 고른다.
  for (const event of raw.split(/\n\n+/)) {
    const data = event
      .split("\n")
      .filter((l) => l.startsWith("data:"))
      .map((l) => l.slice(5).trim())
      .join("");
    if (!data) continue;
    try {
      const parsed = JSON.parse(data) as RpcBody;
      if (parsed.result !== undefined || parsed.error !== undefined) return parsed;
    } catch {
      // 부분 이벤트는 건너뛴다
    }
  }
  throw new Error("vinc hub: no JSON-RPC response in event stream");
}

async function callTool(
  o: Required<Pick<VincSupplementOptions, "url" | "apiKey" | "timeoutMs" | "fetchImpl">>,
  name: string,
  args: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const res = await o.fetchImpl(o.url, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      accept: "application/json, text/event-stream",
      authorization: `Bearer ${o.apiKey}`,
    },
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: 1,
      method: "tools/call",
      params: { name, arguments: args },
    }),
    signal: AbortSignal.timeout(o.timeoutMs),
  });
  if (!res.ok) throw new Error(`vinc hub HTTP ${res.status}`);
  const ctype = res.headers.get("content-type") ?? "";
  const body: RpcBody = ctype.includes("text/event-stream")
    ? parseSse(await res.text())
    : ((await res.json()) as RpcBody);
  if (body.error) {
    throw new Error(`vinc hub RPC ${body.error.code ?? "?"}: ${body.error.message ?? ""}`);
  }
  const result = body.result ?? {};
  if (result.isError) {
    const text = (result.content ?? []).find((c) => c.type === "text")?.text ?? "unknown";
    throw new Error(`vinc tool ${name} failed: ${text}`);
  }
  if (result.structuredContent !== undefined) {
    return result.structuredContent as Record<string, unknown>;
  }
  const text = (result.content ?? []).find((c) => c.type === "text")?.text;
  return typeof text === "string" ? (JSON.parse(text) as Record<string, unknown>) : {};
}

// ── lookup 해석 ──────────────────────────────────────────────────────────────
// search 가 내보낸 path(vinc://doc/… · vinc://node/…)를 그대로 받는 것이 정상 경로.
// 맨몸 id 도 받는다: `kind:slug` 꼴이면 노드, 그 밖(해시형)은 문서로 본다.

function parseLookup(lookup: string): { doc?: string; node?: string } {
  if (lookup.startsWith(DOC_PREFIX)) return { doc: lookup.slice(DOC_PREFIX.length) };
  if (lookup.startsWith(NODE_PREFIX)) return { node: lookup.slice(NODE_PREFIX.length) };
  if (/^[a-z][a-z0-9_-]*:/.test(lookup)) return { node: lookup };
  return { doc: lookup };
}

function sliceLines(
  content: string,
  fromLine?: number,
  lineCount?: number,
): { content: string; fromLine: number; lineCount: number } {
  const all = content.split("\n");
  const from = Math.max(1, fromLine ?? 1);
  const rows = all.slice(from - 1, lineCount != null ? from - 1 + lineCount : undefined);
  return { content: rows.join("\n"), fromLine: from, lineCount: rows.length };
}

// ── 노드 렌더링 (인용 게이트 시맨틱) ─────────────────────────────────────────
// 근거(evidence)가 없는 관계는 **버리지 않고 "unsourced" 로 라벨**해 내보낸다.
// 현 그래프의 관계 근거 커버리지는 2.8% 라, 무근거 거부형 게이트는 관계 인용의
// 약 97% 를 차단한다 — 커버리지가 오를 때까지 라벨이 현실적인 게이트다.
// 근거: `record:openclaw-research-review-2026-08-29` finding 2 (계정 그래프)

type GraphNode = {
  id: string;
  label?: string;
  kind?: string;
  domain?: string;
  one_liner?: string;
  props?: Record<string, unknown>;
  tags?: string[];
  ts?: string;
};

type GraphEdge = {
  source: string;
  target: string;
  type: string;
  evidence?: string;
  confidence?: number;
};

export function renderNode(
  seedId: string,
  graph: { nodes?: GraphNode[]; edges?: GraphEdge[] },
): string {
  const nodes = new Map((graph.nodes ?? []).map((n) => [n.id, n]));
  const seed = nodes.get(seedId);
  const lines: string[] = [];
  const head = seed?.label ?? seedId;
  const meta = [seed?.kind, seed?.domain].filter(Boolean).join(", ");
  lines.push(meta ? `${head} (${meta})` : head);
  if (seed?.one_liner) lines.push(seed.one_liner);
  for (const [k, v] of Object.entries(seed?.props ?? {})) {
    lines.push(`${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`);
  }
  if (seed?.tags?.length) lines.push(`tags: ${seed.tags.join(", ")}`);
  const edges = graph.edges ?? [];
  if (edges.length) {
    lines.push("", "relations:");
    for (const e of edges) {
      const outbound = e.source === seedId;
      const otherId = outbound ? e.target : e.source;
      const other = nodes.get(otherId);
      const arrow = outbound ? "->" : "<-";
      const label = other?.label ? ` "${other.label}"` : "";
      const sourced = e.evidence ? `evidence: "${e.evidence}"` : "unsourced";
      lines.push(`- ${e.type} ${arrow} ${otherId}${label} [${sourced}]`);
    }
  }
  lines.push("", `citation: ${seedId}${seed?.ts ? ` (updated ${seed.ts})` : ""}`);
  return lines.join("\n");
}

// ── supplement 본체 ──────────────────────────────────────────────────────────

export function createVincCorpusSupplement(options: VincSupplementOptions): MemoryCorpusSupplement {
  const o = {
    url: options.url,
    apiKey: options.apiKey,
    timeoutMs: options.timeoutMs ?? 10_000,
    fetchImpl: options.fetchImpl ?? fetch,
  };
  const defaultLimit = options.maxResults ?? 8;
  const log = options.log;

  return {
    async search(input) {
      try {
        const limit = Math.min(input.maxResults ?? defaultLimit, HUB_SEARCH_LIMIT_MAX);
        const r = (await callTool(o, "vinc_search", { query: input.query, limit })) as {
          rows?: Array<{ id?: string; doc_id?: string; score?: number; snippet?: string }>;
          node_hits?: Array<{
            id?: string;
            title?: string;
            kind?: string;
            domain?: string;
            rrf_score?: number;
          }>;
        };
        const out: MemoryCorpusSearchResult[] = [];
        for (const row of r.rows ?? []) {
          if (!row?.doc_id) continue;
          out.push({
            corpus: CORPUS,
            path: DOC_PREFIX + row.doc_id,
            kind: "document",
            score: row.score ?? 0,
            snippet: row.snippet ?? "",
            id: row.id,
            citation: row.doc_id,
            provenanceLabel: "vinc:doc",
            sourceType: "vinc-doc",
          });
        }
        for (const hit of r.node_hits ?? []) {
          if (!hit?.id) continue;
          const meta = [hit.kind ?? "node", hit.domain].filter(Boolean).join(", ");
          out.push({
            corpus: CORPUS,
            path: NODE_PREFIX + hit.id,
            title: hit.title,
            kind: hit.kind,
            score: hit.rrf_score ?? 0,
            snippet: `${hit.title ?? hit.id} (${meta})`,
            id: hit.id,
            citation: hit.id,
            provenanceLabel: "vinc:node",
            sourceType: "vinc-node",
          });
        }
        return out.slice(0, limit);
      } catch (err) {
        // supplement 하나가 죽어도 내장 memory_search 는 살아야 한다 — 빈 결과 + 경고.
        log?.warn(`vinc corpus search failed: ${String(err)}`);
        return [];
      }
    },

    async get(input) {
      try {
        const { doc, node } = parseLookup(input.lookup);
        if (doc) {
          const r = (await callTool(o, "vinc_doc_get", { doc_id: doc })) as {
            text?: string;
            title?: string;
            updated_at?: string;
          };
          if (typeof r.text !== "string") return null;
          return {
            corpus: CORPUS,
            path: DOC_PREFIX + doc,
            title: r.title,
            kind: "document",
            id: doc,
            updatedAt: r.updated_at,
            provenanceLabel: "vinc:doc",
            sourceType: "vinc-doc",
            ...sliceLines(r.text, input.fromLine, input.lineCount),
          };
        }
        const seedId = node as string;
        const r = (await callTool(o, "vinc_graph", { seed_id: seedId, depth: 1 })) as {
          found?: boolean;
          nodes?: GraphNode[];
          edges?: GraphEdge[];
        };
        if (!r.found) return null;
        const seed = (r.nodes ?? []).find((n) => n.id === seedId);
        return {
          corpus: CORPUS,
          path: NODE_PREFIX + seedId,
          title: seed?.label,
          kind: seed?.kind,
          id: seedId,
          updatedAt: seed?.ts,
          provenanceLabel: "vinc:node",
          sourceType: "vinc-node",
          ...sliceLines(renderNode(seedId, r), input.fromLine, input.lineCount),
        };
      } catch (err) {
        log?.warn(`vinc corpus get failed: ${String(err)}`);
        return null;
      }
    },
  };
}
