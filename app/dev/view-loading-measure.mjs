// Run after npm run build and measure_view_graph.py:
// node app/dev/view-loading-measure.mjs APP_DIR OUTPUT_DIR LABEL
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
const require = createRequire(resolve(process.env.VINC_TEST_MODULES || resolve(app, 'package.json')));
const { chromium } = require('playwright');
let size = 200;
const requests = [];
const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.woff2':'font/woff2', '.wasm':'application/wasm', '.svg':'image/svg+xml' };
const server = createServer(async (req, res) => {
  const url = new URL(req.url, 'http://local'), path = url.pathname;
  const started = performance.now();
  let body, backendMeasures, type = 'application/json', cache = 'no-store';
  if (path === '/api/graph/graph') {
    const response = await fetch(`${backend}/graph?size=${size}`);
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
  for (size of [200,3000]) for (const view of ['2d','3d']) {
    const context=await browser.newContext({viewport:{width:1440,height:900}});
    await context.addInitScript(({view})=>{
      // Synthetic auth only. No credential or real account is used.
      Object.defineProperty(document,'cookie',{get:()=> '__Host-csrf=synthetic'});
      localStorage.setItem('vinc.owner','user:synthetic-view-loading');
      localStorage.setItem('vinc.view.coach.seen','1');
      localStorage.setItem('vinc.view.prefs',JSON.stringify({pv:2,view,layout:'hub',edges:'none'}));
      localStorage.setItem('vinc.theme','dark');
      window.__measure={json:[],longTasks:[],first:0,complete:0,logs:[]};
      const info=console.info;
      console.info=function(...args){
        if(String(args[0]).startsWith('View ')){
          const text=String(args[0]); window.__measure.logs.push({at:performance.now(),text});
          if(text.startsWith('View scene complete')) window.__measure.complete=performance.now();
        }
        info.apply(this,args);
      };
      const json=Response.prototype.json;
      Response.prototype.json=async function(){ const t=performance.now(); const value=await json.call(this);
        if(this.url.includes('/graph/graph')) window.__measure.json.push({started:t,ended:performance.now()}); return value; };
      new PerformanceObserver(list=>window.__measure.longTasks.push(...list.getEntries().map(x=>({start:x.startTime,ms:x.duration})))).observe({type:'longtask',buffered:true});
      let frame=0;
      const tick=()=>{ const canvas=document.querySelector(view==='2d'?'.a2-stage canvas':'.scene-container canvas');
        if(canvas&&canvas.getBoundingClientRect().width>0&&getComputedStyle(canvas.closest('#view-canvas')||canvas).opacity!=='0'){
          if(++frame>=3&&!window.__measure.first) {
            window.__measure.first=performance.now();
            if(view==='2d') window.__measure.complete=performance.now();
          }
        } else frame=0;
        if(!window.__measure.first) requestAnimationFrame(tick); };
      requestAnimationFrame(tick);
    },{view});
    const page=await context.newPage(); const errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    for(const temperature of ['cold','warm']) {
      requests.length=0;
      await page.goto(origin,{waitUntil:'domcontentloaded'});
      await page.waitForFunction(()=>window.__measure?.first>0,{timeout:60000});
      if(view==='3d') {
        if(label.startsWith('before')) {
          await page.waitForFunction(()=>window.__measure.logs.some(x=>x.text.startsWith('View scene ready')),{timeout:60000});
          await page.evaluate(()=>new Promise(resolve=>{
            const ready=window.__measure.logs.find(x=>x.text.startsWith('View scene ready')).at;
            setTimeout(()=>{window.__measure.complete=performance.now();resolve();}, Math.max(0,ready+460-performance.now()));
          }));
        } else await page.waitForFunction(()=>window.__measure.complete>0,{timeout:60000});
      }
      const row=await page.evaluate(()=>({...window.__measure,resources:performance.getEntriesByType('resource').map(x=>({name:new URL(x.name).pathname,start:x.startTime,header:x.responseStart,end:x.responseEnd,transfer:x.transferSize,decoded:x.decodedBodySize})),navigation:performance.getEntriesByType('navigation')[0].toJSON()}));
      Object.assign(row,{label,size,view,temperature,errors:[...errors],serverRequests:[...requests]});
      results.push(row); console.log(JSON.stringify({label,size,view,temperature,first:row.first,complete:row.complete,json:row.json,longTaskMs:row.longTasks.reduce((s,x)=>s+x.ms,0),logs:row.logs}));
      await page.screenshot({path:resolve(evidence,`${label}-${size}-${view}-${temperature}.png`)});
    }
    await context.close();
  }
  await writeFile(resolve(evidence,`${label}-web.json`),JSON.stringify(results,null,2));
} finally { await browser.close(); await new Promise(r=>server.close(r)); }
