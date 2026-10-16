/** 방사형 계층 번들(Radial hierarchical edge bundling) — Sankey(합계) 대체.
 *
 *  왜 바꾸는가: 생키는 **롤업**이라 노드가 안 나온다(원장 행의 합계였다). 그래서
 *  "무엇과 무엇이 이어져 있는가"를 물으면 답을 못 했다. 이 뷰는 반대다 — 모든 항목이
 *  둘레에 이름으로 서 있고, 이어진 것만 선으로 지나간다.
 *
 *  계층은 세 단이다(발주자 결정 2026-08-14):
 *    뿌리 → 도메인 → 종류(concept·record·episode·decision) → 노드
 *  ⚠ 도메인 계층은 **이 그래프에서 실제로 2단뿐이다**(`vinc` → 7개, 하위 0개).
 *  그래서 도메인만으로는 번들이 펴질 자리가 없어 종류를 한 단 끼웠다. 도메인이
 *  깊은 데이터가 오면 그 사슬이 그대로 더 깊은 계층이 된다(아래 parseChain).
 *
 *  ⚠ **모든 잎은 항상 둘레에 있다.** 컨트롤 바가 조절하는 것은 라벨이 아니라
 *  **선이 어느 단에서 묶이는가**다(발주자 결정): 1=도메인끼리 · 2=도메인/종류 ·
 *  3=노드끼리(전개). 그래서 저밀도에서도 목록은 그대로고 선만 굵게 뭉친다.
 *
 *  ⚠ 색: 이 파일은 **색을 직접 쓰지 않는다.** 디자인 게이트가 app/src 의 하드코딩
 *  색을 막고(design-hub/scripts/gate_surface.py), 허브 토큰에는 카테고리 팔레트가
 *  아직 없다. 그룹 구분은 지금 `data-g` 속성만 내보내고 실제 칠은 app.css 가 한다 —
 *  팔레트가 생기면 **그 한 곳만** 바뀐다.
 */
import { COPY } from "../copy";
import type { BundleDepth } from "../view-prefs";
import { markSvg, PAD_BOT, PAD_TOP, PAD_X, trackSize, type PlanarHandle } from "./view-planar";
import { esc } from "../components";

export type BundleNode = { id: string; label: string; domain: string; kind?: string;
                           /** 라벨을 솎을 때의 우선순위. 없으면 0 취급. */
                           degree?: number };
export type BundleEdge = { source: string; target: string; type: string };

const SEP = "/";
/** 백엔드 `engines/domain_hierarchy.parse_chain` 과 **같은 규칙**이다.
 *  'a/b/c' → ['a','a/b','a/b/c']. 트리를 주는 엔드포인트가 없어 여기서 판다. */
function parseChain(domain: string): string[] {
  const parts = String(domain || "").split(SEP).filter(Boolean);
  const out: string[] = [];
  for (let i = 0; i < parts.length; i++) out.push(parts.slice(0, i + 1).join(SEP));
  return out;
}

type HNode = {
  key: string; name: string; depth: number;
  parent: HNode | null; children: HNode[];
  leaf: BundleNode | null;
  group: HNode | null;     // 색 그룹(첫 분기 단)의 조상. 잎·내부 모두 채운다
  gi: number;              // 그룹 인덱스(색). 미배정 -1
  angle: number; radius: number; x: number; y: number;
  count: number;           // 이 가지 아래 잎 수
};

const mk = (key: string, name: string, depth: number, parent: HNode | null): HNode => ({
  key, name, depth, parent, children: [], leaf: null, group: null, gi: -1,
  angle: 0, radius: 0, x: 0, y: 0, count: 0,
});

/** 노드 목록에서 계층을 세운다. 경로 = [도메인 사슬..., 종류, 노드].
 *  도메인이 빈 노드는 버리지 않고 `(unfiled)` 가지로 모은다 — 잎이 사라지면
 *  "모든 문서가 나열된다"는 이 뷰의 약속이 깨진다. */
function buildHierarchy(nodes: BundleNode[]): HNode {
  const root = mk("", "", 0, null);
  const byKey = new Map<string, HNode>([["", root]]);
  const child = (parent: HNode, key: string, name: string): HNode => {
    let n = byKey.get(key);
    if (!n) { n = mk(key, name, parent.depth + 1, parent); parent.children.push(n); byKey.set(key, n); }
    return n;
  };
  for (const nd of nodes) {
    const chain = parseChain(nd.domain);
    let cur = root;
    if (chain.length === 0) cur = child(root, "\u0000unfiled", COPY.ui.viewx.bundleUnfiled);
    else for (const c of chain) cur = child(cur, c, c.split(SEP).pop() || c);
    const kind = nd.kind || "node";
    cur = child(cur, `${cur.key}\u0000${kind}`, kind);
    // 잎 키는 노드 id 그대로다 — 엣지가 id 로 오므로 이 맵이 곧 조회표가 된다.
    const lf = child(cur, nd.id, nd.label || nd.id);
    lf.leaf = nd;
  }
  assignGroups(root);
  countLeaves(root);
  return root;
}

