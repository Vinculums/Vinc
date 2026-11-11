/** 뷰 머리 맨 앞의 공간 전환을 백엔드 없이 띄우는 손 확인 판. CI 는 돌리지 않는다.
 *
 *  셸은 진짜다: `index.html` 의 `#shell` 을 그대로 꺼내 세우고 앱의 `mountTopbar` · `mountLnb` ·
 *  `mountSpaceSwitcher` 를 부른다. 바꿔 끼우는 것은 백엔드뿐이다(`api.navTree` · `fetch` · 팀과
 *  커뮤니티 목록). localhost 는 계정 그래프가 아니라 `remote()` 가 거짓이므로, 머리는 앱의 부팅이
 *  아니라 이 판이 세운다. `state=local` 은 부팅과 같은 조건(`remote()`)을 그대로 따라 머리가 없다.
 *
 *      npm run dev  →  /dev/space-switcher.html?state=menu&theme=dark&lang=ko
 *
 *  state: personal · team · community · menu · many · failed · collapsed · local. `open=1` 이면 메뉴를 연다.
 *  `collapsed` 는 접힌 사이드바 띠 옆에서도 머리가 공간 이름을 말하는지 본다(띠에는 공간 표시가 없다).
 *  `skin=web` 은 웹 토큰 세트, `view=graph` 는 그래프 면의 머리(앞자리 · 시간 · 범례)로 본다.
 *  `space=t-m5` 와 `state=many` 를 함께 주면 긴 이름이다. */
import "../src/app.css";
import { api, cfg, isCommunitySpace, remote } from "../src/api";
import type { NavTreeRes } from "../src/api";
import { COPY } from "../src/copy";
import { esc } from "../src/components";
import { mountLnb } from "../src/lnb";
import { mountSpaceSwitcher } from "../src/space-switcher";
import { kindOf } from "../src/space-picker";
import { mountTopbar, setTopbarLead, setTopbarTools } from "../src/topbar";

type Team = { id: string; name: string; role: string; status: string };
const TEAMS: Team[] = [
  { id: "t-acme", name: "Acme Research", role: "editor", status: "active" },
  { id: "t-field", name: "Field Notes", role: "viewer", status: "active" },
  { id: "t-seoul", name: "서울 도시 연구회", role: "admin", status: "active" },
  { id: "t-guild", name: "Design Guild", role: "viewer", status: "pending" },
  { id: "t-lab", name: "Old Lab", role: "viewer", status: "suspended" },
  { id: "t-gone", name: "Deleted Team", role: "viewer", status: "deleted" },
];
const MANY_NAMES = ["Acme Research", "Field Notes", "서울 도시 연구회", "Design Guild", "Old Lab",
  "Platform Reliability and Incident Review", "Billing", "Customer Interviews", "Data Science Reading Group",
  "Docs Crew", "Growth", "Hiring Loop", "Infra On Call", "Legal and Privacy", "Mobile", "Onboarding",
  "Partnerships", "Pricing Lab", "Quality", "Release Train", "Search Quality", "Support Escalations", "Writers Room"];
const MANY: Team[] = MANY_NAMES.map((name, i) => ({ id: i === 0 ? "t-acme" : `t-m${i}`, name, role: "viewer",
  status: name === "Design Guild" ? "pending" : name === "Old Lab" ? "suspended" : "active" }));
const COMMUNITIES = [
  { id: "community:c-physics", name: "Open Physics", role: "member" },
  { id: "community:c-lit", name: "한국 문학 읽기", role: "member" },
];

const STATES = ["personal", "team", "community", "menu", "many", "failed", "collapsed", "local"] as const;
type State = typeof STATES[number];
const params = new URLSearchParams(location.search);
const state: State = (STATES as readonly string[]).includes(params.get("state") || "") ? params.get("state") as State : "team";
// 메뉴를 처음부터 연다. `open=0` 이면 열지 않는다(스크린숏 스크립트가 포인터로 누를 때).
const openAtStart = params.get("open") === "1" || (params.get("open") !== "0" && ["menu", "many", "failed"].includes(state));

// 처음 공간. 전환한 뒤에는 주소의 `space` 가 이긴다.
let team = params.has("space") ? (params.get("space") === "personal" ? "" : params.get("space") || "")
  : state === "personal" || state === "local" ? "" : state === "community" ? "community:c-physics" : "t-acme";
// 목록을 못 받는 상태에서도 머리가 이름을 말하는지 보려고, 실패 상태는 고를 때 남긴 이름을 미리 둔다.
// 전환 뒤의 다시 열기를 건너 이름이 남는지 보려고, 남긴 이름은 이 탭의 sessionStorage 에 둔다(앱은 localStorage).
const NAME_KEY = "dev.space-switcher.name";
let teamName = state === "failed" ? "Acme Research" : params.has("space") ? sessionStorage.getItem(NAME_KEY) || "" : "";
if (state !== "local") {
  Object.defineProperty(cfg, "team", { configurable: true, get: () => team, set: (v: string) => { if (v !== team) teamName = ""; team = v || ""; } });
  Object.defineProperty(cfg, "teamName", { configurable: true, get: () => (team ? teamName : ""),
    set: (v: string) => { teamName = v || ""; sessionStorage.setItem(NAME_KEY, teamName); } });
}

