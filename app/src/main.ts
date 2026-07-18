import "./app.css";
import { COPY } from "./copy";
import { icon } from "./icons";
import { renderIntake } from "./surfaces/intake";
import { renderRecords } from "./surfaces/records";
import { renderAtlas } from "./surfaces/atlas";
import { renderSearch } from "./surfaces/search";
import { renderPacks } from "./surfaces/packs";
import { renderSettings } from "./surfaces/settings";
import { connectSidecar, WEB } from "./api";
import { checkForUpdate } from "./update";

type Surface = "intake" | "records" | "atlas" | "search" | "packs";
const SURFACES: Record<Surface, (el: HTMLElement) => void | Promise<void>> = {
  intake: renderIntake,
  records: renderRecords,
  atlas: renderAtlas,
  search: renderSearch,
  packs: renderPacks,
};
const ORDER: Surface[] = ["intake", "records", "atlas", "search", "packs"];

const surfaceEl = document.getElementById("surface")!;
const rail = document.getElementById("rail")!;

// Notation icons into the rail (D26 — 56px rail, labels on expand)
rail.querySelectorAll<HTMLElement>("[data-icon]").forEach((el) => {
  el.insertAdjacentHTML("afterbegin", icon(el.dataset.icon!, 20));
});

function current(): Surface {
  const h = location.hash.replace(/^#/, "").split("/")[0];
  return (h in SURFACES ? h : "intake") as Surface;
}

async function route() {
  const s = current();
  rail.querySelectorAll("a").forEach((a) =>
    a.classList.toggle("active", a.dataset.surface === s));
  surfaceEl.innerHTML = "";
  await SURFACES[s](surfaceEl);
}

window.addEventListener("hashchange", route);
document.title = `Vinc · ${COPY.tagline}`;

const popover = document.getElementById("settings-popover")!;
document.getElementById("settings-btn")!.addEventListener("click", () => {
  popover.hidden = !popover.hidden;
  if (!popover.hidden) renderSettings(popover);
});

// ── Command palette (Cmd/Ctrl+K — navigation aid, not a replacement; D26) ──
const palette = document.getElementById("palette")!;
type Cmd = { label: string; hint: string; run: () => void };
const COMMANDS: Cmd[] = [
  ...ORDER.map((s, i) => ({
    label: COPY.ui.palette.goTo(`${s[0].toUpperCase()}${s.slice(1)}`), hint: `${i + 1}`,
    run: () => { location.hash = `#${s}`; },
  })),
  { label: COPY.ui.palette.ingest, hint: "Intake", run: () => { location.hash = "#intake"; } },
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
function openCheatsheet() {
  cheatsheet.hidden = false;
  cheatsheet.innerHTML = `
    <div class="palette-card" role="dialog" aria-label="Keyboard shortcuts">
      <h2 style="margin:.2rem 0 .8rem">${COPY.ui.palette.keyboard}</h2>
      ${[["1–5", COPY.ui.palette.goSurface], ["⌘/Ctrl K", COPY.ui.palette.cmdPalette], ["[", COPY.ui.palette.railToggle],
         ["f", COPY.ui.palette.fit], ["Esc", COPY.ui.palette.back], ["?", COPY.ui.palette.thisSheet]]
        .map(([k, v]) => `<div class="palette-row"><span>${v}</span><span class="hint">${k}</span></div>`).join("")}
    </div>`;
  cheatsheet.addEventListener("click", () => { cheatsheet.hidden = true; }, { once: true });
}

document.addEventListener("keydown", (e) => {
  const inField = (e.target as HTMLElement).tagName === "INPUT" || (e.target as HTMLElement).tagName === "TEXTAREA";
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openPalette(); return; }
  if (inField) return;
  if (e.key >= "1" && e.key <= "5") location.hash = `#${ORDER[Number(e.key) - 1]}`;
  if (e.key === "[") document.getElementById("shell")!.classList.toggle("rail-expanded");
  if (e.key === "?") openCheatsheet();
  if (e.key === "Escape") { palette.hidden = true; cheatsheet.hidden = true; }
});

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
  // 셸이면 사이드카 접속(포트·토큰) 확보. 웹 Vinc(호스티드)는 로컬 사이드카가 없어
  // 스킵 — 데이터는 계정 프록시(/api/graph)로 간다. 브라우저 dev 는 connectSidecar 가
  // __TAURI__ 없음으로 즉시 반환(same-origin vite 프록시 유지).
  if (!WEB) await connectSidecar();
  route();
  // 첫 화면을 그린 뒤 조용히 업데이트 확인(비차단; 셸 아니면 no-op).
  void checkForUpdate();
})();
