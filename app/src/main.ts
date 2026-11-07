import "./app.css";
import { COPY } from "./copy";
import { icon } from "./icons";
import { renderView, repaintView, leaveView } from "./surfaces/view";
import { renderHome, leaveHome, homeEditorOpen } from "./surfaces/home";
import { renderSearch } from "./surfaces/search";
import { openSettings } from "./surfaces/settings";
import { connectSidecar, WEB, ensureWebAuth, cfg, IN_SHELL, decideDataMode,
         adoptSpaceFromAddress } from "./api";
import { checkForUpdate } from "./update";
import { mountLiveRefresh, setLiveRefresher } from "./live";
import { wireFileAssociations } from "./openfiles";
import { installTooltip } from "./tooltip";
import { loaderBlock } from "./vinc-loaders.js";
import { mountTopbar, setViewHooks } from "./topbar";
import { mountLnb, refreshLnb, setLnbNavigator, repaintLnb } from "./lnb";
import { showNotice } from "./components";
import { blocksSurfaceShortcuts } from "./keys";
import { disposeEditorsIn, docEditorOpen } from "./doc-editor";
import { mountAuthoringKeys, startNewDoc, startNewNode } from "./authoring";

// 표면 3종: Home(문서) / View(그래프) / Search(+Records). Packs(+Intake)는 표면이 아니라
// Tools 모달로 갔다(P15) — 다섯 기능이 전부 거기 산다.
// 첫 화면은 문서 홈이다. 그래프는 오른쪽 위의 전환 버튼이 연다. 그래프의
// 코드명은 계속 `view`(해시·딥링크·모듈)이고 화면 이름도 View 다.
// 근거: [[decision:the-desktop-shell-is-documents-first]]
type Surface = "home" | "view" | "search";
const SURFACES: Record<Surface, (el: HTMLElement) => void | Promise<void>> = {
  home: renderHome,
  view: renderView,
  search: renderSearch,
};
const ORDER: Surface[] = ["home", "view", "search"];
const SURFACE_LABEL: Record<Surface, string> = {
  home: COPY.ui.home.nav, view: COPY.ui.topbar.home, search: COPY.ui.topbar.search,
};
// 구 표면 해시 → 통합 표면(딥링크·구 CTA 하위호환). 그래프를 가리키던 옛 주소는
// 그래프로 간다 — 문서 홈이 첫 화면이 된 뒤에도 밖에 나간 지도 주소는 지도를 연다.
// `atlas` 는 리네임 전 이 표면의 코드명이었다(F8, 2026-08-15).
// 제거 조건: 없음. 밖에 나간 북마크와 CTA 주소를 받는 표라 무기한 둔다(gate_forbidden F8 이 이 항목을 예외로 둔다).
const ALIAS: Record<string, Surface> = {
  intake: "view", packs: "view", records: "search", atlas: "view",
};
// 옛 주소가 **무엇을 찾아 왔는지**. `#packs` 는 이미 밖에 나가 있는 주소라 표면이
// 사라졌다고 링크까지 죽이지 않는다. 그런데 첫 화면으로 보내기만 하면 지도가 뜰 뿐이라,
// 백업이나 공유를 찾아 온 사람은 그것이 어디로 갔는지 알 길이 없다 — 그래서 그 갈래의
// Tools 를 함께 연다. 나머지 별칭(`atlas`·`records`)은 가리키는 표면이 그대로 있어 없다.
const ALIAS_TOOLS: Record<string, "import" | "share"> = { intake: "import", packs: "share" };

const surfaceEl = document.getElementById("surface")!;
const shellEl = document.getElementById("shell")!;
const docpaneEl = document.getElementById("docpane")!;
const docpaneBodyEl = document.getElementById("docpane-body")!;

