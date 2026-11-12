// "Ask an assistant" 메뉴의 배선: 카드 · 레일 · 문서 읽기가 같은 한 벌을 쓴다.
// 메뉴는 body 에 뜨므로 그 메뉴와 문서 클릭 리스너는 이 모듈의 주인(AbortController)이 거둔다.
import { COPY } from "../copy";
import { esc } from "../components";
import { pushLayer } from "../keys";

/** 메뉴 한 칸 — 이름·부제·담아 갈 프롬프트·따라갈 주소. */
export type AskItem = { name: string; sub: string; body: string; url: string };

/** 지금 body 에 떠 있는 메뉴의 주인. 새 줄이 매달리면 앞 주인을 거둔다(메뉴 · 단추 손 · 문서 리스너). */
let current: AbortController | null = null;

/** 그 줄에 동작을 매단다. 반환값으로 수치를 나중에 채운다(응답이 오면 부제가 산다).
 *
 *  메뉴는 **스크롤 컨테이너 밖**(body)에 뜬다. 카드는 `overflow:hidden`, 그 본문은
 *  `overflow:auto` 라 안에 두면 세 항목 중 하나만 보였다(실측 2026-08-27). 레일도
 *  같은 사정이라 한 벌로 두는 편이 맞다. 아래가 모자라면 위로 뒤집는다.
 *
 *  프롬프트 셋은 번호가 아니라 **이름**이다. 숫자(깊이 1/2/3)는 크기를 말해 주지 않아
 *  늘 경고를 달고 다녀야 했는데, 이름은 무엇을 담아 가는지 스스로 말한다.
 *  근거: 디자인 정본 `Vinc Node Card.dc.html` (2b).
 *
 *  문서 클릭 리스너는 **메뉴가 열려 있는 동안만** 붙는다. 닫히면(바깥 클릭 · Esc · 항목 선택)
 *  떼고, 붙어 있던 표면이 사라졌거나 다음 줄이 매달리면 메뉴와 함께 거둔다. 그래서 문서에
 *  남는 리스너는 많아야 하나다. */
export function wireAsk(scope: HTMLElement, alive: () => boolean,
                        build: (deg: number, docs: number) => AskItem[]):
                        (deg: number, docs: number) => void {
  const V = COPY.ui.viewx;
  const trigger = scope.querySelector<HTMLButtonElement>(".js-ask");
  if (!trigger) return () => {};
  current?.abort();                                         // 앞 메뉴와 그 리스너를 거둔다
  document.getElementById("ask-menu-live")?.remove();      // 한 번에 하나만 뜬다
  const owner = new AbortController();
  current = owner;
  const menu = document.createElement("div");
  menu.id = "ask-menu-live";
  menu.className = "card-menu";
  menu.setAttribute("role", "menu");
  menu.hidden = true;
  document.body.appendChild(menu);

  const label = (s: string) => {
    if (trigger.firstChild) trigger.replaceChild(document.createTextNode(s), trigger.firstChild);
  };
  // 열린 메뉴는 층의 쌓임에 오른다: 맨 위일 때 Esc 는 메뉴만 닫고(레일은 남는다) 초점을 단추로 돌린다.
  let layer: (() => void) | null = null;
  // 열려 있는 동안의 문서 리스너 주인. 닫힘이 곧 떼기다.
  let outside: AbortController | null = null;
  const close = () => {
    menu.hidden = true; trigger.setAttribute("aria-expanded", "false");
    outside?.abort(); outside = null;
    layer?.(); layer = null;
  };
  owner.signal.addEventListener("abort", () => { close(); menu.remove(); });
  const onDocClick = (e: Event) => {
    // 붙어 있던 표면이 사라졌으면 메뉴도 데려간다 — body 에 있으니 저절로 안 없어진다.
    if (!alive()) { owner.abort(); return; }
    if (!(e.target as HTMLElement).closest(".js-ask,#ask-menu-live")) close();
  };
  const place = () => {
    const r = trigger.getBoundingClientRect();
    const w = 246, gap = 6;
    menu.style.left = `${Math.max(8, Math.min(r.right - w, window.innerWidth - w - 8))}px`;
    const below = window.innerHeight - r.bottom;
    menu.style.top = below > 160 || r.top < 170
      ? `${r.bottom + gap}px` : `${Math.max(8, r.top - gap - menu.offsetHeight)}px`;
  };
  trigger.addEventListener("click", () => {
    if (!menu.hidden) { close(); return; }
    menu.hidden = false;
    place();
    trigger.setAttribute("aria-expanded", "true");
    layer ??= pushLayer({ menu: true, alive: () => menu.isConnected && !menu.hidden && trigger.isConnected,
      escape: () => { close(); trigger.focus(); } });
    if (!outside) {
      outside = new AbortController();
      document.addEventListener("click", onDocClick, { signal: outside.signal });
    }
  }, { signal: owner.signal });

  return (deg: number, docs: number) => {
    const items = build(deg, docs);
    menu.innerHTML = items.map((it, i) => `
      <button type="button" role="menuitem" class="menu-item" data-i="${i}">
        <span class="menu-name">${esc(it.name)}</span>
        <span class="menu-sub">${esc(it.sub)}</span>
      </button>`).join("");
    menu.querySelectorAll<HTMLElement>(".menu-item").forEach((b) =>
      b.addEventListener("click", () => {
        const it = items[Number(b.dataset.i)];
        // 되먹임은 트리거 버튼에 뜨고 메뉴는 닫힌다(디자인 2b 주석).
        label(V.copyLinkDone);
        window.setTimeout(() => label(V.cardAskCopy), 1400);
        close();
        void navigator.clipboard.writeText(`${it.body} ${it.url}`)
          .catch(() => label(V.copyLinkFail));
      }));
  };
}
