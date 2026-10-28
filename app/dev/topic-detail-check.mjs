// VINC_TEST_MODULES=<package exposing playwright> node dev/topic-detail-check.mjs
// 실제 앱과 렌더러를 격리 브라우저에서 실행한다. 설명 쓰기도 합성 응답이며 외부 요청을 차단한다.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {createServer} from 'vite';
import {createServer as portServer} from 'node:net';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const {chromium}=createRequire(process.env.VINC_TEST_MODULES||resolve(root,'package.json'))('playwright');
const output=resolve(root,'../_scratch/topic-detail-browser');
process.env.VINC_SIDECAR='http://127.0.0.1:1';
const probe=portServer();await new Promise(r=>probe.listen(0,'127.0.0.1',r));
const freePort=probe.address().port;await new Promise(r=>probe.close(r));
const server=await createServer({root,configFile:resolve(root,'vite.config.ts'),
  cacheDir:resolve(root,'node_modules/.vite-topic-detail-check'),server:{host:'0.0.0.0',port:freePort,strictPort:true}});
await server.listen();
const port=server.httpServer.address().port;
const browser=await chromium.launch({channel:'msedge',headless:true,args:['--enable-webgl','--ignore-gpu-blocklist']});
const rows=[],errors=[];
const nodes=[['domain:project','Project','domain','project',0,0],
  ['domain:project/child','Child','domain','project/child',140,60],
  ['concept:member','Member','concept','project/child',280,0],
  ['concept:empty','Empty node','concept','project/child',280,100]].map(([id,label,kind,domain,x,y])=>
  ({id,label,kind,domain,x,y,z:0,x2:x,y2:y,degree:0,ppr:.25,ts:'2026-10-13T00:00:00Z'}));
const emptyDescription=()=>({executed:true,status:'ok',exists:false,text:'',content_hash:''});
const topic=(path,description=emptyDescription())=>({executed:true,status:'ok',node_kind:'domain',title:path.split('/').at(-1),topic:{
  description,
  path,parent:path.includes('/')?{id:'domain:project',title:'project',domain:'project'}:null,
  child_topics:path==='project'?1:0,direct_nodes:path==='project'?0:2,
  subtree_nodes:2,descendant_nodes:path==='project'?2:0,structure:{total:path==='project'?1:2,
    offset:0,truncated:false,items:path==='project'?
      [{rel:'PARENT_OF',id:'domain:project/child',title:'project/child',domain:'project'}]:
      ['member','empty'].map(n=>({rel:'IN_DOMAIN',id:'concept:'+n,title:n==='member'?'Member':'Empty node',domain:path}))}}});
const ordinary=id=>({executed:true,title:id==='concept:member'?'Member':'Empty node',node_kind:'concept',props:{},
  excerpts:[],links:{executed:true,total:0,degree:0,out:0,in:0,attached:0,items:[],truncated:false}});
