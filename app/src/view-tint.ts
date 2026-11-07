import { topicJewels, type ThemeMode } from "./theme";
import { areaKeys } from "./area-key";

/** 주제 색 — **영역이 보석 하나를 입고, 그 안의 주제는 그 보석을 몇 도씩 돌린 색**을 입는다.
 *  보석 값은 공홈 조형과 같은 토큰이다(루비·사파이어·에메랄드·호박·자수정). **늘 켜져 있다.**
 *
 *  주제마다 보석을 하나씩 순서대로 주던 동안 주제 174개가 다섯 보석을 20° 씩 돌려 받아 전체
 *  화면이 아무 뜻 없는 무지개였다. 3D 가 주제 칸을 영역별로 모으므로(view-layout-spatial) 색도
 *  자리와 같은 말을 한다: 같은 계열 = 같은 영역. 영역 안의 이웃 주제는 돌린 각도로 갈린다.
 *  근거: [[decision:topics-wear-their-area-jewel-2026-10-28]]
 *
 *  영역은 **칠하는 집합**의 공통 접두 다음 단계다(`areaKeys`, 레이아웃과 같은 규칙). 그래서
 *  영역 하나만 화면에 있으면 그 안의 주제가 각자 온전한 보석을 받는다 — 집합이 바뀌면 같은
 *  주제가 다른 색이 될 수 있다는 성질은 예전과 같다. 모든 표면(3D·2D·타임라인·번들·노드 카드·
 *  좌측 트리)이 이 함수 하나를 읽는다.
 *
 *  **값은 토큰이다**(`topicJewels` in design-tokens.json). 대비 바닥이 곧 그 값이라(다크는
 *  밝힌 벌로 배경 대비 5.5:1 이상, 라이트는 짙은 벌로 3:1 이상) 앱 코드의 리터럴로 두면
 *  디자인 게이트 밖으로 샌다. 주제의 돌림은 ±12° 안이라 채도·명도가 그대로다. */

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
  return tintPlan(domains, mode).topics;
}

/** 영역 안 k 번째 주제의 돌림(도). 0 은 영역 보석 그대로고, 일곱 칸마다 되돌아온다 — 더 넓히면
 *  이웃 영역(20° 돌린 다음 벌)과 겹친다. */
const TOPIC_TURN = [0, 4, -4, 8, -8, 12, -12];

/** 색 한 벌의 전부 — 주제 색, 영역 색, 주제의 영역. 3D 의 영역 부피와 영역 사이 선이 주제 색과
 *  **같은 영역 판정**을 읽어야 해서 따로 내놓는다(판정이 두 벌이면 부피와 색이 갈린다). */
export function tintPlan(domains: (string | undefined)[], mode: ThemeMode): {
  topics: Map<string, string>; areas: Map<string, string>; areaOf: Map<string, string>;
} {
  const uniq = [...new Set(domains.map((d) => (d || "").trim()).filter(Boolean))].sort();
  const list = topicJewels[mode];
  const keys = areaKeys(uniq);
  const areaList = [...new Set(keys)].sort();
  const areas = new Map(areaList.map((a, i) => [a, jewelAt(list, i)]));
  const seen = new Map<string, number>();
  const topics = new Map<string, string>(), areaOf = new Map<string, string>();
  uniq.forEach((d, i) => {
    const a = keys[i], k = seen.get(a) ?? 0;
    seen.set(a, k + 1);
    const base = areas.get(a)!, turn = TOPIC_TURN[k % TOPIC_TURN.length];
    topics.set(d, turn ? rotateHue(parseInt(base.slice(1), 16), turn) : base);
    areaOf.set(d, a);
  });
  return { topics, areas, areaOf };
}

/** 주제 표식: 속이 빈 점선 고리. 주제는 노드가 아니라 노드를 담는 것이라 꽉 찬 원(개념)과 다르게 그린다.
 *  그래프 · 범례 · 고정 칩 · 사이드바가 모두 이 함수 하나로 그린다(한 가족으로 보이게).
 *  점과 틈은 둘레에 비례한다: 한 칸 약 4.2 단위에 점 55 %, 틈 45 %. 끝은 각지게(butt) 그어 틈이 보이게 한다
 *  (둥근 끝은 굵기만큼 점을 늘려 틈을 메우고, 크게 보면 구슬을 이은 고리가 된다). */
export function topicRing(r: number, colour: string, width: number, cls = ""): string {
  const n = Math.max(8, Math.round((2 * Math.PI * r) / 4.2)), per = (2 * Math.PI * r) / n, dash = per * 0.55;
  return `<circle${cls ? ` class="${cls}"` : ""} cx="0" cy="0" r="${r}" fill="none" style="stroke:${colour}" stroke-width="${width}"`
    + ` stroke-dasharray="${dash.toFixed(2)} ${(per - dash).toFixed(2)}" stroke-linecap="butt"/>`;
}

/** HTML 안에 놓는 작은 주제 표식(사이드바 줄). 가로 · 세로 `size` px, 색은 currentColor 를 따른다. */
export function topicRingIcon(size = 11, width = 2): string {
  const h = size / 2, r = h - width / 2;
  return `<svg viewBox="${-h} ${-h} ${size} ${size}" width="${size}" height="${size}" aria-hidden="true" focusable="false">${topicRing(r, "currentColor", width)}</svg>`;
}
