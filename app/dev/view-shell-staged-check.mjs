// Run: node app/dev/view-shell-staged-check.mjs APP_DIR SYNTHETIC_GRAPH_JSON OUTPUT_JSON
// Playwright must resolve from app/package.json or the package.json given by VINC_TEST_MODULES.
// The desktop shell is simulated by the host name `tauri.localhost` (the app's own shell test)
// with the sidecar at 127.0.0.1:8787 answered by this script. No real engine and no user data.
// Checks the staged initial read in local mode and in account mode (through /space/proxy).
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
const server=await createServer({root:app,configFile:resolve(app,'vite.config.ts'),server:{host:'127.0.0.1',port:0}});
await server.listen();
const port=server.httpServer.address().port;
const origin=`http://tauri.localhost:${port}`;
let browser, held=null;
const rows=[], errors=[], sidecar=[];
const cors={'access-control-allow-origin':origin,'access-control-allow-headers':'*','access-control-allow-methods':'GET,POST,OPTIONS'};
const graphReply=(p,route)=>{
  const stage=p.stage;
  if(stage==='cancel')return route.fulfill({headers:cors,json:{executed:true,cancelled:true}});
  if(stage==='preview'){
    const nodes=fixture.nodes.filter(n=>n.kind==='concept').slice(0,128),ids=new Set(nodes.map(n=>n.id));
    return route.fulfill({headers:cors,json:{executed:true,kind:'graph_preview',partial:true,nodes,
      edges:fixture.edges.filter(e=>ids.has(e.source)&&ids.has(e.target)).slice(0,512),
      snapshot:{protocol:'graph-preview-v1',token:'x'.repeat(43)}}});
  }
  if(stage==='complete'&&!held){held=route;return;}
  return route.fulfill({headers:cors,json:fixture});
};
try {
  browser=await chromium.launch({channel:'msedge',headless:true,args:['--enable-webgl','--ignore-gpu-blocklist']});
  for (const mode of ['local','account']) {
    sidecar.length=0;held=null;
    const context=await browser.newContext({viewport:{width:1440,height:900}});
    await context.addInitScript(m=>{
      localStorage.setItem('vinc.owner','user:synthetic-view-loading');
      localStorage.setItem('vinc.lang','en');localStorage.setItem('vinc.theme','dark');
      localStorage.setItem('vinc.view.coach.seen','1');
      localStorage.setItem('vinc.mode',m);localStorage.setItem('vinc.sync.linked','1');
      if(m==='account')localStorage.setItem('vinc.sync.key','synthetic-member-key');
      localStorage.setItem('vinc.view.prefs',JSON.stringify({pv:2,view:'3d',layout:'hub',edges:'none'}));
    },mode);
    await context.route('**/*',async route=>{
      const req=route.request(),url=new URL(req.url());
      if(url.origin===origin){return route.continue();}
      if(url.host==='127.0.0.1:8787'){
        if(req.method()==='OPTIONS')return route.fulfill({status:204,headers:cors});
        const p=Object.fromEntries(url.searchParams);
        if(url.pathname==='/config')return route.fulfill({headers:cors,json:{executed:true,mode,mode_chosen:true,data_path:null,data_file:null,default_folder:'',suggested_folder:'',pinned:false,restart_needed:false,store_open:true}});
        if(url.pathname==='/graph'){sidecar.push({via:'local',stage:p.stage||null});return graphReply(p,route);}
        if(url.pathname==='/space/proxy'){
          const body=JSON.parse(req.postData()||'{}');
          if(body.path==='/graph'){sidecar.push({via:'proxy',stage:body.params?.stage||null,token:body.token});return graphReply(body.params||{},route);}
          return route.fulfill({headers:cors,json:{executed:true,marker:'m',domains:[],unfiled:[],rows:[],counts:{kinds:{}}}});
        }
        return route.fulfill({headers:cors,json:{executed:true,ok:true,marker:'m',domains:[],unfiled:[],rows:[],counts:{kinds:{}},status:'ok'}});
      }
      errors.push(`Blocked ${url.origin}`);return route.abort();
    });
    const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
    await page.goto(origin);
    assert.equal(await page.evaluate(()=>location.hostname),'tauri.localhost');
    await page.locator('[data-graph-preview]').waitFor({timeout:30000});
    for(let i=0;i<200&&!held;i++)await page.waitForTimeout(20);
    assert.ok(held,`${mode}: continuation read starts behind the preview`);
    assert.equal(await page.locator('[data-graph-preview] circle').count(),128);
    assert.deepEqual(sidecar.filter(r=>r.stage).map(r=>r.stage),['preview','complete']);
    assert.ok(sidecar.every(r=>r.via===(mode==='account'?'proxy':'local')),`${mode}: graph reads use the ${mode} route`);
    rows.push({test:`desktop ${mode} mode shows the bounded overview before the full graph`,calls:[...sidecar]});
    await held.fulfill({headers:cors,json:fixture});held=null;
    await page.waitForFunction(()=>!document.querySelector('[data-graph-preview]')&&!!window.__g3?.graphData()?.nodes.length,null,{timeout:60000});
    rows.push({test:`${mode}: overview is replaced by the full scene`});
    await context.close();
  }
  assert.deepEqual(errors,[]);
  await writeFile(output,JSON.stringify({rows,errors,sidecar},null,2));console.log(JSON.stringify({passed:rows.length,errors}));
} finally {if(held)await held.abort().catch(()=>{});await browser?.close();await server.close();}
