import { api, cfg, WEB, teamsApi, viewScope, viewAccess, spaceKey, storeDownReason, isWriteConflict, storage, restartSidecar, type GraphEdge, type GraphNode,
         type NodeLinks, type NodeContext } from "../api";
import { COPY } from "../copy";
import { formatViewUrl, parseViewUrl } from "../view-url";
import { viewSessionCache } from "../view-session-cache";
import { createGraphLoader } from "../view-graph-loader";
import { retainedViews, restoreRetained } from "../view-retained";
import { mountCompass, compassDataFrom, type CompassHandle, type CompassState } from "./view-compass";
import { openShareSheet } from "../sheets";
import { sayings, statedStart, decisionSteps, type ItemLink, type Step } from "../item-page";
import { isHistorical, conflictIds } from "../node-validity";
import { esc, openContextMenu, confirmDestructive } from "../components";
import { hostOf } from "../storage-mode";
import { shortcutsRest } from "../keys";
import { wireAsk, askRowHTML, nodeAskItems, chev, SHARE_DEPTH } from "./view-ask";
import { disposeEditorsIn, docEditorOpen } from "../doc-editor";

import { dayOf, localStamp } from "../view-prefs";
import { refreshTopbar } from "../topbar";
import { setLnbActive, repaintLnbGraph, setLnbGraphActive,
         refreshLnb } from "../lnb";
import { isAgentRoleId, stripRolePrefix } from "../agent-roster";
import { createPropsCard } from "./view-props-card";
import { createRail } from "./view-rail";
import { createCards, detailErrorHTML } from "./view-cards";
import { createEdgeDetail } from "./view-edge";
import { createReader } from "./view-reader";
import type { N3, ViewHost } from "./view-host";
import { openFloatPanel, closeFloatPanel, isFloatPanelOpen } from "../float-panel";
import { bindSurface } from "../surface-binding";
import { emptyInviteHtml } from "../empty-invite";
import { takePendingNewDoc } from "../authoring";
import { detailState, detailFailure, topicDetailHTML, type DetailState } from "../topic-detail";
import { bindTopicDescription, type TopicDraft } from "../topic-description";

/** Surface 3 — View (spec: [[vinc.spec.viewpoints]] · as-built [[vinc.reference.view-controls]]).
 *
 *  그래프는 2D 나침반 하나다(view-compass.ts). 이 표면은 데이터를 읽어 나침반에 넘기고, 문서 열 · 노드 카드 ·
 *  상세 레일 · 주소를 맡는다. "어디"는 Level(trail)이 쥔다.
 *  근거: [[decision:the-graph-is-one-2d-compass-lens-2026-11-06]] */

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


// 런타임 테마 모드 — 테마 전환(retheme)이 칠한 모드와 견준다.
import { type ThemeMode } from "../theme";
import { loaderBlock, loaderInline } from "../vinc-loaders.js";
const curMode = (): ThemeMode =>
  document.documentElement.dataset.theme === "dark" ? "dark" : "light";

/** 마운트 세대 — 라우터가 표면을 갈아끼워도 컨테이너는 남으므로, "내가 아직 살아
 *  있는가"는 이 카운터로만 판별한다(전역 리스너 누수 방지). */
let liveGen = 0;
let disposeView: (() => void) | undefined;
export function leaveView(): void { disposeView?.(); disposeView = undefined; ++liveGen; }

/** 살아 있는 마운트의 테마 재칠 — renderView 가 걸고, 표면이 떠나면 스스로 물러난다. */
let themeRepaint: (() => void) | null = null;
/** 테마가 뒤집혔다 — 열린 화면을 **다시 칠하기만** 한다. `main.ts applyTheme` 가 좌측
 *  트리(`repaintLnb`)와 나란히 부른다: 설정 클릭과 OS 추종이 만나는 곳이 거기뿐이다.
 *  근거: [[decision:a-theme-switch-repaints-the-open-view-in-place]] */
export function repaintView(): void { themeRepaint?.(); }

