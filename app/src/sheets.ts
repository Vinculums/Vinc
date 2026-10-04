/** Share / Import 시트 — 공통 규격(D63 `openSheet`)을 쓴다.
 *  공유 시트는 "나가기 전에 무엇이 나가는지 전부 보여준다"는 원칙 그대로
 *  (정본 §5.1): 스코프 요약 · 포함 목록(개별 제외) · 미포함 고지 · 저장. */
import { AccountError, accountMode, api, cfg, community, IN_SHELL, shellInvoke, WEB, type PackProgress, type Scope } from "./api";
import { toBytes } from "./packfile";
import { keptDocs, standaloneDocs } from "./share-docs";
import { shareMerges } from "./share-merges";
import { COPY } from "./copy";
import { openSheet, sheetActions, sheetProgress } from "./components";

const esc = (s: string) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  c === "&" ? "&amp;" : c === "<" ? "&lt;" : c === ">" ? "&gt;" : "&quot;");

/**
 * 팩 사양 표 — 나가는 것의 수치.
 *
 * **문서 수는 장식이 아니라 계측기다.** 문서는 고르는 것이 아니라 담긴 개념을
 * 언급하는 것만 서버가 역추적해 싣는다(`api/share.py` `_docs_for_concepts`).
 * 그래서 스코프가 어긋나면 개념도 문서도 0인 **빈 팩**이 조용히 저장되는데,
 * 여태 그 사실이 처음 보이는 자리는 **받는 쪽**의 가져오기 시트였다. 보내는
 * 사람이 저장 전에 알아챌 수 있는 유일한 자리가 이 줄이다.
 *
 * 고르는 화면과 보낼 곳 고르는 화면이 **같은 표**를 쓴다: 개별 제외로 수가 줄면
 * 마지막 화면의 수도 함께 준다. 형식이 두 화면에서 같은 이유는 스페이스·커뮤니티로
 * 보낼 때도 나가는 것이 같은 팩이기 때문이다(`api.importPack`·`community.publish`
 * 둘 다 이 팩 객체를 그대로 받는다).
 */
function specRows(c: Record<string, number>, scopeLabel: string): string {
  const S = COPY.ui.sheet;
  return `
    <div class="sheet-spec">
      <div><span class="hint">${S.specScope}</span><b>${esc(scopeLabel)}</b></div>
      <div><span class="hint">${S.specConcepts}</span><b>${c.concepts ?? 0}</b></div>
      <div><span class="hint">${S.specRecords}</span><b>${c.records ?? 0}</b></div>
      <div><span class="hint">${S.specDecisions}</span><b>${c.decisions ?? 0}</b></div>
      <div><span class="hint">${S.specLinks}</span><b>${c.edges ?? 0}</b></div>
      <div><span class="hint">${S.specDocs}</span><b>${c.documents ?? 0}</b></div>
      <div><span class="hint">${S.specFormat}</span><b>.vincpack</b></div>
    </div>`;
}

/** VIN-105 매니페스트와 같은 술어: `records` 는 Episode+Record, `decisions` 는 Decision.
 *  구버전 팩에 `decisions` 키가 없어도 노드 타입으로 다시 센다. */
function recount(
  nodes: any[], edges: any[], docs: any[], prev: Record<string, number>,
): Record<string, number> {
  return {
    ...prev,
    concepts: nodes.filter((n) => n.type === "concept").length,
    records: nodes.filter((n) => n.type === "record" || n.type === "episode").length,
    decisions: nodes.filter((n) => n.type === "decision").length,
    edges: edges.length,
    documents: docs.length,
  };
}

