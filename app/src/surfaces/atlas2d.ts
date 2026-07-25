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

export type Handle2d = { dispose(): void };

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
  o: { onClick: (n: GraphNode) => void; onRightClick?: (n: GraphNode) => void; seed?: string },
  prefs: AtlasPrefs): Handle2d {
  const bg = hexStr(pal().background);
  el.innerHTML = `<div class="a2-stage"></div>
    <div class="a2-badges"></div>
    <div class="a2-metrics"></div>`;
  const stage = el.querySelector<HTMLElement>(".a2-stage")!;
  const badgeBox = el.querySelector<HTMLElement>(".a2-badges")!;
  const metrics = el.querySelector<HTMLElement>(".a2-metrics")!;

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
  for (const e of edges) {
    if (!g.hasNode(e.source) || !g.hasNode(e.target)) continue;
    if (g.hasEdge(e.source, e.target)) continue;
    const dir = DIRECTED.includes(e.type);
    g.addEdge(e.source, e.target, {
      // 화살촉은 열린 층(prefs.arrows)이 켜져 있고 방향 관계일 때만.
      type: dir && prefs.arrows ? "arrow" : "line",
      // 뼈대는 잉크에 가깝게, 살은 배경 쪽으로 섞어 눕힌다. `line()`(테두리색)은
      // 뼈대로 쓰기엔 너무 옅어 방향 관계가 배경에 묻혔다(실측).
      size: dir ? 1.8 : e.cross_domain ? 0.6 : 0.9,
      color: dir ? inkMute() : mix(line(), bg, 0.35),
      directed: dir,
    });
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

  // 지표 4종 — 추가 조회 없이 지금 그린 그래프에서 바로 센다(레퍼런스 하단 바).
  const total = shown.length;
  const linked = shown.filter((n) => (n.degree || 0) > 0).length;
  const orphan = total - linked;
  const rate = total ? Math.round((linked / total) * 100) : 0;
  const latest = shown.map((n) => n.ts || "").filter(Boolean).sort().pop() || "";
  const cell = (label: string, value: string) =>
    `<span class="a2-metric"><b class="pl-num">${value}</b>${label}</span>`;
  metrics.innerHTML =
    cell(COPY.ui.atlasx.mDocs, String(total))
    + cell(COPY.ui.atlasx.mLinked, `${rate}%`)
    + cell(COPY.ui.atlasx.mOrphan, String(orphan))
    + (latest ? cell(COPY.ui.atlasx.mLatest, latest.slice(0, 10)) : "");

  // ── 입력 ────────────────────────────────────────────────────────────────
  sigma.on("enterNode", ({ node }) => { focus = node; sigma.refresh(); });
  sigma.on("leaveNode", () => { focus = null; sigma.refresh(); });
  sigma.on("clickNode", ({ node }) => { const n = byId.get(node); if (n) o.onClick(n); });
  sigma.on("rightClickNode", ({ node, event }) => {
    event.preventSigmaDefault();
    event.original.preventDefault();
    const n = byId.get(node);
    if (n && o.onRightClick) o.onRightClick(n);
  });

  return {
    dispose() {
      sigma.removeAllListeners();
      sigma.kill();
      el.innerHTML = "";
    },
  };
}
