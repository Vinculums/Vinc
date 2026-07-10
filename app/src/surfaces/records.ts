import { api, cfg } from "../api";
import { COPY } from "../copy";

/** Surface 2 — Records (spec: vinc.d.records v0.1). Every row hands off to Atlas. */
export async function renderRecords(el: HTMLElement) {
  el.innerHTML = `<h1>Records</h1><div id="records-body"><div class="hint">Loading…</div></div>`;
  const body = el.querySelector<HTMLElement>("#records-body")!;
  try {
    const res = await api.recall(cfg.owner);
    const rows: any[] = res.rows || [];
    if (!res.executed || rows.length === 0) {
      body.innerHTML = `
        <div class="empty"><p>${COPY.records.idle}</p>
        <a class="btn btn-primary" href="#intake">${COPY.records.idleCta}</a></div>`;
      return;
    }
    const domains = new Set(rows.map((r) => r.domain).filter(Boolean));
    body.innerHTML = `
      <p class="hint">${COPY.records.header(rows.length, domains.size)}</p>
      ${rows.map((r) => `
        <div class="list-row" data-id="${r.id}" data-domain="${r.domain || ""}">
          <span class="title">${r.title || r.id}</span>
          <span class="meta">${r.domain || ""} · ${r.date || ""}${r.pinned ? " · pinned" : ""}</span>
          <span class="actions">
            <button data-act="atlas">${COPY.records.actions.atlas}</button>
            <button data-act="pin">${COPY.records.actions.pin}</button>
          </span>
        </div>`).join("")}
    `;
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
