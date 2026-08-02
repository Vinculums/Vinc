import Graph from "graphology";
import Sigma from "sigma";
import { EdgeArrowProgram } from "sigma/rendering";
import type { GraphEdge, GraphNode } from "../api";
import { COPY } from "../copy";
import { palette as signal, hexStr } from "../theme";
import type { AtlasPrefs } from "../atlas-prefs";
import { recede } from "../recede";

/** Atlas 2D 관점 — 독립 렌더러 (DEC D46).
 *
 *  **왜 새로 짓는가.** 예전 2D 는 3D 씬(atlas3d.ts)에 `flat` 플래그를 꽂아 z 를
 *  0 으로 만든 **투영**이었다. 그래서 세로 위치가 시간도 위계도 아닌
 *  `y + z*0.55`(3D 깊이의 잔상)였다 — 2D 인데 세로에 아무 뜻이 없었다.
 *  여기서는 사이드카가 준 **평면 정본(x2/y2)만** 쓴다(z 는 읽지 않는다). D17 결정론
 *  계약 그대로이고 좌표 소유권도 그대로 사이드카에 있다.
 *
 *  D74 — 그 평면 정본은 3D 좌표를 접은 값이 **아니다**. 사이드카가 같은 도메인
 *  트리를 평면 문법(부채꼴)으로 다시 배치해 따로 실어 보낸다. 접어서 쓰면 3D
 *  원뿔에서 깊이로만 갈라져 있던 노드들이 평면에서 같은 점이 되는데, 그건 노드가
 *  하나 사라진 것이다 — 사후에 떼어내는 대신 애초에 안 생기게 한다.
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
/** `focus(id)` 는 **성공 여부를 돌려준다**(D70) — 그 노드가 지금 마운트된 집합에
 *  없으면 false 이고, 호출측(atlas.focusNode)은 그때만 스코프를 갈아 다시 마운트한다.
 *  이 신호가 없으면 "이미 화면에 있는 노드"까지 매번 재조회·재마운트하게 된다. */
export type Handle2d = { dispose(): void; fit(): void; focus(id: string): boolean };

/** 방향 3종 — 화살촉이 붙는 관계(atlas-planar 의 DIRECTED 와 같은 정의). */
const DIRECTED = ["PREREQUISITE_OF", "DERIVES_FROM", "INHERITS_FROM"];

function pal() {
  return signal[document.documentElement.dataset.theme === "dark" ? "dark" : "light"];
}
const ink = () => hexStr(pal().text);
/** 엣지 색 램프 — 선 전용으로 대비를 맞춘 값(theme.ts). 3D 와 같은 원천을 쓴다.
 *  (구 `line()`·`inkMute()` 는 보더·캡션 토큰이라 선 대비가 미달이었다 — 제거했다.) */
const ramp = () => pal().linkRamp;
const point = () =>
  hexStr(document.documentElement.dataset.theme === "dark" ? pal().ring : pal().primary);

/** 물러난 마크의 색 — 불투명도 대신 잉크를 배경 쪽으로 섞는다(sigma 는 노드
 *  알파를 따로 받지 않는다). 결과는 CSS 의 opacity 와 같은 인상이다. */
function mix(fg: string, bg: string, t: number): string {
  const hx = (s: string) => [1, 3, 5].map((i) => parseInt(s.slice(i, i + 2), 16));
  const [r1, g1, b1] = hx(fg);
  const [r2, g2, b2] = hx(bg);
  const c = (a: number, b: number) => Math.round(b + (a - b) * t).toString(16).padStart(2, "0");
  return `#${c(r1, r2)}${c(g1, g2)}${c(b1, b2)}`;
}

/** APCA Lc(크기) — 섞은 색이 배경에서 사라졌는지 판정하는 데만 쓴다.
 *  상수는 공식 0.98G-4g(게이트 `scripts/gate_contrast.py` 와 같은 식). */
function lc(fg: string, bg: string): number {
  const y = (h: string) => {
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
    return 0.2126729 * r ** 2.4 + 0.7151522 * g ** 2.4 + 0.0721750 * b ** 2.4;
  };
  const soft = (v: number) => (v > 0.022 ? v : v + (0.022 - v) ** 1.414);
  const yt = soft(y(fg)), yb = soft(y(bg));
  if (Math.abs(yb - yt) < 0.0005) return 0;
  const s = yb > yt ? (yb ** 0.56 - yt ** 0.57) * 1.14 : (yb ** 0.65 - yt ** 0.62) * 1.14;
  const c = yb > yt ? (s < 0.1 ? 0 : s - 0.027) : (s > -0.1 ? 0 : s + 0.027);
  return Math.abs(c * 100);
}

//: 후퇴 채널의 불가시 하한(APCA). 이 아래는 "보이지 않는 것"이라, 물러남이 아니라 소멸이다.
const LC_FLOOR = 15;

/** 하한이 있는 mix — 배경 쪽으로 섞되 **Lc 15 아래로는 내려가지 않는다**.
 *
 *  구 `mix` 는 하한이 없어 다크에서 무방향 엣지가 실효 1.20:1(Lc ~0)까지 내려갔다.
 *  WCAG 1.4.11 은 후퇴·비활성 채널에 3:1 을 강제하지 않지만, 완전 소멸은 후퇴가
 *  아니라 삭제다. t 를 이분 탐색으로 되돌려 하한에 걸리는 지점에서 멈춘다. */
