import { api, cfg, WEB, teamsApi, isCommunitySpace, canWriteHere, viewScope, viewAccess, docMidWrite, spaceName, storeDownReason, messageOf, isWriteConflict, storage, restartSidecar, type GraphEdge, type GraphNode,
         type NodeLinks, type NodeContext } from "../api";
import { COPY } from "../copy";
import { docTitle } from "../doc-title";
import { absoluteViewUrl, formatViewUrl, parseViewUrl } from "../view-url";
import type { N3 } from "./view3d";
import { viewSessionCache } from "../view-session-cache";
import { createGraphLoader } from "../view-graph-loader";
import { retainedViews, restoreRetained } from "../view-retained";
import { mountCompass, compassDataFrom, type CompassHandle, type CompassState } from "./view-compass";
import { openShareSheet } from "../sheets";
import { sayings, statedStart, decisionSteps, type ItemLink, type Step } from "../item-page";
import { isHistorical, conflictIds } from "../node-validity";
import { tintMap } from "../view-tint";
import { esc, openContextMenu, confirmDestructive } from "../components";
import { hostOf } from "../storage-mode";
import { blocksSurfaceShortcuts } from "../keys";
import { disposeEditorsIn, docEditorOpen, renderMarkdown } from "../doc-editor";
import { hydrateRich } from "../rich-blocks";

import { RAIL_MIN, RAIL_MAX, loadPrefs, savePrefs, dayOf, localStamp,
         type ViewPrefs } from "../view-prefs";
import { setViewHooks, refreshTopbar, setSearchFilter, setTopbarLeadTools } from "../topbar";
import { setLnbActive, setLnbNavigator, setLnbTopicPicker, setLnbScopeClearer, setLnbGraphSource, repaintLnbGraph, setLnbGraphActive,
         refreshLnb, isAgentRoleId, stripRolePrefix } from "../lnb";
import { PROP_KINDS, propsEditable, rowsFrom, propsFrom, convertText, sameProps, propsPatchOf,
         type PropRow, type PropKind } from "../props-edit";
import { openFloatPanel, closeFloatPanel, isFloatPanelOpen } from "../float-panel";
import { icon } from "../icons";
import { setLiveRefresher } from "../live";
import { emptyInviteHtml } from "../empty-invite";
import { takePendingNewDoc } from "../authoring";
import { detailState, detailFailure, detailMessage, topicDetailHTML, type DetailState } from "../topic-detail";
import { bindTopicDescription, type TopicDraft } from "../topic-description";

/** Surface 3 — View (spec: [[vinc.spec.viewpoints]] · as-built [[vinc.reference.view-controls]]).
 *
 *  그래프는 2D 나침반 하나다(view-compass.ts). 이 표면은 데이터를 읽어 나침반에 넘기고, 문서 열 · 노드 카드 ·
 *  상세 레일 · 주소를 맡는다. "어디"는 Level(trail)이 쥔다. 3D 렌더러(view3d.ts)는 커뮤니티 그래프 페이지만 쓴다.
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

/** 노드 하나를 팩으로 건넬 때의 기본 반경. 진입점이 셋(우클릭 메뉴 · 상세 레일 ·
 *  상단 바)이라 값이 흩어져 있으면 어디서 보냈느냐에 따라 받는 쪽 그림이 달라진다. */
const SHARE_DEPTH = 2;


/** 복사 버튼 한 벌 — 레일·문서 카드·영역이 같은 처리를 쓴다.
 *
 *  복사가 막혀도 **오류로 키우지 않는다**: 주소는 어차피 주소창에 그대로 있고,
 *  클립보드 권한은 사용자 환경의 일이다. settings 의 MCP 주소 복사와 같은 태도다.
 *  버튼 라벨을 잠깐 바꾸는 것으로 끝낸다 — 알림을 띄우면 복사 한 번이 사건이 된다. */


/** 복사되는 값 — 한 마디 + 주소, **한 줄**로.
 *
 *  줄을 안 나눈다: 붙여넣는 자리가 대부분 한 줄짜리 입력이라 나누면 거기서 잘린다.
 *  `@` 도 안 붙인다 — Figma 가 붙이는 것은 그쪽 클라이언트에서 그 토큰이 첨부를 뜻해서고,
 *  여기서는 아무 뜻이 없으며 오히려 `vinc_graph(url=…)` 인자에 실려 주소를 깨뜨린다. */
function promptFor(url: string): string {
  return `${COPY.ui.viewx.promptLead} ${url}`;
}

function bindCopyLink(btn: Element | null, make: () => string): void {
  if (!btn) return;
  // span 에 role=button 을 달았으므로 Enter·Space 를 직접 받아야 한다 — 진짜 button
  // 이 공짜로 주던 것이고, 안 달면 키보드만 쓰는 사람에게는 이 기능이 없는 것과 같다.
  btn.addEventListener("keydown", (e) => {
    const k = (e as KeyboardEvent).key;
    if (k === "Enter" || k === " ") { e.preventDefault(); (btn as HTMLElement).click(); }
  });
  btn.addEventListener("click", (e) => {
    e.stopPropagation();          // 행의 다른 일로 새면 안 된다(레일에서는 노드 선택)
    const tip = COPY.ui.viewx;
    const el = btn as HTMLElement;
    const label = el.textContent || "";
    // **글귀를 그 자리에서 바꾼다.** 여태 되먹임이 툴팁(`data-tip`)뿐이었는데, 툴팁은
    // 이미 떠 있으면 갱신되지 않고 손을 뗐다 다시 올려야 보였다 — 눌렀는데 아무 일도
    // 안 일어난 것처럼 읽힌다. 쓰기를 기다리지 않고 **먼저** 바꾸고, 실패하면 되돌린다:
    // 로컬 클립보드 쓰기는 사실상 즉시라 낙관적으로 보여 주는 편이 정직하다.
    el.textContent = tip.copyLinkDone;
    window.setTimeout(() => { el.textContent = label; }, 1400);
    void navigator.clipboard.writeText(make()).catch(() => {
      el.textContent = tip.copyLinkFail;
    });
  });
}

/** 리더 머리에 세울 개념 칩 수. 나머지는 "+N" 으로 센다 — 좁은 열에서 칩이 줄을
 *  넘기기 시작하면 그 줄이 본문보다 커진다(관계 카드의 `relsMore` 와 같은 태도). */
const READER_CHIPS = 6;

// 런타임 테마 모드 — 주제 틴트(`tintMap`)가 모드에 따라 고른다.
import { type ThemeMode } from "../theme";
import { loaderBlock, loaderInline } from "../vinc-loaders.js";
const curMode = (): ThemeMode =>
  document.documentElement.dataset.theme === "dark" ? "dark" : "light";

/** 마운트 세대 — 라우터가 표면을 갈아끼워도 컨테이너는 남으므로, "내가 아직 살아
 *  있는가"는 이 카운터로만 판별한다(전역 리스너 누수 방지). */
let liveGen = 0;
let disposeView: (() => void) | undefined;
export function leaveView(): void { disposeView?.(); disposeView = undefined; ++liveGen; }

/** 지금 지도가 칠한 주제 색 — 마운트가 짓고 노드 카드가 읽는다. 주제 틴트를 끄면 null
 *  이라 카드의 표식도 잉크 그대로다(지도와 같다). */
let shownTints: Map<string, string> | null = null;

/** 살아 있는 마운트의 테마 재칠 — renderView 가 걸고, 표면이 떠나면 스스로 물러난다. */
let themeRepaint: (() => void) | null = null;
/** 테마가 뒤집혔다 — 열린 화면을 **다시 칠하기만** 한다. `main.ts applyTheme` 가 좌측
 *  트리(`repaintLnb`)와 나란히 부른다: 설정 클릭과 OS 추종이 만나는 곳이 거기뿐이다.
 *  근거: [[decision:a-theme-switch-repaints-the-open-view-in-place]] */
export function repaintView(): void { themeRepaint?.(); }

