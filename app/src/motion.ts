/** 모션 한 벌 — 화면 위에 뜨는 것들이 **자기 자리에서** 부드럽게 나타나고 사라진다.
 *
 *  ⚠ 이 파일은 2026-09-09 에 뒤집혔다. 2026-08-05 부터 여기 살던 것은 **dock 물성**
 *  이었다: 패널이 트리거 버튼의 중심을 기준점으로 0.16 배에서 자라나고, 버튼이 함께
 *  튕기고, 닫을 때 그 점으로 빨려 들어갔다. 목적은 "어느 버튼이 이걸 열었나"를 화면이
 *  스스로 말하게 하는 것이었다.
 *
 *  걷어낸 이유는 발주자 지시다(2026-09-09): "해당 버튼에서 나오는 형태로 지금 구성하고
 *  있는데 그렇게 하지 말고 부드럽게 나와야 하는 위치에 생기게 하자. 사라질 때도
 *  그렇게." 즉 패널의 등장 자리는 **패널이 있어야 할 자리**이고, 어디서 왔는지는
 *  모션이 할 말이 아니다. 그래서 기준점은 늘 자기 중심이고 시작 크기는 0.98 하나다 —
 *  면 크기에 따라 시작 크기를 바꾸던 계산도, dock bounce 도 함께 사라졌다(그 둘은
 *  "어디서 나왔다"를 읽히게 하려고 존재했으므로 목적이 없어지면 남을 이유도 없다).
 *
 *  남은 것은 그때와 같다:
 *   · **퇴장 애니메이션**이 있다. 원래 없던 것이 이것이고(등장은 부드럽고 퇴장은 뚝
 *     끊겼다), WAAPI 를 쓰는 이유가 이것이다 — 닫기를 **기다린 뒤** DOM 을 지운다.
 *   · 열리는 도중에 닫히면 새 애니메이션을 겹치지 않고 **되감는다**.
 *   · `prefers-reduced-motion` 이면 크기 변화를 빼고 짧은 불투명도 전이만 남긴다.
 *   · 값(시간·곡선)은 허브 토큰에서 읽는다(SSoT 는 design-hub). 두 벌을 두지 않는다.
 *
 *  호출부는 트리거를 넘기지 않는다 — 넘길 것이 없다. */

const EASE_IN_FALLBACK = "cubic-bezier(.16, 1, .3, 1)";   // = 토큰 --ease-settle
const EASE_OUT_FALLBACK = "cubic-bezier(.22, 1, .36, 1)"; // = 토큰 --ease-out
const IN_MS_FALLBACK = 400;                                // = 토큰 --dur-in
const OUT_MS_FALLBACK = 240;                               // = 토큰 --dur-out
const REDUCED_FADE_MS = 120;
/** 시작 크기. **하나뿐이다** — 면이 커도 작아도 같은 값을 쓴다. 0.98 인 이유는 이
 *  모션이 "여기 생겼다"만 말해야 하기 때문이다: 더 작게 시작하면 어딘가에서 날아온
 *  것처럼 읽히고, 그것이 방금 걷어낸 바로 그 인상이다. */
const FROM_SCALE = 0.98;

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
const easeOut = () => cssVar("--ease-out") || EASE_OUT_FALLBACK;
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

function reduced(): boolean {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** 자기 자리에서 나타난다: 살짝 작은 데서 제 크기로, 불투명도와 함께.
 *  오버슈트는 없다 — 넘어갔다 돌아오는 것은 관성이 있는 물체의 문법이고, 이 패널은
 *  날아오지 않고 그 자리에 생긴다. */
export function popIn(panel: HTMLElement): Animation | null {
  if (reduced()) {
    panel.style.opacity = "0";
    const a = panel.animate([{ opacity: 0 }, { opacity: 1 }],
      { duration: REDUCED_FADE_MS, easing: "ease-out", fill: "both" });
    void a.finished.then(() => { a.cancel(); panel.style.opacity = ""; }).catch(() => { /* 무시 */ });
    return a;
  }
  panel.style.transformOrigin = "center";
  // **깜빡임 방지**(발주자 지적 2026-08-05): 호출부가 `hidden = false` 를 한 뒤 이
  // 함수가 불리는데, 애니메이션의 첫 프레임은 다음 프레임에나 적용된다. 그 사이 한
  // 프레임 동안 요소가 **완성된 모습으로** 그려져 번쩍인다. 여기서 동기적으로 시작
  // 상태를 박아 그 프레임을 없앤다.
  panel.style.opacity = "0";
  const anim = panel.animate(
    [{ opacity: 0, transform: `scale(${FROM_SCALE})` },
     { opacity: 1, transform: "scale(1)" }],
    { duration: inMs(), easing: easeIn(), fill: "both" },
  );
  inflight.set(panel, anim);
  // 끝나면 인라인 값을 **거둔다**. `fill: both` 로 붙잡아 두면 다음에 열 때 그 잔상이
  // 먼저 적용돼 또 한 프레임이 튄다. 애니메이션도 함께 취소해 스타일 소유권을 CSS 로.
  void anim.finished.then(() => {
    if (anim.playbackRate < 0) return;   // 되감긴 것은 popOut 이 정리한다
    inflight.delete(panel);
    anim.cancel();
    panel.style.opacity = "";
    panel.style.transform = "";
  }).catch(() => { /* 중간 취소는 무시 */ });
  return anim;
}

/** 자기 자리에서 사라진다. **끝날 때까지 기다릴 수 있다** — 호출부는 이 프라미스
 *  뒤에서 DOM 을 지우거나 hidden 을 건다. */
export async function popOut(panel: HTMLElement): Promise<void> {
  if (await reverseInflight(panel)) { panel.style.opacity = "0"; return; }
  if (reduced()) {
    const a = panel.animate([{ opacity: 1 }, { opacity: 0 }],
      { duration: REDUCED_FADE_MS, easing: "ease-out", fill: "both" });
    try { await a.finished; } catch { /* 무시 */ }
    a.cancel(); panel.style.opacity = "0";
    return;
  }
  panel.style.transformOrigin = "center";
  const anim = panel.animate(
    [{ opacity: 1, transform: "scale(1)" },
     { opacity: 0, transform: `scale(${FROM_SCALE})` }],
    { duration: outMs(), easing: easeOut(), fill: "both" },
  );
  try { await anim.finished; } catch { /* 중간에 취소되면 그대로 진행 */ }
  // 사라진 상태를 **인라인으로** 고정하고 애니메이션을 거둔다. 호출부가 hidden 을
  // 걸기 전 한 프레임 동안 원래 모습으로 되돌아가 번쩍이는 것을 막는다.
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
