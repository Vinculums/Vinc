// 노드 상세의 근거 절(수치 · 속성 · 기본 정보 · 발췌 문서 줄), 문서 리더(읽기 · 편집 · 복사 · 넓히기), 연결 없는 노드의 잇기 제안.
// 그래프 표면(`view.ts`)이 `ViewHost` 와 노드 상세의 절 그리기를 넘겨 만든다. 노드 상세의 틀(`railFor`)은 표면이 그린다.
import { api, cfg, viewScope, isCommunitySpace, canWriteHere, docMidWrite, type NodeContext, type NodeLinks } from "../api";
import { COPY } from "../copy";
import { docTitle } from "../doc-title";
import { esc } from "../components";
import { renderMarkdown } from "../doc-editor";
import { hydrateRich } from "../rich-blocks";
import { loaderInline } from "../vinc-loaders.js";
import { detailState, detailFailure, type DetailState } from "../topic-detail";
import { wireAsk, askRowHTML, docAskItems } from "./view-ask";
import { detailErrorHTML } from "./view-cards";
import type { PropsCard } from "./view-props-card";
import type { N3, ViewHost } from "./view-host";

/** 리더 머리에 세울 개념 칩 수. 나머지는 "+N" 으로 센다 — 좁은 열에서 칩이 줄을
 *  넘기기 시작하면 그 줄이 본문보다 커진다(관계 카드의 `relsMore` 와 같은 태도). */
const READER_CHIPS = 6;

/** 노드 상세 안에서 표면이 그리는 절. 근거 절이 노드 맥락 응답을 받으면 이것들을 채운다. */
export type NodeSections = {
  /** 수치 절(`#node-stats`): 종류 · 주제 · 연결 수 · 방향. */
  drawNodeStats(nd: N3, links?: NodeLinks, kind?: string): void;
  /** 속성 절(`view-props-card.ts`). */
  drawNodeProps: PropsCard["drawNodeProps"];
  /** 넓은 열의 연결 절(`#node-links`). */
  drawPaneLinks(nd: N3, links: NodeLinks): void;
  /** 넓은 열의 항목 페이지(`#node-item`). */
  drawItemPage(nd: N3, res: NodeContext, canEdit: boolean): void;
};

/** 표면이 쥐는 근거 절과 리더의 손잡이. */
export type Reader = {
  /** 노드 상세의 근거 절을 노드 맥락 응답 하나로 채운다. 레일이 그 노드의 상세를 그린 뒤 부른다. */
  loadNodeSource(nd: N3): Promise<void>;
  /** 노드가 있으면 그 노드에 먼저 내려앉고, 그 뒤 문서를 리더로 연다. */
  openDocAt(docId: string, nodeId?: string, domain?: string, chunkIndex?: number): Promise<void>;
  /** 문서를 상세 레일의 리더로 연다. `conceptId` 면 그 개념의 밑줄을, `chunkIndex` 면 그 대목으로 앉힌다. */
  openReader(docId: string, conceptId?: string, chunkIndex?: number): Promise<void>;
};

