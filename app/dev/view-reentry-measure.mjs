// Run after npm run build, with OUTPUT_DIR/backend.json naming a loopback synthetic /graph backend.
// The Python server that wrote it (scripts/measure_view_reentry.py) was deleted in VIN-566 as an orphan;
// recover it with: git show c6988043:scripts/measure_view_reentry.py
// node app/dev/view-reentry-measure.mjs APP_DIR OUTPUT_DIR LABEL
// Playwright must resolve from app/package.json or the package.json given by VINC_TEST_MODULES.
// A LABEL starting with before reconstructs the baseline completion boundary.
// See measurement JSON for the limits of the first/completion proxies.
import { createRequire } from 'node:module';
import { createServer } from 'node:http';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { resolve, extname, sep } from 'node:path';
import { gzipSync } from 'node:zlib';
const app = resolve(process.argv[2]), evidence = resolve(process.argv[3]), label = process.argv[4] || 'before';
const backend = JSON.parse(await readFile(resolve(evidence, 'backend.json'), 'utf8')).url;
const backendUrl = new URL(backend);
if (backendUrl.protocol !== 'http:' || backendUrl.hostname !== '127.0.0.1' || !backendUrl.port || backendUrl.username || backendUrl.password) {
  throw new Error('The synthetic backend must be an HTTP loopback endpoint with an explicit port.');
}
const require = createRequire(process.env.VINC_TEST_MODULES||resolve(app,'package.json'));
const { chromium } = require('playwright');
let size = 200;
const requests = [];
const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.woff2':'font/woff2', '.wasm':'application/wasm', '.svg':'image/svg+xml' };
const server = createServer(async (req, res) => {
  const url = new URL(req.url, 'http://local'), path = url.pathname;
  const started = performance.now();
  let body, backendMeasures, type = 'application/json', cache = 'no-store';
  if (path === '/api/graph/graph') {
    const response = await fetch(`${backend}/graph?size=${size}&${url.searchParams.toString()}`, {headers:{Authorization:'Bearer synthetic-view-loading'}});
    if(!response.ok){res.writeHead(response.status,{'Content-Type':'application/json'});res.end(await response.text());return;}
    const internal = await response.text();
    const start = performance.now();
    body = Buffer.from(JSON.stringify(JSON.parse(internal)));
    backendMeasures = {...JSON.parse(response.headers.get('x-synthetic-measures')), proxyParseSerializeMs:performance.now()-start};
  }
  else if (path === '/api/me') body = Buffer.from('{"user":{"email":"synthetic@example.invalid","name":"Synthetic"}}');
  else if (path === '/api/teams') body = Buffer.from('{"teams":[]}');
  else if (path === '/api/graph/changes') body = Buffer.from('{"marker":"synthetic-static"}');
  else if (path.startsWith('/api/')) body = Buffer.from('{"executed":true,"domains":[],"unfiled":[],"counts":{"kinds":{}},"teams":[]}');
  else if (path === '/assets/gnb.js') { body=Buffer.from(''); type='text/javascript'; }
  else {
    const safe = resolve(app, 'dist', '.' + (path === '/' ? '/index.html' : path));
    if (!safe.startsWith(resolve(app,'dist') + sep) && safe !== resolve(app,'dist','index.html')) { res.writeHead(403).end(); return; }
    try { body = await readFile(safe); type = types[extname(safe)] || 'application/octet-stream'; cache=path==='/index.html'||path==='/'?'no-cache':'public, max-age=3600'; }
    catch { res.writeHead(404).end(); return; }
  }
  const encoded = /gzip/.test(req.headers['accept-encoding']||'') ? gzipSync(body) : body;
  res.writeHead(200, { 'Content-Type':type, 'Cache-Control':cache, 'Content-Length':encoded.length,
    ...(encoded!==body?{'Content-Encoding':'gzip'}:{}) }); res.end(encoded);
  requests.push({path, query:url.search, bytes:body.length, sent:encoded.length, ms:performance.now()-started, backendMeasures});
});
await new Promise(r=>server.listen(0,'127.0.0.2',r));
const origin=`http://127.0.0.2:${server.address().port}`;
const browser=await chromium.launch({channel:'msedge',headless:true,args:['--enable-webgl','--ignore-gpu-blocklist',
  '--disable-background-networking','--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.2']});
