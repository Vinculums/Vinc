import { api, cfg } from "../api";
import { COPY } from "../copy";
import { docTitle } from "../doc-title";
import { emptyInviteHtml, wireEmptyInvite } from "../empty-invite";
import { stateDot } from "../icons";
import { openDocSheet } from "../doc-editor";
import { viewHooks } from "../topbar";
import { esc } from "../components";
import { dayOf } from "../view-prefs";
import { loaderRows } from "../vinc-loaders.js";

/** 모든 문서 — **주제 → 문서** 2단(D66).
 *
 *  구 목록은 91건을 한 줄씩 평평하게 쏟아 냈다. 앱의 다른 모든 곳(L0 주제 표,
 *  Sankey 가운데 열, 크럼)이 "주제 먼저, 그 다음 알갱이"인데 여기만 평면이라
 *  같은 데이터가 화면마다 다른 구조로 보였다. 여기도 같은 깊이를 준다.
 *
 *  ⚠ 문서에는 주제 열이 없다 — 주제는 **문서가 언급하는 개념**에서 유도된다
 *  (docstore.list_docs, `MENTIONS→Concept.domain`). 링크가 아직 없는 문서는
 *  주제가 없고, 억지로 배정하지 않고 "주제 없음" 묶음에 둔다.
 *
 *  검색은 이 모달 안에 산다: 목록을 열어 놓고 좁히는 일이라 상단 바 검색
 *  (지도로 착지하는 것)과 목적이 다르다. 여기 입력은 **이 목록만** 좁힌다.
 *
 *  `새 문서` 는 여기 없다 — 문서를 만드는 일은 "들여오기"(가져오기 시트)에 모았다.
 *  목록은 있는 것을 보는 자리고, 만드는 자리가 아니다. */

type Doc = { doc_id: string; title: string; updated_at?: string;
             topic?: string; topics?: string[]; primary_concept_id?: string };

/** 목록을 감싼 시트가 스스로를 닫을 수 있게 넘겨받는다(D70) — 문서를 눌러 지도로
 *  날아갔는데 목록이 그 위에 그대로 떠 있으면, 방금 간 곳이 가려진다. */
type BrowseOpts = { close?: () => void };

/** 문서 행의 메타 한 줄.
 *
 *  ⚠ `origin` 은 **보여주지 않는다.** 그건 "어디서 왔는지"의 표시일 뿐 조인 키가
 *  아니고(D45 — Chunk 가 정본), 팩으로 받은 문서에서는 **보낸 사람 기계의 경로**다
 *  (`WORKSPACES/learning/physics/...`). 내 컴퓨터에 있지도 않은 경로를 파일 위치처럼
 *  내보이면 "원본이 있어야 열리나?" 라는 오해를 부른다 — 실제로는 원본 없이 복원된다.
 *  ISO 타임스탬프(`2026-07-26T05:08:41.939Z`)도 사람이 읽는 형식이 아니다. 날짜만. */
function docMeta(d: Doc): string {
  const day = dayOf(d.updated_at || "");
  return day ? COPY.records.edited(day) : "";
}

