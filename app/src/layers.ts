/** 화면 위에 겹친 층 가운데 맨 위가 무엇인가 — 시트의 Tab 덫과 Esc 가 이 판정 하나를 쓴다.
 *
 *  층은 둘이다: 시트와 작은 다이얼로그의 딤(`.sheet-scrim`), 그리고 딤 밖에서 직접 그린
 *  모달 화면(`aria-modal="true"`: 로그인 화면 · 기기 그래프 알림 같은 것). 시트 상자 자신도
 *  `aria-modal` 이지만 자기 딤 안에 있으므로 따로 세지 않는다. 문서 순서의 마지막이 맨 위다.
 *  ⚠ 딤만 세면 로그인 화면이 시트 위에 떠도 아래 시트가 자기를 맨 위로 알고 Tab 을 끌어간다.
 *  ⚠ DOM 을 직접 부르지 않는다(후보 목록과 `matches`·`closest`, 받은 뿌리의 질의만 쓴다):
 *  `app/dev/layers-check.ts` 가 노드에서 이 파일을 돌린다. */
export const LAYER_SELECTOR = '.sheet-scrim, [aria-modal="true"]';

type LayerLike = { matches(selector: string): boolean; closest(selector: string): unknown };

/** 후보(문서 순서) 가운데 맨 위 층. 없으면 null. */
export function topLayerOf<T extends LayerLike>(candidates: ArrayLike<T>): T | null {
  for (let i = candidates.length - 1; i >= 0; i--) {
    const el = candidates[i];
    if (el.matches(".sheet-scrim") || !el.closest(".sheet-scrim")) return el;
  }
  return null;
}

/** 딤 없이 뜨는 메뉴와 팝오버: 우클릭 메뉴 · 공간 전환 메뉴 · 검색 모드 메뉴 · 그래프 머리의 시간과 범례 창.
 *  떠 있는 동안 표의 단축키가 쉬고(`keys.shortcutsRest`), 시트의 Esc 는 이것에 먼저 양보한다.
 *  숨김으로 닫는 것(모드 메뉴 · 시간과 범례 창)은 아래 `shown` 이 거른다. */
export const MENU_SELECTOR = ".ctx-menu, .space-menu, #tb-modemenu, .cmp-pop";
/** 그 메뉴를 연 단추(열려 있는 동안 `aria-expanded="true"`). 시트가 열릴 때 이 단추를 눌러 메뉴를 자기 길로
 *  닫는다. 우클릭 메뉴는 연 단추가 없어 `components.ts` 가 직접 닫는다. */
export const MENU_OPENER_SELECTOR =
  '.space-switch[aria-expanded="true"], #tb-mode[aria-expanded="true"], button[data-pop][aria-expanded="true"]';
/** 단축키를 쉬게 하는 나머지 층: 열린 팔레트와 딤 밖의 모달 화면(로그인 · 기기 그래프 알림). */
const SCREEN_SELECTOR = '#palette, [aria-modal="true"]';

type Probe = { closest(selector: string): unknown; getClientRects(): { length: number } };
type Root = { querySelectorAll(selector: string): ArrayLike<Probe> };

/** 화면에 보이는가: 숨긴 조상이 없고 상자가 하나라도 있다(CSS 로 감춘 것도 거른다). */
const shown = (el: Probe): boolean => !el.closest("[hidden]") && el.getClientRects().length > 0;

/** 메뉴나 팝오버가 하나라도 떠 있는가. */
export function menuOpenIn(root: Root): boolean {
  return Array.from(root.querySelectorAll(MENU_SELECTOR)).some(shown);
}

/** 시트 말고 단축키를 쉬게 하는 층이 떠 있는가: 메뉴 · 팝오버 · 팔레트 · 딤 밖의 모달 화면.
 *  시트와 작은 다이얼로그는 여기서 세지 않는다: 그것들은 열 때 잡고 닫기를 시작할 때 놓으므로
 *  (`keys.holdShortcuts`), 퇴장 애니메이션 동안 딤이 남아 있어도 단축키는 이미 돈다.
 *  `root` 는 앱에서 `document` 다. */
export function openLayerIn(root: Root): boolean {
  return menuOpenIn(root)
    || Array.from(root.querySelectorAll(SCREEN_SELECTOR)).some((el) => !el.closest(".sheet-scrim") && shown(el));
}
