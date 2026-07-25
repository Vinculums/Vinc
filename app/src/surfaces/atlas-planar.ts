import type { LedgerRow } from "../api";
import { COPY } from "../copy";

/** Atlas 평면 뷰 (D43) — Flow · Timeline · Totals.
 *
 *  3D/2D 맵과 같은 시각 문법을 평면으로 옮긴다: 모노크롬 잉크가 정본이고
 *  의미는 형태 · 농도 · 굵기 · 위치가 나른다(색상환은 쓰지 않는다, D41).
 *  초점색(hsl(var(--ring)))은 딱 한 곳 — hover/고정된 리니지뿐이며, 그 외의
 *  어떤 마크도 색으로 구분되지 않는다.
 *
 *  의존성 0: 순수 DOM + 인라인 SVG(캔버스도 WebGL 도 아니다). 색은 전부 토큰
 *  참조(hsl(var(--…)))라 테마 전환에 자동으로 따라간다 — hex 하드코딩 금지.
 *  레이아웃 수치는 전부 입력 순서의 결정론(Math.random · 시간 의존 없음)이라
 *  같은 데이터는 언제 그려도 같은 그림이 된다(D17 좌표 철학의 평면판).
 *
 *  스타일 규칙(.pl-*)은 app.css 의 Atlas 절에 산다 — 템플릿 문자열 안에 숨은
 *  두 번째 스타일시트를 만들지 않는다(디자인 시스템 · 모노크롬 게이트가 훑는
 *  파일이 곧 규칙이 사는 파일이어야 한다).
 *
 *  화면 문구는 전부 copy.ts 덱(COPY.ui.atlasx)에서 온다. atlas3d.ts 가 COPY 를
 *  import 하지 않는 것은 그쪽이 문구를 한 줄도 저작하지 않고 데이터 필드만 그리는
 *  순수 렌더러이기 때문이다. 이 파일은 차트 이름과 구간 이름을 스스로 짓는 쪽이라
 *  문구를 저작하는 다른 모듈들과 같은 규칙을 따른다(각자 COPY 를 import).
 *
 *  <svg> 에는 aria-label 만 두고 role="img" 는 두지 않는다: role="img" 는 하위
 *  트리를 통째로 접어 마크마다 달아 둔 설명을 전부 가린다. 이름은 남기고 안은
 *  열어 둔다(키보드 순회는 이 코드베이스에 없는 문법이라 새로 만들지 않는다).
 *
 *  툴팁은 `data-tip` 속성으로만 낸다(D48-T) — SVG `<title>` = 네이티브 툴팁은
 *  WCAG 1.4.13(dismissable·hoverable·persistent)을 하나도 만족하지 못해 전면
 *  교체했다. 실제 표시는 tooltip.ts 의 단일 오버레이가 담당한다. */

export type PlanarHandle = { dispose(): void };
export type FlowNode = { id: string; label: string; domain: string };
export type FlowEdge = {
  source: string; target: string; type: string;
  confidence?: number; evidence?: string; weight?: number;
};

/** 방향 3종 — 화살촉이 붙는 관계(atlas.ts:217 DIRECTED 와 같은 정의). */
const DIRECTED = ["PREREQUISITE_OF", "DERIVES_FROM", "INHERITS_FROM"];
/** Timeline 에서 "문서 연결"(timelineDocs=off)로 걷어내는 부착 노드종. */
const DOC_KINDS = ["episode", "record"];
/** 같은 걸러내기를 호출부(빈 상태 판정·날짜 없는 수 세기)도 써야 한다 — 챠트가
 *  그리는 집합과 판정하는 집합이 어긋나면 "빈 캔버스 + 무문구"가 난다. */
export const timelinePool = <T extends { kind?: string }>(nodes: T[], docs: boolean) =>
  docs ? nodes : nodes.filter((n) => !DOC_KINDS.includes(n.kind || ""));

// 실측 여백 — app.css 의 .pl-wrap padding 과 같은 값을 유지해야 한다(둘이 어긋나면
// viewBox 가 컨테이너보다 커져 스크롤이 생긴다).
const PAD_X = 16;      // .pl-wrap 좌우 패딩
const PAD_TOP = 56;    // 상단 크롬(크럼 · 툴 행) 회피
const PAD_BOT = 16;

// ── 문자열 조립 헬퍼 ───────────────────────────────────────────────────────
const esc = (s: string) => s.replace(/[&<>"]/g, (c) =>
  c === "&" ? "&amp;" : c === "<" ? "&lt;" : c === ">" ? "&gt;" : "&quot;");
/** 좌표 소수 1자리 — 문자열이 흔들리지 않게(결정론 + diff 가독). */
const num = (v: number) => String(Math.round(v * 10) / 10);
const cut = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1) + "…" : s);
/** 좌→우 수평 3차 베지어(층 사이 커넥터). */
const curveH = (x1: number, y1: number, x2: number, y2: number) => {
  const mx = (x1 + x2) / 2;
  return `M${num(x1)} ${num(y1)} C${num(mx)} ${num(y1)} ${num(mx)} ${num(y2)} ${num(x2)} ${num(y2)}`;
};
/** 정육각 꼭짓점 — 형태=타입(D35): 기록물 계열은 육각, 개념은 원. */
const hexPts = (cx: number, cy: number, r: number) => {
  const p: string[] = [];
  for (let i = 0; i < 6; i++) {
    const a = (Math.PI / 180) * (60 * i - 30);
    p.push(`${num(cx + r * Math.cos(a))},${num(cy + r * Math.sin(a))}`);
  }
  return p.join(" ");
};
const wrap = (svg: string) => `<div class="pl-wrap">${svg}</div>`;

/** 라벨 디클러터 — 앵커 y 를 최소 간격까지 밀어내되 원 위치에 최대한 붙인다.
 *  아래로 한 번, 위로 한 번 쓸어 양끝 쏠림을 없앤다(밀집 구간의 겹침 실측 대응). */
function declutter(anchors: number[], minSep: number): number[] {
  const items = anchors.map((y, i) => ({ i, y, ly: y }))
    .sort((p, q) => (p.y === q.y ? p.i - q.i : p.y - q.y));
  for (let k = 1; k < items.length; k++)
    if (items[k].ly - items[k - 1].ly < minSep) items[k].ly = items[k - 1].ly + minSep;
  for (let k = items.length - 2; k >= 0; k--)
    if (items[k + 1].ly - items[k].ly < minSep) items[k].ly = items[k + 1].ly - minSep;
  const out: number[] = [];
  for (const it of items) out[it.i] = it.ly;
  return out;
}

