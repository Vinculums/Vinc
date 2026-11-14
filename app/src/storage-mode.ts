/** 저장 위치(VIN-130) — 설정 › 저장 위치 탭. 셸과 로컬 dev 전용.
 *
 *  맨 위 카드가 지금 그래프가 있는 곳(계정 · 폴더 · 내 데이터베이스)을 말하고, "위치 바꾸기"가 세 카드의
 *  고르기를 연다. 고른 카드 아래 판에서 옮긴다. 데이터베이스 판은 연결 → 확인(`/config/inspect-database`)
 *  → 시작 고르기 → 바꾸기(`/config/move` 나 `POST /config`) 순이고, 비밀번호는 바꾸기 직전에 OS 키링에만
 *  둔다(셸이 사이드카를 띄울 때 `secret_ref` 이름으로 꺼낸다). 바뀌면 곧바로 사이드카를 다시 띄운다.
 *  근거: 그래프의 개념 bring-your-own-database 와 그 계획 문서 §3.
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
import { accountMode, cfg, dataMode, messageOf, pollJob, restartSidecar, shellInvoke, storage,
         type ConnectionCheck, type FolderCheck, type StorageConfig, type StorageLocation } from "./api";
import { accountKey, setDeviceSecret } from "./account-connect";
import { COPY } from "./copy";
import { esc } from "./components";
import { loaderTrack } from "./vinc-loaders.js";

/** 옮기기의 진행 상한. 큰 그래프를 계정 왕복으로 나르므로 보통 잡(5분)보다 길다. */
const MOVE_TIMEOUT_MS = 30 * 60_000;

/** 설정 › 저장 위치 탭의 본문을 그린다. 설정을 못 읽으면(옛 엔진) 줄 하나만 남긴다.
 *  `openAccount` 는 옮기기에 로그인이 필요할 때 계정 탭으로 보내는 길이다. */
export function mountStorage(box: HTMLElement, opts: { openAccount?: () => void } = {}): void {
  openAccount = opts.openAccount ?? null;
  void draw(box);
}

/** 지금 그린 저장 위치 탭이 계정 탭으로 보내는 길. 설정 시트는 한 번에 하나만 열린다. */
let openAccount: (() => void) | null = null;

/** 옮기기에 계정 키가 없다: 그렇다고 말하고, 갈 수 있으면 계정 탭으로 가는 버튼을 준다. */
function needKey(result: HTMLElement): void {
  const U = COPY.ui.settingsUi;
  result.textContent = U.moveNeedKey;
  const go = openAccount;
  if (!go) return;
  const btn = document.createElement("button");
  btn.className = "btn";
  btn.type = "button";
  btn.style.marginLeft = ".5rem";
  btn.textContent = U.moveNeedKeyGo;
  btn.addEventListener("click", go);
  result.append(btn);
}

type Loc = "account" | "folder" | "database";
type DbLocation = Extract<StorageLocation, { kind: "database" }>;

/** 사이드카 `store/neo4j_backend.py` 의 `BLOCKING_RISKS` 와 같다. 받아들일 수 없는 위험이다. */
const BLOCKING_RISKS = ["version_below_floor", "read_only_user"];

/** 지금 열린 고르기 판을 닫는 길. 판 안의 취소 버튼이 부른다(`draw` 가 매번 다시 건다). */
let closePick: () => void = () => {};

function closePanel(panel: HTMLElement): void {
  panel.innerHTML = "";
  closePick();
}

/** 설정이 말하는 저장 위치. 아직 안 골랐으면 창의 모드로 읽는다. */
function currentLoc(conf: StorageConfig): Loc {
  const k = conf.location?.kind;
  if (k === "account" || k === "folder" || k === "database") return k;
  return accountMode() ? "account" : "folder";
}

/** `neo4j+s://host:7687/x` → `host:7687`. 화면에 적는 주소다. */
export function hostOf(uri: string): string {
  return uri.trim().replace(/^[a-z0-9+]+:\/\//i, "").replace(/\/.*$/, "");
}

/** 키링 항목의 기본 이름. `vinc storage connect` 와 같은 규칙이다(`api/cli_storage.py` `_location`). */
function secretRefFor(uri: string, database: string): string {
  const host = hostOf(uri).replace(/^\[([^\]]*)\](?::\d+)?$/, "$1").replace(/:\d+$/, "").toLowerCase();
  return `vinc-database:${host || "database"}/${database}`;
}

