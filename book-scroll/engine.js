/* Vinc Book scroll engine — the single source of truth for motion.
   Inlined verbatim into every chapter artboard by build.py. Do not edit a
   generated .dc.html; edit this and rebuild, or the chapters drift apart
   again (they already had: six divergences measured across Ch1/Ch3/Ch7).

   v2 (2026-09-08, owner review of the first edition): every stroke is drawn
   by a hand — a seeded wobble, no dash pattern — figures carry the chapter's
   own image (bone to vessel, the ring of watchers to a ring of travellers,
   fire, doors, clocks, gates), marks sit on a depth axis so links arc like
   bridges between planes, and one hatched colour wash per chapter marks the
   beat the figure is about. Reasons and the catalogue:
   [[vinc.plan.book-figurative-motion-v2]].

   Line ink is three SOLID steps, never opacity and never a dash pattern.
   Reason: ink_900 at the brief's 0.12 blends to ~#D7D8D6 on grain_100,
   which is lighter than grain_300 — the token file's declared light fade
   floor at APCA Lc 16.0 — so it lands under the Lc 15 invisibility floor
   and a 1px hairline visibly breaks up. Values: [[vinc.design.tokens]]. */

const INK   = '#17171A';  // ink_900   — the line this beat is about
const EDGE  = '#747790';  // slate_500 — "the main edge line", passes both gates
const FADE  = '#D1D2D6';  // grain_300 — the declared light fade floor, Lc 16.0
const PAPER = '#F2F2F0';  // grain_100
const FOCUS = '#7E86D3';  // peri_500  — at most once per chapter, never on a line
const META  = '#82838D';  // ink_400   — captions and meta only
// colour washes: hatched patches behind a figure, never a line colour. Each
// is a token; the owner asked for "colour now and then", so a chapter gets
// at most one wash plus the focus tint.
const WASH  = { peri: '#B9BEF0', ok: '#8FCBA8', danger: '#E08585', warn: '#E5DCC3', indigo: '#BDBFE3' };  // peri_200 · ok_300 · danger_300 · warn_bg · peri_250

const ENTER = 'cubic-bezier(.22,1,.36,1)';   // one entering curve
const LEAVE = 'cubic-bezier(.4,0,.7,1)';     // one leaving curve, shorter
const T_MARK = 300, T_LINE = 500, T_DIM = 200, T_OUT = 200;
const LINE_LAG = 150;      // a line starts this long after its marks settle
const STAGGER  = 25;       // per-index; groups are capped at GROUP_CAP
const GROUP_CAP = 400;     // brief: a whole group stays under 400ms
const CURVE = 0.10;        // one curvature constant for every line on the site
const RECEDE_MARK = 0.40, RECEDE_TEXT = 0.35;
const K = 0.5523;          // circle-as-bezier constant, for the blob morph
const JIT = 0.9;           // the hand: wobble amplitude in px, one value everywhere
const RISE = 0.22;         // how far a link between two depths bows upward, per px of length

const lerp  = (a, b, t) => a + (b - a) * t;
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const smooth = t => { t = clamp(t, 0, 1); return t * t * (3 - 2 * t); };
const pol = (cx, cy, rad, deg) => [cx + rad * Math.cos(deg * Math.PI / 180),
                                   cy + rad * Math.sin(deg * Math.PI / 180)];
const blob = (x, y, r, k) =>
  `M${x},${y - r} C${x + k * r},${y - r} ${x + r},${y - k * r} ${x + r},${y}` +
  ` C${x + r},${y + k * r} ${x + k * r},${y + r} ${x},${y + r}` +
  ` C${x - k * r},${y + r} ${x - r},${y + k * r} ${x - r},${y}` +
  ` C${x - r},${y - k * r} ${x - k * r},${y - r} ${x},${y - r}Z`;
const mixHex = (a, b, t) => {
  const ch = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
  const A = ch(a), B = ch(b);
  return '#' + A.map((v, i) => Math.round(lerp(v, B[i], t)).toString(16).padStart(2, '0')).join('');
};

/* ---- the hand ---------------------------------------------------------
   Every visible stroke goes through here. A deterministic wobble seeded by
   the element's own identity, so a re-render draws the same line and a
   scrub never jitters. No dash pattern anywhere: a hand-drawn line is a
   solid line that is not quite straight. */

const hash = n => { const x = Math.sin(n * 12.9898 + 78.233) * 43758.5453; return x - Math.floor(x); };
const fx = v => Math.round(v * 10) / 10;
const mid = (a, b) => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];

function wob(pts, seed, amp) {
  amp = amp == null ? JIT : amp;
  return pts.map((q, i) => [q[0] + (hash(seed * 31.7 + i * 7.1) - 0.5) * 2 * amp,
                            q[1] + (hash(seed * 17.3 + i * 3.3 + 5) - 0.5) * 2 * amp]);
}
// subdivide so a long straight run gets the slight waver a hand gives it
function dense(pts, step) {
  step = step || 22;
  const out = [];
  for (let i = 0; i < pts.length - 1; i++) {
    const a = pts[i], b = pts[i + 1], d = Math.hypot(b[0] - a[0], b[1] - a[1]);
    const n = Math.max(1, Math.round(d / step));
    for (let k = 0; k < n; k++) out.push([lerp(a[0], b[0], k / n), lerp(a[1], b[1], k / n)]);
  }
  out.push(pts[pts.length - 1]);
  return out;
}
// polyline to path, quadratic through the midpoints so corners round the
// way a pen rounds them; a closed stroke overshoots its start by one
// segment, which is what a drawn circle actually does
function toPath(pts, closed) {
  if (pts.length < 2) return '';
  if (closed) pts = pts.concat([pts[0], pts[1]]);
  let d = `M${fx(pts[0][0])},${fx(pts[0][1])}`;
  if (pts.length === 2) return d + ` L${fx(pts[1][0])},${fx(pts[1][1])}`;
  const m0 = mid(pts[0], pts[1]);
  d += ` L${fx(m0[0])},${fx(m0[1])}`;
  for (let i = 1; i < pts.length - 1; i++) {
    const m = mid(pts[i], pts[i + 1]);
    d += ` Q${fx(pts[i][0])},${fx(pts[i][1])} ${fx(m[0])},${fx(m[1])}`;
  }
  const L = pts[pts.length - 1];
  return d + ` L${fx(L[0])},${fx(L[1])}`;
}
const sketch = (pts, seed, closed, amp) => toPath(wob(dense(pts), seed, amp), closed);
// a drawn circle: sixteen points from just before the top, round, and a
// little past where it started
function circPts(x, y, r, n) {
  n = n || 16;
  return Array.from({ length: n + 2 }, (_, i) => pol(x, y, r, -100 + i * 360 / n));
}
const sketchCircle = (x, y, r, seed, amp) => toPath(wob(circPts(x, y, r), seed, amp == null ? Math.min(JIT, r * 0.12) : amp), false);
const sketchRect = (x, y, w, h, seed) => sketch([[x, y], [x + w, y], [x + w, y + h], [x, y + h]], seed, true);
// the second, lighter pass a sketch gets: one ink step down, offset wobble.
// FADE has no step below it, so a faded thing is drawn once.
const inkBelow = c => c === INK ? EDGE : (c === EDGE ? FADE : null);

/* hatching, for colour washes and for bars: lines at 45°, clipped by
   geometry rather than by a clipPath so the export stays plain paths */
