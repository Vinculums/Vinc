/** 화면 위에 겹친 층 가운데 맨 위가 무엇인가 — 시트의 Tab 덫과 Esc 가 이 판정 하나를 쓴다.
 *
 *  층은 둘이다: 시트와 작은 다이얼로그의 딤(`.sheet-scrim`), 그리고 딤 밖에서 직접 그린
 *  모달 화면(`aria-modal="true"`: 로그인 화면 · 기기 그래프 알림 같은 것). 시트 상자 자신도
 *  `aria-modal` 이지만 자기 딤 안에 있으므로 따로 세지 않는다. 문서 순서의 마지막이 맨 위다.
 *  ⚠ 딤만 세면 로그인 화면이 시트 위에 떠도 아래 시트가 자기를 맨 위로 알고 Tab 을 끌어간다.
 *  ⚠ DOM 을 직접 부르지 않는다(후보 목록과 `matches`·`closest` 만 쓴다): `app/dev/layers-check.ts`
 *  가 노드에서 이 판정을 돌린다. */
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
