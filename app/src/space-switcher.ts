/** 뷰 머리 맨 앞의 공간 전환. 머리의 첫 칸(`#tb-space`)이 지금 공간의 이름과 캐럿을 **글자로만**
 *  말하고(표식은 없다, 커뮤니티는 자물쇠와 "읽기 전용"이 이름 옆에 선다), 누르면 개인 · 팀 · 커뮤니티로
 *  묶은 메뉴가 단추 바로 아래에 15rem 이상의 폭으로 내려온다. 종류는 메뉴의 묶음 이름이 말한다.
 *  사이드바가 접혀 있어도, 좁은 창이어도 자리가 같다(머리는 늘 이름을 말한다).
 *
 *  계정 그래프(웹 · 셸의 계정 모드)에서만 선다: 로컬 모드에는 고를 공간이 없다. 부르는 쪽은 부팅이
 *  모드와 스페이스를 정한 뒤다(`main.ts`). 규칙(고를 수 있는 것 · 전환 순서)은 전부
 *  `createSpacePicker` 가 쥐고, 여기는 그리기와 손잡이만 한다. */
import { cfg, communitiesApi, isCommunitySpace, teamsApi } from "./api";
import { COPY } from "./copy";
import { esc } from "./components";
import { icon } from "./icons";
import { createSpacePicker, currentSpace, kindOf, menuView, type CurrentSpace, type SpaceDeps,
         type SpaceKind, type SpaceRow } from "./space-picker";

/** 앱의 의존. 보관과 팀 키는 고르는 순간에만 싣는다(머리가 그래프 모듈을 끌고 오지 않게). */
function appDeps(): SpaceDeps {
  return {
    cfg, teamsApi, communitiesApi, isCommunitySpace,
    retainBeforeSwitch: () => import("./view-retained").then((m) => m.retainBeforeSwitch()),
    ensureTeam: (team, name) => import("./team-key").then((m) => m.ensureTeamCredential(team, name)),
    reload: () => location.reload(),
  };
}

type Picker = ReturnType<typeof createSpacePicker>;

/** 이보다 좁게 잘린 이름은 읽을 수 없다고 본다(글자 크기 기준, 13px 에서 65px · 라틴 글자 여덟쯤). */
const READABLE_EM = 5;

/** 화면이 부르는 이름. 목록도 남긴 이름도 없으면 종류의 이름으로 부른다. */
function nameOf(c: CurrentSpace): string {
  const U = COPY.ui.settingsUi;
  if (c.kind === "personal") return U.spacePersonal;
  return c.name || (c.kind === "team" ? U.spaceUnnamedTeam : U.spaceUnnamedCommunity);
}

let mounted = false;

