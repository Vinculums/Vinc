import { api, cfg } from "./api";
import { COPY } from "./copy";
import { openSheet } from "./components";
import { viewHooks } from "./topbar";

/** 묻기 — 앱 안의 **무LLM 대화**(D73).
 *
 *  성립 조건이 하나다: **문장을 생성하지 않는다.** 답변 본문은 전부 스토어에 저장된
 *  문장을 그대로 인용한 것이고(개념 요약 · 정본 발췌), 이 모듈이 하는 일은 어느
 *  문장을 가져올지 **결정론으로 고르는 것**뿐이다. 그래서 외부 키가 필요 없고
 *  데스크탑과 웹이 같은 답을 낸다(F5 불변).
 *
 *  파이프라인 — 전 단계 결정론:
 *    질문 → `search().node_hits`(개체 해소, 기존 함수 재사용) → 후보 판정
 *         → `node_context()`(본문·근거·연결 집계) → 카드 조립
 *
 *  후보 판정은 **추측하지 않는다.** 제목이 정확히 맞으면 그것, 제목 일치가 하나면
 *  그것, 여럿이면 **되묻는다**. LLM 이 없어 애매성을 봉합할 수단이 없는데, 그것은
 *  결함이 아니라 명시적 되묻기로 전환할 기회다 — 모르는 것을 아는 척하지 않는 것이
 *  이 표면의 신뢰 기반이다.
 *
 *  멀티턴은 대명사 해소 없이 성립한다: 다음 수가 **id 를 든 칩**으로 나오므로
 *  사용자가 "그거"를 타이핑할 일이 없다(칩이 곧 다음 턴의 개체다).
 *
 *  ⚠ 아직 안 하는 것(연구 문서 `vinc.d.chat-research` §7 · §8): 조사 접미 확장,
 *  의도 라우터(관계·이력·기간·진단), 요약·종합. 요약·종합은 **영구 제외**이고
 *  나머지는 단계 2·4다. 여기서 유도하지 않는다 — 되는 질문꼴만 화면이 말한다. */

const LOG_KEY = "vinc.ask.log";
/** 보관 상한. 넘으면 오래된 턴부터 버리고 그 사실을 화면에 적는다(조용한 절단 금지). */
const MAX_TURNS = 60;
/** 본문 인용 후보로 받아 올 발췌 수(가장 관련 있는 하나만 본문에 쓴다). */
const MAX_EXCERPTS = 3;
/** 한 답변에 칩으로 세우는 관련 노드 수. 넘으면 "외 N개"로 밝힌다(조용한 절단 금지). */
const MAX_RELATED = 6;

type Brief = { id: string; title: string; domain: string };
/** 근거 한 줄 — 관련 노드 하나와 그것이 답과 맺은 관계. */
type Rel = { id: string; title: string; domain: string; type: string; dir: string;
             evidence: boolean; confidence: number };

type Card =
  | { kind: "answer"; id: string; title: string; domain: string; body: string;
      /** 본문 출처. `doc` 이면 정본 발췌를 인용한 것이라 출처를 함께 적는다. */
      bodySrc: BodySrc; bodyCite: string;
      related: Rel[]; relatedTotal: number;
      degree: number; attached: number }
  | { kind: "choose"; options: Brief[] }
  | { kind: "none"; query: string }
  | { kind: "error" };

type Turn = { role: "you"; at: number; text: string }
           | { role: "vinc"; at: number; card: Card };

let turns: Turn[] = load();
let panel: HTMLElement | null = null;
let onClose: (() => void) | null = null;
let busy = false;

const esc = (s: unknown) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  c === "&" ? "&amp;" : c === "<" ? "&lt;" : c === ">" ? "&gt;" : "&quot;");

function load(): Turn[] {
  try {
    const raw = JSON.parse(localStorage.getItem(LOG_KEY) || "[]");
    return Array.isArray(raw) ? (raw as Turn[]).slice(-MAX_TURNS) : [];
  } catch { return []; }
}

function save() {
  // 상한을 **저장 시점에** 건다 — 메모리와 디스크가 어긋나면 재시작 후 이력이
  // 갑자기 늘거나 줄어 보인다.
  turns = turns.slice(-MAX_TURNS);
  try { localStorage.setItem(LOG_KEY, JSON.stringify(turns)); } catch { /* 용량 초과 — 화면은 계속 산다 */ }
}

/** 상단 바가 패널 요소와 닫기 동작을 넘긴다(패널의 주인은 상단 바다). */
export function initAsk(el: HTMLElement, close: () => void) {
  panel = el;
  onClose = close;
}

