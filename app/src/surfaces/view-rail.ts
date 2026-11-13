// 상세 레일(셸의 문서 패널)의 크롬: 폭과 그 그립(끌기 · 키보드), 옆 카드 · 넓게 연 크기, 열 머리(캡션 · 크기 전환 · 닫기), 열고 닫기.
// 그래프 표면(`view.ts`)이 `ViewHost` 와 레일이 붙는 자리(`RailFrame`)를 넘겨 만든다. 열 안의 내용은 표면과 다른 묶음이 그린다.
import { COPY } from "../copy";
import { disposeEditorsIn } from "../doc-editor";
import { closeFloatPanel } from "../float-panel";
import { RAIL_MIN, RAIL_MAX, loadPrefs, savePrefs } from "../view-prefs";
import type { ViewHost } from "./view-host";

/** 레일이 붙는 자리. 셸과 문서 패널은 패널이 없는 임베드에서 없다. 셸이 없으면 폭은 표면 컨테이너(`el`)에 얹는다. */
export type RailFrame = {
  el: HTMLElement;
  shell: HTMLElement | null;
  docpane: HTMLElement | null;
  railGrip: HTMLElement;
};

/** 표면이 쥐는 레일의 손잡이. */
export type Rail = {
  /** 열을 연다. 안의 편집기를 먼저 확정하고, 새 판을 돌려준다. */
  openRail(): number;
  /** 열을 닫는다. 편집기를 확정한 뒤 비우고, 옆 카드 크기로 돌리고, 떠 있는 카드도 닫는다. */
  closeRail(): void;
  /** 열 머리 한 벌의 마크업(캡션 · 크기 전환 · 닫기). */
  railHeadHTML(caption: string): string;
  /** 지금 열에 그린 머리의 크기 전환 · 닫기 단추를 배선한다. */
  wireRailHead(): void;
  /** 지도 칸 전체로 열렸는가. 표면은 LNB 에서 고른 것을 열기 전에 내린다(옆 카드로 연다). */
  full: boolean;
  /** 열의 판. 열고 닫을 때마다 오르고, 표면도 층을 바꿀 때 올린다. 늦게 온 응답은 판이 다르면 그리지 않는다. */
  revision: number;
};

