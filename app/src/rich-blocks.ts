/** 수식·다이어그램 — **늦게 싣는다.**
 *
 *  KaTeX 와 mermaid 는 이 앱에서 가장 무거운 의존성이다(설치 기준 4.4MB · 87MB).
 *  문서 대부분에는 둘 다 없으므로 기본 번들에 넣으면 모두가 안 쓰는 무게를 진다.
 *  그래서 **그 블록이 실제로 있는 문서에서만** 동적 import 로 데려온다 — 한 번
 *  데려오면 프라미스를 캐시해 두 번 받지 않는다.
 *
 *  왜 렌더러가 아니라 여기인가: 이 앱에는 마크다운을 그리는 곳이 둘이다(문서 열의
 *  CM 라이브 프리뷰 · 발췌와 폴백의 `renderMarkdown`). 같은 문서가 두 곳에서 다르게
 *  보이면 안 된다는 규율이 `renderMarkdown` 머리에 있고, 그것을 지키는 방법은
 *  **로더와 그리는 손을 한 벌로 두는 것**이다. 두 렌더러는 자리만 만들고 채우는
 *  일은 전부 이 파일이 한다.
 *
 *  실패는 **삼키지 않고 원문으로 되돌린다**. 수식이 안 그려졌을 때 빈 칸을 남기면
 *  문서가 조용히 내용을 잃는다 — 소스가 보이는 것이 낫다. */

import { palette, hexStr } from "./theme";

let katexP: Promise<typeof import("katex")> | null = null;
let mermaidP: Promise<typeof import("mermaid")> | null = null;

function katex() {
  // CSS 도 여기서 함께 온다 — 이 import 가 늦으면 스타일도 늦어야 짝이 맞는다.
  if (!katexP) katexP = import("katex").then(async (m) => {
    await import("katex/dist/katex.min.css");
    return m;
  });
  return katexP;
}

const isDark = () => document.documentElement.dataset.theme === "dark";

/** 다이어그램도 흑백이다(2026-09-24). 라이트는 mermaid 의 `neutral` 이 이미 회색조이고,
 *  다크 기본 테마는 강조색이 섞여서 `base` 에 토큰을 직접 먹인다. */
function mermaidTheme() {
  if (!isDark()) return { theme: "neutral" as const };
  const p = palette.dark, c = hexStr;
  return { theme: "base" as const, themeVariables: {
    darkMode: true, background: c(p.background), fontFamily: "inherit",
    primaryColor: c(p.card), primaryTextColor: c(p.text), primaryBorderColor: c(p.textSoft),
    secondaryColor: c(p.surface), tertiaryColor: c(p.background),
    lineColor: c(p.textSoft), textColor: c(p.text), mainBkg: c(p.card), nodeBorder: c(p.textSoft),
    clusterBkg: c(p.surface), clusterBorder: c(p.line), edgeLabelBackground: c(p.surface),
    noteBkgColor: c(p.surface), noteTextColor: c(p.text), noteBorderColor: c(p.line),
  } };
}

function mermaid() {
  if (!mermaidP) mermaidP = import("mermaid").then((m) => {
    // `startOnLoad` 를 끄는 것이 핵심이다 — 켜 두면 문서에 붙는 순간 스스로 훑어
    // 그리려 들고, 우리가 부르는 것과 두 벌이 된다.
    m.default.initialize({ startOnLoad: false, securityLevel: "strict", ...mermaidTheme() });
    return m;
  });
  return mermaidP;
}

/** 수식 한 덩이. `display` 면 가운데 블록, 아니면 줄 안. */
export async function fillMath(el: HTMLElement, tex: string, display: boolean): Promise<void> {
  try {
    const k = await katex();
    k.default.render(tex, el, { displayMode: display, throwOnError: false });
  } catch {
    el.textContent = display ? `$$${tex}$$` : `$${tex}$`;   // 원문으로 되돌린다
  }
}

/** 다이어그램 한 장. id 는 mermaid 가 요구하는 문서 내 유일값이다. */
let seq = 0;
export async function fillMermaid(el: HTMLElement, src: string): Promise<void> {
  try {
    const m = await mermaid();
    const { svg } = await m.default.render(`vinc-mmd-${++seq}`, src);
    el.innerHTML = svg;
  } catch (e) {
    // 문법이 틀린 다이어그램은 흔하다(쓰는 중인 문서). 오류를 화면에 던지지 않고
    // 원문을 코드처럼 보여 준다 — 고칠 사람이 볼 것은 오류 문구가 아니라 자기 소스다.
    const pre = document.createElement("pre");
    pre.textContent = src;
    el.replaceChildren(pre);
    void e;
  }
}

/** `renderMarkdown` 이 남긴 자리를 채운다. 두 번 불러도 안전하다(채운 자리는 표식을
 *  지운다) — 같은 DOM 이 다시 그려질 수 있는 자리에서 부르기 때문이다. */
export function hydrateRich(root: ParentNode): void {
  root.querySelectorAll<HTMLElement>("[data-rich]").forEach((el) => {
    const kind = el.dataset.rich;
    const src = el.dataset.src || "";
    delete el.dataset.rich;
    if (kind === "math") void fillMath(el, src, true);
    else if (kind === "mermaid") void fillMermaid(el, src);
  });
}
