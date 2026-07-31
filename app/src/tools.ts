import { api, cfg } from "./api";
import { COPY } from "./copy";
import { openSheet, sheetActions, type SheetHandle } from "./components";
import { atlasHooks } from "./topbar";

/** Tools (DEC D71) — 만들기 · 가져오기 · 공유가 한 모달에 산다.
 *
 *  여태 상단 바에 `가져오기`와 `공유` 두 버튼이 있었고, `새 문서`는 가져오기 시트의
 *  부 액션으로 숨어 있었다(D67). 셋은 사실 같은 종류의 일이다 — **지식이 드나드는
 *  문**. 문이 셋인데 자리가 셋이면 어디로 가야 할지 매번 고르게 된다.
 *
 *  탭은 세 갈래를 **한 층**에 눕힌다(중첩 금지): 상단에서 고르고, 아래는 그 갈래의
 *  일만 한다. 시트 규격(D63)은 그대로다 — 머리·몸·발, 그리고 Esc·✕·배경으로 닫힘.
 *
 *  ⚠ 만들기 탭의 링크는 **문서↔문서가 아니다.** 스토어에 문서 사이 관계 테이블이
 *  없다(_DocMeta 는 정체성 메타이고 관계는 개념 사이에만 있다). 그래서 각 문서의
 *  대표 개념 사이에 `relates_to` 를 긋고, 그 사실을 화면이 그대로 말한다. */

type Tab = "create" | "import" | "share";

const esc = (s: string) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  c === "&" ? "&amp;" : c === "<" ? "&lt;" : c === ">" ? "&gt;" : "&quot;");

type DocRow = { doc_id: string; title: string; primary_concept_id?: string;
                primary_concept_title?: string };

/** 사이드카 쓰기 큐는 단일 워커라 앞선 잡이 막히면 뒤가 전부 함께 선다(실측:
 *  문서 저장 직후 쓰기 요청이 영구히 응답하지 않는 상태가 재현된다). 그때 화면이
 *  "처리 중"에 영원히 머무르면 사용자는 무엇이 됐고 무엇이 안 됐는지 알 길이 없다.
 *  기다림에 한계를 두고 **실패로 정직하게 내려** 재시도 손잡이를 준다. */
const OP_TIMEOUT = 12_000;
function withTimeout<T>(p: Promise<T>, ms = OP_TIMEOUT): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const t = window.setTimeout(() => reject(new Error("timeout")), ms);
    p.then((v) => { window.clearTimeout(t); resolve(v); },
           (e) => { window.clearTimeout(t); reject(e); });
  });
}

export function openTools(tab: Tab = "create") {
  const T = COPY.ui.tools;
  const sheet = openSheet({ title: T.title, size: "browse" });
  let cleanup: (() => void) | null = null;

  const draw = (t: Tab) => {
    cleanup?.();
    cleanup = null;
    sheet.body.innerHTML = `
      <div class="tools-tabs">
        <span class="seg">
          ${(["create", "import", "share"] as Tab[]).map((k) =>
            `<button class="chip${k === t ? " active" : ""}" data-tab="${k}">${esc(T[k])}</button>`).join("")}
        </span>
        <span class="hint tools-sub"></span>
      </div>
      <div class="tools-pane" id="tools-pane"></div>`;
    sheet.body.querySelector<HTMLElement>(".tools-sub")!.textContent =
      t === "create" ? T.createSub : t === "import" ? T.importSub : T.shareSub;
    sheet.body.querySelectorAll<HTMLElement>("[data-tab]").forEach((b) =>
      b.addEventListener("click", () => draw(b.dataset.tab as Tab)));
    const pane = sheet.body.querySelector<HTMLElement>("#tools-pane")!;
    sheet.foot.innerHTML = "";
    if (t === "create") void renderCreate(pane, sheet, (fn) => { cleanup = fn; });
    else if (t === "import") renderImport(pane, sheet);
    else void renderShare(pane, sheet);
  };
  draw(tab);
}

// ── 가져오기 ────────────────────────────────────────────────────────────────
// 본문은 한 줄, 발에 `파일 고르기` 하나. `새 문서`는 만들기 탭으로 승격했다(D71).
function renderImport(pane: HTMLElement, sheet: SheetHandle) {
  pane.innerHTML = `<p class="hint">${COPY.ui.sheet.importTip}</p>`;
  sheet.foot.innerHTML = sheetActions({ id: "tl-pick", label: COPY.ui.sheet.pickPack });
  sheet.foot.querySelector("#tl-pick")!.addEventListener("click", async () => {
    sheet.close();
    const { pickAndImport } = await import("./sheets");
    pickAndImport();
  });
}

