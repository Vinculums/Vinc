/** 나침반의 전체 보기: 모든 깊이의 주제를 한꺼번에 펴서 노드를 하나도 빼지 않고 놓는 배치와, 화면에 그릴 몫을 고르는 규칙.
 *
 *  DOM 을 모른다(노드 테스트가 그대로 부른다). 그리기와 손짓은 view-compass.ts 가 한다.
 *
 *  한 주제(허브 = 원점)의 모양은 층 보기의 나침반 문법을 따른다:
 *   - 12시: 부모에서 내려오는 줄기가 곧게 닿는다(그 쐐기에는 아무것도 서지 않는다).
 *   -  9시: 뜻의 노드(개념 · 문서 · 다리 아닌 뜻의 것)가 동심 고리에 선다. 고리 하나의 자리 수는 호 길이 ÷ 간격이고,
 *          다음 고리는 이름표 길이만큼 바깥이다. 그래서 이름표는 바깥으로 곧게 뻗고 다른 노드 · 이름표와 겹칠 수 없다.
 *   -  3시: 이 주제에 선 다리가 위아래 두 쐐기에 같은 고리 규칙으로 선다. 그 사이(3시 축)는 하위 주제로 가는 줄기가 지난다.
 *          허브 이름표는 그 축 바로 위에 가로로 쓴다.
 *   -  6시: 줄기가 곧게 내려가 붙은 것(결정 · 기록 · 사건)의 목록 버스에 닿는다. 목록은 열마다 ROWS 줄씩 옆으로 이어진다.
 *  하위 주제는 자기 칸(cell)이 되어 허브 상자의 오른쪽에 격자로 선다. 격자의 열 · 줄 사이는 비어 있는 통로이고, 부모에서
 *  자식으로 가는 줄기는 그 통로로만 꺾는다. 칸끼리는 상자로 떨어져 있으므로 다른 주제의 부채가 겹칠 수 없다. */

export type Pt = [number, number];
export interface WNodeIn { id: string; k: string; label: string; prio: number }
export interface WTopicIn { id: string; label: string; count: number; r: number; mean: WNodeIn[]; bridges: WNodeIn[];
  lists: { k: string; name: string; items: WNodeIn[] }[]; subs: WTopicIn[] }
export interface WItem { id: string; k: string; x: number; y: number; r: number; role: "hub" | "mean" | "bridge" | "list";
  /** 회전 이름표의 방향(라디안). 목록 · 허브 이름표는 가로라 없다 */ ang?: number; label: string; lw: number; topic: string; depth: number; order: number }
export interface WHead { x: number; y: number; text: string; topic: string; w: number }
export interface WLayout { items: WItem[]; byId: Map<string, WItem>; heads: WHead[];
  /** 주제마다: 허브 → 노드 빛살의 끝점들, 목록 줄기(꺾은선 여럿), 하위 주제로 가는 줄기(공유 버스 + 자식마다 마지막 토막) */
  topics: { id: string; hub: WItem; spokes: string[]; stems: Pt[][]; trunk: Pt[][]; drops: { to: string; pts: Pt[] }[]; box: Box; own: Box }[];
  bounds: Box }
export type Box = [number, number, number, number];
type Measure = (text: string, font: "leaf" | "hub" | "head") => number;

/** 화면 글자의 기준: 이보다 작게 보일 이름표는 그리지 않는다(px). 확대하면 돌아온다 */
export const LABEL_MIN_PX = 8;
/** 그림 단위의 글자 크기(app.css 의 .cmp svg.g.whole 규칙과 같아야 한다) */
export const FONT = { leaf: 11, hub: 15, head: 11 } as const;
export const MAXW = 150;   // 이름표 최대 길이(그림 단위). 넘으면 말줄임
export const PITCH = 16, ROWS = 24, ROW = 17, GAP = 90, ASP = 1.6;
const R_MEAN = 6, R_BRIDGE = 7, R_LIST = 5, LH = 13;   // LH: 이름표 높이(글자 11 + 위아래 여유)
const D2R = Math.PI / 180;
/** 고리를 놓는 쐐기(도, 화면 좌표: 0 = 3시, 90 = 6시). 12시(270) · 3시 축(0) · 6시(90) 둘레는 줄기가 지나므로 비워 둔다 */
export const SECT_MEAN: [number, number][] = [[115, 245]];
// 위 쐐기는 -40° 부터: 그보다 3시에 가까우면 허브에서 다리로 가는 빛살이 허브 이름(3시 축 바로 위 가로 글자)을 지난다
export const SECT_BRIDGE: [number, number][] = [[-72, -40], [14, 72]];

/** 고리 하나(반지름 R)가 받는 자리 수: 쐐기마다 호 길이 ÷ 간격(내림), 쐐기마다 적어도 하나 */
export function ringCapacity(R: number, sectors: [number, number][], pitch = PITCH): number[] {
  return sectors.map(([a, b]) => Math.max(1, Math.floor(((b - a) * D2R * R) / pitch)));
}
/** n 개를 안쪽 고리부터 채운다. 한 고리 안에서는 쐐기의 자리 수에 비례해 나누고, 쐐기 안에서는 고르게 편다.
 *  돌려주는 것: 자리마다 [각(라디안), 반지름, 고리 번호] */
