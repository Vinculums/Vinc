/** 계정 연결(디바이스 플로우) 한 벌.
 *
 *  여태 이 흐름은 Settings 의 Account 탭 안 클로저였다. 계정을 화면에서 **보이는
 *  길**로 올리면서 부르는 자리가 셋이 됐고(첫 실행 카드 · 원격 커넥터 행 · 기존
 *  Account 탭), 클로저인 채로 두면 셋이 각자 폴링 루프를 갖는다. 하나로 모으면
 *  중단 신호도 하나다.
 *
 *  ⚠ 앱은 계정 API 를 직접 부르지 않는다. 브라우저가 승인하고 사이드카 루프백으로
 *  되돌아온 키를 폴링으로 **한 번** 수령해 이 기기에만 둔다. 계정 모드의 데이터 호출도
 *  이 키를 사이드카 통과로에 실어 보낼 뿐 계정을 직접 부르지 않는다(`api.ts` accountCall).
 *
 *  데스크톱은 로그인해야 쓴다(2026-09-24, `firstrun.ts`). 이 모듈은 그 로그인의 한 벌이다.
 */
import { cfg, forgetMemberKey, shellInvoke } from "./api";

const K_KEY = "vinc.sync.key";
const K_MAIL = "vinc.sync.email";
const K_USER = "vinc.sync.username";
/** "이 기기에 키가 있다" 표식(비밀 아님). OS 저장소로 옮긴 뒤에는 `K_KEY` 가
 *  localStorage 에서 사라지므로, 이것이 없으면 **연결 여부를 동기적으로 알 길이
 *  없다.** 디바이스 플로우는 이메일·핸들이 남아 가려졌지만 수동 키 경로는 그 둘도
 *  안 써서 로그인해도 화면이 영영 "미연결"이었다. */
const K_LINKED = "vinc.sync.linked";

let aborted = false;

/** OS 비밀 저장소를 쓸 수 있는가. `null` = 아직 안 물어봤다. */
let secretOk: boolean | null = null;

/** 셸이고 저장소가 실제로 되는가. 한 번만 묻고 기억한다(프로브가 쓰고 지운다). */
async function secretStore(): Promise<((c: string, a?: any) => Promise<any>) | null> {
  const invoke = await shellInvoke();
  if (!invoke) return null;                       // 웹·브라우저 dev — 셸이 없다
  if (secretOk === null) {
    secretOk = await invoke("secret_available").then(Boolean).catch(() => false);
  }
  return secretOk ? invoke : null;
}

/** 이 기기에 OS 저장소가 없어 키가 앱 프로파일에 눕는가. 설정 화면이 이걸 적는다. */
export function keyIsOnDisk(): boolean {
  return secretOk === false && !!(localStorage.getItem(K_KEY) || "").trim();
}

/** 계정 키. OS 저장소가 되면 거기서, 아니면 이전 자리에서.
 *
 *  ⚠ 이행: 예전 값은 `localStorage` 에 있다. 처음 읽을 때 옮기고 **원래 자리를 지운다**
 *  — 옮기기만 하고 안 지우면 평문 사본이 그대로 남아 이 작업이 아무것도 안 한 것이 된다. */
export async function accountKey(): Promise<string> {
  const store = await secretStore();
  const legacy = (localStorage.getItem(K_KEY) || "").trim();
  if (!store) return legacy;
  const held = await store("secret_get", { name: K_KEY }).catch(() => null);
  if (typeof held === "string" && held.trim()) {
    if (legacy) localStorage.removeItem(K_KEY);   // 늦게 발견한 잔재도 치운다
    return held.trim();
  }
  if (legacy) {
    await store("secret_set", { name: K_KEY, value: legacy }).catch(() => null);
    localStorage.removeItem(K_KEY);
    return legacy;
  }
  return "";
}

/** 손으로 붙여넣은 키를 보관한다(폴백 경로 · Advanced 탭). */
export async function setAccountKey(value: string): Promise<void> {
  return storeKey(value);
}

