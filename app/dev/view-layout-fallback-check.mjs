// Run: node app/dev/view-layout-fallback-check.mjs APP_DIR SYNTHETIC_GRAPH_JSON OUTPUT_JSON
// Playwright must resolve from app/package.json or the package.json given by VINC_TEST_MODULES.
// Time branches on a slow or failing layout: the by-centre geometry the read already returned is drawn
// with a status line instead of an error box, and the branches replace it when the worker finishes.
// Every API is synthetic and every non-loopback request is blocked. Takes about 40 s (15 s patience twice).
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFile,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
const app=resolve(process.argv[2]), fixture=JSON.parse(await readFile(process.argv[3],'utf8')), output=resolve(process.argv[4]);
fixture.nodes.forEach((n,i)=>{if(n.kind!=='domain')n.ts=`2026-10-${String(i%9+1).padStart(2,'0')}T00:00:00Z`;});
const require=createRequire(resolve(app,'package.json')), pw=createRequire(process.env.VINC_TEST_MODULES||resolve(app,'package.json'));
process.chdir(app);
const {createServer}=require('vite'),{chromium}=pw('playwright');
const server=await createServer({root:app,configFile:resolve(app,'vite.config.ts'),server:{host:'127.0.0.2',port:0}});
await server.listen(); const origin=`http://127.0.0.2:${server.httpServer.address().port}`;
const browser=await chromium.launch({channel:'msedge',headless:true,args:['--enable-webgl','--ignore-gpu-blocklist']});
const rows=[], errors=[];
try {
  const run=async mode=>{
    const context=await browser.newContext({viewport:{width:1440,height:900}});
    await context.addInitScript(m=>{
      Object.defineProperty(document,'cookie',{get:()=> '__Host-csrf=synthetic'});
      localStorage.setItem('vinc.owner','user:synthetic-view-loading');localStorage.setItem('vinc.lang','en');
      localStorage.setItem('vinc.theme','dark');localStorage.setItem('vinc.view.coach.seen','1');
      localStorage.setItem('vinc.view.prefs',JSON.stringify({pv:2,view:'3d',layout:'temporalRoots',edges:'none'}));
      window.__checks={logs:[],held:[]};
      const info=console.info;console.info=function(...a){if(String(a[0]).startsWith('View '))window.__checks.logs.push(String(a[0]));info.apply(this,a);};
      const W=window.Worker;
      // Only the layout worker is controlled; other workers run normally.
      window.Worker=class extends W{
        constructor(url,...rest){super(url,...rest);this.layout=String(url).includes('view-layout.worker');}
        postMessage(...a){
          if(!this.layout)return super.postMessage(...a);
          if(m==='fail'){setTimeout(()=>this.onerror?.({message:'synthetic layout failure'}),50);return;}
          window.__checks.held.push(()=>super.postMessage(...a));
        }
      };
    },mode);
    await context.route('**/*',async route=>{
      const url=new URL(route.request().url());
      if(url.origin!==origin){errors.push(`Blocked ${url.origin}`);return route.abort();}
      if(url.pathname==='/assets/gnb.js')return route.fulfill({body:'',contentType:'text/javascript'});
      if(!url.pathname.startsWith('/api/'))return route.continue();
      if(url.pathname==='/api/me')return route.fulfill({json:{user:{email:'a@example.invalid',name:'Synthetic'}}});
      if(url.pathname==='/api/teams')return route.fulfill({json:{teams:[]}});
      if(url.pathname==='/api/graph/graph'){
        if(url.searchParams.get('stage')==='cancel')return route.fulfill({json:{executed:true,cancelled:true}});
        return route.fulfill({json:{...fixture,marker:'epoch.1'}});
      }
      return route.fulfill({json:{executed:true,marker:'epoch.1',domains:[],unfiled:[],rows:[],counts:{kinds:{}}}});
    });
    const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
    await page.goto(origin+'/#view',{waitUntil:'domcontentloaded'});
    return {context,page};
  };
  const note=page=>page.locator('[data-layout-note]').textContent().catch(()=>null);
  // The 3D scene rescales coordinates, so compare positions before and after the swap instead.
  const positions=page=>page.evaluate(()=>Object.fromEntries(window.__g3.graphData().nodes.filter(n=>n.kind!=='domain')
    .map(n=>[n.id,[Math.round(n.x*1e3)/1e3,Math.round(n.y*1e3)/1e3]])));

  // 1. Slow layout: by-centre first with a note, then the branches when the worker finishes.
  {
    const {context,page}=await run('slow');
    await page.waitForFunction(()=>window.__checks.held.length>0,null,{timeout:30000});
    const started=Date.now();
    await page.locator('[data-layout-note]').waitFor({timeout:30000});
    assert.ok(Date.now()-started>=10000,'the by-centre geometry waits for the patience period first');
    assert.match(await note(page),/still being laid out/);
    assert.equal(await page.locator('#view-msg .empty').count(),0,'no error box');
    assert.ok(await page.evaluate(()=>window.__g3?.graphData()?.nodes.length>0));
    const before=await positions(page);
    assert.equal(Object.keys(before).length,fixture.nodes.filter(n=>n.kind!=='domain').length,'every node is drawn');
    rows.push({test:'a slow time-branches layout shows the by-centre geometry with a note instead of waiting or failing'});
    await page.evaluate(()=>{window.__checks.logs=[];for(const f of window.__checks.held.splice(0))f();});
    await page.waitForFunction(()=>window.__checks.logs.some(t=>t.startsWith('View layout stages')),null,{timeout:60000});
    await page.waitForFunction(()=>!document.querySelector('[data-layout-note]'),null,{timeout:30000});
    await page.waitForFunction(()=>window.__g3?.graphData()?.nodes.length>0,null,{timeout:30000});
    const after=await positions(page);
    const moved=Object.keys(after).filter(id=>before[id]&&(before[id][0]!==after[id][0]||before[id][1]!==after[id][1])).length;
    assert.ok(moved>Object.keys(after).length/2,`the branches replaced the by-centre geometry (${moved} moved)`);
    rows.push({test:'when the worker finishes the branches replace the by-centre geometry and the note goes'});
    await context.close();
  }
  // 2. Failing layout: by-centre with a failure note, never the error box.
  {
    const {context,page}=await run('fail');
    await page.locator('[data-layout-note]').waitFor({timeout:30000});
    assert.match(await note(page),/couldn't be laid out/);
    assert.equal(await page.locator('#view-msg .empty').count(),0,'no error box');
    assert.ok(await page.evaluate(()=>window.__g3?.graphData()?.nodes.length>0),'the by-centre scene is drawn');
    rows.push({test:'a failed time-branches layout keeps the by-centre geometry with a note'});
    await context.close();
  }
  assert.deepEqual(errors,[]);
  await writeFile(output,JSON.stringify({rows,errors},null,2));console.log(JSON.stringify({passed:rows.length,errors}));
} finally {await browser.close();await server.close();}
