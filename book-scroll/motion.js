/* Vinc Book motion v3 — one engine, inlined into every chapter by build.py.
   The text comes from the graph; this draws beside it (or behind it).

   Three primitives, each scrubbed by scroll and reversible:
     diagram   the document's own text diagram, parsed and laid out, drawn on
               as the reader moves through the beat
     graph3d   a small constellation of nodes projected from three dimensions,
               the camera turned by scroll, edges explained one at a time
     figure    a drawn form that travels and morphs (a bone thrown, a ship)
   Beat `step` and progress `p` (0..1 inside the beat) come from the scroll
   spy below; nothing plays on a timer; scrolling back reverses everything.
   Colours are the token palette; hierarchy is ink, never hue. */

const INK = '#17171A', EDGE = '#747790', FADE = '#D1D2D6', PAPER = '#F2F2F0';
const FOCUS = '#7E86D3', META = '#82838D', TINT = '#E4E6F6';
const FONT = 'Pretendard, ui-sans-serif, system-ui, sans-serif';
const ENTER = 'cubic-bezier(.22,1,.36,1)';
const RECEDE_TEXT = 0.35;

const lerp = (a, b, t) => a + (b - a) * t;
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const smooth = t => { t = clamp(t, 0, 1); return t * t * (3 - 2 * t); };
const fx = v => Math.round(v * 10) / 10;
const mixHex = (a, b, t) => {
  const ch = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
  const A = ch(a), B = ch(b);
  return '#' + A.map((v, i) => Math.round(lerp(v, B[i], clamp(t, 0, 1))).toString(16).padStart(2, '0')).join('');
};

/* ---------- text diagrams -------------------------------------------------
   The documents draw with a handful of ASCII habits; each becomes a model. */
const T = s => s.trim();
function parseDiagram(text) {
  const raw = text.replace(/\r/g, '').split('\n');
  const groups = []; let cur = [];
  raw.forEach(l => { if (l.trim()) cur.push(l); else if (cur.length) { groups.push(cur); cur = []; } });
  if (cur.length) groups.push(cur);
  const lines = groups.flat();
  if (lines.some(l => /[┌┐]/.test(l))) {                       // a causal cone
    const text = lines.map(T).filter(l => /[A-Za-z]/.test(l));
    const c = lines.find(l => /┌/.test(l)).match(/┌─+([^─]+)─+┐/);
    const kids = lines.map(T).find(l => /^([A-Z]\s+){1,}[A-Z]$/.test(l));
    return { kind: 'cone', top: text[0], center: c ? T(c[1]) : 'A', children: kids ? kids.split(/\s+/) : [],
             bottom: text[text.length - 1] };
  }
  if (lines.some(l => /─+.*>/.test(l))) {                       // A ──rel──> B
    const edges = [];
    lines.forEach(l => {
      const m = l.match(/^(.*?)\s+(<?)─+([^─>]*?)─*>\s+(.*)$/);
      if (m) edges.push({ from: T(m[1]), to: T(m[4]), label: T(m[3]), both: !!m[2] });
    });
    const nodes = [...new Set(edges.flatMap(e => [e.from, e.to]))];
    return { kind: 'edges', nodes, edges };
  }
  if (lines.some(l => /[├└]─/.test(l))) {                       // a tree, maybe with a tail
    const nodes = []; const tail = []; let root = null, seenTree = false;
    lines.forEach(l => {
      const m = l.match(/^(\s*)(?:│\s*)*[├└]─+\s*(.*)$/);
      if (m) { seenTree = true; nodes.push({ label: T(m[2]), depth: 1 + Math.floor(m[1].length / 6) }); return; }
      const t = T(l);
      if (!t || /^[│↓↑]+$/.test(t)) return;
      if (!seenTree && root == null) root = t; else tail.push(t);
    });
    return { kind: 'tree', root: root || '', nodes, tail };
  }
  if (lines.some(l => /^[↓↑]$/.test(T(l)))) {                   // stacks, side by side per blank-line group
    const cols = groups.map(g => g.map(T).filter(l => !/^[↓↑]$/.test(l))).filter(c => c.length);
    return { kind: 'stack', cols };
  }
  if (lines.length === 1 && lines[0].includes('→')) return { kind: 'chain', items: lines[0].split('→').map(T) };
  if (lines.length === 1 && / \+ /.test(lines[0])) return { kind: 'chain', items: lines[0].split(' + ').map(T), plus: true };
  if (lines.filter(l => T(l).startsWith('→')).length >= 2)
    return { kind: 'stack', cols: [[T(lines[0])].concat(lines.slice(1).map(l => T(l).replace(/^→\s*/, '')))] };
  if (lines.some(l => l.includes('→'))) return { kind: 'pairs', rows: lines.map(l => l.split('→').map(T)) };
  if (lines.some(l => /≠/.test(l))) {
    const items = lines.length === 1 ? lines[0].split('≠').map(T) : lines.map(T).filter(l => l !== '≠');
    return { kind: 'contrast', items };
  }
  if (lines.some(l => T(l) === '+')) return { kind: 'sum', items: lines.map(T).filter(l => l !== '+') };
  if (lines.some(l => l.includes('='))) {
    const rows = [], cards = [];
    const split = l => { const i = l.indexOf('='); return [T(l.slice(0, i)), T(l.slice(i + 1))]; };
    groups.forEach(g => {
      const t = g.map(T);
      if (t.every(l => l.includes('='))) t.forEach(l => rows.push(split(l)));          // one definition per line
      else if (t.length > 1 && !t[0].includes('=') && t.slice(1).every(l => l.includes('=')))
        cards.push({ title: t[0], rows: t.slice(1).map(split) });                     // a titled card of fields
      else rows.push([t[0].replace(/\s*=\s*$/, ''), t.slice(1).map(x => x.replace(/^=\s*/, '')).join(' ')]);
    });
    if (cards.length && !rows.length) return { kind: 'cards', cards };
    return { kind: 'pairs', rows };
  }
  if (lines.length <= 2 && lines.join(' ').split(',').length >= 4)
    return { kind: 'chips', items: lines.join(' ').split(',').map(T).filter(Boolean) };
  return { kind: 'stack', cols: [lines.map(T)] };
}