async function storeKey(value: string): Promise<void> {
  // 표식이 먼저다. 아래에서 어느 분기를 타든 "키가 있다"는 사실은 같고, OS 저장소
  // 쓰기가 실패해도(catch 로 삼킨다) 화면은 연결됨으로 그려져야 한다 — 안 그러면
  // 사용자는 키를 넣고도 미연결 화면을 보며 같은 일을 반복한다.
  localStorage.setItem(K_LINKED, "1");
  // 들고 있던 옛 키를 버린다. 계정 모드는 키를 메모리에 캐시하므로(`api.ts`), 여기서
  // 안 비우면 키를 바꿔도 이 창이 닫힐 때까지 옛 키로 계정을 부른다.
  forgetMemberKey();
  const store = await secretStore();
  if (store) {
    await store("secret_set", { name: K_KEY, value }).catch(() => null);
    localStorage.removeItem(K_KEY);
  } else {
    localStorage.setItem(K_KEY, value);           // 저장소 없는 기기 — 화면이 이를 알린다
  }
  // 계정이 방금 붙었다. 디바이스 플로우와 수동 붙여넣기가 **둘 다 여기로** 모이므로
  // 신호는 이 한 곳이면 된다 — 부르는 자리 셋에 각자 걸면 하나를 빠뜨린다. 이벤트인
  // 이유는 스케줄러와의 import 순환을 피하려서다(스케줄러가 이 모듈을 읽는다).
  //
  // ⚠ 이른 return 을 if/else 로 편 것이 이 신호의 전제다. return 이 남아 있으면 OS
  // 저장소가 있는 기기(=대부분)에서 이 줄에 **영영 안 닿는다.**
  dispatchEvent(new CustomEvent("vinc:linked"));
}

/** 이 기기에 계정 키가 있는가.
 *
 *  ⚠ 동기 함수라 **OS 저장소를 못 본다.** 첫 실행 카드처럼 "묻기 전에 이미 로그인돼
 *  있나"를 빠르게 가르는 자리에만 쓴다. 실제로 키가 필요한 자리는 `accountKey()` 를
 *  기다릴 것 — 표식(`K_LINKED`)이 저장소와 무관하게 남아 있어 성립한다.
 *
 *  **연결 여부는 이 함수로 묻는다. `accountWho()` 로 묻지 마라** — 그쪽은 이름을
 *  모르면 null 이라, 이름 없는 연결(수동 키)을 미연결로 읽는다. */
export function hasAccount(): boolean {
  return !!(localStorage.getItem(K_LINKED) || localStorage.getItem(K_KEY)
            || localStorage.getItem(K_USER) || localStorage.getItem(K_MAIL) || "");
}

/** 표시용 이름(핸들 → 이메일 → 키 앞부분). **모르면 null — 미연결이라는 뜻이 아니다.**
 *  수동으로 붙여넣은 키는 누구 것인지 알 길이 없어 여기가 null 이 된다. */
export function accountWho(): string | null {
  // 표시용 이름은 **비밀이 아니라서** 이전 자리에 남는다. 키가 OS 저장소로 옮겨진
  // 뒤에도 화면이 "누구로 로그인했는지"를 동기적으로 그릴 수 있어야 한다.
  const username = localStorage.getItem(K_USER) || "";
  const email = localStorage.getItem(K_MAIL) || "";
  const key = (localStorage.getItem(K_KEY) || "").trim();
  if (!username && !email && !key) return null;
  return username ? `@${username}` : (email || key.slice(0, 12) + "…");
}

/** 이 기기에서 키를 지운다(폐기는 아니다 — 계정 쪽 키 관리에서 한다). */
export function forgetAccount(): void {
  aborted = true;
  forgetMemberKey();
  localStorage.removeItem(K_LINKED);
  localStorage.removeItem(K_KEY);
  localStorage.removeItem(K_MAIL);
  localStorage.removeItem(K_USER);
  // OS 저장소 쪽도 지운다. 여기서 기다리지 않는 이유는 화면이 즉시 다시 그려져야
  // 하기 때문이고, 실패해도 표시상 로그아웃은 이미 참이다(키만 남는다 — 그 경우
  // 다음 로그인이 덮는다).
  void secretStore().then((s) => s && s("secret_delete", { name: K_KEY })).catch(() => {});
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
        await storeKey(st.key);
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
