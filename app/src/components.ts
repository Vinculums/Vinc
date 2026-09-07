/** Shared surface chrome — Figma first-draft visual grammar (nodes 5:2/5:71/
 *  6:57/7:2/7:78) carried over with approved deltas (D18 English copy). */
import { icon, stateDot } from "./icons";
import { COPY } from "./copy";
import { popIn, popOut, fadeIn, fadeOut } from "./motion";

/** ── 세그먼트 컨트롤의 물성(2026-08-05) ────────────────────────────────────
 *
 *  `.seg`(3D/2D/타임라인/Sankey · 테마 · 언어 · 배치)는 여태 **활성 칩의 배경색만
 *  갈아 끼웠다.** 그래서 고를 때 선택이 "이동"하지 않고 한 칸이 꺼지고 다른 칸이
 *  켜졌다 — 어느 것에서 어느 것으로 옮겼는지 눈이 따라갈 대상이 없다.
 *
 *  움직이는 알약(thumb)을 하나 넣어 그것이 미끄러진다. 배경은 thumb 이 갖고 칩은
 *  글자만 갖는다(둘 다 배경을 가지면 이동 중에 두 개가 켜져 보인다).
 *
 *  **호출부를 하나도 고치지 않는다.** 세그를 그리는 곳이 여섯 군데(View 관점·렌즈
 *  패널·설정 셋·팩)인데 각자 innerHTML 로 다시 그리므로, 배선을 심으면 다시 그릴
 *  때마다 사라진다. 그래서 문서 전체를 한 번 관찰하고(MutationObserver) 활성 칩이
 *  바뀌거나 세그가 새로 생기면 알약을 그 자리로 옮긴다. */
let segRaf = 0;

/** 세그를 **다시 그려도** 알약이 이어지게 하는 기억(FLIP).
 *
 *  왜 필요한가: 세그를 그리는 곳들이 선택을 바꿀 때 컨테이너를 innerHTML 로 다시
 *  그린다(설정의 테마는 패널 전체, View 의 관점은 표면 전체). 그러면 알약도 새
 *  요소로 태어나므로 전이할 이전 상태가 없어 **그냥 새 자리에 나타난다** — 미끄러지지
 *  않는다. 호출부 여섯 군데를 "상태만 갱신"으로 바꾸는 것이 정공법이지만, 그건 각
 *  화면의 렌더 계약을 건드리는 일이라 위험이 크다. 대신 마지막 자리를 기억해 두고
 *  새 알약을 그 자리에 놓은 뒤 목표로 옮긴다(첫 프레임만 전이 없이).
 *
 *  키는 "어느 컨테이너의 어떤 선택지 묶음인가" — 라벨 조합이 그 세그의 정체다. */
const segMemory = new Map<string, { t: string; w: string; h: string }>();

function segKey(seg: HTMLElement): string {
  const host = seg.closest<HTMLElement>("[id]")?.id ?? "";
  const labels = Array.from(seg.querySelectorAll<HTMLElement>(".chip"))
    .map((c) => c.textContent?.trim() ?? "").join("|");
  return `${host}::${labels}`;
}

function placeSegThumb(seg: HTMLElement): void {
  const active = seg.querySelector<HTMLElement>(".chip.active");
  let thumb = seg.querySelector<HTMLElement>(".seg-thumb");
  if (!active) { if (thumb) thumb.style.opacity = "0"; return; }
  const key = segKey(seg);
  if (!thumb) {
    thumb = document.createElement("span");
    thumb.className = "seg-thumb";
    thumb.setAttribute("aria-hidden", "true");
    seg.prepend(thumb);
    // 새로 태어난 알약을 **기억한 자리에** 먼저 세운다(전이 없이). 그래야 바로 아래
    // 목표 위치 지정이 "이동"으로 보인다. reflow 한 번이 그 둘을 가른다.
    const prev = segMemory.get(key);
    if (prev) {
      thumb.style.transition = "none";
      thumb.style.opacity = "1";
      thumb.style.transform = prev.t;
      thumb.style.width = prev.w;
      thumb.style.height = prev.h;
      void thumb.offsetWidth;              // 강제 reflow — 위 상태를 확정시킨다
      thumb.style.transition = "";
    }
  }
  // ⚠ `getBoundingClientRect` 를 쓰지 않는다. 세그가 **스케일 애니메이션 중인 조상**
  // 안에 있으면(모달이 열리는 340ms 동안 전부 그렇다) 그 rect 는 축소된 좌표를 주고,
  // 그 값이 알약의 width 로 굳어 8px 짜리 점이 된다(실측). `offset*` 은 레이아웃
  // 값이라 변환의 영향을 받지 않는다.
  // offsetLeft/Top 은 offsetParent(=position:relative 인 .seg)의 테두리 기준이고,
  // 알약은 padding 상자 기준(left:0)이므로 테두리 두께만 뺀다.
  if (!active.offsetWidth) return;         // 아직 레이아웃 전 — 다음 관찰에서 잡는다
  thumb.style.opacity = "1";
  thumb.style.width = `${active.offsetWidth}px`;
  thumb.style.height = `${active.offsetHeight}px`;
  thumb.style.transform =
    `translate(${active.offsetLeft - seg.clientLeft}px, ${active.offsetTop - seg.clientTop}px)`;
  segMemory.set(key, { t: thumb.style.transform, w: thumb.style.width, h: thumb.style.height });
}

