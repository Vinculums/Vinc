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
 *  트리를 통째로 접어 노드 · 엣지 · 구간마다 달아 둔 <title> 을 전부 가린다.
 *  이름은 남기고 안은 열어 둔다(키보드 순회는 이 코드베이스에 없는 문법이라
 *  새로 만들지 않는다 — 가리지 않는 것까지가 이 변경의 범위다). */

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
}): () => void {
  const hit = (e: Event) => (e.target as Element).closest?.("[data-id]") as SVGElement | null;
  const over = (e: Event) => { const g = hit(e); if (g) h.focus(g.dataset.id!); else h.clear(); };
  const leave = () => h.clear();
  const click = (e: Event) => { const g = hit(e); if (g) h.open(g.dataset.id!); };
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
 *  나타내고(색조 금지), evidence 는 경로의 <title> = 네이티브 툴팁이 된다. */
export function mountFlow(el: HTMLElement, nodes: FlowNode[], edges: FlowEdge[],
  o: { seed?: string; depth: 1 | 2 | 3; arrows: boolean; labels: boolean;
       onOpen: (id: string) => void }): PlanarHandle {
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
      return `<g class="pl-link" data-e="${i}"><path class="pl-edge" d="${curveH(x1, a.y, x2, b.y)}"`
        + ` stroke-opacity="${(0.35 + 0.5 * c).toFixed(2)}" stroke-width="${(1 + c).toFixed(2)}"`
        + `${o.arrows ? ` marker-end="url(#pl-arw)"` : ""}><title>${esc(tip)}</title></path></g>`;
    }).join("");

    const marks = live.map((id) => {
      const n = byId.get(id)!, p = pos.get(id)!, r = rOf(id);
      const lab = o.labels
        ? `<text class="pl-lab" x="${num(p.x)}" y="${num(p.y - r - 6)}" text-anchor="middle">${esc(cut(n.label, 24))}</text>`
        : "";
      return `<g class="pl-node" data-id="${esc(id)}">`
        + `<circle class="pl-mark${id === o.seed ? " pl-focus" : ""}" cx="${num(p.x)}" cy="${num(p.y)}" r="${num(r)}">`
        + `<title>${esc(n.label)}</title></circle>${lab}</g>`;
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
  const stopFocus = wireFocus(el, { focus, clear, open: o.onOpen });
  return { dispose() { stopFocus(); stopSize(); } };
}

// ── 2. Timeline — 언제 무엇이 생겼는가 ────────────────────────────────────
/** 시간축 하나 + 주제당 레인 하나. ts 는 파싱하지 않는다: ISO 는 사전순이
 *  곧 시간순이고(자유서식 값도 같은 규칙으로 안전하게 접힌다), 월 거터는
 *  ts.slice(0,7) 로 만든다. 밀집 구간은 최소간격 디클러터 + 지시선으로 푼다. */
export function mountTimeline(el: HTMLElement,
  nodes: (FlowNode & { ts?: string; kind?: string })[],
  o: { docs: boolean; labels: boolean; onOpen: (id: string) => void }): PlanarHandle {
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
  const LANE_W = 190, BAND_H = 96;

  function draw(w: number, h: number): string {
    const W = Math.max(w, 120 + lanes.length * LANE_W);
    const H = Math.max(h, 90 + months.length * BAND_H);
    const top = 74, bot = 26;
    const bandH = (H - top - bot) / Math.max(months.length, 1);
    const laneX = (d: string) =>
      110 + ((laneAt.get(d) || 0) + 0.5) * ((W - 130) / Math.max(lanes.length, 1));
    const yOf = (n: { id: string; ts?: string }) => {
      const m = n.ts!.slice(0, 7);
      const b = top + (monthAt.get(m) || 0) * bandH;
      return b + (bandH * (rank.get(n.id)! + 0.5)) / Math.max(per.get(m) || 1, 1);
    };

    // 월 거터 — 홀짝 밴드 + 좌측 눈금. 밴드는 muted 면이라 색이 아니라 면이 구분한다.
    const bands = months.map((m, i) =>
      (i % 2 === 1
        ? `<rect class="pl-band" x="34" y="${num(top + i * bandH)}" width="${num(W - 46)}" height="${num(bandH)}"/>`
        : "")
      + `<text class="pl-cap pl-num" x="38" y="${num(top + i * bandH + 13)}">${esc(m)}</text>`)
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
        return `<g class="pl-node" data-id="${esc(n.id)}"><title>${esc(n.label)} ${esc(n.ts || "")}</title>`
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
  const stopFocus = wireFocus(el, { focus, clear, open: o.onOpen });
  return { dispose() { stopFocus(); stopSize(); } };
}

// ── 3. Totals — 어디에 얼마나 쌓였는가 ────────────────────────────────────
/** 주제당 가로 누적 막대. 구간은 색조가 아니라 잉크 농도 3단으로 갈리고,
 *  구간 사이는 배경색 실선 한 겹으로 벌린다. 합은 보존한다: 그리는 폭의 합이
 *  곧 표기하는 행 합계이고, 행 합계의 합이 곧 맨 아래 총계다. */
export function mountTotals(el: HTMLElement, rows: LedgerRow[],
  o: { group: "topic" | "origin"; unlinked: "show" | "hide"; current?: string;
       onOpen: (domain: string) => void }): PlanarHandle {
  const KEYS = o.group === "origin"
    ? (["episodic", "record", "unsourced"] as const)
    : (["connected", "gateway", "orphan"] as const);
  // unlinked=hide — 고아 구간을 걷어내고, 그래서 합이 0 이 된 행은 그리지 않는다.
  const segs = KEYS.filter((k) => !(o.unlinked === "hide" && k === "orphan"));
  const data = rows.map((r) => {
    const parts = segs.map((k) => Math.max(0, Number(r[k]) || 0));
    return { domain: r.domain, parts, total: parts.reduce((a, b) => a + b, 0) };
  }).filter((r) => r.total > 0);
  const grand = data.reduce((a, r) => a + r.total, 0);
  const max = Math.max(1, ...data.map((r) => r.total));
  const label = COPY.ui.atlasx.totalsChart(data.length, grand);
  // 구간 이름 — 원장 키(episodic·gateway…)는 코드 식별자라 화면에는 덱 문구가 나간다.
  // 잉크 농도 3단이 무엇을 뜻하는지 풀어 주는 유일한 자리라 이 툴팁이 곧 범례다.
  const segTip = (k: (typeof segs)[number], v: number) =>
    COPY.ui.atlasx.segTip(COPY.ui.atlasx.seg[k], v);
  const ROW = 34;

  function draw(w: number, h: number): string {
    const W = Math.max(w, 520);
    const H = Math.max(h, data.length * ROW + 76);
    const labW = 132, numW = 56;
    const barW = W - labW - numW - 24;

    const bars = data.map((r, i) => {
      const y = 34 + i * ROW;
      const bh = 16;
      let x = labW;
      const cells = r.parts.map((v, si) => {
        const cw = (v / max) * barW;
        const rect = v <= 0 ? "" :
          `<rect class="pl-seg pl-s${si}" x="${num(x)}" y="${num(y)}" width="${num(cw)}" height="${bh}">`
          + `<title>${esc(segTip(segs[si], v))}</title></rect>`
          + (cw > 24
            ? `<text class="pl-cap pl-num" x="${num(x + cw / 2)}" y="${num(y + 11.5)}" text-anchor="middle">${v}</text>`
            : "");
        x += cw;
        return rect;
      }).join("");
      // 이미 그 주제만 보고 있는 행(o.current)은 눌러도 갈 곳이 없다 — data-id 를
      // 빼 hover/click 위임에서 아예 제외하고 손가락 커서도 내린다. 구간 <title>
      // (실제 값인 툴팁)은 rect 에 달려 있어 그대로 살아 있다.
      const dead = r.domain === o.current;
      return `<g class="pl-row${dead ? " pl-static" : ""}"${dead ? "" : ` data-id="${esc(r.domain)}"`}>`
        + `<rect class="pl-track" x="${labW}" y="${num(y)}" width="${num(barW)}" height="${bh}" rx="3"/>`
        + `<text class="pl-lab" x="${labW - 10}" y="${num(y + 12)}" text-anchor="end">${esc(cut(r.domain, 18))}</text>`
        + `${cells}`
        + `<text class="pl-sub pl-num" x="${num(labW + barW + 14)}" y="${num(y + 12)}">${r.total}</text></g>`;
    }).join("");

    // 총계 — 열의 합이 보존됨을 눈으로 확인시킨다(구간 합 = 행 합 = 총계).
    const ry = 34 + data.length * ROW + 6;
    const foot = `<path class="pl-ax" d="M${num(labW + barW + 6)} ${num(ry)} L${num(labW + barW + numW)} ${num(ry)}"/>`
      + `<text class="pl-lab pl-num" x="${num(labW + barW + 14)}" y="${num(ry + 15)}">${grand}</text>`;

    return `<svg class="pl-svg" aria-label="${esc(label)}"`
      + ` viewBox="0 0 ${num(W)} ${num(H)}" width="${num(W)}" height="${num(H)}">`
      + `${bars}${foot}</svg>`;
  }

  let rowEls: SVGElement[] = [];
  const collect = () => { rowEls = [...el.querySelectorAll<SVGElement>(".pl-row")]; };
  function focus(id: string) {
    for (const g of rowEls) {
      const on = g.dataset.id === id;
      g.classList.toggle("pl-fade", !on);
      g.classList.toggle("pl-hl", on);
    }
  }
  function clear() { for (const g of rowEls) g.classList.remove("pl-fade", "pl-hl"); }

  const stopSize = trackSize(el, (w, h) => { el.innerHTML = wrap(draw(w, h)); collect(); });
  const stopFocus = wireFocus(el, { focus, clear, open: o.onOpen });
  return { dispose() { stopFocus(); stopSize(); } };
}