/** 색 그룹 = **처음으로 갈라지는 단**. 한 뿌리(`vinc`) 아래 전부가 매달린 이 데이터
 *  에서는 그 단이 `vinc/design`·`vinc/engineering`… 이 된다. 뿌리에서 바로 색을
 *  집으면 전부 한 색이 돼 구분이 사라진다(실측 구조). */
function assignGroups(root: HNode): void {
  let n = root;
  while (n.children.length === 1) n = n.children[0];
  const groups = n.children.length ? n.children : [n];
  groups.forEach((g, i) => {
    g.gi = i;
    const stack = [g];
    while (stack.length) {
      const c = stack.pop()!;
      c.group = g; c.gi = i;
      for (const k of c.children) stack.push(k);
    }
  });
}

function countLeaves(n: HNode): number {
  n.count = n.leaf ? 1 : n.children.reduce((s, c) => s + countLeaves(c), 0);
  return n.count;
}

function leavesOf(root: HNode): HNode[] {
  const out: HNode[] = [];
  const walk = (n: HNode) => { if (n.leaf) out.push(n); else n.children.forEach(walk); };
  walk(root);
  return out;
}

/** 방사 배치. 잎은 전부 반지름 R 의 둘레에 같은 간격으로 서고, 내부 마디는 자식의
 *  평균 각도 · 깊이에 비례한 반지름에 앉는다(그 두 값이 곧 번들이 지나갈 길이다).
 *  그룹 사이에는 각도 틈을 준다. 예전엔 색이 없어 **틈이 유일한 경계 신호**였고,
 *  2026-08-16 부터 주제 틴트가 같이 말한다. 틈을 남겨 두는 이유는 틴트가 열린
 *  층이라서다 — 사용자가 끄면 경계를 말하는 것이 다시 틈뿐이 된다. */
function layoutRadial(root: HNode, o: { radius: number; gapDeg?: number; rot?: number }): void {
  const leaves = leavesOf(root);
  if (!leaves.length) return;
  const groups = new Map<number, HNode[]>();
  for (const lf of leaves) {
    const g = lf.gi;
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g)!.push(lf);
  }
  const gaps = groups.size > 1 ? groups.size : 0;
  const gap = ((o.gapDeg ?? 3) * Math.PI) / 180;
  const usable = Math.PI * 2 - gap * gaps;
  const per = usable / leaves.length;
  // `rot` 은 링 전체를 돌린 각이다(노드를 클릭해 정면으로 보내는 회전). 배치 단계에서
  // 더하는 이유: 라벨 뒤집힘 판정이 `cos(angle)` 이라, 나중에 CSS 로만 돌리면 왼쪽으로
  // 넘어간 글자가 거꾸로 선 채 남는다. 각도에 실어야 판정이 함께 따라온다.
  let a = -Math.PI / 2 + (gaps ? gap / 2 : 0) + (o.rot ?? 0);   // 12시부터 시계방향
  for (const [, list] of [...groups.entries()].sort((p, q) => p[0] - q[0])) {
    for (const lf of list) { lf.angle = a + per / 2; a += per; }
    a += gap;
  }
  let maxDepth = 0;
  for (const lf of leaves) maxDepth = Math.max(maxDepth, lf.depth);
  const place = (n: HNode): number => {
    if (n.leaf) { n.radius = o.radius; }
    else {
      const kids = n.children.map(place);
      n.angle = kids.reduce((s, v) => s + v, 0) / (kids.length || 1);
      n.radius = maxDepth ? (o.radius * n.depth) / maxDepth : 0;
    }
    n.x = Math.cos(n.angle) * n.radius;
    n.y = Math.sin(n.angle) * n.radius;
    return n.angle;
  };
  place(root);
}

/** 두 마디를 잇는 **계층 경로** — a → 공통조상 → b. 번들이 되는 이유가 이것이다:
 *  같은 조상을 지나는 선끼리 저절로 겹쳐 다발이 된다. */
function hierPath(a: HNode, b: HNode): HNode[] {
  const up: HNode[] = [];
  for (let n: HNode | null = a; n; n = n.parent) up.push(n);
  const seen = new Set(up);
  const down: HNode[] = [];
  let l: HNode | null = b;
  while (l && !seen.has(l)) { down.push(l); l = l.parent; }
  if (!l) return [];                      // 다른 뿌리 — 그릴 수 없다
  return [...up.slice(0, up.indexOf(l) + 1), ...down.reverse()];
}

export type P = { x: number; y: number };