export async function renderView(el: HTMLElement, resume = false) {
  leaveView();
  const myGen = ++liveGen;
  // 맨 `#view` 로 돌아온 마운트는 떠날 때 보던 범위(책갈피)로 연다.
  const restoreEntryScope = resume && location.hash === "#view";
  const mountScope = viewScope();
  const accessJob = new AbortController();
  disposeView = () => accessJob.abort();
  const firstAccess = WEB ? viewAccess(mountScope, accessJob.signal).catch(() => null) : Promise.resolve(null);
  // 스페이스 전환으로 보관해 둔 항목이 있을 수 있으면 신원·표식을 먼저 확인한다.
  const holdsRetained = WEB && !viewSessionCache.count && !viewSessionCache.hasBookmark && retainedViews.has();
  const entryAccess = WEB && (viewSessionCache.count || viewSessionCache.hasBookmark || holdsRetained)
    ? await firstAccess : null;
  if (myGen !== liveGen || mountScope.key !== viewScope().key) { accessJob.abort(); return; }
  if (entryAccess) {
    viewSessionCache.activate(entryAccess.session, entryAccess.revision);
    if (holdsRetained) {
      await restoreRetained(entryAccess.session, entryAccess.revision);
      if (myGen !== liveGen || mountScope.key !== viewScope().key) { accessJob.abort(); return; }
    }
  }
  const bookmark = entryAccess && restoreEntryScope ? viewSessionCache.last(entryAccess.session) : undefined;
  el.innerHTML = `
    <div id="view-canvas"></div>
    <!-- 하단 바에는 **지표만** 남는다(D71). 렌즈는 상단 바로 올라갔고,
         지금은 그 바의 **오른쪽**에 있다(왼쪽은 설정·도구·규칙 = 앱을 다루는 일).
         하단 바의 남은 일은 "지금 화면이 무엇을 세고 있는가" 하나다. 지표는 여전히
         떠 있는 알약이 아니라 상태 표시줄이다(떠 있는 알약은 배너로 읽혀 무시된다
         — D65). -->
    <div id="view-chrome">
      <div id="view-metrics"></div>
    </div>
    <div id="view-detail" hidden></div>
    <!-- 폭 조절 그립은 패널의 **형제**다: 패널 내용은 화면마다 innerHTML 로 갈리는데
         (상세·관계·드릴다운·리더) 그 안에 두면 매번 다시 붙이고 다시 배선해야 한다. -->
    <div id="rail-grip" role="separator" aria-orientation="vertical"
         aria-controls="view-detail" tabindex="0" hidden></div>
    <!-- 인캔버스 크롬의 위치는 CSS 가 정한다. 문서 패널이 캔버스 위에 떠 있으므로
         패널 폭만큼 크롬을 밀지 않는다. 임베드 배치의 오프셋은 CSS 변수가 맡는다. -->
    <div id="view-msg"></div>
    <div id="view-tools"></div>
  `;
  const canvas = el.querySelector<HTMLElement>("#view-canvas")!;
  const detail = el.querySelector<HTMLElement>("#view-detail")!;
  const railGrip = el.querySelector<HTMLElement>("#rail-grip")!;
  // 상세는 셸의 문서 패널에, 그립은 그 패널의 직속 자식에 옮긴다. 둘 다
  // 캔버스와 같은 그리드 칸에 뜬다. 패널이 없는 임베드에서는 표면 안에 남는다.
  const shell = document.getElementById("shell");
  const docpane = document.getElementById("docpane");
  // 머리는 셸 것이므로 상세는 본문에만 심는다. 그립은 머리 옆까지 패널 전체
  // 높이에 걸쳐야 하니 패널 직속이다.
  document.getElementById("docpane-body")?.append(detail);
  if (docpane) docpane.append(railGrip);
  const msg = el.querySelector<HTMLElement>("#view-msg")!;
  const metricsBar = el.querySelector<HTMLElement>("#view-metrics")!;
  // 떼어 낸 묶음(레일 · 속성 절 · 카드 · 관계 상세 · 리더)에 내주는 손잡이 한 벌. 화살표가 읽는 닫힌 변수는 아래에서 선언되고, 부를 때 읽는다.
  const host: ViewHost = {
    detail,
    reloadNode: (nd) => { ctxCache = null; return loadNodeSource(nd); },
    isLive: () => myGen === liveGen,
    dropPick,
    forgetNode: () => {
      lastNode = null;
      if (focusId) { focusId = ""; syncHash(); }   // 주소는 화면이 말하는 것만 말한다
    },
    closeRail: () => rail.closeRail(),
    railFor,
    focusNode,
    openEdgeDetail: (e) => openEdgeDetail(e),
    openTopicCard,
    homeCardPos,
    topicColour: (domain) => compass?.colour(domain),
    hasHistory: () => detailHistory.length > 0,
    wireDetailBack,
    lastNode: () => lastNode,
    focusedId: () => focusId,
    nodeContextOnce: (id) => nodeContextOnce(id),
    dropNodeContext: () => { ctxCache = null; },
    showLevel: (opts) => show(trail[trail.length - 1], opts),
    openRail: () => rail.openRail(),
    railHeadHTML: (caption) => rail.railHeadHTML(caption),
    wireRailHead: () => rail.wireRailHead(),
    curNodes: () => curNodes,
    curEdges: () => curEdges,
    openDetail,
    railRevision: () => rail.revision,
    railFull: () => rail.full,
    railAskCounts: () => railAskCounts,
    dropRailAskCounts: () => { railAskCounts = null; },
    openNodePane,
  };
  // 레일(폭 · 그립 · 크기 · 머리 · 열고 닫기)은 `view-rail.ts` 가 맡는다. 만들 때 저장된 폭으로 닫힌 채 시작한다.
  const rail = createRail(host, { el, shell, docpane, railGrip });
  const { openRail, closeRail, railHeadHTML, wireRailHead } = rail;


  let compass: CompassHandle | null = null;   // 나침반 렌즈(그래프 보기)
  // 같은 범위를 다시 그릴 때(`keepCamera`: 변경 감지 새로고침 · 쓰고 난 뒤) 나침반이 보던 자리. 다음 마운트가 한 번 받아 간다
  let compassKeep: CompassState | null = null;
  let unbindSearch: (() => void) | null = null;   // 나침반이 건 검색 거르개를 푸는 손(나침반을 부술 때)
  let compassDisposeWrapped = false;   // dispose 를 나침반 정리로 한 번만 감싼다(마운트마다 감싸면 사슬이 자란다)
  let lastNode: N3 | null = null;                   // Reader 뒤로가기용 마지막 상세 노드(D45-V2)
  /** 잇기(흐름 F)가 기다리는 대상 — 있으면 다음 초점이 카드 대신 여기로 간다(`focusOnMap`). */
  let pickTarget: ((id: string, title: string) => void) | null = null;
  let curEdges: GraphEdge[] = [];     // 현재 레벨 엣지 — 상세 레일 in/out 표기용
  // 지금 그린 노드들 — focusNode 가 id 만 받고도 라벨·주제를 알아야 한다(D70).
  // 렌더러에서 꺼내지 않고 마운트 시점에 한 벌 챙겨 둔다.
  let curNodes: GraphNode[] = [];
  /** 지금 그려진 노드를 갈아 끼운다. 대입은 이 한 곳에서만 한다. */
  function setCurNodes(next: GraphNode[]): void {
    curNodes = next;
  }
  /** 지금 렌더러들이 칠해진 모드. 새 마운트는 언제나 그때의 모드로 칠하므로, 이 값은
   *  테마가 뒤집힐 때만 바뀐다 — 같은 값을 다시 쓰는 applyTheme(설정에서 같은 칸을 다시
   *  누름 · "auto" 가 이미 OS 와 같음)에는 아무 일도 안 한다. */
  let paintedMode = curMode();
  /** 테마 전환을 **색에만** 반영한다. `show()` 를 부르지 않는 것이 요점이다 — 그 길은
   *  읽던 문서 열과 노드 카드를 닫고(detail.hidden · closeFloatPanel) 카메라·점등까지
   *  되감는다. 주제 색 표는 나침반이 다시 짓고, 떠 있는 카드는 그 표에서 자기 굳은 값만 갈아 끼운다.
   *  근거: [[decision:a-theme-switch-repaints-the-open-view-in-place]] */
  function retheme(): void {
    const mode = curMode();
    if (mode === paintedMode) return;
    paintedMode = mode;
    compass?.retheme();
    // 떠 있는 노드 카드의 주제 표식(openRelsCard) — 나침반이 다시 지은 표를 읽는다.
    document.querySelectorAll<HTMLElement>(".float-mark[data-domain]").forEach((mk) => {
      const t = compass?.colour(mk.dataset.domain || "");
      if (t) mk.style.color = t; else mk.style.removeProperty("color");
    });
  }
  // 라우터는 표면을 떠날 때 컨테이너를 비운다 — 캔버스가 문서에서 떨어졌으면 이 마운트는
  // 끝난 것이다(새 마운트는 liveGen 을 올리고 자기 것을 건다).
  themeRepaint = () => { if (myGen === liveGen && canvas.isConnected) retheme(); };
  // 마지막으로 짚은 곳(D70) — 합계의 `기타` 병합 핀에 쓴다. trail 과 별개다:
  // 트리에서 문서를 눌러도 층은 그대로일 수 있는데, 그때도 그 주제는 살아 있어야 한다.
  let lnbFocus: { domain?: string; nodeId?: string } = {};
  const trail: Level[] = [];

  // deep links — 공유 문법은 `parseViewUrl` **하나**로 읽는다(`#view` · `#view/<a,b>` ·
  // `trace/<id>` · `ids/<a,b>` · `doc/<id>` · `/d<n>` · `/as/<vp>`). 여기 손으로 짠 파서가
  // `trace`·`as` 만 알던 동안, 앱이 스스로 복사하는 `doc`·`ids` 주소가 "doc"·"ids" 라는
  // 주제로 읽혀 빈 지도가 오류 없이 떴다(view-url.ts 머리말의 그 함정).
  // 근거: [[decision:the-app-reads-its-own-view-addresses-through-the-shared-parser]]
  // 공유 문법 밖은 앱 전용 `#view/read/<id>[/<doc>]`(LNB 착지) 하나라 그것만 따로 읽는다.
  const rawParts = location.hash.split("/").map(decodeURIComponent);
  const readRoute = rawParts[1] === "read";
  let addr = readRoute ? null : parseViewUrl(location.hash);
  if (!readRoute && bookmark?.domains.length)
    addr = parseViewUrl(formatViewUrl({ kind: "domains", domains: bookmark.domains }));
  // 주소의 `/as/<관점>` 칸은 읽지 않는다 — 보기는 나침반 하나다(view-prefs `VIEWPOINTS`).

  function setMsg(html: string) {
    msg.innerHTML = html;
    msg.style.display = html ? "grid" : "none";
    // 빈 상태의 CTA 는 표면으로 데려가지 않고 그 자리에서 시트를 연다 — 첫 사용자가
    // "가져오기"를 누르고 또 다른 화면에서 다시 "가져오기"를 찾게 두지 않는다.
    msg.querySelector("[data-import]")?.addEventListener("click", async () => {
      const { pickAndImport } = await import("../sheets");
      pickAndImport();
    });
    // 초대 블록(빈 그래프)은 끌어다 놓기·파일 고르기·노드 만들기를 한자리에 편다.
    // 적재나 생성이 끝나면 **지금 보고 있던 층을 그대로** 다시 그린다 — 만든 노드가
    // 어디에도 안 보이면 만든 것 같지 않다.
    if (msg.querySelector("[data-invite]")) {
      void import("../empty-invite").then(({ wireEmptyInvite }) =>
        wireEmptyInvite(msg, async (nodeId) => {
          await show(trail[trail.length - 1]);
          if (nodeId) void focusNode(nodeId);
        }));
    }
    // 좁힌 범위가 비었을 때의 출구 — 바닥(l0, 전체 지도)으로 되돌아간다.
    msg.querySelector("[data-ei-all]")?.addEventListener("click", () => {
      trail.length = 1;
      void show(trail[0]);
    });
    // 읽기 실패 상자의 다시 시도 — 지금 보던 층을 그대로 다시 읽는다(층을 바꾸거나 바닥으로 내리지 않는다).
    msg.querySelector("[data-view-retry]")?.addEventListener("click", () => {
      void show(trail[trail.length - 1]);
    });
  }

  /** 그래프를 못 읽었을 때의 상자: 빈 상태와 같은 모양에 다시 시도 단추 하나. 스토어·데이터베이스가
   *  응답하지 않는 상자(storeDown · showDatabaseDown)는 따로 있고 자기 단추를 가진다. */
  const readFailedBox = () => `<div class="empty" role="alert"><p>${COPY.view.error}</p>
      <p><button class="btn" type="button" data-view-retry>${esc(COPY.ui.lnb.retry)}</button></p></div>`;

  /** 결과가 0 인 자리의 빈 상태. 범위를 좁혀서 빈 것과 스토어가 빈 것은 다르게 말한다:
   *  주제 하나가 비었는데 "아직 그래프가 없다"고 하면 개념이 수백 개 있는 사람에게
   *  거짓말이 된다. 어느 쪽이든 가져오기·노드 만들기는 남긴다(막다른 골목 금지). */
  function emptyFor(domains: string[]): string {
    return domains.length
      ? emptyInviteHtml(COPY.view.scopeEmpty, { scoped: true })
      : emptyInviteHtml(COPY.view.idle);
  }


  /** 표면 공용 이스케이프 — 여태 함수마다 같은 것을 다시 선언하고 있었다(esc·esc2). */

  /** 잇기 대상 고르기를 푼다(열·층이 바뀌면 고르던 자리가 사라진다). 잇기 띠가 듣고 닫힌다. */
  function dropPick() {
    if (!pickTarget) return;
    pickTarget = null;
    dispatchEvent(new CustomEvent("vinc:pick-cancel"));
  }
  // Esc 단계 후퇴(정본 §2) 1단계 — 레일이 열려 있으면 레일부터 닫는다. 보기 패널이
  // 열려 있을 땐 그쪽 핸들러가 먼저 잡으므로(패널에 포커스가 있다) 충돌하지 않는다.
  /** 정본 §2 조작 문법(V12) — 여태 규정만 있고 구현이 없던 키들.
   *  Esc 는 **단계적 후퇴**다: 패널 → 레일 → 상위 층. 한 번에 다 닫지 않는다. */
  const onKey = (e: KeyboardEvent) => {
    if (myGen !== liveGen) { document.removeEventListener("keydown", onKey); return; }
    // ⚠ **Esc 만은 통과시킨다.** 이 가드가 옳게 비켜서는 바람에, 편집기에 포커스가
    // 있으면 레일을 Esc 로 닫을 수 없었다 — 들어가는 문은 모든 마크에 달려 있는데
    // 나오는 문이 ✕ 하나로 줄었다. 이제 어느 문으로 나가도 대기 중 편집이 확정되므로
    // (doc-editor 의 dispose) 이 문을 되살려도 잃는 것이 없다.
    if (shortcutsRest("surface", e.target, document.activeElement, docEditorOpen())
        && e.key !== "Escape") return;
    if (e.key === "Escape") {
      // 가장 나중에 뜬 것부터 접는다. 카드가 렌즈 패널보다 앞이다 — 카드는 방금
      // 고른 노드에 딸린 것이고, 패널은 그보다 오래 떠 있던 크롬이다(D70).
      // 여기서 접었으면 나침반이 같은 Esc 로 뿌리로 돌아가지 않게 표시한다(나침반은 defaultPrevented 를 본다)
      if (isFloatPanelOpen()) { e.preventDefault(); closeFloatPanel(); return; }
      if (!detail.hidden) { e.preventDefault(); closeRail(); return; }
      if (trail.length > 1) { e.preventDefault(); trail.pop(); void show(trail[trail.length - 1]); }
      return;
    }
  };
  document.addEventListener("keydown", onKey);
  // 표면이 갈아끼워지면 전역 리스너를 걷어낸다. **컨테이너 존재로는 판별할 수 없다** —
  // 라우터는 #surface 의 innerHTML 만 비우고 컨테이너는 그대로 두기 때문이다(구
  // MutationObserver 판정이 영원히 참이라 리스너가 쌓였다). 마운트 세대로 가른다.


  /** 지표바 — 떠 있는 알약이 아니라 하단 상태 표시줄이라
   *  배너로 읽혀 무시되는 자리를 벗어난다. `연결 없음` 은 **누를 수 있다** — 세어만
   *  놓고 해소할 길을 안 주면 미완의 일로 남는다(Zeigarnik). */
  // ⚠ 세는 것은 **그래프 노드**다(파생된 주제 노드 포함) — 라벨이 "문서"였던 동안
  // 개념 하나를 만든 첫 화면이 "문서 3"이라고 말했다(개념 1 + 주제 2). 세는 것과
  // 부르는 이름을 갈라 두면 화면이 조용히 거짓말한다.
  function drawMetrics(nodes: { degree?: number; ts?: string }[] | null) {
    if (!nodes || nodes.length === 0) { metricsBar.innerHTML = ""; return; }
    const total = nodes.length;
    const linked = nodes.filter((n) => (n.degree || 0) > 0).length;
    const orphan = total - linked;
    const rate = total ? Math.round((linked / total) * 100) : 0;
    const latest = nodes.map((n) => n.ts || "").filter(Boolean).sort().pop() || "";
    const cell = (label: string, value: string, act = false, tip = "") =>
      `<span class="a2-metric${act ? " metric-act" : ""}"${tip ? ` title="${esc(tip)}"` : ""}${act ? ' data-orphans="1" role="button" tabindex="0"' : ""}>`
      + `<b class="pl-num">${value}</b>${label}</span>`;
    metricsBar.innerHTML =
      cell(COPY.ui.viewx.mDocs, String(total))
      + cell(COPY.ui.viewx.mLinked, `${linked}/${total} (${rate}%)`)
      + cell(COPY.ui.viewx.mOrphan, String(orphan), orphan > 0)
      + (latest ? cell(COPY.ui.viewx.mLatest, esc(dayOf(localStamp(latest))),
        false, `${COPY.ui.viewx.mDateTip} ${localStamp(latest).replace("T", " ")}`) : "");
    const openOrphans = () =>
      void openDrilldown(lvDomain(trail[trail.length - 1]), "orphan");
    const orphansCell = metricsBar.querySelector<HTMLElement>("[data-orphans]");
    orphansCell?.addEventListener("click", openOrphans);
    orphansCell?.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        openOrphans();
      }
    });
  }


  /** 지금 상세가 열려 있는 노드. **주소가 스코프보다 이것을 먼저 싣는다** — 사람이
   *  주소창에서 복사해 건네고 싶은 것은 "무슨 주제를 보고 있었나"가 아니라 "이 노드"다.
   *  L2 가 스코프였을 때는 그 층이 주소를 실었는데, 층이 사라져도 그 능력은 남긴다. */
  let focusId = "";

  /** 주소창을 "지금 보고 있는 곳"으로 맞춘다. base 를 location.hash 에서 뜨면 안 된다 —
   *  인앱 이동은 해시를 갱신하지 않는다. 그래서 base 는 trail 과 focusId 에서 만든다.
   *  replaceState 라 hashchange 가 뜨지 않는다(main.ts 라우터 무영향). */
  function syncHash() {
    const lv = trail[trail.length - 1];
    // 문법은 `view-url.ts` 한 벌이다. 여기 손으로 짜 두면 복사 버튼이 만드는 주소와
    // 주소창에 있는 주소가 갈라지고, 그건 사용자가 복사한 것이 자기가 본 것이 아니게
    // 된다는 뜻이다. `replaceState` 인 것은 그대로 — 여기서 location.hash 에 대입하면
    // hashchange 라우터가 재진입해 표면을 다시 마운트한다.
    history.replaceState(null, "", formatViewUrl(
      focusId ? { kind: "node", id: focusId }
      : lv.kind === "l1" ? { kind: "domains", domains: lv.domains }
      : { kind: "all" }));
  }

  /** gateway = 지금 걸린 렌즈(주제) **밖**의 노드. 브릿지는 면제(F2) — 단일 domain 이
   *  없다(domain=source_domain 은 보고 편의). 반투명+클릭 시 렌즈 이동은 브릿지에 무의미.
   *
   *  판정을 여기 **한 벌만** 둔다 — 렌더러에 넘기는 자리마다 이 함수를 통과시킨다. */
  /** 렌즈 안인가 — **하위 트리 포함**이다(api/query.domain_subtree_where 와 같은 규칙).
   *  두 곳이 어긋나면 스코프가 들여보낸 노드를 화면이 "밖"이라 그린다. 접두에
   *  슬래시를 붙이는 것까지 같다: `vinc` 가 `vincent` 를 삼키면 안 된다. */
  const inLens = (dom: string | undefined, lens: string[]) =>
    lens.some((d) => dom === d || (dom || "").startsWith(d.replace(/\/+$/, "") + "/"));
  const isGateway = (nd: { domain?: string; kind?: string }, lens: string[]) =>
    lens.length > 0 && !inLens(nd.domain, lens) && (nd.kind || "concept") !== "bridge";

  function toN3(nodes: GraphNode[], lens: string[] = [], markId?: string): N3[] {
    const today = new Date().toISOString().slice(0, 10);
    // 충돌은 짝으로만 잰다 — 노드 하나를 넘기는 호출(카드·레일)에서는 비어 있다.
    const conflicts = nodes.length > 1 ? conflictIds(nodes, today) : new Set<string>();
    return nodes.map((nd) => ({
      id: nd.id,
      label: nd.label === nd.id ? nd.id.replace(/^[a-z]+:/, "") : nd.label,
      domain: nd.domain, degree: nd.degree,
      kind: nd.kind || "concept",
      gateway: isGateway(nd, lens),
      focus: markId !== undefined && nd.id === markId,
      x: nd.x, y: nd.y, z: nd.z, ts: nd.ts,
      historical: isHistorical(nd, today),
      conflict: conflicts.has(nd.id),
    }));
  }


  /** 그래프 읽기는 `/graph` 하나를 같은 인자로 부른다 — 한 함수로 접는다.
   *  · **순서**: 빠르게 연달아 show() 가 나면 늦게 도착한 응답이 새 그림을 덮는다.
   *    부를 때의 세대를 기억해, 돌아왔을 때 세대가 지났으면 `stale` 로 답한다.
   *  읽기와 탭 보관소의 상태는 `view-graph-loader.ts` 가 든다. */
  const loader = createGraphLoader({
    mountScope, accessJob, firstAccess,
    seq: () => showSeq,
    alive: () => myGen === liveGen && canvas.isConnected,
  });
  let displayedScene: { key: string; domains: string[] } | undefined;
  const markDisplayed = (domains: string[]) => {
    displayedScene = { key: loader.scopeKey!, domains: [...domains] };
  };
  const dispose = () => {
    const retained = loader.retained;
    if (retained && displayedScene?.key === retained.key
        && mountScope.key === viewScope().key && myGen === liveGen) {
      viewSessionCache.remember(retained.session, retained.key, retained.revision, displayedScene.domains);
    }
    accessJob.abort(); loader.abort();
    // 떠 있는 카드는 표면 밖(`body`)에 붙어 있어서 그래프를 떠나도 혼자 남았다. 그래프의 것이니 함께 걷는다.
    closeFloatPanel();
    removalObserver.disconnect(); swapSurface();
  };
  disposeView = dispose;
  const removalObserver = new MutationObserver(() => {
    if (canvas.isConnected && myGen === liveGen) return;
    dispose();
  });
  removalObserver.observe(document.body, { childList: true, subtree: true });
  async function showGraphScope(scope: { domains?: string[] }) {
    const domains = scope.domains || [];
    setMsg(loaderBlock("branch", COPY.view.loading(domains.length ? 1 : 0)));
    const res = await loader.fetchGraph(scope);
    if (res.stale) return;                        // 더 새 show() 가 이미 화면을 가져갔다
    swapSurface();
    // 도달 불가도 빈 상태와 **같은 상자**로 낸다. 붉은 배너만 다른 모양이면 같은 사정이
    // 다른 사건처럼 읽힌다. 문구는
    // 그대로 "못 불러왔다"다 — 상자를 통일하는 것이지 사정을 뭉개는 것이 아니다
    // (여기에 "파일을 추가하세요"를 대면 백엔드가 죽었을 때 거짓 조언이 된다).
    if (!res.executed) { setMsg(readFailedBox()); return; }
    // 주제 설명의 문서 미러는 그 주제의 노드가 아니다(주제가 글을 못 담아 문서로 둔 것). 지도·트리·수에서 빼고
    // 설명은 주제 상세가 보여 준다. 표시는 서버가 단다(`api/graph_read.py` `_mark_topic_descriptions`)
    const nodes: GraphNode[] = (res.nodes || []).filter((n) => !n.topic_description);
    if (nodes.length === 0) {
      // 전체가 비었으면 가져오기까지 붙인다 — 빈 첫 화면이 막다른 골목이면 안 된다.
      // CTA 를 **항상** 붙인다 — Sankey 가 그렇게 한다. 스코프가 좁아 비었을 때
      // "여긴 비었다"만 말하고 나갈 길을 안 주면 그 자리가 막다른 골목이 된다.
      setMsg(emptyFor(domains));
      return;
    }
    setMsg("");
    curEdges = res.edges || [];
    setCurNodes(nodes);
    // 지표는 **커서가 고른 모집단**을 센다. 시간을 잘라 놓고 수는 전량을 말하면
    // 그 줄이 화면을 거짓으로 서술한다. 다만 `unlinked` 는 반영하지 않는다 — 고아
    // 칸이 곧 그것을 되돌리는 컨트롤이라, 숨긴 순간 0 이 되면 돌아올 길이 사라진다.
    // 커서는 "이 읽기에 무엇이 존재하는가"이고 unlinked 는 "그중 무엇을 그리는가"다.
    drawMetrics(nodes);
    // 초점 마크(닫힌 층의 단일 초점색)는 이제 **지금 짚고 있는 노드**가 든다. 예전엔
    // L2 씨앗이 그 자리였는데 층이 사라졌고, 스코프를 넓혀 데려온 노드가 어느 점인지
    // 말해 주는 일은 남아야 한다 — 카메라만 옮기면 "어디로 갔는지"가 안 남는다.
    // ⚠ **전량을 싣는다**(2026-09-10). 여기서 시간을 걸러 내면 커서를 밀 때마다
    //    장면을 다시 지어야 한다 — 아래 `applyTimeCut` 이 가시성으로 대신 한다.
    //    `filterUnlinked` 는 전량 기준이 오히려 옳다: 커서가 이웃을 잠시 가렸다고
    //    그 노드가 고아가 되는 것은 아니다.
    {
      // 그래프 보기는 나침반 하나다. 받은 전량을 그대로 넘기고, 가운데·펼침·기간·종류·고정은 나침반이 다룬다.
      // 그 손잡이들(길·찾기·기간·종류·고정·읽는 법)은 LNB 의 칸으로 선다(렌즈 패널은 쓰지 않는다).
      const chain = domains.length === 1 ? domains[0].split("/").map((_, i, a) => `topic:${a.slice(0, i + 1).join("/")}`) : undefined;
      // LNB = 문서 트리와 같은 그림의 노드 트리(lnb.ts). 시간 · 범례 = 머리 뒤 묶음(문서 | 그래프 왼쪽) 단추가 여는 창.
      let ready = false;
      const restore = compassKeep; compassKeep = null;
      compass = mountCompass(canvas, compassDataFrom(nodes, curEdges), { startPath: chain, restore,
        store: spaceKey(),
        onTree: (o) => repaintLnbGraph(o),
        onOpen: (id) => openFromCompass(id),
        onChange: () => { if (ready && compass) setLnbGraphActive(compass.centre()); },
        onDestroy: () => unbindGraph() });
      const cmp = compass;
      const unbindGraph = bindSurface({
        lnbGraphSource: { groups: () => cmp.tree(), open: (id) => cmp.open(id), mark: (k) => cmp.mark(k), dock: cmp.side },
        topbarTools: cmp.tools });
      ready = true;
      setLnbGraphActive(cmp.centre());
      unbindSearch = bindSurface({ searchFilter: (q) => compass?.filter(q) });
      if (!compassDisposeWrapped) {
        const prevDispose = disposeView; compassDisposeWrapped = true;
        disposeView = () => { prevDispose?.(); unbindSearch?.(); if (compass) { compass.destroy(); compass = null; } };
      }
      markDisplayed(domains);
      return;
    }
  }


  /** 노드의 화면 좌표(카드를 그 옆에 띄우기 위한 것). 렌더러마다 좌표계가 달라
   *  구할 수 없으면 캔버스 우상단 근처로 강등한다 — 카드가 안 뜨는 것보다 낫다. */
  /** 부유 카드의 집 — 지도 카드의 좌상단에서 한 칸 들어온 자리.
   *  뷰포트 상수가 아니라 **실측 rect** 에서 뽑는다: 왼쪽 열이 접히거나 창이 바뀌면
   *  지도 카드의 위치도 바뀌고, 상수로 두면 그때 카드가 크롬 위에 얹힌다. */
  function homeCardPos(): { x: number; y: number } {
    const r = (el.querySelector("#view-canvas") ?? el).getBoundingClientRect();
    const step = parseFloat(getComputedStyle(document.documentElement)
      .getPropertyValue("--shell-gap")) * 16 || 8;
    return { x: Math.round(r.left + step * 2), y: Math.round(r.top + step * 2) };
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
   *  끝에는 떠 있는 카드가 열린다(`card=false` 면 열지 않는다). 착지한 노드를 돌려준다
   *  (없는 노드면 null). */
  let focusRevision = 0;
  const detailHistory: N3[] = [];
  async function focusNode(id: string, domain?: string, card = true, keepHistory = false): Promise<N3 | null> {
    const revision = ++focusRevision;
    // 주제를 밖에서 알려 주면 먼저 기억한다 — 합계 뷰는 개념 마크가 없어 그래프
    // 노드로 되짚을 수 없고(가운데 열이 주제다), 트리는 그 값을 이미 안다.
    // 노드 id 는 주제를 모를 때도 적어 둔다: 다시 그릴 때 그 마크를 초점색으로
    // 그릴 근거가 이것뿐이다(예전엔 L2 씨앗이 그 일을 했다).
    lnbFocus = domain ? { domain, nodeId: id } : { ...lnbFocus, nodeId: id };
    {
      const at = trail[trail.length - 1];
      const already = domain
        ? at.kind === "l1" && at.domains.length === 1 && at.domains[0] === domain
        : at.kind === "l0";
      if (!already) {
        trail.length = 1;                       // 바닥은 언제나 l0
        if (domain) trail.push({ kind: "l1", domains: [domain] });
        await show(trail[trail.length - 1]);
        if (revision !== focusRevision || myGen !== liveGen) return null;
      }
    }
    const raw = curNodes.find((n) => n.id === id);
    // 전량을 그린 화면에도 없으면 그 노드는 **없는 것**이다(오래된 딥링크가 이 경우다).
    // 빈 상세를 열어 있는 척하지 않는다 — 화면은 전체 지도로 남는다.
    if (!raw && trail[trail.length - 1].kind === "l0" && !keepHistory && !id.startsWith("domain:")) {
      lnbFocus = {}; setLnbActive({}); return null;
    }
    // 못 찾았지만 스코프가 좁은 경우(평면 캡에 잘렸거나 렌즈 밖)는 최소 정보로 연다 —
    // 제목은 곧 node-context 가 채운다.
    const nd = raw ? toN3([raw])[0]
      : ({ id, label: id.replace(/^[a-z]+:/, ""), domain: domain || "", degree: 0 } as N3);
    // 레일은 열지 않는다 — 관계 줄·트리·검색이 전부 여기로 오는데, 그때마다 문서
    // 원문까지 딸려 열리면 지도를 보는 일이 계속 끊긴다. 레일을 여는 것은 카드의
    // "Details and source" 와 컨텍스트 메뉴의 "열기" 둘뿐이다(발주자 지시 2026-08-27).
    // (LNB 는 예외다 — `openFromNav` 가 이 뒤에 옆 칸을 연다.)
    focusOnMap(nd, card, keepHistory);
    lnbFocus = { domain: nd.domain, nodeId: id };
    setLnbActive(lnbFocus);
    return nd;
  }

  /** LNB 에서 고른 것 — 노드·문서·Agent 역할. 지도에서 고른 것과 같이 그 노드에 초점을 걸고
   *  떠 있는 카드를 열며, 옆 칸(문서 열의 좁은 크기)에 문서나 노드 상세를 연다. 지도를 덮는
   *  넓은 열로 열지 않는다: 그래프에서 고른 것은 그래프 위에서 보여야 한다.
   *  근거: [[decision:lnb-picks-in-the-graph-open-the-floating-card-and-the-side-pane-2026-10-28]] */
  async function openFromNav(id: string, domain?: string, docId?: string) {
    rail.full = false;
    const nd = id ? await focusNode(id, domain, true) : null;
    if (myGen !== liveGen) return;
    if (docId) await openReader(docId, id || undefined);
    else if (nd) railFor(nd);
  }

  /** 방금 만든(또는 고친) 노드를 지도에 세우고 그 자리로 간다(authoring 의 `reveal`).
   *
   *  `focusNode` 로는 안 된다: 그 노드가 지금 스코프에 속하면 다시 그리지 않으므로 방금
   *  만든 노드는 끝내 지도에 없다. 그래서 **늘 다시 받는다** — 스코프 안이면 카메라를 잇고,
   *  밖이면 그 주제로 넓힌다. 문서 열이 열려 있으면 닫지 않는다(거기서 쓰던 중이다). */
  async function revealNode(id: string, domain?: string, card = true, label?: string) {
    const lv = trail[trail.length - 1];
    const inScope = lv.kind === "l0"
      || (!!domain && lv.domains.some((d) => domain === d || domain.startsWith(d + "/")));
    if (!inScope) {
      trail.length = 1;
      if (domain) trail.push({ kind: "l1", domains: [domain] });
    }
    const paneOpen = !detail.hidden;
    // 열이 바로 이 노드의 상세를 보이고 있으면(잇기·기본 정보 저장 뒤) 새 값으로 다시 그린다.
    const paneShowsIt = paneOpen && !!detail.querySelector("#node-stats") && lastNode?.id === id
      ? lastNode : null;
    await show(trail[trail.length - 1], { keepPanel: true, keepCamera: inScope, keepDetail: paneOpen });
    if (myGen !== liveGen) return;
    const raw = curNodes.find((n) => n.id === id);
    if (paneShowsIt) railFor(raw ? toN3([raw])[0] : paneShowsIt);
    if (!raw) {
      // 지도에 자리가 없다(연결 없는 기록·결정은 전체 지도가 그리지 않는다). 이름을 알면
      // 노드 상세로 대신 연다 — 만든 것이 어디에도 안 보이면 만든 것 같지 않다.
      if (label && card && !paneOpen) railFor({ id, label, domain: domain || "", degree: 0 } as N3);
      return;
    }
    focusOnMap(toN3([raw])[0], card && !paneOpen);
    lnbFocus = { domain: raw.domain, nodeId: id };
    setLnbActive(lnbFocus);
  }

  /** 만든 노드를 문서 열의 노드 상세로 연다(컴포저의 Ctrl/Cmd+Enter · 기본 정보 저장 뒤). */
  async function openNodePane(id: string, domain?: string, label?: string) {
    await revealNode(id, domain, false);
    if (myGen !== liveGen) return;
    const raw = curNodes.find((n) => n.id === id);
    railFor(raw ? toN3([raw])[0] : ({ id, label: label || id.replace(/^[a-z]+:/, ""),
                                      domain: domain || "", degree: 0 } as N3));
  }

  /** 흐름 B — 오른쪽 문서 열에 새 문서. 첫 저장이 미러 노드를 만들면 그 노드를 지도에 세운다
   *  (열은 그대로 두고). 트리는 여기서 **명시적으로** 새로 읽는다 — 저절로 새로워지는 갱신은
   *  문서 열이 열린 동안 쉬므로(`live.ts` busy) 기다리면 방금 만든 것이 안 보인다. */
  async function openWriter(topic?: string) {
    // 열려 있던 편집기가 **다 닫힌 뒤에** 연다 — 만들어지기 전에 닫히는 새 문서는 그때 초안을
    // 넘기고, 새 문서는 그 초안을 읽는다(authoring.mountDocWriter). 순서가 뒤집히면 초안이 엇갈린다.
    await disposeEditorsIn(detail);
    if (myGen !== liveGen) return;
    openRail();
    detail.innerHTML = `${railHeadHTML(COPY.ui.viewx.edNew)}<div id="wr-host"></div>`;
    wireRailHead();
    const { mountDocWriter } = await import("../authoring");
    const host = detail.querySelector<HTMLElement>("#wr-host");
    if (!host) return;
    await mountDocWriter(host, {
      topic,
      onCreated: (docId, tp) => {
        void refreshLnb();
        void revealNode(`concept:doc.${docId}`, tp || undefined);
      },
      onTopicChanged: (tp, docId) => {
        void refreshLnb();
        void revealNode(`concept:doc.${docId}`, tp);
      },
    });
  }

  /** 노드 맥락(`/node-context`) 한 번만 — 레일과 카드가 같은 노드에 같은 요청을 각자
   *  보내고 있었다(실측 2026-08-17: 클릭 한 번에 두 번, 63ms·100ms). 마지막 노드의
   *  약속을 기억해 둘이 나눠 쓴다. 층이 바뀌거나(show) 그래프를 고치면(삭제·연결)
   *  버린다 — 오래 들고 있으면 "방금 이은 연결이 안 보인다"가 된다. */
  /** 레일의 ask 줄에 수치를 채우는 손 — node-context 가 오면 부제가 산다.
   *  카드는 자기 응답을 직접 들고 있지만 레일은 `loadNodeSource` 가 따로 받으므로,
   *  그 자리에서 부를 수 있도록 여기 둔다. 레일이 닫히면 다음 열기가 덮어쓴다. */
  let railAskCounts: ((deg: number, docs: number) => void) | null = null;

  let ctxCache: { id: string; scope: string; p: ReturnType<typeof api.nodeContext> } | null = null;
  const topicDrafts = new Map<string, TopicDraft>();
  const nodeContextOnce = (id: string) => {
    const scope = viewScope().key;
    if (ctxCache?.id !== id || ctxCache.scope !== scope)
      // 근거 문장까지 싣는다 — 넓은 열의 항목 페이지가 그것을 인용한다(카드는 불리언만 읽는다).
      ctxCache = { id, scope, p: api.nodeContext(cfg.owner, id, 4, { team: cfg.team || null }, 0, true) };
    return ctxCache.p;
  };

  async function followDetail(from: N3, id: string, domain: string | undefined, pane: boolean) {
    if (from.id === id) return;
    if (detailHistory.at(-1)?.id !== from.id) detailHistory.push(from);
    const nd = await focusNode(id, domain, !pane, true);
    if (pane && nd && myGen === liveGen) railFor(nd);
  }
  async function backDetail(pane: boolean) {
    const previous = detailHistory.pop();
    if (!previous) return;
    const nd = await focusNode(previous.id, previous.domain, !pane, true);
    if (pane && nd && myGen === liveGen) railFor(nd);
  }
  function wireDetailBack(scope: HTMLElement, pane: boolean) {
    scope.querySelector("[data-detail-back]")?.addEventListener("click", () => void backDetail(pane));
  }
  async function loadTopicDetail(box: HTMLElement, nd: N3, pane: boolean, offset = 0) {
    const revision = rail.revision;
    const scopeKey = viewScope().key;
    const current = () => box.isConnected && myGen === liveGen && focusId === nd.id
      && viewScope().key === scopeKey && (!pane || (revision === rail.revision && !detail.hidden));
    const scope = { team: cfg.team || null };
    const requestOwner = cfg.owner;
    box.innerHTML = `<p class="card-note">${loaderInline(COPY.ui.viewx.detailLoading)}</p>`;
    const fail = (state: DetailState) => {
      box.innerHTML = detailErrorHTML(state);
      box.querySelector("[data-detail-retry]")?.addEventListener("click", () => {
        ctxCache = null; void loadTopicDetail(box, nd, pane, offset);
      });
    };
    try {
      const res = await (offset ? api.nodeContext(cfg.owner, nd.id, 4, scope, offset) : nodeContextOnce(nd.id));
      if (!current() || scope.team !== (cfg.team || null)) return;
      const state = detailState(res);
      if (state !== "ready") { fail(state); return; }
      if (!res.topic) { fail("unsupported"); return; }
      let canEdit = !scope.team;
      if (scope.team) {
        const teams = await teamsApi.list({ throwOnError: true }).catch(() => []);
        canEdit = teams.some(team => team.id === scope.team && ["editor", "admin"].includes(team.role));
      }
      if (!current()) return;
      box.innerHTML = topicDetailHTML(res.topic, COPY.ui.viewx, esc);
      bindTopicDescription({
        root: box.querySelector<HTMLElement>("[data-topic-description]")!,
        description: res.topic.description, copy: COPY.ui.viewx, esc,
        drafts: topicDrafts, key: JSON.stringify([scopeKey, nd.id]), canEdit, current,
        save: (text, hash) => api.topicDescription(requestOwner, nd.id, text, hash, scope),
        reload: () => { ctxCache = null; void loadTopicDetail(box, nd, pane, offset); },
      });
      box.querySelectorAll<HTMLElement>("[data-topic-go]").forEach(b => b.addEventListener("click", () =>
        void followDetail(nd, b.dataset.topicGo!, b.dataset.dom || undefined, pane)));
      box.querySelectorAll<HTMLElement>("[data-topic-more]").forEach(b => b.addEventListener("click", () =>
        void loadTopicDetail(box, nd, pane, Number(b.dataset.topicMore))));
    } catch (error) { if (current()) fail(detailFailure(error)); }
  }

  function openTopicCard(nd: N3) {
    const home = homeCardPos(), V = COPY.ui.viewx;
    const card = openFloatPanel({ title: nd.label, x: home.x, y: home.y, closeLabel: V.railClose, variant: "float-card--tall" });
    // 토픽 본문은 `.card-rows` 밖에 둔다. 그 줄들은 음수 여백으로 카드 안쪽 여백을 지우므로
    // 안에 두면 글이 카드 테두리에 붙는다.
    card.body.innerHTML = `<div class="card-rows">
      ${detailHistory.length ? `<button class="btn" type="button" data-detail-back>${esc(V.readerBack)}</button>` : ""}
      <button class="card-row" type="button" data-topic-open>${esc(V.topicDetail)}${chev("right")}</button></div>
      <div class="card-topic-body" data-topic-body></div>`;
    wireDetailBack(card.body, false);
    card.body.querySelector("[data-topic-open]")?.addEventListener("click", () => railFor(nd));
    void loadTopicDetail(card.body.querySelector<HTMLElement>("[data-topic-body]")!, nd, false);
  }

  // 지도 위 노드 카드와 연결 목록은 `view-cards.ts` 가 맡는다. 넓은 열의 연결 절(`drawPaneLinks`)도 같은 목록을 그린다.
  const { openRelsCard, relsHTML, wireRels } = createCards(host);

  /** 지도에서 노드를 고른 것 — **레일은 열지 않는다.**
   *
   *  여태 클릭 한 번이 다섯 가지를 했다(카메라 비행 · 주소 · 원뿔 · 레일 · 카드).
   *  고르는 것과 읽는 것은 다른 일이고, 레일은 문서 원문까지 끌고 오므로 지나가며
   *  누른 노드마다 그것이 열리면 지도를 보는 일이 계속 끊긴다. 이제 클릭은 여기까지고,
   *  레일은 카드의 버튼이 연다. 나머지 진입점(컨텍스트 메뉴 "열기" · 트리 · 검색 ·
   *  드릴다운)은 그대로 `openDetail` 을 부르므로 거기서는 아무것도 안 달라진다. */
  function focusOnMap(nd: N3, card = true, keepHistory = false) {
    ++focusRevision;
    if (!keepHistory) detailHistory.length = 0;
    // 잇기(흐름 F)가 대상을 고르는 중이면 이 초점은 그 대상이다 — 지도 클릭·검색 착지가
    // 전부 이 깔때기로 오므로 여기 한 곳에서 가로챈다.
    if (pickTarget) { const f = pickTarget; pickTarget = null; f(nd.id, nd.label); return; }
    lastNode = nd;   // Reader 뒤로가기 복귀 지점
    // 주소가 이 노드를 싣는다 — 주소창에서 복사해 건네는 것이 "노드 하나"라는 성질은
    // L2 가 스코프였을 때부터의 것이고, 층이 사라져도 남는다.
    focusId = nd.id; syncHash();
    // 카드를 띄우지 않는 초점이면 앞서 떠 있던 카드를 거둔다(다른 노드의 카드가 남지 않게).
    if (card) void openRelsCard(nd);
    else closeFloatPanel();
  }


  /** 나침반의 가운데를 한 번 더 누르면: 그 노드(또는 주제)의 상세를 옆 레일에 연다. 나침반 자리는 그대로다.
   *  카드는 띄우지 않는다(가운데가 이미 그 노드다). 주제는 `domain:` 상세로 연다. */
  function openFromCompass(id: string) {
    const nd: N3 = id.startsWith("topic:")
      ? ({ id: `domain:${id.slice(6)}`, label: id.slice(6).split("/").pop() || id.slice(6), domain: id.slice(6), degree: 0 } as N3)
      : (() => { const raw = curNodes.find((n) => n.id === id);
          return raw ? toN3([raw])[0] : ({ id, label: id.replace(/^[a-z]+:/, ""), domain: "", degree: 0 } as N3); })();
    focusOnMap(nd, false);
    railFor(nd);
  }

  /** 지도 밖 진입점이 쓰는 것 — 고르고(카드·원뿔·주소) **레일까지 연다**. */
  function openDetail(nd: N3) {
    focusOnMap(nd);
    railFor(nd);
  }

  /** 레일만 — 카드는 손대지 않는다. 카드의 "정보 열기"가 이걸 부르는데, 거기서
   *  `openDetail` 을 부르면 카드가 자기를 다시 그려 접어 둔 목록이 도로 펴진다. */
  function railFor(nd: N3) {
    openRail();
    if (nd.id.startsWith("domain:")) {
      railAskCounts = null;
      detail.innerHTML = `${railHeadHTML(COPY.ui.viewx.topicDetail)}
        ${detailHistory.length ? `<button class="btn" type="button" data-detail-back>${esc(COPY.ui.viewx.readerBack)}</button>` : ""}
        <h2>${esc(nd.label)}</h2><div data-topic-body></div>`;
      wireRailHead(); wireDetailBack(detail, true);
      void loadTopicDetail(detail.querySelector<HTMLElement>("[data-topic-body]")!, nd, true);
      return;
    }
    // 수치는 여기서 세지 않는다 — loadNodeSource 가 node-context 응답(links)으로
    // 채운다. 여태 이 자리에서 `curEdges`(마지막으로 그린 그래프의 잔상)를 세고
    // 드릴다운 경로에서는 degree 를 0 으로 박아, 같은 노드가 동선마다 다른 숫자를
    // 말하고 "연결 0개 · → 3 나감" 같은 자기모순까지 냈다.
    // 속성(props)과 연결은 node-context 가 오면 loadNodeSource 가 채운다. 연결은 넓은
    // 열에서만 싣는다 — 옆 칸일 때는 지도의 카드가 그 일을 한다(D70). 넓게 열린 것은
    // LNB 에서 온 것이고 그때는 카드가 없다.
    detail.innerHTML = `
      ${railHeadHTML(COPY.ui.viewx.railTitle)}
      ${detailHistory.length ? `<button class="btn" type="button" data-detail-back>${esc(COPY.ui.viewx.readerBack)}</button>` : ""}
      ${askRowHTML()}
      <h2>${esc(isAgentRoleId(nd.id) ? stripRolePrefix(nd.label) : nd.label)}</h2>
      <div id="node-stats"><p class="hint">${loaderInline(COPY.ui.viewx.sourceLoading)}</p></div>
      <p class="rail-acts">
        <button class="btn btn-primary" id="nd-share">${COPY.ui.viewx.shareCta}</button>
        <button class="btn" id="nd-connect" data-tip="${esc(COPY.author.connectTip)}">${
          esc(COPY.author.connect)}</button>
        <button class="btn btn-quiet" id="nd-del">${COPY.ui.viewx.del}</button>
      </p>
      <div id="node-item"></div>
      <div id="node-basics"></div>
      <div id="node-props"></div>
      <div id="node-links"></div>
      <div id="node-source"></div>
      <div id="ties-suggest"></div>`;
    wireRailHead();
    wireDetailBack(detail, true);
    // 카드와 **같은 줄**이다. 수치는 loadNodeSource 가 node-context 를 받고 채운다.
    railAskCounts = wireAsk(detail, () => !detail.hidden,
      (deg, docs) => nodeAskItems(nd, deg, docs));
    // 공유는 "스코프의 출구" — 이 노드의 ego 를 기본 범위로 시트를 연다(D56 상단 바
    // 버튼과 같은 표면). 레일에서도 나갈 수 있어야 진입이 제스처 하나에 갇히지 않는다.
    detail.querySelector("#nd-share")!.addEventListener("click", () =>
      openShareSheet({ seed: nd.id, depth: SHARE_DEPTH }));
    // 이름은 열 제목에서 읽는다 — 지도에 자리가 없는 노드는 제목이 늦게 온다(loadNodeSource).
    detail.querySelector("#nd-connect")!.addEventListener("click", () => void import("../connect")
      .then((m) => m.startConnect({ id: nd.id,
                                    title: detail.querySelector("h2")?.textContent || nd.label })));
    // S1 — 노드 삭제(되돌릴 수 없음). 성공하면 상세를 닫고 현재 층을 다시 그려
    // 지도에서도 사라지게 한다(응답 후 재조회 — 엣지까지 지워지므로 부분 갱신 불가).
    detail.querySelector("#nd-del")!.addEventListener("click", async () => {
      // 확인 창과 함께 미리본다. 확인한 뒤 바뀌었으면(`preview_conflict`) 지우지 않고 실패로 말한다.
      const guard = api.previewDeleteNode(cfg.owner, nd.id);
      if (!await confirmDestructive(COPY.ui.viewx.delNodeAsk(nd.label),
            COPY.ui.viewx.delConfirm, COPY.ui.viewx.delCancel)) return;
      try {
        const res = await api.deleteNode(cfg.owner, nd.id, await guard);
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

  // 관계 상세(연결 줄의 관계 이름이 연다)와 지표 바의 드릴다운은 `view-edge.ts` 가 맡는다.
  const { openEdgeDetail, openDrilldown } = createEdgeDetail(host);

  // 속성 절(그리기 · 편집 · 저장)은 `view-props-card.ts` 가 맡는다. 다시 읽기는 노드 맥락 메모를 비우고 이 열을 새로 그린다.
  const { drawNodeProps } = createPropsCard(host);

  /** 상세 레일의 수치·관계 목록(T3·T4 · D45-V) — node-context 응답 하나가 정본이다.
   *  구 사이드카는 `links` 키 자체가 없다: 그때만 그래프 잔상(curEdges)으로 강등하고
   *  캡션도 정직하게 바꾼다(수치의 출처를 화면이 스스로 밝힌다). */

  /** 넓은 열의 연결 절 — 카드가 없는 자리라 관계를 여기 싣는다. 모양은 카드와 같다. */
  /** 항목 페이지(A2) — 넓은 열에서만. 효력 시작일(비었으면 노드가 말한 날로 한 번에 채우기),
   *  대체 관계, 그래프가 말하는 것(저장된 문장 인용), 어떻게 정해졌나(목록 · 지도).
   *  문장을 만들지 않는다: 근거는 `item-page.ts` 머리 주석. */
  function drawItemPage(nd: N3, res: NodeContext, canEdit: boolean) {
    const box = detail.querySelector<HTMLElement>("#node-item");
    if (!box) return;
    const V = COPY.ui.viewx;
    const kind = res.node_kind || "";
    const links = (res.links?.items || []) as ItemLink[];
    const t = res.temporal || {};
    const validFrom = t.valid_from || "";
    const fix = canEdit && (kind === "decision" || kind === "record") ? statedStart(validFrom, res.props) : "";
    const steps = decisionSteps({ id: nd.id, title: res.title || nd.label, validFrom,
      validUntil: t.valid_until, createdAt: t.created_at }, links);
    const replacedBy = steps.filter((s) => s.rel === "replaced_by");
    const replaces = steps.filter((s) => s.rel === "replaces");
    const row = (k: string, v: string) => `<div class="stat-row"><span class="hint">${esc(k)}</span>${v}</div>`;
    const names = (ss: Step[]) => `<b>${ss.map((s) => esc(s.title)).join(", ")}</b>`;
    const facts = (kind === "decision" || kind === "record" ? row(V.itemInForce, validFrom
        ? `<b>${esc(validFrom)}</b>`
        : `<span class="item-fix"><span class="hint">${esc(V.itemNotSet)}</span>${fix
          ? `<button class="btn" type="button" data-item-fix="${esc(fix)}">${esc(V.itemSetOn(fix))}</button>` : ""}</span>`) : "")
      + (t.valid_until ? row(V.itemEnded, `<b>${esc(String(t.valid_until))}</b>`) : "")
      + (replacedBy.length ? row(V.itemReplacedBy, names(replacedBy)) : "")
      + (replaces.length ? row(V.itemReplaces, names(replaces)) : "");
    const says = sayings(res.props, links);
    const relName = (type: string, dir: string) => COPY.ui.ask.relTag[`${type}:${dir}`] || type.toLowerCase();
    const sayHTML = says.length ? says.map((s) => `<blockquote class="item-say">
        <p>${esc(s.text)}</p>
        <p class="hint">${s.from === "prop" ? esc(V.itemSayProp(COPY.author.kinds[kind] || kind, s.key))
          : `${esc(V.itemSayRel(relName(s.type, s.dir)))} <button type="button" class="link-btn" data-item-go="${
            esc(s.id)}">${esc(s.title)}</button>`}</p></blockquote>`).join("")
      : `<p class="hint">${esc(V.itemSaysNone)}</p>`;
    const status = (s: Step) => s.rel === "self" ? V.itemStepSelf : s.ended ? V.itemStepEnded(s.ended)
      : s.rel === "replaced_by" ? V.itemReplacedBy : s.rel === "replaces" ? V.itemReplaces : V.itemStepRelated;
    const list = steps.map((s) => `<div class="item-step${s.rel === "self" ? " is-self" : ""}">
        <span class="hint">${esc(s.date || V.itemNoDate)}</span>
        ${s.rel === "self" ? `<b>${esc(s.title)}</b>` : `<button type="button" class="link-btn" data-item-go="${
          esc(s.id)}">${esc(s.title)}</button>`}
        <span class="hint">${esc(status(s))}</span></div>`).join("");
    // 지도 = 같은 걸음을 시간 순서대로 왼쪽에서 오른쪽으로(time-branches 와 같은 읽기 방향).
    const map = `<div class="item-map">${steps.map((s, i) => `${i ? `<span class="item-arrow" aria-hidden="true">→</span>` : ""}
        <div class="item-cell${s.rel === "self" ? " is-self" : ""}"><span class="hint">${esc(status(s))}</span>
        <b>${esc(s.title)}</b><span class="hint">${esc(s.date || V.itemNoDate)}</span></div>`).join("")}</div>`;
    const decided = steps.length > 1;
    box.innerHTML = `${facts ? `<div class="item-facts">${facts}</div>` : ""}
      <section class="item-sec"><h3 class="hint rail-sec">${esc(V.itemSays)}</h3>${sayHTML}</section>
      ${decided ? `<section class="item-sec"><div class="item-sec-head"><h3 class="hint rail-sec">${esc(V.itemDecided)}</h3>
        <span class="seg" role="radiogroup" aria-label="${esc(V.itemDecided)}">
          <button type="button" role="radio" aria-checked="true" data-item-view="list">${esc(V.itemList)}</button>
          <button type="button" role="radio" aria-checked="false" data-item-view="map">${esc(V.itemMap)}</button>
        </span></div>
        <div data-item-pane="list">${list}</div><div data-item-pane="map" hidden>${map}</div></section>` : ""}`;
    box.querySelectorAll<HTMLButtonElement>("[data-item-view]").forEach((b) => b.addEventListener("click", () => {
      box.querySelectorAll<HTMLButtonElement>("[data-item-view]").forEach((o) =>
        o.setAttribute("aria-checked", String(o === b)));
      box.querySelectorAll<HTMLElement>("[data-item-pane]").forEach((p) => {
        p.hidden = p.dataset.itemPane !== b.dataset.itemView; });
    }));
    box.querySelectorAll<HTMLElement>("[data-item-go]").forEach((b) => b.addEventListener("click", () =>
      void followDetail(nd, b.dataset.itemGo!, undefined, true)));
    box.querySelector<HTMLButtonElement>("[data-item-fix]")?.addEventListener("click", async (ev) => {
      const btn = ev.currentTarget as HTMLButtonElement;
      const day = btn.dataset.itemFix!;
      btn.disabled = true;
      try {
        // 결정의 구간은 속성에, 기록의 구간은 열에 있다(GRAPH.md). 판 확인은 서버가 한다.
        const node = kind === "decision"
          ? { id: nd.id, type: kind, props_patch: { valid_from: day } }
          : { id: nd.id, type: kind, valid_from: day };
        const out: any = await api.editNode(node, res.rev ?? 0);
        if (isWriteConflict(out) || out?.executed === false) throw new Error(out?.error || "conflict");
        ctxCache = null;
        await loadNodeSource(nd);
      } catch {
        btn.disabled = false;
        btn.insertAdjacentHTML("afterend", `<span class="hint">${esc(V.itemFixFailed)}</span>`);
      }
    });
  }

  function drawPaneLinks(nd: N3, links: NodeLinks) {
    const box = detail.querySelector<HTMLElement>("#node-links");
    if (!box) return;
    box.innerHTML = `
      <section class="pane-links">
        <h3 class="hint rail-sec">${esc(COPY.ui.viewx.statLinks)}</h3>
        ${relsHTML(links)}
      </section>`;
    box.querySelector(".link-more")?.remove();   // 이미 넓은 자리라 "더 보기"가 갈 곳이 없다
    wireRels(box, nd, links);
  }

  function drawNodeStats(nd: N3, links?: NodeLinks, kind?: string) {
    const box = detail.querySelector<HTMLElement>("#node-stats");
    if (!box) return;
    if (!links?.executed) { box.innerHTML = detailErrorHTML("failed"); return; }
    const { out, in: inn, attached: att, degree } = links;
    const row = (k: string, v: string) =>
      `<div class="stat-row"><span class="hint">${k}</span><b>${v}</b></div>`;
    // 무엇인가(종류)를 먼저 말한다. 근거 절이 발췌 글을 싣지 않으므로 노드를 설명하는 첫 줄이다.
    const kindName = kind ? COPY.author.kinds[kind] : "";
    box.innerHTML =
      (kindName ? row(COPY.ui.viewx.statKind, esc(kindName)) : "")
      + row(COPY.ui.viewx.statTopic, esc(nd.domain || COPY.ui.common.none))
      + row(COPY.ui.viewx.statLinks, String(degree))
      + (out + inn > 0 ? row(COPY.ui.viewx.statDirection, COPY.ui.viewx.statDir(out, inn)) : "")
      + (att > 0 ? row(COPY.ui.viewx.statAttached, String(att)) : "")
      + (nd.gateway ? `<p class="hint">${COPY.ui.viewx.gateway.replace(/^ · /, "")}</p>` : "")
      + `<p class="hint stat-note">${COPY.ui.viewx.statsFrom}</p>`;
  }

  // 근거 절(`loadNodeSource`) · 문서 리더(`openReader` · `openDocAt`) · 잇기 제안은 `view-reader.ts` 가 맡는다.
  // 근거 절은 노드 맥락 응답이 오면 위의 수치 · 속성 · 연결 · 항목 절을 그린다.
  const { loadNodeSource, openDocAt, openReader } = createReader(host,
    { drawNodeStats, drawNodeProps, drawPaneLinks, drawItemPage });

  const showL1 = (domains: string[]) => showGraphScope({ domains });

  /** 전환 피드백(Doherty) — 층 전환이 길면 1초 가까이 걸린다. 400ms 를 넘기는 동안
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

  /** show() 세대. 연달아 두 번 부르면 앞의 것이 fetch 에서 돌아왔을 때 이 값이 이미
   *  넘어가 있다 — fetchGraph 가 그걸 보고 `stale` 로 접는다(옛 응답이 새 그림을 덮던
   *  것, 그리고 옛 마운트가 새 캔버스 위에 한 번 더 붙던 것을 막는다). */
  /** 옛 표면을 부수고 새 표면을 받을 준비를 한다. 각 show* 가 데이터를 받은 **직후**,
   *  그리기 **직전**에 부른다. 빈 상태·오류 상자도 이 뒤에 그린다(옛 지도 위에 얹지 않게).
   *  캔버스를 투명하게 두고 나가면 revealSurface() 가 첫 그림을 페이드인한다. */
  function swapSurface() {
    displayedScene = undefined;
    if (compass) { unbindSearch?.(); compass.destroy(); compass = null; }
    canvas.innerHTML = "";
    canvas.style.opacity = "0";
  }
  /** swapSurface 뒤 첫 그림을 160ms 로 들여온다(불투명도만 — 다시 그리기는 하루 수십 번
   *  하는 일이라 이보다 길거나 움직이면 기다림이 된다). 감소 모션이면 바로 보인다. */
  function revealSurface() {
    if (canvas.style.opacity !== "0") return;      // swap 이 없었다(stale 등)
    canvas.style.opacity = "";
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    canvas.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 160, easing: "ease-out" });
  }
  let showSeq = 0;
  /** 스토어가 안 열렸고 저장 위치가 내 데이터베이스면, 빈 지도 대신 그 데이터베이스가 응답하지 않는다고
   *  말하고 다시 시도 · 저장 설정을 준다. 그 밖의 위치는 위의 일반 문장을 그대로 둔다. */
  async function showDatabaseDown(seq: number) {
    if (WEB) return;
    const conf = await storage.config().catch(() => null);
    const loc = conf?.location;
    if (seq !== showSeq || loc?.kind !== "database") return;
    const L = COPY.ui.settingsUi.db;
    setMsg(`<div class="empty" role="alert"><p><strong>${esc(L.downTitle)}</strong></p>
      <p>${esc(L.downBody(hostOf(loc.uri)))}</p>
      <p><button class="btn btn-primary" type="button" data-db-retry>${esc(L.downRetry)}</button>
        <button class="btn" type="button" data-db-settings>${esc(L.downSettings)}</button></p></div>`);
    msg.querySelector<HTMLButtonElement>("[data-db-retry]")?.addEventListener("click", async (e) => {
      (e.currentTarget as HTMLButtonElement).disabled = true;
      // 사이드카는 기동할 때 한 번 연다. 다시 띄운 뒤 화면을 새로 읽는다.
      await restartSidecar();
      location.reload();
    });
    msg.querySelector("[data-db-settings]")?.addEventListener("click", () => {
      void import("./settings").then((m) => m.openSettings("storage"));
    });
  }
  async function show(level: Level,
                      opts?: { keepPanel?: boolean; keepCamera?: boolean; keepDetail?: boolean }) {
    const seq = ++showSeq;
    loader.abort();
    setBusy(true, opts?.keepPanel ? 120 : 400);
    // 같은 범위를 다시 그리면 나침반이 보던 자리를 잇는다. 캡처는 파괴 **전에**.
    compassKeep = opts?.keepCamera && compass ? compass.snapshot() : null;
    ctxCache = null;                           // 층이 바뀌면 노드 맥락 메모도 버린다
    dropPick();
    // `keepDetail` — 방금 만든 것을 지도에 세우는 재그리기(revealNode)는 문서 열을 닫지
    // 않는다. 거기서 쓰고 있던 글이 첫 저장과 함께 사라지면 안 된다.
    if (!opts?.keepDetail) { ++rail.revision; detail.hidden = true; }
    closeFloatPanel();   // 층이 바뀌면 카드가 가리키던 노드도 화면에서 사라진다
    // 옛 표면은 **여기서 부수지 않는다.** 새 데이터가 도착해 그릴 준비가 됐을 때
    // swapSurface() 가 부수고 곧바로 새 표면을 세운다 — 그래야 fetch 동안 빈 상자가
    // 아니라 보던 지도가 남고, 새 지도가 그 위로 짧게 페이드인한다(2026-09-05 감사).
    drawMetrics(null);
    // 위치 표시는 좌측 트리 담당(D71 — 크럼 폐지). 지금 층의 주제를 넘겨 준다.
    setLnbActive(level.kind === "l1" ? { domains: level.domains } : {});
    // U4 — 인앱 이동도 주소를 남긴다. 그래야 새로고침해도 "지금 보는 곳"이 남고 공유도
    // 된다. replaceState 라
    // hashchange 가 안 떠 라우터는 무영향이다.
    //
    // 층이 바뀌면 열려 있던 상세는 닫힌다(바로 위에서 detail 을 숨긴다) — 주소도
    // 그 노드를 놓아야 한다. 안 그러면 아무것도 안 열린 화면이 `#view/trace/<id>` 를
    // 계속 주장한다.
    focusId = "";
    syncHash();
    refreshTopbar();
    try {
      // D69 — L0 도 이제 그냥 그래프다(스코프 없는 `/graph`). 주제 표는 좌측 트리로,
      // 주제 이름 변경도 그 트리의 우클릭으로 옮겼다.
      if (level.kind === "l0") await showGraphScope({});
      else await showL1(level.domains);
      // 노드로 데려가는 일(검색 착지·트리 클릭·딥링크)은 focusNode 가 진다 — 마운트가
      // 자리를 잡은 뒤 한 번 더 날리는 지연도 거기 있다. 여기서는 층만 그린다.
    } catch (e) {
      if (seq !== showSeq) return;
      // 위 빈 상태와 같은 상자 — 예외 경로만 다른 모양으로 남기면
      // "왜 어떤 실패는 붉은 띠고 어떤 실패는 빈 상자인가"라는 질문만 남는다.
      //
      // 단 **스토어를 못 읽는 경우만은 다른 말**을 한다(2026-08-16). 그 상태에서
      // 사이드카는 503 을 주고, 여기 "못 불러왔다"로 뭉개면 화면은 빈 지도와
      // 구분되지 않는다 — 사용자가 보는 것은 "내 지식이 사라졌다"이다. 사유
      // (락 충돌·손상)는 사람이 읽을 문장이 아니라서 접어 두고 열게 한다.
      const why = storeDownReason(e);
      if (import.meta.env.DEV) console.error("view show failed", e);
      if (why === null) {
        setMsg(readFailedBox());
      } else {
        setMsg(`<div class="empty"><p>${COPY.view.storeDown}</p>${
          why ? `<details><summary>${COPY.view.storeDownWhy}</summary><pre></pre></details>` : ""}</div>`);
        // 사유는 드라이버·OS 가 만든 문자열(경로 포함)이라 마크업으로 붙이지 않는다.
        const pre = msg.querySelector("pre");
        if (pre) pre.textContent = why;
        void showDatabaseDown(seq);
      }
    } finally {
      if (seq === showSeq) {
        setBusy(false); revealSurface();   // 새 세대가 돌고 있으면 그쪽이 끈다
      }
    }
  }


  trail.push({ kind: "l0" });
  // `#view/compare/a/b` 는 프레임 폐기(2026-08-02)로 사라진 딥링크다. 옛 주소를
  // 404 로 떨구지 않고 L0 로 받는다 — 북마크가 조용히 깨지는 것보다 낫다.
  // 제거 조건: 없음. 밖에 나간 북마크를 받는 갈래라 main.ts `ALIAS` 와 같이 무기한 둔다.
  //
  // `#view/trace/<id>` 는 **스코프가 아니라 포커스**다(씨앗 층은 폐지됐다).
  // 층은 전량으로 두고 마운트가 끝난 뒤 그 노드로 간다 — 전량은 반드시 그 노드를
  // 담으므로 딥링크가 빈 화면으로 끝나지 않는다. 철자는 영구 유지한다(별칭 규율).
  const sel = addr?.sel;
  // `#view/read/<id>[/<doc>]` 는 LNB 가 View 밖에서 쓰는 착지다 — trace 와 같되 옆 칸에
  // 문서나 노드 상세까지 연다(openFromNav). 착지한 뒤 주소는 syncHash 가 trace 로 쓴다.
  const landWide = readRoute;
  const landOn = readRoute ? (rawParts[2] || "") : sel?.kind === "node" ? sel.id : "";
  const landDoc = readRoute && rawParts[3] && rawParts[3] !== "as" ? rawParts[3] : "";
  // `#view/doc/<id>` — 그 문서를 리더로 연다(리더의 "어시스턴트에게 묻기 · 프롬프트
  // 복사"가 만드는 주소다). 옛 `#view/ids/<a,b>`(올가미 점등)는 전체로 받는다.
  const landReader = sel?.kind === "doc" ? sel.id : "";
  // 주제 여럿은 쉼표로 잇는다(`#view/a,b`) — 주제 이름에 쉼표가 없다는 관례가 전제고,
  // 백엔드의 `domains=a,b` 파라미터와 같은 문법이라 주소와 질의가 한 모양이다.
  // 옛 `#view/compare/…` 는 주제가 아니다(위 주석) — 전체로 받는다.
  if (sel?.kind === "domains" && !sel.domains.some((d) => d === "compare" || d.startsWith("compare/")))
    trail.push({ kind: "l1", domains: sel.domains });
  // D56 — 상단 바에 자기 동작을 건다. 공유 범위는 **지금 보고 있는 스코프**가
  // 기본값이다(정본 §5 "공유는 스코프의 출구"). 여태 진입이 올가미 제스처 하나뿐이라
  // 발견되지 않았고, 우클릭 메뉴는 아직 없다.
  bindSurface({ viewHooks: {
    share: () => {
      const lv = trail[trail.length - 1];
      // 노드 하나를 건네는 길은 그 노드의 우클릭 메뉴와 상세 레일이 든다(SHARE_DEPTH).
      // 상단 바는 **지금 보고 있는 스코프**를 건넨다.
      if (lv.kind === "l1") openShareSheet({ domain: lvDomain(lv) });
      else openShareSheet({});                      // L0 = 전체
    },
    openNode: (id, dom) => { if (compass) compass.open(id); else void focusNode(id, dom); },
    openDoc: (docId, nodeId, dom, chunkIndex) => void openDocAt(docId, nodeId, dom, chunkIndex),
    // 작업하는 자리에서 만들기(authoring.ts) — 만드는 흐름이 지도와 문서 열에 닿는 문.
    newDoc: (topic) => void openWriter(topic),
    reveal: (id, dom, label) => revealNode(id, dom, true, label),
    openNodePane: (id, dom, label) => openNodePane(id, dom, label),
    // 지금 보고 있는 자리의 주제: 짚은 노드의 주제 → 좁힌 주제 하나 → 없음.
    context: () => {
      const lv = trail[trail.length - 1];
      const scoped = lv.kind === "l1" && lv.domains.length === 1 ? lv.domains[0] : "";
      return { topic: lastNode?.domain || lnbFocus.domain || scoped, nodeId: lastNode?.id || "" };
    },
    pickNode: (cb) => { pickTarget = cb; },
  } });
  // 지도 빈 곳 우클릭 = 여기에 새로 만들기(흐름 A). 노드 우클릭은 렌더러가 자기 메뉴를
  // 먼저 연다 — 캡처로 받고 한 틱 뒤에 메뉴가 이미 떠 있으면 비켜선다(렌더러 둘에 빈 곳
  // 훅을 따로 뚫지 않는 방법이다).
  canvas.addEventListener("contextmenu", (ev) => {
    ev.preventDefault();
    window.setTimeout(() => {
      if (myGen !== liveGen || document.querySelector(".ctx-menu")) return;
      const lv = trail[trail.length - 1];
      const topic = lv.kind === "l1" && lv.domains.length === 1 ? lv.domains[0] : "";
      void import("../authoring").then((m) => openContextMenu(ev.clientX, ev.clientY, [
        { label: COPY.author.hereNode, run: () => void m.startNewNode({ topic }) },
        { label: COPY.author.hereDoc, run: () => m.startNewDoc(topic) },
      ]));
    }, 0);
  }, true);
  // 좌측 트리의 문서 클릭이 여기로 온다(D69·D70). 훅이 없으면 트리는 해시 착지로
  // 강등되므로, View 가 살아 있는 동안만 "리마운트 없는 이동"이 성립한다.
  // LNB 에서 고른 것은 카드와 옆 칸으로 연다(openFromNav).
  bindSurface({ lnbNavigator: (id, dom, docId) => void openFromNav(id, dom, docId) });
  // 주제 고르기 — **즉시 반영**이다(발주자 결정 2026-08-15). 고른 것과 보는 것이
  // 어긋나는 중간 상태를 두지 않는 대신, 고를 때마다 서버를 한 번 왕복한다.
  bindSurface({ lnbScopeClearer: () => false });
  bindSurface({ lnbTopicPicker: (path, add, set) => {
    const lv = trail[trail.length - 1];
    const cur = lv.kind === "l1" ? lv.domains : [];
    // `set` 은 토글이 아니라 "이 주제(들)로" 다(Agent 레일: 역할 주제 둘). 이미 그 주제거나
    // 그 안쪽(`agent/release`)으로 좁혀져 있으면 그대로 둔다. 넓히면 좁힌 것이 풀린다.
    const paths = Array.isArray(path) ? path : [path];
    const one = paths[0];
    // 이미 그 주제면 다시 읽지 않고 그 주제의 첫 자리로 돌아간다(Agent 를 다시 누른 손): 고른 노드의
    // 카드와 옆 칸을 닫는다.
    if (set && cur.length
        && cur.every((d) => paths.some((p) => d === p || d.startsWith(p + "/")))) {
      if (focusId || !detail.hidden || isFloatPanelOpen()) closeRail();
      return;
    }
    // 이미 고른 것을 다시 누르면 **뺀다** — 더하는 길만 있고 빼는 길이 없으면
    // 되돌리려고 홈까지 갔다 와야 한다.
    const next = set ? paths : add
      ? (cur.includes(one) ? cur.filter((d) => d !== one) : [...cur, one])
      : (cur.length === 1 && cur[0] === one ? [] : [one]);
    // 전부 빼면 그것은 L0(전체)다 — 빈 L1 은 "아무것도 안 보이는 주제 화면"이라
    // 상태로서 뜻이 없다.
    if (lv.kind === "l1") trail.pop();
    if (next.length) trail.push({ kind: "l1", domains: next });
    void show(trail[trail.length - 1]);
  } });


  // 저절로 새로워지는 갱신(VIN-129). 지금 층을 다시 받되 카메라와 렌즈 패널은 잇는다 —
  // 옵션 재마운트(`applyPrefs`)와 같은 길이라 "보던 곳을 잃는다"가 다시 생기지 않는다.
  // 재생 중이거나 카드·상세가 떠 있으면 "지금은 아니다"(`false`): 다시 그리기가 그것들을
  // 닫는다. 바뀐 것은 `live.ts` 가 기억했다가 손을 뗀 뒤 적용한다.
  bindSurface({ liveRefresher: async () => {
    if (myGen !== liveGen) return;
    // 잇기 대상을 고르는 중에도 기다린다 — 다시 그리기가 그 고르기를 푼다.
    if (isFloatPanelOpen() || !detail.hidden || pickTarget) return false;
    loader.reset();
    await show(trail[trail.length - 1], { keepPanel: true, keepCamera: true });
  } });

  await show(trail[trail.length - 1]);
  // View 밖에서 "새 문서"를 눌러 여기로 왔다(authoring.startNewDoc) — 첫 화면 뒤에 연다.
  const pendingDoc = takePendingNewDoc();
  if (pendingDoc) void openWriter(pendingDoc.topic);
  else if (landOn) await (landWide ? openFromNav(landOn, undefined, landDoc || undefined)
                                 : focusNode(landOn));
  else if (landReader) await openDocAt(landReader);
}