function packDoneTexts(c: Record<string, number>, where: string): string[] {
  const U = COPY.ui.sheet;
  const concepts = c.concepts ?? 0;
  const records = c.records ?? 0;
  const decisions = c.decisions ?? 0;
  const docs = c.documents ?? 0;
  if (concepts + records + decisions + docs === 0) return [U.destNone(where)];
  const out: string[] = [];
  if (concepts > 0) out.push(U.destDone(concepts, where));
  if (records > 0) out.push(U.destDoneRecords(records));
  if (decisions > 0) out.push(U.destDoneDecisions(decisions));
  if (docs > 0) out.push(U.destDoneDocs(docs));
  return out;
}

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
  // 요청이 뜬 뒤 공간을 바꿔도 파일 이름은 이 팩을 만든 공간을 가리킨다.
  const source = opts.from?.team === null ? "" : opts.from?.team ?? cfg.team;
  const spaceName = source || (accountMode() || WEB ? "personal" : "local");
  const life: SheetLife = { onClose: () => {} };
  const sheet = openSheet({ title: COPY.ui.sheet.shareTitle,
                            subtitle: COPY.ui.sheet.shareSub,
                            onClose: () => life.onClose() });
  sheet.body.innerHTML = sheetProgress(COPY.ui.sheet.preparing);
  try {
    const pack = await api.share(cfg.owner, {
      ids: scope.ids?.join(","), seed: scope.seed, domain: scope.domain, depth: scope.depth,
    }, opts.from);
    const nodes: any[] = pack.fragment?.nodes || [];
    const unlinked = standaloneDocs(pack.documents, pack.doc_refs);
    const c = pack.manifest?.counts || {};
    // 씨앗은 문서가 아니라 노드다 — 그 이름을 팩에서 읽어 범위에 적는다(VIN-265).
    const seedName = scope.seed
      ? nodes.find((n) => n.id === scope.seed)?.title || scope.seed.replace(/^[a-z]+:/, "")
      : "";
    const scopeLabel =
      scope.seed ? COPY.ui.sheet.scopeSeed(seedName, scope.depth ?? 2)
      : scope.domain ? scope.domain
      : scope.ids?.length ? COPY.ui.sheet.scopeSelection(scope.ids.length)
      : COPY.ui.sheet.scopeAll;
    sheet.body.innerHTML = `
      ${specRows(c, scopeLabel)}
      <div class="sheet-list">
        ${nodes.map((n, i) => `
          <label class="list-row" style="cursor:pointer">
            <input type="checkbox" checked data-i="${i}" />
            <span class="title">${esc(n.title)}</span><span class="meta">${esc(n.domain || n.type)}</span>
          </label>`).join("")}
        ${unlinked.map((d, i) => `
          <label class="list-row" style="cursor:pointer">
            <input type="checkbox" checked data-doc-i="${i}" />
            <span class="title">${esc(d.title || d.doc_id)}</span><span class="meta">${esc(COPY.ui.sheet.specDocs)}</span>
          </label>`).join("")}
        ${nodes.length || unlinked.length ? "" : `<p class="hint">${COPY.ui.sheet.nothing}</p>`}
      </div>
      <p class="hint">${COPY.ui.sheet.keepNote}</p>`;
    sheet.foot.innerHTML = sheetActions(
      { id: "sheet-save", label: COPY.ui.sheet.save },
      { id: "sheet-cancel", label: COPY.ui.common.cancel });
    sheet.foot.querySelector("#sheet-cancel")!.addEventListener("click", sheet.close);
    sheet.foot.querySelector("#sheet-save")!.addEventListener("click", () => {
      const excluded = new Set([...sheet.body.querySelectorAll<HTMLInputElement>("input[data-i]")]
        .filter((cb) => !cb.checked).map((cb) => nodes[Number(cb.dataset.i)].id));
      const standalone = new Set([...sheet.body.querySelectorAll<HTMLInputElement>("input[data-doc-i]")]
        .filter((cb) => cb.checked).map((cb) => unlinked[Number(cb.dataset.docI)].doc_id as string));
      const keep = nodes.filter((n) => !excluded.has(n.id));
      const keepIds = new Set(keep.map((n) => n.id));
      const edges = (pack.fragment?.edges || []).filter((e: any) => keepIds.has(e.from) && keepIds.has(e.to));
      const docs = keptDocs(pack.documents, excluded, pack.doc_refs, keepIds, standalone);
      const out = { ...pack, fragment: { nodes: keep, edges },
        ...(pack.documents ? { documents: docs } : {}),
        manifest: { ...pack.manifest, counts: recount(keep, edges, docs, c) } };
      // 둘 다 응답에만 붙는 값이라 팩 파일에는 안 싣는다(`doc_refs` 는 위 원문 선택에만 쓴다).
      delete (out as any).executed;
      delete (out as any).doc_refs;
      // 목적지가 이미 정해져도 내 공간에 합치는 내용은 먼저 확인한다.
      if (opts.into) {
        if (!opts.into.team) { void copyToPersonal(sheet, life, out, keep.length); return; }
        void sendToSpace(sheet, life, out, keep.length, opts.into,
                         opts.intoLabel || COPY.ui.sheet.destTeam);
        return;
      }
      void chooseDestination(sheet, life, out, keep.length,
        packFilename(spaceName, scope.domain || scope.seed || "share"), scopeLabel);
    });
  } catch {
    sheet.body.innerHTML = `<div class="error-banner">${COPY.packs.error}</div>`;
  }
}

