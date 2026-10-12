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
  './view-layout-wire': url(await js('view-layout-wire.ts')),
};
const prefsUrl = url(await js('view-prefs.ts'));
modules['./view-graph-read'] = url((await js('view-graph-read.ts')).replace('"./view-prefs"', JSON.stringify(prefsUrl)));
modules['./view-graph-loading'] = url(await js('view-graph-loading.ts'));
let code = await js('view-graph-loader.ts');
for (const [name, replacement] of Object.entries(modules)) code = code.replace(JSON.stringify(name), JSON.stringify(replacement));
const {createGraphLoader} = await import(url(code));
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
  canvasRatio:()=>1.8,mounted:()=>true,planar:()=>false,cameraPrefs:()=>'',showPreview:()=>()=>{},
  restoreCameras(){},layoutReady(){h.calls.push(['layoutReady']);},layoutFailed(){h.calls.push(['layoutFailed']);},
  ...overrides});
const workers = () => h.calls.filter(c=>c[0]==='worker');

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
for (const state of ['off','pending','stale','failed']) test(`${state} uses the existing worker path`,async()=>{
  h.roots=async()=>({...ready(),state});
  assert.equal((await make().fetchGraph(prefs,{})).nodes[0].x,99);assert.equal(workers().length,1);
});
for (const [name, change] of [
  ['another marker',r=>{r.marker='epoch.0';}],
  ['a missing node',r=>{r.answer.nodes.pop();}],
  ['a missing relation',r=>{r.answer.edges=[];}],
  ['malformed answer',r=>{r.answer={};}],
  ['unexecuted answer',r=>{r.executed=false;}],
]) test(`${name} falls back to the worker`,async()=>{
  h.roots=async()=>{const r=ready();change(r);return r;};
  assert.equal((await make().fetchGraph(prefs,{})).nodes[0].x,99);assert.equal(workers().length,1);
});
test('a graph without a marker cannot use a ready answer',async()=>{
  h.read=async()=>({...raw,marker:undefined});
  await make().fetchGraph(prefs,{});assert.equal(workers().length,1);
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
