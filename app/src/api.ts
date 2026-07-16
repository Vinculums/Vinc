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
const APP_V = typeof __APP_VERSION__ === "string" ? __APP_VERSION__ : "dev";
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
    sidecarDiag = `v${APP_V} conn.port=${c?.port ?? "?"} base=${cfg.base} `
      + `token=${c?.token ? c.token.length + "ch" : "MISSING"}`;
  } catch (e: any) {
    sidecarDiag = `invoke sidecar_conn 예외: ${e?.message || e}`;
  }
  for (let i = 0; i < 20; i++) {                     // 사이드카 기동 대기 ≤ ~5s
    try {
      if ((await fetch(new URL("/health", cfg.base))).ok) { sidecarDiag += " · health ok"; return; }
    } catch {}
    await new Promise((r) => setTimeout(r, 250));
  }
  sidecarDiag += " · health TIMEOUT";
}

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
