/** 저장 위치(VIN-130) — 설정 › 계정의 한 덩어리. 셸과 로컬 dev 전용.
 *
 *  데스크톱은 기본으로 계정 그래프를 본다. 로컬 모드는 **사람이 켜는 설정**이고, 켜면
 *  그래프가 고른 폴더에 있고 계정에는 사본이 없다(로그인은 그대로 필요하다). 계속 도는
 *  동기화는 없다: 데이터는 **모드를 바꿀 때만**, 확인한 뒤 한 번 옮긴다.
 *
 *    계정 → 로컬   폴더를 고른다 → 사이드카가 그 폴더로 다시 뜬다 → (고르면) 계정 그래프를
 *                  전부 받는다 → 수가 맞으면 로컬 모드
 *    로컬 → 계정   이 기기의 모든 주제를 계정으로 올린다 → 수가 맞으면 계정 모드
 *
 *  ⚠ **수가 안 맞으면 모드를 바꾸지 않는다.** 옮기기는 어느 쪽에서도 지우지 않으므로
 *  멈춰도 잃는 것은 없다. 반대로 모자란 채로 바꾸면 빠진 것이 보이지 않는 쪽에 남아
 *  "사라졌다"로 읽힌다. 폴더 안의 것도 지우거나 덮지 않는다: 이미 그래프가 있으면 연다.
 *
 *  근거: [[decision:the-desktop-signs-in-and-shows-the-account-graph-live-and-local-mode-is-a-setting]]
 */
import { accountMode, cfg, dataMode, pollJob, restartSidecar, shellInvoke, storage,
         type FolderCheck, type StorageConfig } from "./api";
import { accountKey } from "./account-connect";
import { COPY } from "./copy";
import { esc } from "./components";

/** 옮기기의 진행 상한. 큰 그래프를 계정 왕복으로 나르므로 보통 잡(5분)보다 길다. */
const MOVE_TIMEOUT_MS = 30 * 60_000;

/** 설정 › 계정에 저장 위치 덩어리를 그린다. 설정을 못 읽으면(옛 엔진) 줄 하나만 남긴다. */
export function mountStorage(box: HTMLElement): void {
  void draw(box);
}

async function draw(box: HTMLElement): Promise<void> {
  const U = COPY.ui.settingsUi;
  const conf = await storage.config().catch(() => null);
  if (!box.isConnected) return;
  const local = !accountMode();
  const where = local
    ? U.storageLocal(esc(folderOf(conf)))
    : U.storageAccount;
  box.innerHTML = `
    <label>${U.storage}</label>
    <p class="rail-sync"><span class="sync-dot"></span><span>${where}</span></p>
    ${conf ? `
    <div class="settings-row">
      <div class="settings-row-copy">
        <strong>${esc(U.localMode)}</strong>
        <p class="hint">${esc(U.localModeHint)}</p>
      </div>
      <button class="btn" type="button" id="st-toggle">${
        esc(local ? U.localOffAct : U.localOnAct)}</button>
    </div>
    ${local && !conf.pinned ? `
    <div class="settings-row">
      <div class="settings-row-copy">
        <strong>${esc(U.folder)}</strong>
        <p class="hint">${esc(U.folderChangeHint)}</p>
      </div>
      <button class="btn" type="button" id="st-folder">${esc(U.folderChange)}</button>
    </div>` : ""}
    ${conf.pinned ? `<p class="hint">${esc(U.pinned)}</p>` : ""}
    <div class="st-panel" id="st-panel" hidden></div>
    <p class="hint" id="st-result" role="status"></p>` : ""}
    <hr class="settings-sep" />`;
  if (!conf) return;
  const panel = box.querySelector<HTMLElement>("#st-panel")!;
  const result = box.querySelector<HTMLElement>("#st-result")!;
  box.querySelector("#st-toggle")!.addEventListener("click", () => {
    result.textContent = "";
    if (local) askTurnOff(panel, result);
    else if (conf.pinned) result.textContent = U.pinned;
    else askTurnOn(panel, result, conf);
  });
  box.querySelector("#st-folder")?.addEventListener("click", () => {
    result.textContent = "";
    askFolder(panel, result, conf, "change");
  });
}

