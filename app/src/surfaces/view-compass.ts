// @ts-nocheck — 시안(scratchpad)에서 옮긴 첫 판이다. 형식은 이 파일 안에서만 느슨하고, 밖으로 내는 함수(mountCompass)의 형식은 아래에 적었다.
/** 그래프 탭의 나침반 렌즈 — 가운데가 지금 보는 것(주제 또는 노드)이고, 누르면 그 자리에서 같은 나침반으로 펼친다.
 *
 *  자리는 나침반으로 정해져 있다(온 방향을 따라 돈다): 12시 = 부모(온 곳), 9시 = 뜻의 관계·이 주제의 개념, 3시 = 하위 주제와 그 사이의
 *  다리·여기서 나온 것, 6시 = 줄기로 이어진 결정·기록·사건. 주제가 가운데면 하위 주제는 둘레 전체에 고르게 선다. 지나온 층은 옅게 남는다.
 *  선은 렌즈 문법 그대로다: 뜻의 관계 = 빛살(가운데 쪽이 굵고 양끝은 노드 둘레를 띄운 원호), 방향 있는 관계 = 가운데의 표(›, ∘›, |›),
 *  붙은 것 = 줄기와 가지(꺾이는 곳은 둥글게). 다른 주제의 노드에는 가운데 주제 색의 초승달, 다리는 잇는 주제마다 한 조각인 갈래 고리.
 *  길·찾기·기간·종류·고정·읽는 법은 LNB 의 칸(side)에 선다(기간 밖은 흐리게, 끈 종류는 뺀다). 고정은 이 기기에만 저장한다.
 *  SVG 하나라 GPU 가 없어도 된다. 한 번에 그리는 것은 펼친 층의 이웃뿐이다. */
import { COPY } from "../copy";
import { esc as escHtml } from "../components";
import { tintPlan, topicRing } from "../view-tint";
import { topicJewels } from "../theme";

export interface CompassData { nodes: any[]; edges: [string, string, string][] }
/** 다시 그릴 때 이어 받는 나침반의 자리(`CompassHandle.snapshot`). */
export interface CompassState { path: string[]; hist: string[][]; hi: number; mode: string; filt: [string, string[]][]; more: [string, number][];
  koff: string[]; tsel: [string, string] | null; view: { x: number; y: number; w: number; h: number } | null; query: string }
export interface CompassCtx { startPath?: string[]; restore?: CompassState | null; store?: string; onChange?: () => void; onOpen?: (id: string) => void; onDestroy?: () => void;
  onTree?: (o: { reveal: string[]; reset: boolean }) => void }
export interface CompassHandle { destroy: () => void; side: HTMLElement; tools: HTMLElement; filter: (q: string) => void; open: (id: string) => void;
  tree: () => { domain: string; rows: { id: string; title: string; kind: string; depth: number }[] }[]; mark: (kind: string) => string; colour: (domain: string) => string; centre: () => string;
  snapshot: () => CompassState; retheme: () => void }

const themeMode = () => (document.documentElement.dataset.theme === "dark" ? "dark" : "light");

/** 그래프 탭의 읽기(GraphNode·GraphEdge)를 나침반이 읽는 모양으로. 주제(domain) 노드는 노드의 domain 사슬에서 다시 만든다. */
const idDate = (id: string) => { const m = /-(20\d\d-\d\d-\d\d)$/.exec(String(id)); return m ? m[1] : ""; };
export function compassDataFrom(nodes: any[], edges: any[]): CompassData {
  const out = nodes.filter((n) => n.kind !== "domain" && n.kind !== "area").map((n) => ({
    id: n.id, k: String(n.id).startsWith("concept:doc.") ? "doc" : n.kind, t: n.label || n.title || n.id, d: n.domain || "",   // 주제 없는 노드는 빈 값 그대로 둔다: 어느 뿌리에 설지는 mountCompass 가 정한다
    // 날짜: 사건은 그래프 읽기의 ts 가 곧 그 일이 있었던 날이다(updated_at 은 마지막으로 고친 때라 뒤에 둔다).
    // 결정은 내용 시간 칸이 없어 props.valid_from(결정한 날)을, 그것도 없으면 id 끝의 날짜(…-2026-09-20, 이 그래프의 이름 관례)를 쓴다.
    ts: String(n.ts || n.updated_at || "").slice(0, 10), ct: String(n.content_ts || "").slice(0, 10) || idDate(n.id) || undefined,
    vf: String(n.valid_from || n.props?.valid_from || "").slice(0, 10) || undefined, vu: n.valid_until || undefined, g: n.degree || 0, m: 0 }));
  const byId = new Map(out.map((n) => [n.id, n])), M = new Set(["RELATED_TO", "PREREQUISITE_OF", "DERIVES_FROM", "INHERITS_FROM", "RETURNS_TO", "CONNECTS"]);
  const es: [string, string, string][] = [];
  for (const e of edges) { const a = typeof e.source === "object" ? e.source.id : e.source, b = typeof e.target === "object" ? e.target.id : e.target;
    if (!byId.has(a) || !byId.has(b) || e.presentation) continue; es.push([a, b, e.type]); if (M.has(e.type)) { byId.get(a).m++; byId.get(b).m++; } }
  return { nodes: out, edges: es };
}

/** 고정 핀(압정). 45° 기운 머리 · 목 · 깃 · 바늘. 채움은 currentColor 라 놓인 자리의 색을 따른다. */
const PIN_PATHS = `<g transform="rotate(45 12 12)"><rect x="7.2" y="1.6" width="9.6" height="4" rx="2" fill="currentColor"/><path d="M9.3 5.4h5.4v3.9l3.4 3.9H5.9l3.4-3.9z" fill="currentColor"/><path d="M12 13.2V21.6" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"/></g>`;
const pinIcon = (s = 14) => `<svg class="pin-ic" viewBox="0 0 24 24" width="${s}" height="${s}" aria-hidden="true">${PIN_PATHS}</svg>`;
const CMP_HTML = () => `<div class="cmp-stage"><svg class="g" id="cmp-g" role="group" aria-label="${escHtml(COPY.ui.compass.lens)}"></svg></div><div id="cmp-tip"></div>`;
/** LNB 맨 위의 검색 칸(topbar.ts, setSearchFilter) · 시간 · 범례의 종류가 한 거르개다: 함께 건 결과가 나침반과 LNB 트리(lnb.ts
 *  setLnbGraphSource)에 같이 나온다. LNB 에는 고정 판만 서고, 시간과 범례는 머리의 "그래프" 글자 오른쪽 버튼이 여는 창이다. */
const SIDE_HTML = `<div class="cmp-sec" id="cmp-dock" hidden></div>`;
const TOOLS_HTML = () => `<button class="cmp-tbtn" type="button" data-pop="time" aria-haspopup="dialog" aria-expanded="false">${escHtml(COPY.ui.compass.time)}</button><button class="cmp-tbtn" type="button" data-pop="legend" aria-haspopup="dialog" aria-expanded="false">${escHtml(COPY.ui.compass.legend)}</button>`;