/** 컨테이너 실측 추적 — draw 는 (w,h) 의 순수 함수라 리사이즈는 "다시 그릴" 뿐이다.
 *  rAF 로 합치고 크기가 실제로 변할 때만 그린다(리사이즈 폭주 방지). */
function trackSize(el: HTMLElement, paint: (w: number, h: number) => void): () => void {
  let last = "";
  let raf = 0;
  const run = () => {
    raf = 0;
    const w = Math.max(el.clientWidth - PAD_X * 2, 360);
    const h = Math.max(el.clientHeight - PAD_TOP - PAD_BOT, 260);
    const key = `${w}x${h}`;
    if (key === last) return;
    last = key;
    paint(w, h);
  };
  run();
  const ro = new ResizeObserver(() => { if (!raf) raf = requestAnimationFrame(run); });
  ro.observe(el);
  return () => { ro.disconnect(); if (raf) cancelAnimationFrame(raf); };
}

/** D32 hover 문법 배선 — 초점은 남고 나머지는 물러난다. 마크마다 리스너를 다는
 *  대신 컨테이너에 위임 3개만 건다(다시 그려도 재배선 불필요 · dispose 가 단순). */
function wireFocus(el: HTMLElement, h: {
  focus: (id: string) => void; clear: () => void; open: (id: string) => void;
  /** 선(엣지) 클릭 — data-e 인덱스. 노드가 우선(마크가 DOM 상 뒤 = 위)이라
   *  노드에 닿은 클릭은 여기로 오지 않는다. 미지정 시 기존 동작 그대로. */
  edge?: (idx: number) => void;
  /** 생키 구간 클릭 — (도메인, mark). 구간 rect 의 data-dom·data-seg 로 자기완결.
   *  구간이 행(data-id)보다 **우선**한다(구간 드릴다운 ≠ 행 열기, D45-V2 3단계). */
  seg?: (dom: string, mark: string) => void;
  /** 타임라인 월-밴드 클릭 — data-month("YYYY-MM"). 노드(data-id)보다 **나중**에
   *  검사해 점 클릭이 우선한다(빈 밴드 클릭만 기간 선택, D45-V2 4단계). */
  month?: (m: string) => void;
}): () => void {
  const hit = (e: Event) => (e.target as Element).closest?.("[data-id]") as SVGElement | null;
  const over = (e: Event) => { const g = hit(e); if (g) h.focus(g.dataset.id!); else h.clear(); };
  const leave = () => h.clear();
  const click = (e: Event) => {
    if (h.seg) {
      const gs = (e.target as Element).closest?.("[data-seg]") as SVGElement | null;
      if (gs) { h.seg(gs.dataset.dom!, gs.dataset.seg!); return; }
    }
    const g = hit(e);
    if (g) { h.open(g.dataset.id!); return; }
    if (h.edge) {
      const ge = (e.target as Element).closest?.("[data-e]") as SVGElement | null;
      if (ge) { h.edge(Number(ge.dataset.e)); return; }
    }
    if (h.month) {
      const gm = (e.target as Element).closest?.("[data-month]") as SVGElement | null;
      if (gm) h.month(gm.dataset.month!);
    }
  };
  el.addEventListener("mouseover", over);
  el.addEventListener("mouseleave", leave);
  el.addEventListener("click", click);
  return () => {
    el.removeEventListener("mouseover", over);
    el.removeEventListener("mouseleave", leave);
    el.removeEventListener("click", click);
  };
}

// ── 1. Flow — 무엇이 무엇을 전제하는가 ────────────────────────────────────
/** 방향 관계만의 리니지 DAG. 층화는 최장경로(수기야마 1단계), 층 안 순서는
 *  배리센터 스윕(2단계)으로 교차를 줄인다. confidence 는 농도와 굵기로만
 *  나타내고(색조 금지), evidence 는 경로의 `data-tip` = 자체 툴팁이 된다. */
