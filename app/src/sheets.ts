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
 * 진행 표시 — 공유 경로의 **모든 기다림이 이 한 벌**을 쓴다(팩 준비 · 스페이스로
 * 보내기 · 파일로 받기 · 커뮤니티 게시). 전에는 자리마다 `<p class="hint">` 한 줄이라,
 * 같은 시트 안에서 같은 일을 기다리는데 모양이 자리마다 달랐다.
 *
 * **게이지는 불확정(indeterminate)이다. 단계를 못 만들어서가 아니라 없는 단계를
 * 지어내지 않기 위해서다.** 다만 네 자리가 기다리는 것은 한 모양이 아니라 **셋**이다:
 *
 *   1. `call()`(`api.ts:147`) — `api.share` · `api.importPack` 이 지난다. `fetch`
 *      하나와 `res.json()` 하나뿐이라 중간 보고가 물리적으로 없다.
 *   2. `communityPost()`(`api.ts:465`) — `community.publish` 는 `call()` 을 **안
 *      지난다**. 별개의 `fetch` 다. 다른 함수지만 역시 요청 하나 · 응답 하나다.
 *   3. `toBytes()`(`packfile.ts:54`) — 파일로 받기는 `api.ts` 를 **아예 안 탄다**.
 *      그리고 여기엔 셀 수 있는 일이 있다: `for (const [name, obj] of files)` 가
 *      파일마다 `await deflateRaw` 를 돌고, 총수를 `files.length`(매니페스트 ·
 *      프래그먼트 · 문서 N)로 **미리 안다**.
 *
 * 3번을 그래도 안 세는 이유는 셀 수 없어서가 아니다. 그 루프가 이 경로의 기다림
 * **전부**라(뒤는 Blob 생성과 `a.click()` 뿐이다) 퍼센트를 그리면 막대가 100으로
 * 달려가 곧바로 사라진다. 그것을 보여주려면 `toBytes` 에 콜백을 뚫어야 하는데,
 * 호출부는 `downloadPack` 하나뿐이면서(전수 확인) 팩 규격 파일이 시트의 표시 사정을
 * 알게 되는 대가는 그 한 자리보다 크다. 1·2번은 애초에 셀 것이 없다.
 *
 * 퍼센트를 시계로 지어내는 쪽은 참조 원본(커뮤니티 페이지)이 실제로 그렇게 한다:
 * 요청은 하나인데 `setInterval(…, 550)` 이 막대를 8 → 32 → 56 → 80% 로 밀고, 응답이
 * 오면 100% 로 점프한다. 그 수치는 서버가 어디까지 갔는지와 아무 관계가 없다.
 *
 * **`role="status"` 는 이 마크업에 없다.** 라이브 영역은 내용이 바뀌기 **전에** DOM 에
 * 서 있어야 읽히는데 이것은 내용과 함께 삽입되므로, 자기 자신에 달면 아무것도 알리지
 * 못한다. 붙일 상주 자리가 있는 곳은 커뮤니티 게시뿐이라 거기만 붙였다(`#pub-progress`
 * 가 폼과 함께 먼저 서 있다). 나머지 셋은 `sheet.body` 를 통째로 갈아치우므로 **알리는
 * 것이 없다.** 상주 자리를 만들려면 `openSheet` 를 고쳐야 하는데(시트 20 곳 공용)
 * 그쪽엔 더 앞선 결함이 있다 — 시트를 열 때 초점을 시트 안으로 옮기지 않는다. 초점이
 * 뒤의 버튼에 남아 있는 채로 알림만 먼저 다는 것은 순서가 거꾸로다.
 *
 * 라벨은 **한 번만** 싣는다. 보이는 `<p>` 를 `aria-labelledby` 로 가리킨다 — 전에는
 * `aria-label` 에 같은 문구를 한 벌 더 실어 두 번 읽혔다. `aria-valuenow` 는 없다.
 * 그것이 불확정의 정의다.
 *
 * 옮겨 온 것은 **모양**이다(4px · radius 2px · `--muted` 트랙 위 `--primary` 채움).
 * 움직임은 지도의 `.view-busy` 에서 가져왔다 — 앱 안에서 "바쁨"이 두 가지 모양으로
 * 읽히지 않게. 색·간격은 토큰에서만 온다(하드코딩 색은 표면 게이트가 막는다).
 */