export function ringSlots(n: number, o: { r0: number; step: number; sectors: [number, number][]; pitch?: number }): [number, number, number][] {
  const out: [number, number, number][] = []; let R = o.r0, left = n, ring = 0;
  while (left > 0) {
    const caps = ringCapacity(R, o.sectors, o.pitch), cap = caps.reduce((s, x) => s + x, 0), take = Math.min(cap, left);
    const k = caps.map((c) => Math.floor((take * c) / cap));
    let rest = take - k.reduce((s, x) => s + x, 0);
    const byRoom = caps.map((_, i) => i).sort((i, j) => caps[j] - k[j] - (caps[i] - k[i]) || i - j);
    for (let q = 0; rest > 0; q++) { const i = byRoom[q % byRoom.length]; if (k[i] < caps[i]) { k[i]++; rest--; } }
    o.sectors.forEach(([a, b], i) => { for (let j = 0; j < k[i]; j++) out.push([(a + ((j + .5) * (b - a)) / k[i]) * D2R, R, ring]); });
    left -= take; R += o.step; ring++;
  }
  return out;
}
/** 이름표 글자를 최대 길이에 맞춰 자른다(말줄임 하나 포함) */
export function fitLabel(text: string, measure: Measure, max = MAXW): { text: string; w: number } {
  let w = measure(text, "leaf"); if (w <= max) return { text, w };
  let lo = 0, hi = text.length;
  while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (measure(text.slice(0, mid) + "…", "leaf") <= max) lo = mid; else hi = mid - 1; }
  const t = text.slice(0, lo).trimEnd() + "…"; w = measure(t, "leaf"); return { text: t, w };
}

const grow = (b: Box, x0: number, y0: number, x1: number, y1: number) => { b[0] = Math.min(b[0], x0); b[1] = Math.min(b[1], y0); b[2] = Math.max(b[2], x1); b[3] = Math.max(b[3], y1); };
/** 회전 이름표가 차지하는 네 꼭짓점: 중심 c 에서 각 a 로 s0 부터 len 만큼, 두께 h */
export function labelPoly(cx: number, cy: number, a: number, s0: number, len: number, h = LH): Pt[] {
  const ux = Math.cos(a), uy = Math.sin(a), nx = -uy * h / 2, ny = ux * h / 2, s1 = s0 + len;
  return [[cx + ux * s0 + nx, cy + uy * s0 + ny], [cx + ux * s1 + nx, cy + uy * s1 + ny], [cx + ux * s1 - nx, cy + uy * s1 - ny], [cx + ux * s0 - nx, cy + uy * s0 - ny]];
}
/** 그림에서 겹침을 따질 모양들: 노드 원(상자로), 이름표(회전 사각형 또는 가로 상자), 목록 머리. 테스트와 화면 측정이 같이 쓴다 */
export function itemPolys(it: WItem): Pt[][] {
  // 노드 원은 바깥에 접하는 팔각형으로 잰다(사각형으로 재면 대각선 쪽을 4할 넘게 부풀려 이웃 자리를 겹침으로 읽는다)
  const q = it.r / Math.cos(Math.PI / 8), d: Pt[] = Array.from({ length: 8 }, (_, i) => [it.x + q * Math.cos((i + .5) * Math.PI / 4), it.y + q * Math.sin((i + .5) * Math.PI / 4)] as Pt);
  if (!it.lw) return [d];
  if (it.role === "hub") { const x0 = it.x + it.r + 8, y1 = it.y - 2, y0 = y1 - (FONT.hub + 4); return [d, [[x0, y0], [x0 + it.lw, y0], [x0 + it.lw, y1], [x0, y1]]]; }
  if (it.role === "list") { const x0 = it.x + it.r + 6, y0 = it.y - LH / 2; return [d, [[x0, y0], [x0 + it.lw, y0], [x0 + it.lw, y0 + LH], [x0, y0 + LH]]]; }
  return [d, labelPoly(it.x, it.y, it.ang ?? 0, it.r + 6, it.lw)];
}

