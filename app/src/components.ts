/** Shared surface chrome — Figma first-draft visual grammar (nodes 5:2/5:71/
 *  6:57/7:2/7:78) carried over with approved deltas (D18 English copy). */
import { icon } from "./icons";

/** Surface header: display title + muted meta line. (아이콘은 LNB 와 중복이라
 *  페이지에서 미표기 — iconName 은 호출부 안정성 위해 시그니처만 유지.) */
export function header(_iconName: string, title: string, meta: string,
                       cta = ""): string {
  return `
    <header class="surface-header">
      <div class="surface-title">
        <div>
          <h1>${title}</h1>
          <p class="hint surface-meta">${meta}</p>
        </div>
      </div>
      ${cta}
    </header>`;
}

/** ── 시트(Sheet) — 화면 위에 뜨는 것의 **단일 규격**(D63) ────────────────────
 *
 *  여태 오버레이가 제각각이었다: 공유·가져오기·문서·확인·프롬프트가 저마다 다른
 *  마크업으로 딤을 깔고, 제목 위치·액션 정렬·닫는 법이 서로 달랐다. 화면마다 다른
 *  구조를 익히게 하는 것이 곧 학습 비용이다.
 *
 *  규격은 넷으로 고정한다:
 *    머리(제목 + 부제 + ✕) · 몸(자유) · 발(주 액션 오른쪽, 부 액션 왼쪽) · 닫힘 규칙
 *  닫힘 규칙도 하나다 — **Esc · ✕ · 바깥 클릭**. 어느 시트든 같다.
 *
 *  파괴적 확인(confirmDestructive)만 바깥 클릭을 취소로 삼는다(오조작 방지).
 *  패널(렌즈·검색 결과·크럼 깊이)은 시트가 아니다 — 앵커에 붙고 딤이 없다. */
export type SheetHandle = {
  el: HTMLElement;
  body: HTMLElement;
  foot: HTMLElement;
  close: () => void;
};

export function openSheet(opts: {
  title: string; subtitle?: string; width?: string;
  /** 닫힐 때 한 번 호출(편집기 dispose 등). */
  onClose?: () => void;
  /** 라벨 없이 몸만 쓰는 시트(문서 편집기처럼) — 머리의 제목을 숨긴다. */
  bare?: boolean;
}): SheetHandle {
  const esc = (s: string) => s.replace(/[&<>"]/g, (c) =>
    c === "&" ? "&amp;" : c === "<" ? "&lt;" : c === ">" ? "&gt;" : "&quot;");
  const el = document.createElement("div");
  el.className = "sheet-scrim";
  el.innerHTML = `
    <div class="sheet" role="dialog" aria-modal="true"
         aria-label="${esc(opts.title)}" style="width:${opts.width || "34rem"}">
      <div class="sheet-head">
        <div class="sheet-titles">
          ${opts.bare ? "" : `<h2>${esc(opts.title)}</h2>`}
          ${opts.subtitle ? `<p class="hint">${esc(opts.subtitle)}</p>` : ""}
        </div>
        <button class="rail-x sheet-x" aria-label="Close">✕</button>
      </div>
      <div class="sheet-body"></div>
      <div class="sheet-foot"></div>
    </div>`;
  document.body.appendChild(el);
  const close = () => {
    el.remove();
    document.removeEventListener("keydown", onKey, true);
    opts.onClose?.();
  };
  const onKey = (e: KeyboardEvent) => {
    if (e.key !== "Escape") return;
    // 시트가 여럿 쌓여도 **맨 위 하나만** 닫는다(가장 최근 것이 마지막 자식).
    if (document.querySelectorAll(".sheet-scrim")[document.querySelectorAll(".sheet-scrim").length - 1] !== el) return;
    e.stopPropagation();
    close();
  };
  document.addEventListener("keydown", onKey, true);
  el.addEventListener("click", (e) => { if (e.target === el) close(); });
  el.querySelector(".sheet-x")!.addEventListener("click", close);
  return {
    el,
    body: el.querySelector<HTMLElement>(".sheet-body")!,
    foot: el.querySelector<HTMLElement>(".sheet-foot")!,
    close,
  };
}

/** 시트 발의 액션 행 — 주 액션은 언제나 오른쪽 끝. 순서가 시트마다 다르면
 *  근육 기억이 무너진다(확인/취소 위치가 바뀌는 것이 오조작의 흔한 원인). */
export function sheetActions(primary: { id: string; label: string; danger?: boolean },
                             secondary?: { id: string; label: string },
                             extra?: { id: string; label: string }): string {
  return `${extra ? `<button class="btn sheet-extra" id="${extra.id}">${extra.label}</button>` : ""}
    <span class="sheet-gap"></span>
    ${secondary ? `<button class="btn" id="${secondary.id}">${secondary.label}</button>` : ""}
    <button class="btn ${primary.danger ? "btn-danger" : "btn-primary"}" id="${primary.id}">${primary.label}</button>`;
}

/** ── 컨텍스트 메뉴(D64) — 우클릭의 보편 관용구 ────────────────────────────
 *
 *  Jakob 의 법칙: 우클릭에 사람들이 기대하는 것은 **메뉴**다. 여태 우클릭이 곧바로
 *  "여기서 추적"을 실행해, 메뉴를 기대한 손이 예고 없이 화면을 갈아탔다(되돌릴
 *  방법도 크럼뿐이었다). 관용구대로 메뉴를 띄우고, 그 안 첫 줄을 추적으로 둔다.
 *
 *  시트도 패널도 아닌 **세 번째 층**이다: 딤 없음, 포인터 좌표에 붙음, 한 번 고르면
 *  사라짐. 화면 밖으로 나가지 않도록 좌표를 접어 넣는다. */
export type MenuItem = { label: string; run: () => void; danger?: boolean };

export function openContextMenu(x: number, y: number, items: MenuItem[]) {
  document.querySelectorAll(".ctx-menu").forEach((m) => m.remove());
  const el = document.createElement("div");
  el.className = "ctx-menu";
  el.setAttribute("role", "menu");
  el.innerHTML = items.map((it, i) =>
    `<button class="ctx-item${it.danger ? " ctx-danger" : ""}" role="menuitem" data-i="${i}">${it.label}</button>`).join("");
  document.body.appendChild(el);
  // 화면 밖으로 넘치면 반대쪽으로 접는다(모서리에서 잘리지 않게).
  const r = el.getBoundingClientRect();
  el.style.left = `${Math.min(x, window.innerWidth - r.width - 8)}px`;
  el.style.top = `${Math.min(y, window.innerHeight - r.height - 8)}px`;
  const close = () => {
    el.remove();
    document.removeEventListener("mousedown", onOut, true);
    document.removeEventListener("keydown", onKey, true);
  };
  const onOut = (e: MouseEvent) => { if (!el.contains(e.target as Node)) close(); };
  const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); close(); } };
  document.addEventListener("mousedown", onOut, true);
  document.addEventListener("keydown", onKey, true);
  el.querySelectorAll<HTMLElement>(".ctx-item").forEach((b) =>
    b.addEventListener("click", () => { const it = items[Number(b.dataset.i)]; close(); it.run(); }));
  el.querySelector<HTMLElement>(".ctx-item")?.focus();
}

