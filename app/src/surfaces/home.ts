import { api, canWriteHere, cfg, isCommunitySpace } from "../api";
import { COPY } from "../copy";
import { docTitle } from "../doc-title";
import { esc, confirmDestructive } from "../components";
import { icon } from "../icons";
import { dayOf } from "../view-prefs";
import { tintMap } from "../view-tint";
import type { ThemeMode } from "../theme";
import type { DocEditorHandle } from "../doc-editor";
import { setTopbarLead, setViewHooks } from "../topbar";
import { agentRoleCount, setLnbActive, setLnbDocOpener, setLnbTopicPicker, setLnbScopeClearer, refreshLnb } from "../lnb";
import { setLiveRefresher } from "../live";
import { loaderRows } from "../vinc-loaders.js";

/** 문서 홈(2026-10-22) — 문서를 보고 쓰고 정리하는 첫 화면.
 *
 *  그래프 위주이던 셸에서 문서는 지도의 노드를 눌러야 열리는 부속이었다. 여기서는 문서가
 *  주인이다: 목록이 첫 화면이고, 문서를 누르면 같은 칸 전체에 읽고 고치는 면이 선다.
 *  그래프는 오른쪽 위의 전환 버튼이 연다(topbar.ts).
 *
 *  짜임은 웹 커뮤니티 홈을 따른다: 주제 칩으로 좁히고(빵부스러기 + 하위 주제 칩), 제목
 *  거르기 · 정렬 · 더 보기가 있는 목록, 그리고 문서 면은 본문 칸(최대 720px) + 옆 칸
 *  (주제 · 수정한 날 · 그래프 자리 · 복사 · 지우기)이다.
 *  근거: [[decision:the-desktop-shell-is-documents-first]]
 *
 *  주소: `#home` = 목록 · `#home/doc/<id>` = 문서 면 · `#home/new` = 새 문서 ·
 *  `#home/agents` = Agents 보드(surfaces/agents-board.ts, Agent 레일이 연다). 거르기 상태는
 *  주소에 싣지 않는다(세션 안에서만 남는다) — 공유할 주소는 문서 하나의 주소다. */

type Doc = {
  doc_id: string; title: string; updated_at?: string; chunks?: number;
  topic?: string; topics?: string[]; primary_concept_id?: string; primary_concept_title?: string;
};
type Sort = "recent" | "name" | "topic";

const PAGE = 40;
const SORT_KEY = "vinc.home.sort";

/** 목록 캐시. 문서를 열었다 돌아올 때마다 다시 읽지 않는다. 쓰기·지우기·갱신 신호가 비운다. */
let docsCache: Doc[] | null = null;
/** 거르는 주제(접두). 비면 전체. 여럿이면 합집합이다(Agent: 역할 주제들). 세션 안에서 남는다. */
let scope: string[] = [];
/** 지금 거르기가 Agent 가 건 역할 주제들인가. 그때 빈 목록은 거르기 풀기가 아니라 역할 만들기로 잇는다. */
let agentScope = false;
let query = "";
let shown = PAGE;
let sort: Sort = (() => {
  try {
    const v = localStorage.getItem(SORT_KEY);
    return v === "name" || v === "topic" ? v : "recent";
  } catch { return "recent"; }
})();

/** 마운트 세대 — 라우터가 표면을 갈아끼워도 컨테이너는 남으므로 늦게 온 응답을 버린다. */
let liveGen = 0;
let editor: DocEditorHandle | null = null;

/** 라우터가 표면을 떠날 때 부른다. 편집기는 라우터가 먼저 확정·해제한다(disposeEditorsIn). */
export function leaveHome(): void {
  ++liveGen;
  // Agents 보드의 갱신 구독은 여기서 풀지 않는다. 비동기로 풀면 곧이어 그린 새 보드의 구독이
  // 풀린다. 떠난 보드의 구독은 다음 신호 때 스스로 풀린다(agents-board.ts).
  editor = null;
  setLnbDocOpener(null);
  setLnbTopicPicker(null);
  setLnbScopeClearer(null);
}

const themeMode = (): ThemeMode =>
  document.documentElement.dataset.theme === "dark" ? "dark" : "light";
const inScope = (d: Doc, topics: string[]): boolean => !topics.length
  || topics.some((t) => !!d.topic && (d.topic === t || d.topic.startsWith(t + "/")));

