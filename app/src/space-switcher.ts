/** 사이드바 머리의 공간 전환. 사이드바 맨 위, 홈 · Agent 줄 위의 한 줄이 지금 공간의 표식 · 이름 ·
 *  종류를 말하고, 누르면 개인 · 팀 · 커뮤니티로 묶은 메뉴가 사이드바 안쪽 폭으로 내려온다.
 *  접힌 띠(좁은 창의 첫 화면도 이것이다)에서는 표식만 남고, 누르면 서랍을 펴지 않고 띠 오른쪽에
 *  같은 메뉴가 뜬다.
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

/** 앱의 의존. 보관과 팀 키는 고를 때만 쓰므로 그때 싣는다(사이드바가 그래프 모듈을 끌고 오지 않게). */
function appDeps(): SpaceDeps {
  return {
    cfg, teamsApi, communitiesApi, isCommunitySpace,
    retainBeforeSwitch: () => import("./view-retained").then((m) => m.retainBeforeSwitch()),
    ensureTeam: (team, name) => import("./team-key").then((m) => m.ensureTeamCredential(team, name)),
    reload: () => location.reload(),
  };
}

type Picker = ReturnType<typeof createSpacePicker>;
type Place = "below" | "right";

/** 공간 표식: 개인은 동그라미 안의 Vinc 고리, 팀은 네모 안의 첫 글자, 커뮤니티는 점선 동그라미 안의
 *  첫 글자. 색으로만 말하지 않는다: 모양이 종류를 가른다. */
function markHtml(kind: SpaceKind, name: string, size: "sm" | "md" = "md"): string {
  const inner = kind === "personal" ? icon("ring", size === "md" ? 16 : 14)
    : esc((Array.from(name.trim())[0] || "?").toUpperCase());
  return `<span class="space-mark space-mark-${kind} space-mark-${size}" aria-hidden="true">${inner}</span>`;
}

/** 화면이 부르는 이름. 목록도 남긴 이름도 없으면 종류의 이름으로 부른다. */
function nameOf(c: CurrentSpace): string {
  const U = COPY.ui.settingsUi;
  if (c.kind === "personal") return U.spacePersonal;
  return c.name || (c.kind === "team" ? U.spaceUnnamedTeam : U.spaceUnnamedCommunity);
}

/** 머리의 둘째 줄. 커뮤니티는 읽기 전용을 앞에 둔다: 폭이 모자라 말줄임이 되면 뒤의 종류가 잘리고,
 *  종류는 점선 표식도 말한다. */
function subLineHtml(c: CurrentSpace): string {
  const U = COPY.ui.settingsUi;
  if (c.kind === "personal") return esc(U.spacePersonalHint);
  if (c.kind === "team") return esc(U.spaceKindTeam);
  return `<span class="space-ro">${icon("lock", 12)}${esc(U.spaceReadOnly)}</span> · ${esc(U.spaceKindCommunity)}`;
}

let mounted = false;

/** 사이드바 맨 위에 머리를 세운다. 두 번 불러도 하나다. `deps` 는 dev 판(`app/dev/`)이 바꿔 끼운다. */
export function mountSpaceSwitcher(deps: SpaceDeps = appDeps()): void {
  const lnb = document.getElementById("lnb");
  if (mounted || !lnb) return;
  mounted = true;
  const U = COPY.ui.settingsUi;
  const picker: Picker = createSpacePicker(deps);
  lnb.insertAdjacentHTML("afterbegin", `<div class="space-head" id="space-head"></div>`);
  const head = document.getElementById("space-head")!;
  const here = (): SpaceKind => kindOf(deps.cfg.team, deps.isCommunitySpace);

  let menu: { el: HTMLElement; paint: () => void; close: () => void } | null = null;
  let query = "";
  let choosing = "";

  // 손잡이는 한 번 만들고 안만 다시 그린다: 메뉴가 열린 채 목록이 와도 메뉴가 붙은 손잡이가 그대로다.
  head.innerHTML = `<button type="button" class="space-switch" aria-haspopup="dialog" aria-expanded="false"
      data-tip="${esc(U.spaceSwitchTip)}"></button>`;
  const button = head.querySelector<HTMLButtonElement>(".space-switch")!;
  function paintHead(): void {
    const c = currentSpace(picker.model, deps.cfg.team, deps.cfg.teamName || "", deps.isCommunitySpace);
    const name = nameOf(c);
    button.setAttribute("aria-label", `${U.space}: ${name}`);
    button.innerHTML = `${markHtml(c.kind, name)}
      <span class="space-text"><span class="space-name">${esc(name)}</span><span class="space-sub">${subLineHtml(c)}</span></span>
      <span class="space-caret" aria-hidden="true">${icon("chevron-left", 14)}</span>`;
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
      ${markHtml(kind, name, "sm")}
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

  /** 펼친 사이드바면 머리 손잡이 바로 아래에 그 폭으로, 접힌 띠면 띠 오른쪽에 연다. */
  function placeMenu(el: HTMLElement, anchor: HTMLElement, place: Place): void {
    const a = anchor.getBoundingClientRect();
    const rem = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
    const gap = rem / 4, edge = rem / 2;
    const left = place === "right" ? a.right + gap : a.left;
    const width = Math.min(place === "right" ? 15 * rem : a.width, innerWidth - left - edge);
    const top = place === "right" ? a.top : a.bottom + gap;
    el.style.left = `${Math.round(left)}px`;
    el.style.width = `${Math.round(width)}px`;
    el.style.top = `${Math.round(top)}px`;
    el.style.maxHeight = `${Math.round(innerHeight - top - edge)}px`;
  }

  function openMenu(anchor: HTMLElement): void {
    const place: Place = document.getElementById("shell")?.dataset.lnb === "closed" ? "right" : "below";
    const el = document.createElement("div");
    el.className = `ctx-menu space-menu space-menu-${place}`;
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
    const reflow = () => placeMenu(el, anchor, place);
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
