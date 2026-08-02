/** Share / Import 시트 — 공통 규격(D63 `openSheet`)을 쓴다.
 *  공유 시트는 "나가기 전에 무엇이 나가는지 전부 보여준다"는 원칙 그대로
 *  (정본 §5.1): 스코프 요약 · 포함 목록(개별 제외) · 미포함 고지 · 저장. */
import { api, cfg, community, WEB, type Scope } from "./api";
import { toBytes } from "./packfile";
import { COPY } from "./copy";
import { openSheet, sheetActions } from "./components";

const esc = (s: string) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  c === "&" ? "&amp;" : c === "<" ? "&lt;" : c === ">" ? "&gt;" : "&quot;");

/**
 * `opts.from` 은 팩을 **어느 스페이스에서** 뜰지, `opts.into` 는 목적지를 미리
 * 정할지를 말한다. 둘 다 없으면 현재 스페이스에서 떠서 목적지를 물어보는
 * 기존 동작이다.
 *
 * 개인 → 팀 불러오기가 이 두 옵션으로 표현된다(`from` = 개인, `into` = 팀).
 * 미리보기·개별 제외 화면을 공유와 **똑같이** 쓰는 이유는, 나가는 것을 고르는
 * 일과 들여오는 것을 고르는 일이 사용자에게 같은 판단이기 때문이다.
 */
export async function openShareSheet(
  scope: { ids?: string[]; seed?: string; domain?: string; depth?: number },
  opts: { from?: Scope; into?: Scope; intoLabel?: string } = {},
) {
  const sheet = openSheet({ title: COPY.ui.sheet.shareTitle,
                            subtitle: COPY.ui.sheet.shareSub });
  sheet.body.innerHTML = `<p class="hint">${COPY.ui.sheet.preparing}</p>`;
  try {
    const pack = await api.share(cfg.owner, {
      ids: scope.ids?.join(","), seed: scope.seed, domain: scope.domain, depth: scope.depth,
    }, opts.from);
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
      // 목적지가 이미 정해진 흐름(개인 → 팀 불러오기)은 묻지 않는다.
      if (opts.into) {
        const U = COPY.ui.sheet;
        const where = opts.intoLabel || U.destTeam;
        sheet.body.innerHTML = `<p class="hint">${esc(U.destWorking)}</p>`;
        sheet.foot.innerHTML = "";
        void api.importPack(out, cfg.owner, opts.into).then(
          () => {
            sheet.body.innerHTML = `<p class="hint">${esc(U.destDone(keep.length, where))}</p>`;
            sheet.foot.innerHTML = sheetActions({ id: "sheet-done", label: U.destClose });
            sheet.foot.querySelector("#sheet-done")!.addEventListener("click", sheet.close);
          },
          () => { sheet.body.innerHTML = `<div class="error-banner">${esc(U.destFailed)}</div>`; },
        );
        return;
      }
      void chooseDestination(sheet, out, keep.length,
        `vinc-share-${scope.domain || scope.seed || "slice"}.vincpack`);
    });
  } catch {
    sheet.body.innerHTML = `<div class="error-banner">${COPY.packs.error}</div>`;
  }
}

/** D55 — 쓰기는 zip 컨테이너로. 원문(documents)을 실은 팩이 통짜 JSON 이면
 *  수 MB 로 붓고 문서 하나만 꺼낼 수도 없다(실측 1.6MB → 461KB, 28%). */
