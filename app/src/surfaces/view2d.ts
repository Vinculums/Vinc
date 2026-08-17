import Graph from "graphology";
import Sigma from "sigma";
import { EdgeArrowProgram } from "sigma/rendering";
import { createNodeImageProgram } from "@sigma/node-image";
import type { GraphEdge, GraphNode } from "../api";
import { COPY } from "../copy";
import { icon } from "../icons";
import { palette as signal, hexStr, type ThemeMode } from "../theme";
import { tintMap } from "../view-tint";
import type { ViewPrefs } from "../view-prefs";
import { recede } from "../recede";
import { markTexture } from "./view-planar";

/** View 2D 관점 — 독립 렌더러 (DEC D46).
 *
 *  **왜 새로 짓는가.** 예전 2D 는 3D 씬(view3d.ts)에 `flat` 플래그를 꽂아 z 를
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
 *  없으면 false 이고, 호출측(view.focusNode)은 그때만 스코프를 갈아 다시 마운트한다.
 *  이 신호가 없으면 "이미 화면에 있는 노드"까지 매번 재조회·재마운트하게 된다. */
export type Handle2d = {
  dispose(): void; fit(): void; focus(id: string): boolean;
  /** 옵션을 **제자리에서** 반영한다. 못 하는 변경(선 모드·연결 없음·범례 필터처럼
   *  노드·엣지 집합이 바뀌는 것)은 false 를 돌려 주고, 호출측이 다시 마운트한다. */
  update(p: ViewPrefs): boolean;
};

/** 방향 3종 — 화살촉이 붙는 관계(view-planar 의 DIRECTED 와 같은 정의). */
const DIRECTED = ["PREREQUISITE_OF", "DERIVES_FROM", "INHERITS_FROM"];

const mode = (): ThemeMode =>
  document.documentElement.dataset.theme === "dark" ? "dark" : "light";
function pal() {
  return signal[mode()];
}
const ink = () => hexStr(pal().text);
/** 엣지 색 램프 — 선 전용으로 대비를 맞춘 값(theme.ts). 3D 와 같은 원천을 쓴다.
 *  (구 `line()`·`inkMute()` 는 보더·캡션 토큰이라 선 대비가 미달이었다 — 제거했다.) */
const ramp = () => pal().linkRamp;
/** 초점색 — 토큰 하나(`semantic.*.focus`). 라이트는 로고 인디고, 다크는 페리윙클. */
const point = () => hexStr(pal().focus);

/** hover 배경판. sigma 의 기본 `drawDiscNodeHover` 는 이 판을 **`#FFF` 로 박아
 *  둔다** — 다크 테마에서는 흰 덩어리가 마크를 덮고 그 위에 흰 잉크로 이름을 써서
 *  아무것도 안 읽혔다(2026-08-17 제보). 색만 토큰으로 갈면 되는데 sigma 에 그 설정이
 *  없어서 기하만 그대로 옮겨 왔다(원본: `drawDiscNodeHover`, 원 + 라벨 상자의 합집합).
 *  글자는 `settings.defaultDrawNodeLabel` 에 그대로 넘긴다 — 라벨 색·글꼴 규칙이
 *  hover 에서만 갈라지지 않게. */