function placeAllSegThumbs(): void {
  segRaf = 0;
  document.querySelectorAll<HTMLElement>(".seg").forEach(placeSegThumb);
}

function scheduleSegPlace(): void {
  if (segRaf) return;                      // 한 프레임에 한 번만(관찰이 폭주해도)
  segRaf = requestAnimationFrame(placeAllSegThumbs);
}

/** ── 바깥 클릭으로 닫기 ────────────────────────────────────────────────────
 *
 *  두 함정을 함께 막는다:
 *   ① **스크롤바 클릭**. 요소가 스크롤되면 그 스크롤바를 잡는 것도 그 요소 위의
 *      클릭이다 — 딤을 눌렀다고 오인해 닫혔다(실측 버그). 포인터가 client 영역
 *      **안쪽**인지 확인한다(스크롤바는 clientWidth/Height 밖이다).
 *   ② **안에서 시작한 드래그**. 시트 안에서 글자를 선택하다 밖에서 손을 떼면 click
 *      은 딤에서 발생한다. 그래서 **누른 곳과 뗀 곳이 모두** 딤이어야 닫는다. */
function inClientArea(el: HTMLElement, e: PointerEvent | MouseEvent): boolean {
  const r = el.getBoundingClientRect();
  return e.clientX - r.left < el.clientWidth && e.clientY - r.top < el.clientHeight;
}

/** 딤(스크림)을 눌러 닫는 배선. 대상이 딤 **자신**일 때만 닫힌다. */
export function wireScrimDismiss(scrim: HTMLElement, close: () => void): void {
  let armed = false;
  scrim.addEventListener("pointerdown", (e) => {
    armed = e.target === scrim && inClientArea(scrim, e);
  });
  scrim.addEventListener("click", (e) => {
    if (armed && e.target === scrim) close();
    armed = false;
  });
}

/** 딤이 없는 패널(렌즈·범례처럼 지도를 계속 만질 수 있어야 하는 것)을 바깥 클릭으로
 *  닫는다. `keep` 은 눌려도 닫지 않을 요소들(자기를 연 버튼 — 그 버튼은 스스로
 *  토글하므로 여기서 닫으면 두 번 처리돼 다시 열린다).
 *  반환 = 배선 해제(패널이 사라질 때 호출). */
export function wireOutsideDismiss(panel: HTMLElement, close: () => void,
                                   ...keep: (Element | null | undefined)[]): () => void {
  const onDown = (e: PointerEvent) => {
    const t = e.target as Node | null;
    if (!t || panel.hidden) return;
    if (panel.contains(t) || keep.some((k) => k?.contains(t))) return;
    close();
  };
  // capture 로 받는다 — 캔버스(WebGL)가 자기 포인터 이벤트를 먼저 삼켜도 여기까지 온다.
  document.addEventListener("pointerdown", onDown, true);
  return () => document.removeEventListener("pointerdown", onDown, true);
}

/** 앱 기동 때 한 번. 이후 세그가 어디서 다시 그려지든 알약이 따라간다. */
/** 이 변화가 세그와 **관계가 있는가**. 없으면 아무 일도 하지 않는다.
 *
 *  왜 필요한가(2026-08-05 실측): 관찰 범위가 `document.body` 전체라, 툴팁이 뜨고 지는
 *  것 같은 무관한 변화에도 콜백이 돌았다. 그 콜백은 문서의 모든 `.chip` 좌표를
 *  `offset*` 으로 읽으므로 **강제 레이아웃**이다 — 옵션 패널을 굴리면 포인터가 지나는
 *  라벨마다 툴팁이 붙었다 떨어지고, 그때마다 전체 측정이 끼어들어 스크롤이 끊겼다. */