export function askHasTurns(): boolean { return turns.length > 0; }

export function askOpen() {
  if (!panel) return;
  panel.hidden = false;
  render();
}

export function askClose() {
  if (panel) panel.hidden = true;
}

export function askIsOpen(): boolean { return !!panel && !panel.hidden; }

/** 한 턴 — 질문을 싣고, 답을 만들고, 다시 그린다. */
export async function askSubmit(raw: string) {
  const q = raw.trim();
  if (!q || busy) return;
  busy = true;
  turns.push({ role: "you", at: Date.now(), text: q });
  save();
  askOpen();
  render(true);                       // 생각 중 표시(질문은 이미 화면에 있다)
  let card: Card;
  try {
    card = await resolve(q);
  } catch {
    card = { kind: "error" };
  }
  turns.push({ role: "vinc", at: Date.now(), card });
  save();
  busy = false;
  render();
}

// ── 결정론 해소 ────────────────────────────────────────────────────────────

/** 질문 → 카드. 판단이 들어가는 유일한 자리이고, 규칙이 전부 명시적이다.
 *
 *  **2026-08-03 재설계.** 구 규칙은 세 갈래였다: 제목 완전일치 → 확정 /
 *  `matched_in==='title'` 이 하나 → 확정 / 여럿 → 되묻기 / **그 외 → `hits[0]`**.
 *  마지막 갈래가 이 표면을 망가뜨리고 있었다. 사이드카의 구 노드 검색이 토큰 AND
 *  라 자연어 질문은 제목 매치를 **한 건도** 못 냈고(조사·의문사가 AND 를 깨뜨렸다),
 *  그래서 실행 경로는 언제나 `hits[0]` 로 떨어졌다. 그런데 그 자리의 후보 순서는
 *  그래프 암의 **id 알파벳순**이다 — 실측: "결제 실패하면 어떻게 되나요?"가
 *  `결제 실패 처리` 를 두고 `멱등 키`(i < p)를 답했다. 즉 답이 질문과 무관하게
 *  뽑히고 있었고, 이 모듈이 내세운 "추측하지 않는다"는 약속이 실은 깨져 있었다.
 *
 *  이제 사이드카가 매치 근거를 **수치로** 준다(`hit_tokens`/`title_tokens`,
 *  A1.10). 1위가 2위를 **엄격히** 이길 때만 확정하고, 비기면 되묻는다 — 임의
 *  임계값은 여전히 없다. 비교할 수치가 없던 것이 문제였지 규칙이 문제가 아니었다. */
async function resolve(q: string): Promise<Card> {
  const res: any = await api.search(q);
  const hits: any[] = (res?.node_hits || []).filter((h: any) => h && h.id);
  if (!hits.length) return { kind: "none", query: q };

  const norm = (s: unknown) => String(s ?? "").trim().toLowerCase();
  // 제목이 질문과 정확히 같으면 그것. 사용자가 이름을 댄 경우다.
  const exact = hits.find((h) => norm(h.title) === norm(q));
  if (exact) return await answerFor(brief(exact));
  if (hits.length === 1) return await answerFor(brief(hits[0]));

  const tok = (h: any) => Number(h.hit_tokens || 0);
  const ttl = (h: any) => Number(h.title_tokens || 0);
  // 안정 정렬 — 동점이면 사이드카가 준 순서(RRF)를 그대로 존중한다.
  const ranked = hits.slice().sort((a, b) => tok(b) - tok(a) || ttl(b) - ttl(a));
  const [top, second] = ranked;
  // 확정 조건: 토큰이 하나라도 맞았고, 2위를 맞은 토큰 수나 제목 토큰 수로 이긴다.
  // 그래프 암만 올라온 경우(전부 0)는 확정하지 않는다 — 청크 인접은 "관련 있다"는
  // 뜻이지 "이것을 물었다"는 뜻이 아니다.
  const decisive = tok(top) > 0 && (tok(top) > tok(second) || ttl(top) > ttl(second));
  if (!decisive) return { kind: "choose", options: ranked.slice(0, 3).map(brief) };
  return await answerFor(brief(top));
}

function brief(h: any): Brief {
  return { id: String(h.id || ""), title: String(h.title || h.id || ""),
           domain: String(h.domain || "") };
}