/** 주제 하나의 몸(허브 · 고리 · 다리 · 목록)을 허브 원점에 놓는다 */
function ownBlock(t: WTopicIn, measure: Measure, depth: number) {
  const items: WItem[] = [], heads: WHead[] = [], stems: Pt[][] = [], spokes: string[] = [];
  const hubLw = measure(t.label, "hub") + 6 + measure(String(t.count), "leaf");
  const hub: WItem = { id: t.id, k: "topic", x: 0, y: 0, r: t.r, role: "hub", label: t.label, lw: hubLw, topic: t.id, depth, order: 0 };
  items.push(hub);
  const box: Box = [-t.r, -t.r, t.r, t.r]; for (const p of itemPolys(hub)) for (const [x, y] of p) grow(box, x, y, x, y);
  const ring = (list: WNodeIn[], sectors: [number, number][], r: number, role: "mean" | "bridge", r0: number) => {
    if (!list.length) return;
    const lab = list.map((n) => fitLabel(n.label, measure)), maxW = Math.max(...lab.map((l) => l.w));
    const slots = ringSlots(list.length, { r0, step: 2 * r + 6 + maxW + 14, sectors });
    list.forEach((n, i) => { const [a, R] = slots[i];
      const it: WItem = { id: n.id, k: n.k, x: Math.cos(a) * R, y: Math.sin(a) * R, r, role, ang: a, label: lab[i].text, lw: lab[i].w, topic: t.id, depth, order: items.length };
      items.push(it); spokes.push(n.id); for (const p of itemPolys(it)) for (const [x, y] of p) grow(box, x, y, x, y); }); };
  const byPrio = (l: WNodeIn[]) => [...l].sort((a, b) => b.prio - a.prio || a.label.localeCompare(b.label));
  ring(byPrio(t.mean), SECT_MEAN, R_MEAN, "mean", t.r + 44);
  ring(byPrio(t.bridges), SECT_BRIDGE, R_BRIDGE, "bridge", Math.max(t.r + 56, 84));
  // 6시 목록: 허브에서 곧게 내려가 버스, 열마다 ROWS 줄. 종류마다 머리(이름 · 수)는 그 종류의 첫 열 위에
  const lists = t.lists.filter((l) => l.items.length);
  if (lists.length) {
    const busY = Math.max(box[3], t.r + 20) + 30, cols: { x: number; w: number; items: WNodeIn[]; head?: string; labs: { text: string; w: number }[] }[] = [];
    for (const l of lists) { const labs = l.items.map((n) => fitLabel(n.label, measure)), head = `${l.name} ${l.items.length}`;
      const w = Math.max(...labs.map((q) => q.w), measure(head, "head") - 14) + 14 + 2 * R_LIST + 6 + 26;
      for (let c = 0; c * ROWS < l.items.length; c++) cols.push({ x: 0, w, items: l.items.slice(c * ROWS, (c + 1) * ROWS), labs: labs.slice(c * ROWS, (c + 1) * ROWS), head: c === 0 ? head : undefined }); }
    const total = cols.reduce((s, c) => s + c.w, 0); let x = -total / 2;
    for (const c of cols) { c.x = x; x += c.w; }
    const top = busY + 34, spineX = (c: { x: number }) => c.x + 4;
    stems.push([[0, t.r + 3], [0, busY]], [[spineX(cols[0]), busY], [spineX(cols[cols.length - 1]), busY]]);
    grow(box, Math.min(0, spineX(cols[0])), busY, Math.max(0, spineX(cols[cols.length - 1])), busY);
    for (const c of cols) {
      if (c.head) { const w = measure(c.head, "head"); heads.push({ x: c.x + 12, y: busY + 20, text: c.head, topic: t.id, w }); grow(box, c.x + 12, busY + 9, c.x + 12 + w, busY + 24); }
      const lastY = top + (c.items.length - 1) * ROW; stems.push([[spineX(c), busY], [spineX(c), lastY]]);
      c.items.forEach((n, j) => { const y = top + j * ROW, nx = c.x + 26;
        stems.push([[spineX(c), y], [nx - R_LIST - 2, y]]);
        const it: WItem = { id: n.id, k: n.k, x: nx, y, r: R_LIST, role: "list", label: c.labs[j].text, lw: c.labs[j].w, topic: t.id, depth, order: items.length };
        items.push(it); for (const p of itemPolys(it)) for (const [px, py] of p) grow(box, px, py, px, py); }); }
  }
  return { hub, items, heads, stems, spokes, box };
}

interface Cell { own: ReturnType<typeof ownBlock>; kids: { cell: Cell; dx: number; dy: number }[]; box: Box; trunk: Pt[][]; drops: { to: string; pts: Pt[] }[] }
/** 칸 하나를 짓는다(허브 원점의 지역 좌표). 하위 주제 칸은 몸 상자 오른쪽에 격자로 선다: 열 수는 칸 전체가 가장 작고
 *  화면 비율(ASP)에 가까워지는 것으로 고른다 */
