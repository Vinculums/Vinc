import { api, cfg } from "../api";
import { COPY } from "../copy";
import { stateDot } from "../icons";

/** Browse list (구 Surface 2 — Records; LNB 통합으로 Search 빈 상태의 전체 문서
 *  브라우즈로 재사용). 자체 header/filter 크롬은 없음 — Search 의 검색 필드가
 *  단일 입력. 행은 표기 상태 마크(● pinned · ◐ tagged · ○ plain — 색 아님) +
 *  meta "domain · date · pin" + Atlas/pin 액션. */
export async function renderBrowse(body: HTMLElement) {
  body.innerHTML = `<div class="hint">${COPY.ui.common.loading}</div>`;
  let rows: any[] = [];

  const draw = () => {
    if (rows.length === 0) {
      body.innerHTML = `<div class="empty"><p>${COPY.records.idle}</p>
        <a class="btn btn-primary" href="#packs">${COPY.records.idleCta}</a></div>`;
      return;
    }
    const domains = new Set(rows.map((r) => r.domain).filter(Boolean));
    body.innerHTML = `
      <p class="hint">${COPY.records.header(rows.length, domains.size)}</p>
      ${rows.map((r) => `
        <div class="list-row" data-id="${r.id}" data-domain="${r.domain || ""}">
          ${stateDot(r.pinned ? "filled" : (r.tags || []).length ? "half" : "empty")}
          <span class="title">${r.title || r.id}</span>
          <span class="meta">${[r.domain, r.date, r.pinned ? "pin" : ""].filter(Boolean).join(" · ")}</span>
          <span class="actions">
            <button data-act="atlas">${COPY.records.actions.atlas}</button>
            <button data-act="pin">${COPY.records.actions.pin}</button>
          </span>
        </div>`).join("")}`;
  };

  try {
    const res = await api.recall(cfg.owner);
    rows = res.executed ? res.rows || [] : [];
    draw();
    body.addEventListener("click", async (e) => {
      const btn = (e.target as HTMLElement).closest("button[data-act]") as HTMLElement | null;
      if (!btn) return;
      const row = btn.closest(".list-row") as HTMLElement;
      if (btn.dataset.act === "atlas") location.hash = `#atlas/${encodeURIComponent(row.dataset.domain || "")}`;
      if (btn.dataset.act === "pin") await api.pin(row.dataset.id!);
    });
  } catch {
    body.innerHTML = `<div class="error-banner">${COPY.records.error}</div>`;
  }
}
