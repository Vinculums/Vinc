import { api, cfg, type SearchOptions } from "../api";
import { COPY } from "../copy";
import { header, armsNoticeBadge, searchTemporalText } from "../components";
import { renderBrowse } from "./records";
import { viewHooks } from "../topbar";
import { renderSearchError } from "../search-error";
import { partitionSearchResults } from "../search-relevance";

function esc(s: string): string {
  return String(s ?? "").replace(/[&<>"]/g, (c) =>
    c === "&" ? "&amp;" : c === "<" ? "&lt;" : c === ">" ? "&gt;" : "&quot;");
}

/** Surface 4 — Search (spec: [[vinc.spec.surface-search]], DEC D24).
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
    ${header("search", COPY.ui.topbar.search, COPY.ui.meta.search)}
    <form id="search-form" role="search">
      <input id="q" type="search" placeholder="${COPY.search.idle}  ·  domain:math tag:core &quot;phrase&quot;"
             style="width:100%;font:inherit;padding:var(--space-3,.75rem);
                    border:1px solid hsl(var(--input));border-radius:var(--radius);
                    background:hsl(var(--card));color:hsl(var(--foreground))" />
      <label class="hint" for="search-scope">${COPY.search.modeLabel}</label>
      <select id="search-scope" class="btn" aria-label="${COPY.search.modeLabel}">
        ${[["general", COPY.search.modes.general], ["current", COPY.search.modes.current], ["historical", COPY.search.modes.historical]].map(([value, label]) =>
          `<option value="${value}">${esc(label)}</option>`).join("")}
      </select>
    </form>
    <div id="search-body"><p class="hint">${COPY.search.idle}</p></div>`;
  const body = el.querySelector<HTMLElement>("#search-body")!;

  const RECENT_KEY = "vinc.search.recent";
  const recent: string[] = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]");
  let seq = 0;
  const scope = el.querySelector<HTMLSelectElement>("#search-scope")!;
  scope.addEventListener("change", () => {
    clearTimeout(debounce);
    const raw = (el.querySelector("#q") as HTMLInputElement).value.trim();
    if (raw) void run(raw, false);
  });

  // 빈 검색어(부팅·필드 비움) = 전체 문서 브라우즈(구 Records 흡수) + 최근검색 칩.
  function showEmpty() {
    ++seq;
    body.innerHTML = `<div id="search-recent"></div><div id="search-list"></div>`;
    const rec = body.querySelector<HTMLElement>("#search-recent")!;
    if (recent.length) {
      rec.innerHTML = recent.map((r) => `<button class="chip" data-recent>${esc(r)}</button>`).join(" ");
      rec.querySelectorAll("[data-recent]").forEach((b) =>
        b.addEventListener("click", () => {
          (el.querySelector("#q") as HTMLInputElement).value = (b as HTMLElement).textContent!;
          run((b as HTMLElement).textContent!.trim(), false);
        }));
    }
    void renderBrowse(body.querySelector<HTMLElement>("#search-list")!);
  }
  showEmpty();
  // `#search/<검색어>` 로 들어오면 그 검색어로 바로 찾는다(상단 검색의 "문서 안에서 찾기").
  let pre = "";
  try { pre = decodeURIComponent(location.hash.replace(/^#search\/?/, "")); }
  catch { /* 손으로 친 잘못된 %-주소는 빈 검색으로 연다(화면을 죽이지 않는다) */ }
  if (pre.trim()) {
    (el.querySelector("#q") as HTMLInputElement).value = pre;
    void run(pre.trim(), true);
  }

  let debounce: number | undefined;
  el.querySelector("#q")!.addEventListener("input", (e) => {
    ++seq;
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
    const mine = ++seq;
    if (commit) {
      const next = [raw, ...recent.filter((r) => r !== raw)].slice(0, 6);
      localStorage.setItem(RECENT_KEY, JSON.stringify(next));
    }
    const { text, domain, tag } = parseQuery(raw);
    body.innerHTML = `<div class="hint">${COPY.search.searching}</div>`;
    try {
      const mode = scope.value as SearchOptions["mode"];
      // Explicit scopes use one retrieval path so title matches cannot bypass its filters.
      const [res, docs] = await Promise.all([
        api.search(text || raw, 20, { mode }),
        mode === "general" ? api.docTitleMatches(text || raw).catch(() => null) : Promise.resolve(null),
      ]);

      if (mine !== seq) return;
      // Chunk 결과(rows)와 **개념/레코드 타이틀·그래프 매치(node_hits)를 함께** 보여준다.
      // 백엔드(T3b)는 Chunk 인덱스에 없는 Concept/Record 를 항상 node_hits 로 반환한다
      // ("Chunk 0개 스토어에서도 개념이 검색된다") — 팩만 넣어 문서·청크가 없어도 개념이
      // 검색되게 하는 핵심 경로. 이전엔 프론트가 rows 만 읽어 개념이 통째로 안 보였다.
      const nodeHits: any[] = (res.node_hits || []).map((h: any) => ({
        ...h,
        arms: ["graph", "semantic"].includes(h.matched_in) ? ["semantic"] : ["keyword"],   // 마크 재사용(≈/≡)
        _node: true,
      }));
      const exact = (docs?.docs || []).map((d) => ({
        ...d, id: d.doc_id, arms: ["keyword"],
      }));
      let rows: any[] = [...exact, ...(res.rows || []), ...nodeHits];
      if (domain) rows = rows.filter((r) => (r.domain || r.doc_id || "").includes(domain));
      if (tag) rows = rows.filter((r) => (r.tags || []).includes(tag));
      const { primary, weak } = partitionSearchResults(rows);
      // 벡터 팔이 빠졌으면 그 사실을 말한다(GM-13 이행 배지도 여기로 합쳐졌다).
      // 판단·톤 분기는 `armsNoticeBadge` 한 곳에 있다 — 세 표면이 같은 규칙을
      // 써야 같은 고장이 도구에 따라 보이거나 안 보이는 일이 없다. 정상이면 빈
      // 문자열이라 화면은 지금 그대로다.
      // 결과 마크업은 **이 한 곳으로만** 나간다 — 갈래마다 배지를 따로 붙이면 빠지기
      // 쉬운 쪽이 하필 빈 결과이고, 반쪽 검색의 0건이 진짜 0건으로 읽힌다.
      const show = (html: string) => {
        if (res.temporal_error) html = `<p class="hint">${COPY.search.temporalNotice}</p>` + html;
        body.innerHTML = armsNoticeBadge(res) + html;
      };
      if (rows.length === 0) {
        show(`<div class="empty"><p>${esc(COPY.search.empty(raw))}</p>
          <a class="btn" href="#view">${COPY.search.emptyCta}</a></div>`);
        return;
      }
      // 도메인별 그루핑(D24) — 단일 도메인 필터 시 플랫
      const groups = new Map<string, any[]>();
      for (const r of primary) {
        const d = domain ? "" : (r.domain || r.doc_id || "other");
        if (!groups.has(d)) groups.set(d, []);
        groups.get(d)!.push(r);
      }
      const row = (r: any) => {
        const marks = (r.arms || []).map((a: string) => MARK[a] || "").join("");
        const score = r.rrf_score ? ` data-tip="RRF ${r.rrf_score.toFixed(4)}"` : "";
        // 착지 대상 — 노드 히트는 그 노드로, **청크 히트는 그 문서의 그 대목으로**.
        // 여태 청크 행은 `data-go` 가 없어 눌러도 아무 일이 없었고(불활성), 노드 행과
        // 생김새가 같아 "이 줄만 고장났나"로 읽혔다. 청크 id 는 `<doc>#00007` 이라
        // 문서와 청크 번호를 이미 들고 있다 — 새 조회 없이 그대로 쓴다.
        const target = r._node ? String(r.id || "") : "";
        const cid = !r._node ? String(r.id || "") : "";
        const at = cid.lastIndexOf("#");
        const cdoc = !r._node ? (r.doc_id || (at > 0 ? cid.slice(0, at) : "")) : "";
        const cidx = at > 0 ? Number(cid.slice(at + 1)) : NaN;
        // 왜 맞았는지를 한 줄 더 보여준다(D24 §4.2) — 제목만으로는 고를 수 없다.
        const snip = r.one_liner || r.snippet || r.text || r.section_title || "";
        const clickable = target || cdoc;
        const dates = searchTemporalText(r);
        return `
          <div class="list-row" ${target ? `data-go="${esc(target)}"` : ""}
               ${cdoc ? `data-doc="${esc(cdoc)}"${Number.isFinite(cidx)
                 ? ` data-chunk="${cidx}"` : ""}` : ""}
               style="display:grid;grid-template-columns:1.4rem 1fr auto;gap:.6rem;align-items:baseline${clickable ? ";cursor:pointer" : ""}">
            <span class="meta"${score}>${marks || "≡"}</span>
            <span style="min-width:0">
              <span class="title">${esc(r.title || r.section_title || r.id)}</span>
              <span class="meta" style="display:block">${esc(dates)}</span>
              ${snip ? `<span class="hint" style="display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${
                esc(String(snip).slice(0, 140))}</span>` : ""}
            </span>
            <span class="meta">${esc(r.domain || "")}</span>
          </div>`;
      };
      const primaryHtml = primary.length ? `<p class="hint">${COPY.search.matches(primary.length)}</p>` +
        [...groups.entries()].map(([d, rs]) =>
          (d ? `<h3>${esc(d)} <span class="hint">${rs.length}</span></h3>` : "") +
          rs.map(row).join("")).join("") :
          `<div class="empty"><p>${esc(COPY.search.empty(raw))}</p>
            <a class="btn" href="#view">${COPY.search.emptyCta}</a></div>`;
      const weakHtml = weak.length ? `<details class="search-weak">
        <summary>${COPY.search.weakMatches(weak.length)}</summary>
        <p class="hint">${COPY.search.weakHint}</p>${weak.map(row).join("")}</details>` : "";
      show(primaryHtml + weakHtml);
      // V2 — 여태 이 버튼은 `location.hash='#view'` 였다: 찾은 노드가 아니라 L0
      // 첫 화면으로 보내는 스텁이라, 검색이 조회 동선을 끝맺지 못했다. `trace` 딥링크와
      // 초점색 착지는 이미 구현돼 있었는데 쓰지 않고 있었을 뿐이다.
      // 훅이 있으면 표면을 갈아엎지 않고 그 자리로 간다(상단 바 검색과 같은 태도).
      // 없으면 해시 착지로 강등 — 청크 행은 착지할 노드가 없으므로 문서를 시트로 연다.
      body.querySelectorAll<HTMLElement>("[data-go],[data-doc]").forEach((r) =>
        r.addEventListener("click", async () => {
          const node = r.dataset.go || "";
          const doc = r.dataset.doc || "";
          const chunk = r.dataset.chunk ? Number(r.dataset.chunk) : undefined;
          const h = viewHooks();
          if (h && doc) { h.openDoc(doc, node || undefined, undefined, chunk); return; }
          if (h && node) { h.openNode(node); return; }
          if (node) { location.hash = `#view/trace/${encodeURIComponent(node)}`; return; }
          if (doc) {
            const { openDocSheet } = await import("../doc-editor");
            void openDocSheet({ owner: cfg.owner, docId: doc });
          }
        }));
    } catch (failure) {
      if (mine !== seq) return;
      renderSearchError(body, failure, () => void run(raw, false));
    }
  }
}
