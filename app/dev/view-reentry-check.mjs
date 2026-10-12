// Run: node app/dev/view-reentry-check.mjs APP_DIR SYNTHETIC_GRAPH_JSON OUTPUT_JSON
// Every API is synthetic and every non-loopback request is blocked. No user data is read.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFile,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
const app=resolve(process.argv[2]), fixture=JSON.parse(await readFile(process.argv[3],'utf8')), output=process.argv[4];
fixture.nodes.forEach((n,i)=>{if(n.kind!=='domain')n.ts=`2026-10-${String(i%9+1).padStart(2,'0')}T00:00:00Z`;});
const require=createRequire(resolve(app,'package.json')), pw=createRequire(process.env.VINC_TEST_MODULES||resolve(app,'package.json'));
process.chdir(app);
const {createServer}=require('vite'),{chromium}=pw('playwright');
const server=await createServer({root:app,configFile:resolve(app,'vite.config.ts'),server:{host:'127.0.0.2',port:0}});
await server.listen(); const origin=`http://127.0.0.2:${server.httpServer.address().port}`;
const browser=await chromium.launch({channel:'msedge',headless:true,args:['--enable-webgl','--ignore-gpu-blocklist','--disable-background-networking']});
const rows=[], allErrors=[];
async function setup(view='3d',layout='hub',hash='#view') {
  const context=await browser.newContext({viewport:{width:1440,height:900}});
  const state={identity:'a@example.invalid',revision:'epoch.1',changesStatus:200,graphStatus:200,holdChanges:false,holdGraph:false,heldChanges:[],heldGraphs:[],requests:[]};
  await context.addInitScript(({view,layout})=>{
    Object.defineProperty(document,'cookie',{get:()=> '__Host-csrf=synthetic'});
    localStorage.setItem('vinc.owner','user:synthetic-view-loading');localStorage.setItem('vinc.lang','en');
    localStorage.setItem('vinc.theme','dark');localStorage.setItem('vinc.view.coach.seen','1');
    localStorage.setItem('vinc.view.prefs',JSON.stringify({pv:2,view,layout,edges:'none'}));
    window.__checks={logs:[],workers:0,liveWorkers:0,holdWorker:false,heldWorkers:[]};
    const info=console.info;console.info=function(...args){if(String(args[0]).startsWith('View '))window.__checks.logs.push(String(args[0]));info.apply(this,args);};
    const OriginalWorker=window.Worker;
    window.Worker=class extends OriginalWorker {
      constructor(...args){super(...args);window.__checks.workers++;window.__checks.liveWorkers++;this.alive=true;}
      postMessage(...args){if(window.__checks.holdWorker)window.__checks.heldWorkers.push(()=>super.postMessage(...args));else super.postMessage(...args);}
      terminate(){if(this.alive){this.alive=false;window.__checks.liveWorkers--;}super.terminate();}
    };
  },{view,layout});
  const graphFor=url=>{
    const scope=[state.identity,url.searchParams.get('team')||'personal',url.searchParams.get('owner')||'',state.revision].join('|');
    const id=x=>`${scope}:${x}`;
    // The server labels each read with the marker it took before computing it (marker=1).
    return {...fixture,...(state.unlabelled?{}:{marker:state.revision}),nodes:fixture.nodes.map(n=>({...n,id:id(n.id)})),edges:fixture.edges.map(e=>({...e,source:id(e.source),target:id(e.target)})),topicEdges:(fixture.topicEdges||[]).map(e=>({...e,source:id(e.source),target:id(e.target)}))};
  };
  await context.route('**/*',async route=>{
    const req=route.request(),url=new URL(req.url());
    if(url.origin!==origin){allErrors.push(`Blocked ${url.origin}`);return route.abort();}
    if(url.pathname==='/assets/gnb.js')return route.fulfill({body:'',contentType:'text/javascript'});
    if(!url.pathname.startsWith('/api/'))return route.continue();
    assert.equal(req.method(),'GET');state.requests.push({path:url.pathname,query:url.search});
    if(url.pathname==='/api/me')return route.fulfill({json:{user:{email:state.identity,name:'Synthetic',teams:[]}}});
    if(url.pathname==='/api/teams')return route.fulfill({json:{teams:[{id:'synthetic-team',name:'Synthetic',role:'owner',status:'active'}]}});
    if(url.pathname==='/api/graph/changes'){
      if(state.holdChanges){state.heldChanges.push(route);return;}
      return route.fulfill({status:state.changesStatus,json:{executed:state.changesStatus===200,marker:state.revision}});
    }
    if(url.pathname==='/api/graph/graph'){
      if(url.searchParams.get('stage')==='cancel')return route.fulfill({json:{executed:true,cancelled:true}});
      if(state.graphStatus!==200)return route.fulfill({status:state.graphStatus,json:{executed:false,error:'synthetic_denied'}});
      const graph=graphFor(url);
      if(url.searchParams.get('stage')==='preview'){
        const nodes=graph.nodes.filter(n=>n.kind==='concept').slice(0,128),ids=new Set(nodes.map(n=>n.id));
        return route.fulfill({json:{executed:true,kind:'graph_preview',partial:true,nodes,edges:graph.edges.filter(e=>ids.has(e.source)&&ids.has(e.target)).slice(0,512),snapshot:{protocol:'graph-preview-v1',token:'x'.repeat(43)}}});
      }
      if(state.holdGraph){state.heldGraphs.push({route,graph});return;}
      return route.fulfill({json:graph});
    }
    return route.fulfill({json:{executed:true,marker:state.revision,domains:[],unfiled:[],rows:[],counts:{kinds:{}}}});
  });
  const page=await context.newPage();page.on('pageerror',e=>allErrors.push(e.message));
  await page.goto(origin+hash,{waitUntil:'domcontentloaded'});
  const cache=()=>page.evaluate(async()=>{const c=(await import('/src/view-session-cache.ts')).viewSessionCache;return {count:c.count,bytes:c.bytes,generation:c.generation};});
  const complete=()=>page.waitForFunction(v=>window.__checks.logs.some(t=>t.startsWith(v==='2d'?'View scene ready':'View scene complete')),view,{timeout:60000});
  const cached=()=>page.waitForFunction(async()=> (await import('/src/view-session-cache.ts')).viewSessionCache.count>0,{timeout:15000});
  const search=async()=>{await page.evaluate(()=>location.hash='#search');await page.locator('#search-form').waitFor();await page.waitForFunction(()=>!document.querySelector('#view-canvas'));};
  const enter=async(target='#view')=>{
    state.requests.length=0;await page.evaluate(()=>window.__checks.logs=[]);
    if(target==='#view')await page.locator('a[href="#view"][data-rail]').first().click();else await page.evaluate(h=>location.hash=h,target);
    await complete();
  };
  const graphCount=()=>state.requests.filter(r=>r.path==='/api/graph/graph'&&!r.query.includes('stage=cancel')).length;
  const scene=()=>page.evaluate(v=>v==='3d'?{
    camera:window.__g3.cameraPosition(),target:{...window.__g3.controls().target},
    positions:window.__g3.graphData().nodes.map(n=>[n.id,n.x,n.y,n.z]),
    visibleNodes:window.__g3.graphData().nodes.filter(n=>{const f=window.__g3.nodeVisibility();return typeof f==='function'?f(n):f!==false;}).length,
  }:{camera:window.__s2.getCamera().getState(),positions:window.__s2.getGraph().nodes().map(id=>{const n=window.__s2.getGraph().getNodeAttributes(id);return [id,n.x,n.y];}),visibleNodes:window.__s2.getGraph().nodes().filter(id=>!window.__s2.getNodeDisplayData(id)?.hidden).length},view);
  return {context,page,state,cache,complete,cached,search,enter,graphCount,scene};
}
async function waitHeld(a,key){for(let i=0;i<200&&!a.state[key].length;i++)await a.page.waitForTimeout(10);assert.ok(a.state[key].length,`${key} reached`);}
try {
  for(const view of ['2d','3d']) {
    const a=await setup(view);await a.complete();await a.cached();
    // Change camera and time filter through the real renderer and UI.
    await a.page.evaluate(v=>{if(v==='2d')window.__s2.getCamera().setState({x:.35,y:.6,ratio:.65,angle:.1});else window.__g3.cameraPosition({x:600,y:450,z:1300},{x:40,y:20,z:0},0);},view);
    await a.page.locator('#view-lens').click();await a.page.locator('[data-tab="opts"]').click();
    await a.page.locator('#pref-when').evaluate(el=>{el.value='0.3';el.dispatchEvent(new Event('input',{bubbles:true}));});
    await a.page.waitForTimeout(80);const before=await a.scene();
    await a.search();await a.enter();await a.page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));const after=await a.scene();
    assert.equal(a.graphCount(),0);assert.equal(JSON.stringify(after.positions),JSON.stringify(before.positions));
    for(const key of Object.keys(before.camera)) if(typeof before.camera[key]==='number')assert.ok(Math.abs(after.camera[key]-before.camera[key])<.02,`restore ${view} ${key}`);
    if(view==='3d')for(const key of ['x','y','z'])assert.ok(Math.abs(after.target[key]-before.target[key])<.02,`restore target ${key}`);
    assert.equal(after.visibleNodes,before.visibleNodes);assert.ok(after.visibleNodes<before.positions.length,'time filter affects the displayed graph');
    assert.equal(await a.page.evaluate(()=>JSON.parse(localStorage.getItem('vinc.view.prefs')).when),.3);
    const canvasCount=await a.page.locator('#view-canvas canvas').count();
    for(let n=0;n<4;n++){await a.search();await a.enter();assert.equal(a.graphCount(),0);assert.equal(await a.page.locator('#view-canvas canvas').count(),canvasCount);}
    const bound=await a.cache();assert.ok(bound.count<=4&&bound.bytes<=32*1024*1024);assert.equal(await a.page.evaluate(()=>window.__checks.liveWorkers),0);
    rows.push({test:`${view} actual View button reuses complete coordinates, restores camera/filter, repeated cleanup`,...bound});
    // A node-set deep link must use initial framing rather than the remembered pan/zoom.
    const id=after.positions[0][0];await a.search();await a.enter(`#view/ids/${encodeURIComponent(id)}/as/${view}`);
    assert.equal(a.graphCount(),0);const linked=await a.scene();assert.notDeepEqual(linked.camera,before.camera);
    rows.push({test:`${view} explicit node-set link overrides camera bookmark`});
    await a.context.close();
  }
  {
    const a=await setup('3d','hub','#view/synthetic');await a.complete();await a.cached();
    await a.search();await a.enter();assert.equal(a.graphCount(),0);assert.ok((new URL(a.page.url())).hash.includes('/synthetic'));
    rows.push({test:'plain View button restores prior domain scope'});
    a.state.requests.length=0;await a.page.evaluate(()=>{window.__checks.logs=[];location.hash='#view';});await a.complete();await a.cached();
    assert.ok(!(new URL(a.page.url())).hash.includes('/synthetic'));assert.ok(a.graphCount()>0);
    rows.push({test:'explicit whole-graph navigation within View clears the previous domain scope'});
    await a.search();a.state.revision='epoch.2';await a.enter();assert.ok(a.graphCount()>0);
    assert.ok((await a.scene()).positions.every(n=>n[0].includes('epoch.2')));await a.cached();
    rows.push({test:'revision changed while Search forces current read'});
    await a.search();a.state.identity='b@example.invalid';await a.enter();assert.ok(a.graphCount()>0);
    assert.ok((await a.scene()).positions.every(n=>n[0].startsWith('b@example.invalid')));await a.cached();
    rows.push({test:'different authenticated account with same owner/revision cannot hit cache'});
    await a.search();await a.page.evaluate(async()=>{(await import('/src/api.ts')).cfg.team='synthetic-team';});assert.equal((await a.cache()).count,0);
    await a.enter();assert.ok(a.graphCount()>0);assert.ok((await a.scene()).positions.every(n=>n[0].includes('|synthetic-team|')));await a.cached();
    rows.push({test:'Space switch disposes entries and reads correct team'});
    for(const field of ['owner','token','base']){
      await a.search();await a.page.evaluate(async field=>{const {cfg}=await import('/src/api.ts');cfg[field]=field==='base'?cfg.base+'/':`synthetic-${field}`;},field);
      assert.equal((await a.cache()).count,0);await a.enter();assert.ok(a.graphCount()>0);await a.cached();
      rows.push({test:`Advanced ${field} change invalidates entries`});
    }
    // No valid changes check means no retained-data fallback.
    await a.search();a.state.changesStatus=503;await a.enter();assert.ok(a.graphCount()>0);assert.equal((await a.cache()).count,0);
    rows.push({test:'failed permission/revision probe never serves old cached graph'});
    a.state.changesStatus=200;await a.search();await a.enter();await a.cached();await a.search();
    a.state.changesStatus=403;a.state.graphStatus=403;await a.page.evaluate(()=>location.hash='#view');
    await a.page.waitForFunction(()=>document.querySelector('#view-msg .empty'));assert.equal(await a.page.locator('#view-canvas canvas').count(),0);assert.equal((await a.cache()).count,0);
    rows.push({test:'revoked Space permission shows no retained canvas'});
    await a.context.close();
  }
  for(const view of ['2d','3d']) {
    const a=await setup(view,'temporalRoots');await a.complete();await a.cached();const before=await a.scene();
    const workers=await a.page.evaluate(()=>window.__checks.workers);await a.search();await a.enter();assert.equal(a.graphCount(),0);
    assert.equal(await a.page.evaluate(()=>window.__checks.workers),workers);assert.equal(JSON.stringify((await a.scene()).positions),JSON.stringify(before.positions));
    rows.push({test:`${view} Roots reentry reuses worker coordinates without another worker`});
    await a.search();await a.page.evaluate(()=>{const p=JSON.parse(localStorage.getItem('vinc.view.prefs'));p.timeBasis='content';localStorage.setItem('vinc.view.prefs',JSON.stringify(p));});
    await a.enter();assert.equal(a.graphCount(),0);assert.equal(await a.page.evaluate(()=>window.__checks.workers),workers+1);await a.cached();
    rows.push({test:`${view} different Roots time basis recomputes only coordinate layout`});
    await a.search();await a.page.setViewportSize({width:1000,height:720});const next=await a.page.evaluate(()=>window.__checks.workers);await a.enter();
    assert.equal(a.graphCount(),0);assert.equal(await a.page.evaluate(()=>window.__checks.workers),next+1);
    rows.push({test:`${view} different viewport aspect ratio invalidates Roots coordinates`});
    await a.context.close();
  }
  {
    const a=await setup('3d');
    await a.page.waitForFunction(()=>window.__checks.logs.some(t=>t.startsWith('View scene ready')));await a.cached();
    await a.search();
    const saved=await a.page.evaluate(async()=>{const c=(await import('/src/view-session-cache.ts')).viewSessionCache;return [...c.entries.values()].map(e=>e.state&&JSON.parse(e.state)).find(Boolean);});
    await a.enter();const actual=await a.scene();
    for(const key of ['x','y','z'])assert.ok(Math.abs(actual.camera[key]-saved.camera3.pos[key])<.02);
    assert.equal(a.graphCount(),0);rows.push({test:'rapid 3D leave during framing restores the captured camera without re-reading graph'});
    await a.context.close();
  }
  {
    const a=await setup('2d');await a.complete();await a.cached();await a.search();
    a.state.holdChanges=true;await a.page.evaluate(()=>location.hash='#view');await waitHeld(a,'heldChanges');
    await a.page.evaluate(()=>location.hash='#search');await a.page.locator('#search-form').waitFor();
    a.state.holdChanges=false;for(const r of a.state.heldChanges.splice(0))await r.fulfill({json:{marker:a.state.revision}}).catch(()=>{});
    await a.page.waitForTimeout(180);assert.equal(await a.page.locator('#view-canvas').count(),0);assert.ok(await a.page.locator('#search-form').count());
    rows.push({test:'late entry permission response cannot replace Search'});
    // A change during a full graph snapshot must make the completed response ineligible.
    await a.page.evaluate(async()=>{(await import('/src/view-session-cache.ts')).viewSessionCache.clear();});
    a.state.holdGraph=true;await a.page.evaluate(()=>location.hash='#view');await waitHeld(a,'heldGraphs');
    a.state.revision='epoch.2';a.state.holdGraph=false;for(const h of a.state.heldGraphs.splice(0))await h.route.fulfill({json:h.graph});
    await a.complete();await a.page.waitForTimeout(120);assert.equal((await a.cache()).count,0);
    await a.search();await a.enter();assert.ok(a.graphCount()>0);await a.cached();
    rows.push({test:'revision changed during graph snapshot prevents retaining obsolete response'});
    await a.search();await a.page.evaluate(async()=>{(await import('/src/view-session-cache.ts')).viewSessionCache.clear();});
    a.state.holdGraph=true;await a.page.evaluate(()=>{window.__checks.logs=[];location.hash='#view';});await waitHeld(a,'heldGraphs');
    await a.search();a.state.holdGraph=false;
    for(const h of a.state.heldGraphs.splice(0))await h.route.fulfill({json:h.graph}).catch(()=>{});
    await a.page.waitForTimeout(100);assert.equal(await a.page.locator('#view-canvas').count(),0);assert.equal((await a.cache()).count,0);
    rows.push({test:'leaving during graph read aborts fetch and ignores late full response'});
    // With nothing to reuse the first graph read starts without waiting for the marker probe. The
    // read is labelled by the marker its response carries, and kept only when the same marker is
    // confirmed afterwards.
    await a.search();await a.page.evaluate(async()=>{(await import('/src/view-session-cache.ts')).viewSessionCache.clear();});
    a.state.requests.length=0;a.state.holdChanges=true;await a.page.evaluate(()=>location.hash='#view');await waitHeld(a,'heldChanges');
    for(let i=0;i<100&&!a.graphCount();i++)await a.page.waitForTimeout(20);
    assert.ok(a.graphCount()>0,'the graph read starts while the marker probe is still pending');
    assert.equal((await a.cache()).count,0,'nothing is retained before the marker is confirmed');
    a.state.holdChanges=false;for(const r of a.state.heldChanges.splice(0))await r.fulfill({json:{executed:true,marker:a.state.revision}}).catch(()=>{});
    await a.complete();await a.cached();rows.push({test:'first graph read starts before the marker probe and is retained under its response marker'});
    // A server that does not label the read (older hub) gives nothing to retain under.
    await a.search();await a.page.evaluate(async()=>{(await import('/src/view-session-cache.ts')).viewSessionCache.clear();});
    a.state.unlabelled=true;await a.enter();await a.page.waitForTimeout(250);
    assert.equal((await a.cache()).count,0,'an unlabelled read is not retained');a.state.unlabelled=false;
    rows.push({test:'a read without a marker label is displayed but not retained'});
    for(const event of ['vinc:signin-required','vinc:write','pagehide']){
      const g=(await a.cache()).generation;await a.page.evaluate(event=>window.dispatchEvent(new Event(event)),event);
      assert.equal((await a.cache()).count,0);assert.ok((await a.cache()).generation>g);
    }
    rows.push({test:'sign-in, write and page disposal clear retained entries'});
    await a.context.close();
  }
  {
    const a=await setup('2d');await a.complete();await a.cached();
    await a.page.evaluate(()=>window.__s2.getCamera().setState({x:.3,y:.4,ratio:.6,angle:.1}));
    await a.page.locator('#view-lens').click();await a.page.locator('[data-tab="opts"]').click();
    await a.page.evaluate(()=>window.__checks.holdWorker=true);
    await a.page.locator('[data-pref="layout"][data-val="temporalRoots"]').click();
    await a.page.waitForFunction(()=>window.__checks.heldWorkers.length>0);
    await a.search();assert.equal(await a.page.evaluate(()=>window.__checks.liveWorkers),0);
    const saved=await a.page.evaluate(async()=>{const c=(await import('/src/view-session-cache.ts')).viewSessionCache;return [...c.entries.values()].map(e=>e.state&&JSON.parse(e.state));});
    assert.ok(saved.filter(Boolean).length>0,'displayed scene camera was saved');
    assert.ok(saved.filter(Boolean).every(s=>JSON.parse(s.prefs)[0].layout==='hub'));
    rows.push({test:'leaving during Roots calculation saves displayed scene camera under original preferences and terminates worker'});
    await a.context.close();
  }
  assert.deepEqual(allErrors,[]);
  await writeFile(output,JSON.stringify({rows,errors:allErrors},null,2));console.log(JSON.stringify({passed:rows.length,errors:allErrors}));
}finally{await browser.close();await server.close();}
