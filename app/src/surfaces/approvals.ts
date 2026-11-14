/** 승인함: 사람의 답을 기다리는 실행을 보고 답하는 면. 첫 대상은 Android Chrome 폭(360·412)이고
 *  iPhone 도 같은 화면으로 받는다. 누르는 자리는 48px 이상이다.
 *
 *  주소: `#approvals`(목록) · `#approvals/<run>`(하나). 밖에서 오는 경로형 딥링크
 *  `/app/approvals/<run>` 은 부팅이 이 해시로 옮긴다(main.ts, `approvals.ts` `deepLinkTarget`).
 *
 *  카드 하나가 실행 하나다: 제목 · 역할 · 스페이스 · 단계 · 만료, 질문, 행동마다 호스트가 적은 이유를
 *  고정폭 블록에 **글자 그대로**(줄바꿈, 자르지 않음). 답은 승인 · 거절 · 답글만 보내기 셋이고 답글은
 *  앞의 둘에 붙일 수도 있다. 만료된 것은 답할 수 없고, 답한 것은 이 창에서 답한 상태로 남는다.
 *
 *  답은 웹(쿠키 세션)에서만 보낸다. 셸의 계정 모드는 키 통과로라 계정이 거절하므로 목록만 보이고
 *  웹으로 여는 길을 준다. 로컬 모드에는 계정의 실행이 없다.
 *  계획: `vinc.plan.agent-approvals-in-the-vinc-app-2026-11-07`, 실행 프로토콜: [[vinc.canon.mcp-contract]] */
import { api, cfg, WEB, accountMode, canWriteHere, remote, teamsApi } from "../api";
import { COPY } from "../copy";
import { esc } from "../components";
import { setTopbarLead } from "../topbar";
import { bindSurface } from "../surface-binding";
import { loaderRows } from "../vinc-loaders.js";
import {
  ANSWER_TEXT_MAX, answerBody, approvalHash, canSend, isExpired, minutesLeft,
  normalizeRuns, openCount, roleLabel, runOfHash, type Answer, type WaitingRun,
} from "../approvals";

/** 이 창에서 답한 것. 허브 목록에서는 빠지지만 사람이 결과를 볼 수 있게 남긴다. */
const answered = new Map<string, { run: WaitingRun; answer: Answer; at: number }>();
/** 쓰다 만 답글. 다시 그려도 잃지 않는다. */
const drafts = new Map<string, string>();
let gen = 0;
let ticker = 0;
/** 이 스페이스에서 답할 수 있는가(팀은 편집자 이상, 허브의 `run-answer` 등급과 같다). */
let mayAnswer = true;
/** 카드에 적는 스페이스 이름. 목록은 지금 스페이스의 것이라 한 번만 정한다. */
let spaceLabel = "";

/** 라우터가 이 면을 떠날 때 부른다. 만료 시계를 멈춘다. */
export function leaveApprovals(): void {
  gen++;
  clearInterval(ticker);
  ticker = 0;
}

const timeOf = (ms: number): string =>
  new Date(ms).toLocaleTimeString(cfg.lang === "ko" ? "ko-KR" : "en-US", { hour: "2-digit", minute: "2-digit" });

