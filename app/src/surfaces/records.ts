import { api, cfg } from "../api";
import { COPY } from "../copy";
import { stateDot } from "../icons";
import { openDocSheet } from "../doc-editor";
import { confirmDestructive } from "../components";

/** Browse list (구 Surface 2 — Records; LNB 통합으로 Search 빈 상태의 전체 문서
 *  브라우즈로 재사용). 자체 header/filter 크롬은 없음 — Search 의 검색 필드가
 *  단일 입력. 행은 표기 상태 마크(● pinned · ◐ tagged · ○ plain — 색 아님) +
 *  meta "domain · date · pin" + Atlas/pin 액션. */
export async function renderBrowse(body: HTMLElement) {
  body.innerHTML = `<div class="hint">${COPY.ui.common.loading}</div>`;
  let rows: any[] = [];
  let docs: { doc_id: string; title: string; origin: string; updated_at: string }[] = [];

  // D45-E — 문서 정본 목록(편집 진입). "새 문서" + 각 문서 클릭 → 편집 시트.
  const docsSection = () => `
    <div class="doc-section" style="margin-bottom:.6rem">
      <p class="hint" style="display:flex;justify-content:space-between;align-items:center">
        <span>${COPY.records.docsHeader(docs.length)}</span>
        <button class="btn" id="doc-new">${COPY.ui.atlasx.edNew}</button></p>
      ${docs.map((d) => `
        <div class="list-row doc-row" data-doc="${d.doc_id}" style="cursor:pointer">
          ${stateDot("empty")}
          <span class="title">${d.title || d.doc_id}</span>
          <span class="meta">${[d.origin, d.updated_at].filter(Boolean).join(" · ")}</span>
        </div>`).join("")}
    </div>`;

  const draw = () => {
    const docsHtml = docs.length > 0 || rows.length === 0 ? docsSection() : "";
    if (rows.length === 0 && docs.length === 0) {
      body.innerHTML = docsSection() + `<div class="empty"><p>${COPY.records.idle}</p>
        <a class="btn btn-primary" href="#packs">${COPY.records.idleCta}</a></div>`;
    } else {
      const domains = new Set(rows.map((r) => r.domain).filter(Boolean));
      body.innerHTML = docsHtml + `
        <p class="hint">${COPY.records.header(rows.length, domains.size)}</p>
        ${rows.map((r) => `
          <div class="list-row" data-id="${r.id}" data-domain="${r.domain || ""}">
            ${stateDot(r.pinned ? "filled" : (r.tags || []).length ? "half" : "empty")}
            <span class="title">${r.title || r.id}</span>
            <span class="meta">${[r.domain, r.date, r.pinned ? "pin" : ""].filter(Boolean).join(" · ")}</span>
            <span class="actions">
              <button data-act="atlas">${COPY.records.actions.atlas}</button>
              <button data-act="pin">${COPY.records.actions.pin}</button>
              <button data-act="del">${COPY.ui.atlasx.del}</button>
            </span>
          </div>`).join("")}`;
    }
    wire();
  };

  const reloadDocs = async () => {
    try { const d = await api.docList(cfg.owner); docs = d.executed ? d.docs || [] : []; }
    catch { /* 문서 목록 실패는 무해 — episodes 목록은 그대로 */ }
  };

  const wire = () => {
    body.querySelector("#doc-new")?.addEventListener("click", () =>
      void openDocSheet({ owner: cfg.owner, onSaved: () => void reloadDocs().then(draw) }));
    body.querySelectorAll<HTMLElement>(".doc-row").forEach((row) =>
      row.addEventListener("click", () =>
        void openDocSheet({ owner: cfg.owner, docId: row.dataset.doc,
                            onSaved: () => void reloadDocs().then(draw) })));
    body.querySelectorAll<HTMLElement>(".list-row[data-id]").forEach(() => {});
  };

  try {
    // Episode(recall) + 문서(docList) + **Concept(graph)** 를 함께 브라우즈한다.
    // 개념은 Atlas 담당이던 구 설계 갭 해소 — 팩(=개념)만 넣어도 Search 빈 화면에
    // 지식이 보인다. graph 는 이미 owner 스코프·화이트리스트(Atlas 와 동일 경로).
    // ⚠ graph 는 엣지·좌표까지 실어 큰 그래프에선 무겁다 — 후속: 경량 concept-list
    //   엔드포인트로 대체(빈화면 재조회 비용 절감).
    const [recallRes, graphRes] = await Promise.all([
      api.recall(cfg.owner), api.graph(cfg.owner), reloadDocs(),
    ]);
    const episodes: any[] = recallRes.executed ? recallRes.rows || [] : [];
    const concepts: any[] = (graphRes.executed ? graphRes.nodes || [] : [])
      .filter((n: any) => (n.kind || "concept") === "concept")
      .map((n: any) => ({ id: n.id, title: n.label, domain: n.domain, tags: n.tags || [] }))
      .sort((a: any, b: any) => (a.domain || "").localeCompare(b.domain || "")
        || (a.title || "").localeCompare(b.title || ""));
    rows = [...episodes, ...concepts];
    draw();
    body.addEventListener("click", async (e) => {
      const btn = (e.target as HTMLElement).closest("button[data-act]") as HTMLElement | null;
      if (!btn) return;
      const row = btn.closest(".list-row") as HTMLElement;
      if (btn.dataset.act === "atlas") location.hash = `#atlas/${encodeURIComponent(row.dataset.domain || "")}`;
      if (btn.dataset.act === "pin") await api.pin(row.dataset.id!);
      // S1 — 노드 삭제(개념·에피소드). 되돌릴 수 없어 확인 게이트를 거치고,
      // 성공하면 목록에서 즉시 제거한다(재조회 없이 — 응답이 곧 진실).
      if (btn.dataset.act === "del") {
        const title = row.querySelector(".title")?.textContent || row.dataset.id || "";
        if (!await confirmDestructive(COPY.ui.atlasx.delNodeAsk(title),
              COPY.ui.atlasx.delConfirm, COPY.ui.atlasx.delCancel)) return;
        try {
          const res = await api.deleteNode(cfg.owner, row.dataset.id!);
          if (!res.executed) throw new Error(res.note || res.error || "delete failed");
          rows = rows.filter((r) => r.id !== row.dataset.id);
          draw();
        } catch {
          btn.textContent = COPY.ui.atlasx.delFail;
        }
      }
    });
  } catch {
    body.innerHTML = `<div class="error-banner">${COPY.records.error}</div>`;
  }
}