async function downloadPack(out: Record<string, unknown>, filename: string): Promise<void> {
  const bytes = await toBytes(out);
  const blob = new Blob([bytes as BlobPart], { type: "application/zip" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

type Sheet = ReturnType<typeof openSheet>;
type Dest = { key: string; label: string; note: string; where: string; run: () => Promise<void> };

/**
 * 목적지 고르기 — 같은 팩을 스페이스·커뮤니티·파일 중 하나로 보낸다.
 *
 * 스페이스를 건너는 이동은 전부 **한 가지 동작**이다: 한쪽에서 팩을 만들고
 * 다른 쪽으로 적재한다. 그래서 팀→개인 공유와 개인→팀 불러오기가 같은 코드를
 * 쓰고, 둘 중 하나만 고쳐지는 일이 생기지 않는다.
 *
 * 고를 것이 하나뿐이면 묻지 않는다 — 셸 모드에는 계정이 없어 파일밖에 없고,
 * 거기에 모달을 띄우면 선택 없는 선택지를 강요하는 꼴이 된다.
 */
async function chooseDestination(
  sheet: Sheet, out: any, conceptCount: number, filename: string,
): Promise<void> {
  const U = COPY.ui.sheet;
  const dests: Dest[] = [];
  // 팀에 있을 때만 "내 스페이스"가 목적지가 된다(개인에서 개인은 무의미).
  if (cfg.team) {
    dests.push({
      key: "personal", label: U.destPersonal, note: U.destPersonalNote, where: U.destPersonal,
      run: async () => { await api.importPack(out, cfg.owner, { team: null }); },
    });
  }
  // 커뮤니티는 계정 세션이 필요하다 — 셸·로컬 dev 에는 세션이 없다.
  if (WEB) {
    dests.push({
      key: "community", label: U.destCommunity, note: U.destCommunityNote, where: U.destCommunity,
      run: () => publishToCommunity(sheet, out),
    });
  }
  dests.push({
    key: "file", label: U.destFile, note: U.destFileNote, where: U.destFile,
    run: () => downloadPack(out, filename),
  });

  if (dests.length === 1) {
    await dests[0]!.run();
    sheet.close();
    return;
  }

  sheet.body.innerHTML = `
    <p class="hint">${esc(U.destSub)}</p>
    <div class="sheet-list">
      ${dests.map((d) => `
        <button type="button" class="list-row" data-dest="${d.key}" style="cursor:pointer;width:100%;text-align:left">
          <span class="title">${esc(d.label)}</span><span class="meta">${esc(d.note)}</span>
        </button>`).join("")}
    </div>`;
  sheet.foot.innerHTML = sheetActions({ id: "sheet-cancel", label: COPY.ui.common.cancel });
  sheet.foot.querySelector("#sheet-cancel")!.addEventListener("click", sheet.close);

  for (const d of dests) {
    sheet.body.querySelector(`[data-dest="${d.key}"]`)!.addEventListener("click", () => {
      // 커뮤니티는 자체 폼으로 이어지므로 여기서 완료 화면을 그리지 않는다.
      if (d.key === "community") { void d.run(); return; }
      sheet.body.innerHTML = `<p class="hint">${esc(U.destWorking)}</p>`;
      sheet.foot.innerHTML = "";
      void d.run().then(
        () => {
          sheet.body.innerHTML = `<p class="hint">${esc(U.destDone(conceptCount, d.where))}</p>`;
          sheet.foot.innerHTML = sheetActions({ id: "sheet-done", label: COPY.ui.sheet.destClose });
          sheet.foot.querySelector("#sheet-done")!.addEventListener("click", sheet.close);
        },
        () => { sheet.body.innerHTML = `<div class="error-banner">${esc(U.destFailed)}</div>`; },
      );
    });
  }
}

/** 커뮤니티 게시 — 이름·설명·동의를 받는다. 동의는 기본값이 꺼짐이어야 한다
 *  (데이터가 공개되는 일이라 무심코 지나칠 수 있으면 안 된다). */
async function publishToCommunity(sheet: Sheet, out: unknown): Promise<void> {
  const P = COPY.ui.packsUi;
  sheet.body.innerHTML = `
    <p class="hint">${esc(P.publishTitle)}</p>
    <label class="pack-field">${esc(P.publishName)}
      <input id="pub-name" type="text" maxlength="80" />
    </label>
    <label class="pack-field">${esc(P.publishDesc)}
      <input id="pub-desc" type="text" maxlength="200" />
    </label>
    <label class="list-row" style="cursor:pointer">
      <input id="pub-consent" type="checkbox" />
      <span class="meta">${esc(P.publishConsent)}</span>
    </label>
    <p class="hint" id="pub-err"></p>`;
  sheet.foot.innerHTML = sheetActions(
    { id: "sheet-pub", label: P.publishGo },
    { id: "sheet-cancel", label: COPY.ui.common.cancel });
  sheet.foot.querySelector("#sheet-cancel")!.addEventListener("click", sheet.close);
  sheet.foot.querySelector("#sheet-pub")!.addEventListener("click", () => {
    const name = (sheet.body.querySelector("#pub-name") as HTMLInputElement).value.trim();
    const desc = (sheet.body.querySelector("#pub-desc") as HTMLInputElement).value.trim();
    const ok = (sheet.body.querySelector("#pub-consent") as HTMLInputElement).checked;
    const err = sheet.body.querySelector("#pub-err")!;
    if (!name) { err.textContent = P.publishNeedName; return; }
    if (!ok) { err.textContent = P.publishNeedConsent; return; }
    err.textContent = COPY.ui.sheet.destWorking;
    void community.publish(name, desc, out).then(
      () => {
        sheet.body.innerHTML = `<p class="hint">${esc(
          COPY.ui.sheet.destDoneCommunity)}</p>`;
        sheet.foot.innerHTML = sheetActions({ id: "sheet-done", label: COPY.ui.sheet.destClose });
        sheet.foot.querySelector("#sheet-done")!.addEventListener("click", sheet.close);
      },
      () => { err.textContent = COPY.ui.sheet.destFailed; },
    );
  });
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

/** 파일에서 팩·문서를 골라 해당 시트를 연다. 파일 입력은 만들었다 버리는 일회용 —
 *  셸에 숨은 input 을 남기지 않는다.
 *
 *  구 `openIntakeSheet`(D67 — 몸 한 줄 + `팩 고르기`/`새 문서` 두 액션)는 폐지했다:
 *  세 문(만들기·가져오기·공유)이 Tools 모달 한 자리로 갔고(D71), `새 문서`는 거기서
 *  `만들기` 탭으로 승격했다. 파일을 고르는 일 자체는 여기 그대로 남는다. */
export function pickAndImport(onDone?: (res: any) => void) {
  const input = document.createElement("input");
  input.type = "file";
  // 드롭존(surfaces/intake.ts)과 **같은 목록**이어야 한다 — 갈라지면 진입점에 따라
  // 열리는 파일이 달라진다(application/zip 은 .vincpack 컨테이너용으로 여기만 유지).
  input.accept = ".vincpack,.json,.md,.markdown,.txt,.rst,.pdf," +
                 "application/json,application/zip,text/markdown,text/plain,application/pdf";
  input.style.display = "none";
  document.body.appendChild(input);
  input.addEventListener("change", async () => {
    const f = input.files?.[0];
    input.remove();
    if (!f) return;
    const { isDocFile } = await import("./doc-import");
    // 원문 문서는 팩 파서를 거치지 않는다(드롭존과 같은 규칙).
    if (isDocFile(f)) { void openDocImportSheet(f, onDone); return; }
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

/** 원문 문서 1개 가져오기(D45) — 팩 시트와 같은 규격(머리·몸·발)으로, 적재 결과와
 *  개념 추출 승인을 한 자리에서 끝낸다. 추출은 드롭존과 마찬가지로 **미리보기 →
 *  승인** 2단계다(자동 반영하면 임의 문서의 헤딩이 그대로 개념이 된다). */
export async function openDocImportSheet(f: File, onDone?: (res: any) => void) {
  const S = COPY.ui.sheet;
  const sheet = openSheet({ title: S.docTitle, subtitle: S.docSub });
  const ext = (f.name.match(/\.[^.]+$/) || [""])[0].toLowerCase();
  sheet.body.innerHTML = `
    <div class="sheet-spec">
      <div><span class="hint">${S.specDocs}</span><b>1</b></div>
      <div><span class="hint">${S.specFormat}</span><b>${esc(ext)}</b></div>
    </div>
    <p>${esc(S.docNote(f.name))}</p>
    <div id="sheet-result" role="status"></div>`;
  sheet.foot.innerHTML = sheetActions(
    { id: "sheet-import", label: S.importCta },
    { id: "sheet-cancel", label: COPY.ui.common.cancel });
  sheet.foot.querySelector("#sheet-cancel")!.addEventListener("click", sheet.close);
  sheet.foot.querySelector("#sheet-import")!.addEventListener("click", async () => {
    const out = sheet.body.querySelector<HTMLElement>("#sheet-result")!;
    out.innerHTML = `<p class="hint">${COPY.packs.importing}</p>`;
    const dm = await import("./doc-import");
    try {
      const idx = await dm.originIndex(cfg.owner);
      const r = await dm.importDocFile(f, cfg.owner, idx);
      out.innerHTML = `<p>${esc(r.skipped ? S.docSkipped(r.title) : S.docDone(r.title))}</p>`;
      sheet.foot.innerHTML = sheetActions({ id: "sheet-go", label: S.viewAtlas });
      sheet.foot.querySelector("#sheet-go")!.addEventListener("click", () => {
        sheet.close();
        location.hash = "#atlas";
        location.reload();   // 그래프가 바뀌었다 — 현재 층을 새로 세운다
      });
      onDone?.({ executed: true, doc_id: r.docId });
      // 추출 제안은 결과 아래에 붙인다(승인 전까지 그래프는 그대로).
      const n = await dm.previewExtract(cfg.owner, [r.docId]);
      if (!n || !out.isConnected) return;
      const offer = document.createElement("p");
      offer.innerHTML = `<button class="btn" id="sheet-extract">${
        COPY.ui.atlasx.edExtractOffer(n)}</button>`;
      out.appendChild(offer);
      offer.querySelector("#sheet-extract")!.addEventListener("click", async () => {
        offer.innerHTML = `<span class="hint">${COPY.packs.importing}</span>`;
        const ap = await dm.applyExtract(cfg.owner, [r.docId]);
        if (!offer.isConnected) return;
        offer.innerHTML = `<span class="hint">${
          COPY.ui.atlasx.edExtractDone(ap.created, ap.mentions)}</span>`;
      });
    } catch (e) {
      out.innerHTML = `<div class="error-banner">${
        esc(dm.docImportError(e, f.name) || COPY.packs.error)}</div>`;
    }
  });
}
