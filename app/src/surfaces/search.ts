import { api } from "../api";
import { COPY } from "../copy";

/** Surface 4 — Search (spec: vinc.d.search v0.1). Hybrid is the default; no mode toggle. */
export function renderSearch(el: HTMLElement) {
  el.innerHTML = `
    <h1>Search</h1>
    <form id="search-form" role="search">
      <input id="q" type="search" placeholder="${COPY.search.idle}"
             style="width:100%;font:inherit;padding:var(--space-3,.75rem);
                    border:1px solid hsl(var(--input));border-radius:var(--radius);
                    background:hsl(var(--card));color:hsl(var(--foreground))" />
    </form>
    <div id="search-body"><p class="hint">${COPY.search.idle}</p></div>`;
  const body = el.querySelector<HTMLElement>("#search-body")!;
  el.querySelector("#search-form")!.addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = (el.querySelector("#q") as HTMLInputElement).value.trim();
    if (!q) return;
    body.innerHTML = `<div class="hint">Searching…</div>`;
    try {
      const res = await api.search(q);
      const rows: any[] = res.rows || [];
      if (rows.length === 0) {
        body.innerHTML = `<div class="empty"><p>${COPY.search.empty(q)}</p>
          <a class="btn" href="#atlas">${COPY.search.emptyCta}</a></div>`;
        return;
      }
      body.innerHTML = `<p class="hint">${COPY.search.matches(rows.length)}</p>` +
        rows.map((r) => `
          <div class="list-row">
            <span class="title">${r.title || r.id}</span>
            <span class="meta">${r.domain || r.doc_id || ""}</span>
            <span class="actions"><button onclick="location.hash='#atlas'">${COPY.search.actions.atlas}</button></span>
          </div>`).join("");
    } catch {
      body.innerHTML = `<div class="error-banner">${COPY.search.error}</div>`;
    }
  });
}
