import Graph from "graphology";
import Sigma from "sigma";
import { api, cfg, type GraphEdge, type GraphNode, type LedgerRes, type LedgerRow,
         type NodeLinks } from "../api";
import { COPY } from "../copy";
import { enableLasso, flyTo3d, findNode3d, mount3d, mountMinis3d,
         type L3, type N3 } from "./atlas3d";
import { mount2d, type Handle2d } from "./atlas2d";
import { bucketRange, foldLedgerPaths, mountTimeline, mountTotals, pickGrain,
         timelinePool, type Grain,
         type PlanarHandle, type TotalsPath } from "./atlas-planar";
import { openShareSheet } from "../sheets";
import { openContextMenu, confirmDestructive } from "../components";

import { LAYOUTS, VIEWS, RAIL_MIN, RAIL_MAX, isPlanar, loadPrefs, savePrefs,
         type AtlasLayout, type AtlasPrefs, type AtlasView } from "../atlas-prefs";
import { lensSlot, viewsSlot, setAtlasHooks, refreshTopbar } from "../topbar";
import { setLnbActive, setLnbNavigator } from "../lnb";
import { openFloatPanel, closeFloatPanel, isFloatPanelOpen } from "../float-panel";
import { icon } from "../icons";

/** Surface 3 — Atlas (spec: vinc.d.graph v0.2 · vinc.d.atlas-visual-language).
 *
 *  Full-3D renderer (DEC D21 revised): L0 Domain Atlas / L1 Domain Lens /
 *  L2 Node Focus run on three.js (see atlas3d.ts — primitives = type,
 *  monochrome ink materials, paper fog). Compare keeps flat Sigma lenses —
 *  side-by-side comparison is deliberately planar.
 *
 *  Navigation: L0 click = open lens · L0 link click = Compare ·
 *  L1 click = gateway→follow / node→detail · right-click = L2 refocus.
 *
 *  D43 — 두 축뿐이다: "어디"는 Level(trail·크럼)이 쥐고, "어떻게"는
 *  prefs.view(관점 5지선다 — 3D/2D/Flow/Timeline/Totals)가 쥔다. 관점은
 *  trail 에 절대 들어가지 않으므로 둘은 구조적으로 어긋날 수 없다. */

// title: 크럼이 읽는 사람의 말. 여태 L2 크럼이 raw id(`concept:physics-ads-cft`)를
// 그대로 실었다 — 딥링크로 들어오면 제목을 모르므로 선택 필드로 두고, 알게 되는
// 순간(노드 클릭·그래프 응답) 채운다(domain 과 같은 규칙).
type Level = { kind: "l0" } | { kind: "l1"; domain: string }
  | { kind: "l2"; seed: string; domain?: string; title?: string }
  | { kind: "compare"; a: string; b: string }
  | { kind: "grid" } | { kind: "matrix" };

// Signal 팔레트 — theme.ts(허브 생성물)에서 런타임 모드에 따라 읽는다(하드코딩 금지).
import { palette as signal, hexStr } from "../theme";
function curPal() {
  return signal[document.documentElement.dataset.theme === "dark" ? "dark" : "light"];
}
const THEME = {
  get ink() { return hexStr(curPal().text); },
  get inkMute() { return hexStr(curPal().textMute); },
  get edge() { return hexStr(curPal().line); },
  get point() {
    const dark = document.documentElement.dataset.theme === "dark";
    return hexStr(dark ? curPal().ring : curPal().primary);
  },
};

/** 마운트 세대 — 라우터가 표면을 갈아끼워도 컨테이너는 남으므로, "내가 아직 살아
 *  있는가"는 이 카운터로만 판별한다(전역 리스너 누수 방지). */
let liveGen = 0;

