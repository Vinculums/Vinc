import Graph from "graphology";
import Sigma from "sigma";
import { api, cfg, storeDownReason, type GraphEdge, type GraphNode,
         type NodeLinks } from "../api";
import { COPY } from "../copy";
import { camera3d, enableLasso, flyTo3d, findNode3d, mount3d,
         type Cam3, type L3, type N3 } from "./view3d";
import { mount2d, type Handle2d } from "./view2d";
import { bucketRange, mountColumns, pickGrain, timelinePool, type Grain,
         type PlanarHandle } from "./view-planar";
import { mountBundle } from "./view-bundle";
import { openShareSheet } from "../sheets";
import { tintMap } from "../view-tint";
import { openContextMenu, confirmDestructive } from "../components";

import { LAYOUTS, VIEWPOINTS, RAIL_MIN, RAIL_MAX, NODE_SCALE, EDGE_SCALE, CONE_SCALE,
         WHEN_SCALE, timeCutoff, nodeTimeOn, visibleAt,
         EDGE_KINDS, BUNDLE_DEPTHS, BUNDLE_RELS, edgeKindOf, edgeShowFor, edgeShowsFor, isPlanar,
         loadPrefs, savePrefs,
         type ViewPrefs, type Viewpoint, type BundleDepth,
         type EdgeKind } from "../view-prefs";
import { lensSlot, viewpointsSlot, setViewHooks, refreshTopbar } from "../topbar";
import { setLnbActive, setLnbNavigator, setLnbTopicPicker } from "../lnb";
import { openFloatPanel, closeFloatPanel, isFloatPanelOpen } from "../float-panel";
import { popIn, popOut } from "../motion";
import { wireOutsideDismiss } from "../components";
import { icon } from "../icons";

/** Surface 3 — View (spec: [[vinc.spec.viewpoints]] · as-built [[vinc.reference.view-controls]]).
 *
 *  Full-3D renderer (DEC D21 revised): L0 Domain View / L1 Domain Lens run on
 *  three.js (see view3d.ts — primitives = type, monochrome ink materials,
 *  paper fog).
 *
 *  Navigation: click = gateway→follow / node→detail · right-click = menu.
 *
 *  D43 — 두 축뿐이다: "어디"는 Level(trail)이 쥐고, "어떻게"는
 *  prefs.view(3D · 2D · 타임라인 · 번들)가 쥔다. 관점은
 *  trail 에 절대 들어가지 않으므로 둘은 구조적으로 어긋날 수 없다. */

// 2026-08-02 — **프레임 폐기(D34 개정)**. Grid(도메인별 미니 카드)·Matrix
// (도메인×도메인 히트맵)는 발주자 지시로 제거했고, Compare(A ⇄ B)는 **Matrix 셀
// 클릭이 유일한 진입점**이라 함께 내렸다. 되살리려면 새 진입점 설계가 선행된다.
//
// 2026-08-17 — **씨앗 층(L2) 폐기.** 스코프는 전량(L0)과 주제(L1) 둘뿐이다. 노드
// 하나로 좁히는 층은 재마운트 값이 비싼 데 비해 허브에서 걷어 내는 것이 적었고,
// 그 층이 답하던 질문은 짚기(recede)·관계 카드·`데려오기`가 제자리에서 답한다.
// 노드로 **가는** 일은 층이 아니라 focusNode 가 진다(스코프를 넓혀 데려온다).
/** L1 은 **주제 하나가 아니라 목록**이다(2026-08-15). 고른 주제 몇 개를 한 화면에
 *  놓는 것이 요구였고, 그건 강조가 아니라 **스코프**라 trail 이 쥔다 — 크럼에 뜨고
 *  주소에 남아 공유·딥링크가 같은 그림을 연다(정본 §1 "스코프의 주인은 trail").
 *  목록 하나짜리가 곧 예전의 L1 이므로 별도 층을 만들지 않았다. */
type Level = { kind: "l0" } | { kind: "l1"; domains: string[] };

/** 그 층이 걸고 있는 주제들. */
const lvDomains = (lv: Level): string[] => lv.kind === "l1" ? lv.domains : [];
/** 주제 **하나**만 받는 자리를 위한 대표값(공유 시트 · 드릴다운 · 기간 열기).
 *  여럿을 고른 상태에서 그 자리들이 무엇을 뜻해야 하는지는 아직 답이 없다 —
 *  첫 번째를 쓰고, 답이 생기면 여기 한 곳만 고치면 된다. */
const lvDomain = (lv: Level): string => lvDomains(lv)[0] || "";

/** 노드 하나를 팩으로 건넬 때의 기본 반경. 진입점이 셋(우클릭 메뉴 · 상세 레일 ·
 *  상단 바)이라 값이 흩어져 있으면 어디서 보냈느냐에 따라 받는 쪽 그림이 달라진다. */
const SHARE_DEPTH = 2;

/** 리더 머리에 세울 개념 칩 수. 나머지는 "+N" 으로 센다 — 좁은 열에서 칩이 줄을
 *  넘기기 시작하면 그 줄이 본문보다 커진다(관계 카드의 `relsMore` 와 같은 태도). */
const READER_CHIPS = 6;

// Signal 팔레트 — theme.ts(허브 생성물)에서 런타임 모드에 따라 읽는다(하드코딩 금지).
import { palette as signal, hexStr, type ThemeMode } from "../theme";
const curMode = (): ThemeMode =>
  document.documentElement.dataset.theme === "dark" ? "dark" : "light";
function curPal() {
  return signal[curMode()];
}
const THEME = {
  get ink() { return hexStr(curPal().text); },
  get inkMute() { return hexStr(curPal().textMute); },
  get edge() { return hexStr(curPal().line); },
  //: 초점색은 토큰 하나다(`semantic.*.focus`). 모드 분기를 손으로 쓰던 자리인데,
  //  같은 식이 view2d·view3d 에도 각각 있었고 타임라인은 `--ring` 을 써서 갈렸다.
  get point() { return hexStr(curPal().focus); },
};

/** 마운트 세대 — 라우터가 표면을 갈아끼워도 컨테이너는 남으므로, "내가 아직 살아
 *  있는가"는 이 카운터로만 판별한다(전역 리스너 누수 방지). */
let liveGen = 0;

