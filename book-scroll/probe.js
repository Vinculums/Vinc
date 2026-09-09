// Headless render probe: parses a generated artboard's component script and
// walks every beat, reporting what the brief actually constrains.
const fs = require('fs'), path = require('path');
global.React = { createElement: (t, p, ...k) => ({ t, p: p || {}, k: k.flat().filter(Boolean) }) };
global.window = { matchMedia: () => ({ matches: false }), addEventListener(){}, innerWidth: 1440, innerHeight: 900 };
global.document = { addEventListener(){}, querySelectorAll: () => [] };
global.DCLogic = class { constructor(){ this.props = {}; this.state = {}; } setState(s){ Object.assign(this.state, s); } };

function load(file) {
  const src = fs.readFileSync(file, 'utf8');
  const m = src.match(/<script data-dc-script[^>]*>\n([\s\S]*?)\n<\/script>/);
  if (!m) throw new Error('no component script in ' + file);
  return eval('(function(){' + m[1] + '\nreturn Component;})()');
}

let fail = 0;
for (const f of fs.readdirSync('out').filter(n => n.endsWith('.dc.html')).sort()) {
  const C = load(path.join('out', f));
  const c = new C();
  const rows = [];
  let prevMarks = -1, shrank = [], dashes = 0, badInk = new Set(), focusBeats = 0, stageMin = 1e9;
  const ALLOWED = new Set(['#17171A', '#747790', '#D1D2D6']);
  for (let s = 0; s <= c.LAST; s++) {
    c.state = { step: s, p: 1 };
    const marks = c.renderMarks(), lines = c.renderLines();
    const vm = marks.filter(g => g.p.style.opacity > 0).length;
    const vl = lines.filter(g => g.p.style.opacity > 0);
    vl.forEach(g => {
      const st = g.k[0].p.stroke;
      if (!ALLOWED.has(st)) badInk.add(st);
      if (String(g.k[0].p.strokeDasharray) !== '1') dashes++;
    });
    // focus tint present on any mark this beat?
    const tinted = marks.some(g => JSON.stringify(g.k).includes('7E86D3') ||
      (g.k[0] && /#(7[eE]86|8|9|a|b)/.test(String(g.k[0].p.fill || g.k[0].p.stroke || ''))));
    if (tinted) focusBeats++;
    if (prevMarks >= 0 && vm < prevMarks) shrank.push(s);
    prevMarks = vm;
    // the part registers draw into stage(); count what they put on the canvas
    const st = c.stage ? c.stage(s, 1) : null;
    const sc = st ? (st.k || []).length : 0;
    stageMin = Math.min(stageMin, sc);
    rows.push(`${s}:${vm}m/${vl.length}l/${sc}s`);
  }
  const probs = [];
  if (c.PART === 0 && shrank.length) probs.push('SHRANK@' + shrank.join(','));
  if (dashes) probs.push('DASH=' + dashes);
  if (badInk.size) probs.push('INK=' + [...badInk].join('|'));
  // Parts 1 to 3 carry their whole canvas in the register, so an empty
  // stage on any beat means the register never drew
  if (c.PART > 0 && stageMin === 0) probs.push('EMPTY-STAGE');
  if (probs.length) fail++;
  console.log(`${f.padEnd(16)} part${c.PART} last=${c.LAST}  ${rows.join(' ')}  ${probs.join(' ') || 'ok'}`);
}
console.log(fail ? `\n${fail} artboard(s) with problems` : '\nall artboards clean');
