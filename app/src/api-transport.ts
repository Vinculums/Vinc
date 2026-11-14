// 데이터 호출의 전송: 이 기기의 사이드카 · 셸의 계정 통과로 · 웹의 계정 프록시와 쿠키 라우트로 보내고, 실패는
// `api-error.ts` 의 모양으로 돌려준다. 설정은 `api-config.ts` 에서 읽고 `api.ts` 는 읽지 않는다: 엔드포인트 표(`api.ts`)가 이것을 부른다.
import { viewSessionCache } from "./view-session-cache";
import { ApiError, parseEnvelope, localFailure, type AccountFailureKind } from "./api-error";
import type { Scope, SearchOptions } from "./api-types";
import { cfg, WEB, dataMode, accountMode, shellInvoke, messageOf } from "./api-config";

/** CSRF(JS-readable) 쿠키 값 — 웹모드 쓰기의 CSRF 더블서브밋 헤더용. HTTPS 에서는 `__Host-csrf`,
 *  셀프호스트 허브를 평문 localhost 로 열면 `vinc-csrf` 다(브라우저가 평문에서 `__Host-` 를 안 받는다). */
const CSRF_COOKIE = /(?:^|;\s*)(?:__Host-csrf|vinc-csrf)=([^;]+)/;
function csrfToken(): string {
  const m = document.cookie.match(CSRF_COOKIE);
  return m ? decodeURIComponent(m[1]!) : "";
}

/** 계정 쿠키 세션의 같은 출처 라우트(`/api/me` · `/api/teams` · `/api/push/*` · `/logout` …)로 가는 문 하나.
 *  웹 Vinc 와 셀프호스트 허브에서만 뜻이 있다. 쿠키를 싣고, POST 에는 CSRF 더블서브밋 표식을 붙이며, 본문(`json`)을
 *  넘기면 JSON 으로 싣는다(넘긴 값이 undefined 여도 JSON 머리는 붙는다). 응답은 그대로 돌려준다: 상태 확인과
 *  본문 해석은 부르는 쪽 몫이다. 경로와 메서드는 부르는 자리에 글자 그대로 쓴다: 계약 검사
 *  (`app/dev/sidecar-contract-check.ts`)가 그 자리에서 읽는다. 그래프 평면(`/api/graph/*`)은 `localCall` 이 맡는다. */
function webFetch(path: string, init: { method?: "GET" | "POST"; cache?: RequestCache; signal?: AbortSignal;
                                        json?: unknown } = {}): Promise<Response> {
  const post = init.method === "POST";
  const json = "json" in init;
  return fetch(new URL(path, location.origin), {
    ...(post ? { method: "POST" } : {}),
    credentials: "include",
    ...("cache" in init ? { cache: init.cache } : {}),
    ...("signal" in init ? { signal: init.signal } : {}),
    ...(post ? { headers: { ...(json ? { "Content-Type": "application/json" } : {}), "X-CSRF-Token": csrfToken() } } : {}),
    ...(json ? { body: JSON.stringify(init.json) } : {}),
  });
}

// 계정 모드에서도 **사이드카가 직접** 답하는 경로. 기기 연결은 루프백 콜백이라 사이드카가
// 받아야 하고, 동기화·백업·헬스는 이 기기의 일이며, 통과로 자신을 통과로에 실을 수는 없다.
// 이 목록 밖의 데이터 호출은 전부 계정으로 간다.
const SIDECAR_ONLY = ["/health", "/connect/", "/sync", "/backup", "/space/", "/config",
                      "/shutdown"];
const sidecarOnly = (path: string): boolean =>
  SIDECAR_ONLY.some((p) => path === p || path.startsWith(p));

