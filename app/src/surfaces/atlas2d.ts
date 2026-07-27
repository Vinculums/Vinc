import Graph from "graphology";
import Sigma from "sigma";
import { EdgeArrowProgram } from "sigma/rendering";
import type { GraphEdge, GraphNode } from "../api";
import { COPY } from "../copy";
import { palette as signal, hexStr } from "../theme";
import type { AtlasPrefs } from "../atlas-prefs";

/** Atlas 2D 관점 — 독립 렌더러 (DEC D46).
 *
 *  **왜 새로 짓는가.** 예전 2D 는 3D 씬(atlas3d.ts)에 `flat` 플래그를 꽂아 z 를
 *  0 으로 만든 **투영**이었다. 그래서 세로 위치가 시간도 위계도 아닌
 *  `y + z*0.55`(3D 깊이의 잔상)였다 — 2D 인데 세로에 아무 뜻이 없었다.
 *  여기서는 사이드카가 준 **xy 평면 좌표만** 쓴다(z 는 읽지 않는다). D17 결정론
 *  계약 그대로이고 좌표 소유권도 그대로 사이드카에 있다.
 *
 *  **무엇을 2D 답게 만드는가**(레퍼런스 해부 [[vinc.d.view-redesign-research]] §5.1
 *  C안 — 축을 새로 두지 않고 네 문법만 이식):
 *    ① 허브 중심 방사(크기 = √연결수, 이미 D32 서열이 좌표에 반영돼 있다)
 *    ② 허브 노드에 **수치 배지**(연결 수) — 마크에 "얼마나"가 붙는다
 *    ③ 허브 라벨 **상시 노출**(hover 를 기다리지 않는다)
 *    ④ 하단 **지표바** — 지도를 보면서 전체 수치를 잃지 않는다
 *  넷 다 축 없이 성립하고 닫힌 층(모노크롬·초점색 1점·형태=타입)을 건드리지 않는다.
 *
 *  렌더러는 sigma(WebGL) — 이미 의존성이고 Compare 뷰가 쓰던 것을 관점으로
 *  승격했다. 배지·지표바는 캔버스 위 DOM 오버레이라 툴팁(tooltip.ts)과 같은 층에
 *  산다. 색은 Signal 팔레트만 읽는다(hex 하드코딩 금지). */

/** fit: 카메라를 그래프 전체에 맞춘다(`f` 키 — 정본 §2). */
export type Handle2d = { dispose(): void; fit(): void };

/** 방향 3종 — 화살촉이 붙는 관계(atlas-planar 의 DIRECTED 와 같은 정의). */
const DIRECTED = ["PREREQUISITE_OF", "DERIVES_FROM", "INHERITS_FROM"];

function pal() {
  return signal[document.documentElement.dataset.theme === "dark" ? "dark" : "light"];
}
const ink = () => hexStr(pal().text);
const inkMute = () => hexStr(pal().textMute);
const line = () => hexStr(pal().line);
const point = () =>
  hexStr(document.documentElement.dataset.theme === "dark" ? pal().ring : pal().primary);

/** D32-a 물러남 수치 — app.css 의 `--recede-*` 와 **같은 값**이어야 한다.
 *  sigma 는 CSS 를 타지 않는 WebGL 이라 여기서 한 번 읽어 온다(정본은 CSS 변수). */
function recede(name: "node" | "line" | "text", fallback: number): number {
  const v = getComputedStyle(document.documentElement).getPropertyValue(`--recede-${name}`);
  const n = Number.parseFloat(v);
  return Number.isFinite(n) ? n : fallback;
}
/** 물러난 마크의 색 — 불투명도 대신 잉크를 배경 쪽으로 섞는다(sigma 는 노드
 *  알파를 따로 받지 않는다). 결과는 CSS 의 opacity 와 같은 인상이다. */
function mix(fg: string, bg: string, t: number): string {
  const hx = (s: string) => [1, 3, 5].map((i) => parseInt(s.slice(i, i + 2), 16));
  const [r1, g1, b1] = hx(fg);
  const [r2, g2, b2] = hx(bg);
  const c = (a: number, b: number) => Math.round(b + (a - b) * t).toString(16).padStart(2, "0");
  return `#${c(r1, r2)}${c(g1, g2)}${c(b1, b2)}`;
}