export async function renderBrowse(body: HTMLElement, opts: BrowseOpts = {}) {
  body.innerHTML = loaderRows(COPY.ui.common.loading, { count: 6 });
  let docs: Doc[] = [];
  let topic: string | null = null;   // null = 주제 층, 문자열 = 그 주제의 문서 층
  let q = "";

  const NO_TOPIC = "\u0000";   // "주제 없음" 묶음의 내부 키(실 주제명과 충돌 불가)

  const load = async () => {
    const d = await api.docList(cfg.owner);
    docs = d.executed ? (d.docs as Doc[]) || [] : [];
  };

  /** 질의로 좁힌 집합. 주제 층에서도 검색이 먹어야 한다 — 찾는 문서의 주제를
   *  모르는 채로 여는 것이 목록을 여는 흔한 이유다. */
  const filtered = () => {
    const t = q.trim().toLowerCase();
    if (!t) return docs;
    return docs.filter((d) => docTitle(d.title).toLowerCase().includes(t));
  };

  const groups = (list: Doc[]) => {
    const m = new Map<string, Doc[]>();
    for (const d of list) {
      const k = d.topic || NO_TOPIC;
      (m.get(k) || m.set(k, []).get(k)!).push(d);
    }
    // 많은 순 · 동률이면 이름순. "주제 없음"은 언제나 마지막(잔여물이지 주제가 아니다).
    return [...m.entries()].sort((a, b) =>
      (a[0] === NO_TOPIC ? 1 : 0) - (b[0] === NO_TOPIC ? 1 : 0)
      || b[1].length - a[1].length
      || a[0].localeCompare(b[0]));
  };

  const head = () => `
    <div class="dl-head">
      <input id="dl-q" type="search" autocomplete="off" spellcheck="false"
             placeholder="${esc(COPY.records.filterPh)}" value="${esc(q)}" />
      <span class="hint" id="dl-count"></span>
    </div>`;

  function draw() {
    const list = filtered();
    if (docs.length === 0) {
      // 문서가 0 인 것은 오류가 아니라 초대다 — 끌어다 놓기·파일 고르기·노드 만들기.
      body.innerHTML = emptyInviteHtml(COPY.records.idle);
      wireEmptyInvite(body, () => void load().then(draw));
      return;
    }
    // ── 문서 층(주제 하나 안) ────────────────────────────────────────────────
    if (topic !== null) {
      const inTopic = list.filter((d) => (d.topic || NO_TOPIC) === topic);
      const name = topic === NO_TOPIC ? COPY.records.noTopic : topic;
      body.innerHTML = head() + `
        <button class="dl-back" id="dl-back">‹ ${esc(COPY.records.allTopics)}</button>
        <p class="hint dl-sec">${esc(name)} · ${inTopic.length}</p>
        ${inTopic.map(row).join("") || `<p class="hint">${COPY.records.noMatch}</p>`}`;
      body.querySelector("#dl-back")!.addEventListener("click", () => { topic = null; draw(); });
    } else {
      // ── 주제 층 ───────────────────────────────────────────────────────────
      const gs = groups(list);
      body.innerHTML = head() + (gs.length === 0
        ? `<p class="hint">${COPY.records.noMatch}</p>`
        : gs.map(([k, ds]) => `
          <div class="list-row dl-topic" data-topic="${esc(k)}" role="button" tabindex="0">
            <span class="title">${esc(k === NO_TOPIC ? COPY.records.noTopic : k)}</span>
            <span class="meta">${COPY.records.docsN(ds.length)}</span>
          </div>`).join(""));
      body.querySelectorAll<HTMLElement>(".dl-topic").forEach((r) => {
        const go = () => { topic = r.dataset.topic!; draw(); };
        r.addEventListener("click", go);
        r.addEventListener("keydown", (e) => {
          const k = (e as KeyboardEvent).key;
          if (k === "Enter" || k === " ") { e.preventDefault(); go(); }
        });
      });
    }
    const c = body.querySelector<HTMLElement>("#dl-count");
    if (c) c.textContent = COPY.records.docsN(list.length);
    wireQuery();
    // 문서 클릭 = **지도의 그 자리로 간다**(D70). 여태는 편집기를 열어, 목록에서
    // 고른 문서가 지도 어디에 사는지는 끝내 알 수 없었다. 착지 노드가 없는 문서
    // (개념 언급 0 — 지도에 자리가 없다)만 종전대로 편집기로 연다.
    body.querySelectorAll<HTMLElement>(".doc-row").forEach((r) =>
      r.addEventListener("click", () => {
        const node = r.dataset.node || "";
        if (!node) {
          void openDocSheet({ owner: cfg.owner, docId: r.dataset.doc,
                              onSaved: () => void load().then(draw) });
          return;
        }
        opts.close?.();
        const h = viewHooks();
        // 지도로 가고 **문서도 연다**. 여태 노드로만 날아가서, 목록에서 고른 문서의
        // 본문을 보려면 그 노드의 발췌 카드가 마침 이 문서일 때만 한 번 더 누를 수
        // 있었다. 훅이 없으면(다른 표면) 종전대로 해시 착지로 강등한다.
        if (h) h.openDoc(r.dataset.doc || "", node);
        else location.hash = `#view/trace/${encodeURIComponent(node)}`;
      }));
  }

  const row = (d: Doc) => `
    <div class="list-row doc-row" data-doc="${esc(d.doc_id)}"
         data-node="${esc(d.primary_concept_id || "")}" style="cursor:pointer">
      ${stateDot("empty")}
      <span class="title">${esc(docTitle(d.title))}</span>
      <span class="meta">${esc(docMeta(d))}</span>
    </div>`;

  /** 입력 중 포커스·커서를 잃지 않게, 다시 그린 뒤 값을 복원하고 끝으로 커서를 둔다. */
  function wireQuery() {
    const input = body.querySelector<HTMLInputElement>("#dl-q");
    if (!input) return;
    input.addEventListener("input", () => {
      q = input.value;
      draw();
      const next = body.querySelector<HTMLInputElement>("#dl-q");
      if (next) { next.focus(); next.setSelectionRange(next.value.length, next.value.length); }
    });
  }

  // 목록을 못 읽으면 배너에 다시 시도 단추를 둔다. 누르면 읽기부터 다시 하고, 또 실패하면 같은 배너가 다시 선다.
  const start = async () => {
    try {
      await load();
      draw();
    } catch {
      body.innerHTML = `<div class="error-banner" role="alert">${COPY.records.error}
        <button class="btn btn-sm" type="button" data-dl-retry>${esc(COPY.ui.lnb.retry)}</button></div>`;
      body.querySelector<HTMLButtonElement>("[data-dl-retry]")?.addEventListener("click", () => {
        body.innerHTML = loaderRows(COPY.ui.common.loading, { count: 6 });
        void start();
      });
    }
  };
  await start();
}