function buildCell(t: WTopicIn, measure: Measure, depth: number): Cell {
  const own = ownBlock(t, measure, depth), kidCells = t.subs.map((s) => buildCell(s, measure, depth + 1));
  if (!kidCells.length) return { own, kids: [], box: [...own.box] as Box, trunk: [], drops: [] };
  const sz = kidCells.map((c) => [c.box[2] - c.box[0], c.box[3] - c.box[1]]);
  let best: { k: number; cw: number[]; rh: number[]; sc: number } | null = null;
  for (let k = 1; k <= kidCells.length; k++) {
    const rows = Math.ceil(kidCells.length / k), cw = Array(k).fill(0), rh = Array(rows).fill(0);
    sz.forEach(([w, h], i) => { cw[i % k] = Math.max(cw[i % k], w); rh[Math.floor(i / k)] = Math.max(rh[Math.floor(i / k)], h); });
    const gw = cw.reduce((s, x) => s + x, 0) + GAP * (k - 1), gh = rh.reduce((s, x) => s + x, 0) + GAP * rows - GAP / 2;
    const W = own.box[2] - own.box[0] + GAP + gw, Hh = Math.max(own.box[3] - own.box[1] + GAP / 2, gh + GAP / 2), sc = W * Hh * (1 + Math.abs(Math.log(W / Hh / ASP)));
    if (!best || sc < best.sc) best = { k, cw, rh, sc };
  }
  const { k, cw, rh } = best!, gx0 = own.box[2] + GAP, gy0 = own.box[1], busX = own.box[2] + GAP / 2;
  const colX: number[] = []; for (let j = 0, x = gx0; j < k; j++) { colX.push(x); x += cw[j] + GAP; }
  const rowY: number[] = []; for (let r = 0, y = gy0; r < rh.length; r++) { rowY.push(y); y += rh[r] + GAP; }
  const kids = kidCells.map((cell, i) => { const j = i % k, r = Math.floor(i / k); return { cell, dx: colX[j] - cell.box[0], dy: rowY[r] - cell.box[1] }; });
  const box = [...own.box] as Box;
  for (const q of kids) grow(box, q.cell.box[0] + q.dx, q.cell.box[1] + q.dy, q.cell.box[2] + q.dx, q.cell.box[3] + q.dy);
  grow(box, busX, gy0 - GAP / 2, busX, gy0 - GAP / 2);
  // 줄기: 허브 3시로 나가 버스(busX), 버스를 따라 줄마다의 통로(줄 위 GAP/2), 통로를 따라 자식 허브 위까지, 곧게 내려 허브로
  const corr = (r: number) => rowY[r] - GAP / 2, lastR = Math.floor((kids.length - 1) / k);
  const trunk: Pt[][] = [[[own.hub.r + 3, 0], [busX, 0]], [[busX, Math.min(0, corr(0))], [busX, Math.max(0, corr(lastR))]]];
  const drops = kids.map((q, i) => { const r = Math.floor(i / k), hx = q.dx, hy = q.dy, cy = corr(r);
    return { to: q.cell.own.hub.id, pts: [[busX, cy], [hx, cy], [hx, hy - q.cell.own.hub.r - 3]] as Pt[] }; });
  return { own, kids, box, trunk, drops };
}

/** 전체 배치: 뿌리 칸을 원점에 두고 모든 칸을 절대 좌표로 편다 */
export function layoutWhole(root: WTopicIn, measure: Measure): WLayout {
  const items: WItem[] = [], heads: WHead[] = [], topics: WLayout["topics"] = [];
  const top = buildCell(root, measure, 0);
  const walk = (c: Cell, ox: number, oy: number) => {
    const sh = (p: Pt): Pt => [p[0] + ox, p[1] + oy];
    const first = items.length;   // 몸의 첫 항목이 허브다(ownBlock 이 맨 먼저 넣는다)
    for (const it of c.own.items) items.push({ ...it, x: it.x + ox, y: it.y + oy, order: items.length });
    for (const h of c.own.heads) heads.push({ ...h, x: h.x + ox, y: h.y + oy });
    const hub = items[first];
    topics.push({ id: c.own.hub.id, hub, spokes: c.own.spokes, stems: c.own.stems.map((s) => s.map(sh)), trunk: c.trunk.map((s) => s.map(sh)),
      drops: c.drops.map((d) => ({ to: d.to, pts: d.pts.map(sh) })), box: [c.box[0] + ox, c.box[1] + oy, c.box[2] + ox, c.box[3] + oy],
      own: [c.own.box[0] + ox, c.own.box[1] + oy, c.own.box[2] + ox, c.own.box[3] + oy] });
    for (const q of c.kids) walk(q.cell, ox + q.dx, oy + q.dy);
  };
  walk(top, 0, 0);
  return { items, byId: new Map(items.map((it) => [it.id, it])), heads, topics, bounds: [...top.box] as Box };
}

/** 그리기의 세 단(화면 배율 s = 화면 px / 그림 단위):
 *   near: 노드 이름표가 읽힐 만하다(FONT.leaf × s ≥ LABEL_MIN_PX) → 모양 · 이름표 · 빛살
 *   mid : 이름표는 작아 빼고, 노드 모양과 빛살은 그린다
 *   far : 노드는 주제마다 점 한 겹(모양 없음), 빛살도 뺀다. 허브 이름만 화면 크기로 겹치지 않는 것을 단다 */
export type Tier = "far" | "mid" | "near";
export const labelShown = (fontUnits: number, scale: number, min = LABEL_MIN_PX) => fontUnits * scale >= min;
/** 먼 배치의 점이 제 주제의 것으로 읽히는가(측정 값). inOtherBlock: 다른 주제 몸 상자 안에 앉은 노드 수. nearerOther: 제 허브보다
 *  다른 허브에 더 가까운 노드 수. knnForeign: 화면 12px 안의 가까운 이웃(넷까지) 가운데 다른 주제인 몫. fused: 자리를 못 받은 노드 */
