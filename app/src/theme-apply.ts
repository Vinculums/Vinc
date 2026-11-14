// 저장된 테마(밝게 · 어둡게 · OS 따라)를 문서에 걸고, 열린 지도와 좌측 트리를 그 모드로 다시 칠한다.
// 진입점(`main.ts`)이 기동 때와 OS · 웹 GNB 의 신호에 부르고, 설정의 테마 단추가 부른다. 색 값은 `theme.ts` 에 있다.
import { repaintView } from "./surfaces/view";
import { repaintLnb } from "./lnb";

const THEME_KEY = "vinc.theme";   // "auto" | "light" | "dark"
// 웹에서는 공홈과 같은 오리진이라 사이트의 테마 키도 함께 본다. 둘이 어긋나면 사이트와
// 앱을 오갈 때마다 밝기가 뒤집혀 번쩍인다. 앱 값이 없으면 사이트 값을 따르고, 정한 밝기는
// 사이트 키에도 적어 둔다(사이트 GNB 의 토글도 두 키를 함께 쓴다).
const SITE_THEME_KEY = "vinc.site.theme.v2";
export function applyTheme() {
  const web = document.documentElement.dataset.skin === "web";
  let pref = "dark";   // D38 — 기본 = 다크
  try {
    pref = localStorage.getItem(THEME_KEY)
      || (web ? localStorage.getItem(SITE_THEME_KEY) : "") || "dark";
  } catch { /* 저장소가 막힌 창은 기본값으로 */ }
  const dark = pref === "dark" || (pref === "auto" &&
    window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  if (web) try { localStorage.setItem(SITE_THEME_KEY, dark ? "dark" : "light"); } catch {}
  // 좌측 트리의 주제 색은 paint 때 굳은 HEX 다(CSS 변수가 아니라 스스로 안 뒤집힌다).
  // 여기서 다시 그리지 않으면 뒤집힌 화면에 반대 모드의 색이 남아 도트가 배경에 묻는다.
  // ⚠ 설정 화면의 클릭 핸들러가 아니라 **여기**여야 한다: pref 가 "auto" 면 `main.ts` 의
  //   matchMedia 가 OS 를 따라 부르므로 설정 클릭이 아예 없는 경로가 생긴다.
  //   마운트 전 첫 호출은 `#lnb-body` 가 없어 paint 가 알아서 되돌아간다.
  // 열린 지도도 같은 사정이다(3D·2D·타임라인·번들 모두 마운트 때 색을 굳힌다). 다시
  // 그리는 것(route·show)이 아니라 **다시 칠하는 것**이라 읽던 문서 열과 카드가 남는다.
  // 근거: [[decision:a-theme-switch-repaints-the-open-view-in-place]]
  // ⚠ 지도가 **먼저**다: 그래프 트리의 색은 나침반의 표(`gsrc.colour`)를 읽으므로, 트리를 먼저 그리면
  //   나침반이 표를 새 모드로 다시 짓기 전의 색(반대 모드)이 트리에 남는다.
  repaintView();
  repaintLnb();
}
