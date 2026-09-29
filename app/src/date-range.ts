import { COPY } from "./copy";
import { icon } from "./icons";
import {
  PERIODS, TIME_KEY, nextTimeKey, periodRange, utcDay, type PeriodKey,
} from "./view-prefs";

/** 기간 선택(날짜 범위) — 앱의 첫 날짜 컴포넌트(2026-09-28).
 *
 *  브라우저 기본 날짜 입력을 쓰지 않는 이유: 셸마다 모양이 다르고(WebView2 · WKWebView),
 *  범위를 고르지 못하고, 기록이 **어디 있는지** 보여 줄 수 없다. 여기서는 기록 있는
 *  날에 점을 찍어 역사가 어디 몰려 있는지 고르기 전에 보이게 한다.
 *
 *  고르는 알갱이는 셋이다: 날 · 달 · 해. 머리의 제목을 누르면 달 격자 → 해 격자로
 *  바뀌고, 그 격자에서 고르면 그 알갱이로 범위가 정해진다(내용 시점은 `YYYY`·`YYYY-MM`
 *  으로 적히기도 한다 — 날로만 고르면 그런 시점은 어느 범위에도 안 들어간다).
 *  두 번 눌러 범위를 정한다: 첫 번째가 시작, 두 번째가 끝이고, 끝을 고르는 순간
 *  적용된다(확인 버튼이 없다 — 렌즈 옵션이 다 그렇다).
 *
 *  날짜는 **UTC 달력**이다(Q2.3). 기록 시각이 UTC ISO 라 타임라인 열도 UTC 날로 나뉜다 —
 *  고르는 칸과 그려지는 칸이 같은 경계를 써야 한다. 현지 날짜로 옮기는 일은 따로다.
 *  주는 두 언어 모두 일요일에 시작한다.
 *  근거: [[vinc.plan.view-card-timeline-period-authoring-2026-09-27]] §2.4 */

export type RangeValue = { preset: PeriodKey; from: string; to: string };
export type DateRangeOpts = {
  /** 이 요소 아래(자리가 없으면 위)에 열린다. 닫히면 포커스가 여기로 돌아간다. */
  anchor: HTMLElement;
  /** 기준이 다시 그려져 떨어져 나간 뒤 닫힐 때 포커스를 돌려줄 곳을 다시 찾는다. */
  reanchor?: () => HTMLElement | null;
  value: RangeValue;
  /** 날(`YYYY-MM-DD`) → 기록 수. 점의 진하기가 이 수를 따른다. */
  marks?: Map<string, number>;
  onChange: (v: RangeValue) => void;
};

const POP_ID = "date-range-pop";
type Mode = "days" | "months" | "years";

/** 프리셋의 화면 이름 — 옵션 탭 · 차트 위 칩 · 이 컴포넌트가 같은 말을 쓴다. */
export function periodLabel(k: PeriodKey): string {
  const P = COPY.ui.panel;
  return k === "7d" ? P.period7d : k === "30d" ? P.period30d : k === "90d" ? P.period90d
    : k === "year" ? P.periodYear : k === "custom" ? P.periodCustom : P.periodAll;
}

/** 범위를 짧게 적는다: `08-29 ~ 09-27` 처럼 같은 해면 해를 떼고, 다르면 붙인다.
 *  (화면 텍스트에 대시를 쓰지 않는다 — 물결표가 범위의 기호다.) */
export function rangeText(v: RangeValue, now = new Date()): string {
  const r = periodRange({ period: v.preset, periodFrom: v.from, periodTo: v.to }, now);
  if (!r) return "";
  const a = r.start, b = lastKeyOf(r);
  const sameYear = a.slice(0, 4) === b.slice(0, 4) && a.length === 10 && b.length === 10;
  const short = (k: string) => (sameYear ? k.slice(5) : k);
  return a === b ? a : `${short(a)} ~ ${short(b)}`;
}