function hatchCircle(x, y, r, gap) {
  const out = [];
  for (let o = -r + gap / 2; o < r; o += gap) {
    const h = Math.sqrt(Math.max(0, r * r - o * o));
    const c = [x + o * 0.7071, y + o * 0.7071];
    out.push([[c[0] - h * 0.7071, c[1] + h * 0.7071], [c[0] + h * 0.7071, c[1] - h * 0.7071]]);
  }
  return out;
}
function hatchRect(x, y, w, h, gap) {
  const out = [];
  for (let o = gap; o < w + h; o += gap) {
    // the diagonal x + y' = o inside the rect, y' measured downward
    const a = [x + Math.max(0, o - h), y + Math.min(h, o)];
    const b = [x + Math.min(w, o), y + Math.max(0, o - w)];
    if (a[0] < x + w && b[1] < y + h) out.push([a, b]);
  }
  return out;
}
/* a wash: hatched patch of colour behind a thing. `t` scrubs how much of it
   has been scribbled in, so the reader watches it fill and can reverse it */
function washPatch(x, y, r, color, seed, t, key) {
  const E = React.createElement;
  if (color === FOCUS) color = WASH.peri;
  const lines = hatchCircle(x, y, r, 4.6);
  const n = Math.round(clamp(t, 0, 1) * lines.length);
  return E('g', { key: key || 'wash', 'data-wash': color, style: { opacity: n ? 1 : 0, transition: `opacity ${T_DIM}ms ${LEAVE}` } },
    lines.slice(0, n).map((ln, i) => E('path', { key: i, d: sketch(ln, seed * 3 + i, false, 0.7),
      fill: 'none', stroke: color, strokeWidth: 2, strokeLinecap: 'round' })));
}

/* ---- glyphs -------------------------------------------------------------
   Figures are built from the same strokes as the diagram: polylines in a
   100×100 box centred on the origin. A pair that morphs (bone↔ship,
   ape↔astronaut) has the same stroke count and the same point count per
   stroke, so the tween is a straight lerp between coordinate arrays and the
   halfway state is a legible form rather than a smear. A stroke given as
   {c:[x,y,r]} is a drawn circle. `closed` lists strokes that close. */
const GLYPH = {
  bone: [[[-30, -5], [-15, -6], [0, -6], [15, -6], [30, -5]],
         [[-30, 5], [-15, 6], [0, 6], [15, 6], [30, 5]],
         [[-30, -5], [-42, -14], [-50, 0], [-42, 14], [-30, 5]],
         [[30, -5], [42, -14], [50, 0], [42, 14], [30, 5]]],
  ship: [[[-12, 34], [-16, 8], [-12, -16], [-6, -32], [0, -46]],
         [[12, 34], [16, 8], [12, -16], [6, -32], [0, -46]],
         [[-12, 10], [-28, 40], [-12, 34], [0, 38], [12, 34]],
         [[12, 10], [28, 40], [12, 34], [6, 48], [-6, 48]]],
  ape: [[[-2, -30], [8, -36], [14, -26], [4, -20]],
        [[4, -20], [-10, -4], [-16, 14], [-12, 30]],
        [[-4, -6], [8, 8], [14, 24], [16, 36]],
        [[-10, 2], [-20, 14], [-26, 26], [-28, 36]],
        [[-12, 30], [-6, 42], [2, 46], [10, 46]]],
  astronaut: [[[-10, -40], [0, -48], [10, -40], [0, -31]],
              [[0, -31], [0, -16], [0, 0], [0, 16]],
              [[0, -24], [-12, -14], [-18, -2], [-16, 8]],
              [[0, -24], [12, -14], [18, -2], [16, 8]],
              [[-8, 44], [0, 16], [8, 44], [9, 46]]],
  // the mark: outer ring, inner ring, sphere — from design-hub/assets/logo
  logo: [[[0, -46], [16, -24], [33, 0], [16, 24], [0, 46], [-16, 24], [-33, 0], [-16, -24]],
         [[0, -34], [13, -18], [26, 0], [13, 18], [0, 34], [-13, 18], [-26, 0], [-13, -18]],
         { c: [0, 0, 8.6] }],
  fire: [[[-18, 22], [-24, 2], [-10, -18], [0, -38], [10, -16], [24, 0], [18, 22]],
         [[-8, 22], [-11, 6], [0, -10], [11, 6], [8, 22]],
         [[-30, 26], [-12, 23], [12, 23], [30, 26]]],
  doc: [[[-24, -32], [12, -32], [24, -20], [24, 32], [-24, 32]],
        [[12, -32], [12, -20], [24, -20]],
        [[-14, -6], [14, -6]], [[-14, 6], [14, 6]], [[-14, 18], [4, 18]]],
  stack: [[[-30, -8], [10, -8], [10, 28], [-30, 28]],
          [[-22, -18], [18, -18], [18, 18]],
          [[-14, -28], [26, -28], [26, 8]]],
  clock: [{ c: [0, 0, 30] }, [[0, 0], [0, -20]], [[0, 0], [14, 8]]],
  lens: [{ c: [-8, -8, 22] }, [[8, 8], [30, 30]]],
  door: [[[-20, 40], [-20, -30], [0, -42], [20, -30], [20, 40]],
         [[-20, 40], [20, 40]], [[10, 2], [12, 6]]],
  gate: [[[-24, -30], [-24, 40]], [[24, -30], [24, 40]], [[-32, -30], [32, -30]]],
  cone: [[[-40, -40], [0, 0], [40, -40]], [[-40, 40], [0, 0], [40, 40]], [[-48, 0], [48, 0]]],
  agent: [[[-18, -36], [18, -36], [18, -8], [-18, -8]],
          [[0, -36], [0, -46], [4, -49]],
          [[-9, -22], [-5, -22]], [[5, -22], [9, -22]],
          [[-22, -4], [22, -4], [22, 30], [-22, 30]],
          [[-22, 4], [-34, 12]], [[22, 4], [34, 12]]],
  tablet: [[[-26, -34], [26, -34], [28, 34], [-28, 34]],
           [[-16, -18], [16, -18]], [[-16, -4], [16, -4]], [[-16, 10], [16, 10]]],
  book: [[[-40, -20], [0, -12], [40, -20]],
         [[-40, -20], [-40, 24], [0, 32], [40, 24], [40, -20]],
         [[0, -12], [0, 32]]],
  screen: [[[-34, -28], [34, -28], [34, 16], [-34, 16]], [[0, 16], [0, 28]], [[-16, 28], [16, 28]]],
  check: [[[-20, 2], [-6, 16], [24, -18]]],
  cross: [[[-16, -16], [16, 16]], [[16, -16], [-16, 16]]],
  arrow: [[[-40, 0], [40, 0]], [[26, -10], [40, 0], [26, 10]]],
  cube: [[[-20, -8], [20, -8], [20, 32], [-20, 32]],
         [[-20, -8], [-6, -24], [34, -24], [20, -8]],
         [[20, -8], [34, -24], [34, 16], [20, 32]]],
  question: [[[-10, -20], [0, -30], [12, -22], [0, -8], [0, 4]], { c: [0, 14, 2] }],
  wall: [[[-4, -46], [-4, 46]], [[4, -46], [4, 46]],
         [[-4, -20], [4, -20]], [[-4, 6], [4, 6]], [[-4, 32], [4, 32]]],
};
const CLOSED = {
  logo: [0, 1], doc: [0], stack: [0], agent: [0, 4], tablet: [0], screen: [0], cube: [0],
  ship: [], bone: [], ape: [], astronaut: [],
};

/* strokes of a glyph at (x, y), `size` px for the 100 box, rotated, and
   lerped toward a second glyph by `t`. Returns path elements. */
