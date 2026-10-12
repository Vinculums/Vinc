// Run: node app/dev/view-retained-check.mjs APP_DIR SYNTHETIC_GRAPH_JSON OUTPUT_JSON
// Playwright must resolve from app/package.json or the package.json given by VINC_TEST_MODULES.
// Every API is synthetic and every non-loopback request is blocked. No user data is read.
// Drives the same two calls the Space picker makes (retainBeforeSwitch, then team + reload).
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
const state={identity:'a@example.invalid',revision:{personal:'epoch.1',team:'epoch.1'},requests:[]};
try {
  const context=await browser.newContext({viewport:{width:1440,height:900}});
  await context.addInitScript(()=>{
    Object.defineProperty(document,'cookie',{get:()=> '__Host-csrf=synthetic'});
    localStorage.setItem('vinc.owner','user:synthetic-view-loading');localStorage.setItem('vinc.lang','en');
    localStorage.setItem('vinc.theme','dark');localStorage.setItem('vinc.view.coach.seen','1');
    localStorage.setItem('vinc.view.prefs',JSON.stringify({pv:2,view:'3d',layout:'temporalRoots',edges:'none'}));
    window.__checks={logs:[],workers:0};
    const info=console.info;console.info=function(...a){if(String(a[0]).startsWith('View '))window.__checks.logs.push(String(a[0]));info.apply(this,a);};
    const W=window.Worker;window.Worker=class extends W{constructor(...a){super(...a);window.__checks.workers++;}};
  });
  const graphFor=url=>{
    const scope=[url.searchParams.get('team')||'personal',state.identity].join('|'),id=x=>`${scope}:${x}`;
    return {...fixture,nodes:fixture.nodes.map(n=>({...n,id:id(n.id)})),edges:fixture.edges.map(e=>({...e,source:id(e.source),target:id(e.target)})),topicEdges:[]};
  };
  await context.route('**/*',async route=>{
    const req=route.request(),url=new URL(req.url());
    if(url.origin!==origin){errors.push(`Blocked ${url.origin}`);return route.abort();}
    if(url.pathname==='/assets/gnb.js')return route.fulfill({body:'',contentType:'text/javascript'});
    if(!url.pathname.startsWith('/api/'))return route.continue();
    state.requests.push({path:url.pathname,query:url.search});
    const space=url.searchParams.get('team')?'team':'personal';
    if(url.pathname==='/api/me')return route.fulfill({json:{user:{email:state.identity,name:'Synthetic',teams:[]}}});
    if(url.pathname==='/api/teams')return route.fulfill({json:{teams:[{id:'synthetic-team',name:'Synthetic',role:'owner',status:'active'}]}});
    if(url.pathname==='/api/graph/changes')return route.fulfill({json:{executed:true,marker:state.revision[space]}});
    if(url.pathname==='/api/graph/graph'){
      if(url.searchParams.get('stage')==='cancel')return route.fulfill({json:{executed:true,cancelled:true}});
      return route.fulfill({json:graphFor(url)});
    }
    return route.fulfill({json:{executed:true,marker:'m',domains:[],unfiled:[],rows:[],counts:{kinds:{}}}});
  });
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  const sceneDone=()=>page.waitForFunction(()=>window.__checks.logs.some(t=>t.startsWith('View scene complete')),null,{timeout:60000});
  const cached=()=>page.waitForFunction(async()=>(await import('/src/view-session-cache.ts')).viewSessionCache.count>0,null,{timeout:30000});
  const graphReads=()=>state.requests.filter(r=>r.path==='/api/graph/graph'&&!r.query.includes('stage=cancel')).length;
  const logs=()=>page.evaluate(()=>window.__checks.logs);
  const records=()=>page.evaluate(()=>new Promise(r=>{const q=indexedDB.open('vinc-view-retained',1);
    q.onupgradeneeded=()=>q.result.createObjectStore('records',{keyPath:'id'});
    q.onsuccess=()=>{const t=q.result.transaction('records').objectStore('records').getAll();t.onsuccess=()=>r(t.result.map(x=>({id:x.id,revision:x.revision,entries:x.entries.length})));};}));
  // The Space picker: retain the Space being left, then switch and reload.
  const switchTo=async team=>{
    state.requests.length=0;
    const kept=await page.evaluate(async t=>{
      const r=await import('/src/view-retained.ts'),a=await import('/src/api.ts');
      const ok=await r.retainBeforeSwitch();a.cfg.team=t;return ok;
    },team);
    await Promise.all([page.waitForNavigation({waitUntil:'domcontentloaded'}),page.evaluate(()=>location.reload())]);
    await sceneDone();
    return kept;
  };
  const open=async(url=origin+'/#view')=>{state.requests.length=0;await page.goto(url,{waitUntil:'domcontentloaded'});await sceneDone();};

  // 1. personal -> team -> personal restores the personal graph and its Roots layout.
  await open();await cached();
  assert.ok(graphReads()>=1,'cold personal load reads the graph');
  assert.equal(await switchTo('synthetic-team'),true,'the left Space is retained');
  assert.equal((await records()).length,1);
  await cached();
  assert.ok(graphReads()>=1,'a Space opened for the first time is read, not restored');
  rows.push({test:'first visit to the other Space is cold and the left Space is stored'});
  assert.equal(await switchTo(''),true);
  assert.equal(graphReads(),0,'returning to the first Space reads no graph');
  const log1=await logs();
  assert.ok(log1.some(t=>t.startsWith('View graph read')&&t.includes('"cached":true')),'served from the restored entry');
  assert.ok(log1.some(t=>t==='View layout reused'),'the Roots layout was restored');
  assert.equal(await page.evaluate(()=>window.__checks.workers),0,'no layout worker on the restored load');
  assert.ok(await page.evaluate(()=>window.__g3.graphData().nodes.filter(n=>n.kind!=='domain').every(n=>n.id.startsWith('personal|'))),'only personal nodes');
  rows.push({test:'returning restores the graph and the Roots layout without a read or a worker'});
  assert.equal((await records()).filter(r=>r.entries>0).length,1,'the personal record was consumed; only the team Space remains');

  // 2. a changed marker discards the stored copy and reads again.
  await cached();
  assert.equal(await switchTo('synthetic-team'),true);await cached();
  state.revision.personal='epoch.2';
  assert.equal(await switchTo(''),true);
  assert.ok(graphReads()>=1,'a marker change forces a fresh read');
  rows.push({test:'a changed marker is not restored'});

  // 3. another identity never receives the stored copy.
  await cached();
  assert.equal(await switchTo('synthetic-team'),true);await cached();
  state.identity='b@example.invalid';
  assert.equal(await switchTo(''),true);
  assert.ok(graphReads()>=1,'another identity reads its own graph');
  assert.ok(await page.evaluate(()=>window.__g3.graphData().nodes.filter(n=>n.kind!=='domain').every(n=>n.id.includes('b@example.invalid'))));
  rows.push({test:'another identity is not served the stored copy'});

  // 4. losing the sign-in removes every stored copy.
  await cached();
  assert.equal(await switchTo('synthetic-team'),true);
  assert.ok((await records()).length>=1);
  await page.evaluate(()=>window.dispatchEvent(new Event('vinc:signin-required')));
  await page.waitForTimeout(400);
  assert.equal((await records()).length,0,'purge empties the store');
  assert.equal(await page.evaluate(()=>localStorage.getItem('vinc.view.retained.until')),null);
  rows.push({test:'sign-in required purges the stored copies and the marker'});

  // 5. ordinary reads and reloads write nothing.
  await open();await cached();
  await page.reload({waitUntil:'domcontentloaded'});await sceneDone();
  assert.equal((await records()).length,0,'a plain reload stores nothing');
  rows.push({test:'a plain reload does not write a stored copy'});

  assert.deepEqual(errors,[]);
  await writeFile(output,JSON.stringify({rows,errors},null,2));console.log(JSON.stringify({passed:rows.length,errors}));
} finally {await browser.close();await server.close();}
