// 관계 상세(문장 · 근거 · 양 끝 · 지우기)와 지표 바의 드릴다운(그 구간의 개념 목록): 둘 다 상세 레일에 그린다.
// 그래프 표면(`view.ts`)이 `ViewHost` 를 넘겨 만든다. 레일의 크롬은 `view-rail.ts`, 노드 상세는 표면이 그린다.
import { api, cfg } from "../api";
import { COPY } from "../copy";
import { esc, confirmDestructive } from "../components";
import { loaderInline } from "../vinc-loaders.js";
import type { N3, ViewHost } from "./view-host";

/** 표면이 쥐는 관계 상세와 드릴다운의 손잡이. */
export type EdgeDetail = {
  /** 관계 하나의 상세를 레일에 연다. 연결 줄의 관계 이름이 부른다. */
  openEdgeDetail: ViewHost["openEdgeDetail"];
  /** 그 주제 · 구간(`mark`)의 개념 목록을 레일에 연다. 줄을 누르면 그 개념을 고르고 상세를 연다. */
  openDrilldown(domain: string, mark: string, seed?: string): Promise<void>;
};

/** 관계 상세와 드릴다운을 그 표면의 상세 레일에 묶는다. 표면의 상태(지금 층의 노드 · 관계, 레일, 층)에는 `host` 로만 닿는다. */
export function createEdgeDetail(host: ViewHost): EdgeDetail {
  const { detail } = host;

  async function openEdgeDetail(e: { id?: string; source: string; target: string; type: string;
                                     srcTitle?: string; dstTitle?: string }) {
    // D45-V 1단계 — 흐름 선 클릭: "왜 A 가 B 로 이어지는가". 클릭=고정(pin) —
    // 상세 패널 자리를 그대로 쓴다(노드 상세와 같은 표면, ESC/다른 클릭으로 이동).
    host.openRail();
    detail.innerHTML = `<p class="hint">${loaderInline(COPY.ui.viewx.relLoading)}</p>`;
    const pending = detail.firstChild, graphAtOpen = host.curEdges();
    // Use only an exact authored relation from this already-authorized graph.
    // A parallel relation without an ID must never borrow another one's evidence.
    const matches = host.curEdges().filter(x => !x.presentation && x.source === e.source
      && x.target === e.target && x.type === e.type && (!e.id || x.id === e.id));
    const known = matches.length === 1 ? matches[0] : undefined;
    try {
      let res: Awaited<ReturnType<typeof api.edgeContext>>;
      try { res = await api.edgeContext(cfg.owner, e.source, e.target, e.type); }
      catch (error) { if (!known) throw error; res = { executed: false }; }
      let guessed = false;   // 서버가 이 관계를 못 읽었다 — 근거가 있는지도 모른다
      if (detail.hidden || detail.firstChild !== pending || host.curEdges() !== graphAtOpen) return;
      if (!res.executed && known) res = { executed: true,
        confidence: known.confidence, evidence: known.evidence,
        src: { id: e.source, title: host.curNodes().find(n => n.id === e.source)?.label || e.srcTitle },
        dst: { id: e.target, title: host.curNodes().find(n => n.id === e.target)?.label || e.dstTitle } };
      // 관계 상세 조회는 개념끼리만 안다(결정·기록이 낀 관계는 거절한다). 이름을 들고 왔으면
      // 근거 없이 그 관계를 그대로 보이고 지우는 문은 남긴다 — 이은 것을 못 지우면 안 된다.
      if (!res.executed) {
        if (!e.srcTitle || !e.dstTitle) {
          detail.innerHTML = `<p class="hint">${COPY.ui.viewx.relFail}</p>`;
          return;
        }
        res = { executed: true };
        guessed = true;
      }
      const a = res.src?.title || e.srcTitle || e.source, b = res.dst?.title || e.dstTitle || e.target;
      const sentence = (COPY.ui.viewx.relSentence[e.type]
        || COPY.ui.viewx.relSentence.RELATED_TO)(a, b);
      const conf = res.confidence && res.confidence > 0
        ? ` <span class="hint">· ${COPY.ui.viewx.relConfidence(res.confidence)}</span>` : "";
      // 양 끝은 한 줄 설명만 보인다. 문서 발췌로 메우지 않는다: 잘린 마크다운은 문장으로
      // 읽히지 않는다. 설명이 없으면 제목(위 h2)이 그 자리를 한다.
      const side = (t: string, s?: { one_liner?: string }) => {
        const body = s?.one_liner || "";
        return body ? `
          <div class="list-row" style="display:block">
            <span class="title" style="font-size:.85em">${esc(t)}</span>
            <p class="hint" style="margin:.15rem 0 0">${esc(body)}</p>
          </div>` : "";
      };
      // 열 머리(크기 · 닫기)를 함께 싣는다 — 여태 이 화면만 머리가 없어 Esc 말고는 나갈 문이 없었다.
      detail.innerHTML = `
        ${host.railHeadHTML(esc(COPY.author.linkDetailTip))}
        <h2>${esc(a)} → ${esc(b)}</h2>
        <p>${esc(sentence)}${conf}</p>
        ${guessed ? "" : `<p class="hint" style="margin-bottom:.3rem">${COPY.ui.viewx.relEvidence}</p>
        ${res.evidence
          ? `<p>${esc(res.evidence)}</p>`
          : `<p class="hint">${COPY.ui.viewx.relEvidenceNone}</p>`}`}
        ${side(a, res.src)}${side(b, res.dst)}
        <p class="rail-acts"><button class="btn btn-quiet" id="edge-del">${
          esc(COPY.author.edgeDel)}</button></p>
        <p class="hint" id="edge-del-msg" role="status"></p>`;
      host.wireRailHead();
      // 연결 지우기(흐름 F 의 짝). 여태 API 는 있는데 부르는 곳이 없었다. 되돌릴 수 없으므로
      // 확인을 받고, 지운 것이 0 이면(지울 수 없는 종류·이미 없음) 그렇게 말한다.
      detail.querySelector("#edge-del")!.addEventListener("click", async () => {
        const A = COPY.author;
        const say = (t: string) => { const m = detail.querySelector("#edge-del-msg"); if (m) m.textContent = t; };
        const guard = api.previewDeleteEdge(cfg.owner, e.source, e.target, e.type);
        if (!await confirmDestructive(esc(A.edgeDelAsk(a, b)), COPY.ui.viewx.delConfirm,
                                      COPY.ui.viewx.delCancel)) return;
        try {
          const r = await api.deleteEdge(cfg.owner, e.source, e.target, e.type, await guard);
          if (!r.executed) { say(A.edgeDelFail); return; }
          if (!r.deleted?.edges) { say(r.note ? A.edgeDelUnsupported : A.edgeDelFail); return; }
          host.closeRail();
          void host.showLevel({ keepPanel: true, keepCamera: true });
        } catch { say(A.edgeDelFail); }
      });
    } catch {
      if (!detail.hidden && detail.firstChild === pending && host.curEdges() === graphAtOpen)
        detail.innerHTML = `<p class="hint">${COPY.ui.viewx.relFail}</p>`;
    }
  }

  /** 합계 구간 클릭 → 개념 목록 드릴다운(D45-V2 3단계). "이 숫자는 어떤 개념들인가".
   *  개념 클릭 → 그 개념 상세(node-context)로 한 홉 더(문서는 거기서). */
  async function openDrilldown(domain: string, mark: string, seed?: string) {
    host.openRail();
    detail.innerHTML = `<p class="hint">${loaderInline(COPY.ui.viewx.drillLoading)}</p>`;
    try {
      const res = await api.ledgerDrilldown(cfg.owner, domain, mark, seed);
      if (detail.hidden) return;
      if (!res.executed) { detail.innerHTML = `<p class="hint">${COPY.ui.viewx.drillFail}</p>`; return; }
      const items = res.items || [];
      const segName = (COPY.ui.viewx.seg as Record<string, string>)[mark] || mark;
      const head = `<h2>${esc(COPY.ui.viewx.drillHeader(domain, segName, res.total || 0))}</h2>`;
      if (items.length === 0) {
        detail.innerHTML = head + `<p class="hint">${COPY.ui.viewx.drillEmpty}</p>`;
        return;
      }
      // V10 — 목록 안 필터. 40행짜리 제목의 벽에서 원하는 것을 눈으로 찾게 두지 않는다.
      const rowsHtml = (q: string) => items
        .filter((it) => !q || (it.title + " " + (it.one_liner || "")).toLowerCase().includes(q))
        .map((it) => `
        <div class="list-row drill-c" data-id="${esc(it.concept_id)}" data-lab="${esc(it.title)}"
             data-dom="${esc(domain)}" style="display:block;cursor:pointer">
          <span class="title" style="font-size:.9em">${esc(it.title)}</span>
          ${it.one_liner ? `<p class="hint" style="margin:.1rem 0 0">${esc(it.one_liner)}</p>` : ""}
        </div>`).join("");
      const wireRows = () => detail.querySelectorAll<HTMLElement>(".drill-c").forEach((row) =>
        row.addEventListener("click", () => host.openDetail({
          id: row.dataset.id!, label: row.dataset.lab!, domain: row.dataset.dom!,
          degree: 0,
        } as N3)));
      detail.innerHTML = head
        + (items.length > 8
          ? `<input id="drill-q" class="rail-filter" type="search" autocomplete="off"
               placeholder="${esc(COPY.ui.viewx.drillFilter)}" />` : "")
        + `<div id="drill-list">${rowsHtml("")}</div>`;
      detail.querySelector<HTMLInputElement>("#drill-q")?.addEventListener("input", (e) => {
        const q = (e.target as HTMLInputElement).value.trim().toLowerCase();
        detail.querySelector<HTMLElement>("#drill-list")!.innerHTML = rowsHtml(q);
        wireRows();
      });
      wireRows();
      return;
    } catch {
      if (!detail.hidden) detail.innerHTML = `<p class="hint">${COPY.ui.viewx.drillFail}</p>`;
    }
  }

  return { openEdgeDetail, openDrilldown };
}