function cardHtml(run: WaitingRun, now: number, focus: boolean): string {
  const A = COPY.approvals;
  const done = answered.get(run.id);
  const expired = !done && isExpired(run, now);
  const state = done ? "answered" : expired ? "expired" : "open";
  const left = minutesLeft(run, now);
  const meta = [roleLabel(run.role), spaceLabel, run.step ? A.step(run.step) : ""].filter(Boolean);
  const title = run.title || run.task || run.run_id || run.id;
  const reqs = run.decision.requests.map((q) => `
    <div class="ap-req">
      <code class="ap-act">${esc(q.action)}</code>
      ${q.reason ? `<pre class="ap-reason">${esc(q.reason)}</pre>`
        : `<p class="hint ap-noreason">${esc(A.noReason)}</p>`}
    </div>`).join("");
  const canAnswer = state === "open" && WEB && mayAnswer;
  const draft = drafts.get(run.id) || "";
  return `<article class="ap-card" data-id="${esc(run.id)}" data-state="${state}">
    <header class="ap-head">
      ${focus ? `<h2 class="ap-title">${esc(title)}</h2>`
        : `<h2 class="ap-title"><a href="${esc(approvalHash(run.id))}">${esc(title)}</a></h2>`}
      <span class="badge ap-kind">${esc(run.decision.kind === "ask_first" ? A.askFirst : A.personOnly)}</span>
    </header>
    <p class="ap-meta">${meta.map(esc).join('<span aria-hidden="true"> · </span>')}${
      state === "open" && left !== null ? `<span aria-hidden="true"> · </span><span class="ap-left">${esc(A.expiresIn(left))}</span>` : ""}</p>
    ${run.decision.question ? `<p class="ap-question">${esc(run.decision.question)}</p>` : ""}
    ${reqs ? `<div class="ap-reqs">${reqs}</div>` : ""}
    ${state === "expired" ? `<p class="ap-state ap-expired"><strong>${esc(A.expired)}</strong> ${esc(A.expiredNote)}</p>` : ""}
    ${done ? `<p class="ap-state ap-done" role="status">${esc(A.answered(done.answer, timeOf(done.at)))}</p>` : ""}
    ${canAnswer ? `
      <label class="ei-field ap-replyfield"><span>${esc(A.reply)}</span>
        <textarea class="ap-text" rows="2" maxlength="${ANSWER_TEXT_MAX}" placeholder="${esc(A.replyPh)}">${esc(draft)}</textarea>
      </label>
      <div class="ap-acts">
        <button class="btn btn-primary" type="button" data-answer="approve">${esc(A.approve)}</button>
        <button class="btn" type="button" data-answer="decline">${esc(A.decline)}</button>
        <button class="btn btn-quiet" type="button" data-answer="reply"${canSend("reply", draft) ? "" : " disabled"}>${esc(A.replyOnly)}</button>
      </div>
      <p class="hint ap-hint">${esc(A.replyHint)}</p>
      <p class="ap-msg" role="alert" hidden></p>` : ""}
    ${state === "open" && WEB && !mayAnswer ? `<p class="hint ap-hint">${esc(A.answerError("team_role_required"))}</p>` : ""}
    ${state === "open" && !WEB && accountMode() ? `
      <p class="hint ap-hint">${esc(A.accountOnly)}</p>
      <button class="btn" type="button" data-web="${esc(run.id)}">${esc(A.openWeb)}</button>` : ""}
  </article>`;
}