async function loadDocs(): Promise<Doc[]> {
  if (docsCache) return docsCache;
  const r = await api.docList(cfg.owner);
  if (!r.executed) throw new Error("doc list failed");
  docsCache = (r.docs as Doc[]) || [];
  return docsCache;
}

export async function renderHome(el: HTMLElement): Promise<void> {
  leaveHome();
  const gen = ++liveGen;
  const live = () => gen === liveGen && el.isConnected;
  const parts = location.hash.replace(/^#/, "").split("/").map(decodeURIComponent);
  const mode = parts[1] === "doc" && parts[2] ? "doc" : parts[1] === "new" ? "new"
    : parts[1] === "agents" ? "agents" : "list";

  // 다른 화면(검색 결과 · 새 문서 단축키 · 묻기의 착지)이 이 면으로 오는 길. 그래프가 없는
  // 동안 View 훅을 이 면이 대신 든다 — 문서는 여기서 열고, 노드는 그래프로 보낸다.
  setViewHooks({
    share: () => void import("./settings").then((m) => m.openSettings("data", "share")),
    openNode: (id) => { location.hash = `#view/trace/${encodeURIComponent(id)}`; },
    openDoc: (docId) => { location.hash = `#home/doc/${encodeURIComponent(docId)}`; },
    newDoc: (topic) => openWriter(topic),
    context: () => ({ topic: scope.length === 1 ? scope[0] : "", nodeId: "" }),
  });
  setLnbDocOpener((docId) => { location.hash = `#home/doc/${encodeURIComponent(docId)}`; });
  // 트리 줄 끝의 거르기 버튼 · Agent 가 이 목록을 좁힌다(그래프에서는 같은 손잡이가 지도를 좁힌다).
  setLnbTopicPicker((path, add, set) => {
    const paths = Array.isArray(path) ? path : [path];
    const one = paths[0];
    agentScope = !!set;
    scope = set ? paths
      : add ? (scope.includes(one) ? scope.filter((t) => t !== one) : [...scope, one])
      : (scope.length === 1 && scope[0] === one ? [] : [one]);
    shown = PAGE;
    if (mode === "list") paintList(el, gen);
    // 보드에서 주제를 고르면 그 주제의 문서 목록으로 간다. Agent 가 거는 역할 주제 묶음(set)은
    // 보드가 이미 그 역할들을 보여 주므로 따르지 않는다.
    else if (!(mode === "agents" && set)) location.hash = "#home";
  });
  // 저절로 새로워지는 갱신(VIN-129). 쓰는 중에는 다시 그리지 않는다 — 목록만 새로 읽는다.
  setLiveRefresher(async () => {
    if (!live()) return;
    docsCache = null;
    if (mode === "list") await paintList(el, gen);
  });

  if (mode === "agents" && parts[2]) {
    // 역할 편집 면(`#home/agents/<id>` · `#home/agents/new[/<팀>]`). 보드 안의 한 면이다.
    const { renderRoleEditor } = await import("../role-composer");
    await renderRoleEditor(el, live, parts.slice(2));
  } else if (mode === "agents") {
    const { renderAgentsBoard } = await import("./agents-board");
    await renderAgentsBoard(el, live);
  } else if (mode === "doc") await paintDoc(el, gen, parts[2]);
  else if (mode === "new") await paintWriter(el, gen, scope.length === 1 ? scope[0] : "");
  else await paintList(el, gen);
}

/** 새 문서 면을 연다. 이미 홈이면 주소만 바꾼다(라우터가 다시 그린다). */
let pendingTopic = "";
function openWriter(topic = ""): void {
  pendingTopic = topic;
  if (location.hash === "#home/new") window.dispatchEvent(new HashChangeEvent("hashchange"));
  else location.hash = "#home/new";
}

// ── 목록 ────────────────────────────────────────────────────────────────────────
async function paintList(el: HTMLElement, gen: number): Promise<void> {
  const H = COPY.ui.home;
  if (!el.querySelector(".home")) {
    el.innerHTML = `<div class="home"><div class="home-read">${loaderRows(COPY.ui.common.loading, { count: 6 })}</div></div>`;
  }
  let docs: Doc[];
  try { docs = await loadDocs(); } catch {
    if (gen !== liveGen) return;
    el.innerHTML = `<div class="home"><p class="hint">${esc(H.loadFailed)}</p>
      <button class="btn btn-quiet" type="button" id="home-retry">${esc(H.retry)}</button></div>`;
    el.querySelector("#home-retry")?.addEventListener("click", () => { docsCache = null; void paintList(el, gen); });
    return;
  }
  if (gen !== liveGen) return;
  setLnbActive({ domains: scope });
  // 뷰 머리 앞자리 — 목록에서는 자리 이름 하나다(주제 경로는 아래 주제 줄이 갖는다).
  setTopbarLead(`<span class="tb-lead-name">${esc(H.back)}</span>`);
  const tints = tintMap(docs.map((d) => d.topic || ""), themeMode());
  const q = query.trim().toLowerCase();
  const picked = docs.filter((d) => inScope(d, scope)
    && (!q || docTitle(d.title).toLowerCase().includes(q)));
  const byName = (a: Doc, b: Doc) => docTitle(a.title).localeCompare(docTitle(b.title));
  const byRecent = (a: Doc, b: Doc) => (b.updated_at || "").localeCompare(a.updated_at || "") || byName(a, b);
  picked.sort(sort === "name" ? byName
    : sort === "topic" ? (a, b) => {
      // 주제 없는 문서는 끝으로 간다. 같은 주제 안에서는 최근 수정순이다.
      const ta = a.topic || "￿", tb = b.topic || "￿";
      return ta.localeCompare(tb) || byRecent(a, b);
    } : byRecent);
  const page = picked.slice(0, shown);

  const row = (d: Doc) => {
    const t = d.topic || "";
    const tint = tints?.get(t);
    const day = dayOf(d.updated_at || "");
    return `<a class="home-row" href="#home/doc/${encodeURIComponent(d.doc_id)}">
      <span class="home-dot" aria-hidden="true"${tint ? ` style="--tint:${tint}"` : ""}></span>
      <span class="home-rtitle">${esc(docTitle(d.title))}</span>
      <span class="home-rmeta">${esc(t || H.noTopic)}${day ? ` · ${esc(H.updated(day))}` : ""}</span>
    </a>`;
  };
  let rows = "";
  if (sort === "topic") {
    let cur = "\u0000";
    for (const d of page) {
      const t = d.topic || "";
      if (t !== cur) { cur = t; rows += `<p class="home-group">${esc(t || H.noTopic)}</p>`; }
      rows += row(d);
    }
  } else rows = page.map(row).join("");

  const roles = agentRoleCount() || 0;
  // 커뮤니티 스페이스는 읽기 전용이다. 쓰기 진입점을 그리지 않는다. 계정도 쓰기를 거절한다.
  const readOnly = isCommunitySpace(cfg.team);
  // Agent 가 건 거르기에서 빈 목록은 문서 전체가 비었든 아니든 역할 만들기로 잇는다.
  const empty = !picked.length && agentScope && !query
    ? `<div class="home-empty"><p>${esc(roles ? H.agentNoDocs : H.agentNone)}</p>
        <div class="home-acts"><button class="btn btn-primary" type="button" data-act="role">${esc(COPY.author.role.title)}</button>
        ${roles ? `<button class="btn" type="button" data-act="new">${esc(H.newDoc)}</button>` : ""}</div></div>`
    : !docs.length
      ? `<div class="home-empty"><p>${esc(H.empty)}</p>
          ${readOnly ? "" : `<div class="home-acts"><button class="btn btn-primary" type="button" data-act="new">${esc(H.newDoc)}</button>
          <button class="btn" type="button" data-act="import">${esc(H.import)}</button></div>`}</div>`
    : !picked.length
      ? `<div class="home-empty"><p>${esc(H.emptyFiltered)}</p>
          <button class="btn btn-quiet" type="button" data-act="clear">${esc(H.clearFilter)}</button></div>`
      : "";
  const more = picked.length > shown
    ? `<button class="btn btn-quiet home-more" type="button" data-act="more">${
      esc(H.more(Math.min(PAGE, picked.length - shown)))}</button>` : "";

  el.innerHTML = `<div class="home">
    <header class="home-head">
      <h1>${esc(H.title)} <span class="home-count">${esc(H.count(picked.length))}</span></h1>
      <div class="home-acts">
        ${readOnly ? "" : `<button class="btn btn-primary" type="button" data-act="new">${icon("doc", 15)}<span>${esc(H.newDoc)}</span></button>
        <button class="btn" type="button" data-act="import">${icon("pack-in", 15)}<span>${esc(H.import)}</span></button>`}
      </div>
    </header>
    ${topicPanel(docs)}
    <div class="home-toolbar">
      <input class="home-filter" id="home-q" type="search" autocomplete="off" spellcheck="false"
             placeholder="${esc(H.filterPh)}" aria-label="${esc(H.filterPh)}" value="${esc(query)}"/>
      <label class="home-sort"><span class="hint">${esc(H.sortLabel)}</span>
        <select id="home-sort">${(["recent", "name", "topic"] as Sort[]).map((k) =>
          `<option value="${k}"${k === sort ? " selected" : ""}>${esc(H.sort[k])}</option>`).join("")}</select>
      </label>
    </div>
    <div class="home-list" role="list">${rows}</div>
    ${empty}${more}
  </div>`;

  const qEl = el.querySelector<HTMLInputElement>("#home-q")!;
  qEl.addEventListener("input", () => {
    query = qEl.value; shown = PAGE;
    const at = qEl.selectionStart;
    void paintList(el, gen).then(() => {
      // 다시 그리면 입력이 새 요소가 된다 — 초점과 커서를 되돌린다.
      const next = el.querySelector<HTMLInputElement>("#home-q");
      if (next) { next.focus(); if (at !== null) next.setSelectionRange(at, at); }
    });
  });
  el.querySelector<HTMLSelectElement>("#home-sort")!.addEventListener("change", (e) => {
    sort = (e.target as HTMLSelectElement).value as Sort;
    try { localStorage.setItem(SORT_KEY, sort); } catch { /* 저장 못 해도 이번 화면은 바뀐다 */ }
    void paintList(el, gen);
  });
  el.querySelectorAll<HTMLElement>("[data-act]").forEach((b) => b.addEventListener("click", () => {
    const act = b.dataset.act;
    if (act === "new") openWriter(scope.length === 1 ? scope[0] : "");
    else if (act === "import") void import("./settings").then((m) => m.openSettings("data", "import"));
    else if (act === "role") void import("../role-composer").then((m) => m.openRoleComposer());
    else if (act === "clear") { scope = []; agentScope = false; query = ""; shown = PAGE; void paintList(el, gen); }
    else if (act === "more") { shown += PAGE; void paintList(el, gen); }
  }));
  el.querySelectorAll<HTMLElement>("[data-topic]").forEach((b) => b.addEventListener("click", () => {
    const t = b.dataset.topic || "";
    scope = t ? [t] : [];
    agentScope = false;
    shown = PAGE;
    void paintList(el, gen);
  }));
}

/** 주제 칸 — 빵부스러기(모든 주제 › vinc › design)와 지금 자리의 하위 주제 칩. 칩을 누르면 한
 *  단 안으로 들어가 목록이 그 주제로 좁혀진다. 여럿을 걸어 둔 상태(Agent)는 칩 대신 그 사실과
 *  푸는 버튼을 말한다. */
function topicPanel(docs: Doc[]): string {
  const H = COPY.ui.home;
  if (scope.length > 1) {
    return `<div class="home-topics"><span class="hint">${esc(H.scopeNote(scope.length))}</span>
      <button class="btn btn-quiet" type="button" data-topic="">${esc(H.clearFilter)}</button></div>`;
  }
  const here = scope[0] || "";
  const segs = here ? here.split("/") : [];
  const crumb = [`<button class="home-crumb${here ? "" : " on"}" type="button" data-topic="">${esc(H.allTopics)}</button>`]
    .concat(segs.map((s, i) => {
      const p = segs.slice(0, i + 1).join("/");
      return `<span class="home-sep" aria-hidden="true">›</span><button class="home-crumb${
        p === here ? " on" : ""}" type="button" data-topic="${esc(p)}">${esc(s)}</button>`;
    })).join("");
  // 지금 자리의 바로 아래 주제와 그 아래 문서 수.
  const kidsOf = (at: string) => {
    const kids = new Map<string, number>();
    for (const d of docs) {
      const t = d.topic || "";
      if (!t || (at && !(t === at || t.startsWith(at + "/")))) continue;
      const rest = at ? t.slice(at.length + 1) : t;
      if (!rest) continue;
      const child = (at ? at + "/" : "") + rest.split("/")[0];
      kids.set(child, (kids.get(child) || 0) + 1);
    }
    return kids;
  };
  // 하위가 하나뿐인 단은 건너뛴다(계정 그래프의 드릴다운과 같은 규칙) — 칩 하나짜리 줄은
  // 고를 것이 없다. 그 단에 문서가 직접 걸려 있어도 칩은 아래 단을 보인다.
  let at = here, kids = kidsOf(at);
  while (kids.size === 1) {
    const [only] = kids.keys();
    const next = kidsOf(only);
    if (!next.size) break;
    at = only; kids = next;
  }
  const chips = [...kids].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([p, n]) => `<button class="home-chip" type="button" data-topic="${esc(p)}">${
      esc(p.split("/").at(-1) || p)}<span class="home-chip-n">${n}</span></button>`).join("");
  return `<div class="home-topics"><nav class="home-crumbs" aria-label="${esc(H.metaTopic)}">${crumb}</nav>${
    chips ? `<div class="home-chips">${chips}</div>` : ""}</div>`;
}

