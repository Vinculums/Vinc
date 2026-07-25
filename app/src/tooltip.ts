/** 자체 툴팁 (D48-T) — 네이티브 `title` 속성·SVG `<title>` 을 대체한다.
 *
 *  네이티브 툴팁은 WCAG 2.1 **1.4.13 Content on Hover or Focus** 세 조건을 하나도
 *  만족하지 못한다: Esc 로 닫을 수 없고(dismissable), 포인터를 툴팁 위로 옮길 수
 *  없으며(hoverable), 브라우저가 제멋대로 지운다(persistent). 지연·수명·글꼴도
 *  OS 가 정하므로 디자인 시스템 밖이다. 이 모듈이 그 셋을 전부 만족시킨다.
 *
 *  계약: 마크에 `data-tip="문구"` 를 달면 끝이다. 앱 전체에 인스턴스 하나만 살고
 *  (문서 레벨 위임), 대상이 사라지면 스스로 닫힌다. 문구는 호출부가 COPY 덱에서
 *  가져와 넣는다 — 이 모듈은 문구를 한 줄도 저작하지 않는다.
 *
 *  재질은 크롬 층(D37 Liquid Glass)이다: 반투명 + blur. 스타일은 app.css 의
 *  `.tip` 절 하나뿐이다(템플릿 문자열 안에 두 번째 스타일시트를 만들지 않는다). */

let box: HTMLElement | null = null;
let anchor: Element | null = null;
/** 포인터가 툴팁 위로 건너가는 짧은 순간(hoverable)에 닫히지 않게 하는 유예. */
let closing = 0;

function ensure(): HTMLElement {
  if (box) return box;
  box = document.createElement("div");
  box.className = "tip";
  box.setAttribute("role", "tooltip");
  box.hidden = true;
  // 툴팁 자신에도 hover 를 허용한다(1.4.13 hoverable) — 위에 올라오면 유예를 취소.
  box.addEventListener("mouseenter", () => { clearTimeout(closing); });
  box.addEventListener("mouseleave", () => hide());
  document.body.appendChild(box);
  return box;
}

/** 화면 밖으로 나가지 않게 뒤집어 놓는다(bklit 의 flip 감지와 같은 규칙).
 *  기준은 앵커의 실제 사각형 — 커서 추종은 하지 않는다(마크가 작아 흔들린다). */
function place(el: HTMLElement, at: Element) {
  const r = at.getBoundingClientRect();
  const b = el.getBoundingClientRect();
  const GAP = 8;
  let x = r.left + r.width / 2 - b.width / 2;
  let y = r.top - b.height - GAP;
  if (y < 4) y = r.bottom + GAP;                                  // 위가 좁으면 아래로
  x = Math.max(6, Math.min(x, window.innerWidth - b.width - 6));  // 좌우는 밀어 넣는다
  el.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`;
}

function show(at: Element, text: string) {
  clearTimeout(closing);
  const el = ensure();
  anchor = at;
  el.textContent = text;
  el.hidden = false;
  // 폭이 정해진 뒤에 자리를 잡는다(숨은 채 재면 0 이 나온다).
  place(el, at);
}

function hide() {
  clearTimeout(closing);
  anchor = null;
  if (box) { box.hidden = true; box.textContent = ""; }
}

/** 유예 후 닫기 — 마크에서 툴팁으로 건너가는 동안 살아 있어야 한다(hoverable). */
function hideSoon() {
  clearTimeout(closing);
  closing = window.setTimeout(hide, 120);
}

const tipOf = (t: EventTarget | null) =>
  (t instanceof Element ? t.closest("[data-tip]") : null) as HTMLElement | null;

/** 앱 부팅 때 한 번 호출한다. 위임 하나로 모든 `[data-tip]` 을 담당한다. */
export function installTooltip(): void {
  ensure();
  document.addEventListener("mouseover", (e) => {
    const t = tipOf(e.target);
    if (!t) { if (anchor) hideSoon(); return; }
    const text = t.dataset.tip || "";
    if (!text) { hideSoon(); return; }
    if (t !== anchor) show(t, text);
  });
  document.addEventListener("mouseout", (e) => {
    // 툴팁 자신으로 나가는 경우는 유예가 살려 준다(mouseenter 가 취소).
    if (tipOf(e.target) && anchor) hideSoon();
  });
  // 키보드 초점에도 뜬다(1.4.13 은 hover 와 focus 를 같이 규정한다).
  document.addEventListener("focusin", (e) => {
    const t = tipOf(e.target);
    if (t && t.dataset.tip) show(t, t.dataset.tip);
  });
  document.addEventListener("focusout", () => { if (anchor) hideSoon(); });
  // Esc — 포인터를 움직이지 않고 닫는다(1.4.13 dismissable). 다른 Esc 소비자
  // (패널·시트)를 막지 않도록 stopPropagation 하지 않는다.
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") hide(); });
  // 스크롤·리사이즈로 앵커가 움직이면 자리만 다시 잡는다(따라다니되 재계산 1회).
  const reflow = () => { if (anchor && box && !box.hidden) place(box, anchor); };
  window.addEventListener("scroll", reflow, true);
  window.addEventListener("resize", reflow);
}

/** 앵커가 DOM 에서 사라졌는데 툴팁만 남는 일을 막는다(뷰 전환·재렌더 시 호출). */
export function closeTooltip(): void { hide(); }
