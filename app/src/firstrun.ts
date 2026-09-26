/** 첫 화면 — 데스크톱은 로그인이 먼저다(VIN-128).
 *
 *  여태 이 자리는 "로그인 / 오프라인으로 계속" 갈림길이었고 한 번만 물었다. 2026-09-24
 *  발주자 결정으로 뒤집혔다: 데스크톱은 로그인해야 쓰고, 기본으로 웹과 같은 계정 그래프를
 *  보며, 이 기기에만 두는 것은 설정의 로컬 모드다. 그래서 오프라인 버튼이 없고, 로그인이
 *  끝날 때까지 이 화면이 닫히지 않는다.
 *  근거: [[decision:the-desktop-signs-in-and-shows-the-account-graph-live-and-local-mode-is-a-setting]]
 *
 *  **창 전체가 로그인이다**(2026-09-26). 여태는 빈 앱 셸 위에 닫기 없는 시트(모달)가
 *  떴다 — 앱을 쓰다 잠깐 묻는 모양인데, 실제로는 로그인 전에 쓸 수 있는 것이 없다.
 *  그래서 웹 로그인 페이지(`Vinc-homepage/site/login`)와 같은 결의 풀화면으로 바꿨다:
 *  가운데 카드 하나, 로고 · 제목 · 이유 · 로그인 단추. 타이틀바는 덮지 않는다(창을
 *  옮기고 닫는 손잡이가 거기 있다). 뒤의 셸은 `inert` 로 잠가 포커스가 새지 않는다.
 *  근거: [[decision:the-desktop-sign-in-fills-the-window-instead-of-a-modal-2026-09-26]]
 *
 *  ⚠ 이 게이트는 **통제가 아니다.** 로컬 프로세스에 둔 검사는 지울 수 있다(계정 정책
 *  §3.3 의 기준 그대로). 그래서 여기서 막는 것은 제품의 모양이지 권한이 아니고, 권한은
 *  여전히 계정과 허브가 판정한다.
 *
 *  ⚠ 로그인은 사이드카 루프백으로 키를 받는다(`account-connect`). 그러니 이 화면은
 *  `connectSidecar` 뒤에 떠야 한다. 오프라인이면 브라우저 승인까지 갈 수 없고, 화면은
 *  실패를 말한 채 다시 시도를 기다린다. 첫 실행이 오프라인이면 못 쓰는 것은 결정의 비용이다.
 */
import { COPY } from "./copy";
import { hasAccount, connectAccount } from "./account-connect";
import { IN_SHELL, WEB } from "./api";

/** 이 기기에서 로그인이 필요한가. 셸만 해당한다(웹은 세션 게이트가 따로 있다).
 *
 *  ⚠ 동기 판정이라 OS 키체인을 못 본다(`hasAccount` 주석). 키를 넣으면 비밀이 아닌
 *  표식이 함께 남으므로 이것으로 충분하다. 표식이 있는데 키가 죽었으면 첫 계정 호출이
 *  `vinc:signin-required` 를 내고, 그때 같은 화면이 다시 뜬다. */
export function needsSignIn(): boolean {
  // 브라우저 dev(셸 아님)는 개발자의 자리라 막지 않는다. 다만 이 화면을 화면 보드로
  // 캡처해야 하므로(`design-hub/scripts/capture_app.mjs` 의 first-run 상태) dev 에서
  // 이 표식이 있으면 셸처럼 띄운다. 웹 Vinc 는 세션 게이트가 따로 있어 해당 없다.
  const devShow = !IN_SHELL && !WEB && localStorage.getItem("vinc.dev.signin") === "1";
  return (IN_SHELL || devShow) && !hasAccount();
}

let pending: Promise<void> | null = null;

/** 풀화면 로그인을 그린다. 닫을 문이 없다 — 로그인해야 쓰는 앱에서 닫히는 로그인은
 *  빈 화면으로 가는 문이다. 돌려주는 `close` 는 로그인이 끝났을 때만 부른다. */
function openSignInScreen(reason: "first" | "expired") {
  const c = COPY.firstRun;
  const shell = document.getElementById("shell");
  const screen = document.createElement("section");
  screen.id = "signin-screen";
  screen.setAttribute("role", "dialog");
  screen.setAttribute("aria-modal", "true");
  screen.setAttribute("aria-labelledby", "signin-title");
  screen.innerHTML = `
    <div class="si-card">
      <span class="brand-logo si-mark" role="img" aria-label="Vinc"></span>
      <h1 class="si-title" id="signin-title">${c.title}</h1>
      <p class="si-lead">${reason === "expired" ? c.leadExpired : c.lead}</p>
      <ul class="fr-why">${c.reasons.map((r) => `<li>${r}</li>`).join("")}</ul>
      <button class="btn btn-primary si-cta" id="fr-signin" type="button">${c.signInCta}</button>
      <p class="si-state" id="fr-state" role="status" aria-live="polite"></p>
      <p class="hint si-note">${c.localNote}</p>
    </div>`;
  // 뒤의 셸을 잠근다 — 탭 키가 보이지 않는 앱 손잡이로 새지 않게.
  shell?.setAttribute("inert", "");
  document.body.append(screen);
  const btn = screen.querySelector<HTMLButtonElement>("#fr-signin")!;
  btn.focus();
  return {
    btn,
    state: screen.querySelector<HTMLElement>("#fr-state")!,
    close() { screen.remove(); shell?.removeAttribute("inert"); },
  };
}

/** 로그인이 끝날 때까지 기다린다. 이미 화면이 떠 있으면 그 약속을 같이 기다린다
 *  (부팅과 만료 신호가 겹쳐도 화면은 하나다). */
export function requireSignIn(reason: "first" | "expired" = "first"): Promise<void> {
  if (pending) return pending;
  pending = new Promise<void>((resolve) => {
    const c = COPY.firstRun;
    const { btn, state, close } = openSignInScreen(reason);
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      state.textContent = c.opening;
      const r = await connectAccount();
      btn.disabled = false;
      if (r === "connected") {
        pending = null;
        close();
        resolve();
        return;
      }
      if (r === "aborted") { state.textContent = ""; return; }
      // 실패해도 화면은 남는다. 같은 단추가 다시 시도다.
      state.textContent = r === "expired" ? c.expired : c.failed;
    });
  });
  return pending;
}
