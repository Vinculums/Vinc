import Graph from "graphology";
import { esc } from "../components";
import Sigma from "sigma";
import { EdgeArrowProgram } from "sigma/rendering";
import { createNodeImageProgram } from "@sigma/node-image";
import type { GraphEdge, GraphNode } from "../api";
import { COPY } from "../copy";
import { icon } from "../icons";
import { palette as signal, hexStr, type ThemeMode } from "../theme";
import { tintMap } from "../view-tint";
import type { ViewPrefs } from "../view-prefs";
import { edgeKey, type Spot } from "../spotlight";
import { markTexture, MARK_TEX_PX } from "./view-planar";
import { glassShapeOf, glassSprite, type GlassShape } from "../glass-sprite";
import { HIT_MIN_PX, wireDragConnect, type DragEnd, type DragHit } from "./drag-connect";
import { SMALL_GRAPH_LABEL_LIMIT, nonOverlappingLabels, type LabelBox } from "../view-labels";

/** 유리 마크의 노드 색 — 그림 모드는 그림 밖 칸을 노드 색으로 채우므로 투명이어야 한다. */
const CLEAR = "rgba(0,0,0,0)";

/** View 2D 관점 — 독립 렌더러 (DEC D46).
 *
 *  **독립 렌더러다.** 사이드카가 준 평면 정본(x2/y2)만 쓰고 z 는 읽지 않는다 —
 *  3D 씬을 눌러 만든 투영이 아니다(그 시절 세로는 3D 깊이의 잔상이라 아무 뜻이
 *  없었다). 좌표 소유권은 그대로 사이드카에 있다.
 *
 *  ⚠ 평면 정본은 3D 좌표를 **접은 값이 아니다** — 접으면 깊이로만 갈라져 있던
 *  노드들이 한 점이 되어 노드가 사라진다([[vinc.spec.viewpoints]]).
 *
 *  **무엇을 2D 답게 만드는가**(레퍼런스 해부 [[vinc.research.view-redesign]] §5.1
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
  /** 줌 한 걸음. `factor < 1` 이 가까이다 — sigma 는 ratio 가 작을수록 파고든다.
   *  3D 의 `zoomBy3d` 와 **같은 배율 규약**을 쓰도록 뒤집지 않고 그대로 곱한다. */
  zoom(factor: number): void;
  /** 옵션을 **제자리에서** 반영한다. 못 하는 변경(선 모드·연결 없음·범례 필터처럼
   *  노드·엣지 집합이 바뀌는 것)은 false 를 돌려 주고, 호출측이 다시 마운트한다. */
  update(p: ViewPrefs): boolean;
  /** 점등 집합을 **밖에서** 건다(범례 갈래 hover · 인과 원뿔). `null` 이면 hover
   *  기본 문법으로 돌아간다. 프리퍼런스가 아니라 일시 상태라 저장되지 않는다. */
  spotlight(s: Spot | null): void;
  /** 테마가 뒤집혔을 때 색만 다시 칠한다(노드·선·라벨·배경 혼합). 카메라·끌어 둔 자리·
   *  모아 둔 고리·점등은 건드리지 않는다 — 다시 마운트하면 그것들과 함께 읽던 문서 열도
   *  닫힌다. 근거: [[decision:a-theme-switch-repaints-the-open-view-in-place]] */
  retheme(): void;
  /** 시간 커서가 남긴 집합. `null` 이면 전량이다.
   *
   *  **빼는 것이 아니라 숨긴다**(2026-09-10). 노드를 그래프에서 덜어 내면 배치가
   *  다시 잡혀 남은 점들이 통째로 움직이는데, 시간 커서에서 그것은 정반대의 인상이다
   *  — 커서를 미는 동안 "무엇이 언제 생겼나"를 읽으려면 **자리가 가만히 있어야**
   *  한다. 이 렌더러의 좌표는 미리 계산돼 오므로(px/py) 숨긴 점이 남의 자리를
   *  건드리지 않아 그 성질이 공짜다. */
  setTimeVisible(ids: Set<string> | null): void;
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
       onEdge?: (e: GraphEdge) => void; markId?: string;
       /** Alt 끌기로 두 노드를 골랐다(끌어서 잇기). 쓰기는 호출측의 관계 고르기가 한다. */
       onConnect?: (a: DragEnd, b: DragEnd) => void;
       canConnect?: (a: string, b: string) => boolean },
  prefs: ViewPrefs): Handle2d {
  // 물러난 선과 미니맵이 섞어 드는 배경. 테마 전환이 다시 읽는다(`retheme`).
  let bg = hexStr(pal().background);
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
    ? nodes.filter((n) => (n.degree || 0) > 0 || n.id === o.markId)
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
  /** 마크 = 노드종. **다른 관점과 같은 그림**을 쓴다(`markSvg` 를 구운 텍스처).
   *  ⚠ 자기 어휘로 다시 그리지 말 것 — 관점은 그릴 수 있는 만큼만 고를 수 있고
   *  **다시 매핑할 수는 없다**([[record:2d-marks-were-remapped-onto-circles]]).
   *
   *  색은 여기서 안 정한다 — 이미지 프로그램이 이 그림을 마스크로 삼아 노드의
   *  `color` 로 칠하므로, 잉크·틴트·초점·물러남은 색 하나만 갈아 끼우면 된다. */
  //: `ringImage` 는 외곽선을 두른 같은 마크다 — 선택이 떠 있을 때 리듀서가 갈아 끼운다.
  //: 몸통이 있는 종(개념·결정·다리·문서류)은 **유리 그림**이다(`glass` 프로그램, 그림 모드):
  //: 3D 의 유리 노드를 평면으로 구운 것이라 색과 음영이 그림 안에 있고, 노드 `color` 는
  //: 투명으로 둔다(그림 모드는 그림 밖 칸을 노드 색으로 채운다). 주제 겹껍질과 관문은
  //: 면이 없거나 반투명이라 마스크 마크(`circle`)로 남는다.
  //: 근거: [[decision:app-nodes-wear-the-hero-glass-2026-09-26]]
  const markOf = (n: Node2d): Record<string, unknown> => {
    const shape = glassShapeOf(n.kind, !!n.gateway);
    if (!shape) return {
      type: "circle", color: inkOf(n),
      image: markTexture(n.kind, !!n.gateway),
      ringImage: markTexture(n.kind, !!n.gateway, true),
    };
    const hex = inkOf(n);
    return { type: "glass", glass: shape, color: CLEAR,
             image: glassSprite(shape, hex), ringImage: glassSprite(shape, hex, true) };
  };

  /** 노드 잉크. 주제 틴트를 끄면 모노크롬 잉크 그대로다(**기본은 켬** — 2026-08-16
   *  발주자 지시로 뒤집혔다). 초점은 틴트를 타지 않는다 — 초점색은 "지금 초점" 하나만
   *  뜻하므로 주제가 덮으면 안 된다. */
  //: 색상표는 **받은 노드 전부**로 짓는다(`shown` 이 아니다). 색은 주제의 순위로 매겨
  //: 지므로, 고아 숨김으로 주제 하나가 빠지면 나머지 주제의 색이 한 칸씩 밀려 3D·
  //: 타임라인·노드 카드와 같은 주제가 다른 색이 된다.
  //: 명도가 모드별 토큰이라 테마가 뒤집히면 표도 다시 짓는다(`retheme`).
  const tintsNow = () =>
    tintMap(nodes.map((n) => n.domain), mode());
  let tints = tintsNow();
  const inkOf = (n: GraphNode) => {
    if (n.id === o.markId) return point();
    return tints?.get((n.domain || "").trim()) ?? ink();
  };

  const g = new Graph();
  for (const n of shown) {
    // z 는 읽지 않는다 — 그것이 이 렌더러의 존재 이유다(위 주석). px/py 가 평면
    // 정본(x2/y2)을 집어 오고, 구 사이드카에서만 x/y 로 강등된다.
    g.addNode(n.id, {
      label: n.label === n.id ? n.id.replace(/^[a-z]+:/, "") : n.label,
      x: px(n) * prefs.spread, y: py(n) * prefs.spread,
      size: sizeOf(n),
      ...markOf(n),
    });
  }
  // 선의 기준색은 **엣지 램프**(theme.ts linkRamp)다. 여태 보더 토큰(`line`)을 섞어
  // 썼는데 그 토큰은 다크에서 Lc 0(#262A3D)이라 아무리 섞어도 선이 배경 밑에
  // 있었다(실효 1.20:1). 램프는 선 전용으로 대비를 맞춰 둔 값이다.
  // 서열(방향 > 무방향 > 크로스 > 다리)은 그대로 두되, 아래 두 단은 mixFloor 가
  // 불가시 하한(Lc 15) 위에서 멈춘다 — 물러남이지 삭제가 아니다.
  // 마운트와 테마 전환(`retheme`)이 이 한 벌을 읽는다.
  const edgeColor = (e: GraphEdge) => {
    if (DIRECTED.includes(e.type)) return ramp().mid;
    return e.type === "CONNECTS" ? mixFloor(ramp().base, bg, 0.6)
      : e.cross_domain ? mixFloor(ramp().base, bg, 0.8) : ramp().base;
  };
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
      color: edgeColor(e),                     // 램프와 하한(위 edgeColor 주석)
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
  // Small graphs need names even when the default .7 node scale puts every
  // mark below Sigma's 8px threshold. Larger graphs keep density thinning.
  const forceHubLabels = shown.length <= SMALL_GRAPH_LABEL_LIMIT;
  for (const n of shown) g.setNodeAttribute(n.id, "forceLabel", false);

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
        //: 굽는 크기를 **강제**한다. 기본(`max 512`)은 그림의 고유 크기를 넘지 않아서
        //: 마크가 64px 비트맵으로 구워졌다(view-planar.ts `MARK_TEX_PX` 주석).
        size: { mode: "force", value: MARK_TEX_PX },
      }),
      //: 유리 마크 — 그림을 **그대로** 그린다(`"background"`: 그림 색은 두고 바깥 칸만 노드 색,
      //: 그 노드 색이 투명이다). 색과 음영이 그림 안에 있다. 굽는 크기는 위와 같다.
      glass: createNodeImageProgram({
        drawingMode: "background",
        keepWithinCircle: false,
        correctCentering: true,
        padding: 0,
        size: { mode: "force", value: MARK_TEX_PX },
      }),
    },
    renderLabels: prefs.labels !== "none",
    // Small graphs force the top hubs; larger graphs retain the rendered-size
    // threshold and density grid. Other labels remain available on hover.
    labelRenderedSizeThreshold: prefs.labels === "none" || forceHubLabels ? 1e9 : 8,
    labelColor: { color: ink() },
    // sigma 기본 hover 판이 `#FFF` 박제라 다크에서 흰 덩어리가 된다(위 drawHover).
    defaultDrawNodeHover: drawHover as never,
    // 라벨은 노드 오른쪽으로 뻗는데 rank 프리셋의 열 간격이 라벨보다 좁아 옆 열까지
    // 침범한다. 격자당 하나만 남겨(밀도 제한) 겹침을 줄인다 — 나머지는 hover 로 뜬다.
    labelGridCellSize: 140,
    labelDensity: 0.6,
  });

  // dev 전용 진단 핸들(프로덕션 번들에서는 상수 폴딩으로 제거됨) — 3D 의 `__g3` 와 같은
  // 자리다. 노드·선 색과 설정값을 수치로 대조하는 데 쓴다(테마 전환 검증).
  if (import.meta.env.DEV) (window as any).__s2 = sigma;

  const syncHubLabels = () => {
    if (!forceHubLabels) return;
    const boxes: LabelBox[] = [];
    const settings = sigma.getSettings();
    const ctx = sigma.getCanvases().labels.getContext("2d")!;
    ctx.font = `${settings.labelWeight} ${settings.labelSize}px ${settings.labelFont}`;
    if (prefs.labels !== "none") for (const n of hubs) {
      const data = sigma.getNodeDisplayData(n.id);
      if (!data || data.hidden || !data.label) continue;
      const p = sigma.framedGraphToViewport(data);
      const left = p.x + sigma.scaleSize(data.size) + 3;
      const baseline = p.y + settings.labelSize / 3;
      const metrics = ctx.measureText(data.label);
      boxes.push({ id: n.id, left, right: left + metrics.width,
        top: baseline - (metrics.actualBoundingBoxAscent || settings.labelSize),
        bottom: baseline + (metrics.actualBoundingBoxDescent || 2) });
    }
    const visible = nonOverlappingLabels(boxes);
    // One follow-up render applies the chosen set; unchanged sets do not queue
    // another frame. This uses the current camera/resize transform from Sigma.
    if (shown.some(n => g.getNodeAttribute(n.id, "forceLabel") !== visible.has(n.id))) {
      g.updateEachNodeAttributes((id, attrs) => ({ ...attrs, forceLabel: visible.has(id) }));
    }
  };
  sigma.on("afterRender", syncHubLabels);
  sigma.scheduleRender();

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
  /** 밖에서 건 점등 집합. **hover 가 이긴다**(바로 위 주석의 규칙 그대로: 손이
   *  가리키는 것이 언제나 지금 관심사다) — 그래서 우선순위 코드가 따로 없다. */
  let spot: Spot | null = null;
  const focusId = () => hovered ?? landed;
  const keepOf = (id: string) => new Set([id, ...(nbr.get(id) || [])]);
  /** 시간 커서. `null` = 전량. 리듀서가 매 프레임 읽으므로 Set 조회 하나로 끝난다. */
  let timeVisible: Set<string> | null = null;
  sigma.setSetting("nodeReducer", (id, data) => {
    // 시간이 **먼저**다. 아래 점등·물러남 문법은 "그려지는 것들 사이의 위계"인데,
    // 커서 밖 노드는 애초에 그려지지 않으므로 그 위계에 낄 자리가 없다.
    if (timeVisible && !timeVisible.has(id)) return { ...data, hidden: true };
    const focus = focusId();
    const keep = spot && !hovered ? spot.nodes : focus ? keepOf(focus) : null;
    if (!keep) return data;
    if (keep.has(id)) {
      // 외곽선은 **고른 것**(클릭·점등)에만 두른다. 지나가는 hover 에는 안 두른다 —
      // 손이 움직일 때마다 링이 깜빡이면 평소의 조용한 평면이 깨진다.
      // 근거: [[decision:2d-selection-outlines-the-kept-nodes]]
      const ring = hovered ? {} : { image: (data as { ringImage?: string }).ringImage };
      if (id !== focus) return { ...data, ...ring };
      // 초점은 포인트색이다. 유리 마크는 색이 그림 안에 있으므로 포인트색으로 구운 그림을 끼운다.
      const shape = (data as { glass?: GlassShape }).glass;
      return shape ? { ...data, image: glassSprite(shape, point(), !hovered), zIndex: 1 }
                   : { ...data, ...ring, color: point(), zIndex: 1 };
    }
    // 물러나는 것은 **이름뿐**이다. 노드는 제 색 그대로 두고, 남은 것은 엣지와
    // 라벨이 말한다 — 몸통을 배경 쪽으로 섞던 동안 같은 노드가 상태에 따라 진한 색과
    // 옅은 틴트를 오갔다. 근거: [[decision:a-node-keeps-its-own-colour-in-every-state]]
    return { ...data, label: "" };
  });
  const home = new Map<string, [number, number]>(
    shown.map((n) => [n.id, [px(n) * prefs.spread, py(n) * prefs.spread]]));
  /** 정규화 상자를 **집 좌표에 고정한다**(2026-08-15).
   *
   *  sigma 는 `nodeExtent = graphExtent(graph)` 를 매번 다시 재서 정규화한다(소스로
   *  확인). 그래서 노드 하나라도 기존 범위 **밖으로** 끌려 나가면 범위가 커지고 화면
   *  전체가 그만큼 작아진다. 상자를 고정하면 끈 노드는 제자리대로 나가고 배율만 안
   *  흔들린다. (처음 이 처방이 막던 것은 걷어 낸 Bring & Go 의 고리였다 — 끌기는 같은
   *  고장을 그대로 갖고 있어 남긴다.) */
  {
    const xs = [...home.values()].map((v) => v[0]);
    const ys = [...home.values()].map((v) => v[1]);
    if (xs.length) {
      //: 끌기가 나갈 여유. 크게 잡으면 **평소 지도가 그만큼 작게** 그려진다(상자가
      //: 곧 `fit` 의 대상이다). 0.15 면 배율 손해가 13% 쯤이다. 이보다 더 나가는 것은
      //: 잘리지 않고 상자 밖에 그려진다 — 배율은 어떤 경우에도 안 흔들린다.
      const mx = (Math.max(...xs) - Math.min(...xs)) * 0.15 || 1;
      const my = (Math.max(...ys) - Math.min(...ys)) * 0.15 || 1;
      sigma.setCustomBBox({ x: [Math.min(...xs) - mx, Math.max(...xs) + mx],
                            y: [Math.min(...ys) - my, Math.max(...ys) + my] });
    }
  }


  // `edges: "focus"` — 선을 **짚을 때만** 그린다(2026-08-14). 헤어볼의 정체는 선
  // 자체가 아니라 "관심 밖 선까지 늘 그려져 있다"는 것이라, 인코딩(화살촉·굵기·
  // 농도·갈래)은 하나도 바꾸지 않고 그려지는 **순간**만 좁힌다. 방향은 그대로
  // 화살촉이 말한다 — 한 번에 한 노드 분량만 뜰 뿐이다. 초점이 없으면 지도는 점만
  // 남고, 그때 위계는 크기(√연결수)와 허브 배지가 계속 말한다.
  const focusOnly = () => prefs.edges === "focus";
  sigma.setSetting("edgeReducer", (id, data) => {
    // 선은 **끝점을 따라간다** — 한쪽이 아직 안 생겼으면 그릴 곳이 없다.
    if (timeVisible) {
      const [a2, b2] = g.extremities(id);
      if (!timeVisible.has(a2) || !timeVisible.has(b2)) return { ...data, hidden: true };
    }
    // 노드만(기본) — 선을 그리지 않는다. 고른 것은 외곽선이 말한다.
    // 근거: [[decision:bring-and-go-is-retired-and-nodes-only-is-the-default]]
    if (prefs.edges === "none") return { ...data, hidden: true };
    const focus = focusId();
    // 점등 중에는 **그 집합에 든 선만** 켜진다. 열쇠로 대조하는 이유: 이 렌더러는
    // 같은 쌍의 평행 엣지를 하나로 접으므로(위 `g.hasEdge` 가드) 양 끝만으로는
    // 어느 관계가 그려진 것인지 알 수 없다 — `edgeOf` 가 원본을 쥐고 있다.
    if (spot && !hovered) {
      const src = edgeOf.get(id);
      const on2 = !!src && spot.edges.has(edgeKey(src));
      if (on2) return { ...data, color: point(), size: (data.size as number) * 1.4 };
      return focusOnly() ? { ...data, hidden: true } : { ...data, color: ramp().fade };
    }
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
    + ` data-tip="${esc(COPY.ui.viewx.badgeTip(n.label, n.degree || 0))}">${n.degree || 0}</span>`).join("");
  const badges = [...badgeBox.querySelectorAll<HTMLElement>(".a2-badge")];
  /** 배지를 노드 위에 얹는다 — 카메라가 움직일 때마다 좌표만 다시 계산한다. */
  function placeBadges() {
    for (const b of badges) {
      const id = b.dataset.id!;
      if (!g.hasNode(id)) { b.style.display = "none"; continue; }
      const p = sigma.graphToViewport({ x: g.getNodeAttribute(id, "x") as number,
                                        y: g.getNodeAttribute(id, "y") as number });
      const r = sigma.scaleSize(sigma.getNodeDisplayData(id)?.size ?? 6);
      b.style.display = "";
      // Keep the pill above the name instead of covering its first letters.
      const above = Math.max(r, sigma.getSetting("labelSize") * 2 / 3);
      b.style.transform = `translate(${Math.round(p.x + r + 3)}px, ${Math.round(p.y - above - b.offsetHeight - 2)}px)`;
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
    // 딸려 온 것들도 그 자리가 새 집이다 — 앵커를 풀 때(releaseAnchor) 돌아갈 자리다.
    for (const id of dragIds()) {
      if (!pull(dragging!, id)) continue;
      home.set(id, [g.getNodeAttribute(id, "x") as number,
                    g.getNodeAttribute(id, "y") as number]);
    }
    // 손으로 옮긴 자리가 그 노드의 새 집이다.
    home.set(dragging, [g.getNodeAttribute(dragging, "x") as number,
                        g.getNodeAttribute(dragging, "y") as number]);
    g.setNodeAttribute(dragging, "type", g.getNodeAttribute(dragging, "glass") ? "glass" : "circle");
    dragging = null;
    sigma.getCamera().enable();
    placeBadges(); placeAnchors();
  };
  stage.addEventListener("mouseup", endDrag);
  stage.addEventListener("mouseleave", endDrag);

  // ── 끌어서 잇기(Alt 끌기) ─────────────────────────────────────────────────
  // 맨 끌기(위의 자리 옮기기)와 손이 겹치지 않게 Alt 로 가른다. 찾기는 리듀서를 거친
  // 표시 데이터로 한다 — 시간 커서가 숨긴 노드는 잡히지 않고, 반지름은 화면에 그려진 크기다.
  const unwireConnect = o.onConnect ? wireDragConnect(el, {
    hitAt: (x, y) => {
      let best: DragHit | null = null, bestD = Infinity;
      g.forEachNode((id) => {
        const dd = sigma.getNodeDisplayData(id);
        if (!dd || dd.hidden) return;
        const v = sigma.framedGraphToViewport(dd);
        const d = Math.hypot(v.x - x, v.y - y);
        const r = Math.max(HIT_MIN_PX, sigma.scaleSize(dd.size));
        if (d <= r && d < bestD) {
          best = { id, title: byId.get(id)?.label ?? String(g.getNodeAttribute(id, "label") ?? id),
                   x: v.x, y: v.y, r };
          bestD = d;
        }
      });
      return best;
    },
    lock: (on) => { if (on) sigma.getCamera().disable(); else sigma.getCamera().enable(); },
    onConnect: o.onConnect,
    allowed: o.canConnect,
  }) : null;

  // ── 입력 ────────────────────────────────────────────────────────────────
  // hover 는 **어둡게만** 한다 — 배치는 건드리지 않는다.
  sigma.on("enterNode", ({ node }) => { hovered = node; sigma.refresh(); });
  sigma.on("leaveNode", () => { hovered = null; sigma.refresh(); });
  sigma.on("clickNode", ({ node }) => {
    const n = byId.get(node);
    if (n) o.onClick(n);
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
    setTimeVisible(ids) {
      timeVisible = ids;
      sigma.refresh();          // 좌표는 그대로 — 리듀서만 다시 돈다
    },
    dispose() {
      unwireConnect?.();
      sigma.removeAllListeners();
      sigma.kill();
      el.innerHTML = "";
    },
    /** 줌 — 카메라 ratio 만 바꾼다. 방향도 중심도 안 건드리는 것이 3D 와 같은 약속이고,
     *  울타리가 없으면 몇 번 눌러 아무것도 아닌 화면이 된다(3D 쪽과 같은 이유). */
    zoom(factor) {
      const cam = sigma.getCamera();
      const want = Math.min(4, Math.max(0.05, cam.ratio * factor));
      if (Math.abs(want - cam.ratio) < 1e-3) return;
      cam.animate({ ratio: want }, { duration: 260 });
    },
    spotlight(s) { spot = s; sigma.refresh(); },
    retheme() {
      // 리듀서·hover 판·미니맵은 팔레트를 부를 때 읽어 저절로 따른다. 굳어 있던 것은
      // 그래프 속성(노드·선 색)과 라벨 설정, 그리고 섞을 때 쓰는 배경이다.
      bg = hexStr(pal().background);
      tints = tintsNow();
      for (const n of shown) if (g.hasNode(n.id)) g.mergeNodeAttributes(n.id, markOf(n));
      edgeOf.forEach((e, key) => { if (g.hasEdge(key)) g.setEdgeAttribute(key, "color", edgeColor(e)); });
      sigma.setSetting("labelColor", { color: ink() });
      sigma.refresh();
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
        ["nodeScale", "edgeScale", "arrows", "labels", "size", "spread", "railW"]);
      if (!changed.every((k) => ok.has(k))) return false;
      prefs = np;
      tints = tintsNow();
      for (const n of shown) {
        if (!g.hasNode(n.id)) continue;
        g.setNodeAttribute(n.id, "size", sizeOf(n));
        if (prefs.labels === "none") g.setNodeAttribute(n.id, "forceLabel", false);
        g.mergeNodeAttributes(n.id, markOf(n));
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
      sigma.setSetting("labelRenderedSizeThreshold", prefs.labels === "none" || forceHubLabels ? 1e9 : 8);
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