export async function renderAtlas(el: HTMLElement) {
  const myGen = ++liveGen;
  el.innerHTML = `
    <div id="atlas-canvas"></div>
    <!-- 하단 바에는 **지표만** 남는다(D71). 렌즈와 관점 세그는 상단 바 좌측으로
         올라갔다 — 좌측을 문서 트리가 차지하면서 하단 바의 남은 일이 "지금 화면이
         무엇을 세고 있는가" 하나가 됐기 때문이다. 지표는 여전히 떠 있는 알약이
         아니라 상태 표시줄이다(떠 있는 알약은 배너로 읽혀 무시된다 — D65). -->
    <div id="atlas-chrome">
      <div id="atlas-metrics"></div>
    </div>
    <div id="atlas-detail" hidden></div>
    <!-- 폭 조절 그립은 패널의 **형제**다: 패널 내용은 화면마다 innerHTML 로 갈리는데
         (상세·관계·드릴다운·기간·리더) 그 안에 두면 매번 다시 붙이고 다시 배선해야 한다. -->
    <div id="rail-grip" role="separator" aria-orientation="vertical" hidden></div>
    <!-- D70 — 인캔버스 크롬의 위치를 인라인 style 에서 **CSS 로 되돌린다.** 여태
         인라인이었던 이유는 레일이 열릴 때 JS 가 같은 속성을 덮어써야 했기 때문인데
         (클래스 규칙은 인라인을 못 이긴다), 이제 레일 폭이 CSS 변수 하나(--rail-w)라
         비켜서는 일을 CSS 가 스스로 한다. 폭의 정의가 한 군데로 모인다. -->
    <div id="atlas-msg"></div>
    <div id="atlas-tools"></div>
    <div id="atlas-float" hidden></div>
    <div id="atlas-hint" hidden></div>
    <div id="atlas-view-panel" hidden></div>
  `;
  const canvas = el.querySelector<HTMLElement>("#atlas-canvas")!;
  const detail = el.querySelector<HTMLElement>("#atlas-detail")!;
  const railGrip = el.querySelector<HTMLElement>("#rail-grip")!;
  const msg = el.querySelector<HTMLElement>("#atlas-msg")!;
  const tools = el.querySelector<HTMLElement>("#atlas-tools")!;
  // D71 — 렌즈·관점은 상단 바 슬롯에 그린다(내용의 주인은 여전히 이 표면이다).
  // 바가 없는 환경(임베드)에서는 표면 안에 자기 자리를 만들어 같은 코드로 그린다:
  // 그리는 함수는 한 벌이고 자리만 다르다.
  const chromeBar = el.querySelector<HTMLElement>("#atlas-chrome")!;
  const fallbackSlot = (id: string) => {
    const d = document.createElement("div");
    d.id = id;
    chromeBar.prepend(d);
    return d;
  };
  const legend = lensSlot() ?? fallbackSlot("atlas-legend");
  const viewsBar = viewsSlot() ?? fallbackSlot("atlas-views");
  const metricsBar = el.querySelector<HTMLElement>("#atlas-metrics")!;
  const float = el.querySelector<HTMLElement>("#atlas-float")!;
  const lassoHint = el.querySelector<HTMLElement>("#atlas-hint")!;
  lassoHint.textContent = COPY.ui.atlasx.lassoHint;
  const viewPanel = el.querySelector<HTMLElement>("#atlas-view-panel")!;
  // 레일 폭 복원 + 닫힌 상태로 시작(크롬은 아직 비킬 것이 없다).
  setRailWidth(loadPrefs().railW);
  railChrome(false);
  wireRailGrip(railGrip);

  // lasso(영역 선택→공유) 배선 + 발견성 힌트. L1/L2 그래프에서만 활성.
  function wireLasso() {
    lassoHint.textContent = COPY.ui.atlasx.lassoHint;   // 다른 용도로 쓰였을 수 있다
    lassoHint.hidden = false;   // "Shift+드래그로 영역 선택" 안내(발견성)
    enableLasso(g3, canvas, (ids) => {
      lassoHint.hidden = true;
      float.hidden = false;
      float.innerHTML = `<button class="btn btn-primary">${COPY.ui.atlasx.shareSlice(ids.length)}</button>`;
      float.querySelector("button")!.addEventListener("click", () => {
        float.hidden = true; openShareSheet({ ids });
      });
      setTimeout(() => { float.hidden = true; lassoHint.hidden = false; }, 8000);
    });
  }

  let g3: any = null;                 // 3D 인스턴스 (L0/L1/L2)
  let g2: Handle2d | null = null;     // 2D 인스턴스 (D46 — 독립 렌더러)
  let minis3d: { dispose(): void } | null = null;   // Grid 미니(단일 렌더러, D34)
  let planar: PlanarHandle | null = null;           // 평면 관점(Flow/Timeline/Totals, D43)
  let lastNode: N3 | null = null;                   // Reader 뒤로가기용 마지막 상세 노드(D45-V2)
  let extraSigmas: Sigma[] = [];      // Compare 2D 렌즈
  let curEdges: GraphEdge[] = [];     // 현재 레벨 엣지 — 상세 레일 in/out 표기용
  // 지금 그린 노드들 — focusNode 가 id 만 받고도 라벨·주제를 알아야 한다(D70).
  // 렌더러마다 노드를 꺼내는 법이 달라(3D 는 graphData, 2D 는 graphology, 평면은
  // DOM) 마운트 시점에 한 벌 챙겨 두는 편이 확실하다.
  let curNodes: GraphNode[] = [];
  // 지금 타임라인이 쓰는 버킷 알갱이(D72) — 밴드 클릭이 같은 구간을 만들어야 한다.
  let periodGrain: Grain = "month";
  // 마지막으로 짚은 곳(D70) — 합계의 `기타` 병합 핀에 쓴다. trail 과 별개다:
  // 트리에서 문서를 눌러도 층은 그대로일 수 있는데, 그때도 그 주제는 살아 있어야 한다.
  let lnbFocus: { domain?: string; nodeId?: string } = {};
  const cardLayout = new Map<string, AtlasLayout>();  // Grid 카드별 프리셋 오버라이드(D34)
  const trail: Level[] = [];

  // deep links: #atlas/<domain> · #atlas/compare/<a>/<b> · #atlas/trace/<id> · …/as/<view>
  const parts = location.hash.split("/").map(decodeURIComponent);
  const deep = parts[1] || "";
  // /as/<view> 는 이 마운트에서만 저장값을 이긴다 — 절대 savePrefs 하지 않는다
  // (공유 링크가 받는 사람의 저장 관점을 영구히 덮어쓰지 않게).
  const asAt = parts.indexOf("as");
  let hashView: AtlasView | null =
    asAt > 0 && VIEWS.includes(parts[asAt + 1] as AtlasView) ? (parts[asAt + 1] as AtlasView) : null;
  const curPrefs = (): AtlasPrefs => (hashView ? { ...loadPrefs(), view: hashView } : loadPrefs());

  function setMsg(html: string) {
    msg.innerHTML = html;
    msg.style.display = html ? "grid" : "none";
    // 빈 상태의 CTA 는 표면으로 데려가지 않고 그 자리에서 시트를 연다 — 첫 사용자가
    // "가져오기"를 누르고 또 다른 화면에서 다시 "가져오기"를 찾게 두지 않는다.
    msg.querySelector("[data-import]")?.addEventListener("click", async () => {
      const { pickAndImport } = await import("../sheets");
      pickAndImport();
    });
  }

  /** 범례 — 닫힌 층(형태=타입 · 선=관계 · 초점색 1점)을 화면이 스스로 가르친다(U1).
   *  여태 `#atlas-legend` 는 CSS 와 DOM 만 있고 **채우는 코드가 어디에도 없었다**.
   *
   *  형태는 정본(D35)을 따른다 — 개념=원, 기록물=육각, 다리=마름모, 주제=원+링.
   *  닫힌 층이라 시안이 바꿀 수 있는 층이 아니다(시안의 □·△ 미채택).
   *
   *  자리는 렌즈 패널 안이다: 지도를 보는 손잡이(관점·범위·옵션)와 지도를 읽는 법
   *  (범례)은 같은 물음("이 그림을 어떻게 볼까")에 답하므로 한 패널에 둔다. */
  const LEGEND_KEY = "vinc.atlas.legend.open";
  const OPTS_KEY = "vinc.atlas.opts.open";
  function legendHTML(): string {
    const L = COPY.ui.legend;
    const mark = (svg: string, label: string) =>
      `<span class="lg-item"><svg class="lg-mark" viewBox="0 0 16 16" aria-hidden="true">${svg}</svg>${label}</span>`;
    const line = (attrs: string, label: string) =>
      `<span class="lg-item"><svg class="lg-mark" viewBox="0 0 16 16" aria-hidden="true">`
      + `<path d="M1 8h14" ${attrs}/></svg>${label}</span>`;
    const hex = (cx: number, cy: number, r: number) => Array.from({ length: 6 }, (_, i) => {
      const a = (Math.PI / 180) * (60 * i - 30);
      return `${(cx + r * Math.cos(a)).toFixed(1)},${(cy + r * Math.sin(a)).toFixed(1)}`;
    }).join(" ");
    return `<div class="lg-body">
      <p class="hint lg-cap">${L.shapes}</p>
      ${mark(`<circle cx="8" cy="8" r="4.4"/>`, L.concept)}
      ${mark(`<polygon points="${hex(8, 8, 6.6)}"/>`, L.document)}
      ${mark(`<polygon points="8,1.6 14.4,8 8,14.4 1.6,8"/>`, L.bridge)}
      ${mark(`<circle cx="8" cy="8" r="3.6"/><ellipse cx="8" cy="8" rx="7.2" ry="2.6" fill="none" stroke="currentColor" stroke-width="1.1"/>`, L.topic)}
      <p class="hint lg-cap">${L.lines}</p>
      <!-- ⚠ 범례는 **실제로 그려지는 것만** 가르친다. 정본(§1.2)은 파선=크로스 ·
           점선=Bridge 매개 · 이중선=상속을 규정하지만, 2D(sigma)·3D(three) 어느
           렌더러도 아직 점선 계열을 그리지 않는다(D33 "후속" 명시). 그리지 않는
           문법을 범례가 가르치면 화면이 자기를 반박한다 — 지금 변별에 실제로
           쓰이는 채널(굵기·화살촉·농도)을 그대로 싣는다. -->
      ${line(`stroke="currentColor" stroke-width="1.8"`, L.directed)}
      ${line(`stroke="currentColor" stroke-width="1"`, L.related)}
      ${line(`stroke="currentColor" stroke-width=".8" opacity=".5"`, L.cross)}
      <p class="hint lg-cap lg-focus"><span class="lg-dot"></span>${L.focus}</p>
    </div>`;
  }

  /** 렌즈 버튼 — 아이콘 하나. 누르면 위로 패널이 열리고 그 안에 `보기`(관점·범위·
   *  옵션)와 `범례`가 함께 있다. 상단 바에 있던 `보기` 버튼이 여기로 합쳐졌다:
   *  지도에 관한 손잡이는 지도 위에 두고, 바는 검색·공유에 자리를 내준다. */
  let lensBtn: HTMLElement | null = null;
  function drawLegend() {
    legend.innerHTML =
      `<button class="lens-btn" id="atlas-lens" aria-haspopup="dialog" aria-expanded="false"
               aria-controls="atlas-view-panel" data-tip="${esc(COPY.ui.legend.lensTip)}"
               aria-label="${esc(COPY.ui.legend.lensTip)}">${icon("dot-grid", 17)}</button>`;
    lensBtn = legend.querySelector<HTMLElement>("#atlas-lens");
    lensBtn?.addEventListener("click", () => (viewPanel.hidden ? openPanel() : closePanel()));
  }

  /** 표면 공용 이스케이프 — 여태 함수마다 같은 것을 다시 선언하고 있었다(esc·esc2). */
  const esc = (s: string) => s.replace(/[&<>"]/g, (c) =>
    c === "&" ? "&amp;" : c === "<" ? "&lt;" : c === ">" ? "&gt;" : "&quot;");

  /** 레일 닫기 — 여태 상세 패널에는 닫는 수단이 **하나도 없었다**(버튼도 Esc 도).
   *  들어가는 문은 모든 마크에 달려 있는데 나오는 문이 크럼 하나뿐이었던 비대칭을
   *  여기서 끝낸다. 스택(상세→드릴다운→Reader→편집)은 각자 '뒤로'가 있고, ✕/Esc 는
   *  스택 전체를 접는다. */
  /** 레일 폭의 **단일 원천**(D70). 여태 CSS(`#atlas-detail{width:20rem}`)와 JS 상수
   *  두 군데에 같은 숫자가 적혀 있었고, 열릴 때 비켜서는 크롬 3개는 JS 가 인라인
   *  style 로 손수 밀었다. 이제 값은 변수 둘로만 산다:
   *    --rail-size : 사용자가 끌어 정한 폭(패널 자신의 width)
   *    --rail-w    : 지금 실제로 점유 중인 폭(닫히면 0) — 크롬이 이걸 보고 비킨다
   *  덕분에 "레일이 열렸을 때 무엇이 비켜야 하는가"가 CSS 한 곳의 규칙이 된다. */
  function setRailWidth(rem: number) {
    const w = Math.min(RAIL_MAX, Math.max(RAIL_MIN, rem));
    el.style.setProperty("--rail-size", `${w}rem`);
    return w;
  }
  function railChrome(open: boolean) {
    el.style.setProperty("--rail-w", open ? "var(--rail-size)" : "0px");
    railGrip.hidden = !open;
  }
  function closeRail() {
    detail.hidden = true;
    detail.innerHTML = "";
    lastNode = null;
    railChrome(false);
    // 카드는 선택에 딸린 물건이라 선택이 끝나면 같이 사라진다(떠 있는 창을 남기지 않는다).
    closeFloatPanel();
  }
  function openRail() {
    detail.hidden = false;
    railChrome(true);
  }
  /** 좌측 모서리 그립 — 끌어서 폭 조절. 포인터 캡처라 커서가 캔버스(WebGL) 위로
   *  넘어가도 드래그가 끊기지 않는다. 확정 값만 프리퍼런스에 적는다(끄는 동안
   *  매 프레임 localStorage 를 두드리지 않게). */
  function wireRailGrip(grip: HTMLElement) {
    grip.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      grip.setPointerCapture(e.pointerId);
      const root = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
      const startX = e.clientX;
      const startW = detail.getBoundingClientRect().width / root;
      let live = startW;
      const move = (ev: PointerEvent) => {
        live = setRailWidth(startW + (startX - ev.clientX) / root);
      };
      const up = () => {
        grip.removeEventListener("pointermove", move);
        grip.removeEventListener("pointerup", up);
        // ⚠ applyPrefs 를 쓰지 않는다: 그것은 "행을 내보낸 층은 다시 그린다"는 계약이라
        // 폭만 바꿨는데 그래프가 재마운트되고 렌즈 패널까지 열린다. 폭은 렌더러가
        // 읽는 값이 아니므로 저장만 한다.
        savePrefs({ ...loadPrefs(), railW: live });
      };
      grip.addEventListener("pointermove", move);
      grip.addEventListener("pointerup", up);
    });
  }
  // Esc 단계 후퇴(정본 §2) 1단계 — 레일이 열려 있으면 레일부터 닫는다. 보기 패널이
  // 열려 있을 땐 그쪽 핸들러가 먼저 잡으므로(패널에 포커스가 있다) 충돌하지 않는다.
  /** 정본 §2 조작 문법(V12) — 여태 규정만 있고 구현이 없던 키들.
   *  Esc 는 **단계적 후퇴**다: 패널 → 레일 → 상위 층. 한 번에 다 닫지 않는다.
   *  `f` = 지금 층을 화면에 맞춤. 입력 필드 안에서는 전부 무시(타이핑 보호). */
  const onKey = (e: KeyboardEvent) => {
    if (myGen !== liveGen) { document.removeEventListener("keydown", onKey); return; }
    const t = e.target as HTMLElement | null;
    const typing = !!t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA"
      || t.isContentEditable);
    if (typing) return;
    if (e.key === "Escape") {
      // 가장 나중에 뜬 것부터 접는다. 카드가 렌즈 패널보다 앞이다 — 카드는 방금
      // 고른 노드에 딸린 것이고, 패널은 그보다 오래 떠 있던 크롬이다(D70).
      if (isFloatPanelOpen()) { closeFloatPanel(); return; }
      if (!viewPanel.hidden) { closePanel(); return; }
      if (!detail.hidden) { closeRail(); return; }
      if (trail.length > 1) { trail.pop(); void show(trail[trail.length - 1], false); }
      return;
    }
    if (e.key === "f" || e.key === "F") {
      // 3D 는 전체 맞춤, 2D 는 sigma 카메라 리셋 — 둘 다 "지금 층을 화면에".
      (g3 as any)?.zoomToFit?.(400, 60);
      g2?.fit?.();
    }
  };
  document.addEventListener("keydown", onKey);
  // 표면이 갈아끼워지면 전역 리스너를 걷어낸다. **컨테이너 존재로는 판별할 수 없다** —
  // 라우터는 #surface 의 innerHTML 만 비우고 컨테이너는 그대로 두기 때문이다(구
  // MutationObserver 판정이 영원히 참이라 리스너가 쌓였다). 마운트 세대로 가른다.

  const isScope = (lv: Level) => lv.kind === "l0" || lv.kind === "l1" || lv.kind === "l2";
  const viewName = (v: AtlasView) =>
    v === "3d" ? "3D" : v === "2d" ? "2D"
    : v === "timeline" ? COPY.ui.atlasx.timeline : COPY.ui.atlasx.totals;
  const frameName = (lv: Level) =>
    lv.kind === "grid" ? COPY.ui.atlasx.grid
    : lv.kind === "matrix" ? COPY.ui.atlasx.matrix
    : lv.kind === "compare" ? `${lv.a} ⇄ ${lv.b}` : "";
  /** 그 층이 그 관점을 실제로 그리는가 — 관점 커밋이 프레임에 남을지 떠날지를
   *  판정하는 단 하나의 자리. 스코프(l0/l1/l2)는 5종을 전부 그린다. Grid 는 미니가
   *  prefs.view 를 읽어(flat = 2d) 3D/2D 만 그리고 평면 3종은 그리지 않는다.
   *  Compare/Matrix 는 렌더러가 프리퍼런스를 한 줄도 읽지 않아 어떤 관점도 그리지
   *  않는다 → 거기서의 관점 커밋은 평면 커밋과 같이 프레임을 떠나야 한다(한 행에
   *  두 가지 의미가 섞이지 않게). */
  const rendersView = (lv: Level, v: AtlasView): boolean =>
    isScope(lv) ? true : lv.kind === "grid" ? !isPlanar(v) : false;
  /** 화면에 실제로 걸린 관점. 칩 라벨·크럼 꼬리·패널의 활성 표시·재클릭 가드가
   *  모두 이 하나만 읽는다 — UI 가 렌더러에 없는 상태를 주장하지 않게 하는 단일 진실.
   *  Grid: 미니는 저장값이 평면이어도 3D 컷을 그린다 → 그리는 것("3D")을 표시한다
   *  (저장된 평면 관점은 프레임을 벗어나는 순간 그 자리에서 되살아난다).
   *  Compare/Matrix: 자기 화면만 그리므로 걸린 관점이 없다 → null(아무 칩도 활성 아님).
   *  가장 작은 표현을 고른 이유 — 비활성/숨김은 관점을 "못 고르는 것"으로 보이게 하고
   *  (§3.2: 관점은 절대 비활성·숨김이 아니다), 별도 안내 문구는 새 카피를 요구한다. */
  const shownView = (lv: Level): AtlasView | null =>
    isScope(lv) ? curPrefs().view
    : lv.kind === "grid" ? (curPrefs().view === "2d" ? "2d" : "3d")
    : null;
  /** 평면 관점의 스코프는 오롯이 trail 에서 읽는다(관점은 스코프를 갖지 않는다). */
  const scopeOf = (lv: Level): { seed?: string; domain?: string } =>
    lv.kind === "l1" ? { domain: lv.domain }
    : lv.kind === "l2" ? { seed: lv.seed, domain: lv.domain }
    : {};
  /** 딥링크(#atlas/trace/<id>)만 주제를 모른 채 들어온다 — 앱 안에서 미는 l2 는
   *  전부 domain 을 싣는다. 씨앗의 주제를 알게 된 순간 trail 항목 자체를 채워
   *  스코프를 읽는 모든 곳(Totals 원장 필터 등)이 같은 값을 보게 한다. 채우지
   *  않으면 크럼은 한 노드를 가리키는데 Totals 는 전체 롤업을 그린다. */
  function fillSeedDomain(seed: string, nodes: GraphNode[]) {
    const lv = trail[trail.length - 1];
    if (lv.kind !== "l2" || lv.seed !== seed) return;
    const n = nodes.find((x) => x.id === seed);
    if (!lv.domain && n?.domain) lv.domain = n.domain;
    // 딥링크(#atlas/trace/<id>)는 주제를 거치지 않고 노드에 바로 앉는다. 주제를
    // 알게 된 순간 그 층을 트레일에 **끼워 넣어** 직접 링크로 온 사람도 같은
    // 뒤로가기(Esc 후퇴·공유 스코프)를 얻게 한다.
    if (lv.domain && !trail.some((t) => t.kind === "l1" && t.domain === lv.domain))
      trail.splice(trail.length - 1, 0, { kind: "l1", domain: lv.domain });
    if (!lv.title && n?.label && n.label !== seed) lv.title = n.label;
    // 위치 표시는 좌측 트리가 한다(D69·D71 — 크럼 폐지). 씨앗의 주제를 이제야
    // 알았으므로 트리의 활성 표시도 여기서 따라가야 한다. 안 그러면 딥링크로
    // 들어온 화면만 트리가 아무 데도 가리키지 않는다.
    setLnbActive({ domain: lv.domain, nodeId: seed });
  }

  /** D31 + D43 — 인캔버스 View 컨트롤. view 는 이제 관점 5지선다(맵 3D/2D + 평면 3종)라
   *  칩은 현재 관점 이름을 그대로 싣는다. Grid/Matrix 는 같은 패널의 두 번째 행으로
   *  옮겨 스위처를 하나로 만든다(툴 행에 느슨한 칩을 남기지 않는다).
   *  닫힌 층(형태=타입 · 포인트색=초점 · 모노크롬)은 여전히 노출하지 않는다. */
  /** 칩 라벨 — 프레임에 있으면 프레임 이름, 아니면 관점 이름. 그리지도 않는 관점을
   *  칩이 주장하면(예: Grid 에서 "보기 · 흐름") 화면과 컨트롤이 서로를 반박한다. */
  const viewLabel = () => frameName(trail[trail.length - 1]) || viewName(curPrefs().view);
  /** D56 — `보기`·`공유` 는 상단 바 버튼이다. 표면은 자기 동작만 등록하고, 버튼의
   *  존재·라벨은 바가 관리한다(Atlas 를 떠나면 자동으로 사라진다). */
  function viewControl() { refreshTopbar(); drawViews(); }

  /** 관점 세그먼트 — **상시 노출**(D65). 여태 렌즈 패널 안에 있어, 가장 자주 쓰는
   *  전환을 하려면 매번 패널을 열어야 했다(칩 20개짜리 1102px 패널 안에서 4개를
   *  찾는 일). 관점은 4지선다라 바에 그대로 눕히는 것이 맞다 — 선택지가 보이면
   *  결정 시간이 짧아진다(Hick). 프레임·옵션·범례는 렌즈 패널에 남는다. */
  function drawViews() {
    const shown = shownView(trail[trail.length - 1]);
    viewsBar.innerHTML = `<span class="seg">${VIEWS.map((v) =>
      `<button class="chip${v === shown ? " active" : ""}" data-view="${v}">${viewName(v)}</button>`)
      .join("")}</span>`;
    viewsBar.querySelectorAll<HTMLElement>("[data-view]").forEach((b) =>
      b.addEventListener("click", () => void applyView(b.dataset.view as AtlasView)));
  }

  /** 지표바 — 전 관점 공통(구 2D 전용). 떠 있는 알약이 아니라 하단 상태 표시줄이라
   *  배너로 읽혀 무시되는 자리를 벗어난다. `연결 없음` 은 **누를 수 있다** — 세어만
   *  놓고 해소할 길을 안 주면 미완의 일로 남는다(Zeigarnik). */
  function drawMetrics(nodes: { degree?: number; ts?: string }[] | null) {
    if (!nodes || nodes.length === 0) { metricsBar.innerHTML = ""; return; }
    const total = nodes.length;
    const linked = nodes.filter((n) => (n.degree || 0) > 0).length;
    const orphan = total - linked;
    const rate = total ? Math.round((linked / total) * 100) : 0;
    const latest = nodes.map((n) => n.ts || "").filter(Boolean).sort().pop() || "";
    const cell = (label: string, value: string, act = false) =>
      `<span class="a2-metric${act ? " metric-act" : ""}"${act ? ' data-orphans="1" role="button" tabindex="0"' : ""}>`
      + `<b class="pl-num">${value}</b>${label}</span>`;
    metricsBar.innerHTML =
      cell(COPY.ui.atlasx.mDocs, String(total))
      + cell(COPY.ui.atlasx.mLinked, `${rate}%`)
      + cell(COPY.ui.atlasx.mOrphan, String(orphan), orphan > 0)
      + (latest ? cell(COPY.ui.atlasx.mLatest, latest.slice(0, 10)) : "");
    const openOrphans = () => {
      const lv = trail[trail.length - 1];
      void openDrilldown(lv.kind === "l1" ? lv.domain : "", "orphan",
        lv.kind === "l2" ? lv.seed : undefined);
    };
    metricsBar.querySelector<HTMLElement>("[data-orphans]")?.addEventListener("click", openOrphans);
  }

  // 패널 열림/닫힘·aria·포커스를 한 곳에서만 만진다(드리프트 방지).
  function openPanel() {
    viewPanel.hidden = false;
    // 렌즈 버튼이 상단 바로 올라가면서(D71) 패널은 표면 밖의 버튼에 붙어야 한다 —
    // 표면 기준 absolute 로는 사이드바 폭·타이틀바·웹 GNB 만큼 어긋난다. 좌표를
    // 버튼 실측 rect 에서 뽑아 fixed 로 놓으면 세 스킨이 전부 자동으로 맞는다.
    const r = lensBtn?.getBoundingClientRect();
    if (r) {
      viewPanel.style.top = `${Math.round(r.bottom + 6)}px`;
      viewPanel.style.left = `${Math.round(Math.min(r.left, window.innerWidth - 300))}px`;
    }
    lensBtn?.setAttribute("aria-expanded", "true");
    // 발견 힌트는 "한 번 열면" 끝난다 — 쓰는 곳도 읽는 곳도 하나씩.
    localStorage.setItem("vinc.atlas.views.seen", "1");
    if (lassoHint.textContent === COPY.ui.atlasx.viewsHint) lassoHint.hidden = true;
    drawViewPanel();
    // 활성 칩이 없는 프레임(Compare — 관점도 프레임도 활성이 아니다)에서도 포커스는
    // 반드시 패널 안으로 들어와야 한다: Escape 핸들러가 패널에 걸려 있어, 포커스가
    // 밖(상단 바 `보기` 버튼)에 남으면 열어 놓고 키보드로 닫을 길이 사라진다.
    (viewPanel.querySelector<HTMLElement>(".seg .chip.active")
      ?? viewPanel.querySelector<HTMLElement>(".seg .chip"))?.focus();
  }
  function closePanel() {
    viewPanel.hidden = true;
    const b = lensBtn;
    b?.setAttribute("aria-expanded", "false");
    b?.focus();
  }

  /** 옵션 행 — 키는 "관점"이 아니라 **그 층의 렌더러가 실제로 읽는 것**이다.
   *  아무 일도 하지 않는 컨트롤은 아예 그리지 않는다(보이면 반드시 화면이 바뀐다).
   *
   *  Grid: 미니 반경=size(nodeRadius), 카드 기본 배치=layout, 고아 제거=unlinked
   *    (filterUnlinked). 라벨·화살표·산개는 미니가 아예 그리지 않는다.
   *  Compare/Matrix: 렌더러가 프리퍼런스를 한 줄도 읽지 않는다 → 행 없음.
   *  L0: **더는 특례가 아니다**(D69). 구 L0 는 자기 원환을 직접 계산해(반경=records
   *    고정 공식, ties 엣지에 type 없음) size·arrows·layout 이 아무것도 안 바꿨고,
   *    타임라인 L0 도 주제 노드에 날짜가 없다는 이유로 행이 비어 있었다. 지금 L0 는
   *    L1/L2 와 같은 `/graph` 를 같은 렌더러로 그리므로 같은 행을 쓴다(타임라인 L0 의
   *    빈 행은 D46-S 로 이미 stale 이었다 — 그때부터 전량 노드를 그려 왔다).
   *  L1/L2 + Timeline: 좌표를 쓰지 않는 평면 차트라 layout·spread·size 는 무의미
   *    (fetch 는 layout 을 실어 보내지만 mountFlow/mountTimeline 은 좌표를 안 읽는다).
   *  Totals + 출처별: 원장 구간이 episodic·record·unsourced 라 고아 구간이 없다 →
   *    unlinked 가 아무것도 바꾸지 못한다(주제별일 때만 orphan 구간을 걷어낸다). */
  /** 평면(SVG DOM) 상한 — 노드마다 <g> 를 만드는 렌더라 전량 스코프(L0)에서
   *  무한정 그릴 수 없다. 넘치면 최근 것부터 남기고 캡션이 잘린 수를 밝힌다. */
  const PLANAR_CAP = 1200;
  const GRID_ROWS = ["size", "unlinked", "layout"];
  function rowsFor(lv: Level, p: AtlasPrefs): string[] {
    if (lv.kind === "grid") return GRID_ROWS;
    if (!isScope(lv)) return [];
    switch (p.view) {
      case "3d":
      case "2d":
        return ["size", "labels", "arrows", "unlinked", "layout", "spread"];
      // D49 — `깊이` 는 **씨앗이 있을 때만** 뜻이 있다("씨앗에서 몇 세대"). 씨앗 없는
      // 층(L0/L1)에서는 자를 기준점이 없어 렌더러가 상한을 걸지 않으므로, 아무 일도
      // 안 하는 컨트롤을 내놓지 않는다(보이는 손잡이는 반드시 무언가를 해야 한다).
      case "timeline": return ["basis", "docs", "labels"];
      // D47 — 3열 생키는 출처와 상태를 **동시에** 보여주므로 묶음 토글이 사라졌고,
      // 고아 숨김도 받지 않는다(주변합만으로 걷어내면 열 합 보존이 깨진다 —
      // atlas-planar.mountTotals 주석). 원장 뷰에 남는 선택지가 없다.
      case "totals": return [];
    }
  }

  function drawViewPanel() {
    const p = curPrefs();
    const lv = trail[trail.length - 1];
    const chip = (pref: string, val: string, label: string, active: boolean) =>
      `<button class="chip${active ? " active" : ""}" data-pref="${pref}" data-val="${val}">${label}</button>`;
    const row = (cap: string, chips: string) =>
      `<p class="hint">${cap}</p><p><span class="seg">${chips}</span></p>`;
    // 활성 표시는 "지금 그려지는 것"만 따른다(shownView) — Compare/Matrix 는 어떤
    // 관점도 그리지 않아 무표시, Grid 는 미니가 그리는 3D/2D 중 하나를 표시한다.
    // 저장값은 프레임 안에서도 그대로 살아 있다(벗어나면 그 자리에서 되살아난다).
    const shown = shownView(lv);
    const fchip = (k: "grid" | "matrix", label: string) =>
      `<button class="chip${lv.kind === k ? " active" : ""}" data-frame="${k}">${label}</button>`;

    // 두 줄로 나눈 이유: 첫 줄은 "어떻게 보는가"(크럼 불변), 둘째 줄은 "어디에 있는가"
    // (크럼이 자라고 뒤로가기가 먹는다). 한 줄 7칩은 이 차이를 지운다.
    // 관점은 하단 바 세그먼트가 상시로 맡는다(D65) — 여기 또 두면 같은 선택지가
    // 두 곳에 살고, 패널은 그만큼 길어진다.
    const head = `
      <p class="hint" style="margin-top:0">${COPY.ui.panel.allTopics}</p>
      <p><span class="seg">${fchip("grid", COPY.ui.atlasx.grid)}${fchip("matrix", COPY.ui.atlasx.matrix)}</span></p>`;

    const rows: Record<string, string> = {
      size: row(COPY.ui.panel.nodeSize,
        chip("size", "links", COPY.ui.panel.byConnections, p.size === "links")
        + chip("size", "uniform", COPY.ui.panel.uniform, p.size === "uniform")),
      labels: row(COPY.ui.panel.labels,
        chip("labels", "hover", COPY.ui.panel.onHover, p.labels === "hover")
        + chip("labels", "none", COPY.ui.panel.off, p.labels === "none")),
      arrows: row(COPY.ui.panel.arrows,
        chip("arrows", "on", COPY.ui.panel.on, p.arrows)
        + chip("arrows", "off", COPY.ui.panel.off, !p.arrows)),
      unlinked: row(COPY.ui.panel.unlinkedNodes,
        chip("unlinked", "show", COPY.ui.panel.show, p.unlinked === "show")
        + chip("unlinked", "hide", COPY.ui.panel.hide, p.unlinked === "hide")),
      layout: row(COPY.ui.panel.layout, LAYOUTS.map((l) => chip("layout", l, l, p.layout === l)).join("")),
      spread: `<label class="hint" style="display:block">${COPY.ui.panel.spread}
        <input id="pref-spread" type="range" min="0.6" max="1.6" step="0.2" value="${p.spread}" style="width:100%">
      </label>`,
      docs: row(COPY.ui.panel.docLinks,
        chip("timelineDocs", "on", COPY.ui.panel.on, p.timelineDocs)
        + chip("timelineDocs", "off", COPY.ui.panel.off, !p.timelineDocs)),
      // D72 — 시간축. 관점이 아니라 그 관점 **안의 옵션**이다: 같은 것을 다른
      // 시계로 볼 뿐이라 관점 세그(3D·2D·타임라인·Sankey) 옆에 두지 않는다.
      basis: row(COPY.ui.panel.timeBasis,
        chip("timelineBasis", "recorded", COPY.ui.panel.basisRecorded,
             p.timelineBasis === "recorded")
        + chip("timelineBasis", "content", COPY.ui.panel.basisContent,
               p.timelineBasis === "content")),
    };

    // 범례는 **접힘 기본**. 매번 다 펼쳐 두면 패널이 화면 높이를 덮는다(실측 1102px).
    // 옵션도 **접는다**. 배치 프리셋 6종을 포함해 6개 컨트롤이 늘 펼쳐져 있어
    // 패널이 화면 높이를 덮었다(실측 761px). 옵션은 한 번 정하면 잘 안 바꾸는
    // 것들이라 접힘이 기본이고, 편 상태는 기억한다. 관점은 하단 세그먼트가 상시로
    // 맡으므로 접혀 있어도 자주 쓰는 전환은 막히지 않는다.
    const optKeys = rowsFor(lv, p);
    const optOpen = localStorage.getItem(OPTS_KEY) === "1";
    const lgOpen = localStorage.getItem(LEGEND_KEY) === "1";
    const sec = (label: string, open: boolean, key: string, body: string) =>
      `<button class="lg-sec-toggle" data-sec="${key}" aria-expanded="${open}">
         ${label}<span class="lg-caret">${open ? "▾" : "▸"}</span></button>`
      + (open ? body : "");
    viewPanel.innerHTML = head
      + (optKeys.length
        ? sec(COPY.ui.panel.options, optOpen, OPTS_KEY, optKeys.map((k) => rows[k]).join(""))
        : "")
      + sec(COPY.ui.legend.title, lgOpen, LEGEND_KEY, legendHTML())
      + `<p class="hint" style="margin:.6rem 0 0">${COPY.ui.panel.fixedNote}</p>`;
    viewPanel.querySelectorAll<HTMLElement>("[data-sec]").forEach((b) =>
      b.addEventListener("click", () => {
        const k = b.dataset.sec!;
        localStorage.setItem(k, localStorage.getItem(k) === "1" ? "0" : "1");
        drawViewPanel();
      }));
    viewPanel.querySelectorAll<HTMLElement>("[data-pref]").forEach((b) =>
      b.addEventListener("click", () => {
        const k = b.dataset.pref as keyof AtlasPrefs;
        const val = b.dataset.val!;
        const mut: Partial<AtlasPrefs> =
          k === "arrows" ? { arrows: val === "on" }
          : k === "timelineDocs" ? { timelineDocs: val === "on" }
          : ({ [k]: val } as Partial<AtlasPrefs>);
        applyPrefs(mut, `[data-pref="${k}"][data-val="${val}"]`);
      }));
    // 산개는 3D/2D 에만 있는 행이라 평면 관점에서는 존재하지 않는다(? 필수).
    viewPanel.querySelector<HTMLInputElement>("#pref-spread")?.addEventListener("change", (e) =>
      applyPrefs({ spread: Number((e.target as HTMLInputElement).value) }, "#pref-spread"));
    viewPanel.querySelectorAll<HTMLElement>("[data-view]").forEach((b) =>
      b.addEventListener("click", () => applyView(b.dataset.view as AtlasView)));
    viewPanel.querySelectorAll<HTMLElement>("[data-frame]").forEach((b) =>
      b.addEventListener("click", () => applyFrame(b.dataset.frame as "grid" | "matrix")));
    viewPanel.onkeydown = (e) => { if ((e as KeyboardEvent).key === "Escape") closePanel(); };
  }

  async function applyView(v: AtlasView) {
    const cur = trail[trail.length - 1];
    const shown = shownView(cur);
    hashView = null;                              // 사용자가 고른 순간 링크 오버라이드 해제
    savePrefs({ ...loadPrefs(), view: v });       // 저장값은 언제나 방금 고른 관점
    // 이미 그리고 있는 관점을 다시 누르면 화면을 건드리지 않는다(applyFrame·applyPrefs
    // 와 같은 계약 — 3D 카메라 프레이밍과 평면 뷰 스크롤이 살아남는다). Grid 는
    // 저장값이 평면이어도 미니가 3D 컷을 그리므로 "3D"가 이미 활성이다: 저장값만
    // 화면에 맞춰 두고(프레임을 벗어나면 그 값이 그대로 걸린다) 다시 그리지 않는다.
    if (shown === v) { syncHash(v); closePanel(); return; }
    // 지금 프레임이 그리지 않는 관점을 고르면 프레임을 떠난다 — 보이는 컨트롤은
    // 언제나 화면을 바꾼다. Compare/Matrix 는 어떤 관점도 그리지 않으므로 5종 전부가
    // 프레임을 떠나고, Grid 는 평면 3종만 떠난다(3D/2D 는 미니가 실제로 그린다).
    if (!rendersView(cur, v))
      while (trail.length > 1 && !isScope(trail[trail.length - 1])) trail.length -= 1;
    syncHash(v);                                  // 프레임을 떠난 뒤 = 도착지의 주소
    await show(trail[trail.length - 1], false);
    closePanel();                                 // 목적지 커밋 — 새 칩으로 포커스 복귀
  }

  async function applyFrame(kind: "grid" | "matrix") {
    const cur = trail[trail.length - 1];
    if (cur.kind === kind) return;
    if (!isScope(cur)) trail.length -= 1;         // 다른 프레임에서 갈아탈 때만 교체
    trail.push({ kind });
    await show(trail[trail.length - 1]);
    closePanel();
  }

  async function applyPrefs(mut: Partial<AtlasPrefs>, refocus?: string) {
    const cur = loadPrefs();
    // 이미 활성인 옵션을 다시 누르면 무동작이다(applyView·applyFrame 과 같은 계약).
    // 없으면 3D 는 카메라 프레이밍이, 평면 뷰는 스크롤 위치가 재마운트로 되감긴다.
    if ((Object.keys(mut) as (keyof AtlasPrefs)[]).every((k) => cur[k] === mut[k])) {
      if (refocus) viewPanel.querySelector<HTMLElement>(refocus)?.focus();
      return;
    }
    savePrefs({ ...cur, ...mut });
    const lv = trail[trail.length - 1];
    // 행을 내보낸 층 = 그 프리퍼런스를 실제로 읽는 층이다(Grid 포함). 보이는 컨트롤은
    // 언제나 화면을 바꾼다 — 좌표는 그대로 두고 프리퍼런스만 재적용한다.
    if (rowsFor(lv, curPrefs()).length > 0) await show(lv, false);
    openPanel();                                  // 패널 유지(연속 조정) + aria·포커스 재확정
    if (refocus) viewPanel.querySelector<HTMLElement>(refocus)?.focus();
  }

  /** 주소창을 "관점이 실제로 걸리는 곳 + 그 관점"으로 맞춘다. base 를 location.hash
   *  에서 뜨면 안 된다 — 인앱 이동은 해시를 갱신하지 않으므로, 다른 곳을 가리키는
   *  주소에 관점만 찍힌다(Compare 에서 Flow → #atlas/compare/a/b/as/flow 인데 화면은
   *  L0 Flow). 그래서 base 는 trail 에서 만든다. 프레임(compare/grid/matrix)은 관점을
   *  그리지 않으므로 관점이 걸리는 곳은 언제나 가장 가까운 스코프 층 = 프레임을
   *  닫으면 돌아갈 자리다(Grid·Matrix 에는 딥링크 문법이 아예 없다).
   *  replaceState 라 hashchange 가 뜨지 않는다(main.ts 라우터 무영향). */
  function syncHash(v: AtlasView = shownView(trail[trail.length - 1]) ?? curPrefs().view) {
    let i = trail.length - 1;
    while (i > 0 && !isScope(trail[i])) i -= 1;
    const lv = trail[i];
    const enc = encodeURIComponent;
    const base =
      lv.kind === "l1" ? `#atlas/${enc(lv.domain)}`
      : lv.kind === "l2" ? `#atlas/trace/${enc(lv.seed)}`
      : "#atlas";
    history.replaceState(null, "", v === "3d" ? base : `${base}/as/${v}`);
  }

  function toN3(nodes: GraphNode[], lensDomain?: string, focusId?: string): N3[] {
    return nodes.map((nd) => ({
      id: nd.id,
      label: nd.label === nd.id ? nd.id.replace(/^[a-z]+:/, "") : nd.label,
      domain: nd.domain, degree: nd.degree,
      kind: nd.kind || "concept",
      // 브릿지는 gateway 판정 면제(F2) — 단일 domain 이 없다(domain=source_domain 은
      // 보고 편의). 반투명+클릭 시 렌즈 이동은 브릿지에 무의미하다.
      gateway: lensDomain !== undefined && nd.domain !== lensDomain
        && (nd.kind || "concept") !== "bridge",
      focus: focusId !== undefined && nd.id === focusId,
      x: nd.x, y: nd.y, z: nd.z,
    }));
  }
  function toL3(edges: GraphEdge[]): L3[] {
    return edges.map((e) => ({ source: e.source, target: e.target, type: e.type, cross: e.cross_domain }));
  }
  /** D32 — Unlinked 필터(열린 층): 고아(연결 0)를 숨긴다. 초점(seed)은 항상 유지. */
  function filterUnlinked(ns: N3[]): N3[] {
    if (loadPrefs().unlinked !== "hide") return ns;
    return ns.filter((n) => (n.degree || 0) > 0 || n.focus);
  }

  /** 지도 마운트 공통 경로(3D) — 전체(L0)·주제(L1)·씨앗(L2)이 같은 코드를 쓴다.
   *
   *  **L0 이 바뀌었다(D69).** 주제 표(D58)도 그 이전의 주제 원환(D16)도 폐지한다.
   *  주제 목록은 이제 좌측 트리가 상시로 들고 있으므로 첫 화면이 다시 목록일 이유가
   *  없고, 첫 화면은 **전체 개념 그래프**다(발주자 지시 2026-07-31). 서버는 스코프
   *  없는 `/graph` 를 원래 지원한다 — 평면 관점의 L0 가 이미 그 경로를 쓰고 있었다
   *  (D46-S). 즉 이 변경으로 지도 관점과 평면 관점의 L0 모집단이 **같아진다**.
   *
   *  세 층은 스코프만 다르고 나머지(적재·지표·게이트웨이 판정·올가미)가 완전히 같아
   *  한 함수로 접는다. 갈라 두면 셋 중 하나만 고치는 드리프트가 난다. */
  async function showGraphScope(scope: { domain?: string; seed?: string }) {
    const { domain, seed } = scope;
    const whole = seed === undefined && !domain;
    const p = curPrefs();
    setMsg(`<p class="hint">${COPY.atlas.loading(whole ? 0 : 1)}</p>`);
    const res = seed !== undefined
      ? await api.graph(cfg.owner, { seed, depth: 2, layout: p.layout })
      : await api.graph(cfg.owner, { ...(domain ? { domain } : {}), layout: p.layout });
    if (!res.executed) { setMsg(`<div class="error-banner">${COPY.atlas.error}</div>`); return; }
    const nodes: GraphNode[] = res.nodes || [];
    if (seed !== undefined) fillSeedDomain(seed, nodes);
    if (nodes.length === 0) {
      // 전체가 비었으면 가져오기까지 붙인다 — 빈 첫 화면이 막다른 골목이면 안 된다.
      setMsg(whole
        ? `<div class="empty"><p>${COPY.atlas.idle}</p>
             <button class="btn btn-primary" data-import>${COPY.atlas.idleCta}</button></div>`
        : `<div class="empty"><p>${COPY.atlas.idle}</p></div>`);
      return;
    }
    setMsg("");
    curEdges = res.edges || [];
    curNodes = nodes;
    drawMetrics(nodes);
    g3 = mount3d(canvas, filterUnlinked(toN3(nodes, domain, seed)), toL3(curEdges), {
      // 게이트웨이(렌즈 밖 노드)는 그 주제로 건너뛴다 — 주제 렌즈에서만 성립하고,
      // 전체 화면에는 렌즈가 없으므로 toN3 가 애초에 표식을 달지 않는다.
      onClick: (nd) => {
        if (nd.gateway) { trail.push({ kind: "l1", domain: nd.domain! }); show(trail[trail.length - 1]); }
        else openDetail(nd);
      },
      onRightClick: (nd, ev) => nodeMenu(nd, ev),
    }, p);
    wireLasso();
  }

  /** 노드 우클릭 = 컨텍스트 메뉴(D64). 정본 §2 의 항목 그대로 — 열기 · 여기서 추적 ·
   *  여기서 공유 · 삭제. 여태 우클릭이 곧바로 추적을 실행해 메뉴를 기대한 손이
   *  예고 없이 화면을 갈아탔다(Jakob: 우클릭의 기대는 메뉴다). */
  function nodeMenu(nd: N3, ev: MouseEvent) {
    openContextMenu(ev.clientX, ev.clientY, [
      { label: COPY.ui.menu.open, run: () => openDetail(nd) },
      { label: COPY.ui.atlasx.traceCta, run: () => {
        trail.push({ kind: "l2", seed: nd.id, domain: nd.domain, title: nd.label });
        void show(trail[trail.length - 1]);
      } },
      { label: COPY.ui.menu.shareHere, run: () => openShareSheet({ seed: nd.id, depth: 2 }) },
      { label: COPY.ui.atlasx.del, danger: true, run: async () => {
        if (!await confirmDestructive(COPY.ui.atlasx.delNodeAsk(nd.label),
              COPY.ui.atlasx.delConfirm, COPY.ui.atlasx.delCancel)) return;
        try {
          const res = await api.deleteNode(cfg.owner, nd.id);
          if (!res.executed) throw new Error(res.note || res.error || "delete failed");
          closeRail();
          void show(trail[trail.length - 1], false);
        } catch { /* 실패는 조용히 — 지워진 것이 없다 */ }
      } },
    ]);
  }

  /** 지금 마운트된 렌더러에서 그 노드로 카메라를 옮긴다. 못 찾으면 false —
   *  호출측이 그때만 스코프를 갈아 다시 마운트한다(재조회는 비싸다). */
  function flyToMounted(id: string, domain?: string): boolean {
    if (g3) {
      const n = findNode3d(g3, id);
      if (!n) return false;
      flyTo3d(g3, n, curPrefs());
      return true;
    }
    if (g2) return g2.focus(id);
    if (planar) {
      if (planar.focus?.(id)) return true;
      // 합계는 개념을 그리지 않는다(가운데 열이 주제다) — 그 개념의 **주제**를
      // 대신 짚는다. 알갱이가 다를 뿐 거짓은 아니고, 개념 자체는 옆 카드가 든다.
      const dom = domain || curNodes.find((n) => n.id === id)?.domain;
      return dom ? (planar.focus?.(dom) ?? false) : false;
    }
    return false;
  }

  /** 노드의 화면 좌표(카드를 그 옆에 띄우기 위한 것). 렌더러마다 좌표계가 달라
   *  구할 수 없으면 캔버스 우상단 근처로 강등한다 — 카드가 안 뜨는 것보다 낫다. */
  function nodeScreenPos(id: string): { x: number; y: number } {
    const r = canvas.getBoundingClientRect();
    const n = g3 ? findNode3d(g3, id) : null;
    const p = n && g3?.graph2ScreenCoords
      ? g3.graph2ScreenCoords(n.x, n.y, n.z) : null;
    if (p && Number.isFinite(p.x) && Number.isFinite(p.y))
      return { x: r.left + p.x + 28, y: r.top + p.y - 20 };
    return { x: r.right - 360, y: r.top + 72 };
  }

  /** 노드로 이동(D70) — 좌측 트리·검색 착지·카드 안 항목이 전부 이 문 하나로 들어온다.
   *
   *  화면에 이미 있으면 카메라만 옮기고, 없으면 그 노드를 씨앗으로 스코프를 간다.
   *  끝에는 언제나 같은 둘이 열린다: 옆에 뜨는 관련 문서 카드 + 우측 문서 패널. */
  async function focusNode(id: string, domain?: string) {
    // 주제를 밖에서 알려 주면 먼저 기억한다 — 합계 뷰는 개념 마크가 없어 그래프
    // 노드로 되짚을 수 없고(가운데 열이 주제다), 트리는 그 값을 이미 안다.
    if (domain) lnbFocus = { domain, nodeId: id };
    if (!flyToMounted(id, domain)) {
      trail.push({ kind: "l2", seed: id, domain });
      await show(trail[trail.length - 1]);
      flyToMounted(id, domain);
    }
    const raw = curNodes.find((n) => n.id === id);
    // 노드 메타를 못 찾는 경우(평면 캡에 잘렸거나 스코프 밖) 최소 정보로 연다 —
    // 제목은 곧 node-context 가 채운다.
    const nd = raw ? toN3([raw])[0]
      : ({ id, label: id.replace(/^[a-z]+:/, ""), degree: 0 } as N3);
    openDetail(nd);
    lnbFocus = { domain: nd.domain, nodeId: id };
    setLnbActive(lnbFocus);
  }

  /** 관련 문서 카드 — 노드 옆에 뜨고, 끌어 옮길 수 있고, 항목을 누르면 그 노드로 간다. */
  async function openRelsCard(nd: N3) {
    const pos = nodeScreenPos(nd.id);
    const card = openFloatPanel({
      title: nd.label, x: pos.x, y: pos.y,
      closeLabel: COPY.ui.atlasx.railClose,
    });
    card.body.innerHTML = `<p class="hint">${COPY.ui.atlasx.sourceLoading}</p>`;
    try {
      const res = await api.nodeContext(cfg.owner, nd.id);
      if (!card.el.isConnected) return;          // 그 사이 닫혔다 — 늦은 응답 폐기
      card.setTitle(nd.label);
      if (!res.links?.executed) { card.body.innerHTML = `<p class="hint">${COPY.ui.atlasx.relsNone}</p>`; return; }
      card.body.innerHTML = relsHTML(res.links);
      wireRels(card.body, nd, res.links);
    } catch {
      if (card.el.isConnected) card.body.innerHTML = `<p class="hint">${COPY.ui.atlasx.relsNone}</p>`;
    }
  }

  function openDetail(nd: N3) {
    lastNode = nd;   // Reader 뒤로가기 복귀 지점
    openRail();
    void openRelsCard(nd);
    // 수치는 여기서 세지 않는다 — loadNodeSource 가 node-context 응답(links)으로
    // 채운다. 여태 이 자리에서 `curEdges`(마지막으로 그린 그래프의 잔상)를 세고
    // 드릴다운 경로에서는 degree 를 0 으로 박아, 같은 노드가 동선마다 다른 숫자를
    // 말하고 "연결 0개 · → 3 나감" 같은 자기모순까지 냈다.
    detail.innerHTML = `
      <div class="rail-head">
        <span class="hint">${COPY.ui.atlasx.railTitle}</span>
        <button class="rail-x" id="rail-close" aria-label="${COPY.ui.atlasx.railClose}"
                data-tip="${COPY.ui.atlasx.railClose}">✕</button>
      </div>
      <h2>${esc(nd.label)}</h2>
      <div id="node-stats"><p class="hint">${COPY.ui.atlasx.sourceLoading}</p></div>
      <p class="rail-acts">
        <button class="btn btn-primary" id="focus">${COPY.ui.atlasx.traceCta}</button>
        <button class="btn" id="nd-share">${COPY.ui.atlasx.shareCta}</button>
        <button class="btn btn-quiet" id="nd-del">${COPY.ui.atlasx.del}</button>
      </p>
      <div id="node-source"></div>
      <div id="ties-suggest"></div>`;
    detail.querySelector("#rail-close")!.addEventListener("click", closeRail);
    // 공유는 "스코프의 출구" — 이 노드의 ego 를 기본 범위로 시트를 연다(D56 상단 바
    // 버튼과 같은 표면). 레일에서도 나갈 수 있어야 진입이 제스처 하나에 갇히지 않는다.
    detail.querySelector("#nd-share")!.addEventListener("click", () =>
      openShareSheet({ seed: nd.id, depth: 2 }));
    detail.querySelector("#focus")!.addEventListener("click", () => {
      trail.push({ kind: "l2", seed: nd.id, domain: nd.domain }); show(trail[trail.length - 1]);
    });
    // S1 — 노드 삭제(되돌릴 수 없음). 성공하면 상세를 닫고 현재 층을 다시 그려
    // 지도에서도 사라지게 한다(응답 후 재조회 — 엣지까지 지워지므로 부분 갱신 불가).
    detail.querySelector("#nd-del")!.addEventListener("click", async () => {
      if (!await confirmDestructive(COPY.ui.atlasx.delNodeAsk(nd.label),
            COPY.ui.atlasx.delConfirm, COPY.ui.atlasx.delCancel)) return;
      try {
        const res = await api.deleteNode(cfg.owner, nd.id);
        if (!res.executed) throw new Error(res.note || res.error || "delete failed");
        detail.hidden = true;
        show(trail[trail.length - 1]);
      } catch {
        detail.querySelector("#nd-del")!.textContent = COPY.ui.atlasx.delFail;
      }
    });
    // D45-V — 수치·관계·정본 근거를 한 응답으로 채운다(잇기 제안 판정도 그 안에서).
    void loadNodeSource(nd);
  }

  async function openEdgeDetail(e: { source: string; target: string; type: string }) {
    // D45-V 1단계 — 흐름 선 클릭: "왜 A 가 B 로 이어지는가". 클릭=고정(pin) —
    // 상세 패널 자리를 그대로 쓴다(노드 상세와 같은 표면, ESC/다른 클릭으로 이동).
    openRail();
    detail.innerHTML = `<p class="hint">${COPY.ui.atlasx.relLoading}</p>`;
    try {
      const res = await api.edgeContext(cfg.owner, e.source, e.target, e.type);
      if (detail.hidden) return;   // 그 사이 다른 층으로 이동 — 늦은 응답 폐기
      if (!res.executed) {
        detail.innerHTML = `<p class="hint">${COPY.ui.atlasx.relFail}</p>`;
        return;
      }
      const a = res.src?.title || e.source, b = res.dst?.title || e.target;
      const sentence = (COPY.ui.atlasx.relSentence[e.type]
        || COPY.ui.atlasx.relSentence.RELATED_TO)(a, b);
      const conf = res.confidence && res.confidence > 0
        ? ` <span class="hint">· ${COPY.ui.atlasx.relConfidence(res.confidence)}</span>` : "";
      const side = (t: string, s?: { one_liner?: string;
                                     excerpt?: { excerpt: string } | null }) => {
        const body = s?.one_liner || s?.excerpt?.excerpt || "";
        return body ? `
          <div class="list-row" style="display:block">
            <span class="title" style="font-size:.85em">${esc(t)}</span>
            <p class="hint" style="margin:.15rem 0 0">${esc(body)}</p>
          </div>` : "";
      };
      detail.innerHTML = `
        <h2>${esc(a)} → ${esc(b)}</h2>
        <p>${esc(sentence)}${conf}</p>
        <p class="hint" style="margin-bottom:.3rem">${COPY.ui.atlasx.relEvidence}</p>
        ${res.evidence
          ? `<p>${esc(res.evidence)}</p>`
          : `<p class="hint">${COPY.ui.atlasx.relEvidenceNone}</p>`}
        ${side(a, res.src)}${side(b, res.dst)}`;
    } catch {
      if (!detail.hidden) detail.innerHTML = `<p class="hint">${COPY.ui.atlasx.relFail}</p>`;
    }
  }

  /** 합계 구간 클릭 → 개념 목록 드릴다운(D45-V2 3단계). "이 숫자는 어떤 개념들인가".
   *  개념 클릭 → 그 개념 상세(node-context)로 한 홉 더(문서는 거기서). */
  async function openDrilldown(domain: string, mark: string, seed?: string) {
    openRail();
    detail.innerHTML = `<p class="hint">${COPY.ui.atlasx.drillLoading}</p>`;
    try {
      const res = await api.ledgerDrilldown(cfg.owner, domain, mark, seed);
      if (detail.hidden) return;
      if (!res.executed) { detail.innerHTML = `<p class="hint">${COPY.ui.atlasx.drillFail}</p>`; return; }
      const items = res.items || [];
      const segName = (COPY.ui.atlasx.seg as Record<string, string>)[mark] || mark;
      const head = `<h2>${esc(COPY.ui.atlasx.drillHeader(domain, segName, res.total || 0))}</h2>`;
      if (items.length === 0) {
        detail.innerHTML = head + `<p class="hint">${COPY.ui.atlasx.drillEmpty}</p>`;
        return;
      }
      // V10 — 목록 안 필터. 40행짜리 제목의 벽에서 원하는 것을 눈으로 찾게 두지 않는다.
      const rowsHtml = (q: string) => items
        .filter((it) => !q || (it.title + " " + (it.one_liner || "")).toLowerCase().includes(q))
        .map((it) => `
        <div class="list-row drill-c" data-id="${esc(it.concept_id)}" data-lab="${esc(it.title)}"
             data-dom="${esc(domain)}" style="display:block;cursor:pointer">
          <span class="title" style="font-size:.9em">${esc(it.title)}</span>
          ${it.one_liner ? `<p class="hint" style="margin:.1rem 0 0">${esc(it.one_liner)}</p>` : ""}
        </div>`).join("");
      const wireRows = () => detail.querySelectorAll<HTMLElement>(".drill-c").forEach((row) =>
        row.addEventListener("click", () => openDetail({
          id: row.dataset.id!, label: row.dataset.lab!, domain: row.dataset.dom!,
          degree: 0,
        } as N3)));
      detail.innerHTML = head
        + (items.length > 8
          ? `<input id="drill-q" class="rail-filter" type="search" autocomplete="off"
               placeholder="${esc(COPY.ui.atlasx.drillFilter)}" />` : "")
        + `<div id="drill-list">${rowsHtml("")}</div>`;
      detail.querySelector<HTMLInputElement>("#drill-q")?.addEventListener("input", (e) => {
        const q = (e.target as HTMLInputElement).value.trim().toLowerCase();
        detail.querySelector<HTMLElement>("#drill-list")!.innerHTML = rowsHtml(q);
        wireRows();
      });
      wireRows();
      return;
    } catch {
      if (!detail.hidden) detail.innerHTML = `<p class="hint">${COPY.ui.atlasx.drillFail}</p>`;
    }
  }

  /** 타임라인 밴드 클릭 → 그 구간 항목(D45-V2 4단계). "그때 무슨 일이 있었나".
   *  브러시는 밴드 하나 = 한 버킷으로 이산화돼 있다(자유 픽셀 드래그 아님).
   *  ⚠ 구간과 **기준(basis)** 둘 다 차트가 쓴 것을 그대로 넘긴다(D72) — 하나라도
   *  어긋나면 밴드가 보여 준 수와 목록의 수가 달라져 화면이 자기를 반박한다. */
  async function openPeriod(bucket: string, domain?: string) {
    const p = curPrefs();
    const [start, end] = bucketRange(bucket, periodGrain);
    openRail();
    detail.innerHTML = `<p class="hint">${COPY.ui.atlasx.periodLoading}</p>`;
    try {
      const res = await api.docsInRange(cfg.owner, start, end, domain, p.timelineBasis);
      if (detail.hidden) return;
      if (!res.executed) { detail.innerHTML = `<p class="hint">${COPY.ui.atlasx.periodFail}</p>`; return; }
      const items = res.items || [];
      const head = `<h2>${esc(COPY.ui.atlasx.periodHeader(bucket, res.total || 0))}</h2>`;
      if (items.length === 0) {
        detail.innerHTML = head + `<p class="hint">${COPY.ui.atlasx.periodEmpty}</p>`;
        return;
      }
      detail.innerHTML = head + items.map((it) => `
        <div class="list-row period-i" data-id="${esc(it.id)}" data-lab="${esc(it.title)}"
             data-dom="${esc(it.domain)}" style="display:block;cursor:pointer">
          <span class="title" style="font-size:.9em">${esc(it.title)}
            <span class="hint">· ${esc(it.ts)}</span></span>
        </div>`).join("");
      detail.querySelectorAll<HTMLElement>(".period-i").forEach((row) =>
        row.addEventListener("click", () => openDetail({
          id: row.dataset.id!, label: row.dataset.lab!, domain: row.dataset.dom!, degree: 0,
        } as N3)));
    } catch {
      if (!detail.hidden) detail.innerHTML = `<p class="hint">${COPY.ui.atlasx.periodFail}</p>`;
    }
  }

  /** 상세 레일의 수치·관계 목록(T3·T4 · D45-V) — node-context 응답 하나가 정본이다.
   *  구 사이드카는 `links` 키 자체가 없다: 그때만 그래프 잔상(curEdges)으로 강등하고
   *  캡션도 정직하게 바꾼다(수치의 출처를 화면이 스스로 밝힌다). */
  function drawNodeStats(nd: N3, links?: NodeLinks) {
    const box = detail.querySelector<HTMLElement>("#node-stats");
    if (!box) return;
    let out: number, inn: number, att: number, degree: number, exact: boolean;
    if (links?.executed) {
      ({ out, in: inn, attached: att, degree } = links);
      exact = true;
    } else {
      // 강등 경로 — 여기 수치는 "지금 그린 그래프"의 것이라 스코프에 따라 달라진다.
      const DIRECTED = ["PREREQUISITE_OF", "DERIVES_FROM", "INHERITS_FROM"];
      const ATTACH = ["CONNECTS", "ABOUT", "ATTACHES_TO"];
      out = curEdges.filter((e) => e.source === nd.id && DIRECTED.includes(e.type)).length;
      inn = curEdges.filter((e) => e.target === nd.id && DIRECTED.includes(e.type)).length;
      att = curEdges.filter((e) =>
        (e.source === nd.id || e.target === nd.id) && ATTACH.includes(e.type)).length;
      degree = nd.degree ?? out + inn;
      exact = false;
    }
    const row = (k: string, v: string) =>
      `<div class="stat-row"><span class="hint">${k}</span><b>${v}</b></div>`;
    box.innerHTML =
      row(COPY.ui.atlasx.statTopic, esc(nd.domain || COPY.ui.common.none))
      + row(COPY.ui.atlasx.statLinks, String(degree))
      + (out + inn > 0 ? row(COPY.ui.atlasx.statDirection, COPY.ui.atlasx.statDir(out, inn)) : "")
      + (att > 0 ? row(COPY.ui.atlasx.statAttached, String(att)) : "")
      + (nd.gateway ? `<p class="hint">${COPY.ui.atlasx.gateway.replace(/^ · /, "")}</p>` : "")
      + `<p class="hint stat-note">${exact
        ? COPY.ui.atlasx.statsFrom : COPY.ui.atlasx.statsFromView}</p>`;
  }

  /** 관계 목록 — 방향(→/←/부착) · 상대 · 관계 타입 · 근거 유무.
   *
   *  **자리가 바뀌었다(D70).** 여태 우측 레일에 있었는데, 레일은 이제 문서 원문
   *  전용이고 관계는 노드 옆에 뜨는 이동 가능한 카드로 나간다. 줄을 누르면 그
   *  노드로 **이동**한다(카드는 제자리에서 갱신 — 리마운트 없음). 근거가 있는
   *  관계는 근거 줄이 별도 버튼이라 거기서 edge-context 로 들어간다: 목록의
   *  주된 일은 이동이고, 근거 읽기는 그 안의 곁가지다. */
  function relsHTML(links: NodeLinks): string {
    if (links.items.length === 0) return `<p class="hint">${COPY.ui.atlasx.relsNone}</p>`;
    const arrow = (d: string) => (d === "out" ? "→" : d === "in" ? "←" : "·");
    return links.items.map((it, i) => `
        <div class="list-row rel-row" data-i="${i}" style="display:block;cursor:pointer">
          <span class="title" style="font-size:.9em">
            <span class="rel-dir">${arrow(it.dir)}</span> ${esc(it.title)}
            <span class="hint rel-type">${esc(it.type.toLowerCase())}</span></span>
          ${it.evidence && it.dir !== "att"
            ? `<button class="hint rel-ev" data-ev="${i}">${COPY.ui.atlasx.relEvidence}</button>`
            : `<p class="hint" style="margin:.1rem 0 0">${COPY.ui.atlasx.relNoEvidence}</p>`}
        </div>`).join("")
      + (links.truncated ? `<p class="hint">${COPY.ui.atlasx.relsMore(
        links.degree + links.attached - links.items.length)}</p>` : "");
  }

  function wireRels(box: HTMLElement, nd: N3, links: NodeLinks) {
    box.querySelectorAll<HTMLElement>(".rel-row").forEach((r) =>
      r.addEventListener("click", (e) => {
        const it = links.items[Number(r.dataset.i)];
        const ev = (e.target as HTMLElement).closest<HTMLElement>(".rel-ev");
        if (ev) {
          e.stopPropagation();
          void openEdgeDetail(it.dir === "out"
            ? { source: nd.id, target: it.id, type: it.type }
            : { source: it.id, target: nd.id, type: it.type });
          return;
        }
        void focusNode(it.id);
      }));
  }

  async function loadNodeSource(nd: N3) {
    // 발췌는 증강 — 실패·부재가 상세 패널을 깨지 않는다(문구만 남기고 조용히).
    const box = detail.querySelector<HTMLElement>("#node-source");
    if (!box) return;
    box.innerHTML = `<p class="hint">${COPY.ui.atlasx.sourceLoading}</p>`;
    try {
      const res = await api.nodeContext(cfg.owner, nd.id);
      // 수치는 발췌보다 먼저 — 실패해도(구 사이드카) 강등본이 뜬다. 관계 목록은
      // 이 패널을 떠나 노드 옆 카드로 갔다(D70).
      drawNodeStats(nd, res.links);
      // D32 고아 최소화 — "확실히 연결 0"일 때만 잇기 후보를 낸다. 여태 드릴다운
      // 경로가 degree 0 을 박아 33연결 허브에도 제안이 떴다.
      if (res.links?.executed && res.links.degree + res.links.attached === 0)
        void suggestTiesInto(nd);
      if (!res.executed) { box.innerHTML = ""; return; }
      // 패널이 그 사이 다른 노드로 갱신됐으면 버린다(늦은 응답이 남의 상세를 덮지 않게).
      if (!detail.contains(box) || detail.hidden) return;
      const parts: string[] = [];
      if (res.one_liner) parts.push(`<p>${esc(res.one_liner)}</p>`);
      const ex = res.excerpts || [];
      if (ex.length > 0) {
        const header = res.arm === "fts"
          ? COPY.ui.atlasx.sourceRelated : COPY.ui.atlasx.sourceHeader;
        // 발췌 카드 = 클릭 시 Reader 전문 승격(peek-definition 동선). doc_id 를
        // data 로 실어 두고, 밑줄은 개념 제목 토큰을 발췌 문자열에 클라이언트 강조.
        parts.push(`<p class="hint" style="margin-bottom:.3rem">${header}</p>` +
          ex.map((e, i) => `
            <div class="list-row rdr-open" data-doc="${esc(e.doc_id)}" data-i="${i}"
                 style="display:block;cursor:pointer">
              <span class="title" style="font-size:.85em">${esc(e.doc_title)}${
                e.section ? `<span class="hint"> · ${esc(e.section)}</span>` : ""}</span>
              <p class="hint" style="margin:.15rem 0 0">${underlineTerms(e.excerpt, nd.label)}</p>
            </div>`).join(""));
      } else if (!res.one_liner) {
        parts.push(`<p class="hint">${COPY.ui.atlasx.sourceNone}</p>`);
      }
      box.innerHTML = parts.join("");
      box.querySelectorAll<HTMLElement>(".rdr-open").forEach((row) =>
        row.addEventListener("click", () =>
          void openReader(row.dataset.doc!, nd.id)));
    } catch {
      box.innerHTML = "";       // 발췌 불가(구 사이드카 등) — 상세는 그대로 유효
      drawNodeStats(nd);        // 수치는 강등본으로라도 채운다(빈 자리를 남기지 않는다)
    }
  }

  /** 발췌 문자열 안에서 개념 제목 토큰(길이≥2)을 밑줄 강조 — 색 없이(모노크롬),
   *  <u> 만. 정본 char offset 이 아니라 발췌 로컬 매칭이라 근사지만, Reader 전문은
   *  백엔드 계산 범위(정확)를 쓴다. */
  function underlineTerms(text: string, label: string): string {
    const terms = [...new Set((label.toLowerCase().match(/[0-9a-z가-힣]+/g) || [])
      .filter((t) => t.length >= 2))].slice(0, 6);
    if (terms.length === 0) return esc(text);
    // 토큰 span 을 훑어 매치만 <u> — 낱말 단위(문장 전체 강조 금지, D45-V2 규율).
    let out = "", last = 0;
    const re = /[0-9A-Za-z가-힣]+/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(text))) {
      if (terms.includes(m[0].toLowerCase())) {
        out += esc(text.slice(last, m.index)) + "<u>" + esc(m[0]) + "</u>";
        last = m.index + m[0].length;
      }
    }
    return out + esc(text.slice(last));
  }

  /** Reader 서브뷰(D45-V2 2단계) — 상세 패널을 정본 전문으로 확장한다. 새 표면
   *  신설 없이 Atlas 안에 머문다(D15). 뒤로 = 노드 상세로 복귀. 밑줄 범위는
   *  백엔드 /doc/highlights(토큰 매칭 계산, 정확한 char offset)로 받아 첫 범위로
   *  스크롤한다. verified 배지로 "정본" 신뢰를 시각화. */
  async function openReader(docId: string, conceptId: string) {
    openRail();
    detail.innerHTML = `<p class="hint">${COPY.ui.atlasx.readerLoading}</p>`;
    try {
      const [doc, hl] = await Promise.all([
        api.docGet(cfg.owner, docId),
        api.docHighlights(cfg.owner, docId, conceptId),
      ]);
      if (detail.hidden) return;
      if (!doc.executed || doc.text === undefined) {
        detail.innerHTML = `<p class="hint">${COPY.ui.atlasx.readerFail}</p>`;
        return;
      }
      // 문서가 자기 제목을 `# 제목` 으로 갖고 있으면 레일 제목은 생략한다 —
      // 같은 글자가 두 번 나오면 어느 쪽이 정본인지 헷갈린다.
      const firstLine = (doc.text.split("\n").find((l) => l.trim()) || "").trim();
      const dupTitle = !!doc.title
        && firstLine.replace(/^#{1,6}\s+/, "").trim().toLowerCase() === doc.title.trim().toLowerCase();
      const badge = doc.verified
        ? `<span class="badge">${COPY.ui.atlasx.readerVerified}</span>`
        : `<span class="hint">${COPY.ui.atlasx.readerUnverified}</span>`;
      detail.innerHTML = `
        <div class="rail-head">
          <span class="hint">${COPY.ui.atlasx.readerTitle}</span>
          <button class="rail-x" id="rail-close" aria-label="${COPY.ui.atlasx.railClose}"
                  data-tip="${COPY.ui.atlasx.railClose}">✕</button>
        </div>
        <p class="rail-acts">
          <button class="btn" id="rdr-back">${COPY.ui.atlasx.readerBack}</button>
          <button class="btn" id="rdr-edit">${COPY.ui.atlasx.edEdit}</button>
          <button class="btn" id="rdr-expand">${COPY.ui.atlasx.edExpand}</button>
        </p>
        ${dupTitle ? "" : `<h2 style="margin-top:.4rem">${esc(doc.title || "")}</h2>`}
        <p>${badge} <span class="hint" id="rdr-state"></span></p>
        <div id="rdr-host"></div>`;
      detail.querySelector("#rail-close")!.addEventListener("click", closeRail);
      detail.querySelector("#rdr-back")!.addEventListener("click", () => {
        void handle?.dispose();
        if (lastNode) openDetail(lastNode); else closeRail();
      });
      // D45-E 개정 — **Reader 와 편집기가 한 표면이다.** 문서는 언제나 렌더된
      // 모습으로 보이고(원문 마크다운 노출 없음), `편집`은 그 자리에 커서를 켤
      // 뿐이다. 여태 둘은 다른 화면이었고, 보기 화면은 `$$…$$`·`**…**` 같은
      // 소스를 그대로 노출했다(실측).
      const { mountDocEditor, openDocSheet } = await import("../doc-editor");
      const handle = await mountDocEditor(detail.querySelector<HTMLElement>("#rdr-host")!, {
        owner: cfg.owner, docId, initial: doc.text, title: doc.title,
        contentHash: doc.content_hash, readOnly: true, hideTitle: true,
        highlights: hl.ranges || [],
      });
      const editBtn = detail.querySelector<HTMLElement>("#rdr-edit")!;
      const state = detail.querySelector<HTMLElement>("#rdr-state")!;
      editBtn.addEventListener("click", () => {
        const on = !handle.isEditable();
        handle.setEditable(on);
        editBtn.textContent = on ? COPY.ui.atlasx.edDone : COPY.ui.atlasx.edEdit;
        state.textContent = on ? COPY.ui.atlasx.edLive : "";
      });
      // peek→전체폭 승격 — 좁은 레일이 답답할 때 같은 문서를 시트로 연다.
      detail.querySelector("#rdr-expand")!.addEventListener("click", () => {
        handle.dispose();
        closeRail();
        void openDocSheet({ owner: cfg.owner, docId });
      });
    } catch {
      if (!detail.hidden) detail.innerHTML = `<p class="hint">${COPY.ui.atlasx.readerFail}</p>`;
    }
  }

  async function suggestTiesInto(nd: N3) {
    const box = detail.querySelector<HTMLElement>("#ties-suggest");
    if (!box) return;
    box.innerHTML = `<p class="hint">${COPY.ui.atlasx.suggestLooking}</p>`;
    try {
      const res = await api.suggestTies(cfg.owner, nd.id);
      const sug: { id: string; title: string; domain: string; shared_tags: string[] }[] =
        res.suggestions || [];
      if (!res.executed || sug.length === 0) {
        box.innerHTML = `<p class="hint">${COPY.ui.atlasx.suggestNone}</p>`;
        return;
      }
      box.innerHTML = `<p class="hint" style="margin-bottom:.3rem">${COPY.ui.atlasx.suggestHeader}</p>` +
        sug.map((s, i) => `
          <div class="list-row">
            <span class="title" style="font-size:.9em">${s.title}
              ${s.shared_tags.length ? `<span class="hint">· ${s.shared_tags.join(", ")}</span>` : ""}</span>
            <span class="actions" style="visibility:visible"><button data-bind="${i}">${COPY.ui.atlasx.bind}</button></span>
          </div>`).join("");
      box.querySelectorAll<HTMLElement>("[data-bind]").forEach((b) =>
        b.addEventListener("click", async () => {
          const s = sug[Number(b.dataset.bind)];
          b.textContent = "…";
          try {
            // 양 노드 모두 기존 노드(matched) — title 만 동봉해 클로버 없이 MERGE
            await api.ingestFragment({
              nodes: [{ type: "concept", id: nd.id, title: nd.label },
                      { type: "concept", id: s.id, title: s.title }],
              edges: [{ type: "relates_to", from: nd.id, to: s.id }],
            });
            await show(trail[trail.length - 1], false);   // 재렌더(연결 반영)
            openRail();                        // show() 가 닫으므로 확인 문구 재표시
            detail.innerHTML = `<h2>${nd.label}</h2>
              <p>${COPY.ui.atlasx.bound(s.title)}</p>`;
          } catch {
            b.textContent = "Bind";
            box.insertAdjacentHTML("beforeend",
              `<div class="error-banner">${COPY.ui.atlasx.bindFail}</div>`);
          }
        }));
    } catch {
      box.innerHTML = `<p class="hint">${COPY.ui.atlasx.unavailable}</p>`;
    }
  }

  /** 2D 관점 — 독립 렌더러(D46). 3D 씬의 z-평면화가 아니라 사이드카 xy 좌표만
   *  쓰는 sigma 렌더러다. L0 는 3D 와 같은 주제 층(Domain Atlas, D16 불변)이되
   *  주제 노드를 **2D 그래프로** 짓는다 — 이 관점은 3D 렌더러에 더 이상 기대지
   *  않는다. (atlas3d 의 flat 분기는 Grid 미니 카드가 계속 쓴다.) */
  async function show2d(level: Level) {
    const p = curPrefs();
    // D69 — L0 도 그냥 전체 그래프다(구 주제 원 배치 폐지). 3D 경로와 같은 모집단을
    // 쓰므로 관점을 바꿔도 같은 것을 다르게 볼 뿐, 다른 것을 보게 되지 않는다.
    const { seed, domain } = scopeOf(level);
    const whole = seed === undefined && !domain;
    setMsg(`<p class="hint">${COPY.atlas.loading(whole ? 0 : 1)}</p>`);
    const res = seed !== undefined
      ? await api.graph(cfg.owner, { seed, depth: 2, layout: p.layout })
      : await api.graph(cfg.owner, { ...(domain ? { domain } : {}), layout: p.layout });
    if (!res.executed) { setMsg(`<div class="error-banner">${COPY.atlas.error}</div>`); return; }
    const gnodes: GraphNode[] = res.nodes || [];
    curEdges = res.edges || [];
    curNodes = gnodes;
    if (seed !== undefined) fillSeedDomain(seed, gnodes);
    if (gnodes.length === 0) {
      setMsg(whole
        ? `<div class="empty"><p>${COPY.atlas.idle}</p>
             <button class="btn btn-primary" data-import>${COPY.atlas.idleCta}</button></div>`
        : `<div class="empty"><p>${COPY.atlas.idle}</p></div>`);
      return;
    }
    setMsg("");
    drawMetrics(gnodes);
    g2 = mount2d(canvas, gnodes, curEdges, {
      seed,
      onClick: (nd) => openDetail(toN3([nd])[0]),
      onRightClick: (nd, ev) => nodeMenu(toN3([nd])[0], ev),
      // D53 — 흐름 폐기로 갈 곳을 잃은 관계 근거 패널(D45-V)의 새 진입점.
      onEdge: (e) => void openEdgeDetail(e),
    }, p);
  }

  const showL1 = (domain: string) => showGraphScope({ domain });
  const showL2 = (seed: string) => showGraphScope({ seed });

  /** Compare — 좌우 2D 렌즈(Sigma) + 중앙 개념 결합 레일 (D22). 비교 뷰는
   *  의도적으로 평면(나란히 대조가 목적 — 3D 회전은 좌우 대응을 깨뜨림). */
  function addShapedGraph(g: Graph, nodes: GraphNode[], edges: GraphEdge[]) {
    for (const nd of nodes) {
      const label = nd.label === nd.id ? nd.id.replace(/^[a-z]+:/, "") : nd.label;
      g.addNode(nd.id, { label, x: nd.x ?? 0, y: nd.y ?? 0,
        size: 4 + Math.sqrt(nd.degree || 1) * 2, color: THEME.ink, domain: nd.domain });
    }
    for (const e of edges) {
      if (g.hasNode(e.source) && g.hasNode(e.target) && !g.hasEdge(e.source, e.target))
        g.addEdge(e.source, e.target, { size: e.cross_domain ? 0.8 : 1.6,
          color: e.cross_domain ? THEME.inkMute : THEME.edge });
    }
  }

  async function showCompare(a: string, b: string) {
    setMsg("");
    canvas.innerHTML = `
      <div style="display:grid;grid-template-columns:1fr 22rem 1fr;height:100%">
        <div id="cmp-a"></div>
        <div id="cmp-rail" style="overflow:auto;border-left:1px solid ${THEME.edge};
             border-right:1px solid ${THEME.edge};padding:0 1rem">
          <h3 style="position:sticky;top:0;background:hsl(var(--background))">${a} ⇄ ${b}</h3>
          <div id="cmp-pairs" class="hint">${COPY.ui.atlasx.tiesLoading}</div>
        </div>
        <div id="cmp-b"></div>
      </div>`;
    const [ga, gb, cp] = await Promise.all([
      api.graph(cfg.owner, { domain: a }), api.graph(cfg.owner, { domain: b }),
      api.coupling(cfg.owner, a, b),
    ]);
    const mk = (elm: HTMLElement, res: any) => {
      const g = new Graph();
      addShapedGraph(g, res.nodes || [], res.edges || []);
      const s = new Sigma(g, elm, { allowInvalidContainer: true });
      extraSigmas.push(s);
      return { g, s };
    };
    const left = mk(canvas.querySelector("#cmp-a")!, ga);
    const right = mk(canvas.querySelector("#cmp-b")!, gb);
    const pairs: any[] = cp.pairs || [];
    const railEl = canvas.querySelector<HTMLElement>("#cmp-pairs")!;
    railEl.classList.remove("hint");
    railEl.innerHTML = pairs.length === 0
      ? `<p class="hint">${COPY.ui.atlasx.noTies}</p>`
      : pairs.map((p, i) => `
        <div class="list-row" data-i="${i}">
          <span class="title" style="font-size:.9em">${p.a_title}
            <span class="hint">${p.kind === "direct" ? "⇢" : "◇"}</span> ${p.b_title}</span>
          ${p.via_title ? `<span class="meta">${COPY.ui.atlasx.via(p.via_title)}</span>` : ""}
        </div>`).join("");
    const highlight = (p: any | null) => {
      for (const { s } of [left, right]) {
        s.setSetting("nodeReducer", (node, data) => {
          if (!p) return data;
          const hit = node === p.a_id || node === p.b_id;
          return hit ? { ...data, color: THEME.point, zIndex: 1, size: (data.size as number) * 1.4 }
                     : { ...data, color: THEME.edge, label: "" };
        });
        s.refresh();
      }
    };
    railEl.querySelectorAll(".list-row").forEach((row) => {
      row.addEventListener("mouseenter", () => highlight(pairs[Number((row as HTMLElement).dataset.i)]));
      row.addEventListener("mouseleave", () => highlight(null));
    });
  }


  /** Multiples 그리드(Phase C1) — 도메인당 평면 미니 렌즈(좌표 재사용), hover 시
   *  같은 id(크로스도메인 고스트)가 다른 카드에서 동시 점등(brushing&linking). */
  async function showGrid() {
    setMsg(`<p class="hint">${COPY.atlas.loading(0)}</p>`);
    const l0 = await api.atlasL0(cfg.owner);
    const domains: string[] = (l0.domains || []).slice(0, 9).map((d: any) => d.domain);
    if (domains.length === 0) { setMsg(`<div class="empty"><p>${COPY.atlas.idle}</p></div>`); return; }
    setMsg("");
    const lay = (d: string) => cardLayout.get(d) || loadPrefs().layout;
    canvas.innerHTML = `<div style="display:grid;grid-template-columns:repeat(${Math.min(domains.length, 3)},1fr);
      gap:.75rem;height:100%;padding:3rem 0 .5rem">${domains.map((d) => `
      <div style="display:flex;flex-direction:column;border:1px solid ${THEME.edge};border-radius:var(--radius);overflow:hidden">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <button class="chip" data-open="${d}" style="border:none;border-radius:0;text-align:left;padding:.35rem .75rem">${d}</button>
          <button class="chip" data-cycle="${d}" data-tip="Cycle layout (D34)"
                  style="border:none;border-radius:0;padding:.35rem .75rem">${lay(d)} ↻</button>
        </div>
        <div data-mini="${d}" style="flex:1;min-height:0"></div>
      </div>`).join("")}</div>`;
    // 카드별 레이아웃 사이클 — 프리셋만 바꿔 그리드 재조회(좌표는 사이드카 정본)
    canvas.querySelectorAll<HTMLElement>("[data-cycle]").forEach((b) =>
      b.addEventListener("click", () => {
        const d = b.dataset.cycle!;
        cardLayout.set(d, LAYOUTS[(LAYOUTS.indexOf(lay(d)) + 1) % LAYOUTS.length]);
        show(trail[trail.length - 1], false);
      }));
    // 미니 = 3D(단일 WebGL 렌더러 뷰포트 분할 — 컨텍스트 상한 회피, D34).
    // hover 시 같은 id(크로스도메인 고스트)가 전 카드에서 포인트색 동시 점등.
    const cells: { el: HTMLElement; nodes: N3[]; links: L3[] }[] = [];
    for (const d of domains) {
      const res = await api.graph(cfg.owner, { domain: d, layout: lay(d) });
      cells.push({
        el: canvas.querySelector(`[data-mini="${CSS.escape(d)}"]`) as HTMLElement,
        nodes: filterUnlinked(toN3(res.nodes || [])),
        links: toL3(res.edges || []),
      });
    }
    minis3d = mountMinis3d(canvas.firstElementChild as HTMLElement, cells, curPrefs());
    canvas.querySelectorAll("[data-open]").forEach((b) =>
      b.addEventListener("click", () => {
        trail.push({ kind: "l1", domain: (b as HTMLElement).dataset.open! }); show(trail[trail.length - 1]);
      }));
  }

  /** Coupling Matrix(Phase C2) — 도메인×도메인 히트맵(잉크 농도 3단), 셀 → Compare. */
  async function showMatrix() {
    setMsg(`<p class="hint">${COPY.atlas.loading(0)}</p>`);
    const l0 = await api.atlasL0(cfg.owner);
    const domains: string[] = (l0.domains || []).map((d: any) => d.domain);
    const ties = new Map<string, number>();
    for (const t of l0.ties || []) {
      ties.set(`${t.source}|${t.target}`, (ties.get(`${t.source}|${t.target}`) || 0) + t.ties);
      ties.set(`${t.target}|${t.source}`, (ties.get(`${t.target}|${t.source}`) || 0) + t.ties);
    }
    setMsg("");
    const max = Math.max(1, ...ties.values());
    const shade = (v: number) => v === 0 ? "transparent"
      : v < max / 3 ? "hsl(var(--muted))" : v < (2 * max) / 3 ? "hsl(var(--muted-foreground)/.45)" : "hsl(var(--foreground)/.8)";
    canvas.innerHTML = `<div style="padding:3.5rem 1rem 1rem;overflow:auto;height:100%">
      <table style="border-collapse:collapse">
        <tr><th></th>${domains.map((d) => `<th style="padding:.3rem .6rem;font-weight:400" class="hint">${d}</th>`).join("")}</tr>
        ${domains.map((a) => `<tr><th style="padding:.3rem .6rem;text-align:right;font-weight:400" class="hint">${a}</th>
          ${domains.map((b) => {
            const v = a === b ? 0 : (ties.get(`${a}|${b}`) || 0);
            return `<td data-a="${a}" data-b="${b}" data-tip="${v} ties" style="width:3.2rem;height:2.4rem;
              border:1px solid ${THEME.edge};background:${shade(v)};cursor:${v ? "pointer" : "default"};
              text-align:center;color:${v >= (2 * max) / 3 && v > 0 ? "hsl(var(--background))" : "inherit"}">${v || ""}</td>`;
          }).join("")}</tr>`).join("")}
      </table></div>`;
    canvas.querySelectorAll("td[data-a]").forEach((td) =>
      td.addEventListener("click", () => {
        const a = (td as HTMLElement).dataset.a!, b = (td as HTMLElement).dataset.b!;
        if (a !== b && (td as HTMLElement).title !== "0 ties") {
          trail.push({ kind: "compare", a, b }); show(trail[trail.length - 1]);
        }
      }));
  }

  /** D43 평면 관점(Flow · Timeline · Totals). 스코프 상태는 하나도 갖지 않고
   *  trail 의 마지막 층을 읽어 필요한 것만 가져온다. 데이터가 없는 스코프에서도
   *  관점은 선택된 채로 두고(비활성·숨김 없음) 캔버스에 두 절짜리 빈 상태를 그린다. */
  async function showPlanar(level: Level) {
    const p = curPrefs();
    const { seed, domain } = scopeOf(level);
    // 이미 그 주제만 보고 있으면 무동작 — L1 원장은 그 주제 한 줄뿐이라, 그 줄을
    // 누르면 같은 이름이 크럼에 한 칸씩 쌓였다(physics › physics › physics).
    const openTopic = (d: string) => {
      const lv = trail[trail.length - 1];
      if (lv.kind === "l1" && lv.domain === d) return;
      trail.push({ kind: "l1", domain: d }); show(trail[trail.length - 1]);
    };

    // Totals — 원장은 조회 전용 롤업이라 그래프 fetch 를 타지 않는다. 구버전
    // 사이드카(404)·네트워크 실패는 일반 에러 배너가 아니라 전용 문구로 내린다.
    if (p.view === "totals") {
      setMsg(`<p class="hint">${COPY.atlas.loading(0)}</p>`);
      let res: LedgerRes;
      let rows: LedgerRow[] = [];
      let paths: TotalsPath[] | undefined;   // D63 — 경로 보존 튜플(주제 알갱이 전용)
      try {
        // l2 는 ego 롤업(씨앗 주변 — L2 서브그래프와 같은 depth 2 순회)을 요청한다.
        // domain 은 **구버전 폴백 힌트**로 함께 싣는다: 구 사이드카는 seed 를 조용히
        // 무시하므로, domain 이 실려 있어야 전체 롤업 대신 주제 롤업으로 떨어진다.
        // D52 — 주제를 눌러 들어왔으면(L1) **개념 단위**로 묻는다. 도메인 단위로
        // 그대로 물으면 가운데 열이 그 주제 하나로 쪼그라들어 "더 깊이 안 들어가는"
        // 화면이 된다(발주자 지적). 구 사이드카는 `by` 를 조용히 무시하고 주제 롤업을
        // 돌려주므로, 응답의 `by` 에코로 판별해 라벨만 주제로 떨어뜨린다.
        const grain: "domain" | "concept" =
          seed === undefined && domain !== undefined ? "concept" : "domain";
        // D63 — 주제 알갱이 화면(L0·l2)도 **개념 행**으로 받는다: 경로 보존 리본은
        // (출처×주제×상태) 교차표가 필요한데 도메인 롤업은 주변합만 준다. 노드 행은
        // 클라이언트에서 접는다(foldLedgerPaths — 사이드카 롤업과 같은 규칙). 구
        // 사이드카는 by 를 조용히 무시하고 도메인 롤업을 돌려주므로(res.by 부재)
        // 접기·튜플 없이 종전 인접 교차로 자연 강등된다.
        res = await api.ledger(cfg.owner,
          seed !== undefined ? { seed, depth: 2, by: "concept", ...(domain ? { domain } : {}) }
          : domain ? { domain, by: grain } : { by: "concept" });
        // 딥링크(#atlas/trace/<id>/as/totals)만 주제를 모른 채 도착한다. 신 사이드카는
        // seed 만으로 ego 를 집계하고 scope 를 에코하므로 선조회가 필요 없다 — scope
        // 부재(=seed 가 무시된 구버전)인데 domain 힌트도 못 실었을 때만, 주제를 한 번
        // 알아내 주제 롤업으로 다시 묻는다(예전 상시 선조회를 스큐 경로로만 게이트).
        if (seed !== undefined && !domain && res.executed && res.scope === undefined) {
          try {
            const s = await api.graph(cfg.owner, { seed, depth: 1 });
            if (s.executed) fillSeedDomain(seed, s.nodes || []);
            const d = level.kind === "l2" ? level.domain : undefined;
            if (d) {
              const r2 = await api.ledger(cfg.owner, { seed, depth: 2, domain: d });
              if (r2.executed) res = r2;   // 재조회가 강등이면 전체 롤업이라도 그린다
            }
          } catch { /* 주제를 못 알아내면 전체 롤업으로 강등 — 화면은 그대로 그려진다 */ }
        }
        // executed=false 는 "비었다"가 아니라 "못 돌렸다"(스토어 도달 불가·강등)다.
        // 여기에 빈 상태 문구를 대면 백엔드가 죽었을 때 "파일을 추가하세요"라고
        // 거짓 조언을 하게 된다 — 404/네트워크 실패와 같은 문구로 내린다.
        if (!res.executed) { setMsg(`<div class="empty"><p>${COPY.ui.atlasx.totalsOff}</p></div>`); return; }
        rows = res.rows || [];
        // D63 — 주제 알갱이 화면인데 개념 행이 왔다(신 사이드카): 노드 행으로 접고
        // 경로 튜플을 뽑는다. 구 사이드카(by 무시 → res.by 부재)는 이 분기를 안 탄다.
        if (grain === "domain" && res.by === "concept") {
          const folded = foldLedgerPaths(rows);
          rows = folded.rows;
          paths = folded.paths;
        }
      } catch {
        setMsg(`<div class="empty"><p>${COPY.ui.atlasx.totalsOff}</p></div>`);
        return;
      }
      // ego 인구 0(씨앗 부재·부착 없는 씨앗)도 rows=[] 로 온다 — 기존 빈 상태 그대로.
      if (rows.length === 0) { setMsg(`<div class="empty"><p>${COPY.ui.atlasx.totalsEmpty}</p>
          <button class="btn btn-primary" data-import>${COPY.atlas.idleCta}</button></div>`); return; }
      setMsg("");
      // 원장 경로는 그래프 노드를 타지 않는다 — 지표는 롤업 행에서 센다(관점이
      // 달라도 하단 바가 비지 않게).
      drawMetrics(rows.map((r) => ({
        degree: (r.connected || 0) + (r.gateway || 0), ts: "",
      })));
      // D45-V2 3단계 — 구간 클릭 → 개념 드릴다운. l2(seed) 스코프면 원장과 같은
      // ego 모집단을 쓰도록 seed 를 넘긴다(수치 정합).
      const drillSeed = level.kind === "l2" ? level.seed : undefined;
      // 개념 알갱이면(D52) 가운데 열이 개념이다 — 누르면 주제가 아니라 **그 개념**으로
      // 들어가고, 리본을 눌러도 드릴다운(=1건 목록)이 아니라 같은 곳으로 간다.
      // D63 이후 L0·l2 도 by=concept 로 **받지만** 화면 알갱이는 주제다(위에서 접었다)
      // — 표시 판정은 요청 의도(grain)와 에코가 둘 다 개념일 때만 참이어야 한다.
      const byConcept = seed === undefined && domain !== undefined && res.by === "concept";
      const openConcept = (id: string) => {
        const row = rows.find((r) => r.domain === id);
        trail.push({ kind: "l2", seed: id, domain: row?.topic });
        show(trail[trail.length - 1]);
      };
      planar = mountTotals(canvas, rows, {
        grain: byConcept ? "concept" : "domain",
        paths,   // D63 — 경로 보존 리본(주제 알갱이 + 신 사이드카에서만 채워진다)
        onOpen: (id) => {
          if (!byConcept) { openTopic(id); return; }
          openConcept(id);
        },
        // 눌러도 갈 곳이 없는 행 = openTopic 이 무동작으로 삼키는 그 행. 판정 조건은
        // openTopic 의 가드와 글자 그대로 같아야 한다(L1 에서 자기 주제일 때만).
        // 개념 알갱이에서는 모든 행이 갈 곳이 있다.
        current: !byConcept && level.kind === "l1" ? level.domain : undefined,
        // T5 — 개념 알갱이에서 리본은 개념 하나를 가리킨다(값 1). 드릴다운(=1건
        // 목록)은 낭비이므로 곧장 그 개념을 연다. 주석만 그렇게 말하고 실제로는
        // 아무 일도 하지 않던 상태를 끝낸다(실측: 무동작).
        onSeg: byConcept ? (id) => openConcept(id)
          : (domain, mark) => void openDrilldown(domain, mark, drillSeed),
        // D70 — 지금 가리키는 주제는 `기타` 에 삼켜지지 않는다. 좌측 트리·검색으로
        // 짚어 온 주제가 화면에서 사라져 있으면 가리킨 일이 무의미해진다.
        pin: !byConcept ? (lnbFocus.domain || (level.kind === "l1" ? level.domain : undefined))
          : undefined,
      });
      // l2 스코프 캡션 — Timeline "날짜 없음" 알약과 같은 자리·같은 기제(mountTotals
      // 는 순수 렌더러로 남긴다). 크럼은 문서 하나를 가리키므로 차트가 무엇을 세는지
      // 밝힌다: scope="seed" 는 씨앗 주변 집계(신 사이드카), scope 부재는 seed 무시
      // = 주제 집계(구버전·entity 강등). 주제조차 몰랐으면(전체 롤업) 캡션도 없다.
      if (level.kind === "l2") {
        if (res.scope === "seed") {
          const n = res.population ?? rows.reduce((a, r) => a + (r.concepts || 0), 0);
          lassoHint.textContent = COPY.ui.atlasx.totalsAround(n);
          lassoHint.hidden = false;
        } else if (level.domain) {
          lassoHint.textContent = COPY.ui.atlasx.totalsTopic;
          lassoHint.hidden = false;
        }
      }
      return;
    }

    // L0/L1/L2 — 3D 와 똑같은 서브그래프를 받아 평면으로만 다시 그린다(별도 계약 없음).
    //
    // D46-S: **L0 는 전량**이다(정본 [[vinc.d.graph-views]] §3.2 "L0=전체", §2.3.1
    // 표 "L0 = 전체 개념"). 예전엔 여기서 `/atlas`(주제 집계)만 불러 흐름은 주제
    // ties 를, 타임라인은 "주제에는 날짜가 없다" 막다른 골목을 냈다 — 합계만
    // `/ledger?owner=` 로 이미 전량이라 관점끼리 모집단이 어긋나 있었다.
    // `domain`·`seed` 둘 다 없으면 사이드카는 owner 스코프 전량을 돌려준다
    // (query.py `graph()` "기본은 owner(±domain) 스코프 전체"). call() 이 undefined
    // 파라미터를 빼므로 요청은 그대로 `/graph?owner=…&layout=…` 이 된다.
    setMsg(`<p class="hint">${COPY.atlas.loading(level.kind === "l0" ? 0 : 1)}</p>`);
    const res = seed !== undefined
      ? await api.graph(cfg.owner, { seed, depth: 2, layout: p.layout })
      : await api.graph(cfg.owner, { domain, layout: p.layout });
    if (!res.executed) { setMsg(`<div class="error-banner">${COPY.atlas.error}</div>`); return; }
    const gnodes: GraphNode[] = res.nodes || [];
    curEdges = res.edges || [];
    curNodes = gnodes;
    if (seed !== undefined) fillSeedDomain(seed, gnodes);
    // 전량이 비었다 = 아직 아무것도 없다. 첫 화면이므로 다음 행동(팩)까지 준다.
    if (gnodes.length === 0) {
      setMsg(level.kind === "l0"
        ? `<div class="empty"><p>${COPY.atlas.idle}</p>
           <button class="btn btn-primary" data-import>${COPY.atlas.idleCta}</button></div>`
        : `<div class="empty"><p>${COPY.atlas.idle}</p></div>`);
      return;
    }
    drawMetrics(gnodes);
    // D72 — 시간축은 여기서 고른다. 서버가 `content` 를 요청받으면 ts 를 이미 갈아
    // 끼워 주지만, 두 열이 다 실려 오므로 **재조회 없이** 바꿔 끼울 수 있다. 구
    // 사이드카에는 content_ts 자체가 없어 내용 축이 빈 상태로 정직하게 강등된다.
    const onContent = p.timelineBasis === "content";
    const nodes = gnodes.map((nd) => ({
      id: nd.id,
      label: nd.label === nd.id ? nd.id.replace(/^[a-z]+:/, "") : nd.label,
      domain: nd.domain, ts: onContent ? (nd.content_ts || "") : nd.ts,
      kind: nd.kind || "concept",
    }));
    const openNode = (id: string) => {
      trail.push({ kind: "l2", seed: id, domain: gnodes.find((n) => n.id === id)?.domain });
      show(trail[trail.length - 1]);
    };
    // 씨앗이 혼자인 경우 — 점 하나는 그려서 "여기 있다"를 남기고 문구를 덧댄다.
    const solo = seed !== undefined && (gnodes.find((n) => n.id === seed)?.degree || 0) === 0;

    if (p.view === "timeline") {
      // 판정도 차트가 실제로 그리는 집합(문서 연결 토글 적용본) 위에서 한다 —
      // 걸러진 노드를 세면 "빈 캔버스에 문구 없음"이거나 undated 가 부풀어 오른다.
      const shown = timelinePool(nodes, p.timelineDocs);
      const dated = shown.filter((n) => !!(n.ts && n.ts.trim()));
      if (dated.length === 0) {
        // 내용 축이 비어 있는 것과 아예 날짜가 없는 것은 다른 사정이다 — 내용 시점은
        // 사람이 적어야 생기므로, 그 축에서는 무엇을 하면 채워지는지까지 말한다(D72).
        setMsg(`<div class="empty"><p>${onContent
          ? COPY.ui.panel.noContentTime : COPY.ui.atlasx.noDates}</p></div>`);
        return;
      }
      setMsg(solo ? `<div class="empty"><p>${COPY.ui.atlasx.soloNode}</p></div>` : "");
      // 상한 가드 — 평면은 노드마다 <g> 를 만드는 DOM 렌더라 전량(L0)에서 수천 개면
      // 무거워진다. 자르되 **조용히 자르지 않는다**: 최근 것부터 남기고 몇 개를
      // 안 그렸는지 캡션에 밝힌다(정직한 축소).
      let feed = nodes;
      const over = dated.length - PLANAR_CAP;
      if (over > 0) {
        const keep = new Set(dated.slice()
          .sort((a, b) => (a.ts! < b.ts! ? 1 : a.ts! > b.ts! ? -1 : a.id < b.id ? 1 : -1))
          .slice(0, PLANAR_CAP).map((n) => n.id));
        feed = nodes.filter((n) => keep.has(n.id));
      }
      // D45-V2 4단계 — 월-밴드 클릭 → 그 달 기간 항목. L1 은 도메인 렌즈, L2(ego)는
      // 스코프가 여러 도메인이라 렌즈 없이(그 달 전체) 연다.
      const periodDomain = level.kind === "l1" ? level.domain : undefined;
      // 밴드 클릭이 넘길 구간을 만들려면 차트와 **같은 알갱이**를 알아야 한다 —
      // 차트는 월, 목록은 연으로 세면 두 수가 어긋난다(D72).
      periodGrain = pickGrain(dated.map((n) => n.ts!));
      planar = mountTimeline(canvas, feed, {
        docs: p.timelineDocs, labels: p.labels !== "none", onOpen: openNode,
        onMonth: (m) => void openPeriod(m, periodDomain),
      });
      // 차트가 못 올린 것들을 한 줄로 밝힌다: 날짜 없는 문서 + 상한에 잘린 문서.
      const undated = shown.length - dated.length;
      const notes = [
        undated > 0 ? COPY.ui.atlasx.undated(undated) : "",
        over > 0 ? COPY.ui.atlasx.capped(over) : "",
      ].filter(Boolean);
      if (!solo && notes.length) {
        lassoHint.textContent = notes.join(" · ");
        lassoHint.hidden = false;
      }
      return;
    }

    // D53 — 평면 관점은 이제 timeline·totals 둘뿐이라 여기 도달하면 timeline 이
    // 이미 처리하고 돌아갔어야 한다. 방어적으로 빈 상태를 남긴다.
    setMsg(`<div class="empty"><p>${COPY.atlas.idle}</p></div>`);
  }

  /** 전환 피드백(Doherty) — 실측 관점 전환이 234~1018ms 다. 400ms 를 넘기는 동안
   *  화면은 **이전 뷰 그대로**였고 아무 표시도 없었다(작업이 진행 중인지 알 수 없다).
   *  400ms 타이머를 걸어 그 이후에만 표시를 켠다: 그보다 빠른 전환에 표시를 켜면
   *  깜빡임만 늘어난다(느껴지지 않는 지연에 스피너를 붙이면 더 느려 보인다). */
  let busyTimer: number | undefined;
  function setBusy(on: boolean) {
    window.clearTimeout(busyTimer);
    if (!on) { el.classList.remove("atlas-busy"); return; }
    busyTimer = window.setTimeout(() => el.classList.add("atlas-busy"), 400);
  }

  async function show(level: Level, push = true) {
    setBusy(true);
    if (push && trail[trail.length - 1] !== level) { /* pushed by caller */ }
    detail.hidden = true;
    closeFloatPanel();   // 층이 바뀌면 카드가 가리키던 노드도 화면에서 사라진다
    float.hidden = true;
    lassoHint.hidden = true;   // 기본 숨김 — L1/L2 의 wireLasso 가 다시 켠다
    closePanel();
    extraSigmas.forEach((s) => s.kill());
    extraSigmas = [];
    if (minis3d) { minis3d.dispose(); minis3d = null; }
    if (planar) { planar.dispose(); planar = null; }
    if (g2) { g2.dispose(); g2 = null; }
    if (g3) { g3._destructor?.(); g3 = null; }
    canvas.innerHTML = "";
    drawLegend();
    drawMetrics(null);
    // 위치 표시는 좌측 트리 담당(D71 — 크럼 폐지). 지금 층의 주제를 넘겨 준다:
    // 씨앗 층은 fillSeedDomain 이 주제를 알게 된 뒤 한 번 더 갱신한다.
    const here = level;
    setLnbActive(here.kind === "l1" ? { domain: here.domain }
      : here.kind === "l2" ? { domain: here.domain, nodeId: here.seed } : {});
    // U4 — 인앱 이동도 주소를 남긴다. 여태 관점 커밋에서만 동기화해, L2 까지 파고든
    // 뒤 새로고침하면 L1 로 튕기고 "지금 보는 곳"을 공유할 수도 없었다(화면은 L2 인데
    // 해시는 #atlas/physics). replaceState 라 hashchange 가 안 떠 라우터는 무영향이고,
    // 프레임(Grid·Matrix·Compare)은 syncHash 가 가장 가까운 스코프로 접어 준다.
    syncHash();
    // View 컨트롤은 모든 층에 — Compare/Grid/Matrix 에서도 관점으로 되돌아갈 길이 있어야 한다.
    viewControl();
    try {
      // 관점이 Level 보다 먼저다 — 평면 관점에서는 mount3d 가 아예 도달 불가능해진다.
      const v = curPrefs().view;
      if (level.kind === "compare") await showCompare(level.a, level.b);
      else if (level.kind === "grid") await showGrid();
      else if (level.kind === "matrix") await showMatrix();
      else if (isPlanar(v)) await showPlanar(level);
      else if (v === "2d") await show2d(level);
      // D69 — L0 도 이제 그냥 그래프다(스코프 없는 `/graph`). 주제 표는 좌측 트리로,
      // 주제 이름 변경도 그 트리의 우클릭으로 옮겼다.
      else if (level.kind === "l0") await showGraphScope({});
      else if (level.kind === "l1") await showL1(level.domain);
      else await showL2(level.seed);
      // 씨앗 층 착지(검색 결과·딥링크·`여기서 추적`)는 전체 맞춤으로 끝나 있었다 —
      // "그 노드로 갔다"고 해 놓고 화면은 ego 전체를 담아, 정작 씨앗이 어느 점인지
      // 알 수 없었다. 마운트가 자리를 잡은 뒤 씨앗으로 한 번 더 날린다(D70).
      if (level.kind === "l2") {
        const seed = level.seed;
        setTimeout(() => { if (myGen === liveGen) flyToMounted(seed); }, 420);
      }
    } catch {
      setMsg(`<div class="error-banner">${COPY.atlas.error}</div>`);
    } finally {
      setBusy(false);
    }
  }

  /** 1회 온보딩(Paradox of the Active User) — 사람들은 설명서를 읽지 않고 바로
   *  쓴다. 그래서 "어디에 무엇이 있는지"는 문서가 아니라 **화면이 한 번** 가리켜야
   *  한다. 렌즈(보는 방식)와 우클릭(더 많은 동작) 둘만 짚는다 — 첫 화면에서 알아야
   *  할 최소치이고, 그 이상은 소음이다. 한 번 닫으면 다시 뜨지 않는다. */
  const COACH_KEY = "vinc.atlas.coach.seen";
  function maybeCoach() {
    if (localStorage.getItem(COACH_KEY) === "1") return;
    const box = document.createElement("div");
    box.className = "coach";
    box.innerHTML = `
      <p class="coach-line"><b>${esc(COPY.ui.coach.lensTitle)}</b> ${esc(COPY.ui.coach.lensBody)}</p>
      <p class="coach-line"><b>${esc(COPY.ui.coach.menuTitle)}</b> ${esc(COPY.ui.coach.menuBody)}</p>
      <button class="btn btn-primary coach-ok">${esc(COPY.ui.coach.ok)}</button>`;
    el.appendChild(box);
    const done = () => { localStorage.setItem(COACH_KEY, "1"); box.remove(); };
    box.querySelector(".coach-ok")!.addEventListener("click", done);
    // 아무 곳이나 눌러도 닫힌다 — 코치마크가 길을 막지 않는다.
    setTimeout(() => document.addEventListener("click", done, { once: true }), 400);
  }

  trail.push({ kind: "l0" });
  if (deep === "compare" && parts[2] && parts[3]) trail.push({ kind: "compare", a: parts[2], b: parts[3] });
  else if (deep === "trace" && parts[2]) trail.push({ kind: "l2", seed: parts[2] });
  else if (deep && deep !== "as") trail.push({ kind: "l1", domain: deep });
  // D56 — 상단 바에 자기 동작을 건다. 공유 범위는 **지금 보고 있는 스코프**가
  // 기본값이다(정본 §5 "공유는 스코프의 출구"). 여태 진입이 올가미 제스처 하나뿐이라
  // 발견되지 않았고, 우클릭 메뉴는 아직 없다.
  setAtlasHooks({
    share: () => {
      const lv = trail[trail.length - 1];
      if (lv.kind === "l2") openShareSheet({ seed: lv.seed, depth: 2 });
      else if (lv.kind === "l1") openShareSheet({ domain: lv.domain });
      else openShareSheet({});                      // L0·프레임 = 전체
    },
    openNode: (id) => void focusNode(id),
  });
  // 좌측 트리의 문서 클릭이 여기로 온다(D69·D70). 훅이 없으면 트리는 해시 착지로
  // 강등되므로, Atlas 가 살아 있는 동안만 "리마운트 없는 이동"이 성립한다.
  setLnbNavigator((id, dom) => void focusNode(id, dom));

  await show(trail[trail.length - 1]);
  maybeCoach();
}