// 멤버 키 — OS 키체인에서 한 번 읽어 이 창이 사는 동안 들고 있는다. 호출마다 IPC 를
// 태우면 화면 하나에 수십 번이다. 계정이 키를 거절하거나 로그아웃하면 비운다.
import { ScopedCredentials, type CredentialIdentity } from "./credential-scope";
const credentials = new ScopedCredentials({
  personal: async () => (await import("./account-connect")).accountKey(),
  team: async (subject, team) => (await import("./account-connect")).teamAccountKey(subject, team),
  save: async (subject, team, key) => (await import("./account-connect")).setTeamAccountKey(subject, team, key),
  inspect: async key => {
    const response = await viaProxy(key, "GET", "/spaces", { identity: "1" }, undefined, "");
    if (!response.ok) throw new Error(response.status === 401 ? "invalid_key" : "credential_identity_unavailable");
    return response.json() as Promise<CredentialIdentity>;
  },
});
async function memberKey(team = ""): Promise<string> {
  return team ? credentials.forTeam(team) : credentials.personal();
}
export async function hasTeamCredential(team: string): Promise<boolean> {
  return !!await memberKey(team);
}
export async function bindTeamCredential(team: string, key: string): Promise<void> {
  await credentials.bind(team, key);
  viewSessionCache.purge();
}
/** 들고 있던 멤버 키를 버린다(로그아웃·키 교체·거절 뒤). */
export function forgetMemberKey(): void { viewSessionCache.purge(); credentials.forget(); }

/** 계정 모드의 실패 종류. 화면은 이것으로 가르고 영어 서버 문자열을 읽지 않는다.
 *  `signIn` = 키가 더는 통하지 않는다(로그인 시트) · `unreachable` = 계정·허브에 못
 *  닿았다(조용히 빈 결과로 떨어지지 않고 그 상태를 말한다). `body` 는 문구가 본문을 싣는 실패에만 넘긴다. */
function accountFailure(message: string, kind: AccountFailureKind,
                        status = 0, code = "unknown", body?: unknown): ApiError {
  const err = new ApiError(message, { status, code, account: kind, body });
  if (kind === "signIn") dispatchEvent(new CustomEvent("vinc:signin-required"));
  if (kind === "teamKey") dispatchEvent(new CustomEvent("vinc:team-key-required"));
  return err;
}

let accountSuccess = 0;
let accountProbe: { key: string; team: string; promise: Promise<void> } | null = null;

// 검색 한 번의 실패로 전체 화면을 끊지 않는다. 같은 그래프의 가벼운 읽기로 확인한다.
async function confirmAccountConnectivity(key: string, team: string): Promise<void> {
  if (accountProbe?.key === key && accountProbe.team === team) return accountProbe.promise;
  const before = accountSuccess;
  const promise = (async () => {
    const probe = await viaProxy(key, "GET", "/changes", {}, undefined, team).catch(() => null);
    // 모드·스페이스·키가 바뀌었거나 다른 호출이 성공했다면 늦은 실패로 배너를 띄우지 않는다.
    if (!accountMode() || cfg.team !== team || await memberKey(team).catch(() => "") !== key) return;
    if (probe && probe.status < 500) {
      dispatchEvent(new CustomEvent("vinc:account-reachable"));
    } else if (accountSuccess === before) {
      dispatchEvent(new CustomEvent("vinc:account-unreachable"));
    }
  })();
  accountProbe = { key, team, promise };
  try { await promise; }
  finally { if (accountProbe?.promise === promise) accountProbe = null; }
}

// 검색어·키·응답 본문은 남기지 않는다. 셸의 기존 Warn 로그 경로를 재사용한다.
async function logSearchFailure(status: number, duration: number): Promise<void> {
  const message = `GET /api/graph/search failed status=${status} duration_ms=${Math.round(duration)}`;
  console.warn(`[search] ${message}`);
  const invoke = await shellInvoke();
  if (invoke) await invoke("plugin:log|log", { level: 4, message, location: "search" })
    .catch(() => undefined);
}

/** 통과로를 한 번 태운다. 계정의 상태코드와 몸통이 그대로 돌아온다(`api/spaceproxy.py`). */
async function viaProxy(key: string, method: "GET" | "POST", path: string,
                        params: Record<string, string>, body: unknown,
                        team: string, signal?: AbortSignal): Promise<Response> {
  const started = performance.now();
  let response: Response;
  try {
    response = await fetch(new URL("/space/proxy", cfg.base), {
      method: "POST", signal,
      headers: {
        ...(cfg.token ? { Authorization: `Bearer ${cfg.token}` } : {}),
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        method, path, params, token: key,
        ...(method === "POST" ? { body } : {}),
        // 개인이면 싣지 않는다. 빈 문자열도 계정 쪽에서는 "팀을 말했다"로 읽힐 수 있다.
        ...(team ? { team } : {}),
      }),
    });
  } catch (failure) {
    if (path === "/search") await logSearchFailure(0, performance.now() - started);
    throw failure;
  }
  if (path === "/search" && !response.ok)
    await logSearchFailure(response.status, performance.now() - started);
  return response;
}