/** 상세 레일을 그 표면의 문서 패널에 묶는다. 만들 때 저장된 폭을 되살리고, 닫힌 채로 시작하고, 그립을 배선한다. */
export function createRail(host: ViewHost, frame: RailFrame): Rail {
  const { detail } = host;
  const { el, shell, docpane, railGrip } = frame;

  /** 문서 열 폭의 **단일 원천**(D70). 여태 CSS(`#view-detail{width:20rem}`)와 JS
   *  상수 두 군데에 같은 숫자가 적혀 있었다. 지금 값은 변수 하나로만 산다:
   *    --rail-size : 사용자가 끌어 정한 폭
   *  이 변수를 **셸**에 얹는다(표면이 아니라) — 폭을 실제로 쓰는 것은 셸의 그리드
   *  열(`#shell[data-doc="open"] { --doc-w: var(--rail-size) }`)이기 때문이다.
   *  겹쳐 뜨던 시절에 필요했던 `--rail-w`(비켜설 거리)는 사라졌다: 열이 캔버스를
   *  덮지 않으므로 비켜설 것이 없다. */
  function setRailWidth(rem: number) {
    const w = Math.min(RAIL_MAX, Math.max(RAIL_MIN, rem));
    (shell ?? el).style.setProperty("--rail-size", `${w}rem`);
    updateRailGrip();
    return w;
  }
  /** 문서 패널의 크기 — 옆 카드(false)냐, 지도 칸 전체(true)냐.
   *
   *  열 때는 늘 옆 카드다(지도에서 고른 것도 LNB 에서 고른 것도). 넓게 여는 것은 패널 머리의
   *  크기 전환 버튼뿐이다. 패널 안에서 옮겨 다니는 것(뒤로·언급 칩·연결 줄)은 지금 크기를
   *  그대로 두고, 닫으면 옆 카드로 돌아간다.
   *  근거: [[decision:lnb-picks-in-the-graph-open-the-floating-card-and-the-side-pane-2026-10-28]] */
  let railFull = false;
  let railRevision = 0;
  function setRailFull(full: boolean) {
    railFull = full;
    if (!detail.hidden) railChrome(true);
    const b = detail.querySelector<HTMLElement>("#rail-size");
    if (b) {
      const label = full ? COPY.ui.viewx.railSide : COPY.ui.viewx.railFull;
      b.textContent = label;
      b.setAttribute("aria-pressed", String(full));
    }
  }
  /** 열 머리 — 캡션 · 크기 전환 · 닫기. 머리를 그리는 자리가 셋(상세 · 리더 · 리더
   *  실패)이라 한 벌로 모았다. 단추는 글자다: 아이콘 세트(Figma 29:56)에 넓히기
   *  글리프가 없고, 그림 하나를 더하는 것은 세트의 일이다. */
  function railHeadHTML(caption: string): string {
    const V = COPY.ui.viewx;
    return `
      <div class="rail-head">
        <span class="hint">${caption}</span>
        <span class="rail-head-acts">
          <button class="rail-x rail-size" id="rail-size" type="button"
                  aria-pressed="${railFull}">${railFull ? V.railSide : V.railFull}</button>
          <button class="rail-x" id="rail-close" type="button" aria-label="${V.railClose}"
                  data-tip="${V.railClose}">✕</button>
        </span>
      </div>`;
  }
  function wireRailHead() {
    detail.querySelector("#rail-close")?.addEventListener("click", closeRail);
    detail.querySelector("#rail-size")?.addEventListener("click", () => setRailFull(!railFull));
  }
  function railChrome(open: boolean) {
    // 패널의 열림과 전체 폭 여부는 셸 상태로 표현한다. 닫을 때 hidden 도 걸어
    // 접힌 패널의 테두리나 그림자가 지도 위에 남지 않게 한다.
    if (shell) {
      shell.dataset.doc = open ? "open" : "closed";
      shell.dataset.docSize = open && railFull ? "full" : "side";
    }
    // 떠 있는 노드 카드는 #shell 밖(body)에 붙어서 셸 상태를 못 본다. 넓게 연 항목 페이지가
    // 같은 노드를 다 보여 주는 동안 카드가 그 제목을 덮지 않도록 body 에도 같은 상태를 걸고,
    // CSS 가 그동안만 카드를 숨긴다. 옆 카드로 돌아오면 카드가 그대로 돌아온다.
    document.body.dataset.docSize = open && railFull ? "full" : "side";
    if (docpane) docpane.hidden = !open;
    // 지도 칸 전체로 연 패널에는 끌어서 줄일 모서리가 없다.
    railGrip.hidden = !open || railFull;
    updateRailGrip();
  }
  /** 레일 닫기 — 여태 상세 패널에는 닫는 수단이 **하나도 없었다**(버튼도 Esc 도).
   *  들어가는 문은 모든 마크에 달려 있는데 나오는 문이 크럼 하나뿐이었던 비대칭을
   *  여기서 끝낸다. 스택(상세→드릴다운→Reader→편집)은 각자 '뒤로'가 있고, ✕/Esc 는
   *  스택 전체를 접는다. */
  function closeRail() {
    ++railRevision;
    host.dropPick();
    detail.hidden = true;
    // 열의 내용을 지우기 **전에** 그 안의 편집기를 확정·해제한다. 아래 innerHTML 은
    // 마운트 지점을 걷어 갈 뿐 디바운스를 건드리지 못한다(저장이 화면 없는 곳에서
    // 터졌다 — 2026-09-07 실측).
    disposeEditorsIn(detail);
    detail.innerHTML = "";
    host.forgetNode();
    railFull = false;     // 다음 열기는 옆 카드가 기본이다(railFull 주석)
    railChrome(false);
    // 카드는 선택에 딸린 물건이라 선택이 끝나면 같이 사라진다(떠 있는 창을 남기지 않는다).
    closeFloatPanel();
  }
  function openRail() {
    ++railRevision;
    host.dropPick();
    // 여는 것도 **덮어쓰는** 것이다: 이 뒤에 오는 것은 예외 없이 `detail.innerHTML`
    // 대입이다(상세 · 관계 · 드릴다운 · 리더 재진입). 그 자리가 스무 곳이
    // 넘으므로 문 앞에서 한 번 걷는다 — 호출부마다 적으면 다음에 느는 하나가 샌다.
    disposeEditorsIn(detail);
    detail.hidden = false;
    railChrome(true);
    return railRevision;
  }
  // CSS가 화면 폭으로 접은 실제 범위를 키보드와 접근성 값도 함께 쓴다.
  function railBounds() {
    const root = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
    const pane = docpane ?? detail;
    const gap = parseFloat(getComputedStyle(pane).right) || 0;
    const max = Math.max(0, Math.min(RAIL_MAX,
      ((pane.parentElement?.clientWidth ?? window.innerWidth) - gap * 2) / root));
    return { root, min: Math.min(RAIL_MIN, max), max };
  }
  function updateRailGrip() {
    if (railGrip.hidden) return;
    const { root, min, max } = railBounds();
    const now = (docpane ?? detail).getBoundingClientRect().width / root;
    railGrip.setAttribute("aria-label", COPY.ui.viewx.railResize);
    railGrip.setAttribute("aria-valuemin", String(min));
    railGrip.setAttribute("aria-valuemax", String(max));
    railGrip.setAttribute("aria-valuenow", String(Math.min(max, Math.max(min, now))));
  }
  /** 패널 **왼쪽** 모서리 그립 — 왼쪽으로 끌면 문서가 넓어진다. 포인터 캡처라
   *  커서가 WebGL 캔버스로 넘어가도 드래그가 끊기지 않는다. 확정 값만
   *  프리퍼런스에 적는다(끄는 동안 매 프레임 저장하지 않는다). */
  function wireRailGrip(grip: HTMLElement) {
    grip.addEventListener("keydown", (e) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
      e.preventDefault();
      e.stopPropagation();
      const { root, min, max } = railBounds();
      const now = (docpane ?? detail).getBoundingClientRect().width / root;
      const next = e.key === "Home" ? min : e.key === "End" ? max
        : now + (e.key === "ArrowLeft" ? 1 : -1) * (e.shiftKey ? 4 : 1);
      const w = setRailWidth(Math.min(max, Math.max(min, next)));
      savePrefs({ ...loadPrefs(), railW: w });
    });
    grip.addEventListener("pointerdown", (e) => {
      if (e.button !== 0 || !e.isPrimary) return;
      e.preventDefault();
      grip.setPointerCapture(e.pointerId);
      const root = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
      const startX = e.clientX;
      const startW = (docpane ?? detail).getBoundingClientRect().width / root;
      let live = startW;
      const move = (ev: PointerEvent) => {
        // ⚠ 부호가 **뺄셈**이다: 패널이 오른쪽에 붙어 있으므로(R1) 왼쪽으로 끌 때
        // 넓어진다. 더하기로 두면 잡은 손과 모서리가 반대로 움직인다.
        live = setRailWidth(startW - (ev.clientX - startX) / root);
      };
      const up = () => {
        grip.removeEventListener("pointermove", move);
        grip.removeEventListener("pointerup", up);
        grip.removeEventListener("pointercancel", up);
        grip.removeEventListener("lostpointercapture", up);
        // ⚠ applyPrefs 를 쓰지 않는다: 그것은 "행을 내보낸 층은 다시 그린다"는 계약이라
        // 폭만 바꿨는데 그래프가 재마운트되고 렌즈 패널까지 열린다. 폭은 렌더러가
        // 읽는 값이 아니므로 저장만 한다.
        savePrefs({ ...loadPrefs(), railW: live });
      };
      grip.addEventListener("pointermove", move);
      grip.addEventListener("pointerup", up);
      grip.addEventListener("pointercancel", up);
      grip.addEventListener("lostpointercapture", up);
    });
    const observer = new ResizeObserver(() => {
      if (!host.isLive() || !grip.isConnected) { observer.disconnect(); return; }
      updateRailGrip();
    });
    observer.observe(docpane ?? detail);
    observer.observe((docpane ?? detail).parentElement!);
  }
  // 레일 폭 복원 + 닫힌 상태로 시작(크롬은 아직 비킬 것이 없다).
  setRailWidth(loadPrefs().railW);
  railChrome(false);
  wireRailGrip(railGrip);

  return {
    openRail, closeRail, railHeadHTML, wireRailHead,
    get full() { return railFull; },
    set full(v: boolean) { railFull = v; },
    get revision() { return railRevision; },
    set revision(v: number) { railRevision = v; },
  };
}