/** 번들 강도 β — 제어점을 직선 쪽으로 당긴다. 1 이면 계층을 그대로 타고(다발이
 *  강하다), 0 이면 직선이 된다. 원 논문(Holten)의 기본이 0.85 근처다. */
function applyBeta(pts: P[], beta: number): P[] {
  const n = pts.length - 1;
  if (n <= 0) return pts;
  const p0 = pts[0], pn = pts[n];
  return pts.map((p, i) => {
    const t = i / n;
    return {
      x: beta * p.x + (1 - beta) * (p0.x + t * (pn.x - p0.x)),
      y: beta * p.y + (1 - beta) * (p0.y + t * (pn.y - p0.y)),
    };
  });
}

const f = (v: number) => (Math.round(v * 100) / 100).toString();

/** 균일 3차 B-스플라인 경로(d3.curveBasis 와 같은 모양). 양 끝을 3중으로 겹쳐
 *  끝점을 실제로 지나가게 한다 — 안 겹치면 선이 노드에 닿지 않고 뜬다. */
function splinePath(pts: P[]): string {
  if (pts.length < 2) return "";
  if (pts.length === 2)
    return `M${f(pts[0].x)},${f(pts[0].y)}L${f(pts[1].x)},${f(pts[1].y)}`;
  const q = [pts[0], pts[0], ...pts, pts[pts.length - 1], pts[pts.length - 1]];
  const mix = (a: P, b: P, c: P, wa: number, wb: number, wc: number, d: number): P =>
    ({ x: (a.x * wa + b.x * wb + c.x * wc) / d, y: (a.y * wa + b.y * wb + c.y * wc) / d });
  const b0 = mix(q[0], q[1], q[2], 1, 4, 1, 6);
  const out = [`M${f(b0.x)},${f(b0.y)}`];
  for (let i = 0; i + 3 < q.length; i++) {
    const [a, b, c, d] = [q[i], q[i + 1], q[i + 2], q[i + 3]];
    const c1 = { x: (b.x * 2 + c.x) / 3, y: (b.y * 2 + c.y) / 3 };
    const c2 = { x: (b.x + c.x * 2) / 3, y: (b.y + c.y * 2) / 3 };
    const e = mix(b, c, d, 1, 4, 1, 6);
    void a;
    out.push(`C${f(c1.x)},${f(c1.y)} ${f(c2.x)},${f(c2.y)} ${f(e.x)},${f(e.y)}`);
  }
  return out.join("");
}

/** 잎에서 집약 단의 마디로 올라간다. 3=잎 · 2=종류 · 1=색 그룹(도메인). */
function endpointAt(leaf: HNode, depth: BundleDepth): HNode {
  if (depth === 3) return leaf;
  if (depth === 2) return leaf.parent ?? leaf;
  return leaf.group ?? leaf;
}

type BundledEdge = { a: HNode; b: HNode; type: string; n: number };

/** 선을 집약한다. 같은 (양끝, 타입)이면 하나로 합치고 `n` 에 몇 개였는지 남긴다.
 *  ⚠ 집약하면 **같은 마디로 되돌아오는 선**(도메인 내부 연결)이 생기는데, 계층
 *  경로가 한 점이라 그릴 수가 없다. 버리되 몇 개를 버렸는지 함께 돌려준다 —
 *  조용히 사라지면 화면이 "연결이 없다"고 거짓말한다. */
function bundleEdges(root: HNode, edges: BundleEdge[], depth: BundleDepth):
    { rows: BundledEdge[]; selfDropped: number } {
  const leaf = new Map<string, HNode>();
  for (const lf of leavesOf(root)) leaf.set(lf.key, lf);
  const agg = new Map<string, BundledEdge>();
  let selfDropped = 0;
  for (const e of edges) {
    const la = leaf.get(e.source), lb = leaf.get(e.target);
    if (!la || !lb) continue;                       // 스코프 밖 끝점
    const a = endpointAt(la, depth), b = endpointAt(lb, depth);
    if (a === b) { selfDropped++; continue; }
    const k = a.key < b.key ? `${a.key}\u0000${b.key}\u0000${e.type}`
                            : `${b.key}\u0000${a.key}\u0000${e.type}`;
    const hit = agg.get(k);
    if (hit) hit.n++;
    else agg.set(k, { a, b, type: e.type, n: 1 });
  }
  return { rows: [...agg.values()], selfDropped };
}

/** 관계 패널용 집약 통계 — 이 단에서 **타입별로** 몇 다발을 그리고 몇 자기연결을
 *  버리는지. 렌더와 같은 함수(bundleEdges)를 타므로 패널의 수와 그림이 어긋날 수
 *  없다. 타입을 가르는 것이 그림이 아니라 수인 관점이라(화살촉·타입별 굵기 없음)
 *  이 수가 곧 그 행의 견본이다. */