/* ---------- layout: everything lands in a 640 x 640 box ------------------ */
const CH = 7.6, FS = 14, LH = 18, PAD = 12;
function wrap(label, max) {
  max = max || 24;
  const words = label.split(' '), out = []; let line = '';
  words.forEach(w => { if ((line + ' ' + w).trim().length > max && line) { out.push(line); line = w; } else line = (line + ' ' + w).trim(); });
  if (line) out.push(line);
  return out;
}
function box(label, opts) {
  const lines = wrap(label, (opts && opts.max) || 24);
  const w = Math.max(64, Math.max(...lines.map(l => l.length)) * CH + PAD * 2);
  return { label, lines, w, h: lines.length * LH + PAD * 2 };
}
function stackCol(items, x, y0, gap) {
  const out = []; let y = y0;
  items.forEach(label => { const b = box(label); out.push(Object.assign(b, { x: x - b.w / 2, y })); y += b.h + gap; });
  return out;
}
function layoutDiagram(m) {
  const nodes = [], links = [], texts = [];
  const W = 640, H = 640, cx = W / 2, cy = H / 2;
  const arrow = (a, b, o) => links.push(Object.assign({ a, b }, o || {}));
  if (m.kind === 'stack') {
    const n = m.cols.length, colW = Math.min(300, W / n);
    m.cols.forEach((items, ci) => {
      const x = n === 1 ? cx : lerp(colW / 2 + 20, W - colW / 2 - 20, ci / Math.max(1, n - 1));
      const col = stackCol(items, x, 0, 30);
      col.forEach((b, i) => { nodes.push(b); if (i) arrow(col[i - 1], b, { dir: true }); });
      if (items.length === 1) nodes[nodes.length - 1].terminal = true;
      else col[col.length - 1].terminal = true;
    });
  } else if (m.kind === 'chain') {
    const per = m.items.length <= 4 ? m.items.length : Math.ceil(m.items.length / 2);
    let y = 0;
    for (let r = 0; r < m.items.length; r += per) {
      const row = m.items.slice(r, r + per).map(l => box(l, { max: 16 }));
      const tot = row.reduce((s, b) => s + b.w, 0) + (row.length - 1) * 44;
      let x = cx - tot / 2;
      row.forEach((b, i) => { b.x = x; b.y = y; x += b.w + 44; nodes.push(b); if (nodes.length > 1) arrow(nodes[nodes.length - 2], b, { dir: !m.plus, plus: m.plus }); });
      y += 90;
    }
    nodes[nodes.length - 1].terminal = true;
  } else if (m.kind === 'tree') {
    const root = box(m.root); root.x = cx - root.w / 2; root.y = 0; nodes.push(root);
    const l1 = m.nodes.filter(n => n.depth === 1), l2 = m.nodes.filter(n => n.depth >= 2);
    const bs = l1.map(n => box(n.label, { max: 18 }));
    const tot = bs.reduce((s, b) => s + b.w, 0) + (bs.length - 1) * 24;
    let x = cx - tot / 2;
    bs.forEach(b => { b.x = x; b.y = root.h + 56; x += b.w + 24; nodes.push(b); arrow(root, b, { dir: false, tree: true }); });
    if (l2.length && bs.length) {                              // children of the last first-level node
      const parent = bs[bs.length - 1]; let y = parent.y + parent.h + 40;
      l2.forEach(n => { const b = box(n.label, { max: 18 }); b.x = parent.x + parent.w / 2 - b.w / 2; b.y = y; y += b.h + 24; nodes.push(b); arrow(parent, b, { dir: false, tree: true }); });
    }
    if (m.tail.length) {                                       // the tail hangs from the whole row of children
      const bottom = Math.max(...nodes.map(b => b.y + b.h));
      const col = stackCol(m.tail, cx, bottom + 56, 30);
      const row = { x: Math.min(...bs.map(b => b.x)), y: bottom - 1, w: Math.max(...bs.map(b => b.x + b.w)) - Math.min(...bs.map(b => b.x)), h: 1 };
      col.forEach((b, i) => { nodes.push(b); arrow(i ? col[i - 1] : row, b, { dir: true }); });
      col[col.length - 1].terminal = true;
    }
  } else if (m.kind === 'edges') {
    const depth = {}; const ins = {};
    m.edges.forEach(e => { ins[e.to] = (ins[e.to] || 0) + 1; });
    m.nodes.forEach(n => { depth[n] = 0; });
    for (let k = 0; k < 4; k++) m.edges.forEach(e => { depth[e.to] = Math.max(depth[e.to], depth[e.from] + 1); });
    const maxD = Math.max(...Object.values(depth));
    const byD = {}; m.nodes.forEach(n => { (byD[depth[n]] = byD[depth[n]] || []).push(n); });
    const pos = {};
    Object.keys(byD).forEach(d => {
      const arr = byD[d];
      arr.forEach((n, i) => {
        const b = box(n, { max: 14 });
        b.x = (maxD ? lerp(90, W - 90, d / maxD) : cx) - b.w / 2;
        b.y = cy - (arr.length - 1) * 50 + i * 100 - b.h / 2 - 320 + 320 - 0;
        pos[n] = b; nodes.push(b);
      });
    });
    m.edges.forEach(e => arrow(pos[e.from], pos[e.to], { dir: true, both: e.both, label: e.label }));
  } else if (m.kind === 'pairs') {
    let y = 0;
    m.rows.forEach(([a, b]) => {
      const A = box(a, { max: 18 }), B = box(b, { max: 30 });
      A.x = cx - 40 - A.w; A.y = y; B.x = cx + 40; B.y = y; B.plain = true;
      nodes.push(A, B); arrow(A, B, { dir: true });
      y += Math.max(A.h, B.h) + 22;
    });
  } else if (m.kind === 'contrast' || m.kind === 'sum') {
    const col = stackCol(m.items, cx, 0, 54);
    col.forEach((b, i) => { nodes.push(b); if (i) texts.push({ x: cx, y: b.y - 27, t: m.kind === 'contrast' ? '≠' : '+', big: true }); });
    if (m.kind === 'sum') col[col.length - 1].terminal = true;
  } else if (m.kind === 'cone') {
    const c = box(m.center); c.x = cx - c.w / 2; c.y = cy - c.h / 2; c.terminal = true; nodes.push(c);
    texts.push({ x: cx, y: 90, t: m.top }, { x: cx, y: H - 80, t: m.bottom });
    const kids = m.children.map(k => box(k));
    const tot = kids.reduce((s, b) => s + b.w, 0) + (kids.length - 1) * 60;
    let x = cx - tot / 2;
    kids.forEach(b => { b.x = x; b.y = cy + 110; x += b.w + 60; nodes.push(b); arrow(c, b, { dir: true }); });
    links.push({ a: { x: cx - 1, y: 110, w: 2, h: 1 }, b: c, dir: true, up: true });
  } else if (m.kind === 'cards') {                            // a titled box with field rows inside
    const n = m.cards.length, colW = Math.min(300, W / n);
    m.cards.forEach((c, ci) => {
      const x = n === 1 ? cx : lerp(colW / 2 + 20, W - colW / 2 - 20, ci / Math.max(1, n - 1));
      const rowsTxt = c.rows.map(([a, b]) => `${a} = ${b}`);
      const wdt = Math.max(c.title.length, ...rowsTxt.map(r => r.length)) * CH + PAD * 2 + 8;
      const w = Math.min(320, Math.max(120, wdt)), h = PAD * 2 + LH + 8 + c.rows.length * LH;
      const b = { label: c.title, lines: [c.title], w, h, x: x - w / 2, y: 0, card: c.rows.map(([a, v]) => `${a} = ${v}`) };
      nodes.push(b);
    });
    nodes[nodes.length - 1].terminal = true;
  } else if (m.kind === 'chips') {
    let x = 0, y = 0, row = [];
    const rows = [];
    m.items.forEach(l => { const b = box(l, { max: 22 }); if (x + b.w > 560 && row.length) { rows.push(row); row = []; x = 0; } b.x = x; b.y = y; row.push(b); x += b.w + 14; });
    if (row.length) rows.push(row);
    rows.forEach((r, ri) => { const tot = r.reduce((s, b) => s + b.w, 0) + (r.length - 1) * 14; r.forEach(b => { b.x += cx - tot / 2; b.y = ri * 62; nodes.push(b); }); });
  }
  // centre the whole picture; a small one grows to fill, a large one shrinks to fit
  const xs = nodes.map(b => b.x).concat(texts.map(t => t.x - 60)), xe = nodes.map(b => b.x + b.w).concat(texts.map(t => t.x + 60));
  const ys = nodes.map(b => b.y).concat(texts.map(t => t.y - 10)), ye = nodes.map(b => b.y + b.h).concat(texts.map(t => t.y + 10));
  const bw = Math.max(...xe) - Math.min(...xs), bh = Math.max(...ye) - Math.min(...ys);
  const s = Math.min(1.45, 560 / Math.max(1, bw), 560 / Math.max(1, bh));
  const dx = cx - (Math.min(...xs) + bw / 2), dy = cy - (Math.min(...ys) + bh / 2);
  return { nodes, links, texts, transform: `translate(${fx(cx)} ${fx(cy)}) scale(${fx(s)}) translate(${fx(dx - cx)} ${fx(dy - cy)})` };
}