/** 셸 계정 모드의 데이터 호출. 경로는 웹과 같은 이름이고(`/view`·`/ingest/fragment`),
 *  사이드카 통과로가 `/api/graph` 접두를 붙여 계정으로 넘긴다. */
async function accountCall(path: string, params: Record<string, string | number | undefined> | undefined,
                           body: unknown, scope?: Scope, signal?: AbortSignal): Promise<any> {
  const team = scope ? scope.team || "" : cfg.team;
  const key = await memberKey(team).catch(error => {
    if (error?.message === "personal_key_required") throw accountFailure("sign in required", "signIn", 401);
    if (team) throw accountFailure(String(error?.message || "team key required"), "teamKey", 401);
    throw error;
  });
  if (!key) throw accountFailure(team ? "team key required" : "sign in required", team ? "teamKey" : "signIn", 401);
  // 팀을 묶인 팀 키로 여는가, 계정이 허락해 개인 키로 여는가. 거절됐을 때 다시 받을 키가 갈린다.
  const bound = !!team && key !== await credentials.personal().catch(() => "");
  const q: Record<string, string> = {};
  for (const [k, v] of Object.entries(params || {}))
    if (v !== undefined && v !== "") q[k] = String(v);
  const method = body !== undefined ? "POST" : "GET";
  let res: Response;
  try {
    res = await viaProxy(key, method, path, q, body, team, signal);
  } catch (e: any) {
    // 호출자가 접었다. 엔진 문제가 아니므로 그대로 올려 보낸다.
    if (signal?.aborted) throw e;
    // 사이드카 자체에 못 닿았다. 계정 문제가 아니라 이 기기의 엔진 문제다.
    throw new Error(`engine unreachable: ${messageOf(e)}`);
  }
  if (res.ok) {
    accountSuccess++;
    if (method === "POST") announceWrite(path, body);
    return res.json();
  }
  const b: any = await res.json().catch(() => ({}));
  // 계정의 오류 봉투는 `{error:{code,message}}`, 사이드카와 통과로는 `{error:"..."}`(api-error.ts).
  const { code, detail, reason } = parseEnvelope(b);
  if (res.status === 401 && code === "unauthorized") {
    // 사이드카 세션 토큰이 틀렸다(셸이 준 값). 로그인과는 무관하다.
    throw accountFailure("engine session rejected", "other", res.status, code);
  }
  if (code === "account_unreachable" || res.status === 502 || res.status === 504) {
    // 검색은 자동 재시도까지 끝낸 뒤 확인한다. 다른 읽기·쓰기는 재전송하지 않는다.
    if (path !== "/search") await confirmAccountConnectivity(key, team);
    throw accountFailure(detail || "account unreachable", "unreachable", res.status, code);
  }
  if (res.status === 401 && code === "not_authenticated") {
    // 팀 키가 거절됐거나, 계정이 개인 키로 팀을 여는 것을 막았다(엄격 모드): 팀 키를 받는다.
    // 개인 키 자체가 거절된 것이면 아래에서 로그인을 다시 받을지 가린다.
    if (team && (reason === "key_graph_scope_mismatch" || (bound && reason === "invalid_key"))) {
      forgetMemberKey();
      throw accountFailure(detail, "teamKey", 401, code);
    }
    // 계정은 **키 거절**과 **권한 거절**(팀 역할·읽기 전용 키)을 같은 코드로 준다.
    // 둘을 가르지 않으면 팀에서 권한 없는 일을 한 번 눌렀다고 로그아웃된다. 그래서
    // 키만 보는 가벼운 읽기(`/spaces`)로 한 번 더 묻는다: 그것도 거절이면 키가 죽은 것이다.
    const probe = await viaProxy(key, "GET", "/spaces", {}, undefined, "").catch(() => null);
    if (probe && probe.status === 401) {
      forgetMemberKey();
      throw accountFailure(detail, bound ? "teamKey" : "signIn", 401, code);
    }
    throw accountFailure(detail, "other", 401, code);
  }
  throw accountFailure(`${res.status} ${detail || JSON.stringify(b)}`, "other", res.status, code, detail ? undefined : b);
}