export function bundleStatsByType(nodes: BundleNode[], edges: BundleEdge[],
                                  depth: BundleDepth):
    { byType: Map<string, { drawn: number; dropped: number }>; leaves: number } {
  const root = buildHierarchy(nodes);
  const byType = new Map<string, { drawn: number; dropped: number }>();
  for (const t of [...new Set(edges.map((e) => e.type))]) {
    const { rows, selfDropped } = bundleEdges(root, edges.filter((e) => e.type === t), depth);
    byType.set(t, { drawn: rows.length, dropped: selfDropped });
  }
  return { byType, leaves: leavesOf(root).length };
}

// ── 렌더 ────────────────────────────────────────────────────────────────────

const PAD = 8;
const LABEL_GAP = 6;      // 링과 글자 사이
//: 글자가 쓸 바깥 여백(px). **잘라 낸 라벨의 최대 폭보다 커야 한다** — 좁게 잡으면
//: 글자가 SVG 밖으로 나가 컨테이너가 넘치고 링이 잘린다(실측 2026-08-16: 여백 132 에
//: 라벨 중앙값 154 · 최대 227 이라 원이 위아래로 잘려 나갔다).
const LABEL_ROOM = 150;
//: 라벨 최대 글자수. 넘으면 말줄임한다 — 전체 제목은 hover 툴팁이 답한다.
//: 8.5px 폰트에서 대략 이 길이가 LABEL_ROOM 안에 든다.
const LABEL_MAX_CH = 26;
//: 라벨 하나가 링 둘레에서 차지해야 하는 최소 간격(px). 폰트 8.5px × 여유.
const LABEL_PITCH = 10.5;

/** 그림이 들어갈 상자다. `el` 은 바깥 컨테이너라 그 폭을 그대로 쓰면 `.pl-wrap` 의
 *  패딩만큼 SVG 가 넘쳐 잘린다(실측 2026-08-16: 폭 819 를 793 짜리 상자에 그려
 *  26px 이 스크롤 밖으로 나갔다). 패딩 값은 view-planar 와 app.css 가 같이 쓴다. */
function box(el: HTMLElement): [number, number] {
  return [Math.max(el.clientWidth - PAD_X * 2 - PAD * 2, 320),
          Math.max(el.clientHeight - PAD_TOP - PAD_BOT - PAD * 2, 320)];
}

type BundleOpts = {
  /** 진입 안무(선 그리기)를 돌릴 것인가. 옵션만 바꾼 재마운트는 false. 기본 true. */
  entrance?: boolean;
  /** 시간 커서가 남긴 노드 id. `null`·미지정이면 전량.
   *
   *  ⚠ **링 배치는 이 집합을 안 본다.** 계층(`buildHierarchy`)은 마운트 때 전량으로
   *  한 번 잡히고 각도는 거기서 나온다 — 커서를 밀 때마다 다시 잡으면 남은 잎이
   *  통째로 미끄러져, "무엇이 언제 생겼나"를 읽으려던 눈이 따라갈 자리를 잃는다.
   *  그래서 자리는 그대로 두고 **그리지 않을 뿐**이다(3D·2D 와 같은 태도). */
  visible?: Set<string> | null;
  depth: BundleDepth;
  /** 끌 관계 타입(대문자). 저장은 **끈 목록**이라 새 타입이 생겨도 조용히 숨지 않는다. */
  off: string[];
  labels: boolean;
  /** 주제 틴트(주제 → 색). `null` 이면 한 잉크다. **다른 관점과 같은 맵**을 받는다 —
   *  같은 주제가 관점마다 다른 색이면 색이 뜻을 잃는다. 여태 이 관점만 CSS
   *  `hue-rotate` 로 **구조 그룹**을 칠하고 있어서, 주제 틴트를 켜도 여기만 안 변하고
   *  꺼도 여기만 색이 남았다. 그 갈림을 없앤다. */
  tint: Map<string, string> | null;
  onOpen: (id: string) => void;
  /** 버려진 자기연결 수를 알린다(캡션에 쓴다). 조용한 누락 금지. */
  onStats?: (s: { drawn: number; selfDropped: number; leaves: number }) => void;
};

const reduced = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
/** 회전 시간 — 허브 토큰 `--dur-in` 을 읽는다(motion.ts 와 같은 값, 한 벌). 없으면 400. */
function spinMs(): number {
  const v = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--dur-in"));
  return Number.isNaN(v) ? 400 : Math.round(v * 1000);
}
/** 이 수를 넘으면 선을 "그려 나가지" 않고 불투명도만 올린다(아래 paint 주석). */
const LINE_DRAW_MAX = 300;

