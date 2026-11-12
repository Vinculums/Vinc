import { palette, topicJewels, type ThemeMode } from "./theme";
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
 *  **값은 토큰이다**(`topicJewels` in design-tokens.json). 앱 코드는 색을 새로 짓지 않고, 토큰
 *  보석을 돌린(`rotateHue`) 뒤, 마크 바닥을 못 넘는 색만 색상각을 지킨 채 바닥까지 옮긴다(`planColour` →
 *  `markColour`). 그래서 모든 색이 사이드바 줄의 어느 면(판 · 서랍 카드, 쉼 · 고름 칠)
 *  위에서도 비텍스트 바닥(`MARK_FLOOR`)을 넘고, 그래프 · 범례 · 홈 목록 · Agent 보드도 같은 값을 받는다. */

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


/** 영역 안 k 번째 주제의 돌림(도). 0 은 영역 보석 그대로고, 일곱 칸마다 되돌아온다 — 더 넓히면
 *  이웃 영역(20° 돌린 다음 벌)과 겹친다. */
const TOPIC_TURN = [0, 4, -4, 8, -8, 12, -12];

/** a 번째 영역의 k 번째 주제가 **바닥 전에** 입는 색: 보석을 영역 차례만큼, 다시 주제 차례만큼 돌린 것. */
function rawPlanColour(a: number, k: number, mode: ThemeMode): string {
  const base = jewelAt(topicJewels[mode], a), turn = TOPIC_TURN[k % TOPIC_TURN.length];
  return turn ? rotateHue(parseInt(base.slice(1), 16), turn) : base;
}
/** a 번째 영역의 k 번째 주제가 입는 마크 색. 계획은 고정된 차례로 색을 주므로 이 값은 (a, k, 모드)만의 함수다 —
 *  주제가 늘어도 다른 주제의 색은 계획이 원래 바꾸던 만큼만 바뀐다. 바닥을 이미 넘는 색은 계획 그대로이고,
 *  못 넘는 색만 같은 색상각에서 바닥을 막 넘는 값으로 옮긴다(`markColour`: 탐색이 찾은 것 가운데 원래 색에
 *  가장 가까운 값). 옮긴 색끼리나 제자리 색과의 거리는 지키지 않는다: 재고(`dev/topic-tint-contrast.mjs`)
 *  테스트가 지금보다 나빠지지 않게 묶는다. */
export function planColour(a: number, k: number, mode: ThemeMode): string {
  return markColour(rawPlanColour(a, k, mode), mode);
}

/** 색 한 벌의 전부 — 주제 색, 영역 색, 주제의 영역. 3D 의 영역 부피와 영역 사이 선이 주제 색과
 *  **같은 영역 판정**을 읽어야 해서 따로 내놓는다(판정이 두 벌이면 부피와 색이 갈린다). */
export function tintPlan(domains: (string | undefined)[], mode: ThemeMode): {
  topics: Map<string, string>; areas: Map<string, string>; areaOf: Map<string, string>;
} {
  const uniq = [...new Set(domains.map((d) => (d || "").trim()).filter(Boolean))].sort();
  const keys = areaKeys(uniq);
  const areaList = [...new Set(keys)].sort();
  const rank = new Map(areaList.map((a, i) => [a, i]));
  const areas = new Map(areaList.map((a, i) => [a, planColour(i, 0, mode)]));
  const seen = new Map<string, number>();
  const topics = new Map<string, string>(), areaOf = new Map<string, string>();
  uniq.forEach((d, i) => {
    const a = keys[i], k = seen.get(a) ?? 0;
    seen.set(a, k + 1);
    topics.set(d, planColour(rank.get(a)!, k, mode));
    areaOf.set(d, a);
  });
  return { topics, areas, areaOf };
}

