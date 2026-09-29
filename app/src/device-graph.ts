/** 로그인 뒤 한 번 알리는 화면 — 이 컴퓨터에 이미 그래프가 있을 때(VIN-131).
 *
 *  데스크톱은 기본으로 계정 그래프를 본다. 그런데 로그인하기 전부터 이 기기에 쌓인 작업이
 *  있으면, 말없이 계정으로 가면 그 작업이 빈 계정 화면 뒤로 가려져 유실처럼 읽히고, 말없이
 *  로컬로 가면(0.9.4·0.9.5) 계정 그래프가 어디 갔는지 모른다. 그래서 **로컬 모드로 계속 가고,
 *  그렇다는 것과 바꾸는 자리를 한 번 알린다.** 부르는 조건은 `decideDataMode` 가 정한다(사람이
 *  고른 적이 없고, 이 기기에 보여 줄 작업이 있을 때).
 *
 *  ⚠ 여기서는 옮기지 않는다. 옮기기는 설정 › 계정·저장 › "그래프를 이 기기에 두기" 끄기
 *  판(`askTurnOff`)에만 있다: 거기서 옮기거나 옮기지 않고 끈다. 답은 사이드카 설정에
 *  `mode: "local", mode_chosen: true` 로 남아 다시 알리지 않는다. 창 저장소가 아니라 설정이
 *  정본이다(`decideDataMode` 주석).
 *
 *  근거: [[decision:after-sign-in-a-device-with-a-graph-stays-local-and-moving-is-only-in-settings-2026-09-29]]
 */
import { COPY } from "./copy";
import { dataMode, storage, type DataMode } from "./api";
import { esc } from "./components";

/** 확인을 받을 때까지 기다린다. 늘 로컬 모드를 돌려준다. 닫을 문은 확인 하나다: 설정에
 *  적기 전에 닫으면 다음 기동이 같은 알림을 다시 띄워야 하는데, 그걸 막을 길이 없다. */
export function tellAboutDeviceGraph(): Promise<DataMode> {
  return new Promise((resolve) => {
    const c = COPY.deviceGraph;
    const shell = document.getElementById("shell");
    const screen = document.createElement("section");
    screen.id = "device-graph-screen";
    screen.setAttribute("role", "dialog");
    screen.setAttribute("aria-modal", "true");
    screen.setAttribute("aria-labelledby", "dg-title");
    screen.setAttribute("aria-describedby", "dg-lead dg-switch");
    screen.innerHTML = `
      <div class="si-card">
        <span class="brand-logo si-mark" role="img" aria-label="Vinc"></span>
        <h1 class="si-title" id="dg-title">${esc(c.title)}</h1>
        <p class="si-lead" id="dg-lead">${esc(c.lead)}</p>
        <p class="hint si-note" id="dg-switch">${esc(c.switchHint)}</p>
        <button class="btn btn-primary si-cta" id="dg-ok" type="button">${esc(c.okCta)}</button>
        <p class="si-state" id="dg-state" role="status" aria-live="polite"></p>
      </div>`;
    shell?.setAttribute("inert", "");
    document.body.append(screen);
    const ok = screen.querySelector<HTMLButtonElement>("#dg-ok")!;
    const state = screen.querySelector<HTMLElement>("#dg-state")!;
    ok.focus();

    /** 정본(사이드카 설정)에 먼저 적고 창의 사본을 맞춘다. 못 적으면 닫지 않는다: 다음
     *  기동이 같은 알림을 다시 해야 하는데, 창에만 적으면 알린 줄 알고 넘어간다. */
    ok.addEventListener("click", async () => {
      ok.disabled = true;
      try {
        const r = await storage.setConfig({ mode: "local", mode_chosen: true }).catch(() => ({ status: 0 }));
        if (r?.status !== undefined) { state.textContent = c.saveFailed; return; }
        dataMode.set("local");
        screen.remove();
        shell?.removeAttribute("inert");
        resolve("local");
      } finally {
        ok.disabled = false;
      }
    });
  });
}
