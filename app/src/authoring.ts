/** 작업하는 자리에서 만들기 — 흐름 여럿이 함께 쓰는 것만 여기 둔다.
 *
 *  id 짓기 · 관계 규칙 · 주제 목록 · 만들기 메뉴와 단축키(흐름 A) · 문서 열의 새 문서(흐름 B).
 *  컴포저(E)는 `node-composer.ts`, 잇기(F)는 `connect.ts`, 추출 목록(G)은 `extract-list.ts`,
 *  기본 정보 편집(H)은 `node-basics.ts` 에 있다.
 *
 *  규칙은 하나다: **조용히 짐작하지 않는다.** 쓰기는 무엇을 만들었고 무엇을 맞췄고 무엇을
 *  거절당했는지를 말하고, 이미 있는 id 에 조용히 합치지 않는다.
 *  근거: [[vinc.plan.view-card-timeline-period-authoring-2026-09-27]] Part 3
 *  (인용 대조표 `scripts/graph-refs.json` 이 이 문서를 아직 싣지 않아 이중대괄호를 뺐다). */
import { api, cfg, docMidWrite } from "./api";
import { COPY } from "./copy";
import { armsNotice, esc, openContextMenu } from "./components";
import { viewHooks } from "./topbar";
import type { DocEditorHandle } from "./doc-editor";

export type NodeKind = "concept" | "record" | "decision" | "episode";
export const NODE_KINDS: readonly NodeKind[] = ["concept", "record", "decision", "episode"];

/** 제목 → id 조각. **어떤 문자 체계든** 글자와 숫자는 남긴다(여태 `a-z0-9` 와 한글만 남겨
 *  일본어·키릴 제목은 빈 조각이 됐다). 기호뿐이면 빈 문자열 — 호출부가 시각 조각으로 메운다. */
export function slugOf(title: string): string {
  return title.normalize("NFKC").toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-+|-+$/g, "")
    .slice(0, 60).replace(/-+$/, "");
}

/** 조각이 빈 제목을 위한 자리. 한 번 정하면 그 화면에서는 같은 값을 쓴다(미리보기와 쓰기가 같아야 한다). */
export const stampSlug = (): string => `note-${Date.now().toString(36)}`;

/** id 접두로 종을 읽는다. 문서 미러(`concept:doc.<id>`)도 개념이다. 모르는 접두는 "other". */
export function kindOfId(id: string): NodeKind | "other" {
  const p = id.slice(0, id.indexOf(":"));
  return (NODE_KINDS as readonly string[]).includes(p) ? p as NodeKind : "other";
}

/** `YYYY` · `YYYY-MM` · `YYYY-MM-DD`. 서버(`_norm_content_time`)와 같은 꼴만 받는다. */
export const isWhen = (s: string): boolean => /^\d{4}(-\d{2}(-\d{2})?)?$/.test(s);

// ── 관계 — 사람에게 보이는 넷(Q3.5) ────────────────────────────────────────────
export const RELS = ["relates_to", "prerequisite_of", "derives_from", "inherits_from"] as const;
export type Rel = typeof RELS[number];
/** 관계 문장(`relSentence`)과 관계 목록(node-context)은 저장 이름으로 찾는다. */
const REL_STORED: Record<Rel, string> = {
  relates_to: "RELATED_TO", prerequisite_of: "PREREQUISITE_OF",
  derives_from: "DERIVES_FROM", inherits_from: "INHERITS_FROM",
};
export const relStored = (rel: Rel): string => REL_STORED[rel];

/** 이 쌍을 이을 수 있는가. 되면 null, 안 되면 **사람이 읽을 이유**.
 *  규칙은 쓰기 계약 그대로다(`api/fragment.py` `_ALLOWED_PAIRS_HINT`): relates_to 는
 *  개념→개념만, 나머지 셋은 개념·결정 사이, 기록→개념·결정은 넷 다. UI 가 먼저 막는 것은
 *  서버가 거절할 쌍을 보내 놓고 "0개 이었다"를 받지 않으려는 것이다. */
export function relRefusal(rel: Rel, src: string, dst: string): string | null {
  const W = COPY.author.relWhy;
  const s = kindOfId(src), d = kindOfId(dst);
  if (src === dst) return W.same;
  if (s === "other" || d === "other") return W.other;
  if (s === "episode" || d === "episode") return W.episode;
  if (d === "record") return W.recordTarget;
  if (s === "record") return null;                      // 기록 → 개념·결정: 넷 다
  if (rel === "relates_to") return s === "concept" && d === "concept" ? null : W.relatesConcept;
  return null;                                          // 개념·결정 사이
}

