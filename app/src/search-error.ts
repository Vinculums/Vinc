import { COPY } from "./copy";
import { esc } from "./components";

/** 두 검색 입구가 상태를 보존하고 같은 재시도 단추를 제공한다. 서버 본문은 표시하지 않는다. */
export function renderSearchError(el: HTMLElement, failure: any, retry: () => void,
                                  keepInputFocus = false): void {
  const status = Number(failure?.status) || 0;
  el.innerHTML = `<div class="error-banner" role="alert" data-search-status="${status}">
    <p>${esc(COPY.search.error)}${status >= 100 && status < 600 ? ` (HTTP ${status})` : ""}</p>
    <button class="btn search-retry" type="button">${esc(COPY.accountMode.retry)}</button>
  </div>`;
  const button = el.querySelector<HTMLButtonElement>(".search-retry")!;
  if (keepInputFocus) button.addEventListener("mousedown", (e) => e.preventDefault());
  button.addEventListener("click", () => { button.disabled = true; retry(); });
}