export function mountBundle(el: HTMLElement, nodes: BundleNode[], edges: BundleEdge[],
                            o: BundleOpts): PlanarHandle {
  let opts = o;
  const root = buildHierarchy(nodes);
  const leaves = leavesOf(root);
  /** 링 전체의 회전각(rad). 노드를 클릭하면 그 노드가 3시 방향(글자가 가로로 읽히는
   *  자리)에 오도록 이 값이 바뀐다. 12시가 아니라 3시인 이유: 12시에서는 라벨이
   *  90° 로 서서 세로가 된다 — "정방향"이 아니다. */
  let rot = 0;

  /** 보기 창(viewBox). `null` 이면 그림 전체가 딱 들어오는 기본 창이다.
   *  확대·이동은 **이 값만 바꾸고 다시 그리지 않는다** — 경로 수백 개를 다시 만들
   *  이유가 없다. 값이 살아 있으므로 회전·옵션 변경으로 다시 그려도 창은 유지된다. */
  let vb: { x: number; y: number; s: number } | null = null;
  let lastSide = 0;

  function draw(w: number, h: number): string {
    const R = Math.max(60, Math.min(w, h) / 2 - LABEL_ROOM);
    layoutRadial(root, { radius: R, rot });
    const vis = opts.visible;
    // 선은 끝점을 따라간다 — 한쪽이 아직 안 생겼으면 그릴 곳이 없다.
    const live = edges.filter((e) => !opts.off.includes(e.type)
      && (!vis || (vis.has(e.source) && vis.has(e.target))));
    const { rows, selfDropped } = bundleEdges(root, live, opts.depth);
    // 굵은 다발이 먼저 깔리고 가는 선이 위에 오게 — 반대면 굵은 선이 전부 덮는다.
    rows.sort((p, q) => q.n - p.n);
    // 끝점은 **언제나 링 위**다. 집약 단(1·2)에서 양 끝은 내부 마디라 반지름이
    // `R × 깊이/최대깊이` 밖에 안 된다 — 그대로 그리면 선이 안쪽에만 갇혀 둘레의
    // 라벨과 끊겨 보인다(실측: 단1 에서 가운데에만 선이 떴다). 그래서 그 마디의
    // **각도로 링 위 한 점**을 앞뒤에 덧댄다. 경로 가운데는 그대로 계층을 타므로
    // 다발은 유지되고, 선이 자기 도메인 자리에서 출발해 자기 도메인 자리로 들어간다.
    // 잎(단3)에서는 그 점이 잎 자신과 같은 좌표라 아무것도 바뀌지 않는다.
    /** 마디의 색. 잎이면 그 노드의 주제, 집약 마디면 마디 키가 곧 주제 경로다. */
    const tintOf = (n: HNode): string | undefined =>
      opts.tint?.get(n.leaf?.domain ?? n.key);

    const onRing = (n: HNode): P => ({ x: Math.cos(n.angle) * R, y: Math.sin(n.angle) * R });
    const paths = rows.map((r, i) => {
      const chain = hierPath(r.a, r.b);
      if (chain.length < 2) return "";
      const pts = [onRing(r.a), ...chain.map((n) => ({ x: n.x, y: n.y })), onRing(r.b)];
      const d = splinePath(applyBeta(pts, 0.85));
      if (!d) return "";
      // `pathLength="1"` 로 길이를 정규화한다 — 그래야 dasharray/dashoffset 한 벌로
      // 길이가 제각각인 선들을 **같은 비율로** 그려 나갈 수 있다(JS 로 실측할 필요 없음).
      // `--i` 는 그리기 시작 순서(스태거). 상한을 두는 이유는 400개가 넘으면 마지막
      // 선이 나타나기까지 반 초 넘게 걸려 "느리다"로 읽히기 때문이다.
      const ink = tintOf(r.a);
      return `<path class="pl-bd-link" data-t="${esc(r.type)}" data-g="${r.a.gi}"`
        + ` data-n="${r.n}" pathLength="1"`
        + ` style="--n:${r.n};--i:${Math.min(i, 120)}${ink ? `;stroke:${ink}` : ""}"`
        + ` d="${d}"></path>`;
    }).join("");

    // 라벨은 **둘레에 들어갈 만큼만** 그린다(발주자 결정 2026-08-16). 여태 잎마다
    // 무조건 그려서, 204개면 2,200px 어치를 둘레 1,275px 에 밀어 넣는 꼴이었다 —
    // 겹치는 것이 아니라 겹칠 수밖에 없었다. 지도가 허브 라벨만 상시로 띄우는 것과
    // 같은 규칙이다: 연결이 많은 것부터 남기고 나머지는 hover 가 답한다.
    // 무엇을 남길지는 연결수 우선, 같으면 이름순 — 같은 데이터면 같은 그림이 되게.
    const fits = Math.max(1, Math.floor((2 * Math.PI * R) / LABEL_PITCH));
    const shown = new Set(
      leaves.length <= fits ? leaves.map((l) => l.key)
        : [...leaves]
            .sort((a, b) => (b.leaf?.degree || 0) - (a.leaf?.degree || 0)
                            || (a.name < b.name ? -1 : a.name > b.name ? 1 : 0))
            .slice(0, fits).map((l) => l.key));
    const cut = (t: string) =>
      t.length > LABEL_MAX_CH ? t.slice(0, LABEL_MAX_CH - 1) + "…" : t;

    const marks = leaves.map((lf) => {
      // 커서 밖 잎은 자리를 지키되 그려지지 않는다(위 `visible` 주석).
      if (vis && lf.leaf && !vis.has(lf.leaf.id)) return "";
      const deg = (lf.angle * 180) / Math.PI;
      const flip = Math.cos(lf.angle) < 0;
      const lx = R + LABEL_GAP;
      const label = opts.labels && shown.has(lf.key)
        ? `<text class="pl-bd-label" x="${f(flip ? -lx : lx)}" y="0"`
          + ` text-anchor="${flip ? "end" : "start"}"`
          + ` transform="${flip ? "rotate(180)" : ""}">${esc(cut(lf.name))}</text>`
        : "";
      const ink = tintOf(lf);
      return `<g class="pl-bd-leaf" data-id="${esc(lf.key)}" data-g="${lf.gi}"`
        + (ink ? ` style="--tint:${ink}"` : "")
        + ` transform="rotate(${f(deg)})">`
        // 잎의 모양도 종이 정한다 — 지도·타임라인과 같은 어휘(markSvg). 여기만
        // 원으로 두면 같은 노드가 화면마다 다른 모양이 되어, 형태에 뜻을 실은 것을
        // 스스로 무너뜨린다. 반지름 2 는 그대로(번들의 잎은 크기 인코딩이 없다).
        // 과녁을 넓히는 투명 원. **보이는 점은 안 건드린다** — 잎의 반지름 2 는
        // "번들의 잎은 크기 인코딩이 없다"는 약속이고, 그것을 키우면 화면이
        // 없는 뜻을 말하기 시작한다. 손이 못 짚는 것은 다른 문제라 다른 것으로
        // 푼다: 그려지지 않지만 눌리는 원을 같은 자리에 덧댄다(발주자 지시
        // 2026-08-27 — Weave 에서 노드를 집기가 어렵다).
          + `<circle class="pl-bd-hit" cx="${f(R)}" cy="0" r="7"></circle>`
        + markSvg(lf.leaf?.kind, false, R, 0, 2, "pl-bd-dot")
        // ⚠ 예전엔 뒤집힌 라벨을 `translate(lx*2)` 로 한 번 더 밀었다. 그럴 이유가
        // 없다 — `rotate(180)` 이 이미 x=-lx 를 바깥쪽 +lx 로 보낸다. 그래서 왼쪽
        // 절반의 라벨만 반지름이 **3배**가 되어 링에서 떨어져 나갔다(실측 2026-08-16:
        // 점은 148, 라벨은 162~497). 화면이 고장 난 것처럼 보이던 것이 이것이다.
        + label
        + `</g>`;
    }).join("");

    opts.onStats?.({ drawn: rows.length, selfDropped, leaves: leaves.length });
    const side = Math.min(w, h);
    lastSide = side;
    const view = vb ?? { x: -side / 2, y: -side / 2, s: side };
    // 회전은 **한 겹**(`pl-bd-spin`)에만 건다 — 선과 잎이 따로 돌면 선이 라벨을 놓친다.
    return `<svg class="pl-svg pl-bd" viewBox="${f(view.x)} ${f(view.y)} ${f(view.s)} ${f(view.s)}"`
      + ` width="${f(w)}" height="${f(h)}" aria-label="${esc(COPY.ui.viewx.bundle)}">`
      + `<g class="pl-bd-spin">`
      + `<g class="pl-bd-links">${paths}</g>`
      + `<g class="pl-bd-leaves">${marks}</g>`
      + `</g></svg>`;
  }

  /** `anim` 이 참일 때만 선이 그려지는 모션이 돈다. **리사이즈에는 끈다** — 창을
   *  줄일 때마다 400개 선이 다시 그려지면 모션이 신호가 아니라 잡음이 된다.
   *  모션은 "무언가 바뀌었다"를 말해야 하고, 리사이즈는 바뀐 것이 없다. */
  let sel = "";                                   // 초점 잎. 다시 그려도 살아남아야 한다
  const paint = (w: number, h: number, anim = false) => {
    el.innerHTML = `<div class="pl-wrap">${draw(w, h)}</div>`;
    const links = el.querySelector(".pl-bd-links");
    if (anim && !reduced() && links) {
      links.classList.add("pl-bd-anim");
      // ponytail: dashoffset 은 GPU 로 합성되지 않고 경로를 매 프레임 다시 그린다.
      // 수백 개가 동시에 돌면 끊긴다. 그 위로는 불투명도만 올려(리페인트가 훨씬 싸다)
      // "붙는다"는 인상만 남긴다. 임계는 실측으로 조정할 값이지 진리가 아니다.
      if (links.childElementCount > LINE_DRAW_MAX) links.classList.add("pl-bd-lite");
    }
    if (sel) el.querySelector(`.pl-bd-leaf[data-id="${CSS.escape(sel)}"]`)?.classList.add("on");
  };
  // 첫 그리기 한 번은 모션을 태운다(관점에 들어선 순간) — 그 뒤 리사이즈는 조용하다.
  let first = o.entrance !== false;
  let stop = trackSize(el, box, (w, h) => { paint(w, h, first); first = false; });

  const TAU = Math.PI * 2;
  /** 클릭한 잎을 3시 방향으로 돌린다. 각도에 회전을 실어 다시 그린 뒤(라벨 뒤집힘까지
   *  올바른 최종 상태), **거꾸로 틀어 놓고 0 으로 되돌리는** FLIP 으로 움직임만 CSS 에
   *  맡긴다. 매 프레임 400개 경로를 다시 만들지 않으므로 회전이 공짜에 가깝다. */
  function spinTo(leafKey: string) {
    const lf = leaves.find((l) => l.key === leafKey);
    if (!lf) return;
    let delta = -lf.angle;                       // 3시 = 각 0
    delta = ((delta % TAU) + TAU + Math.PI) % TAU - Math.PI;   // 가까운 쪽으로
    if (Math.abs(delta) < 1e-3) return;
    rot += delta;
    paint(...box(el));
    const spin = el.querySelector<SVGGElement>(".pl-bd-spin");
    if (!spin || reduced()) return;
    const deg = (-delta * 180) / Math.PI;
    spin.style.transition = "none";
    spin.style.transform = `rotate(${deg.toFixed(2)}deg)`;
    void spin.getBoundingClientRect();            // 시작 상태를 확정(안 하면 한 프레임에 건너뛴다)
    spin.style.transition = `transform ${spinMs()}ms var(--ease-settle, cubic-bezier(.16, 1, .3, 1))`;
    spin.style.transform = "rotate(0deg)";
  }

  // ── 확대 · 이동 ──────────────────────────────────────────────────────
  // 링은 화면에 꽉 차게 그려지고 라벨은 그 바깥에 선다. 항목이 많아지면 라벨이
  // 창 밖으로 나가는데, 여태 그것을 되찾을 방법이 없었다(이 관점에만 확대가 없었다).
  const ZOOM_IN = 8, ZOOM_OUT = 3;   // 기본 창 대비 최대 배율
  const svgEl = () => el.querySelector<SVGSVGElement>("svg.pl-bd");

  /** 창을 화면에 반영한다. 다시 그리지 않고 속성 하나만 쓴다. */
  function applyView(): void {
    const svg = svgEl();
    if (!svg || !vb) return;
    svg.setAttribute("viewBox", `${f(vb.x)} ${f(vb.y)} ${f(vb.s)} ${f(vb.s)}`);
  }

  function curView(): { x: number; y: number; s: number } {
    return vb ?? { x: -lastSide / 2, y: -lastSide / 2, s: lastSide };
  }

  /** viewBox 는 정사각이고 SVG 요소는 가로로 넓다. 기본 preserveAspectRatio(meet)는
   *  짧은 변에 맞춰 **한 배율**로 그리고 긴 변 쪽을 가운데 정렬하므로, 화면 1px 이 몇
   *  단위인지는 두 축 모두 `s / min(w, h)` 이고 긴 축에는 여백 오프셋이 붙는다. 축마다
   *  `r.width`·`r.height` 로 따로 나누면 가로가 종횡비만큼 덜 움직인다(실측 2026-08-17:
   *  200px 끌어 가로 129.5·세로 199.8 단위, 즉 0.65 대 1.00). */
  const frame = (r: DOMRect) => {
    const side = Math.min(r.width, r.height);
    return { side, ox: r.left + (r.width - side) / 2, oy: r.top + (r.height - side) / 2 };
  };

  /** 커서 밑의 점을 **제자리에 붙들고** 배율만 바꾼다. 그러지 않으면 확대할 때마다
   *  보던 곳이 화면 밖으로 밀려나 "어디를 보고 있었는지"를 잃는다. */
  const onWheel = (ev: WheelEvent) => {
    const svg = svgEl();
    if (!svg || !lastSide) return;
    ev.preventDefault();
    const r = svg.getBoundingClientRect();
    if (!r.width || !r.height) return;
    const v = curView();
    const { side, ox, oy } = frame(r);
    const fx = (ev.clientX - ox) / side, fy = (ev.clientY - oy) / side;
    const wx = v.x + fx * v.s, wy = v.y + fy * v.s;
    const s = Math.min(lastSide * ZOOM_OUT, Math.max(lastSide / ZOOM_IN,
      v.s * (ev.deltaY > 0 ? 1.12 : 1 / 1.12)));
    vb = { x: wx - fx * s, y: wy - fy * s, s };
    applyView();
  };
  el.addEventListener("wheel", onWheel, { passive: false });

  // 끌어서 이동. 잎 클릭(=회전)과 겹치므로 **움직인 거리**로 가른다 — 4px 를 넘으면
  // 이동으로 보고 그 뒤의 click 을 삼킨다. 문턱이 없으면 손이 떨릴 때마다 링이 돈다.
  let drag: { x: number; y: number; moved: number } | null = null;
  let swallow = false;                 // 이동으로 끝난 제스처의 click 을 삼킨다
  const onDown = (ev: PointerEvent) => {
    if (ev.button !== 0 || !svgEl()) return;
    drag = { x: ev.clientX, y: ev.clientY, moved: 0 };
  };
  const onMove = (ev: PointerEvent) => {
    const svg = svgEl();
    if (!drag || !svg) return;
    const r = svg.getBoundingClientRect();
    if (!r.width) return;
    const dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
    drag.moved += Math.abs(dx) + Math.abs(dy);
    drag.x = ev.clientX; drag.y = ev.clientY;
    if (drag.moved < 4) return;
    const v = curView();
    const k = v.s / frame(r).side;                 // 두 축 같은 배율(위 frame 주석)
    vb = { x: v.x - dx * k, y: v.y - dy * k, s: v.s };
    applyView();
  };
  const onUp = () => { if (drag && drag.moved >= 4) swallow = true; drag = null; };
  el.addEventListener("pointerdown", onDown);
  window.addEventListener("pointermove", onMove);
  window.addEventListener("pointerup", onUp);

  /** 되돌리기. 확대해 들어갔다가 전체를 다시 보는 길이 있어야 한다. */
  const onDbl = (ev: Event) => {
    if ((ev.target as Element).closest?.(".pl-bd-leaf")) return;
    vb = null;
    paint(...box(el));
  };
  el.addEventListener("dblclick", onDbl);

  const onClick = (ev: Event) => {
    if (swallow) { swallow = false; return; }
    const g = (ev.target as Element).closest?.(".pl-bd-leaf") as SVGElement | null;
    const id = g?.dataset.id;
    if (!id) return;
    sel = id;
    spinTo(id);
    opts.onOpen(id);
  };
  el.addEventListener("click", onClick);

  return {
    dispose() {
      stop();
      el.removeEventListener("click", onClick);
      el.removeEventListener("wheel", onWheel);
      el.removeEventListener("pointerdown", onDown);
      el.removeEventListener("dblclick", onDbl);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      el.innerHTML = "";
    },
    /** 바깥(좌측 트리·검색 착지)에서 거는 초점. 클릭과 **같은 회전**을 태운다 —
     *  다른 문으로 들어왔다고 같은 노드가 다른 자리에 서면 화면이 자기를 반박한다. */
    focus(id: string) {
      const hit = el.querySelector<SVGElement>(`.pl-bd-leaf[data-id="${CSS.escape(id)}"]`);
      if (!hit) return false;
      sel = id;
      for (const n of el.querySelectorAll(".pl-bd-leaf.on")) n.classList.remove("on");
      hit.classList.add("on");
      spinTo(id);
      return true;
    },
    /** 테마 전환 — 새 틴트 표로 SVG 만 다시 그린다. 모션은 태우지 않는다(바뀐 것은
     *  색뿐이다). 회전·보기 창·고른 잎은 `paint` 가 그대로 잇는다. */
    retint(tint: Map<string, string> | null) {
      opts = { ...opts, tint };
      paint(...box(el));
    },
    /** 옵션만 바뀐 재그리기 — **다시 fetch 하지 않는다.** 기존 렌즈 패널이 옵션
     *  하나에도 전량 재조회 + 재마운트를 하던 것이 느림의 원인이었다(리서치
     *  vinc.d.view-interaction-perf). 여기서는 같은 데이터로 그리기만 다시 한다.
     *  그리고 이때만 선이 그려지는 모션을 태운다: 무엇이 바뀌었는지 눈이 따라가야 한다. */
    update(next: Partial<BundleOpts>, anim = true) {
      opts = { ...opts, ...next };
      stop();
      stop = trackSize(el, box, (w, h) => paint(w, h));
      // 시간 커서를 밟는 동안에는 안무를 끈다 — 걸음마다 선을 다시 그려 내면
      // 흐름이 아니라 깜빡임으로 보인다(재생은 걸음이 곧 모션이다).
      paint(...box(el), anim);
    },
  } as PlanarHandle & { update(next: Partial<BundleOpts>, anim?: boolean): void };
}