/** 머리 맨 앞 칸에 전환을 세운다. 두 번 불러도 하나다. `deps` 는 dev 판(`app/dev/`)이 바꿔 끼운다. */
export function mountSpaceSwitcher(deps: SpaceDeps = appDeps()): void {
  const slot = document.getElementById("tb-space");
  if (mounted || !slot) return;
  const head: HTMLElement = slot;
  mounted = true;
  const U = COPY.ui.settingsUi;
  const picker: Picker = createSpacePicker(deps);
  const here = (): SpaceKind => kindOf(deps.cfg.team, deps.isCommunitySpace);

  let menu: { el: HTMLElement; paint: () => void; close: () => void } | null = null;
  let query = "";
  let choosing = "";

  // 손잡이는 한 번 만들고 안만 다시 그린다: 메뉴가 열린 채 목록이 와도 메뉴가 붙은 손잡이가 그대로다.
  // 뒤의 세로선은 지금 자리(앞자리)와 가르는 장식이다.
  head.innerHTML = `<button type="button" class="space-switch" aria-haspopup="dialog" aria-expanded="false"
      data-tip="${esc(U.spaceSwitchTip)}"></button><span class="tb-space-sep" aria-hidden="true"></span>`;
  const button = head.querySelector<HTMLButtonElement>(".space-switch")!;
  function paintHead(): void {
    const c = currentSpace(picker.model, deps.cfg.team, deps.cfg.teamName || "", deps.isCommunitySpace);
    const name = nameOf(c);
    // 커뮤니티는 이름 옆에 읽기 전용을 말한다(이름이 말줄임으로 줄어도 이 표시는 남는다).
    const ro = c.readOnly ? `<span class="space-ro">${icon("lock", 12)}<span class="space-ro-text">${esc(U.spaceReadOnly)}</span></span>` : "";
    button.setAttribute("aria-label", `${U.space}: ${name}${c.readOnly ? ` · ${U.spaceReadOnly}` : ""}`);
    button.innerHTML = `<span class="space-name">${esc(name)}</span>${ro}
      <span class="space-caret" aria-hidden="true">${icon("chevron-left", 14)}</span>`;
    fit();
  }

  /** 좁은 머리의 마지막 수단 둘. 잘린 이름이 `READABLE_EM` 보다 좁으면 먼저 구분선과 앞자리가 비키고
   *  (`#tb-space[data-tight]`), 그래도 좁으면 커뮤니티의 "읽기 전용" 글자가 비켜 자물쇠만 남는다
   *  (`[data-ro-short]`; 단추의 이름표와 메뉴 줄은 글자를 그대로 말한다). 매번 표시를 전부 지우고 처음부터
   *  잰다: 비킨 상태에서 재면 이름이 넓어 보여 펴고 접기를 오간다. 자리가 다시 나면 글자부터 돌아온다. */
  function fit(): void {
    head.removeAttribute("data-tight");
    head.removeAttribute("data-ro-short");
    const n = button.querySelector<HTMLElement>(".space-name");
    if (!n) return;
    const em = parseFloat(getComputedStyle(n).fontSize) || 13;
    const unreadable = () => n.scrollWidth > n.clientWidth + 1 && n.clientWidth < READABLE_EM * em;
    if (!unreadable()) return;
    head.setAttribute("data-tight", "");
    if (unreadable() && button.querySelector(".space-ro-text")) head.setAttribute("data-ro-short", "");
  }
  // 머리 폭이 바뀌거나(창 · 사이드바) 뒤 묶음이 바뀌면(그래프의 시간 · 범례) 다시 잰다.
  if (typeof ResizeObserver !== "undefined") {
    const ro = new ResizeObserver(() => fit());
    for (const el of [document.getElementById("topbar"), document.querySelector(".tb-trail")]) if (el) ro.observe(el);
  }
  // 웹 글꼴(swap)이 늦게 오면 머리 폭은 그대로인데 이름 폭만 바뀐다: 글꼴이 자리 잡을 때도 다시 잰다.
  if ("fonts" in document) {
    void document.fonts.ready.then(() => fit());
    document.fonts.addEventListener("loadingdone", () => fit());
  }

  function rowHtml(kind: SpaceKind, r: SpaceRow): string {
    const name = kind === "personal" ? U.spacePersonal : r.name;
    const off = picker.model.busy || r.state !== "active";
    const opening = choosing === r.id && picker.model.busy;
    const sub = opening ? U.spaceOpening
      : kind === "personal" ? U.spacePersonalHint
      : r.state === "pending" ? U.spacePending : r.state === "suspended" ? U.spaceSuspended
      : kind === "community" ? U.spaceReadOnly : "";
    const lock = kind === "community" && !opening ? icon("lock", 12) : "";
    return `<button type="button" class="ctx-item space-row${r.state !== "active" ? " space-row-off" : ""}" role="radio"
        aria-checked="${r.current}" data-kind="${kind}" data-space="${esc(r.id)}"${off ? ' aria-disabled="true"' : ""}>
      <span class="space-row-text"><span class="space-row-name">${esc(name)}</span>${
        sub ? `<span class="space-row-sub">${lock}${esc(sub)}</span>` : ""}</span>
      <span class="space-row-end">${r.current ? `${icon("check", 14)}<span class="space-vh">${esc(U.spaceInUse)}</span>` : ""}</span>
    </button>`;
  }

  function menuHtml(): string {
    const v = menuView(picker.model, query, U.spacePersonal, here());
    const label = (s: string) => `<div class="lnb-sec-label space-group-label">${esc(s)}</div>`;
    const note = (s: string) => `<p class="space-note">${esc(s)}</p>`;
    const group = (name: string, inner: string, radio = true) =>
      `<div class="space-group"${radio ? ` role="radiogroup" aria-label="${esc(name)}"` : ""}>${inner}</div>`;
    let body = "";
    if (v.personal) body += group(U.spacePersonal, picker.rows("personal").map((r) => rowHtml("personal", r)).join(""));
    if (v.teams.state === "failed")
      body += group(U.spaceTeams, label(U.spaceTeams) + note(U.spaceListFailed)
        + `<button type="button" class="btn btn-quiet space-retry">${esc(COPY.ui.lnb.retry)}</button>`, false);
    else if (v.teams.state === "loading") body += group(U.spaceTeams, label(U.spaceTeams) + note(COPY.ui.lnb.loading), false);
    else if (v.teams.state === "none") body += group(U.spaceTeams, label(U.spaceTeams) + note(U.spaceNone), false);
    else if (v.teams.rows.length)
      body += group(U.spaceTeams, label(U.spaceTeams) + v.teams.rows.map((r) => rowHtml("team", r)).join(""));
    if (v.communities.length)
      body += group(U.spaceCommunities, label(U.spaceCommunities) + v.communities.map((r) => rowHtml("community", r)).join(""));
    if (v.empty) body += `<p class="space-note space-nomatch">${esc(U.spaceNoMatch)}</p>`;
    const foot = v.foot === "readOnly" ? U.spaceCommunityReadOnly : v.foot === "activation" ? U.spaceActivationHint : "";
    const find = v.find ? `<div class="space-find">${icon("search", 14)}<input class="space-find-input" type="search"
        placeholder="${esc(U.spaceFind)}" aria-label="${esc(U.spaceFind)}" value="${esc(query)}" autocomplete="off" spellcheck="false" /></div>` : "";
    return `${find}<div class="space-list">${body}</div>${foot ? `<p class="space-foot">${esc(foot)}</p>` : ""}`;
  }

  /** 단추 바로 아래, 왼쪽 끝을 맞춰 연다. 폭은 15rem 이상(단추가 더 넓으면 단추 폭)이고 창 안에 머문다:
   *  오른쪽이 넘치면 왼쪽으로 당기고, 창보다 넓을 수는 없다. */
  function placeMenu(el: HTMLElement, anchor: HTMLElement): void {
    const a = anchor.getBoundingClientRect();
    const rem = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
    const gap = rem / 4, edge = rem / 2;
    const width = Math.min(Math.max(15 * rem, a.width), innerWidth - edge * 2);
    const left = Math.max(edge, Math.min(a.left, innerWidth - edge - width));
    const top = a.bottom + gap;
    el.style.left = `${Math.round(left)}px`;
    el.style.width = `${Math.round(width)}px`;
    el.style.top = `${Math.round(top)}px`;
    el.style.maxHeight = `${Math.round(innerHeight - top - edge)}px`;
  }

  function openMenu(anchor: HTMLElement): void {
    const el = document.createElement("div");
    // `ctx-menu` 를 달지 않는다: `openContextMenu`(components.ts)가 그 클래스를 전부 지워서, 달면 이 메뉴의
    // 요소만 사라지고 문서에 건 듣기는 남는다. 판의 모양은 CSS 가 `.ctx-menu, .space-menu` 로 나눠 쓴다.
    el.className = "space-menu";
    el.setAttribute("role", "dialog");
    el.setAttribute("aria-label", U.space);
    const paint = () => {
      const typing = document.activeElement?.classList.contains("space-find-input");
      el.innerHTML = menuHtml();
      if (typing) {
        const i = el.querySelector<HTMLInputElement>(".space-find-input");
        i?.focus();
        i?.setSelectionRange(query.length, query.length);
      }
    };
    const reflow = () => placeMenu(el, anchor);
    const close = (refocus = false) => {
      el.remove();
      menu = null;
      query = "";
      anchor.setAttribute("aria-expanded", "false");
      document.removeEventListener("pointerdown", onOut, true);
      document.removeEventListener("keydown", onKey, true);
      removeEventListener("resize", reflow);
      if (refocus) anchor.focus();
    };
    const onOut = (e: PointerEvent) => {
      const t = e.target as Node;
      if (!el.contains(t) && !head.contains(t)) close();
    };
    const stops = () => [...el.querySelectorAll<HTMLElement>(".space-find-input, .space-row, .space-retry")];
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.stopPropagation(); close(true); return; }
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      const list = stops(), i = list.indexOf(document.activeElement as HTMLElement);
      const next = list[(i + (e.key === "ArrowDown" ? 1 : -1) + list.length) % list.length];
      if (next) { e.preventDefault(); next.focus(); }
    };
    paint();
    document.body.appendChild(el);
    reflow();
    anchor.setAttribute("aria-expanded", "true");
    document.addEventListener("pointerdown", onOut, true);
    document.addEventListener("keydown", onKey, true);
    addEventListener("resize", reflow);
    el.addEventListener("input", (e) => {
      const t = e.target as HTMLInputElement;
      if (!t.classList.contains("space-find-input")) return;
      query = t.value;
      paint();
    });
    el.addEventListener("click", (e) => {
      const t = e.target as HTMLElement;
      if (t.closest(".space-retry")) {
        picker.model.teams = { load: "loading" };
        paint();
        void picker.load().then(() => { paintHead(); menu?.paint(); });
        return;
      }
      const row = t.closest<HTMLElement>(".space-row");
      if (!row || row.getAttribute("aria-disabled") === "true") return;
      picker.setKind(row.dataset.kind as SpaceKind);
      choosing = row.dataset.space || "";
      // 이미 연 공간이거나 팀 키를 받지 못했으면 거짓이다. 그때는 메뉴만 닫는다.
      void picker.choose(choosing, paint).then((ok) => { if (!ok) close(true); });
    });
    menu = { el, paint, close };
    // 찾기 칸은 손가락 기기에서 바로 잡지 않는다: 잡으면 화면 키보드가 메뉴를 가린다.
    const find = matchMedia("(pointer: fine)").matches ? el.querySelector<HTMLElement>(".space-find-input") : null;
    const first = find || el.querySelector<HTMLElement>('.space-row[aria-checked="true"]');
    first?.focus({ preventScroll: true });
  }

  button.addEventListener("click", () => {
    if (menu) menu.close();
    else openMenu(button);
  });

  paintHead();
  // 목록이 오면 이름을 다시 말한다(남긴 이름보다 받은 이름이 새롭다). 열린 메뉴도 다시 그린다.
  void picker.load().then(() => { paintHead(); menu?.paint(); });
}