function touchesSeg(recs: MutationRecord[]): boolean {
  const isSegish = (n: Node) => n instanceof Element
    && (n.matches(".seg, .chip, .seg-thumb") || !!n.querySelector(".seg"));
  for (const r of recs) {
    const t = r.target;
    if (t instanceof Element && t.closest(".seg")) return true;
    for (const n of r.addedNodes) if (isSegish(n)) return true;
    for (const n of r.removedNodes) if (isSegish(n)) return true;
  }
  return false;
}

export function initSegMotion(): void {
  scheduleSegPlace();
  new MutationObserver((recs) => { if (touchesSeg(recs)) scheduleSegPlace(); })
    .observe(document.body, { subtree: true, childList: true, attributeFilter: ["class"] });
  window.addEventListener("resize", scheduleSegPlace);
}

/** Surface header: display title + muted meta line. (아이콘은 LNB 와 중복이라
 *  페이지에서 미표기 — iconName 은 호출부 안정성 위해 시그니처만 유지.) */
export function header(_iconName: string, title: string, meta: string,
                       cta = ""): string {
  return `
    <header class="surface-header">
      <div class="surface-title">
        <div>
          <h1>${title}</h1>
          <p class="hint surface-meta">${meta}</p>
        </div>
      </div>
      ${cta}
    </header>`;
}

/** ── 시트(Sheet) — 화면 위에 뜨는 것의 **단일 규격**(D63) ────────────────────
 *
 *  여태 오버레이가 제각각이었다: 공유·가져오기·문서·확인·프롬프트가 저마다 다른
 *  마크업으로 딤을 깔고, 제목 위치·액션 정렬·닫는 법이 서로 달랐다. 화면마다 다른
 *  구조를 익히게 하는 것이 곧 학습 비용이다.
 *
 *  규격은 넷으로 고정한다:
 *    머리(제목 + 부제 + ✕) · 몸(자유) · 발(주 액션 오른쪽, 부 액션 왼쪽) · 닫힘 규칙
 *  닫힘 규칙도 하나다 — **Esc · ✕ · 바깥 클릭**. 어느 시트든 같다.
 *
 *  파괴적 확인(confirmDestructive)만 바깥 클릭을 취소로 삼는다(오조작 방지).
 *  패널(렌즈·검색 결과·크럼 깊이)은 시트가 아니다 — 앵커에 붙고 딤이 없다. */
export type SheetHandle = {
  el: HTMLElement;
  body: HTMLElement;
  foot: HTMLElement;
  close: () => void;
};

/** HTML 이스케이프 한 벌. 여태 이 네 글자짜리 함수가 세 벌 복제돼 있었고, 복제는
 *  **빠뜨리기 좋은 형태**다 — 그래프 유래 문자열이 esc 없이 innerHTML 로 가는 자리가
 *  다섯 곳 있었다(감사 2026-08-18 M9). 한 벌이면 빠뜨린 자리를 찾을 때 여기서 시작한다.
 *
 *  ⚠ `"` 까지 escape 하므로 **속성값에도 그대로 쓸 수 있다.** 실제로 뚫려 있던 것 중
 *  하나가 `data-tip="${...}"` 속성 탈출이었다. `'` 는 escape 하지 않으니 속성은 반드시
 *  큰따옴표로 감쌀 것.
 *  근거: [[decision:escape-once-at-the-boundary]] */