/** 노드 → 답변 카드. 본문·근거·집계 전부 `node_context` 한 번으로 얻는다.
 *
 *  **답과 근거의 자리 바꿈(2026-08-03 발주자 지시).** 예전에는 본문이 개념 요약
 *  한 줄이고 그 아래 **문서 발췌**가 근거로 깔렸다. 그 배치는 두 가지가 어긋난다:
 *  ① 요약 열(`one_liner`)이 자주 비어 답이 통째로 비고 화면엔 문서 덩어리만 남는다
 *  ② 사용자가 물은 것은 "무엇인가"인데 근거 자리가 그 답을 대신 말하고 있었다.
 *  이제 **정본 발췌는 본문으로 승격**하고(출처를 함께 적는다), **근거는 이 노드에
 *  연결된 관련 노드들**이 진다. 근거가 그래프인 것은 이 제품의 구조적 우위이기도
 *  하다 — 관계마다 evidence·confidence 가 붙어 있고 노트 앱은 그것을 못 붙인다. */
async function answerFor(b: Brief): Promise<Card> {
  const ctx: any = await api.nodeContext(cfg.owner, b.id, MAX_EXCERPTS);
  const links = ctx?.links || {};
  const picked = pickBody(ctx);
  const all: Rel[] = (links.items || []).map((it: any) => ({
    id: String(it.id || ""), title: String(it.title || it.id || ""),
    domain: String(it.domain || ""), type: String(it.type || ""),
    dir: String(it.dir || ""), evidence: !!it.evidence,
    confidence: Number(it.confidence || 0),
  })).filter((r: Rel) => r.id);
  // 근거로서 강한 것부터: 신뢰도 → 근거 문장 보유 → 사이드카 순서(안정 정렬).
  all.sort((x, y) => y.confidence - x.confidence ||
                     Number(y.evidence) - Number(x.evidence));
  return {
    kind: "answer",
    id: b.id,
    title: String(ctx?.title || b.title),
    domain: b.domain,
    body: picked.text,
    bodySrc: picked.src,
    bodyCite: picked.cite,
    related: all.slice(0, MAX_RELATED),
    relatedTotal: all.length,
    degree: Number(links.degree || 0),
    attached: Number(links.attached || 0),
  };
}

/** 본문 고르기 — `one_liner` → `props` → **정본 발췌** 순.
 *
 *  `one_liner` 는 **쓰기 경로가 없는 열**이라(MCP 적재분 전량이 빈 값) 이것만 보면
 *  답이 통째로 빈다. 사람이 쓴 한두 문장 요약은 실제로 `props` 에 들어와 있다.
 *  키 이름은 사용자 저작이라 고정할 수 없으므로 흔한 이름을 먼저 보고, 없으면
 *  문장처럼 보이는 값 중 가장 긴 것을 쓴다.
 *
 *  마지막 폴백이 **발췌**다. 예전에는 여기서 멈추고 빈 본문을 냈다 — 발췌가 아래
 *  근거 자리에 이미 있어 같은 문장이 두 번 나오기 때문이었다. 근거가 관련 노드로
 *  바뀐 지금은 중복이 없으므로, 있는 문장을 답으로 쓰지 않을 이유가 없다.
 *  ⚠ 생성이 아니라 **인용**이다(F5 불변) — 그래서 출처를 함께 돌려준다. */
type BodySrc = "one_liner" | "props" | "doc" | "none";

function pickBody(ctx: any): { text: string; src: BodySrc; cite: string } {
  const ol = String(ctx?.one_liner || "").trim();
  if (ol) return { text: ol, src: "one_liner", cite: "" };
  const props = (ctx?.props && typeof ctx.props === "object") ? ctx.props : {};
  for (const k of ["note", "summary", "one_liner", "description", "desc"]) {
    const v = props[k];
    if (typeof v === "string" && v.trim()) return { text: v.trim(), src: "props", cite: "" };
  }
  const rest = Object.values(props)
    .filter((v): v is string => typeof v === "string" && v.trim().length > 12)
    .sort((a, b) => b.length - a.length);
  if (rest.length) return { text: rest[0]!.trim(), src: "props", cite: "" };
  const ex = (ctx?.excerpts || []).find((e: any) => String(e?.excerpt || "").trim());
  if (ex) {
    const where = String(ex.doc_title || ex.doc_id || "");
    const sec = String(ex.section || "");
    return { text: String(ex.excerpt).trim(), src: "doc",
             cite: sec ? `${where} · ${sec}` : where };
  }
  return { text: "", src: "none", cite: "" };
}

// ── 그리기 ────────────────────────────────────────────────────────────────

