/** 물러남(recede) — 값을 읽는 **유일한 런타임 seam** (DEC D32-a).
 *
 *  정본은 `app.css` 의 `--recede-line/.12 · --recede-text/.35`
 *  한 곳이고, 뷰 코드에는 숫자 리터럴을 두지 않는다는 것이 그 규율이다. 읽기를
 *  여기로 모으는 이유: view2d(sigma)·view3d(three) 는 둘 다 CSS 를 타지 않는
 *  WebGL 이라 각자 값을 알아야 하는데, 각자 알아내게 두면 한쪽이 어긋난다 —
 *  실제로 view3d 는 `base × 0.15` 리터럴로 정본(0.40)의 **3.75 배**를 물러나
 *  hover 시 비이웃 노드가 배경 대비 1.15:1 로 소멸했다(가시성 연구 C4).
 *
 *  ⚠ `getComputedStyle` 은 레이아웃을 읽는 호출이라 **루프 안에서 부르지 말 것**.
 *  hover 핸들러처럼 여러 마크를 도는 자리에서는 루프 밖에서 한 번 받아 쓴다.
 *
 *  `node` 는 없다 — 노드 몸통은 물러나지 않는다(제 색 그대로).
 *  근거: [[decision:a-node-keeps-its-own-colour-in-every-state]]
 */
export type RecedeKind = "line" | "text";

/** D32-a 정본값. CSS 를 읽을 수 없는 환경(테스트·비브라우저)에서만 쓰이는 폴백이며,
 *  `app.css` 의 `:root` 값과 **같아야 한다** — 어긋나면 게이트가 잡는다. */
const FALLBACK: Record<RecedeKind, number> = { line: 0.12, text: 0.35 };

export function recede(name: RecedeKind): number {
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue(`--recede-${name}`);
    const n = Number.parseFloat(v);
    return Number.isFinite(n) ? n : FALLBACK[name];
  } catch {
    return FALLBACK[name];
  }
}
