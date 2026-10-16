/** 흐름 G — 저장 뒤 "이 문서에서 찾은 개념" 목록.
 *
 *  여태는 "후보 N개 · 반영하기" 단추 하나였다: 전부를 한 번에 반영했고, 저장할 때마다
 *  같은 후보를 다시 권했고, 주제가 없어 서버가 거절해도 `executed` 를 안 봐서 "개념 +0"
 *  만 남았다. 이제 후보마다 체크하고 제목을 고칠 수 있으며, 이미 있는 개념은 표시하고,
 *  거절되면 이유를 말한다. 반영하거나 건너뛴 후보는 그 문서에서 다시 권하지 않는다.
 *
 *  "다시 권하지 않음"은 이 기기의 기억이다(localStorage, 문서별). 서버는 권한 기록을
 *  갖지 않으므로 다른 기기에서는 한 번 더 뜬다 — 반영된 것은 거기서 "이미 있음"으로 뜬다. */
import { api, messageOf } from "./api";
import { COPY } from "./copy";
import { esc } from "./components";
import { fillTopicList, forgetTopics } from "./authoring";

type Cand = NonNullable<Awaited<ReturnType<typeof api.docExtract>>["candidates"]>[number];
const candidateKey = (c: Cand) => c.candidate_id || c.slug || c.title;
let nextTopicList = 0;

const seenKey = (owner: string, docId: string) => `vinc.extract.seen.${owner}.${docId}`;
function loadSeen(owner: string, docId: string): Set<string> {
  try { return new Set(JSON.parse(localStorage.getItem(seenKey(owner, docId)) || "[]")); }
  catch { return new Set(); }
}
function saveSeen(owner: string, docId: string, add: string[]): void {
  try {
    const s = loadSeen(owner, docId);
    add.forEach((x) => s.add(x));
    localStorage.setItem(seenKey(owner, docId), JSON.stringify([...s].slice(-400)));
  } catch { /* 저장소가 막혀 있으면 이번 화면에서만 기억한다 */ }
}