async function draw(box: HTMLElement): Promise<void> {
  const U = COPY.ui.settingsUi;
  const L = U.db;
  const conf = await storage.config().catch(() => null);
  if (!box.isConnected) return;
  if (!conf) {
    // 설정을 못 읽는 엔진: 어디에 있는지 한 줄만 남긴다.
    box.innerHTML = `<p class="rail-sync"><span class="sync-dot"></span><span>${
      accountMode() ? esc(U.storageAccount) : U.storageLocal("")}</span></p>`;
    return;
  }
  const cur = currentLoc(conf);
  const db = conf.location?.kind === "database" ? conf.location : null;
  const down = !!conf.location_error || (cur === "database" && !conf.store_open);
  const name = cur === "account" ? L.locAccount : cur === "folder" ? L.locFolder : L.locDatabase;
  const detail = cur === "account" ? U.storageAccount
    : cur === "folder" ? folderOf(conf) : db ? `${hostOf(db.uri)}/${db.database}` : "";
  const card = (loc: Loc, title: string, hint: string, data: string) => {
    const now = loc === cur;
    // 폴더는 지금 위치여도 고른다: 다른 폴더로 바꾸는 길이 그 판이다.
    const off = now && loc !== "folder";
    return `
      <button type="button" class="sl-card" role="radio" data-pick="${loc}" aria-checked="false"${off ? " disabled" : ""}>
        <span class="sl-card-title">${esc(title)}${now ? ` <span class="settings-state on">${esc(L.now)}</span>` : ""}</span>
        <span class="hint">${esc(hint)}</span>
        ${now && loc !== "folder" ? "" : `<span class="hint sl-card-data">${esc(now ? U.folderChangeHint : data)}</span>`}
      </button>`;
  };
  box.innerHTML = `
    <div class="sl-loc">
      <div class="sl-loc-copy">
        <p class="sl-loc-eyebrow">${esc(L.livesIn)}</p>
        <p class="sl-loc-name">${esc(name)}</p>
        <p class="sl-loc-detail"><span class="sync-dot${down ? " down" : ""}" aria-hidden="true"></span><span>${
          esc(down && detail ? `${detail} · ${L.notAnswering}` : detail || L.notAnswering)}</span></p>
      </div>
      <span class="sl-loc-acts">
        ${down && cur === "database" ? `<button class="btn btn-primary" type="button" id="st-retry">${esc(L.downRetry)}</button>` : ""}
        ${conf.pinned ? "" : `<button class="btn" type="button" id="st-change">${esc(L.changeLocation)}</button>`}
      </span>
    </div>
    ${down ? `<p class="hint" id="st-down" role="status">${esc(conf.location_error ? L.downBadConfig
      : L.downBody(db ? hostOf(db.uri) : ""))}</p>` : ""}
    ${conf.pinned ? `<p class="hint">${esc(U.pinned)}</p>` : ""}
    <div id="st-choose" hidden>
      <p class="sl-subhead">${esc(L.chooseTitle)}</p>
      <p class="hint">${esc(L.chooseHint)}</p>
      <div class="sl-cards" role="radiogroup" aria-label="${esc(L.chooseTitle)}">
        ${card("account", L.locAccount, L.locAccountHint, L.dataAccount)}
        ${card("folder", L.locFolder, L.locFolderHint, L.dataFolder)}
        ${card("database", L.locDatabase, L.locDatabaseHint, L.dataDatabase)}
      </div>
      <div class="sl-panel" id="st-pick" hidden>
        <div class="sl-panel-head"><h3 id="st-pick-title"></h3>
          <button class="sl-link" type="button" id="st-back">${esc(L.back)}</button></div>
        <div class="st-panel" id="st-panel"></div>
      </div>
    </div>
    <p class="hint" id="st-result" role="status"></p>`;
  const choose = box.querySelector<HTMLElement>("#st-choose")!;
  const pick = box.querySelector<HTMLElement>("#st-pick")!;
  const panel = box.querySelector<HTMLElement>("#st-panel")!;
  const result = box.querySelector<HTMLElement>("#st-result")!;
  const change = box.querySelector<HTMLButtonElement>("#st-change");
  const cards = [...box.querySelectorAll<HTMLButtonElement>("[data-pick]")];
  const back = () => {
    pick.hidden = true;
    panel.innerHTML = "";
    cards.forEach((c) => c.setAttribute("aria-checked", "false"));
  };
  closePick = () => {
    back();
    choose.hidden = true;
    if (change) change.disabled = false;
  };
  box.querySelector("#st-back")!.addEventListener("click", back);
  change?.addEventListener("click", () => {
    choose.hidden = false;
    change.disabled = true;
    result.textContent = "";
    cards.find((c) => !c.disabled)?.focus();
  });
  box.querySelector<HTMLButtonElement>("#st-retry")?.addEventListener("click", async (e) => {
    const btn = e.currentTarget as HTMLButtonElement;
    btn.disabled = true;
    const msg = box.querySelector<HTMLElement>("#st-down");
    if (msg) msg.textContent = L.testing;
    // 사이드카는 기동할 때 한 번 연다. 다시 띄워야 다시 닿아 본다.
    await restartSidecar();
    void draw(box);
  });
  cards.forEach((c, i) => {
    c.addEventListener("click", () => {
      const loc = c.dataset.pick as Loc;
      cards.forEach((o) => o.setAttribute("aria-checked", String(o === c)));
      result.textContent = "";
      pick.hidden = false;
      box.querySelector<HTMLElement>("#st-pick-title")!.textContent =
        loc === "account" ? L.locAccount : loc === "folder" ? L.locFolder : L.locDatabase;
      if (loc === "account") askTurnOff(panel, result);
      else if (loc === "folder") {
        askFolder(panel, result, conf, cur === "account" ? "turn-on" : cur === "folder" ? "change" : "from-database");
      } else askDatabase(panel, result, cur);
    });
    // 라디오 그룹: 화살표로 옆 카드에 간다(꺼진 카드는 건너뛴다).
    c.addEventListener("keydown", (e) => {
      const step = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1
        : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
      if (!step) return;
      e.preventDefault();
      for (let k = 1; k < cards.length; k++) {
        const next = cards[(i + step * k + cards.length) % cards.length]!;
        if (!next.disabled) { next.focus(); break; }
      }
    });
  });
}

