/** Shared surface chrome — Figma first-draft visual grammar (nodes 5:2/5:71/
 *  6:57/7:2/7:78) carried over with approved deltas (D18 English copy). */
import { icon } from "./icons";

/** Surface header: notation icon + display title + muted meta line
 *  (Figma: 아이콘 + 타이틀 + 엔드포인트 서브텍스트). */
export function header(iconName: string, title: string, meta: string,
                       cta = ""): string {
  return `
    <header class="surface-header">
      <div class="surface-title">
        ${icon(iconName, 26)}
        <div>
          <h1>${title}</h1>
          <p class="hint surface-meta">${meta}</p>
        </div>
      </div>
      ${cta}
    </header>`;
}

/** Top-right primary CTA (Figma: 포인트색 필 버튼, 화면당 1개). */
export function headerCta(id: string, iconName: string, label: string): string {
  return `<button class="btn btn-primary header-cta" id="${id}">${icon(iconName, 16)} ${label}</button>`;
}
