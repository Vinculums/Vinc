// 문서 면의 보기 설정과 범례. 머리 맨 끝, 그래프의 렌즈 버튼과 같은 자리 · 같은 버튼이다.
// 그래프에서는 View 가 그 자리에 자기 렌즈를 채우고, 문서 면에서는 이 버튼이 선다(둘 중 하나).
//
// 설정은 셋이다: 글자 크기 · 읽기 폭 · 줄 간격. 값은 `<html>` 의 `data-doc-*` 로 얹고 CSS 가
// 받는다(app.css `--doc-fs` · `--doc-lh` · `--page-col`). 그래프 옆 칸의 문서도 같은 글자를
// 쓰므로 같은 값을 따른다. 저장은 이 기기의 브라우저 저장소뿐이고, 못 읽으면 기본값이다.
import { COPY } from "./copy";
import { icon } from "./icons";
import { popIn, popOut } from "./motion";
import { wireOutsideDismiss, esc } from "./components";
import { floatCeiling } from "./float-panel";

export type DocText = "small" | "medium" | "large";
export type DocWidth = "narrow" | "standard" | "wide";
export type DocLines = "compact" | "standard" | "relaxed";
export type DocPrefs = { text: DocText; width: DocWidth; lines: DocLines };

export const DOC_PREFS_KEY = "vinc.doc.prefs";
/** 옵션·범례 단추를 내지 않는다(발주자 지시). 단추와 패널 코드는 지우는 중이다. */
const DOC_OPTIONS_OFF = true;
export const DOC_DEFAULTS: DocPrefs = { text: "medium", width: "standard", lines: "standard" };
const CHOICES = {
  text: ["small", "medium", "large"],
  width: ["narrow", "standard", "wide"],
  lines: ["compact", "standard", "relaxed"],
} as const;

/** 저장된 값을 읽는다. 모르는 값 · 깨진 JSON · 막힌 저장소는 그 칸만 기본값으로 돌린다. */
export function readDocPrefs(raw: string | null): DocPrefs {
  let o: Record<string, unknown> = {};
  try { o = raw ? JSON.parse(raw) ?? {} : {}; } catch { o = {}; }
  const pick = <K extends keyof DocPrefs>(k: K): DocPrefs[K] =>
    ((CHOICES[k] as readonly string[]).includes(o[k] as string) ? o[k] : DOC_DEFAULTS[k]) as DocPrefs[K];
  return { text: pick("text"), width: pick("width"), lines: pick("lines") };
}

function load(): DocPrefs {
  try { return readDocPrefs(localStorage.getItem(DOC_PREFS_KEY)); } catch { return { ...DOC_DEFAULTS }; }
}

function save(p: DocPrefs) {
  try { localStorage.setItem(DOC_PREFS_KEY, JSON.stringify(p)); } catch { /* 이 세션만 바뀐다 */ }
}

/** 기본값인 칸은 속성을 지운다 — CSS 의 기본이 그대로 산다. */
export function applyDocPrefs(p: DocPrefs, root: HTMLElement = document.documentElement) {
  for (const k of Object.keys(CHOICES) as (keyof DocPrefs)[]) {
    if (p[k] === DOC_DEFAULTS[k]) root.removeAttribute(`data-doc-${k}`);
    else root.setAttribute(`data-doc-${k}`, p[k]);
  }
}

/** 설정의 읽기 분류가 쓰는 길: 지금 값을 읽고, 바꾸면 곧바로 저장하고 얹는다. 고를 수 있는
 *  값은 `DOC_CHOICES` 뿐이다(그 밖의 값은 `readDocPrefs` 처럼 기본값으로 접힌다). */
export const DOC_CHOICES = CHOICES;
export function currentDocPrefs(): DocPrefs { return load(); }
export function setDocPrefs(next: DocPrefs) {
  prefs = readDocPrefs(JSON.stringify(next));
  save(prefs);
  applyDocPrefs(prefs);
}

const TAB_KEY = "vinc.doc.panel.tab";