// ── 내 데이터베이스(Neo4j) ─────────────────────────────────────────────────────

type Step = "connect" | "testing" | "check" | "moving" | "done" | "failed";
type Start = "copy" | "empty" | "existing" | "merge";

interface DbFlow {
  step: Step;
  conn: { uri: string; database: string; user: string; password: string };
  check: ConnectionCheck | null;
  accepted: boolean;
  start: Start;
  missing: number;
  done: string;
}

type Line = { kind: "ok" | "info" | "warn" | "block"; text: string; sub?: string };
const MARK: Record<Line["kind"], string> = { ok: "✓", info: "i", warn: "△", block: "✕" };

function blockedCheck(c: ConnectionCheck): boolean {
  return !c.ok || c.risks.some((r) => BLOCKING_RISKS.includes(r)) || !!c.writer_lease?.active;
}

/** 확인 결과를 문장으로. 막는 것 · 경고 · 알림 · 괜찮음 순으로 선다(읽는 순서가 할 일 순서다). */
function checkLines(c: ConnectionCheck, conn: DbFlow["conn"]): Line[] {
  const L = COPY.ui.settingsUi.db;
  if (!c.ok) {
    const text = c.problem === "unreachable" ? L.unreachable(hostOf(conn.uri))
      : c.problem === "auth_failed" ? L.authFailed
      : c.problem === "database_not_found" ? L.notFound(conn.database)
      : c.problem === "driver_missing" ? L.driverMissing : L.checkError;
    return [{ kind: "block", text }];
  }
  const lines: Line[] = [];
  if (c.risks.includes("version_below_floor"))
    lines.push({ kind: "block", text: L.versionBelowFloor(c.server_version || "") });
  if (c.risks.includes("read_only_user")) lines.push({ kind: "block", text: L.readOnlyUser(conn.user) });
  if (c.writer_lease?.active) lines.push({ kind: "block", text: L.anotherWriter });
  if (c.risks.includes("unencrypted_transport")) lines.push({ kind: "warn", text: L.unencrypted });
  if (c.risks.includes("remote_latency")) lines.push({ kind: "warn", text: L.remoteLatency });
  if (blockedCheck(c)) return lines;
  if (c.vinc_schema_version != null)
    lines.push({ kind: "info", text: L.existing(c.vinc_schema_version, c.vinc_counts.nodes, c.vinc_counts.docs) });
  if (c.foreign_labels.count > 0)
    lines.push({ kind: "info", text: L.foreign(c.foreign_labels.nodes || c.foreign_labels.count),
                 sub: L.foreignNames(c.foreign_labels.names.join(", ")) });
  if (c.vinc_schema_version == null && c.foreign_labels.count === 0) lines.push({ kind: "ok", text: L.emptyDb });
  if (c.version_supported) lines.push({ kind: "ok", text: L.versionOk });
  lines.push({ kind: "ok", text: L.dbSafe });
  return lines;
}

