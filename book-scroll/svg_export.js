/* Render each generated chapter artboard's canvas to a standalone SVG, using
   the SAME engine the web page runs. Figma imports these as vectors, so the
   Figma boards and the web chapters cannot drift: one engine, two outputs.

   Emits svg/<Artboard>.svg plus text/<Artboard>.json (the beat's copy), and
   a manifest the Figma scripts read. */
const fs = require('fs'), path = require('path');

global.React = { createElement: (t, p, ...k) => ({ t, p: p || {}, k: k.flat().filter(Boolean) }) };
global.window = { matchMedia: () => ({ matches: false }), addEventListener() {}, innerWidth: 1440, innerHeight: 900 };
global.document = { addEventListener() {}, querySelectorAll: () => [] };
global.DCLogic = class { constructor() { this.props = {}; this.state = {}; } setState(s) { Object.assign(this.state, s); } };

const CAMEL = {
  strokeWidth: 'stroke-width', strokeLinecap: 'stroke-linecap',
  strokeLinejoin: 'stroke-linejoin', strokeDasharray: 'stroke-dasharray',
  strokeDashoffset: 'stroke-dashoffset', fontSize: 'font-size',
  fontStyle: 'font-style', fontFamily: 'font-family', textAnchor: 'text-anchor',
  pathLength: 'pathLength', viewBox: 'viewBox',
};

function attrs(p) {
  const out = [];
  for (const [k, v] of Object.entries(p)) {
    if (k === 'key' || k === 'style' || v == null || v === false) continue;
    if (k.startsWith('data-')) continue;
    // Inline style: keep only what a static export needs. Transitions are
    // motion, not geometry, and a rest-state export must not carry them.
    if (k === 'children') continue;
    // stroke-dashoffset machinery is how the web page DRAWS a line; a static
    // export is the rest state. Leaving pathLength+dasharray in makes Figma
    // render a 1-unit dash, i.e. exactly the broken line this build removed.
    if (k === 'pathLength' || k === 'strokeDasharray' || k === 'strokeDashoffset') continue;
    // hoisted to the root svg and inherited, so it is not repeated per path
    if (k === 'strokeLinecap' && v === 'round') continue;
    if (k === 'strokeLinejoin' && v === 'round') continue;
    if (k === 'strokeWidth' && v === 1) continue;
    if (k === 'fill' && v === 'none') continue;
    if (k === 'fontFamily') continue;
    const name = CAMEL[k] || k;
    out.push(`${name}="${String(v).replace(/"/g, '&quot;')}"`);
  }
  if (p.style) {
    const st = p.style;
    // the rest state: a line that finished drawing has offset 0; anything
    // the engine hid at this beat is dropped entirely by the caller
    if (st.opacity != null && st.opacity !== 1) out.push(`opacity="${st.opacity}"`);
    if (st.transform && st.transform !== 'scale(1)') out.push(`transform="${st.transform}"`);
  }
  return out.length ? ' ' + out.join(' ') : '';
}

function ser(node) {
  if (node == null || node === false) return '';
  if (typeof node === 'string' || typeof node === 'number') return String(node)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const { t, p, k } = node;
  // drop anything the engine has hidden at this beat, so the export is the
  // picture a reader actually sees rather than every beat at once
  if (p.style && p.style.opacity === 0) return '';
  const inner = (k || []).map(ser).join('');
  const VOID = new Set(['path', 'circle', 'line', 'polygon', 'rect', 'ellipse']);
  if (VOID.has(t) && !inner) return `<${t}${attrs(p)}/>`;
  return `<${t}${attrs(p)}>${inner}</${t}>`;
}

function load(file) {
  const src = fs.readFileSync(file, 'utf8');
  const m = src.match(/<script data-dc-script[^>]*>\r?\n([\s\S]*?)\r?\n<\/script>/);
  if (!m) throw new Error('no component script in ' + file);
  return eval('(function(){' + m[1] + '\nreturn Component;})()');
}