// ── 마크 바닥 ────────────────────────────────────────────────────────────────
/** 주제 색을 입은 마크(점 · 고리 · 종류 표식 · Agent · 정책 점)는 장식이라 **비텍스트 바닥 3:1**(WCAG 1.4.11)을 진다.
 *  글자와 주제 색이 없는 마크(잉크 대체값 · 고른 줄의 `--ring`)는 이 값과 상관없이 4.5:1 이다(app.css). 바닥은 사이드바 줄이
 *  앉는 면 전부다: 판(`--surface-0`: 라이트 muted · 다크 background)과 좁은 창 서랍(`--surface-2` = card),
 *  각각 맨 면 · 쉼(글자색 .07, `.lnb-doc:hover`) · 고름(링 .16, `.lnb-group.on`). app.css 의 그 알파나
 *  면 매핑이 바뀌면 여기도 같이 바뀌어야 한다 — `tests/topic-tint-contrast.test.mjs` 가 CSS 에서 면을
 *  따로 지어 이 값과 맞대므로, 대비가 낮아지는 쪽으로 어긋나면 빌드가 멈춘다(면이 밝아지는 쪽으로 어긋나면
 *  색을 필요보다 조금 더 옮길 뿐이라 바닥 테스트는 지나가고, 옮긴 거리 테스트가 그 몫을 묶는다). 그래프 바탕과 범례 카드는 이 면 가운데 하나와 같거나(범례 카드 =
 *  서랍 카드, 다크 그래프 바탕 = 다크 판) 그보다 바깥쪽(라이트 그래프 바탕은 판보다 밝다)이라 여기를 넘으면 거기도 넘는다. */
const MARK_FLOOR = 3;
/** 면은 화면이 그리는 값으로 짓는다(`drawnRgb`, 줄 칠은 실수로 섞어 8비트로). 화면이 줄 칠을 섞어 놓는 픽셀은 그 값과
 *  채널마다 8비트 한 칸쯤 어긋난다. 묶는 면(라이트 고른 줄)에서 지은 값이 대비를 0.0046 낙관하고, 다크의 묶는 면
 *  (서랍 고른 줄)에서는 비관적이다(0). 그 낙관만큼을 위에 둔다(잰 값: `dev/topic-tint-contrast.mjs` 가 아닌 화면 픽셀). */
const GROUND_EPSILON = 0.005;
const ROW_HOVER = 0.07, ROW_PICK = 0.16;

type Rgb = [number, number, number];
const rgbOf = (n: number): Rgb => [(n >> 16) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
const lin = (v: number) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
const gam = (v: number) => (v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055);
const lum = (c: Rgb) => 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2]);
/** 알파 칠을 바닥에 얹는다(감마 공간에서 섞고 8비트로 내린다). */
const over = (fg: Rgb, a: number, bg: Rgb): Rgb => [0, 1, 2].map((i) => Math.round((fg[i] * a + bg[i] * (1 - a)) * 255) / 255) as Rgb;
/** 파이썬 `round` 와 같은 반올림(정확히 반이면 짝수로). 토큰 생성기가 HSL 채널을 이렇게 내린다. */
const roundHalfEven = (x: number) => { const f = Math.floor(x), d = x - f; return d > 0.5 + 1e-9 ? f + 1 : d < 0.5 - 1e-9 ? f : f % 2 === 0 ? f : f + 1; };
/** 토큰 HEX 를 화면이 그리는 값으로: 토큰 CSS 는 채널을 정수로 반올림한 HSL 로 적고(build_tokens.py `hsl_channels`),
 *  브라우저는 그 HSL 을 8비트로 내린다. HEX 를 그대로 쓰면 대비가 0.01~0.03 어긋난다. */
function drawnRgb(n: number): Rgb {
  const [r, g, b] = rgbOf(n), mx = Math.max(r, g, b), mn = Math.min(r, g, b), l = (mx + mn) / 2, d = mx - mn;
  let h = 0, s = 0;
  if (d > 0) {
    s = l <= 0.5 ? d / (mx + mn) : d / (2 - mx - mn);
    const rc = (mx - r) / d, gc = (mx - g) / d, bc = (mx - b) / d;
    h = ((((r === mx ? bc - gc : g === mx ? 2 + rc - bc : 4 + gc - rc) / 6) % 1) + 1) % 1;
  }
  const H = roundHalfEven(h * 360) % 360, S = roundHalfEven(s * 100) / 100, L = roundHalfEven(l * 100) / 100;
  const k = (m: number) => (m + H / 30) % 12, a = S * Math.min(L, 1 - L);
  const f = (m: number) => Math.round((L - a * Math.max(-1, Math.min(k(m) - 3, 9 - k(m), 1))) * 255) / 255;
  return [f(0), f(8), f(4)];
}

const groundCache = new Map<ThemeMode, number[]>();
/** 모드의 바닥 휘도들(화면이 그리는 값). */
function groundLums(mode: ThemeMode): number[] {
  let g = groundCache.get(mode);
  if (g) return g;
  const p = palette[mode], base = drawnRgb(mode === "light" ? p.surface : p.background), drawer = drawnRgb(p.card);
  g = [base, drawer].flatMap((s) => [s, over(drawnRgb(p.text), ROW_HOVER, s), over(drawnRgb(p.ring), ROW_PICK, s)]).map(lum);
  groundCache.set(mode, g);
  return g;
}
const worstRatio = (c: Rgb, mode: ThemeMode) => Math.min(...groundLums(mode).map((b) => {
  const a = lum(c); return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}));