function sheetProgress(label: string): string {
  return `
    <div class="sheet-progress">
      <p class="hint" id="sheet-progress-label">${esc(label)}</p>
      <div class="sheet-progress-track" role="progressbar"
           aria-labelledby="sheet-progress-label">
        <span></span>
      </div>
    </div>`;
}

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
      <div><span class="hint">${S.specLinks}</span><b>${c.edges ?? 0}</b></div>
      <div><span class="hint">${S.specDocs}</span><b>${c.documents ?? 0}</b></div>
      <div><span class="hint">${S.specFormat}</span><b>.vincpack</b></div>
    </div>`;
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
  const sheet = openSheet({ title: COPY.ui.sheet.shareTitle,
                            subtitle: COPY.ui.sheet.shareSub });
  sheet.body.innerHTML = sheetProgress(COPY.ui.sheet.preparing);
  try {
    const pack = await api.share(cfg.owner, {
      ids: scope.ids?.join(","), seed: scope.seed, domain: scope.domain, depth: scope.depth,
    }, opts.from);
    const nodes: any[] = pack.fragment?.nodes || [];
    const c = pack.manifest?.counts || {};
    const scopeLabel =
      scope.seed ? COPY.ui.sheet.scopeSeed(scope.depth ?? 2)
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
        sheet.body.innerHTML = sheetProgress(U.destWorking);
        sheet.foot.innerHTML = "";
        void api.importPack(out, cfg.owner, opts.into).then(
          (res) => {
            sheet.body.innerHTML = landedLines(landedOf(res, keep.length), where);
            sheet.foot.innerHTML = sheetActions({ id: "sheet-done", label: U.destClose });
            sheet.foot.querySelector("#sheet-done")!.addEventListener("click", sheet.close);
          },
          () => { sheet.body.innerHTML = `<div class="error-banner">${esc(U.destFailed)}</div>`; },
        );
        return;
      }
      void chooseDestination(sheet, out, keep.length,
        `vinc-share-${scope.domain || scope.seed || "slice"}.vincpack`, scopeLabel);
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
type Dest = { key: string; label: string; note: string; where: string;
              run: () => Promise<Landed | void> };

/** 적재 응답에서 **실제로 착지한 것**만 추린다. */
type Landed = { nodes: number; skipped: number; conflicts: number; docs: number };

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
    skipped: Math.max(0, requested - nodes),
    conflicts: (res?.id_conflicts || []).length,
    docs: res?.documents?.applied || 0,
  };
}

/** 착지 요약 → 사람이 읽는 문장들. 하나도 안 들어갔으면 그렇게 말한다. */
function landedLines(l: Landed, where: string): string {
  const U = COPY.ui.sheet;
  const out = [l.nodes > 0 ? U.destDone(l.nodes, where) : U.destNone(where)];
  if (l.conflicts > 0) out.push(U.destSkippedIds(l.conflicts));
  else if (l.skipped > 0) out.push(U.destSkippedOther(l.skipped));
  if (l.docs > 0) out.push(U.destDoneDocs(l.docs));
  return out.map((t) => `<p class="hint">${esc(t)}</p>`).join("");
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
  sheet: Sheet, out: any, conceptCount: number, filename: string, scopeLabel: string,
): Promise<void> {
  const U = COPY.ui.sheet;
  const dests: Dest[] = [];
  // 팀에 있을 때만 "내 스페이스"가 목적지가 된다(개인에서 개인은 무의미).
  if (cfg.team) {
    dests.push({
      key: "personal", label: U.destPersonal, note: U.destPersonalNote, where: U.destPersonal,
      run: async () => landedOf(await api.importPack(out, cfg.owner, { team: null }),
                                conceptCount),
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

  // 수치는 여기서도 보인다 — 나가는 순간은 [공유]가 아니라 **이 클릭**이다.
  // 개별 제외로 다시 세는 것은 `concepts`·`edges` 둘뿐이고 `documents` 는 `c` 의
  // 값이 그대로 온다. 그래도 표시가 거짓이 되지 않는 것은 `out` 이 `pack.documents`
  // 를 손대지 않고 그대로 실어 나르기 때문이다(보이는 수 = 실제로 나가는 수).
  // 그래서 노드를 전부 제외하면 개념 0 · 링크 0 · 문서 N 이 되는데, 맞는 표시다.
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
      // 커뮤니티는 자체 폼으로 이어지므로 여기서 완료 화면을 그리지 않는다.
      if (d.key === "community") { void d.run(); return; }
      sheet.body.innerHTML = sheetProgress(U.destWorking);
      sheet.foot.innerHTML = "";
      void d.run().then(
        (landed) => {
          // 파일 저장처럼 적재가 아닌 목적지는 요약이 없다 — 고른 개수 그대로가 참이다.
          sheet.body.innerHTML = landed
            ? landedLines(landed, d.where)
            : `<p class="hint">${esc(U.destDone(conceptCount, d.where))}</p>`;
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
      () => { prog.innerHTML = ""; err.textContent = COPY.ui.sheet.destFailed; },
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
          COPY.ui.viewx.edExtractDone(ap.created, ap.mentions)}</span>`;
      });
    } catch (e) {
      out.innerHTML = `<div class="error-banner">${
        esc(dm.docImportError(e, f.name) || COPY.packs.error)}</div>`;
    }
  });
}