export const esc = (s: string): string => String(s).replace(/[&<>"]/g, (c) =>
  c === "&" ? "&amp;" : c === "<" ? "&lt;" : c === ">" ? "&gt;" : "&quot;");

export function openSheet(opts: {
  title: string; subtitle?: string;
  /** 크기는 **내용의 종류**를 따른다 — 시트마다 제멋대로면 열 때마다 화면이
   *  출렁이지만, 하는 일이 다른데 같은 상자에 밀어 넣는 것도 낭비다(D63 개정).
   *  단계는 셋뿐이고, 시트는 그중 하나를 고를 뿐 임의 폭을 쓰지 못한다:
   *    `action`(기본) 묻고 답하는 자리 — 확인·프롬프트·공유·가져오기
   *    `write`  글 쓰는 자리 — 문서 편집기
   *    `browse` 훑는 자리 — 모든 문서(화면의 2/3 남짓) */
  size?: "action" | "write" | "browse";
  /** 닫힐 때 한 번 호출(편집기 dispose 등). */
  onClose?: () => void;
  /** 라벨 없이 몸만 쓰는 시트(문서 편집기처럼) — 머리의 제목을 숨긴다. */
  bare?: boolean;
  /** **선택을 요구한다** — Esc·스크림·✕ 세 탈출구를 전부 막는다. 발의 버튼만이 문이다.
   *
   *  거의 안 쓴다. 쓰는 자리는 갈림길 하나뿐이다(첫 실행: 오프라인이냐 로그인이냐).
   *  그런 자리에서 "닫기"는 답이 아니라 **답을 안 한 것**인데, 시트는 닫힌 것을
   *  기억하므로 답 안 한 상태가 답으로 굳는다.
   *
   *  ⚠ 이걸 켜는 자리는 **다른 데서 되돌아올 길이 반드시 있어야 한다.** 첫 실행은
   *  Settings → Account 가 그 길이고 계정 없이도 열린다. 그 길 없이 켜면 그 기기는
   *  영영 못 빠져나온다. */
  mustChoose?: boolean;
}): SheetHandle {
  const el = document.createElement("div");
  el.className = "sheet-scrim";
  el.innerHTML = `
    <div class="sheet" role="dialog" aria-modal="true"
         aria-label="${esc(opts.title)}" data-size="${opts.size || "action"}">
      <div class="sheet-head">
        <div class="sheet-titles">
          ${opts.bare ? "" : `<h2>${esc(opts.title)}</h2>`}
          ${opts.subtitle ? `<p class="hint">${esc(opts.subtitle)}</p>` : ""}
        </div>
        <button class="rail-x sheet-x" aria-label="Close">✕</button>
      </div>
      <div class="sheet-body"></div>
      <div class="sheet-foot"></div>
    </div>`;
  document.body.appendChild(el);
  // 시트는 **자기를 연 버튼에서** 자라고 그 자리로 사라진다(motion.ts). 트리거를
  // 인자로 받지 않고 추정하는 이유: openSheet 호출부가 20 곳 가까이 되는데 전부
  // 클릭 핸들러 안이라 방금 눌린 버튼이 곧 activeElement 다. 배선을 20 곳에 퍼뜨리는
  // 대신 한 군데서 추정하고, 못 찾으면 중립 지점(가운데 위)으로 물러선다.
  const sheetEl = el.querySelector<HTMLElement>(".sheet")!;
  const trigger = document.activeElement;
  fadeIn(el);
  popIn(sheetEl, trigger);
  let closed = false;
  const close = () => {
    if (closed) return;            // 애니메이션 도중 두 번 눌려도 한 번만 닫는다
    closed = true;
    document.removeEventListener("keydown", onKey, true);
    // 퇴장을 **기다린 뒤** 지운다 — 여태 닫기는 애니메이션이 아예 없어 뚝 끊겼다.
    void Promise.all([popOut(sheetEl, trigger), fadeOut(el)]).then(() => {
      el.remove();
      opts.onClose?.();
    });
  };
  const onKey = (e: KeyboardEvent) => {
    if (e.key !== "Escape") return;
    // 시트가 여럿 쌓여도 **맨 위 하나만** 닫는다(가장 최근 것이 마지막 자식).
    if (document.querySelectorAll(".sheet-scrim")[document.querySelectorAll(".sheet-scrim").length - 1] !== el) return;
    e.stopPropagation();
    close();
  };
  if (!opts.mustChoose) {
    document.addEventListener("keydown", onKey, true);
    wireScrimDismiss(el, close);
    el.querySelector(".sheet-x")!.addEventListener("click", close);
  } else {
    // ✕ 는 숨긴다 — 남겨 두고 무반응이면 눌러 보고 고장으로 읽는다. 없는 문이
    // 안 열리는 문보다 정직하다.
    el.querySelector<HTMLElement>(".sheet-x")!.hidden = true;
  }
  return {
    el,
    body: el.querySelector<HTMLElement>(".sheet-body")!,
    foot: el.querySelector<HTMLElement>(".sheet-foot")!,
    close,
  };
}

/**
 * 진행 표시 — **공유·백업 동선의 기다림이 전부 이 한 벌**을 쓴다. 여섯 자리다:
 * 시트 넷(팩 준비 · 스페이스로 보내기 · 파일로 받기 · 커뮤니티 게시)과 Tools 둘
 * (공유 탭 진입 · 전체 백업). 전에는 자리마다 `<p class="hint">` 한 줄이라, 같은
 * 동선에서 같은 일을 기다리는데 모양이 자리마다 달랐다.
 *
 * ⚠ **여섯은 이 동선의 전부이지 앱의 전부가 아니다.** `sheets.ts` 의 **가져오기**
 * 동선에는 `<p class="hint">${COPY.packs.importing}</p>` 가 아직 셋 남아 있다
 * (`openImportSheet` 하나 · `openDocImportSheet` 둘). 그중 첫째는 아래 1번이 근거로
 * 든 바로 그 `api.importPack` 을 기다린다 — 같은 함수를 기다리는 자리가 동선에 따라
 * 두 모양이라는 뜻이다. 옮기지 않은 이유는 셋 중 하나가 **자리에 안 들어가서**다:
 * 추출 제안의 것은 `<p>` 안의 `<span>` 한 칸이라 막대를 넣으면 `<p>` 안에 `<div>` 가
 * 들어간다. 나머지 둘만 옮기면 가져오기 동선이 다시 두 모양이 되므로, 셋을 한 번에
 * 옮기는 것을 별건으로 둔다. 이 주석의 수는 그때 함께 늘릴 것.
 *
 * 두 표면이 함께 쓰므로 자리는 여기다(`sheets.ts` 와 `tools.ts` 둘 다 이 파일에서
 * 시트 규격을 가져온다). 전에는 `sheets.ts` 안에 있었고, 그래서 Tools 가 못 썼다.
 *
 * **게이지는 불확정(indeterminate)이다. 단계를 못 만들어서가 아니라 없는 단계를
 * 지어내지 않기 위해서다.** 다만 여섯 자리가 기다리는 것은 한 모양이 아니라 **넷**이다:
 *
 *   1. `call()`(`api.ts:147`) — `api.share` · `api.importPack` · `api.activate`(전체
 *      백업) · `api.domains`(공유 탭 진입)이 지난다. `fetch` 하나와 `res.json()`
 *      하나뿐이라 중간 보고가 물리적으로 없다.
 *   2. `communityPost()`(`api.ts:465`) — `community.publish` 는 `call()` 을 **안
 *      지난다**. 별개의 `fetch` 다. 다른 함수지만 역시 요청 하나 · 응답 하나다.
 *   3. `teamsApi.list()`(`api.ts:490`) — 이것도 자기 `fetch` 를 따로 연다. 공유 탭
 *      진입은 이것과 1번의 `api.domains` **둘**을 이어 기다리므로 셀 수 있는 단계가
 *      둘 있는 셈인데, 세면 막대가 50 에서 100 으로 뛴다. 두 칸짜리 게이지는 진행이
 *      아니라 깜빡임이다.
 *   4. `toBytes()`(`packfile.ts:54`) — 파일로 받기는 `api.ts` 를 **아예 안 탄다**.
 *      그리고 여기엔 셀 수 있는 일이 있다: `for (const [name, obj] of files)` 가
 *      파일마다 `await deflateRaw` 를 돌고, 총수를 `files.length`(매니페스트 ·
 *      프래그먼트 · 문서 N)로 **미리 안다**.
 *
 * 4번을 그래도 안 세는 이유는 셀 수 없어서가 아니다. 그 루프가 이 경로의 기다림
 * **전부**라(뒤는 Blob 생성과 `a.click()` 뿐이다) 퍼센트를 그리면 막대가 100으로
 * 달려가 곧바로 사라진다. 그것을 보여주려면 `toBytes` 에 콜백을 뚫어야 하는데,
 * 호출부는 `downloadPack` 하나뿐이면서(전수 확인) 팩 규격 파일이 시트의 표시 사정을
 * 알게 되는 대가는 그 한 자리보다 크다. 1·2번은 애초에 셀 것이 없다.
 *
 * 퍼센트를 시계로 지어내는 쪽은 참조 원본(커뮤니티 페이지)이 실제로 그렇게 한다:
 * 요청은 하나인데 `setInterval(…, 550)` 이 막대를 8 → 32 → 56 → 80% 로 밀고, 응답이
 * 오면 100% 로 점프한다. 그 수치는 서버가 어디까지 갔는지와 아무 관계가 없다.
 *
 * **`role="status"` 는 이 마크업에 없다.** 라이브 영역은 내용이 바뀌기 **전에** DOM 에
 * 서 있어야 읽히는데 이것은 내용과 함께 삽입되므로, 자기 자신에 달면 아무것도 알리지
 * 못한다. 붙일 상주 자리가 있는 곳에만 붙인다 — 커뮤니티 게시(`#pub-progress` 가 폼과
 * 함께 먼저 선다)와 전체 백업(`#tl-backup-msg` 가 공유 탭과 함께 먼저 선다) 둘이다.
 * 나머지 넷은 담는 자리를 통째로 갈아치우므로 **알리는 것이 없다.** 상주 자리를
 * 만들려면 `openSheet` 를 고쳐야 하는데(시트 20 곳 공용) 그쪽엔 더 앞선 결함이 있다 —
 * 시트를 열 때 초점을 시트 안으로 옮기지 않는다. 초점이 뒤의 버튼에 남아 있는 채로
 * 알림만 먼저 다는 것은 순서가 거꾸로다.
 *
 * 라벨은 **한 번만** 싣는다. 보이는 `<p>` 를 `aria-labelledby` 로 가리킨다 — 전에는
 * `aria-label` 에 같은 문구를 한 벌 더 실어 두 번 읽혔다. `aria-valuenow` 는 없다.
 * 그것이 불확정의 정의다.
 *
 * 옮겨 온 것은 **모양**이다(4px · radius 2px · `--muted` 트랙 위 `--primary` 채움).
 * 움직임은 지도의 `.view-busy` 에서 가져왔다 — 앱 안에서 "바쁨"이 두 가지 모양으로
 * 읽히지 않게. 색·간격은 토큰에서만 온다(하드코딩 색은 표면 게이트가 막는다).
 *
 * ⚠ **id 는 호출마다 새로 만든다.** 전에는 고정이었고 "두 벌이 동시에 설 수 없다"고
 * 적어 두었는데 거짓이었다 — `openSheet` 의 `close()` 는 퇴장 애니메이션을 **기다린
 * 뒤에** 시트를 지우므로(위 `close`), 닫히는 중인 시트가 아직 DOM 에 있는 채로 다음
 * 시트가 선다. 실제 경로가 있다: 전체 백업이 도는 동안 `공유 시작`을 누르면
 * `sheet.close()` 직후 `openShareSheet` 가 곧바로 자기 막대를 세운다. 그러면 id 가
 * 겹치고 `aria-labelledby` 는 **문서 순서상 앞의 것**, 즉 사라지는 중인 시트의 라벨을
 * 가리킨다. 겹침을 막는 것보다 이름을 안 겹치게 하는 쪽이 짧다. */
let progressSeq = 0;

export function sheetProgress(label: string): string {
  const id = `sheet-progress-label-${++progressSeq}`;
  return `
    <div class="sheet-progress">
      <p class="hint" id="${id}">${esc(label)}</p>
      <div class="sheet-progress-track" role="progressbar"
           aria-labelledby="${id}">
        <span></span>
      </div>
    </div>`;
}

/** 시트 발의 액션 행 — 주 액션은 언제나 오른쪽 끝. 순서가 시트마다 다르면
 *  근육 기억이 무너진다(확인/취소 위치가 바뀌는 것이 오조작의 흔한 원인). */
export function sheetActions(primary: { id: string; label: string; danger?: boolean },
                             secondary?: { id: string; label: string },
                             extra?: { id: string; label: string }): string {
  return `${extra ? `<button class="btn sheet-extra" id="${extra.id}">${extra.label}</button>` : ""}
    <span class="sheet-gap"></span>
    ${secondary ? `<button class="btn" id="${secondary.id}">${secondary.label}</button>` : ""}
    <button class="btn ${primary.danger ? "btn-danger" : "btn-primary"}" id="${primary.id}">${primary.label}</button>`;
}

/** ── 컨텍스트 메뉴(D64) — 우클릭의 보편 관용구 ────────────────────────────
 *
 *  Jakob 의 법칙: 우클릭에 사람들이 기대하는 것은 **메뉴**다. 여태 우클릭이 곧바로
 *  "여기서 추적"을 실행해, 메뉴를 기대한 손이 예고 없이 화면을 갈아탔다(되돌릴
 *  방법도 크럼뿐이었다). 관용구대로 메뉴를 띄우고, 그 안 첫 줄을 추적으로 둔다.
 *
 *  시트도 패널도 아닌 **세 번째 층**이다: 딤 없음, 포인터 좌표에 붙음, 한 번 고르면
 *  사라짐. 화면 밖으로 나가지 않도록 좌표를 접어 넣는다. */
export type MenuItem = { label: string; run: () => void; danger?: boolean };

export function openContextMenu(x: number, y: number, items: MenuItem[]) {
  document.querySelectorAll(".ctx-menu").forEach((m) => m.remove());
  const el = document.createElement("div");
  el.className = "ctx-menu";
  el.setAttribute("role", "menu");
  el.innerHTML = items.map((it, i) =>
    `<button class="ctx-item${it.danger ? " ctx-danger" : ""}" role="menuitem" data-i="${i}">${esc(it.label)}</button>`).join("");
  document.body.appendChild(el);
  // 화면 밖으로 넘치면 반대쪽으로 접는다(모서리에서 잘리지 않게).
  const r = el.getBoundingClientRect();
  el.style.left = `${Math.min(x, window.innerWidth - r.width - 8)}px`;
  el.style.top = `${Math.min(y, window.innerHeight - r.height - 8)}px`;
  const close = () => {
    el.remove();
    document.removeEventListener("mousedown", onOut, true);
    document.removeEventListener("keydown", onKey, true);
  };
  const onOut = (e: MouseEvent) => { if (!el.contains(e.target as Node)) close(); };
  const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); close(); } };
  document.addEventListener("mousedown", onOut, true);
  document.addEventListener("keydown", onKey, true);
  el.querySelectorAll<HTMLElement>(".ctx-item").forEach((b) =>
    b.addEventListener("click", () => { const it = items[Number(b.dataset.i)]; close(); it.run(); }));
  el.querySelector<HTMLElement>(".ctx-item")?.focus();
}

