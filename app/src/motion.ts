/** 모션 한 벌 — 화면 위에 뜨는 것들이 **자기가 나온 곳에서** 자라고 그 자리로 사라진다.
 *
 *  왜 필요한가(발주자 지적 2026-08-05): 시트·모달·패널이 전부 정적으로 느껴졌다.
 *  기존 애니메이션이 없었던 것은 아니고(`.sheet` 0.18s translateY, `#settings-popover`
 *  0.16s scale) **셋 다 화면 중앙에서 제자리 커지기**였다: 어느 버튼이 그것을 열었는지
 *  화면이 말하지 않으므로, 눌렀을 때 "저기서 나왔다"는 인상이 남지 않는다. 게다가
 *  닫기에는 애니메이션이 아예 없어 그냥 사라졌다(등장은 부드럽고 퇴장은 뚝 끊긴다).
 *
 *  이건 UI 라이브러리 유무의 문제가 아니다. shadcn/Radix 를 쓰면 enter/exit 키프레임이
 *  기본으로 딸려 오지만(그래서 "부드럽다"고 느껴진다), 그 물성은 CSS 몇 줄이 만든다 —
 *  아래가 그 몇 줄이다. 우리에게 없던 것은 라이브러리가 아니라 **퇴장 애니메이션과
 *  기준점**이었다.
 *
 *  기준점(transform-origin)을 트리거 버튼의 중심으로 옮기는 것이 핵심이다: macOS 에서
 *  앱이 독 아이콘에서 튀어나오고 다시 그 아이콘으로 빨려 들어가는 것과 같은 단서다.
 *
 *  WAAPI(`Element.animate`)를 쓰는 이유: 닫기를 **기다릴 수 있어야** 한다(애니메이션이
 *  끝난 뒤에 DOM 을 지운다). CSS 클래스로 하면 transitionend 를 붙였다 떼는 배선이
 *  호출부마다 반복된다.
 *
 *  `prefers-reduced-motion` 이면 **움직임만 뺀다** — 크기·튕김은 없고 짧은 불투명도 전이만
 *  남긴다(어지럼은 이동에서 오고, 페이드는 상태가 바뀌었음을 읽게 한다). 값(시간·곡선)은
 *  허브 토큰(tokens.desktop.css 의 --dur-in · --dur-out · --ease-settle, SSoT 는 design-hub)에서
 *  읽는다: 두 벌이 아니라 한 벌이다. */

/** macOS dock 에서 앱이 열리는 물성을 흉내낸다(발주자 지시 2026-08-05):
 *    ① 아이콘 자리에서 **아주 작게** 시작해 원래 크기로 자란다(0.94→1 은 "살짝 커짐"
 *       이라 어디서 나왔는지 읽히지 않았다 — 0.16 이어야 "그 점에서 나왔다"가 된다)
 *    ② 끝에서 살짝 **넘어갔다 돌아온다**(1.02) — 관성이 있는 물체로 읽히는 지점
 *    ③ 닫을 때는 반대로 그 점으로 **빨려 들어간다**(감속이 아니라 가속 곡선)
 *    ④ 아이콘이 눌린 것처럼 **튕긴다**(dock bounce) — 어느 아이콘이 이걸 열었는지
 *       화면이 스스로 가리킨다
 *  불투명도는 앞쪽 40% 에서만 올린다: 끝까지 함께 올리면 다 자란 뒤에도 흐릿해 보인다. */
const EASE_IN_FALLBACK = "cubic-bezier(.16, 1, .3, 1)";   // = 토큰 --ease-settle
const EASE_OUT = "cubic-bezier(.4, 0, .9, .4)";            // 가속(빨려 들어감) — 닫기 전용, 토큰 없음
const IN_MS_FALLBACK = 400;                                // = 토큰 --dur-in
const OUT_MS_FALLBACK = 240;                               // = 토큰 --dur-out
const BOUNCE_MS = 460;
const REDUCED_FADE_MS = 120;

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
/** `.4s`·`400ms` 둘 다 ms 로. 토큰이 없으면(테스트·옛 CSS) 폴백. */
function cssMs(name: string, fallback: number): number {
  const v = cssVar(name);
  const n = parseFloat(v);
  if (!v || Number.isNaN(n)) return fallback;
  return /ms$/.test(v) ? n : n * 1000;
}
const easeIn = () => cssVar("--ease-settle") || EASE_IN_FALLBACK;
const inMs = () => cssMs("--dur-in", IN_MS_FALLBACK);
const outMs = () => cssMs("--dur-out", OUT_MS_FALLBACK);

