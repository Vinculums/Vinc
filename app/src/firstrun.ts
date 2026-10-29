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
 *  ⚠ 로그인은 사이드카 루프백으로 키를 받는다(`account-connect`). 화면은 사이드카를 안
 *  기다리고 먼저 뜨고, **단추가** 사이드카를 기다린다(죽었으면 다시 띄운다, `ensureSidecar`).
 *  오프라인이면 브라우저 승인까지 갈 수 없고, 화면은 실패를 말한 채 다시 시도를 기다린다.
 *  첫 실행이 오프라인이면 못 쓰는 것은 결정의 비용이다.
 */
import { COPY } from "./copy";
import { hasAccount, connectAccount, openBrowser } from "./account-connect";
import { IN_SHELL, WEB, ensureSidecar } from "./api";
import { loaderInline } from "./vinc-loaders.js";

/** 이 기기에서 로그인이 필요한가. 셸만 해당한다(웹은 세션 게이트가 따로 있다).
 *
 *  ⚠ 동기 판정이라 OS 키체인을 못 본다(`hasAccount` 주석). 키를 넣으면 비밀이 아닌
 *  표식이 함께 남으므로 이것으로 충분하다. 표식이 있는데 키가 죽었으면 첫 계정 호출이
 *  `vinc:signin-required` 를 내고, 그때 같은 화면이 다시 뜬다. */
export function needsSignIn(): boolean {
  // 브라우저 dev(셸 아님)는 개발자의 자리라 막지 않는다. 다만 이 화면을 화면 보드로
  // 캡처해야 하므로(`scripts/design/capture_app.mjs` 의 first-run 상태) dev 에서
  // 이 표식이 있으면 셸처럼 띄운다. 웹 Vinc 는 세션 게이트가 따로 있어 해당 없다.
  const devShow = !IN_SHELL && !WEB && localStorage.getItem("vinc.dev.signin") === "1";
  return (IN_SHELL || devShow) && !hasAccount();
}

let pending: Promise<void> | null = null;

/** 풀화면 로그인을 그린다. 닫을 문이 없다 — 로그인해야 쓰는 앱에서 닫히는 로그인은
 *  빈 화면으로 가는 문이다. 돌려주는 `close` 는 로그인이 끝났을 때만 부른다. */
// ds-allow: Google 로고는 Google 브랜드 지침이 정한 네 색 그대로여야 한다(앱 팔레트가 아니다). 웹 로그인과 같은 도형이다.
const GOOGLE_G = `<svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true"><path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/><path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/><path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/><path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg>`;