/** 확인·입력 다이얼로그의 등퇴장 배선 — `openSheet` 와 **같은 물성**을 두 형제에게도.
 *
 *  왜 이 함수가 생겼나: 예전에는 `.sheet { animation: sheetRise }` 라는 CSS 한 줄이
 *  세 다이얼로그의 등장을 다 책임졌다. 시트를 WAAPI(motion.ts)로 옮기면서 그 키프레임을
 *  지웠는데, `.sheet` 를 직접 만드는 이 둘은 대체 배선을 못 받아 **모션을 통째로
 *  잃었다**(등장은 사라지고 퇴장은 애초에 없었다). 게다가 스크림 배선도 옛
 *  `e.target === el` 방식이라, 같은 릴리스에서 `wireScrimDismiss` 로 고친 결함(시트
 *  안을 스크롤하다 스크롤바를 잡으면 닫힘)이 여기만 그대로 남아 있었다.
 *
 *  세 곳에 흩어 놓으면 다음에도 한 곳만 고쳐진다. 그래서 한 벌로 묶는다.
 *  반환값은 "닫아라" — 퇴장이 끝난 뒤에 넘긴 콜백을 부른다. */
function wireDialogMotion(el: HTMLElement, dismiss: () => void): (then: () => void) => void {
  const sheetEl = el.querySelector<HTMLElement>(".sheet")!;
  // 이 다이얼로그를 연 버튼. 아래에서 자기 버튼으로 포커스를 옮기기 **전에** 잡는다.
  const trigger = document.activeElement;
  fadeIn(el);
  popIn(sheetEl, trigger);
  wireScrimDismiss(el, dismiss);
  let closed = false;
  return (then) => {
    if (closed) return;            // 애니메이션 도중 두 번 눌려도 한 번만 닫는다
    closed = true;
    void Promise.all([popOut(sheetEl, trigger), fadeOut(el)]).then(() => { el.remove(); then(); });
  };
}

