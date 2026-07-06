// 데이터 평면(HTTP) — 사이드카(api/server.py)를 localhost + Bearer 토큰으로 호출.
// UI 는 그래프/질의 로직을 담지 않는다 — 엔드포인트는 CLI 1:1([[vinc.arch]] §1).
//
// 라우트(server.py):
//   GET  /health /recall /domains /graph /search /decide /learner /jobs/{id}
//   POST /pin /tag /touch /activate /backup /ingest/{engine}
//
// 데모 모드(port=0, token='demo')에선 목 데이터를 낸다 — 사이드카 없이 표면 검토용.

import { isTauri, type SidecarInfo } from "./ipc";

export type GraphNode = { id: string; label: string; domain?: string; state?: "full" | "half" | "empty" };
export type GraphEdge = { source: string; target: string; kind?: string };
export type GraphData = { nodes: GraphNode[]; edges: GraphEdge[] };
export type RecallRecord = { id: string; title: string; domain?: string; ts?: string; pinned?: boolean; tags?: string[] };
export type DomainStat = { domain: string; count: number };
export type SearchHit = { id: string; title: string; score: number; snippet?: string };
export type Job = { id: string; state: string; result?: unknown; error?: string };

export class SidecarClient {
  constructor(private info: SidecarInfo) {}

  private base() {
    return `http://${this.info.host}:${this.info.port}`;
  }
  private headers() {
    return {
      "Content-Type": "application/json",
      Authorization: `Bearer ${this.info.token}`,
    };
  }

  private get demo() {
    return !isTauri || this.info.port === 0 || this.info.token === "demo";
  }

  private async get<T>(path: string, params?: Record<string, string | number | undefined>): Promise<T> {
    const qs = params
      ? "?" +
        Object.entries(params)
          .filter(([, v]) => v !== undefined && v !== "")
          .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
          .join("&")
      : "";
    const res = await fetch(`${this.base()}${path}${qs}`, { headers: this.headers() });
    if (!res.ok) throw new Error(`GET ${path} → ${res.status} ${await res.text()}`);
    return res.json() as Promise<T>;
  }

  private async post<T>(path: string, body: unknown): Promise<T> {
    const res = await fetch(`${this.base()}${path}`, {
      method: "POST",
      headers: this.headers(),
      body: JSON.stringify(body ?? {}),
    });
    if (!res.ok) throw new Error(`POST ${path} → ${res.status} ${await res.text()}`);
    return res.json() as Promise<T>;
  }

  // ── 조회 ────────────────────────────────────────────────
  async recall(query: string, owner?: string): Promise<RecallRecord[]> {
    if (this.demo) return DEMO.recall(query);
    const r = await this.get<{ episodes?: RecallRecord[]; results?: RecallRecord[] }>("/recall", { query, owner });
    return r.episodes ?? r.results ?? [];
  }

  async domains(owner?: string): Promise<DomainStat[]> {
    if (this.demo) return DEMO.domains();
    const r = await this.get<{ domains?: DomainStat[] }>("/domains", { owner });
    return r.domains ?? [];
  }

  async graph(owner: string, domain?: string): Promise<GraphData> {
    if (this.demo) return DEMO.graph(domain);
    const r = await this.get<GraphData>("/graph", { owner, domain });
    return { nodes: r.nodes ?? [], edges: r.edges ?? [] };
  }

  async search(query: string, topK = 5): Promise<SearchHit[]> {
    if (this.demo) return DEMO.search(query, topK);
    const r = await this.get<{ hits?: SearchHit[]; results?: SearchHit[] }>("/search", { query, top_k: topK });
    return r.hits ?? r.results ?? [];
  }

  async decide(topic: string, depth = 2): Promise<GraphData> {
    if (this.demo) return DEMO.graph(topic);
    return this.get<GraphData>("/decide", { topic, depth });
  }

  // ── 쓰기(선호 신호) ─────────────────────────────────────
  async pin(episode: string, off = false): Promise<unknown> {
    if (this.demo) return { episode, pinned: !off };
    return this.post("/pin", { episode, off });
  }
  async tag(episode: string, tags: string[]): Promise<unknown> {
    if (this.demo) return { episode, tags };
    return this.post("/tag", { episode, tags });
  }

  // ── 팩 내보내기 ─────────────────────────────────────────
  async activate(owner: string, query = "", out?: string): Promise<{ pack?: string; count?: number }> {
    if (this.demo) return { pack: "packs/vinc-context.pack.json", count: 42 };
    return this.post("/activate", { owner, query, out });
  }

  // ── 적재(비동기 잡 202 → job_id 폴링) ───────────────────
  async ingest(engine: string, body: unknown): Promise<Job> {
    if (this.demo) return { id: "demo-job", state: "done", result: { engine, ingested: 12 } };
    return this.post<Job>(`/ingest/${engine}`, body);
  }
  async job(id: string): Promise<Job> {
    if (this.demo) return { id, state: "done" };
    return this.get<Job>(`/jobs/${id}`);
  }

  async health(): Promise<boolean> {
    if (this.demo) return true;
    try {
      const r = await fetch(`${this.base()}/health`);
      return r.ok;
    } catch {
      return false;
    }
  }
}

// ── 데모 데이터(사이드카 부재 시 표면 검토용) ──────────────────────────────
const DEMO = {
  recall(query: string): RecallRecord[] {
    const base: RecallRecord[] = [
      { id: "ep-1", title: "환불 정책 v0.4 개정 노트", domain: "billing", ts: "2026-06-30", pinned: true, tags: ["정책"] },
      { id: "ep-2", title: "온보딩 플로우 사용자 인터뷰 요약", domain: "onboarding", ts: "2026-06-28" },
      { id: "ep-3", title: "그래프 마이닝 성능 벤치(kuzu 0.11)", domain: "engine", ts: "2026-06-25", tags: ["perf"] },
      { id: "ep-4", title: "하이브리드 검색 RRF 튜닝", domain: "search", ts: "2026-06-20" },
    ];
    if (!query) return base;
    return base.filter((r) => r.title.includes(query) || r.domain?.includes(query));
  },
  domains(): DomainStat[] {
    return [
      { domain: "billing", count: 128 },
      { domain: "search", count: 96 },
      { domain: "engine", count: 74 },
      { domain: "onboarding", count: 41 },
      { domain: "graph", count: 33 },
    ];
  },
  graph(seed?: string): GraphData {
    const states: Array<"full" | "half" | "empty"> = ["full", "half", "empty"];
    const nodes: GraphNode[] = Array.from({ length: 9 }, (_, i) => ({
      id: `n${i}`,
      label: `${seed || "노드"} ${i + 1}`,
      domain: ["billing", "search", "engine"][i % 3],
      state: states[i % 3],
    }));
    const edges: GraphEdge[] = [
      { source: "n0", target: "n1" }, { source: "n0", target: "n2" },
      { source: "n1", target: "n3" }, { source: "n2", target: "n4" },
      { source: "n3", target: "n5" }, { source: "n4", target: "n6" },
      { source: "n5", target: "n7" }, { source: "n6", target: "n8" },
      { source: "n7", target: "n0" }, { source: "n8", target: "n1" },
    ];
    return { nodes, edges };
  },
  search(query: string, topK: number): SearchHit[] {
    return Array.from({ length: topK }, (_, i) => ({
      id: `hit-${i}`,
      title: `"${query || "질의"}" 관련 결과 ${i + 1}`,
      score: +(0.98 - i * 0.11).toFixed(2),
      snippet: "…의미·키워드 하이브리드(RRF) 상위 결과 발췌…",
    }));
  },
};