/* a connector between two boxes: leaves the nearer edge, arrives at the
   nearer edge, with barbs at the target when directed */
function connector(a, b) {
  const ac = [a.x + a.w / 2, a.y + a.h / 2], bc = [b.x + b.w / 2, b.y + b.h / 2];
  const dx = bc[0] - ac[0], dy = bc[1] - ac[1];
  const vert = Math.abs(dy) >= Math.abs(dx);
  const p0 = vert ? [ac[0], dy > 0 ? a.y + a.h : a.y] : [dx > 0 ? a.x + a.w : a.x, ac[1]];
  const p1 = vert ? [bc[0], dy > 0 ? b.y : b.y + b.h] : [dx > 0 ? b.x : b.x + b.w, bc[1]];
  return { p0, p1, vert };
}
function arrowPath(p0, p1, dir) {
  const d = `M${fx(p0[0])},${fx(p0[1])} L${fx(p1[0])},${fx(p1[1])}`;
  if (!dir) return d;
  const ang = Math.atan2(p1[1] - p0[1], p1[0] - p0[0]);
  const b = a => [p1[0] - 7 * Math.cos(ang + a), p1[1] - 7 * Math.sin(ang + a)];
  const L = b(0.5), R = b(-0.5);
  return d + ` M${fx(L[0])},${fx(L[1])} L${fx(p1[0])},${fx(p1[1])} L${fx(R[0])},${fx(R[1])}`;
}