/** 진행 중인 등장 애니메이션 — 열리는 도중에 닫히면 새 것을 겹치지 않고 **되감는다**. */
const inflight = new WeakMap<HTMLElement, Animation>();
async function reverseInflight(el: HTMLElement): Promise<boolean> {
  const a = inflight.get(el);
  if (!a || a.playState !== "running") return false;
  a.reverse();
  try { await a.finished; } catch { /* 취소 */ }
  a.cancel();
  inflight.delete(el);
  return true;
}

/** 시작 크기는 **면의 크기에 반비례**한다(발주자 지적 2026-08-05 — "모든 문서"가
 *  화면 전체가 움직이는 것처럼 보인다).
 *
 *  같은 0.16 을 작은 팝오버와 화면 3분의 2 짜리 시트에 똑같이 쓰면 결과가 정반대로
 *  읽힌다: 작은 것은 "아이콘에서 튀어나왔다"가 되지만, 큰 것은 움직이는 면적이 커서
 *  **화면 자체가 줌인되는 것**처럼 보인다. 무거운 물체는 덜 움직인다는 물리와도 맞고,
 *  큰 면이 크게 움직이면 시선이 따라갈 지점을 잃는다.
 *
 *  뷰포트 대비 면적 비율로 0.16(작은 것) ~ 0.9(거의 전면)를 잇는다. 시간도 조금
 *  길게 준다 — 같은 거리를 같은 시간에 가면 큰 면이 더 빨라 보인다. */
function fromScale(panel: HTMLElement): number {
  const b = panel.getBoundingClientRect();
  const cover = (b.width * b.height) / (window.innerWidth * window.innerHeight || 1);
  // 25% 아래는 감쇠 없음(작은 팝오버·메뉴 — 여기서 dock 느낌이 나야 한다),
  // 50% 이상은 최대 감쇠(`모든 문서`처럼 화면을 덮는 시트). 그 사이만 섞는다.
  // 임계를 두는 이유: 단순 비례(cover/.45)로 하면 설정 모달(면적 32%)도 시작 크기가
  // .69 까지 올라가 "자라나온다"는 인상이 통째로 사라졌다(실측).
  const t = Math.min(1, Math.max(0, (cover - .25) / .25));
  return .16 + (.9 - .16) * t;
}

function durationFor(from: number): number {
  // 시작 크기가 클수록(=면이 클수록) 살짝 길게: 400 → 480ms
  return Math.round(inMs() * (1 + (from - .16) / (.9 - .16) * .2));
}

