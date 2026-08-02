import { api, cfg } from "./api";
import { COPY } from "./copy";
import { openSheet } from "./components";
import { atlasHooks } from "./topbar";

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
/** 한 답변에 싣는 근거 인용 수. 말풍선이 화면을 삼키지 않게. */
const MAX_QUOTES = 2;

type Brief = { id: string; title: string; domain: string };

type Card =
  | { kind: "answer"; id: string; title: string; domain: string; body: string;
      quotes: { title: string; section: string; text: string }[];
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

/** 질문 → 카드. 판단이 들어가는 유일한 자리이고, 규칙이 전부 명시적이다. */
async function resolve(q: string): Promise<Card> {
  const res: any = await api.search(q);
  const hits: any[] = (res?.node_hits || []).filter((h: any) => h && h.id);
  if (!hits.length) return { kind: "none", query: q };

  const norm = (s: unknown) => String(s ?? "").trim().toLowerCase();
  // 제목이 질문과 정확히 같으면 그것. 사용자가 이름을 댄 경우다.
  const exact = hits.find((h) => norm(h.title) === norm(q));
  // 그 외에는 **검색이 스스로 남긴 매치 근거**(matched_in)를 쓴다. 임의 임계값을
  // 지어내지 않는다 — 제목에 맞은 것이 하나면 확정, 여럿이면 되묻는다.
  const byTitle = hits.filter((h) => h.matched_in === "title");
  const pick = exact || (byTitle.length === 1 ? byTitle[0] : null);
  if (!pick && byTitle.length > 1)
    return { kind: "choose", options: byTitle.slice(0, 3).map(brief) };
  return await answerFor(brief(pick || hits[0]));
}

function brief(h: any): Brief {
  return { id: String(h.id || ""), title: String(h.title || h.id || ""),
           domain: String(h.domain || "") };
}

/** 노드 → 답변 카드. 본문·근거·집계 전부 `node_context` 한 번으로 얻는다. */
async function answerFor(b: Brief): Promise<Card> {
  const ctx: any = await api.nodeContext(cfg.owner, b.id, MAX_QUOTES + 1);
  const quotes = (ctx?.excerpts || []).slice(0, MAX_QUOTES).map((e: any) => ({
    title: String(e.doc_title || e.doc_id || ""),
    section: String(e.section || ""),
    text: String(e.excerpt || ""),
  })).filter((x: any) => x.text);
  const links = ctx?.links || {};
  return {
    kind: "answer",
    id: b.id,
    title: String(ctx?.title || b.title),
    domain: b.domain,
    body: pickBody(ctx),
    quotes,
    degree: Number(links.degree || 0),
    attached: Number(links.attached || 0),
  };
}

/** 본문 고르기 — `one_liner` → `props` 순.
 *
 *  `one_liner` 는 **쓰기 경로가 없는 열**이라(MCP 적재분 전량이 빈 값) 이것만 보면
 *  답이 통째로 빈다. 사람이 쓴 한두 문장 요약은 실제로 `props` 에 들어와 있다.
 *  키 이름은 사용자 저작이라 고정할 수 없으므로 흔한 이름을 먼저 보고, 없으면
 *  문장처럼 보이는 값 중 가장 긴 것을 쓴다. 그래도 없으면 **빈 채로 둔다** —
 *  발췌를 본문 자리로 끌어오면 아래 근거와 같은 문장이 두 번 나온다. */
function pickBody(ctx: any): string {
  const ol = String(ctx?.one_liner || "").trim();
  if (ol) return ol;
  const props = (ctx?.props && typeof ctx.props === "object") ? ctx.props : {};
  for (const k of ["note", "summary", "one_liner", "description", "desc"]) {
    const v = props[k];
    if (typeof v === "string" && v.trim()) return v.trim();
  }
  const rest = Object.values(props)
    .filter((v): v is string => typeof v === "string" && v.trim().length > 12)
    .sort((a, b) => b.length - a.length);
  return rest.length ? rest[0]!.trim() : "";
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

  const src = card.quotes.length ? `<div class="ask-src">
      <span class="ask-src-h">${esc(c.sources(card.quotes.length))}</span>
      ${card.quotes.map((q) => `<blockquote class="ask-quote">${esc(q.text)}
        <span class="ask-qmeta">${esc(q.title)}${q.section ? " · " + esc(q.section) : ""}</span>
      </blockquote>`).join("")}</div>` : "";
  const stat = (card.degree || card.attached)
    ? `<p class="ask-stat">${esc(c.stat(card.degree, card.attached))}</p>` : "";
  return `<div class="ask-ctitle"><b>${esc(card.title)}</b>${
      card.domain ? `<span class="ask-cdomain">${esc(card.domain)}</span>` : ""}</div>
    <p class="ask-body${card.body ? "" : " empty"}">${esc(card.body || c.noBody)}</p>
    ${src}${stat}
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
  // 지도 착지 — Atlas 가 떠 있으면 훅으로 그 노드까지, 아니면 해시로 띄운다
  // (topbar 의 결과 착지와 같은 규약).
  panel.querySelectorAll<HTMLElement>("[data-go]").forEach((b) =>
    b.addEventListener("click", () => {
      const id = b.dataset.go!;
      const h = atlasHooks();
      if (h) h.openNode(id);
      else location.hash = `#atlas/trace/${encodeURIComponent(id)}`;
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
