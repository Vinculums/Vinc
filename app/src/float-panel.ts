import { icon } from "./icons";

/** 옮길 수 있는 맥락 카드 (DEC D70) — 화면 위에 뜨는 **네 번째 층**.
 *
 *  시트도 패널도 컨텍스트 메뉴도 아니다:
 *    - 시트(components.openSheet): 딤 + aria-modal + 규격 폭 3종. 뒤를 못 만진다.
 *    - 문서 내용 열(#docpane 안의 #view-detail): 셸의 **그리드 열**이라 지도를
 *      덮지 않는다(폭만 조절). 겹치지 않는 것이 이 카드와의 결정적 차이다.
 *    - 컨텍스트 메뉴: 좌표에 붙되 한 번 고르면 사라진다.
 *  이 카드는 **선택한 노드에 딸려** 뜨고, 그 옆에 머물면서, 사용자가 원하는 자리로
 *  끌려 간다. 지도를 계속 만질 수 있어야 하므로 딤이 없고 모달도 아니다.
 *
 *  D26 이 기각한 "플로팅 내비 패널"과 다른 물건이다 — 저건 *내비게이션*(어디로
 *  갈지 고르는 상시 크롬)이었고 이건 *선택에 종속된 맥락*이다. 선택이 사라지면
 *  카드도 사라진다.
 *
 *  한 번에 하나만 뜬다(싱글턴): 노드를 옮겨 다닐 때마다 카드가 쌓이면 그게 곧
 *  화면을 덮는 창 무더기다. */

export type FloatHandle = {
  el: HTMLElement;
  body: HTMLElement;
  /** 제목을 갈아 끼운다(같은 카드에서 다른 노드로 건너뛸 때). */
  setTitle(t: string): void;
  /** 좌표를 다시 잡는다(뷰포트 안으로 접어 넣는다). */
  place(x: number, y: number): void;
  close(): void;
};

let live: { el: HTMLElement; onClose?: () => void } | null = null;
/** 마지막으로 놓인 자리 — 카드를 닫았다 다시 열어도 손이 기억하는 자리에 온다.
 *  세션 안에서만 산다(localStorage 아님): 창 크기가 바뀌면 의미가 없는 좌표다. */
let lastPos: { x: number; y: number } | null = null;
let moved = false;

const MARGIN = 8;

function clamp(el: HTMLElement, x: number, y: number) {
  const r = el.getBoundingClientRect();
  const nx = Math.max(MARGIN, Math.min(x, window.innerWidth - r.width - MARGIN));
  const ny = Math.max(MARGIN, Math.min(y, window.innerHeight - r.height - MARGIN));
  el.style.left = `${Math.round(nx)}px`;
  el.style.top = `${Math.round(ny)}px`;
}

export function closeFloatPanel() {
  if (!live) return;
  const { el, onClose } = live;
  live = null;
  el.remove();
  onClose?.();
}

export function isFloatPanelOpen(): boolean {
  return live !== null;
}

export function openFloatPanel(opts: {
  title: string; x: number; y: number;
  closeLabel: string;
  onClose?: () => void;
}): FloatHandle {
  closeFloatPanel();
  const el = document.createElement("div");
  el.className = "float-card";
  el.setAttribute("role", "dialog");
  el.setAttribute("aria-label", opts.title);
  el.innerHTML = `
    <div class="float-head">
      <span class="float-grip" aria-hidden="true">${icon("dot-grid", 13)}</span>
      <span class="float-title"></span>
      <button class="rail-x float-x" type="button"></button>
    </div>
    <div class="float-body"></div>`;
  document.body.appendChild(el);
  const titleEl = el.querySelector<HTMLElement>(".float-title")!;
  const body = el.querySelector<HTMLElement>(".float-body")!;
  const xBtn = el.querySelector<HTMLButtonElement>(".float-x")!;
  titleEl.textContent = opts.title;
  xBtn.textContent = "✕";
  xBtn.setAttribute("aria-label", opts.closeLabel);
  xBtn.setAttribute("data-tip", opts.closeLabel);

  // 이미 옮겨 본 적이 있으면 그 자리를 유지한다 — 노드마다 카드가 튀어 다니면
  // 사용자가 방금 치워 둔 자리가 매번 무너진다.
  const start = moved && lastPos ? lastPos : { x: opts.x, y: opts.y };
  clamp(el, start.x, start.y);

  live = { el, onClose: opts.onClose };
  xBtn.addEventListener("click", () => closeFloatPanel());

  // 머리를 잡고 끌기. 포인터 캡처라 커서가 WebGL 캔버스 위로 넘어가도 끊기지 않는다.
  const head = el.querySelector<HTMLElement>(".float-head")!;
  head.addEventListener("pointerdown", (e) => {
    if ((e.target as HTMLElement).closest(".float-x")) return;
    e.preventDefault();
    head.setPointerCapture(e.pointerId);
    const r = el.getBoundingClientRect();
    const dx = e.clientX - r.left, dy = e.clientY - r.top;
    const move = (ev: PointerEvent) => {
      moved = true;
      clamp(el, ev.clientX - dx, ev.clientY - dy);
    };
    const up = () => {
      head.removeEventListener("pointermove", move);
      head.removeEventListener("pointerup", up);
      const b = el.getBoundingClientRect();
      lastPos = { x: b.left, y: b.top };
    };
    head.addEventListener("pointermove", move);
    head.addEventListener("pointerup", up);
  });

  // 창이 줄면 카드가 밖으로 나가 있을 수 있다 — 다시 접어 넣는다.
  const onResize = () => { const b = el.getBoundingClientRect(); clamp(el, b.left, b.top); };
  window.addEventListener("resize", onResize);
  const origClose = opts.onClose;
  live.onClose = () => { window.removeEventListener("resize", onResize); origClose?.(); };

  return {
    el, body,
    setTitle(t: string) { titleEl.textContent = t; el.setAttribute("aria-label", t); },
    place(x: number, y: number) { if (!moved) clamp(el, x, y); },
    close: closeFloatPanel,
  };
}