function glyphEls(name, name2, t, x, y, size, rot, ink, seed, key, o) {
  o = o || {};
  const E = React.createElement, A = GLYPH[name], B = name2 ? GLYPH[name2] : null;
  if (!A) return [];
  const k = size / 100, c = Math.cos(rot * Math.PI / 180), s = Math.sin(rot * Math.PI / 180);
  const tf = q => [x + (q[0] * c - q[1] * s) * k, y + (q[0] * s + q[1] * c) * k];
  const closed = new Set(CLOSED[name] || []);
  const vis = o.vis !== false;
  const out = [];
  A.forEach((st, i) => {
    let pts;
    if (st.c) {
      pts = circPts(0, 0, st.c[2]).map(q => [q[0] + st.c[0], q[1] + st.c[1]]);
    } else {
      const bs = B && B[i];
      pts = st.map((q, j) => bs ? [lerp(q[0], bs[j][0], t), lerp(q[1], bs[j][1], t)] : q);
    }
    const world = pts.map(tf);
    const d = sketch(world, seed * 7 + i, !st.c && closed.has(i), Math.min(JIT, size * 0.012));
    const delay = (o.delay || 0) + i * 70;
    const tr = vis
      ? `stroke-dashoffset ${T_LINE}ms ${ENTER} ${delay}ms, stroke ${T_DIM}ms ${LEAVE}`
      : `stroke-dashoffset ${T_OUT}ms ${LEAVE}`;
    const base = { fill: 'none', strokeWidth: o.width || 1.25, strokeLinecap: 'round', strokeLinejoin: 'round',
      pathLength: 1, strokeDasharray: 1 };
    out.push(E('path', Object.assign({ key: key + '_' + i, d, stroke: ink,
      style: { strokeDashoffset: vis ? 0 : 1, transition: tr } }, base)));
    const below = o.double && inkBelow(ink);
    if (below) {
      out.push(E('path', Object.assign({ key: key + '_g' + i, d: sketch(world, seed * 11 + i + 3, !st.c && closed.has(i), Math.min(JIT, size * 0.014)),
        stroke: below, style: { strokeDashoffset: vis ? 0 : 1, transition: tr } }, base, { strokeWidth: 0.9 })));
    }
  });
  return out;
}

/* ---- geometry -------------------------------------------------------- */

function shapePoints(shape, x, y, r) {
  if (shape === 'hex') return Array.from({ length: 6 }, (_, k) => pol(x, y, r, 60 * k - 30).join(',')).join(' ');
  if (shape === 'diamond') return [[x, y - r], [x + r, y], [x, y + r], [x - r, y]].map(p => p.join(',')).join(' ');
  return null;
}
function shapePts(shape, x, y, r) {
  if (shape === 'hex') return Array.from({ length: 6 }, (_, k) => pol(x, y, r, 60 * k - 30));
  if (shape === 'diamond') return [[x, y - r], [x + r, y], [x, y + r], [x - r, y]];
  return null;
}
const rot2 = (v, deg) => { const c = Math.cos(deg * Math.PI / 180), s = Math.sin(deg * Math.PI / 180); return [v[0] * c - v[1] * s, v[0] * s + v[1] * c]; };
const norm = v => { const l = Math.hypot(v[0], v[1]) || 1; return [v[0] / l, v[1] / l]; };
// a point on a cubic
const cub = (P0, C1, C2, P2, t) => {
  const u = 1 - t;
  return [u * u * u * P0[0] + 3 * u * u * t * C1[0] + 3 * u * t * t * C2[0] + t * t * t * P2[0],
          u * u * u * P0[1] + 3 * u * u * t * C1[1] + 3 * u * t * t * C2[1] + t * t * t * P2[1]];
};

/* One path string carrying the curve AND, for a directed relation, its
   arrowhead. They must share a path so one stroke-dashoffset reveals both:
   a separately faded-in head leaves the line stopping short of its target
   with nothing at the tip for the length of the delay.

   v2: a cubic whose leaving tangent continues the line that ARRIVED at the
   source mark (`tIn`), so a chain of links reads as one flowing stroke
   rather than a row of separate segments; and a link between two depths
   bows upward by `rise`, so it reads as a bridge between planes. The
   points are then passed through the hand. */
function linkPath(a, b, side, directed, o) {
  o = o || {};
  const dx = b.p[0] - a.p[0], dy = b.p[1] - a.p[1];
  const d = Math.hypot(dx, dy) || 1, u = [dx / d, dy / d];
  const P0 = [a.p[0] + u[0] * (a.r + 2), a.p[1] + u[1] * (a.r + 2)];
  const gap = b.r + 2;
  const P2 = [b.p[0] - u[0] * gap, b.p[1] - u[1] * gap];
  const nx = -u[1], ny = u[0];
  const theta = 20 * side * (CURVE / 0.10);
  // continue the arriving tangent only when this link leaves roughly the
  // same way; a hub's spokes fan out in every direction and must not all
  // inherit one arrival, or they knot (seen on the chapter 1 and 9 rings)
  const cont = o.tIn && (o.tIn[0] * u[0] + o.tIn[1] * u[1]) > 0.35;
  const tIn = cont ? norm([o.tIn[0] * 0.6 + u[0] * 0.4, o.tIn[1] * 0.6 + u[1] * 0.4]) : rot2(u, theta);
  const tOut = rot2(u, -theta * 0.5);
  const rise = o.rise || 0;
  const C1 = [P0[0] + tIn[0] * d * 0.34, P0[1] + tIn[1] * d * 0.34 - rise];
  const C2 = [P2[0] - tOut[0] * d * 0.34, P2[1] - tOut[1] * d * 0.34 - rise];
  const n = Math.max(3, Math.round(d / 18));
  const pts = Array.from({ length: n + 1 }, (_, i) => cub(P0, C1, C2, P2, i / n));
  const midp = cub(P0, C1, C2, P2, 0.5);
  let dAttr = toPath(wob(pts, (o.seed || 0) + 1, o.amp), false);
  if (directed) {
    // barbs measured off the curve's own tangent at P2, so the head sits
    // square to the arrival direction rather than to the chord
    const e = norm([P2[0] - C2[0], P2[1] - C2[1]]), px = -e[1], py = e[0], L = 7, W = 4;
    const A = [P2[0] - e[0] * L + px * W, P2[1] - e[1] * L + py * W];
    const B = [P2[0] - e[0] * L - px * W, P2[1] - e[1] * L - py * W];
    const hd = wob([A, P2, B], (o.seed || 0) + 2, 0.5);
    dAttr += ` M${fx(hd[0][0])},${fx(hd[0][1])} L${fx(hd[1][0])},${fx(hd[1][1])} L${fx(hd[2][0])},${fx(hd[2][1])}`;
  }
  return { d: dAttr, mid: midp, nx, ny, side, tOut: norm([P2[0] - C2[0], P2[1] - C2[1]]) };
}

/* ---- the shared core ------------------------------------------------- */

class BookChapter extends DCLogic {
  state = { step: 0, p: 0 };

  componentDidMount() {
    this.reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.onScroll = () => {
      if (this.raf) return;
      this.raf = requestAnimationFrame(() => { this.raf = 0; this.spy(); });
    };
    document.addEventListener('scroll', this.onScroll, true);
    window.addEventListener('resize', this.onScroll);
    this.spy();
    // the artboard iframe mounts and lays out late; re-read rather than
    // trusting the first measurement
    setTimeout(() => this.spy(), 300);
    setTimeout(() => this.spy(), 1200);
    this.onKey = e => {
      if (e.metaKey || e.ctrlKey || e.altKey || /INPUT|TEXTAREA|BUTTON/.test(e.target.tagName)) return;
      const dir = (e.key === 'ArrowDown' || e.key === 'j') ? 1
                : (e.key === 'ArrowUp'   || e.key === 'k') ? -1 : 0;
      if (!dir) return;
      const els = Array.from(document.querySelectorAll('[data-step]'));
      const next = els[clamp(this.state.step + dir, 0, els.length - 1)];
      if (!next) return;
      e.preventDefault();
      const stacked = window.innerWidth <= 900;
      const line = window.innerHeight * (this.props.triggerLine ?? 0.5);
      const top = next.getBoundingClientRect().top;
      const target = stacked
        ? top - window.innerHeight * 0.46 - 16
        : top - line + Math.min(48, next.getBoundingClientRect().height * 0.1);
      window.scrollBy({ top: target, behavior: this.reduced ? 'auto' : 'smooth' });
    };
    window.addEventListener('keydown', this.onKey);
  }