type Doc = [title: string, depth?: number];
function group(domain: string, docs: Doc[]) {
  return { domain, concepts: docs.length, docs: docs.map(([title, depth = 0], i) => ({
    doc_id: `doc:${domain}/${i}`, title, updated_at: "", node_id: `concept:${domain.replace(/\//g, ".")}.${i}`,
    node_title: title, depth })) };
}
const TREES: Record<"personal" | "team" | "community", NavTreeRes> = {
  personal: { executed: true, unfiled: [], domains: [
    group("notes/reading", [["Books this year"], ["How to read a paper", 1]]),
    group("notes/projects", [["Garden plan"], ["Kitchen remodel"]]),
  ] },
  team: { executed: true, unfiled: [], domains: [
    group("research/experiments", [["Protocol template"], ["Run log March"], ["Calibration", 1]]),
    group("research/papers", [["Reading list"], ["Draft: sensor drift"]]),
  ] },
  community: { executed: true, unfiled: [], domains: [
    group("physics/mechanics", [["Newton's laws"], ["Conservation of energy", 1]]),
    group("physics/optics", [["Refraction"], ["Thin lenses"]]),
  ] },
};

async function main(): Promise<void> {
  cfg.lang = params.get("lang") === "ko" ? "ko" : "en";
  document.documentElement.lang = cfg.lang;
  // `skin=web` 이면 웹 토큰 세트로 본다(웹 Vinc 도 같은 머리를 쓴다).
  document.documentElement.dataset.skin = params.get("skin") === "web" ? "web" : "desktop";
  document.documentElement.dataset.theme = params.get("theme") === "light" ? "light" : "dark";
  location.hash = "#home";
  try { localStorage.setItem("vinc.shell.lnb", state === "collapsed" ? "closed" : "open"); } catch { /* 저장소가 없으면 기본(열림)이다 */ }

  const realFetch = window.fetch.bind(window);
  const page = await (await realFetch("/index.html")).text();
  const shell = new DOMParser().parseFromString(page, "text/html").getElementById("shell")!;
  document.body.prepend(document.importNode(shell, true));
  window.fetch = async () => new Response(JSON.stringify({ executed: false }), { status: 200, headers: { "content-type": "application/json" } });
  (api as unknown as { navTree: () => Promise<NavTreeRes> }).navTree = async () => TREES[kindOf(cfg.team, isCommunitySpace)];

  mountTopbar();
  mountLnb();
  setTopbarLead(`<span class="tb-lead-name">${esc(COPY.ui.home.nav)}</span>`);
  // `view=graph`: 그래프 면의 머리를 흉내 낸다(앞자리 "그래프 · 범위", 전환 왼쪽의 시간 · 범례 단추).
  // 단추 모양은 나침반의 TOOLS_HTML 과 같은 클래스다. 창은 열지 않는다(나침반이 없다).
  if (params.get("view") === "graph") {
    document.getElementById("shell")!.dataset.surface = "view";
    setTopbarLead(`<span class="tb-lead-name">${esc(COPY.ui.topbar.graph)}</span><span class="tb-lead-sep" aria-hidden="true">·</span><span class="tb-lead-scope">research / experiments</span>`);
    const tools = document.createElement("span");
    tools.className = "cmp-tools";
    tools.innerHTML = [COPY.ui.compass.time, COPY.ui.compass.legend]
      .map((n) => `<button class="cmp-tbtn" type="button" aria-expanded="false">${esc(n)}</button>`).join("");
    setTopbarTools(tools);
    document.getElementById("tb-to-docs")?.setAttribute("aria-pressed", "false");
    document.getElementById("tb-to-graph")?.setAttribute("aria-pressed", "true");
  }
  // 좁은 창은 서랍이 닫힌 채 시작한다(첫 화면 = 접힌 띠). 공간 전환은 머리에 있으므로 서랍을 펴지 않는다.

  // 부팅과 같은 조건이다(`main.ts`). localhost 에서는 거짓이라 `local` 은 여기서 멈춘다.
  if (state === "local") { if (remote()) mountSpaceSwitcher(); document.body.dataset.ready = "1"; return; }
  let attempts = 0;
  mountSpaceSwitcher({
    cfg, isCommunitySpace,
    teamsApi: { list: async () => {
      // 실패 상태: 첫 요청만 실패한다(다시 시도하면 받아진다).
      if (state === "failed" && attempts++ === 0) throw new Error("offline");
      return state === "many" ? MANY : TEAMS;
    } },
    communitiesApi: { list: async () => COMMUNITIES },
    retainBeforeSwitch: () => new Promise((r) => setTimeout(r, 400)),
    reload: () => {
      const p = new URLSearchParams(location.search);
      p.set("space", cfg.team || "personal");
      p.set("state", kindOf(cfg.team, isCommunitySpace));
      p.delete("open");
      location.search = p.toString();
    },
  });
  await new Promise((r) => setTimeout(r, 50));
  if (openAtStart) {
    // 셸 열의 전이가 끝난 뒤에 자리를 잰다.
    await new Promise((r) => setTimeout(r, 400));
    document.querySelector<HTMLElement>(".space-switch")?.click();
  }
  document.body.dataset.ready = "1";
}
void main();
