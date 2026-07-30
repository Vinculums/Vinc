/** Sidecar client — data plane is localhost HTTP + session token (control will
 *  be Tauri IPC when the shell lands, P4). Graph/query logic never lives here. */

const BASE_KEY = "vinc.sidecar.base";
const TOKEN_KEY = "vinc.sidecar.token";
const OWNER_KEY = "vinc.owner";
const LANG_KEY = "vinc.lang";

// Tauri 셸 webview 의 origin 은 tauri://localhost(mac·linux) 또는
// http://tauri.localhost(windows) — 사이드카가 아니다. 셸(main.rs)은 사이드카를
// 127.0.0.1 의 동적 포트로 띄우고 그 포트를 sidecar_conn 으로 넘기므로, 아래 8787 은
// connectSidecar 가 실제 포트를 주입하기 전까지의 폴백 기본값일 뿐이다.
// (127.0.0.1 은 secure-context 예외라 tauri:// 에서의 fetch 도 차단되지 않는다.)
const IN_SHELL =
  location.protocol === "tauri:" || location.hostname === "tauri.localhost";
const DEFAULT_BASE = IN_SHELL ? "http://127.0.0.1:8787" : location.origin;

// 웹 Vinc(홈페이지용): 셸도 아니고 로컬 dev(localhost)도 아니면 = 호스티드 웹
// (vincs.io/app). 이 경우 데이터 평면은 로컬 사이드카가 아니라 **계정 프록시**
// (/api/graph/*)로 가고, 인증은 Bearer 토큰이 아니라 **계정 세션 쿠키**로 한다
// (쓰기는 CSRF 더블서브밋). owner 는 클라가 보내지 않는다 — 허브가 세션 sub 로 강제.
// (로컬 vite dev 는 !IN_SHELL 이지만 localhost 라 WEB=false → 기존 same-origin 프록시.)
export const WEB = !IN_SHELL
  && location.hostname !== "localhost" && location.hostname !== "127.0.0.1";

/** __Host-csrf(JS-readable) 쿠키 값 — 웹모드 쓰기의 CSRF 더블서브밋 헤더용. */
function csrfToken(): string {
  const m = document.cookie.match(/(?:^|;\s*)__Host-csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]!) : "";
}

export const cfg = {
  // 기본 = 셸에서는 고정 사이드카 주소, 브라우저에서는 same-origin(vite dev proxy 경유).
  get base(): string { return localStorage.getItem(BASE_KEY) || DEFAULT_BASE; },
  set base(v: string) { localStorage.setItem(BASE_KEY, v); },
  get token(): string { return localStorage.getItem(TOKEN_KEY) || ""; },
  set token(v: string) { localStorage.setItem(TOKEN_KEY, v); },
  get owner(): string { return localStorage.getItem(OWNER_KEY) || "tenant:local"; },
  set owner(v: string) { localStorage.setItem(OWNER_KEY, v); },
  // D42 — 표면 언어. 무설정 = OS 로케일 추종(ko-* → 한국어), 그 외 영어.
  get lang(): "en" | "ko" {
    const v = localStorage.getItem(LANG_KEY);
    if (v === "ko" || v === "en") return v;
    return (navigator.language || "").toLowerCase().startsWith("ko") ? "ko" : "en";
  },
  set lang(v: "en" | "ko") { localStorage.setItem(LANG_KEY, v); },
};

/** 연결 셋업 진단 문자열 — 실패 시 화면에 노출해 원인을 즉시 파악(v0.1.3).
 *  v0.1.7: 앱 버전 + 셸이 준 실제 포트(conn.port)를 박는다 — "base=8787 인데 어느
 *  버전인가/셸이 무슨 포트를 줬나"가 리포트만으로 판별되게(macOS 리포트 대응). */
declare const __APP_VERSION__: string;   // vite define 주입(빌드 상수)
declare const __BUILD_SHA__: string;     // 〃 — 짧은 커밋 SHA(웹은 태그와 무관하게
                                         // 배포돼 버전만으로는 빌드를 특정 못 한다)