  componentWillUnmount() {
    document.removeEventListener('scroll', this.onScroll, true);
    window.removeEventListener('resize', this.onScroll);
    window.removeEventListener('keydown', this.onKey);
  }

  spy() {
    const els = Array.from(document.querySelectorAll('[data-step]'));
    if (!els.length) return;
    const stacked = window.innerWidth <= 900;
    const line = stacked ? window.innerHeight * 0.46 + 80
                         : window.innerHeight * (this.props.triggerLine ?? 0.5);
    const tops = els.map(el => el.getBoundingClientRect().top);
    let a = 0;
    tops.forEach((t, i) => { if (t <= line) a = i; });
    els.forEach((el, i) => { el.style.opacity = i === a ? '1' : String(RECEDE_TEXT); });
    const span = a + 1 < tops.length ? tops[a + 1] - tops[a]
                                     : els[a].getBoundingClientRect().height * 0.5;
    const p = this.reduced ? 1 : clamp((line - tops[a]) / Math.max(1, span), 0, 1);
    const step = Math.min(a, this.LAST);
    if (step !== this.state.step || Math.abs(p - this.state.p) > 0.002) this.setState({ step, p });
  }

  /* progress of a scrubbed change that lives on beat `at`: 0 before it,
     scrubbed by p across [from, to] on it, 1 after it */
  scrub(at, from, to) {
    const { step: s, p } = this.state;
    if (s > at) return 1;
    if (s < at) return 0;
    from = from == null ? 0 : from; to = to == null ? 1 : to;
    return smooth(clamp((p - from) / Math.max(0.05, to - from), 0, 1));
  }

  /* index within the group of marks that share an `at`, so a group staggers
     by index and still lands inside GROUP_CAP */
  groupIndex() {
    if (this._gi) return this._gi;
    const gi = {}, count = {}, size = {};
    this.MARKS.forEach(m => { size[m.at] = (size[m.at] || 0) + 1; });
    this.MARKS.forEach(m => {
      gi[m.id] = count[m.at] = (count[m.at] || 0);
      count[m.at]++;
    });
    const step = {};
    Object.keys(size).forEach(at => { step[at] = Math.min(STAGGER, GROUP_CAP / Math.max(1, size[at])); });
    this._gi = {};
    this.MARKS.forEach(m => { this._gi[m.id] = gi[m.id] * step[m.at]; });
    return this._gi;
  }

  /* A mark may carry `pos: {"<step>": [x, y]}` — the brief's "move"
     operation. The position in force is the latest keyed step at or below
     the current one, eased toward the next key by within-beat progress, so
     a move is scrubbed like everything else and reverses on scroll-up.
     Resolved once per render because lines have to follow a moving end. */
  markById() {
    const { step: s, p } = this.state;
    const w = smooth((p - 0.35) / 0.6);
    const out = {};
    const put = m => {
      // `arrange` is a list of positions the mark passes through inside one
      // beat, scrubbed by progress. Used where the argument is that the same
      // marks take a different arrangement and the count never changes.
      if (m.arrange && s >= (m.arrangeAt ?? m.at)) {
        const n = m.arrange.length;
        const f = clamp(p, 0, 1) * (n - 1);
        const i = Math.min(n - 2, Math.floor(f)), t = smooth(f - i);
        const A = m.arrange[i], B = m.arrange[i + 1];
        out[m.id] = Object.assign({}, m, { p: [lerp(A[0], B[0], t), lerp(A[1], B[1], t)] });
        return;
      }
      if (!m.pos) { out[m.id] = m; return; }
      const keys = Object.keys(m.pos).map(Number).sort((a, b) => a - b);
      const past = keys.filter(k => k <= s);
      const cur = m.pos[past.length ? past[past.length - 1] : keys[0]];
      const nxt = m.pos[s + 1];
      out[m.id] = Object.assign({}, m, {
        p: nxt ? [lerp(cur[0], nxt[0], w), lerp(cur[1], nxt[1], w)] : cur });
    };
    (this.ANCHORS || []).forEach(put);
    this.MARKS.forEach(put);
    return out;
  }

  /* focus tint: 0..1, driven by the chapter's own focusAt/focusScrub */
  focusT() {
    const { step: s, p } = this.state;
    if (this.FOCUS_AT == null) return 0;
    if (s > this.FOCUS_AT) return 1;
    if (s < this.FOCUS_AT) return 0;
    return this.FOCUS_SCRUB === false ? 1 : smooth(p * 1.4);
  }

  /* depth: a mark's `z` runs 0 (far) to 1 (near). Size, ink and a drawn
     shadow follow it, and a link between two depths bows upward. */
  zOf(m) { return m.z == null ? 0.6 : m.z; }

  renderMarks() {
    const { step: s, p } = this.state, E = React.createElement;
    const delay = this.groupIndex(), ft = this.focusT(), BY = this.markById();
    return this.MARKS.map((src, idx) => {
      const m = BY[src.id] || src;
      const vis = s >= m.at;
      const dim = m.dim != null && s >= m.dim && !(m.focus && ft > 0);
      // a focus mark that was dimmed rises back to full ink as the focus
      // tint scrubs in, which is what makes the closing return read as a
      // return rather than as a new drawing
      const rising = m.focus && m.dim != null && s >= m.dim;
      const op = !vis ? 0 : dim ? RECEDE_MARK : (rising ? lerp(RECEDE_MARK, 1, ft) : 1);
      const tr = !vis
        ? `opacity ${T_OUT}ms ${LEAVE}, transform ${T_OUT}ms ${LEAVE}`
        : dim
          ? `opacity ${T_DIM}ms ${LEAVE}`
          : `opacity ${T_MARK}ms ${ENTER} ${delay[m.id]}ms, transform ${T_MARK}ms ${ENTER} ${delay[m.id]}ms`;
      const z = this.zOf(m), zs = 0.85 + 0.3 * z;
      const col = m.focus ? mixHex(INK, FOCUS, ft) : (z < 0.3 ? EDGE : INK);
      const r = m.r * zs;
      const seed = idx + 1;
      const kids = [];
      // a near mark casts a drawn shadow on the plane under it
      if (z > 0.78 && vis) {
        kids.push(E('path', { key: 'sh', d: sketch(
          Array.from({ length: 9 }, (_, i) => pol(m.p[0], m.p[1] + r * 1.55, r * 1.25, 180 + i * 22.5))
            .map(q => [q[0], m.p[1] + r * 1.55 + (q[1] - m.p[1] - r * 1.55) * 0.32]), seed + 40, false, 0.5),
          fill: 'none', stroke: FADE, strokeWidth: 1, strokeLinecap: 'round' }));
      }
      if (m.glyph) {
        // a mark drawn as a figure: the figure keeps the mark's position,
        // ink, dimming and lines, and may morph into a matched glyph by
        // focus tint or on its own beat
        const mt = m.morphGlyph ? (m.glyphAt != null ? this.scrub(m.glyphAt, 0, 0.85) : ft) : 0;
        kids.push(...glyphEls(m.glyph, m.morphGlyph, mt, m.p[0], m.p[1], (m.gs ?? m.r * 2.6) * zs, m.rot ?? 0,
          col, seed * 3, 'gm' + m.id, { vis, double: !!m.double, width: 1.1 }));
      } else if (m.shape === 'circle') {
        kids.push(E('path', { key: 's', d: blob(m.p[0], m.p[1], r, m.morphToHex ? lerp(K, 0, ft) : K), fill: col }));
      } else if (m.shape === 'ring') {
        kids.push(
          E('path', { key: 's', d: sketchCircle(m.p[0], m.p[1], r, seed), fill: PAPER, stroke: col, strokeWidth: 1.5, strokeLinecap: 'round' }),
          E('path', { key: 'd', d: blob(m.p[0], m.p[1], r * 0.42, K), fill: col }));
      } else if (m.shape === 'bar') {
        kids.push(E('rect', { key: 's', x: m.p[0] - m.r, y: m.p[1] - (m.h ?? 6) / 2, width: m.r * 2, height: m.h ?? 6, fill: col }));
      } else {
        const pts = shapePts(m.shape, m.p[0], m.p[1], r);
        kids.push(E('path', {
          key: 's', d: sketch(pts, seed, true, Math.min(JIT, r * 0.09)),
          fill: m.shape === 'diamond' ? col : PAPER, stroke: col,
          strokeWidth: 1.25, strokeLinejoin: 'round', strokeLinecap: 'round' }));
      }
      if (m.wash && s >= (m.washAt ?? m.at)) {
        kids.unshift(washPatch(m.p[0], m.p[1], (m.washR ?? r * 1.9), WASH[m.wash] || m.wash, seed + 9,
          this.scrub(m.washAt ?? m.at, 0.15, 0.8), 'w'));
      }
      if (m.label) {
        const lv = s >= (m.labelAt ?? m.at);
        // lpos places the label on one of four sides; a dense hub needs it,
        // and it keeps labels off the lines rather than on top of them
        const LP = {
          above: [0, -r - 12, 'middle'],
          below: [0, r + 18, 'middle'],
          left:  [-r - 10, 4, 'end'],
          right: [r + 10, 4, 'start'],
        }[m.lpos || 'below'];
        kids.push(E('text', {
          key: 'l', x: m.p[0] + LP[0] + (m.labelDx ?? 0), y: m.p[1] + LP[1] + (m.labelDy ?? 0),
          textAnchor: m.labelAnchor ?? LP[2], fontSize: m.labelSize ?? 13,
          fontStyle: 'italic', fill: INK,
          style: { opacity: lv ? (dim ? RECEDE_TEXT : 1) : 0, transition: `opacity ${T_MARK}ms ${ENTER}` } },
          m.label));
      }
      return E('g', {
        key: m.id, 'data-mark': m.id,
        style: {
          opacity: op, transform: vis ? 'scale(1)' : 'scale(.85)',
          transformBox: 'fill-box', transformOrigin: 'center', transition: tr } },
        kids);
    });
  }

