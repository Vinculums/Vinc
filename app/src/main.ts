import "./app.css";
import { COPY } from "./copy";
import { icon } from "./icons";
import { renderAtlas } from "./surfaces/atlas";
import { renderSearch } from "./surfaces/search";
import { renderPacks } from "./surfaces/packs";
import { renderSettings } from "./surfaces/settings";
import { connectSidecar, WEB, ensureWebAuth, cfg } from "./api";
import { checkForUpdate } from "./update";
import { installTooltip } from "./tooltip";
import { mountTopbar, setAtlasHooks } from "./topbar";
import { mountLnb, setLnbNavigator } from "./lnb";

// 표면 3종: Atlas / Search(+Records) / Packs(+Intake).
type Surface = "atlas" | "search" | "packs";
const SURFACES: Record<Surface, (el: HTMLElement) => void | Promise<void>> = {
  atlas: renderAtlas,
  search: renderSearch,
  packs: renderPacks,
};
const ORDER: Surface[] = ["atlas", "search", "packs"];
// 화면 이름 ↔ 내부 코드명 분리. 첫 화면의 코드명은 계속 `atlas`(해시·딥링크·모듈)
// 이지만 사용자에게는 `Home` 이다 — 발주자 지시 2026-07-27.
const SURFACE_LABEL: Record<Surface, string> = {
  atlas: COPY.ui.topbar.home, search: "Search", packs: "Packs",
};
// 구 표면 해시 → 통합 표면(딥링크·구 CTA 하위호환).
const ALIAS: Record<string, Surface> = { intake: "packs", records: "search" };

const surfaceEl = document.getElementById("surface")!;
const topbar = document.getElementById("topbar")!;

// Notation icons into the top bar (CSS가 18px 로 클램프)
topbar.querySelectorAll<HTMLElement>("[data-icon]").forEach((el) => {
  el.insertAdjacentHTML("afterbegin", icon(el.dataset.icon!, 18));
});
document.getElementById("settings-btn")!.setAttribute("aria-label", COPY.ui.topbar.settings);
document.getElementById("settings-btn")!.setAttribute("data-tip", COPY.ui.topbar.settings);
document.getElementById("sync-status")!.textContent = COPY.ui.common.local;

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
// 좌측 문서 트리(D69). 표면과 무관하게 한 번만 마운트하고, 내용은 스스로 갱신한다.
mountLnb();

function current(): Surface {
  const h = location.hash.replace(/^#/, "").split("/")[0];
  if (h in SURFACES) return h as Surface;
  if (h in ALIAS) return ALIAS[h];
  return "atlas";   // 기본 첫 화면 = Atlas(최상단)
}

async function route() {
  // 구 표면 해시로 진입 시 정본 해시로 정규화(활성 하이라이트·딥링크 일치).
  const rawHead = location.hash.replace(/^#/, "").split("/")[0];
  if (rawHead in ALIAS) { location.hash = `#${ALIAS[rawHead]}`; return; }  // hashchange → 재진입
  const s = current();
  surfaceEl.innerHTML = "";
  // 상단 바의 Atlas 전용 버튼은 표면을 떠나는 즉시 사라진다 — Atlas 면 아래에서
  // 다시 등록한다. 표면이 자기 소멸을 감지하게 두는 대신 라우터가 명시적으로 끈다
  // (컨테이너가 남아 있어 표면 스스로는 떠난 줄을 알 수 없다).
  setAtlasHooks(null);
  // 좌측 트리의 착지 훅도 같이 끈다 — Atlas 가 없으면 "그 노드로 날아가기"가
  // 성립하지 않으므로 문서 클릭은 해시 착지로 강등된다(lnb.openDoc).
  setLnbNavigator(null);
  await SURFACES[s](surfaceEl);
}

window.addEventListener("hashchange", route);
document.title = `Vinc · ${COPY.tagline}`;

// Settings — 우측 슬라이드오버(딤 스크림 + 우측 패널). 스크림/Esc 로 닫힘.
const popover = document.getElementById("settings-popover")!;
const settingsScrim = document.getElementById("settings-scrim")!;
function openSettings() { settingsScrim.hidden = false; popover.hidden = false; void renderSettings(popover); }
function closeSettings() { settingsScrim.hidden = true; popover.hidden = true; }
document.getElementById("settings-btn")!.addEventListener("click", () => {
  if (popover.hidden) openSettings(); else closeSettings();
});
settingsScrim.addEventListener("click", closeSettings);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !popover.hidden) closeSettings();
});

// ── Command palette (Cmd/Ctrl+K — navigation aid, not a replacement; D26) ──
const palette = document.getElementById("palette")!;
type Cmd = { label: string; hint: string; run: () => void };
const COMMANDS: Cmd[] = [
  ...ORDER.map((s, i) => ({
    label: COPY.ui.palette.goTo(SURFACE_LABEL[s]), hint: `${i + 1}`,
    run: () => { location.hash = `#${s}`; },
  })),
  { label: COPY.ui.palette.ingest, hint: "Packs", run: () => { location.hash = "#packs"; } },
  { label: COPY.ui.palette.share, hint: "Packs", run: () => { location.hash = "#packs"; } },
  { label: COPY.ui.palette.settings, hint: "", run: () => (document.getElementById("settings-btn") as HTMLElement).click() },
];

