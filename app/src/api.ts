/** Sidecar client — data plane is localhost HTTP + session token (control will
 *  be Tauri IPC when the shell lands, P4). Graph/query logic never lives here. */

const BASE_KEY = "vinc.sidecar.base";
const TOKEN_KEY = "vinc.sidecar.token";
const OWNER_KEY = "vinc.owner";
const LANG_KEY = "vinc.lang";
// 현재 스페이스. 빈 문자열 = 개인. 팀이면 팀 publicId 를 담는다(계정이 이 값을
// team:<uuid> graph_owner 로 바꿔 허브에 서명해 보낸다 — 클라는 owner 를 모른다).
const TEAM_KEY = "vinc.team";

// Tauri 셸 webview 의 origin 은 tauri://localhost(mac·linux) 또는
// http://tauri.localhost(windows) — 사이드카가 아니다. 셸(main.rs)은 사이드카를
// 127.0.0.1 의 동적 포트로 띄우고 그 포트를 sidecar_conn 으로 넘기므로, 아래 8787 은
// connectSidecar 가 실제 포트를 주입하기 전까지의 폴백 기본값일 뿐이다.
// (127.0.0.1 은 secure-context 예외라 tauri:// 에서의 fetch 도 차단되지 않는다.)
// 셸 판정 — `location` 으로 본다(전역 주입 대기는 withGlobalTauri 를 끄면서 폐기).
export const IN_SHELL =
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
  // KO 비활성(2026-08-15): EN 카피 재작성이 끝날 때까지 영어만 노출한다(홈페이지와
  // 동일 조치, Vinc-homepage/site/assets/gnb.js 참조). 재활성화: 아래 return 을 지우면
  // 원래 판정(저장값 → OS 로케일)이 살아난다. KO 덱(copy.ts)은 재작성 원본으로 보존.
  get lang(): "en" | "ko" {
    return "en";
    // 재활성화 시 위 return 을 지우고 아래 원래 판정을 살릴 것:
    // const v = localStorage.getItem(LANG_KEY);
    // if (v === "ko" || v === "en") return v;
    // return (navigator.language || "").toLowerCase().startsWith("ko") ? "ko" : "en";
  },
  set lang(v: "en" | "ko") { localStorage.setItem(LANG_KEY, v); },
  /** 현재 스페이스 — "" = 개인, 그 외 = 팀 publicId. 웹모드에서만 의미가 있다
   *  (셸은 로컬 사이드카에 붙고 팀은 계정 프록시 경유라서). 셸에서 남은 값이
   *  조용히 따라다니지 않도록 getter 에서 WEB 을 함께 본다. */
  get team(): string { return WEB ? localStorage.getItem(TEAM_KEY) || "" : ""; },
  set team(v: string) {
    if (v) localStorage.setItem(TEAM_KEY, v);
    else localStorage.removeItem(TEAM_KEY);
  },
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

/** 셸에서만 `invoke` 를 가져온다(웹 Vinc·브라우저 dev 는 null).
 *  **동적 import 인 이유**: 정적으로 쓰면 이 모듈이 웹 번들에도 들어간다. */
export async function shellInvoke():
    Promise<(<T = unknown>(cmd: string, args?: any) => Promise<T>) | null> {
  if (!IN_SHELL) return null;
  try {
    const { invoke } = await import("@tauri-apps/api/core");
    return invoke as any;
  } catch {
    return null;
  }
}

/** 셸에서 사이드카 접속정보(포트·세션 토큰)를 받아 cfg 에 주입한다. main.rs 의
 *  `sidecar_conn` 커맨드를 invoke 해 win.eval 타이밍 의존 없이 토큰을 확보하고(정본),
 *  이어 /health 가 응답할 때까지 잠깐 대기해 첫 상호작용 실패(기동 지연)를 막는다.
 *  브라우저(dev)에는 셸이 없어 no-op — same-origin vite 프록시를 그대로 쓴다.
 *
 *  ⚠ 전역 `window.__TAURI__` 를 쓰지 않는다(Q2, 2026-08-07). `withGlobalTauri` 는
 *  **전체 API 를 페이지 전역에 노출**하는데, 이 앱은 사용자 문서를 렌더하고 웹 Vinc
 *  로도 같은 번들이 나가므로 표면을 열어 둘 이유가 없다. 대신 모듈에서 가져오되
 *  **셸일 때만 동적 import** 한다 — 정적 import 는 웹 번들에도 들어간다. */