export function mountFlow(el: HTMLElement, nodes: FlowNode[], edges: FlowEdge[],
  o: { seed?: string; depth: 1 | 2 | 3; arrows: boolean; labels: boolean;
       onOpen: (id: string) => void;
       /** 선 클릭 → 관계 패널(D45-V). 미지정 = 히트영역·클릭 없음(L0 집계 엣지). */
       onEdge?: (e: FlowEdge) => void }): PlanarHandle {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  // 방향 3종만 남긴다. L0(주제 ties)은 type 대신 weight 로 방향을 싣고 오므로
  // weight 보유 엣지도 통과시킨다(L1/L2 fetch 의 GraphEdge 에는 weight 가 없다).
  const kept = edges.filter((e) =>
    e.source !== e.target && byId.has(e.source) && byId.has(e.target)
    && (DIRECTED.includes(e.type) || e.weight !== undefined));
  const out = new Map<string, FlowEdge[]>();
  const inn = new Map<string, FlowEdge[]>();
  for (const e of kept) {
    (out.get(e.source) || out.set(e.source, []).get(e.source)!).push(e);
    (inn.get(e.target) || inn.set(e.target, []).get(e.target)!).push(e);
  }

  const ids = nodes.map((n) => n.id);
  const layer = new Map<string, number>();
  if (o.seed && byId.has(o.seed)) {
    // 씨앗 리니지 — 하류(+)·상류(-) 각각 depth 세대까지. 첫 방문 승리로
    // 사이클을 끊는다(방문 순서 = 입력 순서라 결정론).
    layer.set(o.seed, 0);
    for (const dir of [1, -1]) {
      let front = [o.seed];
      for (let d = 1; d <= o.depth && front.length; d++) {
        const next: string[] = [];
        for (const v of front)
          for (const e of (dir > 0 ? out.get(v) : inn.get(v)) || []) {
            const nx = dir > 0 ? e.target : e.source;
            if (!layer.has(nx)) { layer.set(nx, dir * d); next.push(nx); }
          }
        front = next;
      }
    }
  } else {
    // 최장경로 층화 — Kahn 위상정렬로 진입차수 0 부터 흘린다. 사이클에 갇혀
    // 큐에 못 든 노드는 확정된 상류의 다음 층으로 밀어 넣는다(첫 방문 승리).
    const indeg = new Map(ids.map((id) => [id, (inn.get(id) || []).length]));
    for (const id of ids) layer.set(id, 0);
    const queue = ids.filter((id) => indeg.get(id) === 0);
    const settled = new Set(queue);
    for (let qi = 0; qi < queue.length; qi++) {
      const v = queue[qi];
      for (const e of out.get(v) || []) {
        layer.set(e.target, Math.max(layer.get(e.target)!, layer.get(v)! + 1));
        const d = indeg.get(e.target)! - 1;
        indeg.set(e.target, d);
        if (d === 0) { settled.add(e.target); queue.push(e.target); }
      }
    }
    for (const id of ids) {
      if (settled.has(id)) continue;
      let mx = -1;
      for (const e of inn.get(id) || [])
        if (settled.has(e.source)) mx = Math.max(mx, layer.get(e.source)!);
      layer.set(id, mx + 1);
      settled.add(id);
    }
    // depth = 세대 상한 — 넘는 층은 그리지 않는다(이미 받아온 서브그래프의 순회 상한).
    for (const id of ids) if (layer.get(id)! > o.depth) layer.delete(id);
  }

  // 그릴 노드 = 방향 엣지에 걸린 노드 + 씨앗(혼자여도 "여기 있다"는 보여준다).
  const linked = new Set<string>();
  for (const e of kept)
    if (layer.has(e.source) && layer.has(e.target)) { linked.add(e.source); linked.add(e.target); }
  const live = ids.filter((id) => layer.has(id) && (linked.has(id) || id === o.seed));
  const liveSet = new Set(live);
  const links = kept.filter((e) => liveSet.has(e.source) && liveSet.has(e.target));

  const minL = live.length ? Math.min(...live.map((id) => layer.get(id)!)) : 0;
  const cols: string[][] = [];
  for (const id of live) {
    const li = layer.get(id)! - minL;
    while (cols.length <= li) cols.push([]);
    cols[li].push(id);
  }

  // 배리센터 2회 스윕 — 정규화 위치(0..1)로 비교해 열마다 개수가 달라도 공평하다.
  // Array#sort 는 안정(ES2019)이라 같은 입력이면 같은 순서로 수렴한다.
  const nbrOf = (id: string) => [
    ...(out.get(id) || []).map((e) => e.target),
    ...(inn.get(id) || []).map((e) => e.source),
  ].filter((x) => liveSet.has(x));
  for (let pass = 0; pass < 2; pass++)
    for (const col of cols) {
      const at = new Map<string, number>();
      for (const c of cols) c.forEach((id, i) => at.set(id, (i + 0.5) / Math.max(c.length, 1)));
      const bary = (id: string) => {
        const ns = nbrOf(id)
          .map((x) => at.get(x))
          .filter((v): v is number => v !== undefined);
        return ns.length ? ns.reduce((a, b) => a + b, 0) / ns.length : at.get(id)!;
      };
      col.sort((a, b) => bary(a) - bary(b));
    }

  const degree = new Map(live.map((id) =>
    [id, (out.get(id) || []).length + (inn.get(id) || []).length]));
  const rOf = (id: string) => 6 + Math.sqrt(degree.get(id) || 1) * 1.4;
  const label = COPY.ui.atlasx.flowChart(live.length, links.length);
  const ROW = 40;   // 노드 최소 세로 간격 — 위에 얹는 라벨이 겹치지 않는 값

  function draw(w: number, h: number): string {
    const maxCol = Math.max(1, ...cols.map((c) => c.length));
    const W = Math.max(w, cols.length * 210);
    const H = Math.max(h, maxCol * ROW + 48);
    const colX = (li: number) =>
      cols.length <= 1 ? W / 2 : 78 + (li * (W - 156)) / (cols.length - 1);
    const pos = new Map<string, { x: number; y: number }>();
    cols.forEach((c, li) => {
      const gap = (H - 48) / Math.max(c.length, 1);
      c.forEach((id, i) => pos.set(id, { x: colX(li), y: 34 + gap * (i + 0.5) }));
    });

    // 화살촉은 열린 층(prefs.arrows)에서만. 초점 리니지용 촉을 따로 두어
    // 강조된 선이 회색 촉으로 끝나지 않게 한다(marker 는 참조부 캐스케이드 밖).
    const defs = o.arrows ? `<defs>
        <marker id="pl-arw" viewBox="0 0 8 8" refX="7.2" refY="4" markerWidth="5.5"
                markerHeight="5.5" orient="auto-start-reverse"><path class="pl-arrow" d="M0.4 1 L7.4 4 L0.4 7 Z"/></marker>
        <marker id="pl-arw-hl" viewBox="0 0 8 8" refX="7.2" refY="4" markerWidth="5.5"
                markerHeight="5.5" orient="auto-start-reverse"><path class="pl-arrow pl-arrow-hl" d="M0.4 1 L7.4 4 L0.4 7 Z"/></marker>
      </defs>` : "";

    const paths = links.map((e, i) => {
      const a = pos.get(e.source)!, b = pos.get(e.target)!;
      // confidence → 농도(.35+.5c) · 굵기(1+c). 색조는 절대 건드리지 않는다(D41).
      const c = e.confidence === undefined ? 0.6 : Math.max(0, Math.min(1, e.confidence));
      const x1 = a.x + rOf(e.source);
      const x2 = b.x - rOf(e.target) - (o.arrows ? 3 : 0);
      const tip = e.evidence
        || `${byId.get(e.source)!.label} → ${byId.get(e.target)!.label}`;
      // 히트영역(D45-V 1단계): 1~2px 선은 Fitts 상 최악의 표적 — 같은 경로에
      // 투명 14px 스트로크를 겹쳐 클릭/hover 표적만 넓힌다(GLSP 표준 기법).
      // pointer-events="stroke" 가 핵심: 투명 스트로크도 포인터를 받는다.
      const hitPath = o.onEdge
        ? `<path class="pl-hit" d="${curveH(x1, a.y, x2, b.y)}" fill="none"`
          + ` stroke="transparent" stroke-width="14" pointer-events="stroke"`
          + ` style="cursor:pointer"></path>`
        : "";
      return `<g class="pl-link" data-e="${i}"><path class="pl-edge" d="${curveH(x1, a.y, x2, b.y)}"`
        + ` stroke-opacity="${(0.35 + 0.5 * c).toFixed(2)}" stroke-width="${(1 + c).toFixed(2)}"`
        + `${o.arrows ? ` marker-end="url(#pl-arw)"` : ""} data-tip="${esc(tip)}"></path>${hitPath}</g>`;
    }).join("");

    const marks = live.map((id) => {
      const n = byId.get(id)!, p = pos.get(id)!, r = rOf(id);
      const lab = o.labels
        ? `<text class="pl-lab" x="${num(p.x)}" y="${num(p.y - r - 6)}" text-anchor="middle">${esc(cut(n.label, 24))}</text>`
        : "";
      return `<g class="pl-node" data-id="${esc(id)}">`
        + `<circle class="pl-mark${id === o.seed ? " pl-focus" : ""}" cx="${num(p.x)}" cy="${num(p.y)}" r="${num(r)}"`
        + ` data-tip="${esc(n.label)}"></circle>${lab}</g>`;
    }).join("");

    return `<svg class="pl-svg" aria-label="${esc(label)}"`
      + ` viewBox="0 0 ${num(W)} ${num(H)}" width="${num(W)}" height="${num(H)}">`
      + `${defs}${paths}${marks}</svg>`;
  }

  let nodeEls: SVGElement[] = [];
  let linkEls: SVGElement[] = [];
  const collect = () => {
    nodeEls = [...el.querySelectorAll<SVGElement>(".pl-node")];
    linkEls = [...el.querySelectorAll<SVGElement>(".pl-link")];
  };

  /** 리니지 도달집합 — 상류·하류를 따로 훑는다(양쪽에 걸친 노드에서 한쪽이
   *  다른 쪽 탐색을 막지 않게). RELATED_TO 는 애초에 들어오지 않는다. */
  function reach(id: string) {
    const keepN = new Set([id]);
    const keepE = new Set<FlowEdge>();
    for (const dir of [1, -1]) {
      const seen = new Set([id]);
      const q = [id];
      for (let qi = 0; qi < q.length; qi++)
        for (const e of (dir > 0 ? out.get(q[qi]) : inn.get(q[qi])) || []) {
          if (!liveSet.has(e.source) || !liveSet.has(e.target)) continue;
          keepE.add(e);
          const nx = dir > 0 ? e.target : e.source;
          if (!seen.has(nx)) { seen.add(nx); keepN.add(nx); q.push(nx); }
        }
    }
    return { keepN, keepE };
  }
  const setArrow = (g: SVGElement, on: boolean) => {
    if (o.arrows) g.querySelector("path")?.setAttribute("marker-end", `url(#pl-arw${on ? "-hl" : ""})`);
  };
  function focus(id: string) {
    if (!liveSet.has(id)) { clear(); return; }
    const { keepN, keepE } = reach(id);
    for (const g of nodeEls) {
      const on = keepN.has(g.dataset.id!);
      g.classList.toggle("pl-fade", !on);
      g.classList.toggle("pl-hl", on);
    }
    for (const g of linkEls) {
      const on = keepE.has(links[Number(g.dataset.e)]);
      g.classList.toggle("pl-fade", !on);
      g.classList.toggle("pl-hl", on);
      setArrow(g, on);
    }
  }
  function clear() {
    for (const g of nodeEls) g.classList.remove("pl-fade", "pl-hl");
    for (const g of linkEls) { g.classList.remove("pl-fade", "pl-hl"); setArrow(g, false); }
  }

  const stopSize = trackSize(el, (w, h) => { el.innerHTML = wrap(draw(w, h)); collect(); });
  const stopFocus = wireFocus(el, {
    focus, clear, open: o.onOpen,
    edge: o.onEdge ? (i) => { const e = links[i]; if (e) o.onEdge!(e); } : undefined,
  });
  return { dispose() { stopFocus(); stopSize(); } };
}