export interface Attribution { n: number; inOtherBlock: number; nearerOther: number; knnForeign: number; fused: number }
export function attribution(L: WLayout, F: FarArr): Attribution {
  const hubs = L.topics.map((t) => t.hub), HC = 400 / F.s, HG = new Map<string, number[]>();
  hubs.forEach((h, i) => { const k = Math.floor(h.x / HC) + "," + Math.floor(h.y / HC); if (!HG.has(k)) HG.set(k, []); HG.get(k)!.push(i); });
  const pts = [...F.pos].map(([id, [x, y]]) => ({ id, x, y, t: L.byId.get(id)!.topic })), C = 12 / F.s, NG = new Map<string, number[]>();
  pts.forEach((q, i) => { const k = Math.floor(q.x / C) + "," + Math.floor(q.y / C); if (!NG.has(k)) NG.set(k, []); NG.get(k)!.push(i); });
  const own = new Map(L.topics.map((t) => [t.id, t])); let inOther = 0, nearer = 0, kt = 0, kf = 0;
  for (const q of pts) { const mine = own.get(q.t)!, dOwn = Math.hypot(q.x - mine.hub.x, q.y - mine.hub.y);
    for (const t of L.topics) if (t.id !== q.t && q.x >= t.own[0] && q.x <= t.own[2] && q.y >= t.own[1] && q.y <= t.own[3]) { inOther++; break; }
    let near = false; for (let r = 0; r < 64 && !near; r = r ? r * 2 : 1) { const gx = Math.floor(q.x / HC), gy = Math.floor(q.y / HC);
      for (let i = gx - r; i <= gx + r && !near; i++) for (let j = gy - r; j <= gy + r && !near; j++) for (const k of HG.get(i + "," + j) || []) { const h = hubs[k];
        if (h.topic !== q.t && Math.hypot(q.x - h.x, q.y - h.y) < dOwn - 1e-6) { near = true; break; } }
      if (r * HC > dOwn) break; }
    if (near) nearer++;
    const gx = Math.floor(q.x / C), gy = Math.floor(q.y / C), cand: [number, string][] = [];
    for (let i = gx - 1; i <= gx + 1; i++) for (let j = gy - 1; j <= gy + 1; j++) for (const k of NG.get(i + "," + j) || []) { const o = pts[k]; if (o === q) continue; const d = Math.hypot(o.x - q.x, o.y - q.y); if (d < C) cand.push([d, o.t]); }
    cand.sort((a, b) => a[0] - b[0]); for (const [, t] of cand.slice(0, 4)) { kt++; if (t !== q.t) kf++; } }
  return { n: pts.length, inOtherBlock: inOther, nearerOther: nearer, knnForeign: kt ? kf / kt : 0, fused: F.fused };
}
/** 문턱: 점이 제 주제의 것으로 읽히는 그림과 영역마다 한 덩어리로 섞이는 그림 사이. 잰 값(2,500 노드): 읽히는 그림은 다른 허브에 더
 *  가까운 노드 18~24%, 이웃의 다른 주제 몫 2~4%. 섞이는 그림은 63% 넘게, 9~19%. 문턱은 그 사이에 두 벌이다(이력):
 *  허브만에서 점으로 넘어갈 때는 ATTR_IN 아래여야 하고, 점에서 허브만으로 돌아갈 때는 ATTR_OUT 을 넘어야 한다. 격자의 자리가 칸 폭
 *  1px 마다 달라져 이웃의 다른 주제 몫이 ±0.5%p 흔들리므로, 문턱이 하나면 천천히 창을 줄일 때 경계에서 몇 번이고 오간다 */
export const ATTR_IN = { nearerOther: .38, knnForeign: .055 } as const;
export const ATTR_OUT = { nearerOther: .42, knnForeign: .065 } as const;
type Thresholds = { nearerOther: number; knnForeign: number };
export const attributable = (a: Attribution, t: Thresholds = ATTR_IN) => a.fused === 0 && a.inOtherBlock === 0 && a.nearerOther <= t.nearerOther * a.n && a.knnForeign <= t.knnForeign;
/** 맞춤 그림에서 그리는 것: 앞의 선택(prev, 처음이면 null)이 점이면 ATTR_OUT 을 넘을 때만 허브만으로, 아니면 ATTR_IN 아래일 때만 점으로 */
export function fitChoice(prev: "dots" | "hubs" | null, a: Attribution): "dots" | "hubs" {
  return attributable(a, prev === "dots" ? ATTR_OUT : ATTR_IN) ? "dots" : "hubs";
}
/** 먼 단계의 계획: 맞춤 배율부터 1.35 배씩 올리며 처음으로 귀속 시험을 통과하는 격자를 찾는다. sDots 보다 멀면 허브만 그리고
 *  (점 없이), sDots 부터 RING_SEP 아래까지 그 격자의 점을 그린다. 통과하는 배율이 없으면 RING_SEP 아래는 모두 허브만이다 */
export function farPlan(L: WLayout, sFit: number, nameW: (topicId: string) => number, o: { dotPx?: number; gapPx?: number; hubPx?: number; namePx?: number } = {}) {
  const tried: { s: number; a: Attribution; ok: boolean }[] = [];
  for (let s = sFit; s < RING_SEP; s *= 1.35) { const F = farArrange(L, s, nameW, o), a = attribution(L, F), ok = attributable(a); tried.push({ s, a, ok }); if (ok) return { sDots: s, F, tried }; }
  return { sDots: null as number | null, F: null as FarArr | null, tried };
}
/** 고리 배치의 노드가 화면에서 떨어져 보이는 가장 작은 배율: 고리 간격(PITCH)과 목록 줄 간격(ROW) 가운데 좁은 것이 4.8px */
export const RING_SEP = 4.8 / Math.min(PITCH, ROW);
export function lodTier(scale: number): Tier { return labelShown(FONT.leaf, scale) ? "near" : scale >= RING_SEP ? "mid" : "far"; }