  renderLines() {
    const { step: s } = this.state, E = React.createElement;
    const BY = this.markById();
    // the direction each mark was arrived at by, so the next link can
    // leave it along the same tangent (the chain reads as one stroke)
    const inDir = {};
    this.LINES.forEach(l => {
      const a = BY[l.from], b = BY[l.to];
      if (!a || !b || s < l.at) return;
      if (!inDir[l.to]) inDir[l.to] = norm([b.p[0] - a.p[0], b.p[1] - a.p[1]]);
    });
    // a link meets a figure-mark at the figure's own edge
    const edge = m => m.glyph ? Object.assign({}, m, { r: (m.gs ?? m.r * 2.6) * 0.48 }) : m;
    return this.LINES.map((l, i) => {
      const a0 = BY[l.from], b0 = BY[l.to];
      if (!a0 || !b0) return null;
      const a = edge(a0), b = edge(b0);
      const vis = s >= l.at;
      // Three solid steps. A line is full ink unless the chapter says it is
      // context (ink: 2) or has receded (fade: <step>). Nothing is expressed
      // as low opacity, so no line falls under the visibility floor, and
      // nothing is expressed as a dash, so no line reads as broken.
      const receded = l.fade != null && s >= l.fade;
      const stroke = receded ? FADE : (l.ink === 2 ? EDGE : INK);
      const side = l.side ?? (i % 2 ? 1 : -1);
      const dz = Math.abs(this.zOf(a) - this.zOf(b));
      const len = Math.hypot(b.p[0] - a.p[0], b.p[1] - a.p[1]);
      const geo = linkPath(a, b, side, !!l.dir, {
        tIn: l.free ? null : inDir[l.from], rise: dz > 0.2 ? dz * len * RISE : 0, seed: i + 11 });
      // a line waits only for the marks it connects, and only when they are
      // new on this same beat
      const fresh = a.at === l.at || b.at === l.at;
      const lag = (fresh ? T_MARK + LINE_LAG : LINE_LAG)
                + Math.min(STAGGER, GROUP_CAP / Math.max(1, this.LINES.length)) * (l.stagger ?? i % 9);
      const tr = vis
        ? `stroke-dashoffset ${T_LINE}ms ${ENTER} ${lag}ms, stroke ${T_DIM}ms ${LEAVE}`
        : `stroke-dashoffset ${T_OUT}ms ${LEAVE}`;
      const kids = [E('path', {
        key: 'p', d: geo.d, fill: 'none', stroke, strokeWidth: 1,
        strokeLinecap: 'round', strokeLinejoin: 'round',
        pathLength: 1, strokeDasharray: 1,
        style: { strokeDashoffset: vis ? 0 : 1, transition: tr } })];
      if (l.label) {
        const lv = s >= (l.labelAt ?? l.at);
        kids.push(E('text', {
          key: 'l', x: geo.mid[0] + geo.nx * 12 * side, y: geo.mid[1] + geo.ny * 12 * side + 4,
          textAnchor: 'middle', fontSize: 12.5, fontStyle: 'italic',
          fill: receded ? FADE : INK,
          style: { opacity: lv ? 1 : 0, transition: `opacity ${T_MARK}ms ${ENTER}` } },
          l.label));
      }
      return E('g', { key: l.from + '>' + l.to + i, 'data-line': l.from + '>' + l.to,
        style: { opacity: vis ? 1 : 0, transition: `opacity ${T_OUT}ms ${LEAVE}` } }, kids);
    }).filter(Boolean);
  }

  /* ---- figures ----------------------------------------------------------
     A chapter's own images: `figures: [{id, glyph, p, size, at, ...}]`.
       until / dim      last beat it is full ink / beat it recedes
       morph            {glyph, at, from, to}: lerp to a matched glyph, scrubbed
       fly              {p0, rot0, arc, from, to}: travel from p0 to p on beat `at`
       wash, washAt     a hatched colour patch behind it, scribbled in by scroll
       ink: 2           drawn in the second ink (context, not subject)
       label            set text under it
     Every value is read off (step, p); nothing plays on a timer. */
  renderFigures() {
    const { step: s, p } = this.state, E = React.createElement;
    return (this.FIGURES || []).map((fg, i) => {
      const seed = 100 + i * 13;
      const vis = s >= fg.at && (fg.until == null || s <= fg.until);
      const dim = fg.dim != null && s >= fg.dim;
      let x = fg.p[0], y = fg.p[1], rot = fg.rot ?? 0, size = fg.size ?? 60;
      if (fg.fly) {
        const t = this.scrub(fg.at, fg.fly.from, fg.fly.to);
        x = lerp(fg.fly.p0[0], fg.p[0], t);
        y = lerp(fg.fly.p0[1], fg.p[1], t) - Math.sin(t * Math.PI) * (fg.fly.arc ?? 0);
        rot = lerp(fg.fly.rot0 ?? 0, fg.rot ?? 0, t);
        if (fg.fly.size0 != null) size = lerp(fg.fly.size0, size, t);
      }
      const mt = fg.morph ? this.scrub(fg.morph.at ?? fg.at, fg.morph.from, fg.morph.to) : 0;
      const ink = dim ? FADE : (fg.ink === 2 ? EDGE : INK);
      const kids = [];
      if (fg.wash && s >= (fg.washAt ?? fg.at)) {
        kids.push(washPatch(x, y, fg.washR ?? size * 0.6, WASH[fg.wash] || fg.wash, seed + 5,
          this.scrub(fg.washAt ?? fg.at, fg.washFrom ?? 0.15, fg.washTo ?? 0.8), 'w'));
      }
      kids.push(...glyphEls(fg.glyph, fg.morph && fg.morph.glyph, mt, x, y, size, rot, ink, seed, 'f' + i,
        { vis, double: fg.double !== false && !dim, delay: fg.delay || 0 }));
      if (fg.label) {
        kids.push(E('text', { key: 'lb', x: x + (fg.labelDx ?? 0), y: y + size * 0.5 + (fg.labelDy ?? 18),
          textAnchor: 'middle', fontSize: fg.labelSize ?? 12.5, fontStyle: 'italic',
          fill: dim ? EDGE : INK,
          style: { opacity: vis && s >= (fg.labelAt ?? fg.at) ? 1 : 0, transition: `opacity ${T_MARK}ms ${ENTER}` } },
          fg.label));
      }
      return E('g', { key: fg.id || 'fig' + i, 'data-fig': fg.id || String(i),
        style: { opacity: vis ? (dim ? RECEDE_MARK : 1) : 0, transition: `opacity ${T_OUT}ms ${LEAVE}` } }, kids);
    });
  }