// ── 2. Timeline — 언제 무엇이 생겼는가 ────────────────────────────────────
/** 시간축 하나 + 주제당 레인 하나. ts 는 파싱하지 않는다: ISO 는 사전순이
 *  곧 시간순이고(자유서식 값도 같은 규칙으로 안전하게 접힌다), 월 거터는
 *  ts.slice(0,7) 로 만든다. 밀집 구간은 최소간격 디클러터 + 지시선으로 푼다. */
export function mountTimeline(el: HTMLElement,
  nodes: (FlowNode & { ts?: string; kind?: string })[],
  o: { docs: boolean; labels: boolean; onOpen: (id: string) => void;
       /** 월-밴드 클릭 → 그 달("YYYY-MM") 기간 항목(D45-V2 4단계). 세로 밴드
        *  타임라인에 맞춰 브러시를 월 단위로 이산화한다(자유 픽셀 드래그 대신). */
       onMonth?: (month: string) => void }): PlanarHandle {
  const pool = timelinePool(nodes, o.docs);
  // 날짜 없는 노드는 레인에 올리지 않는다(빈 ts 는 정상값 — 상류가 자주 비운다).
  const dated = pool.filter((n) => !!(n.ts && n.ts.trim()))
    .slice()
    .sort((a, b) => (a.ts! < b.ts! ? -1 : a.ts! > b.ts! ? 1 : a.id < b.id ? -1 : 1));
  const lanes = [...new Set(dated.map((n) => n.domain || ""))].sort();
  const laneAt = new Map(lanes.map((d, i) => [d, i]));
  const months = [...new Set(dated.map((n) => n.ts!.slice(0, 7)))];   // 정렬본 → 이미 오름차순
  const monthAt = new Map(months.map((m, i) => [m, i]));
  // 월 안 순위 = 전역 정렬 순위 — 레인이 달라도 세로 위치가 시간순을 지킨다.
  const rank = new Map<string, number>();
  const per = new Map<string, number>();
  for (const n of dated) {
    const m = n.ts!.slice(0, 7);
    const k = per.get(m) || 0;
    rank.set(n.id, k);
    per.set(m, k + 1);
  }
  const label = COPY.ui.atlasx.timelineChart(dated.length, lanes.length);
  const LANE_W = 190;
  /** 월 밴드 최소 높이(성긴 달의 기존 인상 유지)와 항목 1건이 요구하는 세로 슬롯.
   *  SLOT_H 는 라벨 디클러터 최소 간격(13)과 같은 값이어야 한다 — 밴드가 그보다
   *  촘촘하면 점은 들어가도 라벨이 전부 밀려 지시선만 남는다. */
  const BAND_MIN = 96, SLOT_H = 13;

  function draw(w: number, h: number): string {
    const W = Math.max(w, 120 + lanes.length * LANE_W);
    const top = 74, bot = 26;
    // D46-S: 밴드 높이는 **그 달의 항목 수에 비례**한다. 고정 96px 시절엔 한 달에
    // 40건이 들어오면 간격이 2.4px 로 눌려(마크 반지름 5.2px) 서로 겹쳤다 —
    // L0 가 전량이 되면서 흔한 상황이 됐다. 넘치는 높이는 .pl-wrap 이 스크롤한다.
    const rawH = months.map((m) => Math.max(BAND_MIN, (per.get(m) || 1) * SLOT_H));
    const rawSum = rawH.reduce((a, v) => a + v, 0);
    const H = Math.max(h, top + bot + rawSum);
    // 컨테이너가 더 크면 남는 높이를 비례 배분한다(빈 아래 여백 대신 숨 쉬는 밴드).
    const k = Math.max(1, (H - top - bot) / Math.max(rawSum, 1));
    const bandH = rawH.map((v) => v * k);
    const bandTop: number[] = [];
    bandH.reduce((y, v, i) => { bandTop[i] = y; return y + v; }, top);
    const laneX = (d: string) =>
      110 + ((laneAt.get(d) || 0) + 0.5) * ((W - 130) / Math.max(lanes.length, 1));
    const yOf = (n: { id: string; ts?: string }) => {
      const i = monthAt.get(n.ts!.slice(0, 7)) || 0;
      const m = n.ts!.slice(0, 7);
      return bandTop[i] + (bandH[i] * (rank.get(n.id)! + 0.5)) / Math.max(per.get(m) || 1, 1);
    };

    // 월 거터 — 홀짝 밴드 + 좌측 눈금. 밴드는 muted 면이라 색이 아니라 면이 구분한다.
    // onMonth 지정 시 밴드 전폭에 투명 히트 rect(data-month) — lanes 보다 먼저
    // 그려 노드 밑에 깔린다(점 클릭이 우선, 빈 밴드 클릭만 기간 선택).
    const bands = months.map((m, i) =>
      (i % 2 === 1
        ? `<rect class="pl-band" x="34" y="${num(bandTop[i])}" width="${num(W - 46)}" height="${num(bandH[i])}"/>`
        : "")
      + (o.onMonth
        ? `<rect class="pl-band-hit" data-month="${esc(m)}" x="34" y="${num(bandTop[i])}"`
          + ` width="${num(W - 46)}" height="${num(bandH[i])}" fill="transparent" style="cursor:pointer"/>`
        : "")
      + `<text class="pl-cap pl-num" x="38" y="${num(bandTop[i] + 13)}">${esc(m)}</text>`)
      .join("");

    const laneGs = lanes.map((d) => {
      const list = dated.filter((n) => (n.domain || "") === d);
      const xs = laneX(d);
      const ys = list.map(yOf);
      const y0 = Math.min(...ys) - 14, y1 = Math.max(...ys) + 14;
      // 레인 굵기 = 밀도(개수). 굵기이지 색이 아니다.
      const sw = list.length >= 8 ? 2.4 : list.length >= 4 ? 1.8 : 1.2;
      const line = `<path class="pl-lane-line" stroke-width="${sw}" d="M${num(xs)} ${num(y0)} L${num(xs)} ${num(y1)}"/>`;
      const head = `<text class="pl-sub" x="${num(xs)}" y="${num(top - 22)}" text-anchor="middle">${esc(cut(d, 18))}</text>`
        + `<text class="pl-cap pl-num" x="${num(xs)}" y="${num(top - 9)}" text-anchor="middle">${list.length}</text>`;
      const ly = declutter(ys, 13);
      const marks = list.map((n, i) => {
        const y = ys[i], lyi = ly[i];
        const doc = DOC_KINDS.includes(n.kind || "");
        const mark = doc
          ? `<polygon class="pl-mark pl-soft" points="${hexPts(xs, y, 5)}"/>`
          : `<circle class="pl-mark" cx="${num(xs)}" cy="${num(y)}" r="5.2"/>`;
        // 라벨이 밀렸으면 지시선으로 원래 자리를 되돌려 준다(디클러터의 짝).
        const lead = Math.abs(lyi - y) > 1.5
          ? `<path class="pl-ax" d="M${num(xs + 6)} ${num(y)} L${num(xs + 11)} ${num(lyi)}"/>` : "";
        const text = o.labels
          ? `<text class="pl-lab" x="${num(xs + 13)}" y="${num(lyi + 4)}">${esc(cut(n.label, 20))}</text>` : "";
        return `<g class="pl-node" data-id="${esc(n.id)}" data-tip="${esc(n.label)} ${esc(n.ts || "")}">`
          + `${mark}${lead}${text}</g>`;
      }).join("");
      return `<g class="pl-lane" data-lane="${esc(d)}">${line}${head}${marks}</g>`;
    }).join("");

    return `<svg class="pl-svg" aria-label="${esc(label)}"`
      + ` viewBox="0 0 ${num(W)} ${num(H)}" width="${num(W)}" height="${num(H)}">`
      + `${bands}${laneGs}</svg>`;
  }

  const domOf = new Map(dated.map((n) => [n.id, n.domain || ""]));
  let laneEls: SVGElement[] = [];
  let nodeEls: SVGElement[] = [];
  const collect = () => {
    laneEls = [...el.querySelectorAll<SVGElement>(".pl-lane")];
    nodeEls = [...el.querySelectorAll<SVGElement>(".pl-node")];
  };
  // 여기서 "관련"은 같은 레인(주제) — mountTimeline 은 엣지를 받지 않는다.
  function focus(id: string) {
    const d = domOf.get(id);
    if (d === undefined) { clear(); return; }
    for (const g of laneEls) g.classList.toggle("pl-fade", g.dataset.lane !== d);
    for (const g of nodeEls) g.classList.toggle("pl-hl", g.dataset.id === id);
  }
  function clear() {
    for (const g of laneEls) g.classList.remove("pl-fade");
    for (const g of nodeEls) g.classList.remove("pl-hl");
  }

  const stopSize = trackSize(el, (w, h) => { el.innerHTML = wrap(draw(w, h)); collect(); });
  const stopFocusT = wireFocus(el, { focus, clear, open: o.onOpen, month: o.onMonth });
  return { dispose() { stopFocusT(); stopSize(); } };
}