// ── 문서 면 ────────────────────────────────────────────────────────────────────
/** 원문의 절(제목 1~3). 코드펜스 안과 프론트매터는 건너뛴다 — 거기 든 `#` 는 제목이 아니다.
 *  서버 목차(`/doc/context` sections)를 쓰지 않는 이유: 그것은 저장 시점의 것이라 쓰는 동안
 *  어긋난다. 이 칸은 지금 화면의 원문에서 다시 센다. */
type Sec = { title: string; level: number; at: number };
function sectionsOf(text: string): Sec[] {
  const out: Sec[] = [];
  const lines = text.split("\n");
  let pos = 0, fence = false, fm = lines[0]?.trim() === "---";
  lines.forEach((l, i) => {
    if (fm) { if (i > 0 && l.trim() === "---") fm = false; }
    else if (/^\s*(```|~~~)/.test(l)) fence = !fence;
    else if (!fence) {
      const m = /^(#{1,3})\s+(.+?)\s*#*\s*$/.exec(l);
      if (m) out.push({ level: m[1].length, title: m[2].replace(/[*_`]|\[\[|\]\]/g, ""), at: pos });
    }
    pos += l.length + 1;
  });
  return out;
}

async function paintDoc(el: HTMLElement, gen: number, docId: string): Promise<void> {
  const H = COPY.ui.home;
  // 목록을 못 읽어도 문서는 연다 — 옆 칸이 비어 있을 뿐이다.
  const meta = await loadDocs().then((ds) => ds.find((d) => d.doc_id === docId)).catch(() => undefined);
  if (gen !== liveGen) return;
  const topic = meta?.topic || "";
  setLnbActive({ domains: scope, docId, reveal: topic });
  const day = dayOf(meta?.updated_at || "");
  const node = meta?.primary_concept_id || "";
  // 경로는 뷰 머리 앞자리에 선다 — 본문 첫 글자와 같은 세로선이다(머리와 본문이 한 칼럼).
  // 옆 칸에서 주제를 바꾸면 다시 그린다.
  const paintCrumbs = (tp: string) => {
    const parts = tp ? tp.split("/") : [];
    const lead = setTopbarLead(`<nav class="home-crumbs" aria-label="${esc(H.metaTopic)}">
        <a class="home-back" href="#home">‹ ${esc(H.back)}</a>
        ${parts.map((s, i) => `<span class="home-sep" aria-hidden="true">›</span><button class="home-crumb" type="button"
          data-topic="${esc(parts.slice(0, i + 1).join("/"))}">${esc(s)}</button>`).join("")}
      </nav>`);
    lead?.querySelectorAll<HTMLElement>("[data-topic]").forEach((b) => b.addEventListener("click", () => {
      scope = b.dataset.topic ? [b.dataset.topic] : [];
      shown = PAGE;
      location.hash = "#home";
    }));
  };
  paintCrumbs(topic);
  el.innerHTML = `<div class="home home-read">
    <div class="home-read-grid">
      <article class="home-doc" id="home-ed"></article>
      <aside class="home-aside">
        <div class="ha-acts">
          ${node ? `<button class="btn ha-btn" type="button" data-act="graph">${icon("graph", 15)}<span>${esc(H.showInGraph)}</span></button>` : ""}
          <button class="btn btn-quiet ha-btn" type="button" data-act="copy">${esc(H.copyText)}</button>
        </div>
        <section class="ha-sec" id="ha-toc" hidden>
          <h2 class="ha-h" id="ha-toc-h"></h2>
          <ol class="ha-toc"></ol>
        </section>
        <section class="ha-sec" id="ha-concepts" hidden>
          <h2 class="ha-h">${esc(COPY.ui.viewx.readerConcepts)}</h2>
          <div class="ha-chips"></div>
        </section>
        <dl class="ha-meta">
          <dt>${esc(H.metaTopic)}</dt><dd id="ha-topic"></dd>
          ${day ? `<dt>${esc(H.metaUpdated)}</dt><dd>${esc(day)}</dd>` : ""}
          <dt>${esc(H.metaConcept)}</dt><dd>${node
            ? esc(meta?.primary_concept_title || node) : `<span class="hint">${esc(H.notInGraph)}</span>`}</dd>
        </dl>
        ${isCommunitySpace(cfg.team) ? "" : `<button class="ha-del" type="button" data-act="del">${esc(H.delDoc)}</button>`}
      </aside>
    </div>
  </div>`;
  const { mountDocEditor } = await import("../doc-editor");
  if (gen !== liveGen) return;
  let tocTimer = 0;
  const handle = await mountDocEditor(el.querySelector<HTMLElement>("#home-ed")!, {
    owner: cfg.owner, docId, autoFocus: false, readOnly: isCommunitySpace(cfg.team),
    onSaved: () => { docsCache = null; void refreshLnb({ quiet: true }); },
    onDocLink: (id) => { location.hash = `#home/doc/${encodeURIComponent(id)}`; },
    onText: (text) => {
      window.clearTimeout(tocTimer);
      tocTimer = window.setTimeout(() => { if (gen === liveGen) drawToc(text); }, 400);
    },
  });
  if (gen !== liveGen) { void handle.dispose(); return; }
  editor = handle;

  // ── 옆 칸: 목차 — 지금 원문에서 세고, 지금 읽는 절을 표시한다 ──────────────────
  let secs: Sec[] = [];
  const tocBox = el.querySelector<HTMLElement>("#ha-toc")!;
  const tocList = tocBox.querySelector<HTMLElement>(".ha-toc")!;
  function drawToc(text: string) {
    secs = sectionsOf(text);
    // 절이 하나면 목차가 길잡이 노릇을 못 한다.
    tocBox.hidden = secs.length < 2;
    if (tocBox.hidden) return;
    const top = Math.min(...secs.map((x) => x.level));
    tocBox.querySelector("#ha-toc-h")!.textContent = COPY.ui.viewx.readerOutline(secs.length);
    tocList.innerHTML = secs.map((x, i) => `<li><button type="button" class="ha-toc-i" data-i="${i}"
      style="--lv:${x.level - top}">${esc(x.title)}</button></li>`).join("");
    tocList.querySelectorAll<HTMLElement>(".ha-toc-i").forEach((b) => b.addEventListener("click", () => {
      const x = secs[Number(b.dataset.i)];
      if (x) handle.scrollToOffset(x.at, "start");
    }));
    markActive();
  }
  // 지금 읽는 절 = 화면 위쪽 띠(바 아래)를 이미 지난 마지막 제목. 끝까지 굴려 더 내려갈 수
  // 없으면 화면에 보이는 마지막 제목이다 — 짧은 끝 절은 위 띠에 닿지 못해 영영 안 켜졌다.
  let raf = 0;
  const scroller = (() => {
    for (let n: HTMLElement | null = el; n; n = n.parentElement) {
      const o = getComputedStyle(n).overflowY;
      if (o === "auto" || o === "scroll") return n;
    }
    return document.scrollingElement as HTMLElement | null;
  })();
  function markActive() {
    raf = 0;
    if (!secs.length || !handle.lineTop) return;
    const line = 150;
    const atEnd = !!scroller && scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 4;
    let on = 0;
    secs.forEach((x, i) => {
      const top = handle.lineTop!(x.at);
      if (top <= line || (atEnd && top < window.innerHeight - 40)) on = i;
    });
    tocList.querySelectorAll<HTMLElement>(".ha-toc-i").forEach((b, i) => {
      b.classList.toggle("on", i === on);
      if (i === on) b.setAttribute("aria-current", "location"); else b.removeAttribute("aria-current");
    });
  }
  const onScroll = () => {
    if (gen !== liveGen) { document.removeEventListener("scroll", onScroll, true); return; }
    if (!raf) raf = requestAnimationFrame(markActive);
  };
  document.addEventListener("scroll", onScroll, { capture: true, passive: true });
  drawToc(handle.getText());

  // ── 옆 칸: 이 문서의 개념 — 누르면 본문에서 나오는 자리에 밑줄을 긋는다 ─────────────
  void api.docContext(cfg.owner, docId).then((ctx) => {
    if (gen !== liveGen || !ctx?.executed || !ctx.concepts?.length) return;
    const box = el.querySelector<HTMLElement>("#ha-concepts")!;
    const chips = box.querySelector<HTMLElement>(".ha-chips")!;
    const list = ctx.concepts.slice().sort((a, b) => b.count - a.count).slice(0, 16);
    chips.innerHTML = list.map((c) => `<button type="button" class="ha-chip" data-cid="${esc(c.id)}"
      data-tip="${esc(H.conceptMark)}">${esc(c.title)}<span class="ha-chip-n">${c.count}</span></button>`).join("");
    box.hidden = false;
    chips.querySelectorAll<HTMLElement>(".ha-chip").forEach((b) => b.addEventListener("click", async () => {
      const on = !b.classList.contains("on");
      chips.querySelectorAll(".ha-chip.on").forEach((x) => x.classList.remove("on"));
      if (!on) { handle.highlight?.([]); return; }
      b.classList.add("on");
      const r = await api.docHighlights(cfg.owner, docId, b.dataset.cid!).catch(() => null);
      if (gen !== liveGen || !b.classList.contains("on")) return;
      handle.highlight?.(r?.executed ? r.ranges ?? [] : []);
    }));
  }).catch(() => { /* 옆 칸 한 줄이 빠질 뿐이다 — 문서는 그대로 열려 있다 */ });
  el.querySelector('[data-act="graph"]')?.addEventListener("click", () => {
    location.hash = `#view/read/${encodeURIComponent(node)}/${encodeURIComponent(docId)}`;
  });
  void wireTopic();

  // ── 옆 칸: 주제 — 바꾸기는 편집기의 저장과 같은 길이다(원격 판 확인 · 같은 충돌 상자).
  // 본문이 그대로면 서버는 청크를 건드리지 않고 미러 노드의 주제만 적는다. 주제는 비울 수 없다
  // (서버가 빈 값을 "안 건드림"으로 읽는다). 쓸 수 없는 스페이스에서는 바꾸기 단추를 내지 않는다.
  async function wireTopic(): Promise<void> {
    let curTopic = topic;
    const dd = el.querySelector<HTMLElement>("#ha-topic");
    if (!dd) return;
    const paint = (writable: boolean) => {
      dd.innerHTML = `<span>${curTopic
        // 좁은 칸에서 경로가 낱말 한가운데서 꺾이지 않게, 꺾을 자리를 `/` 뒤에 준다.
        ? esc(curTopic).replace(/\//g, "/<wbr>") : esc(H.noTopic)}</span>${writable
        ? ` <button class="ha-link" type="button" data-topic-edit>${esc(H.topicChange)}</button>` : ""}`;
      dd.querySelector("[data-topic-edit]")?.addEventListener("click", () => edit());
    };
    paint(false);
    const writable = !isCommunitySpace(cfg.team) && await canWriteHere();
    if (gen !== liveGen) return;
    paint(writable);
    function edit(): void {
      dd!.innerHTML = `<form class="ha-topic-form">
          <input class="home-filter ha-topic-in" list="ha-topics" value="${esc(curTopic)}"
            aria-label="${esc(H.metaTopic)}" autocomplete="off" spellcheck="false"/>
          <datalist id="ha-topics"></datalist>
          <div class="ha-topic-acts">
            <button class="btn btn-quiet" type="button" data-topic-cancel>${esc(COPY.ui.common.cancel)}</button>
            <button class="btn btn-primary" type="submit">${esc(H.topicSave)}</button>
          </div>
          <p class="hint ha-topic-msg" role="status"></p>
        </form>`;
      const form = dd!.querySelector<HTMLFormElement>("form")!;
      const input = form.querySelector<HTMLInputElement>("input")!;
      const msg = form.querySelector<HTMLElement>(".ha-topic-msg")!;
      const busy = (on: boolean) => form.querySelectorAll<HTMLInputElement | HTMLButtonElement>("input, button")
        .forEach((x) => { x.disabled = on; });
      void import("../authoring").then((m) => m.fillTopicList(form.querySelector<HTMLDataListElement>("datalist")!));
      input.focus();
      input.select();
      form.querySelector("[data-topic-cancel]")!.addEventListener("click", () => paint(true));
      form.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); paint(true); } });
      form.addEventListener("submit", (e) => void (async () => {
        e.preventDefault();
        const next = input.value.trim().replace(/^\/+|\/+$/g, "");
        if (!next) { msg.textContent = H.topicNeed; input.focus(); return; }
        if (next === curTopic) { paint(true); return; }
        busy(true);
        msg.textContent = H.topicSaving;
        const ok = await (handle.saveTopic?.(next) ?? Promise.resolve(false)).catch(() => false);
        if (gen !== liveGen) return;
        if (!ok) { busy(false); msg.textContent = H.topicFail; return; }
        curTopic = next;
        docsCache = null;
        void import("../authoring").then((m) => m.forgetTopics());
        void refreshLnb({ quiet: true });
        setLnbActive({ domains: scope, docId, reveal: curTopic });
        paintCrumbs(curTopic);
        paint(true);
      })());
    }
  }
  el.querySelector<HTMLButtonElement>('[data-act="copy"]')?.addEventListener("click", async (e) => {
    const b = e.currentTarget as HTMLButtonElement;
    try {
      await navigator.clipboard.writeText(handle.getText());
      b.textContent = H.copied;
      window.setTimeout(() => { if (b.isConnected) b.textContent = H.copyText; }, 1600);
    } catch { /* 클립보드를 못 쓰는 자리다 — 본문을 직접 긁을 수 있다 */ }
  });
  el.querySelector<HTMLButtonElement>('[data-act="del"]')?.addEventListener("click", async (e) => {
    const b = e.currentTarget as HTMLButtonElement;
    const V = COPY.ui.viewx;
    const title = docTitle(el.querySelector<HTMLInputElement>("#ed-title")?.value || meta?.title);
    if (!await confirmDestructive(V.delDocAsk(title), V.delConfirm, V.delCancel)) return;
    if (gen !== liveGen) return;
    b.disabled = true;
    // 지우는 동안 늦은 자동저장이 문서를 되살리지 않게 막는다(openDocSheet 와 같은 순서).
    await handle.pauseSaving(true);
    const { deleteDocument } = await import("../doc-delete");
    const outcome = await deleteDocument(cfg.owner, docId, { team: cfg.team || null });
    docsCache = null;
    void refreshLnb({ quiet: true });
    if (outcome === "deleted") {
      await handle.dispose(true);
      if (gen === liveGen) location.hash = "#home";
      return;
    }
    b.disabled = false;
    b.textContent = outcome === "present" ? V.delDocPresent : V.delDocUnknown;
    if (outcome === "present") await handle.pauseSaving(false);
  });
}