/** OKLab ↔ sRGB(감마). 색상각을 지키며 명도만 옮기려고 쓴다. */
function toLab(c: Rgb): Rgb {
  const [r, g, b] = c.map(lin);
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  return [0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s];
}
/** 범위 밖이면 null. */
function fromLab(L: number, a: number, b: number): Rgb | null {
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  const out = [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s];
  return out.every((v) => v >= -1e-7 && v <= 1 + 1e-7) ? (out.map((v) => gam(Math.min(1, Math.max(0, v)))) as Rgb) : null;
}
/** 명도 L, 색상각 h 에서 준 채도 C 를 넘지 않는 색: 색역 안이면 그대로, 밖이면 채도만 깎는다. */
function atL(L: number, C: number, h: number): Rgb {
  const at = (c: number) => fromLab(L, c * Math.cos(h), c * Math.sin(h));
  const full = at(C);
  if (full) return full;
  let lo = 0, hi = C;
  for (let i = 0; i < 24; i++) { const mid = (lo + hi) / 2; if (at(mid)) lo = mid; else hi = mid; }
  return at(lo)!;
}
const toHex = (c: Rgb) => `#${c.map((v) => Math.round(v * 255).toString(16).padStart(2, "0")).join("")}`;
const markCache = new Map<string, string>();

/** 보석 하나를 마크 바닥까지 옮긴다: 이미 넘으면 그대로, 아니면 **같은 색상각**(OKLab hue)에서 바닥을 넘는
 *  가장 가까운 값(OKLab 거리)으로. 채도 몫마다(0.5~1.1, 0.02 칸) 명도를 바닥까지 이분 탐색하고(30 번, 명도 오차
 *  1e-9 아래) 원래 색에 가장 가까운 것을 고른다. 라이트는 어두운 쪽, 다크는 밝은 쪽으로 간다. 재는 값은 화면에
 *  놓이는 8비트 색이다. 결과는 이 탐색 격자 안에서 가장 가까운 값이지, 8비트 색을 모두 뒤진 최근접은 아니다:
 *  그 최근접보다 원래 색에서 더 멀어지는 몫은 `tests/topic-tint-contrast.test.mjs` 가 CIEDE2000 0.5 아래로 묶는다. */
function markColour(hexColour: string, mode: ThemeMode): string {
  const key = `${mode}${hexColour}`, hit = markCache.get(key);
  if (hit) return hit;
  const need = MARK_FLOOR + GROUND_EPSILON, src = rgbOf(parseInt(hexColour.slice(1), 16));
  let out = hexColour.toLowerCase();
  if (worstRatio(src, mode) < need) {
    const lab0 = toLab(src), [L, a, b] = lab0, C = Math.hypot(a, b), h = Math.atan2(b, a), end = mode === "light" ? 0 : 1;
    const q = (c: Rgb): Rgb => c.map((v) => Math.round(v * 255) / 255) as Rgb;   // 화면에 놓이는 8비트 값으로 잰다
    let best = Infinity;
    for (let cs = 0.5; cs <= 1.1 + 1e-9; cs += 0.02) {
      let lo = 0, hi = 1;
      for (let i = 0; i < 30; i++) { const t = (lo + hi) / 2; if (worstRatio(q(atL(L + (end - L) * t, C * cs, h)), mode) >= need) hi = t; else lo = t; }
      const c = q(atL(L + (end - L) * hi, C * cs, h));
      if (worstRatio(c, mode) < need) continue;
      const l1 = toLab(c), d = Math.hypot(l1[0] - lab0[0], l1[1] - lab0[1], l1[2] - lab0[2]);
      if (d < best) { best = d; out = toHex(c); }
    }
  }
  markCache.set(key, out);
  return out;
}

const passes = (hexColour: string, mode: ThemeMode) => worstRatio(rgbOf(parseInt(hexColour.slice(1), 16)), mode) >= MARK_FLOOR + GROUND_EPSILON;
/** 재고 테스트하는 도구(`dev/topic-tint-contrast.mjs`, `tests/topic-tint-contrast.test.mjs`)만 쓴다. 화면 코드는 부르지 않는다. */
export const floorKit = { rawPlanColour, passes, floor: MARK_FLOOR, turns: TOPIC_TURN.length };

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