async function call(path: string, params?: Record<string, string | number | undefined>,
                    body?: unknown, scope?: Scope): Promise<any> {
  if (accountMode() && !sidecarOnly(path)) return accountCall(path, params, body, scope);
  return localCall(path, params, body, scope);
}


async function searchCall(query: string, top_k: number, options: SearchOptions = {}): Promise<any> {
  const scope = { team: cfg.team || null };
  const mode = dataMode.get();
  for (let attempt = 0; ; attempt++) {
    const started = performance.now();
    try {
      const result = await call("/search", { query, top_k, ...options,
        historical: options.historical === undefined ? undefined : String(options.historical) }, undefined, scope);
      // 오류 봉투가 HTTP 200 으로 와도 빈 결과로 읽지 않는다.
      if (result?.error || result?.executed === false) {
        if (accountMode()) await logSearchFailure(200, performance.now() - started);
        throw accountFailure("search response rejected", "other", 200, "search_rejected");
      }
      return result;
    }
    catch (failure: any) {
      const status = Number(failure?.status) || 0;
      // 멱등 읽기인 검색만 5xx 에 한 번 더 묻는다. 4xx·네트워크 실패·쓰기는 반복하지 않는다.
      if (attempt === 0 && status >= 500 && status < 600 && dataMode.get() === mode
          && cfg.team === (scope.team || "")) continue;
      if (failure?.account === "unreachable" && accountMode()) {
        const key = await memberKey(scope.team || "").catch(() => "");
        if (key && dataMode.get() === mode && cfg.team === (scope.team || ""))
          await confirmAccountConnectivity(key, scope.team || "");
      }
      throw failure;
    }
  }
}

/** 계정 모드와 무관하게 이 기기의 사이드카(웹이면 계정 프록시)로 간다. 모드 전환이 쓴다:
 *  계정 모드에서 도는 옮기기 잡의 진행(`/jobs/`)은 계정이 아니라 사이드카에 있다. */
async function localCall(path: string, params?: Record<string, string | number | undefined>,
                         body?: unknown, scope?: Scope, signal?: AbortSignal): Promise<any> {
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
    signal,
    method: isWrite ? "POST" : "GET",
    // 웹: 계정 세션 쿠키를 실어 보낸다(같은 오리진). 셸: 쿠키 없음.
    ...(WEB ? { credentials: "include" as const } : {}),
    ...(WEB && path === "/changes" ? { cache: "no-store" as const } : {}),
    headers: {
      // 셸: 세션 토큰(Bearer). 웹: 쿠키 세션이라 Bearer 불필요, 쓰기엔 CSRF 더블서브밋.
      ...(!WEB && cfg.token ? { Authorization: `Bearer ${cfg.token}` } : {}),
      ...(isWrite ? { "Content-Type": "application/json" } : {}),
      ...(WEB && isWrite ? { "X-CSRF-Token": csrfToken() } : {}),
    },
    body: isWrite ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    if (res.status === 401 || res.status === 403) viewSessionCache.purge();
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
    // 실패의 모양(503 의 storeDown · reason 과 그 밖의 code · body)은 api-error.ts 가 한곳에서 짓는다.
    throw localFailure(res.status, await res.text());
  }
  if (isWrite) announceWrite(path, body);
  return res.json();
}

/** 쓰기가 끝났다는 신호(`vinc:write`). 듣는 쪽은 View 의 메모리 보관본이다
 *  (`view-session-cache.ts`). 데이터 평면 셋(로컬 사이드카·셸 계정 모드·웹)이 같은 신호를
 *  낸다: 여태 로컬 쓰기만 알려서 계정 모드의 쓰기 뒤에는 보관본이 남았다.
 *  미리보기(`dry_run`)는 쓰기가 아니다. `/sync*`·`/connect/*` 는 기계 쪽 호출이라 뺀다. */
function announceWrite(path: string, body: unknown): void {
  if ((body as { dry_run?: unknown } | null)?.dry_run === true) return;
  if (path.startsWith("/sync") || path.startsWith("/connect/")) return;
  dispatchEvent(new CustomEvent("vinc:write"));
}

// 엔드포인트 표(`api.ts`)가 부르는 것.
export { CSRF_COOKIE, csrfToken, webFetch, sidecarOnly, accountCall, call, searchCall, localCall };