const C0 = () => COPY.ui.compass;
/** 층의 실제 크기(measureCross)를 데이터별로 한 번만 잰다. 다시 그릴 때(새로고침 · 쓰고 난 뒤 마운트) 같은 주제 나무면 그대로 쓴다. */
const MEAS_CACHE = new Map<string, unknown>();
export function mountCompass(host: HTMLElement, data: CompassData, ctx: CompassCtx = {}): CompassHandle {
  const C = C0();
  const AC = new AbortController();
  const onWin = (t, f, o) => window.addEventListener(t, f, { ...(typeof o === "object" ? o : { capture: !!o }), signal: AC.signal });
  host.innerHTML = `<div class="cmp">${CMP_HTML()}</div>`;
  const root = host.querySelector(".cmp");
  const side = document.createElement("div"); side.className = "cmp-side"; side.innerHTML = SIDE_HTML;
  const tools = document.createElement("span"); tools.className = "cmp-tools"; tools.innerHTML = TOOLS_HTML();
  const pop = document.createElement("div"); pop.className = "cmp-side cmp-pop"; pop.setAttribute("role", "dialog"); pop.hidden = true; document.body.append(pop);
  const $id = (k) => { const sel = k === "g" ? "#cmp-g" : `#cmp-${k.replace(/^cmp-/, "")}`; return root.querySelector(sel) || side.querySelector(sel) || pop.querySelector(sel); };

  const W0 = data;
  const ATTACH = new Set(['ABOUT', 'ATTACHES_TO', 'MENTIONS']);
  // 뿌리: 이 그래프의 최상위 주제가 하나뿐이면 그 주제가 뿌리다('vinc' 만 쓰는 그래프는 예전과 같다). 둘 이상이거나 하나도 없으면
  // 이름 없는 가상 뿌리('')가 모든 최상위 주제를 품는다. 주제 없는 노드는 뿌리에 직접 서고, 이름을 지어 붙이지 않는다.
  // RD 는 뿌리가 경로에서 차지하는 칸 수다(영역 = 뿌리 다음 칸을 자를 때 쓴다). ⚠ 코드 어디에도 최상위 이름을 박지 말 것.
  const tops = new Set(W0.nodes.map((n) => String(n.d || '').split('/')[0]).filter(Boolean));
  const ROOT = tops.size === 1 ? [...tops][0] : '', RD = ROOT ? 1 : 0;
  const nodes = W0.nodes.map((n) => (n.d ? n : { ...n, d: ROOT })), byId = new Map(nodes.map((n) => [n.id, n]));
  const edges = W0.edges.map(([a, b, t]) => ({ a: byId.get(a), b: byId.get(b), t })).filter((e) => e.a && e.b);
  const adj = new Map(nodes.map((n) => [n.id, []])); for (const e of edges) { adj.get(e.a.id).push(e); adj.get(e.b.id).push(e); }
  // 다리는 주제를 갖지 않는다(여러 주제 사이에 있다). 그래서 다리를 끝들이 만나는 주제, 곧 끝 노드들 주제의 가장 가까운 공통 조상에 둔다.
  // 같은 영역 안의 끝들을 잇는 다리는 그 영역 층에 서고, 영역을 넘는 다리만 뿌리에 남는다. 끝마다의 주제(BENDS)는 끝 층에서 다리를 보일 때 쓴다
  const BENDS = new Map();
  for (const b of nodes) { if (b.k !== 'bridge') continue; const raw = W0.nodes.find((n) => n.id === b.id);
    const ds = [...new Set(adj.get(b.id).filter((e) => e.t === 'CONNECTS').map((e) => (e.a === b ? e.b : e.a).d).filter((d) => d && d !== ROOT))];
    BENDS.set(b.id, ds); if (raw && raw.d || !ds.length) continue;
    const seg = ds.map((d) => d.split('/')); let k = 0; while (seg.every((x) => k < x.length && x[k] === seg[0][k])) k++;
    if (k > RD) b.d = seg[0].slice(0, k).join('/'); }
  const newest = nodes.map((n) => n.ts).filter(Boolean).sort().pop();
  const fresh = (n) => { const w = when(n); return !!w && (Date.parse(newestW()) - Date.parse(w)) / 864e5 <= 7; };   // '최근' = 내용 시간으로 7일 안
  let _nw = ''; const newestW = () => _nw || (_nw = nodes.map(when).filter(Boolean).sort().pop() || '');
  // 주제: 노드의 domain 사슬에서 만든다. 'vinc/agent/roles' → vinc, vinc/agent, vinc/agent/roles (가상 뿌리면 '' 가 맨 앞에 붙는다)
  const topics = new Map();
  const tget = (p) => { if (!topics.has(p)) topics.set(p, { id: `topic:${p}`, path: p, k: 'topic', direct: [], sub: new Set(), count: 0 }); return topics.get(p); };
  const chainOf = (d) => { const o = ROOT ? [] : [''], s = d ? d.split('/') : []; for (let i = 1; i <= s.length; i++) o.push(s.slice(0, i).join('/')); return o; };
  const rootTopic = tget(ROOT); if (!ROOT) rootTopic.t = C.rootTopic;   // 뿌리는 노드가 없어도 늘 있다. 가상 뿌리만 이름표(t)를 갖는다
  for (const n of nodes) { const ch = chainOf(n.d);
    ch.forEach((p, i) => { const t = tget(p); t.count++; if ((n.ts || '') > (t.ts || '')) t.ts = n.ts; if (i < ch.length - 1) t.sub.add(ch[i + 1]); });
    tget(ch[ch.length - 1]).direct.push(n); }
  // ── 주제 색: 그래프 보기와 같은 규칙(tintPlan)이다. 영역마다 보석색 하나, 그 안의 주제는 색상각을 조금씩 돌린다 ─────────────
  let TOPIC_COL = new Map(), AREA_COL = new Map();
  function buildTopicColours() { const plan = tintPlan([...topics.keys()], themeMode()); TOPIC_COL = plan.topics; AREA_COL = plan.areas; }
  const topicColour = (p) => { if (!p || p === ROOT) return 'hsl(var(--muted-foreground))';   // 뿌리는 어느 영역도 아니라 중립색
    const s = p.split('/'); for (let i = s.length; i >= 1; i--) { const c = TOPIC_COL.get(s.slice(0, i).join('/')); if (c) return c; } return 'hsl(var(--muted-foreground))'; };
  const topicOf = (n) => (n.k === 'topic' ? n.path : n.d);
  // 섞기: 주제 색을 카드색 쪽으로 바랜다. 라이트는 카드가 거의 흰색이고 보석 토큰의 대비 바닥도 3:1(다크 5.5:1)이라
  // 같은 비율로 바래면 빛살 끝이 배경 대비 1.3 안팎으로 사라진다. 그래서 라이트에서는 바래는 몫을 45%만 쓴다(30% → 69%, 55% → 80%)
  const colv = (n) => topicColour(topicOf(n)), mix = (c, p) => `color-mix(in srgb, ${c} ${themeMode() === 'light' ? Math.round(100 - (100 - p) * .45) : p}%, var(--c-card))`;
  const isMean = (n) => ['concept', 'doc', 'bridge'].includes(n.k) || n.m > 0;
  const titleOf = (n) => { if (n.k === 'topic') return n.path ? n.path.split('/').pop() : C.rootTopic; const s = (n.t || n.id).replace(/\s*\((?:\d{4}-\d\d-\d\d|pstack principle|Anthropic)[^)]*\)/g, '');
    return ['concept', 'doc', 'bridge'].includes(n.k) ? s.replace(/^([^:]{3,40}):.*$/, '$1') : s; };
  const short = (s, n) => (s.length > n ? s.slice(0, n - 1) + '…' : s);
  const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  const KIND = COPY.ui.compass.kinds;

  // ── 모양·빛살·표(문법) ─────────────────────────────────────────────────────────────────────
  const GAP = 4, P = (pts) => pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  const P2 = (pts) => pts.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(' ');   // 줄기 조각: 0.1 단위로 자르면 확대했을 때 호가 각진다
  const arcPts = (cx0, cy0, r, mid, half, n = 6) => { const o = []; for (let q = 0; q <= n; q++) { const a = mid - half + (2 * half * q) / n; o.push([cx0 + Math.cos(a) * r, cy0 + Math.sin(a) * r]); } return o; };
  function shape(kind, x, y, r, fill, hollow) {
    const st = hollow ? `fill="none" stroke="${fill}" stroke-width="1.6"` : `fill="${fill}"`;
    if (kind === 'doc') return `<g class="shape"><circle cx="${x}" cy="${y}" r="${r}" ${st}/><rect x="${x - r * .42}" y="${y - r * .12}" width="${r * .84}" height="${r * .24}" rx="${r * .1}" fill="var(--c-card)"/></g>`;
    if (kind === 'decision') { const d = r * 1.25; return `<path class="shape" d="M${x} ${y - d}L${x + d} ${y}L${x} ${y + d}L${x - d} ${y}Z" ${st}/>`; }
    if (kind === 'record') { const s = r * .9; return `<rect class="shape" x="${x - s}" y="${y - s}" width="${2 * s}" height="${2 * s}" rx="${r * .25}" ${st}/>`; }
    if (kind === 'episode') { const h = r * 1.2; return `<path class="shape" d="M${x} ${y - h}L${x + h * 1.05} ${y + h * .75}L${x - h * 1.05} ${y + h * .75}Z" ${st} stroke-linejoin="round"/>`; }
    // 다리는 범례와 같은 이음 표식(위아래 호 둘과 가운데 점)이다. 끝 주제 색을 아는 자리는 `junction` 이 그린다
    if (kind === 'bridge') { const k = r * .7, w = Math.max(1.6, r * .36).toFixed(2);
      return `<g class="shape"><path d="M${x - k} ${y - k}A${r} ${r} 0 0 1 ${x + k} ${y - k}M${x + k} ${y + k}A${r} ${r} 0 0 1 ${x - k} ${y + k}" fill="none" stroke="${fill}" stroke-width="${w}" stroke-linecap="round"/><circle cx="${x}" cy="${y}" r="${(r * .33).toFixed(2)}" fill="${fill}"/></g>`; }
    return `<circle class="shape" cx="${x}" cy="${y}" r="${r}" ${st}/>`;
  }
  // 빛살: 가운데 쪽이 굵고 바깥으로 가늘다. 양끝은 노드와 같은 중심의 원호로, 틈만큼 떨어져 잘린다
  function ray(a, b, i, w0 = 3, w1 = .5) {
    if (themeMode() === 'light') { w0 *= 1.45; w1 = Math.max(w1 * 1.6, 1); }   // 밝은 바탕에서는 가는 띠가 묻힌다: 라이트만 굵게, 바깥 끝도 1 아래로 가늘어지지 않게
    const th = Math.atan2(b.y - a.y, b.x - a.x), ra = a.r + GAP, rb = b.r + GAP;
    const pts = [...arcPts(a.x, a.y, ra, th, Math.asin(Math.min(.95, w0 / ra))), ...arcPts(b.x, b.y, rb, th + Math.PI, Math.asin(Math.min(.95, w1 / rb))).reverse()];
    const x0 = a.x + Math.cos(th) * ra, y0 = a.y + Math.sin(th) * ra, x1 = b.x - Math.cos(th) * rb, y1 = b.y - Math.sin(th) * rb;
    return { def: `<linearGradient id="r${i}" gradientUnits="userSpaceOnUse" x1="${x0}" y1="${y0}" x2="${x1}" y2="${y1}"><stop offset="0" style="stop-color:${mix(a.c, 85)}"/><stop offset="1" style="stop-color:${mix(b.c, 30)}"/></linearGradient>`,
             svg: `<polygon class="ray" data-a="${a.id}" data-b="${b.id}" points="${P(pts)}" style="fill:url(#r${i})"/>`, x0, y0, x1, y1 };
  }
  function bead(x, y, th, c, kind) {
    const p = (dx, dy) => [x + Math.cos(th) * dx - Math.sin(th) * dy, y + Math.sin(th) * dx + Math.cos(th) * dy], f = (q) => `${q[0].toFixed(1)} ${q[1].toFixed(1)}`;
    let d = `M${f(p(-1.5, -4))}L${f(p(2.5, 0))}L${f(p(-1.5, 4))}`, extra = '';
    if (kind === 'INHERITS_FROM') d += `M${f(p(-6, -4))}L${f(p(-6, 4))}`;
    if (kind === 'DERIVES_FROM') { const q = p(-6.5, 0); extra = `<circle cx="${q[0].toFixed(1)}" cy="${q[1].toFixed(1)}" r="2.2" fill="var(--c-card)" stroke="${c}" stroke-width="1.4"/>`; }
    return `<path class="ray" d="${d}" fill="none" stroke="${c}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>${extra}`;
  }

  // ── 이웃 모으기: 가운데가 노드일 때와 주제일 때 ──────────────────────────────────────────────
  // 나침반의 네 자리에 무엇이 가는가(가운데가 주제든 노드든 같은 규칙):
  //   12시 = 어디서 왔나(위 주제는 정확히 위, 원천·부모·앞선 것·붙은 곳·새것은 그 좌우로)
  //    9시 = 뜻의 관계(노드) / 이 주제에 직접 속한 개념(주제)
  //    3시 = 그 아래 구성(하위 주제와 그 사이를 잇는 다리) / 여기서 나온 것·물려받은 것·뒤의 것·옛것(노드)
  //    6시 = 줄기가 곧게 내려가 붙은 결정·기록·사건·문서의 목록으로(최신순)
  const bridgeEnds = (b) => adj.get(b.id).filter((e) => e.t === 'CONNECTS').map((e) => (e.a === b ? e.b : e.a));
  function gather(c) {
    const up = [], left = [], right = [], bridges = [], shelf = { decision: [], record: [], episode: [], doc: [] };
    if (c.k === 'topic') {
      // 뿌리 아래 첫 칸 주제의 부모는 가상 뿌리('')다. 뿌리 자신은 부모가 없다
      const parent = c.path === ROOT ? null : topics.get(c.path.includes('/') ? c.path.split('/').slice(0, -1).join('/') : '');
      if (parent) up.push({ n: parent, rel: C.rel.parentTopic, main: true });
      const childOf = (d) => (!d || d === c.path ? null : !c.path ? d.split('/')[0] : d.startsWith(c.path + '/') ? `${c.path}/${d.slice(c.path.length + 1).split('/')[0]}` : null);
      // 다리: 잇는 끝이 이 주제의 하위 주제 둘 이상이면 오른쪽 하위 주제 사이로, 아니면 왼쪽 개념 부채에
      for (const n of c.direct.filter(isMean)) {
        if (n.k === 'bridge') { const subs = [...new Set(bridgeEnds(n).map((m) => childOf(m.d)).filter(Boolean))]; if (subs.length >= 1) { bridges.push({ n, subs, outside: bridgeEnds(n).some((m) => !childOf(m.d)) }); continue; } }
        left.push({ n, rel: C.rel.concept });
      }
      left.sort((a, b) => b.n.m - a.n.m || b.n.g - a.n.g);
      for (const n of c.direct.filter((x) => !isMean(x))) (shelf[n.k] || shelf.record).push(n);
      for (const p of [...c.sub].sort((a, b) => topics.get(b).count - topics.get(a).count)) right.push({ n: topics.get(p), rel: C.rel.subtopic });
    } else {
      up.push({ n: topics.get(c.d), rel: C.rel.topic, main: true });
      for (const e of adj.get(c.id)) {
        const o = e.a === c ? e.b : e.a, out = e.a === c;
        if (ATTACH.has(e.t)) { if (out) up.push({ n: o, rel: C.rel.attachedTo, e }); else (shelf[o.k] || shelf.record).push(o); continue; }
        if (e.t === 'SUPERSEDES') { (out ? right : up).push({ n: o, rel: out ? C.rel.replacedOld : C.rel.replacedNew, e }); continue; }
        if (e.t === 'DERIVES_FROM' || e.t === 'INHERITS_FROM') { (out ? up : right).push({ n: o, rel: e.t === 'DERIVES_FROM' ? (out ? C.rel.source : C.rel.derived) : (out ? C.rel.parent : C.rel.inherited), e }); continue; }
        if (e.t === 'PREREQUISITE_OF') { (out ? right : up).push({ n: o, rel: out ? C.rel.next : C.rel.prev, e }); continue; }
        left.push({ n: o, rel: e.t, e });
      }
      left.sort((a, b) => (b.n.m || 0) - (a.n.m || 0));
    }
    for (const k of Object.keys(shelf)) shelf[k].sort((a, b) => (b.ts || '').localeCompare(a.ts || ''));
    // 자르지 않고 다 돌려준다: 부채 크기는 compassOf 가 기간 · 종류로 거른 **뒤에** 자른다(지나온 길의 노드는 자르지 않는다).
    return { up, left, right, bridges, shelf };
  }

  // ── 펼침: 네 방향 십자 ─────────────────────────────────────────────────────────────────────
  // 층마다 12시는 부모(온 곳) 쪽, 3시는 하위 주제, 6시는 바깥(목록), 9시는 개념이다. 층은 화면에서 위·아래·왼쪽·오른쪽
  // 넷 중 한 방향으로만 서서(0·90·180·270°) 호나 나선이 생기지 않는다. 맨 위 주제의 하위 주제는 네 팔로 나뉘고, 펼친 층은
  // 부모에서 그 팔 방향으로 곧게 나간다. 위·아래로 서는 부채는 가로 이름표의 두 줄 목록으로 편다.
  const svg = $id('g');
  const ROWS = 18;
  const REL = { up: [-26, 26], left: [-135, -45], right: [45, 135] };   // 12시 방향(θ12)에서 잰 각도(도)
  const rad = (d) => d * Math.PI / 180, todeg = (r) => r * 180 / Math.PI;
  let path = [], prevPos = new Map(), LZ = null, VIEW = null, WIDE = false, KEEPCAM = false, PINCHAIN = new Set();
  let TPOS = null, CANONL = null, ARMS_OF = null;
  let ARMS_FIXED = false, measIdle = 0;   // ARMS_FIXED: 잰 크기로 팔을 골랐다. 그 뒤로는 팔을 바꾸지 않는다
  // 배치 한 번이 읽는 상태(S). 그리기는 지금 상태로(curS), 재기 · 정해진 자리 짓기는 따로 만든 S 로 부른다: 전역(path · TPOS ·
  // RANGE · KOFF)을 바꿨다 되돌리지 않는다. place: 주제 층의 허브를 이 표의 자리에 둔다(하나씩 열기의 묶음 배치).
  // dirs: 부모 기준으로 놓는 층의 방향(th12)을 읽는 표. canon: 정해진 자리를 처음 짓는 중(거르기 · 밀어내기를 하지 않는다)
  const curS = () => ({ mode: 'path', path, place: null, dirs: TPOS, canon: false, range: RANGE, koff: KOFF });
  const MORE = new Map();   // '허브|종류' → 목록에 더 보인 줄 수(긴 목록은 20개씩 더 연다)
  const KOFF = new Set();   // 범례에서 잠시 끈 종류. 저장하지 않는다: 그래프를 열 때마다 모두 켠 채 시작한다
  const kindKey = (n) => (n.k === 'decision' || n.k === 'record' || n.k === 'episode' || n.k === 'bridge' ? n.k : n.k === 'doc' ? 'doc' : 'concept');   // 범례의 종류 키(끄고 켜는 단위)
  let RANGE = null;         // [시작, 끝] 날짜 문자열(YYYY-MM-DD). 없으면 전체 기간
  const fullDate = (s) => (!s ? '' : s.length === 4 ? s + '-01-01' : s.length === 7 ? s + '-01' : s.slice(0, 10));   // '2026' · '2026-03' 같은 덜 적힌 날짜는 그 첫날로
  // 날짜 = 그 일이 있었던 날: 내용 시간 → 유효 시작(→ 사건은 그날). 마지막으로 고친 때만 있는 노드는 날짜가 없는 것으로 본다(막대에 넣지 않고 흐리지도 않는다).
  const when = (n) => fullDate(n.ct || n.vf || (n.k === 'episode' ? n.ts : '') || '');
  const inRange = (n, rg = RANGE) => { const w = when(n); return !rg || !w || (w >= rg[0] && w <= rg[1]); };
  function fanPlace(list, center, a0, a1, rMin, gap, size) {
    const need = list.reduce((s, u) => s + 2 * size(u) + gap, 0), span = rad(a1 - a0), R = Math.max(rMin, need / span);
    let acc = rad(a0) + (span - need / R) / 2;
    return { R, pos: list.map((u) => { const w = (2 * size(u) + gap) / R, mid = acc + w / 2; acc += w; return { u, ang: mid, x: center.x + Math.cos(mid) * R, y: center.y + Math.sin(mid) * R }; }) };
  }
  const subR = (n) => Math.max(9, Math.min(24, 4 + 1.5 * Math.sqrt(n.count || 1)));
  const sizeU = (u) => (u.n.k === 'topic' ? subR(u.n) : isMean(u.n) ? 7 : 5.5);
  // 위·아래로 서는 부채(가운데 각이 거의 수직)는 두 줄 목록으로 편다. 부채 그대로면 이름표가 세로로 서서 읽기 어렵다.
  // side: -1 왼쪽 줄만, 1 오른쪽 줄만, 0 반씩 양쪽. 두 줄 사이는 비워 둔다(부모·자식 선이 그 사이로 지나간다)
  function colPlace(list, center, cDeg, side, size) {
    const s = Math.sin(rad(cDeg)) > 0 ? 1 : -1, n = list.length, nl = side === 0 ? Math.ceil(n / 2) : side < 0 ? n : 0, r0 = (center.r || 22) + 34, acc = [r0, r0];
    const pos = list.map((u, i) => { const L = i < nl ? 0 : 1, h = Math.max(22, 2 * size(u) + 8), d = acc[L] + h / 2; acc[L] += h;
      return { u, ang: L === 0 ? Math.PI : 0, x: center.x + (L === 0 ? -1 : 1) * (40 + size(u)), y: center.y + s * d }; });
    return { R: Math.max(acc[0], acc[1]), pos };
  }
  // 한 층(나침반 하나)의 내용. 부모는 이미 12시에 있으므로 뺀다
  function compassOf(node, parentId, S = curS()) {
    const G = gather(node), drop = (l) => l.filter((u) => u.n.id !== parentId);
    G.up = drop(G.up); G.left = drop(G.left); G.right = drop(G.right);
    // 기간: 시간 막대에서 고른 구간 밖의 노드는 뺀다(주제와 지나온 길의 노드는 남긴다)
    const keep = (n) => S.canon || n.k === 'topic' || S.path.some((q) => q.id === n.id) || PINS.has(n.id) || (inRange(n, S.range) && !S.koff.has(kindKey(n)));
    G.up = G.up.filter((u) => keep(u.n)); G.left = G.left.filter((u) => keep(u.n)); G.right = G.right.filter((u) => keep(u.n));
    for (const k of Object.keys(G.shelf)) G.shelf[k] = G.shelf[k].filter(keep);
    G.bridges = G.bridges.filter((b) => keep(b.n));
    // 부채가 받는 수: 위 11 · 왼쪽 20 · 오른쪽 16. 지나온 길의 노드와 하위 주제는 넘쳐도 남긴다(잘리면 그 노드를 열 수 없고,
    // 정해진 자리를 잴 때 주제가 빠진다). 순서는 그대로다
    const onPathU = (u) => S.path.some((q) => q.id === u.n.id);
    const cap = (l, n, must) => { if (l.length <= n) return l; let room = n - l.slice(n).filter(must).length; return l.filter((u) => must(u) || room-- > 0); };
    G.up = cap(G.up, 11, onPathU); G.left = cap(G.left, 20, onPathU); G.right = cap(G.right, 16, (u) => onPathU(u) || u.n.k === 'topic');
    const probe = { x: 0, y: 0, r: 22 };
    const rL = G.left.length ? Math.max(fanPlace(G.left, probe, 0, REL.left[1] - REL.left[0], 150, 9, sizeU).R, colPlace(G.left, probe, 90, 0, sizeU).R) : 0;
    const shelfN = Object.values(G.shelf).reduce((x, l) => x + l.length, 0);
    const rR = G.right.length ? Math.max(fanPlace(G.right, probe, 0, REL.right[1] - REL.right[0], 170, 40, sizeU).R, colPlace(G.right, probe, 90, 0, sizeU).R) : 0;
    const E = Math.max(rL, rR, 120) + 70;
    // 이 층이 차지하는 둘레: 나침반(E)과 목록(6시 쪽으로 늘어지거나 옆으로 퍼진다). 정해진 자리를 잴 때 층끼리 이만큼 띄운다
    const ncol = ['decision', 'record', 'episode', 'doc'].filter((k) => G.shelf[k].length).length, rows = Math.min(ROWS, Math.max(0, ...Object.values(G.shelf).map((l) => l.length)));
    const FP = ncol ? Math.max(E, E * .6 + ncol * 210, E * .7 + 46 + rows * 17 + 30) : E;
    return { node, G, rL, rR, shelfN, E, FP };
  }
  // 펼친 나무: 펼칠 노드들(expand: 부모 id → 펼칠 자식 id 들)을 BFS 로 놓는다. 펼친 노드는 부모 부채의 자기 자리에서 바깥으로 빠져나와
  // 같은 나침반을 편다. 부모 기준으로 놓은 층은 앞서 놓인 층의 항목(이름표까지)과 겹치지 않을 때까지 자기 방향으로 더 나간다(settle). 목록은 focus 층만 펴고 나머지는 수로 접는다.

  function layoutTree(root, expand, focusId, S = curS()) {
    const items = [], comps = [], placed = new Map(), onPath = new Set(S.path.map((n) => n.id)), queued = new Set([root.id]);
    const mk = (n, x, y, r, extra) => { const o = { id: n.id, src: n, k: n.k, x, y, r, c: colv(n), label: titleOf(n), ...extra }; items.push(o); placed.set(n.id, o); return o; };
    const queue = [{ node: root, parent: null }];
    // 겹침 풀기: 부모 기준으로 놓은 층이 앞서 놓인 어느 층(부모 · 형제 · 지나온 층 · 고정 층)과 겹치면, 자기 방향으로 한 칸씩 밀어낸다.
    // 상자는 항목마다 따로 잰다(노드와 이름표 끝까지, extentPts). 층 전체를 상자 하나로 보면 비스듬한 부채의 빈 모서리까지 막혀
    // 이미 떨어진 층도 밀려난다. 지금 층(focus)은 6시 목록이 설 자리까지 센다
    const own = (c) => [c.hub, ...c.items.filter((o) => o.id && !o.link && o.comp === c.hub.id)];
    const boxOf = (o) => { let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      for (const [x, y] of extentPts(o)) { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); } return [x0, y0, x1, y1]; };
    const boxes = (c) => { const out = own(c).map(boxOf);
      if (c.stub) out.push([c.stub.x - 20, c.stub.y - 14, c.stub.x + 170, c.stub.y + 14]);
      if (c.focus && S.mode !== 'all' && c.kinds && c.kinds.length) { const a = rad(c.th12 + 180), ux = Math.cos(a), uy = Math.sin(a), d = Math.max(c.cp.FP, c.hub.r + 80), w = Math.max(c.cp.E, c.kinds.length * 105 + 60);
        const xs = [-1, 1].map((k) => c.hub.x + ux * d - uy * w * k), ys = [-1, 1].map((k) => c.hub.y + uy * d + ux * w * k), x0 = c.hub.x + ux * (c.hub.r + 30), y0 = c.hub.y + uy * (c.hub.r + 30);
        out.push([Math.min(x0, ...xs), Math.min(y0, ...ys), Math.max(x0, ...xs), Math.max(y0, ...ys)]); }
      return out; };
    const hit = (A, B, m = 12) => A.some((a) => B.some((b) => a[0] < b[2] + m && b[0] < a[2] + m && a[1] < b[3] + m && b[1] < a[3] + m));
    const shift = (c, dx, dy) => { for (const o of own(c)) { o.x += dx; o.y += dy; } if (c.stub) { c.stub.x += dx; c.stub.y += dy; } };
    const settle = (c, dir) => { const ux = Math.cos(dir), uy = Math.sin(dir); let mine = boxes(c); const others = comps.map(boxes);
      for (let k = 0; k < 80 && others.some((B) => hit(mine, B)); k++) { shift(c, ux * 60, uy * 60); mine = boxes(c); }
      if (c.hub.route) c.hub.route[c.hub.route.length - 1] = [c.hub.x, c.hub.y]; };   // 마지막 토막은 팔 방향이라 끝점만 옮기면 곧게 남는다
    while (queue.length) {
      const { node, parent } = queue.shift(); const i = comps.length, kids = expand.get(node.id) || new Set();
      const ck = S.cps && `${node.id}|${parent ? parent.hub.id : ''}`, cp = (ck && S.cps.get(ck)) || compassOf(node, parent ? parent.hub.id : null, S), focus = node.id === focusId;
      if (ck) S.cps.set(ck, cp);
      // 고정 노드로 가는 길에만 있는 층(지금 길 밖): 다음 사슬 노드와 고정 노드만 남긴다(길만 잇는다)
      const pinOnly = S.mode !== 'all' && !onPath.has(node.id) && PINCHAIN.has(node.id);
      if (pinOnly) { const ok = (u) => kids.has(u.n.id) || PINS.has(u.n.id); cp.G.up = cp.G.up.filter(ok); cp.G.left = cp.G.left.filter(ok); cp.G.right = cp.G.right.filter(ok); cp.G.bridges = [];
        for (const k of Object.keys(cp.G.shelf)) cp.G.shelf[k] = cp.G.shelf[k].filter((n) => PINS.has(n.id)); cp.shelfN = 0; }
      let hub, th12, free = null;   // free: 정해진 자리 없이 부모 기준으로 놓은 층이면 밀어낼 방향(라디안)
      const T0 = !S.canon && S.place && parent ? S.place.get(node.id) : null;
      if (!parent) { hub = mk(node, 0, 0, node.k === 'topic' ? 24 : 15, { role: 'hub', level: 0 }); th12 = -90; }
      else if (T0) { const slot = parent.slots.get(node.id);   // 정해진 자리(어떤 길로 와도 같다)
        hub = mk(node, T0.x, T0.y, 22, { role: 'hub', level: i, parentHub: parent.hub, viaE: slot && slot.e, route: T0.route && [...T0.route] }); th12 = T0.th12;
        if (S.mode !== 'all') free = rad(th12 + 180); }   // 묶음이 모르는 층(주제가 아닌 노드로 들어간 층)과 겹치면 팔 방향으로 비킨다
      else {
        const slot = parent.slots.get(node.id) || { ang: rad(parent.th12 + 180), r: parent.cp.E - 70 };
        const Tc = S.dirs && S.dirs.get(node.id), Tp = S.dirs && S.dirs.get(parent.hub.id), extra = { role: 'hub', level: i, parentHub: parent.hub, viaE: slot.e };
        if (parent.slots.size > 1 && Tc && Tp) {
          // 한 층에서 여럿을 펼치면(고정한 노드로 가는 길이 갈라질 때) 전체 배치에서의 상대 자리를 그대로 쓴다: 형제가 한 자리에 겹치지 않는다
          hub = mk(node, parent.hub.x + Tc.x - Tp.x, parent.hub.y + Tc.y - Tp.y, 22, extra); th12 = Tc.th12;
          free = Math.atan2(hub.y - parent.hub.y, hub.x - parent.hub.x);
        } else {
          // 펼친 층은 부모에서 팔 방향(부모의 6시 쪽)으로 곧게 나간다. 주제가 아닌 노드는 부모 부채에서의 자리를 가까운 네 방향으로 맞춘다.
          // 다만 온 쪽(부모의 12시)으로는 열지 않는다(앞 층 위로 되돌아가 겹친다). 다리도 하위 주제 부채 한가운데가 아니라 앞(6시 쪽)으로 연다.
          // 층 사이: 위·아래로 나가면 두 나침반 반지름의 3/4, 가로로 나가면 6할에 이름표 폭만큼 더. 그래도 겹치면 아래 settle 이 밀어낸다
          let ang = Tc ? rad(Tc.th12 + 180) : Math.round(slot.ang / (Math.PI / 2)) * (Math.PI / 2);
          const back = rad(parent.th12), near = (a, b) => Math.abs(Math.atan2(Math.sin(a - b), Math.cos(a - b))) < Math.PI / 4;
          if (!Tc && (node.k === 'bridge' || near(ang, back))) ang = rad(parent.th12 + 180);
          const dist = Math.abs(Math.cos(ang)) > .5 ? (parent.cp.E + cp.E) * .62 + 320 : (parent.cp.E + cp.E) * .75 + 40;
          hub = mk(node, parent.hub.x + Math.cos(ang) * dist, parent.hub.y + Math.sin(ang) * dist, node.k === 'topic' ? 22 : 14, extra);
          th12 = todeg(ang) + 180; free = ang;
        }
      }
      const chain = parent ? [...parent.chain, node] : [node];
      hub.chain = chain;
      const comp = { cp, hub, th12, slots: new Map(), focus, level: i, items: [], chain, parent, pinOnly, free };
      const put = (p, role, extra = {}) => { const r = Math.hypot(p.x - hub.x, p.y - hub.y);
        // 지나온 길의 노드는 반드시 자기 층(허브)으로 펼쳐져야 한다. 앞 층의 부채에 먼저 나와도 여기서 그리지 않고, 나중에 그 허브로 빛살만 잇는다.
        // (이걸 안 하면 길의 노드가 앞 층의 잎으로 먼저 놓여 펼쳐지지 못하고, 지금 층을 잃어 맨 위 vinc 로 돌아간 것처럼 보였다)
        if (onPath.has(p.u.n.id) && !kids.has(p.u.n.id)) { comp.items.push({ linkId: p.u.n.id, e: p.u.e }); return; }
        if (kids.has(p.u.n.id) && queued.has(p.u.n.id)) { comp.items.push({ linkId: p.u.n.id, e: p.u.e }); return; }   // 같은 노드가 두 관계로 두 번 나오면 한 번만 편다
        if (kids.has(p.u.n.id) && !placed.has(p.u.n.id)) { queued.add(p.u.n.id); comp.slots.set(p.u.n.id, { ang: Math.atan2(p.y - hub.y, p.x - hub.x), r, e: p.u.e }); queue.push({ node: p.u.n, parent: comp }); return; }
        if (placed.has(p.u.n.id)) { comp.items.push({ link: placed.get(p.u.n.id), e: p.u.e }); return; }
        comp.items.push(mk(p.u.n, p.x, p.y, sizeU(p.u), { role, rel: p.u.rel, e: p.u.e, ang: p.ang, level: i, comp: hub.id, chain, ...(p.u.label ? { label: p.u.label, farEnds: p.u.farEnds } : {}), ...extra })); };
      const take = (list, a0, a1, rMin, gap, role) => { if (!list.length) return; const c = th12 + (a0 + a1) / 2, cc = Math.cos(rad(c));
        const P = Math.abs(Math.sin(rad(c))) > .7 ? colPlace(list, hub, c, Math.abs(cc) < .05 ? 0 : cc < 0 ? -1 : 1, sizeU) : fanPlace(list, hub, th12 + a0, th12 + a1, rMin, gap, sizeU);
        P.pos.forEach((p) => put(p, role)); };
      const G = cp.G;
      const ups = G.up; take(ups.slice(0, Math.ceil(ups.length / 2)), REL.up[0], -8, 190, 60, 'up'); take(ups.slice(Math.ceil(ups.length / 2)), 8, REL.up[1], 190, 60, 'up');
      take(G.left, ...REL.left, cp.rL || 150, 9, 'left');
      take(G.right, ...REL.right, cp.rR || 170, 40, 'right');
      // 다리: 이 층의 하위 주제들 사이(펼쳐진 하위 주제는 그 자리 각도로 센다)
      const angOf = new Map([...comp.items.filter((o) => o.role === 'right').map((o) => [o.id, Math.atan2(o.y - hub.y, o.x - hub.x)]), ...[...comp.slots].map(([id, s]) => [id, s.ang])]);
      const posOf = new Map([...comp.items.filter((o) => o.role === 'right').map((o) => [o.id, [o.x, o.y]]), ...[...comp.slots].map(([id, s]) => [id, [hub.x + Math.cos(s.ang) * s.r, hub.y + Math.sin(s.ang) * s.r]])]);
      for (const b of G.bridges) { const angs = b.subs.map((s) => angOf.get(`topic:${s}`)).filter((a) => a != null); if (!angs.length || placed.has(b.n.id)) continue;
        if (onPath.has(b.n.id) && !kids.has(b.n.id)) continue;
        let a = Math.atan2(angs.reduce((s, x) => s + Math.sin(x), 0), angs.reduce((s, x) => s + Math.cos(x), 0)), r = Math.max(hub.r + 70, (cp.rR || 170) - 90);
        // 3시가 위·아래로 서서 하위 주제가 두 줄 목록이면, 다리는 두 줄 사이 빈 길(3시 축) 위, 잇는 하위 주제들의 가운데 거리에 선다
        const c3 = rad(th12 + 90); if (Math.abs(Math.sin(c3)) > .7) { const ds = b.subs.map((x) => posOf.get(`topic:${x}`)).filter(Boolean).map(([x, y]) => (x - hub.x) * Math.cos(c3) + (y - hub.y) * Math.sin(c3));
          if (ds.length) { a = c3; r = ds.reduce((s, x) => s + x, 0) / ds.length; } }
        // 다리끼리 이름표가 겹치지 않게: 겹치면 안쪽으로 한 칸, 더 들어갈 수 없으면 옆으로 조금 돈다
        const bw = (o) => 7 * Math.min(20, (o.label || '').replace(/ junction$/i, '').length) + 16, myW = 7 * Math.min(20, titleOf(b.n).replace(/ junction$/i, '').length) + 16;
        const busy = () => { const x = hub.x + Math.cos(a) * r, y = hub.y + Math.sin(a) * r;
          return items.some((o) => o.role === 'bridge' && Math.abs(o.x - x) < (bw(o) + myW) / 2 && Math.abs(o.y - y) < 30) || items.some((o) => o.role === 'right' && o.comp === hub.id && Math.hypot(o.x - x, o.y - y) < o.r + 26); };
        const lane = Math.abs(Math.sin(c3)) > .7 && a === c3;
        for (let k = 0; k < 16 && busy(); k++) { if (lane) r += 26; else if (r - 26 > hub.r + 60) r -= 26; else a += .14; }
        if (kids.has(b.n.id)) { if (queued.has(b.n.id)) continue; queued.add(b.n.id); comp.slots.set(b.n.id, { ang: a, r }); queue.push({ node: b.n, parent: comp }); continue; }   // 다리를 눌렀으면 그 자리에서 다리의 나침반을 편다
        comp.items.push(mk(b.n, hub.x + Math.cos(a) * r, hub.y + Math.sin(a) * r, 8, { role: 'bridge', subs: b.subs.map((s) => `topic:${s}`), outside: b.outside, ang: a, level: i, comp: hub.id, chain })); }
      // 다음 층이 6시 목록(결정 · 기록 · 사건 · 문서)에 있으면 6시 쪽으로 층을 연다. 부채에서만 찾으면 목록의 노드는 가운데로 오지 못했다
      for (const k of Object.keys(G.shelf)) G.shelf[k] = G.shelf[k].filter((n) => {
        if (!kids.has(n.id) || queued.has(n.id) || placed.has(n.id)) return true;
        queued.add(n.id); comp.slots.set(n.id, { ang: rad(th12 + 180), r: hub.r + 46 }); queue.push({ node: n, parent: comp }); return false; });
      const kinds = [['decision', KIND.decision], ['record', KIND.record], ['episode', KIND.episode], ['doc', KIND.doc]].filter(([k]) => G.shelf[k].length);
      comp.kinds = kinds.map(([k, name]) => ({ k, name, all: G.shelf[k] }));
      if (!focus && kinds.length && S.mode !== 'all') { const a = rad(th12 + 180 + (comp.slots.size ? 22 : 0)), r = hub.r + 46;   // 펼친 층으로 가는 선(6시)이 있으면 조금 비켜 둔다
        comp.stub = { x: hub.x + Math.cos(a) * r, y: hub.y + Math.sin(a) * r, text: kinds.map(([k, name]) => `${name} ${G.shelf[k].length}`).join(' · '), ang: a }; }
      if (free != null && !S.canon) settle(comp, free);
      comps.push(comp);
    }
    // 미뤄 둔 빛살을 실제 허브에 잇는다
    for (const c of comps) c.items = c.items.map((o) => (o.linkId ? (placed.has(o.linkId) && placed.get(o.linkId) !== c.hub ? { link: placed.get(o.linkId), e: o.e } : null) : o)).filter(Boolean);
    const F = comps.find((c) => c.focus) || comps[comps.length - 1];
    if (!F) return { items, comps, focus: comps[0], shelves: [], sets: [], Q: null, BY: 0, mode: 'none', side: 1, a6: 0 };
    // 층마다의 목록(결정·기록·사건·문서). 지금 층에만 단다(정해진 자리를 잴 때는 목록이 있는 모든 층)
    const setFor = (F) => {
    // 지금 층의 목록: 6시가 아래쪽이면 아래에 좌우 대칭으로, 옆쪽이면 그쪽으로 가로로 늘어놓는다(글자는 늘 바로 선다)
    let a6 = rad(F.th12 + 180); const down = Math.sin(a6) > .94; let side = Math.cos(a6) >= 0 ? 1 : -1;   // 거의 곧게 아래일 때만 아래 버스, 아니면 6시 방향으로 비스듬히 나가 옆 버스
    // 목록이 부채의 이름표와 겹치지 않게: 이름표가 닿는 끝(노드 바깥 약 160px)까지 재서 그 바깥에 둔다
    const reach = (o) => { const a = o.ang ?? 0, L = o.r + (o.role === 'right' && o.k === 'topic' ? 120 : 160); return [o.x + Math.cos(a) * L, o.y + Math.sin(a) * L]; };
    const ends = F.items.filter((o) => !o.link).flatMap((o) => [[o.x, o.y], reach(o)]);
    const ext = down ? Math.max(F.hub.r, ...ends.map(([x, y]) => y - F.hub.y)) + 50 : Math.max(F.hub.r, ...ends.map(([x]) => side * (x - F.hub.x))) + 50;
    const COLW = 210, ROW = 17; const shelves = [];
    let mode, Q;
    if (down) { mode = 'down'; Q = { x: F.hub.x, y: F.hub.y + ext }; const x0 = Q.x - (F.kinds.length - 1) * COLW / 2;
      F.kinds.forEach((s, i) => shelves.push({ ...s, x: x0 + i * COLW })); }
    else { mode = 'side';
      // 6시 방향(두 부채 사이의 빈 틈)으로 곧게 나가 부채의 이름표 바깥에서 꺾는다. 그 점이 버스의 시작이다
      const along = Math.max(F.hub.r + 60, ...ends.map(([x, y]) => (x - F.hub.x) * Math.cos(a6) + (y - F.hub.y) * Math.sin(a6))) + 40;
      Q = { x: F.hub.x + Math.cos(a6) * along, y: F.hub.y + Math.sin(a6) * along, a6 };
      // 목록은 버스(Q 높이)에서 아래로 늘어진다. 그 높이 안에 걸친 부채 이름표가 있으면 그 끝보다 더 옆에서 시작한다.
      // 6시가 거의 위·아래로 서면(옆이 정해지지 않으면) 걸리는 것이 적은 쪽으로 보낸다.
      const rows = Math.max(1, ...F.kinds.map((k) => Math.min(k.all.length, ROWS + (MORE.get(`${F.hub.id}|${k.k}`) || 0)))), yTop = Q.y - 12, yBot = Q.y + 46 + rows * ROW + 12;
      const hubLab = [[F.hub.x - F.hub.r - 60, F.hub.y + F.hub.r + 24], [F.hub.x + F.hub.r + 60, F.hub.y + F.hub.r + 24], [F.hub.x - F.hub.r - 10, F.hub.y], [F.hub.x + F.hub.r + 10, F.hub.y]];   // 허브 자신과 그 이름표
      const inBand = ends.concat(F.items.filter((o) => !o.link).map((o) => [o.x, o.y]), hubLab).filter(([, y]) => y >= yTop && y <= yBot);
      const reachOn = (sd) => Math.max(0, ...inBand.map(([x]) => sd * (x - Q.x)));
      // 6시가 거의 위·아래로 서면 옆을 정할 수 없다: 9시(개념) 쪽으로 꺾는다. 3시 쪽은 하위 주제로 가는 선이 지나간다
      let sd = side; if (Math.abs(Math.cos(a6)) < .35) sd = Math.cos(rad(F.th12 - 90)) >= 0 ? 1 : -1;
      side = sd;   // 그리기(buildTree)가 같은 쪽으로 버스를 긋는다
      // 거의 선 6시는 곧게 세운다: 비스듬히 안쪽으로 올라갔다가 바깥으로 되돌아 꺾는 군더더기를 없앤다
      if (Math.abs(Math.cos(a6)) < .35) { a6 = Math.sin(a6) < 0 ? -Math.PI / 2 : Math.PI / 2; Q = { x: F.hub.x, y: F.hub.y + Math.sin(a6) * along, a6 }; }
      const off = Math.max(40, reachOn(sd) + 36);
      F.kinds.forEach((s, i) => shelves.push({ ...s, x: sd > 0 ? Q.x + off + i * COLW : Q.x - off - (F.kinds.length - i) * COLW + 20 })); }
    const BY = Q.y, TOP = BY + 46;
      for (const s of shelves) s.items = s.all.filter((n) => !placed.has(n.id)).slice(0, ROWS + (MORE.get(`${F.hub.id}|${s.k}`) || 0)).map((n, j) => mk(n, s.x + 26, TOP + j * ROW, 5, { role: 'shelf', level: F.level, comp: F.hub.id, chain: F.chain })).filter(Boolean);
      return { hub: F.hub, comp: F, shelves, Q, BY, mode, side, a6 };
    };
    const fset = setFor(F), sets = [fset];
    // 지금 층의 목록은 층을 다 놓은 뒤에 선다. 그 목록이 앞서 놓인 층에 닿으면 지금 층을 목록째 자기 방향으로 더 민다
    if (S.mode !== 'all' && !S.canon && F.free != null) {
      const ux = Math.cos(F.free) * 60, uy = Math.sin(F.free) * 60, list = () => fset.shelves.flatMap((sh) => sh.items), others = comps.filter((c) => c !== F).map(boxes);
      for (let k = 0; k < 80 && others.some((B) => hit([...boxes(F), ...list().map(boxOf)], B)); k++) { shift(F, ux, uy); for (const o of list()) { o.x += ux; o.y += uy; }
        for (const sh of fset.shelves) sh.x += ux; fset.Q = { ...fset.Q, x: fset.Q.x + ux, y: fset.Q.y + uy }; fset.BY += uy; }
      if (F.hub.route) F.hub.route[F.hub.route.length - 1] = [F.hub.x, F.hub.y]; }
    if (S.mode === 'all') for (const c of comps) if (c !== F && !c.pinOnly && c.kinds.length) sets.push(setFor(c));
    return { items, comps, focus: F, shelves: fset.shelves, sets, Q: fset.Q, BY: fset.BY, mode: fset.mode, side: fset.side, a6: fset.a6, boxes };
  }

  // ── 그리기 ──────────────────────────────────────────────────────────────────────────────
  const ringS = (r, col, w) => topicRing(r, col, w, 'shape');   // 주제 고리: 범례 · 고정 칩 · 사이드바와 같은 함수(view-tint.ts)
  // 펼칠 것: path = 지나온 길만, all = 모든 주제(+ 지나온 길)
  function expandMap() { const m = new Map(), add = (a, b) => { if (!m.has(a)) m.set(a, new Set()); m.get(a).add(b); };
    for (let i = 1; i < path.length; i++) add(path[i - 1].id, path[i].id);
    // 고정한 노드까지의 길: 그 노드의 주제 사슬을 펼친다(고정 노드 자신은 잎으로 남는다)
    PINCHAIN = new Set();
    for (const id of PINS) { const n = byAny(id); if (!n) continue; const ch = chainTo(n);
      for (let i = 1; i < ch.length - 1; i++) { add(ch[i - 1].id, ch[i].id); PINCHAIN.add(ch[i].id); } }
    return m; }
  // 노드 하나가 화면에서 차지하는 점들(이름표까지). 카메라와 전체 배치의 층 크기 재기가 같이 쓴다
  const extentPts = (o) => { if (o.role === 'shelf') return [[o.x - 30, o.y - 40], [o.x + 200, o.y + 12]];
      if (o.pinned) return [[o.x - 60, o.y - 50], [o.x + 180, o.y + 50]];   // 고정 노드: 이름표까지 들어오게
      // 이름표가 실제로 차지하는 만큼만 잡는다: 가로로 쓰는 고리 주제는 옆으로 약 110px·위아래로 30px, 비스듬한 부채 이름은 이름 길이만큼
      if (o.role === 'hub') return o.k === 'topic' ? [[o.x - o.r - 30, o.y - o.r - 10], [o.x + o.r + 30, o.y + o.r + 40]] : [[o.x - o.r - 170, o.y - o.r - 20], [o.x + o.r + 20, o.y + o.r + 30]];
      const a = o.ang ?? 0;
      // 하위 주제 이름은 기울이지 않고 가로로 쓴다(수 배지까지): 부채 방향이 아니라 왼쪽이나 오른쪽으로 길어진다
      if (o.role === 'right' && o.k === 'topic') { const s = Math.cos(a) < 0 ? -1 : 1, L = o.r + 12 + 6.6 * (Math.min(18, o.label.length) + 4); return [[o.x - o.r, o.y - o.r - 4], [o.x + o.r, o.y + o.r + 4], [o.x + s * L, o.y]]; }
      const L = o.r + 12 + 6.6 * Math.min(24, o.label.length); return [[o.x - o.r, o.y - o.r], [o.x + o.r, o.y + o.r], [o.x + Math.cos(a) * L, o.y + Math.sin(a) * L]]; };
  // 옮겨 가는 움직임(FLIP)의 프레임과 끝난 뒤 가지를 키우는 타이머. 새 render 와 destroy 가 지운다: 늦게 온 타이머가 새 배치를 지난 배치의 가지로 덮지 않게
  let flipRaf = 0, flipTo = 0;
  let render = function () {
    cancelAnimationFrame(flipRaf); clearTimeout(flipTo);
    const S0 = curS(), ex = expandMap(), fid = path[path.length - 1].id; let L = layoutTree(path[0], ex, fid, S0);
    { const place = packPath(L); if (place) L = layoutTree(path[0], ex, fid, { ...S0, place }); }
    L = pinLayout(L);
    const F = L.focus;
    for (const c of L.comps) c.far = c !== F; LZ = { hub: L.focus.hub, ...L }; let defs = '', rays = '', nodesS = '', deco = '', i0 = 0;
    const drawRay = (inner, outer, e, w0, w1 = .5, cls = '') => { const r = ray(inner, outer, i0++, w0, w1); defs += r.def; rays += cls ? r.svg.replace('class="ray"', `class="ray ${cls}"`) : r.svg;
      if (e && ['PREREQUISITE_OF', 'DERIVES_FROM', 'INHERITS_FROM', 'RETURNS_TO'].includes(e.t)) {
        const A = e.a.id === inner.id ? inner : outer, B = A === inner ? outer : inner, src = e.t === 'PREREQUISITE_OF' || e.t === 'RETURNS_TO' ? A : B, dst = src === A ? B : A;
        rays += bead((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2, Math.atan2(dst.y - src.y, dst.x - src.x), mix(src.c, 90), e.t).replace(/class="ray"/, `class="ray ${cls}" data-a="${A.id}" data-b="${B.id}"`); } };
    const at = new Map(L.items.map((o) => [o.id, o]));
    // 묶음 배치의 부모 선: 상자 사이의 빈 길(줄기·통로)을 따라가되, 꺾는 곳마다 양옆 구간의 절반까지 굴려 곧은 토막 없이
    // 한 줄기 곡선으로 그린다. 지나가는 자리는 통로 안이라 다른 층의 상자를 거의 지나지 않는다. 양끝은 두 허브의 둘레에서 자른다
    const routeS = (pts, h, p, cls = 'path') => { const q = pts.map(([x, y]) => [x, y]), n = q.length, f = (a) => `${a[0].toFixed(1)} ${a[1].toFixed(1)}`;
      const unit = (a, b) => { const d = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1; return [(b[0] - a[0]) / d, (b[1] - a[1]) / d, d]; };
      const u0 = unit(q[0], q[1]), u1 = unit(q[n - 1], q[n - 2]);
      q[0] = [q[0][0] + u0[0] * (p.r + 5), q[0][1] + u0[1] * (p.r + 5)]; q[n - 1] = [q[n - 1][0] + u1[0] * (h.r + 5), q[n - 1][1] + u1[1] * (h.r + 5)];
      let d = `M${f(q[0])}`;
      for (let i = 1; i < n - 1; i++) { const a = q[i - 1], b = q[i], c = q[i + 1], [ax, ay, l1] = unit(b, a), [cx, cy, l2] = unit(b, c);
        const r1 = i === 1 ? l1 : l1 / 2, r2 = i === n - 2 ? l2 : l2 / 2;   // 끝 구간은 통째로, 가운데 구간은 이웃 모서리와 반씩 나눈다
        d += `L${f([b[0] + ax * r1, b[1] + ay * r1])}Q${f(b)} ${f([b[0] + cx * r2, b[1] + cy * r2])}`; }
      return `<path class="ray ${cls}" data-a="${esc(p.id)}" data-b="${esc(h.id)}" d="${d}L${f(q[n - 1])}" fill="none" style="stroke:${mix(h.c, 80)}" stroke-width="${themeMode() === 'light' ? 2.6 : 2}" stroke-linejoin="round" stroke-linecap="round"/>`; };
    for (const comp of L.comps) {
      const far = comp.far ? 'far' : '', hub = comp.hub;
      // 부모 선의 모양: 묶음 배치가 상자를 격자로 놓은 주제 층(hub.route 가 있다)은 두 모드 모두 상자 사이 통로를 따라 굴린 선(routeS),
      // 부모 기준으로 놓은 층(주제가 아닌 노드로 들어간 층, 그 아래의 주제)은 곧은 빛살(ray)로 잇는다
      if (hub.parentHub && hub.route) rays += routeS(hub.route, hub, hub.parentHub, comp.pinOnly ? 'pinpath' : 'path');
      else if (hub.parentHub) drawRay(hub.parentHub, hub, hub.viaE, comp.pinOnly ? 2.2 : 3.4, comp.pinOnly ? .8 : 1.2, comp.pinOnly ? 'pinpath' : 'path');          // 펼쳐 들어온 길: 굵게(고정 노드로 가는 길은 조금 가늘게)
      for (const o of comp.items) { if (o.link) { drawRay(hub, o.link, o.e, 1.4, .5, `${far} link`); continue; }
        if (o.role === 'bridge') { for (const s of o.subs) { const t = at.get(s); if (t) drawRay({ ...o, c: mix(o.c, 70) }, { ...t, c: mix(t.c, 60) }, null, 1.2, 1, far); } continue; }
        drawRay(hub, o, o.e, o.role === 'left' ? 3 : 2.4, .5, comp.pinOnly ? 'pinpath' : far); }
      if (comp.stub) { const s = comp.stub; deco += `<g class="stub ${far}" data-id="${esc(hub.id)}" data-chain="${esc(comp.chain.map((n) => n.id).join('|'))}"><line x1="${(hub.x + Math.cos(s.ang) * (hub.r + 5)).toFixed(1)}" y1="${(hub.y + Math.sin(s.ang) * (hub.r + 5)).toFixed(1)}" x2="${s.x.toFixed(1)}" y2="${s.y.toFixed(1)}" style="stroke:${mix(hub.c, 55)}" stroke-width="2.2" stroke-linecap="round"/><text class="badge" x="${s.x.toFixed(1)}" y="${(s.y + 4).toFixed(1)}" text-anchor="${Math.cos(s.ang) < -.3 ? 'end' : Math.cos(s.ang) > .3 ? 'start' : 'middle'}" dx="${Math.cos(s.ang) < -.3 ? -6 : Math.cos(s.ang) > .3 ? 6 : 0}" dy="${Math.sin(s.ang) > .3 ? 10 : Math.sin(s.ang) < -.3 ? -6 : 0}">${esc(s.text)}</text></g>`; }
    }
    // 고정한 노드(그래프 안의 제자리에 박힌 것): 지금 펼친 노드 가운데 이어진 것으로 빛살을 긋는다
    for (const pn of L.pinned || []) { if (pn.role !== 'pin') continue;
      for (const e of adj.get(pn.id) || []) { const t = at.get((e.a.id === pn.id ? e.b : e.a).id); if (t && t !== pn) drawRay(pn, t, e, 1.6, .6, 'pinray'); } }
    for (const st of L.sets) for (const s of st.shelves) deco += `<text class="head" x="${s.x + 14}" y="${st.BY + 30}">${s.name} ${s.all.length}${s.all.length > s.items.length ? ` <tspan class="badge">${esc(C.recent(s.items.length))}</tspan>` : ''}</text>` + (s.all.length > s.items.length ? `<text class="more-btn" data-more="${esc(st.hub.id)}|${s.k}" x="${s.x + 26}" y="${st.BY + 46 + s.items.length * 17 + 14}">${esc(C.more(s.all.length - s.items.length))}</text>` : '');
    const rot = (o, pad, cls, n) => { const dg = todeg(o.ang), flip = Math.cos(o.ang) < 0, rr = flip ? dg + 180 : dg, ox = Math.cos(o.ang) * (o.r + pad), oy = Math.sin(o.ang) * (o.r + pad);
      return `<text class="${cls}" x="${ox.toFixed(1)}" y="${oy.toFixed(1)}" dy="4" text-anchor="${flip ? 'end' : 'start'}" transform="rotate(${rr.toFixed(1)} ${ox.toFixed(1)} ${oy.toFixed(1)})">${esc(short(o.label, n))}</text>`; };
    const lbl = (o) => {
      if (o.role === 'hub') { const a = o.parentHub ? Math.atan2(o.y - o.parentHub.y, o.x - o.parentHub.x) : rad(135), dx = Math.cos(a + Math.PI / 2), dy = Math.sin(a + Math.PI / 2);
        return o.k === 'topic' ? `<text class="hub" x="0" y="${o.r + 20}" text-anchor="middle">${esc(short(o.label, 28))}</text><text class="badge" x="0" y="${o.r + 34}" text-anchor="middle">${esc(C.topicCount(o.src.count))}</text>` : `<text class="hub" x="${(-o.r * .7 - 8)}" y="${o.r + 16}" text-anchor="end">${esc(short(o.k === 'bridge' ? o.label.replace(/ junction$/i, '') : o.label, 28))}${o.k === 'topic' ? `<tspan class="badge" dx="6">${esc(C.topicCount(o.src.count))}</tspan>` : ''}</text>`; }
      if (o.role === 'right' && o.k === 'topic') { const flip = Math.cos(o.ang) < 0; return `<text class="l1" x="${flip ? -(o.r + 7) : o.r + 7}" y="4" text-anchor="${flip ? 'end' : 'start'}">${esc(short(o.label, 18))}<tspan class="badge" dx="5">${o.src.count}</tspan></text>`; }
      if (o.role === 'up' || o.role === 'left' || o.role === 'right') return rot(o, 7, 'l1', o.farEnds ? 40 : 24);
      if (o.role === 'bridge') return `<text class="l2" x="0" y="${o.r + 13}" text-anchor="middle">${esc(short(o.label.replace(/ junction$/i, ''), 20))}</text>`;
      return `<text x="${o.r + 7}" y="4">${esc(short(o.label, 24))}</text>`; };
    // 초승달: 그 층 허브와 다른 주제의 노드에, 그 층 허브 주제의 색으로
    const compHub = new Map(L.comps.map((c) => [c.hub.id, c.hub]));
    const crescent = (o) => { const h = compHub.get(o.comp); if (!h || o.k === 'topic' || o.k === 'bridge' || o.role === 'hub') return ''; const ht = topicOf(h.src), d = topicOf(o.src);
      const cross = h.k === 'topic' ? !(d === ht || d.startsWith(ht + '/')) : d !== ht; if (!cross) return '';
      const a = o.role === 'shelf' ? Math.PI : Math.atan2(h.y - o.y, h.x - o.x), dx = Math.cos(a) * o.r * .5, dy = Math.sin(a) * o.r * .5;
      return `<g class="cres">${shape(o.k, +dx.toFixed(2), +dy.toFixed(2), o.r, topicColour(ht))}${shape(o.k, 0, 0, o.r + 1.3, 'var(--c-bg)')}</g>`; };
    // 다리 = 갈래 고리. 잇는 주제마다 고리 한 조각을 그 주제 쪽으로 향하게 두고, 그 주제의 색으로 칠한다.
    // 조각 수가 곧 몇 주제를 잇는가이고, 조각의 방향이 곧 빛살이 나가는 방향이다. 이 층 밖으로도 이어지면 회색 조각이 하나 더 붙는다.
    const junction = (o) => { const ts = o.farEnds ? o.farEnds.map((d, i, l) => ({ a: -Math.PI / 2 + i * 2 * Math.PI / l.length, c: topicColour(d) }))
        : (o.subs || []).map((s) => at.get(s)).filter(Boolean).map((q) => ({ a: Math.atan2(q.y - o.y, q.x - o.x), c: q.c }));
      if (o.outside) { const avg = Math.atan2(ts.reduce((s, q) => s + Math.sin(q.a), 0), ts.reduce((s, q) => s + Math.cos(q.a), 0)); ts.push({ a: avg + Math.PI, c: 'var(--c-mute)' }); }
      if (!ts.length) return shape('concept', 0, 0, o.r, o.c, true);
      ts.sort((x, y) => x.a - y.a); const n = ts.length, R = o.r, w = 2.6;
      const half = Math.min(Math.PI / n - .32, 1.1);
      const arc = (a, c) => { const a0 = a - half, a1 = a + half, P = (q) => `${(Math.cos(q) * R).toFixed(2)} ${(Math.sin(q) * R).toFixed(2)}`;
        return `<path class="shape" d="M${P(a0)}A${R} ${R} 0 0 1 ${P(a1)}" fill="none" style="stroke:${c}" stroke-width="${w}" stroke-linecap="round"/>`; };
      return ts.map((q) => arc(q.a, q.c)).join('') + `<circle class="shape" r="${(R * .32).toFixed(2)}" style="fill:${mix(o.c, 80)}"/>`; };
    // 다리는 어느 자리에서나 이음 표식이다. 층 안의 다리(`role: bridge`)는 이미 끝을 알고,
    // 눌러 가운데(허브)가 된 다리는 CONNECTS 끝들의 주제 색으로 호를 그린다(알약 모양이 남지 않게)
    const bridgeBody = (o) => o.role === 'bridge' || o.farEnds ? junction(o)
      : junction({ ...o, farEnds: [...new Set(bridgeEnds(o.src).map((m) => m.d).filter(Boolean))] });
    const body = (o) => o.k === 'bridge' ? bridgeBody(o) : crescent(o) + (o.k === 'topic' ? ringS(o.r, o.role === 'hub' || fresh(o.src) ? o.c : mix(o.c, 70), o.role === 'hub' ? 2.6 : 2)
      : shape(o.k, 0, 0, o.r, o.src.vu ? o.c : fresh(o.src) ? o.c : mix(o.c, 55), !!o.src.vu)) + '';
    const compOf = new Map(L.comps.map((c) => [c.hub.id, c]));
    for (const o of L.items) { const oc = o.role === 'hub' ? compOf.get(o.id) : compOf.get(o.comp), far = oc && oc.far && o.role !== 'hub' ? ' far' : '', cur = o.role === 'hub' && oc === F ? ' sel' : o.role === 'hub' && path.some((n) => n.id === o.id) ? ' onpath' : '';
      nodesS += `<g class="node${far}${cur}" data-id="${esc(o.id)}" data-role="${o.role}" data-chain="${esc((o.role === 'hub' ? (compOf.get(o.id)?.chain || []) : (o.chain || [])).map((n) => n.id).join('|'))}" data-level="${o.level}" tabindex="0" role="button" aria-label="${esc((KIND[o.k] || o.k) + ' ' + o.label)}" style="transform: translate(${o.x}px, ${o.y}px)"><circle class="ring" r="${o.r + 4.5}"/><circle class="hit" r="${o.r + 5}" fill="transparent"/>${body(o)}${lbl(o)}</g>`; }
    svg.innerHTML = `<defs>${defs}</defs><g id="cmp-deco">${deco}</g><g id="cmp-rays">${rays}</g><g id="cmp-tree"></g><g id="cmp-nodes">${nodesS}</g>`;
    // 카메라: 지금 층(허브·부채·목록)과 바로 위 허브가 들어오게
    const inFocus = (o) => { const c = o.role === 'hub' ? compOf.get(o.id) : compOf.get(o.comp); return o.role === 'shelf' || (!!c && c === F); };
    const box = L.items.filter((o) => inFocus(o) || nearPin(o, F) || (o.role === 'hub' && F.parent && o.id === F.parent.hub.id));
    const pts = box.flatMap(extentPts);
    const xs = pts.map((q) => q[0]), ys = pts.map((q) => q[1]);
    // 넓게 보기: 지나온 허브들 + 지금 층만 담는다(옆 갈래의 잎까지 담으면 너무 멀어진다)
    if (KEEPCAM) KEEPCAM = false;
    else if (WIDE) { const pinHub = new Set(L.comps.filter((c) => c.pinOnly).map((c) => c.hub.id)), all = L.items.filter((o) => (o.role === 'hub' && !pinHub.has(o.id)) || o.comp === F.hub.id || o.role === 'shelf' || nearPin(o, F)), ax = all.flatMap((o) => [o.x - 160, o.x + 200]), ay = all.flatMap((o) => [o.y - 40, o.y + 40]), c = F.hub;
      const hw = Math.max(c.x - Math.min(...ax), Math.max(...ax) - c.x), hh = Math.max(c.y - Math.min(...ay), Math.max(...ay) - c.y);
      flyView({ x: c.x - hw, y: c.y - hh, w: 2 * hw, h: 2 * hh }); WIDE = false;   // 고른 노드를 가운데 두고, 펼친 길 전체가 보이게 물러난다
    } else flyView({ x: Math.min(...xs) - 20, y: Math.min(...ys) - 20, w: Math.max(...xs) - Math.min(...xs) + 40, h: Math.max(...ys) - Math.min(...ys) + 40 });
    // 남는 노드는 옛 자리에서 새 자리로(FLIP)
    const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (!still && prevPos.size) { svg.classList.add('moving');
      for (const g of svg.querySelectorAll('#cmp-nodes .node')) { const was = prevPos.get(g.dataset.id); g.style.transition = 'none'; if (was) g.style.transform = `translate(${was.x}px, ${was.y}px)`; else g.classList.add('enter'); }
      svg.getBoundingClientRect();
      flipRaf = requestAnimationFrame(() => { for (const g of svg.querySelectorAll('#cmp-nodes .node')) { const o = at.get(g.dataset.id); if (!o) continue; g.style.transition = ''; g.style.transform = `translate(${o.x}px, ${o.y}px)`; g.classList.remove('enter'); }
        flipTo = setTimeout(() => { svg.classList.remove('moving'); grow(L.sets.flatMap(buildTree)); }, 400); }); }
    else grow(L.sets.flatMap(buildTree));
    prevPos = new Map(L.items.map((o) => [o.id, { x: o.x, y: o.y }]));
  }
  // 카메라: viewBox 를 부드럽게 옮긴다(화면 비율에 맞춰 넓힌다)
  let flyRaf = 0;
  function flyView(b) {
    const W = svg.clientWidth || 1400, H = svg.clientHeight || 900, ar = W / H; let { x, y, w, h } = b; w = Math.max(w, 420); h = Math.max(h, 320);   // 너무 작은 층만 과하게 확대되지 않게
    if (w / h > ar) { const nh = w / ar; y -= (nh - h) / 2; h = nh; } else { const nw = h * ar; x -= (nw - w) / 2; w = nw; }
    const to = { x, y, w, h }, from = VIEW || to, t0 = performance.now(), ms = matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 520;
    cancelAnimationFrame(flyRaf);
    const step = () => { const u = ms ? Math.min(1, (performance.now() - t0) / ms) : 1, e = 1 - (1 - u) ** 3, v = { x: from.x + (to.x - from.x) * e, y: from.y + (to.y - from.y) * e, w: from.w + (to.w - from.w) * e, h: from.h + (to.h - from.h) * e };
      svg.setAttribute('viewBox', `${v.x.toFixed(1)} ${v.y.toFixed(1)} ${v.w.toFixed(1)} ${v.h.toFixed(1)}`); VIEW = v; lod(); if (u < 1) flyRaf = requestAnimationFrame(step); };
    step();
  }

  // ── 줄기와 가지(지금 층의 목록): 허브에서 6시 쪽으로 곧게 나가 버스에 닿고, 목록마다 세로 줄기, 항목마다 가지 ─────────
  const Wd = (d) => Math.max(.8, 2.8 - d * .0016);   // 폭은 허브에서 멀수록 가늘어진다
  function buildTree(L) {
    const prims = [], hub = L.hub, hc = hub.c, Rc = 12, Rb = 7, F = 4.5, BY = L.BY;
    const cols = L.shelves.filter((s) => s.items.length); if (!cols.length) return prims;
    // 곧은 토막은 양 끝을 반 픽셀씩 늘려 이웃 조각(꺾임 · 모서리 · 끝마감) 안으로 들인다: 맞닿기만 하면 그 자리에 실금이 비친다
    const seg = (a, b, d0, c) => { const len = Math.hypot(b[0] - a[0], b[1] - a[1]), ux = (b[0] - a[0]) / (len || 1), uy = (b[1] - a[1]) / (len || 1), o = .5;
      prims.push({ t: 'seg', a: [a[0] - ux * o, a[1] - uy * o], b: [b[0] + ux * o, b[1] + uy * o], d0, d1: d0 + len, c }); return d0 + len; };
    const elbow = (x, y, R, a0, a1, d0, c) => { const len = R * Math.abs(a1 - a0); prims.push({ t: 'arc', cx: x, cy: y, R, a0, a1, d0, d1: d0 + len, c }); return d0 + len; };
    const fillet = (jx, jy, w, sx, sy, dd, c) => prims.push({ t: 'fil', jx, jy, wv: w, wh: w, sx, sy, F, d0: dd, c });
    const spine = (s, ds, sy0) => { const lastY = s.items[s.items.length - 1].y, sEnd = seg([s.x, sy0], [s.x, lastY - Rb], ds, hc);
      s.items.forEach((n, q) => { const isLast = q === s.items.length - 1, dy = ds + (n.y - sy0), endX = n.x - (n.r + GAP + 3); let db;
        if (isLast) db = elbow(s.x + Rb, lastY - Rb, Rb, Math.PI, Math.PI / 2, sEnd, n.c); else { fillet(s.x, n.y, Wd(dy), 1, -1, dy, hc); fillet(s.x, n.y, Wd(dy), 1, 1, dy, hc); db = dy; }
        const de = seg([isLast ? s.x + Rb : s.x, n.y], [endX, n.y], db, n.c); prims.push({ t: 'cap', n, d0: de, c: n.c }); }); };
    if (L.mode === 'down') {   // 6시가 아래: 곧게 내려가 버스, 좌우로 갈라진다
      const CX = hub.x, xs = cols.map((s) => s.x), xL = Math.min(...xs), xR = Math.max(...xs);
      if (cols.length === 1 && Math.abs(cols[0].x - CX) < 1) { spine(cols[0], seg([CX, hub.y + hub.r + GAP], [CX, BY], 0, hc), BY); return prims; }
      const L0 = seg([CX, hub.y + hub.r + GAP], [CX, BY], 0, hc); fillet(CX, BY, Wd(L0), -1, -1, L0, hc); fillet(CX, BY, Wd(L0), 1, -1, L0, hc);
      const bL = xL < CX - 1 ? seg([CX, BY], [xL + Rc, BY], L0, hc) : L0, bR = xR > CX + 1 ? seg([CX, BY], [xR - Rc, BY], L0, hc) : L0;
      for (const s of cols) { const dj = L0 + Math.abs(s.x - CX);
        if (Math.abs(s.x - xL) < 1 && xL < CX - 1) { spine(s, elbow(xL + Rc, BY + Rc, Rc, -Math.PI / 2, -Math.PI, bL, hc), BY + Rc); continue; }
        if (Math.abs(s.x - xR) < 1 && xR > CX + 1) { spine(s, elbow(xR - Rc, BY + Rc, Rc, -Math.PI / 2, 0, bR, hc), BY + Rc); continue; }
        fillet(s.x, BY, Wd(dj), -1, 1, dj, hc); fillet(s.x, BY, Wd(dj), 1, 1, dj, hc); spine(s, dj, BY); }
      return prims; }
    // 6시가 옆: 허브에서 그쪽으로 곧게 나가 버스가 되고, 목록은 버스에서 아래로 내려간다(맨 끝은 둥글게 꺾인다)
    // 6시 방향으로 비스듬히 나가(빛살 사이의 빈 틈), Q 에서 둥글게 꺾여 가로 버스가 된다
    const sd = L.side, a6 = L.a6, ux = Math.cos(a6), uy = Math.sin(a6), Q = L.Q, ordered = [...cols].sort((a, b) => sd * (a.x - b.x)), end = ordered[ordered.length - 1];
    const vx = sd, vy = 0, turn = Math.acos(Math.max(-1, Math.min(1, ux * vx + uy * vy))), R = 16, dt = R * Math.tan(turn / 2);
    const P1 = [Q.x - ux * dt, Q.y - uy * dt], P2 = [Q.x + vx * dt, Q.y];
    let d0 = seg([hub.x + ux * (hub.r + GAP), hub.y + uy * (hub.r + GAP)], P1, 0, hc);
    if (turn > .02) { const cr = ux * vy - uy * vx, nx = cr > 0 ? -uy : uy, ny = cr > 0 ? ux : -ux, C0 = [P1[0] + nx * R, P1[1] + ny * R];
      let b0 = Math.atan2(P1[1] - C0[1], P1[0] - C0[0]), b1 = Math.atan2(P2[1] - C0[1], P2[0] - C0[0]); if (b1 - b0 > Math.PI) b1 -= 2 * Math.PI; if (b0 - b1 > Math.PI) b1 += 2 * Math.PI;
      d0 = elbow(C0[0], C0[1], R, b0, b1, d0, hc); }
    const x0 = P2[0], busEnd = seg([x0, BY], [end.x - sd * Rc, BY], d0, hc);
    for (const s of ordered) { const dj = d0 + Math.abs(s.x - x0);
      if (s === end) { spine(s, elbow(end.x - sd * Rc, BY + Rc, Rc, -Math.PI / 2, sd > 0 ? 0 : -Math.PI, busEnd, hc), BY + Rc); continue; }
      fillet(s.x, BY, Wd(dj), -1, 1, dj, hc); fillet(s.x, BY, Wd(dj), 1, 1, dj, hc); spine(s, dj, BY); }
    return prims;
  }

  function primPoly(p, D) {
    if (p.t === 'seg') { const q = Math.max(0, Math.min(1, (D - p.d0) / (p.d1 - p.d0))); if (q <= 0) return ''; const [ax, ay] = p.a, bx = ax + (p.b[0] - ax) * q, by = ay + (p.b[1] - ay) * q, len = Math.hypot(bx - ax, by - ay) || 1;
      const nx = -(by - ay) / len, ny = (bx - ax) / len, wa = Wd(p.d0), wb = Wd(p.d0 + (p.d1 - p.d0) * q); return P2([[ax + nx * wa, ay + ny * wa], [bx + nx * wb, by + ny * wb], [bx - nx * wb, by - ny * wb], [ax - nx * wa, ay - ny * wa]]); }
    if (p.t === 'arc') { const q = Math.max(0, Math.min(1, (D - p.d0) / (p.d1 - p.d0))); if (q <= 0) return ''; const A = [], B = [];
      for (let s = 0; s <= 12; s++) { const u = s / 12 * q, a = p.a0 + (p.a1 - p.a0) * u, w = Wd(p.d0 + (p.d1 - p.d0) * u); A.push([p.cx + Math.cos(a) * (p.R + w), p.cy + Math.sin(a) * (p.R + w)]); B.push([p.cx + Math.cos(a) * (p.R - w), p.cy + Math.sin(a) * (p.R - w)]); }
      return P2([...A, ...B.reverse()]); }
    if (p.t === 'fil') { if (D < p.d0) return ''; const { jx, jy, wv, wh, sx, sy, F } = p, ccx = jx + sx * (wv + F), ccy = jy + sy * (wh + F), a0 = sx > 0 ? Math.PI : 0, a1 = sy > 0 ? -Math.PI / 2 : Math.PI / 2;
      let da = a1 - a0; if (da > Math.PI) da -= 2 * Math.PI; if (da < -Math.PI) da += 2 * Math.PI; const arc = []; for (let s = 0; s <= 8; s++) { const a = a0 + da * s / 8; arc.push([ccx + Math.cos(a) * F, ccy + Math.sin(a) * F]); }
      // 모서리 조각은 줄기와 가지 안으로 0.6 만큼 파고든다(맞닿은 변에 실금이 서지 않게)
      const b = .6; arc[0] = [arc[0][0] - sx * b, arc[0][1]]; arc[arc.length - 1] = [arc[arc.length - 1][0], arc[arc.length - 1][1] - sy * b];
      return P2([...arc, [jx + sx * (wv - b), jy + sy * (wh - b)]]); }
    // 끝마감: arcPts 는 아래(y + w1)에서 시작해 위로 돈다. 다각형도 아래에서 시작해야 꼬이지 않는다(위에서 시작하면 두 변이 엇갈려 모래시계가 된다)
    if (p.t === 'cap') { if (D < p.d0) return ''; const n = p.n, rr = n.r + GAP + 3, w1 = Wd(p.d0), c = arcPts(n.x, n.y, rr, Math.PI, Math.asin(Math.min(.95, w1 / rr))); return P2([[n.x - rr - .8, n.y + w1], ...c, [n.x - rr - .8, n.y - w1]]); }
    return '';
  }
  let traf = 0;
  function grow(prims) {
    cancelAnimationFrame(traf); const g = $id('cmp-tree'), still = matchMedia('(prefers-reduced-motion: reduce)').matches;
    // 한 줄기는 한 색이다. 조각마다 거리로 다른 색을 칠하면 맞닿는 곳에 계단과 덧칠한 블록이 보였다. 허브와 다른 주제 색인 가지는
    // 줄기보다 먼저(아래에) 그려 그 시작이 줄기 안에 묻히게 한다
    const hc = LZ.hub.c; prims = [...prims.filter((p) => p.c !== hc), ...prims.filter((p) => p.c === hc)];
    g.innerHTML = prims.map((p) => `<polygon data-bus="${p.c === hc ? 'hub' : 'item'}" style="fill:${mix(p.c, p.c === hc ? 82 : 66)}"/>`).join('');
    const els = [...g.children], maxD = Math.max(0, ...prims.map((p) => p.d1 ?? p.d0)), t0 = performance.now();
    const step = () => { const D = still ? 1e9 : (performance.now() - t0) * 2.2; prims.forEach((p, i) => els[i].setAttribute('points', primPoly(p, D))); traf = D < maxD ? requestAnimationFrame(step) : 0; }; step();
  }

  // ── 다니기: 누르면 그 자리에서 펼친다. 지나온 층의 노드를 누르면 그 층으로 접고 거기서 새로 펼친다 ─────────────────
  let hist = [], hi = -1;
  const byAny = (id) => byId.get(id) || byId.get(String(id).split('@@')[0]) || (id.startsWith('topic:') ? topics.get(id.slice(6)) : null);   // 'id@@…' 별칭은 앞의 id 로 찾는다
  function setPath(P, push = true) { path = P; if (push) { hist = hist.slice(0, hi + 1); hist.push(P.map((n) => n.id)); hi = hist.length - 1; } render(); }
  function act(g, e) { if (!g) return; const chain = (g.dataset.chain || '').split('|').filter(Boolean).map(byAny), n = byAny(g.dataset.id);
    const isFocus = g.classList.contains('sel');
    if (g.dataset.role === 'hub' && !isFocus) { setPath(chain.length ? chain : [n]); return; }   // 지나온 층의 허브: 그 층으로 접는다
    if (isFocus) return;   // 지금 가운데는 이미 펼쳐져 있다
    setPath([...chain, n]); }
  svg.addEventListener('click', (e) => { const mb = e.target.closest('[data-more]'); if (mb) { MORE.set(mb.dataset.more, (MORE.get(mb.dataset.more) || 0) + 20); MEASURED = false; prevPos = new Map(); render(); return; } const st = e.target.closest('.stub'); if (st) { setPath(st.dataset.chain.split('|').map(byAny)); return; } act(e.target.closest('.node'), e); });
  svg.addEventListener('keydown', (e) => { if (e.key !== 'Enter' && e.key !== ' ') return; const g = e.target.closest('.node'); if (!g) return; e.preventDefault(); const r = g.getBoundingClientRect(); act(g, { clientX: r.right, clientY: r.top }); });
  const tip = $id('tip');
  svg.addEventListener('pointerover', (e) => { const g = e.target.closest('.node'); svg.classList.toggle('hot', !!g);
    svg.querySelectorAll('.ray').forEach((r) => r.classList.toggle('near', !!g && (r.dataset.a === g?.dataset.id || r.dataset.b === g?.dataset.id)));
    if (g) { const n = byAny(g.dataset.id), o = LZ.items.find((q) => q.id === g.dataset.id); tip.style.display = 'block';
      tip.innerHTML = `${esc(n.t || n.path)}<small>${KIND[n.k] || n.k}${o?.rel ? ' · ' + esc(o.rel) : ''}${n.d ? ' · ' + esc(n.d) : ''}${n.ts ? ' · ' + n.ts : ''}</small>`; } else tip.style.display = 'none'; });
  svg.addEventListener('pointermove', (e) => { tip.style.left = `${e.clientX + 14}px`; tip.style.top = `${e.clientY + 12}px`; });
  svg.addEventListener('pointerleave', () => { tip.style.display = 'none'; svg.classList.remove('hot'); });
  onWin('keydown', (e) => { if (e.defaultPrevented || e.target.closest?.('input,textarea,[contenteditable],dialog') || !root.isConnected || root.closest('[hidden]')) return; if (e.altKey && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) e.preventDefault(); if (e.altKey && e.key === 'ArrowLeft') { if (hi > 0) { hi--; setPath(hist[hi].map(byAny).filter(Boolean), false); } } else if (e.altKey && e.key === 'ArrowRight') { if (hi < hist.length - 1) { hi++; setPath(hist[hi].map(byAny).filter(Boolean), false); } }
    else if (e.key === 'Escape' || (e.key === 'Backspace' && path.length > 1)) { if (e.key === 'Escape') setPath([topics.get(ROOT)]); else setPath(path.slice(0, -1)); } });
  // 찾기: 주제 사슬을 따라 펼친 다음 그 노드를 편다
  const chainTo = (n) => { const out = chainOf(topicOf(n)).map((p) => topics.get(p)); if (n.k !== 'topic') out.push(n); return out; };
  let QUERY = '';
  const norm = (s) => String(s || '').toLowerCase();
  const matchQ = (n) => !QUERY || norm(n.t || n.path || n.id).includes(QUERY) || norm(n.id).includes(QUERY);
  const setQuery = (q) => { const was = QUERY; QUERY = norm(q); paintSide(); treeChanged(!QUERY && !!was); };
  // 굴려서 확대·끌어서 이동: viewBox 를 직접 옮긴다(펼친 그림이 화면보다 클 때)
  // 멀리서 볼수록 덜 그린다: 1단(노드 이름·목록 줄 글자 숨김), 2단(목록 줄과 잎 노드 숨김, 주제와 선만)
  const lod = () => { const z = VIEW ? VIEW.w / (svg.clientWidth || 1) : 1; svg.classList.toggle('lod1', z > 2.4); svg.classList.toggle('lod2', z > 5); };
  const setVB = (v) => { VIEW = v; svg.setAttribute('viewBox', `${v.x.toFixed(1)} ${v.y.toFixed(1)} ${v.w.toFixed(1)} ${v.h.toFixed(1)}`); lod(); };
  svg.addEventListener('wheel', (e) => { e.preventDefault(); cancelAnimationFrame(flyRaf); const r = svg.getBoundingClientRect(), f = Math.exp(e.deltaY * .0015), v = VIEW;
    const px = v.x + (e.clientX - r.left) / r.width * v.w, py = v.y + (e.clientY - r.top) / r.height * v.h; setVB({ x: px - (px - v.x) * f, y: py - (py - v.y) * f, w: v.w * f, h: v.h * f }); }, { passive: false });
  let pan = null;
  svg.addEventListener('pointerdown', (e) => { if (e.target.closest('.node, .stub')) return; pan = { x: e.clientX, y: e.clientY, v: { ...VIEW } }; svg.setPointerCapture(e.pointerId); svg.style.cursor = 'grabbing'; });
  svg.addEventListener('pointermove', (e) => { if (!pan) return; const r = svg.getBoundingClientRect(), k = pan.v.w / r.width; setVB({ ...pan.v, x: pan.v.x - (e.clientX - pan.x) * k, y: pan.v.y - (e.clientY - pan.y) * k }); });
  svg.addEventListener('pointerup', () => { pan = null; svg.style.cursor = ''; });

  // ── 기간과 종류(옵션·범례가 다룬다): 기간 밖의 노드는 흐리게, 범례에서 끈 종류는 렌즈에서 뺀다 ─────────────────────
  let TSEL = null;   // [시작, 끝] 날짜. 없으면 전체
  const dated = nodes.filter((n) => when(n));
  const allDays = (() => { const ds = dated.map(when).sort(); if (!ds.length) return [C.before]; const lo = ds[Math.floor(ds.length * .005)], out = [C.before];
    for (let x = Date.parse(lo); x <= Date.parse(ds[ds.length - 1]); x += 864e5) out.push(new Date(x).toISOString().slice(0, 10)); return out; })();
  const dIdx0 = new Map(allDays.map((d, i) => [d, i])), dIdx = { get: (d) => dIdx0.get(d) ?? (d && d < allDays[1] ? 0 : undefined) };
  function dimLens() { const sel = TSEL; for (const g of svg.querySelectorAll('#cmp-nodes .node')) { const n = byAny(g.dataset.id), w = n && n.k !== 'topic' ? when(n) : '';
      g.classList.toggle('out', !!sel && !!w && g.dataset.role !== 'hub' && (w < sel[0] || w > sel[1])); } }
  const _render = render; render = function () { _render(); dimLens(); ctx.onChange?.(); };

  // ── 고정: 고른 노드를 다른 층으로 가도 왼쪽 판에 남긴다(Neo4j·Palantir 의 pin). 판의 노드를 짚으면 지금 렌즈에서 그와 이어진 노드가 진해지고,
  // 누르면 그리로 간다. 지금 렌즈에 고정한 노드와 이어진 노드가 있으면 그 노드에 작은 핀 표가 붙는다. 이 기기에만 저장한다(localStorage).
  // 고정은 이 기기에 스페이스마다 따로 둔다(`ctx.store`, 없으면 예전 열쇠). 다른 스페이스의 고정을 덮어쓰지 않고, 깨진 값은 빈 것으로 읽는다
  const PKEY = ctx.store ? `vinc.pins:${ctx.store}` : 'vinc.pins';
  const readJSON = (k, d) => { try { const v = JSON.parse(localStorage.getItem(k) || ''); return v && typeof v === 'object' ? v : d; } catch { return d; } };
  const PINS = new Set((Array.isArray(readJSON(PKEY, [])) ? readJSON(PKEY, []) : []).filter((id) => typeof id === 'string' && byAny(id)));
  const dock = $id('dock');
  // ── 고정: 고른 노드를 **그래프 안의 그 자리**(배치 좌표)에 박아 둔다. 가운데를 옮겨 다시 배치해도 그 노드는 같은 자리에 남고,
  // 새로 펼친 노드 가운데 이어진 것으로 빛살이 간다. 겹치는 새 노드는 비켜 선다. 이 기기에만 저장한다.
  localStorage.removeItem('vinc.pins.at');   // 앞선 시안의 화면 좌표 자리는 쓰지 않는다
  function savePins() { localStorage.setItem(PKEY, JSON.stringify([...PINS])); localStorage.removeItem(`${PKEY}.w`); }   // .w: 끌어 옮긴 자리(걷어냄)
  const LASTPIN = new Map();   // 이번 세션에서 고정 노드가 마지막으로 놓였던 자리(배치 좌표)
  function pinLayout(L) {
    if (!PINS.size) return L;
    const at = new Map(L.items.map((o) => [o.id, o])), pinned = [];
    for (const id of PINS) { const n = byAny(id); if (!n) continue; let o = at.get(id);
      if (o) { LASTPIN.set(id, { x: o.x, y: o.y }); o.pinned = true; pinned.push(o); continue; }
      const w2 = LASTPIN.get(id); if (!w2) continue;   // 길로 못 잇는 경우(배치가 그 노드를 못 놓을 때)는 마지막으로 놓였던 자리에
      const chain = chainTo(n).slice(0, -1);
      o = { id, src: n, k: n.k, x: w2.x, y: w2.y, r: n.k === 'topic' ? 13 : 6, c: colv(n), label: titleOf(n), role: 'pin', chain, comp: '', level: chain.length, pinned: true };
      L.items.push(o); pinned.push(o); }
    // 비키기: 고정한 자리에 겹친 새 노드를 그 자리 바깥으로 민다(허브와 목록 줄은 자리를 지킨다)
    for (const p of pinned) for (const o of L.items) { if (o.pinned || o.role === 'hub' || o.role === 'shelf') continue;
      const dx = o.x - p.x, dy = o.y - p.y, d = Math.hypot(dx, dy), need = o.r + p.r + 18; if (d >= need) continue;
      const ux = d ? dx / d : 1, uy = d ? dy / d : 0; o.x = p.x + ux * need; o.y = p.y + uy * need; }
    L.pinned = pinned; return L; }
  // 카메라: 지금 층에서 너무 멀지 않은 고정 노드는 함께 담는다(멀면 무대 가장자리에 방향 표만 둔다)
  const nearPin = (o, F) => !!o.pinned && Math.hypot(o.x - F.hub.x, o.y - F.hub.y) < 520;
  function togglePin(id) { if (PINS.has(id)) { PINS.delete(id); LASTPIN.delete(id); } else PINS.add(id);
    savePins(); KEEPCAM = true; render(); }
  const linkedTo = (id) => { const s = new Set(); for (const e of adj.get(id) || []) s.add((e.a.id === id ? e.b : e.a).id);
    const n = byAny(id); if (n && n.k === 'topic') for (const m of nodes) if ((m.d || '') === n.path) s.add(m.id); return s; };
  function drawDock() {
    dock.hidden = !PINS.size; if (!PINS.size) return;
    const here = new Set(LZ ? LZ.items.map((o) => o.id) : []);
    dock.innerHTML = `<div class="cmp-sec-h cmp-pinh">${pinIcon(14)}${esc(C.pinned(PINS.size))}</div>` + [...PINS].map((id) => { const n = byAny(id), ln = [...linkedTo(id)].filter((x) => here.has(x)).length;
      return `<div class="pi" data-id="${esc(id)}"><button class="pg" data-id="${esc(id)}" title="${esc(n.t || n.path || id)}"><svg viewBox="-7 -7 14 14">${n.k === 'topic' ? topicRing(5, colv(n), 1.8) : shape(n.k, 0, 0, 5, colv(n), !!n.vu)}</svg><span>${esc(short(titleOf(n), 34))}</span>${ln ? `<em>${ln}</em>` : ''}</button><button class="px" data-x="${esc(id)}" aria-label="${esc(C.unpin)}">✕</button></div>`; }).join('');
  }
  // 고정 표식. 노드마다 LZ.items 를 다시 훑지 않는다(큰 그래프에서 렌더마다 n² 이었다): id 표를 한 번 만든다
  function markPins() { if (!LZ) return; const lk = new Map(); for (const id of PINS) for (const x of linkedTo(id)) lk.set(x, (lk.get(x) || 0) + 1);
    const nodesEl = svg.querySelectorAll('#cmp-nodes .node');
    if (!PINS.size) { for (const g of nodesEl) { g.querySelector('.pinmark')?.remove(); g.classList.remove('pinned'); } return; }
    const byItem = new Map(LZ.items.map((q) => [q.id, q]));
    for (const g of nodesEl) { g.querySelector('.pinmark')?.remove(); g.classList.toggle('pinned', PINS.has(g.dataset.id)); const o = byItem.get(g.dataset.id); if (!o) continue;
      const mine = PINS.has(g.dataset.id), k = lk.get(g.dataset.id); if (!mine && !k) continue;
      g.insertAdjacentHTML('beforeend', `<g class="pinmark${mine ? ' self' : ''}" transform="translate(${(o.r * .8 + 3).toFixed(1)} ${(-o.r * .8 - 3).toFixed(1)})"><circle r="${mine ? 6 : 3.2}"/>${mine ? `<svg x="-4.4" y="-4.4" width="8.8" height="8.8" viewBox="0 0 24 24">${PIN_PATHS}</svg>` : ''}</g>`); } }
  dock.addEventListener('click', (e) => { const x = e.target.closest('[data-x]'); if (x) { togglePin(x.dataset.x); return; }
    const g = e.target.closest('.pg'); if (!g) return; const n = byAny(g.dataset.id), o = LZ && LZ.items.find((q) => q.id === n.id && q.chain);
    if (o) setPath(o.role === 'hub' ? o.chain : [...o.chain, n]); else setPath(chainTo(n)); });
  dock.addEventListener('pointerover', (e) => { const pi = e.target.closest('.pi'); const on = pi ? new Set([pi.dataset.id, ...linkedTo(pi.dataset.id)]) : null;
    svg.classList.toggle('hot', !!on); svg.querySelectorAll('#cmp-nodes .node').forEach((g) => g.classList.toggle('pick', !!on && on.has(g.dataset.id))); });
  dock.addEventListener('pointerleave', () => { svg.classList.remove('hot'); svg.querySelectorAll('#cmp-nodes .node.pick').forEach((g) => g.classList.remove('pick')); });
  let hoverNode = null; svg.addEventListener('pointerover', (e) => { const g = e.target.closest('.node'); hoverNode = g ? g.dataset.id : null; });
  svg.addEventListener('pointerleave', () => { hoverNode = null; });
  // P 하나만(⌘/Ctrl+P 는 인쇄다). 입력 칸 · 숨은 면에서는 듣지 않는다
  onWin('keydown', (e) => { if (e.key.toLowerCase() !== 'p' || e.ctrlKey || e.metaKey || e.altKey || !hoverNode || e.target.closest?.('input,textarea,[contenteditable]') || !root.isConnected || root.closest('[hidden]')) return; togglePin(hoverNode); });
  { const _r2 = render; render = function () { _r2(); drawDock(); markPins(); }; }
  buildTopicColours();
  // 전체 배치(네 팔). 맨 위 주제의 상자를 가운데 두고, 그 하위 주제 가지를 아래·오른쪽·위·왼쪽 팔에 나눈다. 팔은 바람개비처럼
  // 맨 위 상자의 한 모서리에서 시계 방향으로 뻗어(위 팔은 오른쪽으로, 오른쪽 팔은 아래로 …) 서로의 자리에 들어가지 않는다.
  // 가지 하나는 상자 하나다: 자기 층(폭 2·HW, 깊이 TOP + DEP) 아래에 자식 상자들을 열을 맞춘 격자로 둔다. 격자의 열 사이와
  // 줄 사이는 비어 있는 통로이고, 부모에서 자식으로 가는 선(route)은 그 통로만 따라 꺾는다. 열 수는 상자가 화면 비율에 가깝게 고른다.
  // 좌표: u 는 팔 방향(부모에서 멀어지는 쪽), v 는 그 3시 쪽. 가로 팔에서는 이름표가 u 쪽으로 길어 앞뒤 여유를 더 준다
  function crossCanon(L, meas = null, armsOf = null, straight = false) {
    const GAP = 80, ASP = 1.6, OUT = new Map(), KO = new Map(), q = (c, o) => meas && meas.get(c.hub.id) && meas.get(c.hub.id)[o];
    for (const c of L.comps) if (c.parent && c.hub.k === 'topic') { if (!KO.has(c.parent)) KO.set(c.parent, []); KO.get(c.parent).push(c); }
    const rowsOf = (c) => Math.min(ROWS, Math.max(0, ...Object.values(c.cp.G.shelf).map((l) => l.length)));
    // 잰 크기가 있으면 그것에 여유를 더하고, 없으면(처음 열 때) 나침반 반지름과 목록 수로 어림한다
    // o: 층을 세운 방향. v 는 아래 팔, u 는 위 팔(목록이 부모 쪽으로 늘어지므로 따로 잰다), h 는 가로 팔
    const HW = (c, o, hz = o === 'h') => (q(c, o) ? q(c, o).hw + 40 : Math.max(c.cp.E + 90, (c.cp.FP > c.cp.E ? c.cp.FP - c.cp.E * .6 : 0) / 2 + 150, hz ? 46 + rowsOf(c) * 17 + 70 : 0));
    const TOP = (c, o, hz = o === 'h') => (q(c, o) ? q(c, o).top + 24 : c.cp.E * .75 + (hz ? 170 : 0)), DEP = (c, o, hz = o === 'h') => (q(c, o) ? q(c, o).dep + 24 : c.cp.FP + (hz ? 120 : 0));
    const grid = (ks, k, B) => { const rows = Math.ceil(ks.length / k), cw = Array(k).fill(0), rh = Array(rows).fill(0);
      ks.forEach((c, i) => { const b = B.get(c); cw[i % k] = Math.max(cw[i % k], b.w); rh[Math.floor(i / k)] = Math.max(rh[Math.floor(i / k)], b.h); });
      return { k, cw, rh, W: cw.reduce((x, w) => x + w, 0) + GAP * (k - 1), H: rh.reduce((x, h) => x + h, 0) + GAP * (rows - 1) }; };
    const best = (ks, B, asp, w0, h0) => { let b = null;
      for (let k = 1; k <= ks.length; k++) { const g = grid(ks, k, B), w = Math.max(w0, g.W), h = h0 + (h0 ? GAP : 0) + g.H, sc = w * h * (1 + Math.abs(Math.log(w / h / asp)));
        if (!b || sc < b.sc) b = { ...g, w, h, sc }; }
      return b; };
    const measure = (c, o, B) => { const ks = KO.get(c) || []; ks.forEach((k) => measure(k, o, B)); const w0 = 2 * HW(c, o), h0 = TOP(c, o) + DEP(c, o);
      B.set(c, ks.length ? { ...best(ks, B, o === 'h' ? 1 / ASP : ASP, w0, h0), w0, h0 } : { w: w0, h: h0, w0, h0 }); };
    const S = (A, v, u) => [A.O[0] + A.g[0] * u + A.p[0] * v, A.O[1] + A.g[1] * u + A.p[1] * v];
    // 격자 하나를 놓는다. pre 는 부모 허브에서 이 격자 위 통로(bus0)까지 온 길이다
    // straight(하나씩 열기): 부모 바로 앞(팔 축 위)에 홀로 선 층은 통로를 돌지 않고 부모에서 곧게 잇는다
    const lay = (ks, b, x0, g0, A, pre, vp = 0) => {
      const colL = []; for (let j = 0, x = x0; j < b.k; j++) { colL.push(x); x += b.cw[j] + GAP; }
      const rowT = []; for (let r = 0, y = g0; r < b.rh.length; r++) { rowT.push(y); y += b.rh[r] + GAP; }
      const bus0 = g0 - GAP / 2;
      ks.forEach((c, i) => { const j = i % b.k, r = Math.floor(i / b.k), vc = colL[j] + b.cw[j] / 2, uT = rowT[r];
        // 둘째 줄부터는 열 사이 통로로 내려간다. 마지막 열은 왼쪽 통로를 쓰고, 열이 하나면 상자 오른쪽 바깥(부모 통로와 겹치지 않게 1/3 지점)으로
        const vk = j < b.k - 1 ? colL[j] + b.cw[j] + GAP / 2 : b.k > 1 ? colL[j] - GAP / 2 : colL[j] + b.cw[j] + GAP / 3;
        put(c, vc, uT, A, r === 0 && straight && b.k === 1 && Math.abs(vc - vp) < .5 ? [pre[0]] : r === 0 ? [...pre, S(A, vc, bus0)] : [...pre, S(A, vk, bus0), S(A, vk, uT - GAP / 2), S(A, vc, uT - GAP / 2)]); }); };
    const put = (c, vc, uT, A, route) => { const b = A.B.get(c), hu = uT + TOP(c, A.o), hub = S(A, vc, hu);
      OUT.set(c.hub.id, { x: hub[0], y: hub[1], th12: A.th12, route: [...route, hub] });
      const ks = KO.get(c) || []; if (!ks.length) return;
      const g0 = uT + b.h0 + GAP, port = vc + b.w0 / 2 - 12;   // 자기 층의 3시 쪽 가장자리로 나가 그 줄을 따라 자식 격자 위 통로로
      lay(ks, b, vc - b.W / 2, g0, A, [hub, S(A, port, hu), S(A, port, g0 - GAP / 2)], vc); };
    const R = L.comps[0], kids = KO.get(R) || [];
    OUT.set(R.hub.id, { x: R.hub.x, y: R.hub.y, th12: -90 });
    if (!kids.length) return { out: OUT, arms: new Map(armsOf || []) };
    const Bv = new Map(), Bu = new Map(), Bh = new Map(); for (const k of kids) { measure(k, 'v', Bv); measure(k, 'u', Bu); measure(k, 'h', Bh); }
    const hwR = HW(R, 'v'), Ht = TOP(R, 'v') + 16, Hb = DEP(R, 'v') + 16;   // 맨 위 주제의 상자: 개념은 9시, 목록은 6시
    const ARMS = [{ g: [0, 1], p: [1, 0], hz: false, o: 'v', th12: -90, B: Bv }, { g: [1, 0], p: [0, -1], hz: true, o: 'h', th12: 180, B: Bh },
                  { g: [0, -1], p: [-1, 0], hz: false, o: 'u', th12: 90, B: Bu }, { g: [-1, 0], p: [0, 1], hz: true, o: 'h', th12: 0, B: Bh }];
    // 팔의 격자(폭 W = 팔에 수직인 길이, 깊이 H)에서 팔이 차지하는 화면 상자와 격자의 시작 v
    // 팔 격자는 바람개비처럼 맨 위 주제 상자의 한쪽 모서리에 맞춘다(이웃 팔과 엇갈리게). solo: 쓰는 팔이 하나뿐이면 가운데에 둔다
    const span = (A, P, i, solo = false) => { if (!P) return null; const { W, H } = P, hw = solo ? Infinity : hwR;
      if (i === 0) { const x1 = Math.min(hw, W / 2); return { box: [x1 - W, Hb + GAP, x1, Hb + GAP + H], O: [0, Hb + GAP], v0: x1 - W }; }
      if (i === 1) { const y0 = Math.max(solo ? -Infinity : -Ht, -W / 2); return { box: [hwR + GAP, y0, hwR + GAP + H, y0 + W], O: [hwR + GAP, 0], v0: -(y0 + W) }; }
      if (i === 2) { const x0 = Math.max(-hw, -W / 2); return { box: [x0, -Ht - GAP - H, x0 + W, -Ht - GAP], O: [0, -Ht - GAP], v0: -(x0 + W) }; }
      const y1 = Math.min(solo ? Infinity : Hb, W / 2); return { box: [-hwR - GAP - H, y1 - W, -hwR - GAP, y1], O: [-hwR - GAP, 0], v0: y1 - W }; };
    const order = new Map(kids.map((k, i) => [k, i])), members = ARMS.map(() => []);
    const packArm = (i, ks) => (ks.length ? best(ks, ARMS[i].B, ARMS[i].hz ? 1 / ASP : ASP, 0, 0) : null);
    const score = (packs) => { let x0 = -hwR, y0 = -Ht, x1 = hwR, y1 = Hb;
      packs.forEach((P, i) => { const sp = span(ARMS[i], P, i); if (sp) { x0 = Math.min(x0, sp.box[0]); y0 = Math.min(y0, sp.box[1]); x1 = Math.max(x1, sp.box[2]); y1 = Math.max(y1, sp.box[3]); } });
      const w = x1 - x0, h = y1 - y0; return w * h * (1 + Math.abs(Math.log(w / h / ASP))); };
    // 큰 가지부터, 전체 상자가 가장 작고 화면 비율에 가까워지는 팔에 넣는다. 팔은 마운트 때 어림한 크기로 한 번만 고르고,
    // 잰 크기로 다시 놓거나 하나씩 열기를 놓을 때(armsOf)는 그대로 두어 주제마다 방향이 늘 같다
    const area = (k) => Bv.get(k).w * Bv.get(k).h;
    if (armsOf) for (const k of kids) { const i = armsOf.get(k.hub.id); if (i != null) members[i].push(k); }
    for (const k of [...kids].filter((k) => !armsOf || armsOf.get(k.hub.id) == null).sort((a, b) => area(b) - area(a))) { let bi = 0, bs = Infinity;
      for (let i = 0; i < 4; i++) { const packs = members.map((m, j) => packArm(j, (j === i ? [...m, k] : m).sort((a, b) => order.get(a) - order.get(b)))), sc = score(packs); if (sc < bs) { bs = sc; bi = i; } }
      members[bi].push(k); members[bi].sort((a, b) => order.get(a) - order.get(b)); }
    const arms = new Map([...(armsOf || []), ...members.flatMap((m, i) => m.map((k) => [k.hub.id, i]))]);
    // 맨 위 주제에서 나가는 길: 3시 쪽(하위 주제 자리, 비어 있다)으로 나가 상자 둘레의 통로를 돌아 그 팔의 첫 통로로
    const E1 = [hwR + GAP / 2, 0], hub0 = [R.hub.x, R.hub.y], port0 = [hwR - 12, 0];
    const lead = [[hub0, port0, E1], [hub0, port0, E1], [hub0, port0, E1, [hwR + GAP / 2, -Ht - GAP / 2]], [hub0, port0, E1, [hwR + GAP / 2, Hb + GAP / 2], [-hwR - GAP / 2, Hb + GAP / 2]]];
    lead[0].push([hwR + GAP / 2, Hb + GAP / 2]);
    const solo = straight && members.filter((m) => m.length).length === 1;
    ARMS.forEach((A, i) => { const ks = members[i]; if (!ks.length) return; const P = packArm(i, ks), sp = span(A, P, i, solo); A.O = sp.O; lay(ks, P, sp.v0, 0, A, lead[i]); });
    return { out: OUT, arms };
  }
  // 하나씩 열기의 배치: 모든 주제를 펼친 정해진 자리와 같은 묶음 배치(crossCanon)에 지금 펼친 층들(지나온 길과 고정 노드로 가는 길)만 넘긴다.
  // 층의 크기는 방금 부모 기준으로 놓아 본 배치(L)에서 층마다 자기 방향으로 잰다(접힌 목록 표 · 지금 층의 목록 자리까지).
  // 팔은 정해진 자리에서 고른 것(ARMS_OF)을 그대로 쓰므로 주제마다 방향(th12)이 어떤 길로 와도 같다. 주제 층만 묶음에 들어가고,
  // 주제가 아닌 노드로 들어간 층과 그 아래는 부모 기준 자리(settle)에 남는다
  function packPath(L) {
    if (!ARMS_OF || !L.comps.some((c) => c.parent && c.hub.k === 'topic' && c.parent.hub.k === 'topic')) return null;
    const meas = new Map(), shelfOf = new Map(L.sets.map((st) => [st.comp, st.shelves.flatMap((s) => s.items)]));
    for (const c of L.comps) { if (c.hub.k !== 'topic') continue;
      const a = rad(c.th12 + 180), g = [Math.cos(a), Math.sin(a)], pv = [g[1], -g[0]]; let u0 = 0, u1 = 0, v0 = 0, v1 = 0;
      const pts = [...L.boxes(c).flatMap(([x0, y0, x1, y1]) => [[x0, y0], [x1, y0], [x0, y1], [x1, y1]]), ...(shelfOf.get(c) || []).flatMap(extentPts)];
      for (const [x, y] of pts) { const dx = x - c.hub.x, dy = y - c.hub.y, u = dx * g[0] + dy * g[1], v = dx * pv[0] + dy * pv[1];
        u0 = Math.min(u0, u); u1 = Math.max(u1, u); v0 = Math.min(v0, v); v1 = Math.max(v1, v); }
      const m = { top: -u0, dep: u1, hw: Math.max(-v0, v1) }; meas.set(c.hub.id, { v: m, u: m, h: m }); }
    // 층 사이는 적어도 부모 기준으로 놓던 거리만큼 둔다(세로 팔은 두 반지름 합의 3/4, 가로 팔은 6할에 이름표 폭): 작은 층끼리 붙지 않게
    for (const c of L.comps) { const p = c.parent, m = meas.get(c.hub.id); if (!p || !m || !meas.has(p.hub.id)) continue;
      const hz = Math.abs(Math.cos(rad(c.th12 + 180))) > .5, D0 = hz ? (p.cp.E + c.cp.E) * .62 + 320 : (p.cp.E + c.cp.E) * .75 + 40;
      m.v.top = Math.max(m.v.top, D0 - meas.get(p.hub.id).v.dep - (p === L.comps[0] ? 40 : 24) - 80 - 24); }
    return crossCanon(L, meas, ARMS_OF, true).out;
  }
  // 정해진 자리: 모든 주제를 지금과 같은 규칙으로 한꺼번에 펼쳐(목록 자리까지 비워) 한 번 잰다. 하나씩 들어가든 전체를 펼치든 주제는 이 자리에 선다
  const allExpand = () => { const m = new Map(); for (const tp of topics.values()) { const set = new Set(); for (const s2 of tp.sub) set.add(`topic:${s2}`); m.set(tp.id, set); } return m; };
  // 재기 · 정해진 자리 짓기의 상태: 전부 펼치고 거르기 없이(지금 화면의 상태는 읽지도 바꾸지도 않는다)
  // cps: 층마다의 나침반(compassOf). 거르기가 없으니 정해진 자리 짓기와 세 방향 재기가 같은 것을 한 번만 모은다
  const MCPS = new Map(), measS = (place, canon = false) => ({ mode: 'all', path: [topics.get(ROOT)], place, dirs: place, canon, range: null, koff: new Set(), filt: new Map(), cps: MCPS });
  { CANONL = layoutTree(topics.get(ROOT), allExpand(), null, measS(null, true)); const r = crossCanon(CANONL); TPOS = r.out; ARMS_OF = r.arms; }
  // 팔 정하기: 층의 실제 크기를 재서 팔을 한 번 고른다. 어림한 크기로 고르면 배치가 3할 넘게 넓어지고, 뒤늦게 다시
  // 고르면 그 뒤 하나씩 열기의 방향이 바뀐다. 그래서 마운트 직후 한가할 때 잰다(같은 주제 나무는 MEAS_CACHE).
  // 이미 깊이 들어가 있으면 한 번 다시 그려 방향을 맞춘다
  const measSig = `${ROOT}|${topics.size}|${[...topics.values()].reduce((h, t) => (h * 31 + (t.count || 0) + t.path.length) % 1000000007, 7)}`;
  function settleArms() {
    if (ARMS_FIXED || !CANONL) return;
    // measIdle 은 requestIdleCallback 의 번호다(없을 때만 setTimeout). clearTimeout 에 넘기면 번호가 같은 남의 타이머가 지워진다
    (globalThis.cancelIdleCallback || clearTimeout)(measIdle);
    let meas = MEAS_CACHE.get(measSig);
    if (!meas) { meas = measureCross(); MEAS_CACHE.set(measSig, meas); if (MEAS_CACHE.size > 8) MEAS_CACHE.delete(MEAS_CACHE.keys().next().value); }
    const r = crossCanon(CANONL, meas); TPOS = r.out; ARMS_OF = r.arms; ARMS_FIXED = true;
  }
  if (MEAS_CACHE.has(measSig)) settleArms();
  else measIdle = (globalThis.requestIdleCallback || ((f) => setTimeout(f, 300)))(() => { if (ARMS_FIXED || !root.isConnected) return;
    settleArms(); if (path.length > 1) render(); });
  // 층의 실제 크기: 모든 층을 세 방향(아래 팔, 위 팔, 가로 팔)으로 세워 그려 보고, 층마다 허브에서 잰 범위를 남긴다.
  // 기간·종류 거르기 없이 잰다(거른 뒤에 다시 켜도 상자가 모자라지 않게)
  function measureCross() {
    const out = new Map(), rootId = topics.get(ROOT).id;
    for (const [o, t12] of [['v', -90], ['u', 90], ['h', 180]]) {
        const T = new Map([...TPOS].map(([id, t]) => [id, { x: t.x, y: t.y, th12: id === rootId ? -90 : t12 }])), a = rad(t12 + 180), g = [Math.cos(a), Math.sin(a)];
        const L = layoutTree(topics.get(ROOT), allExpand(), null, measS(T)), own = new Map(L.comps.map((c) => [c.hub.id, [c.hub, ...c.items.filter((o) => o.id && !o.link)]]));
        for (const st of L.sets) for (const sh of st.shelves) own.get(st.hub.id)?.push(...sh.items);
        for (const [id, list] of own) { const h = list[0]; let u0 = 0, u1 = 0, v0 = 0, v1 = 0;
          for (const [x, y] of list.flatMap(extentPts)) { const dx = x - h.x, dy = y - h.y, u = dx * g[0] + dy * g[1], v = dx * g[1] - dy * g[0];
            u0 = Math.min(u0, u); u1 = Math.max(u1, u); v0 = Math.min(v0, v); v1 = Math.max(v1, v); }
          if (!out.has(id)) out.set(id, {}); out.get(id)[o] = { top: -u0, dep: u1, hw: Math.max(-v0, v1) }; } }
    return out; }

  { const R = ctx.restore, rp = R ? R.path.map(byAny).filter(Boolean) : [];
    if (R && rp.length) {
      for (const [k, v] of R.more || []) MORE.set(k, v);
      for (const k of R.koff || []) KOFF.add(k);
      TSEL = R.tsel || null; QUERY = R.query || '';
      hist = (R.hist || []).map((h) => h.filter((id) => byAny(id))).filter((h) => h.length); hi = Math.min(R.hi ?? hist.length - 1, hist.length - 1);
      if (R.view) KEEPCAM = true;
      setPath(rp, !hist.length);
      if (R.view) setVB(R.view);
    } else {
      const sp = ctx.startPath ? ctx.startPath.map(byAny).filter(Boolean) : [];   // 시작 길이 이 데이터에 없는 주제를 가리키면 뿌리에서 시작한다
      setPath(sp.length ? sp : [topics.get(ROOT)]);
    } }
  // ── 렌즈 패널: 옵션(기간)과 범례(종류는 누르면 끄고 켠다 = 범례가 곧 필터) ────────────────────────────────
  const mark = (k) => `<svg class="cmp-mk" viewBox="-8 -8 16 16" aria-hidden="true">${k === "topic" ? topicRing(6, "currentColor", 1.8) : k === "bridge" ? `<path d="M-4.2 -4.2A6 6 0 0 1 4.2 -4.2M4.2 4.2A6 6 0 0 1 -4.2 4.2" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/><circle r="2" fill="currentColor"/>` : shape(k, 0, 0, 5.5, "currentColor")}</svg>`;
  const kindsHTML = () => {
    const c = {}; for (const n of nodes) { if (!matchQ(n) || (TSEL && (!when(n) || when(n) < TSEL[0] || when(n) > TSEL[1]))) continue; const k = kindKey(n); c[k] = (c[k] || 0) + 1; }
    const kinds = ["concept", "doc", "decision", "record", "episode", "bridge"].map((k) => `<button class="cmp-lk${KOFF.has(k) ? " off" : ""}" type="button" data-cmp-k="${k}" aria-pressed="${!KOFF.has(k)}">${mark(k)}<span>${KIND[k]}</span><em>${c[k] || 0}</em></button>`).join("");
    return `<div class="cmp-kinds">${kinds}</div>`; };
  const legendHTML2 = () => {
    const areas = [...AREA_COL].map(([a, col]) => `<span class="cmp-sw"><i style="background:${col}"></i>${a.split("/").pop()}</span>`).join("");
    const row = (art, name) => `<div class="cmp-lr"><svg viewBox="0 0 44 16" aria-hidden="true">${art}</svg><span>${name}</span></div>`;
    return `${row(mark("topic").replace('class="cmp-mk"', 'x="14" width="16"'), esc(KIND.topic), "")}
      ${row(`<path d="M2 6.6L42 7.6L42 8.4L2 9.4Z" fill="currentColor"/>`, esc(C.legendRows.meaning), "")}
      ${row(`<path d="M2 7.4H42" stroke="currentColor" stroke-width="1.4"/><path d="M19 4L23 8L19 12" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>`, esc(C.legendRows.order), "")}
      ${row(`<path d="M4 2V10Q4 14 8 14H40" fill="none" stroke="currentColor" stroke-width="2"/>`, esc(C.legendRows.attached), "")}
      ${row(`<circle cx="22" cy="8" r="5" fill="currentColor"/><path d="M15.6 4.2A6.4 6.4 0 0 0 15.6 11.8" fill="none" stroke="currentColor" stroke-width="2"/>`, esc(C.legendRows.otherTopic), "")}
      ${row(`<path d="M17.8 3.8A6 6 0 0 1 26.2 3.8M26.2 12.2A6 6 0 0 1 17.8 12.2" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/><circle cx="22" cy="8" r="2" fill="currentColor"/>`, esc(KIND.bridge), "")}
      <div class="cmp-sws">${areas}</div>`; };
  // ── 시간(시안의 시간 모달을 LNB 로): 날마다의 수(종류별로 쌓음, 끌어서 기간)와 기간 버튼 ─────
  let TSCOPE = 'all';
  const jewel = (i) => `#${topicJewels[themeMode()][i].toString(16).padStart(6, '0')}`;   // 토큰 값을 읽어 쓴다(글자로 박지 않는다)
  const TK = () => [['decision', KIND.decision, jewel(3)], ['record', KIND.record, jewel(1)], ['episode', KIND.episode, jewel(0)], ['concept', C.conceptAndDoc, jewel(2)]];
  const tKind = (n) => (n.k === 'decision' || n.k === 'record' || n.k === 'episode' ? n.k : 'concept');
  function scopeNodes() { if (TSCOPE === 'all') return dated; const s = new Set(); if (LZ) for (const o of LZ.items) if (o.src && o.src.k !== 'topic' && o.role !== 'hub') s.add(o.src);
    if (LZ && LZ.focus) { const G = gather(LZ.focus.hub.src); for (const l of Object.values(G.shelf)) for (const n of l) s.add(n); } return [...s].filter((n) => when(n)); }
  function timeHTML() {
    const ns = scopeNodes().filter((n) => matchQ(n) && !KOFF.has(kindKey(n))), W = Math.max(300, Math.round((pop.clientWidth || 520) - 34)), H = 80, padB = 14, bw = W / allDays.length, K = TK();
    const stack = allDays.map(() => ({ decision: 0, record: 0, episode: 0, concept: 0 })); for (const n of ns) { const i = dIdx.get(when(n)); if (i != null) stack[i][tKind(n)]++; }
    const tot = stack.map((q) => q.decision + q.record + q.episode + q.concept), maxC = Math.max(1, ...tot), sc = (c) => (H - padB - 4) * c / maxC;
    const i0 = TSEL ? (dIdx.get(TSEL[0]) ?? 0) : -1, i1 = TSEL ? (dIdx.get(TSEL[1]) ?? allDays.length - 1) : -1; let bars = '';
    allDays.forEach((d, i) => { let y = H - padB; const inR = !TSEL || (i >= i0 && i <= i1);
      for (const [k, , c] of K) { const h = sc(stack[i][k]); if (!h) continue; y -= h; bars += `<rect x="${(i * bw).toFixed(1)}" y="${y.toFixed(1)}" width="${Math.max(1, bw - .4).toFixed(1)}" height="${h.toFixed(1)}" style="fill:${c};opacity:${inR ? .9 : .22}"/>`; }
      if (i === 0) bars += `<text x="0" y="${H - 2}">${esc(C.before)}</text>`; else if (d.endsWith('-01') && i * bw > 22) bars += `<text x="${(i * bw + 1).toFixed(1)}" y="${H - 2}">${d.slice(5)}</text>`; });
    if (TSEL) bars += `<rect class="sel" x="${(i0 * bw).toFixed(1)}" y="1" width="${((i1 - i0 + 1) * bw).toFixed(1)}" height="${H - padB - 1}" rx="3"/>`;
    const rows = ns.filter((n) => (!TSEL || (when(n) >= TSEL[0] && when(n) <= TSEL[1])) && matchQ(n) && !KOFF.has(kindKey(n)));
    const cnt = K.map(([k, name]) => `${name} ${rows.filter((n) => tKind(n) === k).length}`).join(' · '), undated = nodes.filter((n) => !when(n)).length;
    const lab = TSEL ? `${TSEL[0] === '0000-01-01' ? C.start : TSEL[0]} ~ ${TSEL[1]}` : `${allDays[1]} ~ ${allDays[allDays.length - 1]}`;
    return `      <div class="cmp-seg">${[['7', C.days(7)], ['30', C.days(30)], ['all', C.all]].map(([k, l]) => `<button class="btn btn-sm" data-cmp-r="${k}">${l}</button>`).join('')}</div>
      <p class="cmp-range">${lab}</p>
      <div class="cmp-tleg">${K.map(([k, name, c]) => `<span><i style="background:${c}"></i>${name} ${rows.filter((n) => tKind(n) === k).length}</span>`).join('')}${undated ? `<span title="${esc(C.undatedTip)}">${esc(C.undated(undated))}</span>` : ''}</div>
      <svg class="cmp-bars" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" data-bars>${bars}</svg>`;
  }
  // 붙은 곳: 기록·사건·결정이 붙은 뜻의 노드(개념·문서). LNB 트리가 그 아래로 들여 쓴다
  const hostOf = (n) => { if (isMean(n)) return null; for (const e of adj.get(n.id) || []) if (ATTACH.has(e.t) && e.a === n && isMean(e.b)) return e.b; return null; };
  // ── 시간 · 범례 창: 머리의 버튼 바로 아래, 버튼 왼쪽 끝과 창 왼쪽 끝을 맞춘다. 밖을 누르거나 Esc 면 닫힌다 ─────────────
  let POP = '';
  const btnOf = (k) => tools.querySelector(`[data-pop="${k}"]`);
  function placePop() { const b = POP && btnOf(POP); if (!b) return; const r = b.getBoundingClientRect();
    pop.style.left = `${Math.round(r.left)}px`; pop.style.top = `${Math.round(r.bottom + 6)}px`; pop.style.maxWidth = `${Math.max(280, window.innerWidth - r.left - 16)}px`; }
  function paintSide() { if (!POP) return;
    pop.innerHTML = POP === 'time' ? `<div class="cmp-sec cmp-time">${timeHTML()}</div>` : `<div class="cmp-sec">${kindsHTML()}<div class="cmp-legend">${legendHTML2()}</div></div>`; }
  function openPop(k) { if (POP === k) { closePop(); return; } POP = k; pop.dataset.pop = k; pop.hidden = false;
    tools.querySelectorAll('[data-pop]').forEach((b) => b.setAttribute('aria-expanded', String(b.dataset.pop === k))); placePop(); paintSide(); if (k === 'time') paintSide(); }
  function closePop() { if (!POP) return; POP = ''; pop.hidden = true; pop.innerHTML = ''; tools.querySelectorAll('[data-pop]').forEach((b) => b.setAttribute('aria-expanded', 'false')); highlight(null); }
  tools.addEventListener('click', (e) => { const b = e.target.closest('[data-pop]'); if (b) openPop(b.dataset.pop);
  });
  onWin('pointerdown', (e) => { if (POP && !pop.contains(e.target) && !tools.contains(e.target)) closePop(); }, true);
  onWin('keydown', (e) => { if (e.key === 'Escape' && POP) { e.stopImmediatePropagation(); closePop(); } }, true);
  onWin('resize', () => { if (POP) { placePop(); paintSide(); } });
  // ── LNB 트리의 줄: 거르개(검색 ∧ 기간 ∧ 종류)를 지난 노드를 주제별로. 뜻의 층(depth 0) 아래에 붙은 것(depth 1) ──────
  const passes = (n) => n.k !== 'topic' && matchQ(n) && !KOFF.has(kindKey(n)) && (!TSEL || (!!when(n) && when(n) >= TSEL[0] && when(n) <= TSEL[1]));
  function treeGroups() {
    const by = new Map(); for (const n of nodes) { if (!passes(n)) continue; const d = n.d; if (!by.has(d)) by.set(d, []); by.get(d).push(n); }
    const newestFirst = (a, b) => (when(b) || '').localeCompare(when(a) || '') || titleOf(a).localeCompare(titleOf(b));
    const out = [];
    for (const d of [...by.keys()].sort()) { const its = by.get(d), inList = new Set(its.map((n) => n.id)), kids = new Map(), loose = [], rows = [];
      for (const n of its) { if (isMean(n)) continue; const h = hostOf(n); if (h && inList.has(h.id)) { if (!kids.has(h.id)) kids.set(h.id, []); kids.get(h.id).push(n); } else loose.push(n); }
      const R = (n, depth) => rows.push({ id: n.id, title: titleOf(n), kind: n.k, depth });
      for (const m of its.filter(isMean).sort(newestFirst)) { R(m, 0); for (const c of (kids.get(m.id) || []).sort(newestFirst)) R(c, 1); }
      for (const n of loose.sort(newestFirst)) R(n, 0);
      out.push({ domain: d, rows }); }
    return out; }
  // 거르개가 걸려 있고 남은 것이 적으면 그 주제들을 펴서 보인다(많으면 기본 펼침 그대로: 수천 줄을 쏟지 않는다)
  function treeChanged(reset = false) { const g = treeGroups(), n = g.reduce((x, q) => x + q.rows.length, 0), on = !!QUERY || !!TSEL || KOFF.size > 0;
    ctx.onTree?.({ reveal: on && n <= 300 ? g.map((q) => q.domain) : [], reset }); }
  const treeMark = (k) => mark(k);
  const highlight = (ids) => { svg.classList.toggle('hot', !!ids && ids.size > 0); svg.querySelectorAll('#cmp-nodes .node').forEach((g) => g.classList.toggle('pick', !!ids && ids.has(g.dataset.id))); };
  pop.addEventListener("click", (e) => {
    const kb = e.target.closest("[data-cmp-k]"); if (kb) { const k = kb.dataset.cmpK; KOFF.has(k) ? KOFF.delete(k) : KOFF.add(k);
prevPos = new Map(); render(); treeChanged(); return; }
    const rb = e.target.closest("[data-cmp-r]"); if (rb) { const k = rb.dataset.cmpR; TSEL = k === "all" ? null : [allDays[Math.max(1, allDays.length - +k)], allDays[allDays.length - 1]]; dimLens(); paintSide(); treeChanged(); return; }
  });
  let br = null;
  const barsEl = () => pop.querySelector("[data-bars]");
  const day = (e) => { const r = barsEl().getBoundingClientRect(); return Math.max(0, Math.min(allDays.length - 1, Math.floor((e.clientX - r.left) / (r.width / allDays.length)))); };
  const val = (i, end) => (i === 0 ? (end ? allDays[1] : "0000-01-01") : allDays[i]);
  pop.addEventListener("pointerdown", (e) => { if (!e.target.closest("[data-bars]")) return; br = day(e); pop.setPointerCapture(e.pointerId); TSEL = [val(br), val(br, true)]; dimLens(); });
  pop.addEventListener("pointermove", (e) => { if (br == null) return; const b2 = day(e); TSEL = [val(Math.min(br, b2)), val(Math.max(br, b2), true)]; dimLens(); });
  pop.addEventListener("pointerup", () => { if (br == null) return; br = null; paintSide(); treeChanged(); });
  { const _r3 = render; render = function () { _r3(); paintSide(); }; }
  const destroy = () => { AC.abort(); (globalThis.cancelIdleCallback || clearTimeout)(measIdle); cancelAnimationFrame(flyRaf); cancelAnimationFrame(traf); cancelAnimationFrame(flipRaf); clearTimeout(flipTo); host.innerHTML = ""; side.remove(); tools.remove(); pop.remove(); ctx.onDestroy?.(); };
  if (import.meta.env?.DEV) (window as any).__compass = { open: (ids) => setPath(ids.map(byAny)), lz: () => LZ, pin: togglePin, range: (r) => { TSEL = r; dimLens(); } };
  // 목록에서 고른 노드로: 지금 렌즈에 이미 사슬이 있으면 그 사슬로(맥락 유지), 없으면 주제 사슬을 따라 편다
  function goTo(id) { const n = byAny(id); if (!n) return; const o = LZ && LZ.items.find((q) => q.id === n.id && q.chain);
    WIDE = true; if (o) setPath(o.role === 'hub' ? o.chain : [...o.chain, n]); else setPath(chainTo(n)); }
  const centre = () => (path.length ? path[path.length - 1].id : '');
  // 같은 범위를 다시 그릴 때(변경 감지 새로고침 · 쓰고 난 뒤) 보던 자리를 그대로 잇는다: 길 · 앞뒤 기록 ·
  // 더 연 목록 · 끈 종류 · 기간 · 카메라 · 찾기. 새 데이터에 없는 노드는 버린다
  const snapshot = () => ({ path: path.map((n) => n.id), hist, hi, mode: 'path', filt: [], more: [...MORE],
    koff: [...KOFF], tsel: TSEL, view: VIEW, query: QUERY });
  // 밝기 테마가 바뀌면 주제 색 표(밝은 판 · 어두운 판)를 다시 짓고 다시 그린다
  const retheme = () => { buildTopicColours(); render(); };
  return { destroy, side, tools, filter: setQuery, open: goTo, tree: treeGroups, mark: treeMark, colour: (p) => (!p || p === ROOT ? '' : topicColour(p)), centre, snapshot, retheme };   // 뿌리는 색을 입지 않는다(LNB 줄은 잉크 그대로)
}
