/** 관례 시트(GM-8) — 어시스턴트가 쓰기 전에 읽는 것을 사람이 관리하는 자리.
 *
 *  한 시트에 두 구역을 둔다. **사전 예방**(관례)과 **사후 교정**(재가)은 한 쌍인데
 *  화면이 갈리면 "관례를 적었는데 왜 여전히 제안이 쌓이지"를 서로 다른 자리에서
 *  겪게 된다.
 *
 *  ⚠ 생성 구역(마커 사이)은 **읽기 전용**이다. 스토어에서 옮겨 적은 사본이라 손으로
 *  고쳐 봐야 다음 [다시 생성]이 지운다 — 고칠 수 있게 두는 것 자체가 거짓말이다.
 *  그래서 편집기를 하나로 두지 않고 앞산문·생성구역·뒷산문 셋으로 쪼갠다.
 *
 *  ⚠ 재가 구역은 **셸 전용**이다. 주제 어휘(themes/types)에는 owner 차원이 없어
 *  계정 프록시가 의도적으로 막는다(`vinc_mcp/http_server.py` _PROXY_OPS 주석) —
 *  웹에서 열면 남의 어휘를 만지게 된다. 웹에서는 관례 구역만 보인다.
 */
import { api, canWriteHere, cfg, remote } from "./api";
import { COPY } from "./copy";
import { esc, sheetActions } from "./components";

const GEN_START = /^<!--\s*vinc:generated start\b.*-->\s*$/;
const GEN_END = /^<!--\s*vinc:generated end\s*-->\s*$/;

/** 본문을 (앞산문, 생성구역, 뒷산문)으로 가른다. 마커가 정상 쌍이 아니면 null. */
function splitConventions(text: string): [string, string, string] | null {
  const lines = (text || "").replace(/\r\n/g, "\n").split("\n");
  const s = lines.findIndex((l) => GEN_START.test(l));
  const e = lines.findIndex((l) => GEN_END.test(l));
  if (s < 0 || e < 0 || e < s) return null;
  if (lines.filter((l) => GEN_START.test(l)).length !== 1) return null;
  return [lines.slice(0, s).join("\n"), lines.slice(s, e + 1).join("\n"),
          lines.slice(e + 1).join("\n")];
}

/** 사람이 판정할 것만 남긴다.
 *
 *  주제를 만들면 내장 어휘가 `status:proposed, provenance:system_draft` 로 함께
 *  깔린다(theme_store 독스트링). 그걸 세면 **첫 실행부터 뱃지가 11** 이 되고, 사용자가
 *  제안한 적 없는 것 8개를 판정하라고 요구하게 된다 — 그 순간 뱃지는 신호이기를
 *  그만둔다. 재가를 기다리는 것은 **어시스턴트가 실제로 제안한 것**(ingest_proposed)뿐이다. */
const isPending = (t: any) => t?.provenance === "ingest_proposed";

/** 상단 바 뱃지에 쓰는 제안 적체 수. 어휘를 못 읽는 환경(웹, 셸 계정 모드)에서는 0 이다.
 *  어휘 재가는 사이드카 전용이고 계정 프록시가 의도적으로 막는다. */
async function proposedCount(): Promise<number> {
  if (remote()) return 0;
  try {
    const th = await api.themes();
    const id = th?.active_theme_id;
    if (!id) return 0;
    const res = await api.types(id, "proposed");
    return (res?.types || []).filter(isPending).length;
  } catch { return 0; }
}

export function openConventions() {
  void import("./surfaces/settings").then((m) => m.openSettings("graphmd"));
}