function openPalette() {
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
  const close = () => { palette.hidden = true; };
  q.addEventListener("input", () => { sel = 0; hits = draw(); });
  q.addEventListener("keydown", (e) => {
    if (e.key === "Escape") close();
    if (e.key === "ArrowDown") { sel = Math.min(sel + 1, hits.length - 1); hits = draw(); }
    if (e.key === "ArrowUp") { sel = Math.max(sel - 1, 0); hits = draw(); }
    if (e.key === "Enter" && hits[sel]) { hits[sel].run(); close(); }
  });
  palette.addEventListener("click", (e) => { if (e.target === palette) close(); }, { once: true });
  q.focus();
}

// ── Shortcut cheatsheet (`?`) ──────────────────────────────────────────────
const cheatsheet = document.getElementById("cheatsheet")!;
async function openCheatsheet() {
  // 단축키 표도 시트 규격을 쓴다(D63) — 화면 위에 뜨는 것은 전부 같은 구조·같은 닫힘.
  const { openSheet } = await import("./components");
  const sheet = openSheet({ title: COPY.ui.palette.keyboard });
  sheet.body.innerHTML =
    [["1–3", COPY.ui.palette.goSurface], ["⌘/Ctrl K", COPY.ui.topbar.searchPh],
     ["⌘/Ctrl ⇧ K", COPY.ui.palette.cmdPalette],
     ["f", COPY.ui.palette.fit], ["Esc", COPY.ui.palette.back], ["?", COPY.ui.palette.thisSheet]]
      .map(([k, v]) => `<div class="palette-row"><span>${v}</span><span class="hint">${k}</span></div>`)
      .join("");
}

document.addEventListener("keydown", (e) => {
  const inField = (e.target as HTMLElement).tagName === "INPUT" || (e.target as HTMLElement).tagName === "TEXTAREA";
  // ⌘/Ctrl+K 는 상단 바 검색이 가져갔다(D57 — 조회가 제1 동선). 팔레트는 같은 키에
  // Shift 를 더해 남긴다: 한 키에 두 동작이 붙으면 어느 쪽이 뜰지 예측할 수 없다.
  if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key.toLowerCase() === "k") {
    e.preventDefault(); openPalette(); return;
  }
  if (inField) return;
  if (e.key >= "1" && e.key <= "3") location.hash = `#${ORDER[Number(e.key) - 1]}`;
  // `[`(레일 접기)는 레일과 함께 사라졌다(D56) — 상단 바는 접을 것이 없다.
  if (e.key === "?") void openCheatsheet();
  if (e.key === "Escape") palette.hidden = true;
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
// 웹/브라우저 dev 는 __TAURI__.window 부재 → data-shell 미부여로 CSS가 숨김.
const tauriWin = (window as any).__TAURI__?.window;
if (!WEB && tauriWin) {
  document.documentElement.dataset.shell = "";
  const appWin = tauriWin.getCurrentWindow();
  document.getElementById("tb-min")?.addEventListener("click", () => appWin.minimize());
  document.getElementById("tb-max")?.addEventListener("click", () => appWin.toggleMaximize());
  document.getElementById("tb-close")?.addEventListener("click", () => appWin.close());
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
export function applyTheme() {
  const pref = localStorage.getItem(THEME_KEY) || "dark";   // D38 — 기본 = 다크
  const dark = pref === "dark" || (pref === "auto" &&
    window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme);
applyTheme();

// 셸이면 사이드카 접속정보(포트·토큰)를 먼저 확보한 뒤 첫 화면을 그린다.
// (브라우저 dev 는 connectSidecar 가 즉시 반환 → 기존과 동일)
(async () => {
  // 자체 툴팁(D48-T)은 라우팅보다 먼저 건다 — 첫 화면의 마크도 곧바로 담당한다.
  // 위임 하나라 서피스가 다시 그려져도 재배선이 필요 없다.
  installTooltip();
  // 셸이면 사이드카 접속(포트·토큰) 확보. 웹 Vinc(호스티드)는 로컬 사이드카가 없어
  // 스킵 — 데이터는 계정 프록시(/api/graph)로 간다. 브라우저 dev 는 connectSidecar 가
  // __TAURI__ 없음으로 즉시 반환(same-origin vite 프록시 유지).
  if (!WEB) {
    await connectSidecar();
  } else {
    // 웹모드: 서피스를 그리기 **전에** 세션을 확인한다 — 미인증이면 즉시 /login 으로
    // (앱이 잠깐 떴다가 데이터 401 로 뒤늦게 리다이렉트되는 깜빡임 제거).
    if (!(await ensureWebAuth())) return;
  }
  route();
  // 첫 화면을 그린 뒤 조용히 업데이트 확인(비차단; 셸 아니면 no-op).
  void checkForUpdate();
})();
