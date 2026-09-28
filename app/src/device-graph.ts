/** 로그인 뒤 한 번 묻는 화면 — 이 컴퓨터에 이미 그래프가 있을 때(VIN-131).
 *
 *  데스크톱은 기본으로 계정 그래프를 본다. 그런데 로그인하기 전부터 이 기기에 쌓인 작업이
 *  있으면, 묻지 않고 계정으로 가면 그 작업이 빈 계정 화면 뒤로 가려져 유실처럼 읽히고,
 *  묻지 않고 로컬로 가면(0.9.4·0.9.5) 스페이스도 웹과 같은 그래프도 안 보인다. 그래서
 *  **한 번** 묻는다. 부르는 조건은 `decideDataMode` 가 정한다(사람이 고른 적이 없고, 이
 *  기기에 보여 줄 작업이 있을 때).
 *
 *    계정으로 옮기기(기본)   이 기기의 전부를 올린다 → 빠진 것이 없으면 계정 모드
 *    이 컴퓨터에서 계속 쓰기  지금 연 폴더 그대로 로컬 모드
 *
 *  ⚠ 어느 길도 지우지 않는다. 올리기는 이 기기에 아무것도 안 쓰고, 빠진 것이 있으면 모드를
 *  안 바꾼다(`moveDeviceIntoAccount`). 고른 답은 사이드카 설정에 `mode_chosen` 으로 남아
 *  다시 묻지 않는다: 창 저장소가 아니라 설정이 정본이다(`decideDataMode` 주석).
 *
 *  근거: [[vinc.plan.desktop-account-mode §8]]
 */
import { COPY } from "./copy";
import { accountKey } from "./account-connect";
import { dataMode, storage, type DataMode } from "./api";
import { moveDeviceIntoAccount } from "./storage-mode";
import { esc } from "./components";

/** 답을 받을 때까지 기다린다. 고른 모드를 돌려준다. 닫을 문이 없다: 답하기 전에는 어느
 *  그래프를 그릴지 모른다. */
export function askAboutDeviceGraph(): Promise<DataMode> {
  return new Promise((resolve) => {
    const c = COPY.deviceGraph;
    const shell = document.getElementById("shell");
    const screen = document.createElement("section");
    screen.id = "device-graph-screen";
    screen.setAttribute("role", "dialog");
    screen.setAttribute("aria-modal", "true");
    screen.setAttribute("aria-labelledby", "dg-title");
    screen.innerHTML = `
      <div class="si-card">
        <span class="brand-logo si-mark" role="img" aria-label="Vinc"></span>
        <h1 class="si-title" id="dg-title">${esc(c.title)}</h1>
        <p class="si-lead">${esc(c.lead)}</p>
        <button class="btn btn-primary si-cta" id="dg-move" type="button"
                aria-describedby="dg-move-hint">${esc(c.moveCta)}</button>
        <p class="hint si-note" id="dg-move-hint">${esc(c.moveHint)}</p>
        <button class="btn si-alt" id="dg-keep" type="button"
                aria-describedby="dg-keep-hint">${esc(c.keepCta)}</button>
        <p class="hint si-note" id="dg-keep-hint">${esc(c.keepHint)}</p>
        <p class="si-state" id="dg-state" role="status" aria-live="polite"></p>
        <p class="hint si-note">${esc(c.note)}</p>
      </div>`;
    shell?.setAttribute("inert", "");
    document.body.append(screen);
    const move = screen.querySelector<HTMLButtonElement>("#dg-move")!;
    const keep = screen.querySelector<HTMLButtonElement>("#dg-keep")!;
    const state = screen.querySelector<HTMLElement>("#dg-state")!;
    move.focus();

    const lock = (on: boolean) => { move.disabled = keep.disabled = on; };

    /** 정본(사이드카 설정)에 먼저 적고 창의 사본을 맞춘다. 못 적으면 닫지 않는다: 다음
     *  기동이 같은 질문을 다시 해야 하는데, 창에만 적으면 답한 줄 알고 넘어간다. */
    const settle = async (mode: DataMode): Promise<boolean> => {
      const r = await storage.setConfig({ mode, mode_chosen: true }).catch(() => ({ status: 0 }));
      if (r?.status !== undefined) { state.textContent = c.saveFailed; return false; }
      dataMode.set(mode);
      screen.remove();
      shell?.removeAttribute("inert");
      resolve(mode);
      return true;
    };

    move.addEventListener("click", async () => {
      lock(true);
      state.textContent = c.moving;
      try {
        const key = await accountKey().catch(() => "");
        if (!key) { state.textContent = COPY.ui.settingsUi.moveNeedKey; return; }
        const moved = await moveDeviceIntoAccount(key);
        if (!moved.ok) { state.textContent = `${moved.notes.join(" ")} ${c.retryOrKeep}`; return; }
        state.textContent = moved.notes.join(" ");
        await settle("account");
      } catch (e: any) {
        state.textContent = `${COPY.ui.settingsUi.moveFail}: ${(e && e.message) || e}. ${c.retryOrKeep}`;
      } finally {
        lock(false);
      }
    });

    keep.addEventListener("click", async () => {
      lock(true);
      try { await settle("local"); } finally { lock(false); }
    });
  });
}