function mixFloor(fg: string, bg: string, t: number): string {
  const out = mix(fg, bg, t);
  if (lc(out, bg) >= LC_FLOOR) return out;
  let lo = t, hi = 1;                       // t 를 키울수록(=원색에 가까울수록) 대비가 오른다
  for (let i = 0; i < 12; i++) {
    const m = (lo + hi) / 2;
    if (lc(mix(fg, bg, m), bg) >= LC_FLOOR) hi = m; else lo = m;
  }
  return mix(fg, bg, hi);
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

  /** 평면 정본(D74). 이 렌더러는 **x2/y2 를 읽는다** — z 를 눌러 접은 값이 아니라
   *  같은 도메인 트리를 평면 문법(부채꼴)으로 다시 배치한 좌표라, 3D 의 원뿔에서
   *  깊이로만 갈라져 있던 노드들이 여기서 같은 점이 되는 일이 없다. 구 사이드카는
   *  이 키를 안 보내므로 x/y 로 강등한다(그때는 예전 그림 그대로). */
  const px = (n: GraphNode) => n.x2 ?? n.x ?? 0;
  const py = (n: GraphNode) => n.y2 ?? n.y ?? 0;

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
    const pts = shown.map((n) => [px(n), py(n)] as const);
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
  // 사용자 배율은 밀도 축소(shrink) **뒤에** 곱한다 — 축소는 겹침을 막는 자동 보정이고
  // 배율은 그 위에 얹는 취향이라, 순서를 바꾸면 배율을 올려도 축소가 도로 깎는다.
  const sizeOf = (n: GraphNode) => baseSize(n) * shrink * (prefs.nodeScale ?? 1);

  const g = new Graph();
  for (const n of shown) {
    // z 는 읽지 않는다 — 그것이 이 렌더러의 존재 이유다(위 주석). px/py 가 평면
    // 정본(x2/y2)을 집어 오고, 구 사이드카에서만 x/y 로 강등된다.
    g.addNode(n.id, {
      label: n.label === n.id ? n.id.replace(/^[a-z]+:/, "") : n.label,
      x: px(n) * prefs.spread, y: py(n) * prefs.spread,
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
      // 굵기 차등(방향 > 일반 > 교차 > 브릿지)은 그대로 두고 전역 배율만 곱한다
      // (2026-08-02 사용자 손잡이). 가중치 표현은 D33 대로 색 농도가 계속 맡는다.
      size: (dir ? 1.8 : bridged ? 0.5 : e.cross_domain ? 0.6 : 0.9)
            * (prefs.edgeScale ?? 1),
      // 기준색은 **엣지 램프**(theme.ts linkRamp)다. 여태 보더 토큰(`line`)을 섞어
      // 썼는데 그 토큰은 다크에서 Lc 0(#262A3D)이라 아무리 섞어도 선이 배경 밑에
      // 있었다(실효 1.20:1). 램프는 선 전용으로 대비를 맞춰 둔 값이다.
      // 서열(방향 > 무방향 > 크로스 > 다리)은 그대로 두되, 아래 두 단은 mixFloor 가
      // 불가시 하한(Lc 15) 위에서 멈춘다 — 물러남이지 삭제가 아니다.
      color: dir ? ramp().mid
        : bridged ? mixFloor(ramp().base, bg, 0.6)
        : e.cross_domain ? mixFloor(ramp().base, bg, 0.8) : ramp().base,
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
  let hovered: string | null = null;
  // D70 — 프로그램이 고른 초점(좌측 트리·검색 착지). hover 와 달리 포인터가 떠나도
  // 남는다: 어디로 날아왔는지가 화면에 계속 보여야 하기 때문이다. hover 가 우선 —
  // 손이 가리키는 것이 언제나 지금 관심사다. (아래 `pinned` 는 드래그로 자리를
  // 옮긴 노드 집합이라 다른 개념이다 — 이름을 겹치지 않게 둔다.)
  let landed: string | null = null;
  const focusId = () => hovered ?? landed;
  const keepOf = (id: string) => new Set([id, ...(nbr.get(id) || [])]);
  sigma.setSetting("nodeReducer", (id, data) => {
    const focus = focusId();
    if (!focus) return data;
    const keep = keepOf(focus);
    if (keep.has(id)) {
      return id === focus ? { ...data, color: point(), zIndex: 1 } : data;
    }
    return { ...data, color: mixFloor(ink(), bg, recede("node")), label: "" };
  });
  sigma.setSetting("edgeReducer", (id, data) => {
    const focus = focusId();
    if (!focus) return data;
    const [s, t] = g.extremities(id);
    const on = s === focus || t === focus;
    return on ? { ...data, color: point(), size: (data.size as number) * 1.4 }
              // 후퇴한 선은 램프의 fade 를 쓴다(3D 와 같은 색) — 보더 토큰을 섞으면
              // 다크에서 Lc 0 이라 선이 사라진다. fade 는 하한 Lc 15 위로 잡혀 있다.
              : { ...data, color: ramp().fade };
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
  const pts = shown.map((n) => ({ x: px(n) * prefs.spread, y: py(n) * prefs.spread }));
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
  sigma.on("enterNode", ({ node }) => { hovered = node; sigma.refresh(); });
  sigma.on("leaveNode", () => { hovered = null; sigma.refresh(); });
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
    focus(id) {
      if (!g.hasNode(id)) return false;
      landed = id;
      // 미니맵 클릭과 같은 좌표 변환 경로 — 그래프 좌표를 카메라 좌표계로 접는다.
      const v = sigma.graphToViewport({ x: g.getNodeAttribute(id, "x") as number,
                                        y: g.getNodeAttribute(id, "y") as number });
      const c = sigma.viewportToFramedGraph(v);
      sigma.getCamera().animate({ x: c.x, y: c.y, ratio: 0.55 }, { duration: 420 });
      sigma.refresh();
      return true;
    },
  };
}
