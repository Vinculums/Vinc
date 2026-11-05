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
import { tintPlan } from "../view-tint";
import { topicJewels } from "../theme";

export interface CompassData { nodes: any[]; edges: [string, string, string][] }
export interface CompassCtx { startPath?: string[]; onChange?: () => void; onOpen?: (id: string) => void; onDestroy?: () => void;
  onTree?: (o: { reveal: string[]; reset: boolean }) => void }
export interface CompassHandle { destroy: () => void; side: HTMLElement; tools: HTMLElement; filter: (q: string) => void; open: (id: string) => void;
  tree: () => { domain: string; rows: { id: string; title: string; kind: string; depth: number }[] }[]; mark: (kind: string) => string; colour: (domain: string) => string; centre: () => string }

const themeMode = () => (document.documentElement.dataset.theme === "dark" ? "dark" : "light");

/** 그래프 탭의 읽기(GraphNode·GraphEdge)를 나침반이 읽는 모양으로. 주제(domain) 노드는 노드의 domain 사슬에서 다시 만든다. */
const idDate = (id: string) => { const m = /-(20\d\d-\d\d-\d\d)$/.exec(String(id)); return m ? m[1] : ""; };
export function compassDataFrom(nodes: any[], edges: any[]): CompassData {
  const out = nodes.filter((n) => n.kind !== "domain" && n.kind !== "area").map((n) => ({
    id: n.id, k: String(n.id).startsWith("concept:doc.") ? "doc" : n.kind, t: n.label || n.title || n.id, d: n.domain || "vinc",
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
const CMP_HTML = () => `<div class="cmp-stage"><svg class="g" id="cmp-g" role="group" aria-label="${escHtml(COPY.ui.compass.lens)}"></svg><div id="cmp-pins"><svg class="pl" aria-hidden="true"></svg><div class="pcs"></div></div></div><div id="cmp-menu" role="menu" hidden></div><div id="cmp-tip"></div>`;
/** LNB 맨 위의 검색 칸(topbar.ts, setSearchFilter) · 시간 · 범례의 종류가 한 거르개다: 함께 건 결과가 나침반과 LNB 트리(lnb.ts
 *  setLnbGraphSource)에 같이 나온다. LNB 에는 고정 판만 서고, 시간과 범례는 머리의 "그래프" 글자 오른쪽 버튼이 여는 창이다. */
const SIDE_HTML = `<div class="cmp-sec" id="cmp-dock" hidden></div>`;
const TOOLS_HTML = () => `<button class="cmp-tbtn" type="button" data-pop="time" aria-haspopup="dialog" aria-expanded="false">${escHtml(COPY.ui.compass.time)}</button><button class="cmp-tbtn" type="button" data-pop="legend" aria-haspopup="dialog" aria-expanded="false">${escHtml(COPY.ui.compass.legend)}</button><button class="cmp-tbtn" type="button" data-all aria-pressed="false">${escHtml(COPY.ui.compass.all)}</button>`;

const C0 = () => COPY.ui.compass;
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
  const MEANING = new Set(['RELATED_TO', 'PREREQUISITE_OF', 'DERIVES_FROM', 'INHERITS_FROM', 'RETURNS_TO', 'CONNECTS']);
  const ATTACH = new Set(['ABOUT', 'ATTACHES_TO', 'MENTIONS']);
  const nodes = W0.nodes, byId = new Map(nodes.map((n) => [n.id, n]));
  const edges = W0.edges.map(([a, b, t]) => ({ a: byId.get(a), b: byId.get(b), t })).filter((e) => e.a && e.b);
  const adj = new Map(nodes.map((n) => [n.id, []])); for (const e of edges) { adj.get(e.a.id).push(e); adj.get(e.b.id).push(e); }
  const newest = nodes.map((n) => n.ts).filter(Boolean).sort().pop();
  const fresh = (n) => { const w = when(n); return !!w && (Date.parse(newestW()) - Date.parse(w)) / 864e5 <= 7; };   // '최근' = 내용 시간으로 7일 안
  let _nw = ''; const newestW = () => _nw || (_nw = nodes.map(when).filter(Boolean).sort().pop() || '');
  // 주제: 노드의 domain 사슬에서 만든다. 'vinc/agent/roles' → vinc, vinc/agent, vinc/agent/roles
  const topics = new Map();
  const tget = (p) => { if (!topics.has(p)) topics.set(p, { id: `topic:${p}`, path: p, k: 'topic', direct: [], sub: new Set(), count: 0 }); return topics.get(p); };
  for (const n of nodes) { const parts = (n.d || 'vinc').split('/'); for (let i = 1; i <= parts.length; i++) { const t = tget(parts.slice(0, i).join('/')); t.count++; if ((n.ts || '') > (t.ts || '')) t.ts = n.ts; if (i < parts.length) t.sub.add(parts.slice(0, i + 1).join('/')); } tget(n.d || 'vinc').direct.push(n); }
  // ── 주제 색: 그래프 보기와 같은 규칙(tintPlan)이다. 영역마다 보석색 하나, 그 안의 주제는 색상각을 조금씩 돌린다 ─────────────
  let TOPIC_COL = new Map(), AREA_COL = new Map();
  function buildTopicColours() { const plan = tintPlan([...topics.keys()], themeMode()); TOPIC_COL = plan.topics; AREA_COL = plan.areas; }
  const topicColour = (p) => { if (!p || p === 'vinc') return 'hsl(var(--muted-foreground))';   // 맨 위 주제는 어느 영역도 아니라 중립색
    const s = (p || 'vinc').split('/'); for (let i = s.length; i >= 1; i--) { const c = TOPIC_COL.get(s.slice(0, i).join('/')); if (c) return c; } return 'hsl(var(--muted-foreground))'; };
  const topicOf = (n) => (n.k === 'topic' ? n.path : n.d || 'vinc');
  const colv = (n) => topicColour(topicOf(n)), mix = (c, p) => `color-mix(in srgb, ${c} ${p}%, var(--c-card))`;
  const isMean = (n) => ['concept', 'doc', 'bridge'].includes(n.k) || n.m > 0;
  const titleOf = (n) => { if (n.k === 'topic') return n.path.split('/').pop(); const s = (n.t || n.id).replace(/\s*\((?:\d{4}-\d\d-\d\d|pstack principle|Anthropic)[^)]*\)/g, '');
    return ['concept', 'doc', 'bridge'].includes(n.k) ? s.replace(/^([^:]{3,40}):.*$/, '$1') : s; };
  const short = (s, n) => (s.length > n ? s.slice(0, n - 1) + '…' : s);
  const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  const KIND = COPY.ui.compass.kinds;

  // ── 모양·빛살·표(문법) ─────────────────────────────────────────────────────────────────────
  const GAP = 4, P = (pts) => pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  const arcPts = (cx0, cy0, r, mid, half, n = 6) => { const o = []; for (let q = 0; q <= n; q++) { const a = mid - half + (2 * half * q) / n; o.push([cx0 + Math.cos(a) * r, cy0 + Math.sin(a) * r]); } return o; };
  function shape(kind, x, y, r, fill, hollow) {
    const st = hollow ? `fill="none" stroke="${fill}" stroke-width="1.6"` : `fill="${fill}"`;
    if (kind === 'doc') return `<g class="shape"><circle cx="${x}" cy="${y}" r="${r}" ${st}/><rect x="${x - r * .42}" y="${y - r * .12}" width="${r * .84}" height="${r * .24}" rx="${r * .1}" fill="var(--c-card)"/></g>`;
    if (kind === 'decision') { const d = r * 1.25; return `<path class="shape" d="M${x} ${y - d}L${x + d} ${y}L${x} ${y + d}L${x - d} ${y}Z" ${st}/>`; }
    if (kind === 'record') { const s = r * .9; return `<rect class="shape" x="${x - s}" y="${y - s}" width="${2 * s}" height="${2 * s}" rx="${r * .25}" ${st}/>`; }
    if (kind === 'episode') { const h = r * 1.2; return `<path class="shape" d="M${x} ${y - h}L${x + h * 1.05} ${y + h * .75}L${x - h * 1.05} ${y + h * .75}Z" ${st} stroke-linejoin="round"/>`; }
    if (kind === 'bridge') return `<rect class="shape" x="${x - r * 1.5}" y="${y - r * .6}" width="${r * 3}" height="${r * 1.2}" rx="${r * .6}" ${st}/>`;
    return `<circle class="shape" cx="${x}" cy="${y}" r="${r}" ${st}/>`;
  }
  // 빛살: 가운데 쪽이 굵고 바깥으로 가늘다. 양끝은 노드와 같은 중심의 원호로, 틈만큼 떨어져 잘린다
  function ray(a, b, i, w0 = 3, w1 = .5) {
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
      const parent = c.path.includes('/') ? topics.get(c.path.split('/').slice(0, -1).join('/')) : null;
      if (parent) up.push({ n: parent, rel: C.rel.parentTopic, main: true });
      const childOf = (d) => (d && d.startsWith(c.path + '/') ? `${c.path}/${d.slice(c.path.length + 1).split('/')[0]}` : null);
      // 다리: 잇는 끝이 이 주제의 하위 주제 둘 이상이면 오른쪽 하위 주제 사이로, 아니면 왼쪽 개념 부채에
      for (const n of c.direct.filter(isMean)) {
        if (n.k === 'bridge') { const subs = [...new Set(bridgeEnds(n).map((m) => childOf(m.d)).filter(Boolean))]; if (subs.length >= 1) { bridges.push({ n, subs, outside: bridgeEnds(n).some((m) => !childOf(m.d)) }); continue; } }
        left.push({ n, rel: C.rel.concept });
      }
      left.sort((a, b) => b.n.m - a.n.m || b.n.g - a.n.g);
      for (const n of c.direct.filter((x) => !isMean(x))) (shelf[n.k] || shelf.record).push(n);
      for (const p of [...c.sub].sort((a, b) => topics.get(b).count - topics.get(a).count)) right.push({ n: topics.get(p), rel: C.rel.subtopic });
    } else {
      up.push({ n: topics.get(c.d || 'vinc'), rel: C.rel.topic, main: true });
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
    return { up: up.slice(0, 11), left: left.slice(0, 20), right: right.slice(0, 16), bridges, shelf,
             more: { up: Math.max(0, up.length - 11), left: Math.max(0, left.length - 20), right: Math.max(0, right.length - 16) } };
  }

  // ── 펼침: 들어갈 때의 나침반 모양을 그대로 유지하며 그 자리에서 펼친다 ─────────────────────────────────
  // 나침반의 시각은 '온 방향'에 맞춰 돈다: 12시는 늘 부모(온 곳) 쪽, 3시·9시는 그 양옆, 6시는 바깥(줄기와 목록).
  // 맨 위만 12시가 화면 위다. 펼친 노드는 부모 부채의 자기 자리에서 바깥으로 빠져나와, 거기서 자기 나침반을 편다.
  // 그래서 화면에는 들어온 길(펼친 사슬)이 한 번에 보이고, 각 층은 같은 모양이다. 근거: 렌즈 시안의 나침반 규칙
  const svg = $id('g');
  const ROWS = 18;
  const REL = { up: [-26, 26], left: [-150, -42], right: [42, 150] };   // 12시 방향(θ12)에서 잰 각도(도)
  const rad = (d) => d * Math.PI / 180, todeg = (r) => r * 180 / Math.PI;
  let path = [], prevPos = new Map(), LZ = null, VIEW = null, MODE = 'path', WIDE = false, KEEPCAM = false, PINCHAIN = new Set();
  let CANON = false, TPOS = null;
  const STRICT_FIXED = false;   // 참이면 하나씩 들어가는 보기도 전부 펼친 자리를 쓴다(주제 사이가 수천 단위로 멀어져 읽기 어려웠다)
  const useT = () => !CANON && TPOS && (MODE === 'all' || STRICT_FIXED);   // TPOS: 주제마다 정해진 자리(전부 펼친 배치에서 한 번 잰다). CANON: 그 배치를 재는 중
  const FILT = new Map();
const MORE = new Map();   // '허브|종류' → 목록에 더 보인 줄 수(긴 목록은 20개씩 더 연다)
  const KOFF = new Set();   // 범례에서 잠시 끈 종류. 저장하지 않는다: 그래프를 열 때마다 모두 켠 채 시작한다
  const kindKey = (n) => (n.k === 'decision' || n.k === 'record' || n.k === 'episode' || n.k === 'bridge' ? n.k : n.k === 'doc' ? 'doc' : 'concept');   // 노드 id → 펼칠 때 고른 갈래 {up, left, right, shelf}. 없으면 모두
  let RANGE = null;         // [시작, 끝] 날짜 문자열(YYYY-MM-DD). 없으면 전체 기간
  const fullDate = (s) => (!s ? '' : s.length === 4 ? s + '-01-01' : s.length === 7 ? s + '-01' : s.slice(0, 10));   // '2026' · '2026-03' 같은 덜 적힌 날짜는 그 첫날로
  // 날짜 = 그 일이 있었던 날: 내용 시간 → 유효 시작(→ 사건은 그날). 마지막으로 고친 때만 있는 노드는 날짜가 없는 것으로 본다(막대에 넣지 않고 흐리지도 않는다).
  const when = (n) => fullDate(n.ct || n.vf || (n.k === 'episode' ? n.ts : '') || '');
  const inRange = (n) => { const w = when(n); return !RANGE || !w || (w >= RANGE[0] && w <= RANGE[1]); };
  function fanPlace(list, center, a0, a1, rMin, gap, size) {
    const need = list.reduce((s, u) => s + 2 * size(u) + gap, 0), span = rad(a1 - a0), R = Math.max(rMin, need / span);
    let acc = rad(a0) + (span - need / R) / 2;
    return { R, pos: list.map((u) => { const w = (2 * size(u) + gap) / R, mid = acc + w / 2; acc += w; return { u, ang: mid, x: center.x + Math.cos(mid) * R, y: center.y + Math.sin(mid) * R }; }) };
  }
  const subR = (n) => Math.max(9, Math.min(24, 4 + 1.5 * Math.sqrt(n.count || 1)));
  const sizeU = (u) => (u.n.k === 'topic' ? subR(u.n) : isMean(u.n) ? 7 : 5.5);
  // 하위 주제의 고리: 주제가 가운데면 하위 주제를 한쪽 부채에 모으지 않고 둘레 전체에 고르게 둔다.
  // 12시에서 시계 방향으로 돌며 막힌 자리만 건너뛴다: 부모 선(12시 ±16°), 줄기(6시 ±16°, 목록이 있을 때), 개념 부채(9시 쪽, 개념이 있을 때)
  function ringArcs(hasParent, hasShelf, hasLeft) {
    // 부모 쪽(12시)은 넓게 비운다: 부모 고리의 이웃 주제·이름표와 닿지 않게
    const gaps = []; if (hasParent) gaps.push([-30, 30], [330, 390]); if (hasShelf) gaps.push([160, 200]); if (hasLeft) gaps.push([198, 330]);
    const arcs = []; let s = 0;
    for (const [a, b] of gaps.filter(([a, b]) => b > 0 && a < 360).map(([a, b]) => [Math.max(0, a), Math.min(360, b)]).sort((x, y) => x[0] - y[0])) { if (a > s) arcs.push([s, a]); s = Math.max(s, b); }
    if (s < 360) arcs.push([s, 360]);
    // 두 틈 사이에 남은 좁은 조각(예: 6시 줄기와 9시 부채 사이)에는 주제를 두지 않는다: 이름표가 양옆과 겹친다
    const wide = arcs.filter(([a, b]) => b - a >= 18); return wide.length ? wide : arcs; }
  // 고리 위 주제 하나가 차지할 호의 길이: 원 지름 + 여백, 그리고 가로로 쓰는 이름표(이름 · 수) 폭의 7할(옆 주제와 이름표가 겹치지 않게)
  const ringW = (u) => Math.max(2 * sizeU(u) + 18, .7 * 7 * ((u.n.path ? u.n.path.split('/').pop() : titleOf(u.n)).length + String(u.n.count || '').length + 1));
  function ringPlace(list, center, th12, arcs, rMin, wf = ringW) {
    const avail = arcs.reduce((x, [a, b]) => x + b - a, 0), need = list.reduce((x, u) => x + wf(u), 0);
    const w = list.map((u) => wf(u) / need * avail);   // 남는 자리까지 고르게 나눈다
    const degs = []; { let acc = 0; for (let i = 0; i < list.length; i++) { let s = acc + w[i] / 2; acc += w[i]; let deg = arcs[0] ? arcs[0][0] : 0;
      for (const [a, b] of arcs) { if (s <= b - a) { deg = a + s; break; } s -= b - a; } degs.push(deg); } }
    const at = (R) => list.map((u, i) => { const ang = rad(th12 + degs[i]); return { u, ang, x: center.x + Math.cos(ang) * R, y: center.y + Math.sin(ang) * R }; });
    // 이름표 자리(그리기의 lbl 과 같은 규칙): 오른쪽이면 오른쪽으로, 왼쪽이면 왼쪽으로, 위·아래면 가운데로 쓴다
    const box = (p) => { const r = sizeU(p.u), c = Math.cos(p.ang), sn = Math.sin(p.ang), L = (p.u.n.path ? p.u.n.path.split('/').pop() : titleOf(p.u.n)).length + String(p.u.n.count || '').length + 1, bw = 7.4 * Math.min(L, 22);
      const lx = p.x + c * (r + 7), ly = p.y + sn * (r + 7) + 4 + (Math.abs(c) < .35 ? (sn > 0 ? 10 : -6) : 0), x0 = c > .35 ? lx : c < -.35 ? lx - bw : lx - bw / 2;
      return [[x0, ly - 12, x0 + bw, ly + 4], [p.x - r - 3, p.y - r - 3, p.x + r + 3, p.y + r + 3]]; };
    const hit = (a, b) => a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3];
    // 겹치는 것만 비켜 세운다: 고리 전체를 넓히지 않고, 앞선 이웃과 이름표·원이 닿는 주제만 자기 방향(바깥)으로 한 칸씩 더 내보낸다
    const R = Math.max(rMin, need / rad(avail)), pos = at(R), B = [];
    for (const p of pos) { for (let k = 0; k < 10; k++) { const b = box(p); if (!B.some((q) => hit(b[0], q[0]) || hit(b[0], q[1]) || hit(q[0], b[1]) || hit(b[1], q[1]))) break;
        p.x += Math.cos(p.ang) * 26; p.y += Math.sin(p.ang) * 26; }
      B.push(box(p)); }
    return { R, pos }; }
  // 한 층(나침반 하나)의 내용. 부모는 이미 12시에 있으므로 뺀다
  function compassOf(node, parentId) {
    const G = gather(node), drop = (l) => l.filter((u) => u.n.id !== parentId);
    G.up = drop(G.up); G.left = drop(G.left); G.right = drop(G.right);
    // 기간: 시간 막대에서 고른 구간 밖의 노드는 뺀다(주제와 지나온 길의 노드는 남긴다)
    const keep = (n) => CANON || n.k === 'topic' || path.some((q) => q.id === n.id) || PINS.has(n.id) || (inRange(n) && !KOFF.has(kindKey(n)));
    G.up = G.up.filter((u) => keep(u.n)); G.left = G.left.filter((u) => keep(u.n)); G.right = G.right.filter((u) => keep(u.n));
    for (const k of Object.keys(G.shelf)) G.shelf[k] = G.shelf[k].filter(keep);
    G.bridges = G.bridges.filter((b) => keep(b.n));
    // 넓힐 관계: 이 노드를 펼칠 때 고른 갈래만 남긴다(부모와 다음 층으로 가는 노드는 늘 남긴다)
    const F0 = CANON ? null : FILT.get(node.id);
    if (F0) { const must = (u) => path.some((q) => q.id === u.n.id);
      if (!F0.has('up')) G.up = G.up.filter(must); if (!F0.has('left')) G.left = G.left.filter(must); if (!F0.has('right')) { G.right = G.right.filter(must); G.bridges = []; }
      if (!F0.has('shelf')) for (const k of Object.keys(G.shelf)) G.shelf[k] = []; }
    const probe = { x: 0, y: 0 };
    const rL = G.left.length ? fanPlace(G.left, probe, 0, REL.left[1] - REL.left[0], 150, 9, sizeU).R : 0;
    const shelfN = Object.values(G.shelf).reduce((x, l) => x + l.length, 0);
    const ring = node.k === 'topic' && G.right.length ? ringPlace(G.right, probe, 0, ringArcs(!!parentId, shelfN > 0, G.left.length > 0), 190) : null;
    const rR = ring ? ring.R : G.right.length ? fanPlace(G.right, probe, 0, REL.right[1] - REL.right[0], 170, 40, sizeU).R : 0;
    const E = Math.max(rL, rR, 120) + 70;
    // 이 층이 차지하는 둘레: 나침반(E)과 목록(6시 쪽으로 늘어지거나 옆으로 퍼진다). 정해진 자리를 잴 때 층끼리 이만큼 띄운다
    const ncol = ['decision', 'record', 'episode', 'doc'].filter((k) => G.shelf[k].length).length, rows = Math.min(ROWS, Math.max(0, ...Object.values(G.shelf).map((l) => l.length)));
    const FP = ncol ? Math.max(E, E * .6 + ncol * 210, E * .7 + 46 + rows * 17 + 30) : E;
    return { node, G, rL, rR, ring: !!ring, shelfN, E, FP };
  }
  // 펼친 나무: 펼칠 노드들(expand: 부모 id → 펼칠 자식 id 들)을 BFS 로 놓는다. 펼친 노드는 부모 부채의 자기 자리에서 바깥으로 빠져나와
  // 같은 나침반을 편다. 앞서 놓인 어느 층의 원과도 겹치지 않을 때까지 같은 방향으로 더 나간다. 목록은 focus 층만 펴고 나머지는 수로 접는다.

  let BAL = new Map();   // 가지별 거리(dk)를 미리 잰 표. 지금은 쓰는 곳이 없어 늘 비어 있다
  function layoutTree(root, expand, focusId) {
    BAL = new Map();
    const items = [], comps = [], placed = new Map(), onPath = new Set(path.map((n) => n.id)), queued = new Set([root.id]);
    const mk = (n, x, y, r, extra) => { const o = { id: n.id, src: n, k: n.k, x, y, r, c: colv(n), label: titleOf(n), ...extra }; items.push(o); placed.set(n.id, o); return o; };
    const queue = [{ node: root, parent: null }];
    while (queue.length) {
      const { node, parent } = queue.shift(); const i = comps.length, kids = expand.get(node.id) || new Set();
      const cp = compassOf(node, parent ? parent.hub.id : null), focus = node.id === focusId;
      // 고정 노드로 가는 길에만 있는 층(지금 길 밖): 다음 사슬 노드와 고정 노드만 남긴다(길만 잇는다)
      const pinOnly = MODE !== 'all' && !onPath.has(node.id) && PINCHAIN.has(node.id);
      if (pinOnly) { const ok = (u) => kids.has(u.n.id) || PINS.has(u.n.id); cp.G.up = cp.G.up.filter(ok); cp.G.left = cp.G.left.filter(ok); cp.G.right = cp.G.right.filter(ok); cp.G.bridges = [];
        for (const k of Object.keys(cp.G.shelf)) cp.G.shelf[k] = cp.G.shelf[k].filter((n) => PINS.has(n.id)); cp.shelfN = 0; }
      let hub, th12;
      const T0 = useT() && parent ? TPOS.get(node.id) : null;
      if (!parent) { hub = mk(node, 0, 0, node.k === 'topic' ? 24 : 15, { role: 'hub', level: 0 }); th12 = -90; }
      else if (T0) { const slot = parent.slots.get(node.id);   // 정해진 자리(어떤 길로 와도 같다)
        hub = mk(node, T0.x, T0.y, 22, { role: 'hub', level: i, parentHub: parent.hub, viaE: slot && slot.e }); th12 = T0.th12; }
      else {
        const slot = parent.slots.get(node.id) || { ang: rad(parent.th12 + 90), r: parent.cp.E - 70 };
        const pb = BAL.get(parent.hub.id);
        const bd = pb && pb.dk ? pb.dk.get(node.id) : null;
        let dist = bd || slot.r + cp.E * .85 + 40;
        // 앞서 놓인 다른 층과 겹치거나, 부모 층의 다른 노드(이웃 하위 주제와 그 이름표)가 이 층 안에 들어오면 같은 방향으로 더 내보낸다
        const own = CANON ? cp.FP : cp.E;
        const clash = (d) => { if (bd) return false; const x = parent.hub.x + Math.cos(slot.ang) * d, y = parent.hub.y + Math.sin(slot.ang) * d;
          return comps.some((q) => (q !== parent || CANON) && Math.hypot(x - q.hub.x, y - q.hub.y) < (q === parent ? q.cp.E : CANON ? q.cp.FP : q.cp.E) + own * .9)
            || parent.items.some((o) => o.x != null && o.id !== node.id && Math.hypot(x - o.x, y - o.y) < own + o.r + 70); };   // E 는 이 층 고리 + 이름표 여유, 70 은 이웃의 이름표 몫
        for (let k = 0; k < 120 && clash(dist); k++) dist += 40;
        hub = mk(node, parent.hub.x + Math.cos(slot.ang) * dist, parent.hub.y + Math.sin(slot.ang) * dist, node.k === 'topic' ? 22 : 14, { role: 'hub', level: i, parentHub: parent.hub, viaE: slot.e });
        th12 = todeg(slot.ang) + 180;
      }
      const chain = parent ? [...parent.chain, node] : [node];
      hub.chain = chain;
      const comp = { cp, hub, th12, slots: new Map(), focus, level: i, items: [], chain, parent, pinOnly };
      const put = (p, role, extra = {}) => { const r = Math.hypot(p.x - hub.x, p.y - hub.y);
        // 지나온 길의 노드는 반드시 자기 층(허브)으로 펼쳐져야 한다. 앞 층의 부채에 먼저 나와도 여기서 그리지 않고, 나중에 그 허브로 빛살만 잇는다.
        // (이걸 안 하면 길의 노드가 앞 층의 잎으로 먼저 놓여 펼쳐지지 못하고, 지금 층을 잃어 맨 위 vinc 로 돌아간 것처럼 보였다)
        if (onPath.has(p.u.n.id) && !kids.has(p.u.n.id)) { comp.items.push({ linkId: p.u.n.id, e: p.u.e }); return; }
        if (kids.has(p.u.n.id) && queued.has(p.u.n.id)) { comp.items.push({ linkId: p.u.n.id, e: p.u.e }); return; }   // 같은 노드가 두 관계로 두 번 나오면 한 번만 편다
        if (kids.has(p.u.n.id) && !placed.has(p.u.n.id)) { queued.add(p.u.n.id); comp.slots.set(p.u.n.id, { ang: p.ang, r, e: p.u.e }); queue.push({ node: p.u.n, parent: comp }); return; }
        if (placed.has(p.u.n.id)) { comp.items.push({ link: placed.get(p.u.n.id), e: p.u.e }); return; }
        comp.items.push(mk(p.u.n, p.x, p.y, sizeU(p.u), { role, rel: p.u.rel, e: p.u.e, ang: p.ang, level: i, comp: hub.id, chain, ...extra })); };
      const take = (list, a0, a1, rMin, gap, role) => fanPlace(list, hub, th12 + a0, th12 + a1, rMin, gap, sizeU).pos.forEach((p) => put(p, role));
      const G = cp.G;
      const ups = G.up; take(ups.slice(0, Math.ceil(ups.length / 2)), REL.up[0], -8, 190, 60, 'up'); take(ups.slice(Math.ceil(ups.length / 2)), 8, REL.up[1], 190, 60, 'up');
      take(G.left, ...REL.left, cp.rL || 150, 9, 'left');
      const nb = BAL.get(node.id);
      const fixed = (p) => { const T = useT() && TPOS.get(p.u.n.id); return T ? { ...p, x: T.x, y: T.y, ang: Math.atan2(T.y - hub.y, T.x - hub.x) } : p; };   // 하위 주제는 정해진 자리에
      if (cp.ring) ringPlace(G.right, hub, th12, ringArcs(!!parent, cp.shelfN > 0, G.left.length > 0), cp.rR).pos.forEach((p) => put(fixed(p), 'right', { ring: true }));
      else take(G.right, ...REL.right, cp.rR || 170, 40, 'right');
      // 다리: 이 층의 하위 주제들 사이(펼쳐진 하위 주제는 그 자리 각도로 센다)
      const angOf = new Map([...comp.items.filter((o) => o.role === 'right').map((o) => [o.id, o.ang]), ...[...comp.slots].map(([id, s]) => [id, s.ang])]);
      for (const b of G.bridges) { const angs = b.subs.map((s) => angOf.get(`topic:${s}`)).filter((a) => a != null); if (!angs.length || placed.has(b.n.id)) continue;
        if (onPath.has(b.n.id) && !kids.has(b.n.id)) continue;
        let a = Math.atan2(angs.reduce((s, x) => s + Math.sin(x), 0), angs.reduce((s, x) => s + Math.cos(x), 0)), r = Math.max(hub.r + 70, (cp.rR || 170) * (cp.ring ? .62 : 1) - (cp.ring ? 0 : 90));
        // 다리끼리 이름표가 겹치지 않게: 겹치면 안쪽으로 한 칸, 더 들어갈 수 없으면 옆으로 조금 돈다
        const bw = (o) => 7 * Math.min(20, (o.label || '').replace(/ junction$/i, '').length) + 16, myW = 7 * Math.min(20, titleOf(b.n).replace(/ junction$/i, '').length) + 16;
        const busy = () => { const x = hub.x + Math.cos(a) * r, y = hub.y + Math.sin(a) * r;
          return items.some((o) => o.role === 'bridge' && Math.abs(o.x - x) < (bw(o) + myW) / 2 && Math.abs(o.y - y) < 30) || items.some((o) => o.role === 'right' && o.comp === hub.id && Math.hypot(o.x - x, o.y - y) < o.r + 26); };
        for (let k = 0; k < 16 && busy(); k++) { if (r - 26 > hub.r + 60) r -= 26; else a += .14; }
        if (kids.has(b.n.id)) { if (queued.has(b.n.id)) continue; queued.add(b.n.id); comp.slots.set(b.n.id, { ang: a, r }); queue.push({ node: b.n, parent: comp }); continue; }   // 다리를 눌렀으면 그 자리에서 다리의 나침반을 편다
        comp.items.push(mk(b.n, hub.x + Math.cos(a) * r, hub.y + Math.sin(a) * r, 8, { role: 'bridge', subs: b.subs.map((s) => `topic:${s}`), outside: b.outside, ang: a, level: i, comp: hub.id, chain })); }
      const kinds = [['decision', KIND.decision], ['record', KIND.record], ['episode', KIND.episode], ['doc', KIND.doc]].filter(([k]) => G.shelf[k].length);
      comp.kinds = kinds.map(([k, name]) => ({ k, name, all: G.shelf[k] }));
      if (!focus && kinds.length && MODE !== 'all') { const a = rad(th12 + 180), r = hub.r + 46;
        comp.stub = { x: hub.x + Math.cos(a) * r, y: hub.y + Math.sin(a) * r, text: kinds.map(([k, name]) => `${name} ${G.shelf[k].length}`).join(' · '), ang: a }; }
      comps.push(comp);
    }
    // 미뤄 둔 빛살을 실제 허브에 잇는다
    for (const c of comps) c.items = c.items.map((o) => (o.linkId ? (placed.has(o.linkId) && placed.get(o.linkId) !== c.hub ? { link: placed.get(o.linkId), e: o.e } : null) : o)).filter(Boolean);
    const F = comps.find((c) => c.focus) || comps[comps.length - 1];
    if (!F) return { items, comps, focus: comps[0], shelves: [], sets: [], Q: null, BY: 0, mode: 'none', side: 1, a6: 0 };
    // 층마다의 목록(결정·기록·사건·문서). 평소에는 지금 층만, 전체 펼치기에서는 목록이 있는 모든 층에 단다
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
      // 6시가 거의 위·아래로 서면 옆을 정할 수 없다: 그래프 바깥쪽(맨 위 주제에서 멀어지는 쪽)으로 꺾는다. 안쪽은 다른 층이 있다
      let sd = side; if (Math.abs(Math.cos(a6)) < .35) { const rx = comps[0].hub.x; sd = Math.abs(F.hub.x - rx) > 1 ? (F.hub.x > rx ? 1 : -1) : (reachOn(1) <= reachOn(-1) ? 1 : -1); }
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
    if (MODE === 'all') for (const c of comps) if (c !== F && !c.pinOnly && c.kinds.length) sets.push(setFor(c));
    return { items, comps, focus: F, shelves: fset.shelves, sets, Q: fset.Q, BY: fset.BY, mode: fset.mode, side: fset.side, a6: fset.a6 };
  }

  // ── 그리기 ──────────────────────────────────────────────────────────────────────────────
  const ringS = (r, col, w) => { const n = Math.max(18, Math.round(2 * Math.PI * r / 4.2)), per = 2 * Math.PI * r / n, dash = per * .55;
    return `<circle class="shape" cx="0" cy="0" r="${r}" fill="none" style="stroke:${col}" stroke-width="${w}" stroke-dasharray="${dash.toFixed(2)} ${(per - dash).toFixed(2)}" stroke-linecap="round"/>`; };
  // 펼칠 것: path = 지나온 길만, sub = 길 + 지금 주제의 하위 주제 전부, all = 모든 주제(+ 지나온 길)
  function expandMap() { const m = new Map(), add = (a, b) => { if (!m.has(a)) m.set(a, new Set()); m.get(a).add(b); };
    for (let i = 1; i < path.length; i++) add(path[i - 1].id, path[i].id);
    const last = path[path.length - 1];
    if (MODE === 'sub' && last.k === 'topic') for (const s of last.sub) add(last.id, `topic:${s}`);
    if (MODE === 'all') for (const tp of topics.values()) for (const s of tp.sub) add(tp.id, `topic:${s}`);
    // 고정한 노드까지의 길: 그 노드의 주제 사슬을 펼친다(고정 노드 자신은 잎으로 남는다)
    PINCHAIN = new Set();
    for (const id of PINS) { const n = byAny(id); if (!n) continue; const ch = chainTo(n);
      for (let i = 1; i < ch.length - 1; i++) { add(ch[i - 1].id, ch[i].id); PINCHAIN.add(ch[i].id); } }
    return m; }
  let render = function () {
    const L = pinLayout(layoutTree(path[0], expandMap(), path[path.length - 1].id));
    const F = L.focus, near = new Set([F.hub.id, ...L.comps.filter((c) => c.parent === F).map((c) => c.hub.id)]);
    for (const c of L.comps) c.far = MODE === 'all' ? false : MODE === 'sub' ? !(c === F || c.parent === F) : c !== F; LZ = { hub: L.focus.hub, ...L }; let defs = '', rays = '', nodesS = '', deco = '', i0 = 0;
    const drawRay = (inner, outer, e, w0, w1 = .5, cls = '') => { const r = ray(inner, outer, i0++, w0, w1); defs += r.def; rays += cls ? r.svg.replace('class="ray"', `class="ray ${cls}"`) : r.svg;
      if (e && ['PREREQUISITE_OF', 'DERIVES_FROM', 'INHERITS_FROM', 'RETURNS_TO'].includes(e.t)) {
        const A = e.a.id === inner.id ? inner : outer, B = A === inner ? outer : inner, src = e.t === 'PREREQUISITE_OF' || e.t === 'RETURNS_TO' ? A : B, dst = src === A ? B : A;
        rays += bead((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2, Math.atan2(dst.y - src.y, dst.x - src.x), mix(src.c, 90), e.t).replace(/class="ray"/, `class="ray ${cls}" data-a="${A.id}" data-b="${B.id}"`); } };
    const at = new Map(L.items.map((o) => [o.id, o])), fl = L.comps.length - 1;
    for (const comp of L.comps) {
      const far = comp.far ? 'far' : '', hub = comp.hub;
      if (hub.parentHub) drawRay(hub.parentHub, hub, hub.viaE, comp.pinOnly ? 2.2 : 3.4, comp.pinOnly ? .8 : 1.2, comp.pinOnly ? 'pinpath' : 'path');          // 펼쳐 들어온 길: 굵게(고정 노드로 가는 길은 조금 가늘게)
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
        return o.k === 'topic' ? `<text class="hub" x="0" y="${o.r + 20}" text-anchor="middle">${esc(short(o.label, 28))}</text><text class="badge" x="0" y="${o.r + 34}" text-anchor="middle">${esc(C.topicCount(o.src.count))}</text>` : `<text class="hub" x="${(-o.r * .7 - 8)}" y="${o.r + 16}" text-anchor="end">${esc(short(o.label, 28))}${o.k === 'topic' ? `<tspan class="badge" dx="6">${esc(C.topicCount(o.src.count))}</tspan>` : ''}</text>`; }
      if (o.ring) { const cx2 = Math.cos(o.ang), sy = Math.sin(o.ang), x = cx2 * (o.r + 7), y = sy * (o.r + 7) + 4 + (Math.abs(cx2) < .35 ? (sy > 0 ? 10 : -6) : 0);
        return `<text class="l1" x="${x.toFixed(1)}" y="${y.toFixed(1)}" text-anchor="${cx2 > .35 ? 'start' : cx2 < -.35 ? 'end' : 'middle'}">${esc(short(o.label, 18))}<tspan class="badge" dx="5">${o.src.count}</tspan></text>`; }
      if (o.role === 'right' && o.k === 'topic') { const flip = Math.cos(o.ang) < 0; return `<text class="l1" x="${flip ? -(o.r + 7) : o.r + 7}" y="4" text-anchor="${flip ? 'end' : 'start'}">${esc(short(o.label, 18))}<tspan class="badge" dx="5">${o.src.count}</tspan></text>`; }
      if (o.role === 'up' || o.role === 'left' || o.role === 'right') return rot(o, 7, 'l1', 24);
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
    const junction = (o) => { const ts = (o.subs || []).map((s) => at.get(s)).filter(Boolean).map((q) => ({ a: Math.atan2(q.y - o.y, q.x - o.x), c: q.c }));
      if (o.outside) { const avg = Math.atan2(ts.reduce((s, q) => s + Math.sin(q.a), 0), ts.reduce((s, q) => s + Math.cos(q.a), 0)); ts.push({ a: avg + Math.PI, c: 'var(--c-mute)' }); }
      if (!ts.length) return shape('concept', 0, 0, o.r, o.c, true);
      ts.sort((x, y) => x.a - y.a); const n = ts.length, R = o.r, w = 2.6;
      const half = Math.min(Math.PI / n - .32, 1.1);
      const arc = (a, c) => { const a0 = a - half, a1 = a + half, P = (q) => `${(Math.cos(q) * R).toFixed(2)} ${(Math.sin(q) * R).toFixed(2)}`;
        return `<path class="shape" d="M${P(a0)}A${R} ${R} 0 0 1 ${P(a1)}" fill="none" style="stroke:${c}" stroke-width="${w}" stroke-linecap="round"/>`; };
      return ts.map((q) => arc(q.a, q.c)).join('') + `<circle class="shape" r="${(R * .32).toFixed(2)}" style="fill:${mix(o.c, 80)}"/>`; };
    const body = (o) => o.k === 'bridge' ? junction(o) : crescent(o) + (o.k === 'topic' ? ringS(o.r, o.role === 'hub' || fresh(o.src) ? o.c : mix(o.c, 70), o.role === 'hub' ? 2.6 : 2)
      : shape(o.k, 0, 0, o.r, o.src.vu ? o.c : fresh(o.src) ? o.c : mix(o.c, 55), !!o.src.vu)) + '';
    const compOf = new Map(L.comps.map((c) => [c.hub.id, c]));
    const filtTag = (o) => { const f = FILT.get(o.id); return o.role === 'hub' && f ? `<text class="badge ftag" x="0" y="${-o.r - 9}" text-anchor="middle">${[...f].map((k) => GROUPS.find((g) => g[0] === k)[1]).join(' · ')}${esc(C.only)}</text>` : ''; };
    for (const o of L.items) { const oc = o.role === 'hub' ? compOf.get(o.id) : compOf.get(o.comp), far = oc && oc.far && o.role !== 'hub' ? ' far' : '', cur = o.role === 'hub' && oc === F ? ' sel' : o.role === 'hub' && path.some((n) => n.id === o.id) ? ' onpath' : '';
      nodesS += `<g class="node${far}${cur}" data-id="${esc(o.id)}" data-role="${o.role}" data-chain="${esc((o.role === 'hub' ? (compOf.get(o.id)?.chain || []) : (o.chain || [])).map((n) => n.id).join('|'))}" data-level="${o.level}" tabindex="0" role="button" aria-label="${esc((KIND[o.k] || o.k) + ' ' + o.label)}" style="transform: translate(${o.x}px, ${o.y}px)"><circle class="ring" r="${o.r + 4.5}"/><circle class="hit" r="${o.r + 5}" fill="transparent"/>${body(o)}${lbl(o)}${filtTag(o)}</g>`; }
    svg.innerHTML = `<defs>${defs}</defs><g id="cmp-deco">${deco}</g><g id="cmp-rays">${rays}</g><g id="cmp-tree"></g><g id="cmp-nodes">${nodesS}</g>`;
    // 카메라: 지금 층(허브·부채·목록)과 바로 위 허브가 들어오게
    const inFocus = (o) => { const c = o.role === 'hub' ? compOf.get(o.id) : compOf.get(o.comp); return o.role === 'shelf' || (c && (c === F || (MODE === 'sub' && c.parent === F))); };
    const box = MODE === 'all' ? L.items : L.items.filter((o) => inFocus(o) || nearPin(o, F) || (o.role === 'hub' && F.parent && o.id === F.parent.hub.id));
    const pts = box.flatMap((o) => { if (o.role === 'shelf') return [[o.x - 30, o.y - 40], [o.x + 200, o.y + 12]];
      if (o.pinned) return [[o.x - 60, o.y - 50], [o.x + 180, o.y + 50]];   // 고정 노드: 이름표까지 들어오게
      // 이름표가 실제로 차지하는 만큼만 잡는다: 가로로 쓰는 고리 주제는 옆으로 약 110px·위아래로 30px, 비스듬한 부채 이름은 이름 길이만큼
      if (o.role === 'hub') return o.k === 'topic' ? [[o.x - o.r - 30, o.y - o.r - 10], [o.x + o.r + 30, o.y + o.r + 40]] : [[o.x - o.r - 170, o.y - o.r - 20], [o.x + o.r + 20, o.y + o.r + 30]];
      if (o.ring) { const c2 = Math.cos(o.ang), s2 = Math.sin(o.ang), lw = Math.min(150, 9 + 7 * Math.min(18, o.label.length) + 22);
        return [[o.x - o.r - (c2 < -.35 ? lw : Math.abs(c2) <= .35 ? lw / 2 : 0), o.y - o.r - (s2 < 0 ? 24 : 6)], [o.x + o.r + (c2 > .35 ? lw : Math.abs(c2) <= .35 ? lw / 2 : 0), o.y + o.r + (s2 > 0 ? 24 : 6)]]; }
      const a = o.ang ?? 0, L = o.r + 12 + 6.6 * Math.min(24, o.label.length); return [[o.x - o.r, o.y - o.r], [o.x + o.r, o.y + o.r], [o.x + Math.cos(a) * L, o.y + Math.sin(a) * L]]; });
    const xs = pts.map((q) => q[0]), ys = pts.map((q) => q[1]);
    // 넓게 보기: 지나온 허브들 + 지금 층만 담는다(옆 갈래의 잎까지 담으면 너무 멀어진다)
    if (KEEPCAM) KEEPCAM = false;
    else if (WIDE) { const pinHub = new Set(L.comps.filter((c) => c.pinOnly).map((c) => c.hub.id)), all = L.items.filter((o) => (o.role === 'hub' && !pinHub.has(o.id)) || o.comp === F.hub.id || o.role === 'shelf' || nearPin(o, F)), ax = all.flatMap((o) => [o.x - 160, o.x + 200]), ay = all.flatMap((o) => [o.y - 40, o.y + 40]), c = F.hub;
      const hw = Math.max(c.x - Math.min(...ax), Math.max(...ax) - c.x), hh = Math.max(c.y - Math.min(...ay), Math.max(...ay) - c.y);
      // 시간 창이 열려 있으면 그 창에 가리지 않는 왼쪽 부분에 맞춘다(창 폭만큼 오른쪽을 더 잡는다)
      const tw = 0, sw = svg.clientWidth || 1400, extra = tw && sw > tw ? (2 * hw) * tw / (sw - tw) : 0;
      flyView({ x: c.x - hw, y: c.y - hh, w: 2 * hw + extra, h: 2 * hh }); WIDE = false;   // 고른 노드를 가운데 두고, 펼친 길 전체가 보이게 물러난다
    } else flyView({ x: Math.min(...xs) - 20, y: Math.min(...ys) - 20, w: Math.max(...xs) - Math.min(...xs) + 40, h: Math.max(...ys) - Math.min(...ys) + 40 });
    // 남는 노드는 옛 자리에서 새 자리로(FLIP)
    const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (!still && prevPos.size) { svg.classList.add('moving');
      for (const g of svg.querySelectorAll('#cmp-nodes .node')) { const was = prevPos.get(g.dataset.id); g.style.transition = 'none'; if (was) g.style.transform = `translate(${was.x}px, ${was.y}px)`; else g.classList.add('enter'); }
      svg.getBoundingClientRect();
      requestAnimationFrame(() => { for (const g of svg.querySelectorAll('#cmp-nodes .node')) { const o = at.get(g.dataset.id); if (!o) continue; g.style.transition = ''; g.style.transform = `translate(${o.x}px, ${o.y}px)`; g.classList.remove('enter'); }
        setTimeout(() => { svg.classList.remove('moving'); grow(L.sets.flatMap(buildTree)); }, 400); }); }
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
      svg.setAttribute('viewBox', `${v.x.toFixed(1)} ${v.y.toFixed(1)} ${v.w.toFixed(1)} ${v.h.toFixed(1)}`); VIEW = v; drawPins(); lod(); if (u < 1) flyRaf = requestAnimationFrame(step); };
    step();
  }

  // ── 줄기와 가지(지금 층의 목록): 허브에서 6시 쪽으로 곧게 나가 버스에 닿고, 목록마다 세로 줄기, 항목마다 가지 ─────────
  const Wd = (d) => Math.max(.8, 2.8 - d * .0016), Cd = (d) => Math.round(Math.max(40, 90 - d * .035));
  function buildTree(L) {
    const prims = [], hub = L.hub, hc = hub.c, Rc = 12, Rb = 7, F = 4.5, BY = L.BY;
    const cols = L.shelves.filter((s) => s.items.length); if (!cols.length) return prims;
    const seg = (a, b, d0, c) => { const len = Math.hypot(b[0] - a[0], b[1] - a[1]); prims.push({ t: 'seg', a, b, d0, d1: d0 + len, c }); return d0 + len; };
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
      const nx = -(by - ay) / len, ny = (bx - ax) / len, wa = Wd(p.d0), wb = Wd(p.d0 + (p.d1 - p.d0) * q); return P([[ax + nx * wa, ay + ny * wa], [bx + nx * wb, by + ny * wb], [bx - nx * wb, by - ny * wb], [ax - nx * wa, ay - ny * wa]]); }
    if (p.t === 'arc') { const q = Math.max(0, Math.min(1, (D - p.d0) / (p.d1 - p.d0))); if (q <= 0) return ''; const A = [], B = [];
      for (let s = 0; s <= 12; s++) { const u = s / 12 * q, a = p.a0 + (p.a1 - p.a0) * u, w = Wd(p.d0 + (p.d1 - p.d0) * u); A.push([p.cx + Math.cos(a) * (p.R + w), p.cy + Math.sin(a) * (p.R + w)]); B.push([p.cx + Math.cos(a) * (p.R - w), p.cy + Math.sin(a) * (p.R - w)]); }
      return P([...A, ...B.reverse()]); }
    if (p.t === 'fil') { if (D < p.d0) return ''; const { jx, jy, wv, wh, sx, sy, F } = p, ccx = jx + sx * (wv + F), ccy = jy + sy * (wh + F), a0 = sx > 0 ? Math.PI : 0, a1 = sy > 0 ? -Math.PI / 2 : Math.PI / 2;
      let da = a1 - a0; if (da > Math.PI) da -= 2 * Math.PI; if (da < -Math.PI) da += 2 * Math.PI; const arc = []; for (let s = 0; s <= 8; s++) { const a = a0 + da * s / 8; arc.push([ccx + Math.cos(a) * F, ccy + Math.sin(a) * F]); }
      return P([...arc, [jx + sx * wv, jy + sy * wh]]); }
    if (p.t === 'cap') { if (D < p.d0) return ''; const n = p.n, rr = n.r + GAP + 3, w1 = Wd(p.d0), c = arcPts(n.x, n.y, rr, Math.PI, Math.asin(Math.min(.95, w1 / rr))); return P([[n.x - rr - .8, n.y - w1], ...c, [n.x - rr - .8, n.y + w1]]); }
    return '';
  }
  let traf = 0;
  function grow(prims) {
    cancelAnimationFrame(traf); const g = $id('cmp-tree'), still = matchMedia('(prefers-reduced-motion: reduce)').matches;
    g.innerHTML = prims.map((p) => `<polygon style="fill:${mix(p.c, p.t === 'cap' || (p.t === 'seg' && p.c !== LZ.hub.c) ? 60 : Cd(p.d0))}"/>`).join('');
    const els = [...g.children], maxD = Math.max(0, ...prims.map((p) => p.d1 ?? p.d0)), t0 = performance.now();
    const step = () => { const D = still ? 1e9 : (performance.now() - t0) * 2.2; prims.forEach((p, i) => els[i].setAttribute('points', primPoly(p, D))); traf = D < maxD ? requestAnimationFrame(step) : 0; }; step();
  }

  // ── 다니기: 누르면 그 자리에서 펼친다. 지나온 층의 노드를 누르면 그 층으로 접고 거기서 새로 펼친다 ─────────────────
  let hist = [], hi = -1;
  const byAny = (id) => byId.get(id) || (id.startsWith('topic:') ? topics.get(id.slice(6)) : null);
  function setPath(P, push = true) { path = P; if (push) { hist = hist.slice(0, hi + 1); hist.push(P.map((n) => n.id)); hi = hist.length - 1; } render(); navUI(); }
  function navUI() { const tr = $id('trail'); if (!tr) return;
    $id('trail').innerHTML = path.map((n, i) => `<button data-level="${i}" class="${i === path.length - 1 ? 'cur' : ''}" title="${esc(n.t || n.path || n.id)}">${n.k === 'topic' ? '▦ ' : ''}${esc(short(titleOf(n), 26))}</button>`).join('<span>›</span>'); }
  // 누르면 바로 펼치지 않고 무엇을 넓힐지 묻는다(Neo4j·Palantir 의 '관계 종류별로 넓히기'). 갈래마다 수가 붙고, 0 인 갈래는 흐리다
  const GROUPS = [['all', C.groups.all], ['left', C.groups.left], ['up', C.groups.up], ['right', C.groups.right], ['shelf', C.groups.shelf]];
  function countsOf(n, parentId) { const G = gather(n), not = (l) => l.filter((u) => u.n.id !== parentId && (u.n.k === 'topic' || inRange(u.n))).length;
    return { up: not(G.up), left: not(G.left), right: not(G.right) + G.bridges.length, shelf: Object.values(G.shelf).reduce((s, l) => s + l.filter(inRange).length, 0) }; }
  const menu = $id('menu');
  function openMenu(g, e, onPick) {
    const n = byAny(g.dataset.id), chain = (g.dataset.chain || '').split('|').filter(Boolean), parentId = g.dataset.role === 'hub' ? chain[chain.length - 2] : chain[chain.length - 1];
    const c = countsOf(n, parentId), tot = c.up + c.left + c.right + c.shelf, cur = FILT.get(n.id);
    menu.innerHTML = `<div class="mh">${esc(short(titleOf(n), 40))}<small>${KIND[n.k] || n.k}</small></div>` + GROUPS.map(([k, name]) => {
      const v = k === 'all' ? tot : c[k], on = cur ? (k !== 'all' && cur.has(k)) : k === 'all';
      return `<button class="mi${on ? ' on' : ''}" data-g="${k}" ${v ? '' : 'disabled'}><span>${name}</span><b>${v}</b></button>`; }).join('') + `<button class="mi pin" data-pin="1"><span>${PINS.has(n.id) ? esc(C.unpin) : esc(C.pin)}</span><b class="pin-b">${pinIcon(16)}</b></button>`;
    const r = root.getBoundingClientRect(); menu.style.left = `${Math.min(e.clientX + 10, innerWidth - 260)}px`; menu.style.top = `${Math.min(e.clientY + 10, innerHeight - 260)}px`; menu.hidden = false;
    menu.onclick = (ev) => { if (ev.target.closest('[data-pin]')) { togglePin(n.id); menu.hidden = true; return; } const b = ev.target.closest('[data-g]'); if (!b || b.disabled) return; const k = b.dataset.g;
      if (k === 'all') FILT.delete(n.id); else { const s = ev.shiftKey && FILT.get(n.id) ? new Set(FILT.get(n.id)) : new Set(); s.has(k) && ev.shiftKey ? s.delete(k) : s.add(k); FILT.set(n.id, s); }
      if (ev.shiftKey && k !== 'all') { openMenu(g, e, onPick); return; } menu.hidden = true; onPick(); };
    menu.querySelector('.mi:not([disabled])')?.focus();
  }
  onWin('pointerdown', (e) => { if (!menu.hidden && !e.target.closest('#cmp-menu')) menu.hidden = true; }, true);
  menu.addEventListener('keydown', (e) => { if (e.key === 'Escape') { menu.hidden = true; e.stopPropagation(); } });
  function act(g, e) { if (!g) return; const chain = (g.dataset.chain || '').split('|').filter(Boolean).map(byAny), n = byAny(g.dataset.id);
    const isFocus = g.classList.contains('sel');
    if (g.dataset.role === 'hub' && !isFocus) { setPath(chain.length ? chain : [n]); return; }   // 지나온 층의 허브: 그 층으로 접는다
    const go2 = () => (g.dataset.role === 'hub' ? setPath(chain.length ? chain : [n], false) : setPath([...chain, n]));
    if (e && e.altKey) { FILT.delete(n.id); go2(); return; }
    openMenu(g, e || { clientX: innerWidth / 2, clientY: innerHeight / 2 }, go2); }
  svg.addEventListener('click', (e) => { const mb = e.target.closest('[data-more]'); if (mb) { MORE.set(mb.dataset.more, (MORE.get(mb.dataset.more) || 0) + 20); prevPos = new Map(); render(); return; } const st = e.target.closest('.stub'); if (st) { setPath(st.dataset.chain.split('|').map(byAny)); return; } act(e.target.closest('.node'), e); });
  svg.addEventListener('keydown', (e) => { if (e.key !== 'Enter' && e.key !== ' ') return; const g = e.target.closest('.node'); if (!g) return; e.preventDefault(); const r = g.getBoundingClientRect(); act(g, { clientX: r.right, clientY: r.top }); });
  const tip = $id('tip');
  svg.addEventListener('pointerover', (e) => { const g = e.target.closest('.node'); svg.classList.toggle('hot', !!g);
    svg.querySelectorAll('.ray').forEach((r) => r.classList.toggle('near', !!g && (r.dataset.a === g?.dataset.id || r.dataset.b === g?.dataset.id)));
    if (g) { const n = byAny(g.dataset.id), o = LZ.items.find((q) => q.id === g.dataset.id); tip.style.display = 'block';
      tip.innerHTML = `${esc(n.t || n.path)}<small>${KIND[n.k] || n.k}${o?.rel ? ' · ' + esc(o.rel) : ''}${n.d ? ' · ' + esc(n.d) : ''}${n.ts ? ' · ' + n.ts : ''}</small>`; } else tip.style.display = 'none'; });
  svg.addEventListener('pointermove', (e) => { tip.style.left = `${e.clientX + 14}px`; tip.style.top = `${e.clientY + 12}px`; });
  svg.addEventListener('pointerleave', () => { tip.style.display = 'none'; svg.classList.remove('hot'); });
  onWin('keydown', (e) => { if (e.target.closest?.('input,textarea,[contenteditable],dialog') || !root.isConnected || root.closest('[hidden]')) return; if (e.altKey && e.key === 'ArrowLeft') { if (hi > 0) { hi--; setPath(hist[hi].map(byAny), false); } } else if (e.altKey && e.key === 'ArrowRight') { if (hi < hist.length - 1) { hi++; setPath(hist[hi].map(byAny), false); } }
    else if (e.key === 'Escape' || (e.key === 'Backspace' && path.length > 1)) { if (e.key === 'Escape') setPath([topics.get('vinc')]); else setPath(path.slice(0, -1)); } });
  // 찾기: 주제 사슬을 따라 펼친 다음 그 노드를 편다
  const chainTo = (n) => { const p = (n.k === 'topic' ? n.path : n.d || 'vinc').split('/'), out = []; for (let i = 1; i <= p.length; i++) out.push(topics.get(p.slice(0, i).join('/'))); if (n.k !== 'topic') out.push(n); return out; };
  let QUERY = '';
  const norm = (s) => String(s || '').toLowerCase();
  const matchQ = (n) => !QUERY || norm(n.t || n.path || n.id).includes(QUERY) || norm(n.id).includes(QUERY);
  const setQuery = (q) => { const was = QUERY; QUERY = norm(q); TCELL = null; TWIN = null; paintSide(); treeChanged(!QUERY && !!was); };
  document.querySelectorAll('[data-x]').forEach((b) => b.addEventListener('click', () => { MODE = b.dataset.x; document.querySelectorAll('[data-x]').forEach((x) => x.setAttribute('aria-pressed', String(x === b))); render(); }));
  // 굴려서 확대·끌어서 이동: viewBox 를 직접 옮긴다(펼친 그림이 화면보다 클 때)
  // 멀리서 볼수록 덜 그린다: 1단(노드 이름·목록 줄 글자 숨김), 2단(목록 줄과 잎 노드 숨김, 주제와 선만)
  const lod = () => { const z = VIEW ? VIEW.w / (svg.clientWidth || 1) : 1; svg.classList.toggle('lod1', z > 2.4); svg.classList.toggle('lod2', z > 5); };
  const setVB = (v) => { VIEW = v; svg.setAttribute('viewBox', `${v.x.toFixed(1)} ${v.y.toFixed(1)} ${v.w.toFixed(1)} ${v.h.toFixed(1)}`); drawPins(); lod(); };
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
  const PINS = new Set(JSON.parse(localStorage.getItem('vinc.pins') || '[]').filter((id) => byAny(id)));
  const dock = $id('dock');
  // ── 고정: 고른 노드를 **그래프 안의 그 자리**(배치 좌표)에 박아 둔다. 가운데를 옮겨 다시 배치해도 그 노드는 같은 자리에 남고,
  // 새로 펼친 노드 가운데 이어진 것으로 빛살이 간다. 겹치는 새 노드는 비켜 선다. 끌면 자리를 옮긴다. 이 기기에만 저장한다.
  const PW = new Map(Object.entries(JSON.parse(localStorage.getItem('vinc.pins.w') || '{}')).filter(([id]) => PINS.has(id)));
  localStorage.removeItem('vinc.pins.at');   // 앞선 시안의 화면 좌표 자리는 쓰지 않는다
  function savePins() { localStorage.setItem('vinc.pins', JSON.stringify([...PINS])); localStorage.setItem('vinc.pins.w', JSON.stringify(Object.fromEntries(PW))); }
  const LASTPIN = new Map();   // 이번 세션에서 고정 노드가 마지막으로 놓였던 자리(배치 좌표)
  function pinLayout(L) {
    if (!PINS.size) return L;
    const at = new Map(L.items.map((o) => [o.id, o])), pinned = [];
    for (const id of PINS) { const n = byAny(id); if (!n) continue; let o = at.get(id); const w = PW.get(id);
      if (o) { if (w) { o.x = w.x; o.y = w.y; } LASTPIN.set(id, { x: o.x, y: o.y }); o.pinned = true; pinned.push(o); continue; }
      const w2 = w || LASTPIN.get(id); if (!w2) continue;   // 길로 못 잇는 경우(배치가 그 노드를 못 놓을 때)는 마지막으로 놓였던 자리에
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
  const stage = root.querySelector('.cmp-stage'), pinBox = $id('pins'), pinLines = pinBox.querySelector('.pl'), pinCards = pinBox.querySelector('.pcs');
  const scr = (o) => ({ x: (o.x - VIEW.x) / VIEW.w * (svg.clientWidth || 1), y: (o.y - VIEW.y) / VIEW.h * (svg.clientHeight || 1) });
  // 방향 표: 화면 밖에 있는 고정 노드를 가장자리에 화살과 이름으로. 누르면 그 자리로 화면을 옮긴다.
  function drawPins() {
    if (!LZ || !VIEW) return;
    const W = stage.clientWidth, H = stage.clientHeight, M = 30; let h = '';
    for (const o of LZ.pinned || []) { const q = scr(o); if (q.x >= 0 && q.x <= W && q.y >= 0 && q.y <= H) continue;
      const cx = W / 2, cy = H / 2, dx = q.x - cx, dy = q.y - cy, t = Math.min((W / 2 - M) / Math.abs(dx || 1e-6), (H / 2 - M) / Math.abs(dy || 1e-6));
      const x = cx + dx * t, y = cy + dy * t, ang = Math.atan2(dy, dx) * 180 / Math.PI;
      h += `<button class="pedge" type="button" data-id="${esc(o.id)}" style="left:${x.toFixed(0)}px;top:${y.toFixed(0)}px;--c:${o.c}" title="${esc(o.src.t || o.src.path || o.id)}"><i style="transform:rotate(${ang.toFixed(0)}deg)">➜</i>${pinIcon(13)}<span>${esc(short(o.label, 22))}</span></button>`; }
    pinCards.innerHTML = h; pinLines.innerHTML = ''; }
  pinCards.addEventListener('click', (e) => { const b = e.target.closest('.pedge'); if (!b) return; const o = LZ && LZ.items.find((q) => q.id === b.dataset.id); if (!o) return;
    flyView({ x: o.x - VIEW.w / 2, y: o.y - VIEW.h / 2, w: VIEW.w, h: VIEW.h }); });
  // 끌기: 고정한 노드를 잡아 옮긴다(놓으면 그 자리로 다시 배치하고 빛살을 다시 긋는다). 끈 뒤의 클릭은 메뉴를 열지 않는다.
  let ndrag = null, ndragMoved = false;
  svg.addEventListener('pointerdown', (e) => { const g = e.target.closest('.node'); if (!g || !PINS.has(g.dataset.id) || !LZ) return; const o = LZ.items.find((q) => q.id === g.dataset.id); if (!o) return;
    ndrag = { g, o, x0: e.clientX, y0: e.clientY, ox: o.x, oy: o.y, moved: false }; svg.setPointerCapture(e.pointerId); e.stopPropagation(); }, true);
  svg.addEventListener('pointermove', (e) => { if (!ndrag) return; const dx = e.clientX - ndrag.x0, dy = e.clientY - ndrag.y0; if (!ndrag.moved && Math.hypot(dx, dy) < 4) return;
    ndrag.moved = true; const k = VIEW.w / (svg.getBoundingClientRect().width || 1), nx = ndrag.ox + dx * k, ny = ndrag.oy + dy * k;
    ndrag.o.x = nx; ndrag.o.y = ny; ndrag.g.style.transition = 'none'; ndrag.g.style.transform = `translate(${nx}px, ${ny}px)`; e.stopPropagation(); }, true);
  svg.addEventListener('pointerup', () => { if (!ndrag) return; const d = ndrag; ndrag = null; if (!d.moved) return;
    ndragMoved = true; PW.set(d.o.id, { x: d.o.x, y: d.o.y }); prevPos.set(d.o.id, { x: d.o.x, y: d.o.y }); savePins(); KEEPCAM = true; render(); }, true);
  svg.addEventListener('click', (e) => { if (ndragMoved) { ndragMoved = false; e.stopImmediatePropagation(); } }, true);
  onWin('resize', () => drawPins());
  function togglePin(id) { if (PINS.has(id)) { PINS.delete(id); PW.delete(id); LASTPIN.delete(id); } else PINS.add(id);
    savePins(); KEEPCAM = true; render(); }
  const linkedTo = (id) => { const s = new Set(); for (const e of adj.get(id) || []) s.add((e.a.id === id ? e.b : e.a).id);
    const n = byAny(id); if (n && n.k === 'topic') for (const m of nodes) if ((m.d || '') === n.path) s.add(m.id); return s; };
  function drawDock() {
    dock.hidden = !PINS.size; if (!PINS.size) return;
    const here = new Set(LZ ? LZ.items.map((o) => o.id) : []);
    dock.innerHTML = `<div class="cmp-sec-h cmp-pinh">${pinIcon(14)}${esc(C.pinned(PINS.size))}</div>` + [...PINS].map((id) => { const n = byAny(id), ln = [...linkedTo(id)].filter((x) => here.has(x)).length;
      return `<div class="pi" data-id="${esc(id)}"><button class="pg" data-id="${esc(id)}" title="${esc(n.t || n.path || id)}"><svg viewBox="-7 -7 14 14">${n.k === 'topic' ? `<circle r="5" fill="none" style="stroke:${colv(n)}" stroke-width="1.8" stroke-dasharray="2 1.4"/>` : shape(n.k, 0, 0, 5, colv(n), !!n.vu)}</svg><span>${esc(short(titleOf(n), 34))}</span>${ln ? `<em>${ln}</em>` : ''}</button><button class="px" data-x="${esc(id)}" aria-label="${esc(C.unpin)}">✕</button></div>`; }).join('');
  }
  function markPins() { if (!LZ) return; const lk = new Map(); for (const id of PINS) for (const x of linkedTo(id)) lk.set(x, (lk.get(x) || 0) + 1);
    for (const g of svg.querySelectorAll('#cmp-nodes .node')) { g.querySelector('.pinmark')?.remove(); g.classList.toggle('pinned', PINS.has(g.dataset.id)); const o = LZ.items.find((q) => q.id === g.dataset.id); if (!o) continue;
      const mine = PINS.has(g.dataset.id), k = lk.get(g.dataset.id); if (!mine && !k) continue;
      g.insertAdjacentHTML('beforeend', `<g class="pinmark${mine ? ' self' : ''}" transform="translate(${(o.r * .8 + 3).toFixed(1)} ${(-o.r * .8 - 3).toFixed(1)})"><circle r="${mine ? 6 : 3.2}"/>${mine ? `<svg x="-4.4" y="-4.4" width="8.8" height="8.8" viewBox="0 0 24 24">${PIN_PATHS}</svg>` : ''}</g>`); } }
  dock.addEventListener('click', (e) => { const x = e.target.closest('[data-x]'); if (x) { togglePin(x.dataset.x); return; }
    const g = e.target.closest('.pg'); if (!g) return; const n = byAny(g.dataset.id), o = LZ && LZ.items.find((q) => q.id === n.id && q.chain);
    if (o) setPath(o.role === 'hub' ? o.chain : [...o.chain, n]); else setPath(chainTo(n)); });
  dock.addEventListener('pointerover', (e) => { const pi = e.target.closest('.pi'); const on = pi ? new Set([pi.dataset.id, ...linkedTo(pi.dataset.id)]) : null;
    svg.classList.toggle('hot', !!on); svg.querySelectorAll('#cmp-nodes .node').forEach((g) => g.classList.toggle('pick', !!on && on.has(g.dataset.id))); });
  dock.addEventListener('pointerleave', () => { svg.classList.remove('hot'); svg.querySelectorAll('#cmp-nodes .node.pick').forEach((g) => g.classList.remove('pick')); });
  let hoverNode = null; svg.addEventListener('pointerover', (e) => { const g = e.target.closest('.node'); hoverNode = g ? g.dataset.id : null; });
  onWin('keydown', (e) => { if (e.key.toLowerCase() === 'p' && hoverNode && !e.target.closest('input,textarea,[contenteditable]')) togglePin(hoverNode); });
  { const _r2 = render; render = function () { _r2(); drawDock(); markPins(); drawPins(); }; }
  buildTopicColours();
  // 정해진 자리: 모든 주제를 지금과 같은 규칙으로 한꺼번에 펼쳐(목록 자리까지 비워) 한 번 잰다. 하나씩 들어가든 전체를 펼치든 주제는 이 자리에 선다
  (function buildCanon() { const saveM = MODE, saveP = path; CANON = true; MODE = 'all'; path = [topics.get('vinc')];
    const m = new Map(); for (const tp of topics.values()) { const set = new Set(); for (const s2 of tp.sub) set.add(`topic:${s2}`); m.set(tp.id, set); }
    try { const L = layoutTree(topics.get('vinc'), m, null); TPOS = new Map(L.comps.filter((c) => c.hub.k === 'topic').map((c) => [c.hub.id, { x: c.hub.x, y: c.hub.y, th12: c.th12 }])); }
    finally { CANON = false; MODE = saveM; path = saveP; } })();

  setPath(ctx.startPath ? ctx.startPath.map(byAny).filter(Boolean) : [topics.get('vinc')]);
  // ── 렌즈 패널: 옵션(기간)과 범례(종류는 누르면 끄고 켠다 = 범례가 곧 필터) ────────────────────────────────
  const KLABEL = COPY.ui.compass.kinds;
  const countKinds = () => { const c = {}; if (LZ) for (const o of LZ.items) if (o.src && o.k !== "topic" && o.role !== "hub") { const k = kindKey(o.src); c[k] = (c[k] || 0) + 1; } return c; };
  function barsSVG() {
    const W = 300, H = 64, padB = 14, bw = W / allDays.length, cnt = allDays.map(() => 0); for (const n of dated) { const i = dIdx.get(when(n)); if (i != null) cnt[i]++; }
    const maxC = Math.max(1, ...cnt), i0 = TSEL ? (dIdx.get(TSEL[0]) ?? 0) : -1, i1 = TSEL ? (dIdx.get(TSEL[1]) ?? allDays.length - 1) : -1; let s = "";
    cnt.forEach((c, i) => { if (!c) return; const h = (H - padB - 4) * Math.sqrt(c / maxC), inR = !TSEL || (i >= i0 && i <= i1);
      s += `<rect x="${(i * bw).toFixed(1)}" y="${(H - padB - h).toFixed(1)}" width="${Math.max(1, bw - .5).toFixed(1)}" height="${h.toFixed(1)}" class="${inR ? "in" : ""}"/>`; });
    allDays.forEach((d, i) => { if (d.endsWith("-01")) s += `<text x="${(i * bw + 1).toFixed(1)}" y="${H - 2}">${d.slice(2, 7)}</text>`; });
    if (TSEL) s += `<rect class="sel" x="${(i0 * bw).toFixed(1)}" y="1" width="${((i1 - i0 + 1) * bw).toFixed(1)}" height="${H - padB - 1}" rx="3"/>`;
    return `<svg class="cmp-bars" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" data-bars>${s}</svg>`; }
  const mark = (k) => `<svg class="cmp-mk" viewBox="-8 -8 16 16" aria-hidden="true">${k === "topic" ? `<circle r="6" fill="none" stroke="currentColor" stroke-width="1.8" stroke-dasharray="2.2 1.6"/>` : k === "bridge" ? `<path d="M-4.2 -4.2A6 6 0 0 1 4.2 -4.2M4.2 4.2A6 6 0 0 1 -4.2 4.2" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/><circle r="2" fill="currentColor"/>` : shape(k, 0, 0, 5.5, "currentColor")}</svg>`;
  const kindsHTML = () => {
    const c = {}; for (const n of nodes) { if (!matchQ(n) || (TSEL && (!when(n) || when(n) < TSEL[0] || when(n) > TSEL[1]))) continue; const k = kindKey(n); c[k] = (c[k] || 0) + 1; }
    const kinds = ["concept", "doc", "decision", "record", "episode", "bridge"].map((k) => `<button class="cmp-lk${KOFF.has(k) ? " off" : ""}" type="button" data-cmp-k="${k}" aria-pressed="${!KOFF.has(k)}">${mark(k)}<span>${KLABEL[k]}</span><em>${c[k] || 0}</em></button>`).join("");
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
  // ── 시간(시안의 시간 모달을 LNB 로): 위 = 날마다의 수(종류별로 쌓음, 끌어서 기간), 가운데 = 주제 × 기간 격자, 아래 = 고른 칸의 항목 ─────
  let TSCOPE = 'all', TCELL = null, LASTGRID = new Map(), TWIN = null; const OPENROW = new Set();   // TWIN = 좁을 때 보이는 마지막 칸(없으면 끝)
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
    // 격자: 줄 = 영역(눌러서 하위 주제), 칸 = 기간(3주 이하면 날, 넘으면 주)
    const rows = ns.filter((n) => (!TSEL || (when(n) >= TSEL[0] && when(n) <= TSEL[1])) && matchQ(n) && !KOFF.has(kindKey(n)));
    const lo = TSEL && TSEL[0] !== '0000-01-01' ? TSEL[0] : allDays[1], hi2 = TSEL ? TSEL[1] : allDays[allDays.length - 1];
    // 칸 하나에 14px. 칸 폭(LNB 폭에서 이름 칸·합계 칸을 뺀 것)에 들어가는 만큼 낱알을 굵게 하고, 그래도 넘치면 끝쪽(또는 고른 칸 쪽)만 보인다.
    const avail = Math.max(60, (pop.clientWidth || 520) - 32 - 104 - 34), cap = Math.max(1, Math.floor(avail / 14));
    const D0 = Date.parse(lo), D1 = Date.parse(hi2), spanDays = Math.round((D1 - D0) / 864e5) + 1;
    const monday = (ms) => { const d = new Date(ms); const w = (d.getUTCDay() + 6) % 7; return ms - w * 864e5; };
    const monthKey = (ms) => { const d = new Date(ms); return d.getUTCFullYear() * 12 + d.getUTCMonth(); };
    const grain = spanDays <= cap ? 'day' : Math.ceil((D1 - monday(D0)) / (7 * 864e5)) + 1 <= cap ? 'week' : 'month';
    const unit = (ms) => grain === 'day' ? Math.round((ms - D0) / 864e5) : grain === 'week' ? Math.round((monday(ms) - monday(D0)) / (7 * 864e5)) : monthKey(ms) - monthKey(D0);
    const total = unit(D1) + 1, first = Math.max(0, Math.min(total - 1, (TWIN ?? total - 1) - cap + 1)), ncol = Math.min(cap, total);
    const startOf = (c) => { const u = first + c; if (grain === 'day') return D0 + u * 864e5; if (grain === 'week') return monday(D0) + u * 7 * 864e5; const m = monthKey(D0) + u; return Date.UTC(Math.floor(m / 12), m % 12, 1); };
    const colOf = (w) => { const ms = Date.parse(w); if (ms < D0 || ms > D1 + 864e5) return -1; const c = unit(ms) - first; return c >= 0 && c < ncol ? c : -1; };
    const fmt = (ms) => { const d = new Date(ms).toISOString(); return grain === 'month' ? d.slice(0, 7) : d.slice(5, 10); };
    const hidden = first > 0 || first + ncol < total;
    const keyOf = (n) => { const s2 = (n.d || 'vinc').split('/'), a = s2[1] || '(vinc)'; return OPENROW.has(a) && s2[2] ? `${a}/${s2[2]}` : a; };
    const grid = new Map(); for (const n of rows) { const c = colOf(when(n)); if (c < 0) continue; const k = keyOf(n); if (!grid.has(k)) grid.set(k, new Map()); const m = grid.get(k); if (!m.has(c)) m.set(c, []); m.get(c).push(n); }
    LASTGRID = grid;
    const keys = [...grid.keys()].sort((a, b) => a.split('/')[0].localeCompare(b.split('/')[0]) || (a.includes('/') - b.includes('/')) || a.localeCompare(b));
    const maxN = Math.max(1, ...[...grid.values()].flatMap((m) => [...m.values()].map((l) => l.length))), every = Math.max(1, Math.ceil(ncol / Math.max(2, Math.floor(avail / 46))));
    const head = Array.from({ length: ncol }, (_, i) => `<span>${i % every === 0 ? fmt(startOf(i)) : ''}</span>`).join('');
    let gh = `<div class="gh" style="--n:${ncol}"><span></span>${head}<span></span></div>`;
    for (const k of keys) { const m = grid.get(k), sub = k.includes('/'), tsum = [...m.values()].reduce((x, l) => x + l.length, 0), col = topicColour(`vinc/${k}`);
      gh += `<div class="gr${sub ? ' sub' : ''}" style="--n:${ncol}"><button class="gk" data-row="${esc(k)}" title="${esc(k)}">${sub ? '' : (OPENROW.has(k) ? '▾ ' : '▸ ')}${esc(sub ? k.split('/')[1] : k)}</button>` +
        Array.from({ length: ncol }, (_, i) => { const l = m.get(i); if (!l) return '<span></span>'; const a = .2 + .8 * Math.sqrt(l.length / maxN), on = TCELL && TCELL.k === k && TCELL.c === i;
          return `<button class="gc${on ? ' on' : ''}" data-cell="${esc(k)}|${i}" title="${l.length}" style="background:color-mix(in srgb, ${col} ${Math.round(a * 100)}%, var(--c-card))"></button>`; }).join('') + `<span class="gt">${tsum}</span></div>`; }
    const pick = TCELL && grid.get(TCELL.k)?.get(TCELL.c);
    const cnt = K.map(([k, name]) => `${name} ${rows.filter((n) => tKind(n) === k).length}`).join(' · '), undated = nodes.filter((n) => !when(n)).length;
    const lab = TSEL ? `${TSEL[0] === '0000-01-01' ? C.start : TSEL[0]} ~ ${TSEL[1]}` : `${allDays[1]} ~ ${allDays[allDays.length - 1]}`;
    return `      <div class="cmp-seg">${[['7', C.days(7)], ['30', C.days(30)], ['all', C.all]].map(([k, l]) => `<button class="btn btn-sm" data-cmp-r="${k}">${l}</button>`).join('')}</div>
      <p class="cmp-range">${lab}</p>
      <div class="cmp-tleg">${K.map(([k, name, c]) => `<span><i style="background:${c}"></i>${name} ${rows.filter((n) => tKind(n) === k).length}</span>`).join('')}${undated ? `<span title="${esc(C.undatedTip)}">${esc(C.undated(undated))}</span>` : ''}</div>
      <svg class="cmp-bars" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" data-bars>${bars}</svg>
      <div class="cmp-gnav"${hidden ? '' : ' hidden'}><span data-n="${ncol}" data-t="${total}"></span>${hidden ? `<button class="btn btn-sm" data-tw="-1" ${first <= 0 ? 'disabled' : ''}>◀</button><button class="btn btn-sm" data-tw="1" ${first + ncol >= total ? 'disabled' : ''}>▶</button>` : ''}</div>
      <div class="cmp-tg">${gh}</div>
      ${pick ? `<div class="cmp-tl-h">${esc(TCELL.k)} · ${fmt(startOf(TCELL.c))} · ${pick.length}</div><div class="cmp-tlist">${treeList(pick)}</div>` : ''}`;
  }
  // 결과의 위계: 주제(경로 순, 깊이만큼 들여 씀) → 그 안의 개념·문서·결정(뜻의 층) → 거기 붙은 기록·사건·결정(한 칸 더 들여 씀).
  // 붙을 곳이 목록에 없는 항목은 그 주제의 맨 아래에. 주제 묶음은 누르면 접고 편다(묶음이 많으면 처음엔 접힌다).
  const CLOSEDGRP = new Set(); let grpTouched = false;
  const hostOf = (n) => { if (isMean(n)) return null; for (const e of adj.get(n.id) || []) if (ATTACH.has(e.t) && e.a === n && isMean(e.b)) return e.b; return null; };
  const row = (n, lvl) => `<button class="trow" data-id="${esc(n.id)}" style="--lv:${lvl}"><svg viewBox="-7 -7 14 14">${shape(n.k, 0, 0, 5, colv(n), !!n.vu)}</svg><span>${esc(short(titleOf(n), 60))}</span><em>${when(n) ? when(n).slice(5) : ''}</em></button>`;
  function treeList(items) {
    if (!items.length) return '';
    const byTopic = new Map(); for (const n of items) { const d = n.d || 'vinc'; if (!byTopic.has(d)) byTopic.set(d, []); byTopic.get(d).push(n); }
    const paths = [...byTopic.keys()].sort(), areaOf = (d) => d.split('/').slice(0, 2).join('/');
    // 처음에는 맨 위(영역)까지만 보이게 모두 접는다. 영역을 펴면 그 아래 주제들은 펼친 채로 나온다
    if (!grpTouched) CLOSEDGRP.clear();
    const newest = (a, b) => when(b).localeCompare(when(a)) || a.k.localeCompare(b.k);
    const areaN = new Map(); for (const d of paths) areaN.set(areaOf(d), (areaN.get(areaOf(d)) || 0) + byTopic.get(d).length);
    const body = (its, depth) => { let h = ''; const inList = new Set(its.map((n) => n.id)), kids = new Map(), loose = [];
      for (const n of its) { if (isMean(n)) continue; const hst = hostOf(n); if (hst && inList.has(hst.id)) { if (!kids.has(hst.id)) kids.set(hst.id, []); kids.get(hst.id).push(n); } else loose.push(n); }
      let shown = 0;
      for (const m of its.filter(isMean).sort(newest)) { if (shown++ >= 40) break; h += row(m, depth); for (const c of (kids.get(m.id) || []).sort(newest).slice(0, 12)) h += row(c, depth + 1);
        const more = (kids.get(m.id) || []).length - 12; if (more > 0) h += `<div class="tmore" style="--lv:${depth + 1}">+${more}</div>`; }
      for (const n of loose.sort(newest).slice(0, 40)) h += row(n, depth);
      if (loose.length > 40) h += `<div class="tmore" style="--lv:${depth}">+${loose.length - 40}</div>`; return h; };
    let h = '', doneArea = new Set(), shownAnc = new Set();
    for (const d of paths) {
      const a = areaOf(d), segs = d.split('/');
      if (!doneArea.has(a)) { doneArea.add(a); const open = !CLOSEDGRP.has(a);
        h += `<button class="tgrp" data-grp="${esc(a)}" style="--lv:0;--c:${colv({ k: 'topic', path: a })}" aria-expanded="${open}">${open ? '▾' : '▸'} ${esc(a.split('/')[1] || a)}<em>${areaN.get(a)}</em></button>`; }
      if (CLOSEDGRP.has(a)) continue;
      if (d === a) { h += body(byTopic.get(d), 1); continue; }
      for (let i = 3; i < segs.length; i++) { const anc = segs.slice(0, i).join('/'); if (shownAnc.has(anc) || byTopic.has(anc)) continue; shownAnc.add(anc);
        h += `<div class="tgrp anc" style="--lv:${i - 2}">${esc(segs[i - 1])}</div>`; }
      const depth = segs.length - 2, open = !CLOSEDGRP.has(d);
      h += `<button class="tgrp" data-grp="${esc(d)}" style="--lv:${depth};--c:${colv({ k: 'topic', path: d })}" aria-expanded="${open}">${open ? '▾' : '▸'} ${esc(segs[segs.length - 1])}<em>${byTopic.get(d).length}</em></button>`;
      if (open) h += body(byTopic.get(d), depth + 1);
    }
    return h;
  }
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
    // 전체: 모든 주제를 정해진 자리에 한꺼번에 편다(층마다 목록까지). 다시 누르면 지금 길만 남긴다
    const a = e.target.closest('[data-all]'); if (a) { MODE = MODE === 'all' ? 'path' : 'all'; a.setAttribute('aria-pressed', String(MODE === 'all')); render(); } });
  onWin('pointerdown', (e) => { if (POP && !pop.contains(e.target) && !tools.contains(e.target)) closePop(); }, true);
  onWin('keydown', (e) => { if (e.key === 'Escape' && POP) { e.stopImmediatePropagation(); closePop(); } }, true);
  onWin('resize', () => { if (POP) { placePop(); TWIN = null; paintSide(); } });
  // ── LNB 트리의 줄: 거르개(검색 ∧ 기간 ∧ 종류)를 지난 노드를 주제별로. 뜻의 층(depth 0) 아래에 붙은 것(depth 1) ──────
  const passes = (n) => n.k !== 'topic' && matchQ(n) && !KOFF.has(kindKey(n)) && (!TSEL || (!!when(n) && when(n) >= TSEL[0] && when(n) <= TSEL[1]));
  function treeGroups() {
    const by = new Map(); for (const n of nodes) { if (!passes(n)) continue; const d = n.d || 'vinc'; if (!by.has(d)) by.set(d, []); by.get(d).push(n); }
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
    const rb = e.target.closest("[data-cmp-r]"); if (rb) { const k = rb.dataset.cmpR; TSEL = k === "all" ? null : [allDays[Math.max(1, allDays.length - +k)], allDays[allDays.length - 1]]; TCELL = null; TWIN = null; dimLens(); paintSide(); treeChanged(); return; }
    const tw = e.target.closest("[data-tw]"); if (tw) { const sp = pop.querySelector('.cmp-gnav span'), n2 = +sp.dataset.n, tot = +sp.dataset.t;
      TWIN = Math.max(n2 - 1, Math.min(tot - 1, (TWIN ?? tot - 1) + (+tw.dataset.tw) * n2)); TCELL = null; paintSide(); return; }
    const gk = e.target.closest("[data-row]"); if (gk) { const k = gk.dataset.row; if (!k.includes('/')) { OPENROW.has(k) ? OPENROW.delete(k) : OPENROW.add(k); TCELL = null; paintSide(); } return; }
    const gc = e.target.closest("[data-cell]"); if (gc) { const [k, c] = gc.dataset.cell.split('|'); TCELL = TCELL && TCELL.k === k && TCELL.c === +c ? null : { k, c: +c }; paintSide(); return; }
    const gp = e.target.closest("[data-grp]"); if (gp) { grpTouched = true; const d = gp.dataset.grp; CLOSEDGRP.has(d) ? CLOSEDGRP.delete(d) : CLOSEDGRP.add(d); paintSide(); return; }
    const row = e.target.closest(".trow"); if (row) { goTo(row.dataset.id); return; } });
  pop.addEventListener("pointerover", (e) => { const cell = e.target.closest("[data-cell]"), row = e.target.closest(".trow");
    if (cell) { const [k, c] = cell.dataset.cell.split('|'); highlight(new Set((LASTGRID.get(k)?.get(+c) || []).map((n) => n.id))); }
    else if (row) highlight(new Set([row.dataset.id])); else highlight(null); });
  pop.addEventListener("pointerleave", () => highlight(null));
  let br = null;
  const barsEl = () => pop.querySelector("[data-bars]");
  const day = (e) => { const r = barsEl().getBoundingClientRect(); return Math.max(0, Math.min(allDays.length - 1, Math.floor((e.clientX - r.left) / (r.width / allDays.length)))); };
  const val = (i, end) => (i === 0 ? (end ? allDays[1] : "0000-01-01") : allDays[i]);
  pop.addEventListener("pointerdown", (e) => { if (!e.target.closest("[data-bars]")) return; br = day(e); pop.setPointerCapture(e.pointerId); TSEL = [val(br), val(br, true)]; TCELL = null; dimLens(); });
  pop.addEventListener("pointermove", (e) => { if (br == null) return; const b2 = day(e); TSEL = [val(Math.min(br, b2)), val(Math.max(br, b2), true)]; dimLens(); });
  pop.addEventListener("pointerup", () => { if (br == null) return; br = null; TWIN = null; paintSide(); treeChanged(); });
  { const _r3 = render; render = function () { _r3(); paintSide(); }; }
  const destroy = () => { AC.abort(); cancelAnimationFrame(flyRaf); cancelAnimationFrame(traf); host.innerHTML = ""; side.remove(); tools.remove(); pop.remove(); ctx.onDestroy?.(); };
  if (import.meta.env?.DEV) (window as any).__compass = { open: (ids) => setPath(ids.map(byAny)), lz: () => LZ, pin: togglePin, range: (r) => { TSEL = r; dimLens(); } };
  // 목록에서 고른 노드로: 지금 렌즈에 이미 사슬이 있으면 그 사슬로(맥락 유지), 없으면 주제 사슬을 따라 편다
  function goTo(id) { const n = byAny(id); if (!n) return; const o = LZ && LZ.items.find((q) => q.id === n.id && q.chain);
    WIDE = true; if (o) setPath(o.role === 'hub' ? o.chain : [...o.chain, n]); else setPath(chainTo(n)); }
  const centre = () => (path.length ? path[path.length - 1].id : '');
  return { destroy, side, tools, filter: setQuery, open: goTo, tree: treeGroups, mark: treeMark, colour: topicColour, centre };
}