/** 파괴적 동작 확인(S1) — 삭제는 되돌릴 수 없으므로 반드시 이 게이트를 통과시킨다.
 *  overlay + palette-card(편집 시트와 동일 시각 문법). resolve(true) = 진행.
 *  기본 포커스는 취소 쪽에 두고, Esc·배경 클릭도 취소로 접는다(오조작 방지). */
export function confirmDestructive(message: string, confirmLabel: string,
                                   cancelLabel: string): Promise<boolean> {
  return new Promise((resolve) => {
    const el = document.createElement("div");
    el.className = "sheet-scrim sheet-center";
    el.innerHTML = `
      <div class="sheet" role="alertdialog" aria-modal="true" style="width:26rem">
        <div class="sheet-body" style="padding-top:var(--space-4,1rem)">
          <p style="margin:0">${message}</p>
        </div>
        <div class="sheet-foot">
          <span class="sheet-gap"></span>
          <button class="btn" id="cd-no">${cancelLabel}</button>
          <button class="btn btn-danger" id="cd-yes">${confirmLabel}</button>
        </div>
      </div>`;
    document.body.appendChild(el);
    const done = (v: boolean) => { el.remove(); document.removeEventListener("keydown", onKey); resolve(v); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") done(false); };
    document.addEventListener("keydown", onKey);
    el.addEventListener("click", (e) => { if (e.target === el) done(false); });
    el.querySelector("#cd-no")!.addEventListener("click", () => done(false));
    el.querySelector("#cd-yes")!.addEventListener("click", () => done(true));
    (el.querySelector("#cd-no") as HTMLButtonElement).focus();
  });
}

/** 텍스트 입력 시트(S2 도메인 이름 변경 등). resolve(문자열) = 확인, null = 취소.
 *  세 번째 버튼(extraLabel)을 주면 그 라벨로 **빈 문자열**을 돌려준다 — 도메인
 *  "미분류로 내리기"처럼 값을 비우는 선택지에 쓴다. */
export function promptText(title: string, initial: string, confirmLabel: string,
                           cancelLabel: string,
                           extraLabel?: string): Promise<string | null> {
  return new Promise((resolve) => {
    const el = document.createElement("div");
    el.className = "sheet-scrim sheet-center";
    const esc = (s: string) => s.replace(/[&<>"]/g, (c) =>
      c === "&" ? "&amp;" : c === "<" ? "&lt;" : c === ">" ? "&gt;" : "&quot;");
    el.innerHTML = `
      <div class="sheet" role="dialog" aria-modal="true" style="width:26rem">
        <div class="sheet-body" style="padding-top:var(--space-4,1rem)">
          <p style="margin:0 0 .6rem">${esc(title)}</p>
          <input id="pt-in" class="rail-filter" style="margin:0" value="${esc(initial)}"/>
        </div>
        <div class="sheet-foot">
          ${extraLabel ? `<button class="btn" id="pt-x">${esc(extraLabel)}</button>` : ""}
          <span class="sheet-gap"></span>
          <button class="btn" id="pt-no">${esc(cancelLabel)}</button>
          <button class="btn btn-primary" id="pt-yes">${esc(confirmLabel)}</button>
        </div>
      </div>`;
    document.body.appendChild(el);
    const input = el.querySelector<HTMLInputElement>("#pt-in")!;
    const done = (v: string | null) => {
      el.remove(); document.removeEventListener("keydown", onKey); resolve(v);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") done(null);
      if (e.key === "Enter" && document.activeElement === input) done(input.value.trim());
    };
    document.addEventListener("keydown", onKey);
    el.addEventListener("click", (e) => { if (e.target === el) done(null); });
    el.querySelector("#pt-no")!.addEventListener("click", () => done(null));
    el.querySelector("#pt-x")?.addEventListener("click", () => done(""));
    el.querySelector("#pt-yes")!.addEventListener("click", () => done(input.value.trim()));
    input.focus();
    input.select();
  });
}

/** Top-right primary CTA (Figma: 포인트색 필 버튼, 화면당 1개). */
export function headerCta(id: string, iconName: string, label: string): string {
  return `<button class="btn btn-primary header-cta" id="${id}">${icon(iconName, 16)} ${label}</button>`;
}
