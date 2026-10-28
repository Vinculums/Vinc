import assert from 'node:assert/strict';
import {test, beforeEach} from 'node:test';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';

const url = code => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
const js = async name => ts.transpileModule(await readFile(new URL(`../src/${name}`, import.meta.url), 'utf8'),
  {compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
globalThis.localStorage = {getItem:()=>null, setItem(){}};
const modules = {
  './api': url(`export const WEB=false;
    export const viewScope=()=>globalThis.__loader.scope;
    export const viewAccess=async()=>null;
    export const api={
      graphStage: (...a)=>globalThis.__loader.read(...a),
      graphRoots: (...a)=>globalThis.__loader.roots(...a),
    };`),
  './view-session-cache': url(await js('view-session-cache.ts')),
  './view-layout-client': url(`export const layoutInWorker=(...a)=>globalThis.__loader.worker(...a);`),
  './view-layout-wire': url((await js('view-layout-wire.ts')).replace('"./spotlight"', JSON.stringify(url(await js('spotlight.ts'))))
    .replace('"./node-topic"', JSON.stringify(url(await js('node-topic.ts'))))),
};
const prefsUrl = url(await js('view-prefs.ts'));
modules['./view-graph-read'] = url((await js('view-graph-read.ts')).replace('"./view-prefs"', JSON.stringify(prefsUrl)));
modules['./view-graph-loading'] = url(await js('view-graph-loading.ts'));
let code = await js('view-graph-loader.ts');
for (const [name, replacement] of Object.entries(modules)) code = code.replace(JSON.stringify(name), JSON.stringify(replacement));
const {createGraphLoader, withoutRetiredPages} = await import(url(code));
const {viewSessionCache} = await import(modules['./view-session-cache']);
const {edgeKeys} = await import(modules['./view-layout-wire']);
const raw = {executed:true, marker:'epoch.1', nodes:[{id:'a',kind:'concept',x:1},{id:'b',kind:'record',x:2}],
  edges:[{source:'a',target:'b',type:'about'}]};
const workerGraph = {nodes:raw.nodes.map(n=>({...n,x:99})),edges:raw.edges,topicEdges:[]};
const answer = {nodes:[['a',10,11,12,13,14],['b',20,21,22,23,24]],topics:[],
  edges:edgeKeys(raw.edges).map(k=>[k,null]),topicEdges:[]};
const ready = () => ({executed:true,state:'ready',marker:raw.marker,answer:structuredClone(answer)});
const prefs = {view:'3d',layout:'temporalRoots',timeBasis:'recorded',cone:1,when:1,unlinked:'show',edgeOff:[]};
const deferred = () => {let resolve; const promise=new Promise(r=>{resolve=r;});return {promise,resolve};};
let h;
beforeEach(()=>{
  viewSessionCache.clear();
  h=globalThis.__loader={scope:{owner:'owner',team:'team-a',key:'scope-a'},calls:[],seq:1,alive:true,
    read:async (...a)=>{h.calls.push(['read',...a]);return structuredClone(raw);},
    roots:async (...a)=>{h.calls.push(['roots',...a]);return ready();},
    worker:async (...a)=>{h.calls.push(['worker',...a]);return structuredClone(workerGraph);},
  };
});
const make = (overrides={}) => createGraphLoader({mountScope:{...h.scope},accessJob:new AbortController(),
  firstAccess:Promise.resolve(null),restoreEntryCamera:false,seq:()=>h.seq,alive:()=>h.alive,
  canvasRatio:()=>1.8,mounted:()=>true,cameraPrefs:()=>'',showPreview:()=>()=>{},
  restoreCameras(){},layoutReady(){h.calls.push(['layoutReady']);},layoutFailed(){h.calls.push(['layoutFailed']);},
  ...overrides});
const workers = () => h.calls.filter(c=>c[0]==='worker');
const slowFollow = {followEveryMs:1,followForMs:0};     // 따라 묻기가 시험 중에 돌지 않는다
const quickFollow = {followEveryMs:1,followForMs:5_000};
const until = async ok => { for (let i=0;i<500&&!ok();i++) await new Promise(r=>setTimeout(r,2)); assert.ok(ok()); };

test('a matching ready answer bypasses the worker and preserves graph fields',async()=>{
  const r=await make().fetchGraph(prefs,{});
  assert.equal(r.nodes[0].x,10); assert.equal(r.nodes[0].kind,'concept');
  assert.equal(workers().length,0);
  const call=h.calls.find(c=>c[0]==='roots');
  assert.equal(call[1],'owner');assert.equal(call[2],'recorded');assert.equal(call[4].team,'team-a');
  assert.equal(h.calls.find(c=>c[0]==='read')[2].layout,'hub');
});
test('the graph read starts while roots is still pending',async()=>{
  const d=deferred();h.roots=()=>d.promise;
  const fetching=make().fetchGraph(prefs,{});
  await Promise.resolve();assert.equal(h.calls.filter(c=>c[0]==='read').length,1);
  d.resolve(ready());assert.equal((await fetching).nodes[0].x,10);assert.equal(workers().length,0);
});
// VIN-744: 허브가 답을 하나라도 들고 있으면 브라우저는 전체 배치를 하지 않는다.
test('off ignores any answer and a pending hub with nothing held uses the worker',async()=>{
  h.roots=async()=>({...ready(),state:'off'});
  assert.equal((await make().fetchGraph(prefs,{})).nodes[0].x,99);
  h.roots=async()=>({executed:true,state:'pending'});
  assert.equal((await make().fetchGraph(prefs,{})).nodes[0].x,99);assert.equal(workers().length,2);
});
for (const state of ['stale','failed']) test(`a ${state} answer is drawn without the worker`,async()=>{
  h.roots=async()=>({...ready(),state});
  assert.equal((await make(slowFollow).fetchGraph(prefs,{})).nodes[0].x,10);assert.equal(workers().length,0);
});
for (const [name, change] of [
  ['another marker',r=>{r.marker='epoch.0';}],
  ['a missing node',r=>{r.answer.nodes.pop();}],
  ['a missing relation',r=>{r.answer.edges=[];}],
]) test(`${name} still uses the hub answer without the worker`,async()=>{
  h.roots=async()=>{const r=ready();change(r);return r;};
  assert.equal((await make(slowFollow).fetchGraph(prefs,{})).nodes[0].x,10);assert.equal(workers().length,0);
});
for (const [name, change] of [
  ['malformed answer',r=>{r.answer={};}],
  ['unexecuted answer',r=>{r.executed=false;}],
]) test(`${name} falls back to the worker`,async()=>{
  h.roots=async()=>{const r=ready();change(r);return r;};
  assert.equal((await make().fetchGraph(prefs,{})).nodes[0].x,99);assert.equal(workers().length,1);
});
test('a graph without a marker draws the ready answer but keeps following it',async()=>{
  h.read=async()=>({...raw,marker:undefined});
  assert.equal((await make(slowFollow).fetchGraph(prefs,{})).nodes[0].x,10);assert.equal(workers().length,0);
});
test('a stale answer is swapped for the fresh one once the hub is ready',async()=>{
  const fresh=structuredClone(answer);fresh.nodes[0][1]=77;
  const replies=[{...ready(),state:'stale',digest:'old'},{executed:true,state:'stale',marker:'epoch.1',digest:'old'},
    {...ready(),digest:'new',answer:fresh}];
  h.roots=async(...a)=>{h.calls.push(['roots',...a]);return replies.shift()??{executed:true,state:'ready',digest:'new'};};
  const loader=make(quickFollow);
  assert.equal((await loader.fetchGraph(prefs,{})).nodes[0].x,10);
  await until(()=>h.calls.some(c=>c[0]==='layoutReady'));
  const asks=h.calls.filter(c=>c[0]==='roots');
  assert.equal(asks[1][5],'old');            // 따라 물을 때는 그린 답의 digest 를 싣는다
  loader.reuseGraph=true;
  assert.equal((await loader.fetchGraph(prefs,{})).nodes[0].x,77);
  assert.equal(workers().length,0);
});
test('a ready hub with the same answer ends the follow without a redraw',async()=>{
  const replies=[{...ready(),state:'stale',digest:'same'},{executed:true,state:'ready',marker:'epoch.1',digest:'same'}];
  h.roots=async(...a)=>{h.calls.push(['roots',...a]);return replies.shift()??{executed:true,state:'ready',digest:'same'};};
  await make(quickFollow).fetchGraph(prefs,{});
  await until(()=>h.calls.filter(c=>c[0]==='roots').length>=2);
  await new Promise(r=>setTimeout(r,20));
  assert.equal(h.calls.filter(c=>c[0]==='roots').length,2);
  assert.equal(h.calls.filter(c=>c[0]==='layoutReady').length,0);
});
test('a new node stands right of the latest known member of its topic',async()=>{
  h.read=async()=>({...structuredClone(raw),nodes:[{id:'a',kind:'concept',domain:'t'},{id:'b',kind:'record',domain:'t'},
    {id:'c',kind:'concept',domain:'t'}]});
  h.roots=async()=>({...ready(),state:'stale'});
  const r=await make(slowFollow).fetchGraph(prefs,{});
  const c=r.nodes.find(n=>n.id==='c');
  assert.ok(Math.abs(c.x-20.33)<1e-9);assert.equal(c.y,21);assert.equal(c.z,22);
  assert.equal(workers().length,0);
});
test('a roots transport failure leaves the existing worker path usable',async()=>{
  h.roots=async()=>{throw new Error('offline');};
  await make().fetchGraph(prefs,{});assert.equal(workers().length,1);
});
test('a selected topic and by-centre layout never request stored roots',async()=>{
  await make().fetchGraph(prefs,{domains:['vinc/design']});
  await make().fetchGraph({...prefs,layout:'hub'},{});
  assert.equal(h.calls.filter(c=>c[0]==='roots').length,0);assert.equal(workers().length,1);
});
for (const change of ['abort','space','sequence']) test(`${change} rejects an obsolete stored answer`,async()=>{
  const d=deferred();h.roots=()=>d.promise;
  const loader=make(),fetching=loader.fetchGraph(prefs,{});
  await Promise.resolve();await Promise.resolve();
  if(change==='abort')loader.abort();else if(change==='space')h.scope={...h.scope,key:'scope-b'};else h.seq++;
  d.resolve(ready());
  assert.deepEqual(await fetching,{executed:false,stale:true});assert.equal(workers().length,0);
});
test('a slow roots read times out and uses the worker',async()=>{
  h.roots=(_owner,_basis,signal)=>new Promise((_,reject)=>signal.addEventListener('abort',()=>reject(signal.reason),{once:true}));
  await make({rootsTimeoutMs:1}).fetchGraph(prefs,{});assert.equal(workers().length,1);
});
test('a ready answer arriving after patience swaps from by-centre without a worker',async()=>{
  const d=deferred();h.roots=()=>d.promise;
  const loader=make({patienceMs:1});
  const result=await loader.fetchGraph(prefs,{});
  assert.equal(result.layout,'pending');assert.equal(result.nodes[0].x,1);assert.equal(workers().length,0);
  d.resolve(ready());await new Promise(r=>setTimeout(r,0));
  assert.equal(h.calls.filter(c=>c[0]==='layoutReady').length,1);assert.equal(workers().length,0);
  loader.reuseGraph=true;
  assert.equal((await loader.fetchGraph(prefs,{})).nodes[0].x,10);
});
test('a stored answer arriving after an aborted by-centre handoff cannot replace the screen',async()=>{
  const d=deferred();h.roots=()=>d.promise;
  const loader=make({patienceMs:1});await loader.fetchGraph(prefs,{});loader.abort();
  d.resolve(ready());await new Promise(r=>setTimeout(r,0));
  assert.equal(h.calls.filter(c=>c[0]==='layoutReady'||c[0]==='layoutFailed').length,0);
  assert.equal(workers().length,0);
});
test('off still shows by-centre after patience and swaps when the worker finishes',async()=>{
  h.roots=async()=>({executed:true,state:'off'});
  const d=deferred();h.worker=()=>{h.calls.push(['worker']);return d.promise;};
  const result=await make({patienceMs:1}).fetchGraph(prefs,{});
  assert.equal(result.layout,'pending');assert.equal(result.nodes[0].x,1);
  d.resolve(workerGraph);
  await new Promise(r=>setTimeout(r,0));assert.equal(h.calls.filter(c=>c[0]==='layoutReady').length,1);
});
test('a failed fallback worker keeps by-centre with its failure note',async()=>{
  h.roots=async()=>({executed:true,state:'off'});
  h.worker=async()=>{throw new Error('worker failed');};
  const result=await make().fetchGraph(prefs,{});
  assert.equal(result.layout,'failed');assert.equal(result.nodes[0].x,1);
});
// 커뮤니티 스페이스는 내린 페이지와 그 문서 미러를 그리지 않는다. 개인·팀 그래프는 그대로다.
test('a community space drops retired pages and their document mirrors, other spaces keep them',async()=>{
  const withRetired = {...raw, nodes:[...raw.nodes,
    {id:'concept:old',kind:'concept',props:{status:'retired',doc_id:'page.c.old'}},
    {id:'concept:doc.page.c.old',kind:'concept',props:{}}],
    edges:[...raw.edges,{source:'concept:old',target:'a',type:'relates_to'}]};
  assert.deepEqual(withoutRetiredPages(withRetired).nodes.map(n=>n.id),['a','b']);
  assert.deepEqual(withoutRetiredPages(withRetired).edges,raw.edges);
  assert.equal(withoutRetiredPages(raw),raw,'nothing retired leaves the same object');
  h.read=async()=>structuredClone(withRetired);
  const team=await make().fetchGraph({...prefs,layout:'cone'},{});
  assert.equal(team.nodes.length,4,'a team space keeps every node');
  h.scope={...h.scope,team:'community:01a1367b-e924-72a0-9b24-3e299e2c70a7',key:'scope-c'};
  const community=await make().fetchGraph({...prefs,layout:'cone'},{});
  assert.deepEqual(community.nodes.map(n=>n.id),['a','b']);
});