let prefs: DocPrefs = { ...DOC_DEFAULTS };
let btn: HTMLButtonElement | null = null;
let panel: HTMLElement | null = null;
let unwire: (() => void) | null = null;
let closing = false;
let seq = 0;

const onDocs = () => /^#home(\/|$)/.test(location.hash) || location.hash === "" || location.hash === "#";

/** 앱 기동 때 한 번(topbar.ts `mountTopbar`). 저장된 값을 먼저 얹어 첫 문서부터 그 글자로 그린다. */
export function mountDocOptions() {
  prefs = load();
  applyDocPrefs(prefs);
  // 문서 면의 옵션·범례 단추는 걷었다. 저장된 읽기 값(글자 크기·폭·줄 간격)은 그대로 얹는다.
  const slot = document.getElementById("tb-docopts");
  if (slot) { slot.hidden = true; slot.innerHTML = ""; }
  if (!slot || btn || DOC_OPTIONS_OFF) return;
  const tip = COPY.ui.docOpts.tip;
  slot.innerHTML = `<button class="lens-btn" id="doc-opts" type="button" aria-haspopup="dialog"
      aria-expanded="false" aria-controls="doc-opts-panel" data-tip="${esc(tip)}"
      aria-label="${esc(tip)}">${icon("dot-grid", 17)}</button>`;
  btn = slot.querySelector<HTMLButtonElement>("#doc-opts");
  panel = document.createElement("div");
  panel.id = "doc-opts-panel";
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-label", tip);
  panel.hidden = true;
  (document.getElementById("shell") ?? document.body).appendChild(panel);
  btn?.addEventListener("click", () => { if (panel!.hidden || closing) open(); else void close(); });
  panel.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); void close(); } });
  const sync = () => {
    slot.hidden = !onDocs();
    if (slot.hidden && panel && !panel.hidden) void close({ outside: true });
  };
  window.addEventListener("hashchange", sync);
  window.addEventListener("resize", () => { if (panel && !panel.hidden) place(); });
  sync();
}

function draw() {
  if (!panel) return;
  const D = COPY.ui.docOpts;
  const tab = (() => { try { return localStorage.getItem(TAB_KEY) === "legend" ? "legend" : "opts"; } catch { return "opts"; } })();
  const seg = (k: keyof DocPrefs, label: string) =>
    `<div class="dopt-row"><span class="hint">${esc(label)}</span><div class="seg" role="group" aria-label="${esc(label)}">${
      (CHOICES[k] as readonly string[]).map((v) => `<button class="chip${prefs[k] === v ? " active" : ""}" type="button"
        data-dk="${k}" data-dv="${v}" aria-pressed="${prefs[k] === v}">${esc(D.choice[v as keyof typeof D.choice])}</button>`).join("")
    }</div></div>`;
  const opts = `<section class="opt-sec"><h3 class="opt-sec-title">${esc(D.reading)}</h3>
      ${seg("text", D.text)}${seg("width", D.width)}${seg("lines", D.lines)}
    </section>
    <div class="dopt-reset"><button class="btn" type="button" data-dreset>${esc(D.reset)}</button></div>`;
  const legend = `<div class="dl-list">${D.marks.map((m) =>
    `<div class="dl-row"><span class="dl-ex dl-ex-${m.kind}">${esc(m.sample)}</span><span class="dl-say">${esc(m.say)}</span></div>`).join("")}</div>`;
  const closeBtn = `<button class="lens-close" type="button" data-dclose aria-label="${esc(D.close)}"
      data-tip="${esc(D.close)}">${icon("x", 14)}</button>`;
  panel.innerHTML = `<div class="lg-tabrow"><div class="lg-tabs" role="tablist">
      <button class="lg-tab${tab === "opts" ? " active" : ""}" type="button" role="tab" data-dtab="opts"
        aria-selected="${tab === "opts"}">${esc(D.options)}</button>
      <button class="lg-tab${tab === "legend" ? " active" : ""}" type="button" role="tab" data-dtab="legend"
        aria-selected="${tab === "legend"}">${esc(COPY.ui.legend.title)}</button>
    </div>${closeBtn}</div>
    <div class="lens-scroll">${tab === "opts" ? opts : legend}</div>
    <div class="lens-foot"><p class="hint hint-sm">${esc(D.note)}</p></div>`;
  panel.querySelector("[data-dclose]")?.addEventListener("click", () => void closeDocOptions());
  panel.querySelectorAll<HTMLElement>("[data-dtab]").forEach((b) => b.addEventListener("click", () => {
    try { localStorage.setItem(TAB_KEY, b.dataset.dtab!); } catch { /* 탭만 기억 못 한다 */ }
    draw(); place();
    panel!.querySelector<HTMLElement>(`[data-dtab="${b.dataset.dtab}"]`)?.focus();
  }));
  panel.querySelectorAll<HTMLElement>("[data-dk]").forEach((b) => b.addEventListener("click", () => {
    prefs = { ...prefs, [b.dataset.dk!]: b.dataset.dv } as DocPrefs;
    commit(`[data-dk="${b.dataset.dk}"][data-dv="${b.dataset.dv}"]`);
  }));
  panel.querySelector("[data-dreset]")?.addEventListener("click", () => {
    prefs = { ...DOC_DEFAULTS };
    commit("[data-dreset]");
  });
}