const APP_V = typeof __APP_VERSION__ === "string" ? __APP_VERSION__ : "dev";
const BUILD_SHA = typeof __BUILD_SHA__ === "string" ? __BUILD_SHA__ : "";
/** 진단·리포트용 빌드 식별자 — `v0.6.7+a1b2c3d`. SHA 를 못 구하면 버전만. */
export const buildId = BUILD_SHA ? `${APP_V}+${BUILD_SHA}` : APP_V;
export let sidecarDiag = "(connectSidecar 미실행)";

/** 셸에서 사이드카 접속정보(포트·세션 토큰)를 받아 cfg 에 주입한다. main.rs 의
 *  `sidecar_conn` 커맨드를 invoke 해 win.eval 타이밍 의존 없이 토큰을 확보하고(정본),
 *  이어 /health 가 응답할 때까지 잠깐 대기해 첫 상호작용 실패(기동 지연)를 막는다.
 *  브라우저(dev)에는 __TAURI__ 가 없어 no-op — same-origin vite 프록시를 그대로 쓴다. */
export async function connectSidecar(): Promise<void> {
  let invoke: any;
  for (let i = 0; i < 20; i++) {                     // __TAURI__ 주입 대기 ≤ ~1s
    invoke = (window as any).__TAURI__?.core?.invoke;
    if (typeof invoke === "function") break;
    await new Promise((r) => setTimeout(r, 50));
  }
  if (typeof invoke !== "function") {
    sidecarDiag = "no __TAURI__ (브라우저 dev 이거나 withGlobalTauri 미적용)";
    return;
  }
  try {
    const c = await invoke("sidecar_conn") as { port?: number; token?: string };
    if (c?.port) cfg.base = `http://127.0.0.1:${c.port}`;
    if (c?.token) cfg.token = c.token;
    sidecarDiag = `v${buildId} conn.port=${c?.port ?? "?"} base=${cfg.base} `
      + `token=${c?.token ? c.token.length + "ch" : "MISSING"}`;
  } catch (e: any) {
    sidecarDiag = `invoke sidecar_conn 예외: ${e?.message || e}`;
  }
  // 사이드카 기동 대기 ≤ ~12s — PyInstaller 단일파일은 첫 실행에 압축 해제가 있어
  // (특히 macOS 콜드 스타트) 5s 로는 정상 기동도 오판할 수 있다.
  for (let i = 0; i < 48; i++) {
    try {
      const res = await fetch(new URL("/health", cfg.base));
      if (res.ok) {
        // 사이드카가 떴어도 스토어가 성치 않으면(구버전 잔존 스토어의 프로비저닝
        // 실패 등) status=degraded 로 온다 — "ok" 로 뭉개면 뒤따르는 전 조회가
        // 원인 불명으로 깨진다. 사유를 진단에 그대로 싣는다.
        const h = await res.json().catch(() => ({} as any));
        sidecarDiag += h?.status === "degraded"
          ? ` · health DEGRADED[${h.provision_error || h.error || "?"}]`
          : " · health ok";
        return;
      }
    } catch {}
    await new Promise((r) => setTimeout(r, 250));
  }
  // 실패하면 "왜"를 반드시 싣는다 — 셸이 모아둔 사이드카 수명 이벤트(종료코드·
  // 시그널·stderr)를 붙여, health TIMEOUT 이 원인 불명으로 끝나지 않게 한다.
  let why = "";
  try { why = await invoke("sidecar_status") as string; } catch (e: any) { why = `status 조회 실패: ${e?.message || e}`; }
  sidecarDiag += ` · health TIMEOUT · sidecar[${why}]`;
}

