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

/** 파괴적 동작 확인(S1) — 삭제는 되돌릴 수 없으므로 반드시 이 게이트를 통과시킨다.
 *  overlay + palette-card(편집 시트와 동일 시각 문법). resolve(true) = 진행.
 *  기본 포커스는 취소 쪽에 두고, Esc·배경 클릭도 취소로 접는다(오조작 방지). */
export function confirmDestructive(message: string, confirmLabel: string,
                                   cancelLabel: string): Promise<boolean> {
  return new Promise((resolve) => {
    const el = document.createElement("div");
    el.style.cssText = "position:fixed;inset:0;z-index:70;background:hsl(var(--foreground)/.18);"
      + "display:grid;place-items:center;padding:1rem";
    el.innerHTML = `
      <div class="palette-card" role="alertdialog" aria-modal="true" style="max-width:26rem">
        <p style="margin:0 0 .8rem">${message}</p>
        <div style="display:flex;gap:.5rem;justify-content:flex-end">
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

/** Top-right primary CTA (Figma: 포인트색 필 버튼, 화면당 1개). */
export function headerCta(id: string, iconName: string, label: string): string {
  return `<button class="btn btn-primary header-cta" id="${id}">${icon(iconName, 16)} ${label}</button>`;
}
