// 앱의 설정: 저장 열쇠, 실행 자리(셸 · 웹 · 로컬 dev), 데이터 모드, 사이드카 주소 · 토큰 · 소유자 · 언어 · 스페이스(`cfg`),
// 셸의 `invoke`, 예외의 사유. 전송과 엔드포인트는 `api.ts` 가 이것을 읽어 쓴다. 이 파일은 `api.ts` 를 읽지 않는다.
import { viewSessionCache } from "./view-session-cache";
import { SELF_HOSTED_BUILD } from "./self-hosted";
import type { DataMode } from "./api-types";

const BASE_KEY = "vinc.sidecar.base";
const TOKEN_KEY = "vinc.sidecar.token";
const OWNER_KEY = "vinc.owner";
const LANG_KEY = "vinc.lang";
// 현재 스페이스. 빈 문자열 = 개인. 팀이면 팀 publicId 를 담는다(계정이 이 값을
// team:<uuid> graph_owner 로 바꿔 허브에 서명해 보낸다 — 클라는 owner 를 모른다).
const TEAM_KEY = "vinc.team";
// 그 스페이스의 표시 이름. 목록을 못 받아도 사이드바 머리가 지금 공간을 이름으로 말하게
// 고를 때 함께 남긴다. id 와 짝으로 담아서, 다른 경로로 `cfg.team` 이 바뀌면 이름은 따라오지 않는다.
const TEAM_NAME_KEY = "vinc.team.name";

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
// 셀프호스트 허브의 번들(`SELF_HOSTED_BUILD`)은 localhost 로 열어도 웹 Vinc 다: 허브가 같은
// 출처에서 `/login`·`/api/*` 를 답한다(`vinc_mcp/selfhost_web.py`).
export const WEB = !IN_SHELL && (SELF_HOSTED_BUILD
  || (location.hostname !== "localhost" && location.hostname !== "127.0.0.1"));

// ── 데스크톱 데이터 모드 ──────────────────────────────────────────────────
// 셸은 두 모드 중 하나다. **계정 모드**면 화면이 읽고 쓰는 그래프가 계정 그래프이고
// (웹과 같은 `/api/graph/*`), 사이드카는 그 호출을 계정으로 넘기는 통과로일 뿐이다
// (`/space/proxy`, 키는 바디로만). **로컬 모드**면 이 기기의 스토어다.
// 계정 모드가 기본이다. 모드를 고른 적 없는 설치에 로컬 작업이 있으면 로컬로 계속 가며
// 한 번 알리고, 옮기기는 설정에만 있다(`decideDataMode`): 빈 계정 화면이 기존 작업을
// 가리면 그게 곧 유실처럼 보인다.
// 근거: [[decision:the-desktop-signs-in-and-shows-the-account-graph-live-and-local-mode-is-a-setting]]
const MODE_KEY = "vinc.mode";

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
    if ((localStorage.getItem(TEAM_KEY) || "") !== v) localStorage.removeItem(TEAM_NAME_KEY);
    if (v) localStorage.setItem(TEAM_KEY, v);
    else localStorage.removeItem(TEAM_KEY);
  },
  /** 지금 스페이스(`team`)의 표시 이름. 남긴 이름이 다른 스페이스의 것이면 빈 문자열이다.
   *  개인은 이름을 남기지 않는다(화면이 "개인"이라고 부른다). */
  get teamName(): string {
    const team = this.team;
    if (!team) return "";
    try {
      const saved = JSON.parse(localStorage.getItem(TEAM_NAME_KEY) || "null") as { id?: unknown; name?: unknown } | null;
      return saved && saved.id === team && typeof saved.name === "string" ? saved.name : "";
    } catch { return ""; }
  },
  set teamName(v: string) {
    const team = this.team;
    if (team && v) localStorage.setItem(TEAM_NAME_KEY, JSON.stringify({ id: team, name: v }));
    else localStorage.removeItem(TEAM_NAME_KEY);
  },
};

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

/** 잡은 예외의 사람이 읽을 사유. `message` 가 비었으면 `fallback`, 그것도 안 주면
 *  값 자체를 문자열로 바꾼다(문자열을 던진 자리도 사유가 남는다). */
export function messageOf(e: unknown, fallback?: string): string {
  const m = (e as { message?: unknown } | null | undefined)?.message;
  if (m) return String(m);
  return fallback ?? String(e);
}