try{
  await mkdir(output,{recursive:true});
  for(const [mode,edges,view] of [['local','none','3d'],['local','always','3d'],
    ['web','none','3d'],['web','always','3d']]){
    const origin=`http://${mode==='web'?'127.0.0.2':'127.0.0.1'}:${port}`;
    console.log(`Checking ${mode} ${edges} ${view}`);
    const context=await browser.newContext({viewport:{width:1440,height:1000}});
    await context.addInitScript(({edges,view})=>{
      Object.defineProperty(document,'cookie',{get:()=> '__Host-csrf=synthetic'});
      localStorage.setItem('vinc.owner','user:synthetic');localStorage.setItem('vinc.mode','local');
      localStorage.setItem('vinc.lang','en');localStorage.setItem('vinc.view.coach.seen','1');
      localStorage.setItem('vinc.view.prefs',JSON.stringify({pv:2,view,layout:'hub',edges,labels:'always'}));
    },{edges,view});
    let responseMode='normal',pending=[],requests=[],descriptionMode='normal',pendingSaves=[];
    const descriptions=new Map();
    await context.route('**/*',async route=>{
      const req=route.request(),u=new URL(req.url());
      if(u.origin!==origin) {errors.push('Blocked external request');return route.abort();}
      if(u.pathname==='/assets/gnb.js')return route.fulfill({body:'',contentType:'text/javascript'});
      if(!['fetch','xhr'].includes(req.resourceType()))return route.continue();
      const path=u.pathname.replace(/^\/api\/graph/,'');
      assert.ok(req.method()==='GET'||path==='/topic-description','Only synthetic description writes are allowed');
      requests.push({path,query:u.search});
      if(path==='/topic-description'){
        const body=req.postDataJSON(),key=body.node;
        if(mode==='web')assert.equal(req.headers()['x-csrf-token'],'synthetic');
        assert.equal(typeof body.expected_content_hash,'string');
        const current=descriptions.get(key)||emptyDescription();
        if(descriptionMode==='conflict'){
          descriptions.set(key,{...current,exists:true,text:'Another editor saved this',content_hash:'other-editor-hash'});
          return route.fulfill({json:{executed:false,status:'conflict'}});
        }
        assert.equal(body.expected_content_hash,current.content_hash);
        const saved={executed:true,status:'ok',exists:true,text:body.text,
          content_hash:createHash('sha256').update(body.text).digest('hex')};
        descriptions.set(key,saved);
        if(descriptionMode==='lost')return route.fulfill({status:502,json:{error:'synthetic lost response'}});
        if(descriptionMode==='hold'){pendingSaves.push({route,json:saved});return;}
        return route.fulfill({json:saved});
      }
      let json={executed:true,marker:'fixture',rows:[],domains:[],unfiled:[],docs:[],counts:{kinds:{}}};
      if(u.pathname==='/api/me')json={user:{email:'synthetic@example.invalid',name:'Synthetic'}};
      if(u.pathname==='/api/teams')json={teams:[]};
      if(path==='/graph')json={executed:true,marker:'fixture',nodes,edges:[],topicEdges:[
        {source:'domain:project',target:'domain:project/child',type:'PARENT_OF',presentation:true}]};
      if(path==='/node-context'){
        const id=u.searchParams.get('node');json=id.startsWith('domain:')?topic(id.slice(7),descriptions.get(id)):ordinary(id);
        if(responseMode==='hold'&&id==='domain:project'){pending.push({route,json});return;}
        if(responseMode==='failure')return route.fulfill({status:502,json:{error:'synthetic outage'}});
        if(responseMode==='envelope')json={executed:false,status:'query_failed',error:'synthetic outage'};
        if(responseMode==='unavailable')json={executed:false,status:'unavailable',found:false};
        if(responseMode==='unsupported')json={executed:false,status:'unsupported_kind'};
      }
      await route.fulfill({json});
    });
    const page=await context.newPage();page.setDefaultTimeout(45000);
    page.on('pageerror',error=>errors.push(error.message));
    await page.goto(origin+'/#view/trace/domain%3Aproject/as/'+view,{waitUntil:'domcontentloaded',timeout:120000});
    const card=page.locator('.float-card'),pane=page.locator('#view-detail');
    await card.getByText('Full path',{exact:true}).waitFor();
    const pick=async id=>{
      await page.evaluate(({id})=>{
        const g=window.__g3;g.onNodeClick()(g.graphData().nodes.find(n=>n.id===id));
      },{id,view});
    };
    await card.getByText('No description yet.',{exact:true}).waitFor();
    await card.locator('[data-topic-description-edit]').click();
    const input=card.locator('[data-topic-description-input]');
    await input.fill('Written description <script>safe text</script>\n한국어 설명');
    await card.locator('[data-topic-description-save]').click();
    await card.getByText('Written description <script>safe text</script> 한국어 설명',{exact:true}).waitFor();
    assert.equal(await card.locator('script').count(),0);
    await card.locator('[data-topic-description-edit]').click();
    await input.fill('Uncertain submitted description');descriptionMode='lost';
    await card.locator('[data-topic-description-save]').click();
    await card.getByText("Saving couldn't be confirmed. Your text is kept. Check the saved version before trying again.",{exact:true}).waitFor();
    assert.equal(await input.inputValue(),'Uncertain submitted description');
    assert.equal(await card.locator('[data-topic-description-save]').isDisabled(),true);
    descriptionMode='normal';await card.locator('[data-topic-description-check]').click();
    await card.getByText('Uncertain submitted description',{exact:true}).waitFor();
    await card.locator('[data-topic-description-edit]').click();await input.fill('My conflicting draft');
    descriptionMode='conflict';await card.locator('[data-topic-description-save]').click();
    await card.getByText('The saved description changed. Your text is kept. Check the saved version before editing again.',{exact:true}).waitFor();
    assert.equal(await input.inputValue(),'My conflicting draft');
    descriptionMode='normal';await card.locator('[data-topic-description-check]').click();
    await card.locator('[data-topic-description-input]').waitFor();
    assert.equal(await input.inputValue(),'My conflicting draft');
    await card.locator('[data-topic-description-cancel]').click();
    await card.getByText('Another editor saved this',{exact:true}).waitFor();
    await card.locator('[data-topic-description-edit]').click();await input.fill('Delayed root save');
    descriptionMode='hold';await card.locator('[data-topic-description-save]').click();
    await page.waitForFunction(()=>document.querySelector('[data-topic-description-status]')?.textContent==='Saving…');
    await pick('domain:project/child');await card.getByText('No description yet.',{exact:true}).waitFor();
    assert.ok(pendingSaves.length);
    for(const held of pendingSaves)await held.route.fulfill({json:held.json});pendingSaves=[];
    await page.waitForTimeout(100);
    assert.equal(await card.getByText('Delayed root save',{exact:true}).count(),0);
    descriptionMode='normal';await pick('domain:project');
    await card.getByText('Delayed root save',{exact:true}).waitFor();
    await card.locator('[data-topic-go="domain:project/child"]').click();
    await card.locator('[data-topic-go="concept:member"]').waitFor();
    assert.match(await card.innerText(),/Parent topic/i);
    await card.locator('[data-topic-go="domain:project"]').last().click();
    await card.getByText('Top level topic',{exact:true}).waitFor();
    await card.locator('[data-detail-back]').click();
    await card.locator('[data-topic-go="concept:member"]').waitFor();
    await card.locator('[data-topic-go="concept:member"]').click();
    await card.locator('#c-links').click();
    await card.getByText('No links yet.',{exact:true}).waitFor();
    await card.locator('[data-detail-back]').click();
    await card.locator('[data-topic-open]').click();
    await pane.locator('[data-topic-go="concept:member"]').waitFor();
    await pane.locator('[data-topic-go="concept:member"]').click();
    await pane.locator('#node-stats').getByText('0',{exact:true}).waitFor();
    await pane.locator('[data-detail-back]').click();
    await pane.locator('[data-topic-go="concept:member"]').waitFor();
    await page.screenshot({path:resolve(output,`${mode}-${edges}-${view}.png`)});
    responseMode='hold';
    await pick('domain:project');await card.locator('[data-topic-open]').click();
    await page.waitForFunction(()=>document.querySelector('[data-topic-body]')?.textContent.includes('Loading details'));
    await pick('domain:project/child');await card.locator('[data-topic-open]').click();
    await pane.locator('[data-topic-go="concept:member"]').waitFor();
    assert.ok(pending.length);
    for(const held of pending)await held.route.fulfill({json:held.json});pending=[];
    await page.waitForTimeout(100);
    assert.equal(await pane.locator('h2').textContent(),'Child');
    assert.equal(await pane.getByText('Top level topic',{exact:true}).count(),0);
    for(const [state,message] of [['failure',"Couldn't load details. Try again."],
      ['envelope',"Couldn't load details. Try again."],
      ['unavailable',"This item is unavailable in this space or you don't have access."],
      ['unsupported',"Details aren't supported for this kind of item yet."]]){
      responseMode=state;await pick('domain:project');await card.getByText(message,{exact:true}).waitFor();
      assert.equal(await card.getByText('No links yet.',{exact:true}).count(),0);
      assert.equal(await card.getByText('0',{exact:true}).count(),0);
      await pick('domain:project/child');
    }
    responseMode='failure';await pick('domain:project');
    await card.locator('[data-detail-retry]').waitFor();responseMode='normal';
    await card.locator('[data-detail-retry]').click();await card.getByText('Top level topic',{exact:true}).waitFor();
    responseMode='hold';await pick('domain:project/child');await pick('domain:project');
    await card.getByText('Loading details…',{exact:true}).waitFor();
    const before=await page.evaluate(async mode=>{
      const {cfg,viewScope}=await import('/src/api.ts');const owner=cfg.owner,key=viewScope().key;
      if(mode==='web')cfg.team='different-fixture-team';else cfg.owner='different-fixture-owner';
      return {owner,key,changed:viewScope().key};
    },mode);
    assert.notEqual(before.key,before.changed,'the fixture must change the effective scope');
    for(const held of pending)await held.route.fulfill({json:held.json});pending=[];
    await page.waitForTimeout(100);
    assert.equal(await card.getByText('Top level topic',{exact:true}).count(),0,'old space must not populate this card');
    await page.evaluate(async owner=>{const {cfg}=await import('/src/api.ts');cfg.team='';cfg.owner=owner;},before.owner);
    responseMode='normal';await pick('domain:project/child');await pick('domain:project');
    await card.getByText('Top level topic',{exact:true}).waitFor();
    assert.equal(await page.evaluate(()=>JSON.parse(localStorage.getItem('vinc.view.prefs')).edges),edges);
    assert.ok(requests.some(r=>r.path==='/node-context'));
    rows.push({mode,edges,view,passed:['parent child member navigation','card and pane back',
      'ordinary zero links','stale response guard','space change response guard','failures and access states','retry','edge preference preserved',
      'description save and escaping','lost save response reconciliation','conflict draft retained','late save selection guard']});
    await context.close();
  }
  const shell=`http://tauri.localhost:${port}`,context=await browser.newContext();
  await context.addInitScript(()=>{
    localStorage.setItem('vinc.mode','account');localStorage.setItem('vinc.sync.key','synthetic-member-key');
  });
  let proxy;
  await context.route('**/*',async route=>{
    const u=new URL(route.request().url());
    if(u.origin===shell){
      if(u.pathname==='/')return route.fulfill({contentType:'text/html',body:'<!doctype html><title>Desktop API fixture</title>'});
      return route.continue();
    }
    if(u.href==='http://127.0.0.1:8787/space/proxy'){
      const headers={'access-control-allow-origin':shell,'access-control-allow-headers':'*'};
      if(route.request().method()==='OPTIONS')return route.fulfill({status:204,headers});
      proxy=route.request().postDataJSON();
      return route.fulfill({json:proxy.method==='GET'?topic('project'):
        {executed:true,status:'ok',text:proxy.body.text,content_hash:'synthetic-hash'},headers});
    }
    errors.push('Blocked external desktop request');return route.abort();
  });
  const shellPage=await context.newPage();await shellPage.goto(shell+'/');
  const read=await shellPage.evaluate(async()=>{
    const {api}=await import('/src/api.ts');
    return api.nodeContext('user:synthetic','domain:project',4,{team:'synthetic-team'},24);
  });
  assert.equal(read.topic.path,'project');assert.equal(proxy.path,'/node-context');
  assert.equal(proxy.team,'synthetic-team');assert.equal(proxy.params.links_offset,'24');
  const saved=await shellPage.evaluate(async()=>{
    const {api}=await import('/src/api.ts');
    return api.topicDescription('user:synthetic','domain:project','Desktop description','',{team:'synthetic-team'});
  });
  assert.equal(saved.text,'Desktop description');assert.equal(proxy.path,'/topic-description');
  assert.equal(proxy.method,'POST');assert.equal(proxy.team,'synthetic-team');assert.equal(proxy.body.expected_content_hash,'');
  rows.push({mode:'desktop account proxy',passed:['captured team','GET node context','continuation offset','scoped description write']});
  await context.close();
  assert.deepEqual(errors,[]);
  await writeFile(resolve(output,'results.json'),JSON.stringify({rows,errors},null,2));
  console.log(JSON.stringify({passed:rows.length,errors}));
}finally{await browser.close();await server.close();}