/** "A 가 B 보다 먼저 와요" — 관계 상세가 쓰는 문장과 같은 것. */
export function relSentence(rel: Rel, a: string, b: string): string {
  const f = COPY.ui.viewx.relSentence[REL_STORED[rel]] || COPY.ui.viewx.relSentence.RELATED_TO;
  return f(a, b);
}

/** 서버가 엣지를 건너뛴 이유. 경고는 영어 산문이라 화면에 그대로 싣지 않는다 —
 *  쌍 거절 토큰(`edge_disallowed_pair`)만 알아듣고 나머지는 일반 문구로 말한다. */
export function edgeRefusal(warnings: unknown): string {
  const w = Array.isArray(warnings) ? warnings.map(String) : [];
  return w.some((x) => x.includes("edge_disallowed_pair"))
    ? COPY.author.serverRefused : COPY.author.createFail;
}

// ── 노드 찾기 — `[[` 목록 · 컴포저의 연결 대상 · 잇기 띠가 함께 쓴다 ──────────────
export type NodeHit = { id: string; title: string; domain: string };
/** 이름으로 노드를 찾는다. 의미 팔이 빠진 검색이면 그 사실(`note`)을 함께 준다 — 반쪽
 *  결과를 온전한 것처럼 내밀지 않는다(검색 표면과 같은 규율, `components.armsNotice`). */
export async function findNodes(q: string): Promise<{ hits: NodeHit[]; note: string }> {
  const r: any = await api.search(q, 12).catch(() => null);
  const hits = (r?.node_hits || []).slice(0, 6).map((n: any) => ({
    id: String(n.id || ""), title: String(n.title || n.id || ""), domain: String(n.domain || ""),
  })).filter((h: NodeHit) => h.id);
  return { hits, note: armsNotice(r)?.text || "" };
}
/** 찾은 노드 목록의 마크업(연결 대상·잇기 띠). 행은 `data-id`·`data-title` 을 싣는다. */
export function nodeHitsHtml(hits: NodeHit[], note: string): string {
  return (note ? `<p class="cm-assist-note">${esc(note)}</p>` : "")
    + (hits.length ? hits.map((h) =>
      `<div class="cm-assist-row" role="option" data-id="${esc(h.id)}" data-title="${esc(h.title)}">
         <span class="cm-assist-name">${esc(h.title)}</span>
         <span class="cm-assist-sub">${esc(h.domain)}</span></div>`).join("")
      : `<p class="cm-assist-note">${esc(COPY.author.pickNone)}</p>`);
}

// ── 주제 ───────────────────────────────────────────────────────────────────────
let topicsCache: { at: number; list: string[] } | null = null;
/** 이 소유자의 주제 목록(입력 제안용). 30초 동안 한 번만 묻는다 — 시트를 열 때마다 왕복하지 않게. */
export async function topicOptions(): Promise<string[]> {
  if (topicsCache && Date.now() - topicsCache.at < 30_000) return topicsCache.list;
  const list: string[] = await api.domains(cfg.owner)
    .then((r: any) => (r?.rows || r?.domains || [])
      .map((d: any) => d?.domain || d?.name || d)
      .filter((s: unknown): s is string => typeof s === "string" && !!s))
    .catch(() => []);
  topicsCache = { at: Date.now(), list };
  return list;
}
/** 주제를 새로 만들었을 수 있다 — 다음 제안이 그것을 담게 비운다. */
export const forgetTopics = (): void => { topicsCache = null; };

export async function fillTopicList(dl: HTMLDataListElement): Promise<void> {
  const list = await topicOptions();
  dl.innerHTML = list.map((d) => `<option value="${esc(d)}"></option>`).join("");
}

/** 지금 보고 있는 자리의 주제 — 새로 만드는 것에 미리 채운다. View 가 없으면 빈 값. */
export function contextTopic(): string {
  return viewHooks()?.context?.().topic || "";
}

// ── 흐름 A — 만들기 진입점 ─────────────────────────────────────────────────────
/** View 밖에서 "새 문서"를 눌렀다 — View 가 마운트되면서 한 번 꺼내 연다. */
let pendingNewDoc: { topic: string } | null = null;
export function takePendingNewDoc(): { topic: string } | null {
  const p = pendingNewDoc; pendingNewDoc = null; return p;
}