function beatText(file, step) {
  // pull the copy for this beat straight out of the artboard markup, so the
  // Figma board and the page carry the same sentences
  const src = fs.readFileSync(file, 'utf8');
  const re = new RegExp(`<section data-step="${step}"[\\s\\S]*?</section>`);
  const sec = (src.match(re) || [''])[0];
  const grab = tag => {
    const out = [];
    const r = new RegExp(`<${tag}[^>]*>([\\s\\S]*?)</${tag}>`, 'g');
    let x;
    while ((x = r.exec(sec))) out.push(x[1].replace(/<[^>]+>/g, '').replace(/&amp;/g, '&')
      .replace(/&lt;/g, '<').replace(/&gt;/g, '>').trim());
    return out;
  };
  const ps = grab('p');
  const hs = grab('h2').concat(grab('h1'));
  const sizes = [...sec.matchAll(/font-size:\s*(\d+)px[^>]*>([\s\S]*?)<\/p>/g)]
    .map(m2 => ({ size: +m2[1], text: m2[2].replace(/<[^>]+>/g, '').trim() }));
  const eyebrow = sizes.find(s => s.size === 13);
  const claim = sizes.find(s => s.size === 21);
  const body = sizes.filter(s => s.size === 17).map(s => s.text);
  return {
    eyebrow: eyebrow ? eyebrow.text : '',
    h: hs[0] || '',
    paras: body.length ? body : ps,
    claim: claim ? claim.text : '',
  };
}

const HERE = __dirname;
fs.mkdirSync(path.join(HERE, 'svg'), { recursive: true });
const manifest = [];

for (const f of fs.readdirSync(path.join(HERE, 'out')).filter(n => n.endsWith('.dc.html')).sort()) {
  const file = path.join(HERE, 'out', f);
  const C = load(file);
  const c = new C();
  // the representative beat: the last content beat, where the drawing for
  // this chapter is at its most complete
  const step = Math.max(0, c.LAST - 1);
  c.state = { step, p: 1 };
  const svgEl = c.renderCanvas();
  const body = (svgEl.k || []).map(ser).join('');
  const vb = svgEl.p.viewBox || '0 0 640 640';
  // round coordinates: the engine emits full float precision, which triples
  // the file for digits no one can see at 640px
  const tidy = body.replace(/-?\d+\.\d{3,}/g, m => (+m).toFixed(2));
  // Figma's importer makes a degenerate node from a zero-size rect and an
  // empty group from an empty <g>; strip both so the imported tree is clean
  let clean = tidy.replace(/<rect[^>]*width="0"[^>]*\/>/g, '');
  for (let i = 0; i < 4; i++) clean = clean.replace(/<g[^>]*><\/g>/g, '');
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${vb}" width="640" height="640" `
    + `font-family="Inter, sans-serif" fill="none" stroke-width="1" stroke-linecap="round" stroke-linejoin="round">${clean}</svg>`;
  const name = f.replace('.dc.html', '');
  fs.writeFileSync(path.join(HERE, 'svg', name + '.svg'), svg);
  manifest.push({
    artboard: f, name, part: c.PART, viewBox: vb, step,
    bytes: svg.length, text: beatText(file, step),
  });
}

fs.writeFileSync(path.join(HERE, 'svg', 'manifest.json'), JSON.stringify(manifest, null, 2));
const bad = manifest.filter(m => m.bytes < 400 || !m.text.h);
console.log(`exported ${manifest.length} svg, total ${(manifest.reduce((a, b) => a + b.bytes, 0) / 1024).toFixed(0)} KB`);
console.log(bad.length ? 'SUSPECT: ' + bad.map(b => b.name + '(' + b.bytes + 'B,h=' + JSON.stringify(b.text.h) + ')').join(' ') : 'all svg non-trivial and titled');
