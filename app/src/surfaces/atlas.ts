import Graph from "graphology";
import Sigma from "sigma";
import { api, cfg, type GraphEdge, type GraphNode, type LedgerRes, type LedgerRow } from "../api";
import { COPY } from "../copy";
import { enableLasso, mount3d, mountMinis3d, type L3, type N3 } from "./atlas3d";
import { mountFlow, mountTimeline, mountTotals, timelinePool,
         type FlowEdge, type PlanarHandle } from "./atlas-planar";
import { openShareSheet } from "../sheets";
import { LAYOUTS, VIEWS, isPlanar, loadPrefs, savePrefs,
         type AtlasLayout, type AtlasPrefs, type AtlasView } from "../atlas-prefs";

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

type Level = { kind: "l0" } | { kind: "l1"; domain: string }
  | { kind: "l2"; seed: string; domain?: string }
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

export async function renderAtlas(el: HTMLElement) {
  el.innerHTML = `
    <div id="atlas-crumb"></div>
    <div id="atlas-canvas"></div>
    <div id="atlas-legend"></div>
    <div id="atlas-detail" hidden></div>
    <div id="atlas-msg" style="position:absolute;inset:0;display:grid;place-items:center;z-index:1"></div>
    <div id="atlas-tools" style="position:absolute;top:var(--space-4,1rem);right:var(--space-6,1.5rem);z-index:2;display:flex;gap:.5rem"></div>
    <div id="atlas-float" style="position:absolute;left:50%;bottom:var(--space-6,1.5rem);transform:translateX(-50%);z-index:3" hidden></div>
    <div id="atlas-hint" hidden style="position:absolute;left:50%;bottom:var(--space-6,1.5rem);transform:translateX(-50%);z-index:2;pointer-events:none;white-space:nowrap;font-size:.72rem;color:hsl(var(--caption-foreground));background:hsl(var(--card)/.72);border:1px solid hsl(var(--border));border-radius:999px;padding:.25rem .7rem"></div>
    <div id="atlas-view-panel" hidden
         style="position:absolute;top:3.2rem;right:var(--space-6,1.5rem);z-index:4;width:17rem"></div>
  `;
  const canvas = el.querySelector<HTMLElement>("#atlas-canvas")!;
  const crumbEl = el.querySelector<HTMLElement>("#atlas-crumb")!;
  const detail = el.querySelector<HTMLElement>("#atlas-detail")!;
  const msg = el.querySelector<HTMLElement>("#atlas-msg")!;
  const tools = el.querySelector<HTMLElement>("#atlas-tools")!;
  const float = el.querySelector<HTMLElement>("#atlas-float")!;
  const lassoHint = el.querySelector<HTMLElement>("#atlas-hint")!;
  lassoHint.textContent = COPY.ui.atlasx.lassoHint;
  const viewPanel = el.querySelector<HTMLElement>("#atlas-view-panel")!;

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
  let minis3d: { dispose(): void } | null = null;   // Grid 미니(단일 렌더러, D34)
  let planar: PlanarHandle | null = null;           // 평면 관점(Flow/Timeline/Totals, D43)
  let extraSigmas: Sigma[] = [];      // Compare 2D 렌즈
  let curEdges: GraphEdge[] = [];     // 현재 레벨 엣지 — 상세 레일 in/out 표기용
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

  function setMsg(html: string) { msg.innerHTML = html; msg.style.display = html ? "grid" : "none"; }

  const isScope = (lv: Level) => lv.kind === "l0" || lv.kind === "l1" || lv.kind === "l2";
  const viewName = (v: AtlasView) =>
    v === "3d" ? "3D" : v === "2d" ? "2D"
    : v === "flow" ? COPY.ui.atlasx.flow
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
    if (lv.kind !== "l2" || lv.seed !== seed || lv.domain) return;
    const d = nodes.find((n) => n.id === seed)?.domain;
    if (d) lv.domain = d;
  }

  /** D31 + D43 — 인캔버스 View 컨트롤. view 는 이제 관점 5지선다(맵 3D/2D + 평면 3종)라
   *  칩은 현재 관점 이름을 그대로 싣는다. Grid/Matrix 는 같은 패널의 두 번째 행으로
   *  옮겨 스위처를 하나로 만든다(툴 행에 느슨한 칩을 남기지 않는다).
   *  닫힌 층(형태=타입 · 포인트색=초점 · 모노크롬)은 여전히 노출하지 않는다. */
  function viewControl() {
    const lv = trail[trail.length - 1];
    // 프레임에서는 칩이 지금 있는 프레임 이름을 싣는다 — 그리지도 않는 관점을 칩이
    // 주장하면(예: Grid 에서 "View · Flow") 화면과 컨트롤이 서로를 반박한다.
    // (Grid 는 3D/2D 를 그리지만 칩 한 칸에는 더 구체적인 "장소"를 싣는다 —
    //  어느 관점이 걸렸는지는 패널의 활성 표시가 답한다.)
    const label = frameName(lv) || viewName(curPrefs().view);
    tools.insertAdjacentHTML("afterbegin",
      `<button class="chip" id="t-view" aria-haspopup="dialog" aria-expanded="false"
               aria-controls="atlas-view-panel">${COPY.ui.panel.viewChip(label)}</button>`);
    tools.querySelector("#t-view")!.addEventListener("click", () =>
      viewPanel.hidden ? openPanel() : closePanel());
  }

  // 패널 열림/닫힘·aria·포커스를 한 곳에서만 만진다(드리프트 방지).
  function openPanel() {
    viewPanel.hidden = false;
    document.getElementById("t-view")?.setAttribute("aria-expanded", "true");
    // 발견 힌트는 "한 번 열면" 끝난다 — 쓰는 곳도 읽는 곳도 하나씩.
    localStorage.setItem("vinc.atlas.views.seen", "1");
    if (lassoHint.textContent === COPY.ui.atlasx.viewsHint) lassoHint.hidden = true;
    drawViewPanel();
    // 활성 칩이 없는 프레임(Compare — 관점도 프레임도 활성이 아니다)에서도 포커스는
    // 반드시 패널 안으로 들어와야 한다: Escape 핸들러가 패널에 걸려 있어, 포커스가
    // 밖(#t-view)에 남으면 열어 놓고 키보드로 닫을 길이 사라진다.
    (viewPanel.querySelector<HTMLElement>(".seg .chip.active")
      ?? viewPanel.querySelector<HTMLElement>(".seg .chip"))?.focus();
  }
  function closePanel() {
    viewPanel.hidden = true;
    const b = document.getElementById("t-view");
    b?.setAttribute("aria-expanded", "false");
    b?.focus();
  }

  /** 옵션 행 — 키는 "관점"이 아니라 **그 층의 렌더러가 실제로 읽는 것**이다.
   *  아무 일도 하지 않는 컨트롤은 아예 그리지 않는다(보이면 반드시 화면이 바뀐다).
   *
   *  Grid: 미니 반경=size(nodeRadius), 카드 기본 배치=layout, 고아 제거=unlinked
   *    (filterUnlinked). 라벨·화살표·산개는 미니가 아예 그리지 않는다.
   *  Compare/Matrix: 렌더러가 프리퍼런스를 한 줄도 읽지 않는다 → 행 없음.
   *  L0 + 3D/2D: showL0 이 자기 원환을 직접 계산한다 — 반경은 records 고정 공식
   *    (size 무관), ties 엣지에는 type 이 없어 화살촉이 붙지 않고(arrows 무관),
   *    filterUnlinked 도 api.graph(layout) 도 타지 않는다. 남는 것은 labels·spread.
   *  L0 + Timeline: 주제 노드에 날짜가 없어 그릴 것이 없다 → 행 없음(빈 상태 전용).
   *  L1/L2 + Flow·Timeline: 좌표를 쓰지 않는 평면 차트라 layout·spread·size 는 무의미
   *    (fetch 는 layout 을 실어 보내지만 mountFlow/mountTimeline 은 좌표를 안 읽는다).
   *  Totals + 출처별: 원장 구간이 episodic·record·unsourced 라 고아 구간이 없다 →
   *    unlinked 가 아무것도 바꾸지 못한다(주제별일 때만 orphan 구간을 걷어낸다). */
  const GRID_ROWS = ["size", "unlinked", "layout"];
  function rowsFor(lv: Level, p: AtlasPrefs): string[] {
    if (lv.kind === "grid") return GRID_ROWS;
    if (!isScope(lv)) return [];
    switch (p.view) {
      case "3d":
      case "2d":
        return lv.kind === "l0" ? ["labels", "spread"]
          : ["size", "labels", "arrows", "unlinked", "layout", "spread"];
      case "flow": return ["depth", "arrows", "labels"];
      case "timeline": return lv.kind === "l0" ? [] : ["docs", "labels"];
      case "totals": return p.totalsGroup === "topic" ? ["group", "unlinked"] : ["group"];
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
    const vchip = (v: AtlasView) =>
      `<button class="chip${v === shown ? " active" : ""}" data-view="${v}">${viewName(v)}</button>`;
    const fchip = (k: "grid" | "matrix", label: string) =>
      `<button class="chip${lv.kind === k ? " active" : ""}" data-frame="${k}">${label}</button>`;

    // 두 줄로 나눈 이유: 첫 줄은 "어떻게 보는가"(크럼 불변), 둘째 줄은 "어디에 있는가"
    // (크럼이 자라고 뒤로가기가 먹는다). 한 줄 7칩은 이 차이를 지운다.
    const head = `
      <p class="hint" style="margin-top:0">${COPY.ui.panel.view}</p>
      <p><span class="seg">${VIEWS.map(vchip).join("")}</span></p>
      <p class="hint">${COPY.ui.panel.allTopics}</p>
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
      depth: row(COPY.ui.panel.depth,
        [1, 2, 3].map((d) => chip("flowDepth", String(d), String(d), p.flowDepth === d)).join("")),
      docs: row(COPY.ui.panel.docLinks,
        chip("timelineDocs", "on", COPY.ui.panel.on, p.timelineDocs)
        + chip("timelineDocs", "off", COPY.ui.panel.off, !p.timelineDocs)),
      group: row(COPY.ui.panel.groupBy,
        chip("totalsGroup", "topic", COPY.ui.panel.byTopic, p.totalsGroup === "topic")
        + chip("totalsGroup", "origin", COPY.ui.panel.byOrigin, p.totalsGroup === "origin")),
    };

    viewPanel.innerHTML = head
      + rowsFor(lv, p).map((k) => rows[k]).join("")
      + `<p class="hint" style="margin-bottom:0">${COPY.ui.panel.fixedNote}</p>`;
    viewPanel.querySelectorAll<HTMLElement>("[data-pref]").forEach((b) =>
      b.addEventListener("click", () => {
        const k = b.dataset.pref as keyof AtlasPrefs;
        const val = b.dataset.val!;
        const mut: Partial<AtlasPrefs> =
          k === "arrows" ? { arrows: val === "on" }
          : k === "timelineDocs" ? { timelineDocs: val === "on" }
          : k === "flowDepth" ? { flowDepth: Number(val) as 1 | 2 | 3 }
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
  function syncHash(v: AtlasView) {
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

  function drawCrumb() {
    const html = trail.map((lv, i) => {
      // 프레임 이름은 frameName 하나에서만 온다 — 크럼이 "Grid"를 직접 박고 있으면
      // 바로 옆 View 칩(덱 문구)과 언어가 갈린다("보기 · 그리드" 옆의 "Grid").
      const name = lv.kind === "l0" ? "Atlas" : lv.kind === "l1" ? lv.domain
        : lv.kind === "l2" ? lv.seed : frameName(lv);
      return `<button data-i="${i}">${name}</button>${i < trail.length - 1 ? "›" : ""}`;
    }).join(" ");
    // 평면 관점은 "장소"가 아니라 "보는 법"이라 크럼 꼬리는 클릭되지 않는다.
    // 프레임에서는 평면 관점이 그려지지 않으므로 꼬리도 달지 않는다(shownView=null).
    const v = shownView(trail[trail.length - 1]);
    const tail = v && isPlanar(v) ? ` <span class="chip chip-static">${viewName(v)}</span>` : "";
    crumbEl.innerHTML = html + tail;
    crumbEl.querySelectorAll("button").forEach((b) =>
      b.addEventListener("click", () => { trail.length = Number(b.dataset.i) + 1; show(trail[trail.length - 1], false); }));
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

  async function showL0() {
    setMsg(`<p class="hint">${COPY.atlas.loading(0)}</p>`);
    const res = await api.atlasL0(cfg.owner);
    const domains: { domain: string; episodes: number }[] = res.domains || [];
    const ties: { source: string; target: string; ties: number }[] = res.ties || [];
    if (!res.executed || domains.length === 0) {
      setMsg(`<div class="empty"><p>${COPY.atlas.idle}</p>
        <a class="btn btn-primary" href="#packs">${COPY.atlas.idleCta}</a></div>`);
      return;
    }
    setMsg("");
    const n = Math.max(domains.length, 1);
    const flat = curPrefs().view === "2d";   // 2D 에선 원환을 화면 평면(x/y)에 그대로
    const nodes: N3[] = domains.map((d, i) => {
      const ang = (2 * Math.PI * i) / n;
      return { id: d.domain, label: d.domain, kind: "domain", records: d.episodes,
               x: Math.cos(ang), y: Math.sin(ang) * (flat ? 1 : 0.25), z: flat ? 0 : Math.sin(ang) };
    });
    // Bridge 전용 도메인(레코드 0 — 예: 브릿지 매트릭스의 외부 축)은 L0 노드가
    // 없으므로 결합에서 제외 — 미지 노드 참조는 렌더러가 거부한다.
    const known = new Set(nodes.map((d) => d.id));
    const links: L3[] = ties
      .filter((t) => t.source !== t.target && known.has(t.source) && known.has(t.target))
      .map((t) => ({ source: t.source, target: t.target, cross: true, weight: t.ties }));
    g3 = mount3d(canvas, nodes, links, {
      onClick: (nd) => { trail.push({ kind: "l1", domain: nd.id }); show(trail[trail.length - 1]); },
      onLinkClick: (l) => {
        const a = typeof l.source === "object" ? l.source.id : l.source;
        const b = typeof l.target === "object" ? l.target.id : l.target;
        trail.push({ kind: "compare", a, b }); show(trail[trail.length - 1]);
      },
    }, curPrefs());
    // 관점 발견 힌트(1회) — L0 는 wireLasso 를 돌리지 않으므로 힌트 알약이 비어 있다.
    if (localStorage.getItem("vinc.atlas.views.seen") !== "1") {
      lassoHint.textContent = COPY.ui.atlasx.viewsHint;
      lassoHint.hidden = false;
    }
  }

  function openDetail(nd: N3) {
    // 방향성 관계(화살촉 3종)만 in/out 분리 표기 — 대칭(relates_to)은 방향이 무의미
    const DIRECTED = ["PREREQUISITE_OF", "DERIVES_FROM", "INHERITS_FROM"];
    const out = curEdges.filter((e) => e.source === nd.id && DIRECTED.includes(e.type)).length;
    const inn = curEdges.filter((e) => e.target === nd.id && DIRECTED.includes(e.type)).length;
    // 부착(F2) — 브릿지·에피소드·레코드 팔은 화살촉 없는 구조 연결이라 별도 집계
    const ATTACH = ["CONNECTS", "ABOUT", "ATTACHES_TO"];
    const att = curEdges.filter((e) =>
      (e.source === nd.id || e.target === nd.id) && ATTACH.includes(e.type)).length;
    detail.hidden = false;
    detail.innerHTML = `
      <h2>${nd.label}</h2>
      <p><span class="badge">${COPY.ui.atlasx.domain(nd.domain || COPY.ui.common.none)}</span></p>
      <p class="hint">${COPY.ui.atlasx.connections(nd.degree || 0)}${out + inn > 0
        ? COPY.ui.atlasx.outIn(out, inn) : ""}${att > 0
        ? COPY.ui.atlasx.attachments(att) : ""}${nd.gateway ? COPY.ui.atlasx.gateway : ""}.</p>
      <button class="btn btn-primary" id="focus">${COPY.ui.atlasx.traceCta}</button>
      <div id="node-source"></div>
      <div id="ties-suggest"></div>`;
    detail.querySelector("#focus")!.addEventListener("click", () => {
      trail.push({ kind: "l2", seed: nd.id, domain: nd.domain }); show(trail[trail.length - 1]);
    });
    // D45-V — 정본 근거: 노드 설명(one_liner) + 이 노드를 언급한 문서 발췌.
    void loadNodeSource(nd);
    // D32 고아 최소화 — 연결 0 이면 잇기 후보(태그·도메인 근사, D25 전 단계)를
    // 제안하고, Bind 는 fragment MERGE(단일 쓰기 표면)로 확정한다.
    if ((nd.degree || 0) === 0) void suggestTiesInto(nd);
  }

  async function openEdgeDetail(e: { source: string; target: string; type: string }) {
    // D45-V 1단계 — 흐름 선 클릭: "왜 A 가 B 로 이어지는가". 클릭=고정(pin) —
    // 상세 패널 자리를 그대로 쓴다(노드 상세와 같은 표면, ESC/다른 클릭으로 이동).
    const esc2 = (s: string) => s.replace(/[&<>"]/g, (c) =>
      c === "&" ? "&amp;" : c === "<" ? "&lt;" : c === ">" ? "&gt;" : "&quot;");
    detail.hidden = false;
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
            <span class="title" style="font-size:.85em">${esc2(t)}</span>
            <p class="hint" style="margin:.15rem 0 0">${esc2(body)}</p>
          </div>` : "";
      };
      detail.innerHTML = `
        <h2>${esc2(a)} → ${esc2(b)}</h2>
        <p>${esc2(sentence)}${conf}</p>
        <p class="hint" style="margin-bottom:.3rem">${COPY.ui.atlasx.relEvidence}</p>
        ${res.evidence
          ? `<p>${esc2(res.evidence)}</p>`
          : `<p class="hint">${COPY.ui.atlasx.relEvidenceNone}</p>`}
        ${side(a, res.src)}${side(b, res.dst)}`;
    } catch {
      if (!detail.hidden) detail.innerHTML = `<p class="hint">${COPY.ui.atlasx.relFail}</p>`;
    }
  }

  async function loadNodeSource(nd: N3) {
    // 발췌는 증강 — 실패·부재가 상세 패널을 깨지 않는다(문구만 남기고 조용히).
    const box = detail.querySelector<HTMLElement>("#node-source");
    if (!box) return;
    box.innerHTML = `<p class="hint">${COPY.ui.atlasx.sourceLoading}</p>`;
    const esc = (s: string) => s.replace(/[&<>"]/g, (c) =>
      c === "&" ? "&amp;" : c === "<" ? "&lt;" : c === ">" ? "&gt;" : "&quot;");
    try {
      const res = await api.nodeContext(cfg.owner, nd.id);
      if (!res.executed) { box.innerHTML = ""; return; }
      // 패널이 그 사이 다른 노드로 갱신됐으면 버린다(늦은 응답이 남의 상세를 덮지 않게).
      if (!detail.contains(box) || detail.hidden) return;
      const parts: string[] = [];
      if (res.one_liner) parts.push(`<p>${esc(res.one_liner)}</p>`);
      const ex = res.excerpts || [];
      if (ex.length > 0) {
        const header = res.arm === "fts"
          ? COPY.ui.atlasx.sourceRelated : COPY.ui.atlasx.sourceHeader;
        parts.push(`<p class="hint" style="margin-bottom:.3rem">${header}</p>` +
          ex.map((e) => `
            <div class="list-row" style="display:block">
              <span class="title" style="font-size:.85em">${esc(e.doc_title)}${
                e.section ? `<span class="hint"> · ${esc(e.section)}</span>` : ""}</span>
              <p class="hint" style="margin:.15rem 0 0">${esc(e.excerpt)}</p>
            </div>`).join(""));
      } else if (!res.one_liner) {
        parts.push(`<p class="hint">${COPY.ui.atlasx.sourceNone}</p>`);
      }
      box.innerHTML = parts.join("");
    } catch {
      box.innerHTML = "";   // 발췌 불가(구 사이드카 등) — 상세는 그대로 유효
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
            detail.hidden = false;                        // show() 가 닫으므로 확인 문구 재표시
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

  async function showL1(domain: string) {
    setMsg(`<p class="hint">${COPY.atlas.loading(1)}</p>`);
    const res = await api.graph(cfg.owner, { domain, layout: loadPrefs().layout });
    if (!res.executed || (res.nodes || []).length === 0) {
      setMsg(`<div class="empty"><p>${COPY.atlas.idle}</p></div>`); return;
    }
    setMsg("");
    curEdges = res.edges || [];
    g3 = mount3d(canvas, filterUnlinked(toN3(res.nodes, domain)), toL3(res.edges), {
      onClick: (nd) => {
        if (nd.gateway) { trail.push({ kind: "l1", domain: nd.domain! }); show(trail[trail.length - 1]); }
        else openDetail(nd);
      },
      onRightClick: (nd) => { trail.push({ kind: "l2", seed: nd.id, domain: nd.domain }); show(trail[trail.length - 1]); },
    }, curPrefs());
    wireLasso();
  }

  async function showL2(seed: string) {
    setMsg(`<p class="hint">${COPY.atlas.loading(1)}</p>`);
    const res = await api.graph(cfg.owner, { seed, depth: 2, layout: loadPrefs().layout });
    if (!res.executed) { setMsg(`<div class="error-banner">${COPY.atlas.error}</div>`); return; }
    setMsg("");
    curEdges = res.edges || [];
    fillSeedDomain(seed, res.nodes || []);
    g3 = mount3d(canvas, filterUnlinked(toN3(res.nodes, undefined, seed)), toL3(res.edges), {
      onClick: (nd) => openDetail(nd),
      onRightClick: (nd) => { trail.push({ kind: "l2", seed: nd.id, domain: nd.domain }); show(trail[trail.length - 1]); },
    }, curPrefs());
    wireLasso();
  }

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
          <button class="chip" data-cycle="${d}" title="Cycle layout (D34)"
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
            return `<td data-a="${a}" data-b="${b}" title="${v} ties" style="width:3.2rem;height:2.4rem;
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
      try {
        // l2 는 ego 롤업(씨앗 주변 — L2 서브그래프와 같은 depth 2 순회)을 요청한다.
        // domain 은 **구버전 폴백 힌트**로 함께 싣는다: 구 사이드카는 seed 를 조용히
        // 무시하므로, domain 이 실려 있어야 전체 롤업 대신 주제 롤업으로 떨어진다.
        res = await api.ledger(cfg.owner,
          seed !== undefined ? { seed, depth: 2, ...(domain ? { domain } : {}) }
          : domain ? { domain } : {});
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
      } catch {
        setMsg(`<div class="empty"><p>${COPY.ui.atlasx.totalsOff}</p></div>`);
        return;
      }
      // ego 인구 0(씨앗 부재·부착 없는 씨앗)도 rows=[] 로 온다 — 기존 빈 상태 그대로.
      if (rows.length === 0) { setMsg(`<div class="empty"><p>${COPY.ui.atlasx.totalsEmpty}</p></div>`); return; }
      setMsg("");
      planar = mountTotals(canvas, rows, {
        group: p.totalsGroup, unlinked: p.unlinked, onOpen: openTopic,
        // 눌러도 갈 곳이 없는 행 = openTopic 이 무동작으로 삼키는 그 행. 판정 조건은
        // openTopic 의 가드와 글자 그대로 같아야 한다(L1 에서 자기 주제일 때만).
        current: level.kind === "l1" ? level.domain : undefined,
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

    // L0 — 주제 층. Flow 는 이미 받아온 ties 를 방향 그래프로 그대로 쓴다(백엔드 작업 없음).
    if (level.kind === "l0") {
      setMsg(`<p class="hint">${COPY.atlas.loading(0)}</p>`);
      const res = await api.atlasL0(cfg.owner);
      const domains: { domain: string; episodes: number }[] = res.domains || [];
      if (!res.executed || domains.length === 0) {
        setMsg(`<div class="empty"><p>${COPY.atlas.idle}</p>
          <a class="btn btn-primary" href="#packs">${COPY.atlas.idleCta}</a></div>`);
        return;
      }
      // 주제 노드에는 날짜가 없다 — L0 Timeline 은 어떤 옵션을 눌러도 이 화면이라
      // 옵션 행 자체를 내지 않고(rowsFor), 막다른 골목 대신 "열 수 있는 주제"를
      // 그대로 내놓는다: 문구가 가리키는 다음 행동이 곧 화면에 있다.
      if (p.view === "timeline") {
        setMsg(`<div class="empty"><p>${COPY.ui.atlasx.topicsNoDates}</p>
          <p><span class="seg">${domains.slice(0, 9).map((d) =>
            `<button class="chip" data-topic="${d.domain}">${d.domain}</button>`).join("")}</span></p></div>`);
        msg.querySelectorAll<HTMLElement>("[data-topic]").forEach((b) =>
          b.addEventListener("click", () => openTopic(b.dataset.topic!)));
        return;
      }
      const topics = domains.map((d) => ({ id: d.domain, label: d.domain, domain: d.domain }));
      const known = new Set(topics.map((t) => t.id));
      const ties: { source: string; target: string; ties: number }[] = res.ties || [];
      const links: FlowEdge[] = ties
        .filter((t) => t.source !== t.target && known.has(t.source) && known.has(t.target))
        .map((t) => ({ source: t.source, target: t.target, type: "TIES", weight: t.ties }));
      if (links.length === 0) { setMsg(`<div class="empty"><p>${COPY.ui.atlasx.flowNoTies}</p></div>`); return; }
      setMsg("");
      planar = mountFlow(canvas, topics, links, {
        depth: p.flowDepth, arrows: p.arrows, labels: p.labels !== "none", onOpen: openTopic,
      });
      return;
    }

    // L1/L2 — 3D 와 똑같은 서브그래프를 받아 평면으로만 다시 그린다(별도 계약 없음).
    setMsg(`<p class="hint">${COPY.atlas.loading(1)}</p>`);
    const res = seed !== undefined
      ? await api.graph(cfg.owner, { seed, depth: 2, layout: p.layout })
      : await api.graph(cfg.owner, { domain, layout: p.layout });
    if (!res.executed) { setMsg(`<div class="error-banner">${COPY.atlas.error}</div>`); return; }
    const gnodes: GraphNode[] = res.nodes || [];
    curEdges = res.edges || [];
    if (seed !== undefined) fillSeedDomain(seed, gnodes);
    if (gnodes.length === 0) { setMsg(`<div class="empty"><p>${COPY.atlas.idle}</p></div>`); return; }
    const nodes = gnodes.map((nd) => ({
      id: nd.id,
      label: nd.label === nd.id ? nd.id.replace(/^[a-z]+:/, "") : nd.label,
      domain: nd.domain, ts: nd.ts, kind: nd.kind || "concept",
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
      if (dated.length === 0) { setMsg(`<div class="empty"><p>${COPY.ui.atlasx.noDates}</p></div>`); return; }
      setMsg(solo ? `<div class="empty"><p>${COPY.ui.atlasx.soloNode}</p></div>` : "");
      planar = mountTimeline(canvas, nodes, {
        docs: p.timelineDocs, labels: p.labels !== "none", onOpen: openNode,
      });
      // 날짜 없는 문서 수는 차트 아래 캡션 알약으로 — 레인에 못 올린 만큼을 밝힌다.
      const undated = shown.length - dated.length;
      if (!solo && undated > 0) {
        lassoHint.textContent = COPY.ui.atlasx.undated(undated);
        lassoHint.hidden = false;
      }
      return;
    }

    const DIRECTED = ["PREREQUISITE_OF", "DERIVES_FROM", "INHERITS_FROM"];
    const dirEdges = curEdges.filter((e) => DIRECTED.includes(e.type));
    // 씨앗 리니지는 **씨앗에 걸린** 방향 엣지에서만 자란다. 판정을 전체 dirEdges 로
    // 하면, 이웃끼리만 방향 엣지가 있고 씨앗은 대칭 관계뿐인 흔한 서브그래프에서
    // 점 하나만 덩그러니 그려지고 문구가 한 줄도 안 붙는다(설명 없는 막다른 골목).
    const lone = seed !== undefined
      && !dirEdges.some((e) => e.source === seed || e.target === seed);
    if (seed === undefined ? dirEdges.length === 0 : lone) {
      // 씨앗이 없는 층(L1)은 그릴 것이 아예 없으니 문구만. 씨앗이 있으면 점 하나는
      // 그려서 "여기 있다"를 남기고 문구를 덧댄다(정말 혼자면 그 사실을 먼저 말한다).
      setMsg(`<div class="empty"><p>${solo ? COPY.ui.atlasx.soloNode : COPY.ui.atlasx.flowNone}</p></div>`);
      if (seed === undefined) return;
    } else setMsg("");
    planar = mountFlow(canvas, nodes, dirEdges, {
      seed, depth: p.flowDepth, arrows: p.arrows, labels: p.labels !== "none", onOpen: openNode,
      // D45-V 1단계 — 실엣지 층(L1/L2)에서만 선 클릭 → 관계 패널. L0 은 주제 집계
      // 엣지(TIES — 그래프 관계 아님)라 배선하지 않는다(showPlanar L0 분기 참조).
      onEdge: (e) => void openEdgeDetail(e),
    });
  }

  async function show(level: Level, push = true) {
    if (push && trail[trail.length - 1] !== level) { /* pushed by caller */ }
    detail.hidden = true;
    tools.innerHTML = "";
    float.hidden = true;
    lassoHint.hidden = true;   // 기본 숨김 — L1/L2 의 wireLasso 가 다시 켠다
    closePanel();
    extraSigmas.forEach((s) => s.kill());
    extraSigmas = [];
    if (minis3d) { minis3d.dispose(); minis3d = null; }
    if (planar) { planar.dispose(); planar = null; }
    if (g3) { g3._destructor?.(); g3 = null; }
    canvas.innerHTML = "";
    drawCrumb();
    // View 컨트롤은 모든 층에 — Compare/Grid/Matrix 에서도 관점으로 되돌아갈 길이 있어야 한다.
    viewControl();
    try {
      // 관점이 Level 보다 먼저다 — 평면 관점에서는 mount3d 가 아예 도달 불가능해진다.
      const v = curPrefs().view;
      if (level.kind === "compare") await showCompare(level.a, level.b);
      else if (level.kind === "grid") await showGrid();
      else if (level.kind === "matrix") await showMatrix();
      else if (isPlanar(v)) await showPlanar(level);
      else if (level.kind === "l0") await showL0();
      else if (level.kind === "l1") await showL1(level.domain);
      else await showL2(level.seed);
    } catch {
      setMsg(`<div class="error-banner">${COPY.atlas.error}</div>`);
    }
  }

  trail.push({ kind: "l0" });
  if (deep === "compare" && parts[2] && parts[3]) trail.push({ kind: "compare", a: parts[2], b: parts[3] });
  else if (deep === "trace" && parts[2]) trail.push({ kind: "l2", seed: parts[2] });
  else if (deep && deep !== "as") trail.push({ kind: "l1", domain: deep });
  await show(trail[trail.length - 1]);
}