/** 문서 열에 새 문서를 연다. View 밖(검색 표면)이면 View 로 가서 연다. */
export function startNewDoc(topic = contextTopic()): void {
  const h = viewHooks();
  if (h?.newDoc) { h.newDoc(topic); return; }
  pendingNewDoc = { topic };
  location.hash = "#view";
}

export async function startNewNode(opts: { topic?: string; kind?: NodeKind; title?: string } = {}) {
  const { openComposer } = await import("./node-composer");
  return openComposer({ topic: opts.topic ?? contextTopic(), kind: opts.kind, title: opts.title });
}

/** 만들기 메뉴 — 문서 쓰기 · 노드 만들기 · 파일 가져오기. 주제는 연 자리에서 채운다. */
export function openCreateMenu(x: number, y: number, topic = contextTopic()): void {
  const A = COPY.author;
  openContextMenu(x, y, [
    { label: A.menuWrite, run: () => startNewDoc(topic) },
    { label: A.menuNode, run: () => void startNewNode({ topic }) },
    { label: A.menuImport, run: () => void import("./surfaces/settings")
        .then((m) => m.openSettings("data", "import")) },
  ]);
}

let keysMounted = false;
/** Ctrl/Cmd+N = 새 문서, Ctrl/Cmd+Shift+N = 새 노드. 편집기 안에서도 받는다 — 쓰다가
 *  다음 것을 만드는 손이 가장 흔하다. 시트가 떠 있으면 그 시트의 일이 먼저라 쉰다. */
export function mountAuthoringKeys(): void {
  if (keysMounted) return;
  keysMounted = true;
  document.addEventListener("keydown", (e) => {
    if (!(e.ctrlKey || e.metaKey) || e.altKey || e.key.toLowerCase() !== "n") return;
    if (document.querySelector(".sheet-scrim")) return;
    e.preventDefault();
    if (e.shiftKey) void startNewNode();
    else startNewDoc();
  }, true);
}

// ── 흐름 B — 문서 열의 새 문서 ─────────────────────────────────────────────────
/** 제목 조각에서 **아직 안 쓰인** 문서 id 를 고른다. `skip` 은 이미 거절당한 후보다
 *  (남의 스페이스가 가진 id 는 읽기로는 안 보이고 쓰기에서 거절돼 여기로 돌아온다). */
async function freeDocId(title: string, stamp: string, skip: string[]): Promise<string> {
  const base = slugOf(title) || stamp;
  for (let i = 1; i <= 20; i++) {
    const cand = i === 1 ? base : `${base}-${i}`;
    if (skip.includes(cand)) continue;
    try {
      const d = await api.docGet(cfg.owner, cand);
      if (d.executed || docMidWrite(d)) continue;        // 내 문서가 이미 이 id 를 쓴다
    } catch {
      continue;              // 비어 있는지 확인 못 한 id 는 쓰지 않는다 — 있는 문서를 덮을 수 있다
    }
    return cand;
  }
  // 확인된 빈 id 가 없다 — 짐작한 id 로 쓰지 않고 저장을 실패시킨다(상태 줄이 말한다).
  throw new Error("no free document id");
}

/** 만들어지기 전에 닫힌 새 문서. 다음 "새 문서"가 이것을 되살린다(메모리만 — 앱을 다시 열면
 *  없다). 닫는 문(✕ · Esc · Ctrl/Cmd+N · 다른 노드 열기)이 여럿이라 확인창 대신 초안으로 둔다. */
let draft: { title: string; text: string; contentTime: string; topic: string } | null = null;

/** 문서 열 안에 새 문서를 쓰는 자리를 세운다(View 가 열 머리를 그린 뒤 부른다).
 *
 *  첫 저장이 **제목과 주제**를 함께 보낸다 — 서버가 그 자리에서 미러 노드
 *  (`concept:doc.<id>`)를 만들어 트리와 지도에 곧바로 선다(Q3.1). id 는 제목 조각으로
 *  선언하고 만든 뒤에는 바꾸지 않는다(Q3.4). 제목을 치는 중에는 만들지 않는다 —
 *  "My no" 에서 2초 쉬었다고 id 가 `my-no` 로 굳으면 안 된다. */