// ── 문서 내용 열의 머리 — 접기/펴기 (셸 소유) ──────────────────────────────
// 상태 셋을 구분한다: closed(선택 없음) · open · folded(선택은 살아 있고 열만 접힘).
// **접힘은 저장하지 않는다.** LNB 는 접힌 상태를 기억하지만(사용자 소유) 이 열은
// 선택에 딸린 물건이라, 기억해 두면 다음에 노드를 눌러도 화면이 그대로여서 "눌렀는데
// 아무 일도 안 일어난다"가 된다 — 명시적인 조작의 결과는 반드시 보여야 한다.
// 문서 열에는 **접기 손잡이가 없다**(2026-09-09). 여태 셋이나 있었다: 패널 머리의
// 접기 · 셸 직속 재열기 · 그리고 패널 자기 제목 줄의 ✕. 접기와 닫기는 사용자에게
// 같은 행위(이 면을 치운다)라 ✕ 하나만 남긴다. `data-doc` 의 `folded` 는 그래서
// 이제 아무도 켜지 않는다 — 상태와 그 CSS 는 남겨 둔다(임베드 배치가 쓸 수 있다).
docpaneEl.setAttribute("aria-label", COPY.ui.lnb.paneTitle);

// 표기 아이콘 심기(CSS 가 18px 로 클램프).
// ⚠ 범위는 **셸**이다(2026-09-09). 여태 `topbar` 안만 훑었는데 설정·GRAPH.md 버튼이
// 레일로 내려가면서 그 범위를 벗어나 **아이콘 없는 빈 사각**이 됐다(실측). 선택자에
// 자리를 박아 두면 요소가 옮겨 갈 때마다 조용히 빠진다 — `data-icon` 이 있는 곳이
// 곧 심을 곳이다.
document.querySelectorAll<HTMLElement>("#shell [data-icon]").forEach((el) => {
  el.insertAdjacentHTML("afterbegin", icon(el.dataset.icon!, 18));
});
document.getElementById("settings-btn")!.setAttribute("aria-label", COPY.ui.topbar.settings);
document.getElementById("settings-btn")!.setAttribute("data-tip", COPY.ui.topbar.settings);
// 시트의 이름도 덱에서 온다 — index.html 의 aria-label 은 영어로 박혀 있어서, 열 때
// 스크린리더가 버튼과 다른 언어로 읽는다. 덱이 정본이므로 여기서 덮는다.

// 관례(GM-8) — 아이콘 하나 + 제안 적체 뱃지. 시트는 눌릴 때 지연 로드한다.
{
  const conv = document.getElementById("tb-conv")!;
  conv.setAttribute("aria-label", COPY.ui.conv.title);
  conv.setAttribute("data-tip", COPY.ui.conv.tip);
  conv.addEventListener("click", async () => {
    const { openConventions } = await import("./conventions");
    openConventions();
  });
  import("./conventions").then((m) => m.refreshBadge());
}
mountTopbar();
// 좌측 문서 트리(D69). 크롬은 먼저 마운트하고, 첫 조회는 아래 초기화가 끝난 뒤 보낸다.
mountLnb({ deferLoad: true });
// 새 문서 · 새 노드 단축키(Ctrl/Cmd+N · +Shift). 표면과 무관하게 한 번.
mountAuthoringKeys();