function commit(focusSel: string) {
  save(prefs);
  applyDocPrefs(prefs);
  draw();
  place();   // 폭을 바꾸면 머리 칼럼이 함께 움직여 버튼 자리도 바뀐다
  panel?.querySelector<HTMLElement>(focusSel)?.focus();
}

/** 버튼 바로 아래, 오른쪽 끝을 버튼에 맞춘다. 좁은 창은 CSS 가 전폭 판으로 올린다. */
function place() {
  if (!panel || !btn) return;
  if (window.matchMedia("(max-width: 900px)").matches) {
    // 전폭 판은 머리 바로 아래에서 시작한다(렌즈 패널과 같은 천장, view.ts `positionPanel`).
    const bar = document.getElementById("topbar")?.getBoundingClientRect();
    panel.style.top = bar?.height ? `${Math.round(bar.bottom)}px` : "";
    panel.style.left = panel.style.maxHeight = "";
    return;
  }
  const m = 8, r = btn.getBoundingClientRect();
  panel.style.maxHeight = "";
  const top = Math.max(r.bottom + 6, floatCeiling());
  const room = window.innerHeight - top - m;
  if (panel.offsetHeight > room) panel.style.maxHeight = `${Math.floor(room)}px`;
  panel.style.top = `${Math.round(top)}px`;
  panel.style.left = `${Math.round(Math.max(m, Math.min(r.right, window.innerWidth - m) - panel.offsetWidth))}px`;
}

function open() {
  if (!panel) return;
  const was = !panel.hidden && !closing;
  closing = false;
  seq += 1;
  panel.hidden = false;
  draw();
  place();
  btn?.setAttribute("aria-expanded", "true");
  if (was) return;
  popIn(panel);
  panel.querySelector<HTMLElement>(".lg-tab.active")?.focus({ preventScroll: true });
  unwire?.();
  unwire = wireOutsideDismiss(panel, () => void close({ outside: true }), btn);
}

async function close(opts?: { outside?: boolean }) {
  if (!panel || panel.hidden || closing) return;
  const mine = ++seq;
  unwire?.(); unwire = null;
  closing = true;
  await popOut(panel);
  if (mine !== seq) return;
  closing = false;
  panel.hidden = true;
  btn?.setAttribute("aria-expanded", "false");
  const a = document.activeElement;
  if (!opts?.outside && (!a || a === document.body || panel.contains(a))) btn?.focus();
}

/** 밖에서 닫는다(표면이 바뀔 때 · 닫기 버튼). */
export const closeDocOptions = (): Promise<void> => close();