/** D55 — 쓰기는 zip 컨테이너로. 원문(documents)을 실은 팩이 통짜 JSON 이면
 *  수 MB 로 붓고 문서 하나만 꺼낼 수도 없다(실측 1.6MB → 461KB, 28%). */
function packFilename(space: string, scope: string): string {
  const clean = (s: string) => s.replace(/[<>:"/\\|?*\u0000-\u001f]/g, "-")
    .replace(/[. ]+$/, "").slice(0, 80) || "share";
  const date = new Date().toISOString().replace(/[:.]/g, "-");
  return `vinc-${clean(space)}-${clean(scope)}-${date}-${crypto.randomUUID().slice(0, 8)}.vincpack`;
}

async function downloadPack(out: Record<string, unknown>, filename: string): Promise<string | null> {
  const bytes = await toBytes(out);
  if (IN_SHELL) {
    const invoke = await shellInvoke();
    if (!invoke) throw new Error("shell unavailable");
    return invoke<string | null>("save_pack_file", {
      filename, title: COPY.ui.sheet.destFile, bytes: Array.from(bytes),
    });
  }
  const blob = new Blob([bytes as BlobPart], { type: "application/zip" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
  return filename;                 // 웹에서는 실제 다운로드 폴더 경로를 알 수 없다.
}

type Sheet = ReturnType<typeof openSheet>;
type Dest = { key: string; label: string; note: string; where: string;
              run: () => Promise<void> };

/** 적재 응답에서 **실제로 착지한 것**만 추린다. */
type Landed = { nodes: number; created: number; matched: number; skipped: number; conflicts: number; docs: number };

/**
 * 완료 문구를 응답에서 만든다 — 여기가 이 파일의 급소다.
 *
 * 전에는 사용자가 **고른** 개수로 "N개를 넣었어요"를 찍었다. 그런데 호스티드
 * 그래프는 노드 PK 가 전역이라, 남이(=다른 스페이스의 나 자신도 포함) 이미 가진
 * id 는 조용히 건너뛴다. 그래서 개인 → 팀 불러오기가 노드를 0개 넣고도 성공을
 * 알렸다(실측 2026-08-30). 서버는 `merged`·`id_conflicts` 로 진실을 이미 보내고
 * 있었고 화면만 그것을 안 봤다.
 *
 * `skipped` 를 (요청 - 착지)로 재는 이유: 건너뛰는 사유는 id 충돌 말고도 있고
 * (제목 없는 Entity 등), 그 전부가 사용자에게는 "안 들어간 것"으로 같다.
 */
function landedOf(res: any, requested: number): Landed {
  const n = res?.merged?.nodes || {};
  const nodes = (n.created || 0) + (n.matched || 0);
  return {
    nodes,
    created: n.created || 0,
    matched: n.matched || 0,
    skipped: Math.max(0, requested - nodes),
    conflicts: (res?.id_conflicts || []).length,
    docs: res?.documents?.applied || 0,
  };
}

/** 착지 요약 → 사람이 읽는 문장들. 하나도 안 들어갔으면 그렇게 말한다. */
function landedTexts(l: Landed, where: string, counts?: Record<string, number>): string[] {
  const U = COPY.ui.sheet;
  const totals = [U.destCreated(l.created, where), U.destMerged(l.matched)];
  if (l.skipped === 0 && counts) {
    return [...totals, ...packDoneTexts({ ...counts, documents: l.docs }, where)];
  }
  const out = totals;
  if (l.conflicts > 0) out.push(U.destSkippedIds(l.conflicts));
  else if (l.skipped > 0) out.push(U.destSkippedOther(l.skipped));
  if (l.docs > 0) out.push(U.destDoneDocs(l.docs));
  return out;
}

/** 내 공간에 이미 있는 항목은 복사 전에 사람이 확인한다. 읽기만으로는 보내지 않는다. */
async function copyToPersonal(sheet: Sheet, life: SheetLife, out: any, requested: number): Promise<void> {
  const U = COPY.ui.sheet;
  let closed = false;
  life.onClose = () => { closed = true; };
  const retry = () => { void copyToPersonal(sheet, life, out, requested); };
  sheet.body.innerHTML = sheetProgress(U.destChecking);
  sheet.foot.innerHTML = sheetActions({ id: "sheet-cancel", label: COPY.ui.common.cancel });
  sheet.foot.querySelector("#sheet-cancel")!.addEventListener("click", sheet.close);
  try {
    const matches = await shareMerges(out.fragment?.nodes || [],
      (id) => api.nodeContext(cfg.owner, id, 0, { team: null }));
    if (closed) return;
    if (!matches.length) {
      await sendToSpace(sheet, life, out, requested, { team: null }, U.destPersonal);
      return;
    }
    sheet.body.innerHTML = `<p>${esc(U.destMergeTitle(matches.length))}</p>
      <p class="hint">${esc(U.destMergeNote)}</p>
      <div class="sheet-list">${matches.map((m) => `<div class="list-row">
        <span class="title">${esc(m.title)}</span></div>`).join("")}</div>`;
    sheet.foot.innerHTML = sheetActions(
      { id: "sheet-merge", label: U.destMergeConfirm },
      { id: "sheet-cancel", label: COPY.ui.common.cancel });
    sheet.foot.querySelector("#sheet-cancel")!.addEventListener("click", sheet.close);
    sheet.foot.querySelector("#sheet-merge")!.addEventListener("click", () => {
      void sendToSpace(sheet, life, out, requested, { team: null }, U.destPersonal);
    }, { once: true });
  } catch {
    if (closed) return;
    sheet.body.innerHTML = `<div class="error-banner">${esc(U.destCheckFailed)}</div>`;
    sheet.foot.innerHTML = sheetActions(
      { id: "sheet-retry", label: COPY.ui.lnb.retry },
      { id: "sheet-cancel", label: COPY.ui.common.cancel });
    sheet.foot.querySelector("#sheet-retry")!.addEventListener("click", retry, { once: true });
    sheet.foot.querySelector("#sheet-cancel")!.addEventListener("click", sheet.close);
  }
}

/** 시트가 닫힐 때 할 일. 보내는 중에만 바뀐다(닫으면 알림으로 넘어간다). */
type SheetLife = { onClose: () => void };

const STAGES = ["nodes", "edges", "documents"] as const;

/**
 * 보내기 진행 — 노드·연결·문서 막대 셋. **확정 막대다.** `sheetProgress` 가 불확정인
 * 이유(셀 단계가 없다)의 반대 경우라서다: 조각이 실제 단위이고 각 조각의 항목 수를
 * 보내기 전에 안다. 막대는 한 번 세우고 폭만 바꾼다(다시 그리면 전환이 끊긴다).
 */
function mountGauges(host: HTMLElement, label: string): (p: PackProgress) => void {
  const U = COPY.ui.sheet;
  const names = { nodes: U.gaugeNodes, edges: U.gaugeLinks, documents: U.gaugeDocs };
  host.innerHTML = `
    <div class="sheet-progress">
      <p class="hint">${esc(label)}</p>
      ${STAGES.map((k) => `
        <div class="sheet-gauge" data-stage="${k}">
          <p class="hint"><span>${esc(names[k])}</span><span data-n></span></p>
          <div class="sheet-progress-track is-determinate" role="progressbar"
               aria-label="${esc(names[k])}" aria-valuemin="0"><span></span></div>
        </div>`).join("")}
    </div>`;
  return (p) => {
    for (const k of STAGES) {
      const row = host.querySelector<HTMLElement>(`[data-stage="${k}"]`);
      if (!row) return;   // 담던 자리가 다른 내용으로 바뀌었다
      const total = p.total[k], done = p.done[k];
      row.querySelector("[data-n]")!.textContent = U.gaugeCount(done, total);
      const bar = row.querySelector<HTMLElement>("[role=progressbar]")!;
      bar.setAttribute("aria-valuemax", String(total));
      bar.setAttribute("aria-valuenow", String(done));
      bar.querySelector("span")!.style.width = `${total ? Math.round((done / total) * 100) : 0}%`;
    }
  };
}

/** 시트를 닫은 뒤의 자리 — 화면 아래 알림 한 줄. 끝나면 결과와 닫기 버튼을 단다. */
function transferToast(): { say: (text: string) => void; finish: (text: string) => void } {
  const el = document.createElement("div");
  // 모양은 업데이트 알림의 한 벌을 쓴다. 그것은 데스크탑 셸에만 뜨고 스페이스 보내기는
  // 웹에만 있어서 두 알림이 한 화면에 겹치지 않는다.
  el.className = "update-toast";
  el.setAttribute("role", "status");
  el.innerHTML = `<span class="update-msg"></span><span class="update-actions"></span>`;
  document.body.appendChild(el);
  const msg = el.querySelector<HTMLElement>(".update-msg")!;
  return {
    say: (text) => { msg.textContent = text; },
    finish: (text) => {
      msg.textContent = text;
      const b = document.createElement("button");
      b.className = "btn";
      b.textContent = COPY.ui.sheet.destClose;
      b.addEventListener("click", () => el.remove());
      el.querySelector(".update-actions")!.appendChild(b);
    },
  };
}

/**
 * 한 스페이스로 보낸다 — 개인 → 팀, 팀 → 개인 둘 다 여기다.
 *
 * **시트를 닫아도 멈추지 않는다.** 조각은 이 페이지가 순서대로 쥐고 있으므로 서버에
 * 작업을 따로 맡길 것이 없다. 닫으면 진행과 결과가 화면 아래 알림으로 옮겨 가고,
 * 도는 동안은 페이지를 떠나려 할 때 브라우저가 한 번 묻는다. 떠나면 거기서 멈추지만
 * 다시 보내면 들어간 것은 그대로 두고 나머지를 채운다(`api.importPack` 주석).
 */
async function sendToSpace(sheet: Sheet, life: SheetLife, out: any, requested: number,
                           scope: Scope, where: string): Promise<void> {
  const U = COPY.ui.sheet;
  sheet.foot.innerHTML = "";
  const sum = (c: PackProgress["total"]) => c.nodes + c.edges + c.documents;
  const at: { show: (p: PackProgress) => void; last?: PackProgress;
              toast?: ReturnType<typeof transferToast> } =
    { show: mountGauges(sheet.body, U.destWorking) };
  life.onClose = () => {
    const toast = transferToast();
    at.toast = toast;
    at.show = (p) => toast.say(U.destBackground(sum(p.done), sum(p.total)));
    if (at.last) at.show(at.last);
  };
  const stay = (e: BeforeUnloadEvent) => e.preventDefault();
  addEventListener("beforeunload", stay);

  let lines: string[];
  let failed = false;
  try {
    const res = await api.importPack(out, cfg.owner, scope, (p) => { at.last = p; at.show(p); });
    lines = landedTexts(landedOf(res, requested), where, out.manifest?.counts);
    if (res.too_big > 0) lines.push(U.destTooBig(res.too_big));
  } catch (e) {
    failed = true;
    lines = [(e as any)?.sent > 0 ? U.destPartial : U.destFailed];
  } finally {
    removeEventListener("beforeunload", stay);
    life.onClose = () => {};
  }
  if (at.toast) { at.toast.finish(lines.join(" ")); return; }
  sheet.body.innerHTML = failed
    ? `<div class="error-banner">${esc(lines[0]!)}</div>`
    : lines.map((t) => `<p class="hint">${esc(t)}</p>`).join("");
  sheet.foot.innerHTML = sheetActions({ id: "sheet-done", label: U.destClose });
  sheet.foot.querySelector("#sheet-done")!.addEventListener("click", sheet.close);
}

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
  sheet: Sheet, life: SheetLife, out: any, conceptCount: number, filename: string,
  scopeLabel: string, ask = false,
): Promise<void> {
  const U = COPY.ui.sheet;
  const dests: Dest[] = [];
  // 팀에 있을 때만 "내 스페이스"가 목적지가 된다(개인에서 개인은 무의미).
  if (cfg.team) {
    dests.push({
      key: "personal", label: U.destPersonal, note: U.destPersonalNote, where: U.destPersonal,
      run: () => copyToPersonal(sheet, life, out, conceptCount),
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
    run: async () => {
      let closed = false;
      life.onClose = () => { closed = true; };
      sheet.body.innerHTML = sheetProgress(U.destWorking);
      sheet.foot.innerHTML = "";
      try {
        const path = await downloadPack(out, filename);
        if (closed) return;
        if (!path) {
          // 고르기를 취소하면 성공을 말하거나 다시 고르기 창을 자동으로 열지 않는다.
          await chooseDestination(sheet, life, out, conceptCount, filename, scopeLabel, true);
          return;
        }
        sheet.body.innerHTML = packDoneTexts(out.manifest?.counts || {}, U.destFile)
          .map((t) => `<p class="hint">${esc(t)}</p>`).join("")
          + `<p class="hint break-all"><code>${esc(path)}</code></p>`;
        sheet.foot.innerHTML = sheetActions({ id: "sheet-done", label: U.destClose });
        sheet.foot.querySelector("#sheet-done")!.addEventListener("click", sheet.close);
      } catch {
        if (closed) return;
        await chooseDestination(sheet, life, out, conceptCount, filename, scopeLabel, true);
        sheet.body.insertAdjacentHTML("afterbegin", `<div class="error-banner">${esc(U.destFailed)}</div>`);
      }
    },
  });

  if (dests.length === 1 && !ask) {
    await dests[0]!.run();
    return;
  }

  // 수치는 여기서도 보인다 — 나가는 순간은 [공유]가 아니라 **이 클릭**이다.
  // 개별 제외로 다시 세는 것은 `recount` 가 매니페스트 키 전부를 다시 채운 값이다.
  sheet.body.innerHTML = `
    ${specRows(out.manifest?.counts || {}, scopeLabel)}
    <p class="hint">${esc(U.destSub)}</p>
    <div class="dest-pick">
      ${dests.map((d) => `
        <button type="button" data-dest="${d.key}">
          <span class="title">${esc(d.label)}</span>
          <span class="meta">${esc(d.note)}</span>
        </button>`).join("")}
    </div>`;
  sheet.foot.innerHTML = sheetActions({ id: "sheet-cancel", label: COPY.ui.common.cancel });
  sheet.foot.querySelector("#sheet-cancel")!.addEventListener("click", sheet.close);

  for (const d of dests) {
    sheet.body.querySelector(`[data-dest="${d.key}"]`)!.addEventListener("click", () => {
      // 목적지마다 자체 폼·진행·완료를 그린다.
      void d.run();
    }, { once: true });
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
    <p class="hint" id="pub-err"></p>
    <div id="pub-progress" role="status"></div>`;
  sheet.foot.innerHTML = sheetActions(
    { id: "sheet-pub", label: P.publishGo },
    { id: "sheet-cancel", label: COPY.ui.common.cancel });
  sheet.foot.querySelector("#sheet-cancel")!.addEventListener("click", sheet.close);
  sheet.foot.querySelector("#sheet-pub")!.addEventListener("click", () => {
    const name = (sheet.body.querySelector("#pub-name") as HTMLInputElement).value.trim();
    const desc = (sheet.body.querySelector("#pub-desc") as HTMLInputElement).value.trim();
    const ok = (sheet.body.querySelector("#pub-consent") as HTMLInputElement).checked;
    const err = sheet.body.querySelector("#pub-err")!;
    // 진행 표시는 오류 줄과 **다른 자리**에 둔다. 실패하면 폼이 그대로 남아 다시
    // 누를 수 있어야 하는데(다른 목적지와 달리 여기엔 사용자가 친 이름·설명이 있다),
    // 한 자리를 돌려쓰면 실패 문구가 막대를 지우거나 막대가 문구를 지운다.
    const prog = sheet.body.querySelector("#pub-progress")!;
    if (!name) { err.textContent = P.publishNeedName; return; }
    if (!ok) { err.textContent = P.publishNeedConsent; return; }
    err.textContent = "";
    prog.innerHTML = sheetProgress(COPY.ui.sheet.destWorking);
    void community.publish(name, desc, out).then(
      () => {
        sheet.body.innerHTML = `<p class="hint">${esc(
          COPY.ui.sheet.destDoneCommunity)}</p>`;
        sheet.foot.innerHTML = sheetActions({ id: "sheet-done", label: COPY.ui.sheet.destClose });
        sheet.foot.querySelector("#sheet-done")!.addEventListener("click", sheet.close);
      },
      (e) => {
        prog.innerHTML = "";
        // 게시는 공개 핸들이 붙는 **유일한** 행위라 서버가 username 을 요구한다
        // (400 `username_required`). 이 경우만 갈라 안내한다 — 일반 오류로 뭉개면
        // 왜 안 되는지 알 길이 없다. 폼은 그대로 두어(이름·설명이 살아 있다) 설정하고
        // 바로 다시 누를 수 있게 한다. 구 Packs 화면이 갖고 있던 분기를 옮긴 것이고,
        // 그 화면이 사라지면 이 안내도 함께 사라질 자리였다(P15).
        if (e instanceof AccountError && e.code === "username_required") {
          err.innerHTML = `<span class="hint">${esc(P.publishNeedHandle)}</span> `
            + `<a href="/account/" target="_blank" rel="noopener">${esc(P.publishSetHandle)}</a>`;
          return;
        }
        err.textContent = COPY.ui.sheet.destFailed;
      },
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
      <div><span class="hint">${COPY.ui.sheet.specRecords}</span><b>${c.records ?? 0}</b></div>
      <div><span class="hint">${COPY.ui.sheet.specDecisions}</span><b>${c.decisions ?? 0}</b></div>
      <div><span class="hint">${COPY.ui.sheet.specLinks}</span><b>${c.edges ?? 0}</b></div>
      <div><span class="hint">${COPY.ui.sheet.specDocs}</span><b>${(pack?.documents || []).length}</b></div>
    </div>
    <p>${COPY.ui.sheet.updateNote(c.concepts ?? n, c.edges ?? 0, c.records ?? 0, c.decisions ?? 0, (pack?.documents || []).length)}</p>
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
        { id: "sheet-go", label: COPY.ui.sheet.viewMap });
      sheet.foot.querySelector("#sheet-go")!.addEventListener("click", () => {
        sheet.close();
        location.hash = "#view";
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
      sheet.foot.innerHTML = sheetActions({ id: "sheet-go", label: S.viewMap });
      sheet.foot.querySelector("#sheet-go")!.addEventListener("click", () => {
        sheet.close();
        location.hash = "#view";
        location.reload();   // 그래프가 바뀌었다 — 현재 층을 새로 세운다
      });
      onDone?.({ executed: true, doc_id: r.docId });
      // 추출 제안은 결과 아래에 붙인다(승인 전까지 그래프는 그대로).
      const n = await dm.previewExtract(cfg.owner, [r.docId]);
      if (!n || !out.isConnected) return;
      const offer = document.createElement("p");
      offer.innerHTML = `<button class="btn" id="sheet-extract">${
        COPY.ui.viewx.edExtractOffer(n)}</button>`;
      out.appendChild(offer);
      offer.querySelector("#sheet-extract")!.addEventListener("click", async () => {
        offer.innerHTML = `<span class="hint">${COPY.packs.importing}</span>`;
        const ap = await dm.applyExtract(cfg.owner, [r.docId]);
        if (!offer.isConnected) return;
        offer.innerHTML = `<span class="hint">${
          ap.refused ? esc(COPY.author.exRefusedDocs(ap.refused))
            : COPY.ui.viewx.edExtractDone(ap.created, ap.mentions)}</span>`;
      });
    } catch (e) {
      out.innerHTML = `<div class="error-banner">${
        esc(dm.docImportError(e, f.name) || COPY.packs.error)}</div>`;
    }
  });
}