  /* ---- part registers -------------------------------------------------
     The motion grammar changes by part, and it changes here rather than
     per chapter, so a Part 1 page cannot quietly drift back into Part 0's
     register. Part 0 accumulates on a persistent canvas (marks + lines
     above). The three below are the other parts, plus the hub. */

  /* PART 1, conveyor. Nothing accumulates: a payload enters a stage, takes
     that stage's form, and leaves. Stations are fixed; the payload moves. */
  drawConveyor(s, p) {
    const E = React.createElement, C = this.CONVEYOR;
    const st = C.stations, n = st.length;
    const y = C.y ?? 300, x0 = C.x0 ?? 96, x1 = C.x1 ?? 544;
    const at = i => x0 + (x1 - x0) * (n === 1 ? 0.5 : i / (n - 1));
    const kids = [];
    // the belt: one continuous rule the whole way, so the path a payload
    // takes is never drawn as a broken line
    kids.push(E('path', { key: 'belt', d: sketch([[x0 - 28, y], [x1 + 28, y]], 3, false),
      fill: 'none', stroke: EDGE, strokeWidth: 1, strokeLinecap: 'round' }));
    st.forEach((t, i) => {
      const live = s >= i;
      const g = [E('path', { key: 'tk', d: sketch([[at(i), y - 9], [at(i), y + 9]], 20 + i, false, 0.6),
          fill: 'none', stroke: live ? INK : EDGE, strokeWidth: 1, strokeLinecap: 'round' }),
        E('text', { key: 'nm', x: at(i), y: y + 34, textAnchor: 'middle', fontSize: 12.5,
          fontStyle: 'italic', fill: live ? INK : EDGE }, t.name)];
      // a station may carry its own small figure under the name
      if (t.glyph) g.push(...glyphEls(t.glyph, null, 0, at(i), y + 66, 30, 0, live ? INK : EDGE, 40 + i, 'sg' + i, { width: 1 }));
      kids.push(E('g', { key: 'st' + i,
        style: { opacity: live ? 1 : 0.35, transition: `opacity ${T_DIM}ms ${LEAVE}` } }, g));
    });
    // the payload: between station s-1 and s, scrubbed by p, splitting into
    // this stage's count and taking this stage's shape on arrival
    const i = clamp(s, 0, n - 1);
    const prev = Math.max(0, i - 1);
    const t = i === 0 ? 1 : smooth(clamp(p * 1.2, 0, 1));
    const px = lerp(at(prev), at(i), t);
    const from = st[prev], to = st[i];
    const cnt = Math.max(1, Math.round(lerp(from.count ?? 1, to.count ?? 1, t)));
    const shape = t > 0.5 ? (to.shape || 'circle') : (from.shape || 'circle');
    const r = t > 0.5 ? (to.r ?? 11) : (from.r ?? 11);
    const pg = t > 0.5 ? to.payload : from.payload;
    for (let k = 0; k < cnt; k++) {
      const spread = cnt === 1 ? 0 : (k - (cnt - 1) / 2) * (r * 2.4);
      const py = y - 62 + (to.scatter ? Math.sin(k * 2.399) * 22 * t : 0);
      const cx = px + (to.scatter ? Math.cos(k * 2.399) * 26 * t : 0) + (to.scatter ? 0 : spread);
      if (pg) {
        kids.push(E('g', { key: 'pl' + k }, glyphEls(pg, null, 0, cx, py, r * 3, 0, INK, 60 + k, 'pg' + k, { width: 1.1 })));
      } else if (shape === 'hex' || shape === 'diamond') {
        kids.push(E('path', { key: 'pl' + k, d: sketch(shapePts(shape, cx, py, r), 60 + k + s, true, Math.min(JIT, r * 0.09)),
          fill: PAPER, stroke: INK, strokeWidth: 1.25, strokeLinejoin: 'round', strokeLinecap: 'round' }));
      } else if (shape === 'ring') {
        kids.push(E('g', { key: 'pl' + k },
          E('path', { d: sketchCircle(cx, py, r, 60 + k), fill: PAPER, stroke: INK, strokeWidth: 1.5, strokeLinecap: 'round' }),
          E('path', { d: blob(cx, py, r * 0.42, K), fill: INK })));
      } else {
        kids.push(E('path', { key: 'pl' + k, d: blob(cx, py, r, K), fill: INK }));
      }
    }
    if (to.note) {
      kids.push(E('text', { key: 'note', x: px, y: y - 100, textAnchor: 'middle',
        fontSize: 13, fontStyle: 'italic', fill: INK,
        style: { opacity: t > 0.6 ? 1 : 0, transition: `opacity ${T_MARK}ms ${ENTER}` } }, to.note));
    }
    return E('g', { key: 'conveyor' }, kids);
  }