/** 근거 절 · 리더 · 잇기 제안을 그 표면의 상세 레일에 묶는다. 표면의 상태(레일의 판, 노드 맥락 메모, ask 줄)에는 `host` 로만 닿는다. */
export function createReader(host: ViewHost, sections: NodeSections): Reader {
  const { detail } = host;
  const { drawNodeStats, drawNodeProps, drawPaneLinks, drawItemPage } = sections;

  /** 문서 열의 **읽기/편집 모드**.
   *
   *  ⚠ 이것은 "상시 편집" 지시를 **되돌린 것**이다. 그 지시의 근거는
   *  "고치려면 매번 두 번 눌러야 한다"였고 그건 지금도 참이다. 다만 상시 편집이
   *  치르던 값이 그보다 컸다: 읽으려고 연 문서에서 클릭 한 번이 곧 커서였고,
   *  드래그해서 복사하려는 손과 고치려는 손이 같은 과녁을 나눠 갖지 못했다.
   *  기본은 **읽기**다 — 문서를 여는 대부분의 이유가 읽는 것이라서.
   *  세션 안에서만 사는 이유: 방금 고치던 사람이 다음 문서에서도 고칠 확률이
   *  높지만, 앱을 다시 열었을 때까지 편집으로 남아 있을 이유는 없다. */
  let docWriting = false;

  async function loadNodeSource(nd: N3) {
    // 발췌는 증강 — 실패·부재가 상세 패널을 깨지 않는다(문구만 남기고 조용히).
    const box = detail.querySelector<HTMLElement>("#node-source");
    if (!box) return;
    const revision = host.railRevision();
    const scopeKey = viewScope().key;
    const current = () => host.isLive() && revision === host.railRevision() && detail.contains(box)
      && !detail.hidden && viewScope().key === scopeKey;
    const fail = (state: DetailState) => {
      box.innerHTML = detailErrorHTML(state);
      const stats = detail.querySelector("#node-stats");
      if (stats) stats.innerHTML = "";
      box.querySelector("[data-detail-retry]")?.addEventListener("click", () => {
        host.dropNodeContext(); void loadNodeSource(nd);
      });
    };
    box.innerHTML = `<p class="hint">${loaderInline(COPY.ui.viewx.sourceLoading)}</p>`;
    try {
      const res = await host.nodeContextOnce(nd.id);
      if (!current()) return;
      const state = detailState(res);
      if (state !== "ready") { fail(state); return; }
      // 수치는 조회 성공 값만 쓴다. 응답 실패를 화면의 선이나 0으로 대신하지 않는다.
      drawNodeStats(nd, res.links, res.node_kind);
      detail.querySelector("#node-stats [data-detail-retry]")?.addEventListener("click", () => {
        host.dropNodeContext(); void loadNodeSource(nd);
      });
      // 카드와 같은 줄이므로 부제도 같은 수치를 말해야 한다.
      if (res.links?.executed) host.railAskCounts()?.(res.links.degree + res.links.attached,
                                               (res.excerpts || []).length);
      // D32 고아 최소화 — "확실히 연결 0"일 때만 잇기 후보를 낸다. 여태 드릴다운
      // 경로가 degree 0 을 박아 33연결 허브에도 제안이 떴다.
      if (res.links?.executed && res.links.degree + res.links.attached === 0)
        void suggestTiesInto(nd);
      // 이 스페이스에 쓸 수 없으면(커뮤니티 · 팀 열람자) 편집 단추를 내지 않는다.
      const canEdit = await canWriteHere();
      // 패널이 그 사이 다른 노드로 갱신됐으면 버린다(늦은 응답이 남의 상세를 덮지 않게).
      if (!detail.contains(box) || detail.hidden) return;
      drawNodeProps(nd, res.props, { kind: res.node_kind, rev: res.rev, canEdit });
      // 지도에 자리가 없는 노드(연결 없는 기록·결정)는 id 조각을 이름으로 달고 열린다 —
      // 노드가 답한 제목이 오면 그것으로 바꾼다(역할의 직함은 위에서 이미 골랐다).
      const h2 = detail.querySelector("h2");
      if (h2 && res.title && h2.textContent === nd.id.replace(/^[a-z]+:/, "")) h2.textContent = res.title;
      // 기본 정보(흐름 H) — 저장되면 지도와 이 열을 새 값으로 다시 그린다.
      const basics = detail.querySelector<HTMLElement>("#node-basics");
      if (basics) void import("../node-basics").then((m) => m.drawBasics(basics, nd.id, res,
        (domain) => void host.openNodePane(nd.id, domain || undefined), canEdit));
      if (host.railFull() && res.links?.executed) drawPaneLinks(nd, res.links);
      if (host.railFull()) drawItemPage(nd, res, canEdit);
      const parts: string[] = [];
      if (res.one_liner) parts.push(`<div class="doc-render">${renderMarkdown(res.one_liner)}</div>`);
      const ex = res.excerpts || [];
      if (ex.length > 0) {
        const header = res.arm === "fts"
          ? COPY.ui.viewx.sourceRelated : COPY.ui.viewx.sourceHeader;
        // 문서 줄 = 클릭 시 Reader 전문 승격(peek-definition 동선). 발췌 글은 싣지 않는다:
        // 문서에서 잘라 낸 창이라 제목·목록·강조가 끊긴다. 어느 문서의 어느 절인지만
        // 보이고, 글은 Reader 가 전문으로 보여 준다. 같은 문서는 한 줄로 모은다.
        const seen = new Set<string>();
        const rows = ex.filter((e) => !seen.has(e.doc_id) && seen.add(e.doc_id));
        parts.push(`<p class="hint" style="margin-bottom:.3rem">${header}</p>` +
          rows.map((e, i) => `
            <div class="list-row rdr-open" data-doc="${esc(e.doc_id)}" data-i="${i}"
                 style="display:block;cursor:pointer">
              <span class="title" style="font-size:.85em">${esc(e.doc_title)}${
                // 문서 H1 아래 문단은 절 이름이 곧 문서 제목이라 같은 말을 두 번 적게 된다.
                e.section && e.section.trim() !== (e.doc_title || "").trim()
                  ? `<span class="hint"> · ${esc(e.section)}</span>` : ""}</span>
            </div>`).join(""));
      } else if (!res.one_liner) {
        parts.push(`<p class="hint">${COPY.ui.viewx.sourceNone}</p>`);
      }
      box.innerHTML = parts.join("");
      hydrateRich(box);          // 한 줄 설명 안의 수식·다이어그램을 채운다(늦게 싣는다)
      box.querySelectorAll<HTMLElement>(".rdr-open").forEach((row) =>
        row.addEventListener("click", () =>
          void openReader(row.dataset.doc!, nd.id)));
    } catch (error) {
      if (current()) fail(detailFailure(error));
    }
  }

  /** Reader 서브뷰(D45-V2 2단계) — 상세 패널을 정본 전문으로 확장한다. 새 표면
   *  신설 없이 View 안에 머문다(D15). 뒤로 = 노드 상세로 복귀. 밑줄 범위는
   *  백엔드 /doc/highlights(토큰 매칭 계산, 정확한 char offset)로 받아 첫 범위로
   *  스크롤한다. verified 배지로 "정본" 신뢰를 시각화. */
  /** 문서를 연다 — **지도 착지와 본문 열기를 한 클릭에.** 좌측 트리·문서 목록·검색이
   *  전부 이 문으로 들어온다.
   *
   *  순서가 계약이다: `focusNode` 는 끝에서 노드 상세로 레일을 덮으므로 리더를
   *  **뒤에** 연다. 그래야 리더의 `뒤로`가 그 노드 상세로 돌아간다(lastNode 가
   *  거기서 세워진다) — 반대로 하면 문서를 열자마자 상세가 덮어써 본문이 사라진다. */
  async function openDocAt(docId: string, nodeId?: string,
                           domain?: string, chunkIndex?: number) {
    if (nodeId) await host.focusNode(nodeId, domain);
    await openReader(docId, nodeId, chunkIndex);
  }

  /** `chunkIndex` 는 오프셋이 아니라 **청크 번호**다. 오프셋은 아래에서 부르는
   *  `/doc/context` 가 이미 실어 오므로, 검색이 그것을 알아내려 한 번 더 왕복할
   *  이유가 없다(청크 id `<doc>#00007` 의 뒷자리가 곧 번호다). */
  async function openReader(docId: string, conceptId?: string, chunkIndex?: number) {
    const revision = host.openRail();
    const current = () => revision === host.railRevision() && host.isLive() && !detail.hidden;
    detail.innerHTML = `<p class="hint">${loaderInline(COPY.ui.viewx.readerLoading)}</p>`;
    try {
      // 셋을 나란히 부른다. 밑줄과 머리는 **증강**이라 실패해도 문서는 열려야 한다 —
      // Promise.all 은 하나만 거절돼도 전부를 버리므로 각자 강등본을 준비한다.
      // (개념 없이 열면 밑줄 그을 대상 자체가 없어 부르지도 않는다.)
      const [doc, hl, ctx] = await Promise.all([
        api.docGet(cfg.owner, docId),
        conceptId
          ? api.docHighlights(cfg.owner, docId, conceptId).catch(() => ({ executed: false }))
          : Promise.resolve({ executed: false }),
        api.docContext(cfg.owner, docId).catch(() => ({ executed: false })),
      ]) as [Awaited<ReturnType<typeof api.docGet>>,
             Partial<Awaited<ReturnType<typeof api.docHighlights>>>,
             Partial<Awaited<ReturnType<typeof api.docContext>>>];
      if (!current()) return;
      if (!doc.executed || doc.text === undefined) {
        // ⚠ **이 이른 반환은 이 패키지가 만든 것이 아니다.** `!doc.executed ||
        // doc.text === undefined` 는 P17b 이전부터 여기 있었고, 편집기를 세우지 않는
        // 보호는 거기서 온다(`git show <P17b 이전>:app/src/surfaces/view.ts`).
        // P17 이 `doc_get` 을 이 상태에서 `executed:false` 로 돌린 순간 쓰기 창은
        // 이미 이 가드에 걸렸다. 그러니 그 보호를 P17b 의 공으로 적지 말 것 —
        // 다음 사람이 보호가 어디서 오는지 잘못 안다.
        //
        // 이 패키지가 더한 것은 **문구 갈래 + 사람이 누르는 재시도** 둘뿐이다: 쓰기 창은
        // "못 열었다"와 다른 사건이라(문서는 그대로 있고 비어 있지도 않다) 같은 문구로
        // 떨어지면 화면에서 "빈 문서"로 읽힌다. 자동 재시도는 안 건다 — 크래시 잔해면
        // 영원히 돈다.
        const busy = docMidWrite(doc);
        // 레일 머리는 이 분기에도 있어야 한다. `detail.innerHTML` 을 통째로 갈아 끼우면
        // `.rail-head`(#rail-close)가 함께 사라져 마우스만 쓰는 사람에게 나가는 문이
        // Esc 키뿐이 된다. 쓰기 창 중에서도 **크래시 잔해는 다음 re-put 까지 며칠 간다** —
        // 오래 머무는 상태라 여기부터 닫는다.
        // ⚠ 선행 결함이 남아 있다: `readerLoading`·`readerFail`·`drillFail`
        // 도 같은 모양으로 레일을 비운다(P17b 이전부터). 그 셋은 이 패키지 밖이다.
        detail.innerHTML = `
          ${host.railHeadHTML(COPY.ui.viewx.readerTitle)}
          <p class="hint">${busy ? COPY.ui.viewx.readerMidWrite : COPY.ui.viewx.readerFail}</p>`
          + (busy ? `<button class="btn" id="rdr-retry">${esc(COPY.ui.viewx.readerRetry)}</button>` : "");
        host.wireRailHead();
        detail.querySelector("#rdr-retry")?.addEventListener("click",
          () => void openReader(docId, conceptId, chunkIndex));
        return;
      }
      // 문서가 자기 제목을 `# 제목` 으로 갖고 있으면 레일 제목은 생략한다 —
      // 같은 글자가 두 번 나오면 어느 쪽이 정본인지 헷갈린다.
      const firstLine = (doc.text.split("\n").find((l) => l.trim()) || "").trim();
      const dupTitle = !!doc.title
        && firstLine.replace(/^#{1,6}\s+/, "").trim().toLowerCase() === doc.title.trim().toLowerCase();
      // 배지는 **깨졌을 때만** 뜬다. 저장 무결성은 정상이 기본값이라, 문서를 열
      // 때마다 "검증된 원본"이라고 말하면 읽으러 온 사람에게 매번 남의 일을 알린다.
      // 사람이 알아야 하는 것은 실패한 경우뿐이다.
      const badge = doc.verified
        ? "" : `<span class="hint">${COPY.ui.viewx.readerUnverified}</span>`;
      // 문서→노드. 여태 없던 방향이다 — 문서를 열어 놓고 "이건 무엇에 걸려 있나"를
      // 물을 수 없었다. 0건이면 줄 자체를 안 그린다(빈 자리를 남기지 않는다).
      const cons = ctx.concepts || [];
      const chips = cons.slice(0, READER_CHIPS);
      const mentionsHTML = cons.length === 0 ? "" : `
        <p class="rdr-chips">
          <span class="hint">${COPY.ui.viewx.readerConcepts}</span>
          ${chips.map((c) => `<button class="chip" data-go="${esc(c.id)}"
             data-dom="${esc(c.domain)}">${esc(c.title || c.id)}</button>`).join("")}
          ${cons.length > chips.length
            ? `<span class="hint">${COPY.ui.viewx.relsMore(cons.length - chips.length)}</span>` : ""}
        </p>`;
      // 목차 — 리더 안의 지도. 접어 두는 이유는 이것이 본문이 아니라 길잡이라서다.
      // 깊이는 들여쓰기로만 말한다(글자 크기를 건드리면 목차가 제목 행세를 한다).
      const secs = ctx.sections || [];
      const tocHTML = secs.length < 2 ? "" : `
        <details class="rdr-toc">
          <summary class="hint">${COPY.ui.viewx.readerOutline(secs.length)}</summary>
          ${secs.map((s) => `<button class="rdr-sec" data-at="${s.char_start}"
             style="padding-left:${Math.min(Math.max(s.level - 1, 0), 5) * 0.85}rem"
             >${esc(s.title)}</button>`).join("")}
        </details>`;
      detail.innerHTML = `
        ${host.railHeadHTML(COPY.ui.viewx.readerTitle)}
        <!-- 문서는 읽기 모드로 열린다. "상시 편집"은 2026-09-10 에 되돌렸다:
             읽으며 선택하거나 복사하는 클릭이 곧 편집 커서가 되었기 때문이다.
             아래 rdr-mode 버튼으로 편집을 켜며, 저장 상태는 rdr-state 가 말한다. -->
        <p class="rail-acts">
          <button class="btn" id="rdr-back">${COPY.ui.viewx.readerBack}</button>
          <button class="btn" id="rdr-expand">${COPY.ui.viewx.edExpand}</button>
          <button class="btn" id="rdr-copy">${COPY.ui.viewx.edCopy}</button>
          ${isCommunitySpace(cfg.team) ? "" : `<button class="btn" id="rdr-mode">${
            docWriting ? COPY.ui.viewx.edToRead : COPY.ui.viewx.edToWrite}</button>
          <span class="hint" id="rdr-mode-state">${
            docWriting ? COPY.ui.viewx.edWrite : COPY.ui.viewx.edRead}</span>`}
        </p>
        <!-- 문서도 프롬프트를 나눠 준다(발주자 지시 2026-09-10). 노드 카드·노드 상세와
             같은 줄·같은 메뉴를 쓴다 — 세 자리가 같은 일을 다른 모양으로 하면 어느
             쪽이 맞는지 매번 다시 정해야 한다(askRowHTML 머리말과 같은 이유).
             ⚠ 이 주석은 템플릿 리터럴 **안**이다 — 백틱을 쓰면 문자열이 거기서 끊긴다. -->
        ${askRowHTML()}
        ${dupTitle ? "" : `<h2 id="rdr-title" style="margin-top:.4rem"${docWriting ? " hidden" : ""}>${esc(docTitle(doc.title))}</h2>`}
        <p>${badge} <span class="hint" id="rdr-state"></span></p>
        ${mentionsHTML}
        ${tocHTML}
        <div id="rdr-host"></div>`;
      host.wireRailHead();
      // 칩을 누르면 그 노드로 간다 — D70 의 약속("문서를 고르면 지도 어디인지 안다")을
      // 문서 쪽에서도 지키는 문이다. 스코프가 바뀌면 레일은 노드 상세로 갈린다.
      detail.querySelectorAll<HTMLElement>(".rdr-chips [data-go]").forEach((b) =>
        b.addEventListener("click", () =>
          void host.focusNode(b.dataset.go!, b.dataset.dom || undefined)));
      // 문서의 프롬프트 셋. 수치를 안 쓰므로 한 번 그리고 끝이다(노드 쪽은 관계·
      // 문서 수가 늦게 오지만 문서는 자기 자신이 이미 전부다).
      // ⚠ **노드 쪽 손을 먼저 끊는다.** 두 화면이 같은 패널을 쓰는데 메뉴는 문서에
      //    하나뿐이라, 노드 상세가 남긴 늦은 콜백(nodeContextOnce 응답)이 방금 그린
      //    문서 메뉴를 노드 항목으로 덮는다. 널로 두면 그 호출이 조용히 무시된다.
      host.dropRailAskCounts();
      wireAsk(detail, () => !detail.hidden, () => docAskItems(docId))(0, 0);
      // 여기서 손수 dispose 하지 않는다 — 둘 다 레일을 다시 여닫으므로 위의
      // openRail/closeRail 이 확정까지 맡는다. 두 벌로 두면 다시 갈린다.
      detail.querySelector("#rdr-back")!.addEventListener("click", () => {
        if (host.lastNode()) host.openDetail(host.lastNode()!); else host.closeRail();
      });
      // D45-E 개정 — **Reader 와 편집기가 한 표면이다.** 문서는 언제나 렌더된
      // 모습으로 보이고(원문 마크다운 노출 없음), `편집`은 그 자리에 커서를 켤
      // 뿐이다. 여태 둘은 다른 화면이었고, 보기 화면은 `$$…$$`·`**…**` 같은
      // 소스를 그대로 노출했다.
      const { mountDocEditor, openDocSheet } = await import("../doc-editor");
      if (!current()) return;
      const state = detail.querySelector<HTMLElement>("#rdr-state")!;
      const rdrHost = detail.querySelector<HTMLElement>("#rdr-host")!;
      // 편집 모드에서는 편집기의 제목 줄(제목 · 내용 시점)을 연다. 그때 위의 제목 글자는 숨기고,
      // 저장되면 새 제목으로 다시 쓴다.
      const titleEl = detail.querySelector<HTMLElement>("#rdr-title");
      // 편집 모드의 제목 줄은 자기 저장 상태 글자를 갖는다. 위 상태 줄과 두 번 말하지 않게 숨긴다.
      state.hidden = docWriting;
      let savedHash = doc.content_hash;
      const onSaved = (_id: string, t: string, h: string) => {
        if (h) savedHash = h;
        if (titleEl && t) titleEl.textContent = docTitle(t);
      };
      let handle = await mountDocEditor(rdrHost, {
        owner: cfg.owner, docId, initial: doc.text, title: doc.title,
        contentHash: doc.content_hash, hideTitle: !docWriting, onSaved,
        // 모드가 정한다(위 `docWriting`). 커서는 켜지 않는다 — 문서 열은 지도와
        // 나란히 서므로, 열자마자 키보드를 가져가면 Esc 같은 지도 조작이
        // 글자로 들어간다.
        // ⚠ 읽기 모드에서는 CM 의 장식이 **더 완전하다**: 편집 가능일 때는 커서가
        //    놓인 줄의 소스를 되살려 보여 주는데(라이브 프리뷰의 규칙), 읽기에서는
        //    그럴 이유가 없어 문법이 끝까지 감춰진다. 읽는 화면이 더 깨끗해진다.
        readOnly: !docWriting, autoFocus: false,
        // 제목 줄이 숨어 있어 편집기 자신의 상태 글자도 함께 숨는다 — 저장 신호를
        // 여기로 받아 레일 머리 옆에서 말한다.
        onStatus: (t) => { state.textContent = t; },
        highlights: hl.ranges || [],
        // 본문의 `[[doc_id]]` 가 같은 레일에서 그 문서로 갈린다. 지도 착지는 하지
        // 않는다 — 문서 안의 참조는 "저 문서를 마저 읽자"이지 "저 노드로 가자"가
        // 아니고, 노드로 갈 문은 바로 위 칩이 이미 들고 있다.
        onDocLink: (id) => void openReader(id),
      });
      if (!current()) { await handle.dispose(); return; }
      // 본문 복사. 화면을 긁는 대신 **소스 그대로**를 준다 — 문서의 정본은
      // 마크다운이라, 렌더된 글자를 복사하면 붙여넣은 곳에서 다른 문서가 된다.
      detail.querySelector<HTMLElement>("#rdr-copy")?.addEventListener("click", (e) => {
        const btn = e.currentTarget as HTMLElement;
        const text = handle.getText();
        if (!text) return;                     // 아직 못 읽은 문서 — 조용히 아무 일도 안 한다
        const back = COPY.ui.viewx.edCopy;
        btn.textContent = COPY.ui.viewx.copyLinkDone;
        window.setTimeout(() => { btn.textContent = back; }, 1400);
        void navigator.clipboard.writeText(text)
          .catch(() => { btn.textContent = COPY.ui.viewx.copyLinkFail; });
      });
      // 읽기 ↔ 편집. **다시 읽지 않는다** — 같은 본문 위에서 커서만 켜고 끄는 일이라
      // 왕복을 붙이면 모드 전환이 문서 열기만큼 비싸진다. 나가면서 확정하는 규율은
      // `dispose` 가 이미 진다(대기 중이던 디바운스를 저장하고 놓는다).
      detail.querySelector<HTMLElement>("#rdr-mode")?.addEventListener("click", () => void (async () => {
        const btn = detail.querySelector<HTMLElement>("#rdr-mode");
        const tag = detail.querySelector<HTMLElement>("#rdr-mode-state");
        const text = handle.getText();          // 방금까지 친 것을 그대로 물려준다
        await handle.dispose();
        docWriting = !docWriting;
        if (btn) btn.textContent = docWriting ? COPY.ui.viewx.edToRead : COPY.ui.viewx.edToWrite;
        if (tag) tag.textContent = docWriting ? COPY.ui.viewx.edWrite : COPY.ui.viewx.edRead;
        if (titleEl) titleEl.hidden = docWriting;
        state.hidden = docWriting;
        handle = await mountDocEditor(rdrHost, {
          owner: cfg.owner, docId, initial: text || doc.text,
          title: titleEl?.textContent || doc.title,
          contentHash: savedHash, hideTitle: !docWriting, onSaved,
          readOnly: !docWriting,
          // 편집으로 들어갈 때만 커서를 준다 — 그것이 이 버튼을 누른 이유다.
          autoFocus: docWriting,
          onStatus: (t) => { state.textContent = t; },
          highlights: hl.ranges || [],
          onDocLink: (id) => void openReader(id),
        });
      })());
      // 목차 클릭·청크 히트 착지 — 편집기가 이미 오프셋으로 스크롤할 줄 안다
      // (밑줄 첫 위치로 가던 그 길). 마운트가 앉은 뒤라야 좌표가 맞는다.
      detail.querySelectorAll<HTMLElement>(".rdr-sec").forEach((b) =>
        b.addEventListener("click", () => handle.scrollToOffset(Number(b.dataset.at) || 0)));
      // 청크 히트 착지 — 그 대목으로 앉힌다. 번호를 못 찾으면(재청킹으로 어긋났거나
      // 오프셋을 잃은 레거시 행) 문서 맨 위로 남는다: 조용한 강등이지 오류가 아니다.
      const at = chunkIndex === undefined ? undefined
        : (ctx.chunks || []).find((c) => c.index === chunkIndex)?.char_start;
      if (at) requestAnimationFrame(() => handle.scrollToOffset(at));
      // peek→전체폭 승격 — 좁은 레일이 답답할 때 같은 문서를 시트로 연다.
      // 이 문만 확정을 **기다린다**: 시트가 곧바로 같은 문서를 서버에서 다시 읽으므로,
      // 저장이 끝나기 전에 열면 방금 친 글자가 없는 본문이 뜨고(그 뒤 편집은 해시
      // 충돌로 튄다). 나머지 출구는 다시 읽지 않으므로 기다릴 이유가 없다.
      detail.querySelector("#rdr-expand")!.addEventListener("click", () => void (async () => {
        await handle.dispose();
        host.closeRail();
        await openDocSheet({ owner: cfg.owner, docId });
      })());
    } catch {
      if (current()) detail.innerHTML = `<p class="hint">${COPY.ui.viewx.readerFail}</p>`;
    }
  }

  async function suggestTiesInto(nd: N3) {
    const box = detail.querySelector<HTMLElement>("#ties-suggest");
    if (!box) return;
    box.innerHTML = `<p class="hint">${COPY.ui.viewx.suggestLooking}</p>`;
    try {
      const res = await api.suggestTies(cfg.owner, nd.id);
      const sug: { id: string; title: string; domain: string; shared_tags: string[] }[] =
        res.suggestions || [];
      if (!res.executed || sug.length === 0) {
        box.innerHTML = `<p class="hint">${COPY.ui.viewx.suggestNone}</p>`;
        return;
      }
      box.innerHTML = `<p class="hint" style="margin-bottom:.3rem">${COPY.ui.viewx.suggestHeader}</p>` +
        sug.map((s, i) => `
          <div class="list-row">
            <span class="title" style="font-size:.9em">${esc(s.title)}
              ${s.shared_tags.length ? `<span class="hint">· ${esc(s.shared_tags.join(", "))}</span>` : ""}</span>
            <span class="actions" style="visibility:visible"><button data-bind="${i}">${COPY.ui.viewx.bind}</button></span>
          </div>`).join("");
      box.querySelectorAll<HTMLElement>("[data-bind]").forEach((b) =>
        b.addEventListener("click", async () => {
          const s = sug[Number(b.dataset.bind)];
          b.textContent = "…";
          try {
            // 양 노드 모두 기존 노드(matched) — title 만 동봉해 클로버 없이 MERGE
            await api.ingestFragment({
              nodes: [{ type: "concept", id: nd.id, title: nd.label },
                      { type: "concept", id: s.id, title: s.title }],
              edges: [{ type: "relates_to", from: nd.id, to: s.id }],
            });
            await host.showLevel();   // 재렌더(연결 반영)
            host.openRail();                        // show() 가 닫으므로 확인 문구 재표시
            detail.innerHTML = `<h2>${esc(nd.label)}</h2>
              <p>${COPY.ui.viewx.bound(esc(s.title))}</p>`;
          } catch {
            b.textContent = "Bind";
            box.insertAdjacentHTML("beforeend",
              `<div class="error-banner">${COPY.ui.viewx.bindFail}</div>`);
          }
        }));
    } catch {
      box.innerHTML = `<p class="hint">${COPY.ui.viewx.unavailable}</p>`;
    }
  }

  return { loadNodeSource, openDocAt, openReader };
}
