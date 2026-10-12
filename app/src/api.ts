import { viewSessionCache } from "./view-session-cache";
/** Sidecar client — data plane is localhost HTTP + session token (control will
 *  be Tauri IPC when the shell lands, P4). Graph/query logic never lives here. */
import { chunkPack, type StageCounts } from "./packchunks";

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

// ── 데스크톱 데이터 모드(VIN-128) ──────────────────────────────────────────
// 셸은 두 모드 중 하나다. **계정 모드**면 화면이 읽고 쓰는 그래프가 계정 그래프이고
// (웹과 같은 `/api/graph/*`), 사이드카는 그 호출을 계정으로 넘기는 통과로일 뿐이다
// (`/space/proxy`, 키는 바디로만). **로컬 모드**면 종전대로 이 기기의 스토어다.
// 계정 모드가 기본이지만, 로컬 스토어에 이미 데이터가 있는 설치는 이행 흐름(VIN-131)이
// 생기기 전까지 로컬로 남는다: 빈 계정 화면이 기존 작업을 가리면 그게 곧 유실처럼 보인다.
// 근거: [[decision:the-desktop-signs-in-and-shows-the-account-graph-live-and-local-mode-is-a-setting]]
const MODE_KEY = "vinc.mode";
export type DataMode = "account" | "local";

export const dataMode = {
  /** 정해졌으면 그 값, 아직이면 null(부팅이 `decideDataMode` 로 한 번 정한다). */
  get(): DataMode | null {
    const v = localStorage.getItem(MODE_KEY);
    return v === "account" || v === "local" ? v : null;
  },
  set(v: DataMode) { viewSessionCache.clear(); localStorage.setItem(MODE_KEY, v); },
};

/** 셸이고 계정 모드인가. 모드는 로그인 뒤에 바뀔 수 있어서 상수가 아니라 함수다. */
export function accountMode(): boolean {
  return IN_SHELL && dataMode.get() === "account";
}

/** 화면이 보는 그래프가 이 기기 밖(계정)에 있는가 — 웹이거나 셸의 계정 모드.
 *  원격이라서 달라지는 것(본문 4MB 분할·가져오기 한도·어휘 재가 화면·샘플·동기화 UI)은
 *  이것으로 가른다. 쿠키·CSRF·`/login` 이동처럼 **브라우저라서** 달라지는 것은 `WEB` 그대로. */
export function remote(): boolean {
  return WEB || accountMode();
}

/** __Host-csrf(JS-readable) 쿠키 값 — 웹모드 쓰기의 CSRF 더블서브밋 헤더용. */
function csrfToken(): string {
  const m = document.cookie.match(/(?:^|;\s*)__Host-csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]!) : "";
}

export const cfg = {
  // 기본 = 셸에서는 고정 사이드카 주소, 브라우저에서는 same-origin(vite dev proxy 경유).
  get base(): string { return localStorage.getItem(BASE_KEY) || DEFAULT_BASE; },
  set base(v: string) { viewSessionCache.clear(); localStorage.setItem(BASE_KEY, v); },
  get token(): string { return localStorage.getItem(TOKEN_KEY) || ""; },
  set token(v: string) { viewSessionCache.clear(); localStorage.setItem(TOKEN_KEY, v); },
  get owner(): string { return localStorage.getItem(OWNER_KEY) || "tenant:local"; },
  set owner(v: string) { viewSessionCache.clear(); localStorage.setItem(OWNER_KEY, v); },
  // D42 — 표면 언어. 무설정 = **영어**. OS 로케일은 보지 않는다: 한국어는 설정에서
  // 사람이 고른 뒤에만 나온다([[decision:english-is-the-default-surface-language]]).
  // 덱 둘은 그대로 한 빌드에 실린다(D42). 고른 값은 LANG_KEY 에 남아 그대로 따라온다.
  get lang(): "en" | "ko" {
    const v = localStorage.getItem(LANG_KEY);
    if (v === "ko" || v === "en") return v;
    return "en";
  },
  set lang(v: "en" | "ko") { localStorage.setItem(LANG_KEY, v); },
  /** 현재 스페이스 — "" = 개인, 그 외 = 팀 publicId. 그래프가 계정에 있을 때만
   *  의미가 있다(웹, 셸의 계정 모드). 로컬 모드에서 남은 값이 조용히 따라다니지 않도록
   *  getter 에서 `remote()` 를 함께 본다. */
  get team(): string { return remote() ? localStorage.getItem(TEAM_KEY) || "" : ""; },
  set team(v: string) {
    viewSessionCache.clear();
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
export let sidecarDiag = "(connectSidecar not run)";

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
    sidecarDiag = "not in shell (browser dev, web Vinc): sidecar connect skipped";
    return;
  }
  try {
    const c = await invoke("sidecar_conn") as { port?: number; token?: string };
    if (c?.port) cfg.base = `http://127.0.0.1:${c.port}`;
    if (c?.token) cfg.token = c.token;
    sidecarDiag = `v${buildId} conn.port=${c?.port ?? "?"} base=${cfg.base} `
      + `token=${c?.token ? c.token.length + "ch" : "MISSING"}`;
  } catch (e: any) {
    sidecarDiag = `invoke sidecar_conn threw: ${e?.message || e}`;
  }
  // 사이드카 기동 대기 ≤ ~12s — PyInstaller 단일파일은 첫 실행에 압축 해제가 있어
  // (특히 macOS 콜드 스타트) 5s 로는 정상 기동도 오판할 수 있다.
  if (await waitForSidecar(48)) return;
  // 실패하면 "왜"를 반드시 싣는다 — 셸이 모아둔 사이드카 수명 이벤트(종료코드·
  // 시그널·stderr)를 붙여, health TIMEOUT 이 원인 불명으로 끝나지 않게 한다.
  let why = "";
  try { why = await invoke("sidecar_status") as string; } catch (e: any) { why = `sidecar_status failed: ${e?.message || e}`; }
  sidecarDiag += ` · health TIMEOUT · sidecar[${why}]`;
}

