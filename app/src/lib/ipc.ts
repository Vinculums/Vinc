// 제어 평면(IPC) — Tauri Rust 코어의 #[command] 래퍼([[vinc.arch]] §2).
// 데이터가 아니라 창·세션·사이드카 수명주기·로컬 파일 다이얼로그만 다룬다.
//
// 브라우저(비-Tauri)에서 열리면 데모 모드로 강등한다 — 디자인 표면 검토(D-S01/D-S02)
// 를 Tauri 빌드 없이 돌릴 수 있게. 데모에서는 사이드카가 없으므로 api.ts 가 목 데이터를 낸다.

export type SidecarInfo = {
  host: string;
  port: number;
  token: string;
  driver?: string | null;
};

export const isTauri =
  typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

async function invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<T>(cmd, args);
}

/** 사이드카 기동(또는 기존 정보 반환). 데모 모드에선 합성 정보. */
export async function startSidecar(): Promise<SidecarInfo> {
  if (!isTauri) {
    return { host: "127.0.0.1", port: 0, token: "demo", driver: null };
  }
  return invoke<SidecarInfo>("start_sidecar");
}

export async function sidecarInfo(): Promise<SidecarInfo> {
  if (!isTauri) return { host: "127.0.0.1", port: 0, token: "demo", driver: null };
  return invoke<SidecarInfo>("sidecar_info");
}

/** 로컬 로그인(F4) — 패스프레이즈 검증. 데모 모드에선 비어있지 않으면 통과. */
export async function sessionLogin(passphrase: string, credPath: string): Promise<boolean> {
  if (!isTauri) return passphrase.trim().length > 0;
  return invoke<boolean>("session_login", { passphrase, credPath });
}

/** 온보딩(F4) — 로컬 자격 생성. */
export async function onboard(passphrase: string, credPath: string): Promise<boolean> {
  if (!isTauri) return passphrase.trim().length > 0;
  return invoke<boolean>("onboard", { passphrase, credPath });
}

export async function stopSidecar(): Promise<void> {
  if (!isTauri) return;
  return invoke<void>("stop_sidecar");
}

/** 로컬 디렉토리 선택(온보딩 데이터 디렉토리·적재 소스). Tauri dialog 플러그인. */
export async function pickDirectory(title?: string): Promise<string | null> {
  if (!isTauri) return "/데모/데이터-디렉토리";
  const { open } = await import("@tauri-apps/plugin-dialog");
  const picked = await open({ directory: true, multiple: false, title });
  return typeof picked === "string" ? picked : null;
}

/** 로컬 파일 선택(적재 소스). */
export async function pickFiles(title?: string): Promise<string[]> {
  if (!isTauri) return ["/데모/소스.jsonl"];
  const { open } = await import("@tauri-apps/plugin-dialog");
  const picked = await open({ directory: false, multiple: true, title });
  if (Array.isArray(picked)) return picked;
  return picked ? [picked] : [];
}