function renderDiagram(lay, t, key, rest) {
  const E = React.createElement;
  const N = lay.nodes.length + lay.links.length + lay.texts.length;
  let k = 0;
  const prog = () => { const u = smooth(t * N - k + 0.6); k++; return rest ? 1 : u; };
  const ink = rest ? EDGE : INK, edge = rest ? FADE : EDGE, txt = rest ? META : INK;
  const els = [];
  lay.nodes.forEach((b, i) => {
    const u = prog();
    const g = [];
    if (!b.plain) g.push(E('rect', { key: 'r', x: b.x, y: b.y, width: b.w, height: b.h, rx: 8,
      fill: b.terminal && !rest ? TINT : PAPER, stroke: b.terminal && !rest ? FOCUS : ink, strokeWidth: 1.25 }));
    b.lines.forEach((l, li) => g.push(E('text', { key: 't' + li, x: b.x + b.w / 2, y: b.y + PAD + FS - 2 + li * LH, textAnchor: 'middle',
      fontSize: FS, fontFamily: FONT, fill: b.plain ? (rest ? META : EDGE) : txt, fontWeight: b.card ? 600 : 400 }, l)));
    if (b.card) {
      g.push(E('path', { key: 'rule', d: `M${fx(b.x + 10)},${fx(b.y + PAD + LH + 2)} L${fx(b.x + b.w - 10)},${fx(b.y + PAD + LH + 2)}`, stroke: edge, strokeWidth: 1 }));
      b.card.forEach((row, ri) => g.push(E('text', { key: 'c' + ri, x: b.x + 14, y: b.y + PAD + LH + 8 + FS - 2 + ri * LH, fontSize: 12.5, fontFamily: FONT, fill: rest ? META : EDGE }, row)));
    }
    els.push(E('g', { key: key + 'n' + i, style: { opacity: u, transform: `translateY(${fx((1 - u) * 10)}px)` } }, g));
  });
  lay.links.forEach((l, i) => {
    const u = prog();
    const c = l.up ? { p0: [l.a.x + 1, l.a.y], p1: [l.b.x + l.b.w / 2, l.b.y] } : connector(l.a, l.b);
    const d = arrowPath(c.p0, c.p1, l.dir);
    const mid = [(c.p0[0] + c.p1[0]) / 2, (c.p0[1] + c.p1[1]) / 2];
    const g = [E('path', { key: 'p', d, fill: 'none', stroke: edge, strokeWidth: 1.25, strokeLinecap: 'round', strokeLinejoin: 'round',
      pathLength: 1, strokeDasharray: 1, strokeDashoffset: fx(1 - u) })];
    if (l.plus) g.push(E('text', { key: 'plus', x: mid[0], y: mid[1] + 5, textAnchor: 'middle', fontSize: 16, fontFamily: FONT, fill: edge, opacity: u }, '+'));
    if (l.label) g.push(E('text', { key: 'l', x: mid[0], y: mid[1] - 6, textAnchor: 'middle', fontSize: 12, fontFamily: FONT, fill: META, opacity: u }, l.label));
    els.push(E('g', { key: key + 'l' + i }, g));
  });
  lay.texts.forEach((x, i) => {
    const u = prog();
    els.push(E('text', { key: key + 't' + i, x: x.x, y: x.y + (x.big ? 8 : 4), textAnchor: 'middle', fontSize: x.big ? 22 : 13, fontFamily: FONT,
      fill: x.big ? ink : META, opacity: u }, x.t));
  });
  return E('g', { key, transform: lay.transform }, els);
}