// ── 공유 ────────────────────────────────────────────────────────────────────
// 스코프 피커가 새로 붙는다. 여태 공유는 Atlas 훅에 매여 있어 "지금 보고 있는 곳"
// 하나뿐이었는데, Tools 는 어느 표면에서나 열리므로 범위를 물어야 한다.
async function renderShare(pane: HTMLElement, sheet: SheetHandle) {
  const T = COPY.ui.tools;
  const here = atlasHooks();
  pane.innerHTML = `<p class="hint">${COPY.ui.common.loading}</p>`;
  let domains: string[] = [];
  try {
    const res: any = await api.domains(cfg.owner);
    domains = (res.rows || []).map((r: any) => r.domain).filter(Boolean);
  } catch { /* 주제를 못 읽어도 전체 공유는 된다 — 선택지만 줄어든다 */ }
  if (!pane.isConnected) return;
  pane.innerHTML = `
    <p class="hint rail-sec">${esc(T.scope)}</p>
    <label class="list-row tools-opt"><input type="radio" name="tl-scope" value="all" checked />
      <span class="title">${esc(T.scopeAll)}</span></label>
    <label class="list-row tools-opt"${domains.length ? "" : ' style="opacity:.5"'}>
      <input type="radio" name="tl-scope" value="topic" ${domains.length ? "" : "disabled"} />
      <span class="title">${esc(T.scopeTopic)}</span>
      <select id="tl-topic" ${domains.length ? "" : "disabled"}>
        ${domains.map((d) => `<option value="${esc(d)}">${esc(d)}</option>`).join("")}
      </select></label>
    <label class="list-row tools-opt"${here ? "" : ' style="opacity:.5"'}>
      <input type="radio" name="tl-scope" value="here" ${here ? "" : "disabled"} />
      <span class="title">${esc(T.scopeHere)}</span>
      ${here ? "" : `<span class="hint">${esc(T.scopeHereOff)}</span>`}</label>`;
  sheet.foot.innerHTML = sheetActions(
    { id: "tl-go", label: T.go }, { id: "tl-cancel", label: COPY.ui.common.cancel });
  sheet.foot.querySelector("#tl-cancel")!.addEventListener("click", sheet.close);
  sheet.foot.querySelector("#tl-go")!.addEventListener("click", async () => {
    const pick = pane.querySelector<HTMLInputElement>("input[name=tl-scope]:checked")?.value;
    sheet.close();
    // `지금 보고 있는 곳`은 Atlas 가 자기 스코프를 안다 — 훅이 시트를 연다.
    if (pick === "here") { here?.share(); return; }
    const { openShareSheet } = await import("./sheets");
    if (pick === "topic") {
      const d = pane.querySelector<HTMLSelectElement>("#tl-topic")?.value;
      openShareSheet(d ? { domain: d } : {});
    } else openShareSheet({});
  });
}

