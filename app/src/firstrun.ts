/** 첫 실행 — 계정을 **묻되 막지 않는다**.
 *
 *  여태 데스크탑은 계정이 있는 줄도 모르게 시작됐다(Settings 안 탭 하나). 그래서
 *  다른 기기에서 잇거나 claude.ai 에서 쓰는 길이 있는 사람도 그 길을 못 찾았다.
 *  반대편 극단인 "가입해야 쓸 수 있음"은 택하지 않았다: 로컬 stdio 서버는 사용자
 *  기기에서 사용자 자신의 DB 를 여는 프로세스라 계정 검사가 강제되지 않고, 오프라인
 *  로컬이라는 제품의 주장과도 부딪친다. 그래서 **갈림길**이다.
 *  근거: [[decision:account-is-a-path-not-a-wall]]
 *
 *  ⚠ 한 번만 뜬다. 넘긴 사람에게 다시 묻지 않는다 — 다시 묻는 것이 곧 벽이다.
 */
import { COPY } from "./copy";
import { openSheet } from "./components";
import { hasAccount, connectAccount, abortConnect } from "./account-connect";

const SEEN = "vinc.firstrun.seen";

/** 이 기기에서 첫 실행 카드를 보여야 하는가. */
export function shouldOfferAccount(): boolean {
  return !localStorage.getItem(SEEN) && !hasAccount();
}

export function offerAccount(): void {
  if (!shouldOfferAccount()) return;
  // 카드를 **띄우는 순간** 표시한다. 여기서 죽어도 다음 실행에 또 뜨지는 않게.
  localStorage.setItem(SEEN, "1");

  const c = COPY.firstRun;
  const sheet = openSheet({
    title: c.title,
    subtitle: c.lead,
    onClose: () => abortConnect(),   // 기다리는 중에 닫으면 폴링도 끝낸다
  });
  sheet.body.innerHTML = `
    <ul class="fr-why">${c.reasons.map((r) => `<li>${r}</li>`).join("")}</ul>
    <p class="hint">${c.offlineNote}</p>
    <div id="fr-state" class="hint hint-sm"></div>`;
  sheet.foot.innerHTML = `
    <button class="btn" id="fr-skip">${c.offlineCta}</button>
    <button class="btn btn-primary" id="fr-signin">${c.signInCta}</button>`;

  const state = sheet.body.querySelector<HTMLElement>("#fr-state")!;
  sheet.foot.querySelector("#fr-skip")!.addEventListener("click", () => sheet.close());
  sheet.foot.querySelector("#fr-signin")!.addEventListener("click", async () => {
    state.textContent = c.opening;
    const r = await connectAccount();
    if (r === "connected") { sheet.close(); return; }
    if (r === "aborted") return;            // 사용자가 닫았다 — 시트는 이미 없다
    state.textContent = r === "expired" ? c.expired : c.failed;
  });
}
