// Run: node app/dev/view-staged-check.mjs APP_DIR SYNTHETIC_GRAPH_JSON OUTPUT_JSON
// Playwright must resolve from app/package.json or the package.json given by VINC_TEST_MODULES.
// Remote requests are blocked; synthetic GET responses replace auth and graph APIs.
// The full response is deliberately held for lifecycle checks; this is not a timing benchmark.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFile,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
const app=resolve(process.argv[2]), fixture=JSON.parse(await readFile(process.argv[3],'utf8')), output=resolve(process.argv[4]);
// Time cursor behavior needs dated input; query.graph's synthetic Concepts are
// intentionally timeless. Dates here belong only to the mocked UI responses.
fixture.nodes.forEach((n,i)=>{if(n.kind!=='domain')n.ts=`2026-10-${String(i%9+1).padStart(2,'0')}T00:00:00Z`;});
fixture.edges[0].type='PREREQUISITE_OF';
const require=createRequire(resolve(app,'package.json'));
process.chdir(app);
const {createServer}=require('vite');
const pw=createRequire(process.env.VINC_TEST_MODULES||resolve(app,'package.json'));
const {chromium}=pw('playwright');
const server=await createServer({root:app,configFile:resolve(app,'vite.config.ts'),server:{host:'127.0.0.2',port:0}});
await server.listen();
const origin=`http://127.0.0.2:${server.httpServer.address().port}`;
let browser, held=null, holdNext=true, fail=false;
const rows=[], errors=[], requests=[];
try {
  browser=await chromium.launch({channel:'msedge',headless:true,args:['--enable-webgl','--ignore-gpu-blocklist']});
  const context=await browser.newContext({viewport:{width:1440,height:900}});
  await context.addInitScript(()=>{
    Object.defineProperty(document,'cookie',{get:()=> '__Host-csrf=synthetic'});
    localStorage.setItem('vinc.owner','user:synthetic-view-loading');
    localStorage.setItem('vinc.lang','en');localStorage.setItem('vinc.theme','dark');
    localStorage.setItem('vinc.view.coach.seen','1');
    localStorage.setItem('vinc.view.prefs',JSON.stringify({pv:2,view:new URL(location.href).searchParams.get('checkview')||'3d',layout:'hub',edges:'none'}));
    window.__checks={logs:[],graphs:[]};
    const info=console.info;console.info=function(...args){if(String(args[0]).startsWith('View '))window.__checks.logs.push(String(args[0])); info.apply(this,args);};
    const json=Response.prototype.json;Response.prototype.json=async function(){const data=await json.call(this);if(this.url.includes('/graph/graph'))window.__checks.graphs.push(data);return data;};
  });
  const ownerGraph=owner=>{
    if(!owner.startsWith('team:')) return fixture;
    const id=x=>`team-${x}`;
    return {...fixture,nodes:fixture.nodes.map(n=>({...n,id:id(n.id)})),edges:fixture.edges.map(e=>({...e,source:id(e.source),target:id(e.target)})),topicEdges:(fixture.topicEdges||[]).map(e=>({...e,source:id(e.source),target:id(e.target)}))};
  };
  await context.route('**/*',async route=>{
    const req=route.request(),url=new URL(req.url());
    if(url.origin!==origin){errors.push(`Blocked ${url.origin}`);return route.abort();}
    if(url.pathname==='/assets/gnb.js')return route.fulfill({body:'',contentType:'text/javascript'});
    if(!url.pathname.startsWith('/api/'))return route.continue();
    assert.equal(req.method(),'GET');requests.push({path:url.pathname,query:url.search});
    if(url.pathname==='/api/me')return route.fulfill({json:{user:{email:'synthetic@example.invalid',name:'Synthetic'}}});
    if(url.pathname==='/api/teams')return route.fulfill({json:{teams:[{id:'synthetic-team',name:'Synthetic team',role:'owner',status:'active'}]}});
    if(url.pathname==='/api/graph/graph'){
      const graph=ownerGraph(url.searchParams.get('team')?'team:'+url.searchParams.get('team'):url.searchParams.get('owner')||'');
      if(url.searchParams.get('stage')==='cancel') return route.fulfill({json:{executed:true,cancelled:true}});
      if(!fail && url.searchParams.get('stage')==='preview'){
        const nodes=graph.nodes.filter(n=>n.kind==='concept').slice(0,128),ids=new Set(nodes.map(n=>n.id));
        return route.fulfill({json:{executed:true,kind:'graph_preview',partial:true,nodes,
          edges:graph.edges.filter(e=>ids.has(e.source)&&ids.has(e.target)).slice(0,512),
          snapshot:{protocol:'graph-preview-v1',token:'x'.repeat(43)}}});
      }
      if(holdNext){holdNext=false;held=route;return;}
      if(fail)return route.fulfill({status:503,json:{executed:false,error:'synthetic_unavailable'}});
      return route.fulfill({json:ownerGraph(url.searchParams.get('team')?'team:'+url.searchParams.get('team'):url.searchParams.get('owner')||'')});
    }
    const node=fixture.nodes.find(n=>n.id===url.searchParams.get('node'))||fixture.nodes[0];
    if(url.pathname==='/api/graph/node-context')return route.fulfill({json:{executed:true,node,links:{executed:true,out:2,in:2,degree:4,attached:0,rows:[]},excerpts:[]}});
    return route.fulfill({json:{executed:true,marker:'synthetic',domains:[],unfiled:[],rows:[],counts:{kinds:{}}}});
  });
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  const complete=async()=>page.waitForFunction(()=>window.__checks.logs.some(t=>t.startsWith('View scene complete')),{timeout:60000});
  const state=()=>page.evaluate(()=>{
    const g=window.__g3, data=g.graphData(), v=g.nodeVisibility(), vis=n=>typeof v==='function'?v(n):v!==false;
    const cam=g.camera(), forward=cam.getWorldDirection(cam.position.clone()), inside=[];
    for(const n of data.nodes){if(!vis(n)||!n.__threeObj)continue;const p=g.graph2ScreenCoords(n.x,n.y,n.z);
      const depth=(n.x-cam.position.x)*forward.x+(n.y-cam.position.y)*forward.y+(n.z-cam.position.z)*forward.z;
      if(depth>0&&p.x>=0&&p.y>=0&&p.x<=g.width()&&p.y<=g.height())inside.push(n.id);}
    return {nodes:data.nodes.length,links:data.links.length,objects:data.nodes.filter(n=>n.__threeObj).length,
      lineObjects:data.links.filter(e=>e.__lineObj).length,inside:inside.length,
      arrowObjects:data.links.filter(e=>e.__arrowObj).length,particleObjects:data.links.filter(e=>e.__photonsObj).length,
      camera:[cam.position.x,cam.position.y,cam.position.z],positions:data.nodes.map(n=>[n.id,n.x,n.y,n.z]),visibility:data.nodes.filter(vis).length};
  });
  await page.goto(origin);
  await page.locator('[data-graph-preview]').waitFor();
  for(let i=0;i<100&&!held;i++)await page.waitForTimeout(20);
  assert.ok(held,'full response is withheld in this functional test only');
  assert.equal(await page.locator('[data-graph-preview] circle').count(),128);
  assert.equal(await page.locator('[data-graph-preview] line').count(),0);
  assert.equal(await page.locator('#view-canvas canvas').count(),0);
  assert.ok((await page.locator('#view-canvas [role="status"]').textContent()).includes('Partial overview'));
  await page.screenshot({path:output.replace('.json','-partial.png')});
  rows.push({test:'real partial structure precedes full response; hidden links, bounded nodes and explicit provisional status'});
  await held.fulfill({json:fixture});held=null;await complete();
  await page.waitForFunction(()=>!document.querySelector('[data-graph-preview]'));
  assert.equal(await page.locator('#view-canvas [role="status"]').count(),0);
  let baseline=await state();assert.equal(baseline.nodes,fixture.nodes.length);assert.equal(baseline.links,fixture.edges.length);assert.equal(baseline.lineObjects,0);assert.equal(baseline.objects,fixture.nodes.length);assert.ok(baseline.inside>0);
  assert.ok(JSON.stringify(await page.evaluate(()=>window.__checks.graphs.at(-1)))===JSON.stringify(fixture),'semantic JSON including Unicode and nested properties is identical');
  rows.push({test:'initial full graph, relationships, Unicode properties and useful framing',...baseline,positions:undefined});
  await page.screenshot({path:output.replace('.json','-initial.png')});
  // Hover/selection use the complete topology even when no link geometry exists.
  const interacted=await page.evaluate(async()=>{
    const g=window.__g3,n=g.graphData().nodes.find(n=>n.kind==='concept');
    g.onNodeHover()(n);g.onNodeClick()(n);await new Promise(r=>setTimeout(r,400));
    return {card:document.querySelector('.float-title')?.textContent,links:g.graphData().links.length};
  });
  assert.ok(interacted.card?.includes('Synthetic'));assert.equal(interacted.links,fixture.edges.length);rows.push({test:'hover and selection retained',...interacted});
  // Time cut must not recreate hidden lines; coordinates and camera stay fixed.
  await page.locator('#view-lens').click();await page.locator('[data-tab="opts"]').click();
  const when=page.locator('#pref-when');
  assert.equal(await when.count(),1);
  if(await when.count()) {
    await when.evaluate(el=>{el.value='0.3';el.dispatchEvent(new Event('input',{bubbles:true}));});
    await page.waitForTimeout(120);const cut=await state();assert.equal(cut.lineObjects,0);assert.ok(cut.visibility<fixture.nodes.length);assert.deepEqual(cut.positions,baseline.positions);
    await when.evaluate(el=>{el.value=el.max;el.dispatchEvent(new Event('input',{bubbles:true}));});
    rows.push({test:'time cutoff preserves coordinates and hidden-line object count',visible:cut.visibility});
  }
  for(const mode of ['always','particles','none']){
    const previous=await state();
    await page.evaluate(()=>window.__checks.logs=[]);
    await page.locator(`[data-pref="edges"][data-val="${mode}"]`).click();await complete();
    const now=await state();assert.equal(now.links,fixture.edges.length);
    assert.equal(now.lineObjects,mode==='none'?0:fixture.edges.length);
    assert.deepEqual(now.positions,baseline.positions);
    assert.ok(now.camera.every((x,i)=>Math.abs(x-previous.camera[i])<.01),'edge preference preserves camera');
    if(mode==='particles')assert.ok(now.particleObjects>0);
    if(mode==='none'){assert.equal(now.arrowObjects,0);assert.equal(now.particleObjects,0);}
    rows.push({test:`edge mode ${mode}`,nodes:now.nodes,links:now.links,lineObjects:now.lineObjects,inside:now.inside});
  }
  // Space switch reloads; the fixture's node IDs distinguish account scopes.
  await page.goto(`${origin}/?space=synthetic-team`);await complete();
  assert.ok(await page.evaluate(()=>window.__g3.graphData().nodes.every(n=>n.id.startsWith('team-'))));
  rows.push({test:'Space switch contains only requested team nodes'});
  fail=true;await page.reload();await page.locator('#view-msg .empty').waitFor({timeout:60000});
  assert.equal(await page.locator('#view-canvas canvas').count(),0);rows.push({test:'failed initial graph produces error state, no partial scene'});
  fail=false;
  // Isolate each partial lifecycle with the default initial preferences.
  const initial=async()=>{
    await page.evaluate(()=>localStorage.setItem('vinc.view.prefs',JSON.stringify({pv:2,view:'3d',layout:'hub',edges:'none'})));
    holdNext=true;held=null;await page.goto(origin);
    await page.locator('[data-graph-preview]').waitFor();
    for(let i=0;i<100&&!held;i++)await page.waitForTimeout(20);
    assert.ok(held);
  };
  await initial();
  await page.locator('#view-lens').click();await page.locator('[data-tab="opts"]').click();
  await page.locator('#pref-when').evaluate(el=>{el.value='0.3';el.dispatchEvent(new Event('input',{bubbles:true}));});
  await page.waitForFunction(()=>!document.querySelector('[data-graph-preview]'));
  await held.fulfill({json:fixture});held=null;await complete();
  const filtered=await state();assert.ok(filtered.visibility<fixture.nodes.length);assert.equal(filtered.lineObjects,0);
  rows.push({test:'time filter removes provisional overview; final graph applies current filter without hidden lines'});
  await initial();const removed=held;held=null;
  const cancelBefore=requests.filter(r=>new URLSearchParams(r.query).get('stage')==='cancel').length;
  await page.locator('#view-canvas').evaluate(el=>el.remove());
  for(let i=0;i<100&&requests.filter(r=>new URLSearchParams(r.query).get('stage')==='cancel').length===cancelBefore;i++)await page.waitForTimeout(20);
  assert.ok(requests.filter(r=>new URLSearchParams(r.query).get('stage')==='cancel').length>cancelBefore);
  await removed.fulfill({json:fixture}).catch(()=>{});assert.equal(await page.locator('#view-canvas').count(),0);
  rows.push({test:'removed View cancels continuation and cannot remount a late graph'});
  await initial();await held.fulfill({status:503,json:{executed:false,error:'synthetic_partial_failure'}});held=null;
  await complete();assert.equal(await page.locator('[data-graph-preview]').count(),0);
  assert.equal((await state()).nodes,fixture.nodes.length);
  rows.push({test:'partial completion failure cancels and falls back to the exact legacy full graph'});
  assert.deepEqual(errors,[]);
  await writeFile(output,JSON.stringify({rows,errors,requests},null,2));console.log(JSON.stringify({passed:rows.length,errors}));
} finally {if(held)await held.abort().catch(()=>{});await browser?.close();await server.close();}
