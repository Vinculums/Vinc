// node app/dev/view-layout-stored-check.mjs APP_DIR OUTPUT_JSON
// Synthetic loopback APIs only. Uses the actual app and renderer, plus its local Vite proxy.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {createServer as httpServer} from 'node:http';
import {writeFile, mkdir} from 'node:fs/promises';
import {resolve, dirname} from 'node:path';
const app=resolve(process.argv[2]), output=resolve(process.argv[3]);
const require=createRequire(resolve(app,'package.json'));
const pw=createRequire(process.env.VINC_TEST_MODULES||resolve(app,'package.json'));
const {createServer}=require('vite'), {chromium}=pw('playwright');
const fixture={executed:true,marker:'epoch.1',nodes:Array.from({length:8},(_,i)=>({
  id:`concept:n${i}`,label:`Synthetic ${i}`,kind:'concept',domain:`vinc/topic${i%2}`,
  degree:2,ppr:1/8,ts:`2026-10-${String(i+1).padStart(2,'0')}T00:00:00Z`,
  x:i*20,y:(i%2)*50,z:i*10,x2:i*20,y2:(i%2)*50,
})),edges:[{source:'concept:n0',target:'concept:n1',type:'RELATED_TO'}],topicEdges:[]};
const stored={executed:true,state:'ready',marker:'epoch.1',nodes:8,answer:{
  nodes:fixture.nodes.map((n,i)=>[n.id,i*200,(i%2)*400,i*50,i*200,(i%2)*400]),
  topics:[],edges:[['concept:n0\u0000concept:n1\u0000RELATED_TO\u00000',null]],topicEdges:[],
}};
const localCalls=[];
const sidecar=httpServer((req,res)=>{
  localCalls.push(req.url);res.setHeader('Content-Type','application/json');
  res.end(JSON.stringify(stored));
});
await new Promise(r=>sidecar.listen(0,'127.0.0.1',r));
process.env.VINC_SIDECAR=`http://127.0.0.1:${sidecar.address().port}`;
process.chdir(app);
const server=await createServer({root:app,configFile:resolve(app,'vite.config.ts'),server:{host:'0.0.0.0',port:0}});
await server.listen();const port=server.httpServer.address().port, origin=`http://127.0.0.2:${port}`;
const browser=await chromium.launch({channel:'msedge',headless:true,args:['--enable-webgl','--ignore-gpu-blocklist']});
const rows=[],errors=[];
await mkdir(dirname(output),{recursive:true});
try{
  for(const [mode,view] of [['ready','3d'],['mismatch','3d'],['off','3d']]){
    const context=await browser.newContext({viewport:{width:1440,height:900}});
    await context.addInitScript(({view})=>{
      Object.defineProperty(document,'cookie',{get:()=> '__Host-csrf=synthetic'});
      localStorage.setItem('vinc.owner','user:synthetic');localStorage.setItem('vinc.lang','en');
      localStorage.setItem('vinc.theme','dark');localStorage.setItem('vinc.view.coach.seen','1');
      localStorage.setItem('vinc.view.prefs',JSON.stringify({pv:2,view,layout:'temporalRoots',edges:'none'}));
      window.__checks={workers:0,logs:[]};
      const info=console.info;console.info=(...a)=>{window.__checks.logs.push(String(a[0]));info(...a);};
      const W=window.Worker;
      window.Worker=class extends W{
        constructor(url,...rest){super(url,...rest);this.layout=String(url).includes('view-layout.worker');}
        postMessage(...a){if(!this.layout)return super.postMessage(...a);
          window.__checks.workers++;setTimeout(()=>this.onerror?.({message:'synthetic worker failure'}),20);}
      };
    },{view});
    const requests=[];
    await context.route('**/*',async route=>{
      const u=new URL(route.request().url());
      if(u.origin!==origin){errors.push(`Blocked external request to ${u.origin}`);return route.abort();}
      if(u.pathname==='/assets/gnb.js')return route.fulfill({body:'',contentType:'text/javascript'});
      if(!u.pathname.startsWith('/api/'))return route.continue();
      requests.push(u.pathname);
      if(u.pathname==='/api/me')return route.fulfill({json:{user:{email:'synthetic@example.invalid',name:'Synthetic'}}});
      if(u.pathname==='/api/teams')return route.fulfill({json:{teams:[]}});
      if(u.pathname==='/api/graph/graph/roots'){
        assert.equal(u.searchParams.get('basis'),'recorded');
        const value=mode==='off'?{executed:true,state:'off'}:{...stored,marker:mode==='mismatch'?'epoch.0':'epoch.1'};
        return route.fulfill({json:value});
      }
      if(u.pathname==='/api/graph/graph')return route.fulfill({json:fixture});
      return route.fulfill({json:{executed:true,marker:'epoch.1',domains:[],unfiled:[],rows:[],counts:{kinds:{}}}});
    });
    const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
    await page.goto(origin+'/#view',{waitUntil:'domcontentloaded'});
    if(mode==='ready'){
      await page.waitForFunction(()=>window.__checks.logs.some(l=>l==='View layout loaded from hub'));
      if(view==='3d')await page.waitForFunction(()=>window.__g3?.graphData()?.nodes.length===8);
      else await page.waitForFunction(()=>[...document.querySelectorAll('canvas')].some(c=>c.width>0));
      assert.equal(await page.evaluate(()=>window.__checks.workers),0);
      assert.equal(await page.locator('[data-layout-note]').count(),0);
      if(view==='3d')await page.screenshot({path:output.replace(/\.json$/,'.png')});
    }else{
      await page.locator('[data-layout-note]').waitFor();
      assert.equal(await page.evaluate(()=>window.__checks.workers),1);
      assert.match(await page.locator('[data-layout-note]').textContent(),/couldn't be laid out/);
    }
    assert.ok(requests.includes('/api/graph/graph/roots'));
    assert.equal(await page.locator('#view-msg .empty').count(),0);
    rows.push({mode,view,workers:await page.evaluate(()=>window.__checks.workers)});
    await context.close();
  }
  // An actual loopback sidecar behind the app's Vite proxy, rather than a route interception.
  const context=await browser.newContext(),page=await context.newPage();
  const local=`http://127.0.0.1:${port}`;
  await page.route(local+'/',r=>r.fulfill({body:'<!doctype html><title>Local API check</title>',contentType:'text/html'}));
  await page.goto(local+'/');
  const r=await page.evaluate(async()=>{
    const {api,cfg}=await import('/src/api.ts');cfg.owner='tenant:synthetic';
    return api.graphRoots(cfg.owner,'content',new AbortController().signal);
  });
  assert.equal(r.state,'ready');assert.ok(localCalls.some(p=>p.startsWith('/graph/roots?')&&p.includes('basis=content')));
  rows.push({mode:'local Vite proxy',state:r.state});await context.close();
  // Desktop account mode uses the sidecar's member-key proxy with the captured team.
  const shell=`http://tauri.localhost:${port}`;
  const shellContext=await browser.newContext();
  await shellContext.addInitScript(()=>{
    localStorage.setItem('vinc.mode','account');
    localStorage.setItem('vinc.sync.key','synthetic-member-key');
  });
  await shellContext.route(shell+'/',r=>r.fulfill({body:'<!doctype html><title>Account API check</title>',contentType:'text/html'}));
  let proxied;
  await shellContext.route('http://127.0.0.1:8787/space/proxy',async route=>{
    if(route.request().method()==='OPTIONS')return route.fulfill({status:204,headers:{
      'access-control-allow-origin':shell,'access-control-allow-headers':'*'}});
    proxied=route.request().postDataJSON();
    return route.fulfill({json:stored,headers:{'access-control-allow-origin':shell}});
  });
  const shellPage=await shellContext.newPage();await shellPage.goto(shell+'/');
  const accountResult=await shellPage.evaluate(async()=>{
    const {api}=await import('/src/api.ts');
    return api.graphRoots('user:synthetic','recorded',new AbortController().signal,{team:'synthetic-team'});
  });
  assert.equal(accountResult.state,'ready');
  assert.equal(proxied.path,'/graph/roots');assert.equal(proxied.method,'GET');
  assert.equal(proxied.params.basis,'recorded');assert.equal(proxied.team,'synthetic-team');
  assert.equal(proxied.token,'synthetic-member-key');
  rows.push({mode:'desktop account proxy',state:accountResult.state});await shellContext.close();
  assert.deepEqual(errors,[]);await writeFile(output,JSON.stringify({rows,errors},null,2));
  console.log(JSON.stringify({passed:rows.length,errors}));
}finally{await browser.close();await server.close();await new Promise(r=>sidecar.close(r));}