function checkHtml(f: DbFlow): string {
  const L = COPY.ui.settingsUi.db;
  const c = f.check!;
  const edition = c.edition ? c.edition[0]!.toUpperCase() + c.edition.slice(1) : "";
  const graph = c.vinc_schema_version == null ? L.graphNone : L.graphFound(c.vinc_schema_version);
  const blocked = blockedCheck(c);
  return `
    <div class="sl-check" role="status">
      ${c.ok ? `
      <div class="sheet-spec sl-facts">
        <div><span>${esc(L.factServer)}</span><b>${esc(L.serverLine(c.server_version || "", edition))}</b></div>
        <div><span>${esc(L.factWrite)}</span><b>${esc(c.write_permission ? L.canWrite : L.cannotWrite)}</b></div>
        <div><span>${esc(L.factGraph)}</span><b>${esc(graph)}</b></div>
      </div>` : ""}
      <ul class="sl-lines">
        ${checkLines(c, f.conn).map((l) => `<li class="sl-line sl-${l.kind}"><span class="sl-mark" aria-hidden="true">${
          MARK[l.kind]}</span><span>${esc(l.text)}${l.sub ? `<small class="hint">${esc(l.sub)}</small>` : ""}</span></li>`).join("")}
      </ul>
      ${blocked ? `<p class="error-banner sl-blocked">${esc(L.blocked)}</p>` : ""}
      ${!blocked && c.risks.length ? `
      <label class="st-choice sl-accept"><input type="checkbox" id="sl-accept"${f.accepted ? " checked" : ""} />
        <span>${esc(L.riskAccept)}</span></label>` : ""}
    </div>`;
}

function startHtml(f: DbFlow): string {
  const L = COPY.ui.settingsUi.db;
  const opt = (v: Start, label: string, hint: string) => `
    <label class="st-choice sl-start"><input type="radio" name="sl-start" value="${v}"${f.start === v ? " checked" : ""} />
      <span><span class="sl-start-label">${esc(label)}</span><small class="hint">${esc(hint)}</small></span></label>`;
  return `
    <div class="sl-starts" role="radiogroup" aria-label="${esc(L.startTitle)}">
      <p class="sl-subhead">${esc(L.startTitle)}</p>
      ${f.check?.vinc_schema_version != null
        ? opt("existing", L.startExisting, L.startExistingHint) + opt("merge", L.startMerge, L.startMergeHint)
        : opt("copy", L.startCopy, L.startCopyHint) + opt("empty", L.startEmpty, L.startEmptyHint)}
    </div>`;
}

function flowTo(f: DbFlow): string {
  const L = COPY.ui.settingsUi.db;
  const c = f.check;
  return f.start === "copy" ? L.flowGetsCopy : f.start === "empty" ? L.flowStartsEmpty
    : f.start === "existing" && c ? L.flowOpens(c.vinc_counts.nodes, c.vinc_counts.docs) : L.flowMerges;
}

/** 지금 → 바꾼 뒤. 고른 시작 방법에 따라 오른쪽 칸의 말이 바뀐다. */
function flowHtml(f: DbFlow, cur: Loc): string {
  const L = COPY.ui.settingsUi.db;
  const from = cur === "account" ? L.locAccount : cur === "folder" ? L.locFolder : L.locDatabase;
  return `
    <div class="sl-flow" aria-label="${esc(`${L.flowFrom} → ${L.flowTo}`)}">
      <div class="sl-flow-box"><span class="hint">${esc(L.flowFrom)}</span>
        <b>${esc(from)}</b><p class="hint">${esc(L.flowKeeps)}</p></div>
      <span class="sl-flow-arrow" aria-hidden="true">→</span>
      <div class="sl-flow-box"><span class="hint">${esc(L.flowTo)}</span>
        <b>${esc(`${L.locDatabase} · ${hostOf(f.conn.uri)}`)}</b><p class="hint" id="sl-flow-to">${esc(flowTo(f))}</p></div>
    </div>`;
}

/** 옮긴 수. 계정에서 받았으면 받은 뒤의 수를, 스토어를 옮겼으면 원본 표의 수를 센다(조각·벡터는 빼고). */
function copiedCounts(rep: any): { nodes: number; docs: number } {
  if (rep?.after) return { nodes: Number(rep.after.nodes ?? 0), docs: Number(rep.after.docs ?? 0) };
  const t = rep?.tables || {};
  let nodes = 0;
  for (const [label, v] of Object.entries<any>(t)) {
    if (label.startsWith("_") || label === "Chunk" || label === "ChunkVec" || label === "NodeVec") continue;
    nodes += Number(v?.source ?? 0);
  }
  return { nodes, docs: Number(t._DocMeta?.source ?? 0) };
}