// ── 만들기 ──────────────────────────────────────────────────────────────────
async function renderCreate(pane: HTMLElement, sheet: SheetHandle,
                            setCleanup: (fn: () => void) => void) {
  const T = COPY.ui.tools;
  pane.innerHTML = `
    <div id="tl-ed"></div>
    <p class="hint rail-sec">${esc(T.linkHeader)}</p>
    <p class="hint">${esc(T.linkHint)}</p>
    <input id="tl-q" class="rail-filter" type="search" autocomplete="off"
           placeholder="${esc(T.linkFilter)}" />
    <div class="sheet-list" id="tl-docs"><p class="hint">${COPY.ui.common.loading}</p></div>
    <div id="tl-result" role="status"></div>`;

  const { mountDocEditor } = await import("./doc-editor");
  let docId = "";
  const handle = await mountDocEditor(pane.querySelector<HTMLElement>("#tl-ed")!, {
    owner: cfg.owner,
    onSaved: (id) => { docId = id; },
  });
  setCleanup(() => handle.dispose());

  // 연결 후보 — 대표 개념이 없는 문서는 연결할 자리가 없다(고를 수는 있어도 그을
  // 곳이 없다). 비활성으로 두고 이유를 밝힌다.
  let docs: DocRow[] = [];
  const picked = new Set<string>();
  const listEl = pane.querySelector<HTMLElement>("#tl-docs")!;
  const drawList = () => {
    const q = (pane.querySelector<HTMLInputElement>("#tl-q")?.value || "").trim().toLowerCase();
    const rows = docs.filter((d) => d.doc_id !== docId
      && (!q || (d.title || d.doc_id).toLowerCase().includes(q)));
    listEl.innerHTML = rows.length === 0
      ? `<p class="hint">${esc(T.linkNone)}</p>`
      : rows.map((d) => {
          const ok = !!d.primary_concept_id;
          return `<label class="list-row"${ok ? "" : ' style="opacity:.55"'}>
            <input type="checkbox" data-doc="${esc(d.doc_id)}" ${ok ? "" : "disabled"}
                   ${picked.has(d.doc_id) ? "checked" : ""} />
            <span class="title">${esc(d.title || d.doc_id)}</span>
            <span class="meta">${ok ? esc(d.primary_concept_title || "") : esc(T.noConcept)}</span>
          </label>`;
        }).join("");
    listEl.querySelectorAll<HTMLInputElement>("input[data-doc]").forEach((cb) =>
      cb.addEventListener("change", () => {
        if (cb.checked) picked.add(cb.dataset.doc!); else picked.delete(cb.dataset.doc!);
        drawFoot();
      }));
  };
  const drawFoot = () => {
    sheet.foot.innerHTML = sheetActions(
      { id: "tl-done", label: T.linkCta(picked.size) },
      { id: "tl-cancel", label: COPY.ui.common.cancel });
    sheet.foot.querySelector("#tl-cancel")!.addEventListener("click", sheet.close);
    sheet.foot.querySelector("#tl-done")!.addEventListener("click", () => void finish());
  };
  pane.querySelector<HTMLInputElement>("#tl-q")!.addEventListener("input", drawList);
  drawFoot();
  try {
    const res = await api.docList(cfg.owner);
    docs = (res.docs as DocRow[]) || [];
  } catch { docs = []; }
  if (pane.isConnected) drawList();

  /** 저장 → 개념 파생 → 대표 개념 사이에 relates_to. **원자적이지 않다** —
   *  문서 저장과 링크는 별개 호출이라 링크만 실패할 수 있다. 그 사실을 숨기지
   *  않고 건별로 보고하고, 실패분만 다시 시도할 길을 준다. */
  async function finish() {
    const out = pane.querySelector<HTMLElement>("#tl-result")!;
    if (picked.size === 0) { sheet.close(); return; }
    if (!docId) { out.innerHTML = `<p class="hint">${esc(T.saveFirst)}</p>`; return; }
    out.innerHTML = `<p class="hint">${esc(T.linking)}</p>`;
    try {
      // 내 문서의 대표 개념을 얻으려면 먼저 개념이 있어야 한다 — 추출을 반영한다.
      await withTimeout(api.docExtract(cfg.owner, docId, true));
      const fresh = await withTimeout(api.docList(cfg.owner));
      const rows = (fresh.docs as DocRow[]) || [];
      const mine = rows.find((d) => d.doc_id === docId);
      const from = mine?.primary_concept_id;
      if (!from) { out.innerHTML = `<p class="hint">${esc(T.noConcept)}</p>`; return; }
      const targets = [...picked]
        .map((id) => rows.find((d) => d.doc_id === id))
        .filter((d): d is DocRow => !!d?.primary_concept_id);
      let ok = 0;
      const failed: string[] = [];
      for (const t of targets) {
        try {
          const r: any = await withTimeout(api.ingestFragment({
            nodes: [{ type: "concept", id: from, title: mine?.primary_concept_title || from },
                    { type: "concept", id: t.primary_concept_id, title: t.primary_concept_title || "" }],
            edges: [{ type: "relates_to", from, to: t.primary_concept_id,
                      evidence: COPY.ui.tools.linkHeader, confidence: 0.5 }],
          }));
          if (r?.executed === false) throw new Error(r.error || "link failed");
          ok += 1;
        } catch { failed.push(t.doc_id); }
      }
      if (failed.length === 0) {
        out.innerHTML = `<p>${esc(T.linked(ok))}</p>`;
        window.setTimeout(() => { if (out.isConnected) sheet.close(); }, 900);
        return;
      }
      out.innerHTML = `<p>${esc(T.linkPartial(ok, failed.length))}</p>
        <button class="btn" id="tl-retry">${esc(T.retry)}</button>`;
      out.querySelector("#tl-retry")!.addEventListener("click", () => {
        picked.clear();
        failed.forEach((id) => picked.add(id));
        void finish();
      });
    } catch {
      out.innerHTML = `<div class="error-banner">${COPY.packs.error}</div>`;
    }
  }
}