async function call(path: string, params?: Record<string, string | number | undefined>,
                    body?: unknown): Promise<any> {
  // 웹모드: 경로를 계정 프록시 프리픽스(/api/graph)로 옮긴다(같은 오리진 vincs.io).
  //   call("/atlas") → /api/graph/atlas · call("/ingest/fragment") → /api/graph/ingest/fragment
  const url = new URL(WEB ? "/api/graph" + path : path, cfg.base);
  for (const [k, v] of Object.entries(params || {}))
    if (v !== undefined && v !== "") url.searchParams.set(k, String(v));
  const isWrite = body !== undefined;
  const res = await fetch(url, {
    method: isWrite ? "POST" : "GET",
    // 웹: 계정 세션 쿠키를 실어 보낸다(같은 오리진). 셸: 쿠키 없음.
    ...(WEB ? { credentials: "include" as const } : {}),
    headers: {
      // 셸: 세션 토큰(Bearer). 웹: 쿠키 세션이라 Bearer 불필요, 쓰기엔 CSRF 더블서브밋.
      ...(!WEB && cfg.token ? { Authorization: `Bearer ${cfg.token}` } : {}),
      ...(isWrite ? { "Content-Type": "application/json" } : {}),
      ...(WEB && isWrite ? { "X-CSRF-Token": csrfToken() } : {}),
    },
    body: isWrite ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    // 웹모드에서 세션 만료(401) → 로그인으로 보낸다(앱=/app/, 로그인=/login).
    // 죽은 사이드카에 조용히 실패하지 않고 "다시 로그인" 경로를 준다.
    if (WEB && res.status === 401) {
      location.assign("/login");
      throw new Error("session expired");
    }
    throw new Error(`${res.status} ${await res.text()}`);
  }
  return res.json();
}

export type GraphNode = {
  id: string; label: string; domain: string; degree: number;
  kind?: string;      // concept | bridge | episode | record | entity (F2 — 부착 축)
  domain2?: string;   // bridge 전용 — target_domain (노드 domain = source_domain)
  ts?: string;        // D43 — created/date 우선, 없으면 updated_at. 빈 문자열 가능
  x?: number; y?: number; z?: number;
};
export type GraphEdge = {
  source: string; target: string; type: string; cross_domain: boolean;
  role?: string;        // CONNECTS.role / ATTACHES_TO.type 질화자 (F2)
  confidence?: number;  // D43 — 0..1. 상류 미저작이면 키 자체가 없다
  evidence?: string;    // D43 — 근거 문장. 툴팁 전용
};
/** 상세 레일의 연결 요약 + 관계 목록(node-context ④). 세는 자리가 노드 자신이라
 *  어느 동선(그래프 클릭 · 드릴다운 · 기간 목록 · 검색 착지)으로 열어도 같은 값이다. */
export type NodeLink = {
  id: string; title: string; domain: string; type: string;
  dir: "out" | "in" | "att";
  evidence: boolean;      // 근거 문장이 기록돼 있는가(문장 전문은 edge-context)
  confidence: number;
};
export type NodeLinks = {
  degree: number; out: number; in: number; attached: number;
  items: NodeLink[]; truncated: boolean; executed: boolean;
};
export type LedgerRow = {
  // D52 by=concept: `domain` 은 개념 id, `label` 은 제목, `topic` 은 그 개념의 주제.
  // by=domain(기본)에서는 `domain` 이 곧 주제이고 label/topic 은 없다.
  domain: string; label?: string; topic?: string; concepts: number;
  episodic: number; record: number; unsourced: number;
  connected: number; gateway: number; orphan: number;
};
// D43 ego — /ledger 응답에서 UI 가 읽는 것만. scope·population 은 신형 사이드카의
// 추가 필드다(구버전 응답엔 키 자체가 없다). seed 를 보냈는데 scope 가 없으면 구
// 사이드카가 seed 를 무시하고 주제/전체 롤업을 돌려준 것 — UI 가 캡션으로 밝힌다.
export type LedgerRes = {
  executed: boolean; rows?: LedgerRow[]; note?: string;
  scope?: "seed" | "domain" | "all";   // 무엇을 모집단으로 집계했는가(에코)
  population?: number;                 // scope="seed" 일 때 ego 구성원 수
  by?: "domain" | "concept";           // D52 — 롤업 알갱이 에코(구 사이드카엔 없다)
};

export const api = {
  health: () => call("/health"),
  atlasL0: (owner: string) => call("/atlas", { owner }),
  graph: (owner: string, opts: { domain?: string; seed?: string; depth?: number; layout?: string } = {}) =>
    call("/graph", { owner, ...opts }),
  ledger: (owner: string,
           opts: { domain?: string; seed?: string; depth?: number;
                   by?: "domain" | "concept" } = {}): Promise<LedgerRes> =>
    call("/ledger", { owner, ...opts }),
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
  // D45-V — 노드 클릭 상세: 노드 설명(one_liner/summary) + 정본 근거 발췌.
  // arm: 'mentions'(언급 링크) | 'fts'(제목 일치 폴백) | 'none'(발췌 없음 — 무해).
  // links(신설): 상세 레일 수치의 정본. 구 사이드카 응답엔 키 자체가 없다 —
  // 프론트는 그 부재로 구버전을 판별해 그래프 잔상 집계로 우아하게 강등한다.
  nodeContext: (owner: string, node: string, limit = 4): Promise<{
    executed: boolean; title?: string; one_liner?: string; arm?: string;
    excerpts?: { doc_id: string; doc_title: string; section: string;
                 excerpt: string; method: string }[];
    links?: NodeLinks;
  }> => call("/node-context", { owner, node, limit }),
  // D45-V2 2단계 — Reader 서브뷰: 정본 전문(무손실 복원 + 해시 검증) + 밑줄 범위.
  docGet: (owner: string, id: string): Promise<{
    executed: boolean; text?: string; title?: string; verified?: boolean;
    content_hash?: string; error?: string;
  }> => call("/doc", { owner, id }),
  docHighlights: (owner: string, doc_id: string, concept_id: string): Promise<{
    executed: boolean; ranges?: { char_start: number; char_end: number }[];
  }> => call("/doc/highlights", { owner, doc_id, concept_id }),
  // D45-E — 앱 내 SSoT 편집(MVP): 저장(전체 재청킹, 재임베딩은 지연) + 개념 추출.
  // origin 은 출처 표시(파일 import 시 파일명) — 서버의 doc 정체성 산식에 참여하고
  // 재드롭 식별의 근거가 된다. 조인 키가 아니며 UI 에도 노출하지 않는다(D45).
  docPut: (owner: string, text: string, id?: string, title?: string,
           origin?: string): Promise<{
    executed: boolean; skipped?: boolean; doc_id?: string; content_hash?: string;
    chunks?: number; needs_embed?: boolean; error?: string;
  }> => call("/doc/put", undefined, { owner, text, ...(id ? { id } : {}),
                                      ...(title ? { title } : {}),
                                      ...(origin ? { origin } : {}) }),
  // 파일 바이트 입구(D45) — 텍스트로 못 읽는 것(PDF)과 브라우저 UTF-8 디코드가
  // 망친 것(cp949 등)을 원본 바이트로 넘겨 서버 추출기에 맡긴다. multipart 가
  // 아니라 base64-in-JSON 인 이유는 계정 프록시가 JSON 만 통과시키기 때문.
  docPutFile: (owner: string, filename: string, data_b64: string,
               id?: string, title?: string): Promise<{
    executed: boolean; skipped?: boolean; doc_id?: string; source_format?: string;
    chunks?: number; needs_embed?: boolean; error?: string; note?: string;
  }> => call("/doc/put-file", undefined, { owner, filename, data_b64,
                                           ...(id ? { id } : {}),
                                           ...(title ? { title } : {}) }),
  docReembed: (owner: string, id: string): Promise<{ executed: boolean; docs?: number }> =>
    call("/doc/reembed", undefined, { owner, id }),
  // U2 — 관계 근거 저작(정본 청크에서 결정론 추출). 반복 실행 안전(있는 근거는 보존).
  evidenceBackfill: (owner: string, docId?: string): Promise<{
    executed: boolean; filled?: number; scanned?: number;
  }> => call("/evidence/backfill", undefined, { owner, ...(docId ? { doc_id: docId } : {}) }),
  docExtract: (owner: string, id: string, apply = false): Promise<{
    executed: boolean; candidates?: { title: string }[];
    applied?: { created: number; matched: number }; mentions?: number;
  }> => call("/doc/extract", undefined, { owner, id, apply }),
  docList: (owner: string): Promise<{
    executed: boolean; count?: number;
    docs?: { doc_id: string; title: string; origin: string; chunks: number; updated_at: string }[];
  }> => call("/docs", { owner }),
  // D45-V2 4단계 — 타임라인 기간(월-밴드) 클릭: [start, end) 의 날짜 있는 항목.
  docsInRange: (owner: string, start: string, end: string, domain?: string): Promise<{
    executed: boolean; total?: number;
    items?: { id: string; kind: string; title: string; ts: string; domain: string }[];
  }> => call("/docs-in-range", { owner, start, end, ...(domain ? { domain } : {}) }),
  // D45-V2 3단계 — 합계 구간 클릭: 그 (도메인, mark) 의 개념 목록(원장 수치 정합).
  ledgerDrilldown: (owner: string, domain: string, mark: string, seed?: string): Promise<{
    executed: boolean; total?: number;
    items?: { concept_id: string; title: string; one_liner: string }[];
  }> => call("/ledger-drilldown", { owner, domain, mark, ...(seed ? { seed } : {}) }),
  // D45-V 1단계 — 흐름 선 클릭: 관계의 evidence·confidence + 양끝 노드 메타/발췌.
  edgeContext: (owner: string, src: string, dst: string, type: string): Promise<{
    executed: boolean; evidence?: string; confidence?: number;
    src?: { id: string; title?: string; one_liner?: string;
            excerpt?: { doc_id: string; excerpt: string } | null };
    dst?: { id: string; title?: string; one_liner?: string;
            excerpt?: { doc_id: string; excerpt: string } | null };
  }> => call("/edge-context", { owner, src, dst, type }),
  // fragment MERGE(D11 §5) — Bind 확정도 이 단일 쓰기 표면을 그대로 쓴다(D32)
  ingestFragment: (fragment: unknown) => call("/ingest/fragment", undefined, fragment),
  importPack: (pack: unknown, owner: string) =>
    call("/ingest/pack", undefined, { pack, owner }),
  backup: (out: string) => call("/backup", undefined, { out }),
  pin: (episode: string) => call("/pin", undefined, { episode }),

  // S2 — 도메인 일괄 변경. new='' 는 미분류로 내림(노드는 남는다). 전역 Domain
  // 노드는 서버가 안 건드린다 — 내 노드의 domain 문자열만 바뀐다.
  domainRename: (owner: string, old: string, next: string): Promise<{
    executed: boolean; moved?: number; error?: string;
  }> => call("/domain/rename", undefined, { owner, old, new: next }),

  // ── 삭제(S1) — 앱 UI 배선. 여태 백엔드·MCP 에만 있고 UI 가 없어 "추가는 원클릭,
  // 제거는 불가"였다. owner 는 웹모드에선 허브가 세션 sub 로 강제(바디 owner 무시),
  // 로컬 셸에선 여기서 넘긴 값이 스코프가 된다. 되돌릴 수 없으므로 호출부가 확인을 받는다.
  deleteNode: (owner: string, node: string): Promise<{
    executed: boolean; found?: boolean;
    deleted?: { node: number; edges: number }; note?: string; error?: string;
  }> => call("/delete", undefined, { owner, node }),
  deleteEdge: (owner: string, from: string, to: string, type: string): Promise<{
    executed: boolean; deleted?: number; error?: string;
  }> => call("/delete-edge", undefined, { owner, from, to, type }),
  docDelete: (owner: string, id: string): Promise<{
    executed: boolean; deleted_meta?: number; deleted_chunks?: number; error?: string;
  }> => call("/doc/delete", undefined, { owner, id }),

  // ── 계정 그래프 동기화(S5) ──────────────────────────────────────────────
  // 선택 가능 도메인 + 현재 선택. 오프라인 우선 UI 의 데이터원.
  syncDomains: (owner: string) => call("/sync/domains", { owner }),
  setSyncDomains: (domains: string[]) => call("/sync/domains", undefined, { domains }),
  // 회원 키는 **바디로만** 사이드카에 전달(env·디스크·레포 반입 없음). 긴 작업 →
  // 202+job_id; 진행은 pollJob 으로. base 는 테스트용 override(기본 mcp.vincs.io).
  syncNow: (token: string, owner: string, base?: string) =>
    call("/sync", undefined, { token, owner, ...(base ? { base } : {}) }),
  // 계정 연결(디바이스 플로우) — start 로 state 발급, 브라우저 승인 후 status 폴링으로
  // 키를 1회 수령한다(사이드카 메일박스). 셸 전용 UI(settings)에서만 쓴다.
  connectStart: () => call("/connect/start", undefined, {}),
  connectStatus: (state: string) => call("/connect/status", { state }),
  jobStatus: (jobId: string) => call(`/jobs/${jobId}`),
};

// ── 커뮤니티 공유(웹 전용) ─────────────────────────────────────────────────
// 그래프 프록시(/api/graph)와 별개인 계정 라우트(/api/community). 세션 쿠키 +
// 쓰기 CSRF. 셸/브라우저 dev 에는 세션이 없어 사실상 웹모드 전용(버튼도 WEB 가드).
/** 계정 API 오류 — `code` 를 실어 호출부가 문자열 매칭 없이 분기하게 한다.
 *  (계정 백엔드의 오류 봉투는 `{error:{code,message,request_id}}` 고정.) */
export class AccountError extends Error {
  constructor(readonly status: number, readonly code: string, message: string) {
    super(message);
    this.name = "AccountError";
  }
}

async function communityPost(path: string, body: unknown): Promise<any> {
  const res = await fetch(new URL(path, location.origin), {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken() },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    if (res.status === 401) { location.assign("/login"); throw new Error("session expired"); }
    // 오류 봉투를 파싱해 code 를 살린다 — 예전엔 상태코드+본문을 통째로 message 에
    // 밀어넣어, 호출부가 "왜 실패했는지"로 분기하려면 문자열을 뒤져야 했다.
    const raw = await res.text();
    let code = "", message = raw;
    try {
      const e = JSON.parse(raw)?.error;
      if (e?.code) { code = String(e.code); message = String(e.message ?? raw); }
    } catch { /* 본문이 JSON 이 아니면 원문 그대로 */ }
    throw new AccountError(res.status, code, message);
  }
  return res.json();
}

export const community = {
  /** 지식 팩을 공개 갤러리에 게시. 반환 {publicId}. 데이터가 공개된다(호출부가 동의 확인). */
  publish: (title: string, description: string, pack: unknown): Promise<{ publicId: string }> =>
    communityPost("/api/community/publish", { title, description, pack }),
};

/** 웹모드 부팅 인증 게이트 — /api/me 로 세션을 **먼저** 확인한다. 미인증이면 서피스를
 *  렌더하기 전에 /login 으로 보내고 false 를 돌려준다(main.ts 가 렌더를 중단). 이게 없으면
 *  앱이 화면을 먼저 그린 뒤 데이터 호출이 401 을 받고서야 리다이렉트돼 "화면 깜빡임 → 로그인"
 *  이 된다. 셸/브라우저 dev(WEB=false)에서는 호출되지 않는다(세션 개념 없음). */
export async function ensureWebAuth(): Promise<boolean> {
  // 빠른 경로(왕복 0): 세션이 서면 __Host-csrf(JS-readable)가 함께 세팅된다. 그 쿠키가
  // 아예 없으면 로그인한 적 없음 → 서버 확인 없이 즉시 /login (never-logged-in = 흔한
  // 경우, 완전 즉시 리다이렉트). 쿠키가 있으면 만료됐을 수 있어 /api/me 로 검증한다.
  if (!/(?:^|;\s*)__Host-csrf=/.test(document.cookie)) {
    location.assign("/login");
    return false;
  }
  try {
    const res = await fetch(new URL("/api/me", location.origin), { credentials: "include" });
    if (res.ok) return true;
  } catch { /* 네트워크 실패 → 아래 로그인으로(안전측) */ }
  location.assign("/login");
  return false;
}

/** 잡 완료까지 폴링. jobs.py 계약: 상태 queued|running|done|error, 키는 job_id. */
export async function pollJob(jobId: string, onTick?: (s: any) => void,
                             { intervalMs = 900, timeoutMs = 300000 } = {}): Promise<any> {
  const t0 = Date.now();
  for (;;) {
    const s = await api.jobStatus(jobId);
    onTick?.(s);
    if (s.status === "done") return s;
    if (s.status === "error") throw new Error(s.error || "sync job failed");
    if (Date.now() - t0 > timeoutMs) throw new Error("sync timed out");
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}