/** 지금 연 폴더. 설정에 없으면 기본 폴더(`~/.vinc`)다. */
function folderOf(conf: StorageConfig | null): string {
  if (!conf) return "";
  return conf.data_path || conf.default_folder;
}

function mb(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** 폴더 고르기 창(셸의 dialog 플러그인). JS 패키지를 들이지 않고 플러그인 명령을 직접
 *  부른다: 그 패키지가 하는 일이 이 한 줄이다. 셸이 아니면 null(입력칸에 직접 쓴다). */
async function pickFolder(start: string): Promise<string | null> {
  const invoke = await shellInvoke();
  if (!invoke) return null;
  const got = await invoke<string | string[] | null>("plugin:dialog|open", {
    options: { directory: true, multiple: false, defaultPath: start || undefined },
  }).catch(() => null);
  return Array.isArray(got) ? got[0] ?? null : got;
}

/** 폴더를 살핀 결과를 문장으로. 받을 수 없으면 사유를, 위험하면 경고를 준다. */
function describe(c: FolderCheck): { text: string; risky: boolean } {
  const U = COPY.ui.settingsUi;
  if (!c.ok) {
    const bad = U.folderBad;
    const code = (c.problem || "empty") as keyof typeof bad;
    return { text: bad[code] || bad.empty, risky: false };
  }
  const lines: string[] = [c.has_store ? U.folderHasStore(mb(c.store_bytes)) : U.folderSafe];
  if (c.synced) lines.push(U.folderSynced(c.synced));
  if (c.network) lines.push(U.folderNetwork);
  return { text: lines.join(" "), risky: c.risks.length > 0 };
}

/** 계정 → 로컬(켜기)과 로컬 폴더 바꾸기가 함께 쓰는 폴더 판. */
function askFolder(panel: HTMLElement, result: HTMLElement, conf: StorageConfig,
                   purpose: "turn-on" | "change"): void {
  const U = COPY.ui.settingsUi;
  const start = purpose === "change" ? folderOf(conf) : conf.suggested_folder;
  panel.hidden = false;
  // 바꾸기의 안내("폴더를 바꿔도 옮겨지지 않는다")는 바로 위 줄에 이미 있다.
  panel.innerHTML = `
    ${purpose === "turn-on" ? `<p class="hint">${esc(U.localOnIntro)}</p>` : ""}
    <label for="st-path">${esc(U.folder)}</label>
    <p class="s-keyrow"><input id="st-path" value="${esc(start)}" spellcheck="false"
       autocomplete="off" /><button class="btn" type="button" id="st-pick">${
       esc(U.folderPick)}</button></p>
    <p class="hint" id="st-check"></p>
    <label class="st-choice" id="st-risk" hidden><input type="checkbox" id="st-accept" />
      <span>${esc(U.folderRiskAccept)}</span></label>
    ${purpose === "turn-on" ? `
    <div id="st-start">
      <label class="st-choice"><input type="radio" name="st-start" value="copy" checked />
        <span>${esc(U.startCopy)}</span></label>
      <label class="st-choice"><input type="radio" name="st-start" value="keep" />
        <span id="st-keep-label">${esc(U.startEmpty)}</span></label>
    </div>` : ""}
    <div class="st-acts">
      <button class="btn" type="button" id="st-go">${
        esc(purpose === "turn-on" ? U.localSwitchAct : U.folderChange)}</button>
      <button class="btn quiet" type="button" id="st-cancel">${esc(COPY.ui.common.cancel)}</button>
    </div>`;
  const input = panel.querySelector<HTMLInputElement>("#st-path")!;
  const check = panel.querySelector<HTMLElement>("#st-check")!;
  const risk = panel.querySelector<HTMLElement>("#st-risk")!;
  const accept = panel.querySelector<HTMLInputElement>("#st-accept")!;
  const go = panel.querySelector<HTMLButtonElement>("#st-go")!;
  let last: FolderCheck | null = null;

  async function inspect() {
    const path = input.value.trim();
    const c = await storage.inspect(path).catch(() => null);
    if (!panel.isConnected || input.value.trim() !== path) return;   // 그사이 바뀌었다
    last = c;
    if (!c) { check.textContent = U.moveFail; go.disabled = true; return; }
    const d = describe(c);
    check.textContent = d.text;
    check.classList.toggle("st-warn", d.risky || !c.ok);
    risk.hidden = !d.risky;
    if (!d.risky) accept.checked = false;
    go.disabled = !c.ok;
    // 이미 그래프가 있는 폴더면 "빈 그래프"가 아니라 "폴더에 있는 그래프"를 연다.
    const keep = panel.querySelector<HTMLElement>("#st-keep-label");
    if (keep) keep.textContent = c.has_store ? U.startExisting : U.startEmpty;
  }
  input.addEventListener("change", () => void inspect());
  panel.querySelector("#st-pick")!.addEventListener("click", async () => {
    const got = await pickFolder(input.value.trim());
    if (got) { input.value = got; void inspect(); }
  });
  panel.querySelector("#st-cancel")!.addEventListener("click", () => {
    panel.hidden = true;
    panel.innerHTML = "";
  });
  go.addEventListener("click", async () => {
    if (!last?.ok) return;
    if (last.risks.length && !accept.checked) { check.textContent = U.folderRiskNeeded; return; }
    const copy = purpose === "turn-on"
      && panel.querySelector<HTMLInputElement>('input[name="st-start"]:checked')?.value === "copy";
    go.disabled = true;
    try {
      if (purpose === "turn-on") await turnOn(input.value.trim(), accept.checked, copy, result);
      else await changeFolder(input.value.trim(), accept.checked, result);
      if (dataMode.get() === "local" && purpose === "turn-on") panel.hidden = true;
    } finally {
      go.disabled = false;
    }
  });
  void inspect();
}

function askTurnOn(panel: HTMLElement, result: HTMLElement, conf: StorageConfig): void {
  askFolder(panel, result, conf, "turn-on");
}

/** 폴더를 설정에 적고 사이드카를 그 폴더로 다시 띄운다. 모드는 아직 안 바꾼다. */
async function useFolder(path: string, acceptRisk: boolean, result: HTMLElement): Promise<boolean> {
  const U = COPY.ui.settingsUi;
  const r = await storage.setConfig({ data_path: path, accept_risk: acceptRisk });
  if (r?.status) {
    const bad = U.folderBad;
    const code = String(r.error || "") as keyof typeof bad;
    result.textContent = r.error === "risky_folder" ? U.folderRiskNeeded
      : r.error === "pinned" ? U.pinned : (bad[code] || U.moveFail);
    return false;
  }
  if (r?.restart_needed) {
    result.textContent = U.movingRestart;
    if (!(await restartSidecar())) { result.textContent = U.restartFail; return false; }
  }
  return true;
}

async function changeFolder(path: string, acceptRisk: boolean, result: HTMLElement) {
  if (!(await useFolder(path, acceptRisk, result))) return;
  // 화면 전체가 옛 폴더의 그래프를 들고 있다. 부분 갱신하면 두 폴더가 섞여 보인다.
  location.reload();
}

/** 계정 → 로컬. 고르면 계정 그래프를 전부 받고, 수가 맞을 때만 모드를 바꾼다. */
async function turnOn(path: string, acceptRisk: boolean, copy: boolean, result: HTMLElement) {
  const U = COPY.ui.settingsUi;
  const key = copy ? await accountKey().catch(() => "") : "";
  if (copy && !key) { result.textContent = U.moveNeedKey; return; }
  let copied: string | null = null;
  try {
    if (!(await useFolder(path, acceptRisk, result))) return;
    if (copy) {
      result.textContent = U.movingDown;
      const job = await storage.pullAll(key, cfg.owner);
      const done = await pollJob(job.job_id, undefined,
                                 { timeoutMs: MOVE_TIMEOUT_MS, status: storage.job });
      const r = done.result || {};
      if (r.nodes_mode !== "everything" || r.docs_mode !== "everything") {
        result.textContent = U.movePartial;
        return;
      }
      if (Number(r.missing) > 0) { result.textContent = U.moveMissing(Number(r.missing)); return; }
      copied = U.moveCounts(Number(r.after?.nodes ?? 0), Number(r.after?.docs ?? 0));
      if (Number(r.kept_deleted) > 0) copied += " " + U.moveKeptDeleted(Number(r.kept_deleted));
    }
    await storage.setConfig({ mode: "local" });
    dataMode.set("local");
    result.textContent = copied ?? U.localReady;
    finish(result);
  } catch (e: any) {
    result.textContent = `${U.moveFail}: ${(e && e.message) || e}`;
  }
}

/** 로컬 → 계정. 이 기기의 전부를 올리고, 수가 맞을 때만 모드를 바꾼다. 폴더는 그대로다.
 *
 *  옮기지 않고 바꾸는 길도 둔다. 계정에 이미 그래프가 있는 사람(원격 MCP 로 써 온 사람)
 *  에게 전부 올리기는 필요 없는 일이고, 올리기가 실패하면(스토어를 못 읽는 기기 등) 모드를
 *  바꿀 다른 길이 없어 로컬 모드에 갇혔다. 폴더는 어느 길로도 건드리지 않는다. */
function askTurnOff(panel: HTMLElement, result: HTMLElement): void {
  const U = COPY.ui.settingsUi;
  panel.hidden = false;
  panel.innerHTML = `
    <p class="hint">${esc(U.localOffIntro)}</p>
    <p class="hint">${esc(U.localOffKeepHint)}</p>
    <div class="st-acts">
      <button class="btn" type="button" id="st-go">${esc(U.localOffGo)}</button>
      <button class="btn" type="button" id="st-keep">${esc(U.localOffKeep)}</button>
      <button class="btn quiet" type="button" id="st-cancel">${esc(COPY.ui.common.cancel)}</button>
    </div>`;
  const go = panel.querySelector<HTMLButtonElement>("#st-go")!;
  const keep = panel.querySelector<HTMLButtonElement>("#st-keep")!;
  panel.querySelector("#st-cancel")!.addEventListener("click", () => {
    panel.hidden = true;
    panel.innerHTML = "";
  });
  keep.addEventListener("click", async () => {
    keep.disabled = go.disabled = true;
    try {
      // 정본(사이드카 설정)이 먼저다. 창의 사본만 바꾸면 다음 기동의 `decideDataMode` 가
      // 설정 파일의 "local" 을 도로 읽어 온다.
      const r = await storage.setConfig({ mode: "account" });
      if (r?.status) { result.textContent = U.moveFail; return; }
      dataMode.set("account");
      panel.hidden = true;
      result.textContent = U.localOffKept;
      finish(result);
    } catch (e: any) {
      result.textContent = `${U.moveFail}: ${(e && e.message) || e}`;
    } finally {
      keep.disabled = go.disabled = false;
    }
  });
  go.addEventListener("click", async () => {
    const key = await accountKey().catch(() => "");
    if (!key) { result.textContent = U.moveNeedKey; return; }
    go.disabled = true;
    result.textContent = U.movingUp;
    try {
      const job = await storage.pushAll(key);
      const done = await pollJob(job.job_id, undefined,
                                 { timeoutMs: MOVE_TIMEOUT_MS, status: storage.job });
      const r = done.result || {};
      if (Number(r.missing) > 0) { result.textContent = U.moveMissing(Number(r.missing)); return; }
      const notes = [U.moveCounts(Number(r.nodes ?? 0), Number(r.docs ?? 0))];
      if (Number(r.kept_deleted) > 0) notes.push(U.moveKeptDeleted(Number(r.kept_deleted)));
      if (Number(r.left_behind) > 0) notes.push(U.moveLeftBehind(Number(r.left_behind)));
      await storage.setConfig({ mode: "account" });
      dataMode.set("account");
      // 다시 누를 자리를 치운다. 옮기기는 멱등이지만 두 번 올릴 까닭이 없다.
      panel.hidden = true;
      result.textContent = notes.join(" ");
      finish(result);
    } catch (e: any) {
      result.textContent = `${U.moveFail}: ${(e && e.message) || e}`;
    } finally {
      go.disabled = false;
    }
  });
}

/** 모드가 바뀌었다. 옮긴 수를 읽을 수 있게 두고, 누르면 새 모드로 다시 연다. */
function finish(result: HTMLElement): void {
  const U = COPY.ui.settingsUi;
  const b = document.createElement("button");
  b.className = "btn";
  b.type = "button";
  b.textContent = U.moveOpenAct;
  b.addEventListener("click", () => location.reload());
  const p = document.createElement("p");
  p.appendChild(b);
  result.appendChild(p);
}