export async function renderView(el: HTMLElement, resume = false) {
  leaveView();
  const myGen = ++liveGen;
  const restoreEntryCamera = resume && location.hash === "#view";
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
  const bookmark = entryAccess && restoreEntryCamera ? viewSessionCache.last(entryAccess.session) : undefined;
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
  // 레일 폭 복원 + 닫힌 상태로 시작(크롬은 아직 비킬 것이 없다).
  setRailWidth(loadPrefs().railW);
  railChrome(false);
  wireRailGrip(railGrip);


  let compass: CompassHandle | null = null;   // 나침반 렌즈(그래프 보기)
  // 같은 범위를 다시 그릴 때(`keepCamera`: 변경 감지 새로고침 · 쓰고 난 뒤) 나침반이 보던 자리. 다음 마운트가 한 번 받아 간다
  let compassKeep: CompassState | null = null;
  let compassDisposeWrapped = false;   // dispose 를 나침반 정리로 한 번만 감싼다(마운트마다 감싸면 사슬이 자란다)
  let lastNode: N3 | null = null;                   // Reader 뒤로가기용 마지막 상세 노드(D45-V2)
  /** 잇기(흐름 F)가 기다리는 대상 — 있으면 다음 초점이 카드 대신 여기로 간다(`focusOnMap`). */
  let pickTarget: ((id: string, title: string) => void) | null = null;
  let curEdges: GraphEdge[] = [];     // 현재 레벨 엣지 — 상세 레일 in/out 표기용
  // 지금 그린 노드들 — focusNode 가 id 만 받고도 라벨·주제를 알아야 한다(D70).
  // 렌더러에서 꺼내지 않고 마운트 시점에 한 벌 챙겨 둔다.
  let curNodes: GraphNode[] = [];
  /** 지금 그려진 노드를 갈아 끼운다. 주제 색 표가 이 집합 위에서 정의되므로 대입을
   *  한 곳으로 모은다(곳마다 대입하면 그중 하나에서 옛 표가 남는다). */
  function setCurNodes(next: GraphNode[]): void {
    curNodes = next;
    // 주제 색은 **여기서 한 벌**만 짓는다 — 지도와 노드 카드가 같은 표를 읽는다.
    // 색은 주제의 순위로 매겨지므로 표를 짓는 목록이 곳마다 다르면(고아 숨김으로 걸러진
    // 목록, 주제 하나짜리 목록) 같은 주제가 곳마다 다른 색이 된다.
    // 근거: [[decision:a-node-keeps-its-own-colour-in-every-state]]
    shownTints = tintMap(next.map((n) => n.domain), curMode());
  }
  /** 지금 렌더러들이 칠해진 모드. 새 마운트는 언제나 그때의 모드로 칠하므로, 이 값은
   *  테마가 뒤집힐 때만 바뀐다 — 같은 값을 다시 쓰는 applyTheme(설정에서 같은 칸을 다시
   *  누름 · "auto" 가 이미 OS 와 같음)에는 아무 일도 안 한다. */
  let paintedMode = curMode();
  /** 테마 전환을 **색에만** 반영한다. `show()` 를 부르지 않는 것이 요점이다 — 그 길은
   *  읽던 문서 열과 노드 카드를 닫고(detail.hidden · closeFloatPanel) 카메라·점등까지
   *  되감는다. 표는 여기서 한 벌 다시 짓고, 렌더러마다 자기 굳은 값만 갈아 끼운다.
   *  근거: [[decision:a-theme-switch-repaints-the-open-view-in-place]] */
  function retheme(): void {
    const mode = curMode();
    if (mode === paintedMode) return;
    paintedMode = mode;
    shownTints = tintMap(curNodes.map((n) => n.domain), mode);
    compass?.retheme();
    // 떠 있는 노드 카드의 주제 표식(openRelsCard) — 지도와 같은 표를 읽는다.
    document.querySelectorAll<HTMLElement>(".float-mark[data-domain]").forEach((mk) => {
      const t = shownTints?.get(mk.dataset.domain || "");
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
  // 주소의 `/as/<관점>` 칸은 읽지 않는다 — 보기는 3D 하나다(view-prefs `VIEWPOINTS`).
  const curPrefs = (): ViewPrefs => loadPrefs();

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
  /** 잇기 대상 고르기를 푼다(열·층이 바뀌면 고르던 자리가 사라진다). 잇기 띠가 듣고 닫힌다. */
  function dropPick() {
    if (!pickTarget) return;
    pickTarget = null;
    dispatchEvent(new CustomEvent("vinc:pick-cancel"));
  }
  function closeRail() {
    ++railRevision;
    dropPick();
    detail.hidden = true;
    // 열의 내용을 지우기 **전에** 그 안의 편집기를 확정·해제한다. 아래 innerHTML 은
    // 마운트 지점을 걷어 갈 뿐 디바운스를 건드리지 못한다(저장이 화면 없는 곳에서
    // 터졌다 — 2026-09-07 실측).
    disposeEditorsIn(detail);
    detail.innerHTML = "";
    lastNode = null;
    if (focusId) { focusId = ""; syncHash(); }   // 주소는 화면이 말하는 것만 말한다
    railFull = false;     // 다음 열기는 옆 카드가 기본이다(railFull 주석)
    railChrome(false);
    // 카드는 선택에 딸린 물건이라 선택이 끝나면 같이 사라진다(떠 있는 창을 남기지 않는다).
    closeFloatPanel();
  }
  function openRail() {
    ++railRevision;
    dropPick();
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
      if (myGen !== liveGen || !grip.isConnected) { observer.disconnect(); return; }
      updateRailGrip();
    });
    observer.observe(docpane ?? detail);
    observer.observe((docpane ?? detail).parentElement!);
  }
  // Esc 단계 후퇴(정본 §2) 1단계 — 레일이 열려 있으면 레일부터 닫는다. 보기 패널이
  // 열려 있을 땐 그쪽 핸들러가 먼저 잡으므로(패널에 포커스가 있다) 충돌하지 않는다.
  /** 정본 §2 조작 문법(V12) — 여태 규정만 있고 구현이 없던 키들.
   *  Esc 는 **단계적 후퇴**다: 패널 → 레일 → 상위 층. 한 번에 다 닫지 않는다.
   *  `f` = 지금 층을 화면에 맞춤. 입력 필드 안에서는 전부 무시(타이핑 보호). */
  const onKey = (e: KeyboardEvent) => {
    if (myGen !== liveGen) { document.removeEventListener("keydown", onKey); return; }
    // ⚠ **Esc 만은 통과시킨다.** 이 가드가 옳게 비켜서는 바람에, 편집기에 포커스가
    // 있으면 레일을 Esc 로 닫을 수 없었다 — 들어가는 문은 모든 마크에 달려 있는데
    // 나오는 문이 ✕ 하나로 줄었다. 이제 어느 문으로 나가도 대기 중 편집이 확정되므로
    // (doc-editor 의 dispose) 이 문을 되살려도 잃는 것이 없다.
    if (blocksSurfaceShortcuts(e.target, document.activeElement, docEditorOpen())
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
   *
   *  두 가지를 여기서 처리한다(2026-08-17, [[vinc.research.view-interaction-audit]]):
   *  · **재사용**: 옵션 변경으로 다시 그릴 때는 서버가 필요 없다(좌표를 바꾸는 것은
   *    layout·cone 뿐이고 그건 applyPrefs 가 `reuseGraph` 를 안 켠다). `layout_positions`
   *    는 순수 함수라 같은 요청은 같은 응답이었고, 그 왕복(73~155ms)이 옵션 클릭 지연의
   *    절반이었다.
   *  · **순서**: 빠르게 연달아 show() 가 나면 늦게 도착한 옛 응답이 새 그림을 덮었다.
   *    부를 때의 세대를 기억해, 돌아왔을 때 세대가 지났으면 `stale` 로 답한다. */
  const cameraPrefs = (p: ViewPrefs) => JSON.stringify([p,
    Math.round((canvas.clientWidth || 1200) / (canvas.clientHeight || 750) * 10) / 10]);
  // 그래프 읽기와 가지 배치, 두 캐시의 상태는 `view-graph-loader.ts` 가 든다.
  const loader = createGraphLoader({
    mountScope, accessJob, firstAccess, restoreEntryCamera, noLayout: true,
    seq: () => showSeq,
    alive: () => myGen === liveGen && canvas.isConnected,
    canvasRatio: () => Math.round((canvas.clientWidth || 1200) / (canvas.clientHeight || 750) * 10) / 10,
    mounted: () => false,
    cameraPrefs,
    // 미리보기 그림은 3D 의 것이었다. 나침반은 전량이 올 때 한 번 그린다
    showPreview: () => () => {},
    restoreCameras: () => {},
    // 중심에서 화면 뒤에서 가지 배치가 끝났다. 받은 그래프는 그대로 두고 배치만 갈아 끼운다.
    layoutReady: () => {
      loader.reuseGraph = true;
      void show(trail[trail.length - 1], { keepPanel: true, keepDetail: true });
    },
    layoutFailed: () => showLayoutNote(COPY.view.layoutFailed),
  });
  // 시간순 가지 대신 중심에서 배치를 그렸을 때의 한 줄 안내. 다음 화면이 그려지면 사라진다.
  let layoutNote: HTMLElement | undefined;
  function clearLayoutNote() { layoutNote?.remove(); layoutNote = undefined; }
  function showLayoutNote(text: string) {
    clearLayoutNote();
    const note = document.createElement("div");
    note.setAttribute("role", "status"); note.dataset.layoutNote = "true"; note.textContent = text;
    note.style.cssText = "position:absolute;bottom:12px;left:12px;max-width:min(560px,70%);font-size:var(--fs-xs);"
      + "color:hsl(var(--muted-foreground));pointer-events:none";
    canvas.append(note); layoutNote = note;
  }
  let displayedScene: { key: string; prefs: string; domains: string[]; seq: number } | undefined;
  const markDisplayed = (p: ViewPrefs, domains: string[]) => {
    displayedScene = { key: loader.scopeKey!, prefs: cameraPrefs(p), domains: [...domains], seq: showSeq };
  };
  const dispose = () => {
    const retained = loader.retained;
    if (retained && displayedScene?.key === retained.key
        && mountScope.key === viewScope().key && myGen === liveGen) {
      viewSessionCache.remember(retained.session, retained.key, retained.revision,
        { prefs: displayedScene.seq === showSeq ? cameraPrefs(curPrefs()) : displayedScene.prefs,
          camera3: undefined }, displayedScene.domains);
    }
    accessJob.abort(); loader.abort(); loader.removePreview();
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
    const p = curPrefs();
    setMsg(loaderBlock("branch", COPY.view.loading(domains.length ? 1 : 0)));
    const res = await loader.fetchGraph(p, scope);
    if (res.stale) return;                        // 더 새 show() 가 이미 화면을 가져갔다
    swapSurface();
    // 도달 불가도 빈 상태와 **같은 상자**로 낸다. 붉은 배너만 다른 모양이면 같은 사정이
    // 다른 사건처럼 읽힌다. 문구는
    // 그대로 "못 불러왔다"다 — 상자를 통일하는 것이지 사정을 뭉개는 것이 아니다
    // (여기에 "파일을 추가하세요"를 대면 백엔드가 죽었을 때 거짓 조언이 된다).
    if (!res.executed) { setMsg(readFailedBox()); return; }
    // 주제 설명의 문서 미러는 그 주제의 노드가 아니다(주제가 글을 못 담아 문서로 둔 것). 지도·트리·수에서 빼고
    // 설명은 주제 상세가 보여 준다. 표시는 서버가 단다(`api/query.py` `_mark_topic_descriptions`)
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
      // LNB = 문서 트리와 같은 그림의 노드 트리(lnb.ts). 시간 · 범례 = 머리의 "그래프" 글자 오른쪽 버튼이 여는 창.
      let ready = false;
      const restore = compassKeep; compassKeep = null;
      compass = mountCompass(canvas, compassDataFrom(nodes, curEdges), { startPath: chain, restore,
        store: `${cfg.owner}|${cfg.team || ""}`,
        onTree: (o) => repaintLnbGraph(o),
        onOpen: (id) => openFromCompass(id),
        onChange: () => { if (ready && compass) setLnbGraphActive(compass.centre()); },
        onDestroy: () => { setLnbGraphSource(null); setTopbarLeadTools(null); } });
      const cmp = compass;
      setLnbGraphSource({ groups: () => cmp.tree(), open: (id) => cmp.open(id), mark: (k) => cmp.mark(k), colour: (d) => cmp.colour(d), dock: cmp.side });
      setTopbarLeadTools(cmp.tools);
      ready = true;
      setLnbGraphActive(cmp.centre());
      setSearchFilter((q) => compass?.filter(q));
      if (!compassDisposeWrapped) {
        const prevDispose = disposeView; compassDisposeWrapped = true;
        disposeView = () => { prevDispose?.(); setSearchFilter(null); if (compass) { compass.destroy(); compass = null; } };
      }
      markDisplayed(p, domains);
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
    railFull = false;
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
  /** 문서 열의 **읽기/편집 모드**(발주자 지시 2026-09-10 — 되살림).
   *
   *  ⚠ 이것은 2026-08-05 의 "상시 편집" 지시를 **되돌린 것**이다. 그때의 근거는
   *  "고치려면 매번 두 번 눌러야 한다"였고 그건 지금도 참이다. 다만 상시 편집이
   *  치르던 값이 그보다 컸다: 읽으려고 연 문서에서 클릭 한 번이 곧 커서였고,
   *  드래그해서 복사하려는 손과 고치려는 손이 같은 과녁을 나눠 갖지 못했다.
   *  기본은 **읽기**다 — 문서를 여는 대부분의 이유가 읽는 것이라서.
   *  세션 안에서만 사는 이유: 방금 고치던 사람이 다음 문서에서도 고칠 확률이
   *  높지만, 앱을 다시 열었을 때까지 편집으로 남아 있을 이유는 없다. */
  let docWriting = false;

  let ctxCache: { id: string; scope: string; p: ReturnType<typeof api.nodeContext> } | null = null;
  const topicDrafts = new Map<string, TopicDraft>();
  const nodeContextOnce = (id: string) => {
    const scope = viewScope().key;
    if (ctxCache?.id !== id || ctxCache.scope !== scope)
      // 근거 문장까지 싣는다 — 넓은 열의 항목 페이지가 그것을 인용한다(카드는 불리언만 읽는다).
      ctxCache = { id, scope, p: api.nodeContext(cfg.owner, id, 4, { team: cfg.team || null }, 0, true) };
    return ctxCache.p;
  };

  function detailErrorHTML(state: DetailState) {
    return `<p class="card-note" role="status">${esc(detailMessage(state, COPY.ui.viewx))}</p>`
      + (state === "failed" ? `<button type="button" class="btn" data-detail-retry>${esc(COPY.ui.viewx.detailRetry)}</button>` : "");
  }
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
    const revision = railRevision;
    const scopeKey = viewScope().key;
    const current = () => box.isConnected && myGen === liveGen && focusId === nd.id
      && viewScope().key === scopeKey && (!pane || (revision === railRevision && !detail.hidden));
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

  /** 노드 카드 — 디자인 정본 `Vinc Node Card.dc.html` (2a Final) 그대로.
   *
   *      Details and source   3 documents  >     오른쪽 캐럿 = 문서 열로 나간다
   *      Links                -> 4 · <- 1  v     아래 캐럿 = 카드 안에서 펼친다(기본 펼침)
   *        -> Reciprocal rank fusion  requires
   *      Ask an assistant     [Copy prompt v]    이름 붙은 프롬프트 셋
   *      Zoom                 [ - | + ]
   *      Copy link  Share            Delete      조용한 유틸리티
   *
   *  깊이 선택(1/2/3)이 사라진 자리를 **이름 붙은 프롬프트 셋**이 대신한다. 숫자는
   *  크기를 말해 주지 않아 늘 경고를 달고 다녀야 했는데, 이름은 무엇을 담아 가는지
   *  스스로 말한다("내 문서 안에서만" · "연결 N개, 근거 포함").
   *
   *  인과 원뿔은 줄에서 뺐다 — 노드를 고르는 순간 `focusOnMap` 이 이미 과거·미래
   *  폐포를 점등하므로 카드가 다시 부를 일이 아니다. */
  async function openRelsCard(nd: N3) {
    if (nd.id.startsWith("domain:")) { openTopicCard(nd); return; }
    // 카드의 **집은 지도 카드의 좌상단**이다(발주자 지시 2026-09-09,
    // `수정항목/노드 클릭 플로팅 모달.JPG` 가 그 자리를 가리킨다). 여태는 노드의
    // 화면좌표 옆이었고, 그것이 낸 문제 둘:
    //   ① 자리가 매번 다르다 — 카드를 읽으려면 눈이 카드를 먼저 찾아야 했다.
    //   ② 읽는 면이 오른쪽으로 온 뒤(R1) 오른쪽 노드를 누르면 카드가 본문 위에 겹쳤다.
    // 고정 자리는 그 둘을 함께 없앤다. 노드와의 연결은 좌표가 아니라 **인과 원뿔의
    // 점등**이 말한다(focusOnMap 이 이미 그 일을 한다) — 카드가 노드 옆에 있어야
    // 한다는 요구는 그 점등이 없던 시절의 것이다.
    // 사용자가 끌어 옮기면 그 자리를 기억한다(float-panel.ts) — 고정은 집일 뿐이고
    // 손이 옮긴 자리를 되돌리지 않는다.
    const home = homeCardPos();
    const V = COPY.ui.viewx;
    const card = openFloatPanel({
      title: nd.label, x: home.x, y: home.y, closeLabel: V.railClose,
    });
    // 머리의 표식 — 지도가 그리는 마크 그대로(채운 원 = 개념), 색은 그 노드의 주제 색.
    // 지도와 **같은 표**에서 읽는다. 주제 하나만 넣어 표를 새로 지으면 순위가 늘 0 이라
    // 모든 카드가 같은 색(색상각 0)이 됐고, 지도의 그 노드와 한 번도 맞지 않았다.
    // `data-domain` 은 테마 전환(retheme)이 이 표식을 다시 칠할 때 읽는다.
    const tint = nd.domain ? shownTints?.get(nd.domain.trim()) : undefined;
    card.el.querySelector(".float-title")?.insertAdjacentHTML("beforebegin",
      `<span class="float-mark" aria-hidden="true"${
        nd.domain ? ` data-domain="${esc(nd.domain.trim())}"` : ""}${
        tint ? ` style="color:${tint}"` : ""}>${icon("dot", 11)}</span>`);

    card.body.innerHTML = `
      <div class="card-rows">
        ${detailHistory.length ? `<button class="btn" type="button" data-detail-back>${esc(V.readerBack)}</button>` : ""}
        <button type="button" class="card-row" id="c-open">
          <span class="row-name">${V.cardOpen}</span>
          <span class="row-meta" id="c-docs"></span>
          ${chev("right")}
        </button>
        <div class="card-sec">
          <button type="button" class="card-row" id="c-links" aria-expanded="false">
            <span class="row-name row-name--strong">${V.cardLinks}</span>
            <span class="row-meta" id="c-linkmeta"></span>
            ${chev("down")}
          </button>
          <div class="card-links" id="c-rels" hidden><p class="card-note">${loaderInline(V.sourceLoading)}</p></div>
        </div>
        ${askRowHTML()}
        <div class="card-util">
          <button type="button" id="c-copylink">${V.cardCopyLink}</button>
          <button type="button" id="c-share">${V.cardShare}</button>
          <button type="button" id="c-connect" data-tip="${esc(COPY.author.connectTip)}">${
            esc(COPY.author.connect)}</button>
          <button type="button" id="c-del" class="card-util-del">${V.cardDelete}</button>
        </div>
      </div>`;

    const q = <T extends HTMLElement>(s: string) => card.body.querySelector<T>(s);
    wireDetailBack(card.body, false);
    const url = (depth: number) =>
      absoluteViewUrl({ kind: "node", id: nd.id }, { depth }, spaceName());

    // 행동은 응답과 무관하므로 **지금** 묶는다. 늦게 묶으면 응답이 늦거나 카드가
    // 교체될 때 통째로 안 묶인다(실측으로 한 번 물렸다 — 관계가 통째로 비었다).
    // 다시 누르면 닫힌다. 여는 손짓과 닫는 손짓이 다른 자리에 있으면(레일의 ✕)
    // 방금 연 것을 되돌리려고 눈이 화면을 가로질러야 한다.
    q("#c-open")!.addEventListener("click", () => {
      const openHere = !detail.hidden && lastNode?.id === nd.id;
      if (openHere) closeRail(); else railFor(nd);
    });
    bindCopyLink(q("#c-copylink"), () => promptFor(url(1)));
    q("#c-share")!.addEventListener("click", () =>
      openShareSheet({ seed: nd.id, depth: SHARE_DEPTH }));
    q("#c-connect")!.addEventListener("click", () => void import("../connect")
      .then((m) => m.startConnect({ id: nd.id, title: nd.label })));
    q("#c-del")!.addEventListener("click", async () => {
      const guard = api.previewDeleteNode(cfg.owner, nd.id);   // 확인 창과 함께 미리본다
      if (!await confirmDestructive(V.delNodeAsk(nd.label), V.delConfirm, V.delCancel)) return;
      try {
        const res = await api.deleteNode(cfg.owner, nd.id, await guard);
        if (!res.executed) throw new Error(res.note || res.error || "delete failed");
        closeFloatPanel();
        closeRail();
        void show(trail[trail.length - 1]);
      } catch {
        // 지워진 것은 없지만 조용히 넘기지 않는다: 상세 열의 삭제와 같은 문장을 카드 바닥에 남긴다.
        // 유틸 줄의 단추는 좁아서 문장을 받지 못하므로 그 아래 한 줄이다. 다시 누르면 다시 시도한다.
        let note = card.body.querySelector<HTMLElement>("[data-del-fail]");
        if (!note) {
          note = document.createElement("p");
          note.className = "card-note";
          note.dataset.delFail = "";
          note.setAttribute("role", "alert");
          card.body.querySelector(".card-rows")?.append(note);
        }
        note.textContent = V.delFail;
      }
    });
    // Links 는 기본 접힘. 펼치는 것도 접는 것도 카드 안의 일이라 캐럿이 아래를 본다.
    const linksBox = q("#c-rels")!;
    q("#c-links")!.addEventListener("click", (e) => {
      const b = e.currentTarget as HTMLElement;
      const open = b.getAttribute("aria-expanded") !== "true";
      b.setAttribute("aria-expanded", String(open));
      linksBox.hidden = !open;
    });

    // 프롬프트 메뉴 — 카드면 위의 카드면이다(유리 아님). 디자인 주석의 이유:
    // 지도 위 카드 위에 또 유리를 얹으면 글자가 배경을 통과한다.
    const setCounts = wireAsk(card.body, () => card.el.isConnected,
      (deg, docs) => nodeAskItems(nd, deg, docs));
    const scopeKey = viewScope().key;

    try {
      const res = await nodeContextOnce(nd.id);
      if (!card.el.isConnected || myGen !== liveGen || focusId !== nd.id || viewScope().key !== scopeKey) return;
      const state = detailState(res);
      if (state !== "ready") {
        linksBox.hidden = false; linksBox.innerHTML = detailErrorHTML(state);
        linksBox.querySelector("[data-detail-retry]")?.addEventListener("click", () => {
          ctxCache = null; void openRelsCard(nd);
        });
        return;
      }
      card.setTitle(res.title || nd.label);
      const docs = (res.excerpts || []).length;
      const dq = q("#c-docs");
      if (dq && docs) dq.textContent = V.cardDocs(docs);
      const links = res.links;
      if (!links?.executed) {
        linksBox.hidden = false; linksBox.innerHTML = detailErrorHTML("failed");
        linksBox.querySelector("[data-detail-retry]")?.addEventListener("click", () => {
          ctxCache = null; void openRelsCard(nd);
        });
        return;
      }
      const mq = q("#c-linkmeta");
      if (mq) mq.textContent = V.cardLinkMeta(links.out, links.in);
      linksBox.innerHTML = relsHTML(links);
      wireRels(linksBox, nd, links);
      setCounts(links.degree + links.attached, docs);
    } catch (error) {
      if (card.el.isConnected && myGen === liveGen && focusId === nd.id && viewScope().key === scopeKey) {
        linksBox.hidden = false; linksBox.innerHTML = detailErrorHTML(detailFailure(error));
        linksBox.querySelector("[data-detail-retry]")?.addEventListener("click", () => {
          ctxCache = null; void openRelsCard(nd);
        });
      }
    }
  }

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

  async function openEdgeDetail(e: { id?: string; source: string; target: string; type: string;
                                     srcTitle?: string; dstTitle?: string }) {
    // D45-V 1단계 — 흐름 선 클릭: "왜 A 가 B 로 이어지는가". 클릭=고정(pin) —
    // 상세 패널 자리를 그대로 쓴다(노드 상세와 같은 표면, ESC/다른 클릭으로 이동).
    openRail();
    detail.innerHTML = `<p class="hint">${loaderInline(COPY.ui.viewx.relLoading)}</p>`;
    const pending = detail.firstChild, graphAtOpen = curEdges;
    // Use only an exact authored relation from this already-authorized graph.
    // A parallel relation without an ID must never borrow another one's evidence.
    const matches = curEdges.filter(x => !x.presentation && x.source === e.source
      && x.target === e.target && x.type === e.type && (!e.id || x.id === e.id));
    const known = matches.length === 1 ? matches[0] : undefined;
    try {
      let res: Awaited<ReturnType<typeof api.edgeContext>>;
      try { res = await api.edgeContext(cfg.owner, e.source, e.target, e.type); }
      catch (error) { if (!known) throw error; res = { executed: false }; }
      let guessed = false;   // 서버가 이 관계를 못 읽었다 — 근거가 있는지도 모른다
      if (detail.hidden || detail.firstChild !== pending || curEdges !== graphAtOpen) return;
      if (!res.executed && known) res = { executed: true,
        confidence: known.confidence, evidence: known.evidence,
        src: { id: e.source, title: curNodes.find(n => n.id === e.source)?.label || e.srcTitle },
        dst: { id: e.target, title: curNodes.find(n => n.id === e.target)?.label || e.dstTitle } };
      // 관계 상세 조회는 개념끼리만 안다(결정·기록이 낀 관계는 거절한다). 이름을 들고 왔으면
      // 근거 없이 그 관계를 그대로 보이고 지우는 문은 남긴다 — 이은 것을 못 지우면 안 된다.
      if (!res.executed) {
        if (!e.srcTitle || !e.dstTitle) {
          detail.innerHTML = `<p class="hint">${COPY.ui.viewx.relFail}</p>`;
          return;
        }
        res = { executed: true };
        guessed = true;
      }
      const a = res.src?.title || e.srcTitle || e.source, b = res.dst?.title || e.dstTitle || e.target;
      const sentence = (COPY.ui.viewx.relSentence[e.type]
        || COPY.ui.viewx.relSentence.RELATED_TO)(a, b);
      const conf = res.confidence && res.confidence > 0
        ? ` <span class="hint">· ${COPY.ui.viewx.relConfidence(res.confidence)}</span>` : "";
      // 양 끝은 한 줄 설명만 보인다. 문서 발췌로 메우지 않는다: 잘린 마크다운은 문장으로
      // 읽히지 않는다. 설명이 없으면 제목(위 h2)이 그 자리를 한다.
      const side = (t: string, s?: { one_liner?: string }) => {
        const body = s?.one_liner || "";
        return body ? `
          <div class="list-row" style="display:block">
            <span class="title" style="font-size:.85em">${esc(t)}</span>
            <p class="hint" style="margin:.15rem 0 0">${esc(body)}</p>
          </div>` : "";
      };
      // 열 머리(크기 · 닫기)를 함께 싣는다 — 여태 이 화면만 머리가 없어 Esc 말고는 나갈 문이 없었다.
      detail.innerHTML = `
        ${railHeadHTML(esc(COPY.author.linkDetailTip))}
        <h2>${esc(a)} → ${esc(b)}</h2>
        <p>${esc(sentence)}${conf}</p>
        ${guessed ? "" : `<p class="hint" style="margin-bottom:.3rem">${COPY.ui.viewx.relEvidence}</p>
        ${res.evidence
          ? `<p>${esc(res.evidence)}</p>`
          : `<p class="hint">${COPY.ui.viewx.relEvidenceNone}</p>`}`}
        ${side(a, res.src)}${side(b, res.dst)}
        <p class="rail-acts"><button class="btn btn-quiet" id="edge-del">${
          esc(COPY.author.edgeDel)}</button></p>
        <p class="hint" id="edge-del-msg" role="status"></p>`;
      wireRailHead();
      // 연결 지우기(흐름 F 의 짝). 여태 API 는 있는데 부르는 곳이 없었다. 되돌릴 수 없으므로
      // 확인을 받고, 지운 것이 0 이면(지울 수 없는 종류·이미 없음) 그렇게 말한다.
      detail.querySelector("#edge-del")!.addEventListener("click", async () => {
        const A = COPY.author;
        const say = (t: string) => { const m = detail.querySelector("#edge-del-msg"); if (m) m.textContent = t; };
        const guard = api.previewDeleteEdge(cfg.owner, e.source, e.target, e.type);
        if (!await confirmDestructive(esc(A.edgeDelAsk(a, b)), COPY.ui.viewx.delConfirm,
                                      COPY.ui.viewx.delCancel)) return;
        try {
          const r = await api.deleteEdge(cfg.owner, e.source, e.target, e.type, await guard);
          if (!r.executed) { say(A.edgeDelFail); return; }
          if (!r.deleted?.edges) { say(r.note ? A.edgeDelUnsupported : A.edgeDelFail); return; }
          closeRail();
          void show(trail[trail.length - 1], { keepPanel: true, keepCamera: true });
        } catch { say(A.edgeDelFail); }
      });
    } catch {
      if (!detail.hidden && detail.firstChild === pending && curEdges === graphAtOpen)
        detail.innerHTML = `<p class="hint">${COPY.ui.viewx.relFail}</p>`;
    }
  }

  /** 합계 구간 클릭 → 개념 목록 드릴다운(D45-V2 3단계). "이 숫자는 어떤 개념들인가".
   *  개념 클릭 → 그 개념 상세(node-context)로 한 홉 더(문서는 거기서). */
  async function openDrilldown(domain: string, mark: string, seed?: string) {
    openRail();
    detail.innerHTML = `<p class="hint">${loaderInline(COPY.ui.viewx.drillLoading)}</p>`;
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

  /** 상세 레일의 수치·관계 목록(T3·T4 · D45-V) — node-context 응답 하나가 정본이다.
   *  구 사이드카는 `links` 키 자체가 없다: 그때만 그래프 잔상(curEdges)으로 강등하고
   *  캡션도 정직하게 바꾼다(수치의 출처를 화면이 스스로 밝힌다). */
  /** Agent 역할 계약을 읽는 순서: 임무 → 직함·팀 → 등급 → 금지·사람 몫 → 읽을 것·관문
   *  → 나머지. 목록에 없는 키는 저장된 순서대로 뒤에 온다.
   *  근거: [[vinc.canon.agent-roles §3]] */
  const ROLE_PROP_ORDER = ["mission", "job_title", "team", "team_role", "tier", "rung",
    "delegate_rung", "never", "person_only", "reads", "gates", "test_command", "repos",
    "paths", "tools", "disallowed_tools", "isolation", "writes_graph"];

  /** 속성(props) 절 — 노드가 가진 자유 속성을 **통째로** 보여 준다. 여태 이것을 보는
   *  화면이 없었다: 서버는 props 를 실어 보냈는데 앱은 Ask 본문 한 줄과 Agent 목록의
   *  직함·팀만 골라 썼다(발주자 지적 2026-09-25). 값은 생긴 대로 그린다(글·목록·객체).
   *  비어 있으면 절을 그리지 않는다 — 빈 제목만 남기지 않는다.
   *  근거: [[decision:lnb-picks-in-the-graph-open-the-floating-card-and-the-side-pane-2026-10-28]] */
  /** 속성 절이 읽은 것 — 무슨 종인가(props 를 싣는가), 어느 판(rev)을 읽었는가. */
  type PropsMeta = { kind?: string; rev?: number; canEdit?: boolean };

  function drawNodeProps(nd: N3, props?: Record<string, unknown>, meta: PropsMeta = {}) {
    const box = detail.querySelector<HTMLElement>("#node-props");
    if (!box) return;
    const P = props || {};
    const keys = Object.keys(P);
    // 편집할 수 있는 종이면 비어 있어도 절을 그린다 — 첫 속성을 더할 자리가 있어야 한다.
    const editable = propsEditable(meta.kind) && meta.canEdit !== false;
    if (keys.length === 0 && !editable) { box.innerHTML = ""; return; }
    const role = isAgentRoleId(nd.id);
    const order = role
      ? [...ROLE_PROP_ORDER.filter((k) => k in P),
         ...keys.filter((k) => !ROLE_PROP_ORDER.includes(k))]
      : keys;
    const V = COPY.ui.viewx;
    box.innerHTML = `
      <section class="node-props">
        <div class="props-head">
          <h3 class="hint rail-sec">${esc(V.propsTitle)}</h3>
          ${editable ? `<button class="rail-x props-edit-btn" type="button"
              aria-label="${esc(V.propsEditTip)}" data-tip="${esc(V.propsEditTip)}">${
              esc(V.propsEdit)}</button>` : ""}
        </div>
        ${keys.length ? propsDl(P, order, 0) : `<p class="hint">${esc(V.propsEmpty)}</p>`}
      </section>`;
    // 역할의 이름은 직함이 있으면 그것이다 — Agent 레일(lnb.ts roleTitle)과 같은 규칙.
    const job = role && typeof P.job_title === "string" ? P.job_title.trim() : "";
    const h2 = detail.querySelector("h2");
    if (job && h2) h2.textContent = job;
    box.querySelector(".props-edit-btn")?.addEventListener("click",
      () => openPropsEditor(nd, P, order, meta));
  }

  /** 속성 편집기 — 상세 열의 속성 절을 제자리에서 편집 줄로 바꾼다.
   *
   *  줄마다 이름 · 형식 · 값 · 지우기다(형식과 값의 변환은 `props-edit.ts`). 저장은
   *  **전체 교체**다: 서버는 props 를 통째로 갈아 끼우므로 보내는 것이 늘 편집 결과
   *  전체이고, 지운 줄이 곧 지운 속성이다.
   *  저장 직전에 캐시를 거치지 않고 다시 읽어 **판(rev)이 편집을 시작할 때와 같을 때만**
   *  쓴다. 그 사이 다른 창·에이전트·동기화가 고쳤으면 덮어쓰지 않고 멈춘다 — 필드 이력이
   *  없어서 덮어쓰면 복구가 없다. owner 는 싣지 않는다(서버가 기존 소유자를 지키고,
   *  계정 경로는 허브가 세션으로 정한다).
   *  근거: [[decision:node-props-are-edited-in-the-document-pane-as-a-whole-and-checked-against-the-read-revision-2026-09-26]] */
  function openPropsEditor(nd: N3, props: Record<string, unknown>, order: string[],
                           meta: PropsMeta) {
    const found = detail.querySelector<HTMLElement>("#node-props");
    if (!found || !meta.kind) return;
    const box: HTMLElement = found;     // 안쪽 함수들까지 좁힌 타입이 가도록
    const V = COPY.ui.viewx;
    let rows: PropRow[] = rowsFrom(props, order);
    if (rows.length === 0) rows = [{ key: "", kind: "text", text: "" }];

    const lines = (t: string) => Math.min(12, Math.max(2, t.split("\n").length));
    const valueHTML = (r: PropRow) => {
      if (r.kind === "bool") {
        const on = r.text.trim() === "true";
        return `<select class="props-val" aria-label="${esc(V.propsKinds.bool)}">
          <option value="true"${on ? " selected" : ""}>${esc(V.propsYes)}</option>
          <option value="false"${on ? "" : " selected"}>${esc(V.propsNo)}</option></select>`;
      }
      if (r.kind === "number")
        return `<input class="props-val" type="text" inputmode="decimal" spellcheck="false"
          value="${esc(r.text)}" aria-label="${esc(V.propsKinds.number)}">`;
      const ph = r.kind === "list" ? ` placeholder="${esc(V.propsListHint)}"` : "";
      const mono = r.kind === "json" ? " props-mono" : "";
      return `<textarea class="props-val${mono}" rows="${lines(r.text)}" spellcheck="${
        r.kind === "text"}"${ph} aria-label="${esc(V.propsKinds[r.kind])}">${esc(r.text)}</textarea>`;
    };
    const rowHTML = (r: PropRow, i: number) => `
      <div class="props-erow" data-i="${i}">
        <div class="props-erow-head">
          <input class="props-key" type="text" value="${esc(r.key)}" spellcheck="false"
                 autocomplete="off" placeholder="${esc(V.propsKeyPh)}" aria-label="${esc(V.propsKeyPh)}">
          <select class="props-kind" aria-label="${esc(V.propsKindLabel)}">${PROP_KINDS.map((k) =>
            `<option value="${k}"${k === r.kind ? " selected" : ""}>${esc(V.propsKinds[k])}</option>`)
            .join("")}</select>
          <button class="rail-x props-del" type="button" aria-label="${esc(V.propsRemove(r.key))}"
                  data-tip="${esc(V.propsRemove(r.key))}">✕</button>
        </div>
        ${valueHTML(r)}
        <p class="props-err" role="alert" hidden></p>
      </div>`;

    /** 화면의 줄을 그대로 읽는다 — 상태의 원천은 DOM 하나다(다시 그릴 때만 배열로 옮긴다). */
    const collect = (): PropRow[] => [...box.querySelectorAll<HTMLElement>(".props-erow")]
      .map((row) => ({
        key: row.querySelector<HTMLInputElement>(".props-key")!.value,
        kind: row.querySelector<HTMLSelectElement>(".props-kind")!.value as PropKind,
        text: row.querySelector<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(
          ".props-val")!.value,
      }));
    const paint = (focusRow?: number) => {
      box.innerHTML = `
        <section class="node-props props-editing">
          <div class="props-head"><h3 class="hint rail-sec">${esc(V.propsTitle)}</h3></div>
          <div class="props-rows">${rows.map(rowHTML).join("")}</div>
          <button class="rail-x props-add" type="button">+ ${esc(V.propsAdd)}</button>
          <p class="hint props-status" role="status"></p>
          <div class="props-acts">
            <button class="btn" type="button" data-props="cancel">${esc(V.propsCancel)}</button>
            <button class="btn btn-primary" type="button" data-props="save">${esc(V.propsSave)}</button>
          </div>
        </section>`;
      if (focusRow !== undefined)
        box.querySelector<HTMLElement>(`.props-erow[data-i="${focusRow}"] .props-key`)?.focus();
    };
    const status = (html: string) => {
      const el = box.querySelector<HTMLElement>(".props-status");
      if (el) el.innerHTML = html;
    };
    // 편집기는 절 상자에 리스너를 단다. 접을 때 상자를 새로 갈아 끼워 그 리스너를 떼어
    // 낸다 — 같은 상자를 다시 쓰면 다음 편집에서 클릭 하나가 두 번 처리된다.
    const release = () => {
      const fresh = box.cloneNode(false) as HTMLElement;
      box.replaceWith(fresh);
    };
    const cancel = () => { release(); drawNodeProps(nd, props, meta); };
    const reload = () => { release(); ctxCache = null; void loadNodeSource(nd); };

    async function save() {
      rows = collect();
      const { props: next, errors } = propsFrom(rows);
      box.querySelectorAll<HTMLElement>(".props-err").forEach((p) => { p.hidden = true; });
      box.querySelectorAll(".props-erow").forEach((r) => r.removeAttribute("aria-invalid"));
      if (errors.length) {
        for (const er of errors) {
          const row = box.querySelector<HTMLElement>(`.props-erow[data-i="${er.index}"]`);
          const p = row?.querySelector<HTMLElement>(".props-err");
          if (p) { p.textContent = V.propsErr[er.code]; p.hidden = false; }
          row?.setAttribute("aria-invalid", "true");
        }
        box.querySelector<HTMLElement>(`.props-erow[data-i="${errors[0].index}"] .props-key`)?.focus();
        return;
      }
      if (sameProps(next, props)) { status(esc(V.propsNoChange)); return; }
      const acts = [...box.querySelectorAll<HTMLButtonElement>("button")];
      acts.forEach((b) => { b.disabled = true; });
      status(esc(V.propsSaving));
      const conflict = () => {
        status(`${esc(V.propsConflict)} <button class="rail-x props-reload" type="button">${
          esc(V.propsReload)}</button>`);
        box.querySelector(".props-reload")?.addEventListener("click", reload);
        box.querySelector<HTMLButtonElement>('[data-props="cancel"]')!.disabled = false;
      };
      try {
        // 판 확인은 서버가 쓰기와 같은 writer 안에서 한다(0.9.8 `expected_rev`): 편집을
        // 시작한 판이 아니면 쓰지 않고 충돌로 답한다. 바뀐 키만 `props_patch` 로 싣는다.
        // patch 로 못 싣는 편집(값으로서의 null)은 같은 판 조건으로 전체를 싣는다.
        const patch = propsPatchOf(props, next);
        const res: any = await api.editNode({ id: nd.id, type: meta.kind!,
          ...(patch ? { props_patch: patch } : { props: next }) }, meta.rev ?? 0);
        if (isWriteConflict(res)) { conflict(); return; }
        if (res?.executed === false) throw new Error(res.error || "");
        // ⚠ `merged` 는 실제로 들어간 것만 센다 — warnings 를 안 읽으면 건너뛴 쓰기
        // (남이 소유한 id 등)를 성공으로 읽는다.
        const merged = (res?.merged?.nodes?.created || 0) + (res?.merged?.nodes?.matched || 0);
        if (!merged) throw new Error((res?.warnings || []).join(" · "));
        release();
        ctxCache = null;
        await loadNodeSource(nd);       // 새 판으로 다시 그린다(편집 단추가 새 rev 를 든다)
        // 역할의 직함·팀은 Agent 목록이 props 에서 읽는다 — 목록도 새로 읽는다.
        if (isAgentRoleId(nd.id)) void refreshLnb({ quiet: true });
      } catch (err: any) {
        if (isWriteConflict(err)) { conflict(); return; }
        acts.forEach((b) => { b.disabled = false; });
        const why = messageOf(err, "").trim();
        status(`<span class="error-banner">${esc(V.propsFail)}${why ? ` ${esc(why)}` : ""}</span>`);
      }
    }

    paint();
    box.addEventListener("click", (e) => {
      const t = e.target as HTMLElement;
      if (t.closest(".props-add")) {
        rows = [...collect(), { key: "", kind: "text", text: "" }];
        paint(rows.length - 1);
      } else if (t.closest(".props-del")) {
        const i = Number(t.closest<HTMLElement>(".props-erow")!.dataset.i);
        rows = collect().filter((_, j) => j !== i);
        paint();
        box.querySelector<HTMLElement>(".props-add")?.focus();
      } else if (t.closest('[data-props="cancel"]')) cancel();
      else if (t.closest('[data-props="save"]')) void save();
    });
    box.addEventListener("change", (e) => {
      const sel = (e.target as HTMLElement).closest<HTMLSelectElement>(".props-kind");
      if (!sel) return;
      const cur = collect();
      const i = Number(sel.closest<HTMLElement>(".props-erow")!.dataset.i);
      const prev = rows[i]?.kind ?? "text";
      cur[i] = { ...cur[i], text: convertText(cur[i].text, prev, cur[i].kind) };
      rows = cur;
      paint();
      box.querySelector<HTMLElement>(`.props-erow[data-i="${i}"] .props-val`)?.focus();
    });
    // Esc 는 편집만 접는다(열 전체를 닫는 전역 Esc 까지 가지 않게). Ctrl/Cmd+Enter 는 저장.
    box.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { e.stopPropagation(); cancel(); }
      else if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); void save(); }
    });
    box.querySelector<HTMLElement>(".props-key")?.focus();
  }

  function propsDl(obj: Record<string, unknown>, keys: string[], depth: number): string {
    return `<dl class="props-dl">${keys.map((k) => `
      <div class="props-row"><dt>${esc(k)}</dt><dd>${propValueHTML(k, obj[k], depth)}</dd></div>`)
      .join("")}</dl>`;
  }

  function propValueHTML(key: string, v: unknown, depth: number): string {
    const none = `<span class="hint">${esc(COPY.ui.common.none)}</span>`;
    if (v === null || v === undefined || v === "") return none;
    if (typeof v === "string") {
      // 여러 줄이거나 긴 글은 문서와 같은 렌더러로. 짧은 값은 글자 그대로 둔다.
      return v.includes("\n") || v.length > 160
        ? `<div class="doc-render">${renderMarkdown(v)}</div>` : inlineCode(v);
    }
    if (typeof v === "number" || typeof v === "boolean")
      return `<span class="props-scalar">${esc(String(v))}</span>`;
    if (Array.isArray(v)) {
      if (v.length === 0) return none;
      // `reads` 는 "이 순서로 읽어라"라서 번호를 단다. 나머지는 순서가 뜻이 아니다.
      const tag = key === "reads" ? "ol" : "ul";
      return `<${tag} class="props-list">${v.map((x) => `<li>${
        typeof x === "object" && x !== null ? propValueHTML("", x, depth + 1)
          : inlineCode(String(x))}</li>`).join("")}</${tag}>`;
    }
    if (typeof v === "object") {
      const o = v as Record<string, unknown>;
      const ks = Object.keys(o);
      if (ks.length === 0) return none;
      // 두 단을 넘으면 원문 JSON 으로 — 들여쓰기가 열 폭을 다 먹는다.
      return depth >= 2
        ? `<pre class="props-json">${esc(JSON.stringify(o, null, 2))}</pre>`
        : propsDl(o, ks, depth + 1);
    }
    return esc(String(v));
  }

  /** 짧은 값 — 이스케이프하고 `코드` 표기만 살린다(역할 계약은 도구 이름을 그렇게 적는다). */
  function inlineCode(s: string): string {
    return esc(s).replace(/`([^`]+)`/g, "<code>$1</code>");
  }

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

  /** 캐럿이 **목적지를 말한다**: 오른쪽 = 여기서 나간다(문서 열), 아래 = 여기서
   *  펼친다. 디자인 정본 `Vinc Node Card.dc.html` 의 규칙이고, 그래서 같은 모양의
   *  두 줄이 서로 다른 일을 한다는 것이 눌러 보기 전에 읽힌다. */
  const chev = (dir: "down" | "right") =>
    `<span class="row-chev row-chev--${dir}" aria-hidden="true">${icon("chevron-left", 12)}</span>`;

  /** "Ask an assistant" 한 줄 — **카드와 레일이 같은 것을 쓴다.**
   *
   *  디자인 정본은 카드만 규정하지만, 레일에도 같은 일을 하는 자리가 있었다(구 "MCP ·
   *  예시 프롬프트 복사"). 두 곳이 다른 모양으로 같은 일을 하면 어느 쪽이 맞는지 매번
   *  다시 정해야 하므로 한 벌로 합쳤다. 마크업도 클래스도 같고, 놓이는 자리만 다르다. */
  function askRowHTML(): string {
    const V = COPY.ui.viewx;
    return `<div class="card-row card-row--static ask-row">
        <span class="row-name">${V.cardAsk}</span>
        <button type="button" class="card-ctl js-ask" aria-expanded="false"
                aria-haspopup="menu">${V.cardAskCopy}${chev("down")}</button>
      </div>`;
  }
  /** 그 줄에 동작을 매단다. 반환값으로 수치를 나중에 채운다(응답이 오면 부제가 산다).
   *
   *  메뉴는 **스크롤 컨테이너 밖**(body)에 뜬다. 카드는 `overflow:hidden`, 그 본문은
   *  `overflow:auto` 라 안에 두면 세 항목 중 하나만 보였다(실측 2026-08-27). 레일도
   *  같은 사정이라 한 벌로 두는 편이 맞다. 아래가 모자라면 위로 뒤집는다.
   *
   *  프롬프트 셋은 번호가 아니라 **이름**이다. 숫자(깊이 1/2/3)는 크기를 말해 주지 않아
   *  늘 경고를 달고 다녀야 했는데, 이름은 무엇을 담아 가는지 스스로 말한다.
   *  근거: 디자인 정본 `Vinc Node Card.dc.html` (2b). */
  /** 메뉴 한 칸 — 이름·부제·담아 갈 프롬프트·따라갈 주소. */
  type AskItem = { name: string; sub: string; body: string; url: string };
  /** 노드에서 여는 셋. **에이전트 역할이면 한 칸이 더 붙는다**(발주자 지시
   *  2026-09-10: "에이전트 노드 클릭 시 ask assistant 가 에이전트를 쓰게").
   *  그 노드는 개념이 아니라 시킬 일이라, 설명을 청하는 것이 아니라 그 역할을
   *  맡기는 말이 첫 칸에 온다. 판별은 주제가 아니라 역할 id 로 한다(`isAgentRoleId`,
   *  레일·상세 패널과 같은 함수). Default Agents 역할은 `vinc/agent` 에 서서 주제
   *  일치로는 메뉴가 안 붙었다(VIN-263). */
  function nodeAskItems(nd: N3, deg: number, docs: number): AskItem[] {
    const V = COPY.ui.viewx;
    const at = (depth: number) => absoluteViewUrl({ kind: "node", id: nd.id }, { depth }, spaceName());
    const items: AskItem[] = [
      { name: V.askExplain, sub: V.askExplainSub, body: V.askExplainBody, url: at(1) },
      { name: V.askCompare, sub: V.askCompareSub(deg), body: V.askCompareBody, url: at(SHARE_DEPTH) },
      { name: V.askDocs, sub: V.askDocsSub(docs), body: V.askDocsBody, url: at(1) },
    ];
    if (isAgentRoleId(nd.id)) {
      items.unshift({ name: V.askRole, sub: V.askRoleSub, body: V.askRoleBody, url: at(1) });
    }
    return items;
  }
  /** 문서에서 여는 셋(2026-09-10 신설). 주소는 `kind: "doc"` — 이미 있던 갈래다. */
  function docAskItems(docId: string): AskItem[] {
    const V = COPY.ui.viewx;
    const url = absoluteViewUrl({ kind: "doc", id: docId }, {}, spaceName());
    return [
      { name: V.askDocSum, sub: V.askDocSumSub, body: V.askDocSumBody, url },
      { name: V.askDocConcepts, sub: V.askDocConceptsSub, body: V.askDocConceptsBody, url },
      { name: V.askDocCheck, sub: V.askDocCheckSub, body: V.askDocCheckBody, url },
    ];
  }

  function wireAsk(scope: HTMLElement, alive: () => boolean,
                   build: (deg: number, docs: number) => AskItem[]):
                   (deg: number, docs: number) => void {
    const V = COPY.ui.viewx;
    const trigger = scope.querySelector<HTMLButtonElement>(".js-ask");
    if (!trigger) return () => {};
    document.getElementById("ask-menu-live")?.remove();      // 한 번에 하나만 뜬다
    const menu = document.createElement("div");
    menu.id = "ask-menu-live";
    menu.className = "card-menu";
    menu.setAttribute("role", "menu");
    menu.hidden = true;
    document.body.appendChild(menu);

    const label = (s: string) => {
      if (trigger.firstChild) trigger.replaceChild(document.createTextNode(s), trigger.firstChild);
    };
    const close = () => { menu.hidden = true; trigger.setAttribute("aria-expanded", "false"); };
    const place = () => {
      const r = trigger.getBoundingClientRect();
      const w = 246, gap = 6;
      menu.style.left = `${Math.max(8, Math.min(r.right - w, window.innerWidth - w - 8))}px`;
      const below = window.innerHeight - r.bottom;
      menu.style.top = below > 160 || r.top < 170
        ? `${r.bottom + gap}px` : `${Math.max(8, r.top - gap - menu.offsetHeight)}px`;
    };
    trigger.addEventListener("click", () => {
      const open = menu.hidden;
      menu.hidden = !open;
      if (open) place();
      trigger.setAttribute("aria-expanded", String(open));
    });
    document.addEventListener("click", (e) => {
      // 붙어 있던 표면이 사라졌으면 메뉴도 데려간다 — body 에 있으니 저절로 안 없어진다.
      if (!alive()) { menu.remove(); return; }
      if (!(e.target as HTMLElement).closest(".js-ask,#ask-menu-live")) close();
    });

    return (deg: number, docs: number) => {
      const items = build(deg, docs);
      menu.innerHTML = items.map((it, i) => `
        <button type="button" role="menuitem" class="menu-item" data-i="${i}">
          <span class="menu-name">${esc(it.name)}</span>
          <span class="menu-sub">${esc(it.sub)}</span>
        </button>`).join("");
      menu.querySelectorAll<HTMLElement>(".menu-item").forEach((b) =>
        b.addEventListener("click", () => {
          const it = items[Number(b.dataset.i)];
          // 되먹임은 트리거 버튼에 뜨고 메뉴는 닫힌다(디자인 2b 주석).
          label(V.copyLinkDone);
          window.setTimeout(() => label(V.cardAskCopy), 1400);
          close();
          void navigator.clipboard.writeText(`${it.body} ${it.url}`)
            .catch(() => label(V.copyLinkFail));
        }));
    };
  }
  /** 연결 목록 — 방향 화살표 · 상대 이름 · 관계 이름.
   *
   *  관계 이름은 `COPY.ui.ask.relTag` 가 이미 사람 말로 갖고 있다("derives from").
   *  타입 코드를 그대로 내던 것을 바꾼 것이라 Ask 와 카드가 같은 어휘를 쓴다.
   *  근거가 있는 관계는 **그 이름 자체가** 근거로 들어가는 자리다 — 디자인에 별도
   *  버튼이 없고, 줄을 누르는 것은 이동이므로 둘이 과녁을 나눠 가진다. */
  function relsHTML(links: NodeLinks): string {
    const V = COPY.ui.viewx;
    if (links.items.length === 0) return `<p class="card-note">${V.relsNone}</p>`;
    const TAG = COPY.ui.ask.relTag;
    const arrow = (d: string) => (d === "out" ? "→" : d === "in" ? "←" : "·");
    const rest = links.degree + links.attached - links.items.length;
    return links.items.map((it, i) => {
      const ev = it.evidence && it.dir !== "att";
      // 관계 이름은 **관계 상세로 가는 문**이다 — 근거가 없어도 연다(거기서 지운다).
      // 부착(att)은 지우는 길이 없는 관계라 문을 내지 않는다.
      const open = it.dir !== "att";
      return `
        <button type="button" class="link-row" data-i="${i}">
          <span class="link-dir" aria-hidden="true">${arrow(it.dir)}</span>
          <span class="link-title">${esc(it.title)}</span>
          <span class="link-tag${ev ? " link-tag--ev" : ""}${open ? " link-tag--open" : ""}"${
            open ? ` data-ev="${i}" data-tip="${ev ? V.relEvidence : esc(COPY.author.linkDetailTip)}"` : ""}>${
            esc(TAG[`${it.type}:${it.dir}`] || it.type.toLowerCase())}</span>
        </button>`;
    }).join("")
      + (links.truncated && rest > 0
        ? `<button type="button" class="link-more">${V.relsMore(rest)}</button>` : "");
  }

  function wireRels(box: HTMLElement, nd: N3, links: NodeLinks) {
    box.querySelectorAll<HTMLElement>(".link-row").forEach((r) =>
      r.addEventListener("click", (e) => {
        const it = links.items[Number(r.dataset.i)];
        if ((e.target as HTMLElement).closest<HTMLElement>(".link-tag--open")) {
          e.stopPropagation();
          const here = (detail.contains(box) && detail.querySelector("h2")?.textContent) || nd.label;
          void openEdgeDetail(it.dir === "out"
            ? { source: nd.id, target: it.id, type: it.type, srcTitle: here, dstTitle: it.title }
            : { source: it.id, target: nd.id, type: it.type, srcTitle: it.title, dstTitle: here });
          return;
        }
        // 문서 열 안의 연결 줄이면 **열 안에서** 옮겨 간다(지금 크기 그대로). 카드의
        // 줄이면 지금처럼 지도에서 그 노드로 간다.
        if (detail.contains(box)) void openInPane(it.id, it.domain || undefined);
        else void focusNode(it.id);
      }));
    // `+N more` 는 레일을 연다 — 카드는 좁고, 전부 보려면 넓은 자리가 필요하다.
    box.querySelector(".link-more")?.addEventListener("click", () => railFor(nd));
  }

  /** 문서 열 안에서 다른 노드로 — 카드는 띄우지 않고 열의 크기는 그대로 둔다. */
  async function openInPane(id: string, domain?: string) {
    const nd = await focusNode(id, domain, false);
    if (nd && myGen === liveGen) railFor(nd);
  }

  async function loadNodeSource(nd: N3) {
    // 발췌는 증강 — 실패·부재가 상세 패널을 깨지 않는다(문구만 남기고 조용히).
    const box = detail.querySelector<HTMLElement>("#node-source");
    if (!box) return;
    const revision = railRevision;
    const scopeKey = viewScope().key;
    const current = () => myGen === liveGen && revision === railRevision && detail.contains(box)
      && !detail.hidden && viewScope().key === scopeKey;
    const fail = (state: DetailState) => {
      box.innerHTML = detailErrorHTML(state);
      const stats = detail.querySelector("#node-stats");
      if (stats) stats.innerHTML = "";
      box.querySelector("[data-detail-retry]")?.addEventListener("click", () => {
        ctxCache = null; void loadNodeSource(nd);
      });
    };
    box.innerHTML = `<p class="hint">${loaderInline(COPY.ui.viewx.sourceLoading)}</p>`;
    try {
      const res = await nodeContextOnce(nd.id);
      if (!current()) return;
      const state = detailState(res);
      if (state !== "ready") { fail(state); return; }
      // 수치는 조회 성공 값만 쓴다. 응답 실패를 화면의 선이나 0으로 대신하지 않는다.
      drawNodeStats(nd, res.links, res.node_kind);
      detail.querySelector("#node-stats [data-detail-retry]")?.addEventListener("click", () => {
        ctxCache = null; void loadNodeSource(nd);
      });
      // 카드와 같은 줄이므로 부제도 같은 수치를 말해야 한다.
      if (res.links?.executed) railAskCounts?.(res.links.degree + res.links.attached,
                                               (res.excerpts || []).length);
      // D32 고아 최소화 — "확실히 연결 0"일 때만 잇기 후보를 낸다. 여태 드릴다운
      // 경로가 degree 0 을 박아 33연결 허브에도 제안이 떴다.
      if (res.links?.executed && res.links.degree + res.links.attached === 0)
        void suggestTiesInto(nd);
      // 이 스페이스에 쓸 수 없으면(커뮤니티 · 팀 열람자) 편집 단추를 내지 않는다.
      const canEdit = await canWriteHere();
      // 패널이 그 사이 다른 노드로 갱신됐으면 버린다(늦은 응답이 남의 상세를 덮지 않게).
      if (!detail.contains(box) || detail.hidden) return;
      drawNodeProps(nd, res.props, { kind: res.node_kind, rev: res.rev, canEdit });
      // 지도에 자리가 없는 노드(연결 없는 기록·결정)는 id 조각을 이름으로 달고 열린다 —
      // 노드가 답한 제목이 오면 그것으로 바꾼다(역할의 직함은 위에서 이미 골랐다).
      const h2 = detail.querySelector("h2");
      if (h2 && res.title && h2.textContent === nd.id.replace(/^[a-z]+:/, "")) h2.textContent = res.title;
      // 기본 정보(흐름 H) — 저장되면 지도와 이 열을 새 값으로 다시 그린다.
      const basics = detail.querySelector<HTMLElement>("#node-basics");
      if (basics) void import("../node-basics").then((m) => m.drawBasics(basics, nd.id, res,
        (domain) => void openNodePane(nd.id, domain || undefined), canEdit));
      if (railFull && res.links?.executed) drawPaneLinks(nd, res.links);
      if (railFull) drawItemPage(nd, res, canEdit);
      const parts: string[] = [];
      if (res.one_liner) parts.push(`<div class="doc-render">${renderMarkdown(res.one_liner)}</div>`);
      const ex = res.excerpts || [];
      if (ex.length > 0) {
        const header = res.arm === "fts"
          ? COPY.ui.viewx.sourceRelated : COPY.ui.viewx.sourceHeader;
        // 문서 줄 = 클릭 시 Reader 전문 승격(peek-definition 동선). 발췌 글은 싣지 않는다:
        // 문서에서 잘라 낸 창이라 제목·목록·강조가 끊긴다. 어느 문서의 어느 절인지만
        // 보이고, 글은 Reader 가 전문으로 보여 준다. 같은 문서는 한 줄로 모은다.
        const seen = new Set<string>();
        const rows = ex.filter((e) => !seen.has(e.doc_id) && seen.add(e.doc_id));
        parts.push(`<p class="hint" style="margin-bottom:.3rem">${header}</p>` +
          rows.map((e, i) => `
            <div class="list-row rdr-open" data-doc="${esc(e.doc_id)}" data-i="${i}"
                 style="display:block;cursor:pointer">
              <span class="title" style="font-size:.85em">${esc(e.doc_title)}${
                // 문서 H1 아래 문단은 절 이름이 곧 문서 제목이라 같은 말을 두 번 적게 된다(VIN-258).
                e.section && e.section.trim() !== (e.doc_title || "").trim()
                  ? `<span class="hint"> · ${esc(e.section)}</span>` : ""}</span>
            </div>`).join(""));
      } else if (!res.one_liner) {
        parts.push(`<p class="hint">${COPY.ui.viewx.sourceNone}</p>`);
      }
      box.innerHTML = parts.join("");
      hydrateRich(box);          // 한 줄 설명 안의 수식·다이어그램을 채운다(늦게 싣는다)
      box.querySelectorAll<HTMLElement>(".rdr-open").forEach((row) =>
        row.addEventListener("click", () =>
          void openReader(row.dataset.doc!, nd.id)));
    } catch (error) {
      if (current()) fail(detailFailure(error));
    }
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
    const revision = openRail();
    const current = () => revision === railRevision && myGen === liveGen && !detail.hidden;
    detail.innerHTML = `<p class="hint">${loaderInline(COPY.ui.viewx.readerLoading)}</p>`;
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
      if (!current()) return;
      if (!doc.executed || doc.text === undefined) {
        // ⚠ **이 이른 반환은 이 패키지가 만든 것이 아니다.** `!doc.executed ||
        // doc.text === undefined` 는 P17b 이전부터 여기 있었고, 편집기를 세우지 않는
        // 보호는 거기서 온다(`git show <P17b 이전>:app/src/surfaces/view.ts`).
        // P17(#241)이 `doc_get` 을 이 상태에서 `executed:false` 로 돌린 순간 쓰기 창은
        // 이미 이 가드에 걸렸다. 그러니 그 보호를 P17b 의 공으로 적지 말 것 —
        // 다음 사람이 보호가 어디서 오는지 잘못 안다.
        //
        // 이 패키지가 더한 것은 **문구 갈래 + 사람이 누르는 재시도** 둘뿐이다: 쓰기 창은
        // "못 열었다"와 다른 사건이라(문서는 그대로 있고 비어 있지도 않다) 같은 문구로
        // 떨어지면 화면에서 "빈 문서"로 읽힌다. 자동 재시도는 안 건다 — 크래시 잔해면
        // 영원히 돈다.
        const busy = docMidWrite(doc);
        // 레일 머리는 이 분기에도 있어야 한다. `detail.innerHTML` 을 통째로 갈아 끼우면
        // `.rail-head`(#rail-close)가 함께 사라져 마우스만 쓰는 사람에게 나가는 문이
        // Esc 키뿐이 된다. 쓰기 창 중에서도 **크래시 잔해는 다음 re-put 까지 며칠 간다** —
        // 오래 머무는 상태라 여기부터 닫는다.
        // ⚠ 선행 결함이 남아 있다: `readerLoading`·`readerFail`·`drillFail`
        // 도 같은 모양으로 레일을 비운다(P17b 이전부터). 그 셋은 이 패키지 밖이다.
        detail.innerHTML = `
          ${railHeadHTML(COPY.ui.viewx.readerTitle)}
          <p class="hint">${busy ? COPY.ui.viewx.readerMidWrite : COPY.ui.viewx.readerFail}</p>`
          + (busy ? `<button class="btn" id="rdr-retry">${esc(COPY.ui.viewx.readerRetry)}</button>` : "");
        wireRailHead();
        detail.querySelector("#rdr-retry")?.addEventListener("click",
          () => void openReader(docId, conceptId, chunkIndex));
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
          <span class="hint">${COPY.ui.viewx.readerConcepts}</span>
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
        ${railHeadHTML(COPY.ui.viewx.readerTitle)}
        <!-- 문서는 읽기 모드로 열린다. "상시 편집"은 2026-09-10 에 되돌렸다:
             읽으며 선택하거나 복사하는 클릭이 곧 편집 커서가 되었기 때문이다.
             아래 rdr-mode 버튼으로 편집을 켜며, 저장 상태는 rdr-state 가 말한다. -->
        <p class="rail-acts">
          <button class="btn" id="rdr-back">${COPY.ui.viewx.readerBack}</button>
          <button class="btn" id="rdr-expand">${COPY.ui.viewx.edExpand}</button>
          <button class="btn" id="rdr-copy">${COPY.ui.viewx.edCopy}</button>
          ${isCommunitySpace(cfg.team) ? "" : `<button class="btn" id="rdr-mode">${
            docWriting ? COPY.ui.viewx.edToRead : COPY.ui.viewx.edToWrite}</button>
          <span class="hint" id="rdr-mode-state">${
            docWriting ? COPY.ui.viewx.edWrite : COPY.ui.viewx.edRead}</span>`}
        </p>
        <!-- 문서도 프롬프트를 나눠 준다(발주자 지시 2026-09-10). 노드 카드·노드 상세와
             같은 줄·같은 메뉴를 쓴다 — 세 자리가 같은 일을 다른 모양으로 하면 어느
             쪽이 맞는지 매번 다시 정해야 한다(askRowHTML 머리말과 같은 이유).
             ⚠ 이 주석은 템플릿 리터럴 **안**이다 — 백틱을 쓰면 문자열이 거기서 끊긴다. -->
        ${askRowHTML()}
        ${dupTitle ? "" : `<h2 id="rdr-title" style="margin-top:.4rem"${docWriting ? " hidden" : ""}>${esc(docTitle(doc.title))}</h2>`}
        <p>${badge} <span class="hint" id="rdr-state"></span></p>
        ${mentionsHTML}
        ${tocHTML}
        <div id="rdr-host"></div>`;
      wireRailHead();
      // 칩을 누르면 그 노드로 간다 — D70 의 약속("문서를 고르면 지도 어디인지 안다")을
      // 문서 쪽에서도 지키는 문이다. 스코프가 바뀌면 레일은 노드 상세로 갈린다.
      detail.querySelectorAll<HTMLElement>(".rdr-chips [data-go]").forEach((b) =>
        b.addEventListener("click", () =>
          void focusNode(b.dataset.go!, b.dataset.dom || undefined)));
      // 문서의 프롬프트 셋. 수치를 안 쓰므로 한 번 그리고 끝이다(노드 쪽은 관계·
      // 문서 수가 늦게 오지만 문서는 자기 자신이 이미 전부다).
      // ⚠ **노드 쪽 손을 먼저 끊는다.** 두 화면이 같은 패널을 쓰는데 메뉴는 문서에
      //    하나뿐이라, 노드 상세가 남긴 늦은 콜백(nodeContextOnce 응답)이 방금 그린
      //    문서 메뉴를 노드 항목으로 덮는다. 널로 두면 그 호출이 조용히 무시된다.
      railAskCounts = null;
      wireAsk(detail, () => !detail.hidden, () => docAskItems(docId))(0, 0);
      // 여기서 손수 dispose 하지 않는다 — 둘 다 레일을 다시 여닫으므로 위의
      // openRail/closeRail 이 확정까지 맡는다. 두 벌로 두면 다시 갈린다.
      detail.querySelector("#rdr-back")!.addEventListener("click", () => {
        if (lastNode) openDetail(lastNode); else closeRail();
      });
      // D45-E 개정 — **Reader 와 편집기가 한 표면이다.** 문서는 언제나 렌더된
      // 모습으로 보이고(원문 마크다운 노출 없음), `편집`은 그 자리에 커서를 켤
      // 뿐이다. 여태 둘은 다른 화면이었고, 보기 화면은 `$$…$$`·`**…**` 같은
      // 소스를 그대로 노출했다(실측).
      const { mountDocEditor, openDocSheet } = await import("../doc-editor");
      if (!current()) return;
      const state = detail.querySelector<HTMLElement>("#rdr-state")!;
      const rdrHost = detail.querySelector<HTMLElement>("#rdr-host")!;
      // 편집 모드에서는 편집기의 제목 줄(제목 · 내용 시점)을 연다. 그때 위의 제목 글자는 숨기고,
      // 저장되면 새 제목으로 다시 쓴다.
      const titleEl = detail.querySelector<HTMLElement>("#rdr-title");
      // 편집 모드의 제목 줄은 자기 저장 상태 글자를 갖는다. 위 상태 줄과 두 번 말하지 않게 숨긴다.
      state.hidden = docWriting;
      let savedHash = doc.content_hash;
      const onSaved = (_id: string, t: string, h: string) => {
        if (h) savedHash = h;
        if (titleEl && t) titleEl.textContent = docTitle(t);
      };
      let handle = await mountDocEditor(rdrHost, {
        owner: cfg.owner, docId, initial: doc.text, title: doc.title,
        contentHash: doc.content_hash, hideTitle: !docWriting, onSaved,
        // 모드가 정한다(위 `docWriting`). 커서는 켜지 않는다 — 문서 열은 지도와
        // 나란히 서므로, 열자마자 키보드를 가져가면 `f`(맞춤)·Esc 같은 지도 조작이
        // 글자로 들어간다.
        // ⚠ 읽기 모드에서는 CM 의 장식이 **더 완전하다**: 편집 가능일 때는 커서가
        //    놓인 줄의 소스를 되살려 보여 주는데(라이브 프리뷰의 규칙), 읽기에서는
        //    그럴 이유가 없어 문법이 끝까지 감춰진다. 읽는 화면이 더 깨끗해진다.
        readOnly: !docWriting, autoFocus: false,
        // 제목 줄이 숨어 있어 편집기 자신의 상태 글자도 함께 숨는다 — 저장 신호를
        // 여기로 받아 레일 머리 옆에서 말한다.
        onStatus: (t) => { state.textContent = t; },
        highlights: hl.ranges || [],
        // 본문의 `[[doc_id]]` 가 같은 레일에서 그 문서로 갈린다. 지도 착지는 하지
        // 않는다 — 문서 안의 참조는 "저 문서를 마저 읽자"이지 "저 노드로 가자"가
        // 아니고, 노드로 갈 문은 바로 위 칩이 이미 들고 있다.
        onDocLink: (id) => void openReader(id),
      });
      if (!current()) { await handle.dispose(); return; }
      // 본문 복사. 화면을 긁는 대신 **소스 그대로**를 준다 — 문서의 정본은
      // 마크다운이라, 렌더된 글자를 복사하면 붙여넣은 곳에서 다른 문서가 된다.
      detail.querySelector<HTMLElement>("#rdr-copy")?.addEventListener("click", (e) => {
        const btn = e.currentTarget as HTMLElement;
        const text = handle.getText();
        if (!text) return;                     // 아직 못 읽은 문서 — 조용히 아무 일도 안 한다
        const back = COPY.ui.viewx.edCopy;
        btn.textContent = COPY.ui.viewx.copyLinkDone;
        window.setTimeout(() => { btn.textContent = back; }, 1400);
        void navigator.clipboard.writeText(text)
          .catch(() => { btn.textContent = COPY.ui.viewx.copyLinkFail; });
      });
      // 읽기 ↔ 편집. **다시 읽지 않는다** — 같은 본문 위에서 커서만 켜고 끄는 일이라
      // 왕복을 붙이면 모드 전환이 문서 열기만큼 비싸진다. 나가면서 확정하는 규율은
      // `dispose` 가 이미 진다(대기 중이던 디바운스를 저장하고 놓는다).
      detail.querySelector<HTMLElement>("#rdr-mode")?.addEventListener("click", () => void (async () => {
        const btn = detail.querySelector<HTMLElement>("#rdr-mode");
        const tag = detail.querySelector<HTMLElement>("#rdr-mode-state");
        const text = handle.getText();          // 방금까지 친 것을 그대로 물려준다
        await handle.dispose();
        docWriting = !docWriting;
        if (btn) btn.textContent = docWriting ? COPY.ui.viewx.edToRead : COPY.ui.viewx.edToWrite;
        if (tag) tag.textContent = docWriting ? COPY.ui.viewx.edWrite : COPY.ui.viewx.edRead;
        if (titleEl) titleEl.hidden = docWriting;
        state.hidden = docWriting;
        handle = await mountDocEditor(rdrHost, {
          owner: cfg.owner, docId, initial: text || doc.text,
          title: titleEl?.textContent || doc.title,
          contentHash: savedHash, hideTitle: !docWriting, onSaved,
          readOnly: !docWriting,
          // 편집으로 들어갈 때만 커서를 준다 — 그것이 이 버튼을 누른 이유다.
          autoFocus: docWriting,
          onStatus: (t) => { state.textContent = t; },
          highlights: hl.ranges || [],
          onDocLink: (id) => void openReader(id),
        });
      })());
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
      // 이 문만 확정을 **기다린다**: 시트가 곧바로 같은 문서를 서버에서 다시 읽으므로,
      // 저장이 끝나기 전에 열면 방금 친 글자가 없는 본문이 뜨고(그 뒤 편집은 해시
      // 충돌로 튄다). 나머지 출구는 다시 읽지 않으므로 기다릴 이유가 없다.
      detail.querySelector("#rdr-expand")!.addEventListener("click", () => void (async () => {
        await handle.dispose();
        closeRail();
        await openDocSheet({ owner: cfg.owner, docId });
      })());
    } catch {
      if (current()) detail.innerHTML = `<p class="hint">${COPY.ui.viewx.readerFail}</p>`;
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
            <span class="title" style="font-size:.9em">${esc(s.title)}
              ${s.shared_tags.length ? `<span class="hint">· ${esc(s.shared_tags.join(", "))}</span>` : ""}</span>
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
            detail.innerHTML = `<h2>${esc(nd.label)}</h2>
              <p>${COPY.ui.viewx.bound(esc(s.title))}</p>`;
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
    loader.removePreview();
    clearLayoutNote();
    if (compass) { setSearchFilter(null); compass.destroy(); compass = null; }
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
    loader.abort(); loader.removePreview();
    setBusy(true, opts?.keepPanel ? 120 : 400);
    // 같은 범위를 다시 그리면 나침반이 보던 자리를 잇는다. 캡처는 파괴 **전에**.
    compassKeep = opts?.keepCamera && compass ? compass.snapshot() : null;
    ctxCache = null;                           // 층이 바뀌면 노드 맥락 메모도 버린다
    dropPick();
    // `keepDetail` — 방금 만든 것을 지도에 세우는 재그리기(revealNode)는 문서 열을 닫지
    // 않는다. 거기서 쓰고 있던 글이 첫 저장과 함께 사라지면 안 된다.
    if (!opts?.keepDetail) { ++railRevision; detail.hidden = true; }
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
      loader.removePreview();
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
  setViewHooks({
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
  });
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
  setLnbNavigator((id, dom, docId) => void openFromNav(id, dom, docId));
  // 주제 고르기 — **즉시 반영**이다(발주자 결정 2026-08-15). 고른 것과 보는 것이
  // 어긋나는 중간 상태를 두지 않는 대신, 고를 때마다 서버를 한 번 왕복한다.
  setLnbScopeClearer(() => false);
  setLnbTopicPicker((path, add, set) => {
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
  });


  // 저절로 새로워지는 갱신(VIN-129). 지금 층을 다시 받되 카메라와 렌즈 패널은 잇는다 —
  // 옵션 재마운트(`applyPrefs`)와 같은 길이라 "보던 곳을 잃는다"가 다시 생기지 않는다.
  // 재생 중이거나 카드·상세가 떠 있으면 "지금은 아니다"(`false`): 다시 그리기가 그것들을
  // 닫는다. 바뀐 것은 `live.ts` 가 기억했다가 손을 뗀 뒤 적용한다.
  setLiveRefresher(async () => {
    if (myGen !== liveGen) return;
    // 잇기 대상을 고르는 중에도 기다린다 — 다시 그리기가 그 고르기를 푼다.
    if (isFloatPanelOpen() || !detail.hidden || pickTarget) return false;
    loader.reset();
    await show(trail[trail.length - 1], { keepPanel: true, keepCamera: true });
  });

  await show(trail[trail.length - 1]);
  // View 밖에서 "새 문서"를 눌러 여기로 왔다(authoring.startNewDoc) — 첫 화면 뒤에 연다.
  const pendingDoc = takePendingNewDoc();
  if (pendingDoc) void openWriter(pendingDoc.topic);
  else if (landOn) await (landWide ? openFromNav(landOn, undefined, landDoc || undefined)
                                 : focusNode(landOn));
  else if (landReader) await openDocAt(landReader);
}
