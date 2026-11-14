// "Ask an assistant" 줄과 메뉴: 카드 · 레일 · 문서 읽기가 같은 한 벌(줄의 마크업, 노드 · 문서의 프롬프트 셋, 배선)을 쓴다.
// 메뉴는 body 에 뜨므로 그 메뉴와 문서 클릭 리스너는 이 모듈의 주인(AbortController)이 거둔다.
// 줄의 캐럿(`chev`)과 노드 하나를 건네는 반경(`SHARE_DEPTH`)도 여기 둔다. 표면(`view.ts`)은 같은 값을 여기서 가져다 쓴다.
import { spaceName } from "../api";
import { COPY } from "../copy";
import { esc } from "../components";
import { icon } from "../icons";
import { pushLayer } from "../keys";
import { isAgentRoleId } from "../agent-roster";
import { absoluteViewUrl } from "../view-url";
import type { N3 } from "./view-host";

/** 메뉴 한 칸 — 이름·부제·담아 갈 프롬프트·따라갈 주소. */
export type AskItem = { name: string; sub: string; body: string; url: string };

/** 노드 하나를 팩으로 건넬 때의 기본 반경. 진입점이 셋(우클릭 메뉴 · 상세 레일 ·
 *  상단 바)이라 값이 흩어져 있으면 어디서 보냈느냐에 따라 받는 쪽 그림이 달라진다. */
export const SHARE_DEPTH = 2;

/** 캐럿이 **목적지를 말한다**: 오른쪽 = 여기서 나간다(문서 열), 아래 = 여기서
 *  펼친다. 디자인 정본 `Vinc Node Card.dc.html` 의 규칙이고, 그래서 같은 모양의
 *  두 줄이 서로 다른 일을 한다는 것이 눌러 보기 전에 읽힌다. */
export const chev = (dir: "down" | "right") =>
  `<span class="row-chev row-chev--${dir}" aria-hidden="true">${icon("chevron-left", 12)}</span>`;

/** "Ask an assistant" 한 줄 — **카드와 레일이 같은 것을 쓴다.**
 *
 *  디자인 정본은 카드만 규정하지만, 레일에도 같은 일을 하는 자리가 있다. 두 곳이 다른
 *  모양으로 같은 일을 하면 어느 쪽이 맞는지 매번 다시 정해야 하므로 한 벌로 둔다.
 *  마크업도 클래스도 같고, 놓이는 자리만 다르다. */
export function askRowHTML(): string {
  const V = COPY.ui.viewx;
  return `<div class="card-row card-row--static ask-row">
        <span class="row-name">${V.cardAsk}</span>
        <button type="button" class="card-ctl js-ask" aria-expanded="false"
                aria-haspopup="menu">${V.cardAskCopy}${chev("down")}</button>
      </div>`;
}
/** 노드에서 여는 셋. **에이전트 역할이면 한 칸이 더 붙는다.**
 *  그 노드는 개념이 아니라 시킬 일이라, 설명을 청하는 것이 아니라 그 역할을
 *  맡기는 말이 첫 칸에 온다. 판별은 주제가 아니라 역할 id 로 한다(`isAgentRoleId`,
 *  레일·상세 패널과 같은 함수). Default Agents 역할은 `vinc/agent` 에 서 있어 주제
 *  일치로 가리면 메뉴가 붙지 않는다. */
export function nodeAskItems(nd: N3, deg: number, docs: number): AskItem[] {
  const V = COPY.ui.viewx;
  const at = (depth: number) => absoluteViewUrl({ kind: "node", id: nd.id }, { depth }, spaceName());
  const items: AskItem[] = [
    { name: V.askExplain, sub: V.askExplainSub, body: V.askExplainBody, url: at(1) },
    { name: V.askCompare, sub: V.askCompareSub(deg), body: V.askCompareBody, url: at(SHARE_DEPTH) },
    { name: V.askDocs, sub: V.askDocsSub(docs), body: V.askDocsBody, url: at(1) },
  ];
  if (isAgentRoleId(nd.id)) {
    items.unshift({ name: V.askRole, sub: V.askRoleSub, body: V.askRoleBody, url: at(1) });
  }
  return items;
}
/** 문서에서 여는 셋. 주소는 `kind: "doc"` 갈래다. */
export function docAskItems(docId: string): AskItem[] {
  const V = COPY.ui.viewx;
  const url = absoluteViewUrl({ kind: "doc", id: docId }, {}, spaceName());
  return [
    { name: V.askDocSum, sub: V.askDocSumSub, body: V.askDocSumBody, url },
    { name: V.askDocConcepts, sub: V.askDocConceptsSub, body: V.askDocConceptsBody, url },
    { name: V.askDocCheck, sub: V.askDocCheckSub, body: V.askDocCheckBody, url },
  ];
}

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
