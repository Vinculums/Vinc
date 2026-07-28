/** Share / Import 시트 — 공통 규격(D63 `openSheet`)을 쓴다.
 *  공유 시트는 "나가기 전에 무엇이 나가는지 전부 보여준다"는 원칙 그대로
 *  (정본 §5.1): 스코프 요약 · 포함 목록(개별 제외) · 미포함 고지 · 저장. */
import { api, cfg } from "./api";
import { toBytes } from "./packfile";
import { COPY } from "./copy";
import { openSheet, sheetActions } from "./components";

const esc = (s: string) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  c === "&" ? "&amp;" : c === "<" ? "&lt;" : c === ">" ? "&gt;" : "&quot;");

export async function openShareSheet(scope: { ids?: string[]; seed?: string; domain?: string; depth?: number }) {
  const sheet = openSheet({ title: COPY.ui.sheet.shareTitle,
                            subtitle: COPY.ui.sheet.shareSub });
  sheet.body.innerHTML = `<p class="hint">${COPY.ui.sheet.preparing}</p>`;
  try {
    const pack = await api.share(cfg.owner, {
      ids: scope.ids?.join(","), seed: scope.seed, domain: scope.domain, depth: scope.depth,
    });
    const nodes: any[] = pack.fragment?.nodes || [];
    const c = pack.manifest?.counts || {};
    sheet.body.innerHTML = `
      <div class="sheet-spec">
        <div><span class="hint">${COPY.ui.sheet.specScope}</span><b>${esc(
          scope.seed ? COPY.ui.sheet.scopeSeed(scope.depth ?? 2)
          : scope.domain ? scope.domain
          : scope.ids?.length ? COPY.ui.sheet.scopeSelection(scope.ids.length)
          : COPY.ui.sheet.scopeAll)}</b></div>
        <div><span class="hint">${COPY.ui.sheet.specConcepts}</span><b>${c.concepts ?? 0}</b></div>
        <div><span class="hint">${COPY.ui.sheet.specLinks}</span><b>${c.edges ?? 0}</b></div>
        <div><span class="hint">${COPY.ui.sheet.specFormat}</span><b>.vincpack</b></div>
      </div>
      <div class="sheet-list">
        ${nodes.map((n, i) => `
          <label class="list-row" style="cursor:pointer">
            <input type="checkbox" checked data-i="${i}" />
            <span class="title">${esc(n.title)}</span><span class="meta">${esc(n.domain || n.type)}</span>
          </label>`).join("") || `<p class="hint">${COPY.ui.sheet.nothing}</p>`}
      </div>
      <p class="hint">${COPY.ui.sheet.keepNote}</p>`;
    sheet.foot.innerHTML = sheetActions(
      { id: "sheet-save", label: COPY.ui.sheet.save },
      { id: "sheet-cancel", label: COPY.ui.common.cancel });
    sheet.foot.querySelector("#sheet-cancel")!.addEventListener("click", sheet.close);
    sheet.foot.querySelector("#sheet-save")!.addEventListener("click", () => {
      const excluded = new Set([...sheet.body.querySelectorAll<HTMLInputElement>("input[type=checkbox]")]
        .filter((cb) => !cb.checked).map((cb) => nodes[Number(cb.dataset.i)].id));
      const keep = nodes.filter((n) => !excluded.has(n.id));
      const keepIds = new Set(keep.map((n) => n.id));
      const edges = (pack.fragment?.edges || []).filter((e: any) => keepIds.has(e.from) && keepIds.has(e.to));
      const out = { ...pack, fragment: { nodes: keep, edges },
        manifest: { ...pack.manifest, counts: { ...c, concepts: keep.filter((n) => n.type === "concept").length, edges: edges.length } } };
      delete (out as any).executed;
      // D55 — 쓰기는 zip 컨테이너로. 원문(documents)을 실은 팩이 통짜 JSON 이면
      // 수 MB 로 붓고 문서 하나만 꺼낼 수도 없다(실측 1.6MB → 461KB, 28%).
      void toBytes(out).then((bytes) => {
        const blob = new Blob([bytes as BlobPart], { type: "application/zip" });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = `vinc-share-${scope.domain || scope.seed || "slice"}.vincpack`;
        a.click(); URL.revokeObjectURL(a.href);
        sheet.close();
      });
    });
  } catch {
    sheet.body.innerHTML = `<div class="error-banner">${COPY.packs.error}</div>`;
  }
}

