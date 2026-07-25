import Graph from "graphology";
import Sigma from "sigma";
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
  const sizeOf = (n: GraphNode) =>
    prefs.size === "uniform" ? 6 : 4 + Math.sqrt(n.degree || 1) * 2;

  const g = new Graph();
  for (const n of shown) {
    // z 는 읽지 않는다 — 그것이 이 렌더러의 존재 이유다(위 주석).
    g.addNode(n.id, {
      label: n.label === n.id ? n.id.replace(/^[a-z]+:/, "") : n.label,
      x: (n.x ?? 0) * prefs.spread, y: (n.y ?? 0) * prefs.spread,
      size: sizeOf(n), color: n.id === o.seed ? point() : ink(),
    });
  }
  for (const e of edges) {
    if (!g.hasNode(e.source) || !g.hasNode(e.target)) continue;
    if (g.hasEdge(e.source, e.target)) continue;
    g.addEdge(e.source, e.target, {
      size: e.cross_domain ? 0.8 : 1.6,
      color: e.cross_domain ? inkMute() : line(),
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
    renderLabels: prefs.labels !== "none",
    // **허브만** 상시 라벨(레퍼런스가 얻던 판독성). 문턱 8px = 연결수 4 이상이라
    // 위성까지 다 켜지지 않는다 — 나머지는 hover 때 sigma 가 그린다.
    // (size=uniform 이면 전부 6px 이라 상시 라벨이 없다 — 위계를 끈 선택의 결과.)
    labelRenderedSizeThreshold: prefs.labels === "none" ? 1e9 : 8,
    labelColor: { color: ink() },
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