/** 두 볼록 다각형이 겹치는가(분리축). 닿기만 하면 겹침이 아니다 */
export function polysOverlap(A: Pt[], B: Pt[], eps = .01): boolean {
  for (const P of [A, B]) for (let i = 0; i < P.length; i++) {
    const [x0, y0] = P[i], [x1, y1] = P[(i + 1) % P.length], nx = y0 - y1, ny = x1 - x0;
    let a0 = Infinity, a1 = -Infinity, b0 = Infinity, b1 = -Infinity;
    for (const [x, y] of A) { const d = x * nx + y * ny; a0 = Math.min(a0, d); a1 = Math.max(a1, d); }
    for (const [x, y] of B) { const d = x * nx + y * ny; b0 = Math.min(b0, d); b1 = Math.max(b1, d); }
    const len = Math.hypot(nx, ny) || 1; if (a1 <= b0 + eps * len || b1 <= a0 + eps * len) return false;
  }
  return true;
}
const polyBox = (P: Pt[]): Box => { const b: Box = [Infinity, Infinity, -Infinity, -Infinity]; for (const [x, y] of P) grow(b, x, y, x, y); return b; };

/** 이름표 고르기(전체 보기의 주제 이름 · 가리킨 끝의 이름): 우선순위 높은 것부터, 후보 자리 가운데 이미 놓인 이름표 · 막는 것과
 *  겹치지 않는 첫 자리를 준다. 자리가 없으면 -1(그 이름은 그리지 않고, 가리키면 풍선이 답한다) */
export function placeLabels(cands: { id: string; prio: number; at: Pt[][] }[], blocks: Pt[][] = []): Map<string, number> {
  const out = new Map<string, number>(), placed: { p: Pt[]; b: Box }[] = [];
  if (!cands.length) return out;
  // 격자 통: 막는 것이 수천 개여도 후보마다 가까운 통만 본다. 통 크기는 후보 상자의 가장 긴 변(긴 줄기 하나가 수만 통에 들지 않게 0 보다 크게)
  // 끝없는 상자(그래프 자리 밖이라 쓸 수 없는 후보)는 크기에 넣지 않고, 놓을 수 없는 자리로 본다
  const finite = (b: Box) => b.every(Number.isFinite);
  let cell = 0; for (const c of cands) for (const P of c.at) { const b = polyBox(P); if (finite(b)) cell = Math.max(cell, b[2] - b[0], b[3] - b[1]); }
  if (!(cell > 0)) cell = 1;
  const G = new Map<string, number[]>(), keys = (b: Box) => { const o: string[] = []; for (let i = Math.floor(b[0] / cell); i <= Math.floor(b[2] / cell); i++) for (let j = Math.floor(b[1] / cell); j <= Math.floor(b[3] / cell); j++) o.push(i + "," + j); return o; };
  const add = (p: Pt[]) => { const b = polyBox(p), k = placed.length; if (!finite(b)) return; placed.push({ p, b }); for (const q of keys(b)) { if (!G.has(q)) G.set(q, []); G.get(q)!.push(k); } };
  for (const p of blocks) add(p);
  const hit = (P: Pt[]) => { const b = polyBox(P), seen = new Set<number>(); if (!finite(b)) return true;
    for (const q of keys(b)) for (const k of G.get(q) || []) { if (seen.has(k)) continue; seen.add(k); const o = placed[k]; if (o.b[0] < b[2] && b[0] < o.b[2] && o.b[1] < b[3] && b[1] < o.b[3] && polysOverlap(P, o.p)) return true; }
    return false; };
  for (const c of [...cands].sort((a, b) => b.prio - a.prio || (a.id < b.id ? -1 : 1))) {
    const i = c.at.findIndex((P) => !hit(P)); out.set(c.id, i);
    if (i >= 0) add(c.at[i]);
  }
  return out;
}

/** 먼 단계의 배치(의미 확대). 맞춤 배율에서는 고리에 선 노드가 1px 안팎으로 붙어 한 덩어리로 보인다. 그래서 먼 단계에서는
 *  그래프 전체에 깐 격자 하나(간격 = 맞춤 배율에서 점 지름 + 틈)의 점에 노드를 다시 앉힌다. 한 격자점에는 한 노드만 서므로
 *  어떤 두 점도 간격보다 가깝지 않다. 허브 둘레, 주제 줄기(위에서 드는 것 · 버스 · 통로), 이름 자리에 걸친 격자점은 비운다.
 *  주제는 큰 것부터 자기 몸 상자 안의 빈 점을 먼저, 모자라면 상자에서 가까운 빈 점을 받는다. 노드는 고리 자리의 위 → 아래 ·
 *  왼 → 오른 차례로 받은 점(같은 차례)에 앉는다(가까이 가면 제 고리 자리로 짧게 옮겨 간다).
 *  이름: 허브 왼쪽 몸 상자 안에 화면 크기 이름이 들어가는 주제는 그 자리를 비워 거기에 쓴다(큰 주제부터).
 *  fused: 격자가 모자라 같은 점을 나눠 쓴 노드 수(0 이 아니면 화면이 그렇다고 말해야 한다) */
