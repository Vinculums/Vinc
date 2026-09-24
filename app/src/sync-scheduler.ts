/** 자동 동기화 스케줄러(v0.8.7 §8) — 로컬 우선을 유지한 채 "동기화"라는 말을 화면에서
 *  지운다. 버튼도, 마지막 동기화 시각도, 상태 표시도 두지 않는다.
 *
 *  **왜 사이드카가 아니라 여기인가.** `/sync` 는 회원 키를 바디로만 받는다(D36,
 *  `api/server.py` submit_sync). 사이드카는 키를 갖지 않고 가져서도 안 되며, 키는 OS
 *  저장소에 있어 셸 프런트만 닿는다. 스케줄이 여기 사는 것은 취향이 아니라 제약이다.
 *
 *  ⚠ **동시 실행을 여기서 막지 않는다.** 사이드카가 `_sync_running` 으로 409 를 낸다
 *  (`api/server.py` submit_sync). 커서 경합은 거기서 이미 불변식이고, 클라에 같은
 *  가드를 두면 두 곳이 어긋나는 날 조용히 안 돈다. 409 는 다른 실패와 같이 삼킨다.
 *
 *  근거: [[record:figma-shrinks-the-divergence-window-it-does-not-remove-reconciliation]]
 */
import { api, cfg, remote } from "./api";
import { accountKey } from "./account-connect";

// 타이핑이 멎고 이만큼 뒤. 짧으면 글자마다 왕복하고, 길면 자동으로 안 느껴진다.
const WRITE_DEBOUNCE_MS = 20_000;
// 들어오는 쪽의 유일한 통로다 — 지속 연결이 없으니 이게 없으면 웹에서 쓴 것이
// 이 기기의 로컬 쓰기나 재시작 전까지 영영 안 온다.
const INBOUND_MS = 10 * 60_000;

let timer = 0;

/** 한 번 돈다. 실패는 조용하다 — 오프라인은 정상이지 오류가 아니다.
 *
 *  잡을 폴링하지 않는다(202 만 받고 놓는다). 그래서 **실패가 어디에도 안 남는다** —
 *  다음 신호가 다시 시도하고 apply 는 rev 가드 아래 멱등이라 감수한다. 실패를 보여야
 *  할 날이 오면 `pollJob` 을 걸고 그 결과를 어디에 그릴지부터 정할 것. */
async function run(): Promise<void> {
  const key = await accountKey().catch(() => "");
  if (!key) return;              // 미연결 기기 — 계정이 붙으면 vinc:linked 로 곧 돈다
  await api.syncNow(key, cfg.owner).catch(() => {});
}

/** `delay` 뒤에 한 번. 신호가 겹치면 마지막 것만 남는다. */
export function syncSoon(delay = WRITE_DEBOUNCE_MS): void {
  clearTimeout(timer);
  timer = window.setTimeout(() => void run(), delay);
}

/** 앱 시작 시 한 번 건다(셸·로컬 dev 전용). */
export function mountSyncScheduler(): void {
  // 웹 Vinc 와 셸 계정 모드는 계정 그래프를 직접 본다. 스토어가 하나뿐이라 화해할 것이
  // 없다(VIN-128). 로컬 모드의 동기화는 VIN-130 이 걷어 낸다.
  if (remote()) return;
  syncSoon(0);
  // 로컬 쓰기 — api.ts 가 낸다. 이벤트인 이유는 import 순환을 피하려서다.
  addEventListener("vinc:write", () => syncSoon());
  // 계정이 방금 붙었다 — 기다리게 하지 않는다(§9: 로그인이 눈에 보여야 한다).
  addEventListener("vinc:linked", () => syncSoon(0));
  // 창으로 돌아왔다. 셸에서 최소화가 이걸 반드시 내지는 않으므로 아래 주기가 보험이다.
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) syncSoon(0);
  });
  setInterval(() => syncSoon(0), INBOUND_MS);
}