export function openImportSheet(pack: any, onDone?: (res: any) => void) {
  const m = pack?.manifest || {}; const c = m.counts || {};
  const n = (pack?.fragment?.nodes || []).length;
  const sheet = openSheet({ title: COPY.ui.sheet.importTitle,
                            subtitle: COPY.ui.sheet.importSub });
  sheet.body.innerHTML = `
    <div class="sheet-spec">
      <div><span class="hint">${COPY.ui.sheet.specCreated}</span><b>${esc(String(m.created || "?").slice(0, 10))}</b></div>
      <div><span class="hint">${COPY.ui.sheet.specConcepts}</span><b>${c.concepts ?? n}</b></div>
      <div><span class="hint">${COPY.ui.sheet.specLinks}</span><b>${c.edges ?? 0}</b></div>
      <div><span class="hint">${COPY.ui.sheet.specDocs}</span><b>${(pack?.documents || []).length}</b></div>
    </div>
    <p>${COPY.ui.sheet.updateNote(c.concepts ?? n, c.edges ?? 0, c.records ?? 0)}</p>
    <div id="sheet-result" role="status"></div>`;
  sheet.foot.innerHTML = sheetActions(
    { id: "sheet-import", label: COPY.ui.sheet.importMine },
    { id: "sheet-cancel", label: COPY.ui.common.cancel });
  sheet.foot.querySelector("#sheet-cancel")!.addEventListener("click", sheet.close);
  sheet.foot.querySelector("#sheet-import")!.addEventListener("click", async () => {
    const out = sheet.body.querySelector<HTMLElement>("#sheet-result")!;
    out.innerHTML = `<p class="hint">${COPY.packs.importing}</p>`;
    try {
      const res = await api.importPack(pack, cfg.owner);
      if (res.executed === false) throw new Error(res.error);
      const mm = res.merged?.nodes || {};
      out.innerHTML = `<p>${COPY.packs.imported(mm.created ?? 0, mm.matched ?? 0)}</p>`;
      sheet.foot.innerHTML = sheetActions(
        { id: "sheet-go", label: COPY.ui.sheet.viewAtlas });
      sheet.foot.querySelector("#sheet-go")!.addEventListener("click", () => {
        sheet.close();
        location.hash = "#atlas";
        location.reload();   // 그래프가 통째로 바뀌었다 — 현재 층을 새로 세운다
      });
      onDone?.(res);
    } catch {
      out.innerHTML = `<div class="error-banner">${COPY.packs.error}</div>`;
    }
  });
}

/** 파일에서 팩을 골라 가져오기 시트를 연다(상단 바 `가져오기`의 실체).
 *  파일 입력은 만들었다 버리는 일회용 — 셸에 숨은 input 을 남기지 않는다. */
/** 가져오기 진입 — **기존 가져오기 시트 그대로**에 `새 문서` 만 더한다(D67).
 *
 *  두 갈래를 큰 버튼으로 벌려 놓았더니 시트마다 생김새가 달라졌다. 규격(머리·몸·발)
 *  안에서 해결한다: 몸은 무엇을 하는 자리인지 한 줄, 발은 부 액션(새 문서) 왼쪽 ·
 *  주 액션(팩 고르기) 오른쪽 — 다른 모든 시트와 같은 자리. */
export function openIntakeSheet(onDone?: (res: any) => void) {
  const sheet = openSheet({ title: COPY.ui.sheet.intakeTitle,
                            subtitle: COPY.ui.sheet.intakeSub });
  sheet.body.innerHTML = `<p class="hint">${COPY.ui.sheet.importTip}</p>`;
  sheet.foot.innerHTML = sheetActions(
    { id: "in-pack", label: COPY.ui.sheet.pickPack },
    undefined,
    { id: "in-new", label: COPY.ui.atlasx.edNew });
  sheet.foot.querySelector("#in-pack")!.addEventListener("click", () => {
    sheet.close();
    pickAndImport(onDone);
  });
  sheet.foot.querySelector("#in-new")!.addEventListener("click", async () => {
    sheet.close();
    const { openDocSheet } = await import("./doc-editor");
    void openDocSheet({ owner: cfg.owner });
  });
}

export function pickAndImport(onDone?: (res: any) => void) {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = ".vincpack,.json,application/json,application/zip";
  input.style.display = "none";
  document.body.appendChild(input);
  input.addEventListener("change", async () => {
    const f = input.files?.[0];
    input.remove();
    if (!f) return;
    try {
      const { readPackFile } = await import("./packfile");
      openImportSheet(await readPackFile(f), onDone);
    } catch {
      const sheet = openSheet({ title: COPY.ui.sheet.importTitle });
      sheet.body.innerHTML = `<div class="error-banner">${COPY.packs.error}</div>`;
    }
  });
  input.click();
}