export async function renderApprovals(el: HTMLElement): Promise<void> {
  leaveApprovals();
  const my = ++gen;
  const live = () => my === gen && el.isConnected;
  const A = COPY.approvals;
  const focus = runOfHash(location.hash);
  setTopbarLead(`<span class="tb-lead-name">${esc(A.title)}</span>`);
  el.innerHTML = `<div class="approvals">${loaderRows(COPY.ui.common.loading, { count: 3 })}</div>`;
  const root = el.firstElementChild as HTMLElement;
  if (!remote()) {
    root.innerHTML = `<p class="hint">${esc(A.localMode)}</p>`;
    return;
  }

  let runs: WaitingRun[] = [];
  const load = async (): Promise<boolean> => {
    try {
      runs = normalizeRuns(await api.runsWaiting());
      paintBadge(openCount(runs, Date.now()));
      return true;
    } catch { return false; }
  };

  const draw = () => {
    if (!live()) return;
    const now = Date.now();
    const shown = new Map(runs.map((r) => [r.id, r]));
    // 이 창에서 답한 것은 목록에서 빠져도 남긴다(맨 위, 방금 한 일이 보이게).
    for (const a of answered.values()) if (!shown.has(a.run.id)) shown.set(a.run.id, a.run);
    const list = focus ? [...shown.values()].filter((r) => r.id === focus || r.run_id === focus)
      : [...answered.values()].map((a) => shown.get(a.run.id)!).concat(runs.filter((r) => !answered.has(r.id)));
    const open = openCount(runs.filter((r) => !answered.has(r.id)), now);
    // 이름과 수는 뷰 머리 앞자리가 갖는다. 폰 폭에서 같은 제목을 본문에 한 번 더 세우면 첫 카드가 밀려난다.
    setTopbarLead(`<span class="tb-lead-name">${esc(A.title)}</span>${
      open ? `<span class="tb-lead-sep" aria-hidden="true">·</span><span class="tb-lead-scope">${open}</span>` : ""}`);
    const head = focus ? `<a class="home-back ap-back" href="${esc(approvalHash())}">‹ ${esc(A.all)}</a>` : "";
    const body = list.length ? list.map((r) => cardHtml(r, now, !!focus)).join("")
      : `<p class="hint ap-empty">${esc(focus ? A.gone : A.empty)}</p>`;
    root.innerHTML = head + `<div class="ap-list">${body}</div>`;
  };

  const fail = () => {
    if (!live()) return;
    root.innerHTML = `<p class="hint">${esc(A.loadFailed)}</p>
      <button class="btn btn-quiet" type="button" id="ap-retry">${esc(A.retry)}</button>`;
    root.querySelector("#ap-retry")?.addEventListener("click", () => void start());
  };

  const start = async () => {
    // 팀의 열람자에게는 답 손잡이를 내지 않는다. 눌러도 계정이 401(team_role_required)로 거절하고,
    // 웹의 401 은 로그인 화면으로 보낸다(api.ts localCall).
    mayAnswer = await canWriteHere();
    const U = COPY.ui.settingsUi;
    spaceLabel = !cfg.team ? U.spacePersonal
      : (await teamsApi.list().catch(() => [])).find((t) => t.id === cfg.team)?.name || U.spaceTeams;
    if (!(await load())) return fail();
    draw();
  };

  // 카드 안의 손잡이는 위임 하나로 받는다(다시 그려도 배선이 남는다).
  root.addEventListener("input", (e) => {
    const t = e.target as HTMLElement;
    if (!t.classList.contains("ap-text")) return;
    const card = t.closest<HTMLElement>(".ap-card");
    if (!card) return;
    const v = (t as HTMLTextAreaElement).value;
    drafts.set(card.dataset.id!, v);
    const rb = card.querySelector<HTMLButtonElement>('[data-answer="reply"]');
    if (rb) rb.disabled = !canSend("reply", v);
  });
  root.addEventListener("click", (e) => {
    const t = e.target as HTMLElement;
    const web = t.closest<HTMLElement>("[data-web]");
    if (web) {
      // 웹 앱의 경로형 딥링크(`deepLinkPath` 와 같은 모양). 계정 그래프가 같으므로 같은 실행이 열린다.
      const url = "https://vincs.io/app/approvals/" + encodeURIComponent(web.dataset.web!);
      void import("../account-connect").then((m) => m.openBrowser(url));
      return;
    }
    const b = t.closest<HTMLButtonElement>("button[data-answer]");
    const card = b?.closest<HTMLElement>(".ap-card");
    if (!b || !card) return;
    const run = runs.find((r) => r.id === card.dataset.id);
    if (!run) return;
    void send(card, run, b.dataset.answer as Answer);
  });

  const send = async (card: HTMLElement, run: WaitingRun, answer: Answer) => {
    const text = drafts.get(run.id) || "";
    const msg = card.querySelector<HTMLElement>(".ap-msg");
    if (!canSend(answer, text) || isExpired(run, Date.now())) return;
    const buttons = [...card.querySelectorAll<HTMLButtonElement>("button[data-answer]")];
    const label = buttons.find((x) => x.dataset.answer === answer);
    const was = label?.textContent || "";
    buttons.forEach((x) => { x.disabled = true; });
    if (label) label.textContent = A.sending;
    if (msg) msg.hidden = true;
    let error = "";
    try {
      const r: any = await api.runAnswer(answerBody(run, answer, text));
      if (r?.executed) {
        answered.set(run.id, { run, answer, at: Date.parse(r.answered_at) || Date.now() });
        drafts.delete(run.id);
      } else error = String(r?.error || "");
    } catch (err: any) {
      // 계정의 오류 봉투(`{error:{code, reason}}`)는 사유가 코드보다 자세하다.
      error = String(err?.code || "");
      try { const m = /\{.*\}/s.exec(String(err?.message || "")); const b = m ? JSON.parse(m[0]) : null;
        error = String(b?.error?.reason || b?.error?.code || error); } catch { /* 코드만 쓴다 */ }
    }
    if (!live()) return;
    if (!error) {
      await load();
      draw();
      return;
    }
    // 더는 열려 있지 않은 질문이면 목록을 새로 읽는다. 문장은 남겨 둔다.
    if (error === "already_answered" || error === "request_expired" || error === "run_not_found") {
      await load();
      if (error === "request_expired") runs = runs.map((r) => (r.id === run.id ? { ...r, expired: true } : r));
      draw();
    } else {
      buttons.forEach((x) => { x.disabled = x.dataset.answer === "reply" && !canSend("reply", text); });
      if (label) label.textContent = was;
    }
    const now = root.querySelector<HTMLElement>(`.ap-card[data-id="${CSS.escape(run.id)}"]`);
    const box = (now || card).querySelector<HTMLElement>(".ap-msg")
      || (now || card).appendChild(Object.assign(document.createElement("p"), { className: "ap-msg" }));
    box.setAttribute("role", "alert");
    box.textContent = A.answerError(error);
    box.hidden = false;
  };

  // 저절로 새로워지는 갱신(VIN-129). 답글을 쓰는 중이면 live.ts 가 기다린다.
  bindSurface({ liveRefresher: async () => { if (live() && (await load())) draw(); } });
  // 만료 시계: 남은 분을 고치고, 막 만료된 카드는 답할 수 없는 모습으로 바꾼다. 쓰던 답글은 건드리지 않는다.
  ticker = window.setInterval(() => {
    if (!live()) { leaveApprovals(); return; }
    const now = Date.now();
    for (const card of root.querySelectorAll<HTMLElement>('.ap-card[data-state="open"]')) {
      const run = runs.find((r) => r.id === card.dataset.id);
      if (!run) continue;
      if (isExpired(run, now)) { card.outerHTML = cardHtml(run, now, !!focus); continue; }
      const left = minutesLeft(run, now);
      const span = card.querySelector(".ap-left");
      if (span && left !== null) span.textContent = A.expiresIn(left);
    }
  }, 30_000);
  await start();
}

/** 메뉴의 숫자(열린 대기 수). 0 이면 숨긴다. */
function paintBadge(n: number): void {
  const el = document.getElementById("rail-approvals-badge");
  if (!el) return;
  el.textContent = n > 0 ? String(n) : "";
  el.hidden = n === 0;
  document.getElementById("rail-approvals")?.setAttribute("aria-label",
    n > 0 ? `${COPY.approvals.nav} (${n})` : COPY.approvals.nav);
}

/** 메뉴의 숫자를 새로 읽는다. 계정 그래프가 없으면(로컬 모드) 묻지 않는다. 실패는 조용히 넘긴다. */
export async function refreshApprovalsBadge(): Promise<void> {
  if (!remote()) return;
  try { paintBadge(openCount(normalizeRuns(await api.runsWaiting()), Date.now())); } catch { /* 숫자만 못 고친다 */ }
}