export interface FarArr { pos: Map<string, Pt>; names: Map<string, Box>; dotPx: number; hubPx: number; gapPx: number; pitchPx: number; fused: number; s: number }
export function farArrange(L: WLayout, s: number, nameW: (topicId: string) => number, o: { dotPx?: number; gapPx?: number; hubPx?: number; namePx?: number } = {}): FarArr {
  const dotPx = o.dotPx ?? 3, gapPx = o.gapPx ?? 2, hubPx = o.hubPx ?? 2.5, namePx = o.namePx ?? 12, u = 1 / s, p = (dotPx + gapPx) * u;
  const pos = new Map<string, Pt>(), names = new Map<string, Box>(), by = new Map<string, WItem[]>(); let fused = 0;
  for (const it of L.items) if (it.role !== "hub") { if (!by.has(it.topic)) by.set(it.topic, []); by.get(it.topic)!.push(it); }
  const [bx0, by0, bx1, by1] = L.bounds, ox = bx0 - 4 * p, oy = by0 - 4 * p, NX = Math.ceil((bx1 - bx0) / p) + 9, NY = Math.ceil((by1 - by0) / p) + 9;
  const used = new Uint8Array(NX * NY), at = (i: number, j: number): Pt => [ox + i * p, oy + j * p];
  const block = (x0: number, y0: number, x1: number, y1: number) => { for (let i = Math.max(0, Math.ceil((x0 - ox) / p)); i <= Math.min(NX - 1, Math.floor((x1 - ox) / p)); i++)
    for (let j = Math.max(0, Math.ceil((y0 - oy) / p)); j <= Math.min(NY - 1, Math.floor((y1 - oy) / p)); j++) used[j * NX + i] = 1; };
  const clear = (hubPx + gapPx + dotPx / 2) * u, half = (dotPx / 2 + gapPx) * u;
  for (const tp of L.topics) { const h = tp.hub; block(h.x - clear, h.y - clear, h.x + clear, h.y + clear);
    for (const seg of [...tp.trunk, ...tp.drops.map((d) => d.pts)]) for (let k = 1; k < seg.length; k++) { const [a, c] = [seg[k - 1], seg[k]];
      block(Math.min(a[0], c[0]) - half, Math.min(a[1], c[1]) - half, Math.max(a[0], c[0]) + half, Math.max(a[1], c[1]) + half); } }
  const order = [...L.topics].sort((a, b) => (by.get(b.id)?.length || 0) - (by.get(a.id)?.length || 0) || (a.id < b.id ? -1 : 1));
  // 이름 자리: 허브 왼쪽 · 오른쪽 위(나가는 줄기 위) · 오른쪽 아래 · 아래 가운데 가운데 줄기 선 · 다른 허브 · 앞서 잡은 이름과 닿지 않는 첫 자리.
  // 줄기는 먼 단계에서도 그리므로 이름을 지나면 안 된다. 잡은 자리의 격자점은 비워 두어 점이 이름 밑에 앉지 않는다
  const segs: Box[] = []; for (const tp of L.topics) for (const sg of [...tp.trunk, ...tp.drops.map((d) => d.pts)]) for (let k = 1; k < sg.length; k++) {
    const [a, c] = [sg[k - 1], sg[k]]; segs.push([Math.min(a[0], c[0]), Math.min(a[1], c[1]), Math.max(a[0], c[0]), Math.max(a[1], c[1])]); }
  const hit = (A: Box, B: Box, m = 0) => A[0] < B[2] + m && B[0] < A[2] + m && A[1] < B[3] + m && B[1] < A[3] + m;
  const taken: Box[] = [], g4 = 4 * u;
  for (const tp of order) { const h = tp.hub, w = nameW(tp.id) * u, nh = namePx * 1.25 * u, r = hubPx * u;
    const cands: Box[] = [[h.x - r - g4 - w, h.y - nh / 2, h.x - r - g4, h.y + nh / 2], [h.x + r + g4, h.y - nh - g4, h.x + r + g4 + w, h.y - g4],
      [h.x + r + g4, h.y + g4, h.x + r + g4 + w, h.y + nh + g4], [h.x - w / 2, h.y + r + g4, h.x + w / 2, h.y + r + g4 + nh]];
    const ok = (b: Box) => b[0] >= bx0 && b[2] <= bx1 && b[1] >= by0 && b[3] <= by1 && !segs.some((sg) => hit(b, sg, half)) && !taken.some((t) => hit(b, t, g4))
      && !L.topics.some((o) => o !== tp && hit(b, [o.hub.x, o.hub.y, o.hub.x, o.hub.y], clear));
    const nm = cands.find(ok); if (!nm) continue;
    names.set(tp.id, nm); taken.push(nm); block(nm[0] - half, nm[1] - half, nm[2] + half, nm[3] + half); }
  // 앉히기: 몸 상자 안의 격자점은 그 주제 몫이다(다른 주제가 넘쳐 들어오지 못한다). 먼저 모든 주제가 자기 몫에서 받고, 모자란 주제는
  // 몸 상자에서 한 간격씩 함께 넓혀 가며 주인 없는 점(상자 밖)을 가까운 것부터 받는다: 넘친 노드가 남의 상자를 건너 멀리 가지 않는다
  const owner = new Int32Array(NX * NY).fill(-1), T = order.map((tp) => tp);
  T.forEach((tp, ti) => { const B = tp.own; for (let i = Math.max(0, Math.ceil((B[0] - ox) / p)); i <= Math.min(NX - 1, Math.floor((B[2] - ox) / p)); i++)
    for (let j = Math.max(0, Math.ceil((B[1] - oy) / p)); j <= Math.min(NY - 1, Math.floor((B[3] - oy) / p)); j++) if (owner[j * NX + i] < 0) owner[j * NX + i] = ti; });
  const got = T.map(() => [] as [number, number][]), need = T.map((tp) => (by.get(tp.id) || []).length);
  const boxD = (B: Box, x: number, y: number) => Math.max(0, B[0] - x, x - B[2]) + Math.max(0, B[1] - y, y - B[3]);
  const take = (ti: number, cands: [number, number][], limit = Infinity) => { const h = T[ti].hub;
    cands.sort((A, C) => { const [ax, ay] = at(A[0], A[1]), [cx, cy] = at(C[0], C[1]); return boxD(T[ti].own, ax, ay) - boxD(T[ti].own, cx, cy) || Math.hypot(ax - h.x, ay - h.y) - Math.hypot(cx - h.x, cy - h.y); });
    for (const [i, j] of cands) { if (got[ti].length >= need[ti]) break; if (used[j * NX + i] || boxD(T[ti].own, ...at(i, j)) > limit) continue; used[j * NX + i] = 1; got[ti].push([i, j]); } };
  T.forEach((tp, ti) => { if (!need[ti]) return; const B = tp.own, c: [number, number][] = [];
    for (let i = Math.max(0, Math.ceil((B[0] - ox) / p)); i <= Math.min(NX - 1, Math.floor((B[2] - ox) / p)); i++) for (let j = Math.max(0, Math.ceil((B[1] - oy) / p)); j <= Math.min(NY - 1, Math.floor((B[3] - oy) / p)); j++)
      if (owner[j * NX + i] === ti && !used[j * NX + i]) c.push([i, j]);
    take(ti, c); });
  const lists = new Map<number, [number, number][]>();
  for (let e = p, guard = 0; T.some((_, ti) => got[ti].length < need[ti]) && guard < 4 * Math.max(NX, NY); e += p, guard++) {
    for (let ti = 0; ti < T.length; ti++) { if (got[ti].length >= need[ti]) continue; const B = T[ti].own, R = Math.ceil(e / p);
      if (!lists.has(ti) || guard % 8 === 0) { const c: [number, number][] = [];
        for (let i = Math.max(0, Math.floor((B[0] - ox) / p) - R - 8); i <= Math.min(NX - 1, Math.ceil((B[2] - ox) / p) + R + 8); i++) for (let j = Math.max(0, Math.floor((B[1] - oy) / p) - R - 8); j <= Math.min(NY - 1, Math.ceil((B[3] - oy) / p) + R + 8); j++)
          if (owner[j * NX + i] < 0 && !used[j * NX + i]) c.push([i, j]);
        lists.set(ti, c); }
      take(ti, lists.get(ti)!, e); } }
  T.forEach((tp, ti) => { const its = [...(by.get(tp.id) || [])].sort((a, b) => a.y - b.y || a.x - b.x), h = tp.hub; if (!its.length) return;
    const pts = got[ti].sort((A, C) => A[1] - C[1] || A[0] - C[0]); if (pts.length < its.length) fused += its.length - pts.length;
    its.forEach((it, k) => pos.set(it.id, pts[k] ? at(pts[k][0], pts[k][1]) : [h.x, h.y])); });
  return { pos, names, dotPx, hubPx, gapPx, pitchPx: p * s, fused, s };
}

/** 손 맞추기용 격자 색인: 점에서 가장 가까운 항목(반지름을 뺀 거리)을 maxD 안에서 찾는다 */
export function gridIndex<T extends { x: number; y: number; r: number }>(items: T[], cell = 64) {
  const g = new Map<string, T[]>(), key = (i: number, j: number) => `${i},${j}`;
  for (const it of items) { const k = key(Math.floor(it.x / cell), Math.floor(it.y / cell)); if (!g.has(k)) g.set(k, []); g.get(k)!.push(it); }
  return { near(x: number, y: number, maxD: number): T | null {
    const span = Math.ceil((maxD + 30) / cell), i0 = Math.floor(x / cell), j0 = Math.floor(y / cell); let best: T | null = null, bd = Infinity;
    for (let i = i0 - span; i <= i0 + span; i++) for (let j = j0 - span; j <= j0 + span; j++) for (const it of g.get(key(i, j)) || []) {
      const d = Math.hypot(it.x - x, it.y - y) - it.r; if (d < bd && d <= maxD) { bd = d; best = it; } }
    return best; } };
}
