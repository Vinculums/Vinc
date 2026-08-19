/** 계정 연결(디바이스 플로우) 한 벌.
 *
 *  여태 이 흐름은 Settings 의 Account 탭 안 클로저였다. 계정을 화면에서 **보이는
 *  길**로 올리면서 부르는 자리가 셋이 됐고(첫 실행 카드 · 원격 커넥터 행 · 기존
 *  Account 탭), 클로저인 채로 두면 셋이 각자 폴링 루프를 갖는다. 하나로 모으면
 *  중단 신호도 하나다.
 *
 *  ⚠ 앱은 계정 API 를 직접 부르지 않는다(B1). 브라우저가 승인하고 사이드카
 *  루프백으로 되돌아온 키를 폴링으로 **한 번** 수령해 이 기기 localStorage 에만 둔다.
 *
 *  왜 계정을 강제하지 않고 길로 두는가: [[decision:account-is-a-path-not-a-wall]]
 */
import { cfg, shellInvoke } from "./api";

const K_KEY = "vinc.sync.key";
const K_MAIL = "vinc.sync.email";
const K_USER = "vinc.sync.username";

let aborted = false;

/** 이 기기에 계정 키가 있는가. */
export function hasAccount(): boolean {
  return !!(localStorage.getItem(K_KEY) || "").trim();
}

/** 표시용 이름(핸들 → 이메일 → 키 앞부분). 없으면 null. */
export function accountWho(): string | null {
  const key = (localStorage.getItem(K_KEY) || "").trim();
  if (!key) return null;
  const username = localStorage.getItem(K_USER) || "";
  const email = localStorage.getItem(K_MAIL) || "";
  return username ? `@${username}` : (email || key.slice(0, 12) + "…");
}

/** 이 기기에서 키를 지운다(폐기는 아니다 — 계정 쪽 키 관리에서 한다). */
export function forgetAccount(): void {
  aborted = true;
  localStorage.removeItem(K_KEY);
  localStorage.removeItem(K_MAIL);
  localStorage.removeItem(K_USER);
}

/** 진행 중인 폴링을 멈춘다(시트를 닫거나 화면을 다시 그릴 때). */
export function abortConnect(): void { aborted = true; }

export type ConnectResult = "connected" | "expired" | "aborted" | "failed";

/** 디바이스 플로우를 끝까지 돈다. `onState` 로 진행을 알린다(문구는 호출부가 정한다).
 *
 *  ⚠ 폴링 창은 상태 TTL(10분)과 같아야 한다. 짧으면 승인해도 못 받고, 길면 만료된
 *  상태를 계속 두드린다. */
export async function connectAccount(onState?: (s: "opening" | "waiting") => void): Promise<ConnectResult> {
  const { api } = await import("./api");
  try {
    const { state } = await api.connectStart();
    const port = new URL(cfg.base).port || "8787";
    const url = `https://vincs.io/connect-app/#port=${port}&state=${encodeURIComponent(state)}`;
    onState?.("opening");
    // 셸이면 시스템 브라우저로, 아니면 새 탭(웹 dev).
    const invoke = await shellInvoke();
    if (invoke) await invoke("plugin:shell|open", { path: url });
    else window.open(url, "_blank");
    onState?.("waiting");
    aborted = false;
    for (let i = 0; i < 300 && !aborted; i++) {   // 2s × 300 = TTL(10분)
      await new Promise((r) => setTimeout(r, 2000));
      const st = await api.connectStatus(state).catch(() => null);
      if (!st) continue;
      if (st.status === "delivered") {
        localStorage.setItem(K_KEY, st.key);
        if (st.email) localStorage.setItem(K_MAIL, st.email);
        if (st.username) localStorage.setItem(K_USER, st.username);
        else localStorage.removeItem(K_USER);   // 핸들 미설정 계정 → 이전 값 잔존 방지
        return "connected";
      }
      if (st.status === "expired") return "expired";
    }
    return aborted ? "aborted" : "failed";
  } catch {
    return "failed";
  }
}