const results=[];
try {
  for(size of (process.env.REENTRY_LAYOUT?[200]:[200,3000])) for(const view of ['2d','3d']) {
    const context=await browser.newContext({viewport:{width:1440,height:900}});
    await context.addInitScript(({view,layout})=>{
      Object.defineProperty(document,'cookie',{get:()=> '__Host-csrf=synthetic'});
      localStorage.setItem('vinc.owner','user:synthetic-view-loading');
      localStorage.setItem('vinc.view.coach.seen','1');
      localStorage.setItem('vinc.view.prefs',JSON.stringify({pv:2,view,layout,edges:'none'}));
      localStorage.setItem('vinc.theme','dark');
      window.__reentry={logs:[],workers:0};
      const info=console.info;
      console.info=function(...args){const text=String(args[0]);if(text.startsWith('View '))window.__reentry.logs.push({at:performance.now(),text});info.apply(this,args);};
      const OriginalWorker=window.Worker;
      window.Worker=class extends OriginalWorker {constructor(...args){super(...args);window.__reentry.workers++;}};
      window.__watchView=()=>{
        window.__phase={start:performance.now(),first:0,complete:0,preview:0,frames:0};
        const tick=()=>{
          const x=window.__phase;
          const canvas=document.querySelector(view==='2d'?'.a2-stage canvas':'.scene-container canvas');
          if(canvas&&canvas.getBoundingClientRect().width>0&&getComputedStyle(canvas.closest('#view-canvas')||canvas).opacity!=='0'){
            if(++x.frames>=3&&!x.first)x.first=performance.now();
          }else x.frames=0;
          if(view==='2d'&&x.first)x.complete=x.first;
          const completion=window.__reentry.logs.find(r=>r.at>=x.start&&r.text.startsWith('View scene complete'));
          if(completion)x.complete=completion.at;
          const preview=window.__reentry.logs.find(r=>r.at>=x.start&&r.text.startsWith('View preview visible'));
          if(preview)x.preview=preview.at;
          if(!x.complete)requestAnimationFrame(tick);
        };requestAnimationFrame(tick);
      };
    },{view,layout:process.env.REENTRY_LAYOUT||'hub'});
    const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto(origin,{waitUntil:'domcontentloaded'});
    await page.evaluate(()=>window.__watchView());
    await page.waitForFunction(()=>window.__phase?.complete>0,{timeout:60000});
    for(let trial=1;trial<=3;trial++){
      await page.evaluate(()=>{location.hash='#search';});
      await page.waitForSelector('#search-form');
      await page.waitForFunction(()=>!document.querySelector('#view-canvas'));
      requests.length=0;
      const counts=await page.evaluate(()=>({logs:window.__reentry.logs.length,workers:window.__reentry.workers}));
      await page.evaluate(()=>{window.__watchView();location.hash='#view';});
      await page.waitForFunction(()=>window.__phase?.complete>0,{timeout:60000});
      const row=await page.evaluate(({counts})=>({
        firstMs:window.__phase.first-window.__phase.start,
        usefulMs:(window.__phase.preview||window.__phase.first)-window.__phase.start,
        completeMs:window.__phase.complete-window.__phase.start,
        logs:window.__reentry.logs.slice(counts.logs),workers:window.__reentry.workers-counts.workers,
        graphNodes:window.__g3?.graphData().nodes.length,
      }),{counts});
      const graphs=requests.filter(r=>r.path==='/api/graph/graph');
      Object.assign(row,{label,size,view,trial,errors:[...errors],requests:[...requests],
        graphRequests:graphs.length,fullReads:graphs.filter(r=>r.backendMeasures?.queryMs!==undefined).length,
        fullLayoutCalls:graphs.reduce((s,r)=>s+(r.backendMeasures?.fullLayoutCalls||0),0),
        fullLayoutMs:graphs.reduce((s,r)=>s+(r.backendMeasures?.fullLayoutMs||0),0),
        rendererMounts:row.logs.filter(r=>r.text.startsWith('View scene ready')).length});
      results.push(row);console.log(JSON.stringify({label,size,view,trial,firstMs:row.firstMs,usefulMs:row.usefulMs,completeMs:row.completeMs,graphRequests:row.graphRequests,fullReads:row.fullReads,fullLayoutCalls:row.fullLayoutCalls,workers:row.workers,rendererMounts:row.rendererMounts,errors}));
    }
    await context.close();
  }
  await writeFile(resolve(evidence,`${label}-reentry.json`),JSON.stringify(results,null,2));
}finally{await browser.close();await new Promise(r=>server.close(r));}