function render(thinking = false) {
  if (!panel) return;
  const c = COPY.ui.ask;
  const head = `<div class="ask-head">
      <span class="ask-title">${esc(c.title)}</span>
      <button class="ask-hbtn" id="ask-hist">${esc(c.history)}</button>
      <button class="ask-hbtn" id="ask-clear"${turns.length ? "" : " disabled"}>${esc(c.clear)}</button>
      <button class="ask-hbtn" id="ask-close">${esc(c.close)}</button>
    </div>`;
  const body = turns.length
    ? turns.map(bubble).join("") + (thinking
        ? `<div class="ask-turn vinc"><div class="ask-bubble"><span class="hint">${esc(c.thinking)}</span></div></div>`
        : "")
    : `<div class="ask-empty"><p>${esc(c.intro)}</p>
         <p class="hint">${esc(c.examplesHint)}: ${esc(c.forms)}</p></div>`;
  const note = turns.length >= MAX_TURNS
    ? `<p class="ask-empty hint" style="padding:.35rem">${esc(c.kept(MAX_TURNS))}</p>` : "";
  panel.innerHTML = head + `<div class="ask-log" id="ask-log">${body}${note}</div>`;
  wire();
  const log = panel.querySelector<HTMLElement>("#ask-log");
  if (log) toBottom(log);
}

/** 마지막 턴이 보이게 로그를 맨 아래로. **두 번** 민다:
 *
 *  ① 동기 — `scrollHeight` 를 읽는 순간 레이아웃이 확정되므로 대개 이걸로 끝난다.
 *     창이 화면에 없어 프레임을 안 그리는 상황(백그라운드 탭·미표시 창)에서도
 *     동작하는 유일한 경로다.
 *  ② 다음 프레임 — 폰트·줄바꿈이 늦게 잡혀 높이가 커지는 경우를 흡수한다. 창이
 *     안 그려지면 이 콜백은 아예 실행되지 않으므로(실측) ①이 없으면 스크롤이
 *     영영 0 에 머문다. rAF 만 두는 구현이 이 사고를 낸다.
 *
 *  요소가 그새 교체돼 떨어져 나갔으면(연속 render) 조용히 넘어간다. */
function toBottom(log: HTMLElement) {
  log.scrollTop = log.scrollHeight;
  requestAnimationFrame(() => {
    if (log.isConnected) log.scrollTop = log.scrollHeight;
  });
}

function bubble(t: Turn): string {
  if (t.role === "you")
    return `<div class="ask-turn you"><div class="ask-bubble">${esc(t.text)}</div></div>`;
  return `<div class="ask-turn vinc"><div class="ask-bubble">${cardHtml(t.card)}</div></div>`;
}

function cardHtml(card: Card): string {
  const c = COPY.ui.ask;
  if (card.kind === "error") return `<span class="hint">${esc(c.failed)}</span>`;
  if (card.kind === "none")
    return `<p class="ask-body">${esc(c.notFound(card.query))}</p>
            <p class="hint" style="margin:0">${esc(c.notFoundHint)}</p>`;
  if (card.kind === "choose")
    return `<p class="ask-body">${esc(c.ambiguous)}</p>
      <div class="ask-chips">${card.options.map((o) =>
        // 제목은 **속성으로도** 싣는다 — 칩의 보이는 글자에는 도메인이 붙어 있어서
        // `textContent` 를 그대로 다음 턴의 질문으로 쓰면 "제목 도메인"이 사용자가
        // 한 말인 양 로그에 남는다.
        `<button class="ask-chip" data-pick="${esc(o.id)}" data-title="${esc(o.title)}"
          >${esc(o.title)}${
          o.domain ? ` <span class="hint">${esc(o.domain)}</span>` : ""}</button>`).join("")}</div>`;

  // 본문이 정본 인용이면 어디서 왔는지 밝힌다 — 생성이 아니라 인용이라는 것이
  // 이 표면의 성립 조건이므로, 출처 없는 인용은 그 조건을 흐린다.
  const cite = card.bodySrc === "doc" && card.bodyCite
    ? `<p class="ask-qmeta">${esc(c.bodyFromDoc(card.bodyCite))}</p>` : "";
  // 근거 = 관련 노드. 칩은 **id 를 들고 있어** 누르면 그대로 다음 턴의 개체가 된다
  // (대명사 해소 없이 성립하는 멀티턴 — 이 모듈의 원래 설계 그대로).
  const more = card.relatedTotal - card.related.length;
  const rel = card.related.length ? `<div class="ask-src">
      <span class="ask-src-h">${esc(c.evidence(card.relatedTotal))}</span>
      <div class="ask-chips">${card.related.map((r) => {
        const tag = c.relTag[`${r.type}:${r.dir}`] || r.type.toLowerCase();
        const conf = r.confidence > 0 ? ` <span class="hint">${Math.round(r.confidence * 100)}%</span>` : "";
        return `<button class="ask-chip" data-pick="${esc(r.id)}" data-title="${esc(r.title)}"
          ><span class="hint">${esc(tag)}</span> ${esc(r.title)}${conf}</button>`;
      }).join("")}${more > 0
        ? `<span class="hint">${esc(c.evidenceMore(more))}</span>` : ""}</div></div>`
    : `<p class="ask-stat">${esc(c.evidenceNone)}</p>`;
  const stat = (card.degree || card.attached)
    ? `<p class="ask-stat">${esc(c.stat(card.degree, card.attached))}</p>` : "";
  return `<div class="ask-ctitle"><b>${esc(card.title)}</b>${
      card.domain ? `<span class="ask-cdomain">${esc(card.domain)}</span>` : ""}</div>
    <p class="ask-body${card.body ? "" : " empty"}">${esc(card.body || c.noBody)}</p>
    ${cite}${rel}${stat}
    <div class="ask-chips">
      <button class="ask-chip" data-go="${esc(card.id)}">${esc(c.openMap)}</button>
    </div>`;
}

