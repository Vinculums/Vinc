import { domainTint as TINT, type ThemeMode } from "./theme";

/** 주제 틴트 — **열린 층**(사용자 토글 `ViewPrefs.domainTint`, 기본 끔).
 *
 *  색은 원래 닫힌 층이다: 팩을 받은 쪽이 색을 다시 매핑하면 보낸 사람과 다른
 *  그래프를 보는데 **아무도 오류를 안 낸다**(정본 §1). 그래서 사용자에게 주는 색은
 *  **뜻을 나르지 않는 것**뿐이다 — 꺼도 아무 정보가 사라지지 않아야 한다. 주제는
 *  이미 자리(hub 부챗살)와 타임라인 lane 이 말하고 있고, 틴트는 그 위에 얹는
 *  색조일 뿐이다. 그래서 기본이 끔이고, 이것만으로 두 노드를 갈라야 하는 화면은
 *  만들지 않는다.
 *
 *  **채도·명도는 토큰**이다(`domainTint` in design-tokens.json). 그 두 값이 곧 틴트의
 *  대비 바닥이라 앱 코드의 리터럴로 두면 디자인 게이트 밖으로 샌다. 명도를 모드별로
 *  잉크 근처에 두는 것이 요지다: 색조만 갈리고 **읽히는 무게는 잉크와 같게** 남는다.
 */

/*  각도 배정 — 해시를 버리고 **순위**로 간 이유(2026-08-15 실측).
 *
 *  처음엔 주제 문자열 해시를 360 으로 나눈 나머지를 각도로 썼다. "주제가 자기 색을
 *  계속 갖는다"가 이유였는데, 실제로 그려 보니 **주제 8개 중 두 쌍이 겹쳤다**:
 *  `vinc/design` 50° 와 `vinc/infra` 58° (8° 차이), `vinc` 291° 와
 *  `neo4j/query-planner` 297° (6° 차이). 해시가 균등해도 n 개를 원에 무작위로
 *  뿌리면 이렇게 붙는다 — 균등 분포는 **고른 간격을 뜻하지 않는다**.
 *
 *  그래서 화면에 있는 주제를 정렬해 **황금각(137.5°)으로 배정**한다. 어떤 n 에서도
 *  간격이 고르게 벌어지는 배치다.
 *
 *  대가는 정직하게 적어 둔다: 주제 집합이 바뀌면(스코프 이동·주제 추가) 각도가
 *  다시 배정되므로 **주제가 늘 같은 색은 아니다.** 그래도 이쪽을 고른 것은, 두
 *  주제가 같은 색으로 보이면 이 기능이 아무 일도 안 하기 때문이다. 한 화면 안의
 *  구별이 세션 간 일관성보다 먼저다 — 뜻을 나르지 않는 색이라 일관성이 계약도
 *  아니다(뜻을 날랐다면 반대로 골랐어야 한다). */
const GOLDEN = 137.508;

function hslHex(h: number, s: number, l: number): string {
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const hp = (((h % 360) + 360) % 360) / 60;
  const x = c * (1 - Math.abs((hp % 2) - 1));
  const m = l - c / 2;
  const seg = Math.floor(hp);
  const [r, g, b] = seg === 0 ? [c, x, 0] : seg === 1 ? [x, c, 0] : seg === 2 ? [0, c, x]
    : seg === 3 ? [0, x, c] : seg === 4 ? [x, 0, c] : [c, 0, x];
  const q = (v: number) => Math.round((v + m) * 255).toString(16).padStart(2, "0");
  return `#${q(r)}${q(g)}${q(b)}`;
}

/** 주제 → 틴트 색. 주제가 없는 노드는 표에 없고, 호출측은 그때 잉크를 그대로 쓴다.
 *
 *  마운트당 한 번 만들어 노드마다 조회한다(노드마다 계산하면 같은 답을 n 번 낸다).
 *  정렬은 사전식 — 같은 집합이면 언제 그려도 같은 그림이다(D17 결정론과 같은 규율). */
export function tintMap(domains: (string | undefined)[], mode: ThemeMode): Map<string, string> {
  const uniq = [...new Set(domains.map((d) => (d || "").trim()).filter(Boolean))].sort();
  const { s, l } = TINT[mode];
  return new Map(uniq.map((d, i) => [d, hslHex(i * GOLDEN, s, l)]));
}
