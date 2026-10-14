/** 계정 모드의 신호를 화면에 잇는다(VIN-128).
 *
 *  `api.ts` 가 이것을 이벤트로 내는 이유는 import 순환을 피하려서다(copy·components 가
 *  api 를 읽는다). 신호는 셋이다:
 *
 *    `vinc:signin-required`   계정이 이 기기의 키를 더는 받지 않는다 → 로그인 시트
 *    `vinc:account-unreachable` 계정이나 허브에 못 닿았다 → 화면 위 한 줄
 *    `vinc:account-reachable`   다시 닿았다(`live.ts`, VIN-129) → 그 줄을 거둔다
 *
 *  ⚠ 못 닿은 것을 **말한다.** 계정 모드에서 이 기기에는 그래프 사본이 없으므로, 말하지
 *  않으면 빈 화면이 "데이터가 없다"로 읽힌다. 그 오독이 곧 유실처럼 보인다. 로컬 스토어로
 *  조용히 물러서지도 않는다: 거기 있는 것은 계정 그래프가 아니다.
 */
import { COPY } from "./copy";
import { esc } from "./components";

let notice: HTMLElement | null = null;

function showUnreachable(): void {
  if (notice) return;
  const c = COPY.accountMode;
  notice = document.createElement("div");
  // 표면(`#surface`)은 이동마다 다시 그려지므로 그 밖에 붙인다.
  notice.className = "error-banner acct-notice";
  notice.setAttribute("role", "alert");
  notice.innerHTML = `<span>${esc(c.unreachable)}</span>
    <button class="btn" type="button">${esc(c.retry)}</button>`;
  // 다시 시도는 다시 그리기다. 어느 화면이 어떤 호출에서 실패했는지 여기서는 모른다.
  notice.querySelector("button")!.addEventListener("click", () => location.reload());
  document.body.appendChild(notice);
}

/** 다시 닿았다(`live.ts` 가 표식을 읽을 때마다 낸다). 허브는 짧게 끊겼다 돌아오는 일이
 *  잦다. 한 번 뜬 줄이 다시 시도를 누를 때까지 남으면, 멀쩡해진 뒤에도 고장으로 읽힌다. */
function hideUnreachable(): void {
  notice?.remove();
  notice = null;
}

/** 부팅 때 한 번 건다(셸 전용). */
export function mountAccountSignals(): void {
  addEventListener("vinc:account-unreachable", showUnreachable);
  addEventListener("vinc:account-reachable", hideUnreachable);
  addEventListener("vinc:team-key-required", () => {
    void import("./team-key").then(({ restoreTeamCredential }) => restoreTeamCredential());
  });
  addEventListener("vinc:signin-required", () => {
    void import("./firstrun")
      .then(({ requireSignIn }) => requireSignIn("expired"))
      // 새 키로 모든 표면을 처음부터. 부분 갱신하면 거절당한 호출의 빈 화면이 남는다.
      .then(() => location.reload());
  });
}