/** 편집기의 추출 칸(`#ed-extract`)을 채운다. 권할 것이 없으면 칸을 비운다. */
export async function offerExtraction(box: HTMLElement, o: {
  owner: string; docId: string;
  /** 문서에 주제를 적는다(본문은 그대로). 되면 true. */
  setTopic: (topic: string) => Promise<boolean>;
  /** 가져오기에서는 주제 없는 반영을 요청하기 전에 주제를 묻는다. */
  requireTopic?: boolean;
  onApplied?: () => void;
}): Promise<void> {
  const A = COPY.author;
  let res: Awaited<ReturnType<typeof api.docExtract>>;
  try { res = await api.docExtract(o.owner, o.docId, false); }
  catch { box.innerHTML = ""; return; }
  if (!res.executed) { box.innerHTML = ""; return; }
  const seen = loadSeen(o.owner, o.docId);
  const cands: Cand[] = (res.candidates || []).filter((c) => !seen.has(candidateKey(c)));
  if (!cands.length) { box.innerHTML = ""; return; }
  // 구 서버의 미리보기에는 domain 이 없다. 그때는 직접 고른 주제가 있어야 반영한다.
  let hasTopic = !!res.domain;
  let topicUnknown = res.domain === undefined;
  let applying = false;
  // 이미 편집기가 같은 목록을 펼쳐 두고 있으면(자동저장이 또 돌았다) 손대지 않는다 —
  // 고치던 제목과 체크가 날아가면 그게 곧 "다시 권했다"다.
  const shown = box.dataset.cands || "";
  const key = cands.map(candidateKey).join("|");
  if (shown === key && box.childElementCount) return;
  box.dataset.cands = key;

  // 이미 있는 개념 — 서버가 반영할 때 쓸 id(`concept:<slug>`)로 묻는다.
  const exists = await Promise.all(cands.map((c) => c.slug
    ? api.nodeContext(o.owner, `concept:${c.slug}`, 1).then((r) => !!r.executed).catch(() => false)
    : Promise.resolve(false)));

  const collapsed = () => {
    box.innerHTML = `<div class="ex-bar">
        <span class="hint">${esc(A.exFound(cands.length))}</span>
        <button class="rail-x" type="button" data-ex="open">${esc(A.exReview)}</button>
      </div>`;
    box.querySelector('[data-ex="open"]')!.addEventListener("click", expanded);
  };
  const msg = (html: string) => {
    const m = box.querySelector<HTMLElement>(".ex-msg");
    if (m) m.innerHTML = html;
  };
  const checkedCount = () => box.querySelectorAll<HTMLInputElement>(".ex-check:checked").length;
  const syncApply = () => {
    const b = box.querySelector<HTMLButtonElement>('[data-ex="apply"]');
    if (b) {
      b.textContent = A.exApply(checkedCount());
      b.hidden = !!o.requireTopic && !hasTopic;
    }
  };

  function expanded() {
    box.innerHTML = `
      <section class="ex-list" aria-label="${esc(A.exFound(cands.length))}">
        <div class="ex-bar">
          <span class="hint">${esc(A.exFound(cands.length))}</span>
          <button class="rail-x" type="button" data-ex="close">${esc(A.exHide)}</button>
        </div>
        ${cands.map((c, i) => `
          <div class="ex-row">
            <input class="ex-check" type="checkbox" data-i="${i}" ${exists[i] || c.source === "body" ? "" : "checked"}
                   aria-label="${esc(c.title)}">
            <div class="ex-main">
              <input class="ex-title" type="text" data-i="${i}" value="${esc(c.title)}"
                     spellcheck="false" data-tip="${esc(A.exEditTip)}" aria-label="${esc(A.fTitle)}">
              ${c.one_liner ? `<p class="hint ex-ol">${esc(c.one_liner)}</p>` : ""}
              ${c.source === "body" && c.spans ? `<blockquote>${c.spans.map((s) =>
                `${esc(s.text)} (${esc(String(s.char_start))}:${esc(String(s.char_end))})`).join("<br>")}</blockquote>` : ""}
            </div>
            ${exists[i] ? `<span class="ex-badge">${esc(A.exExists)}</span>` : ""}
          </div>`).join("")}
        <p class="hint ex-rest">${esc(A.exRestSkipped)}</p>
        <div class="ex-acts">
          <button class="btn" type="button" data-ex="skip">${esc(A.exSkipAll)}</button>
          <button class="btn btn-primary" type="button" data-ex="apply"></button>
        </div>
        <div class="ex-msg" role="status"></div>
      </section>`;
    syncApply();
    box.querySelector('[data-ex="close"]')!.addEventListener("click", collapsed);
    box.querySelectorAll(".ex-check").forEach((c) => c.addEventListener("change", syncApply));
    box.querySelector('[data-ex="skip"]')!.addEventListener("click", () => {
      saveSeen(o.owner, o.docId, cands.map(candidateKey));
      box.innerHTML = ""; box.dataset.cands = "";
    });
    box.querySelector('[data-ex="apply"]')!.addEventListener("click", () => void apply());
    if (o.requireTopic && !hasTopic) askTopic();
    // Enter 로 끝난다(제목을 고치던 칸에서도).
    box.querySelector(".ex-list")!.addEventListener("keydown", (e) => {
      const k = e as KeyboardEvent;
      if (k.key === "Enter" && !k.isComposing && (k.target as HTMLElement).closest(".ex-title")) {
        k.preventDefault(); void apply();
      }
    });
  }

  async function apply(): Promise<void> {
    if (applying) return;
    if (o.requireTopic && !hasTopic) { askTopic(); return; }
    const picked = [...box.querySelectorAll<HTMLInputElement>(".ex-check:checked")]
      .map((c) => Number(c.dataset.i));
    if (!picked.length) { msg(esc(A.exNothingChecked)); return; }
    const chosen = picked.map((i) => ({
      ...cands[i],
      title: (box.querySelector<HTMLInputElement>(`.ex-title[data-i="${i}"]`)?.value || cands[i].title).trim(),
      one_liner: cands[i].one_liner || "",
    })).filter((c) => c.title);
    const acts = [...box.querySelectorAll<HTMLButtonElement>(".ex-acts button")];
    acts.forEach((b) => { b.disabled = true; });
    applying = true;
    msg(esc(A.exApplying));
    let r: Awaited<ReturnType<typeof api.docExtract>>;
    try { r = await api.docExtract(o.owner, o.docId, true, chosen,
      chosen.some((c) => c.source === "body") ? chosen.map((c) => c.candidate_id || "") : undefined); }
    catch (e: any) {
      applying = false;
      acts.forEach((b) => { b.disabled = false; });
      msg(`<span class="error-banner">${esc(A.exRefused)} ${esc(messageOf(e, ""))}</span>`);
      return;
    }
    applying = false;
    if (!r.executed) {
      acts.forEach((b) => { b.disabled = false; });
      // 주제가 없어서 거절됐다 — 실패로 끝내지 않고 주제를 먼저 묻는다. 판별은 서버 문장의
      // 머리("no topic")다: 화면에 싣지 않고 갈래를 가르는 데만 쓴다.
      if (/^no topic/i.test(String(r.error || ""))) {
        hasTopic = false; topicUnknown = false; syncApply(); askTopic(); return;
      }
      msg(`<span class="error-banner">${esc(A.exRefused)}</span>`
        + (r.error ? ` <span class="hint">${esc(String(r.error))}</span>` : ""));
      return;
    }
    // 반영한 것도, 체크하지 않고 둔 것도 다시 권하지 않는다(사람이 살펴봤다).
    saveSeen(o.owner, o.docId, cands.map(candidateKey));
    box.dataset.cands = "";
    box.innerHTML = `<p class="hint ex-done" role="status">${esc(A.exDone(
      r.applied?.created || 0, r.applied?.matched || 0, r.mentions || 0))}</p>`;
    forgetTopics();
    o.onApplied?.();
    void import("./lnb").then((m) => m.refreshLnb({ quiet: true }));
  }

  function askTopic(): void {
    msg("");                                   // "더하는 중…" 을 거둔다 — 지금은 주제를 묻는 중이다
    const acts = box.querySelector<HTMLElement>(".ex-acts");
    if (!acts || box.querySelector(".ex-topic")) return;
    const listId = `ex-topics-${++nextTopicList}`;
    acts.insertAdjacentHTML("beforebegin", `
      <div class="ex-topic">
        <p class="hint">${esc(topicUnknown ? A.needTopic : A.exNeedTopic)}</p>
        <div class="ex-topic-row">
          <input type="text" list="${listId}" autocomplete="off" spellcheck="false"
                 placeholder="${esc(A.topicHint)}" aria-label="${esc(A.topic)}">
          <datalist id="${listId}"></datalist>
          <button class="btn btn-primary" type="button">${esc(A.exSetTopic)}</button>
        </div>
      </div>`);
    const wrap = box.querySelector<HTMLElement>(".ex-topic")!;
    const input = wrap.querySelector<HTMLInputElement>("input")!;
    const button = wrap.querySelector<HTMLButtonElement>("button")!;
    void fillTopicList(wrap.querySelector<HTMLDataListElement>("datalist")!);
    const go = async () => {
      if (button.disabled) return;
      const t = input.value.trim();
      if (!t) { input.focus(); return; }
      button.disabled = true;
      if (!(await o.setTopic(t).catch(() => false))) {
        button.disabled = false;
        msg(`<span class="error-banner">${esc(A.exRefused)}</span>`); return;
      }
      if (!wrap.isConnected || !box.contains(wrap)) return;
      hasTopic = true; syncApply();
      wrap.remove();
      await apply();
    };
    button.addEventListener("click", () => void go());
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); e.stopPropagation(); void go(); }
    });
    input.focus();
  }

  collapsed();
}