export async function renderView(el: HTMLElement) {
  const myGen = ++liveGen;
  el.innerHTML = `
    <div id="view-canvas"></div>
    <!-- 하단 바에는 **지표만** 남는다(D71). 렌즈와 관점 세그는 상단 바로 올라갔고,
         지금은 그 바의 **오른쪽**에 있다(왼쪽은 설정·도구·규칙 = 앱을 다루는 일).
         하단 바의 남은 일은 "지금 화면이 무엇을 세고 있는가" 하나다. 지표는 여전히
         떠 있는 알약이 아니라 상태 표시줄이다(떠 있는 알약은 배너로 읽혀 무시된다
         — D65). -->
    <div id="view-chrome">
      <div id="view-metrics"></div>
    </div>
    <div id="view-detail" hidden></div>
    <!-- 폭 조절 그립은 패널의 **형제**다: 패널 내용은 화면마다 innerHTML 로 갈리는데
         (상세·관계·드릴다운·기간·리더) 그 안에 두면 매번 다시 붙이고 다시 배선해야 한다. -->
    <div id="rail-grip" role="separator" aria-orientation="vertical" hidden></div>
    <!-- D70 — 인캔버스 크롬의 위치를 인라인 style 에서 **CSS 로 되돌린다.** 여태
         인라인이었던 이유는 레일이 열릴 때 JS 가 같은 속성을 덮어써야 했기 때문인데
         (클래스 규칙은 인라인을 못 이긴다), 이제 레일 폭이 CSS 변수 하나(--rail-w)라
         비켜서는 일을 CSS 가 스스로 한다. 폭의 정의가 한 군데로 모인다. -->
    <div id="view-msg"></div>
    <div id="view-tools"></div>
    <div id="view-float" hidden></div>
    <div id="view-hint" hidden></div>
    <div id="view-lens-panel" hidden></div>
  `;
  const canvas = el.querySelector<HTMLElement>("#view-canvas")!;
  const detail = el.querySelector<HTMLElement>("#view-detail")!;
  const railGrip = el.querySelector<HTMLElement>("#rail-grip")!;
  // 상세와 폭 그립은 셸의 **가운데 열**로 옮겨 심는다. 만드는 곳은 여기다(내용의
  // 주인은 표면이므로 마크업도 표면이 갖는다) — 그러나 자리는 셸이 준다. 열은
  // 라우터가 표면을 갈아끼울 때 비운다(main.ts route). 열이 없는 배치(임베드)에서는
  // 옮기지 않고 표면 안에 그대로 남아 예전처럼 겹쳐 뜬다.
  const shell = document.getElementById("shell");
  const docpane = document.getElementById("docpane");
  // 머리는 셸 것이므로 본문에만 심는다. 그립은 열 전체 높이에 걸쳐야 하니(머리
  // 옆까지) 열 직속이다 — 둘의 소속이 다른 이유가 그것뿐이다.
  document.getElementById("docpane-body")?.append(detail);
  if (docpane) docpane.append(railGrip);
  const msg = el.querySelector<HTMLElement>("#view-msg")!;
  const tools = el.querySelector<HTMLElement>("#view-tools")!;
  // D71 — 렌즈·관점은 상단 바 슬롯에 그린다(내용의 주인은 여전히 이 표면이다).
  // 바가 없는 환경(임베드)에서는 표면 안에 자기 자리를 만들어 같은 코드로 그린다:
  // 그리는 함수는 한 벌이고 자리만 다르다.
  const chromeBar = el.querySelector<HTMLElement>("#view-chrome")!;
  const fallbackSlot = (id: string) => {
    const d = document.createElement("div");
    d.id = id;
    chromeBar.prepend(d);
    return d;
  };
  const legend = lensSlot() ?? fallbackSlot("view-legend");
  // ⚠ 이 폴백은 **상단 바가 없는 배치(임베드)** 를 위한 것이다. 상단 바는 있는데
  // 슬롯만 못 찾으면 그건 임베드가 아니라 **이름이 어긋난 것**이고, 폴백이 그것을
  // 조용히 삼켜 손잡이가 다른 자리로 옮겨 간다. 실제로 그렇게 됐다: F8 리네임이
  // topbar.ts 와 app.css 는 `tb-viewpoints` 로 고쳤는데 index.html 만 `tb-views` 로
  // 남아, 관점 세그가 상단 바에서 하단 크롬으로 내려앉았다(2026-08-16 규명).
  // 아무것도 오류를 내지 않아 화면만 보고는 결정으로 읽힌다 — 그래서 말하게 한다.
  if (lensSlot() && !viewpointsSlot()) {
    console.warn("[view] 상단 바는 있는데 #tb-viewpoints 슬롯이 없다 — "
      + "마크업과 조회 이름이 어긋났다. 관점 세그가 하단으로 떨어진다.");
  }
  const viewpointsBar = viewpointsSlot() ?? fallbackSlot("view-viewpoints");
  const metricsBar = el.querySelector<HTMLElement>("#view-metrics")!;
  const float = el.querySelector<HTMLElement>("#view-float")!;
  const lassoHint = el.querySelector<HTMLElement>("#view-hint")!;
  lassoHint.textContent = COPY.ui.viewx.lassoHint;
  const viewPanel = el.querySelector<HTMLElement>("#view-lens-panel")!;
  // 레일 폭 복원 + 닫힌 상태로 시작(크롬은 아직 비킬 것이 없다).
  setRailWidth(loadPrefs().railW);
  railChrome(false);
  wireRailGrip(railGrip);

  // lasso(영역 선택→공유) 배선 + 발견성 힌트. 주제(L1) 그래프에서만 활성.
  function wireLasso() {
    lassoHint.textContent = COPY.ui.viewx.lassoHint;   // 다른 용도로 쓰였을 수 있다
    lassoHint.hidden = false;   // "Shift+드래그로 영역 선택" 안내(발견성)
    enableLasso(g3, canvas, (ids) => {
      lassoHint.hidden = true;
      float.hidden = false;
      float.innerHTML = `<button class="btn btn-primary">${COPY.ui.viewx.shareSlice(ids.length)}</button>`;
      float.querySelector("button")!.addEventListener("click", () => {
        float.hidden = true; openShareSheet({ ids });
      });
      setTimeout(() => { float.hidden = true; lassoHint.hidden = false; }, 8000);
    });
  }

  let g3: any = null;                 // 3D 인스턴스 (L0/L1)
  /** 옵션 재마운트에 넘길 카메라(view3d.Cam3). show(…, {keepCamera}) 가 채우고
   *  showGraphScope 가 한 번 쓰고 비운다 — 좌표가 같은 재마운트에서만 뜻이 있다. */
  let keepCam: Cam3 | null = null;
  let g2: Handle2d | null = null;     // 2D 인스턴스 (D46 — 독립 렌더러)
  let planar: PlanarHandle | null = null;           // 평면 관점(타임라인 · 번들)
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
  const trail: Level[] = [];

  // deep links: #view/<domain> · #view/compare/<a>/<b> · #view/trace/<id> · …/as/<view>
  const parts = location.hash.split("/").map(decodeURIComponent);
  const deep = parts[1] || "";
  // /as/<view> 는 이 마운트에서만 저장값을 이긴다 — 절대 savePrefs 하지 않는다
  // (공유 링크가 받는 사람의 저장 관점을 영구히 덮어쓰지 않게).
  const asAt = parts.indexOf("as");
  let hashViewpoint: Viewpoint | null =
    asAt > 0 && VIEWPOINTS.includes(parts[asAt + 1] as Viewpoint) ? (parts[asAt + 1] as Viewpoint) : null;
  /** 저장된 프리퍼런스를 **지금 관점이 실제로 하는 것**으로 해석해 내려보낸다.
   *  해석을 여기 한 곳에 두는 이유: 패널의 활성 칩과 렌더러가 같은 값을 봐야 한다.
   *  저장값 자체는 건드리지 않는다 — 사용자가 고른 뜻은 그대로 남고, 관점을 옮기면
   *  그 관점의 표현으로 다시 해석될 뿐이다. */
  const curPrefs = (): ViewPrefs => {
    const p = hashViewpoint ? { ...loadPrefs(), view: hashViewpoint } : loadPrefs();
    return { ...p, edges: edgeShowFor(p.view, p.edges) };
  };

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
   *  여태 `#view-legend` 는 CSS 와 DOM 만 있고 **채우는 코드가 어디에도 없었다**.
   *
   *  형태는 정본(D35)을 따른다 — 개념=원, 기록물=육각, 다리=마름모, 주제=원+링.
   *  닫힌 층이라 시안이 바꿀 수 있는 층이 아니다(시안의 □·△ 미채택).
   *
   *  자리는 렌즈 패널 안이다: 지도를 보는 손잡이(관점·범위·옵션)와 지도를 읽는 법
   *  (범례)은 같은 물음("이 그림을 어떻게 볼까")에 답하므로 한 패널에 둔다. */
  // 옵션/범례 중 무엇을 보고 있나(2026-08-05). 옛 키 둘(legend.open·opts.open)은
  // 접힘 토글용이라 더 쓰지 않는다 — 남겨 두면 "어느 것이 지금 상태인가"가 세 곳에
  // 흩어진다(그 상태 분산이 옛 구조의 실제 문제였다).
  const PANEL_TAB_KEY = "vinc.view.panel.tab";
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
      <!-- ⚠ 육각이었다. 어느 렌더러도 육각을 그리지 않는다 — 3D 는 정사면체,
           평면은 그 정면인 삼각, 2D 는 굵은 링이다(2026-08-15 Node Kit).
           범례가 그리지 않는 형태를 가르치고 있었고, 그건 바로 위 주석이
           금지한 그것이다. markSvg 와 같은 구성으로 맞춘다: 꼭짓점 각
           -90·30·150, 외접원 1.08r(원과 시각 무게가 같은 비율). -->
      ${mark(`<polygon points="8,3.25 12.11,10.38 3.89,10.38"/>`, L.document)}
      ${mark(`<polygon points="8,1.6 14.4,8 8,14.4 1.6,8"/>`, L.bridge)}
      ${mark(`<circle cx="8" cy="8" r="3.6"/><ellipse cx="8" cy="8" rx="7.2" ry="2.6" fill="none" stroke="currentColor" stroke-width="1.1"/>`, L.topic)}
      <!-- 관문 = 자기 종의 윤곽선. 파선 비율은 markSvg 와 같다(.68r / .56r).
           Weave 는 관문 윤곽을 안 그리므로(view-bundle markSvg(gateway=false)) 거기선
           이 행을 빼는 것이 "그리지 않는 것은 가르치지 않는다"는 위 규칙이다. -->
      ${curPrefs().view === "bundle" ? "" :
        mark(`<circle cx="8" cy="8" r="4.4" fill="none" stroke="currentColor" stroke-width="1.2" stroke-dasharray="3 2.5"/>`, L.gateway)}
      <p class="hint lg-cap">${L.lines}${lineFilterable() ? `<span class="lg-tip">${esc(L.linesToggle)}</span>` : ""}</p>
      <!-- ⚠ 범례는 **실제로 그려지는 것만** 가르친다. 정본(§1.2)은 파선=크로스 ·
           점선=Bridge 매개 · 이중선=상속을 규정하지만, 2D(sigma)·3D(three) 어느
           렌더러도 아직 점선 계열을 그리지 않는다(D33 "후속" 명시). 그리지 않는
           문법을 범례가 가르치면 화면이 자기를 반박한다 — 지금 변별에 실제로
           쓰이는 채널(굵기·화살촉·농도)을 그대로 싣는다.
           D74 — 그 항목이 이제 **스위치**다(발주자 지시 2026-08-02). "지금 이 그림에
           무슨 선이 있는지"를 가르치던 자리가 "그중 무엇을 볼지"를 정하는 자리가
           된다. 지금 그래프에 **실제로 있는 갈래만** 개수와 함께 낸다 — 없는 갈래의
           스위치는 눌러도 아무 일이 없어 화면이 거짓말을 한다. -->
      ${lineRows()}
      <p class="hint lg-cap lg-focus"><span class="lg-dot"></span>${L.focus}</p>
    </div>`;
  }

  /** 선 갈래별 개수 — 지금 층에 실제로 그려질 엣지에서 센다. */
  function edgeKindCounts(): Map<EdgeKind, number> {
    const m = new Map<EdgeKind, number>();
    for (const e of curEdges) {
      const k = edgeKindOf(e);
      m.set(k, (m.get(k) || 0) + 1);
    }
    return m;
  }
  /** 선 필터가 뜻이 있는 관점인가 — 지도(3D·2D)만 엣지를 그린다. 타임라인·합계는
   *  선이 관계가 아니라 흐름·계보라 이 어휘가 안 맞는다(합계 리본은 값이다). */
  const lineFilterable = () => !isPlanar(curPrefs().view);
  function lineRows(): string {
    const L = COPY.ui.legend;
    const counts = edgeKindCounts();
    const off = new Set(curPrefs().edgeOff);
    const style: Record<EdgeKind, string> = {
      directed: `stroke="currentColor" stroke-width="1.8"`,
      related: `stroke="currentColor" stroke-width="1"`,
      cross: `stroke="currentColor" stroke-width=".8" opacity=".5"`,
      bridge: `stroke="currentColor" stroke-width=".8" opacity=".35"`,
    };
    const name: Record<EdgeKind, string> = {
      directed: L.directed, related: L.related, cross: L.cross, bridge: L.bridgeLine,
    };
    const rows = EDGE_KINDS.filter((k) => (counts.get(k) || 0) > 0);
    // 선이 하나도 없으면(고립 노드만 있는 층) 스위치도 내지 않는다 — 켜고 끌 것이 없다.
    if (!rows.length) return "";
    if (!lineFilterable()) {
      return rows.map((k) =>
        `<span class="lg-item"><svg class="lg-mark" viewBox="0 0 16 16" aria-hidden="true">`
        + `<path d="M1 8h14" ${style[k]}/></svg>${name[k]}</span>`).join("");
    }
    return rows.map((k) => {
      const on = !off.has(k);
      return `<button class="lg-item lg-sw${on ? "" : " off"}" type="button"
        data-edgekind="${k}" role="switch" aria-checked="${on}"
        ><svg class="lg-mark" viewBox="0 0 16 16" aria-hidden="true"
          ><path d="M1 8h14" ${style[k]}/></svg
        ><span class="lg-name">${name[k]}</span
        ><span class="lg-n pl-num">${counts.get(k)}</span></button>`;
    }).join("");
  }

  /** 렌즈 버튼 — 아이콘 하나. 누르면 위로 패널이 열리고 그 안에 `보기`(관점·범위·
   *  옵션)와 `범례`가 함께 있다. 상단 바에 있던 `보기` 버튼이 여기로 합쳐졌다:
   *  지도에 관한 손잡이는 지도 위에 두고, 바는 검색·공유에 자리를 내준다. */
  let lensBtn: HTMLElement | null = null;
  function drawLegend() {
    legend.innerHTML =
      `<button class="lens-btn" id="view-lens" aria-haspopup="dialog" aria-expanded="false"
               aria-controls="view-lens-panel" data-tip="${esc(COPY.ui.legend.lensTip)}"
               aria-label="${esc(COPY.ui.legend.lensTip)}">${icon("dot-grid", 17)}</button>`;
    lensBtn = legend.querySelector<HTMLElement>("#view-lens");
    lensBtn?.addEventListener("click", () => { if (viewPanel.hidden) openPanel(); else void closePanel(); });
  }

  /** 표면 공용 이스케이프 — 여태 함수마다 같은 것을 다시 선언하고 있었다(esc·esc2). */
  const esc = (s: string) => s.replace(/[&<>"]/g, (c) =>
    c === "&" ? "&amp;" : c === "<" ? "&lt;" : c === ">" ? "&gt;" : "&quot;");

  /** 레일 닫기 — 여태 상세 패널에는 닫는 수단이 **하나도 없었다**(버튼도 Esc 도).
   *  들어가는 문은 모든 마크에 달려 있는데 나오는 문이 크럼 하나뿐이었던 비대칭을
   *  여기서 끝낸다. 스택(상세→드릴다운→Reader→편집)은 각자 '뒤로'가 있고, ✕/Esc 는
   *  스택 전체를 접는다. */
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
    return w;
  }
  function railChrome(open: boolean) {
    // 열의 열림/닫힘은 셸이 안다(그리드 폭을 그 상태가 정한다). 열 자체를 hidden
    // 으로도 접는다 — 폭 0 인 열에 테두리만 남아 실선 하나가 떠 있는 것을 막는다.
    if (shell) shell.dataset.doc = open ? "open" : "closed";
    if (docpane) docpane.hidden = !open;
    railGrip.hidden = !open;
  }
  function closeRail() {
    detail.hidden = true;
    detail.innerHTML = "";
    lastNode = null;
    if (focusId) { focusId = ""; syncHash(); }   // 주소는 화면이 말하는 것만 말한다
    railChrome(false);
    // 카드는 선택에 딸린 물건이라 선택이 끝나면 같이 사라진다(떠 있는 창을 남기지 않는다).
    closeFloatPanel();
  }
  function openRail() {
    detail.hidden = false;
    railChrome(true);
  }
  /** 열의 **오른쪽** 모서리 그립 — 끌어서 폭 조절. 포인터 캡처라 커서가 캔버스
   *  (WebGL) 위로 넘어가도 드래그가 끊기지 않는다. 확정 값만 프리퍼런스에 적는다
   *  (끄는 동안 매 프레임 localStorage 를 두드리지 않게).
   *  방향이 뒤집혔다: 겹쳐 뜨던 시절 그립은 패널 왼쪽에 있어 **왼쪽으로** 끌면
   *  넓어졌다. 지금은 오른쪽 모서리라 오른쪽으로 끌면 문서가 넓어진다(손이 미는
   *  방향과 자라는 방향이 같다). */
  function wireRailGrip(grip: HTMLElement) {
    grip.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      grip.setPointerCapture(e.pointerId);
      const root = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
      const startX = e.clientX;
      const startW = detail.getBoundingClientRect().width / root;
      let live = startW;
      const move = (ev: PointerEvent) => {
        live = setRailWidth(startW + (ev.clientX - startX) / root);
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
      if (!viewPanel.hidden) { void closePanel(); return; }
      if (!detail.hidden) { closeRail(); return; }
      if (trail.length > 1) { trail.pop(); void show(trail[trail.length - 1]); }
      return;
    }
    if (e.key === "f" || e.key === "F") {
      // 3D 는 전체 맞춤, 2D 는 sigma 카메라 리셋 — 둘 다 "지금 층을 화면에".
      (g3 as any)?.__wake?.(700);                // 유휴 정지 중이면 깨워야 트윈이 돈다(F6)
      (g3 as any)?.zoomToFit?.(400, 60);
      g2?.fit?.();
    }
  };
  document.addEventListener("keydown", onKey);
  // 표면이 갈아끼워지면 전역 리스너를 걷어낸다. **컨테이너 존재로는 판별할 수 없다** —
  // 라우터는 #surface 의 innerHTML 만 비우고 컨테이너는 그대로 두기 때문이다(구
  // MutationObserver 판정이 영원히 참이라 리스너가 쌓였다). 마운트 세대로 가른다.

  const viewpointName = (v: Viewpoint) =>
    v === "3d" ? "3D" : v === "2d" ? "2D"
    : v === "timeline" ? COPY.ui.viewx.timeline
    : COPY.ui.viewx.bundle;
  /** 화면에 실제로 걸린 관점. 칩 라벨·크럼 꼬리·패널의 활성 표시·재클릭 가드가
   *  모두 이 하나만 읽는다 — UI 가 렌더러에 없는 상태를 주장하지 않게 하는 단일 진실. */
  const shownViewpoint = (_lv: Level): Viewpoint | null => curPrefs().view;
  /** 평면 관점의 스코프는 오롯이 trail 에서 읽는다(관점은 스코프를 갖지 않는다). */
  const scopeOf = (lv: Level): { domains?: string[] } =>
    lv.kind === "l1" ? { domains: lv.domains } : {};

  /** D31 + D43 — 인캔버스 View 컨트롤. view 는 이제 관점 5지선다(맵 3D/2D + 평면 3종)라
   *  칩은 현재 관점 이름을 그대로 싣는다. Grid/Matrix 는 같은 패널의 두 번째 행으로
   *  옮겨 스위처를 하나로 만든다(툴 행에 느슨한 칩을 남기지 않는다).
   *  닫힌 층(형태=타입 · 포인트색=초점 · 모노크롬)은 여전히 노출하지 않는다. */
  /** 칩 라벨 — 프레임 폐기 후로는 언제나 관점 이름이다(모든 층이 관점을 그린다). */
  const viewpointLabel = () => viewpointName(curPrefs().view);
  /** D56 — `보기`·`공유` 는 상단 바 버튼이다. 표면은 자기 동작만 등록하고, 버튼의
   *  존재·라벨은 바가 관리한다(View 를 떠나면 자동으로 사라진다). */
  function viewpointControl() { refreshTopbar(); drawViewpoints(); }

  /** 관점 세그먼트 — **상시 노출**(D65). 여태 렌즈 패널 안에 있어, 가장 자주 쓰는
   *  전환을 하려면 매번 패널을 열어야 했다(칩 20개짜리 1102px 패널 안에서 4개를
   *  찾는 일). 관점은 4지선다라 바에 그대로 눕히는 것이 맞다 — 선택지가 보이면
   *  결정 시간이 짧아진다(Hick). 프레임·옵션·범례는 렌즈 패널에 남는다. */
  function drawViewpoints() {
    const shown = shownViewpoint(trail[trail.length - 1]);
    viewpointsBar.innerHTML = `<span class="seg">${VIEWPOINTS.map((v) =>
      `<button class="chip${v === shown ? " active" : ""}" data-view="${v}">${viewpointName(v)}</button>`)
      .join("")}</span>`;
    viewpointsBar.querySelectorAll<HTMLElement>("[data-view]").forEach((b) =>
      b.addEventListener("click", () => void applyViewpoint(b.dataset.view as Viewpoint)));
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
      cell(COPY.ui.viewx.mDocs, String(total))
      + cell(COPY.ui.viewx.mLinked, `${rate}%`)
      + cell(COPY.ui.viewx.mOrphan, String(orphan), orphan > 0)
      + (latest ? cell(COPY.ui.viewx.mLatest, latest.slice(0, 10)) : "");
    const openOrphans = () =>
      void openDrilldown(lvDomain(trail[trail.length - 1]), "orphan");
    metricsBar.querySelector<HTMLElement>("[data-orphans]")?.addEventListener("click", openOrphans);
  }

  // 패널 열림/닫힘·aria·포커스를 한 곳에서만 만진다(드리프트 방지).
  //
  // ⚠ **이미 열려 있으면 다시 "여는" 동작을 하지 않는다.** applyPrefs 가 옵션을 고를
  // 때마다 이 함수를 부르는데(연속 조정을 위해 패널을 유지하려고), 등장 모션과 바깥
  // 클릭 배선까지 다시 걸리면 패널이 매번 렌즈 아이콘으로 줄었다 다시 커진다 —
  // 사용자에게는 "고르면 모달이 닫힌다"로 보인다(발주자 지적 2026-08-05).
  /** 좁은 창(<900px)에서는 **전체폭 유리면으로 승격**한다(발주자 지시 2026-08-05).
   *  그 폭에서 19rem 짜리 팝오버는 지도를 거의 다 덮으면서도 자기 내용은 두 줄로
   *  접혀, 좁은 것을 좁게 쓰는 최악의 조합이 된다. 문서 패널과 같은 처분이다 —
   *  좁으면 한 번에 하나만 보여 준다.
   *
   *  좌표를 **JS 가 지우는 것**이 핵심이다: 넓은 창에서 인라인으로 얹은 top/left 는
   *  미디어쿼리(CSS)를 이기므로, 지우지 않으면 승격 규칙이 먹지 않는다. */
  const NARROW = "(max-width: 900px)";
  function positionPanel() {
    if (window.matchMedia(NARROW).matches) {
      viewPanel.style.top = "";
      viewPanel.style.left = "";
      return;
    }
    // 렌즈 버튼이 상단 바로 올라가면서(D71) 패널은 표면 밖의 버튼에 붙어야 한다 —
    // 표면 기준 absolute 로는 사이드바 폭·타이틀바·웹 GNB 만큼 어긋난다. 좌표를
    // 버튼 실측 rect 에서 뽑아 fixed 로 놓으면 세 스킨이 전부 자동으로 맞는다.
    const r = lensBtn?.getBoundingClientRect();
    if (!r) return;
    viewPanel.style.top = `${Math.round(r.bottom + 6)}px`;
    viewPanel.style.left = `${Math.round(Math.min(r.left, window.innerWidth - 300))}px`;
  }
  // 창 크기가 바뀌면 승격/복귀가 즉시 따라야 한다 — 열어 둔 채 창을 줄였을 때 인라인
  // 좌표가 남아 있으면 팝오버가 어정쩡하게 걸린다. 마운트 세대가 지나면 스스로 뗀다.
  {
    const onResize = () => {
      if (myGen !== liveGen) { window.removeEventListener("resize", onResize); return; }
      if (!viewPanel.hidden) positionPanel();
    };
    window.addEventListener("resize", onResize);
  }

  // 열려 있는 동안 이 함수가 하는 일은 **내용 갱신과 자리 재확정**뿐이다.
  function openPanel() {
    const wasOpen = !viewPanel.hidden;
    panelSeq += 1;                         // 진행 중인 닫힘을 무효화(위 세대 주석)
    viewPanel.hidden = false;
    positionPanel();
    lensBtn?.setAttribute("aria-expanded", "true");
    // (관점 발견 힌트와 그 seen 플래그는 2026-08-17 에 걷어냈다 — 세그먼트가 상단 바로
    //  올라간 뒤 힌트를 띄우는 코드가 없어 쓰는 곳 없이 지우는 곳만 남아 있었다.)
    drawViewPanel();
    // 활성 칩이 없는 프레임(Compare — 관점도 프레임도 활성이 아니다)에서도 포커스는
    // 반드시 패널 안으로 들어와야 한다: Escape 핸들러가 패널에 걸려 있어, 포커스가
    // 밖(상단 바 `보기` 버튼)에 남으면 열어 놓고 키보드로 닫을 길이 사라진다.
    (viewPanel.querySelector<HTMLElement>(".seg .chip.active")
      ?? viewPanel.querySelector<HTMLElement>(".seg .chip"))?.focus({ preventScroll: wasOpen });
    // 렌즈 버튼에서 자라난다 — 좌표를 얹고 내용을 그린 **뒤**에 불러야 기준점이
    // 실제 패널 상자를 기준으로 계산된다(먼저 부르면 빈 상자 기준이 된다).
    if (wasOpen) return;                   // 갱신만 — 아래 등장 배선은 첫 열기 전용
    popIn(viewPanel, lensBtn);
    // 바깥을 누르면 닫힌다(발주자 지시 2026-08-05). 딤이 없는 패널이라 스크림 대신
    // 문서 전체를 듣는다 — 렌즈 버튼은 스스로 토글하므로 예외로 둔다(안 그러면 닫고
    // 다시 여는 두 처리가 겹친다). 배선은 닫을 때 반드시 해제한다.
    unwirePanelDismiss?.();
    unwirePanelDismiss = wireOutsideDismiss(viewPanel, () => void closePanel(), lensBtn);
  }
  let unwirePanelDismiss: (() => void) | null = null;
  /** 열기/닫기 세대. 닫힘은 퇴장 모션(240ms)을 기다린 뒤에 `hidden` 을 거는데, 그
   *  사이에 다시 열리면 **늦게 도착한 닫힘이 열린 패널을 덮는다**(실측 버그: 옵션을
   *  고르면 패널이 사라졌다). 세대가 바뀌었으면 숨기지 않는다. */
  let panelSeq = 0;
  async function closePanel() {
    if (viewPanel.hidden) return;
    const seq = ++panelSeq;
    unwirePanelDismiss?.();
    unwirePanelDismiss = null;
    await popOut(viewPanel, lensBtn);      // 퇴장을 기다린 뒤 숨긴다
    if (seq !== panelSeq) return;          // 그 사이 다시 열렸다 — 이 닫힘은 무효다
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
   *  L0: **더는 특례가 아니다** — L1/L2 와 같은 `/graph` 를 같은 렌더러로 그리므로
   *    같은 행을 쓴다([[vinc.spec.viewpoints]]).
   *  L1/L2 + Timeline: 좌표를 쓰지 않는 평면 차트라 layout·spread·size 는 무의미
   *    (fetch 는 layout 을 실어 보내지만 mountFlow/mountTimeline 은 좌표를 안 읽는다).
   *  Totals + 출처별: 원장 구간이 episodic·record·unsourced 라 고아 구간이 없다 →
   *    unlinked 가 아무것도 바꾸지 못한다(주제별일 때만 orphan 구간을 걷어낸다). */
  /** 평면(SVG DOM) 상한 — 노드마다 <g> 를 만드는 렌더라 전량 스코프(L0)에서
   *  무한정 그릴 수 없다. 넘치면 최근 것부터 남기고 캡션이 잘린 수를 밝힌다. */
  const PLANAR_CAP = 1200;
  /** 옵션 묶음(2026-08-05 재구조화). 여덟 개가 한 줄로 늘어서 있어 무엇에 대한
   *  손잡이인지 알 수 없었다 — 발주자 지시대로 셋으로 나눈다:
   *    노드(무엇이 크게 · 배치 · 크기 배율) · 선(굵기 · 간격 · 방향) · 기타(라벨 · 연결 없음)
   *  `spread`(간격)는 노드 사이 거리이지만 지시의 갈래를 따른다 — 라벨이 `간격`이고,
   *  손이 찾는 자리는 결국 그 이름이 있는 곳이다. */
  type OptGroup = { label: string; keys: string[] };
  function groupsFor(p: ViewPrefs): OptGroup[] {
    const flat = rowsFor(p);
    if (!flat.length) return [];
    if (p.view !== "3d" && p.view !== "2d") {
      return [{ label: "", keys: flat }];   // 평면 관점은 행이 적어 나눌 것이 없다
    }
    const pick = (...ks: string[]) => ks.filter((k) => flat.includes(k));
    return [
      { label: COPY.ui.panel.groupNodes, keys: pick("size", "layout", "cone", "nodeScale") },
      { label: COPY.ui.panel.groupLines, keys: pick("edgeShow", "edgeWidth", "spread", "arrows") },
      { label: COPY.ui.panel.groupOther, keys: pick("labels", "unlinked", "domainTint") },
      // 시간은 **자기 묶음**을 갖는다 — 투영(3D·2D)과 직교하는 축이라 "그 밖"에
      // 섞어 두면 다른 손잡이들과 같은 급으로 읽힌다. 축(basis)과 커서(when)는
      // 한 쌍이다: 커서는 어느 시계를 자르는지 말하지 않으면 뜻이 없다.
      { label: COPY.ui.panel.groupTime, keys: pick("when", "basis") },
    ].filter((g) => g.keys.length > 0);
  }

  function rowsFor(p: ViewPrefs): string[] {
    switch (p.view) {
      // `선 보이기`(edgeShow)는 둘 다에 있지만 **고를 수 있는 값이 다르다**
      // (edgeShowsFor) — 3D 는 입자, 2D 는 짚을 때만. sigma 에 입자가 없어서다.
      // `가지 벌어짐`(cone)은 **3D 에만** 있다. 2D 는 같은 트리를 부채꼴로 눕히는데
      // 부채꼴은 무게 비례로 각도를 나눠 갖는 구조라 벌릴 여지가 없다 — 2D 에 내놓으면
      // 아무 일도 안 하는 손잡이가 된다(§1 의 규칙).
      case "3d":
        return ["size", "nodeScale", "labels", "arrows", "unlinked", "layout", "cone",
                "spread", "edgeWidth", "edgeShow", "domainTint", "when", "basis"];
      case "2d":
        return ["size", "nodeScale", "labels", "arrows", "unlinked", "layout", "spread",
                "edgeWidth", "edgeShow", "domainTint", "when", "basis"];
      // D49 — `깊이` 는 **씨앗이 있을 때만** 뜻이 있다("씨앗에서 몇 세대"). 씨앗 없는
      // 층(L0/L1)에서는 자를 기준점이 없어 렌더러가 상한을 걸지 않으므로, 아무 일도
      // 안 하는 컨트롤을 내놓지 않는다(보이는 손잡이는 반드시 무언가를 해야 한다).
      case "timeline": return ["basis", "docs", "lineage", "domainTint"];
      // 번들 — `단`은 선이 어느 계층에서 묶이는지다(잎은 안 줄어든다. **라벨은**
      // 둘레에 들어갈 만큼만 그린다 — 2026-08-16),
      // `선`은 스키마 타입별 켜고 끄기. 좌표를 안 쓰므로 layout·spread·size 는 무의미하다.
      case "bundle": return ["bundleDepth", "bundleRels", "labels", "domainTint"];
    }
  }

  /** 슬라이더 한 벌(2026-08-05 재설계). 여태 세 개가 각자 인라인 스타일로 그려진
   *  **UA 기본 range** 였다: 트랙이 실선 한 줄, 손잡이가 작고, 무엇보다 **값이 어디에도
   *  안 보였다**. 끌어도 숫자가 안 변하니 "클릭만 먹는다"로 읽힌다(발주자 지적).
   *  이제 값을 옆에 적고(`output`), 끄는 동안 그 숫자가 따라 움직인다. */
  const fmtVal = (v: number | string) => String(Math.round(Number(v) * 100) / 100);
  function slider(id: string, label: string, min: number, max: number,
                  step: number, value: number, text?: string): string {
    // `text` 를 준 손잡이는 값 표시가 숫자가 아니다(시간 커서 — 0.62 는 뜻이 없고
    // 사람이 읽을 것은 잘린 날짜다). 끄는 동안의 갱신도 같은 규칙을 따라야 해서
    // `data-fmt` 로 표시해 둔다(아래 input.slider 배선).
    return `<div class="slider-row">
      <label class="hint" for="${id}">${esc(label)}</label>
      <output class="slider-val" id="${id}-val">${esc(text ?? fmtVal(value))}</output>
      <input class="slider" id="${id}" type="range"${text != null ? ' data-fmt="when"' : ""}
             min="${min}" max="${max}" step="${step}" value="${value}">
    </div>`;
  }

  /** 커서 위치를 사람이 읽는 시각으로. 끔이면 "전부". 지금 실린 노드들 위에서
   *  세므로 스코프가 바뀌면 같은 위치라도 다른 날짜가 된다 — 그것이 분위수의 뜻이다. */
  function whenLabel(when: number): string {
    if (when >= 1) return COPY.ui.panel.whenAll;
    const p = curPrefs();
    const cut = timeCutoff(curNodes.map((n) => nodeTimeOn(n, p.timeBasis)), when);
    // 기록 시각은 ISO 밀리초라 그대로 내면 길다 — 날짜까지만 자른다. 내용 시점은
    // 이미 `YYYY[-MM[-DD]]` 라 자를 것이 없다.
    return cut ? cut.slice(0, 10) : COPY.ui.panel.whenAll;
  }

  function drawViewPanel() {
    const p = curPrefs();
    const lv = trail[trail.length - 1];
    const chip = (pref: string, val: string, label: string, active: boolean) =>
      `<button class="chip${active ? " active" : ""}" data-pref="${pref}" data-val="${val}">${label}</button>`;
    const row = (cap: string, chips: string) =>
      `<p class="hint">${cap}</p><p><span class="seg">${chips}</span></p>`;
    // 활성 표시는 "지금 그려지는 것"만 따른다(shownViewpoint).
    const shown = shownViewpoint(lv);
    // 프레임 칩(Grid·Matrix)이 있던 자리. 프레임 폐기(2026-08-02)로 비운다 —
    // 관점은 하단 바 세그먼트가 상시로 맡는다(D65).
    const head = "";

    const rows: Record<string, string> = {
      size: row(COPY.ui.panel.nodeSize,
        chip("size", "links", COPY.ui.panel.byConnections, p.size === "links")
        + chip("size", "uniform", COPY.ui.panel.uniform, p.size === "uniform")),
      // ⚠ 같은 손잡이가 관점마다 다른 것을 뜻한다. 지도에서는 짚어야 뜨지만
      // Weave 에서는 둘레에 들어갈 만큼 상시로 뜬다 — 그래서 값 이름도 갈린다.
      // 한 이름으로 묶어 두면 화면이 하지 않는 약속("호버 시")을 하게 된다.
      labels: row(COPY.ui.panel.labels,
        chip("labels", "hover",
          p.view === "bundle" ? COPY.ui.panel.labelsFit : COPY.ui.panel.onHover,
          p.labels === "hover")
        + chip("labels", "none", COPY.ui.panel.off, p.labels === "none"))
        + (p.view === "bundle" ? `<p class="hint sub">${esc(COPY.ui.panel.labelsFitHelp)}</p>` : ""),
      arrows: row(COPY.ui.panel.arrows,
        chip("arrows", "on", COPY.ui.panel.on, p.arrows)
        + chip("arrows", "off", COPY.ui.panel.off, !p.arrows)),
      // 선을 **언제** 그리는가(2026-08-14). 굵기·방향 손잡이와 같은 묶음에 두되 맨
      // 위에 온다 — "보일까"가 "얼마나 굵을까"보다 먼저 답해야 하는 질문이다.
      edgeShow: row(COPY.ui.panel.edgeShow,
        edgeShowsFor(p.view).map((v) =>
          chip("edges", v, COPY.ui.panel.edgeShowName[v] || v, p.edges === v)).join(""))
        + `<p class="hint sub">${esc(COPY.ui.panel.edgeShowHelp[p.edges] || "")}</p>`,
      // 주제 틴트 — **열린 층**이라 여기 있다. 색은 원래 스펙 소유지만(팩 공유),
      // 이 색은 뜻을 나르지 않으므로 꺼도 아무 정보가 사라지지 않는다. 기본은 끔:
      // 모노크롬이 기본값이고 틴트는 켜서 얻는 것이다.
      domainTint: row(COPY.ui.panel.domainTint,
        chip("domainTint", "on", COPY.ui.panel.on, p.domainTint)
        + chip("domainTint", "off", COPY.ui.panel.off, !p.domainTint))
        + `<p class="hint sub">${esc(COPY.ui.panel.domainTintHelp)}</p>`,
      unlinked: row(COPY.ui.panel.unlinkedNodes,
        chip("unlinked", "show", COPY.ui.panel.show, p.unlinked === "show")
        + chip("unlinked", "hide", COPY.ui.panel.hide, p.unlinked === "hide")),
      // 고른 배치가 **무슨 순서로** 세우는지 한 줄로 밝힌다(발주자 지적 2026-08-02).
      // 툴팁이 아니라 상시 노출인 이유: 좌표의 뜻은 hover 로 발견할 것이 아니라
      // 지도를 읽는 전제다 — 모르면 배치는 장식으로 읽힌다.
      layout: row(COPY.ui.panel.layout,
        LAYOUTS.map((l) => chip("layout", l, COPY.ui.panel.layoutName[l], p.layout === l)).join(""))
        + `<p class="hint sub">${esc(COPY.ui.panel.layoutHelp[p.layout] || "")}</p>`,
      spread: slider("pref-spread", COPY.ui.panel.spread, 0.6, 1.6, 0.02, p.spread),
      // 시간 커서 — 값 표시는 분수가 아니라 **컷오프 자신**이다(0.62 는 아무 뜻도
      // 없다). 끔은 "전부"로 적는다.
      when: slider("pref-when", COPY.ui.panel.when,
                   WHEN_SCALE.min, WHEN_SCALE.max, WHEN_SCALE.step, p.when,
                   whenLabel(p.when))
        + `<p class="hint sub">${esc(COPY.ui.panel.whenHelp)}</p>`,
      cone: slider("pref-cone", COPY.ui.panel.cone,
        CONE_SCALE.min, CONE_SCALE.max, CONE_SCALE.step, p.cone)
        + `<p class="hint sub">${esc(COPY.ui.panel.coneHelp)}</p>`,
      // 크기·굵기 **배율**(발주자 지시 2026-08-02). 위 `size` 는 무엇이 크고 작은지를
      // 정하는 인코딩이고, 이 둘은 그 위에 곱하는 전역 배율이다 — 인코딩은 그대로 두고
      // 화면 밀도만 취향에 맞춘다. 엣지는 D33 대로 가중치를 농도로 계속 말한다.
      nodeScale: slider("pref-nodescale", COPY.ui.panel.nodeScale,
        NODE_SCALE.min, NODE_SCALE.max, NODE_SCALE.step, p.nodeScale),
      edgeWidth: slider("pref-edgescale", COPY.ui.panel.edgeWidth,
        EDGE_SCALE.min, EDGE_SCALE.max, EDGE_SCALE.step, p.edgeScale),
      docs: row(COPY.ui.panel.docLinks,
        chip("timelineDocs", "on", COPY.ui.panel.on, p.timelineDocs)
        + chip("timelineDocs", "off", COPY.ui.panel.off, !p.timelineDocs)),
      // D74 — 원인·결과 겹치기. 기본은 꺼짐: 선이 있으면 늘 도움이 되는 것이 아니라
      // 촘촘한 그래프에서는 마디를 덮는다. 켤 이유가 생겼을 때 켜는 층이다.
      lineage: row(COPY.ui.panel.lineage,
        chip("timelineLineage", "on", COPY.ui.panel.on, p.timelineLineage)
        + chip("timelineLineage", "off", COPY.ui.panel.off, !p.timelineLineage))
        + `<p class="hint sub">${esc(COPY.ui.panel.lineageHelp)}</p>`,
      // D72 — 시간축. 관점이 아니라 그 관점 **안의 옵션**이다: 같은 것을 다른
      // 시계로 볼 뿐이라 관점 세그(3D·2D·타임라인·Sankey) 옆에 두지 않는다.
      basis: row(COPY.ui.panel.timeBasis,
        chip("timeBasis", "recorded", COPY.ui.panel.basisRecorded,
             p.timeBasis === "recorded")
        + chip("timeBasis", "content", COPY.ui.panel.basisContent,
               p.timeBasis === "content")),
      // 번들 — 이 손잡이가 줄이는 것은 **선**이지 목록이 아니다. 도움말이 매 단마다
      // 그 사실을 되풀이하는 이유가 그것이다(줄었다고 항목이 사라진 줄 알면 오독이다).
      bundleDepth: row(COPY.ui.panel.bundleDepth,
        BUNDLE_DEPTHS.map((d) => chip("bundleDepth", String(d),
          COPY.ui.panel.bundleDepthName[String(d)] || String(d), p.bundleDepth === d)).join(""))
        + `<p class="hint sub">${esc(COPY.ui.panel.bundleDepthHelp[String(p.bundleDepth)] || "")}</p>`,
      // 지금 실린 데이터에 **있는 타입만** 스위치를 낸다(D74 규율 — 보이는 손잡이는
      // 반드시 무언가를 해야 한다). 범례가 곧 필터이므로 별도 범례를 두지 않는다.
      bundleRels: (() => {
        const present = new Set(curEdges.map((e) => e.type));
        const list = BUNDLE_RELS.filter((r) => present.has(r));
        if (!list.length) return "";
        return row(COPY.ui.panel.bundleRels, list.map((r) =>
          chip("bundleOff", r, COPY.ui.panel.relName[r] || r,
               !p.bundleOff.includes(r))).join(""));
      })(),
    };

    // 옵션 / 범례는 **둘 중 하나를 고른다**(발주자 지시 2026-08-05, 기본 = 옵션).
    // 여태는 접기 토글 둘(▸/▾)이었다. 그 구조의 문제:
    //   · 둘 다 접힌 상태가 기본이라 패널을 열면 **아무 내용도 없는 목록 두 줄**이었다
    //     (덮이는 것을 막으려 접었는데, 그래서 열자마자 할 일이 "또 펼치기"가 됐다)
    //   · 둘 다 펼치면 패널이 화면 높이를 덮었다(실측 1102px)
    //   · 접힘/펼침이 각자 기억돼 두 번 누르는 조합이 네 가지였다
    // 하나만 보이면 높이가 저절로 잡히고, 무엇을 보고 있는지가 세그로 드러난다.
    const optKeys = rowsFor(p);
    const hasOpts = optKeys.length > 0;
    const tab = hasOpts
      ? (localStorage.getItem(PANEL_TAB_KEY) === "legend" ? "legend" : "opts")
      : "legend";                       // 관점에 옵션이 없으면 범례만 남는다
    // 탭 형태는 **설정 모달과 같은 것**을 쓴다(발주자 지시 2026-08-05) — 같은 일
    // (여러 면 중 하나 고르기)을 하는 컨트롤이 화면마다 다른 모습이면 그때마다 다시
    // 배워야 한다. `.settings-tabs` 가 이미 그 규격이라 클래스를 그대로 가져온다.
    const tabSeg = hasOpts
      ? `<div class="settings-tabs lg-tabs" role="tablist">
           <button class="settings-tab${tab === "opts" ? " active" : ""}" role="tab"
             data-tab="opts" aria-selected="${tab === "opts"}">${esc(COPY.ui.panel.options)}</button>
           <button class="settings-tab${tab === "legend" ? " active" : ""}" role="tab"
             data-tab="legend" aria-selected="${tab === "legend"}">${esc(COPY.ui.legend.title)}</button>
         </div>`
      : "";
    // 묶음을 **영역**으로 낸다(발주자 지시 2026-08-05) — 제목 한 줄만으로는 어디까지가
    // 그 묶음인지 경계가 없어, 아래 컨트롤이 다음 제목에 붙어 읽혔다. 상자가 그 경계다.
    const groupsHTML = groupsFor(p).map((g) => g.label
      ? `<section class="opt-sec">
           <h3 class="opt-sec-title">${esc(g.label)}</h3>
           ${g.keys.map((k) => rows[k]).join("")}
         </section>`
      : g.keys.map((k) => rows[k]).join("")).join("");
    // 내용은 **스크롤하는 안쪽 면**에 넣는다 — 유리면 자신이 스크롤하면 프레임마다
    // 블러를 다시 계산해 스크롤이 끊긴다(app.css #view-lens-panel 주석).
    // 다시 그려도 **보던 자리**는 남긴다 — innerHTML 로 면이 새 노드가 되면 scrollTop
    // 이 0 으로 돌아가, 내려가서 누른 손잡이가 누르는 순간 화면 밖으로 사라졌다
    // (실측 2026-08-17: 400→0→448). 값을 읽어 두고 새 면에 되돌린다.
    const keepScroll = viewPanel.querySelector<HTMLElement>(".lens-scroll")?.scrollTop ?? 0;
    viewPanel.innerHTML = `<div class="lens-scroll">`
      + head + tabSeg
      + (tab === "opts" ? groupsHTML : legendHTML())
      + `<p class="hint hint-sm">${COPY.ui.panel.fixedNote}</p>`
      + `</div>`;
    if (keepScroll) viewPanel.querySelector<HTMLElement>(".lens-scroll")!.scrollTop = keepScroll;
    viewPanel.querySelectorAll<HTMLElement>("[data-tab]").forEach((b) =>
      b.addEventListener("click", () => {
        localStorage.setItem(PANEL_TAB_KEY, b.dataset.tab!);
        drawViewPanel();
      }));
    viewPanel.querySelectorAll<HTMLElement>("[data-pref]").forEach((b) =>
      b.addEventListener("click", () => {
        const k = b.dataset.pref as keyof ViewPrefs;
        const val = b.dataset.val!;
        // 번들의 관계 스위치는 **토글**이라(고르기가 아니라) 현재 목록을 읽어야 한다.
        const cur = curPrefs();
        const mut: Partial<ViewPrefs> =
          k === "arrows" ? { arrows: val === "on" }
          : k === "timelineDocs" ? { timelineDocs: val === "on" }
          : k === "timelineLineage" ? { timelineLineage: val === "on" }
          : k === "domainTint" ? { domainTint: val === "on" }
          : k === "bundleDepth" ? { bundleDepth: Number(val) as BundleDepth }
          : k === "bundleOff" ? { bundleOff: cur.bundleOff.includes(val)
              ? cur.bundleOff.filter((x) => x !== val) : [...cur.bundleOff, val] }
          : ({ [k]: val } as Partial<ViewPrefs>);
        applyPrefs(mut, `[data-pref="${k}"][data-val="${val}"]`);
      }));
    // 값 표시는 **끄는 동안**(input) 따라 움직인다 — 셋 다. 무엇이 적용되는 시점과는
    // 별개다: 아래 spread 는 놓을 때(change) 적용되지만, 숫자는 손끝을 따라야
    // "이 손잡이가 살아 있다"가 읽힌다(그 피드백이 없던 것이 "클릭만 먹는다"의 정체).
    viewPanel.querySelectorAll<HTMLInputElement>("input.slider").forEach((s) => {
      const out = viewPanel.querySelector<HTMLElement>(`#${s.id}-val`);
      if (out) s.addEventListener("input", () => {
        out.textContent = s.dataset.fmt === "when"
          ? whenLabel(Number(s.value)) : fmtVal(s.value);
      });
    });

    // 가지 벌어짐도 **놓을 때** 적용한다 — 좌표를 사이드카가 다시 만드는 값이라
    // 드래그마다 왕복하면 손끝보다 네트워크가 느려 값이 튄다(spread 와 같은 이유).
    viewPanel.querySelector<HTMLInputElement>("#pref-cone")?.addEventListener("change", (e) =>
      applyPrefs({ cone: Number((e.target as HTMLInputElement).value) }, "#pref-cone"));
    // 시간 커서도 **놓을 때** 적용한다. 서버는 안 부르지만(좌표는 그대로) 보이는
    // 노드 집합이 바뀌므로 제자리 반영이 안 되고 재마운트가 필요하다 — 드래그마다
    // 재마운트하면 손끝보다 화면이 느려진다. 숫자(=잘린 날짜)는 끄는 동안 따라
    // 움직이므로 손잡이가 죽어 보이지는 않는다.
    // ponytail: 놓을 때만 반영. 진짜 스크럽(끄는 내내 장면이 따라옴)은 렌더러가
    // 노드를 제자리에서 숨길 수 있어야 한다(3D 는 nodeVisibility, 2D 는 필터) —
    // 그 배선이 생기면 __update 화이트리스트에 `when` 을 넣고 input 으로 옮긴다.
    viewPanel.querySelector<HTMLInputElement>("#pref-when")?.addEventListener("change", (e) =>
      applyPrefs({ when: Number((e.target as HTMLInputElement).value) }, "#pref-when"));
    // 이 셋은 좌표를 안 바꾸므로 **재조회가 필요 없다** — input(드래그 중)에서
    // 바로 반영해 손끝에 붙게 한다.
    //
    // ⚠ `spread` 는 2026-08-16 까지 놓을 때(change)만 적용됐고, 그 옆 주석은 이유를
    // "레이아웃 인자라 사이드카 왕복"이라고 적어 두었다. **틀린 말이었다.** 소비처를
    // 전부 읽으면 spread 는 서버로 가지 않는다 — view3d 는 `SCALE * prefs.spread`,
    // view2d 는 `px(n) * prefs.spread` 로 **받은 좌표에 곱할 뿐**이고, `/graph` 요청
    // 어디에도 실리지 않는다. 왕복이 아닌 것을 왕복이라 믿어 손끝에서 떼어 놓았고,
    // 그것이 "끊긴다"로 읽혔다(발주자 지적).
    //
    // 진짜 왕복은 `layout` 과 `cone` 둘뿐이다. 그 둘만 change 로 남는다.
    // 스텝을 0.1 → 0.02 로 잘게 나눈 만큼 input 이 다섯 배 자주 온다. 그대로 매번
    // 다시 그리면 손끝은 부드러워지고 화면이 밀린다 — **프레임당 한 번**으로 묶는다
    // (마지막 값만 쓰므로 중간 값을 버려도 결과가 같다).
    const liveScale = (sel: string, key: "nodeScale" | "edgeScale" | "spread") => {
      const s = viewPanel.querySelector<HTMLInputElement>(sel);
      // 조용히 돌아가면 그 손잡이는 **아무 일도 안 하는 슬라이더**가 된다 — 끌리기는
      // 하고 숫자도 움직이는데(값 표시는 별도 배선) 화면만 안 바뀐다. 행 목록과 이
      // 배선이 어긋나는 순간 생기는 일이고, 어긋나도 아무것도 오류를 내지 않는다.
      if (!s) {
        console.warn(`[view] ${sel} 슬라이더가 패널에 없다 — 행 목록과 배선이 어긋났다.`);
        return;
      }
      let pending = 0;
      s.addEventListener("input", () => {
        if (pending) return;
        pending = requestAnimationFrame(() => {
          pending = 0;
          void applyPrefs({ [key]: Number(s.value) } as Partial<ViewPrefs>, sel);
        });
      });
    };
    liveScale("#pref-nodescale", "nodeScale");
    liveScale("#pref-edgescale", "edgeScale");
    liveScale("#pref-spread", "spread");
    viewPanel.querySelectorAll<HTMLElement>("[data-view]").forEach((b) =>
      b.addEventListener("click", () => applyViewpoint(b.dataset.view as Viewpoint)));
    // D74 — 범례 항목 = 선 갈래 스위치. 좌표를 안 바꾸므로 **재조회 없이** 지금
    // 그려진 것만 다시 그린다(배율 슬라이더와 같은 급). 마지막 한 갈래까지 끄는
    // 것은 막지 않는다: "선 없이 노드만"은 실제로 쓰는 읽기다(군집만 보기).
    viewPanel.querySelectorAll<HTMLElement>("[data-edgekind]").forEach((b) =>
      b.addEventListener("click", () => {
        const k = b.dataset.edgekind as EdgeKind;
        const off = new Set(curPrefs().edgeOff);
        if (off.has(k)) off.delete(k); else off.add(k);
        applyPrefs({ edgeOff: EDGE_KINDS.filter((x) => off.has(x)) },
                   `[data-edgekind="${k}"]`);
      }));
    viewPanel.onkeydown = (e) => { if ((e as KeyboardEvent).key === "Escape") closePanel(); };
  }

  async function applyViewpoint(v: Viewpoint) {
    const cur = trail[trail.length - 1];
    const shown = shownViewpoint(cur);
    hashViewpoint = null;                              // 사용자가 고른 순간 링크 오버라이드 해제
    savePrefs({ ...loadPrefs(), view: v });       // 저장값은 언제나 방금 고른 관점
    // 이미 그리고 있는 관점을 다시 누르면 화면을 건드리지 않는다(applyFrame·applyPrefs
    // 와 같은 계약 — 3D 카메라 프레이밍과 평면 뷰 스크롤이 살아남는다). Grid 는
    // 저장값이 평면이어도 미니가 3D 컷을 그리므로 "3D"가 이미 활성이다: 저장값만
    // 화면에 맞춰 두고(프레임을 벗어나면 그 값이 그대로 걸린다) 다시 그리지 않는다.
    if (shown === v) { syncHash(v); closePanel(); return; }
    syncHash(v);
    await show(trail[trail.length - 1]);
    closePanel();                                 // 목적지 커밋 — 새 칩으로 포커스 복귀
  }

  async function applyPrefs(mut: Partial<ViewPrefs>, refocus?: string) {
    const cur = loadPrefs();
    // 이미 활성인 옵션을 다시 누르면 무동작이다(applyViewpoint·applyFrame 과 같은 계약).
    // 없으면 3D 는 카메라 프레이밍이, 평면 뷰는 스크롤 위치가 재마운트로 되감긴다.
    if ((Object.keys(mut) as (keyof ViewPrefs)[]).every((k) => cur[k] === mut[k])) {
      if (refocus) viewPanel.querySelector<HTMLElement>(refocus)?.focus({ preventScroll: true });
      return;
    }
    savePrefs({ ...cur, ...mut });
    // 번들의 손잡이는 **이미 실린 데이터의 표현만** 바꾼다(단·관계·라벨). 아래
    // show() 로 내려가면 `/graph` 를 통째로 다시 받는데, 그것이 리서치
    // [[vinc.research.view-interaction-perf]] 가 지목한 낭비다 — 옵션 여덟 개 중 하나만
    // 서버가 필요한데 전부 전량 재조회 + 재마운트를 하고 있었다. 여기서만 끊는다.
    const bundleOnly = (Object.keys(mut) as string[]).every(
      (k) => k === "bundleDepth" || k === "bundleOff" || k === "labels");
    const bh = planar as (PlanarHandle & { update?: (o: unknown) => void }) | null;
    if (curPrefs().view === "bundle" && bundleOnly && bh?.update) {
      const np = curPrefs();
      bh.update({ depth: np.bundleDepth, off: np.bundleOff, labels: np.labels !== "none" });
      openPanel();
      if (refocus) viewPanel.querySelector<HTMLElement>(refocus)?.focus({ preventScroll: true });
      return;
    }
    const lv = trail[trail.length - 1];
    // layout·cone 만 서버가 좌표를 새로 만든다. 그 둘이 아니면 (1) 서버는 안 부르고
    // (2) 카메라를 잇고 (3) 렌더러가 제자리에서 소화할 수 있으면 다시 그리지도 않는다.
    // 세 층으로 갈리는 이유는 [[vinc.research.view-interaction-audit]] §2.2~2.4: 옵션
    // 하나에 왕복 + 재구성 + 컨텍스트 하나가 들었고, 슬라이더는 그 재구성이 자기
    // 손잡이를 DOM 에서 떼어 내서 끌 수가 없었다.
    const relayout = "layout" in mut || "cone" in mut;
    const np = curPrefs();
    const live = (g3 as { __update?: (p: ViewPrefs) => boolean } | null)?.__update
      ?? g2?.update;
    if (!relayout && live && live(np)) {
      // 제자리 반영 성공. 슬라이더(input 마다 한 번)는 패널을 다시 그리지 않는다 —
      // 숫자 표시는 자기 배선으로 이미 따라 움직이고, 다시 그리면 끌던 손잡이가
      // 새 노드로 바뀌어 드래그가 끊긴다. 칩은 활성 상태가 바뀌므로 다시 그린다.
      const sliderOnly = (Object.keys(mut) as string[]).every(
        (k) => k === "nodeScale" || k === "edgeScale" || k === "spread");
      if (!sliderOnly) { openPanel(); if (refocus) viewPanel.querySelector<HTMLElement>(refocus)?.focus({ preventScroll: true }); }
      return;
    }
    // 행을 내보낸 층 = 그 프리퍼런스를 실제로 읽는 층이다(Grid 포함). 보이는 컨트롤은
    // 언제나 화면을 바꾼다 — 좌표는 그대로 두고 프리퍼런스만 재적용한다.
    reuseGraph = !relayout;                       // fetchGraph 가 읽고 바로 끈다
    if (rowsFor(np).length > 0)
      await show(lv, { keepPanel: true, keepCamera: !relayout });
    openPanel();                                  // 내용 갱신 + aria·포커스 재확정(닫지 않는다)
    if (refocus) viewPanel.querySelector<HTMLElement>(refocus)?.focus({ preventScroll: true });
  }

  /** 지금 상세가 열려 있는 노드. **주소가 스코프보다 이것을 먼저 싣는다** — 사람이
   *  주소창에서 복사해 건네고 싶은 것은 "무슨 주제를 보고 있었나"가 아니라 "이 노드"다.
   *  L2 가 스코프였을 때는 그 층이 주소를 실었는데, 층이 사라져도 그 능력은 남긴다. */
  let focusId = "";

  /** 주소창을 "지금 보고 있는 곳 + 그 관점"으로 맞춘다. base 를 location.hash 에서
   *  뜨면 안 된다 — 인앱 이동은 해시를 갱신하지 않으므로, 다른 곳을 가리키는 주소에
   *  관점만 찍힌다. 그래서 base 는 trail 과 focusId 에서 만든다.
   *  replaceState 라 hashchange 가 뜨지 않는다(main.ts 라우터 무영향). */
  function syncHash(v: Viewpoint = shownViewpoint(trail[trail.length - 1]) ?? curPrefs().view) {
    const lv = trail[trail.length - 1];
    const enc = encodeURIComponent;
    const base =
      focusId ? `#view/trace/${enc(focusId)}`
      : lv.kind === "l1" ? `#view/${lv.domains.map(enc).join(",")}`
      : "#view";
    history.replaceState(null, "", v === "3d" ? base : `${base}/as/${v}`);
  }

  /** gateway = 지금 걸린 렌즈(주제) **밖**의 노드. 브릿지는 면제(F2) — 단일 domain 이
   *  없다(domain=source_domain 은 보고 편의). 반투명+클릭 시 렌즈 이동은 브릿지에 무의미.
   *
   *  판정을 여기 **한 벌만** 둔다. 여태 이 계산은 `toN3` 안에만 있어서 3D 밖으로 못
   *  나갔고, 그래서 2D · 타임라인 · 번들의 gateway 마크(파선 링)가 그려질 일이
   *  없었다 — 렌더러 세 곳이 `gateway` 를 읽는 코드를 가진 채 값을 못 받고 있었다
   *  (2026-08-15 실측). 같은 노드가 화면마다 다른 모양이면 형태에 뜻을 실은 것이
   *  무너지므로, 렌더러에 넘기는 자리마다 이 함수를 통과시킨다. */
  /** 렌즈 안인가 — **하위 트리 포함**이다(api/query.domain_subtree_where 와 같은 규칙).
   *  두 곳이 어긋나면 스코프가 들여보낸 노드를 화면이 "밖"이라 그린다. 접두에
   *  슬래시를 붙이는 것까지 같다: `vinc` 가 `vincent` 를 삼키면 안 된다. */
  const inLens = (dom: string | undefined, lens: string[]) =>
    lens.some((d) => dom === d || (dom || "").startsWith(d.replace(/\/+$/, "") + "/"));
  const isGateway = (nd: { domain?: string; kind?: string }, lens: string[]) =>
    lens.length > 0 && !inLens(nd.domain, lens) && (nd.kind || "concept") !== "bridge";

  function toN3(nodes: GraphNode[], lens: string[] = [], markId?: string): N3[] {
    return nodes.map((nd) => ({
      id: nd.id,
      label: nd.label === nd.id ? nd.id.replace(/^[a-z]+:/, "") : nd.label,
      domain: nd.domain, degree: nd.degree,
      kind: nd.kind || "concept",
      gateway: isGateway(nd, lens),
      focus: markId !== undefined && nd.id === markId,
      x: nd.x, y: nd.y, z: nd.z,
    }));
  }
  /** D74 — 범례 스위치로 끈 갈래를 뺀다. **그리기 필터일 뿐** 데이터 필터가 아니다:
   *  `curEdges` 는 그대로 두므로 상세 레일의 연결 수·방향 집계는 선을 숨겨도
   *  변하지 않는다(숨긴 선이 없던 연결이 되면 화면이 거짓을 말한다). */
  function visibleEdges(): GraphEdge[] {
    const off = new Set(curPrefs().edgeOff);
    return off.size ? curEdges.filter((e) => !off.has(edgeKindOf(e))) : curEdges;
  }
  function toL3(edges: GraphEdge[]): L3[] {
    // B1 — confidence 를 함께 넘긴다. 3D 렌더러의 잉크 농도 램프가 이 값으로 갈린다
    // (구 `weight` 는 싣는 곳이 없어 램프가 사문이었다 — D77/M4).
    return edges.map((e) => ({ source: e.source, target: e.target, type: e.type,
                               cross: e.cross_domain, confidence: e.confidence }));
  }
  /** 시간 커서(2026-08-17 발주자 결정 — 시간은 뷰포인트가 아니라 **세 번째 상태 축**).
   *
   *  전부 클라이언트에서 한다. 페이로드가 노드마다 `ts` 와 `content_ts` 를 **둘 다**
   *  싣고 오므로(api.GraphNode · `_apply_basis` 가 있는 이유가 그것이다) 커서는 서버
   *  왕복이 0 이다. 재조회하는 커서는 애초에 쓸 수 없다 — 지금도 옵션 하나에 전량
   *  재조회가 붙는다([[vinc.research.view-interaction-audit]]).
   *
   *  ⚠ **엣지는 끝점을 따라간다.** 노드를 숨기면 그 노드로 가는 선은 갈 곳이 없다.
   *  여태 이 가지치기가 없어도 됐던 이유는 `filterUnlinked` 가 연결 0 인 노드만
   *  숨겼기 때문이다(정의상 엣지가 없다) — 커서는 다르다. */
  function timeCut(): string {
    const p = curPrefs();
    if (p.when >= 1) return "";
    return timeCutoff(curNodes.map((n) => nodeTimeOn(n, p.timeBasis)), p.when);
  }
  function atTime<T extends { ts?: string; content_ts?: string }>(ns: T[]): T[] {
    const cut = timeCut();
    if (!cut) return ns;
    const basis = curPrefs().timeBasis;
    return ns.filter((n) => visibleAt(nodeTimeOn(n, basis), cut));
  }
  function pruneEdges(es: GraphEdge[], ns: { id: string }[]): GraphEdge[] {
    if (!timeCut()) return es;
    const live = new Set(ns.map((n) => n.id));
    return es.filter((e) => live.has(e.source) && live.has(e.target));
  }

  /** D32 — Unlinked 필터(열린 층): 고아(연결 0)를 숨긴다. 초점(seed)은 항상 유지. */
  function filterUnlinked(ns: N3[]): N3[] {
    if (loadPrefs().unlinked !== "hide") return ns;
    return ns.filter((n) => (n.degree || 0) > 0 || n.focus);
  }

  /** 지도 마운트 공통 경로(3D) — 전체(L0)와 주제(L1)가 같은 코드를 쓴다.
   *
   *  첫 화면은 **전체 개념 그래프**다 — 주제 목록은 좌측 트리가 상시로 들고 있다
   *  ([[vinc.spec.viewpoints]] · [[vinc.spec.surface-tree]]).
   *
   *  세 층은 스코프만 다르고 나머지가 완전히 같아 한 함수로 접는다. 갈라 두면 셋 중
   *  하나만 고치는 드리프트가 난다. */
  /** 세 관점 경로가 같은 `/graph` 를 같은 인자로 부른다 — 한 함수로 접는다.
   *
   *  두 가지를 여기서 처리한다(2026-08-17, [[vinc.research.view-interaction-audit]]):
   *  · **재사용**: 옵션 변경으로 다시 그릴 때는 서버가 필요 없다(좌표를 바꾸는 것은
   *    layout·cone 뿐이고 그건 applyPrefs 가 `reuseGraph` 를 안 켠다). `layout_positions`
   *    는 순수 함수라 같은 요청은 같은 응답이었고, 그 왕복(73~155ms)이 옵션 클릭 지연의
   *    절반이었다.
   *  · **순서**: 빠르게 연달아 show() 가 나면 늦게 도착한 옛 응답이 새 그림을 덮었다.
   *    부를 때의 세대를 기억해, 돌아왔을 때 세대가 지났으면 `stale` 로 답한다. */
  let reuseGraph = false;
  async function fetchGraph(p: ViewPrefs, scope: { domains?: string[] }):
      Promise<{ executed: boolean; stale?: boolean; nodes?: GraphNode[]; edges?: GraphEdge[] }> {
    if (reuseGraph && curNodes.length) {
      reuseGraph = false;
      return { executed: true, nodes: curNodes, edges: curEdges };
    }
    reuseGraph = false;
    const mySeq = showSeq;
    const domains = scope.domains || [];
    const res = await api.graph(cfg.owner, {
      ...(domains.length ? { domains } : {}), layout: p.layout,
      ...(p.cone === 1 ? {} : { cone: p.cone }) });
    if (mySeq !== showSeq) return { executed: false, stale: true };
    return res;
  }

  async function showGraphScope(scope: { domains?: string[] }) {
    const domains = scope.domains || [];
    const p = curPrefs();
    setMsg(`<p class="hint">${COPY.view.loading(domains.length ? 1 : 0)}</p>`);
    const res = await fetchGraph(p, scope);
    if (res.stale) return;                        // 더 새 show() 가 이미 화면을 가져갔다
    // 도달 불가도 **Sankey 와 같은 상자**로 낸다(발주자 지시 2026-08-05). 붉은 배너만
    // 관점 하나가 다른 모양이면 같은 사정이 화면마다 다른 사건처럼 읽힌다. 문구는
    // 그대로 "못 불러왔다"다 — 상자를 통일하는 것이지 사정을 뭉개는 것이 아니다
    // (여기에 "파일을 추가하세요"를 대면 백엔드가 죽었을 때 거짓 조언이 된다).
    if (!res.executed) { setMsg(`<div class="empty"><p>${COPY.view.error}</p></div>`); return; }
    const nodes: GraphNode[] = res.nodes || [];
    if (nodes.length === 0) {
      // 전체가 비었으면 가져오기까지 붙인다 — 빈 첫 화면이 막다른 골목이면 안 된다.
      // CTA 를 **항상** 붙인다 — Sankey 가 그렇게 한다. 스코프가 좁아 비었을 때
      // "여긴 비었다"만 말하고 나갈 길을 안 주면 그 자리가 막다른 골목이 된다.
      setMsg(`<div class="empty"><p>${COPY.view.idle}</p>
        <button class="btn btn-primary" data-import>${COPY.view.idleCta}</button></div>`);
      return;
    }
    setMsg("");
    curEdges = res.edges || [];
    curNodes = nodes;
    // 지표는 **커서가 고른 모집단**을 센다. 시간을 잘라 놓고 수는 전량을 말하면
    // 그 줄이 화면을 거짓으로 서술한다. 다만 `unlinked` 는 반영하지 않는다 — 고아
    // 칸이 곧 그것을 되돌리는 컨트롤이라, 숨긴 순간 0 이 되면 돌아올 길이 사라진다.
    // 커서는 "이 읽기에 무엇이 존재하는가"이고 unlinked 는 "그중 무엇을 그리는가"다.
    drawMetrics(atTime(nodes));
    // 초점 마크(닫힌 층의 단일 초점색)는 이제 **지금 짚고 있는 노드**가 든다. 예전엔
    // L2 씨앗이 그 자리였는데 층이 사라졌고, 스코프를 넓혀 데려온 노드가 어느 점인지
    // 말해 주는 일은 남아야 한다 — 카메라만 옮기면 "어디로 갔는지"가 안 남는다.
    // 시간 필터는 **toN3 앞**이다 — N3 는 렌더 전용 형태라 시간 열을 안 들고 간다.
    const shown3 = filterUnlinked(toN3(atTime(nodes), domains, lnbFocus.nodeId));
    g3 = mount3d(canvas, shown3, toL3(pruneEdges(visibleEdges(), shown3)), {
      // 게이트웨이(렌즈 밖 노드)는 그 주제로 건너뛴다 — 주제 렌즈에서만 성립하고,
      // 전체 화면에는 렌즈가 없으므로 toN3 가 애초에 표식을 달지 않는다.
      onClick: (nd) => {
        if (nd.gateway) { trail.push({ kind: "l1", domains: [nd.domain!] }); show(trail[trail.length - 1]); }
        else openDetail(nd);
      },
      onRightClick: (nd, ev) => nodeMenu(nd, ev),
    }, p, keepCam);
    keepCam = null;                               // 한 번 실어 보내면 끝 — 다음 마운트는 전체 맞춤
    wireLasso();
  }

  /** 노드 우클릭 = 컨텍스트 메뉴(D64) — 열기 · 여기서 공유 · 삭제. 여태 우클릭이
   *  곧바로 추적을 실행해 메뉴를 기대한 손이 예고 없이 화면을 갈아탔다(Jakob:
   *  우클릭의 기대는 메뉴다).
   *
   *  `여기서 추적`은 2026-08-17 에 내렸다. 그 제스처가 답하던 질문("이 노드 둘레에
   *  뭐가 있나")은 이미 제자리에서 답이 난다 — 짚으면 이웃만 남기고 물러나고(recede),
   *  관계는 옆 카드가 들고, `선 보이기: 데려오기`는 이웃을 이름째 링으로 불러온다.
   *  대신 치르던 값은 장면 통째 재마운트 + 레일·카드·패널이 다 닫히는 것이었고,
   *  정작 쓰고 싶어질 허브에서는 depth-2 가 그래프의 절반을 도로 담아 격리가 얇았다. */
  function nodeMenu(nd: N3, ev: MouseEvent) {
    openContextMenu(ev.clientX, ev.clientY, [
      { label: COPY.ui.menu.open, run: () => openDetail(nd) },
      { label: COPY.ui.menu.shareHere, run: () => openShareSheet({ seed: nd.id, depth: SHARE_DEPTH }) },
      { label: COPY.ui.viewx.del, danger: true, run: async () => {
        if (!await confirmDestructive(COPY.ui.viewx.delNodeAsk(nd.label),
              COPY.ui.viewx.delConfirm, COPY.ui.viewx.delCancel)) return;
        try {
          const res = await api.deleteNode(cfg.owner, nd.id);
          if (!res.executed) throw new Error(res.note || res.error || "delete failed");
          closeRail();
          void show(trail[trail.length - 1]);
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
   *  화면에 이미 있으면 카메라만 옮긴다. 없으면 **스코프를 넓혀 데려온다**: 주제를
   *  알면 그 주제 하나로, 모르면 전량으로. 전량은 반드시 그 노드를 담으므로 막다른
   *  길이 구조적으로 없다 — 브릿지는 주제가 비어 있고(F2) 미분류 노드도 있어서,
   *  주제 하나로만 좁히는 사다리는 그것들을 떨어뜨린다.
   *
   *  씨앗 L2(depth 2)를 되살리지 말 것 — 사람에게 뜻이 있는 것은 주제이고, 그 층은
   *  재마운트 값도 비쌌다([[vinc.plan.document-reading]]).
   *
   *  주제로 넓힐 때 trail 을 바닥까지 되감는다: 주제 전환은 쌓기가 아니라 **바꾸기**다.
   *  안 그러면 노드를 옮겨 다닐 때마다 Esc 로 되짚을 층이 끝없이 쌓인다.
   *
   *  끝에는 언제나 같은 둘이 열린다: 옆에 뜨는 관련 문서 카드 + 우측 문서 패널. */
  async function focusNode(id: string, domain?: string) {
    // 주제를 밖에서 알려 주면 먼저 기억한다 — 합계 뷰는 개념 마크가 없어 그래프
    // 노드로 되짚을 수 없고(가운데 열이 주제다), 트리는 그 값을 이미 안다.
    // 노드 id 는 주제를 모를 때도 적어 둔다: 다시 그릴 때 그 마크를 초점색으로
    // 그릴 근거가 이것뿐이다(예전엔 L2 씨앗이 그 일을 했다).
    lnbFocus = domain ? { domain, nodeId: id } : { ...lnbFocus, nodeId: id };
    if (!flyToMounted(id, domain)) {
      const at = trail[trail.length - 1];
      const already = domain
        ? at.kind === "l1" && at.domains.length === 1 && at.domains[0] === domain
        : at.kind === "l0";
      if (!already) {
        trail.length = 1;                       // 바닥은 언제나 l0
        if (domain) trail.push({ kind: "l1", domains: [domain] });
        await show(trail[trail.length - 1]);
      }
      // 마운트 직후에는 좌표가 아직 앉지 않아 한 번에 못 난다. 자리를 잡은 뒤 다시
      // 한 번(D70). 넓혔든(재마운트) 아니든(딥링크로 방금 그린 화면) 사정은 같다.
      if (!flyToMounted(id, domain))
        setTimeout(() => { if (myGen === liveGen) flyToMounted(id, domain); }, 420);
    }
    const raw = curNodes.find((n) => n.id === id);
    // 전량을 그린 화면에도 없으면 그 노드는 **없는 것**이다(오래된 딥링크가 이 경우다).
    // 빈 상세를 열어 있는 척하지 않는다 — 화면은 전체 지도로 남는다.
    if (!raw && trail[trail.length - 1].kind === "l0") {
      lnbFocus = {}; setLnbActive({}); return;
    }
    // 못 찾았지만 스코프가 좁은 경우(평면 캡에 잘렸거나 렌즈 밖)는 최소 정보로 연다 —
    // 제목은 곧 node-context 가 채운다.
    const nd = raw ? toN3([raw])[0]
      : ({ id, label: id.replace(/^[a-z]+:/, ""), degree: 0 } as N3);
    openDetail(nd);
    lnbFocus = { domain: nd.domain, nodeId: id };
    setLnbActive(lnbFocus);
  }

  /** 노드 맥락(`/node-context`) 한 번만 — 레일과 카드가 같은 노드에 같은 요청을 각자
   *  보내고 있었다(실측 2026-08-17: 클릭 한 번에 두 번, 63ms·100ms). 마지막 노드의
   *  약속을 기억해 둘이 나눠 쓴다. 층이 바뀌거나(show) 그래프를 고치면(삭제·연결)
   *  버린다 — 오래 들고 있으면 "방금 이은 연결이 안 보인다"가 된다. */
  let ctxCache: { id: string; p: ReturnType<typeof api.nodeContext> } | null = null;
  const nodeContextOnce = (id: string) => {
    if (ctxCache?.id !== id) ctxCache = { id, p: api.nodeContext(cfg.owner, id) };
    return ctxCache.p;
  };

  /** 관련 문서 카드 — 노드 옆에 뜨고, 끌어 옮길 수 있고, 항목을 누르면 그 노드로 간다. */
  async function openRelsCard(nd: N3) {
    const pos = nodeScreenPos(nd.id);
    const card = openFloatPanel({
      title: nd.label, x: pos.x, y: pos.y,
      closeLabel: COPY.ui.viewx.railClose,
    });
    card.body.innerHTML = `<p class="hint">${COPY.ui.viewx.sourceLoading}</p>`;
    try {
      const res = await nodeContextOnce(nd.id);
      if (!card.el.isConnected) return;          // 그 사이 닫혔다 — 늦은 응답 폐기
      card.setTitle(nd.label);
      if (!res.links?.executed) { card.body.innerHTML = `<p class="hint">${COPY.ui.viewx.relsNone}</p>`; return; }
      card.body.innerHTML = relsHTML(res.links);
      wireRels(card.body, nd, res.links);
    } catch {
      if (card.el.isConnected) card.body.innerHTML = `<p class="hint">${COPY.ui.viewx.relsNone}</p>`;
    }
  }

  function openDetail(nd: N3) {
    lastNode = nd;   // Reader 뒤로가기 복귀 지점
    // 주소가 이 노드를 싣는다 — 주소창에서 복사해 건네는 것이 "노드 하나"라는 성질은
    // L2 가 스코프였을 때부터의 것이고, 층이 사라져도 남는다.
    focusId = nd.id; syncHash();
    openRail();
    void openRelsCard(nd);
    // 수치는 여기서 세지 않는다 — loadNodeSource 가 node-context 응답(links)으로
    // 채운다. 여태 이 자리에서 `curEdges`(마지막으로 그린 그래프의 잔상)를 세고
    // 드릴다운 경로에서는 degree 를 0 으로 박아, 같은 노드가 동선마다 다른 숫자를
    // 말하고 "연결 0개 · → 3 나감" 같은 자기모순까지 냈다.
    detail.innerHTML = `
      <div class="rail-head">
        <span class="hint">${COPY.ui.viewx.railTitle}</span>
        <button class="rail-x" id="rail-close" aria-label="${COPY.ui.viewx.railClose}"
                data-tip="${COPY.ui.viewx.railClose}">✕</button>
      </div>
      <h2>${esc(nd.label)}</h2>
      <div id="node-stats"><p class="hint">${COPY.ui.viewx.sourceLoading}</p></div>
      <p class="rail-acts">
        <button class="btn btn-primary" id="nd-share">${COPY.ui.viewx.shareCta}</button>
        <button class="btn btn-quiet" id="nd-del">${COPY.ui.viewx.del}</button>
      </p>
      <div id="node-source"></div>
      <div id="ties-suggest"></div>`;
    detail.querySelector("#rail-close")!.addEventListener("click", closeRail);
    // 공유는 "스코프의 출구" — 이 노드의 ego 를 기본 범위로 시트를 연다(D56 상단 바
    // 버튼과 같은 표면). 레일에서도 나갈 수 있어야 진입이 제스처 하나에 갇히지 않는다.
    detail.querySelector("#nd-share")!.addEventListener("click", () =>
      openShareSheet({ seed: nd.id, depth: SHARE_DEPTH }));
    // S1 — 노드 삭제(되돌릴 수 없음). 성공하면 상세를 닫고 현재 층을 다시 그려
    // 지도에서도 사라지게 한다(응답 후 재조회 — 엣지까지 지워지므로 부분 갱신 불가).
    detail.querySelector("#nd-del")!.addEventListener("click", async () => {
      if (!await confirmDestructive(COPY.ui.viewx.delNodeAsk(nd.label),
            COPY.ui.viewx.delConfirm, COPY.ui.viewx.delCancel)) return;
      try {
        const res = await api.deleteNode(cfg.owner, nd.id);
        if (!res.executed) throw new Error(res.note || res.error || "delete failed");
        detail.hidden = true;
        show(trail[trail.length - 1]);
      } catch {
        detail.querySelector("#nd-del")!.textContent = COPY.ui.viewx.delFail;
      }
    });
    // D45-V — 수치·관계·정본 근거를 한 응답으로 채운다(잇기 제안 판정도 그 안에서).
    void loadNodeSource(nd);
  }

  async function openEdgeDetail(e: { source: string; target: string; type: string }) {
    // D45-V 1단계 — 흐름 선 클릭: "왜 A 가 B 로 이어지는가". 클릭=고정(pin) —
    // 상세 패널 자리를 그대로 쓴다(노드 상세와 같은 표면, ESC/다른 클릭으로 이동).
    openRail();
    detail.innerHTML = `<p class="hint">${COPY.ui.viewx.relLoading}</p>`;
    try {
      const res = await api.edgeContext(cfg.owner, e.source, e.target, e.type);
      if (detail.hidden) return;   // 그 사이 다른 층으로 이동 — 늦은 응답 폐기
      if (!res.executed) {
        detail.innerHTML = `<p class="hint">${COPY.ui.viewx.relFail}</p>`;
        return;
      }
      const a = res.src?.title || e.source, b = res.dst?.title || e.target;
      const sentence = (COPY.ui.viewx.relSentence[e.type]
        || COPY.ui.viewx.relSentence.RELATED_TO)(a, b);
      const conf = res.confidence && res.confidence > 0
        ? ` <span class="hint">· ${COPY.ui.viewx.relConfidence(res.confidence)}</span>` : "";
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
        <p class="hint" style="margin-bottom:.3rem">${COPY.ui.viewx.relEvidence}</p>
        ${res.evidence
          ? `<p>${esc(res.evidence)}</p>`
          : `<p class="hint">${COPY.ui.viewx.relEvidenceNone}</p>`}
        ${side(a, res.src)}${side(b, res.dst)}`;
    } catch {
      if (!detail.hidden) detail.innerHTML = `<p class="hint">${COPY.ui.viewx.relFail}</p>`;
    }
  }

  /** 합계 구간 클릭 → 개념 목록 드릴다운(D45-V2 3단계). "이 숫자는 어떤 개념들인가".
   *  개념 클릭 → 그 개념 상세(node-context)로 한 홉 더(문서는 거기서). */
  async function openDrilldown(domain: string, mark: string, seed?: string) {
    openRail();
    detail.innerHTML = `<p class="hint">${COPY.ui.viewx.drillLoading}</p>`;
    try {
      const res = await api.ledgerDrilldown(cfg.owner, domain, mark, seed);
      if (detail.hidden) return;
      if (!res.executed) { detail.innerHTML = `<p class="hint">${COPY.ui.viewx.drillFail}</p>`; return; }
      const items = res.items || [];
      const segName = (COPY.ui.viewx.seg as Record<string, string>)[mark] || mark;
      const head = `<h2>${esc(COPY.ui.viewx.drillHeader(domain, segName, res.total || 0))}</h2>`;
      if (items.length === 0) {
        detail.innerHTML = head + `<p class="hint">${COPY.ui.viewx.drillEmpty}</p>`;
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
               placeholder="${esc(COPY.ui.viewx.drillFilter)}" />` : "")
        + `<div id="drill-list">${rowsHtml("")}</div>`;
      detail.querySelector<HTMLInputElement>("#drill-q")?.addEventListener("input", (e) => {
        const q = (e.target as HTMLInputElement).value.trim().toLowerCase();
        detail.querySelector<HTMLElement>("#drill-list")!.innerHTML = rowsHtml(q);
        wireRows();
      });
      wireRows();
      return;
    } catch {
      if (!detail.hidden) detail.innerHTML = `<p class="hint">${COPY.ui.viewx.drillFail}</p>`;
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
    detail.innerHTML = `<p class="hint">${COPY.ui.viewx.periodLoading}</p>`;
    try {
      const res = await api.docsInRange(cfg.owner, start, end, domain, p.timeBasis);
      if (detail.hidden) return;
      if (!res.executed) { detail.innerHTML = `<p class="hint">${COPY.ui.viewx.periodFail}</p>`; return; }
      const items = res.items || [];
      const head = `<h2>${esc(COPY.ui.viewx.periodHeader(bucket, res.total || 0))}</h2>`;
      if (items.length === 0) {
        detail.innerHTML = head + `<p class="hint">${COPY.ui.viewx.periodEmpty}</p>`;
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
      if (!detail.hidden) detail.innerHTML = `<p class="hint">${COPY.ui.viewx.periodFail}</p>`;
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
      row(COPY.ui.viewx.statTopic, esc(nd.domain || COPY.ui.common.none))
      + row(COPY.ui.viewx.statLinks, String(degree))
      + (out + inn > 0 ? row(COPY.ui.viewx.statDirection, COPY.ui.viewx.statDir(out, inn)) : "")
      + (att > 0 ? row(COPY.ui.viewx.statAttached, String(att)) : "")
      + (nd.gateway ? `<p class="hint">${COPY.ui.viewx.gateway.replace(/^ · /, "")}</p>` : "")
      + `<p class="hint stat-note">${exact
        ? COPY.ui.viewx.statsFrom : COPY.ui.viewx.statsFromView}</p>`;
  }

  /** 관계 목록 — 방향(→/←/부착) · 상대 · 관계 타입 · 근거 유무.
   *
   *  **자리가 바뀌었다(D70).** 여태 우측 레일에 있었는데, 레일은 이제 문서 원문
   *  전용이고 관계는 노드 옆에 뜨는 이동 가능한 카드로 나간다. 줄을 누르면 그
   *  노드로 **이동**한다(카드는 제자리에서 갱신 — 리마운트 없음). 근거가 있는
   *  관계는 근거 줄이 별도 버튼이라 거기서 edge-context 로 들어간다: 목록의
   *  주된 일은 이동이고, 근거 읽기는 그 안의 곁가지다. */
  function relsHTML(links: NodeLinks): string {
    if (links.items.length === 0) return `<p class="hint">${COPY.ui.viewx.relsNone}</p>`;
    const arrow = (d: string) => (d === "out" ? "→" : d === "in" ? "←" : "·");
    return links.items.map((it, i) => `
        <div class="list-row rel-row" data-i="${i}" style="display:block;cursor:pointer">
          <span class="title" style="font-size:.9em">
            <span class="rel-dir">${arrow(it.dir)}</span> ${esc(it.title)}
            <span class="hint rel-type">${esc(it.type.toLowerCase())}</span></span>
          ${it.evidence && it.dir !== "att"
            ? `<button class="hint rel-ev" data-ev="${i}">${COPY.ui.viewx.relEvidence}</button>`
            : `<p class="hint" style="margin:.1rem 0 0">${COPY.ui.viewx.relNoEvidence}</p>`}
        </div>`).join("")
      + (links.truncated ? `<p class="hint">${COPY.ui.viewx.relsMore(
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
    box.innerHTML = `<p class="hint">${COPY.ui.viewx.sourceLoading}</p>`;
    try {
      const res = await nodeContextOnce(nd.id);
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
          ? COPY.ui.viewx.sourceRelated : COPY.ui.viewx.sourceHeader;
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
        parts.push(`<p class="hint">${COPY.ui.viewx.sourceNone}</p>`);
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
   *  신설 없이 View 안에 머문다(D15). 뒤로 = 노드 상세로 복귀. 밑줄 범위는
   *  백엔드 /doc/highlights(토큰 매칭 계산, 정확한 char offset)로 받아 첫 범위로
   *  스크롤한다. verified 배지로 "정본" 신뢰를 시각화. */
  /** 문서를 연다 — **지도 착지와 본문 열기를 한 클릭에.** 좌측 트리·문서 목록·검색이
   *  전부 이 문으로 들어온다.
   *
   *  순서가 계약이다: `focusNode` 는 끝에서 노드 상세로 레일을 덮으므로 리더를
   *  **뒤에** 연다. 그래야 리더의 `뒤로`가 그 노드 상세로 돌아간다(lastNode 가
   *  거기서 세워진다) — 반대로 하면 문서를 열자마자 상세가 덮어써 본문이 사라진다. */
  async function openDocAt(docId: string, nodeId?: string,
                           domain?: string, chunkIndex?: number) {
    if (nodeId) await focusNode(nodeId, domain);
    await openReader(docId, nodeId, chunkIndex);
  }

  /** `chunkIndex` 는 오프셋이 아니라 **청크 번호**다. 오프셋은 아래에서 부르는
   *  `/doc/context` 가 이미 실어 오므로, 검색이 그것을 알아내려 한 번 더 왕복할
   *  이유가 없다(청크 id `<doc>#00007` 의 뒷자리가 곧 번호다). */
  async function openReader(docId: string, conceptId?: string, chunkIndex?: number) {
    openRail();
    detail.innerHTML = `<p class="hint">${COPY.ui.viewx.readerLoading}</p>`;
    try {
      // 셋을 나란히 부른다. 밑줄과 머리는 **증강**이라 실패해도 문서는 열려야 한다 —
      // Promise.all 은 하나만 거절돼도 전부를 버리므로 각자 강등본을 준비한다.
      // (개념 없이 열면 밑줄 그을 대상 자체가 없어 부르지도 않는다.)
      const [doc, hl, ctx] = await Promise.all([
        api.docGet(cfg.owner, docId),
        conceptId
          ? api.docHighlights(cfg.owner, docId, conceptId).catch(() => ({ executed: false }))
          : Promise.resolve({ executed: false }),
        api.docContext(cfg.owner, docId).catch(() => ({ executed: false })),
      ]) as [Awaited<ReturnType<typeof api.docGet>>,
             Partial<Awaited<ReturnType<typeof api.docHighlights>>>,
             Partial<Awaited<ReturnType<typeof api.docContext>>>];
      if (detail.hidden) return;
      if (!doc.executed || doc.text === undefined) {
        detail.innerHTML = `<p class="hint">${COPY.ui.viewx.readerFail}</p>`;
        return;
      }
      // 문서가 자기 제목을 `# 제목` 으로 갖고 있으면 레일 제목은 생략한다 —
      // 같은 글자가 두 번 나오면 어느 쪽이 정본인지 헷갈린다.
      const firstLine = (doc.text.split("\n").find((l) => l.trim()) || "").trim();
      const dupTitle = !!doc.title
        && firstLine.replace(/^#{1,6}\s+/, "").trim().toLowerCase() === doc.title.trim().toLowerCase();
      // 배지는 **깨졌을 때만** 뜬다. 저장 무결성은 정상이 기본값이라, 문서를 열
      // 때마다 "검증된 원본"이라고 말하면 읽으러 온 사람에게 매번 남의 일을 알린다.
      // 사람이 알아야 하는 것은 실패한 경우뿐이다.
      const badge = doc.verified
        ? "" : `<span class="hint">${COPY.ui.viewx.readerUnverified}</span>`;
      // 문서→노드. 여태 없던 방향이다 — 문서를 열어 놓고 "이건 무엇에 걸려 있나"를
      // 물을 수 없었다. 0건이면 줄 자체를 안 그린다(빈 자리를 남기지 않는다).
      const cons = ctx.concepts || [];
      const chips = cons.slice(0, READER_CHIPS);
      const mentionsHTML = cons.length === 0 ? "" : `
        <p class="rdr-chips">
          <span class="hint">${COPY.ui.viewx.readerMentions}</span>
          ${chips.map((c) => `<button class="chip" data-go="${esc(c.id)}"
             data-dom="${esc(c.domain)}">${esc(c.title || c.id)}</button>`).join("")}
          ${cons.length > chips.length
            ? `<span class="hint">${COPY.ui.viewx.relsMore(cons.length - chips.length)}</span>` : ""}
        </p>`;
      // 목차 — 리더 안의 지도. 접어 두는 이유는 이것이 본문이 아니라 길잡이라서다.
      // 깊이는 들여쓰기로만 말한다(글자 크기를 건드리면 목차가 제목 행세를 한다).
      const secs = ctx.sections || [];
      const tocHTML = secs.length < 2 ? "" : `
        <details class="rdr-toc">
          <summary class="hint">${COPY.ui.viewx.readerOutline(secs.length)}</summary>
          ${secs.map((s) => `<button class="rdr-sec" data-at="${s.char_start}"
             style="padding-left:${Math.min(Math.max(s.level - 1, 0), 5) * 0.85}rem"
             >${esc(s.title)}</button>`).join("")}
        </details>`;
      detail.innerHTML = `
        <div class="rail-head">
          <span class="hint">${COPY.ui.viewx.readerTitle}</span>
          <button class="rail-x" id="rail-close" aria-label="${COPY.ui.viewx.railClose}"
                  data-tip="${COPY.ui.viewx.railClose}">✕</button>
        </div>
        <!-- "편집" 토글은 없다(발주자 지시 2026-08-05) — 문서 열은 **상시 편집**이다.
             D45-E 가 "Reader 와 편집기는 한 표면이고 편집은 그 자리에 커서를 켤 뿐"이라
             했는데, 그 커서를 켜는 버튼이 마지막 잔재로 남아 있었다: 고치려면 매번 두
             번 눌러야 했다(문서 열기 → 편집). 저장은 여전히 자동(디바운스 2s)+Ctrl/Cmd+S
             이고, 그 상태는 아래 rdr-state 가 편집기에서 받아 말한다. -->
        <p class="rail-acts">
          <button class="btn" id="rdr-back">${COPY.ui.viewx.readerBack}</button>
          <button class="btn" id="rdr-expand">${COPY.ui.viewx.edExpand}</button>
        </p>
        ${dupTitle ? "" : `<h2 style="margin-top:.4rem">${esc(doc.title || "")}</h2>`}
        <p>${badge} <span class="hint" id="rdr-state"></span></p>
        ${mentionsHTML}
        ${tocHTML}
        <div id="rdr-host"></div>`;
      detail.querySelector("#rail-close")!.addEventListener("click", closeRail);
      // 칩을 누르면 그 노드로 간다 — D70 의 약속("문서를 고르면 지도 어디인지 안다")을
      // 문서 쪽에서도 지키는 문이다. 스코프가 바뀌면 레일은 노드 상세로 갈린다.
      detail.querySelectorAll<HTMLElement>(".rdr-chips [data-go]").forEach((b) =>
        b.addEventListener("click", () =>
          void focusNode(b.dataset.go!, b.dataset.dom || undefined)));
      detail.querySelector("#rdr-back")!.addEventListener("click", () => {
        void handle?.dispose();
        if (lastNode) openDetail(lastNode); else closeRail();
      });
      // D45-E 개정 — **Reader 와 편집기가 한 표면이다.** 문서는 언제나 렌더된
      // 모습으로 보이고(원문 마크다운 노출 없음), `편집`은 그 자리에 커서를 켤
      // 뿐이다. 여태 둘은 다른 화면이었고, 보기 화면은 `$$…$$`·`**…**` 같은
      // 소스를 그대로 노출했다(실측).
      const { mountDocEditor, openDocSheet } = await import("../doc-editor");
      const state = detail.querySelector<HTMLElement>("#rdr-state")!;
      const handle = await mountDocEditor(detail.querySelector<HTMLElement>("#rdr-host")!, {
        owner: cfg.owner, docId, initial: doc.text, title: doc.title,
        contentHash: doc.content_hash, hideTitle: true,
        // 상시 편집. 커서는 켜지 않는다 — 문서 열은 지도와 나란히 서므로, 열자마자
        // 키보드를 가져가면 `f`(맞춤)·Esc 같은 지도 조작이 글자로 들어간다.
        readOnly: false, autoFocus: false,
        // 제목 줄이 숨어 있어 편집기 자신의 상태 글자도 함께 숨는다 — 저장 신호를
        // 여기로 받아 레일 머리 옆에서 말한다.
        onStatus: (t) => { state.textContent = t; },
        highlights: hl.ranges || [],
        // 본문의 `[[doc_id]]` 가 같은 레일에서 그 문서로 갈린다. 지도 착지는 하지
        // 않는다 — 문서 안의 참조는 "저 문서를 마저 읽자"이지 "저 노드로 가자"가
        // 아니고, 노드로 갈 문은 바로 위 칩이 이미 들고 있다.
        onDocLink: (id) => void openReader(id),
      });
      // 목차 클릭·청크 히트 착지 — 편집기가 이미 오프셋으로 스크롤할 줄 안다
      // (밑줄 첫 위치로 가던 그 길). 마운트가 앉은 뒤라야 좌표가 맞는다.
      detail.querySelectorAll<HTMLElement>(".rdr-sec").forEach((b) =>
        b.addEventListener("click", () => handle.scrollToOffset(Number(b.dataset.at) || 0)));
      // 청크 히트 착지 — 그 대목으로 앉힌다. 번호를 못 찾으면(재청킹으로 어긋났거나
      // 오프셋을 잃은 레거시 행) 문서 맨 위로 남는다: 조용한 강등이지 오류가 아니다.
      const at = chunkIndex === undefined ? undefined
        : (ctx.chunks || []).find((c) => c.index === chunkIndex)?.char_start;
      if (at) requestAnimationFrame(() => handle.scrollToOffset(at));
      // peek→전체폭 승격 — 좁은 레일이 답답할 때 같은 문서를 시트로 연다.
      detail.querySelector("#rdr-expand")!.addEventListener("click", () => {
        handle.dispose();
        closeRail();
        void openDocSheet({ owner: cfg.owner, docId });
      });
    } catch {
      if (!detail.hidden) detail.innerHTML = `<p class="hint">${COPY.ui.viewx.readerFail}</p>`;
    }
  }

  async function suggestTiesInto(nd: N3) {
    const box = detail.querySelector<HTMLElement>("#ties-suggest");
    if (!box) return;
    box.innerHTML = `<p class="hint">${COPY.ui.viewx.suggestLooking}</p>`;
    try {
      const res = await api.suggestTies(cfg.owner, nd.id);
      const sug: { id: string; title: string; domain: string; shared_tags: string[] }[] =
        res.suggestions || [];
      if (!res.executed || sug.length === 0) {
        box.innerHTML = `<p class="hint">${COPY.ui.viewx.suggestNone}</p>`;
        return;
      }
      box.innerHTML = `<p class="hint" style="margin-bottom:.3rem">${COPY.ui.viewx.suggestHeader}</p>` +
        sug.map((s, i) => `
          <div class="list-row">
            <span class="title" style="font-size:.9em">${s.title}
              ${s.shared_tags.length ? `<span class="hint">· ${s.shared_tags.join(", ")}</span>` : ""}</span>
            <span class="actions" style="visibility:visible"><button data-bind="${i}">${COPY.ui.viewx.bind}</button></span>
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
            await show(trail[trail.length - 1]);   // 재렌더(연결 반영)
            openRail();                        // show() 가 닫으므로 확인 문구 재표시
            detail.innerHTML = `<h2>${nd.label}</h2>
              <p>${COPY.ui.viewx.bound(s.title)}</p>`;
          } catch {
            b.textContent = "Bind";
            box.insertAdjacentHTML("beforeend",
              `<div class="error-banner">${COPY.ui.viewx.bindFail}</div>`);
          }
        }));
    } catch {
      box.innerHTML = `<p class="hint">${COPY.ui.viewx.unavailable}</p>`;
    }
  }

  /** 2D 관점 — 독립 렌더러(D46). 3D 씬의 z-평면화가 아니라 사이드카 xy 좌표만
   *  쓰는 sigma 렌더러다. L0 는 3D 와 같은 주제 층(Domain View, D16 불변)이되
   *  주제 노드를 **2D 그래프로** 짓는다 — 이 관점은 3D 렌더러에 더 이상 기대지
   *  않는다. (view3d 의 flat 분기는 Grid 미니 카드가 계속 쓴다.) */
  async function show2d(level: Level) {
    const p = curPrefs();
    // D69 — L0 도 그냥 전체 그래프다(구 주제 원 배치 폐지). 3D 경로와 같은 모집단을
    // 쓰므로 관점을 바꿔도 같은 것을 다르게 볼 뿐, 다른 것을 보게 되지 않는다.
    const domains = lvDomains(level);
    setMsg(`<p class="hint">${COPY.view.loading(domains.length ? 1 : 0)}</p>`);
    const res = await fetchGraph(p, { domains });
    if (res.stale) return;
    if (!res.executed) { setMsg(`<div class="empty"><p>${COPY.view.error}</p></div>`); return; }
    const gnodes: GraphNode[] = res.nodes || [];
    curEdges = res.edges || [];
    curNodes = gnodes;
    if (gnodes.length === 0) {
      // CTA 를 **항상** 붙인다 — Sankey 가 그렇게 한다. 스코프가 좁아 비었을 때
      // "여긴 비었다"만 말하고 나갈 길을 안 주면 그 자리가 막다른 골목이 된다.
      setMsg(`<div class="empty"><p>${COPY.view.idle}</p>
        <button class="btn btn-primary" data-import>${COPY.view.idleCta}</button></div>`);
      return;
    }
    setMsg("");
    drawMetrics(atTime(gnodes));      // 3D 와 같은 규칙(위 주석)
    const shown2 = atTime(gnodes).map((nd) => ({ ...nd, gateway: isGateway(nd, domains) }));
    g2 = mount2d(canvas, shown2, pruneEdges(visibleEdges(), shown2), {
      markId: lnbFocus.nodeId,
      onClick: (nd) => openDetail(toN3([nd])[0]),
      onRightClick: (nd, ev) => nodeMenu(toN3([nd])[0], ev),
      // D53 — 흐름 폐기로 갈 곳을 잃은 관계 근거 패널(D45-V)의 새 진입점.
      onEdge: (e) => void openEdgeDetail(e),
    }, p);
  }

  const showL1 = (domains: string[]) => showGraphScope({ domains });

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

  /** 평면 관점(타임라인 · 번들). 스코프 상태는 하나도 갖지 않고
   *  trail 의 마지막 층을 읽어 필요한 것만 가져온다. 데이터가 없는 스코프에서도
   *  관점은 선택된 채로 두고(비활성·숨김 없음) 캔버스에 두 절짜리 빈 상태를 그린다. */
  async function showPlanar(level: Level) {
    const p = curPrefs();
    const domains = lvDomains(level);
    // L0/L1 — 3D 와 똑같은 서브그래프를 받아 평면으로만 다시 그린다(별도 계약 없음).
    //
    // D46-S: **L0 는 전량**이다(정본 [[vinc.spec.viewpoints]] §3.2 "L0=전체", §2.3.1
    // 표 "L0 = 전체 개념"). 예전엔 여기서 `/view`(주제 집계)만 불러 흐름은 주제
    // ties 를, 타임라인은 "주제에는 날짜가 없다" 막다른 골목을 냈다 — 합계만
    // `/ledger?owner=` 로 이미 전량이라 관점끼리 모집단이 어긋나 있었다.
    // `domains` 가 없으면 사이드카는 owner 스코프 전량을 돌려준다
    // (query.py `graph()` "기본은 owner(±domain) 스코프 전체"). call() 이 undefined
    // 파라미터를 빼므로 요청은 그대로 `/graph?owner=…&layout=…` 이 된다.
    setMsg(`<p class="hint">${COPY.view.loading(level.kind === "l0" ? 0 : 1)}</p>`);
    const res = await fetchGraph(p, { domains });
    if (res.stale) return;
    if (!res.executed) { setMsg(`<div class="empty"><p>${COPY.view.error}</p></div>`); return; }
    const gnodes: GraphNode[] = res.nodes || [];
    curEdges = res.edges || [];
    curNodes = gnodes;
    // 전량이 비었다 = 아직 아무것도 없다. 첫 화면이므로 다음 행동(팩)까지 준다.
    if (gnodes.length === 0) {
      setMsg(level.kind === "l0"
        ? `<div class="empty"><p>${COPY.view.idle}</p>
           <button class="btn btn-primary" data-import>${COPY.view.idleCta}</button></div>`
        : `<div class="empty"><p>${COPY.view.idle}</p></div>`);
      return;
    }
    drawMetrics(gnodes);
    // D72 — 시간축은 여기서 고른다. 서버가 `content` 를 요청받으면 ts 를 이미 갈아
    // 끼워 주지만, 두 열이 다 실려 오므로 **재조회 없이** 바꿔 끼울 수 있다. 구
    // 사이드카에는 content_ts 자체가 없어 내용 축이 빈 상태로 정직하게 강등된다.
    const onContent = p.timeBasis === "content";
    const planarTints = p.domainTint ? tintMap(gnodes.map((n) => n.domain), curMode()) : null;
    const nodes = gnodes.map((nd) => ({
      id: nd.id,
      label: nd.label === nd.id ? nd.id.replace(/^[a-z]+:/, "") : nd.label,
      domain: nd.domain, ts: onContent ? (nd.content_ts || "") : nd.ts,
      kind: nd.kind || "concept",
      gateway: isGateway(nd, domains),
      //: 주제 틴트(열린 층). 꺼져 있으면 undefined 라 CSS 의 잉크가 그대로 쓰인다.
      tint: planarTints?.get((nd.domain || "").trim()),
      // 기둥 타임라인의 점 크기 = 연결 수, 0 이면 속 빈 점(고아). 이미 실려 오는
      // 값이라 재조회가 없다.
      degree: nd.degree || 0,
    }));
    if (p.view === "timeline") {
      // 판정도 차트가 실제로 그리는 집합(문서 연결 토글 적용본) 위에서 한다 —
      // 걸러진 노드를 세면 "빈 캔버스에 문구 없음"이거나 undated 가 부풀어 오른다.
      const shown = timelinePool(nodes, p.timelineDocs);
      const dated = shown.filter((n) => !!(n.ts && n.ts.trim()));
      if (dated.length === 0) {
        // 내용 축이 비어 있는 것과 아예 날짜가 없는 것은 다른 사정이다 — 내용 시점은
        // 사람이 적어야 생기므로, 그 축에서는 무엇을 하면 채워지는지까지 말한다(D72).
        setMsg(`<div class="empty"><p>${onContent
          ? COPY.ui.panel.noContentTime : COPY.ui.viewx.noDates}</p></div>`);
        return;
      }
      setMsg("");
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
      // D45-V2 4단계 — 월-밴드 클릭 → 그 달 기간 항목. L1 은 도메인 렌즈를 걸고,
      // 전체(L0)는 렌즈 없이(그 달 전체) 연다.
      const periodDomain = level.kind === "l1" ? lvDomain(level) || undefined : undefined;
      // 밴드 클릭이 넘길 구간을 만들려면 차트와 **같은 알갱이**를 알아야 한다 —
      // 차트는 월, 목록은 연으로 세면 두 수가 어긋난다(D72).
      periodGrain = pickGrain(dated.map((n) => n.ts!));
      // D74 — 원인·결과 겹치기. **방향 관계만** 넘긴다(RELATED_TO 는 "왜"를 말하지
      // 않는다).
      // ⚠ 범례의 선 스위치(`edgeOff`)와는 **엮지 않는다**. 그 스위치는 지도(3D·2D)
      // 위에만 뜨므로, 여기서 따르면 화면에 없는 컨트롤이 이 화면을 조용히 비운다
      // ("원인과 결과 = 켬 인데 아무것도 없음"). 관점마다 자기 선을 정하는 손잡이는
      // 하나씩만 둔다 — 여기서는 이 토글이 그 하나다.
      const lineage = p.timelineLineage
        ? curEdges.filter((e) => edgeKindOf(e) === "directed")
        : undefined;
      // 기둥 문법(2026-08-14) — 항목당 점 하나, 기둥 길이가 곧 개수다. 라벨 옵션은
      // 안 받는다: 점마다 이름을 달 자리가 없고(이름은 hover 툴팁이 답한다), 그래서
      // 아래 rowsFor 의 timeline 행에서도 `labels` 를 뺐다.
      planar = mountColumns(canvas, feed, {
        docs: p.timelineDocs,
        // ⚠ 다른 관점과 달리 클릭이 **스코프를 안 바꾼다**(발주자 지시 2026-08-14).
        // 이 관점은 이미 전량을 그리므로 파고들 곳이 없다 — 클릭은 그 자리에서
        // 고정하고 상세를 연다. 번들 관점과 같은 판단이다.
        onOpen: (id) => {
          const nd = gnodes.find((n) => n.id === id);
          if (nd) openDetail(nd as unknown as N3);
        },
        onMonth: (m) => void openPeriod(m, periodDomain),
        lineage,
        // 초점이 남길 것 = **갈래를 가리지 않은 전체 관계**. 그려지는 선(lineage)은
        // 방향 3종뿐이지만, "이어져 있다"는 그보다 넓다(연관·부착·다리도 이어진 것).
        related: curEdges,
      });
      // 차트가 못 올린 것들을 한 줄로 밝힌다: 날짜 없는 문서 + 상한에 잘린 문서.
      // 계보를 켰는데 그릴 것이 없는 경우도 여기서 말한다 — 켠 손잡이가 아무 일도
      // 안 한 채 조용한 것이 이 코드베이스가 되풀이해 온 실패다.
      const undated = shown.length - dated.length;
      const notes = [
        undated > 0 ? COPY.ui.viewx.undated(undated) : "",
        over > 0 ? COPY.ui.viewx.capped(over) : "",
        lineage && lineage.length === 0 ? COPY.ui.viewx.noLineage : "",
      ].filter(Boolean);
      if (notes.length) {
        lassoHint.textContent = notes.join(" · ");
        lassoHint.hidden = false;
      }
      return;
    }

    if (p.view === "bundle") {
      setMsg("");
      // 상한 가드 — 둘레에 항목 하나당 <g> 하나다. 자르되 **조용히 자르지 않는다**.
      // 타임라인은 최근 것부터 남기지만 여기서는 **연결이 많은 것부터** 남긴다:
      // 이 관점이 말하는 것이 연결이라, 고립된 항목을 남기고 허브를 자르면 그림이
      // 뜻을 잃는다.
      let feed = nodes;
      const over = nodes.length - PLANAR_CAP;
      if (over > 0) {
        const deg = new Map(gnodes.map((n) => [n.id, n.degree || 0]));
        const keep = new Set(nodes.slice()
          .sort((a, b) => (deg.get(b.id) || 0) - (deg.get(a.id) || 0)
                          || (a.id < b.id ? -1 : 1))
          .slice(0, PLANAR_CAP).map((n) => n.id));
        feed = nodes.filter((n) => keep.has(n.id));
      }
      planar = mountBundle(canvas, feed, curEdges, {
        depth: p.bundleDepth, off: p.bundleOff, labels: p.labels !== "none", tint: planarTints,
        // 클릭 = 회전 + 상세 레일. 이 관점의 값은 링 전체가 한 화면에 있다는 것이라
        // 스코프를 갈아 파고들면 방금 돌린 회전과 함께 그 값이 사라진다. (그 파고들기
        // 자체가 2026-08-17 에 없어졌다 — 어느 관점도 이제 클릭으로 좁히지 않는다.)
        onOpen: (id) => {
          const nd = gnodes.find((n) => n.id === id);
          if (nd) openDetail(nd as unknown as N3);
        },
        // 집약 단에서 못 그린 자기연결과 상한에 잘린 항목을 한 줄로 밝힌다.
        onStats: (s) => {
          const notes = [
            over > 0 ? COPY.ui.viewx.capped(over) : "",
            s.selfDropped > 0 ? COPY.ui.panel.bundleSelf(s.selfDropped) : "",
          ].filter(Boolean);
          lassoHint.textContent = notes.join(" · ");
          lassoHint.hidden = notes.length === 0;
        },
      });
      return;
    }

    // 평면 관점은 timeline·bundle 둘이라 여기 도달하면 둘 중 하나가 이미 처리하고
    // 돌아갔어야 한다. 방어적으로 빈 상태를 남긴다.
    setMsg(`<div class="empty"><p>${COPY.view.idle}</p></div>`);
  }

  /** 전환 피드백(Doherty) — 실측 관점 전환이 234~1018ms 다. 400ms 를 넘기는 동안
   *  화면은 **이전 뷰 그대로**였고 아무 표시도 없었다(작업이 진행 중인지 알 수 없다).
   *  400ms 타이머를 걸어 그 이후에만 표시를 켠다: 그보다 빠른 전환에 표시를 켜면
   *  깜빡임만 늘어난다(느껴지지 않는 지연에 스피너를 붙이면 더 느려 보인다). */
  let busyTimer: number | undefined;
  /** `delay` — 옵션 경로는 120ms(F8, 2026-08-17). 옵션 클릭은 사용자가 방금 손을 댄
   *  자리라 400ms 의 무표시가 "먹지 않았다"로 읽힌다(층 전환은 그대로 400). 지금은
   *  옵션 대부분이 show() 에 오지도 않으므로(제자리 반영) 이 지연을 보는 것은 캐시
   *  재마운트뿐이다. */
  function setBusy(on: boolean, delay = 400) {
    window.clearTimeout(busyTimer);
    if (!on) { el.classList.remove("view-busy"); return; }
    busyTimer = window.setTimeout(() => el.classList.add("view-busy"), delay);
  }

  /** `keepPanel` — 렌즈 패널을 **닫지 않는다**. 층이 바뀔 때 패널을 닫는 것은 맞지만
   *  (가리키던 층이 사라지므로), 옵션을 고쳐 같은 층을 다시 그리는 경우는 층 전환이
   *  아니다 — 그때 닫으면 "고르면 모달이 닫힌다"가 된다(발주자 지적 2026-08-05).
   *  연속 조정이 이 패널의 존재 이유이므로 그 경로만 예외로 둔다. */
  /** show() 세대. 연달아 두 번 부르면 앞의 것이 fetch 에서 돌아왔을 때 이 값이 이미
   *  넘어가 있다 — fetchGraph 가 그걸 보고 `stale` 로 접는다(옛 응답이 새 그림을 덮던
   *  것, 그리고 옛 마운트가 새 캔버스 위에 한 번 더 붙던 것을 막는다). */
  let showSeq = 0;
  async function show(level: Level,
                      opts?: { keepPanel?: boolean; keepCamera?: boolean }) {
    const seq = ++showSeq;
    setBusy(true, opts?.keepPanel ? 120 : 400);
    // 옵션만 바뀐 재마운트는 카메라를 잇는다 — 전체 맞춤으로 되감기는 것이 "옵션을
    // 누르면 보던 곳을 잃는다"의 정체였다(실측 2026-08-17). 캡처는 파괴 **전에**.
    keepCam = opts?.keepCamera && g3 ? camera3d(g3) : null;
    ctxCache = null;                           // 층이 바뀌면 노드 맥락 메모도 버린다
    detail.hidden = true;
    closeFloatPanel();   // 층이 바뀌면 카드가 가리키던 노드도 화면에서 사라진다
    float.hidden = true;
    lassoHint.hidden = true;   // 기본 숨김 — wireLasso 가 다시 켠다
    if (!opts?.keepPanel) void closePanel();
    extraSigmas.forEach((s) => s.kill());
    extraSigmas = [];
    if (planar) { planar.dispose(); planar = null; }
    if (g2) { g2.dispose(); g2 = null; }
    if (g3) { g3._destructor?.(); g3 = null; }
    canvas.innerHTML = "";
    drawLegend();
    drawMetrics(null);
    // 위치 표시는 좌측 트리 담당(D71 — 크럼 폐지). 지금 층의 주제를 넘겨 준다.
    setLnbActive(level.kind === "l1" ? { domains: level.domains } : {});
    // U4 — 인앱 이동도 주소를 남긴다. 여태 관점 커밋에서만 동기화해, 파고든 뒤
    // 새로고침하면 튕기고 "지금 보는 곳"을 공유할 수도 없었다. replaceState 라
    // hashchange 가 안 떠 라우터는 무영향이다.
    //
    // 층이 바뀌면 열려 있던 상세는 닫힌다(바로 위에서 detail 을 숨긴다) — 주소도
    // 그 노드를 놓아야 한다. 안 그러면 아무것도 안 열린 화면이 `#view/trace/<id>` 를
    // 계속 주장한다.
    focusId = "";
    syncHash();
    viewpointControl();
    try {
      // 관점이 Level 보다 먼저다 — 평면 관점에서는 mount3d 가 아예 도달 불가능해진다.
      const v = curPrefs().view;
      if (isPlanar(v)) await showPlanar(level);
      else if (v === "2d") await show2d(level);
      // D69 — L0 도 이제 그냥 그래프다(스코프 없는 `/graph`). 주제 표는 좌측 트리로,
      // 주제 이름 변경도 그 트리의 우클릭으로 옮겼다.
      else if (level.kind === "l0") await showGraphScope({});
      else await showL1(level.domains);
      // 노드로 데려가는 일(검색 착지·트리 클릭·딥링크)은 focusNode 가 진다 — 마운트가
      // 자리를 잡은 뒤 한 번 더 날리는 지연도 거기 있다. 여기서는 층만 그린다.
    } catch (e) {
      if (seq !== showSeq) return;              // 옛 세대의 실패는 새 화면에 적지 않는다
      // 위 세 관점과 같은 상자(2026-08-05) — 예외 경로만 다른 모양으로 남기면
      // "왜 어떤 실패는 붉은 띠고 어떤 실패는 빈 상자인가"라는 질문만 남는다.
      //
      // 단 **스토어를 못 읽는 경우만은 다른 말**을 한다(2026-08-16). 그 상태에서
      // 사이드카는 503 을 주고, 여기 "못 불러왔다"로 뭉개면 화면은 빈 지도와
      // 구분되지 않는다 — 사용자가 보는 것은 "내 지식이 사라졌다"이다. 사유
      // (락 충돌·손상)는 사람이 읽을 문장이 아니라서 접어 두고 열게 한다.
      const why = storeDownReason(e);
      if (why === null) {
        setMsg(`<div class="empty"><p>${COPY.view.error}</p></div>`);
      } else {
        setMsg(`<div class="empty"><p>${COPY.view.storeDown}</p>${
          why ? `<details><summary>${COPY.view.storeDownWhy}</summary><pre></pre></details>` : ""}</div>`);
        // 사유는 드라이버·OS 가 만든 문자열(경로 포함)이라 마크업으로 붙이지 않는다.
        const pre = msg.querySelector("pre");
        if (pre) pre.textContent = why;
      }
    } finally {
      if (seq === showSeq) setBusy(false);       // 새 세대가 돌고 있으면 그쪽이 끈다
    }
  }

  /** 1회 온보딩(Paradox of the Active User) — 사람들은 설명서를 읽지 않고 바로
   *  쓴다. 그래서 "어디에 무엇이 있는지"는 문서가 아니라 **화면이 한 번** 가리켜야
   *  한다. 렌즈(보는 방식)와 우클릭(더 많은 동작) 둘만 짚는다 — 첫 화면에서 알아야
   *  할 최소치이고, 그 이상은 소음이다. 한 번 닫으면 다시 뜨지 않는다. */
  const COACH_KEY = "vinc.view.coach.seen";
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
  // `#view/compare/a/b` 는 프레임 폐기(2026-08-02)로 사라진 딥링크다. 옛 주소를
  // 404 로 떨구지 않고 L0 로 받는다 — 북마크가 조용히 깨지는 것보다 낫다.
  //
  // `#view/trace/<id>` 는 **스코프가 아니라 포커스**다(2026-08-17, 씨앗 층 폐지).
  // 층은 전량으로 두고 마운트가 끝난 뒤 그 노드로 간다 — 전량은 반드시 그 노드를
  // 담으므로 딥링크가 빈 화면으로 끝나지 않는다. 철자는 영구 유지한다(별칭 규율).
  const landOn = deep === "trace" ? (parts[2] || "") : "";
  // 주제 여럿은 쉼표로 잇는다(`#view/a,b`) — 주제 이름에 쉼표가 없다는 관례가 전제고,
  // 백엔드의 `domains=a,b` 파라미터와 같은 문법이라 주소와 질의가 한 모양이다.
  // `trace` 를 여기서 빼는 이유: 씨앗 없는 `#view/trace` 가 "trace"라는 주제를 만들었다.
  if (deep && deep !== "as" && deep !== "trace")
    trail.push({ kind: "l1", domains: deep.split(",").map(decodeURIComponent).filter(Boolean) });
  // D56 — 상단 바에 자기 동작을 건다. 공유 범위는 **지금 보고 있는 스코프**가
  // 기본값이다(정본 §5 "공유는 스코프의 출구"). 여태 진입이 올가미 제스처 하나뿐이라
  // 발견되지 않았고, 우클릭 메뉴는 아직 없다.
  setViewHooks({
    share: () => {
      const lv = trail[trail.length - 1];
      // 노드 하나를 건네는 길은 그 노드의 우클릭 메뉴와 상세 레일이 든다(SHARE_DEPTH).
      // 상단 바는 **지금 보고 있는 스코프**를 건넨다.
      if (lv.kind === "l1") openShareSheet({ domain: lvDomain(lv) });
      else openShareSheet({});                      // L0 = 전체
    },
    openNode: (id, dom) => void focusNode(id, dom),
    openDoc: (docId, nodeId, dom, chunkIndex) => void openDocAt(docId, nodeId, dom, chunkIndex),
  });
  // 좌측 트리의 문서 클릭이 여기로 온다(D69·D70). 훅이 없으면 트리는 해시 착지로
  // 강등되므로, View 가 살아 있는 동안만 "리마운트 없는 이동"이 성립한다.
  setLnbNavigator((id, dom, docId) =>
    void (docId ? openDocAt(docId, id, dom) : focusNode(id, dom)));
  // 주제 고르기 — **즉시 반영**이다(발주자 결정 2026-08-15). 고른 것과 보는 것이
  // 어긋나는 중간 상태를 두지 않는 대신, 고를 때마다 서버를 한 번 왕복한다.
  setLnbTopicPicker((path, add) => {
    const lv = trail[trail.length - 1];
    const cur = lv.kind === "l1" ? lv.domains : [];
    // 이미 고른 것을 다시 누르면 **뺀다** — 더하는 길만 있고 빼는 길이 없으면
    // 되돌리려고 홈까지 갔다 와야 한다.
    const next = add
      ? (cur.includes(path) ? cur.filter((d) => d !== path) : [...cur, path])
      : (cur.length === 1 && cur[0] === path ? [] : [path]);
    // 전부 빼면 그것은 L0(전체)다 — 빈 L1 은 "아무것도 안 보이는 주제 화면"이라
    // 상태로서 뜻이 없다.
    if (lv.kind === "l1") trail.pop();
    if (next.length) trail.push({ kind: "l1", domains: next });
    void show(trail[trail.length - 1]);
  });

  await show(trail[trail.length - 1]);
  if (landOn) await focusNode(landOn);
  maybeCoach();
}
