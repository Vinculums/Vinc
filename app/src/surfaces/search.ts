import { api } from "../api";
import { COPY } from "../copy";

/** Surface 4 — Search (spec: vinc.d.atlas-visual-language §4, DEC D24).
 *  Single field + operators (domain:, tag:, "phrase"). Results grouped by
 *  domain; match notation ≡ (keyword arm) / ≈ (semantic arm) shows *why* a row
 *  matched — scores stay in the tooltip. */

function parseQuery(raw: string): { text: string; domain?: string; tag?: string } {
  let text = raw;
  const take = (op: string) => {
    const m = text.match(new RegExp(`(?:^|\\s)${op}:(\\S+)`));
    if (!m) return undefined;
    text = text.replace(m[0], " ").trim();
    return m[1];
  };
  return { domain: take("domain"), tag: take("tag"), text: text.trim() };
}

const MARK: Record<string, string> = { keyword: "≡", semantic: "≈" };

export function renderSearch(el: HTMLElement) {
  el.innerHTML = `
    <h1>Search</h1>
    <form id="search-form" role="search">
      <input id="q" type="search" placeholder="${COPY.search.idle}  ·  domain:math tag:core &quot;phrase&quot;"
             style="width:100%;font:inherit;padding:var(--space-3,.75rem);
                    border:1px solid hsl(var(--input));border-radius:var(--radius);
                    background:hsl(var(--card));color:hsl(var(--foreground))" />
    </form>
    <div id="search-body"><p class="hint">${COPY.search.idle}</p></div>`;
  const body = el.querySelector<HTMLElement>("#search-body")!;

  el.querySelector("#search-form")!.addEventListener("submit", async (e) => {
    e.preventDefault();
    const raw = (el.querySelector("#q") as HTMLInputElement).value.trim();
    if (!raw) return;
    const { text, domain, tag } = parseQuery(raw);
    body.innerHTML = `<div class="hint">Searching…</div>`;
    try {
      const res = await api.search(text || raw);
      let rows: any[] = res.rows || [];
      if (domain) rows = rows.filter((r) => (r.domain || r.doc_id || "").includes(domain));
      if (tag) rows = rows.filter((r) => (r.tags || []).includes(tag));
      if (rows.length === 0) {
        body.innerHTML = `<div class="empty"><p>${COPY.search.empty(raw)}</p>
          <a class="btn" href="#atlas">${COPY.search.emptyCta}</a></div>`;
        return;
      }
      // 도메인별 그루핑(D24) — 단일 도메인 필터 시 플랫
      const groups = new Map<string, any[]>();
      for (const r of rows) {
        const d = domain ? "" : (r.domain || r.doc_id || "other");
        if (!groups.has(d)) groups.set(d, []);
        groups.get(d)!.push(r);
      }
      const row = (r: any) => {
        const marks = (r.arms || []).map((a: string) => MARK[a] || "").join("");
        const score = r.rrf_score ? ` title="RRF ${r.rrf_score.toFixed(4)}"` : "";
        return `
          <div class="list-row">
            <span class="title">${r.title || r.section_title || r.id}</span>
            <span class="meta"${score}>${marks || "≡"}</span>
            <span class="actions"><button onclick="location.hash='#atlas'">${COPY.search.actions.atlas}</button></span>
          </div>`;
      };
      body.innerHTML = `<p class="hint">${COPY.search.matches(rows.length)}</p>` +
        [...groups.entries()].map(([d, rs]) =>
          (d ? `<h3>${d} <span class="hint">${rs.length}</span></h3>` : "") +
          rs.map(row).join("")).join("");
    } catch {
      body.innerHTML = `<div class="error-banner">${COPY.search.error}</div>`;
    }
  });
}