/** 반열린 구간의 마지막 칸(시작과 같은 알갱이). */
function lastKeyOf(r: { start: string; end: string }): string {
  const e = r.end;
  if (e.length === 4) return String(Number(e) - 1).padStart(4, "0");
  if (e.length === 7) {
    const [y, m] = e.split("-").map(Number);
    return m === 1 ? `${y - 1}-12` : `${y}-${String(m - 1).padStart(2, "0")}`;
  }
  const [y, m, d] = e.split("-").map(Number);
  return utcDay(new Date(Date.UTC(y, m - 1, d - 1)));
}

/** 칸 키를 날 단위의 첫날 · 마지막 날로 편다(격자에 띠를 칠할 때 쓴다). */
const firstDay = (k: string) => (k.length === 4 ? `${k}-01-01` : k.length === 7 ? `${k}-01` : k);
function lastDay(k: string): string {
  if (k.length === 10) return k;
  const nx = nextTimeKey(k);
  const [y, m, d] = firstDay(nx).split("-").map(Number);
  return utcDay(new Date(Date.UTC(y, m - 1, d - 1)));
}

let current: { close: () => void } | null = null;

export function isDateRangeOpen(): boolean { return !!current; }
export function closeDateRange(): void { current?.close(); }

export function openDateRange(o: DateRangeOpts): void {
  closeDateRange();
  const D = COPY.ui.dateRange;
  const now = new Date();
  const today = utcDay(now);
  const pop = document.createElement("div");
  pop.id = POP_ID;
  pop.className = "drp";
  pop.setAttribute("role", "dialog");
  pop.setAttribute("aria-label", D.title);
  document.body.appendChild(pop);

  const marks = o.marks ?? new Map<string, number>();
  const maxMark = Math.max(1, ...marks.values());
  // 지금 걸린 범위(날 단위로 편 것). 프리셋도 여기서 풀려 띠가 보인다.
  const applied = periodRange({ period: o.value.preset, periodFrom: o.value.from, periodTo: o.value.to }, now);
  let selLo = applied ? firstDay(applied.start) : "";
  let selHi = applied ? lastDay(lastKeyOf(applied)) : "";
  let pending = "";                 // 첫 클릭(시작) — 끝을 기다린다
  let pendingKey = "";              // 그 첫 클릭의 원래 알갱이 키(YYYY · YYYY-MM · 날)
  let hover = "";
  let mode: Mode = "days";
  // 보이는 달(오른쪽 달). 범위가 있으면 그 끝이 든 달, 없으면 이번 달.
  const anchorDay = selHi || today;
  let viewY = Number(anchorDay.slice(0, 4));
  let viewM = Number(anchorDay.slice(5, 7)) - 1;
  let focusKey = selHi || today;
  let msg = "";
  const twoUp = () => window.innerWidth >= 600;

  const pad = (n: number) => String(n).padStart(2, "0");
  const dayKey = (y: number, m: number, d: number) => utcDay(new Date(Date.UTC(y, m, d)));
  const shiftMonth = (y: number, m: number, by: number) => {
    const t = new Date(Date.UTC(y, m + by, 1));
    return [t.getUTCFullYear(), t.getUTCMonth()] as const;
  };

  /** 띠의 양 끝 — 고르는 중이면 첫 클릭과 가리키는 칸 사이. */
  function band(): [string, string] {
    if (pending) {
      const other = hover || pending;
      return pending <= other ? [pending, other] : [other, pending];
    }
    return [selLo, selHi];
  }

  function monthHTML(y: number, m: number): string {
    const first = new Date(Date.UTC(y, m, 1)).getUTCDay();
    const days = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
    const [lo, hi] = band();
    const cells: string[] = [];
    for (let i = 0; i < first; i++) cells.push(`<span class="drp-cell" role="gridcell"></span>`);
    for (let d = 1; d <= days; d++) {
      const k = dayKey(y, m, d);
      const n = marks.get(k) || 0;
      const cls = ["drp-day"];
      if (lo && k >= lo && k <= hi) cls.push("in");
      if (k === lo) cls.push("lo");
      if (k === hi) cls.push("hi");
      if (k === today) cls.push("today");
      const label = [`${D.monthTitle(y, m)} ${d}`, D.weekdaysLong[(first + d - 1) % 7],
        k === today ? D.today : "", D.dayCount(n)].filter(Boolean).join(", ");
      const dot = n ? `<i class="drp-dot" style="opacity:${
        (0.35 + 0.65 * Math.log(n + 1) / Math.log(maxMark + 1)).toFixed(2)}"></i>` : "";
      cells.push(`<span class="drp-cell" role="gridcell"><button type="button" class="${cls.join(" ")}"`
        + ` data-key="${k}" tabindex="${k === focusKey ? 0 : -1}" aria-label="${label}"`
        + ` aria-pressed="${k === lo || k === hi}">${d}${dot}</button></span>`);
    }
    while (cells.length % 7) cells.push(`<span class="drp-cell" role="gridcell"></span>`);
    const rows: string[] = [];
    for (let i = 0; i < cells.length; i += 7) rows.push(`<div class="drp-row" role="row">${cells.slice(i, i + 7).join("")}</div>`);
    const head = D.weekdays.map((w, i) =>
      `<span class="drp-wd" role="columnheader" aria-label="${D.weekdaysLong[i]}">${w}</span>`).join("");
    return `<div class="drp-month"><div class="drp-mtitle">${D.monthTitle(y, m)}</div>`
      + `<div class="drp-grid" role="grid" aria-label="${D.monthTitle(y, m)}">`
      + `<div class="drp-row" role="row">${head}</div>${rows.join("")}</div></div>`;
  }

  /** 달·해 격자 — 3열. 칸 키는 `YYYY-MM` / `YYYY`. */
  function tilesHTML(): string {
    const [lo, hi] = band();
    const keys: { k: string; label: string; has: number }[] = [];
    if (mode === "months") {
      for (let m = 0; m < 12; m++) {
        const k = `${viewY}-${pad(m + 1)}`;
        let has = 0;
        marks.forEach((n, d) => { if (d.startsWith(k)) has += n; });
        keys.push({ k, label: D.monthsShort[m], has });
      }
    } else {
      const base = viewY - (viewY % 12);
      for (let y = base; y < base + 12; y++) {
        const k = String(y);
        let has = 0;
        marks.forEach((n, d) => { if (d.startsWith(k)) has += n; });
        keys.push({ k, label: k, has });
      }
    }
    const tiles = keys.map(({ k, label, has }) => {
      const a = firstDay(k), b = lastDay(k);
      const cls = ["drp-tile"];
      if (lo && b >= lo && a <= hi) cls.push("in");
      if (lo && a <= lo && lo <= b) cls.push("lo");
      if (hi && a <= hi && hi <= b) cls.push("hi");
      if (today.startsWith(k)) cls.push("today");
      return `<button type="button" class="${cls.join(" ")}" data-key="${k}"`
        + ` tabindex="${focusKey.startsWith(k) ? 0 : -1}">${label}${
          has ? `<i class="drp-dot" style="opacity:${
            (0.35 + 0.65 * Math.log(has + 1) / Math.log(maxMark * 31 + 1)).toFixed(2)}"></i>` : ""}</button>`;
    });
    // 포커스 칸이 격자 밖이면 첫 칸이 탭 자리를 갖는다(탭으로 들어올 길이 없어지지 않게).
    if (!tiles.some((t) => t.includes('tabindex="0"'))) tiles[0] = tiles[0].replace('tabindex="-1"', 'tabindex="0"');
    return `<div class="drp-tiles" role="grid" aria-label="${mode === "months" ? D.pickMonth : D.pickYear}">${tiles.join("")}</div>`;
  }

  function render() {
    const two = twoUp();
    const [ly, lm] = two ? shiftMonth(viewY, viewM, -1) : [viewY, viewM];
    const title = mode === "days"
      ? (two ? `${D.monthTitle(ly, lm)} · ${D.monthTitle(viewY, viewM)}` : D.monthTitle(viewY, viewM))
      : mode === "months" ? String(viewY)
      : `${viewY - (viewY % 12)} ~ ${viewY - (viewY % 12) + 11}`;
    const prevLabel = mode === "days" ? D.prevMonth : D.prevYears;
    const nextLabel = mode === "days" ? D.nextMonth : D.nextYears;
    const presets = PERIODS.filter((k) => k !== "custom").map((k) =>
      `<button type="button" class="drp-preset${o.value.preset === k ? " active" : ""}" data-preset="${k}"`
      + ` aria-pressed="${o.value.preset === k}">${periodLabel(k)}</button>`).join("");
    const body = mode === "days"
      ? `<div class="drp-months">${two ? monthHTML(ly, lm) : ""}${monthHTML(viewY, viewM)}</div>`
      : tilesHTML();
    const fromVal = o.value.preset === "custom" ? o.value.from : "";
    const toVal = o.value.preset === "custom" ? o.value.to : "";
    pop.innerHTML =
      `<div class="drp-presets" role="group" aria-label="${D.title}">${presets}</div>`
      + `<div class="drp-main">`
      + `<div class="drp-head">`
      + `<button type="button" class="drp-nav" data-nav="-1" aria-label="${prevLabel}">${icon("chevron-left", 14)}</button>`
      + `<button type="button" class="drp-title" data-mode aria-label="${mode === "days" ? D.pickMonth : mode === "months" ? D.pickYear : D.title}">${title}</button>`
      + `<button type="button" class="drp-nav drp-next" data-nav="1" aria-label="${nextLabel}">${icon("chevron-left", 14)}</button>`
      // 눈에 보이는 문 — Esc·바깥 클릭만으로는 닫는 법이 안 보인다(2026-09-30 지적).
      + `<button type="button" class="rail-x drp-x" data-close aria-label="${D.close}" data-tip="${D.close}">✕</button>`
      + `</div>${body}`
      + `<div class="drp-inputs">`
      + `<label class="drp-field"><span>${D.start}</span><input type="text" inputmode="numeric" data-in="from"`
      + ` value="${fromVal}" placeholder="${D.format}" spellcheck="false" autocomplete="off"></label>`
      + `<label class="drp-field"><span>${D.end}</span><input type="text" inputmode="numeric" data-in="to"`
      + ` value="${toVal}" placeholder="${D.format}" spellcheck="false" autocomplete="off"></label>`
      + `</div>`
      + `<div class="drp-foot"><p class="drp-msg" role="status" aria-live="polite">${msg}</p>`
      + `<button type="button" class="drp-reset" data-reset>${D.reset}</button></div>`
      + `</div>`;
    wire();
  }

  function focusCurrent() {
    const b = pop.querySelector<HTMLElement>('[data-key][tabindex="0"]');
    b?.focus({ preventScroll: true });
  }

  function commit(v: RangeValue) {
    o.onChange(v);
    close();
  }

  /** 칸 하나를 고른다. 첫 번째는 시작, 두 번째는 끝 — 끝에서 적용한다. */
  function pick(k: string) {
    if (!pending) {
      pending = firstDay(k);
      pendingKey = k;
      hover = "";
      msg = D.pickEnd;
      focusKey = k.length === 10 ? k : firstDay(k);
      render();
      focusCurrent();
      return;
    }
    // 두 칸의 알갱이가 다르면 더 고운 쪽으로 편다(시작은 첫날, 끝은 마지막 날).
    const a = pendingKey, b = k;
    const lo = firstDay(a) <= firstDay(b) ? a : b;
    const hi = lo === a ? b : a;
    const same = lo.length === hi.length;
    commit({ preset: "custom", from: same ? lo : firstDay(lo), to: same ? hi : lastDay(hi) });
  }

  function moveFocus(k: string) {
    focusKey = k;
    if (mode === "days") {
      const y = Number(k.slice(0, 4)), m = Number(k.slice(5, 7)) - 1;
      const [ly, lm] = twoUp() ? shiftMonth(viewY, viewM, -1) : [viewY, viewM];
      const inView = (y === viewY && m === viewM) || (y === ly && m === lm);
      if (!inView) {
        // 앞으로 넘어가면 그 달이 오른쪽에, 뒤로 넘어가면 왼쪽에 오게 한다.
        if (`${y}-${pad(m + 1)}` > `${viewY}-${pad(viewM + 1)}`) { viewY = y; viewM = m; }
        else [viewY, viewM] = twoUp() ? shiftMonth(y, m, 1) : [y, m];
      }
      if (pending) hover = k;
    } else if (mode === "months") {
      viewY = Number(k.slice(0, 4));
      if (pending) hover = firstDay(k);
    } else {
      viewY = Number(k);
      if (pending) hover = firstDay(k);
    }
    render();
    focusCurrent();
  }

  function onKey(e: KeyboardEvent) {
    if (e.key === "Escape") {
      // 날짜 선택만 닫는다 — 뒤의 렌즈 패널까지 닫히면 안 된다(패널 Esc 는 document 에서 듣는다).
      e.stopPropagation();
      e.preventDefault();
      if (pending) { pending = ""; pendingKey = ""; hover = ""; msg = ""; render(); focusCurrent(); return; }
      close();
      return;
    }
    const t = e.target as HTMLElement;
    const k = t.dataset?.key;
    if (!k) return;
    let next = "";
    if (mode === "days") {
      const [y, m, d] = k.split("-").map(Number);
      const day = (by: number) => dayKey(y, m - 1, d + by);
      const dow = new Date(Date.UTC(y, m - 1, d)).getUTCDay();
      const monthBy = (by: number) => {
        const [ny, nm] = shiftMonth(y, m - 1, by);
        const last = new Date(Date.UTC(ny, nm + 1, 0)).getUTCDate();
        return dayKey(ny, nm, Math.min(d, last));
      };
      switch (e.key) {
        case "ArrowLeft": next = day(-1); break;
        case "ArrowRight": next = day(1); break;
        case "ArrowUp": next = day(-7); break;
        case "ArrowDown": next = day(7); break;
        case "Home": next = day(-dow); break;
        case "End": next = day(6 - dow); break;
        case "PageUp": next = monthBy(e.shiftKey ? -12 : -1); break;
        case "PageDown": next = monthBy(e.shiftKey ? 12 : 1); break;
      }
    } else {
      const step = mode === "months"
        ? (by: number) => { const [ny, nm] = shiftMonth(Number(k.slice(0, 4)), Number(k.slice(5, 7)) - 1, by); return `${ny}-${pad(nm + 1)}`; }
        : (by: number) => String(Number(k) + by);
      switch (e.key) {
        case "ArrowLeft": next = step(-1); break;
        case "ArrowRight": next = step(1); break;
        case "ArrowUp": next = step(-3); break;
        case "ArrowDown": next = step(3); break;
        case "PageUp": next = step(-12); break;
        case "PageDown": next = step(12); break;
      }
    }
    if (next) { e.preventDefault(); moveFocus(next); }
  }

  function typed() {
    const inFrom = pop.querySelector<HTMLInputElement>('[data-in="from"]')!;
    const inTo = pop.querySelector<HTMLInputElement>('[data-in="to"]')!;
    const a = inFrom.value.trim(), b = inTo.value.trim();
    const okA = TIME_KEY.test(a), okB = TIME_KEY.test(b);
    inFrom.setAttribute("aria-invalid", String(!!a && !okA));
    inTo.setAttribute("aria-invalid", String(!!b && !okB));
    if ((a && !okA) || (b && !okB)) {
      const m = pop.querySelector<HTMLElement>(".drp-msg");
      if (m) m.textContent = D.invalid;
      return;
    }
    // 한쪽만 적었으면 그 한 칸이 곧 범위다(`2025` = 그 해 전체).
    if (!a && !b) return;
    o.onChange({ preset: "custom", from: a || b, to: b || a });
    // 적어서 고르는 동안에는 닫지 않는다 — 결과를 보면서 고쳐 적을 수 있게. 띠만 맞춘다.
    const r = periodRange({ period: "custom", periodFrom: a || b, periodTo: b || a });
    if (r) { selLo = firstDay(r.start); selHi = lastDay(lastKeyOf(r)); }
    o.value = { preset: "custom", from: a || b, to: b || a };
    msg = "";
    const keepFocus = document.activeElement === inTo ? "to" : "from";
    render();
    pop.querySelector<HTMLInputElement>(`[data-in="${keepFocus}"]`)?.focus();
  }

  function wire() {
    pop.querySelectorAll<HTMLElement>("[data-preset]").forEach((b) =>
      b.addEventListener("click", () => commit({ preset: b.dataset.preset as PeriodKey, from: "", to: "" })));
    pop.querySelector("[data-reset]")?.addEventListener("click",
      () => commit({ preset: "all", from: "", to: "" }));
    pop.querySelector("[data-close]")?.addEventListener("click", () => close());
    pop.querySelectorAll<HTMLElement>("[data-nav]").forEach((b) =>
      b.addEventListener("click", () => {
        const by = Number(b.dataset.nav);
        if (mode === "days") [viewY, viewM] = shiftMonth(viewY, viewM, by);
        else viewY += by * (mode === "months" ? 1 : 12);
        render();
        pop.querySelector<HTMLElement>(`[data-nav="${by}"]`)?.focus();
      }));
    pop.querySelector("[data-mode]")?.addEventListener("click", () => {
      mode = mode === "days" ? "months" : mode === "months" ? "years" : "days";
      focusKey = mode === "months" ? `${viewY}-${pad(viewM + 1)}` : mode === "years" ? String(viewY) : focusKey;
      render();
      focusCurrent();
    });
    pop.querySelectorAll<HTMLElement>("[data-key]").forEach((b) => {
      b.addEventListener("click", () => pick(b.dataset.key!));
      b.addEventListener("mouseenter", () => {
        if (!pending) return;
        hover = firstDay(b.dataset.key!);
        // 띠만 다시 칠한다 — 통째로 다시 그리면 가리키던 버튼이 새 노드가 된다.
        paintBand();
      });
    });
    pop.querySelectorAll<HTMLInputElement>("[data-in]").forEach((i) => {
      i.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); typed(); } });
      i.addEventListener("change", typed);
    });
  }

  function paintBand() {
    const [lo, hi] = band();
    pop.querySelectorAll<HTMLElement>(".drp-day").forEach((b) => {
      const k = b.dataset.key!;
      b.classList.toggle("in", !!lo && k >= lo && k <= hi);
      b.classList.toggle("lo", k === lo);
      b.classList.toggle("hi", k === hi);
    });
  }

  function place() {
    // 기준이 다시 그려져 떨어져 나갔으면(패널이 새로 그려졌다) 지금 자리를 지킨다.
    if (!o.anchor.isConnected && pop.style.top) return;
    const r = o.anchor.getBoundingClientRect();
    const m = 8;
    const w = pop.offsetWidth, h = pop.offsetHeight;
    const below = r.bottom + 6;
    const top = below + h + m <= window.innerHeight ? below : Math.max(m, r.top - 6 - h);
    // 오른쪽 끝을 기준에 맞추고, 왼쪽이 모자라면 화면 안으로 민다.
    const left = Math.min(window.innerWidth - w - m, Math.max(m, r.right - w));
    pop.style.top = `${Math.round(top)}px`;
    pop.style.left = `${Math.round(left)}px`;
  }

  const onDown = (e: PointerEvent) => {
    const t = e.target as Node;
    if (pop.contains(t) || o.anchor.contains(t)) return;
    close(false);
  };
  const onResize = () => { render(); place(); };

  function close(returnFocus = true) {
    if (current !== handle) return;
    current = null;
    document.removeEventListener("pointerdown", onDown, true);
    window.removeEventListener("resize", onResize);
    pop.remove();
    if (!returnFocus) return;
    const back = o.anchor.isConnected ? o.anchor : o.reanchor?.();
    back?.focus({ preventScroll: true });
  }
  const handle = { close: () => close() };
  current = handle;

  pop.addEventListener("keydown", onKey);
  render();
  place();
  focusCurrent();
  // 여는 그 클릭이 곧바로 "바깥"으로 잡히지 않게 한 틱 뒤에 듣는다.
  setTimeout(() => {
    if (current === handle) document.addEventListener("pointerdown", onDown, true);
  }, 0);
  window.addEventListener("resize", onResize);
}