export async function mountDocWriter(host: HTMLElement, opts: {
  topic?: string;
  onCreated: (docId: string, topic: string) => void;
  onTopicChanged?: (topic: string, docId: string) => void;
}): Promise<DocEditorHandle> {
  const A = COPY.author;
  // 만들어지기 전에 닫힌 초안이 있으면 그것으로 연다(주제도 그때 것).
  const restored = draft;
  draft = null;
  /** "초안 버리기"를 눌렀다 — 이 편집기가 닫힐 때 내용을 다시 초안으로 넘기지 않는다. */
  let dropped = false;
  const startTopic = restored?.topic || opts.topic || "";
  host.innerHTML = `
    <div class="writer-meta">
      <label class="writer-topic"><span>${esc(A.topic)}</span>
        <input id="wr-topic" type="text" list="wr-topics" autocomplete="off" spellcheck="false"
               placeholder="${esc(A.topicHint)}" value="${esc(startTopic)}"/>
      </label>
      <datalist id="wr-topics"></datalist>
      <span class="hint writer-id" id="wr-id"></span>
    </div>
    <div id="wr-ed"></div>
    <p class="hint writer-note" id="wr-note" role="status"></p>`;
  const topicEl = host.querySelector<HTMLInputElement>("#wr-topic")!;
  const idEl = host.querySelector<HTMLElement>("#wr-id")!;
  const noteEl = host.querySelector<HTMLElement>("#wr-note")!;
  void fillTopicList(host.querySelector<HTMLDataListElement>("#wr-topics")!);
  const stamp = stampSlug();
  let created = "";
  let sentTopic = "";
  // 제목이 비었으면 id 도 말하지 않는다 — 시각 조각을 먼저 보여 주면 그것이 id 인 줄 안다.
  const preview = (title: string) => {
    if (!created) idEl.textContent = title.trim() ? A.idPreview(slugOf(title) || stamp) : "";
  };
  preview(restored?.title || "");

  const { mountDocEditor } = await import("./doc-editor");
  const handle = await mountDocEditor(host.querySelector<HTMLElement>("#wr-ed")!, {
    owner: cfg.owner, initial: restored?.text || "", title: restored?.title || "",
    contentTime: restored?.contentTime || "", autoFocus: false,
    topic: () => topicEl.value.trim(),
    declareId: (title, skip) => freeDocId(title, stamp, skip),
    onUnsaved: (d) => { if (!dropped) draft = { ...d, topic: topicEl.value.trim() }; },
    onCreated: (docId, topic) => {
      created = docId; sentTopic = topic;
      idEl.textContent = A.idFixed(docId);
      const wanted = slugOf(noteTitle()) || stamp;
      noteEl.textContent = docId === wanted ? A.docSavedAs(docId, topic)
        : `${A.docIdTaken(wanted, docId)} ${A.docSavedAs(docId, topic)}`;
      opts.onCreated(docId, topic);
    },
  });
  const titleEl = host.querySelector<HTMLInputElement>("#ed-title");
  const noteTitle = () => titleEl?.value || "";
  titleEl?.addEventListener("input", () => preview(titleEl.value));
  // 만든 뒤 주제를 바꾸면 그 자리에서 미러의 주제를 옮긴다(본문은 마지막 저장본 그대로).
  topicEl.addEventListener("change", () => void (async () => {
    const t = topicEl.value.trim();
    // 아직 안 만들었다 — 주제가 모자라 미뤄 둔 첫 저장을 지금 한다(제목이 없으면 그 이유를 말한다).
    if (!created) { if (t) await handle.saveNow?.(); return; }
    if (!t || t === sentTopic) return;
    try {
      if (!(await handle.saveTopic?.(t))) return;
      sentTopic = t;
      noteEl.textContent = A.docSavedAs(created, t);
      opts.onTopicChanged?.(t, created);
    } catch { /* 저장 상태 줄이 이미 실패를 말한다 */ }
  })());
  // 되살린 초안이면 그렇다고 말하고, 버리고 빈 문서로 시작할 문을 준다.
  if (restored) {
    noteEl.innerHTML = `${esc(A.draftRestored)} <button class="rail-x" type="button" id="wr-discard">${
      esc(A.draftDiscard)}</button>`;
    noteEl.querySelector("#wr-discard")?.addEventListener("click", () => {
      dropped = true;
      startNewDoc(startTopic);
    });
  }
  titleEl?.focus();
  return handle;
}