// ── 3. Totals — 지식은 어디서 와서 어디에 쌓이고 어떤 상태인가 ─────────────
/** 3열 생키(정본 [[vinc.d.graph-views]] §2.3 복귀, D47).
 *
 *  열: **유입원 → 주제 → 연결 상태**. 구 구현은 한 축만 고르는 누적 막대라
 *  "대화에서 온 개념이 어느 주제에 쌓였고 그중 몇이 고아인가"를 경로로 추적할
 *  수 없었다 — 생키의 존재 이유가 그 경로다.
 *
 *  **보존 불변**: 사이드카 폴드가 출처 3분할·연결 3분할을 각각 배타로 세므로
 *  (query.py `ledger`) 두 합이 모두 정확히 `concepts` 다. 따라서 1열 합 = 2열 합
 *  = 3열 합이 항상 성립하고, 어긋나면 그건 집계 결함이라 화면이 먼저 드러낸다
 *  (노드 높이를 두 합의 큰 쪽으로 잡아 리본이 노드를 넘치지 않게만 방어한다).
 *
 *  **고아 필터를 여기서는 받지 않는다**: 고아를 숨기려면 (출처 × 상태) 교차표가
 *  필요한데 원장은 주변합만 준다 — 주변합으로 고아를 걷어내면 1열과 3열의 합이
 *  달라져 보존이 깨진다. 게다가 고아 리본은 D32 고아 최소화 동선의 진입점이라
 *  숨기는 것이 이 뷰의 목적과 반대다. 교차표가 필요해지면 `/ledger` 확장(별건).
 *
 *  색은 여전히 단일 잉크다 — bklit 의 소스→타깃 그라디언트는 모노크롬 규율
 *  (정본 §5 검증기 FAIL)에 걸려 채택하지 않고, 위계는 굵기와 농도만 나른다. */