export function mount2d(el: HTMLElement, nodes: GraphNode[], edges: GraphEdge[],
  o: { onClick: (n: GraphNode) => void; onRightClick?: (n: GraphNode, ev: MouseEvent) => void;
       /** 선 클릭 → 관계 근거 패널(D45-V). 흐름 관점이 폐기되면서(D53) 이 진입점이
        *  여기로 옮겨 왔다 — 방향 엣지가 화살촉을 달고 그려지는 유일한 자리다. */
       onEdge?: (e: GraphEdge) => void; seed?: string },
  prefs: AtlasPrefs): Handle2d {
  const bg = hexStr(pal().background);
  el.innerHTML = `<div class="a2-stage"></div>
    <div class="a2-badges"></div>
    <canvas class="a2-minimap" width="180" height="120" hidden></canvas>`;
  const stage = el.querySelector<HTMLElement>(".a2-stage")!;
  const badgeBox = el.querySelector<HTMLElement>(".a2-badges")!;
  const mini = el.querySelector<HTMLCanvasElement>(".a2-minimap")!;

  const byId = new Map(nodes.map((n) => [n.id, n]));
  const shown = prefs.unlinked === "hide"
    ? nodes.filter((n) => (n.degree || 0) > 0 || n.id === o.seed)
    : nodes;
  // 밀도 적응 크기(D51) — sigma 는 그래프를 캔버스에 **맞춰 넣는데(auto-fit)** 노드
  // 크기는 절대 px 다. 그래서 좌표를 아무리 키워도(=`spread`) 화면은 그대로이고,
  // 노드만 서로 먹는다. 실측: 74노드에서 shell 54%·organic 65%·circle 51% 가 이웃과
  // 겹쳤다. 해법은 좌표가 아니라 **크기**다 — 실제 최근접 이웃 거리를 재서 그 절반을
  // 넘지 않게 상한을 건다. 재는 비용은 마운트 1회이고 큰 그래프는 표본으로 줄인다.
  const density = (() => {
    const pts = shown.map((n) => [(n.x ?? 0), (n.y ?? 0)] as const);
    if (pts.length < 2) return Infinity;
    const w = Math.max(...pts.map((p) => p[0])) - Math.min(...pts.map((p) => p[0]));
    const h = Math.max(...pts.map((p) => p[1])) - Math.min(...pts.map((p) => p[1]));
    // 렌더 배율(px/단위) — sigma 의 fit 과 같은 규칙(짧은 축 기준).
    const k = Math.min((el.clientWidth || 1200) / Math.max(w, 1e-6),
                       (el.clientHeight || 700) / Math.max(h, 1e-6));
    const step = pts.length > 1200 ? Math.ceil(pts.length / 1200) : 1;   // 표본
    const nn: number[] = [];
    for (let i = 0; i < pts.length; i += step) {
      let best = Infinity;
      for (let j = 0; j < pts.length; j++) {
        if (i === j) continue;
        const dx = pts[i][0] - pts[j][0], dy = pts[i][1] - pts[j][1];
        const d = dx * dx + dy * dy;
        if (d < best) best = d;
      }
      if (best < Infinity) nn.push(Math.sqrt(best) * k);
    }
    nn.sort((a, b) => a - b);
    return nn.length ? nn[Math.floor(nn.length / 2)] : Infinity;
  })();
  /** 상한(min)이 아니라 **비례 축소**다. 상한을 걸면 큰 노드만 깎여 허브 크기 신호가
   *  죽는다(D32 의 "허브가 커 보인다"가 사라진다 — 실측에서 전부 같은 점이 됐다).
   *  전체에 같은 배율을 곱하면 겹침은 사라지고 위계는 남는다. 0.35 하한은 너무
   *  작아져 마크 자체가 안 보이는 것을 막는다(그 아래는 줌으로 볼 일이다). */
  const baseSize = (n: GraphNode) =>
    prefs.size === "uniform" ? 6 : 4 + Math.sqrt(n.degree || 1) * 2;
  const shrink = (() => {
    if (!Number.isFinite(density) || !shown.length) return 1;
    const meds = shown.map(baseSize).sort((a, b) => a - b);
    const medR = meds[Math.floor(meds.length / 2)];
    return Math.min(1, Math.max(0.35, density / (2 * medR)));
  })();
  const sizeOf = (n: GraphNode) => baseSize(n) * shrink;

  const g = new Graph();
  for (const n of shown) {
    // z 는 읽지 않는다 — 그것이 이 렌더러의 존재 이유다(위 주석).
    g.addNode(n.id, {
      label: n.label === n.id ? n.id.replace(/^[a-z]+:/, "") : n.label,
      x: (n.x ?? 0) * prefs.spread, y: (n.y ?? 0) * prefs.spread,
      size: sizeOf(n), color: n.id === o.seed ? point() : ink(),
    });
  }
  // D50 — 방향 관계는 **뼈대**, 나머지(RELATED_TO 등)는 **살**이다. 흐름 관점이
  // 방향 3종만 그려 나머지를 통째로 버리는 것과 달리, 여기서는 둘 다 그리되 위계를
  // 준다: 방향 엣지만 화살촉을 달고 진하게, 무방향은 옅고 가늘게 배경으로 깐다.
  // 그래야 `rank` 프리셋에서 좌표가 만드는 흐름을 가리지 않는다.
  const edgeOf = new Map<string, GraphEdge>();
  for (const e of edges) {
    if (!g.hasNode(e.source) || !g.hasNode(e.target)) continue;
    if (g.hasEdge(e.source, e.target)) continue;
    const dir = DIRECTED.includes(e.type);
    // 정본 §1.2 는 관계를 **선의 형태**로 가른다(실선·파선·점선·이중선). sigma 는
    // 파선 프로그램이 없어 형태 대신 **굵기·농도 계단**으로 같은 서열을 만든다:
    // 방향(뼈대) > 같은 주제 무방향(살) > 주제를 넘는 연결(배경 쪽) > 다리 매개.
    // 범례도 이 서열을 그대로 설명한다(선 4종). 진짜 파선은 후속(커스텀 프로그램).
    const bridged = e.type === "CONNECTS";
    const key = g.addEdge(e.source, e.target, {
      // 화살촉은 열린 층(prefs.arrows)이 켜져 있고 방향 관계일 때만.
      type: dir && prefs.arrows ? "arrow" : "line",
      size: dir ? 1.8 : bridged ? 0.5 : e.cross_domain ? 0.6 : 0.9,
      color: dir ? inkMute()
        : bridged ? mix(line(), bg, 0.22)
        : e.cross_domain ? mix(line(), bg, 0.28) : mix(line(), bg, 0.35),
      directed: dir,
    });
    edgeOf.set(key, e);
  }

  // 허브 = 연결수 상위. 라벨과 배지는 여기에만 상시로 붙인다(전부 켜면 소음).
  const hubs = [...shown]
    .sort((a, b) => (b.degree || 0) - (a.degree || 0) || (a.id < b.id ? -1 : 1))
    .slice(0, 8)
    .filter((n) => (n.degree || 0) > 0);
  const hubSet = new Set(hubs.map((n) => n.id));

  const sigma = new Sigma(g, stage, {
    allowInvalidContainer: true,
    // 화살촉 프로그램은 기본 등록이 아니다 — 켜 두지 않으면 type:"arrow" 가 무시된다.
    edgeProgramClasses: { arrow: EdgeArrowProgram },
    renderLabels: prefs.labels !== "none",
    // **허브만** 상시 라벨(레퍼런스가 얻던 판독성). 문턱 8px = 연결수 4 이상이라
    // 위성까지 다 켜지지 않는다 — 나머지는 hover 때 sigma 가 그린다.
    // (size=uniform 이면 전부 6px 이라 상시 라벨이 없다 — 위계를 끈 선택의 결과.)
    labelRenderedSizeThreshold: prefs.labels === "none" ? 1e9 : 8,
    labelColor: { color: ink() },
    // 라벨은 노드 오른쪽으로 뻗는데 rank 프리셋의 열 간격이 라벨보다 좁아 옆 열까지
    // 침범한다. 격자당 하나만 남겨(밀도 제한) 겹침을 줄인다 — 나머지는 hover 로 뜬다.
    labelGridCellSize: 140,
    labelDensity: 0.6,
  });

  // ── 초점 문법(D32-a) — 초점과 이웃만 남고 나머지는 물러난다 ────────────────
  const nbr = new Map<string, Set<string>>();
  for (const e of edges) {
    if (!g.hasNode(e.source) || !g.hasNode(e.target)) continue;
    (nbr.get(e.source) || nbr.set(e.source, new Set()).get(e.source)!).add(e.target);
    (nbr.get(e.target) || nbr.set(e.target, new Set()).get(e.target)!).add(e.source);
  }
  let focus: string | null = null;
  const keepOf = (id: string) => new Set([id, ...(nbr.get(id) || [])]);
  sigma.setSetting("nodeReducer", (id, data) => {
    if (!focus) return data;
    const keep = keepOf(focus);
    if (keep.has(id)) {
      return id === focus ? { ...data, color: point(), zIndex: 1 } : data;
    }
    return { ...data, color: mix(ink(), bg, recede("node", 0.4)), label: "" };
  });
  sigma.setSetting("edgeReducer", (id, data) => {
    if (!focus) return data;
    const [s, t] = g.extremities(id);
    const on = s === focus || t === focus;
    return on ? { ...data, color: point(), size: (data.size as number) * 1.4 }
              : { ...data, color: mix(line(), bg, recede("line", 0.12)) };
  });

  // ── 수치 배지 + 지표바(캔버스 위 DOM) ─────────────────────────────────────
  badgeBox.innerHTML = hubs.map((n) =>
    `<span class="a2-badge" data-id="${n.id.replace(/"/g, "&quot;")}"`
    + ` data-tip="${COPY.ui.atlasx.badgeTip(n.label, n.degree || 0)}">${n.degree || 0}</span>`).join("");
  const badges = [...badgeBox.querySelectorAll<HTMLElement>(".a2-badge")];
  /** 배지를 노드 위에 얹는다 — 카메라가 움직일 때마다 좌표만 다시 계산한다. */
  function placeBadges() {
    for (const b of badges) {
      const id = b.dataset.id!;
      if (!g.hasNode(id)) { b.style.display = "none"; continue; }
      const p = sigma.graphToViewport({ x: g.getNodeAttribute(id, "x") as number,
                                        y: g.getNodeAttribute(id, "y") as number });
      const r = sigma.getNodeDisplayData(id)?.size ?? 6;
      b.style.display = "";
      b.style.transform = `translate(${Math.round(p.x + r + 3)}px, ${Math.round(p.y - r - 12)}px)`;
    }
  }
  sigma.on("afterRender", placeBadges);

  // 지표 4종은 표면 공용 하단 바가 그린다(atlas.ts drawMetrics) — 관점마다
  // 다른 자리에 다른 수치가 뜨지 않게 한 곳에서만 센다.

  // ── 미니맵(정본 §2) — **줌인했을 때만** 뜬다 ────────────────────────────
  // 전체가 이미 화면에 들어와 있을 때(기본 auto-fit) 미니맵은 같은 그림을 한 번 더
  // 그리는 소음일 뿐이다. 카메라 ratio 가 1 보다 작아진(=파고든) 순간에만 나타나
  // "지금 보는 곳이 전체의 어디인가"에 답한다. 클릭 = 그 자리로 점프.
  const mctx = mini.getContext("2d")!;
  const pts = shown.map((n) => ({ x: (n.x ?? 0) * prefs.spread, y: (n.y ?? 0) * prefs.spread }));
  const bx0 = Math.min(...pts.map((p) => p.x), 0), bx1 = Math.max(...pts.map((p) => p.x), 0);
  const by0 = Math.min(...pts.map((p) => p.y), 0), by1 = Math.max(...pts.map((p) => p.y), 0);
  const mw = mini.width, mh = mini.height, mpad = 8;
  const mx = (x: number) => mpad + ((x - bx0) / Math.max(bx1 - bx0, 1e-6)) * (mw - mpad * 2);
  const my = (y: number) => mpad + ((y - by0) / Math.max(by1 - by0, 1e-6)) * (mh - mpad * 2);
  function drawMini() {
    const cam = sigma.getCamera();
    // ratio >= 1 이면 전체가 보인다 = 미니맵이 답할 질문이 없다.
    if (cam.ratio >= 0.98) { mini.hidden = true; return; }
    mini.hidden = false;
    mctx.clearRect(0, 0, mw, mh);
    mctx.fillStyle = mix(ink(), bg, 0.5);
    for (const p of pts) { mctx.beginPath(); mctx.arc(mx(p.x), my(p.y), 1.3, 0, 6.284); mctx.fill(); }
    // 현재 뷰포트 사각형 — 카메라 중심(그래프 좌표)과 배율에서 역산한다.
    const tl = sigma.viewportToGraph({ x: 0, y: 0 });
    const br = sigma.viewportToGraph({ x: stage.clientWidth, y: stage.clientHeight });
    mctx.strokeStyle = point();
    mctx.lineWidth = 1.2;
    mctx.strokeRect(mx(tl.x), my(tl.y), mx(br.x) - mx(tl.x), my(br.y) - my(tl.y));
  }
  sigma.on("afterRender", drawMini);
  mini.addEventListener("click", (e) => {
    const r = mini.getBoundingClientRect();
    const gx = bx0 + ((e.clientX - r.left - mpad) / (mw - mpad * 2)) * (bx1 - bx0);
    const gy = by0 + ((e.clientY - r.top - mpad) / (mh - mpad * 2)) * (by1 - by0);
    const v = sigma.graphToViewport({ x: gx, y: gy });
    const c = sigma.viewportToFramedGraph(v);
    sigma.getCamera().animate({ x: c.x, y: c.y }, { duration: 260 });
  });

  // ── 노드 드래그 = 자리 옮기기 + 핀(정본 §2) ──────────────────────────────
  // 좌표 정본은 사이드카(D17)라 여기서 옮긴 자리는 **이 세션에서만** 산다.
  // 그래서 옮긴 노드에는 핀 표기를 달아 "내가 손댄 자리"임을 화면이 밝힌다 —
  // 새로고침하면 원래 배치로 돌아간다는 사실을 숨기지 않는다.
  let dragging: string | null = null;
  const pinned = new Set<string>();
  sigma.on("downNode", ({ node, event }) => {
    dragging = node;
    sigma.getCamera().disable();
    event.preventSigmaDefault();
  });
  stage.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    const r = stage.getBoundingClientRect();
    const p = sigma.viewportToGraph({ x: e.clientX - r.left, y: e.clientY - r.top });
    g.setNodeAttribute(dragging, "x", p.x);
    g.setNodeAttribute(dragging, "y", p.y);
  });
  const endDrag = () => {
    if (!dragging) return;
    pinned.add(dragging);
    g.setNodeAttribute(dragging, "type", "circle");
    dragging = null;
    sigma.getCamera().enable();
    placeBadges();
  };
  stage.addEventListener("mouseup", endDrag);
  stage.addEventListener("mouseleave", endDrag);

  // ── 입력 ────────────────────────────────────────────────────────────────
  sigma.on("enterNode", ({ node }) => { focus = node; sigma.refresh(); });
  sigma.on("leaveNode", () => { focus = null; sigma.refresh(); });
  sigma.on("clickNode", ({ node }) => { const n = byId.get(node); if (n) o.onClick(n); });
  // 선 클릭 → 관계 근거(D45-V). 방향 관계에만 근거가 실리므로 그때만 연다.
  sigma.on("clickEdge", ({ edge }) => {
    const e = edgeOf.get(edge);
    if (e && o.onEdge && DIRECTED.includes(e.type)) o.onEdge(e);
  });
  sigma.on("rightClickNode", ({ node, event }) => {
    event.preventSigmaDefault();
    event.original.preventDefault();
    const n = byId.get(node);
    if (n && o.onRightClick) o.onRightClick(n, event.original as MouseEvent);
  });

  return {
    dispose() {
      sigma.removeAllListeners();
      sigma.kill();
      el.innerHTML = "";
    },
    fit() {
      // sigma 의 기본 카메라 상태 = auto-fit 결과. 되돌리면 곧 "전체 맞춤"이다.
      sigma.getCamera().animate({ x: 0.5, y: 0.5, ratio: 1, angle: 0 }, { duration: 300 });
    },
  };
}
