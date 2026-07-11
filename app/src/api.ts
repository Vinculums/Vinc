/** Sidecar client — data plane is localhost HTTP + session token (control will
 *  be Tauri IPC when the shell lands, P4). Graph/query logic never lives here. */

const BASE_KEY = "vinc.sidecar.base";
const TOKEN_KEY = "vinc.sidecar.token";
const OWNER_KEY = "vinc.owner";

export const cfg = {
  // 기본 = same-origin(vite dev proxy 경유). Tauri 셸(P4)에서는 사이드카 주소 직접 지정.
  get base(): string { return localStorage.getItem(BASE_KEY) || location.origin; },
  set base(v: string) { localStorage.setItem(BASE_KEY, v); },
  get token(): string { return localStorage.getItem(TOKEN_KEY) || ""; },
  set token(v: string) { localStorage.setItem(TOKEN_KEY, v); },
  get owner(): string { return localStorage.getItem(OWNER_KEY) || "tenant:local"; },
  set owner(v: string) { localStorage.setItem(OWNER_KEY, v); },
};

async function call(path: string, params?: Record<string, string | number | undefined>,
                    body?: unknown): Promise<any> {
  const url = new URL(path, cfg.base);
  for (const [k, v] of Object.entries(params || {}))
    if (v !== undefined && v !== "") url.searchParams.set(k, String(v));
  const res = await fetch(url, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      ...(cfg.token ? { Authorization: `Bearer ${cfg.token}` } : {}),
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

export type GraphNode = { id: string; label: string; domain: string; degree: number; x?: number; y?: number };
export type GraphEdge = { source: string; target: string; type: string; cross_domain: boolean };

export const api = {
  health: () => call("/health"),
  atlasL0: (owner: string) => call("/atlas", { owner }),
  graph: (owner: string, opts: { domain?: string; seed?: string; depth?: number; layout?: string } = {}) =>
    call("/graph", { owner, ...opts }),
  recall: (owner: string, limit = 50) => call("/recall", { owner, limit, rank: "preference" }),
  search: (query: string, top_k = 20) => call("/search", { query, top_k }),
  coupling: (owner: string, a: string, b: string) => call("/coupling", { owner, a, b }),
  domains: (owner: string) => call("/domains", { owner }),
  activate: (owner: string, opts: Record<string, unknown> = {}) =>
    call("/activate", undefined, { owner, ...opts }),
  share: (owner: string, opts: { domain?: string; seed?: string; depth?: number; ids?: string } = {}) =>
    call("/share", { owner, ...opts }),
  promoteCandidates: (owner: string) => call("/promote-candidates", { owner }),
  suggestTies: (owner: string, node: string) => call("/suggest-ties", { owner, node }),
  // fragment MERGE(D11 §5) — Bind 확정도 이 단일 쓰기 표면을 그대로 쓴다(D32)
  ingestFragment: (fragment: unknown) => call("/ingest/fragment", undefined, fragment),
  importPack: (pack: unknown, owner: string) =>
    call("/ingest/pack", undefined, { pack, owner }),
  backup: (out: string) => call("/backup", undefined, { out }),
  pin: (episode: string) => call("/pin", undefined, { episode }),
};
