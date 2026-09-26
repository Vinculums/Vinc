import { topicJewels, type ThemeMode } from "./theme";

/** 주제 색 — 공홈 조형과 같은 **보석색**(루비·사파이어·에메랄드·호박·자수정). **늘 켜져 있다.**
 *
 *  2026-09-26 에 옅은 파스텔 틴트(황금각 색상환, 채도 .4·명도 .72)를 이것으로 갈았다
 *  (발주자: "틴트 색은 버리고, 주제를 가르는 색은 남긴다"). 켜고 끄는 토글도 없앴다 —
 *  토글이 기기별 localStorage 에 살아서 같은 그래프가 노트북마다 색이 있다 없다 했다.
 *  근거: [[decision:topics-wear-the-homepage-jewels-2026-09-26]]
 *
 *  색은 여전히 **뜻을 나르지 않는다**: 주제는 자리(hub 부챗살)와 타임라인 lane 이 이미
 *  말하고, 색은 한 화면 안에서 주제를 가르기만 한다. 그래서 주제 집합이 바뀌면(스코프 이동·
 *  주제 추가) 순위가 다시 매겨져 같은 주제가 다른 보석을 받을 수 있다 — 뜻을 날랐다면
 *  반대로 골랐어야 한다.
 *
 *  **값은 토큰이다**(`topicJewels` in design-tokens.json). 대비 바닥이 곧 그 값이라(다크는
 *  밝힌 벌로 배경 대비 5.5:1 이상, 라이트는 짙은 벌로 3:1 이상) 앱 코드의 리터럴로 두면
 *  디자인 게이트 밖으로 샌다. */

/** 보석 i 번째. 다섯을 넘기면 같은 벌을 **색상각 20° 씩 돌려** 다시 쓴다(+20, −20, +40 …).
 *  두 보석 사이를 섞지 않는 이유: 에메랄드와 호박의 중간은 탁한 올리브다 — 섞은 색은
 *  보석이 아니라 흙이 된다. 돌리면 채도와 명도가 그대로라 대비 바닥도 그대로다. */
function jewelAt(list: readonly number[], i: number): string {
  const base = list[i % list.length];
  const round = Math.floor(i / list.length);
  const turn = round === 0 ? 0 : (round % 2 ? 1 : -1) * Math.ceil(round / 2) * 20;
  return turn ? rotateHue(base, turn) : hex(base);
}

const hex = (n: number) => `#${n.toString(16).padStart(6, "0")}`;

function rotateHue(n: number, deg: number): string {
  const r = (n >> 16) / 255, g = ((n >> 8) & 255) / 255, b = (n & 255) / 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b), l = (max + min) / 2, d = max - min;
  if (d === 0) return hex(n);
  const s = d / (1 - Math.abs(2 * l - 1));
  const h0 = max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  const h = (((h0 * 60 + deg) % 360) + 360) % 360;
  const c = (1 - Math.abs(2 * l - 1)) * s, x = c * (1 - Math.abs(((h / 60) % 2) - 1)), m = l - c / 2;
  const seg = Math.floor(h / 60);
  const [r1, g1, b1] = seg === 0 ? [c, x, 0] : seg === 1 ? [x, c, 0] : seg === 2 ? [0, c, x]
    : seg === 3 ? [0, x, c] : seg === 4 ? [x, 0, c] : [c, 0, x];
  const q = (v: number) => Math.round((v + m) * 255).toString(16).padStart(2, "0");
  return `#${q(r1)}${q(g1)}${q(b1)}`;
}

/** 주제 → 보석색. 주제가 없는 노드는 표에 없고, 호출측은 그때 잉크를 그대로 쓴다.
 *
 *  마운트당 한 번 만들어 노드마다 조회한다(노드마다 계산하면 같은 답을 n 번 낸다).
 *  정렬은 사전식 — 같은 집합이면 언제 그려도 같은 그림이다(D17 결정론과 같은 규율). */
export function tintMap(domains: (string | undefined)[], mode: ThemeMode): Map<string, string> {
  const uniq = [...new Set(domains.map((d) => (d || "").trim()).filter(Boolean))].sort();
  const list = topicJewels[mode];
  return new Map(uniq.map((d, i) => [d, jewelAt(list, i)]));
}