const ORIGINS = ["episodic", "record", "unsourced"] as const;
const STATES = ["connected", "gateway", "orphan"] as const;
/** 생키 치수 — bklit 규격(nodeWidth 16 · nodePadding 24 · lineCap 4)을 그대로 쓴다. */
const NODE_W = 16, NODE_PAD = 24, NODE_CAP = 4;
/** 가장 작은 노드도 이만큼은 보인다(스케일 하한 → 넘치면 H 가 자라고 스크롤). */
const NODE_MIN = 5;

export function mountTotals(el: HTMLElement, rows: LedgerRow[],
  o: { current?: string;
       onOpen: (domain: string) => void;
       /** 리본 클릭 → 그 (도메인, mark) 의 개념 목록 드릴다운(D45-V2 3단계).
        *  구 누적 막대의 구간 클릭과 계약이 같다 — 리본이 곧 그 구간이다. */
       onSeg?: (domain: string, mark: string) => void }): PlanarHandle {
  const val = (r: LedgerRow, k: string) => Math.max(0, Number((r as unknown as Record<string, unknown>)[k]) || 0);
  // 주제(2열) — 두 축 합이 다르면(구버전·집계 결함) 큰 쪽을 노드 높이로 삼아
  // 리본이 노드를 넘치지 않게만 방어한다. 정상 데이터에선 둘이 같다.
  const data = rows.map((r) => {
    const origin = ORIGINS.map((k) => val(r, k));
    const state = STATES.map((k) => val(r, k));
    const oSum = origin.reduce((a, b) => a + b, 0);
    const sSum = state.reduce((a, b) => a + b, 0);
    return { domain: r.domain, origin, state, total: Math.max(oSum, sSum) };
  }).filter((r) => r.total > 0);
  const grand = data.reduce((a, r) => a + r.total, 0);
  // 1열·3열 노드 값 = 주제를 가로지른 합(주변합).
  const originTot = ORIGINS.map((_, i) => data.reduce((a, r) => a + r.origin[i], 0));
  const stateTot = STATES.map((_, i) => data.reduce((a, r) => a + r.state[i], 0));
  const label = COPY.ui.atlasx.totalsChart(data.length, grand);
  const segName = (k: string) => COPY.ui.atlasx.seg[k as keyof typeof COPY.ui.atlasx.seg];
  const segTip = (k: string, v: number) => COPY.ui.atlasx.segTip(segName(k), v);
  /** 첫 그림에만 진입 안무를 얹는다 — 리사이즈 재그리기마다 다시 자라면 소음이다. */
  let first = true;

  function draw(w: number, h: number): string {
    const W = Math.max(w, 620);
    const labW = 136;                       // 좌우 라벨 여백(bklit margin 180 의 축약)
    const TOP = 48, BOT = 34;               // 열 머리글 / 총계
    // 스케일 — 노드가 가장 많은 열이 기준이고, 가장 작은 노드도 NODE_MIN 은 되게
    // 높이를 키운다(넘치면 .pl-wrap 이 세로 스크롤). 전부 입력의 순수 함수.
    const counts = [ORIGINS.length, data.length, STATES.length];
    const maxCount = Math.max(...counts);
    const vals = [...originTot, ...data.map((r) => r.total), ...stateTot].filter((v) => v > 0);
    const minVal = vals.length ? Math.min(...vals) : 1;
    const need = TOP + BOT + (grand * NODE_MIN) / minVal + (maxCount - 1) * NODE_PAD;
    const H = Math.max(h, need);
    const availH = H - TOP - BOT;
    const scale = (availH - (maxCount - 1) * NODE_PAD) / Math.max(grand, 1);
    const xs = [labW, (W - NODE_W) / 2, W - labW - NODE_W];

    /** 열 하나를 세로 중앙에 쌓는다 — 값 순서는 입력 순서 그대로(결정론). */
    const stack = (values: number[]) => {
      const hsum = values.reduce((a, v) => a + v * scale, 0);
      const gaps = (values.length - 1) * NODE_PAD;
      let y = TOP + (availH - hsum - gaps) / 2;
      return values.map((v) => { const top = y; y += v * scale + NODE_PAD; return { y: top, h: v * scale }; });
    };
    const oBox = stack(originTot);
    const dBox = stack(data.map((r) => r.total));
    const sBox = stack(stateTot);

    // 리본 — 노드 안에서 값 순서대로 쌓아 교차를 줄인다. 들어오는 쪽 순서는
    // 나가는 쪽이 정한 순서와 같아야 리본이 꼬이지 않는다(1열 순서 = ORIGINS,
    // 3열 순서 = data 순서).
    const oOff = originTot.map(() => 0);
    const dInOff = data.map(() => 0);
    const dOutOff = data.map(() => 0);
    const sOff = stateTot.map(() => 0);
    const links: string[] = [];
    let li = 0;
    // 농도 정규화 기준은 **리본 최댓값**이다. 예전엔 노드 최솟값×4 로 나눴는데,
    // 그 분모가 실제 리본 값 범위보다 커서 값이 6배 달라져도 농도가 0.32~0.50
    // 안에서만 움직였다 — 농도 채널이 사실상 아무것도 나르지 않았다(실측).
    const ribMax = Math.max(1, ...data.flatMap((r) => [...r.origin, ...r.state]));
    const opOf = (v: number) => 0.16 + 0.5 * (v / ribMax);
    /** 채움 밴드 — 소스 상단 → 타깃 상단으로 흐르고 두께 t 만큼 되돌아와 닫는다.
     *  선(stroke) 리본이 아니라 면(fill)이라 **테두리를 줄 수 있다** — 겹친 리본이
     *  한 덩어리로 뭉개지지 않게 하는 유일한 방법(모노크롬에선 색으로 못 가른다). */
    const band = (x1: number, yTop1: number, x2: number, yTop2: number, t: number) => {
      const mx = (x1 + x2) / 2;
      const y1b = yTop1 + t, y2b = yTop2 + t;
      return `M${num(x1)} ${num(yTop1)} C${num(mx)} ${num(yTop1)} ${num(mx)} ${num(yTop2)} ${num(x2)} ${num(yTop2)}`
        + ` L${num(x2)} ${num(y2b)} C${num(mx)} ${num(y2b)} ${num(mx)} ${num(y1b)} ${num(x1)} ${num(y1b)} Z`;
    };
    const ribbon = (x1: number, yTop1: number, x2: number, yTop2: number, v: number,
                    dom: string, mark: string, from: string, to: string) => {
      const t = Math.max(v * scale, 1);
      // 값 비례 농도 — 색조는 건드리지 않는다. 굵기가 값의 정본이고 농도는 읽기
      // 보조다(정본 §5: 변별은 위치·두께·형태·직접 라벨).
      const op = opOf(v).toFixed(3);
      // 테두리는 밴드가 충분히 두꺼울 때만 — 1~2px 리본에 0.75px 테두리를 두르면
      // 밴드가 테두리에 먹혀 사라진다.
      const ol = t >= 3 ? 0.75 : 0;
      // data-dom·data-seg 는 **항상** 싣는다 — 드릴다운의 표적이자 hover 초점
      // 계산의 열쇠라, onSeg 유무로 빠지면 hover 문법까지 같이 죽는다.
      const cls = `pl-ribbon${o.onSeg ? " pl-drill" : ""}${first ? " pl-in" : ""}`;
      links.push(`<g class="${cls}" data-e="${li}" data-dom="${esc(dom)}"`
        + ` data-seg="${esc(mark)}" style="--i:${li}">`
        + `<path class="pl-flow" d="${band(x1, yTop1, x2, yTop2, t)}"`
        + ` fill-opacity="${op}" stroke-width="${ol}"`
        + ` data-tip="${esc(COPY.ui.atlasx.ribbonTip(from, to, v))}"></path></g>`);
      li++;
    };

    ORIGINS.forEach((k, oi) => {
      data.forEach((r, di) => {
        const v = r.origin[oi];
        if (v <= 0) return;
        const y1 = oBox[oi].y + oOff[oi];
        const y2 = dBox[di].y + dInOff[di];
        oOff[oi] += v * scale; dInOff[di] += v * scale;
        ribbon(xs[0] + NODE_W, y1, xs[1], y2, v, r.domain, k, segName(k), r.domain);
      });
    });
    data.forEach((r, di) => {
      STATES.forEach((k, si) => {
        const v = r.state[si];
        if (v <= 0) return;
        const y1 = dBox[di].y + dOutOff[di];
        const y2 = sBox[si].y + sOff[si];
        dOutOff[di] += v * scale; sOff[si] += v * scale;
        ribbon(xs[1] + NODE_W, y1, xs[2], y2, v, r.domain, k, r.domain, segName(k));
      });
    });

    /** 노드 사각 + 바깥쪽 직접 라벨(1·3열). 판(plate)은 2열에만 필요하다. */
    const sideNode = (id: string, x: number, box: { y: number; h: number },
                      key: string, v: number, right: boolean, i: number) => {
      if (v <= 0) return "";
      const ty = box.y + box.h / 2;
      const lx = right ? x + NODE_W + 10 : x - 10;
      return `<g class="pl-node pl-static${first ? " pl-in" : ""}" data-id="${esc(id)}" style="--i:${i}">`
        + `<rect class="pl-snode" x="${num(x)}" y="${num(box.y)}" width="${NODE_W}"`
        + ` height="${num(Math.max(box.h, 1))}" rx="${NODE_CAP}"`
        + ` data-tip="${esc(segTip(key, v))}"></rect>`
        + `<text class="pl-lab" x="${num(lx)}" y="${num(ty - 1)}"`
        + ` text-anchor="${right ? "start" : "end"}">${esc(cut(segName(key), 20))}</text>`
        + `<text class="pl-cap pl-num" x="${num(lx)}" y="${num(ty + 12)}"`
        + ` text-anchor="${right ? "start" : "end"}">${v}</text></g>`;
    };
    const originNodes = ORIGINS.map((k, i) =>
      sideNode(`org:${k}`, xs[0], oBox[i], k, originTot[i], false, i)).join("");
    const stateNodes = STATES.map((k, i) =>
      sideNode(`st:${k}`, xs[2], sBox[i], k, stateTot[i], true, i)).join("");

    // 2열(주제) — 라벨이 리본 위를 지나므로 판을 깔고 얹는다. 판 폭은 추정하지
    // 않는다: 여기선 자리만 잡고 마운트 후 getBBox() 실측으로 맞춘다(정본 §6
    // 실패 사례 1). 이미 그 주제를 보고 있는 행은 눌러도 갈 곳이 없어 정적이다.
    const domainNodes = data.map((r, i) => {
      const dead = r.domain === o.current;
      const ty = dBox[i].y + dBox[i].h / 2;
      const lx = xs[1] + NODE_W + 9;
      return `<g class="pl-node${dead ? " pl-static" : ""}${first ? " pl-in" : ""}"`
        + ` data-id="${esc(r.domain)}"${dead ? "" : ` data-open="${esc(r.domain)}"`} style="--i:${i}">`
        + `<rect class="pl-snode" x="${num(xs[1])}" y="${num(dBox[i].y)}" width="${NODE_W}"`
        + ` height="${num(Math.max(dBox[i].h, 1))}" rx="${NODE_CAP}"`
        + ` data-tip="${esc(COPY.ui.atlasx.segTip(r.domain, r.total))}"></rect>`
        + `<g class="pl-plate-g">`
        + `<rect class="pl-plate" x="${num(lx - 5)}" y="${num(ty - 10)}" width="10" height="20" rx="3"/>`
        + `<text class="pl-lab" x="${num(lx)}" y="${num(ty + 4)}">${esc(cut(r.domain, 18))}`
        + ` <tspan class="pl-sub pl-num">${r.total}</tspan></text></g></g>`;
    }).join("");

    const head = (x: number, anchor: string, t: string) =>
      `<text class="pl-cap" x="${num(x)}" y="26" text-anchor="${anchor}">${esc(t)}</text>`;
    const heads = head(xs[0] + NODE_W, "end", COPY.ui.atlasx.colOrigin)
      + head(xs[1] + NODE_W / 2, "middle", COPY.ui.atlasx.colTopic)
      + head(xs[2], "start", COPY.ui.atlasx.colState);

    // 총계 — 세 열의 합이 같음을 눈으로 확인시킨다(보존 불변의 화면판).
    const foot = `<text class="pl-sub pl-num" x="${num(W / 2)}" y="${num(H - 12)}"`
      + ` text-anchor="middle">${esc(COPY.ui.atlasx.totalsFoot(grand))}</text>`;

    return `<svg class="pl-svg pl-sankey" aria-label="${esc(label)}"`
      + ` viewBox="0 0 ${num(W)} ${num(H)}" width="${num(W)}" height="${num(H)}">`
      // 리본은 한 겹으로 묶는다 — 겹침 블렌딩(multiply/screen)이 **리본끼리만**
      // 일어나고 배경·노드로 새지 않게 하는 격리 경계(isolation: isolate).
      + `${heads}<g class="pl-ribbons">${links.join("")}</g>`
      + `${originNodes}${domainNodes}${stateNodes}${foot}</svg>`;
  }

  let nodeEls: SVGElement[] = [];
  let ribEls: SVGElement[] = [];
  /** 판 폭 실측 — 숨은 채로 그리면 getBBox() 가 0 을 준다(정본 §6 실패 1).
   *  0 이 나오면 다음 프레임에 다시 잰다(탭이 보이는 순간 맞춰진다). */
  function fitPlates(retry = true) {
    let blind = false;
    for (const g of el.querySelectorAll<SVGGElement>(".pl-plate-g")) {
      const t = g.querySelector("text");
      const p = g.querySelector<SVGRectElement>(".pl-plate");
      if (!t || !p) continue;
      const b = (t as SVGTextElement).getBBox();
      if (!b.width) { blind = true; continue; }
      p.setAttribute("x", String(Math.round((b.x - 6) * 10) / 10));
      p.setAttribute("y", String(Math.round((b.y - 3) * 10) / 10));
      p.setAttribute("width", String(Math.round((b.width + 12) * 10) / 10));
      p.setAttribute("height", String(Math.round((b.height + 6) * 10) / 10));
    }
    if (blind && retry) requestAnimationFrame(() => fitPlates(false));
  }
  const collect = () => {
    nodeEls = [...el.querySelectorAll<SVGElement>(".pl-node")];
    ribEls = [...el.querySelectorAll<SVGElement>(".pl-ribbon")];
    fitPlates();
  };

  /** D32-a — hover 한 노드·리본과 그에 닿은 것만 남고 나머지는 물러난다.
   *  생키의 "닿음"은 리본 하나가 잇는 (주제 · 구간) 쌍이다. */
  function paint(keepNode: Set<string>, keepRib: Set<Element>) {
    for (const g of nodeEls) {
      const on = keepNode.has(g.dataset.id || "");
      g.classList.toggle("pl-fade", !on);
      g.classList.toggle("pl-hl", on);
    }
    for (const g of ribEls) {
      const on = keepRib.has(g);
      g.classList.toggle("pl-fade", !on);
      g.classList.toggle("pl-hl", on);
    }
  }
  /** 구간 키가 1열인지 3열인지 — 두 사전은 서로 겹치지 않는다(배타 6종). */
  const sideId = (mark: string) =>
    (ORIGINS as readonly string[]).includes(mark) ? `org:${mark}` : `st:${mark}`;

  function focusNode(id: string) {
    const keepN = new Set([id]);
    const keepR = new Set<Element>();
    for (const g of ribEls) {
      const dom = g.dataset.dom || "";
      const mark = g.dataset.seg || "";
      // 주제 노드는 자기 리본 전부, 구간 노드(org:/st:)는 그 구간의 리본 전부.
      if (id !== dom && id !== sideId(mark)) continue;
      keepR.add(g);
      keepN.add(dom);
      keepN.add(sideId(mark));
    }
    paint(keepN, keepR);
  }
  function focusRibbon(g: SVGElement) {
    paint(new Set([g.dataset.dom || "", sideId(g.dataset.seg || "")]), new Set([g]));
  }
  function clear() {
    for (const g of [...nodeEls, ...ribEls]) g.classList.remove("pl-fade", "pl-hl");
  }

  // 위임 3개 — 마크마다 리스너를 달지 않는다(다시 그려도 재배선 불필요).
  // 리본이 노드보다 먼저 검사되면 안 된다: 노드는 DOM 상 뒤(위)라 겹친 자리의
  // 포인터를 이미 가져간다. hover 는 노드 우선, 그다음 리본.
  const over = (e: Event) => {
    const t = e.target as Element;
    const n = t.closest?.(".pl-node") as SVGElement | null;
    if (n) { focusNode(n.dataset.id || ""); return; }
    const r = t.closest?.(".pl-ribbon") as SVGElement | null;
    if (r) { focusRibbon(r); return; }
    clear();
  };
  const leave = () => clear();
  const click = (e: Event) => {
    const t = e.target as Element;
    // 리본 클릭 = 구간 드릴다운(주제 열기보다 우선 — 더 구체적인 표적).
    const seg = t.closest?.("[data-seg]") as SVGElement | null;
    if (seg && o.onSeg) { o.onSeg(seg.dataset.dom!, seg.dataset.seg!); return; }
    const open = t.closest?.("[data-open]") as SVGElement | null;
    if (open) o.onOpen(open.dataset.open!);
  };
  el.addEventListener("mouseover", over);
  el.addEventListener("mouseleave", leave);
  el.addEventListener("click", click);

  const stopSize = trackSize(el, (w, h) => {
    el.innerHTML = wrap(draw(w, h));
    collect();
  });
  // 진입 안무는 최초 1회 — 그런데 `first` 를 그리기 콜백 안에서 내리면 **애니메이션이
  // 사실상 안 보인다**: 첫 동기 그리기는 아직 레이아웃 전 크기(0 일 수 있다)로 나가고,
  // 곧바로 ResizeObserver 가 실제 크기로 다시 그리면서 방금 시작한 안무를 지운다
  // (실측: 화면의 리본에 pl-in 이 없었다). 레이아웃이 앉을 때까지(더블 rAF) 기다렸다
  // 내려야 "사용자가 처음 보는 그림"에서 한 번 재생된다. 그 뒤 리사이즈는 조용하다.
  const settle = requestAnimationFrame(() =>
    requestAnimationFrame(() => { first = false; }));
  return {
    dispose() {
      cancelAnimationFrame(settle);
      el.removeEventListener("mouseover", over);
      el.removeEventListener("mouseleave", leave);
      el.removeEventListener("click", click);
      stopSize();
    },
  };
}