/** 파괴적 동작 확인(S1) — 삭제는 되돌릴 수 없으므로 반드시 이 게이트를 통과시킨다.
 *  overlay + palette-card(편집 시트와 동일 시각 문법). resolve(true) = 진행.
 *  기본 포커스는 취소 쪽에 두고, Esc·배경 클릭도 취소로 접는다(오조작 방지). */
export function confirmDestructive(message: string, confirmLabel: string,
                                   cancelLabel: string): Promise<boolean> {
  return new Promise((resolve) => {
    const el = document.createElement("div");
    el.className = "sheet-scrim sheet-center";
    el.innerHTML = `
      <div class="sheet" role="alertdialog" aria-modal="true">
        <div class="sheet-body" style="padding-top:var(--space-4,1rem)">
          <p style="margin:0">${message}</p>
        </div>
        <div class="sheet-foot">
          <span class="sheet-gap"></span>
          <button class="btn" id="cd-no">${cancelLabel}</button>
          <button class="btn btn-danger" id="cd-yes">${confirmLabel}</button>
        </div>
      </div>`;
    document.body.appendChild(el);
    const close = wireDialogMotion(el, () => done(false));
    const done = (v: boolean) => {
      document.removeEventListener("keydown", onKey);
      close(() => resolve(v));
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") done(false); };
    document.addEventListener("keydown", onKey);
    el.querySelector("#cd-no")!.addEventListener("click", () => done(false));
    el.querySelector("#cd-yes")!.addEventListener("click", () => done(true));
    (el.querySelector("#cd-no") as HTMLButtonElement).focus();
  });
}

/** 텍스트 입력 시트(S2 도메인 이름 변경 등). resolve(문자열) = 확인, null = 취소.
 *  세 번째 버튼(extraLabel)을 주면 그 라벨로 **빈 문자열**을 돌려준다 — 도메인
 *  "미분류로 내리기"처럼 값을 비우는 선택지에 쓴다. */
export function promptText(title: string, initial: string, confirmLabel: string,
                           cancelLabel: string,
                           extraLabel?: string): Promise<string | null> {
  return new Promise((resolve) => {
    const el = document.createElement("div");
    el.className = "sheet-scrim sheet-center";
    el.innerHTML = `
      <div class="sheet" role="dialog" aria-modal="true">
        <div class="sheet-body" style="padding-top:var(--space-4,1rem)">
          <p style="margin:0 0 .6rem">${esc(title)}</p>
          <input id="pt-in" class="rail-filter" style="margin:0" value="${esc(initial)}"/>
        </div>
        <div class="sheet-foot">
          ${extraLabel ? `<button class="btn" id="pt-x">${esc(extraLabel)}</button>` : ""}
          <span class="sheet-gap"></span>
          <button class="btn" id="pt-no">${esc(cancelLabel)}</button>
          <button class="btn btn-primary" id="pt-yes">${esc(confirmLabel)}</button>
        </div>
      </div>`;
    document.body.appendChild(el);
    const input = el.querySelector<HTMLInputElement>("#pt-in")!;
    const close = wireDialogMotion(el, () => done(null));
    const done = (v: string | null) => {
      document.removeEventListener("keydown", onKey);
      close(() => resolve(v));
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") done(null);
      if (e.key === "Enter" && document.activeElement === input) done(input.value.trim());
    };
    document.addEventListener("keydown", onKey);
    el.querySelector("#pt-no")!.addEventListener("click", () => done(null));
    el.querySelector("#pt-x")?.addEventListener("click", () => done(""));
    el.querySelector("#pt-yes")!.addEventListener("click", () => done(input.value.trim()));
    input.focus();
    input.select();
  });
}

/** Top-right primary CTA (Figma: 포인트색 필 버튼, 화면당 1개). */
export function headerCta(id: string, iconName: string, label: string): string {
  return `<button class="btn btn-primary header-cta" id="${id}">${icon(iconName, 16)} ${label}</button>`;
}

/** 벡터(의미) 팔이 빠진 검색을 화면이 말할 말. **정상이면 null** 이다.
 *
 *  판단은 **구조화 필드로만** 한다(`vector_note` 의 영문 문장은 카피가 아니다 —
 *  파싱 금지). 세 갈래:
 *    `embed_migration.pending`  모델 이행 중 — 양성. 남은 문서 수까지 말한다
 *    `vector_rebuilding`        쓰기 뒤 재빌드 — 양성. 잠시 뒤 저절로 돌아온다
 *    `vector_note` 만 있음      진짜 실패 — 창구가 답을 못 줬다(인코더 콜드스타트 등)
 *
 *  ⚠ `arms === "fts"` 만으로 판단하지 않는다. 임베딩 창구가 없는 데스크탑에서는
 *  그것이 **영구 정상 상태**라, 그것만 보면 상시 경보가 되고 상시 경보는 안 읽힌다.
 *  ⚠ 양성 둘과 실패를 가르는 것은 사이드카가 보낸 표식뿐이다. 표식이 없는 응답
 *  (구 사이드카)에서는 재빌드 창이 실패로 보인다 — 사이드카를 올리면 갈린다. */
export type ArmsNotice = { text: string; benign: boolean };

export function armsNotice(res: any): ArmsNotice | null {
  const pending = (res?.embed_migration || {}).pending;
  if (typeof pending === "number" && pending > 0)
    return { text: COPY.search.migrating(pending), benign: true };
  if (res?.vector_rebuilding) return { text: COPY.search.rebuilding, benign: true };
  if (res?.vector_note) return { text: COPY.search.degraded, benign: false };
  return null;
}

/** 위 알림의 배지 markup. 색만으로 신호하지 않는다(D38) — 글리프(◐ 대 ○)와 문장이
 *  신호를 나르고 색은 거들 뿐이다. 정상이면 빈 문자열이라 화면이 그대로 남는다. */
export function armsNoticeBadge(res: any): string {
  const n = armsNotice(res);
  if (!n) return "";
  const tone = n.benign ? "ring" : "danger";
  return `<div style="margin:0 0 var(--space-2,.5rem)"><span class="badge"
    style="border-color:hsl(var(--${tone}))">${
    stateDot(n.benign ? "half" : "empty", 12)} ${n.text}</span></div>`;
}