/* ---------- graph3d --------------------------------------------------------
   Nodes at unit-cube positions, a camera turned by yaw and pitch, a mild
   perspective. Depth is ink: near is INK, far fades toward FADE. */
const R3 = 225, F3 = 3.2, NODE_R = 19;
function project(p, yaw, pitch) {
  const cy = Math.cos(yaw), sy = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch);
  const x1 = p[0] * cy + p[2] * sy, z1 = -p[0] * sy + p[2] * cy;
  const y2 = p[1] * cp - z1 * sp, z2 = p[1] * sp + z1 * cp;
  const s = F3 / (F3 + z2);
  return { x: 320 + x1 * R3 * s, y: 320 + y2 * R3 * s, s, z: z2 };
}
function nodeShape(kind, x, y, r) {
  const E = React.createElement;
  if (kind === 'record' || kind === 'episode') {
    const pts = Array.from({ length: 6 }, (_, i) => { const a = Math.PI / 3 * i - Math.PI / 6; return `${fx(x + r * Math.cos(a))},${fx(y + r * Math.sin(a))}`; });
    return E('polygon', { points: pts.join(' ') });
  }
  if (kind === 'bridge') return E('polygon', { points: `${fx(x)},${fx(y - r)} ${fx(x + r)},${fx(y)} ${fx(x)},${fx(y + r)} ${fx(x - r)},${fx(y)}` });
  if (kind === 'decision') return E('rect', { x: x - r * 0.9, y: y - r * 0.9, width: r * 1.8, height: r * 1.8, rx: r * 0.25 });
  if (kind === 'domain') return E('g', {}, E('circle', { cx: x, cy: y, r }), E('circle', { cx: x, cy: y, r: r * 1.5, fill: 'none' }));
  return E('circle', { cx: x, cy: y, r });
}

/* ---------- figures --------------------------------------------------------
   Glyphs are strokes in a unit box (-1..1). A morph resamples both glyphs to
   the same stroke and point counts and lerps them, so any two can blend. */
const GLYPH = {
  monolith: [[[-0.35, -1], [0.35, -1], [0.35, 1], [-0.35, 1], [-0.35, -1]]],
  bone: [[[-1, -0.32], [-0.86, -0.42], [-0.7, -0.3], [-0.62, -0.16], [0.62, -0.16], [0.7, -0.3], [0.86, -0.42], [1, -0.32], [1.02, -0.08], [0.9, 0.02],
          [1, 0.14], [0.9, 0.4], [0.7, 0.34], [0.62, 0.18], [-0.62, 0.18], [-0.7, 0.34], [-0.9, 0.4], [-1, 0.14], [-0.9, 0.02], [-1.02, -0.08], [-1, -0.32]]],
  ship: [[[-1, 0.02], [-0.82, -0.14], [-0.3, -0.2], [0.55, -0.16], [0.9, -0.08], [1, 0.02], [0.9, 0.12], [0.55, 0.18], [0.1, 0.2],
          [-0.02, 0.5], [-0.34, 0.5], [-0.3, 0.2], [-0.82, 0.18], [-1, 0.02]],
         [[-0.72, -0.14], [-0.6, -0.5], [-0.38, -0.5], [-0.3, -0.2]]],
  fire: [[[0, -1], [0.35, -0.5], [0.3, -0.15], [0.6, 0.1], [0.5, 0.7], [0, 1], [-0.5, 0.7], [-0.6, 0.1], [-0.3, -0.15], [-0.35, -0.5], [0, -1]]],
  door: [[[-0.5, 1], [-0.5, -1], [0.5, -1], [0.5, 1]], [[0.28, 0.05], [0.36, 0.05]]],
  dot: [[[0, -1], [0.7, -0.7], [1, 0], [0.7, 0.7], [0, 1], [-0.7, 0.7], [-1, 0], [-0.7, -0.7], [0, -1]]],
};
function resample(stroke, n) {
  const seg = []; let total = 0;
  for (let i = 1; i < stroke.length; i++) { const d = Math.hypot(stroke[i][0] - stroke[i - 1][0], stroke[i][1] - stroke[i - 1][1]); seg.push(d); total += d; }
  const out = []; let acc = 0, j = 0;
  for (let k = 0; k < n; k++) {
    const target = total * k / (n - 1);
    while (j < seg.length - 1 && acc + seg[j] < target) { acc += seg[j]; j++; }
    const t = seg[j] ? clamp((target - acc) / seg[j], 0, 1) : 0;
    out.push([lerp(stroke[j][0], stroke[j + 1][0], t), lerp(stroke[j][1], stroke[j + 1][1], t)]);
  }
  return out;
}
function glyphStrokes(name, n) {
  const g = GLYPH[name] || GLYPH.dot;
  return g.map(s => resample(s, n));
}
function morphStrokes(a, b, t) {
  const n = Math.max(a.length, b.length);
  const A = Array.from({ length: n }, (_, i) => a[Math.min(i, a.length - 1)]), B = Array.from({ length: n }, (_, i) => b[Math.min(i, b.length - 1)]);
  return A.map((s, i) => s.map((p, k) => [lerp(p[0], B[i][k][0], t), lerp(p[1], B[i][k][1], t)]));
}
function strokePath(s, x, y, size, rot) {
  const c = Math.cos(rot * Math.PI / 180), sn = Math.sin(rot * Math.PI / 180);
  return s.map((p, i) => { const X = x + (p[0] * c - p[1] * sn) * size, Y = y + (p[0] * sn + p[1] * c) * size; return (i ? 'L' : 'M') + fx(X) + ',' + fx(Y); }).join(' ');
}