function drawHover(ctx: CanvasRenderingContext2D,
  data: { x: number; y: number; size: number; label: string | null },
  settings: { labelSize: number; labelFont: string; labelWeight: string;
              defaultDrawNodeLabel: (c: CanvasRenderingContext2D, d: typeof data, s: never) => void }): void {
  const size = settings.labelSize, PAD = 2;
  ctx.font = `${settings.labelWeight} ${size}px ${settings.labelFont}`;
  ctx.fillStyle = hexStr(pal().surface);
  ctx.strokeStyle = hexStr(pal().line);
  ctx.lineWidth = 1;
  const radius = Math.max(data.size, size / 2) + PAD;
  ctx.beginPath();
  if (typeof data.label === "string") {
    const boxW = Math.round(ctx.measureText(data.label).width + 5);
    const boxH = Math.round(size + 2 * PAD);
    const a = Math.asin(boxH / 2 / radius);
    const dx = Math.sqrt(Math.abs(radius ** 2 - (boxH / 2) ** 2));
    ctx.moveTo(data.x + dx, data.y + boxH / 2);
    ctx.lineTo(data.x + radius + boxW, data.y + boxH / 2);
    ctx.lineTo(data.x + radius + boxW, data.y - boxH / 2);
    ctx.lineTo(data.x + dx, data.y - boxH / 2);
    ctx.arc(data.x, data.y, radius, a, -a);
  } else {
    ctx.arc(data.x, data.y, radius, 0, Math.PI * 2);
  }
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
  settings.defaultDrawNodeLabel(ctx, data, settings as never);
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

/** `gateway` 는 서버가 주는 값이 아니라 **호출측이 렌즈로 계산해 실어 주는** 값이다
 *  (view.ts `isGateway`). 그래서 GraphNode 계약에 넣지 않고 여기서 덧댄다. */
type Node2d = GraphNode & { gateway?: boolean };

export function mount2d(el: HTMLElement, nodes: Node2d[], edges: GraphEdge[],
  o: { onClick: (n: GraphNode) => void; onRightClick?: (n: GraphNode, ev: MouseEvent) => void;
       /** 선 클릭 → 관계 근거 패널(D45-V). 흐름 관점이 폐기되면서(D53) 이 진입점이
        *  여기로 옮겨 왔다 — 방향 엣지가 화살촉을 달고 그려지는 유일한 자리다. */
       onEdge?: (e: GraphEdge) => void; seed?: string },
  prefs: ViewPrefs): Handle2d {
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

  /** 마크 = 노드종. 동심 띠 둘의 **두께**로만 만든다 — 바깥 띠는 잉크, 가운데 띠는
   *  배경, 남는 안쪽은 다시 잉크다. 그래서 색은 노드당 하나(`color`)뿐이고, 물러남과
   *  초점이 그 하나만 갈아 끼우면 형태는 그대로 유지된다(닫힌 층: 모노크롬 · 초점색
   *  1점 · 형태=타입). 값은 반경의 비율이다.
   *
   *  3D · 타임라인 · 번들의 조형(구 · 정사면체 · 정팔면체 · 겹껍질 · 파선 링)을
   *  **테두리로 옮긴 축약**이라 정확한 1:1 은 아니다. 테두리는 각을 못 만들어
   *  정사면체와 정팔면체가 둘 다 원 계열로 떨어지고, 파선도 못 그린다. 대신 갈리는
   *  것은 채움 · 비움 · 겹의 셋이고, 6px 마크에서 실제로 읽히는 것은 거기까지다. */
  /** 마크 = 노드종. **다른 관점과 같은 그림**을 쓴다 — `markSvg` 를 구운 텍스처다
   *  (`markTexture`). 여태 이 자리는 동심 띠 둘로 원 계열만 만들어, 삼각형(문서류)과
   *  마름모(다리)와 파선(관문)을 전부 링으로 **다시 매핑**하고 있었다. 정본 §3.1 이
   *  "관점은 그릴 수 있는 만큼만 고를 수 있지만 다시 매핑할 수는 없다"고 금지한
   *  바로 그것이다(발주자 지적 2026-08-17). 6px 마크의 한계라고 적어 두었던 것은
   *  구현 한계를 사양으로 승격시킨 것이었다.
   *
   *  색은 여기서 안 정한다. 이미지 프로그램이 이 그림을 마스크로 삼아 노드의
   *  `color` 로 칠하므로(`drawingMode:"color"`), 잉크·틴트·초점·물러남은 색 하나만
   *  갈아 끼우면 된다 — 형태가 그대로 유지되는 성질도 그대로다. */
  const markOf = (n: Node2d): { image: string } => ({
    image: markTexture(n.kind, !!n.gateway),
  });

  /** 노드 잉크. 주제 틴트를 끄면 모노크롬 잉크 그대로다(**기본은 켬** — 2026-08-16
   *  발주자 지시로 뒤집혔다). 초점은 틴트를 타지 않는다 — 초점색은 "지금 초점" 하나만
   *  뜻하므로 주제가 덮으면 안 된다. */
  let tints = prefs.domainTint ? tintMap(shown.map((n) => n.domain), mode()) : null;
  const inkOf = (n: GraphNode) => {
    if (n.id === o.seed) return point();
    return tints?.get((n.domain || "").trim()) ?? ink();
  };

  const g = new Graph();
  for (const n of shown) {
    // z 는 읽지 않는다 — 그것이 이 렌더러의 존재 이유다(위 주석). px/py 가 평면
    // 정본(x2/y2)을 집어 오고, 구 사이드카에서만 x/y 로 강등된다.
    g.addNode(n.id, {
      label: n.label === n.id ? n.id.replace(/^[a-z]+:/, "") : n.label,
      x: px(n) * prefs.spread, y: py(n) * prefs.spread,
      size: sizeOf(n), color: inkOf(n),
      ...markOf(n),
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
    // 아래 `clickEdge` 가 뜨려면 켜야 한다 — sigma 3 기본이 false 라 핸들러가 붙어
    // 있어도 선 클릭이 한 번도 안 왔다(2026-08-17). 값은 엣지 피킹 패스 하나.
    enableEdgeEvents: true,
    // 화살촉 프로그램은 기본 등록이 아니다 — 켜 두지 않으면 type:"arrow" 가 무시된다.
    edgeProgramClasses: { arrow: EdgeArrowProgram },
    // 종별 마크는 `@sigma/node-image` 가 그린다(위 markOf → markTexture). 셰이더를 직접 쓰지 않는
    // 이유: 2026-08-15 에 커스텀 SDF 프로그램을 썼다가 마크 위에 덮개 삼각형이 겹치는
    // 고장을 못 잡고 걷어냈다. 이건 sigma 팀이 유지하는 프로그램이라 그 층을 안 짊어진다.
    // ⚠ `circle` 키를 **덮어쓴다**. sigma 는 nodeProgramClasses 를 기본값과 **합치고**
    // defaultNodeType 이 "circle" 이라, 새 이름을 만들면 프로그램이 두 벌이 되고
    // 타입이 빠진 노드가 기본 원으로 함께 그려진다(2026-08-15 실측). 키를 덮으면 한 벌뿐.
    nodeProgramClasses: {
      //: 그림을 **마스크**로 쓰고 노드 색으로 칠한다(`drawingMode:"color"`).
      //: `keepWithinCircle:false` 라야 삼각형 꼭짓점과 마름모 모서리가 원에 안 잘린다.
      circle: createNodeImageProgram({
        drawingMode: "color",
        keepWithinCircle: false,
        correctCentering: true,
        padding: 0,
      }),
    },
    renderLabels: prefs.labels !== "none",
    // **허브만** 상시 라벨(레퍼런스가 얻던 판독성). 문턱 8px = 연결수 4 이상이라
    // 위성까지 다 켜지지 않는다 — 나머지는 hover 때 sigma 가 그린다.
    // (size=uniform 이면 전부 6px 이라 상시 라벨이 없다 — 위계를 끈 선택의 결과.)
    labelRenderedSizeThreshold: prefs.labels === "none" ? 1e9 : 8,
    labelColor: { color: ink() },
    // sigma 기본 hover 판이 `#FFF` 박제라 다크에서 흰 덩어리가 된다(위 drawHover).
    defaultDrawNodeHover: drawHover as never,
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
  /** 모아 놓은 초점 — **클릭**으로만 선다(발주자 지시 2026-08-15). hover 와 갈라
   *  두는 이유: 지나가기만 해도 지도가 재배열되면 눈이 쉴 곳이 없다. 이제 hover 는
   *  어둡게만 하고, 모으는 것은 고르는 행위(클릭)의 결과다. */
  let gathered: string | null = null;
  const focusId = () => hovered ?? gathered ?? landed;
  const keepOf = (id: string) => new Set([id, ...(nbr.get(id) || [])]);
  sigma.setSetting("nodeReducer", (id, data) => {
    const focus = focusId();
    if (!focus) return data;
    const keep = keepOf(focus);
    if (keep.has(id)) {
      // 데려온 이웃은 **이름이 붙어야** 데려온 값이 있다 — 고리에 선 점 여덟 개가
      // 무엇인지 모르면 선을 지운 대가만 치른 셈이다. 그래서 상시 라벨을 강제한다.
      // 이름은 **모였을 때만** 강제한다. 그냥 지나가는 hover 에까지 붙이면
      // 흐리게 하려던 화면이 글자로 다시 시끄러워진다.
      const lab = bringOn() && gathered && prefs.labels !== "none"
        ? { forceLabel: true } : {};
      return id === focus ? { ...data, ...lab, color: point(), zIndex: 1 }
                          : { ...data, ...lab };
    }
    // 물러남은 **그 노드 자신의 색**을 배경 쪽으로 섞는다. `ink()` 를 박아 두던
    // 동안에는 초점색이나 주제 틴트를 단 노드가 물러나면서 잉크로 바뀌었다.
    return { ...data, color: mixFloor(data.color, bg, recede("node")), label: "" };
  });
  // ── Bring & Go (`edges: "bring"`) — 선을 안 그리고 이웃을 **데려온다** ──────
  //
  //  짚으면 이웃이 초점 둘레로 날아와 고리로 서고, 손을 떼면 제자리로 돌아간다.
  //  연결을 그림이 아니라 **움직임**으로 말하므로 교차가 원리적으로 생기지 않는다.
  //  좌표 정본은 그대로 사이드카(D17)다 — `home` 이 그 정본을 쥐고 있고 화면 좌표만
  //  잠시 벗어났다 돌아온다. 드래그(아래 pinned)와 같은 층위의 "이 세션에서만"이다.
  const bringOn = () => prefs.edges === "bring";
  // 방향 3종만 방향을 갖는다(위 DIRECTED). 고리에서 각도가 그것을 말한다.
  const inbound = new Map<string, Set<string>>();
  const outbound = new Map<string, Set<string>>();
  for (const e of edges) {
    if (!DIRECTED.includes(e.type)) continue;
    (outbound.get(e.source) || outbound.set(e.source, new Set()).get(e.source)!).add(e.target);
    (inbound.get(e.target) || inbound.set(e.target, new Set()).get(e.target)!).add(e.source);
  }
  /** 부채꼴 안에 고르게 — 양 끝을 경계에 붙이지 않고 반 칸 안쪽에 둔다(옆 갈래와 붙지 않게). */
  function arcAngles(count: number, center: number, width: number): number[] {
    if (count <= 0) return [];
    if (count === 1) return [center];
    const step = width / count;
    return Array.from({ length: count }, (_, i) => center - width / 2 + step * (i + 0.5));
  }
  /** 고리 자리 = 방향(D85 후속). **위 = 들어옴 · 아래 = 나감 · 좌우 = 무방향.**
   *
   *  자리는 고정이되 **폭이 비율**이라는 것이 요지다. 폭까지 고정하면 무방향이
   *  대다수인 그래프에서 그것들만 좁은 쐐기에 몰리고(이 그래프가 그렇다 —
   *  RELATED_TO 가 다수다), 순서만 주면 반대로 방향 갈래가 위아래에 안 선다
   *  (나가는 관계가 없는 노드에서 들어옴이 옆으로 밀렸다 — 2026-08-14 실측).
   *  폭을 갈래 크기에 비례시키면 넷이 정확히 원을 채운다: 좌우 폭은
   *  `π - (위폭 + 아래폭)/2` 이고, 방향이 하나도 없으면 그 값이 π 라 예전처럼
   *  고른 고리로 자연히 돌아간다.
   *
   *  위가 선행·상위인 것은 `rank` 프리셋이 좌표로 만드는 흐름과 같은 방향이라
   *  새로 배울 것이 없다. */
  function ringSlots(id: string, list: string[]): { id: string; a: number; t: number }[] {
    const inn = inbound.get(id), out = outbound.get(id);
    const isIn = (x: string) => !!inn?.has(x);
    const isOut = (x: string) => !inn?.has(x) && !!out?.has(x);
    const up = list.filter(isIn);
    const down = list.filter(isOut);
    const side = list.filter((x) => !isIn(x) && !isOut(x));
    const n = list.length || 1;
    const wUp = (up.length / n) * Math.PI * 2;
    const wDown = (down.length / n) * Math.PI * 2;
    const wSide = Math.PI - (wUp + wDown) / 2;
    const half = Math.ceil(side.length / 2);
    const R = side.slice(0, half), L = side.slice(half);
    // sin 이 양수일 때 화면에서 위다(실측 2026-08-14) — 그래서 들어옴이 +π/2 다.
    const lay = (list: string[], center: number, width: number) =>
      list.map((x, i) => {
        const [t, cnt, j] = tier(list.length, i);
        return { id: x, a: arcAngles(cnt, center, width)[j], t };
      });
    return [
      ...lay(R, 0, wSide),
      ...lay(up, Math.PI / 2, wUp),
      ...lay(L, Math.PI, wSide),
      ...lay(down, -Math.PI / 2, wDown),
    ];
  }
  const home = new Map<string, [number, number]>(
    shown.map((n) => [n.id, [px(n) * prefs.spread, py(n) * prefs.spread]]));
  const target = new Map<string, [number, number]>();
  /** 지금 고리에 서 있는 것들(초점 포함). 아래 입력 배선이 이걸로 진동을 막는다. */
  let ring = new Set<string>();
  let flyRaf = 0;
  // 모션 감도를 밝힌 사용자에게는 **배치는 주고 이동만 뺀다** — 고리로 서는 것이
  // 이 방식의 내용이고 날아가는 동작은 전달 방식이라, 통째로 끄면 기능이 사라진다.
  const jumpFly = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
  /** 고리 반지름 — 화면 픽셀을 그래프 단위로 환산한다. 줌이 바뀌어도 고리가 같은
   *  크기로 보이게(그래프 단위 상수로 두면 파고들었을 때 화면 밖으로 나간다). */
  function ringRadius(count: number): number {
    const a = sigma.viewportToGraph({ x: 0, y: 0 });
    const b = sigma.viewportToGraph({ x: 0, y: 110 });
    const unit = Math.hypot(b.x - a.x, b.y - a.y) || 1;
    return unit * (1 + Math.min(count, 24) / 24);
  }
  /** 여덟을 넘으면 **두 겹**으로 세운다. 한 겹에 다 세우면 각 사이 간격이 라벨보다
   *  좁아져 이름이 서로 먹는다(실측: 이웃 14 인 허브에서 라벨 여섯 쌍이 겹쳤다).
   *  번갈아 안팎으로 보내면 같은 겹 안의 간격이 두 배가 되고, 이웃한 라벨이 반지름
   *  방향으로도 엇갈려 두 축 모두에서 떨어진다. */
  const ringOf = (count: number, i: number, r0: number) =>
    count > 8 && i % 2 ? r0 * 1.6 : r0;
  /** 한 갈래를 두 겹으로 나눌 때의 (겹 번호, 그 겹의 개수, 겹 안 순번).
   *  **연속 블록**으로 나눈다 — 번갈아 넣으면 같은 겹의 이웃 사이가 두 칸씩 벌어져
   *  고리가 규칙적으로 안 보인다(발주자 지적 2026-08-15). 연속으로 자르면 각 겹이
   *  자기 개수로 등각이라 두 개의 고른 호가 된다. */
  function tier(count: number, i: number): [number, number, number] {
    if (count <= 6) return [0, count, i];
    const inner = Math.ceil(count / 2);
    return i < inner ? [0, inner, i] : [1, count - inner, i - inner];
  }
  /** 정규화 상자를 **집 좌표에 고정한다.** 이 여섯 줄이 "클릭하면 지도가 쪼그라든다"의
   *  해결이다(2026-08-15).
   *
   *  원인은 sigma 의 계약이었다: `nodeExtent = graphExtent(graph)` 를 매번 다시 재서
   *  그것으로 정규화 함수를 만든다(sigma.cjs.dev.js — 소스로 확인). 그래서 모은 고리가
   *  기존 범위 **밖으로 한 점이라도** 나가면 범위가 커지고 화면 전체가 그만큼 작아진다.
   *  증거는 움직이지도 않은 도메인 마크들의 간격이 0.80 배로 줄어든 것이었다 — 마크에는
   *  엣지가 없어 모일 수가 없으니, 줄어든 것은 노드가 아니라 배율이다.
   *
   *  고리를 상자 안에 **가두는** 처방도 됐지만 버렸다: 가장자리 노드를 짚으면 고리가
   *  경계에 눌려 찌그러진다 — 규칙적으로 모이게 한 것과 정면으로 어긋난다. 상자를
   *  고정하면 고리는 제 모양대로 나가고 배율만 안 흔들린다. 끌기도 같이 낫는다
   *  (밖으로 끌면 마찬가지로 전체가 줄어들고 있었다). */
  {
    const xs = [...home.values()].map((v) => v[0]);
    const ys = [...home.values()].map((v) => v[1]);
    if (xs.length) {
      //: 고리·끌기가 나갈 여유. 크게 잡으면 **평소 지도가 그만큼 작게** 그려진다
      //: (상자가 곧 `fit` 의 대상이다). 0.15 면 배율 손해가 13% 쯤이고 가장자리
      //: 고리도 화면 안에 남는다. 이보다 더 나가는 것은 잘리지 않고 그냥 상자 밖에
      //: 그려진다 — 배율은 어떤 경우에도 안 흔들린다는 것이 이 처방의 요지다.
      const mx = (Math.max(...xs) - Math.min(...xs)) * 0.15 || 1;
      const my = (Math.max(...ys) - Math.min(...ys)) * 0.15 || 1;
      sigma.setCustomBBox({ x: [Math.min(...xs) - mx, Math.max(...xs) + mx],
                            y: [Math.min(...ys) - my, Math.max(...ys) + my] });
    }
  }

  function planFly(id: string | null) {
    target.clear();
    ring = new Set(id ? [id] : []);
    if (id && bringOn() && g.hasNode(id)) {
      const [hx, hy] = home.get(id) ?? [0, 0];
      const slots = ringSlots(id, [...(nbr.get(id) || [])].filter((x) => g.hasNode(x)));
      const r0 = ringRadius(slots.length);
      slots.forEach((s) => {
        const r = s.t ? r0 * 1.6 : r0;
        target.set(s.id, [hx + Math.cos(s.a) * r, hy + Math.sin(s.a) * r]);
        ring.add(s.id);
      });
    }
    // 목적지가 안 잡힌 것은 전부 집으로 — 초점이 바뀌면 지난 고리가 저절로 풀린다.
    for (const [nid, xy] of home) if (!target.has(nid)) target.set(nid, xy);
    if (jumpFly) { stepFly(1); return; }
    if (!flyRaf) flyRaf = requestAnimationFrame(tickFly);
  }
  /** 한 프레임 전진. `t`=1 이면 즉시 도착(모션 감도 경로). 도착 판정은 좌표계가
   *  단위원 규모라(사이드카 정본) 1e-4 면 화면에서 이미 같은 픽셀이다. */
  function stepFly(t: number): boolean {
    let live = false;
    for (const [nid, [tx, ty]] of target) {
      if (!g.hasNode(nid)) continue;
      const cx = g.getNodeAttribute(nid, "x") as number;
      const cy = g.getNodeAttribute(nid, "y") as number;
      const dx = tx - cx, dy = ty - cy;
      if (Math.abs(dx) < 1e-4 && Math.abs(dy) < 1e-4) continue;
      g.setNodeAttribute(nid, "x", cx + dx * t);
      g.setNodeAttribute(nid, "y", cy + dy * t);
      live = true;
    }
    return live;
  }
  function tickFly() { flyRaf = stepFly(0.22) ? requestAnimationFrame(tickFly) : 0; }

  // `edges: "focus"` — 선을 **짚을 때만** 그린다(2026-08-14). 헤어볼의 정체는 선
  // 자체가 아니라 "관심 밖 선까지 늘 그려져 있다"는 것이라, 인코딩(화살촉·굵기·
  // 농도·갈래)은 하나도 바꾸지 않고 그려지는 **순간**만 좁힌다. 방향은 그대로
  // 화살촉이 말한다 — 한 번에 한 노드 분량만 뜰 뿐이다. 초점이 없으면 지도는 점만
  // 남고, 그때 위계는 크기(√연결수)와 허브 배지가 계속 말한다.
  const focusOnly = () => prefs.edges === "focus";
  sigma.setSetting("edgeReducer", (id, data) => {
    if (bringOn()) return { ...data, hidden: true };   // 데려오는 방식엔 선이 없다
    const focus = focusId();
    if (!focus) return focusOnly() ? { ...data, hidden: true } : data;
    const [s, t] = g.extremities(id);
    const on = s === focus || t === focus;
    if (on) return { ...data, color: point(), size: (data.size as number) * 1.4 };
    // 후퇴한 선은 램프의 fade 를 쓴다(3D 와 같은 색) — 보더 토큰을 섞으면 다크에서
    // Lc 0 이라 선이 사라진다. fade 는 하한 Lc 15 위로 잡혀 있다. 짚을 때만 모드에서는
    // 후퇴가 아니라 **숨김**이다: 물러난 선까지 남기면 헤어볼이 옅어질 뿐 안 사라진다.
    return focusOnly() ? { ...data, hidden: true } : { ...data, color: ramp().fade };
  });

  // ── 수치 배지 + 지표바(캔버스 위 DOM) ─────────────────────────────────────
  badgeBox.innerHTML = hubs.map((n) =>
    `<span class="a2-badge" data-id="${n.id.replace(/"/g, "&quot;")}"`
    + ` data-tip="${COPY.ui.viewx.badgeTip(n.label, n.degree || 0)}">${n.degree || 0}</span>`).join("");
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
  /** 고정 앵커 — 끌어서 옮긴 노드에 붙는 작은 마크. 정본 §7 이 "앵커 마크 + 더블탭
   *  해제"라고 적어 둔 것을 실제로 그리는 자리다. 여태 `pinned` 는 집합에만 있고
   *  화면에는 아무것도 없어서, 지도가 사이드카 좌표를 그리고 있는지 손으로 옮긴
   *  자리를 그리고 있는지 구분할 방법이 없었다(같은 구멍을 타임라인에서 먼저 막았다).
   *
   *  캔버스가 아니라 배지와 **같은 DOM 층**에 둔다: 좌표 투영과 카메라 추종이 이미
   *  거기 있고, WebGL 안에 넣으면 클릭 표적을 셰이더로 만들어야 한다. */
  const anchors = new Map<string, HTMLElement>();
  function placeAnchors() {
    for (const [id, b] of anchors) {
      if (!g.hasNode(id)) { b.style.display = "none"; continue; }
      const p = sigma.graphToViewport({ x: g.getNodeAttribute(id, "x") as number,
                                        y: g.getNodeAttribute(id, "y") as number });
      const r = sigma.getNodeDisplayData(id)?.size ?? 6;
      b.style.display = "";
      // 배지는 오른쪽 위, 앵커는 왼쪽 아래 — 허브는 둘 다 달리므로 자리를 갈라 둔다.
      b.style.transform = `translate(${Math.round(p.x - r - 15)}px, ${Math.round(p.y + r + 1)}px)`;
    }
  }
  /** 놓아 준다 = **딸려 온 것까지** 사이드카 좌표로 되돌린다. 끌 때 가지가 함께
   *  움직였으므로 그 노드만 되돌리면 가지가 어긋난 채 남는다(끌기와 대칭이 아니다).
   *  단, 따로 잡아 둔 노드는 건드리지 않는다 — 남의 고정을 대신 풀면 안 된다. */
  function releaseAnchor(id: string) {
    const back = (nid: string) => {
      const n = byId.get(nid);
      if (!n) return;
      const hx = px(n) * prefs.spread, hy = py(n) * prefs.spread;
      home.set(nid, [hx, hy]);
      g.setNodeAttribute(nid, "x", hx);
      g.setNodeAttribute(nid, "y", hy);
    };
    for (const other of dragIds()) {
      if (other === id || pinned.has(other) || !pull(id, other)) continue;
      back(other);
    }
    back(id);
    pinned.delete(id);
    anchors.get(id)?.remove();
    anchors.delete(id);
    sigma.refresh();
  }
  function addAnchor(id: string) {
    if (anchors.has(id)) return;
    const b = document.createElement("button");
    b.type = "button";
    b.className = "a2-anchor";
    b.dataset.id = id;
    b.dataset.tip = COPY.ui.viewx.anchorTip;
    b.setAttribute("aria-label", COPY.ui.viewx.anchorTip);
    b.innerHTML = icon("pin", 10);
    // 더블클릭이 지도까지 새어 나가면 카메라가 함께 반응한다.
    b.addEventListener("dblclick", (e) => { e.stopPropagation(); releaseAnchor(id); });
    badgeBox.appendChild(b);
    anchors.set(id, b);
  }
  sigma.on("afterRender", () => { placeBadges(); placeAnchors(); });

  // 지표 4종은 표면 공용 하단 바가 그린다(view.ts drawMetrics) — 관점마다
  // 다른 자리에 다른 수치가 뜨지 않게 한 곳에서만 센다.

  // 최상위 주제의 **영역은 그리지 않는다**(발주자 정정 2026-08-15). 한때 부채꼴을
  // 옅게 칠했는데, 요청은 영역을 *구분하라*였지 *그리라*가 아니었다. 구분은 배치가
  // 이미 한다(주제마다 자기 부채꼴을 통째로 가진다) — 그 위에 테두리를 더하는 것은
  // 같은 말을 두 번 하면서 지도를 시끄럽게 만든다. 아래 끌기의 장력·반발이 그
  // 경계를 **움직임으로** 느끼게 한다.

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
    // 끌리는 것만 스냅숏에 담는다(장력 0 인 다른 최상위 주제는 제외) — 그래야
    // 반발로 밀려난 이웃이 매 프레임 원자리로 되돌려지지 않는다.
    dragFrom = { at: [0, 0], pos: new Map() };
    for (const id of dragIds()) {
      if (id !== node && !pull(node, id)) continue;
      dragFrom.pos.set(id, [g.getNodeAttribute(id, "x") as number,
                            g.getNodeAttribute(id, "y") as number]);
    }
    // sigma 의 노드 이벤트 좌표는 이미 컨테이너 기준 viewport 다.
    const p0 = sigma.viewportToGraph({ x: event.x, y: event.y });
    dragFrom.at = [p0.x, p0.y];
    sigma.getCamera().disable();
    event.preventSigmaDefault();
  });
  /** 끌 때 **딸린 것이 얼마나 따라오는가**(발주자 지시 2026-08-15).
   *
   *  매달린 정도는 주제 경로가 말한다: 같은 가지 아래일수록 세게, 가지를 벗어나면 0.
   *  뿌리 마크(`domain:vinc`)를 끌면 그 아래 전부가 함께 움직이고, 잎을 끌면 제 무리만
   *  살짝 딸려 온다 — 잎 하나가 무리 전체를 통째로 끌면 배치의 뜻이 사라진다. */
  const pathOf = (id: string) => {
    const n = byId.get(id);
    const d = id.startsWith("domain:") ? id.slice(7) : (n?.domain || "");
    return d ? d.split("/") : [];
  };
  function pull(fromId: string, toId: string): number {
    if (fromId === toId) return 1;
    const a = pathOf(fromId), b = pathOf(toId);
    let common = 0;
    while (common < a.length && common < b.length && a[common] === b[common]) common++;
    if (!common) return 0;                       // 다른 최상위 주제는 안 딸려 온다
    // ⚠ **임시값이다**(2026-08-15). 0.22 도 0.8 도 재서 나온 값이 아니라, "뿌리를
    // 끌면 다 따라오고 잎을 끌면 조금만 따라온다"가 나오도록 고른 수다. 기준을
    // 세운다면 감쇠는 깊이차가 아니라 **소속의 세기**여야 한다(같은 주제 안이면 1,
    // 주제를 벗어나면 0). 지금 0.8 은 3단에서 0.51 이라 손자까지 절반이 따라오는데
    // 그게 맞는지 정한 적이 없다. 이 주석이 없으면 다음 세션이 실측값으로 읽는다.
    const base = fromId.startsWith("domain:") ? 1 : 0.22;
    return base * Math.pow(0.8, Math.max(0, b.length - common));
  }
  //: 반발을 푸는 반복 수. 45노드에서 O(n²)×2 는 프레임당 무시할 비용이고, 큰
  //: 그래프에서는 아예 건너뛴다(끌기는 손끝을 따라야 하므로 지연이 곧 고장이다).
  const RELAX_ITERS = 2, RELAX_MAX_N = 400;
  /** 겹침을 민다 — 마크 반지름의 합보다 가까우면 서로 밀어낸다. 크기가 곧 밀어내는
   *  힘이라, 큰 마크 주변이 저절로 더 비워진다(배치의 간격 규칙과 같은 축). */
  function relax(ids: string[]) {
    if (ids.length > RELAX_MAX_N) return;
    // 화면 px 를 그래프 단위로 환산해 최소 거리를 잡는다(줌과 무관하게 같은 인상).
    const a = sigma.viewportToGraph({ x: 0, y: 0 });
    const b = sigma.viewportToGraph({ x: 0, y: 1 });
    const unit = Math.hypot(b.x - a.x, b.y - a.y) || 1;
    for (let it = 0; it < RELAX_ITERS; it++) {
      for (let i = 0; i < ids.length; i++) {
        for (let j = i + 1; j < ids.length; j++) {
          const A = ids[i], B = ids[j];
          if (A === dragging || B === dragging) continue;   // 손에 쥔 것은 안 민다
          const ax = g.getNodeAttribute(A, "x") as number;
          const ay = g.getNodeAttribute(A, "y") as number;
          const bx = g.getNodeAttribute(B, "x") as number;
          const by = g.getNodeAttribute(B, "y") as number;
          // ⚠ 1.15 도 **임시값**이다(2026-08-15). 이 셋 중에서는 유일하게 잴 수
          // 있는 값이라 기준을 세우기 쉽다: 라벨까지 포함한 마크의 실제 폭을 재서
          // "겹치지 않는 최소 거리"로 잡으면 임의값이 아니게 된다. 지금은 라벨 폭을
          // 아예 안 본다 — 그래서 라벨이 긴 노드끼리는 이 거리를 지켜도 글자가 겹친다.
          const want = ((sizeOf(byId.get(A)!) + sizeOf(byId.get(B)!)) * 1.15) * unit;
          let dx = bx - ax, dy = by - ay;
          let d = Math.hypot(dx, dy);
          if (d >= want) continue;
          if (d < 1e-9) { dx = unit; dy = 0; d = unit; }     // 정확히 겹친 쌍
          const push = (want - d) / 2 / d;
          g.setNodeAttribute(A, "x", ax - dx * push);
          g.setNodeAttribute(A, "y", ay - dy * push);
          g.setNodeAttribute(B, "x", bx + dx * push);
          g.setNodeAttribute(B, "y", by + dy * push);
        }
      }
    }
  }
  const dragIds = () => shown.map((n) => n.id).filter((id) => g.hasNode(id));
  /** 끌기 시작 시점의 스냅숏 — 매 프레임 **시작점 기준 총 델타**를 원좌표에 얹는다.
   *  증분(직전 프레임과의 차)으로 하면 마우스 이동 이벤트 수에 결과가 좌우된다:
   *  빠르게 끌어 이벤트가 한 번뿐이면 딸린 것이 아예 안 따라온다(실측 2026-08-15).
   *  총 델타는 프레임 수와 무관하고, 되돌아올 때도 정확히 제자리로 돌아온다. */
  let dragFrom: { at: [number, number]; pos: Map<string, [number, number]> } | null = null;
  stage.addEventListener("mousemove", (e) => {
    if (!dragging || !dragFrom) return;
    const r = stage.getBoundingClientRect();
    const p = sigma.viewportToGraph({ x: e.clientX - r.left, y: e.clientY - r.top });
    const dx = p.x - dragFrom.at[0], dy = p.y - dragFrom.at[1];
    for (const [id, [ox, oy]] of dragFrom.pos) {
      const w = id === dragging ? 1 : pull(dragging, id);
      g.setNodeAttribute(id, "x", ox + dx * w);
      g.setNodeAttribute(id, "y", oy + dy * w);
    }
    relax(dragIds());
  });
  const endDrag = () => {
    if (!dragging) return;
    pinned.add(dragging);
    addAnchor(dragging);
    dragFrom = null;
    // 딸려 온 것들도 그 자리가 새 집이다 — 안 그러면 다음 모으기가 전부 되돌린다.
    for (const id of dragIds()) {
      if (!pull(dragging!, id)) continue;
      home.set(id, [g.getNodeAttribute(id, "x") as number,
                    g.getNodeAttribute(id, "y") as number]);
    }
    // 손으로 옮긴 자리가 그 노드의 새 집이다 — 안 그러면 Bring & Go 가 이웃을
    // 돌려보낼 때 방금 옮긴 것을 원래 자리로 되돌려 버린다.
    home.set(dragging, [g.getNodeAttribute(dragging, "x") as number,
                        g.getNodeAttribute(dragging, "y") as number]);
    g.setNodeAttribute(dragging, "type", "circle");
    dragging = null;
    sigma.getCamera().enable();
    placeBadges(); placeAnchors();
  };
  stage.addEventListener("mouseup", endDrag);
  stage.addEventListener("mouseleave", endDrag);

  // ── 입력 ────────────────────────────────────────────────────────────────
  // 데려오는 방식에서는 초점이 **끈적하다**. 이유는 실측이다(2026-08-14): 날아온
  // 이웃이 가만히 있는 커서 밑으로 미끄러져 들어오면 그것이 새 초점이 되어 고리를
  // 다시 계획하고, 그러면 둘이 번갈아 초점을 뺏는 진동이 생긴다(palette ↔ tokens).
  //   · 고리 **구성원**으로의 hover 는 초점을 바꾸지 않는다(그 자리로 가려면 누른다.
  //     기법 이름의 "Go" 가 그것이다)
  //   · 노드에서 벗어나도 초점을 놓지 않는다. 고리는 지도 밖으로 나갈 때 풀린다
  // hover 는 **어둡게만** 한다 — 배치는 건드리지 않는다. 고리가 서 있는 동안에는
  // 그 구성원 위를 지나도 흐림 기준이 흔들리지 않게 초점을 그대로 둔다.
  sigma.on("enterNode", ({ node }) => {
    if (bringOn() && gathered && ring.has(node)) return;
    hovered = node; sigma.refresh();
  });
  sigma.on("leaveNode", () => { hovered = null; sigma.refresh(); });
  sigma.on("clickNode", ({ node }) => {
    // 고르는 행위가 곧 모으는 행위다. 같은 것을 다시 누르면 풀린다.
    if (bringOn()) { gathered = gathered === node ? null : node; planFly(gathered); }
    const n = byId.get(node);
    if (n) o.onClick(n);
  });
  // 빈 곳을 누르면 흩어진다 — 고리를 푸는 길이 반드시 눈에 보여야 한다.
  sigma.on("clickStage", () => {
    if (!bringOn() || !gathered) return;
    gathered = null; planFly(null); sigma.refresh();
  });
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
      if (flyRaf) cancelAnimationFrame(flyRaf);
      sigma.removeAllListeners();
      sigma.kill();
      el.innerHTML = "";
    },
    /** 제자리 반영(2026-08-17). 노드 집합·엣지 집합이 그대로인 변경만 받는다:
     *  크기 인코딩·배율·선 굵기·화살촉·라벨·주제 틴트·간격. 값은 그래프 속성에 다시
     *  쓰고 `refresh()` 한 번 — 마운트를 새로 하면 카메라·컨텍스트·패널이 전부
     *  흔들리는데, 이 손잡이들은 그림의 **표현**만 바꾼다. */
    update(np) {
      const prev = prefs;
      const changed = (Object.keys(np) as (keyof ViewPrefs)[])
        .filter((k) => JSON.stringify(prev[k]) !== JSON.stringify(np[k]));
      const ok = new Set<keyof ViewPrefs>(
        ["nodeScale", "edgeScale", "arrows", "labels", "domainTint", "size", "spread", "railW"]);
      if (!changed.every((k) => ok.has(k))) return false;
      prefs = np;
      tints = prefs.domainTint ? tintMap(shown.map((n) => n.domain), mode()) : null;
      for (const n of shown) {
        if (!g.hasNode(n.id)) continue;
        g.setNodeAttribute(n.id, "size", sizeOf(n));
        g.setNodeAttribute(n.id, "color", inkOf(n));
      }
      // 간격은 2D 에서 화면을 안 바꾼다(auto-fit) — 좌표는 손대지 않는다.
      edgeOf.forEach((e, key) => {
        if (!g.hasEdge(key)) return;
        const dir = DIRECTED.includes(e.type);
        const bridged = e.type === "CONNECTS";
        g.setEdgeAttribute(key, "type", dir && prefs.arrows ? "arrow" : "line");
        g.setEdgeAttribute(key, "size",
          (dir ? 1.8 : bridged ? 0.5 : e.cross_domain ? 0.6 : 0.9) * (prefs.edgeScale ?? 1));
      });
      sigma.setSetting("renderLabels", prefs.labels !== "none");
      sigma.setSetting("labelRenderedSizeThreshold", prefs.labels === "none" ? 1e9 : 8);
      sigma.refresh();
      return true;
    },
    fit() {
      // sigma 의 기본 카메라 상태 = auto-fit 결과. 되돌리면 곧 "전체 맞춤"이다.
      sigma.getCamera().animate({ x: 0.5, y: 0.5, ratio: 1, angle: 0 }, { duration: 300 });
    },
    focus(id) {
      if (!g.hasNode(id)) return false;
      landed = id;
      // 트리·검색으로 고른 것도 "고른 행위"다 — 클릭과 같이 모아 준다.
      if (bringOn()) { gathered = id; planFly(id); }
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