function wire() {
  if (!panel) return;
  panel.querySelector("#ask-close")?.addEventListener("click", () => onClose?.());
  panel.querySelector("#ask-clear")?.addEventListener("click", () => {
    turns = []; save(); render();
  });
  panel.querySelector("#ask-hist")?.addEventListener("click", () => openHistory());
  // 지도 착지 — View 가 떠 있으면 훅으로 그 노드까지, 아니면 해시로 띄운다
  // (topbar 의 결과 착지와 같은 규약).
  panel.querySelectorAll<HTMLElement>("[data-go]").forEach((b) =>
    b.addEventListener("click", () => {
      const id = b.dataset.go!;
      const h = viewHooks();
      if (h) h.openNode(id);
      else location.hash = `#view/trace/${encodeURIComponent(id)}`;
    }));
  // 되묻기 칩 — **id 를 들고 있으므로** 다음 턴에 애매성이 없다(대명사 해소 불요).
  panel.querySelectorAll<HTMLElement>("[data-pick]").forEach((b) =>
    b.addEventListener("click", async () => {
      if (busy) return;
      busy = true;
      const title = b.dataset.title || b.textContent?.trim() || "";
      turns.push({ role: "you", at: Date.now(), text: title });
      save(); render(true);
      let card: Card;
      try {
        card = await answerFor({ id: b.dataset.pick!, title, domain: "" });
      } catch { card = { kind: "error" }; }
      turns.push({ role: "vinc", at: Date.now(), card });
      save(); busy = false; render();
    }));
}

/** 대화 이력 조회 — 지난 질문을 날짜로 묶어 보여주고, 누르면 다시 묻는다.
 *  보관은 최근 `MAX_TURNS` 턴까지다(상한을 화면에 적는다 — 조용한 절단 금지). */
function openHistory() {
  const c = COPY.ui.ask;
  const sheet = openSheet({ title: c.historyTitle });
  const asked = turns.filter((t): t is Extract<Turn, { role: "you" }> => t.role === "you");
  if (!asked.length) {
    sheet.body.innerHTML = `<p class="hint">${esc(c.historyEmpty)}</p>`;
    return;
  }
  const day = (ms: number) => new Date(ms).toLocaleDateString();
  const time = (ms: number) =>
    new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  let html = "", last = "";
  for (const t of [...asked].reverse()) {
    const d = day(t.at);
    if (d !== last) { html += `<div class="ask-hday">${esc(d)}</div>`; last = d; }
    html += `<button class="ask-hrow" data-q="${esc(t.text)}">
        <span>${esc(t.text)}</span><span class="hint">${esc(time(t.at))}</span></button>`;
  }
  html += `<p class="hint" style="margin:.6rem .5rem 0">${esc(c.kept(MAX_TURNS))}</p>`;
  sheet.body.innerHTML = html;
  sheet.body.querySelectorAll<HTMLElement>("[data-q]").forEach((r) =>
    r.addEventListener("click", () => {
      sheet.close();
      void askSubmit(r.dataset.q!);
    }));
}