/** 로그인 직전에 사이드카가 답하는지 본다. 셸이 "끝났다"고 알면 다시 띄운다.
 *
 *  로그인은 사이드카 루프백으로 키를 받는다. 기동 중에 죽은 사이드카(스토어 열기가 엔진을
 *  죽인 경우)를 두고는 단추를 몇 번 눌러도 같은 실패만 반복됐다. 아직 뜨는 중이면 죽이지
 *  않고 기다린다: 로그인 화면은 사이드카를 기다리지 않고 먼저 뜨므로 바로 누를 수 있다.
 *  근거: [[record:a-wal-that-crashes-the-engine-takes-the-desktop-sign-in-down-2026-09-27]] */
export async function ensureSidecar(): Promise<boolean> {
  const invoke = await shellInvoke();
  if (!invoke) return true;                       // 웹·브라우저 dev: 셸 사이드카가 없다
  if (await waitForSidecar(4)) return true;
  const alive = await invoke("sidecar_alive").then(Boolean).catch(() => true);
  if (!alive) {
    try { await invoke("sidecar_restart"); } catch { return false; }
  }
  // 남은 WAL 을 점검하면 자식 프로세스가 두 번 뜰 수 있다(store/wal_guard.py). 1분까지 본다.
  return waitForSidecar(240);
}

/** `/health?live=1` 를 최대 `tries * 250ms` 기다린다. 응답과 본문이 끝나면 true. */
async function waitForSidecar(tries: number): Promise<boolean> {
  // 요청·본문 읽기도 대기 상한 안에 둔다. 응답하지 않는 연결이 폴더 전환을 붙잡지 않는다.
  // 근거: [[record:sidecar-health-wait-deadline-restored-2026-10-09]]
  const deadline = performance.now() + tries * 250;
  while (performance.now() < deadline) {
    try {
      const res = await fetch(new URL("/health?live=1", cfg.base), {
        signal: AbortSignal.timeout(Math.max(1, Math.ceil(deadline - performance.now()))),
      });
      if (res.ok) {
        // 사이드카가 떴어도 스토어가 성치 않으면(구버전 잔존 스토어의 프로비저닝
        // 실패 등) status=degraded 로 온다 — "ok" 로 뭉개면 뒤따르는 전 조회가
        // 원인 불명으로 깨진다. 사유를 진단에 그대로 싣는다.
        const h = await res.json().catch(() => ({} as any));
        if (performance.now() >= deadline) return false;
        sidecarDiag += h?.status === "degraded"
          ? ` · health DEGRADED[${h.provision_error || h.error || "?"}]`
          : " · health ok";
        if (h?.migrating) startIndexWatch();
        return true;
      }
    } catch {}
    const remaining = deadline - performance.now();
    if (remaining > 0) await new Promise((r) => setTimeout(r, Math.min(250, remaining)));
  }
  return false;
}

// 검색 색인 이관(VIN-272). 사이드카는 업데이트 뒤 첫 기동에서 색인을 옮기고, 그동안 `/health`
// 는 스토어를 안 읽고 `migrating` 을 싣는다. 살아 있는 것이니 기다림을 끝내고, 화면에는 한 줄을
// 건다(`index-signals.ts`). 끝날 때까지 2초마다 다시 묻는다.
let migrating = false;

/** 사이드카가 지금 검색 색인을 옮기고 있는가(신호 모듈이 늦게 걸릴 때 읽는다). */
export function indexMigrating(): boolean {
  return migrating;
}

function startIndexWatch(): void {
  if (migrating) return;
  migrating = true;
  dispatchEvent(new CustomEvent("vinc:index-migrating"));
  const tick = async () => {
    let still = true;
    try {
      const res = await fetch(new URL("/health", cfg.base));
      if (res.ok) still = Boolean((await res.json().catch(() => ({} as any)))?.migrating);
    } catch {}
    if (still) {
      setTimeout(tick, 2000);
      return;
    }
    migrating = false;
    dispatchEvent(new CustomEvent("vinc:index-ready"));
  };
  setTimeout(tick, 2000);
}

/** 호출 하나의 스페이스를 명시적으로 못박는다. 생략하면 현재 스페이스(`cfg.team`)를
 *  따른다. `{ team: null }` 은 "현재가 팀이어도 이번 호출은 개인" 이라는 뜻이다 —
 *  스페이스를 건너다니는 공유/불러오기가 이걸 쓴다. */
export type Scope = { team: string | null };

// 계정 모드에서도 **사이드카가 직접** 답하는 경로. 기기 연결은 루프백 콜백이라 사이드카가
// 받아야 하고, 동기화·백업·헬스는 이 기기의 일이며, 통과로 자신을 통과로에 실을 수는 없다.
// 이 목록 밖의 데이터 호출은 전부 계정으로 간다.
const SIDECAR_ONLY = ["/health", "/connect/", "/sync", "/backup", "/space/", "/config",
                      "/shutdown"];
const sidecarOnly = (path: string): boolean =>
  SIDECAR_ONLY.some((p) => path === p || path.startsWith(p));

// 멤버 키 — OS 키체인에서 한 번 읽어 이 창이 사는 동안 들고 있는다. 호출마다 IPC 를
// 태우면 화면 하나에 수십 번이다. 계정이 키를 거절하거나 로그아웃하면 비운다.
let memberKeyCache = "";
async function memberKey(): Promise<string> {
  if (memberKeyCache) return memberKeyCache;
  // 동적 import: account-connect 가 이 모듈을 읽으므로 정적으로 쓰면 순환이다.
  const { accountKey } = await import("./account-connect");
  memberKeyCache = await accountKey().catch(() => "");
  return memberKeyCache;
}
/** 들고 있던 멤버 키를 버린다(로그아웃·키 교체·거절 뒤). */
export function forgetMemberKey(): void { viewSessionCache.purge(); memberKeyCache = ""; }