/** 설정 시트의 GRAPH.md 분류 안에 그린다. 시트를 새로 열지 않는다. */
export async function mountConventions(host: HTMLElement, sheet: { body: HTMLElement; foot: HTMLElement; close: () => void }) {
  const C = COPY.ui.conv;
  host.innerHTML = `<p class="hint">${C.loading}</p>`;

  let themeId = "";
  let text = "";
  // 관례 문서의 id 는 owner 마다 다르다(서버가 정한다). 여기서 문자열을 지어내면
  // 한 자리가 더 생기고, 그 자리가 서버와 어긋나면 저장은 성공하는데 다음에 읽히는
  // 것은 옛 본문이 된다. **읽기 응답이 준 값만** 쓴다.
  let docId = "";
  let savedText = "";
  let busy = false;
  // 읽기만 되는 공간(커뮤니티의 GRAPH.md 는 규칙 페이지라 운영자 몫, 팀 열람자)에서는 본문만 보이고
  // 저장·재생성·재가를 걸지 않는다. `canWriteHere` 가 실패하면 연다: 막는 것은 서버다.
  const writable = await canWriteHere().catch(() => true);

  async function draw() {
    // 설정의 분류들은 발(`sheet.foot`)을 함께 쓴다. 읽기를 기다리는 사이 다른 분류로
    // 옮겼으면 이 판은 떨어져 나갔다 — 여기서 발을 쓰면 저장·재생성(실제 쓰기)
    // 버튼이 다른 분류 밑에 선다.
    if (!host.isConnected) return;
    if (!writable) {
      host.innerHTML = `<p class="hint">${esc(COPY.ui.settingsUi.readOnlySpace)}</p>
        <pre class="conv-gen" aria-readonly="true">${esc(text)}</pre>`;
      sheet.foot.innerHTML = "";
      return;
    }
    const parts = splitConventions(text);
    const broken = parts === null;
    const [head, gen, tail] = parts || [text, "", ""];
    host.innerHTML = `
      <div class="conv">
        ${broken ? `<p class="hint conv-warn">${C.broken}</p>` : ""}
        <label class="hint" for="conv-head">${C.prose}</label>
        <textarea id="conv-head" class="conv-ta conv-ta-main" spellcheck="false">${esc(head)}</textarea>
        ${broken ? "" : `
          <details class="conv-fold">
            <summary>${C.generated}</summary>
            <pre class="conv-gen" aria-readonly="true">${esc(gen)}</pre>
            <textarea id="conv-tail" class="conv-ta" spellcheck="false" aria-label="${esc(C.proseAfter)}">${esc(tail)}</textarea>
          </details>`}
        <div id="conv-ratify"></div>
      </div>`;
    sheet.foot.innerHTML = sheetActions(
      { id: "conv-save", label: C.save },
      { id: "conv-rebuild", label: C.rebuild })
      + '<span class="hint" id="conv-status" role="status" aria-live="polite"></span>';

    savedText = compose();
    host.querySelectorAll<HTMLTextAreaElement>(".conv-ta").forEach((input) =>
      input.addEventListener("input", () => status(compose() === savedText ? "" : C.unsaved)));

    sheet.foot.querySelector<HTMLElement>("#conv-save")!.addEventListener("click", save);
    sheet.foot.querySelector<HTMLElement>("#conv-rebuild")!.addEventListener("click", rebuild);
    await drawRatify();
  }

  function status(message: string) {
    if (!host.isConnected) return;
    const line = sheet.foot.querySelector<HTMLElement>("#conv-status");
    if (line) line.textContent = message;
  }

  function setBusy(value: boolean) {
    busy = value;
    if (!host.isConnected) return;
    sheet.foot.querySelectorAll<HTMLButtonElement>("#conv-save, #conv-rebuild")
      .forEach((button) => { button.disabled = value; });
  }

  function compose(): string {
    const head = host.querySelector<HTMLTextAreaElement>("#conv-head")?.value ?? "";
    const gen = host.querySelector<HTMLElement>(".conv-gen")?.textContent ?? "";
    const tail = host.querySelector<HTMLTextAreaElement>("#conv-tail")?.value ?? "";
    return gen ? `${head}\n${gen}\n${tail}`.replace(/\n{3,}/g, "\n\n") : head;
  }

  /** 편집분을 관례 문서 자리에 쓴다. id 를 모르면 **쓰지 않는다** — 모르는 채로
   *  보내면 새 문서 하나가 생기고 관례는 그대로 남는다. */
  async function put(snapshot = compose()): Promise<boolean> {
    if (!docId) return false;
    const res = await api.docPut(cfg.owner, snapshot, docId, "GRAPH.md",
                                 undefined, "vinc:conventions");
    return !!res.executed;
  }

  async function save() {
    if (busy) return;
    const snapshot = compose();
    setBusy(true);
    status(C.saving);
    try {
      if (await put(snapshot)) {
        savedText = snapshot;
        status(compose() === savedText ? C.saved : C.unsaved);
      } else status(C.saveFailed);
    } catch { status(C.saveFailed); }
    finally { setBusy(false); }
  }

  async function rebuild() {
    if (busy) return;
    setBusy(true);
    status(C.saving);
    // 재생성 중 새 편집이 응답 본문에 덮이지 않도록 잠깐 읽기 전용으로 둔다.
    host.querySelectorAll<HTMLTextAreaElement>(".conv-ta").forEach((input) => { input.readOnly = true; });
    let didSave = false;
    try {
      // 먼저 편집분을 저장한다 — 안 그러면 서버가 읽는 것은 옛 산문이고,
      // 방금 쓴 문장이 [다시 생성] 한 번에 사라진 것처럼 보인다.
      if (!await put()) { status(C.saveFailed); return; }
      didSave = true;
      savedText = compose();
      const res = await api.conventionsBuild(cfg.owner);
      if (res?.executed && res.text) { text = res.text; await draw(); status(C.saved); }
      else status(C.rebuildFailed);
    } catch { status(didSave ? C.rebuildFailed : C.saveFailed); }
    finally {
      host.querySelectorAll<HTMLTextAreaElement>(".conv-ta").forEach((input) => { input.readOnly = false; });
      setBusy(false);
    }
  }

  async function drawRatify() {
    const hostEl = host.querySelector<HTMLElement>("#conv-ratify");
    if (!hostEl) return;
    if (remote()) { hostEl.innerHTML = `<p class="hint">${C.webOnly}</p>`; return; }
    let types: any[] = [];
    try {
      const th = await api.themes();
      themeId = th?.active_theme_id || "";
      if (themeId) types = ((await api.types(themeId, "proposed"))?.types || []).filter(isPending);
    } catch { hostEl.innerHTML = ""; return; }
    if (!themeId) { hostEl.innerHTML = ""; return; }
    // 대기가 있으면 펼친 채로, 없으면 접어 둔다 — 할 일이 없는 상자가 자리를
    // 차지하지 않게. 대기 수는 접혀 있어도 요약 줄에 그대로 보인다.
    hostEl.innerHTML = `
      <details class="conv-fold"${types.length ? " open" : ""}>
      <summary>${C.pending(types.length)}</summary>
      ${types.length === 0 ? `<p class="hint">${C.noPending}</p>` : `
        <div class="sheet-list">${types.map((t, i) => `
          <div class="list-row" data-row="${i}">
            <span class="title">${esc(t.name)}</span>
            <span class="meta">${esc(t.kind === "edge" ? C.kindEdge : C.kindNode)}</span>
            <span class="conv-acts">
              <button class="btn" data-act="rename" data-name="${esc(t.name)}">${C.rename}</button>
              <button class="btn" data-act="reject" data-name="${esc(t.name)}">${C.reject}</button>
              <button class="btn btn-primary" data-act="ratify" data-name="${esc(t.name)}">${C.ratify}</button>
            </span>
          </div>`).join("")}</div>`}</details>`;
    hostEl.querySelectorAll<HTMLElement>("[data-act]").forEach((b) =>
      b.addEventListener("click", () => act(b.dataset.act!, b.dataset.name!, b)));
  }

  async function act(kind: string, name: string, btn: HTMLElement) {
    if (kind === "rename") return startRename(name, btn);
    btn.setAttribute("disabled", "true");
    try {
      if (kind === "ratify") await api.typeRatify(themeId, name);
      if (kind === "reject") await api.typeReject(themeId, name);
      await drawRatify();
      refreshBadge();
    } catch { btn.removeAttribute("disabled"); }
  }

  function startRename(name: string, btn: HTMLElement) {
    const row = btn.closest(".list-row");
    const title = row?.querySelector<HTMLElement>(".title");
    if (!row || !title) return;
    title.innerHTML = `<input class="conv-rename" value="${esc(name)}" />`;
    const input = title.querySelector<HTMLInputElement>(".conv-rename")!;
    input.focus();
    input.select();
    const commit = async () => {
      const next = input.value.trim();
      if (!next || next === name) return drawRatify();
      try { await api.typeRename(themeId, name, next); } catch { /* 이름만 되돌린다 */ }
      await drawRatify();
    };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") commit();
      if (e.key === "Escape") drawRatify();
    });
    input.addEventListener("blur", commit);
  }

  try {
    const res = await api.conventions(cfg.owner);
    text = res?.text || "";
    docId = res?.doc_id || "";
  } catch {
    host.innerHTML = `<p class="hint">${C.loadFailed}</p>`;
    return;
  }
  await draw();
}

/** 제안 적체 뱃지. 레일 설정 버튼 · GRAPH.md 분류 · (숨긴) 상단 바 자리에 같은 수를 그린다. */
export function refreshBadge() {
  const paint = (el: HTMLElement | null, n: number) => {
    if (!el) return;
    el.textContent = n > 0 ? String(n) : "";
    el.hidden = n === 0;
  };
  proposedCount().then((n) => {
    paint(document.getElementById("tb-conv-badge"), n);
    paint(document.getElementById("rail-settings-badge"), n);
    paint(document.getElementById("settings-graphmd-badge"), n);
  });
}