class BookChapter extends DCLogic {
  state = { step: 0, p: 0 };

  componentDidMount() {
    this.reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.onScroll = () => { if (this.raf) return; this.raf = requestAnimationFrame(() => { this.raf = 0; this.spy(); }); };
    document.addEventListener('scroll', this.onScroll, true);
    window.addEventListener('resize', this.onScroll);
    this.spy();
    setTimeout(() => this.spy(), 300);
    setTimeout(() => this.spy(), 1200);
    this.onKey = e => {
      if (e.metaKey || e.ctrlKey || e.altKey || /INPUT|TEXTAREA|BUTTON/.test(e.target.tagName)) return;
      const dir = (e.key === 'ArrowDown' || e.key === 'j') ? 1 : (e.key === 'ArrowUp' || e.key === 'k') ? -1 : 0;
      if (!dir) return;
      const els = Array.from(document.querySelectorAll('[data-step]'));
      const next = els[clamp(this.state.step + dir, 0, els.length - 1)];
      if (!next) return;
      e.preventDefault();
      const stacked = window.innerWidth <= 900;
      const line = window.innerHeight * (this.props.triggerLine ?? 0.5);
      const top = next.getBoundingClientRect().top;
      const target = stacked ? top - window.innerHeight * 0.46 - 16 : top - line + Math.min(48, next.getBoundingClientRect().height * 0.1);
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
    const line = stacked ? window.innerHeight * 0.46 + 80 : window.innerHeight * (this.props.triggerLine ?? 0.5);
    const tops = els.map(el => el.getBoundingClientRect().top);
    let a = 0;
    tops.forEach((t, i) => { if (t <= line) a = i; });
    els.forEach((el, i) => { el.style.opacity = i === a ? '1' : String(RECEDE_TEXT); });
    const span = a + 1 < tops.length ? tops[a + 1] - tops[a] : els[a].getBoundingClientRect().height * 0.5;
    const p = this.reduced ? 1 : clamp((line - tops[a]) / Math.max(1, span), 0, 1);
    const step = Math.min(a, this.LAST);
    if (step !== this.state.step || Math.abs(p - this.state.p) > 0.002) this.setState({ step, p });
  }
  /* 0 before beat `at`, scrubbed by p over [from, to] on it, 1 after it */
  scrub(at, from, to) {
    const { step: s, p } = this.state;
    if (s > at) return 1;
    if (s < at) return 0;
    from = from == null ? 0 : from; to = to == null ? 1 : to;
    return smooth(clamp((p - from) / Math.max(0.05, to - from), 0, 1));
  }

  /* ---- what a beat shows ---- */
  motionFor(i) {
    const M = this.MOTION || {};
    const b = this.BEATS[i] || {};
    const authored = (M.beats || {})[String(i)] || {};
    const diagram = authored.diagram != null ? authored.diagram : (b.diagrams && b.diagrams.length ? b.diagrams[0] : null);
    return Object.assign({ diagram }, authored);
  }
  layoutOf(i) {
    this._lay = this._lay || {};
    if (this._lay[i] !== undefined) return this._lay[i];
    const m = this.motionFor(i);
    this._lay[i] = m.diagram ? layoutDiagram(parseDiagram(m.diagram)) : null;
    return this._lay[i];
  }
  lastDiagramBeat(i) { for (let k = i; k >= 0; k--) if (this.layoutOf(k)) return k; return -1; }

  renderDiagrams() {
    const E = React.createElement;
    const { step: s, p } = this.state;
    const here = this.layoutOf(s) ? s : -1;
    const prev = this.lastDiagramBeat(here >= 0 ? s - 1 : s);
    const t = this.reduced ? 1 : smooth(p / 0.45);
    const out = [];
    if (prev >= 0 && (here < 0 || t < 1)) {                    // the last picture recedes as the new one draws
      const fade = here >= 0 ? 1 - smooth(p / 0.35) : 1;
      out.push(E('g', { key: 'prev' + prev, style: { opacity: here >= 0 ? fade : 1, transform: `scale(${fx(1 - 0.04 * (1 - fade))})`, transformOrigin: '320px 320px' } },
        renderDiagram(this.layoutOf(prev), 1, 'd' + prev, here >= 0 || true)));
    }
    if (here >= 0) out.push(renderDiagram(this.layoutOf(here), t, 'd' + here, false));
    return out;
  }

  renderGraph3d() {
    const E = React.createElement, G = (this.MOTION || {}).graph3d;
    if (!G) return null;
    const { step: s, p } = this.state;
    const beats = G.beats || {};
    const keys = Object.keys(beats).map(Number).sort((a, b) => a - b);
    const upto = keys.filter(k => k <= s);
    const shown = new Set(), edgesOn = new Set(); let focus = null, rot = G.rot || [0.5, -0.35], prevRot = rot, evidence = null;
    upto.forEach(k => {
      const b = beats[k];
      (b.show || []).forEach(id => shown.add(id)); (b.hide || []).forEach(id => shown.delete(id));
      (b.edges || []).forEach(e => edgesOn.add(e)); (b.edgesOff || []).forEach(e => edgesOn.delete(e));
      if (b.focus !== undefined) focus = b.focus;
      if (b.rot) { prevRot = rot; rot = b.rot; }
      evidence = b.evidence || null;
    });
    const cur = beats[s] || {};
    const w = cur.rot ? smooth(p / 0.5) : 1;
    const yaw = lerp(prevRot[0], rot[0], w) + (this.reduced ? 0 : 0.08 * p), pitch = lerp(prevRot[1], rot[1], w);
    const P = {}; G.nodes.forEach(n => { P[n.id] = project(n.p, yaw, pitch); });
    const appear = id => { const at = keys.find(k => (beats[k].show || []).includes(id)); return at == null ? 1 : this.scrub(at, 0, 0.4); };
    const order = G.nodes.filter(n => shown.has(n.id)).sort((a, b) => P[b.id].z - P[a.id].z);
    const els = [];
    (G.edges || []).forEach((e, i) => {
      if (!edgesOn.has(i)) return;
      const a = P[e.from], b = P[e.to];
      if (!a || !b) return;
      const at = keys.find(k => (beats[k].edges || []).includes(i));
      const u = at == null ? 1 : this.scrub(at, 0.1, 0.6);
      const ra = (NODE_R + 1) * a.s, rb = (NODE_R + 1) * b.s, ang = Math.atan2(b.y - a.y, b.x - a.x);
      const p0 = [a.x + ra * Math.cos(ang), a.y + ra * Math.sin(ang)], p1 = [b.x - (rb + 3) * Math.cos(ang), b.y - (rb + 3) * Math.sin(ang)];
      const depth = clamp(((a.z + b.z) / 2 + 1) / 2, 0, 1);
      const col = mixHex(EDGE, FADE, depth);
      const g = [E('path', { key: 'p', d: arrowPath(p0, p1, e.dir !== false), fill: 'none', stroke: col, strokeWidth: 1.4, strokeLinecap: 'round', strokeLinejoin: 'round',
        pathLength: 1, strokeDasharray: 1, strokeDashoffset: fx(1 - u) })];
      if (e.label && edgesOn.size <= 3) {                     // labels only while there are few enough to read
        const mx = (p0[0] + p1[0]) / 2, my = (p0[1] + p1[1]) / 2;
        const tw = e.label.length * 6.6 + 10;
        g.push(E('rect', { key: 'lb', x: mx - tw / 2, y: my - 17, width: tw, height: 16, rx: 4, fill: PAPER, opacity: u }));
        g.push(E('text', { key: 'l', x: mx, y: my - 5, textAnchor: 'middle', fontSize: 11.5, fontFamily: FONT, fill: META, opacity: u }, e.label));
      }
      if (evidence && evidence.edge === i) {
        const mx = (p0[0] + p1[0]) / 2, my = (p0[1] + p1[1]) / 2, v = this.scrub(s, 0.3, 0.8);
        const tw = (evidence.text || 'evidence').length * 6.6 + 14;
        g.push(E('path', { key: 'ev', d: `M${fx(mx)},${fx(my)} L${fx(mx)},${fx(my + 34)}`, stroke: FOCUS, strokeWidth: 1.2, pathLength: 1, strokeDasharray: 1, strokeDashoffset: fx(1 - v) }));
        g.push(E('rect', { key: 'evr', x: mx - tw / 2, y: my + 34, width: tw, height: 22, rx: 5, fill: TINT, stroke: FOCUS, strokeWidth: 1, opacity: v }));
        g.push(E('text', { key: 'evt', x: mx, y: my + 49, textAnchor: 'middle', fontSize: 12, fontFamily: FONT, fill: INK, opacity: v }, evidence.text || 'evidence'));
      }
      els.push(E('g', { key: 'e' + i }, g));
    });
    order.forEach(n => {
      const q = P[n.id], u = appear(n.id);
      const depth = clamp((q.z + 1) / 2, 0, 1), r = NODE_R * q.s * lerp(0.6, 1, u);
      const col = mixHex(INK, FADE, depth * 0.85);
      const isF = focus === n.id;
      els.push(E('g', { key: 'n' + n.id, style: { opacity: u } },
        E('g', { fill: isF ? TINT : PAPER, stroke: isF ? FOCUS : col, strokeWidth: isF ? 1.8 : 1.4 }, nodeShape(n.kind, q.x, q.y, r)),
        n.label ? E('text', { x: q.x, y: q.y + r + 15, textAnchor: 'middle', fontSize: 12.5 * lerp(0.9, 1, q.s), fontFamily: FONT, fill: mixHex(INK, META, depth) }, n.label) : null));
    });
    return E('g', { key: 'g3' }, els);
  }

  renderFigures() {
    const E = React.createElement, F = (this.MOTION || {}).figures;
    if (!F) return null;
    const { step: s, p } = this.state;
    const els = [];
    const N = 40;
    // a figure named again in a later beat replaces its earlier self; and when a
    // beat hands the canvas to a diagram, the figures step back
    const latest = {};
    Object.keys(F).map(Number).filter(k => k <= s).forEach(k => (F[k] || []).forEach(f => { if (f.id) latest[f.id] = k; }));
    const recede = this.layoutOf(s) && !F[s] ? 0.12 : 1;
    Object.keys(F).map(Number).sort((a, b) => a - b).forEach(k => {
      (F[k] || []).forEach((f, i) => {
        if (k > s) return;
        if (f.id && latest[f.id] !== k) return;
        const u = k < s ? 1 : this.scrub(k, f.span ? f.span[0] : 0, f.span ? f.span[1] : 1);
        if (k < s && f.hideAfter) return;
        const from = f.from || {}, to = f.to || from;
        const x = lerp(from.x ?? 320, to.x ?? from.x ?? 320, u), y0 = lerp(from.y ?? 320, to.y ?? from.y ?? 320, u);
        const y = y0 - (f.arc || 0) * 4 * u * (1 - u);
        const size = lerp(from.s ?? 40, to.s ?? from.s ?? 40, u), rot = lerp(from.rot ?? 0, to.rot ?? from.rot ?? 0, u);
        const op = lerp(from.o ?? 1, to.o ?? from.o ?? 1, u) * recede;
        let strokes = glyphStrokes(f.glyph, N);
        if (f.morph) { const m = smooth((u - f.morph.at) / (f.morph.over || 0.25)); if (m > 0) strokes = morphStrokes(strokes, glyphStrokes(f.morph.glyph, N), m); }
        const filled = f.fill;
        els.push(E('g', { key: 'f' + k + '_' + i, style: { opacity: op } },
          strokes.map((st, j) => E('path', { key: j, d: strokePath(st, x, y, size, rot) + (filled ? ' Z' : ''), fill: filled ? INK : PAPER, stroke: INK, strokeWidth: 1.6, strokeLinejoin: 'round', strokeLinecap: 'round' }))));
        if (f.caption) els.push(E('text', { key: 'c' + k + '_' + i, x, y: y + size + 22, textAnchor: 'middle', fontSize: 12, fontFamily: FONT, fill: META, opacity: op }, f.caption));
      });
    });
    return E('g', { key: 'figs' }, els);
  }

  renderCanvas() {
    const E = React.createElement;
    const layers = [this.renderGraph3d(), this.renderDiagrams(), this.renderFigures()].filter(Boolean);
    return E('svg', { viewBox: this.VIEWBOX || '0 0 640 640', width: '100%', height: '100%',
      style: { maxWidth: 'min(100%, calc(100vh - 128px))', maxHeight: '100%', display: 'block', overflow: 'visible', fontFamily: FONT } }, layers);
  }
  renderVals() {
    const side = this.props.textSide ?? this.SIDE ?? 'left';
    return { canvas: this.renderCanvas(), side, textOrder: side === 'left' ? 0 : 1, canvasOrder: side === 'left' ? 1 : 0 };
  }
}