/** 계정 모드의 실패 종류. 화면은 이것으로 가르고 영어 서버 문자열을 읽지 않는다.
 *  `signIn` = 키가 더는 통하지 않는다(로그인 시트) · `unreachable` = 계정·허브에 못
 *  닿았다(조용히 빈 결과로 떨어지지 않고 그 상태를 말한다). */
function accountFailure(message: string, kind: "signIn" | "unreachable" | "other",
                        status = 0, code = "unknown"): Error {
  const err: any = new Error(message);
  err.account = kind;
  err.status = status;
  err.code = code;
  if (kind === "signIn") dispatchEvent(new CustomEvent("vinc:signin-required"));
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
    if (!accountMode() || cfg.team !== team || memberKeyCache !== key) return;
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
  const key = await memberKey();
  if (!key) throw accountFailure("sign in required", "signIn", 401);
  const q: Record<string, string> = {};
  for (const [k, v] of Object.entries(params || {}))
    if (v !== undefined && v !== "") q[k] = String(v);
  const team = scope ? scope.team || "" : cfg.team;
  const method = body !== undefined ? "POST" : "GET";
  let res: Response;
  try {
    res = await viaProxy(key, method, path, q, body, team, signal);
  } catch (e: any) {
    // 호출자가 접었다. 엔진 문제가 아니므로 그대로 올려 보낸다.
    if (signal?.aborted) throw e;
    // 사이드카 자체에 못 닿았다. 계정 문제가 아니라 이 기기의 엔진 문제다.
    throw new Error(`engine unreachable: ${e?.message || e}`);
  }
  if (res.ok) { accountSuccess++; return res.json(); }
  const b: any = await res.json().catch(() => ({}));
  // 계정의 오류 봉투는 `{error:{code,message}}`, 사이드카와 통과로는 `{error:"..."}`.
  const code = typeof b?.error === "object" ? String(b.error?.code || "") : String(b?.error || "");
  const detail = typeof b?.error === "object" ? String(b.error?.message || code) : code;
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
    // 계정은 **키 거절**과 **권한 거절**(팀 역할·읽기 전용 키)을 같은 코드로 준다.
    // 둘을 가르지 않으면 팀에서 권한 없는 일을 한 번 눌렀다고 로그아웃된다. 그래서
    // 키만 보는 가벼운 읽기(`/spaces`)로 한 번 더 묻는다: 그것도 거절이면 키가 죽은 것이다.
    const probe = await viaProxy(key, "GET", "/spaces", {}, undefined, "").catch(() => null);
    if (probe && probe.status === 401) {
      forgetMemberKey();
      throw accountFailure(detail, "signIn", 401, code);
    }
    throw accountFailure(detail, "other", 401, code);
  }
  throw accountFailure(`${res.status} ${detail || JSON.stringify(b)}`, "other", res.status, code);
}

async function call(path: string, params?: Record<string, string | number | undefined>,
                    body?: unknown, scope?: Scope): Promise<any> {
  if (accountMode() && !sidecarOnly(path)) return accountCall(path, params, body, scope);
  return localCall(path, params, body, scope);
}

