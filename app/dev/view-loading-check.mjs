// Run: node app/dev/view-loading-check.mjs APP_DIR SYNTHETIC_GRAPH_JSON OUTPUT_JSON
// Playwright must resolve from app/package.json or the package.json given by VINC_TEST_MODULES.
// Remote requests are blocked; synthetic GET responses replace auth and graph APIs.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFile,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
const app=resolve(process.argv[2]), fixture=JSON.parse(await readFile(process.argv[3],'utf8')), output=process.argv[4];
// Time cursor behavior needs dated input; query.graph's synthetic Concepts are
// intentionally timeless. Dates here belong only to the mocked UI responses.
fixture.nodes.forEach((n,i)=>{if(n.kind!=='domain')n.ts=`2026-10-${String(i%9+1).padStart(2,'0')}T00:00:00Z`;});
fixture.edges[0].type='PREREQUISITE_OF';
const require=createRequire(resolve(app,'package.json'));
process.chdir(app);
const {createServer}=require('vite');
const pw=createRequire(process.env.VINC_TEST_MODULES||resolve(process.env.VINC_TEST_MODULES || resolve(app, 'package.json')));
const {chromium}=pw('playwright');
const server=await createServer({root:app,configFile:resolve(app,'vite.config.ts'),server:{host:'127.0.0.2',port:0}});
await server.listen();
const origin=`http://127.0.0.2:${server.httpServer.address().port}`;
let browser, held=null, holdNext=false, fail=false;
const rows=[], errors=[], requests=[];
try {
  browser=await chromium.launch({channel:'msedge',headless:true,args:['--enable-webgl','--ignore-gpu-blocklist']});
  const context=await browser.newContext({viewport:{width:1440,height:900}});
  await context.addInitScript(()=>{
    Object.defineProperty(document,'cookie',{get:()=> '__Host-csrf=synthetic'});
    localStorage.setItem('vinc.owner','user:synthetic-view-loading');
    localStorage.setItem('vinc.lang','en');localStorage.setItem('vinc.theme','dark');
    localStorage.setItem('vinc.view.coach.seen','1');
    localStorage.setItem('vinc.view.prefs',JSON.stringify({pv:2,view:'3d',layout:'hub',edges:'none'}));
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
    if(url.pathname==='/api/me')return route.fulfill({json:{publicId:'synthetic',name:'Synthetic'}});
    if(url.pathname==='/api/teams')return route.fulfill({json:{teams:[{id:'synthetic-team',name:'Synthetic team',role:'owner',status:'active'}]}});
    if(url.pathname==='/api/graph/graph'){
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
  await page.goto(origin);await complete();
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
  // A response from an older View cannot overwrite the newer 2D surface.
  holdNext=true;
  await page.locator('#pref-cone').evaluate(el=>{el.value='0.8';el.dispatchEvent(new Event('change',{bubbles:true}));});
  await page.waitForFunction(()=>document.querySelector('#view-canvas')!==null);
  for(let i=0;i<50&&!held;i++)await page.waitForTimeout(20);
  assert.ok(held);
  await page.locator('[data-view="2d"]').first().click();await page.locator('.a2-stage canvas').first().waitFor();
  await held.fulfill({json:fixture});held=null;await page.waitForTimeout(350);
  assert.ok(await page.locator('.a2-stage canvas').count());assert.equal(await page.locator('#view-canvas .scene-container').count(),0);
  rows.push({test:'late graph response did not replace newer 2D View'});
  // Space switch reloads; the fixture's node IDs distinguish account scopes.
  await page.goto(`${origin}/?space=synthetic-team`);await complete();
  assert.ok(await page.evaluate(()=>window.__g3.graphData().nodes.every(n=>n.id.startsWith('team-'))));
  rows.push({test:'Space switch contains only requested team nodes'});
  fail=true;await page.reload();await page.locator('#view-msg .empty').waitFor({timeout:60000});
  assert.equal(await page.locator('#view-canvas canvas').count(),0);rows.push({test:'failed initial graph produces error state, no partial scene'});
  assert.deepEqual(errors,[]);
  await writeFile(output,JSON.stringify({rows,errors,requests},null,2));console.log(JSON.stringify({passed:rows.length,errors}));
} finally {if(held)await held.abort().catch(()=>{});await browser?.close();await server.close();}