/** 풀화면 로그인을 그린다. 닫을 문이 없다 — 로그인해야 쓰는 앱에서 닫히는 로그인은
 *  빈 화면으로 가는 문이다. 돌려주는 `close` 는 로그인이 끝났을 때만 부른다.
 *
 *  단추는 웹 로그인 페이지와 같은 순서다: 이메일이 먼저, "또는", Google. 둘 다 브라우저를
 *  연다 — 이메일은 연결 페이지의 로그인 안내로, Google 은 그 안내를 건너뛰고 Google 로
 *  (`connectAccount` 의 `via`). 앱은 Google 이 켜져 있는지 모르므로 단추를 늘 보이고,
 *  꺼져 있으면 연결 페이지가 이메일 안내로 돌린다. */
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
      <div class="si-or" aria-hidden="true"><span>${c.or}</span></div>
      <button class="btn si-google" id="fr-google" type="button">${GOOGLE_G}<span>${c.googleCta}</span></button>
      <p class="si-state" id="fr-state" role="status" aria-live="polite"></p>
      <div class="si-progress" id="fr-progress" hidden aria-hidden="true">${loaderInline()}</div>
      <div class="si-actions" id="fr-actions" hidden>
        <button class="btn" id="fr-reopen" type="button" disabled>${c.reopen}</button>
        <button class="btn" id="fr-copy" type="button" disabled>${c.copyLink}</button>
        <button class="btn" id="fr-cancel" type="button">${c.cancel}</button>
      </div>
      <p class="hint si-note">${c.localNote}</p>
      <p class="hint si-note">${c.agreeNote(
        `<a href="https://vincs.io/terms/" target="_blank" rel="noopener noreferrer">${c.terms}</a>`,
        `<a href="https://vincs.io/privacy/" target="_blank" rel="noopener noreferrer">${c.privacy}</a>`)}</p>
    </div>`;
  // 뒤의 셸을 잠근다 — 탭 키가 보이지 않는 앱 손잡이로 새지 않게.
  shell?.setAttribute("inert", "");
  document.body.append(screen);
  const btn = screen.querySelector<HTMLButtonElement>("#fr-signin")!;
  btn.focus();
  return {
    buttons: [btn, screen.querySelector<HTMLButtonElement>("#fr-google")!] as const,
    state: screen.querySelector<HTMLElement>("#fr-state")!,
    progress: screen.querySelector<HTMLElement>("#fr-progress")!,
    actions: screen.querySelector<HTMLElement>("#fr-actions")!,
    cancel: screen.querySelector<HTMLButtonElement>("#fr-cancel")!,
    reopen: screen.querySelector<HTMLButtonElement>("#fr-reopen")!,
    copy: screen.querySelector<HTMLButtonElement>("#fr-copy")!,
    legal: Array.from(screen.querySelectorAll<HTMLAnchorElement>(".si-note a")),
    close() { screen.remove(); shell?.removeAttribute("inert"); },
  };
}

/** 로그인이 끝날 때까지 기다린다. 이미 화면이 떠 있으면 그 약속을 같이 기다린다
 *  (부팅과 만료 신호가 겹쳐도 화면은 하나다). */
export function requireSignIn(reason: "first" | "expired" = "first",
                              sidecar: Promise<unknown> = Promise.resolve()): Promise<void> {
  if (pending) return pending;
  pending = new Promise<void>((resolve) => {
    const c = COPY.firstRun;
    const { buttons, state, close, progress, actions, cancel, reopen, copy, legal } = openSignInScreen(reason);
    const [email, google] = buttons;
    let attempt: AbortController | null = null;
    let signInLink = "";
    const busy = (value: boolean) => {
      buttons.forEach(b => { b.disabled = value; });
      progress.hidden = actions.hidden = !value;
    };
    cancel.addEventListener("click", () => {
      attempt?.abort(); attempt = null; signInLink = "";
      busy(false); state.textContent = ""; email.focus();
    });
    reopen.addEventListener("click", () => {
      const current = attempt;
      if (signInLink) void openBrowser(signInLink).catch(() => {
        if (attempt === current) state.textContent = c.openFailed;
      });
    });
    copy.addEventListener("click", () => {
      const current = attempt, link = signInLink;
      if (link) void Promise.resolve().then(() => navigator.clipboard.writeText(link))
        .then(() => { if (attempt === current) state.textContent = c.copied; })
        .catch(() => { if (attempt === current) state.textContent = c.copyFailed; });
    });
    legal.forEach(a => a.addEventListener("click", event => {
      event.preventDefault();
      void openBrowser(a.href).catch(() => { state.textContent = c.openFailed; });
    }));
    const start = async (via?: "google") => {
      if (attempt) return;
      const current = new AbortController(); attempt = current;
      const active = () => attempt === current && !current.signal.aborted;
      signInLink = ""; reopen.disabled = copy.disabled = true;
      // 기다리는 동안은 둘 다 잠근다 — 흐름 두 개가 같은 사이드카 상태를 두고 겹치지 않게.
      busy(true);
      state.textContent = c.opening;
      // 키는 사이드카 루프백으로 온다. 화면은 사이드카보다 먼저 떴으므로 여기서 기다리고,
      // 사이드카가 끝났으면 다시 띄운다. 못 띄우면 인터넷 탓이 아니라고 말한다.
      const initialized = await sidecar.then(() => true, () => false);
      if (!active()) return;
      const ready = initialized && await ensureSidecar().catch(() => false);
      if (!active()) return;
      if (!ready) {
        attempt = null; busy(false);
        state.textContent = c.localDown;
        return;
      }
      const r = await connectAccount(s => {
        if (active()) state.textContent = s === "waiting" ? c.waiting : c.opening;
      }, { via, signal: current.signal, onLink: url => {
        if (!active()) return;
        signInLink = url; reopen.disabled = copy.disabled = false;
      } });
      if (!active()) return;
      attempt = null; signInLink = ""; busy(false);
      if (r === "connected") {
        pending = null;
        close();
        resolve();
        return;
      }
      if (r === "aborted") { state.textContent = ""; return; }
      // 실패해도 화면은 남는다. 같은 단추가 다시 시도다.
      state.textContent = r === "expired" ? c.expired : c.failed;
    };
    email.addEventListener("click", () => void start());
    google.addEventListener("click", () => void start("google"));
  });
  return pending;
}
