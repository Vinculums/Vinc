import { api, cfg } from "../api";
import { COPY } from "../copy";
import { header } from "../components";
import { stateDot } from "../icons";

/** Surface 2 — Records (Figma 5:71 grammar · spec vinc.d.records).
 *  Top filter input, rows led by a notation state mark (● pinned · ◐ tagged ·
 *  ○ plain — state is never color), meta line "domain · date · pin". */
export async function renderRecords(el: HTMLElement) {
  el.innerHTML = `
    ${header("recall", "Records", COPY.ui.meta.records)}
    <input id="records-filter" type="search" placeholder="${COPY.ui.recordsUi.filter}"
           style="width:100%;font:inherit;padding:var(--space-3,.75rem);margin-bottom:var(--space-4,1rem);
                  border:1px solid hsl(var(--input));border-radius:var(--radius);
                  background:hsl(var(--card));color:hsl(var(--foreground))" />
    <div id="records-body"><div class="hint">${COPY.ui.common.loading}</div></div>`;
  const body = el.querySelector<HTMLElement>("#records-body")!;
  const filter = el.querySelector<HTMLInputElement>("#records-filter")!;
  let rows: any[] = [];

  const draw = () => {
    const t = filter.value.toLowerCase();
    const vis = t ? rows.filter((r) => (r.title || r.id).toLowerCase().includes(t)) : rows;
    if (vis.length === 0) {
      body.innerHTML = `<div class="empty"><p>${COPY.records.idle}</p>
        <a class="btn btn-primary" href="#intake">${COPY.records.idleCta}</a></div>`;
      return;
    }
    const domains = new Set(vis.map((r) => r.domain).filter(Boolean));
    body.innerHTML = `
      <p class="hint">${COPY.records.header(vis.length, domains.size)}</p>
      ${vis.map((r) => `
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
    filter.addEventListener("input", draw);
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