// ── 새 문서 ────────────────────────────────────────────────────────────────────
async function paintWriter(el: HTMLElement, gen: number, fallbackTopic: string): Promise<void> {
  const H = COPY.ui.home;
  const topic = pendingTopic || fallbackTopic;
  pendingTopic = "";
  if (isCommunitySpace(cfg.team)) { location.hash = "#home"; return; }
  setTopbarLead(`<nav class="home-crumbs"><a class="home-back" href="#home">‹ ${esc(H.back)}</a></nav>`);
  el.innerHTML = `<div class="home home-read">
    <div class="home-read-grid home-write"><article class="home-doc" id="home-wr"></article></div>
  </div>`;
  const { mountDocWriter } = await import("../authoring");
  if (gen !== liveGen) return;
  const handle = await mountDocWriter(el.querySelector<HTMLElement>("#home-wr")!, {
    topic,
    onCreated: (docId) => {
      docsCache = null;
      void refreshLnb({ quiet: true });
      // 주소만 문서의 것으로 바꾼다 — 다시 그리면 쓰던 편집기가 닫힌다(hashchange 없음).
      history.replaceState(null, "", `#home/doc/${encodeURIComponent(docId)}`);
    },
    onTopicChanged: () => { docsCache = null; void refreshLnb({ quiet: true }); },
  });
  if (gen !== liveGen) { void handle.dispose(); return; }
  editor = handle;
}

/** 지금 홈에 편집기가 떠 있는가 — 표면 단축키가 쉬어야 하는 조건(main.ts). */
export function homeEditorOpen(): boolean {
  return !!editor && !!document.querySelector("#surface .home .cm-content");
}