async function searchCall(query: string, top_k: number): Promise<any> {
  const scope = { team: cfg.team || null };
  const mode = dataMode.get();
  for (let attempt = 0; ; attempt++) {
    const started = performance.now();
    try {
      const result = await call("/search", { query, top_k }, undefined, scope);
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
        const key = await memberKey();
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
    if (res.status === 503) {
      const b = await res.json().catch(() => ({} as any));
      const err: any = new Error(b?.open_error || b?.error || "no reachable store");
      err.storeDown = true;
      err.reason = String(b?.open_error || "");
      err.status = res.status;
      err.code = typeof b?.error === "object" ? b.error?.code : b?.error;
      throw err;
    }
    const text = await res.text();
    const err: any = new Error(`${res.status} ${text}`);
    err.status = res.status;
    try {
      const b = JSON.parse(text);
      err.code = typeof b?.error === "object" ? b.error?.code : b?.error;
    } catch { /* JSON 오류 봉투가 아니면 상태만 남긴다. */ }
    throw err;
  }
  // 자동 동기화 신호(§8) — 로컬 쓰기가 끝났다. 듣는 쪽이 없으면(웹·스케줄러 미마운트)
  // 비용이 0이다. `/sync*`·`/connect/*` 는 뺀다 — 안 그러면 동기화가 자기를 다시 부른다.
  if (isWrite && !WEB && !path.startsWith("/sync") && !path.startsWith("/connect/"))
    dispatchEvent(new CustomEvent("vinc:write"));
  return res.json();
}

/** 로컬 모드가 **화면에 보여 줄** 데이터가 이 기기 스토어에 있는가. 모드를 정할 때 묻는다.
 *
 *  🔴 `call` 이 아니라 `localCall` 이다. 창에 "account" 가 남아 있으면 `call` 은 계정으로
 *  가서 **계정의** 그래프를 센다. 여기서 묻는 것은 이 기기다.
 *
 *  묻는 것은 "디스크에 뭔가 있나"가 아니라 "로컬로 두면 그 작업이 보이나"다. 로컬에 남기는
 *  이유가 그것뿐이기 때문이다. 그래서 둘을 세지 않는다:
 *  - `user:local` — 옛 로컬 MCP 의 소유자다. 사이드카가 기동하며 `tenant:local` 로 접으므로
 *    (`api/owner_fold.py`, VIN-131) 여기 올 때쯤에는 남아 있지 않다. 남아 있다면 접기가
 *    실패한 것이고, 그 행은 로컬 모드 화면에도 안 보인다.
 *  - 스토어를 못 여는 기기(503, WAL 격리 뒤 스토어 없이 뜬 사이드카) — 로컬 모드가 보여
 *    줄 것이 없다.
 *  어느 쪽도 지우지 않는다: 폴더는 그대로이고, 같은 폴더로 로컬 모드를 켜면 다시 열린다.
 *  샘플 스페이스(`sample:demo`)는 세지 않는다. 그 밖의 실패는 **있다고 답한다** — 모를 때
 *  계정으로 넘기면 보이던 작업이 빈 계정 화면 뒤로 가려진다. */
export async function localStoreHasData(): Promise<boolean> {
  const owner = "tenant:local";
  try {
    const d = await localCall("/domains", { owner });
    if ((d?.rows || []).length) return true;
    const docs = await localCall("/docs", { owner, limit: 1, detail: "summary" });
    return Number(docs?.total_count ?? (docs?.docs || []).length) > 0;
  } catch (e: any) {
    return !e?.storeDown;
  }
}

/** 이 셸이 볼 그래프를 정한다. **정본은 사이드카의 저장 설정**(`~/.vinc/config.json`,
 *  VIN-130)이고 localStorage 는 그 사본이다(`accountMode()` 가 동기로 읽어야 해서).
 *  정본을 파일에 두는 이유: 설정 화면이 모드와 폴더를 한 번에 바꾸고, 사이드카와 로컬
 *  MCP 가 같은 파일을 읽는다. 창 저장소는 그 둘이 못 본다.
 *
 *  **사람이 고른 모드(`mode_chosen`)만 그대로 따른다.** 고른 적이 없으면(새 설치, 그리고
 *  0.9.4·0.9.5 가 이 기기에 데이터가 있다고 말없이 로컬로 정한 설치) 이 기기에 보여 줄
 *  작업이 있는지 본다. 있으면 `"ask"` 를 돌려주고, 부르는 쪽이 로컬 모드로 계속 가며 그렇다고
 *  한 번 알린다(`device-graph.ts`, 옮기기는 설정에만 있다). 계정의 빈 화면이 디스크의 작업을
 *  말없이 가리면 그게 곧 유실로 읽힌다. 없으면 계정이다: 적되 고른 것으로는 적지 않는다
 *  (나중에 이 기기에 작업이 생기면 그때 알린다).
 *  근거: [[vinc.plan.desktop-account-mode §8]] ·
 *  [[decision:after-sign-in-a-device-with-a-graph-stays-local-and-moving-is-only-in-settings-2026-09-29]] */
export async function decideDataMode(): Promise<DataMode | "ask"> {
  const conf = await storage.config().catch(() => null);
  const saved = conf?.mode;
  if (conf?.mode_chosen && (saved === "account" || saved === "local")) {
    dataMode.set(saved);
    return saved;
  }
  if (!conf) {
    // 설정을 못 읽는다(옛 엔진). 물어도 답을 적을 곳이 없으니 종전 규칙대로 간다.
    const held = dataMode.get();
    const next: DataMode = held ?? ((await localStoreHasData()) ? "local" : "account");
    dataMode.set(next);
    return next;
  }
  if (await localStoreHasData()) return "ask";
  dataMode.set("account");
  if (saved !== "account") await storage.setConfig({ mode: "account" }).catch(() => {});
  return "account";
}

// ── 저장 위치와 모드 전환(VIN-130) ──────────────────────────────────────────
// 전부 **이 기기의 사이드카**로 간다(`localCall`). 계정 모드에서 부르는 것들이라 `call` 로
// 보내면 계정으로 간다.
export type StorageConfig = {
  executed: boolean; mode: DataMode | null; mode_chosen: boolean;
  data_path: string | null; data_file: string | null;
  default_folder: string; suggested_folder: string; pinned: boolean;
  restart_needed: boolean; store_open: boolean;
};
export type FolderCheck = {
  path: string; ok: boolean; exists: boolean; has_store: boolean; store_bytes: number;
  writable: boolean; synced: string | null; network: boolean; risks: string[];
  problem: string | null;
};
export const storage = {
  config: (): Promise<StorageConfig> => localCall("/config"),
  inspect: (path: string): Promise<FolderCheck> => localCall("/config/inspect", { path }),
  /** 400·409 는 던지지 않고 그대로 준다: 화면이 사유(`error`·`inspect`)를 그려야 한다. */
  setConfig: async (body: { mode?: DataMode; mode_chosen?: boolean;
                            data_path?: string | null; accept_risk?: boolean }): Promise<any> => {
    try { return await localCall("/config", undefined, body); }
    catch (e: any) {
      const m = /^(400|409) (.*)$/s.exec(String(e?.message || ""));
      if (m) { try { return { status: Number(m[1]), ...JSON.parse(m[2]!) }; } catch {} }
      throw e;
    }
  },
  /** 이 기기의 모든 주제를 계정으로(202 잡). 이 스토어에는 아무것도 안 쓴다. */
  pushAll: (token: string) => localCall("/sync/push-all", undefined, { token }),
  /** 계정 그래프 전부를 이 스토어로(202 잡). 앱의 소유자로 받는다. */
  pullAll: (token: string, owner: string) =>
    localCall("/sync/pull-all", undefined, { token, owner }),
  job: (id: string) => localCall(`/jobs/${id}`),
};

/** 사이드카를 다시 띄운다(셸 `sidecar_restart`). 폴더를 바꾼 뒤에 부른다. 포트·토큰은
 *  그대로라 접속 정보를 다시 받을 필요가 없고, `/health` 가 답할 때까지만 기다린다. */
export async function restartSidecar(): Promise<boolean> {
  const invoke = await shellInvoke();
  if (!invoke) return false;
  await invoke("sidecar_restart");
  return waitForSidecar(120);   // ≤ 30초: 큰 스토어를 여는 데 걸리는 시간
}

/** 이 실패가 "스토어를 못 읽는다"인가. 화면은 이 경우에만 다른 말을 해야 한다 —
 *  나머지 실패는 다시 시도하면 되지만, 이건 **빈 결과와 구분되지 않아서** 위험하다. */
export function storeDownReason(e: unknown): string | null {
  const err = e as any;
  return err?.storeDown ? (err.reason || "") : null;
}

/** 이 `doc_get` 거절이 **쓰기 창·중간에 끊긴 쓰기**인가(P17, `api/docstore.py` get_doc).
 *  그 갈래만 응답에 청크 수를 단다 — 모르는 id·소유자 불일치·죽은 사이드카에는 없다.
 *  `error`·`hint` 는 영어 서버 문자열이라 화면에 쓰지 않고, 판별에만 쓰는 이 열을 본다.
 *
 *  ⚠ 응답은 **"잠시 뒤 낫는다"와 "크래시가 남겨 며칠 간다"를 못 가른다.** 둘이 같은
 *  행 수 불일치로 오고, 서버도 재시도 전에는 어느 쪽인지 알 근거가 없다(쓰기 창은
 *  다음 순간 닫히고, 잔해는 다음 re-put 까지 그대로다 — 디스크 위 모습이 같다).
 *  그래서 문구가 두 처방(잠시 뒤 다시 · 계속 이러면 전문 재저장)을 함께 말한다. */
export const docMidWrite = (d: { executed?: boolean; chunk_count?: number }): boolean =>
  d.executed === false && d.chunk_count !== undefined;

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
  id?: string;        // Preserve an upstream relation identity when supplied.
  source: string; target: string; type: string; cross_domain: boolean;
  role?: string;        // CONNECTS.role / ATTACHES_TO.type 질화자 (F2)
  confidence?: number;  // D43 — 0..1. 상류 미저작이면 키 자체가 없다
  evidence?: string;    // D43 — 근거 문장. 툴팁 전용
  route?: import("./view-route").ViewRoute;
  presentation?: boolean; // Topic membership guide, never a causal relation.
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
/** 좌측 트리의 노드 갈래 한 줄. `concepts` 가 세는 것과 **같은 집합**이다
 *  (Concept + Decision) — 머리가 말하는 수와 줄 수가 갈리면 안 된다. */
export type NavNode = { id: string; title: string; kind: string; depth: number };
/** `nodes` 는 `navTree(owner, {nodes:true})` 로 물었을 때만 온다. 기본 응답을
 *  무겁게 하지 않으려는 것이다 — 개념 제목은 문서보다 수가 훨씬 많고, 웹모드에서는
 *  이 응답이 계정 프록시를 건넌다. */
/** `count` 는 `kind` 로 물었을 때만 온다 - 그 주제에 있는 **그 종**의 수이고,
 *  곧 그 갈래의 줄 수다. `concepts` 는 뜻이 바뀌지 않는다(늘 개념 + 결정). */
export type NavGroup = { domain: string; concepts: number; count?: number;
                         docs: NavDoc[]; nodes?: NavNode[] };
export type NavTreeRes = {
  executed: boolean; note?: string;
  domains?: NavGroup[];
  unfiled?: { doc_id: string; title: string; updated_at: string }[];
  //: `kinds` 는 종별 수 - 레일이 읽는 유일한 집계. `kind` 는 이 응답이 어느 종으로
  //: 좁혀졌는지(안 좁혔으면 없다).
  counts?: { domains: number; docs: number; unfiled: number;
             kinds?: Record<string, number>; kind?: string };
};

export type PackProgress = { total: StageCounts; done: StageCounts };

/** 팩 적재 — 조각으로 나눠 **순서대로 하나씩** 보낸다.
 *
 *  나누는 이유는 허브의 본문 상한(4MB)이고, 순서와 단위는 `packchunks.ts` 가 정한다.
 *  병렬로 보내지 않는다: 허브 쓰기는 잡 큐 하나로 줄을 서므로(`api/server.py`
 *  `run_sync`) 빨라지지 않고, 기다리는 요청마다 계정 프록시의 45초 제한만 먼저 닳는다.
 *
 *  응답은 한 번에 보냈을 때와 **같은 모양**으로 합친다(`merged`·`id_conflicts`·
 *  `documents`) — 호출부가 조각을 몰라도 된다. 중간에 실패하면 던지되 `sent`(성공한
 *  조각 수)를 달아 둔다. 적재는 id 로 MERGE 하고 같은 문서는 건너뛰므로, 처음부터 다시
 *  보내면 들어간 것은 그대로 두고 나머지를 채운다. */
async function importPackInChunks(pack: any, owner: string, scope?: Scope,
                                  onStep?: (p: PackProgress) => void): Promise<any> {
  const { chunks, tooBig } = chunkPack(pack);
  const zero = (): StageCounts => ({ nodes: 0, edges: 0, documents: 0 });
  const total = zero(), done = zero();
  for (const c of chunks) total[c.stage] += c.count;
  const out: any = {
    executed: true, too_big: tooBig, id_conflicts: [] as string[],
    merged: { nodes: { created: 0, matched: 0 }, edges: { created: 0 } },
    documents: { applied: 0, skipped: 0, total: total.documents },
  };
  onStep?.({ total, done: { ...done } });
  let sent = 0;
  for (const c of chunks) {
    let r: any;
    try {
      r = await call("/ingest/pack", undefined, { pack: c.pack, owner }, scope);
    } catch (e) {
      if (e && typeof e === "object") (e as any).sent = sent;
      throw e;
    }
    const n = r?.merged?.nodes || {};
    out.merged.nodes.created += n.created || 0;
    out.merged.nodes.matched += n.matched || 0;
    out.merged.edges.created += r?.merged?.edges?.created || 0;
    out.id_conflicts.push(...(r?.id_conflicts || []));
    out.documents.applied += r?.documents?.applied || 0;
    out.documents.skipped += r?.documents?.skipped || 0;
    // 주제 상한 같은 거절은 오류가 아니라 응답으로 온다 — 그 뒤 조각은 보내지 않는다.
    if (r?.executed === false) return { ...out, executed: false, error: r.error, sent };
    sent++;
    done[c.stage] += c.count;
    onStep?.({ total, done: { ...done } });
  }
  return out;
}

/** 캐시의 데이터 경로를 고정한다. 식별값은 진단 로그에 쓰지 않는다. */
export function viewScope() {
  const owner = cfg.owner, team = cfg.team;
  return { owner, team, key: JSON.stringify([owner, team, cfg.base, dataMode.get(),
    WEB, cfg.token, WEB ? csrfToken() : ""]) };
}

/** 웹 재사용은 현재 계정 identity와 현재 Space 권한을 매번 다시 확인한다. */
export async function viewAccess(scope: ReturnType<typeof viewScope>, signal?: AbortSignal) {
  const [me, changes] = await Promise.all([
    fetch(new URL("/api/me", location.origin), { credentials: "include", cache: "no-store", signal }),
    localCall("/changes", { owner: scope.owner }, undefined, { team: scope.team || null }, signal),
  ]);
  if (!me.ok) { viewSessionCache.purge(); throw new Error("View access unavailable"); }
  const user = (await me.json()).user;
  const identity = typeof user?.email === "string" ? user.email : "";
  const marker = typeof changes?.marker === "string" ? changes.marker : "";
  if (!identity || !marker || scope.key !== viewScope().key || signal?.aborted) return null;
  return { session: JSON.stringify([scope.key, identity]), revision: marker };
}

export const api = {
  roleRoster: async (owner: string) => {
    const scope = { team: cfg.team || null };
    const nodes = new Map<string, any>();
    const edges: GraphEdge[] = [];
    let offset = 0;
    for (;;) {
      const page = await call("/roles", { owner, roster: 1, limit: 100, offset }, undefined, scope);
      if (!page?.executed || !Array.isArray(page.roles) || !Array.isArray(page.relations))
        throw new Error(page?.error || "role roster unavailable");
      for (const r of page.roles) nodes.set(r.id, { ...r, label: r.title, kind: "concept" });
      for (const e of page.relations) edges.push({ ...e, source: e.from, target: e.to });
      if (page.next_offset == null) break;
      if (!Number.isInteger(page.next_offset) || page.next_offset <= offset)
        throw new Error("invalid role roster pagination");
      offset = page.next_offset;
    }
    return { nodes: [...nodes.values()], edges };
  },
  health: () => call("/health"),
  /** 변경 표식(VIN-129). 값은 비교만 한다: 같으면 그대로, 다르면 다시 읽는다(`live.ts`). */
  changes: (owner: string): Promise<{ marker?: string }> => call("/changes", { owner }),
  viewL0: (owner: string) => call("/view", { owner }),
  //: `domains` 는 **주제 여럿**(쉼표 결합). 스코프는 하위 트리 포함이다 —
  //: `domains=vinc` 하나면 `vinc/design` 까지 함께 온다(api/query.normalize_domains).
  graph: (owner: string, opts: { domain?: string; domains?: string[]; seed?: string;
                                 depth?: number; layout?: string;
                                 //: 원뿔 반각 배율(hub 3D). 1 이 기본이라 그때는 안 싣는다 —
                                 //: 안 싣는 요청이 여태와 같은 URL 이어야 캐시가 갈리지 않는다.
                                 cone?: number } = {}) => {
    const { domains, ...rest } = opts;
    return call("/graph", { owner, ...rest,
                            ...(domains?.length ? { domains: domains.join(",") } : {}) });
  },
  // Initial View read in stages. Capture Space and cancellation for both stages and fallback.
  // 웹은 계정 프록시(`/api/graph`)로, 데스크톱 계정 모드는 사이드카 통과로로, 로컬 모드는
  // 이 기기 사이드카로 간다. 세 길 모두 같은 `/graph` 핸들러에 `stage`·`snapshot` 이 닿는다.
  graphStage: (owner: string, opts: { domains?: string[]; layout?: string; cone?: number },
      stage: "preview" | "complete" | "cancel" | undefined, snapshot: string | undefined,
      scope: Scope, signal: AbortSignal) => {
    const { domains, ...rest } = opts;
    const params = { owner, ...rest, stage, snapshot,
      ...(domains?.length ? { domains: domains.join(",") } : {}) };
    // 취소 요청은 접힌 신호에 묶지 않는다: 그 신호가 접혔기 때문에 보내는 것이다.
    const live = stage === "cancel" ? undefined : signal;
    return accountMode() && !sidecarOnly("/graph")
      ? accountCall("/graph", params, undefined, scope, live)
      : localCall("/graph", params, undefined, scope, live);
  },
  recall: (owner: string, limit = 50) => call("/recall", { owner, limit, rank: "preference" }),
  search: (query: string, top_k = 20) => searchCall(query, top_k),
  // 정확한 제목은 저장된 메타에서 찾는다. 본문 색인·그래프 노드 유무와 무관하다.
  docTitleMatches: (title: string): Promise<{ docs?: { doc_id: string; title: string }[] }> =>
    call("/docs", { owner: cfg.owner, title, detail: "summary", limit: 8 }),
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
  nodeContext: (owner: string, node: string, limit = 4, scope?: Scope): Promise<{
    executed: boolean; title?: string; one_liner?: string; arm?: string; note?: string;
    props?: Record<string, unknown>;
    // 속성 편집이 읽는다: 무슨 종인가(props 를 싣는가) · 어느 판을 읽었는가(W3).
    // 구 사이드카엔 키가 없다 — 없으면 편집 단추를 내지 않고, 판은 0 으로 본다.
    node_kind?: string; rev?: number;
    excerpts?: { doc_id: string; doc_title: string; section: string;
                 excerpt: string; method: string }[];
    links?: NodeLinks;
  }> => call("/node-context", { owner, node, limit }, undefined, scope),
  // D45-V2 2단계 — Reader 서브뷰: 정본 전문(무손실 복원 + 해시 검증) + 밑줄 범위.
  docGet: (owner: string, id: string, scope?: Scope): Promise<{
    executed: boolean; text?: string; title?: string; verified?: boolean;
    content_hash?: string; error?: string;
    // 쓰기 창·중단된 쓰기의 거절 표식(P17). 이 갈래에만 실린다 — `docMidWrite` 참조.
    chunk_count?: number;
    // D72 — 최초 기록 시각 · 내용 시점. 구 사이드카에는 키 자체가 없다.
    created_at?: string; content_time?: string;
  }> => call("/doc", { owner, id }, undefined, scope),
  docHighlights: (owner: string, doc_id: string, concept_id: string): Promise<{
    executed: boolean; ranges?: { char_start: number; char_end: number }[];
  }> => call("/doc/highlights", { owner, doc_id, concept_id }),
  // 리더 머리 — 이 문서가 **무엇에 걸려 있고**(개념 칩: 문서→노드, 여태 없던 방향)
  // **어떤 모양인가**(목차), 그리고 검색의 청크 히트가 착지할 오프셋. 세 필드가 전부
  // 옵셔널인 것은 구 사이드카 강등 규약 — 없으면 그 줄을 안 그린다.
  docContext: (owner: string, doc_id: string): Promise<{
    executed: boolean;
    concepts?: { id: string; title: string; domain: string; count: number }[];
    sections?: { title: string; level: number; char_start: number }[];
    chunks?: { index: number; char_start: number }[];
  }> => call("/doc/context", { owner, doc_id }),
  // D45-E — 앱 내 SSoT 편집(MVP): 저장(전체 재청킹, 재임베딩은 지연) + 개념 추출.
  // origin 은 출처 표시(파일 import 시 파일명) — 서버의 doc 정체성 산식에 참여하고
  // 재드롭 식별의 근거가 된다. 조인 키가 아니며 UI 에도 노출하지 않는다(D45).
  // contentTime(D72): **키가 없을 때와 빈 값일 때가 다르다.** undefined 면 보내지
  // 않아 서버가 기존 값을 보존하고, "" 는 사용자가 지운 것이다(그래서 falsy 를
  // 통째로 걸러내는 spread 관용구를 쓰지 않는다).
  // topic: 문서의 주제를 **적는다**(미러 노드 `concept:doc.<id>` 의 domain). 주면 첫 저장에
  // 미러가 생겨 트리와 지도에 곧바로 선다(`docstore.put_doc`). 빈 값은 싣지 않는다 —
  // 서버는 빈 주제를 "지운다"가 아니라 "안 건드린다"로 읽는다.
  docPut: (owner: string, text: string, id?: string, title?: string,
           contentTime?: string, origin?: string, topic?: string): Promise<{
    executed: boolean; skipped?: boolean; doc_id?: string; content_hash?: string;
    chunks?: number; needs_embed?: boolean; content_time?: string; error?: string;
    created?: boolean; topic?: string;
  }> => call("/doc/put", undefined, { owner, text, ...(id ? { id } : {}),
                                      ...(title === undefined ? {} : { title: title.trim() }),
                                      ...(contentTime === undefined ? {} : { content_time: contentTime }),
                                      ...(origin ? { origin } : {}),
                                      ...(topic ? { topic } : {}) }),
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
  // 재임베딩 큐에 되돌릴 뿐이다(벡터는 워커가 채운다). queued = 되돌린 문서 수.
  docReembed: (owner: string, id: string): Promise<{ executed: boolean; queued?: number }> =>
    call("/doc/reembed", undefined, { owner, id }),
  // U2 — 관계 근거 저작(정본 청크에서 결정론 추출). 반복 실행 안전(있는 근거는 보존).
  evidenceBackfill: (owner: string, docId?: string): Promise<{
    executed: boolean; filled?: number; scanned?: number;
  }> => call("/evidence/backfill", undefined, { owner, ...(docId ? { doc_id: docId } : {}) }),
  // candidates: 반영할 목록을 고른다(사람이 체크한 것만). 없으면 서버가 결정론 추출 전량.
  // id 는 서버가 제목 slug 로 다시 짓는다 — 여기서 id 를 실어 보내지 않는다.
  docExtract: (owner: string, id: string, apply = false,
               candidates?: { title: string; one_liner?: string; source?: string;
                 candidate_id?: string; doc_id?: string; source_hash?: string;
                 spans?: { char_start: number; char_end: number; text: string }[];
                 extractor?: { name: string; version: string; runtime: string }; label?: string }[],
               approvedCandidateIds?: string[]): Promise<{
    executed: boolean; candidates?: { title: string; one_liner?: string; slug?: string;
      source?: string; candidate_id?: string; doc_id?: string; source_hash?: string;
      spans?: { char_start: number; char_end: number; text: string }[];
      extractor?: { name: string; version: string; runtime: string }; label?: string;
      evidence?: string; offset_unit?: string }[];
    applied?: { created: number; matched: number }; mentions?: number;
    domain?: string; error?: string;
  }> => call("/doc/extract", undefined, { owner, id, apply,
                                          ...(candidates ? { candidates } : {}),
                                          ...(approvedCandidateIds === undefined ? {} :
                                            { approved_candidate_ids: approvedCandidateIds }) }),
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
  //: `kind` 는 모집단을 그 종 **하나**로 좁힌다(종 레일). 목록을 종별로 가져가는
  //: 것이 계약이다 - 전부를 한 번에 싣는 값은 없고, 레일의 개수는 응답의
  //: `counts.kinds` 가 따로 준다(제목을 싣지 않는 집계).
  //: `domain` 은 그 주제의 **하위 트리**로 좁힌다. 이 값이 없던 동안 레일의 Agent
  //: 갈래는 주제를 선언만 하고 실어 보낼 자리가 없어 **모든 개념**을 세웠다.
  navTree: (owner: string, opts?: { nodes?: boolean; kind?: string; domain?: string;
                                     domains?: string[] },
           ): Promise<NavTreeRes> =>
    call("/nav-tree", { owner, ...(opts?.nodes ? { nodes: 1 } : {}),
                        ...(opts?.kind ? { kind: opts.kind } : {}),
                        ...(opts?.domain ? { domain: opts.domain } : {}),
                        // 하위 트리 여럿의 합(Agent 레일: 역할 주제 둘). `/graph` 와 같은 쉼표 꼴.
                        ...(opts?.domains?.length ? { domains: opts.domains.join(",") } : {}) }),
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
  importPack: importPackInChunks,
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
  // `deleted` 는 수가 아니라 `{edges: n}` 이다(`query.delete_edge`). 지울 수 없는 관계
  // 종류(부착 등)는 executed:true 에 `note` 만 싣고 `deleted` 가 없다.
  deleteEdge: (owner: string, from: string, to: string, type: string): Promise<{
    executed: boolean; found?: boolean; deleted?: { edges: number }; note?: string; error?: string;
  }> => call("/delete-edge", undefined, { owner, from, to, type }),
  docDelete: (owner: string, id: string, scope?: Scope): Promise<{
    executed: boolean; deleted_meta?: number; deleted_chunks?: number; error?: string;
  }> => call("/doc/delete", undefined, { owner, id }, scope),

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
  list: async (options?: { throwOnError?: boolean }): Promise<Array<{ id: string; name: string; role: string; status: string }>> => {
    // 셸 계정 모드: 쿠키가 없으니 `/api/teams` 는 못 부른다. 같은 목록을 계정의 그래프
    // 평면(`/api/graph/spaces`, 키로 열린다)에서 통과로로 받는다(VIN-127).
    try {
      if (accountMode()) {
        const data = await call("/spaces") as { teams?: unknown };
        if (!Array.isArray(data.teams)) throw new Error("team_list_unavailable");
        return data.teams;
      }
      if (!WEB) return [];
      const res = await fetch(new URL("/api/teams", location.origin), { credentials: "include" });
      if (!res.ok) throw new Error("team_list_unavailable");
      const data = await res.json() as { teams?: unknown };
      if (!Array.isArray(data.teams)) throw new Error("team_list_unavailable");
      return data.teams;
    } catch (error) {
      if (options?.throwOnError) throw error;
      return [];
    }
  },
};

// ── 주소의 스페이스 ─────────────────────────────────────────────────────────
// 노드 주소는 자기 스페이스를 말한다: 해시 앞 쿼리 `?space=`. 값은 셋 중 하나다 —
// `personal` · 팀 id · `local`(이 기기에만 있는 그래프, 로컬 모드). 그래서 채팅에
// 붙인 주소 하나로 어시스턴트가 스페이스를 두드려 보지 않고 곧장 간다(허브가 같은
// 쿼리를 읽는다, `vinc_mcp/viewurl.space_of`).
// 근거: [[decision:a-node-address-names-its-space]]

/** 지금 보고 있는 그래프의 주소용 이름. */
export function spaceName(): string {
  if (!remote()) return "local";
  return cfg.team || "personal";
}

// 주소창의 `?space=` 가 **우리가 쓴 것인지** 가리는 표식. 스페이스 전환·모드 전환·
// 로그아웃은 상태를 바꾼 뒤 새로고침하는데, 주소창에는 옛 `?space=` 가 남아 있다.
// 그걸 밖에서 온 링크로 읽으면 방금 바꾼 스페이스를 도로 되돌린다. 탭마다 따로인
// sessionStorage 라서 새 탭에 붙인 링크는 표식이 없고 그대로 따른다.
const SPACE_MARK = "vinc.space.written";

/** 주소창의 `?space=` 를 지금 스페이스로 맞춘다 — 주소창에서 복사해도 따라가게. */
export function writeSpaceToAddress(): void {
  const q = new URLSearchParams(location.search);
  const name = spaceName();
  q.set("space", name);
  try { sessionStorage.setItem(SPACE_MARK, name); } catch {}
  history.replaceState(history.state, "",
    location.pathname + "?" + q.toString() + location.hash);
}

/** 부팅에서 첫 화면 전에 한 번. 주소가 들고 온 스페이스로 연다.
 *  못 열면 그 이유("member" | "local")를, 아니면 ""를 돌려준다 — 화면이 알린다.
 *  멤버인지는 팀 목록으로 본다: 모르는 팀을 그냥 넣으면 모든 조회가 거절되고
 *  그 값이 localStorage 에 남아 다음 실행도 망가진다. */
export async function adoptSpaceFromAddress(): Promise<"" | "member" | "local"> {
  const raw = (new URLSearchParams(location.search).get("space") || "").trim().toLowerCase();
  let mark = "";
  try { mark = sessionStorage.getItem(SPACE_MARK) || ""; } catch {}
  let refused: "" | "member" | "local" = "";
  // 로컬 모드는 고를 스페이스가 없다(셸은 밖에서 오는 링크도 받지 않는다).
  if (raw && raw !== mark && remote() && raw !== spaceName().toLowerCase()) {
    if (raw === "personal") cfg.team = "";
    else if (raw === "local") refused = "local";
    else {
      const rows = await teamsApi.list();
      const hit = rows.find((t) => t.status === "active" && t.id.toLowerCase() === raw);
      if (hit) cfg.team = hit.id;
      else refused = "member";
    }
  }
  writeSpaceToAddress();
  return refused;
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
                             { intervalMs = 900, timeoutMs = 300000,
                               status = api.jobStatus } = {}): Promise<any> {
  const t0 = Date.now();
  for (;;) {
    const s = await status(jobId);
    onTick?.(s);
    if (s.status === "done") return s;
    if (s.status === "error") throw new Error(s.error || "sync job failed");
    if (Date.now() - t0 > timeoutMs) throw new Error("sync timed out");
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}