export async function connectSidecar(): Promise<void> {
  const invoke = await shellInvoke();
  if (!invoke) {
    sidecarDiag = "셸 아님(브라우저 dev · 웹 Vinc) — 사이드카 연결 생략";
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

/** 호출 하나의 스페이스를 명시적으로 못박는다. 생략하면 현재 스페이스(`cfg.team`)를
 *  따른다. `{ team: null }` 은 "현재가 팀이어도 이번 호출은 개인" 이라는 뜻이다 —
 *  스페이스를 건너다니는 공유/불러오기가 이걸 쓴다. */
export type Scope = { team: string | null };

async function call(path: string, params?: Record<string, string | number | undefined>,
                    body?: unknown, scope?: Scope): Promise<any> {
  // 웹모드: 경로를 계정 프록시 프리픽스(/api/graph)로 옮긴다(같은 오리진 vincs.io).
  //   call("/view") → /api/graph/view · call("/ingest/fragment") → /api/graph/ingest/fragment
  const url = new URL(WEB ? "/api/graph" + path : path, cfg.base);
  for (const [k, v] of Object.entries(params || {}))
    if (v !== undefined && v !== "") url.searchParams.set(k, String(v));
  // 스페이스 지정자. 계정이 이걸 읽어 팀 그래프로 서명을 바꾸고, 인자에서는 떼어낸다
  // (넘어가면 사이드카가 op 인자로 오해한다 — 계정 graph.ts 가 구조분해로 제거).
  // 셸 모드에는 계정이 없으므로 붙이지 않는다.
  const team = scope ? scope.team || "" : cfg.team;
  if (WEB && team) url.searchParams.set("team", team);
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
    // 503 = 사이드카는 살아 있는데 **스토어를 못 읽는다**(락 충돌·손상·확장 경로).
    // 이 하나만 특별히 뜯는 이유: 나머지 실패와 달리 화면이 "데이터가 없다"로
    // 오독되기 쉬운 유일한 상태이고, 그 오독이 곧 데이터 유실처럼 보인다.
    // 사이드카가 사유(open_error)를 바디에 실어 주므로 여기서 꺼내 얹는다 —
    // 안 그러면 호출부는 `503 {…json…}` 문자열만 받아 사람에게 못 보여 준다.
    if (res.status === 503) {
      const b = await res.json().catch(() => ({} as any));
      const err: any = new Error(b?.open_error || b?.error || "no reachable store");
      err.storeDown = true;
      err.reason = String(b?.open_error || "");
      throw err;
    }
    throw new Error(`${res.status} ${await res.text()}`);
  }
  return res.json();
}

/** 이 실패가 "스토어를 못 읽는다"인가. 화면은 이 경우에만 다른 말을 해야 한다 —
 *  나머지 실패는 다시 시도하면 되지만, 이건 **빈 결과와 구분되지 않아서** 위험하다. */
export function storeDownReason(e: unknown): string | null {
  const err = e as any;
  return err?.storeDown ? (err.reason || "") : null;
}

/** 시간축(D72). `recorded` = 기록 시각, `content` = 문서 내용이 가리키는 시점. */
export type TimeBasis = "recorded" | "content";

export type GraphNode = {
  id: string; label: string; domain: string; degree: number;
  kind?: string;      // concept | decision | bridge | episode | record | entity (F2 — 부착 축)
  domain2?: string;   // bridge 전용 — target_domain (노드 domain = source_domain)
  ts?: string;        // D43 — created/date 우선, 없으면 updated_at. 빈 문자열 가능
  // D72 — 내용 시점(그 노드가 말하는 일이 언제 있었나). 축을 `content` 로 요청하면
  // 서버가 `ts` 를 이 값으로 갈아 끼우지만, **두 열 모두 실려 온다** — 클라이언트가
  // 재조회 없이 축을 바꿀 수 있게. 구 사이드카에는 이 키가 아예 없다(우아 강등).
  content_ts?: string;
  // D74 — 좌표는 **두 벌**이다: x/y/z 는 3D 정본, x2/y2 는 2D 정본.
  // 2D 는 3D 를 눌러 접은 것이 아니다(접으면 원뿔 배치에서 서로 다른 노드가 같은
  // 점이 된다). 같은 도메인 트리를 평면 문법으로 다시 배치한 값이라 겹침이
  // 원리적으로 생기지 않는다. 구 사이드카에는 이 키가 없다 → x/y 로 우아 강등.
  x?: number; y?: number; z?: number;
  x2?: number; y2?: number;
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
/** D69 좌측 문서 트리. 문서는 주제 열을 갖지 않으므로 주제도 착지 노드도 언급
 *  (MENTIONS)에서 유도된다 — `모든 문서` 목록과 같은 원천이라 두 화면이 같은 문서를
 *  다른 주제에 걸지 않는다. `depth` 는 그 주제 안 개념 계층(선행·파생 BFS)이고,
 *  `node_id` 는 클릭 시 착지할 노드다. 언급 0 문서는 `unfiled`(착지 노드 없음). */
export type NavDoc = {
  doc_id: string; title: string; updated_at: string;
  node_id: string; node_title: string; depth: number;
};
export type NavGroup = { domain: string; concepts: number; docs: NavDoc[] };
export type NavTreeRes = {
  executed: boolean; note?: string;
  domains?: NavGroup[];
  unfiled?: { doc_id: string; title: string; updated_at: string }[];
  counts?: { domains: number; docs: number; unfiled: number };
};

export const api = {
  health: () => call("/health"),
  viewL0: (owner: string) => call("/view", { owner }),
  //: `domains` 는 **주제 여럿**(쉼표 결합). 스코프는 하위 트리 포함이다 —
  //: `domains=vinc` 하나면 `vinc/design` 까지 함께 온다(api/query.normalize_domains).
  graph: (owner: string, opts: { domain?: string; domains?: string[]; seed?: string;
                                 depth?: number; layout?: string } = {}) => {
    const { domains, ...rest } = opts;
    return call("/graph", { owner, ...rest,
                            ...(domains?.length ? { domains: domains.join(",") } : {}) });
  },
  recall: (owner: string, limit = 50) => call("/recall", { owner, limit, rank: "preference" }),
  search: (query: string, top_k = 20) => call("/search", { query, top_k }),
  coupling: (owner: string, a: string, b: string) => call("/coupling", { owner, a, b }),
  // scope 를 주면 그 스페이스의 주제를 읽는다 — 개인 → 팀 불러오기 화면이
  // **개인** 주제 목록을 보여야 해서(팀 것을 보여주면 고를 것이 없다).
  domains: (owner: string, scope?: Scope) => call("/domains", { owner }, undefined, scope),
  // 관례(GRAPH.md) — 읽기는 웹에서도 되지만(허브 _PROXY_OPS 등재), **어휘 재가는
  // 셸 전용**이다: 주제 어휘에는 owner 차원이 없어 계정 프록시가 의도적으로 막는다.
  conventions: (owner: string) => call("/conventions", { owner }),
  conventionsBuild: (owner: string, lang?: string) =>
    call("/conventions/build", undefined, { owner, lang }),
  themes: () => call("/themes"),
  types: (theme_id: string, status?: string) => call("/types", { theme_id, status }),
  typeRatify: (theme_id: string, name: string) =>
    call("/types/ratify", undefined, { theme_id, name }),
  typeReject: (theme_id: string, name: string) =>
    call("/types/reject", undefined, { theme_id, name }),
  typeRename: (theme_id: string, from_name: string, to_name: string) =>
    call("/types/rename", undefined, { theme_id, from_name, to_name }),
  activate: (owner: string, opts: Record<string, unknown> = {}) =>
    call("/activate", undefined, { owner, ...opts }),
  // scope 를 주면 그 스페이스에서 팩을 만든다(생략 = 현재 스페이스).
  // 팀→개인 공유는 scope 없이(=팀에서) 만들고, 개인→팀 불러오기는 {team:null} 로 만든다.
  share: (owner: string, opts: { domain?: string; seed?: string; depth?: number; ids?: string } = {},
          scope?: Scope) =>
    call("/share", { owner, ...opts }, undefined, scope),
  promoteCandidates: (owner: string) => call("/promote-candidates", { owner }),
  suggestTies: (owner: string, node: string) => call("/suggest-ties", { owner, node }),
  // D45-V — 노드 클릭 상세: 노드 설명(one_liner/summary) + 정본 근거 발췌.
  // arm: 'mentions'(언급 링크) | 'fts'(제목 일치 폴백) | 'none'(발췌 없음 — 무해).
  // links(신설): 상세 레일 수치의 정본. 구 사이드카 응답엔 키 자체가 없다 —
  // 프론트는 그 부재로 구버전을 판별해 그래프 잔상 집계로 우아하게 강등한다.
  // props(D73): 개념의 자유 속성. `one_liner` 는 쓰기 경로가 없어 MCP 적재분이
  // 전량 빈 값이고 사람이 쓴 요약은 실제로 props 에 들어 있다 — 호출부는
  // one_liner → props 순으로 본문을 고른다. 구 사이드카엔 키가 없다(undefined).
  nodeContext: (owner: string, node: string, limit = 4): Promise<{
    executed: boolean; title?: string; one_liner?: string; arm?: string;
    props?: Record<string, unknown>;
    excerpts?: { doc_id: string; doc_title: string; section: string;
                 excerpt: string; method: string }[];
    links?: NodeLinks;
  }> => call("/node-context", { owner, node, limit }),
  // D45-V2 2단계 — Reader 서브뷰: 정본 전문(무손실 복원 + 해시 검증) + 밑줄 범위.
  docGet: (owner: string, id: string): Promise<{
    executed: boolean; text?: string; title?: string; verified?: boolean;
    content_hash?: string; error?: string;
    // D72 — 최초 기록 시각 · 내용 시점. 구 사이드카에는 키 자체가 없다.
    created_at?: string; content_time?: string;
  }> => call("/doc", { owner, id }),
  docHighlights: (owner: string, doc_id: string, concept_id: string): Promise<{
    executed: boolean; ranges?: { char_start: number; char_end: number }[];
  }> => call("/doc/highlights", { owner, doc_id, concept_id }),
  // D45-E — 앱 내 SSoT 편집(MVP): 저장(전체 재청킹, 재임베딩은 지연) + 개념 추출.
  // origin 은 출처 표시(파일 import 시 파일명) — 서버의 doc 정체성 산식에 참여하고
  // 재드롭 식별의 근거가 된다. 조인 키가 아니며 UI 에도 노출하지 않는다(D45).
  // contentTime(D72): **키가 없을 때와 빈 값일 때가 다르다.** undefined 면 보내지
  // 않아 서버가 기존 값을 보존하고, "" 는 사용자가 지운 것이다(그래서 falsy 를
  // 통째로 걸러내는 spread 관용구를 쓰지 않는다).
  docPut: (owner: string, text: string, id?: string, title?: string,
           contentTime?: string, origin?: string): Promise<{
    executed: boolean; skipped?: boolean; doc_id?: string; content_hash?: string;
    chunks?: number; needs_embed?: boolean; content_time?: string; error?: string;
  }> => call("/doc/put", undefined, { owner, text, ...(id ? { id } : {}),
                                      ...(title ? { title } : {}),
                                      ...(contentTime === undefined ? {} : { content_time: contentTime }),
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
  // 문서 목록. `topic`/`topics` 는 언급 개념에서 유도된 주제(문서에 주제 열은 없다),
  // `primary_concept_id` 는 **그 문서를 눌렀을 때 착지할 노드**(D69) — 대표 주제 안의
  // 최다 언급 개념이다. 언급이 없으면 빈 문자열이고 착지 대상이 없다(편집기로만 열린다).
  docList: (owner: string): Promise<{
    executed: boolean; count?: number;
    docs?: { doc_id: string; title: string; origin: string; chunks: number;
             updated_at: string; topic?: string; topics?: string[];
             primary_concept_id?: string; primary_concept_title?: string }[];
  }> => call("/docs", { owner }),
  // D69 — 좌측 문서 트리: 주제 → 그 주제의 문서(개념 계층 깊이순) + 미분류.
  navTree: (owner: string): Promise<NavTreeRes> => call("/nav-tree", { owner }),
  // D45-V2 4단계 — 타임라인 기간(월-밴드) 클릭: [start, end) 의 날짜 있는 항목.
  // ⚠ basis 는 차트가 그리는 축과 **글자 그대로 같아야** 한다(D72) — 밴드를 눌러
  // 여는 목록이 다른 시간 열로 세면 화면이 스스로를 반박한다.
  docsInRange: (owner: string, start: string, end: string, domain?: string,
                basis?: TimeBasis): Promise<{
    executed: boolean; total?: number; basis?: string;
    items?: { id: string; kind: string; title: string; ts: string; domain: string }[];
  }> => call("/docs-in-range", { owner, start, end, ...(domain ? { domain } : {}),
                                 ...(basis ? { basis } : {}) }),
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
  // scope 를 주면 그 스페이스로 적재한다(생략 = 현재 스페이스). 스페이스 간 이동은
  // "한쪽에서 share → 다른 쪽으로 importPack" 이라는 한 가지 동작으로 통일돼 있다.
  importPack: (pack: unknown, owner: string, scope?: Scope) =>
    call("/ingest/pack", undefined, { pack, owner }, scope),
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

/** 팀 목록 — 그래프 프록시가 아니라 계정 라우트(/api/teams). 세션 쿠키만 필요하다.
 *  웹모드가 아니면 팀 자체가 없으므로 빈 배열로 끝낸다(호출부 분기 제거). */
export const teamsApi = {
  list: async (): Promise<Array<{ id: string; name: string; role: string; status: string }>> => {
    if (!WEB) return [];
    const res = await fetch(new URL("/api/teams", location.origin), { credentials: "include" });
    if (!res.ok) return [];
    return ((await res.json()) as any).teams || [];
  },
};

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
