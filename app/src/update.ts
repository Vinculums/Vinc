// Vinc 자동 업데이트 (v0.3.0) — Tauri v2 updater.
//
// 셸에서만 동작한다: 웹 Vinc(vincs.io)·브라우저 dev 에는 __TAURI__ 가 없어
// 전 경로가 no-op 다(플러그인 import 도 셸일 때만 발화). 기동 직후 조용히
// 확인하고, **있을 때만** 하단 토스트로 알린다 — 확인 실패(오프라인·매니페스트
// 부재)는 무음이다. 앱 사용을 절대 막지 않는다(비차단: "나중에" 가 기본값).
//
// 서명 검증은 여기서 하지 않는다 — updater 플러그인이 tauri.conf.json 의 pubkey
// 로 다운로드 아티팩트 서명을 강제한다. CI 개인키(TAURI_SIGNING_PRIVATE_KEY)로
// 서명되지 않은 번들은 설치 자체가 거부된다(중간자 방지).
import { COPY } from "./copy";
import { loaderInline } from "./vinc-loaders.js";

const IN_SHELL =
  location.protocol === "tauri:" || location.hostname === "tauri.localhost";

type Relaunch = () => Promise<void>;

/** 기동 시 1회 호출(비차단). 셸이 아니거나 최신이면 아무것도 하지 않는다. */
export async function checkForUpdate(): Promise<void> {
  if (!IN_SHELL) return; // 웹 Vinc·브라우저 dev = no-op

  let updater: typeof import("@tauri-apps/plugin-updater");
  let process: typeof import("@tauri-apps/plugin-process");
  try {
    // 동적 import: 웹 번들이 이 코드를 평가하지 않게(셸 전용 코드 분리).
    updater = await import("@tauri-apps/plugin-updater");
    process = await import("@tauri-apps/plugin-process");
  } catch {
    return; // 플러그인 미탑재 빌드 = 조용히 패스
  }

  let update: Awaited<ReturnType<typeof updater.check>>;
  try {
    update = await updater.check();
  } catch {
    return; // 네트워크·매니페스트 실패 = 무음(앱은 정상 동작)
  }
  if (!update) return; // 이미 최신

  showToast(update, process.relaunch);
}

/** 업데이트 발견 → 하단 토스트. 설치는 사용자 클릭 시에만. */
function showToast(
  update: { version: string; downloadAndInstall: (cb?: (e: DownloadEvent) => void) => Promise<void> },
  relaunch: Relaunch,
): void {
  const t = COPY.ui.update;
  const el = document.createElement("div");
  el.className = "update-toast";
  el.setAttribute("role", "status");
  el.innerHTML = `
    <span class="update-msg">${t.available(update.version)}</span>
    <span class="update-actions">
      <button class="btn" data-act="later">${t.later}</button>
      <button class="btn btn-primary" data-act="install">${t.install}</button>
    </span>`;
  document.body.appendChild(el);

  const msg = el.querySelector<HTMLElement>(".update-msg")!;
  const actions = el.querySelector<HTMLElement>(".update-actions")!;
  // 등장이 있으면 퇴장도 있어야 한다(뚝 끊기지 않게). 감소 모션이면 바로 지운다.
  const dismiss = () => {
    if (!el.animate || window.matchMedia("(prefers-reduced-motion: reduce)").matches) { el.remove(); return; }
    const a = el.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 140, easing: "ease-out", fill: "forwards" });
    void a.finished.then(() => el.remove()).catch(() => el.remove());
  };

  el.querySelector<HTMLButtonElement>('[data-act="later"]')!
    .addEventListener("click", dismiss);

  el.querySelector<HTMLButtonElement>('[data-act="install"]')!
    .addEventListener("click", async () => {
      actions.remove(); // 진행 중엔 버튼 숨김(중복 클릭 방지)
      msg.innerHTML = loaderInline(t.downloading);
      try {
        let total = 0;
        let got = 0;
        await update.downloadAndInstall((e) => {
          if (e.event === "Started") {
            total = e.data.contentLength ?? 0;
          } else if (e.event === "Progress") {
            got += e.data.chunkLength;
            msg.innerHTML = loaderInline(total > 0
              ? t.pct(Math.min(100, Math.round((got / total) * 100)))
              : t.downloading);
          } else if (e.event === "Finished") {
            msg.innerHTML = loaderInline(t.installing);
          }
        });
        await relaunch(); // 설치 완료 → 새 버전으로 재기동
      } catch {
        msg.textContent = t.failed;
        // 실패해도 앱은 그대로 — 잠시 뒤 토스트만 정리.
        setTimeout(dismiss, 6000);
      }
    });
}

type DownloadEvent =
  | { event: "Started"; data: { contentLength?: number } }
  | { event: "Progress"; data: { chunkLength: number } }
  | { event: "Finished"; data?: unknown };