function current(): Surface {
  const h = location.hash.replace(/^#/, "").split("/")[0];
  if (h in SURFACES) return h as Surface;
  if (h in ALIAS) return ALIAS[h];
  return "home";   // 기본 첫 화면 = 문서 홈
}

let activeSurface: Surface | undefined;

async function route() {
  // 구 표면 해시로 진입 시 정본 해시로 정규화(활성 하이라이트·딥링크 일치).
  const rawHead = location.hash.replace(/^#/, "").split("/")[0];
  if (rawHead in ALIAS) {
    // 뒤따르는 경로는 보존한다 — `#atlas/trace/<id>` 같은 구 딥링크가 `#view` 로
    // 뭉개지면 특정 노드를 가리키던 링크가 조용히 기본 화면으로 떨어진다(버그로
    // 읽힌다). intake·records 는 하위경로가 없던 별칭이라 이 차이가 드러나지 않았다.
    const rest = location.hash.replace(/^#[^/]*/, "");
    location.hash = `#${ALIAS[rawHead]}${rest}`;
    if (rawHead in ALIAS_TOOLS) {
      openSettings("data", ALIAS_TOOLS[rawHead]);
    }
    return;                                                          // hashchange → 재진입
  }
  const s = current();
  const resumeView = s === "view" && activeSurface === "search" && location.hash === "#view";
  activeSurface = s;
  leaveView();
  leaveHome();
  // 문서 홈은 편집기를 표면 안에 세운다 — 비우기 전에 확정·해제한다(아래 문서 열과 같은 이유).
  disposeEditorsIn(surfaceEl);
  surfaceEl.innerHTML = "";
  // 문서 열은 셸의 것이지만 내용은 표면이 심는다(View 가 자기 상세를 옮겨 온다).
  // 표면이 사라질 때 그 내용도 함께 사라져야 한다 — 표면 컨테이너를 비우는 것만으로는
  // 안 된다(옮겨 심은 노드는 이미 여기 있으므로 남는다). 라우터가 닫는다.
  // 비우는 것은 **본문뿐**이다: 머리는 셸 것이고 접기 손잡이가 거기 살고 있다.
  // 열을 비우기 **전에** 그 안의 편집기를 확정·해제한다. 여태 이 줄이 마운트 지점만
  // 걷어 가고 핸들에는 닿지 않아서, 2초 디바운스가 화면 없는 곳에서 터졌다(저장 문구가
  // 나갈 노드도 함께 떨어져 나가 흔적조차 안 남았다 — 2026-09-07 실측).
  disposeEditorsIn(docpaneBodyEl);
  docpaneBodyEl.innerHTML = "";
  docpaneEl.querySelector("#rail-grip")?.remove();
  docpaneEl.hidden = true;
  shellEl.dataset.doc = "closed";
  // 상단 바의 View 전용 버튼은 표면을 떠나는 즉시 사라진다 — View 면 아래에서
  // 다시 등록한다. 표면이 자기 소멸을 감지하게 두는 대신 라우터가 명시적으로 끈다
  // (컨테이너가 남아 있어 표면 스스로는 떠난 줄을 알 수 없다).
  setViewHooks(null);
  // 저절로 새로워지는 갱신(VIN-129)도 떠나는 표면을 부르지 않게 푼다. 새 표면이 다시 건다.
  setLiveRefresher(null);
  // 좌측 트리의 착지 훅도 같이 끈다 — View 가 없으면 "그 노드로 날아가기"가
  // 성립하지 않으므로 문서 클릭은 해시 착지로 강등된다(lnb.openDoc).
  setLnbNavigator(null);
  shellEl.dataset.surface = s;
  if (s === "view") await renderView(surfaceEl, resumeView);
  else await SURFACES[s](surfaceEl);
}

window.addEventListener("hashchange", route);
document.title = `Vinc · ${COPY.tagline}`;

// Settings 는 **시트다**. 도구·GRAPH.md 와 같은 상자·자리·딤·모션을 쓰고, 개폐 배선
// (트리거에서 자라는 모션 · 스크림 클릭 · Esc · 중첩 처리)은 시트가 전부 갖는다. 여기에
// 자기 팝오버나 개폐 배선을 다시 두지 않는다: 구현이 둘이면 같은 토큰을 읽어도 다시 갈린다.
const settingsBtn = document.getElementById("settings-btn")!;
settingsBtn.addEventListener("click", () => openSettings("general", undefined, true));

// ── Command palette (Cmd/Ctrl+K — navigation aid, not a replacement; D26) ──
const palette = document.getElementById("palette")!;
let paletteReturnFocus: HTMLElement | null = null;
function closePalette() {
  if (palette.hidden) return;
  palette.hidden = true;
  if (paletteReturnFocus?.isConnected) paletteReturnFocus.focus();
  paletteReturnFocus = null;
}
// 카드 안의 클릭은 무시하고 배경 클릭만 닫는다. 한 번 연 뒤에도 계속 유효해야 한다.
palette.addEventListener("click", (e) => { if (e.target === palette) closePalette(); });
type Cmd = { label: string; hint: string; run: () => void };
const COMMANDS: Cmd[] = [
  ...ORDER.map((s, i) => ({
    label: COPY.ui.palette.goTo(SURFACE_LABEL[s]), hint: `${i + 1}`,
    run: () => { location.hash = `#${s}`; },
  })),
  // 여태 이 둘은 `#packs` 로 보냈다. 표면이 사라졌으니 이제 직접 그 갈래를 연다 —
  // 별칭을 한 번 거치게 두면 팔레트가 주소창을 흔들고 화면이 한 번 갈아엎힌다.
  // 힌트는 명령이 닿는 자리다: Tools 가 설정의 정보 분류로 들어갔으므로 설정.
  // 팔레트의 설정 명령은 **연다**(토글 아님) — 이미 열려 있으면 일반으로 옮길 뿐이다.
  // 만들기(흐름 A) — 단축키가 브라우저에 먹히는 자리(웹)에서도 닿는 길.
  { label: COPY.author.paletteDoc, hint: "⌘/Ctrl N", run: () => startNewDoc() },
  { label: COPY.author.paletteNode, hint: "⌘/Ctrl ⇧ N", run: () => void startNewNode() },
  { label: COPY.ui.palette.ingest, hint: COPY.ui.topbar.settings,
    run: () => openSettings("data", "import") },
  { label: COPY.ui.palette.share, hint: COPY.ui.topbar.settings,
    run: () => openSettings("data", "share") },
  { label: COPY.ui.palette.settings, hint: "", run: () => openSettings("general") },
];

function openPalette() {
  if (palette.hidden) paletteReturnFocus = document.activeElement as HTMLElement | null;
  palette.hidden = false;
  palette.innerHTML = `
    <div class="palette-card" role="dialog" aria-label="Command palette">
      <input id="palette-q" placeholder="${COPY.ui.palette.typeCommand}" autocomplete="off" />
      <div id="palette-list"></div>
    </div>`;
  const q = palette.querySelector<HTMLInputElement>("#palette-q")!;
  const list = palette.querySelector<HTMLElement>("#palette-list")!;
  let sel = 0;
  const draw = () => {
    const t = q.value.toLowerCase();
    const hits = COMMANDS.filter((c) => c.label.toLowerCase().includes(t));
    sel = Math.min(sel, Math.max(hits.length - 1, 0));
    list.innerHTML = hits.map((c, i) => `
      <div class="palette-row${i === sel ? " sel" : ""}" data-i="${i}">
        <span>${c.label}</span><span class="hint">${c.hint}</span>
      </div>`).join("") || `<div class="hint" style="padding:.5rem">${COPY.ui.palette.noCommands}</div>`;
    list.querySelectorAll(".palette-row").forEach((row) =>
      row.addEventListener("click", () => { hits[Number((row as HTMLElement).dataset.i)].run(); close(); }));
    return hits;
  };
  let hits = draw();
  // 팔레트에는 등장·퇴장 애니메이션이 **없다**. 단축키로 하루 수십 번 여는 것이라 모션은
  // 매번 기다리는 시간이 되고(Raycast 가 같은 판단이다), 나온 곳도 없어 설명할 공간 단서도
  // 없다. 시트·모달의 popIn/popOut 은 클릭으로 여는 것들의 문법이다.
  const close = closePalette;
  q.addEventListener("input", () => { sel = 0; hits = draw(); });
  q.addEventListener("keydown", (e) => {
    if (e.key === "Escape") close();
    if (e.key === "ArrowDown") { sel = Math.min(sel + 1, hits.length - 1); hits = draw(); }
    if (e.key === "ArrowUp") { sel = Math.max(sel - 1, 0); hits = draw(); }
    if (e.key === "Enter" && hits[sel]) { hits[sel].run(); close(); }
  });
  q.focus();
}

// ── Shortcut cheatsheet (`?`) ──────────────────────────────────────────────
async function openCheatsheet() {
  // 단축키 표도 시트 규격을 쓴다(D63) — 화면 위에 뜨는 것은 전부 같은 구조·같은 닫힘.
  const { openSheet } = await import("./components");
  const sheet = openSheet({ title: COPY.ui.palette.keyboard });
  sheet.body.innerHTML =
    // 표 안의 범위도 표면 수에서 나온다 — 손으로 적으면 위 단축키와 갈라진다.
    [[`1–${ORDER.length}`, COPY.ui.palette.goSurface], ["⌘/Ctrl K", COPY.ui.topbar.searchPh],
     ["⌘/Ctrl ⇧ K", COPY.ui.palette.cmdPalette],
     ["⌘/Ctrl N", COPY.author.paletteDoc], ["⌘/Ctrl ⇧ N", COPY.author.paletteNode],
     ["F", COPY.ui.palette.fit], ["Esc", COPY.ui.palette.back], ["?", COPY.ui.palette.thisSheet]]
      .map(([k, v]) => `<div class="palette-row"><span>${v}</span><span class="hint">${k}</span></div>`)
      .join("");
}

document.addEventListener("keydown", (e) => {
  // 판정은 keys.ts 한 벌뿐이다. 여기 `tagName` 검사를 다시 적으면 표면 쪽 가드와
  // 또 갈리고, 그 틈으로 지도 단축키가 문서 본문에 떨어진다(2026-09-07 실측).
  // ⌘/Ctrl+K 는 상단 바 검색이 가져갔다(D57 — 조회가 제1 동선). 팔레트는 같은 키에
  // Shift 를 더해 남긴다: 한 키에 두 동작이 붙으면 어느 쪽이 뜰지 예측할 수 없다.
  if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key.toLowerCase() === "k") {
    e.preventDefault(); openPalette(); return;
  }
  // 문서 열이 떠 있으면 표면 단축키는 쉰다 — 판정은 keys.ts, DOM 은 doc-editor.ts.
  if (blocksSurfaceShortcuts(e.target, document.activeElement, docEditorOpen() || homeEditorOpen())) return;
  // 표면 **수만큼만** 받는다. 여기 `"1"`~`"3"` 리터럴이 박혀 있었는데, 그것은 ORDER 가
  // 줄어드는 순간 조용히 틀리는 종류다 — 3 을 누르면 `#undefined` 로 간다. 타입은
  // 문자열 비교라 아무 말도 안 하고, 게이트도 이 자리를 안 본다.
  const n = Number(e.key);
  if (n >= 1 && n <= ORDER.length) location.hash = `#${ORDER[n - 1]}`;
  // `[`(레일 접기)는 레일과 함께 사라졌다(D56) — 상단 바는 접을 것이 없다.
  if (e.key === "?") void openCheatsheet();
  if (e.key === "Escape") closePalette();
});

// ── Skin (Signal) — 웹 Vinc=web(Home·Signal), 데스크탑=desktop(Deep Signal) ──
// 같은 번들이 두 표면을 서빙하므로 런타임에 data-skin으로 토큰 세트를 고른다.
// dev 오버라이드: ?skin=web|desktop 또는 localStorage "vinc.skin"
// (localhost dev는 WEB=false라 오버라이드 없이는 웹 스킨을 볼 수 없다.)
const skinOverride =
  new URLSearchParams(location.search).get("skin") || localStorage.getItem("vinc.skin");
document.documentElement.dataset.skin =
  skinOverride === "web" || skinOverride === "desktop"
    ? skinOverride
    : WEB ? "web" : "desktop";

// ── 커스텀 타이틀바 — 데스크탑 셸(tauri decorations:false)에서만 노출·배선.
// 웹/브라우저 dev 는 셸이 아니므로 data-shell 미부여 → CSS가 숨긴다.
// ⚠ 전역 `__TAURI__` 대신 모듈에서 **동적으로** 가져온다(Q2). 정적 import 는 웹
//   번들에도 들어가고, 전역 노출은 사용자 문서를 렌더하는 페이지에 API 표면을
//   통째로 열어 두는 것이라 끊었다.
if (!WEB && IN_SHELL) {
  void (async () => {
    try {
      const { getCurrentWindow } = await import("@tauri-apps/api/window");
      const appWin = getCurrentWindow();
      document.documentElement.dataset.shell = "";
      document.getElementById("tb-min")?.addEventListener("click", () => appWin.minimize());
      document.getElementById("tb-max")?.addEventListener("click", () => appWin.toggleMaximize());
      document.getElementById("tb-close")?.addEventListener("click", () => appWin.close());
    } catch {
      // 타이틀바 배선 실패가 앱 기동을 막지 않는다 — 창 제어만 못 하게 둔다.
    }
  })();
}

// ── 웹 스킨: 공홈 GNB를 공유 단일 소스(/assets/gnb.js)로 런타임 주입. 데스크탑 셸은 미주입. ──
// 같은 오리진(vincs.io)이라 사이트의 gnb.js 를 그대로 로드 → 홈·서브페이지와 100% 동일한 GNB.
// (로컬 dev ?skin=web 은 vite가 이 파일을 서빙하지 않아 GNB 미표시 — 배포 환경 전용.)
if (document.documentElement.dataset.skin === "web") {
  (window as unknown as { VINC_GNB: unknown }).VINC_GNB = {
    ctx: "app", active: "app", signedIn: true, lang: cfg.lang,
  };
  const g = document.createElement("script");
  g.src = "/assets/gnb.js";
  g.defer = true;
  document.head.appendChild(g);
}

// ── Dark mode (D — desktop-shell §3-9): OS 추종 + Settings 수동 토글 ──────
const THEME_KEY = "vinc.theme";   // "auto" | "light" | "dark"
// 웹에서는 공홈과 같은 오리진이라 사이트의 테마 키도 함께 본다. 둘이 어긋나면 사이트와
// 앱을 오갈 때마다 밝기가 뒤집혀 번쩍인다. 앱 값이 없으면 사이트 값을 따르고, 정한 밝기는
// 사이트 키에도 적어 둔다(사이트 GNB 의 토글도 두 키를 함께 쓴다).
const SITE_THEME_KEY = "vinc.site.theme.v2";
export function applyTheme() {
  const web = document.documentElement.dataset.skin === "web";
  let pref = "dark";   // D38 — 기본 = 다크
  try {
    pref = localStorage.getItem(THEME_KEY)
      || (web ? localStorage.getItem(SITE_THEME_KEY) : "") || "dark";
  } catch { /* 저장소가 막힌 창은 기본값으로 */ }
  const dark = pref === "dark" || (pref === "auto" &&
    window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  if (web) try { localStorage.setItem(SITE_THEME_KEY, dark ? "dark" : "light"); } catch {}
  // 좌측 트리의 주제 색은 paint 때 굳은 HEX 다(CSS 변수가 아니라 스스로 안 뒤집힌다).
  // 여기서 다시 그리지 않으면 뒤집힌 화면에 반대 모드의 색이 남아 도트가 배경에 묻는다.
  // ⚠ 설정 화면의 클릭 핸들러가 아니라 **여기**여야 한다: pref 가 "auto" 면 아래
  //   matchMedia 가 OS 를 따라 부르므로 설정 클릭이 아예 없는 경로가 생긴다.
  //   마운트 전 첫 호출은 `#lnb-body` 가 없어 paint 가 알아서 되돌아간다.
  repaintLnb();
  // 열린 지도도 같은 사정이다(3D·2D·타임라인·번들 모두 마운트 때 색을 굳힌다). 다시
  // 그리는 것(route·show)이 아니라 **다시 칠하는 것**이라 읽던 문서 열과 카드가 남는다.
  // 근거: [[decision:a-theme-switch-repaints-the-open-view-in-place]]
  repaintView();
}
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme);
// 웹 GNB 의 테마 토글(공홈 gnb.js)은 키만 쓰고 이 이벤트를 쏜다. 받아서 같은 길로 다시 칠한다.
window.addEventListener("vinc:theme", applyTheme);
applyTheme();
// 화면 언어를 문서에도 적는다. index.html 은 lang="en" 으로 박혀 있어 한국어 화면을 화면
// 읽기와 번역이 영어로 읽었다(VIN-294). 언어를 바꾸면 페이지를 다시 불러오므로 한 번이면 된다.
document.documentElement.lang = cfg.lang;

// 셸이면 사이드카 접속정보(포트·토큰)를 먼저 확보한 뒤 첫 화면을 그린다.
// (브라우저 dev 는 connectSidecar 가 즉시 반환 → 기존과 동일)
(async () => {
  // 자체 툴팁(D48-T)은 라우팅보다 먼저 건다 — 첫 화면의 마크도 곧바로 담당한다.
  // 위임 하나라 서피스가 다시 그려져도 재배선이 필요 없다.
  installTooltip();
  // 사이드카·로그인·모드를 정하는 동안 첫 화면이 비지 않게 로고 모션을 둔다. 첫 route() 가
  // 표면을 갈아 끼우면 사라진다(로그인 화면은 그 위를 덮는다).
  const bootSurface = document.getElementById("surface");
  if (bootSurface && !bootSurface.childElementCount)
    bootSurface.innerHTML = loaderBlock("mark", COPY.ui.common.starting, { fill: true });
  // 셸이면 사이드카 접속(포트·토큰) 확보. 웹 Vinc(호스티드)는 로컬 사이드카가 없어
  // 스킵 — 데이터는 계정 프록시(/api/graph)로 간다. 브라우저 dev 는 connectSidecar 가
  // __TAURI__ 없음으로 즉시 반환(same-origin vite 프록시 유지).
  if (!WEB) {
    const sidecar = connectSidecar();
    // 데스크톱은 로그인이 먼저다(VIN-128). 로그인 **화면**은 사이드카를 기다리지 않는다:
    // 기다리면 사이드카가 뜨는 동안(죽었으면 12초 내내) 창이 비어 있다. 루프백으로 키를
    // 받는 것은 단추를 누른 뒤라 거기서 `sidecar` 를 기다린다(`firstrun` start).
    // 브라우저 dev(셸 아님)는 종전대로 로컬 사이드카를 본다.
    const { needsSignIn, requireSignIn } = await import("./firstrun");
    if (needsSignIn()) await requireSignIn("first", sidecar);
    await sidecar;
    if (IN_SHELL) {
      // 어느 그래프를 볼지 정한다. 정본은 사이드카의 저장 설정이다(VIN-130). 사람이 고른
      // 적이 없는데 이 기기에 작업이 있으면 로컬로 계속 가고 한 번 알린다(VIN-131). 없으면 계정이다.
      if ((await decideDataMode()) === "ask") {
        const { tellAboutDeviceGraph } = await import("./device-graph");
        await tellAboutDeviceGraph();
      }
      const { mountAccountSignals } = await import("./account-signals");
      mountAccountSignals();
      const { mountIndexSignals } = await import("./index-signals");
      mountIndexSignals();
    }
  } else {
    // 웹모드: 서피스를 그리기 **전에** 세션을 확인한다 — 미인증이면 즉시 /login 으로
    // (앱이 잠깐 떴다가 데이터 401 로 뒤늦게 리다이렉트되는 깜빡임 제거).
    if (!(await ensureWebAuth())) return;
  }
  // 주소가 스페이스를 들고 왔으면(`?space=`) 그 스페이스로 연다. 데이터를 처음 읽기
  // **전**이어야 한다 — 뒤에 바꾸면 첫 화면이 옛 스페이스로 그려진 채 남는다.
  // 근거: [[decision:a-node-address-names-its-space]]
  const refusedSpace = await adoptSpaceFromAddress();
  // 사이드바도 사이드카·로그인·모드·스페이스가 정해진 뒤 처음 읽는다(VIN-360).
  // 그 전에는 로딩으로 기다린다. 실제 요청 실패는 기존 Retry 상태가 맡는다.
  void refreshLnb();
  // 열린 화면이 저절로 새로워진다(VIN-129). 첫 화면보다 **먼저** 건다: 기준 표식을
  // 데이터보다 먼저 읽어야 그 사이의 쓰기가 이미 본 것으로 묻히지 않는다.
  mountLiveRefresh();
  route();
  if (refusedSpace) {
    const U = COPY.ui.settingsUi;
    void showNotice(refusedSpace === "local" ? U.spaceLinkLocal : U.spaceLinkRefused,
                    U.spaceLinkOk);
  }
  // 계속 도는 동기화는 없다(VIN-130). 계정 모드는 계정 그래프를 직접 보고, 로컬 모드의
  // 그래프는 고른 폴더에만 있다. 데이터는 설정에서 모드를 바꿀 때만 옮긴다(storage-mode).
  // 첫 화면을 그린 뒤 조용히 업데이트 확인(비차단; 셸 아니면 no-op).
  void checkForUpdate();
  // `.vincpack` 더블클릭으로 넘어온 argv 를 연다(Q1). 화면을 그린 **뒤**에
  // 부른다 — 임포트 시트가 뜰 자리가 먼저 있어야 한다. 셸 아니면 no-op.
  wireFileAssociations();
})();