function reduced(): boolean {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** 트리거 중심을 패널 좌표계의 % 로 환산한다. 트리거가 없으면(단축키로 열린 경우)
 *  가운데 위 — "어디서 나왔다"고 거짓말하지 않는 중립 지점이다. */
function originOf(panel: HTMLElement, trigger?: Element | null): string {
  const p = panel.getBoundingClientRect();
  const t = trigger?.getBoundingClientRect?.();
  if (!t || !p.width || !p.height) return "50% 0%";
  const clamp = (v: number) => Math.max(-40, Math.min(140, v));
  const x = clamp(((t.left + t.width / 2 - p.left) / p.width) * 100);
  const y = clamp(((t.top + t.height / 2 - p.top) / p.height) * 100);
  return `${x.toFixed(1)}% ${y.toFixed(1)}%`;
}

/** 트리거로 쓸 요소 **정규화** — `<body>` 는 트리거가 아니다.
 *
 *  걸러내는 자리가 왜 여기인가: 호출부는 `document.activeElement` 를 그대로 넘기는
 *  것이 자연스러운데(그것이 방금 눌린 버튼이므로), 포커스가 없으면 그 값이 `body` 다.
 *  그대로 통과시키면 `bounce(document.body)` 가 되어 **앱 전체가 0.84 로 찌그러졌다
 *  되튄다**. 추정 경로에만 가드를 두면 명시 경로가 그 가드를 우회한다 — 실제로
 *  그랬다(시트가 그 길로 body 를 넘기고 있었다). 그래서 두 경로가 함께 지나는
 *  이 자리에서 한 번만 거른다.
 *
 *  버튼이 포커스를 안 받는 환경이 예외가 아니다: WKWebView(macOS)는 클릭한 버튼에
 *  포커스를 주지 않는 것이 기본이라 `activeElement` 가 body 로 남는 일이 흔하다. */
function asTrigger(el: Element | null | undefined): Element | null {
  return el && el !== document.body ? el : null;
}

/** 트리거로 쓸 요소 추정 — 방금 눌린 버튼이 곧 포커스를 갖고 있다.
 *  (클릭 핸들러 안에서 부르므로 `activeElement` 가 그 버튼이다.) */
function guessTrigger(): Element | null {
  return asTrigger(document.activeElement);
}

/** 인자가 말하는 것을 그대로 지킨다.
 *    · 생략(`undefined`) = "네가 추정해라"
 *    · `null`            = "**나온 곳이 없다**" (단축키로 열린 팔레트 등 → 중립 지점)
 *  둘을 `??` 로 뭉뚱그리면 `null` 을 넘겨도 추정으로 떨어져 **중립을 표현할 길이
 *  아예 없어진다** — 검색창에 포커스를 둔 채 단축키를 누르면 팔레트가 검색창에서
 *  자라나고 검색창이 튀었다. */
function resolveTrigger(trigger: Element | null | undefined,
                        explicit: boolean): Element | null {
  return explicit ? asTrigger(trigger) : guessTrigger();
}

/** 아이콘이 눌린 것처럼 튕긴다(dock bounce). 아이콘 **자신**이 움직이는 것이
 *  "이것이 그 문이었다"는 가장 직접적인 단서다. 트리거가 없으면 아무 일도 안 한다. */
function bounce(trigger: Element | null | undefined): void {
  if (!trigger || reduced() || !(trigger instanceof HTMLElement)) return;
  trigger.animate(
    [{ transform: "scale(1)" }, { transform: "scale(.84)", offset: .22 },
     { transform: "scale(1.1)", offset: .58 }, { transform: "scale(1)" }],
    { duration: BOUNCE_MS, easing: "ease-out" },
  );
}

/** 트리거 지점에서 자라나온다(위 주석의 ①②④). 시작 크기·시간은 면의 크기가 정한다. */
export function popIn(panel: HTMLElement, ...trigger: [(Element | null)?]): Animation | null {
  if (reduced()) {
    panel.style.opacity = "0";
    const a = panel.animate([{ opacity: 0 }, { opacity: 1 }],
      { duration: REDUCED_FADE_MS, easing: "ease-out", fill: "both" });
    void a.finished.then(() => { a.cancel(); panel.style.opacity = ""; }).catch(() => { /* 무시 */ });
    return a;
  }
  const t = resolveTrigger(trigger[0], trigger.length > 0);
  panel.style.transformOrigin = originOf(panel, t);
  bounce(t);
  const from = fromScale(panel);
  // 오버슈트도 면 크기에 맞춰 줄인다 — 큰 면이 1.02 로 넘어가면 화면 밖까지 삐져나가
  // 순간적으로 잘린 테두리가 보인다.
  const over = from > .6 ? 1.004 : 1.02;
  const mid = from + (1 - from) * .78;
  // **깜빡임 방지**(발주자 지적 2026-08-05): 호출부가 `hidden = false` 를 한 뒤 이
  // 함수가 불리는데, 애니메이션의 첫 프레임은 다음 프레임에나 적용된다. 그 사이 한
  // 프레임 동안 요소가 **완성된 모습(opacity 1, scale 1)으로** 그려져 번쩍인다.
  // 여기서 동기적으로 시작 상태를 박아 그 프레임을 없앤다.
  panel.style.opacity = "0";
  const anim = panel.animate(
    [{ opacity: 0, transform: `scale(${from.toFixed(3)})` },
     { opacity: 1, offset: .4, transform: `scale(${mid.toFixed(3)})` },
     { opacity: 1, offset: .72, transform: `scale(${over})` },
     { opacity: 1, transform: "scale(1)" }],
    { duration: durationFor(from), easing: easeIn(), fill: "both" },
  );
  inflight.set(panel, anim);
  // 끝나면 인라인 값을 **거둔다**. `fill: both` 로 붙잡아 두면 다음에 열 때 그 잔상이
  // 먼저 적용돼(scale 0.16) 또 한 프레임이 튄다. 애니메이션도 함께 취소해 스타일
  // 소유권을 CSS 로 돌려준다.
  void anim.finished.then(() => {
    if (anim.playbackRate < 0) return;   // 되감긴 것은 popOut 이 정리한다
    inflight.delete(panel);
    anim.cancel();
    panel.style.opacity = "";
    panel.style.transform = "";
  }).catch(() => { /* 중간 취소는 무시 */ });
  return anim;
}

/** 트리거 지점으로 빨려 들어간다(③). **끝날 때까지 기다릴 수 있다** — 호출부는 이
 *  프라미스 뒤에서 DOM 을 지우거나 hidden 을 건다. */
export async function popOut(panel: HTMLElement, ...trigger: [(Element | null)?]): Promise<void> {
  if (await reverseInflight(panel)) { panel.style.opacity = "0"; return; }
  if (reduced()) {
    const a = panel.animate([{ opacity: 1 }, { opacity: 0 }],
      { duration: REDUCED_FADE_MS, easing: "ease-out", fill: "both" });
    try { await a.finished; } catch { /* 무시 */ }
    a.cancel(); panel.style.opacity = "0";
    return;
  }
  panel.style.transformOrigin = originOf(panel, resolveTrigger(trigger[0], trigger.length > 0));
  const from = fromScale(panel);          // 들어올 때와 같은 크기로 되돌아간다
  const anim = panel.animate(
    [{ opacity: 1, transform: "scale(1)" },
     { opacity: .9, offset: .35, transform: `scale(${(from + (1 - from) * .35).toFixed(3)})` },
     { opacity: 0, transform: `scale(${from.toFixed(3)})` }],
    { duration: outMs(), easing: EASE_OUT, fill: "both" },
  );
  try { await anim.finished; } catch { /* 중간에 취소되면 그대로 진행 */ }
  // 사라진 상태를 **인라인으로** 고정하고 애니메이션을 거둔다. 호출부가 hidden 을
  // 걸기 전 한 프레임 동안 원래 모습으로 되돌아가 번쩍이는 것을 막는다(닫을 때의
  // 깜빡임이 이것이었다). 다음 popIn 이 이 값을 덮어쓴다.
  anim.cancel();
  panel.style.opacity = "0";
}

/** 딤(스크림) 페이드 — 패널보다 조금 느리게 사라져 화면이 한 번에 밝아지지 않는다. */
export function fadeIn(el: HTMLElement): void {
  el.style.opacity = "0";              // 첫 프레임에 딤이 전부 깔리는 것을 막는다
  const anim = el.animate([{ opacity: 0 }, { opacity: 1 }],
    { duration: reduced() ? REDUCED_FADE_MS : scrimInMs(), easing: "ease-out", fill: "both" });
  inflight.set(el, anim);
  void anim.finished.then(() => {
    if (anim.playbackRate < 0) return;
    inflight.delete(el); anim.cancel(); el.style.opacity = "";
  }).catch(() => { /* 무시 */ });
}

export async function fadeOut(el: HTMLElement): Promise<void> {
  if (await reverseInflight(el)) { el.style.opacity = "0"; return; }
  const anim = el.animate([{ opacity: 1 }, { opacity: 0 }],
    { duration: reduced() ? REDUCED_FADE_MS : outMs(), easing: "ease-in", fill: "both" });
  try { await anim.finished; } catch { /* 무시 */ }
  anim.cancel();
  el.style.opacity = "0";              // hidden 이 걸리기 전까지 사라진 채로
}

/** 딤은 패널보다 **빨리 깔린다** — 열 때 IN_MS 를 그대로 쓰면 화면이 어두워지는
 *  동안 패널이 이미 다 자라 있어 두 동작이 겹쳐 보인다. 닫을 때는 둘 다 OUT_MS 로
 *  함께 끝난다(딤만 남아 화면이 늦게 밝아지면 닫힘이 굼뜨게 읽힌다). */
const scrimInMs = () => Math.round(inMs() * .6);
