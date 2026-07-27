import { api } from "../api";
import { COPY } from "../copy";
import { header } from "../components";
import { renderBrowse } from "./records";

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
    ${header("search", "Search", COPY.ui.meta.search)}
    <form id="search-form" role="search">
      <input id="q" type="search" placeholder="${COPY.search.idle}  ·  domain:math tag:core &quot;phrase&quot;"
             style="width:100%;font:inherit;padding:var(--space-3,.75rem);
                    border:1px solid hsl(var(--input));border-radius:var(--radius);
                    background:hsl(var(--card));color:hsl(var(--foreground))" />
    </form>
    <div id="search-body"><p class="hint">${COPY.search.idle}</p></div>`;
  const body = el.querySelector<HTMLElement>("#search-body")!;

  const RECENT_KEY = "vinc.search.recent";
  const recent: string[] = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]");

  // 빈 검색어(부팅·필드 비움) = 전체 문서 브라우즈(구 Records 흡수) + 최근검색 칩.
  function showEmpty() {
    body.innerHTML = `<div id="search-recent"></div><div id="search-list"></div>`;
    const rec = body.querySelector<HTMLElement>("#search-recent")!;
    if (recent.length) {
      rec.innerHTML = recent.map((r) => `<button class="chip" data-recent>${r}</button>`).join(" ");
      rec.querySelectorAll("[data-recent]").forEach((b) =>
        b.addEventListener("click", () => {
          (el.querySelector("#q") as HTMLInputElement).value = (b as HTMLElement).textContent!;
          run((b as HTMLElement).textContent!.trim(), false);
        }));
    }
    void renderBrowse(body.querySelector<HTMLElement>("#search-list")!);
  }
  showEmpty();

  let debounce: number | undefined;
  el.querySelector("#q")!.addEventListener("input", (e) => {
    clearTimeout(debounce);
    const raw = (e.target as HTMLInputElement).value.trim();
    if (raw.length === 0) { showEmpty(); return; }        // 필드 비우면 브라우즈 복귀
    if (raw.length >= 2)
      debounce = window.setTimeout(() => run(raw, false), 250);   // 라이브(250ms)
  });

  el.querySelector("#search-form")!.addEventListener("submit", (e) => {
    e.preventDefault();
    const raw = (el.querySelector("#q") as HTMLInputElement).value.trim();
    if (raw) run(raw, true);
  });

  async function run(raw: string, commit: boolean) {
    if (commit) {
      const next = [raw, ...recent.filter((r) => r !== raw)].slice(0, 6);
      localStorage.setItem(RECENT_KEY, JSON.stringify(next));
    }
    const { text, domain, tag } = parseQuery(raw);
    body.innerHTML = `<div class="hint">${COPY.search.searching}</div>`;
    try {
      const res = await api.search(text || raw);
      // Chunk 결과(rows)와 **개념/레코드 타이틀·그래프 매치(node_hits)를 함께** 보여준다.
      // 백엔드(T3b)는 Chunk 인덱스에 없는 Concept/Record 를 항상 node_hits 로 반환한다
      // ("Chunk 0개 스토어에서도 개념이 검색된다") — 팩만 넣어 문서·청크가 없어도 개념이
      // 검색되게 하는 핵심 경로. 이전엔 프론트가 rows 만 읽어 개념이 통째로 안 보였다.
      const nodeHits: any[] = (res.node_hits || []).map((h: any) => ({
        ...h,
        arms: h.matched_in === "graph" ? ["semantic"] : ["keyword"],   // 마크 재사용(≈/≡)
        _node: true,
      }));
      let rows: any[] = [...(res.rows || []), ...nodeHits];
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
      const esc = (s: string) => String(s ?? "").replace(/[&<>"]/g, (c) =>
        c === "&" ? "&amp;" : c === "<" ? "&lt;" : c === ">" ? "&gt;" : "&quot;");
      const row = (r: any) => {
        const marks = (r.arms || []).map((a: string) => MARK[a] || "").join("");
        const score = r.rrf_score ? ` data-tip="RRF ${r.rrf_score.toFixed(4)}"` : "";
        // 착지 대상 — 노드 히트는 그 노드로, 청크 히트는 그 청크가 언급하는 개념이
        // 없으므로 문서 자체로 보낸다(현재는 노드 히트만 Atlas 착지가 성립한다).
        const target = r._node ? String(r.id || "") : "";
        // 왜 맞았는지를 한 줄 더 보여준다(D24 §4.2) — 제목만으로는 고를 수 없다.
        const snip = r.one_liner || r.snippet || r.text || r.section_title || "";
        return `
          <div class="list-row" ${target ? `data-go="${esc(target)}"` : ""}
               style="display:grid;grid-template-columns:1.4rem 1fr auto;gap:.6rem;align-items:baseline${target ? ";cursor:pointer" : ""}">
            <span class="meta"${score}>${marks || "≡"}</span>
            <span style="min-width:0">
              <span class="title">${esc(r.title || r.section_title || r.id)}</span>
              ${snip ? `<span class="hint" style="display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${
                esc(String(snip).slice(0, 140))}</span>` : ""}
            </span>
            <span class="meta">${esc(r.domain || "")}</span>
          </div>`;
      };
      body.innerHTML = `<p class="hint">${COPY.search.matches(rows.length)}</p>` +
        [...groups.entries()].map(([d, rs]) =>
          (d ? `<h3>${esc(d)} <span class="hint">${rs.length}</span></h3>` : "") +
          rs.map(row).join("")).join("");
      // V2 — 여태 이 버튼은 `location.hash='#atlas'` 였다: 찾은 노드가 아니라 L0
      // 첫 화면으로 보내는 스텁이라, 검색이 조회 동선을 끝맺지 못했다. `trace` 딥링크와
      // 초점색 착지는 이미 구현돼 있었는데 쓰지 않고 있었을 뿐이다.
      body.querySelectorAll<HTMLElement>("[data-go]").forEach((r) =>
        r.addEventListener("click", () => {
          location.hash = `#atlas/trace/${encodeURIComponent(r.dataset.go!)}`;
        }));
    } catch {
      body.innerHTML = `<div class="error-banner">${COPY.search.error}</div>`;
    }
  }
}
