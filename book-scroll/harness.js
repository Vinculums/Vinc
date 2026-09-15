// Beat-sheet harness: renders every beat of a chapter's canvas with the real
// engine under real React, into one static page per chapter, so the drawing
// can be looked at without the canvas editor. Not shipped; a dev aid.
//   node harness.js <outdir> [Main Ch10 ...]
const fs = require('fs'), path = require('path');
const OUT = process.argv[2];
const only = process.argv.slice(3);
const files = fs.readdirSync('out').filter(n => n.endsWith('.dc.html') && (!only.length || only.includes(n.replace('.dc.html', '')))).sort();
const index = [];
for (const f of files) {
  const src = fs.readFileSync(path.join('out', f), 'utf8');
  const m = src.match(/<script data-dc-script[^>]*>\n([\s\S]*?)\n<\/script>/);
  const name = f.replace('.dc.html', '');
  const html = `<!doctype html><html><head><meta charset="utf-8"><title>${name} beats</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/react/18.3.1/umd/react.production.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/react-dom/18.3.1/umd/react-dom.production.min.js"></script>
<style>body{margin:0;background:#F2F2F0;font-family:Pretendard,ui-sans-serif,system-ui,sans-serif;color:#17171A}
.grid{display:grid;grid-template-columns:repeat(4,300px);gap:10px;padding:10px}
.cell{background:#F2F2F0;border:1px solid #DEDEDA;position:relative}
.cell svg{width:300px;height:300px;display:block}
.cell .cap{position:absolute;left:8px;top:6px;font-size:12px;color:#82838D}</style></head><body>
<div class="grid" id="grid"></div>
<script>
class DCLogic { constructor(){ this.props = {}; this.state = {}; } setState(s){ Object.assign(this.state, s); } forceUpdate(){} }
${m[1]}
const grid = document.getElementById('grid');
const c = new Component();
const q = new URLSearchParams(location.search);
const only = q.get('only') ? q.get('only').split(',').map(Number) : null;
if (only) { const px = q.get('px') || 600; document.querySelector('.grid').style.gridTemplateColumns = 'repeat(2,' + px + 'px)';
  const st = document.createElement('style'); st.textContent = '.cell svg{width:' + px + 'px;height:' + px + 'px}'; document.head.appendChild(st); }
const beats = [];
const pp = q.get('pp');
for (let s = 0; s <= c.LAST; s++) { if (only && !only.includes(s)) continue; if (pp !== '1') beats.push([s, 0.5]); beats.push([s, 1]); }
for (const [s, p] of beats) {
  const cell = document.createElement('div'); cell.className = 'cell';
  const cap = document.createElement('div'); cap.className = 'cap'; cap.textContent = 'beat ' + s + ' · p=' + p;
  cell.appendChild(cap);
  const mount = document.createElement('div'); cell.appendChild(mount); grid.appendChild(cell);
  c.state = { step: s, p };
  ReactDOM.createRoot(mount).render(c.renderCanvas());
}
</script></body></html>`;
  fs.writeFileSync(path.join(OUT, name + '.html'), html);
  index.push(name);
}
fs.writeFileSync(path.join(OUT, 'index.html'), '<!doctype html><meta charset="utf-8"><body style="font-family:system-ui;padding:16px">' + index.map(n => `<a href="${n}.html">${n}</a>`).join(' · '));
console.log('harness:', index.length, 'pages in', OUT);
