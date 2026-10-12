// Run: node app/dev/view-roots-staged-check.mjs APP_DIR SYNTHETIC_GRAPH_JSON OUTPUT_JSON
// Playwright must resolve from app/package.json or the package.json given by VINC_TEST_MODULES.
// Time branches (temporalRoots) show the same bounded partial overview as the original
// layout while the full read and the ELK worker are pending. Remote requests are blocked.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFile,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
const app=resolve(process.argv[2]), fixture=JSON.parse(await readFile(process.argv[3],'utf8')), output=resolve(process.argv[4]);
fixture.nodes.forEach((n,i)=>{if(n.kind!=='domain')n.ts=`2026-10-${String(i%9+1).padStart(2,'0')}T00:00:00Z`;});
const require=createRequire(resolve(app,'package.json'));
process.chdir(app);
const {createServer}=require('vite');
const {chromium}=createRequire(process.env.VINC_TEST_MODULES||resolve(app,'package.json'))('playwright');
const server=await createServer({root:app,configFile:resolve(app,'vite.config.ts'),server:{host:'127.0.0.2',port:0}});
await server.listen();
const origin=`http://127.0.0.2:${server.httpServer.address().port}`;
let browser, held=null;
const rows=[], errors=[], requests=[];
try {
  browser=await chromium.launch({channel:'msedge',headless:true,args:['--enable-webgl','--ignore-gpu-blocklist']});
  const context=await browser.newContext({viewport:{width:1440,height:900}});
  await context.addInitScript(()=>{
    Object.defineProperty(document,'cookie',{get:()=> '__Host-csrf=synthetic'});
    localStorage.setItem('vinc.owner','user:synthetic-view-loading');
    localStorage.setItem('vinc.lang','en');localStorage.setItem('vinc.theme','dark');
    localStorage.setItem('vinc.view.coach.seen','1');
    localStorage.setItem('vinc.view.prefs',JSON.stringify({pv:2,view:new URL(location.href).searchParams.get('checkview')||'3d',layout:'temporalRoots',edges:'none'}));
    window.__checks={logs:[]};
    const info=console.info;console.info=function(...a){if(String(a[0]).startsWith('View '))window.__checks.logs.push(String(a[0]));info.apply(this,a);};
  });
  await context.route('**/*',async route=>{
    const req=route.request(),url=new URL(req.url());
    if(url.origin!==origin){errors.push(`Blocked ${url.origin}`);return route.abort();}
    if(url.pathname==='/assets/gnb.js')return route.fulfill({body:'',contentType:'text/javascript'});
    if(!url.pathname.startsWith('/api/'))return route.continue();
    requests.push({path:url.pathname,query:url.search});
    if(url.pathname==='/api/me')return route.fulfill({json:{user:{email:'synthetic@example.invalid',name:'Synthetic'}}});
    if(url.pathname==='/api/teams')return route.fulfill({json:{teams:[]}});
    if(url.pathname==='/api/graph/graph'){
      const stage=url.searchParams.get('stage');
      if(stage==='cancel')return route.fulfill({json:{executed:true,cancelled:true}});
      if(stage==='preview'){
        const nodes=fixture.nodes.filter(n=>n.kind==='concept').slice(0,128),ids=new Set(nodes.map(n=>n.id));
        return route.fulfill({json:{executed:true,kind:'graph_preview',partial:true,nodes,
          edges:fixture.edges.filter(e=>ids.has(e.source)&&ids.has(e.target)).slice(0,512),
          snapshot:{protocol:'graph-preview-v1',token:'x'.repeat(43)}}});
      }
      if(!held&&stage==='complete'){held=route;return;}
      return route.fulfill({json:fixture});
    }
    return route.fulfill({json:{executed:true,marker:'synthetic',domains:[],unfiled:[],rows:[],counts:{kinds:{}}}});
  });
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  const layoutRequests=()=>requests.filter(r=>r.path==='/api/graph/graph').map(r=>new URLSearchParams(r.query).get('layout'));
  for (const view of ['3d','2d']) {
    held=null;requests.length=0;
    await page.goto(`${origin}/?checkview=${view}`);
    await page.locator('[data-graph-preview]').waitFor({timeout:30000});
    for(let i=0;i<200&&!held;i++)await page.waitForTimeout(20);
    assert.ok(held,`${view}: continuation read starts behind the preview`);
    assert.equal(await page.locator('[data-graph-preview] circle').count(),128);
    assert.ok((await page.locator('#view-canvas [role="status"]').textContent()).includes('Partial overview'));
    assert.deepEqual([...new Set(layoutRequests())],['hub'],`${view}: roots use the hub population on the wire`);
    rows.push({test:`${view} time branches show the partial overview before the full response`});
    await held.fulfill({json:fixture});held=null;
    // The overview stays until the worker layout is drawn, then disappears for the final scene.
    await page.waitForFunction(v=>!document.querySelector('[data-graph-preview]')&&(v==='3d'?!!window.__g3?.graphData()?.nodes.length:!!document.querySelector('.a2-stage canvas')),view,{timeout:60000});
    assert.ok(await page.evaluate(()=>window.__checks.logs.some(t=>t.startsWith('View layout stages'))),`${view}: worker layout ran`);
    rows.push({test:`${view} final time-branch scene replaces the overview`});
  }
  assert.deepEqual(errors,[]);
  await writeFile(output,JSON.stringify({rows,errors,requests},null,2));console.log(JSON.stringify({passed:rows.length,errors}));
} finally {if(held)await held.abort().catch(()=>{});await browser?.close();await server.close();}