  /* THE HUB. One centre, several roles around it, each on its own spoke:
     the register for a chapter whose argument is "one thing, N faces". The
     spokes are revealed one per beat, so the reader watches the picture
     get its parts named; `crossAt` joins every spoke to every other in the
     fade ink, which is the picture of a graph that says nothing. */
  drawHub(s, p) {
    const E = React.createElement, H = this.HUB;
    const cx = H.cx ?? 320, cy = H.cy ?? 300, r = H.r ?? 72, R = H.spokeR ?? r + 122;
    const at0 = H.at ?? 0, live = s >= at0;
    const kids = [];
    const ctr = H.center || {};
    if (ctr.wash && s >= (ctr.washAt ?? at0)) {
      kids.push(washPatch(cx, cy, (ctr.size ?? 70) * 0.62, WASH[ctr.wash], 7, this.scrub(ctr.washAt ?? at0, 0.15, 0.8), 'cw'));
    }
    kids.push(...glyphEls(ctr.glyph || 'logo', null, 0, cx, cy, ctr.size ?? 70, 0, INK, 5, 'hc', { vis: live, double: true }));
    if (ctr.label) {
      kids.push(E('text', { key: 'cl', x: cx, y: cy + (ctr.size ?? 70) * 0.5 + 20, textAnchor: 'middle', fontSize: 13,
        fontStyle: 'italic', fill: INK, style: { opacity: live ? 1 : 0, transition: `opacity ${T_MARK}ms ${ENTER}` } }, ctr.label));
    }
    // the ring: one drawn circle, second pass in the edge ink
    const ringOn = s >= (H.ringAt ?? at0);
    [[INK, 1, 0], [EDGE, 0.9, 1]].forEach(([c, w, k]) => {
      kids.push(E('path', { key: 'ring' + k, d: sketchCircle(cx, cy, r + k * 1.2, 9 + k * 3), fill: 'none', stroke: c,
        strokeWidth: w, strokeLinecap: 'round', pathLength: 1, strokeDasharray: 1,
        style: { strokeDashoffset: ringOn ? 0 : 1, transition: ringOn ? `stroke-dashoffset ${T_LINE + 200}ms ${ENTER} ${k * 120}ms` : `stroke-dashoffset ${T_OUT}ms ${LEAVE}` } }));
    });
    const n = H.spokes.length;
    const centre = q => pol(cx, cy, R, q);
    H.spokes.forEach((sp, i) => {
      const ang = sp.angle ?? (-90 + i * 360 / n);
      const q = centre(ang);
      const on = s >= sp.at;
      const receded = sp.fade != null && s >= sp.fade;
      const ink = receded ? FADE : (sp.ink === 2 ? EDGE : INK);
      const size = sp.size ?? 44;
      const a0 = { p: pol(cx, cy, r, ang), r: 2 }, a1 = { p: pol(cx, cy, R - size * 0.66, ang), r: 2 };
      const dir = sp.dir ?? 'out';
      const [P, Q] = dir === 'in' ? [a1, a0] : [a0, a1];
      const geo = linkPath(P, Q, i % 2 ? 1 : -1, dir !== 'none', { seed: 30 + i, amp: 0.7 });
      const g = [];
      if (sp.wash && s >= (sp.washAt ?? sp.at)) {
        g.push(washPatch(q[0], q[1], size * 0.6, WASH[sp.wash], 50 + i, this.scrub(sp.washAt ?? sp.at, 0.15, 0.8), 'w'));
      }
      g.push(E('path', { key: 'sp', d: geo.d, fill: 'none', stroke: ink, strokeWidth: 1, strokeLinecap: 'round',
        strokeLinejoin: 'round', pathLength: 1, strokeDasharray: 1,
        style: { strokeDashoffset: on ? 0 : 1, transition: on ? `stroke-dashoffset ${T_LINE}ms ${ENTER} ${LINE_LAG}ms, stroke ${T_DIM}ms ${LEAVE}` : `stroke-dashoffset ${T_OUT}ms ${LEAVE}` } }));
      if (dir === 'both') {
        const back = linkPath(Q, P, i % 2 ? -1 : 1, true, { seed: 80 + i, amp: 0.7 });
        g.push(E('path', { key: 'sp2', d: back.d, fill: 'none', stroke: ink, strokeWidth: 1, strokeLinecap: 'round',
          strokeLinejoin: 'round', pathLength: 1, strokeDasharray: 1,
          style: { strokeDashoffset: on ? 0 : 1, transition: on ? `stroke-dashoffset ${T_LINE}ms ${ENTER} ${LINE_LAG + 120}ms` : `stroke-dashoffset ${T_OUT}ms ${LEAVE}` } }));
      }
      g.push(...glyphEls(sp.glyph || 'doc', null, 0, q[0], q[1], size, 0, ink, 20 + i, 'sg' + i, { vis: on, double: !receded, delay: LINE_LAG + 200 }));
      const lx = q[0] + (sp.labelDx ?? 0), ly = q[1] + size * 0.5 + 16 + (sp.labelDy ?? 0);
      g.push(E('text', { key: 'nm', x: lx, y: ly, textAnchor: 'middle', fontSize: 12.5, fontStyle: 'italic', fill: receded ? EDGE : INK }, sp.name));
      if (sp.sub) g.push(E('text', { key: 'sb', x: lx, y: ly + 15, textAnchor: 'middle', fontSize: 11, fill: META }, sp.sub));
      kids.push(E('g', { key: 'spoke' + i, 'data-spoke': sp.name,
        style: { opacity: on ? 1 : 0, transition: `opacity ${T_MARK}ms ${ENTER}` } }, g));
    });
    if (H.crossAt != null && s >= H.crossAt) {
      const t = this.scrub(H.crossAt, 0.1, 0.9), pairs = [];
      for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) pairs.push([i, j]);
      const m = Math.round(t * pairs.length);
      pairs.slice(0, m).forEach(([i, j], k) => {
        const A = centre(H.spokes[i].angle ?? (-90 + i * 360 / n)), B = centre(H.spokes[j].angle ?? (-90 + j * 360 / n));
        kids.push(E('path', { key: 'x' + k, d: sketch([A, B], 200 + k, false), fill: 'none', stroke: FADE, strokeWidth: 1, strokeLinecap: 'round' }));
      });
    }
    if (H.note && s >= (H.noteAt ?? 0)) {
      kids.push(E('text', { key: 'note', x: cx, y: cy + R + 66, textAnchor: 'middle', fontSize: 13, fontStyle: 'italic', fill: INK }, H.note));
    }
    return E('g', { key: 'hub' }, kids);
  }

  /* PART 2, before and after. The same thing run twice under two conditions,
     the two results side by side, counts and bars. Nothing is dramatised:
     the whole point of Part 2 is that a failure looked normal. */
  drawPair(s, p) {
    const E = React.createElement, P = this.PAIR;
    const rows = P.rows, kids = [];
    const LX = P.lx ?? 200, RX = P.rx ?? 452, Y0 = P.y0 ?? 168, DY = P.dy ?? 96;
    [[LX, P.left], [RX, P.right]].forEach(([x, head], c) => {
      kids.push(E('text', { key: 'h' + c, x: x, y: Y0 - 44, textAnchor: 'middle',
        fontSize: 13, fontStyle: 'italic', fill: INK }, head));
      kids.push(E('path', { key: 'hr' + c, d: sketch([[x - 84, Y0 - 30], [x + 84, Y0 - 30]], 4 + c, false),
        fill: 'none', stroke: EDGE, strokeWidth: 1, strokeLinecap: 'round' }));
    });
    rows.forEach((row, i) => {
      const live = s >= (row.at ?? i + 1);
      const y = Y0 + i * DY;
      kids.push(E('text', { key: 'rl' + i, x: 320, y: y - 22, textAnchor: 'middle',
        fontSize: 12.5, fill: META,
        style: { opacity: live ? 1 : 0, transition: `opacity ${T_MARK}ms ${ENTER}` } }, row.label));
      [[LX, row.a], [RX, row.b]].forEach(([x, v], c) => {
        const g = [];
        if (row.kind === 'count') {
          // literal marks in a row: no bar chart when the number is small
          // enough to count
          for (let k = 0; k < v; k++) {
            g.push(E('path', { key: 'c' + k, d: blob(x - (v - 1) * 9 + k * 18, y, 5.5, K), fill: INK }));
          }
        } else {
          // a bar is hatched, not filled: the same hand that drew the rest
          const w = clamp(v / (row.max ?? 1), 0, 1) * 148;
          const fillT = live ? (s > (row.at ?? i + 1) ? 1 : smooth(clamp(p * 1.3, 0, 1))) : 0;
          hatchRect(x - 74, y - 9, w * fillT, 18, 4.2).forEach((ln, k) =>
            g.push(E('path', { key: 'hh' + k, d: sketch(ln, 300 + i * 20 + k, false, 0.5), fill: 'none', stroke: INK, strokeWidth: 1, strokeLinecap: 'round' })));
          g.push(E('path', { key: 'f', d: sketchRect(x - 74, y - 9, 148, 18, 70 + i * 3 + c), fill: 'none', stroke: EDGE, strokeWidth: 1, strokeLinecap: 'round' }));
          if (w > 0) g.push(E('path', { key: 'e', d: sketch([[x - 74 + w, y - 9], [x - 74 + w, y + 9]], 90 + i, false, 0.5), fill: 'none', stroke: INK, strokeWidth: 1.25, strokeLinecap: 'round' }));
          g.push(E('text', { key: 't', x: x + 84, y: y + 5, fontSize: 12.5, fill: INK,
            style: { fontVariantNumeric: 'tabular-nums' } }, row.fmt ? row.fmt.replace('%', String(v)) : String(v)));
        }
        kids.push(E('g', { key: 'r' + i + '_' + c,
          style: { opacity: live ? 1 : 0, transition: `opacity ${T_MARK}ms ${ENTER} ${c * 60}ms` } }, g));
      });
    });
    if (P.note && s >= (P.noteAt ?? rows.length)) {
      kids.push(E('text', { key: 'pn', x: 320, y: Y0 + rows.length * DY + 12,
        textAnchor: 'middle', fontSize: 13, fontStyle: 'italic', fill: INK }, P.note));
    }
    return E('g', { key: 'pair' }, kids);
  }

  /* PART 3, the loop. The site bans looping motion everywhere else; this is
     the one exception and it is earned, because the chapter's claim is that
     the process is circular. It runs three times and stops on the first
     position, and it is scrubbed by scroll rather than played on a timer. */
  drawRing(s, p) {
    const E = React.createElement, R = this.RING;
    const steps = R.steps, n = steps.length;
    const cx = R.cx ?? 320, cy = R.cy ?? 320, rad = R.rad ?? 176;
    const kids = [];
    // the ring's structure appears early and the traveller runs late, so the
    // canvas is never empty while the chapter is still naming the stations
    const live = s >= (R.at ?? 0);
    const runAt = R.travelAt ?? R.at ?? 0;
    const running = s >= runAt;
    const turns = 3;
    const t = running ? (s > runAt ? 1 : clamp(p, 0, 1)) : 0;
    // the ring itself: one drawn circle, twice over
    [[EDGE, 1, 0], [FADE, 0.9, 1]].forEach(([c, w, k]) => {
      kids.push(E('path', { key: 'ring' + k, d: sketchCircle(cx, cy, rad + k * 1.4, 6 + k), fill: 'none', stroke: c,
        strokeWidth: w, strokeLinecap: 'round', pathLength: 1, strokeDasharray: 1,
        style: { strokeDashoffset: live ? 0 : 1, transition: `stroke-dashoffset ${T_LINE + 200}ms ${ENTER} ${k * 120}ms` } }));
    });
    steps.forEach((name, i) => {
      const a = -90 + i * (360 / n);
      const q = pol(cx, cy, rad, a);
      const lq = pol(cx, cy, rad + 34, a);
      const gq = pol(cx, cy, rad - 46, a);
      const named = s >= (R.at ?? 0) + (R.nameOver ? Math.floor(i * R.nameOver / n) : 0);
      const reached = running ? (t * turns * n >= i) : named;
      const g = [
        E('path', { key: 'd', d: blob(q[0], q[1], 8, K), fill: reached ? INK : FADE,
          style: { transition: `fill ${T_DIM}ms ${LEAVE}` } }),
        E('text', { key: 'n', x: lq[0], y: lq[1] + 4,
          textAnchor: Math.abs(lq[0] - cx) < 12 ? 'middle' : (lq[0] > cx ? 'start' : 'end'),
          fontSize: 12.5, fontStyle: 'italic', fill: reached ? INK : EDGE }, name)];
      // each station may carry the figure of the chapter it stands for,
      // drawn inside the ring so the labels keep the outside
      if (R.glyphs && R.glyphs[i]) g.push(...glyphEls(R.glyphs[i], null, 0, gq[0], gq[1], 30, 0, reached ? INK : EDGE, 70 + i, 'rg' + i, { vis: named, width: 1 }));
      kids.push(E('g', { key: 'sp' + i,
        style: { opacity: live ? 1 : 0, transition: `opacity ${T_MARK}ms ${ENTER} ${i * 25}ms` } }, g));
    });
    // the traveller: three turns, then it stops on the first position and
    // does not start again
    if (running) {
      const prog = Math.min(t, 1) * turns;
      const done = prog >= turns;
      const a = -90 + (done ? 0 : prog * 360);
      const q = pol(cx, cy, rad, a);
      if (done && R.travellerWash) kids.push(washPatch(q[0], q[1], 22, WASH[R.travellerWash], 77, 1, 'tw'));
      if (R.traveller) kids.push(E('g', { key: 'trav' }, glyphEls(R.traveller, null, 0, q[0], q[1], 34, 0, INK, 88, 'tv', { double: true })));
      else kids.push(E('path', { key: 'trav', d: blob(q[0], q[1], 13, K), fill: INK }));
      kids.push(E('text', { key: 'tn', x: cx, y: cy + 6, textAnchor: 'middle',
        fontSize: 13, fontStyle: 'italic', fill: INK },
        done ? 'and record again' : 'turn ' + (Math.floor(prog) + 1) + ' of ' + turns));
    } else if (live) {
      kids.push(E('text', { key: 'tn0', x: cx, y: cy + 6, textAnchor: 'middle',
        fontSize: 13, fontStyle: 'italic', fill: EDGE }, 'eight positions'));
    }
    return E('g', { key: 'ring' }, kids);
  }

  /* Default stage: dispatch on whichever register this chapter declares. A
     chapter may still define its own stage() and override this. */
  stage(s, p) {
    if (this.HUB) return this.drawHub(s, p);
    if (this.CONVEYOR) return this.drawConveyor(s, p);
    if (this.PAIR) return this.drawPair(s, p);
    if (this.RING) return this.drawRing(s, p);
    return null;
  }

  /* the plane the picture sits on: a drawn ellipse in the fade ink, at the
     bottom of the frame. Declared per chapter as GROUND = [cx, cy, rx, ry]. */
  renderGround() {
    const E = React.createElement, G = this.GROUND;
    if (!G) return null;
    const pts = Array.from({ length: 18 }, (_, i) => { const q = pol(G[0], G[1], G[2], -100 + i * 22.5); return [q[0], G[1] + (q[1] - G[1]) * (G[3] / G[2])]; });
    return E('path', { key: 'ground', d: toPath(wob(pts, 2, 0.8), false), fill: 'none', stroke: FADE, strokeWidth: 1,
      strokeLinecap: 'round', pathLength: 1, strokeDasharray: 1,
      style: { strokeDashoffset: 0, transition: `stroke-dashoffset ${T_LINE + 300}ms ${ENTER}` } });
  }

  renderCanvas() {
    const E = React.createElement;
    const { p } = this.state;
    // a few px of depth: the far plane and the near figures drift apart
    // with scroll progress; under reduced motion p is pinned at 1
    const far = this.reduced ? 0 : -3 * p, near = this.reduced ? 0 : 2 * p;
    const layers = [
      E('g', { key: 'far', style: { transform: `translateY(${far.toFixed(1)}px)` } }, this.renderGround()),
      E('g', { key: 'lines' }, this.renderLines()),
      E('g', { key: 'marks' }, this.renderMarks()),
    ];
    if (this.stage) { const extra = this.stage(this.state.step, this.state.p); if (extra) layers.push(extra); }
    layers.push(E('g', { key: 'figs', style: { transform: `translateY(${near.toFixed(1)}px)` } }, this.renderFigures()));
    return E('svg', {
      viewBox: this.VIEWBOX || '0 0 640 640', width: '100%', height: '100%',
      style: {
        maxWidth: 'min(100%, calc(100vh - 128px))', maxHeight: '100%',
        display: 'block', overflow: 'visible',
        fontFamily: 'Pretendard, ui-sans-serif, system-ui, sans-serif' } },
      layers);
  }

  renderVals() {
    const side = this.props.textSide ?? this.SIDE ?? 'left';
    return {
      canvas: this.renderCanvas(), side,
      textOrder: side === 'left' ? 0 : 1,
      canvasOrder: side === 'left' ? 1 : 0,
    };
  }
}