/** 데이터베이스 판: 연결 → 확인 → 시작 고르기 → 바꾸기. 비밀번호는 이 판의 메모리와 키링에만 있다. */
function askDatabase(panel: HTMLElement, result: HTMLElement, cur: Loc): void {
  const U = COPY.ui.settingsUi;
  const L = U.db;
  const f: DbFlow = {
    step: "connect", conn: { uri: "", database: "neo4j", user: "neo4j", password: "" },
    check: null, accepted: false, start: "copy", missing: 0, done: "",
  };
  const canSwitch = () => !!f.check && !blockedCheck(f.check) && (!f.check.risks.length || f.accepted);

  const formHtml = () => {
    const field = (k: keyof DbFlow["conn"], label: string, type = "text", extra = "") => `
      <div class="sl-field sl-field-${k}">
        <label for="sl-${k}">${esc(label)}</label>
        <input id="sl-${k}" data-conn="${k}" type="${type}"${k === "password" ? "" : ` value="${esc(f.conn[k])}"`}
               spellcheck="false" autocomplete="off" ${extra} />
      </div>`;
    return `
      <div class="sl-form">
        ${field("uri", L.uri, "text", `placeholder="${esc(L.uriPlaceholder)}"`)}
        ${field("database", L.database)}
        ${field("user", L.user)}
        ${field("password", L.password, "password")}
      </div>
      <p class="hint sl-pw-hint">${esc(L.passwordHint)}</p>`;
  };
  const summary = () => `<div class="sl-summary"><span>${esc(`${f.conn.uri} · ${f.conn.database} · ${f.conn.user}`)}</span>
    ${f.step === "check" ? `<button class="sl-link" type="button" data-act="edit">${esc(L.edit)}</button>` : ""}</div>`;
  const acts = (...b: string[]) => `<div class="st-acts">${b.join("")}</div>`;
  const btn = (act: string, label: string, primary = false, off = false) =>
    `<button class="btn${primary ? " btn-primary" : ""}" type="button" data-act="${act}"${off ? " disabled" : ""}>${esc(label)}</button>`;
  const cancel = `<button class="btn quiet" type="button" data-act="cancel">${esc(COPY.ui.common.cancel)}</button>`;

  function render(): void {
    const c = f.check;
    let body = "";
    if (f.step === "connect" || f.step === "testing" || (f.step === "check" && c && blockedCheck(c))) {
      // 막힌 확인은 폼을 접지 않는다: 고칠 칸이 바로 위에 남는다.
      body = formHtml()
        + (f.step === "testing" ? `<div class="sheet-progress sl-progress">${loaderTrack(L.testing)}</div>`
          : f.step === "check" ? checkHtml(f) : "")
        + acts(btn("test", f.step === "check" ? L.testAgain : L.test, f.step !== "check", f.step === "testing"), cancel);
    } else if (f.step === "check") {
      body = summary() + checkHtml(f) + startHtml(f) + flowHtml(f, cur)
        + acts(btn("switch", L.switchAct, true, !canSwitch()), cancel)
        + `<p class="hint" id="sl-risk-needed"${canSwitch() ? " hidden" : ""}>${esc(L.riskNeeded)}</p>`;
    } else if (f.step === "moving") {
      body = summary() + flowHtml(f, cur) + `
        <div class="sheet-progress sl-progress">
          ${loaderTrack(f.start === "copy" || f.start === "merge" ? L.moving : L.restarting)}
          <p class="hint">${esc(L.movingHint)}</p>
        </div>`;
    } else if (f.step === "failed") {
      body = summary() + `
        <div class="sl-result" role="status">
          <p class="error-banner">${esc(L.notSwitched)}</p>
          ${f.missing ? `<p class="hint">${esc(L.notSwitchedWhy(f.missing))}</p>` : ""}
        </div>` + acts(btn("retry", L.retryMove, true), cancel);
    } else {
      body = `
        <div class="sl-result" role="status">
          <p class="sl-done"><span class="sl-mark" aria-hidden="true">✓</span><span>${esc(f.done)}</span></p>
          <p class="hint">${esc(L.movedKept)}</p>
        </div>` + acts(btn("done", L.done, true));
    }
    panel.innerHTML = body;
    const pw = panel.querySelector<HTMLInputElement>("#sl-password");
    if (pw) pw.value = f.conn.password;      // 값은 속성으로 DOM 에 적지 않는다
    panel.querySelectorAll<HTMLInputElement>("input[data-conn]").forEach((inp) =>
      inp.addEventListener("input", () => { f.conn[inp.dataset.conn as keyof DbFlow["conn"]] = inp.value; }));
    panel.querySelector<HTMLInputElement>("#sl-accept")?.addEventListener("change", (e) => {
      f.accepted = (e.target as HTMLInputElement).checked;
      const go = panel.querySelector<HTMLButtonElement>('[data-act="switch"]');
      if (go) go.disabled = !canSwitch();
      const need = panel.querySelector<HTMLElement>("#sl-risk-needed");
      if (need) need.hidden = canSwitch();
    });
    panel.querySelectorAll<HTMLInputElement>('input[name="sl-start"]').forEach((r) =>
      r.addEventListener("change", () => {
        f.start = r.value as Start;
        const to = panel.querySelector<HTMLElement>("#sl-flow-to");
        if (to) to.textContent = flowTo(f);
      }));
    panel.querySelectorAll<HTMLElement>("[data-act]").forEach((b) => b.addEventListener("click", () => {
      const act = b.dataset.act;
      if (act === "test") void test();
      else if (act === "edit") { f.step = "connect"; render(); }
      else if (act === "cancel") closePanel(panel);
      else if (act === "switch" && canSwitch()) void run();
      else if (act === "retry") void run();
      else if (act === "done") finish();
    }));
  }

  async function test(): Promise<void> {
    result.textContent = "";
    f.step = "testing";
    f.accepted = false;
    render();
    const { uri, database, user, password } = f.conn;
    try {
      f.check = await storage.inspectDatabase({ uri: uri.trim(), database: database.trim(), user: user.trim(), password });
      f.start = f.check.vinc_schema_version != null ? "existing" : "copy";
      f.step = "check";
    } catch (e: any) {
      // 400 은 주소·이름 모양이 틀렸다는 뜻이다(`bad_location`). 그 밖은 연결 실패와 같게 읽는다.
      f.check = null;
      f.step = "connect";
      result.textContent = /^400 /.test(messageOf(e, "")) ? L.badAddress : `${U.moveFail}: ${messageOf(e)}`;
    }
    if (panel.isConnected) render();
  }

  /** 사이드카가 거절한 사유(400·409)를 확인 화면으로 되돌린다. */
  function refused(r: any): void {
    if (r?.check) f.check = r.check;
    f.step = f.check ? "check" : "connect";
    render();
    result.textContent = r?.error === "risky_database" ? L.riskNeeded
      : r?.error === "another_writer" ? L.anotherWriter
      : r?.error === "bad_location" ? L.badAddress
      : r?.error === "pinned" ? U.pinned : `${U.moveFail}: ${String(r?.error || "")}`;
  }

  async function run(): Promise<void> {
    const loc: DbLocation = {
      kind: "database", engine: "neo4j", uri: f.conn.uri.trim(), database: f.conn.database.trim(),
      user: f.conn.user.trim(), secret_ref: secretRefFor(f.conn.uri, f.conn.database.trim()),
    };
    const password = f.conn.password;
    const copy = f.start === "copy" || f.start === "merge";
    result.textContent = "";
    const key = copy && cur === "account" ? await accountKey().catch(() => "") : "";
    if (copy && cur === "account" && !key) { needKey(result); return; }
    // 비밀번호를 먼저 키링에 둔다: 다시 띄운 사이드카는 셸이 그 이름으로 꺼내 준 값으로만 연다.
    const kept = await setDeviceSecret(loc.secret_ref, password).catch(() => false);
    if (!kept) { result.textContent = L.noKeychain; return; }
    f.step = "moving";
    render();
    try {
      if (!copy) {
        const r = await storage.setConfig({ location: loc, password, mode_chosen: true, accept_risk: f.accepted });
        if (r?.status) { refused(r); return; }
        const c = f.check!;
        f.done = f.start === "existing" ? L.movedOpen(c.vinc_counts.nodes, c.vinc_counts.docs) : L.movedEmpty;
      } else {
        const body: Parameters<typeof storage.move>[0] = { to: loc, password, accept_risk: f.accepted };
        if (cur === "account") Object.assign(body, { from: "account", token: key, owner: cfg.owner });
        const r = await storage.move(body);
        // 접수된 잡도 `status`("queued")를 단다. 거절만 숫자(400·409)다.
        if (typeof r?.status === "number") { refused(r); return; }
        const job = await pollJob(r.job_id, undefined, { timeoutMs: MOVE_TIMEOUT_MS, status: storage.job });
        const res = job.result || {};
        if (!res.switched) {
          f.missing = Number(res.copy?.missing ?? 0);
          f.step = "failed";
          render();
          if (res.error === "another_writer") result.textContent = L.anotherWriter;
          return;
        }
        const n = copiedCounts(res.copy);
        f.done = L.movedCopy(n.nodes, n.docs);
      }
      // 위치가 바뀌었다. 곧바로 다시 띄운다: 그 사이의 쓰기는 이전 위치에 남는다.
      dataMode.set("local");
      result.textContent = L.restarting;
      result.textContent = (await restartSidecar()) ? "" : U.restartFail;
      f.step = "done";
      render();
    } catch (e: any) {
      f.step = f.check ? "check" : "connect";
      render();
      result.textContent = `${U.moveFail}: ${messageOf(e)}`;
    }
  }

  render();
  panel.querySelector<HTMLInputElement>("#sl-uri")?.focus();
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
                   purpose: "turn-on" | "change" | "from-database"): void {
  const U = COPY.ui.settingsUi;
  const start = purpose === "change" ? folderOf(conf) : conf.suggested_folder;
  const choosesStart = purpose !== "change";
  panel.hidden = false;
  // 바꾸기의 안내("폴더를 바꿔도 옮겨지지 않는다")는 바로 위 줄에 이미 있다.
  panel.innerHTML = `
    ${choosesStart ? `<p class="hint">${esc(U.localOnIntro)}</p>` : ""}
    <label for="st-path">${esc(U.folder)}</label>
    <p class="s-keyrow"><input id="st-path" value="${esc(start)}" spellcheck="false"
       autocomplete="off" /><button class="btn" type="button" id="st-pick">${
       esc(U.folderPick)}</button></p>
    <p class="hint" id="st-check"></p>
    <label class="st-choice" id="st-risk" hidden><input type="checkbox" id="st-accept" />
      <span>${esc(U.folderRiskAccept)}</span></label>
    ${choosesStart ? `
    <div id="st-start">
      <label class="st-choice"><input type="radio" name="st-start" value="copy" checked />
        <span>${esc(purpose === "turn-on" ? U.startCopy : U.db.startCopy)}</span></label>
      <label class="st-choice"><input type="radio" name="st-start" value="keep" />
        <span id="st-keep-label">${esc(U.startEmpty)}</span></label>
    </div>` : ""}
    <div class="st-acts">
      <button class="btn" type="button" id="st-go">${
        esc(choosesStart ? U.localSwitchAct : U.folderChange)}</button>
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
    // 데이터베이스에서 옮길 때는 그래프가 있는 폴더에 붓지 않는다(사이드카가 `folder_has_store` 로 거절한다).
    const copyRadio = panel.querySelector<HTMLInputElement>('input[name="st-start"][value="copy"]');
    if (copyRadio && purpose === "from-database") {
      copyRadio.disabled = c.has_store;
      if (c.has_store) panel.querySelector<HTMLInputElement>('input[name="st-start"][value="keep"]')!.checked = true;
    }
  }
  input.addEventListener("change", () => void inspect());
  panel.querySelector("#st-pick")!.addEventListener("click", async () => {
    const got = await pickFolder(input.value.trim());
    if (got) { input.value = got; void inspect(); }
  });
  panel.querySelector("#st-cancel")!.addEventListener("click", () => closePanel(panel));
  go.addEventListener("click", async () => {
    if (!last?.ok) return;
    if (last.risks.length && !accept.checked) { check.textContent = U.folderRiskNeeded; return; }
    const copy = choosesStart
      && panel.querySelector<HTMLInputElement>('input[name="st-start"]:checked')?.value === "copy";
    go.disabled = true;
    try {
      if (purpose === "turn-on") await turnOn(input.value.trim(), accept.checked, copy, result);
      else if (purpose === "from-database") await leaveDatabase(input.value.trim(), accept.checked, copy, result);
      else await changeFolder(input.value.trim(), accept.checked, result);
    } finally {
      go.disabled = false;
    }
  });
  void inspect();
}

/** 데이터베이스 → 폴더. 고르면 데이터베이스의 그래프를 폴더로 옮기고(`/config/move`, 다 왔을 때만 바뀐다),
 *  아니면 폴더의 그래프(없으면 빈 그래프)를 연다. 데이터베이스에는 아무것도 쓰지 않는다. */
async function leaveDatabase(path: string, acceptRisk: boolean, copy: boolean, result: HTMLElement) {
  const U = COPY.ui.settingsUi;
  const bad = U.folderBad;
  const why = (r: any) => r.error === "risky_folder" ? U.folderRiskNeeded : r.error === "pinned" ? U.pinned
    : (bad[String(r.error || "") as keyof typeof bad] || `${U.moveFail}: ${String(r.error || "")}`);
  try {
    if (copy) {
      result.textContent = U.db.moving;
      const r = await storage.move({ to: { kind: "folder", path }, accept_risk: acceptRisk });
      if (typeof r?.status === "number") { result.textContent = why(r); return; }
      const done = await pollJob(r.job_id, undefined, { timeoutMs: MOVE_TIMEOUT_MS, status: storage.job });
      const res = done.result || {};
      if (!res.switched) { result.textContent = U.moveMissing(Number(res.copy?.missing ?? 0)); return; }
    } else {
      const r = await storage.setConfig({ location: { kind: "folder", path }, accept_risk: acceptRisk,
                                          mode_chosen: true });
      if (r?.status) { result.textContent = why(r); return; }
    }
    dataMode.set("local");
    result.textContent = U.movingRestart;
    if (!(await restartSidecar())) { result.textContent = U.restartFail; return; }
    finish();
  } catch (e: any) {
    result.textContent = `${U.moveFail}: ${messageOf(e)}`;
  }
}

/** 설정이 다시 띄우기를 바라면(데이터베이스를 열고 있다가 계정으로 바꿨을 때) 사이드카를 다시 띄운다.
 *  열린 데이터베이스는 쓰기 임대를 쥐고 있어 그대로 두면 다른 Vinc 가 그 데이터베이스에 못 쓴다. */
async function settle(r: any, result: HTMLElement): Promise<void> {
  if (!r?.restart_needed) return;
  if (!(await restartSidecar())) result.textContent = COPY.ui.settingsUi.restartFail;
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
  if (copy && !key) { needKey(result); return; }
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
    const r = await storage.setConfig({ mode: "local", mode_chosen: true });
    if (r?.status) { result.textContent = U.moveFail; return; }
    dataMode.set("local");
    result.textContent = copied ?? U.localReady;
    finish();
  } catch (e: any) {
    result.textContent = `${U.moveFail}: ${messageOf(e)}`;
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
  panel.querySelector("#st-cancel")!.addEventListener("click", () => closePanel(panel));
  keep.addEventListener("click", async () => {
    keep.disabled = go.disabled = true;
    try {
      // 정본(사이드카 설정)이 먼저다. 창의 사본만 바꾸면 다음 기동의 `decideDataMode` 가
      // 설정 파일의 "local" 을 도로 읽어 온다.
      const r = await storage.setConfig({ mode: "account", mode_chosen: true });
      if (r?.status) { result.textContent = U.moveFail; return; }
      dataMode.set("account");
      panel.innerHTML = "";
      result.textContent = U.localOffKept;
      await settle(r, result);
      finish();
    } catch (e: any) {
      result.textContent = `${U.moveFail}: ${messageOf(e)}`;
    } finally {
      keep.disabled = go.disabled = false;
    }
  });
  go.addEventListener("click", async () => {
    const key = await accountKey().catch(() => "");
    if (!key) { needKey(result); return; }
    go.disabled = true;
    result.textContent = U.movingUp;
    try {
      const moved = await moveDeviceIntoAccount(key);
      if (!moved.ok) { result.textContent = moved.notes.join(" "); return; }
      const notes = moved.notes;
      const r = await storage.setConfig({ mode: "account", mode_chosen: true });
      if (r?.status) { result.textContent = U.moveFail; return; }
      dataMode.set("account");
      // 다시 누를 자리를 치운다. 옮기기는 멱등이지만 두 번 올릴 까닭이 없다.
      panel.innerHTML = "";
      result.textContent = notes.join(" ");
      await settle(r, result);
      finish();
    } catch (e: any) {
      result.textContent = `${U.moveFail}: ${messageOf(e)}`;
    } finally {
      go.disabled = false;
    }
  });
}

/** 이 기기의 전부를 계정으로 올린다. **빠진 것이 없을 때만** `ok` 다. 모드는 안 바꾼다:
 *  부르는 쪽(설정의 끄기 · 로그인 뒤 한 번 묻는 화면)이 `ok` 를 보고 바꾼다.
 *
 *  올리기는 이 기기에 아무것도 안 쓴다(`/sync/push-all`). 멈춰도 잃는 것이 없고, 다시
 *  누르면 멱등이다. `notes` 는 사람에게 그대로 보여 줄 문장들이다. */
export async function moveDeviceIntoAccount(key: string): Promise<{ ok: boolean; notes: string[] }> {
  const U = COPY.ui.settingsUi;
  const job = await storage.pushAll(key);
  const done = await pollJob(job.job_id, undefined,
                             { timeoutMs: MOVE_TIMEOUT_MS, status: storage.job });
  const r = done.result || {};
  if (Number(r.missing) > 0) return { ok: false, notes: [U.moveMissing(Number(r.missing))] };
  const notes = [U.moveCounts(Number(r.nodes ?? 0), Number(r.docs ?? 0))];
  if (Number(r.kept_deleted) > 0) notes.push(U.moveKeptDeleted(Number(r.kept_deleted)));
  if (Number(r.left_behind) > 0) notes.push(U.moveLeftBehind(Number(r.left_behind)));
  return { ok: true, notes };
}

/** 정본과 창의 모드가 함께 바뀐 뒤 화면을 다시 읽는다(VIN-361).
 *  별도 클릭을 기다리면 설정·공간 선택·지도에 옛 그래프가 남는다.
 *  창만 새로 읽으므로 앱을 종료하거나 사이드카를 다시 띄우지는 않는다. */
function finish(): void {
  location.reload();
}
