import assert from 'node:assert/strict';
import {test, beforeEach} from 'node:test';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';

const url = code => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
const js = async name => ts.transpileModule(await readFile(new URL(`../src/${name}`, import.meta.url), 'utf8'),
  {compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
globalThis.localStorage = {getItem:()=>null, setItem(){}};
// `api` 는 어떤 메서드를 불러도 기록하는 대역이다: 로더가 `/graph` 읽기 말고 무엇을 부르든 시험이 본다.
const apiStub = web => url(`export const WEB=${web};
  export const viewScope=()=>globalThis.__loader.scope;
  export const viewAccess=async(...a)=>globalThis.__loader.access(...a);
  export const api=new Proxy({}, {get:(_,name)=>(...a)=>globalThis.__loader.api(name,...a)});`);
const cacheUrl = url(await js('view-session-cache.ts'));
const readUrl = url(await js('view-graph-read.ts'));
const loaderJs = await js('view-graph-loader.ts');
const loadLoader = async web => {
  let code = loaderJs;
  for (const [name, replacement] of Object.entries({'./api': apiStub(web), './view-session-cache': cacheUrl,
    './view-graph-read': readUrl})) code = code.replace(JSON.stringify(name), JSON.stringify(replacement));
  return import(url(code));
};
const {createGraphLoader, withoutRetiredPages} = await loadLoader(false);
const {createGraphLoader: createWebLoader} = await loadLoader(true);
const {viewSessionCache} = await import(cacheUrl);
const raw = {executed:true, marker:'epoch.1', nodes:[{id:'a',kind:'concept',x:1},{id:'b',kind:'record',x:2}],
  edges:[{source:'a',target:'b',type:'about'}]};
const deferred = () => {let resolve; const promise=new Promise(r=>{resolve=r;});return {promise,resolve};};
let h;
beforeEach(()=>{
  viewSessionCache.clear();
  h=globalThis.__loader={scope:{owner:'owner',team:'team-a',key:'scope-a'},calls:[],seq:1,alive:true,
    revision:'epoch.1',
    access:async ()=>({session:'s',revision:h.revision}),
    read:async ()=>structuredClone(raw),
    api(name,...a){h.calls.push([name,...a]);return name==='graphStage'?h.read(...a):Promise.reject(new Error(`unexpected api.${name}`));},
  };
});
const make = (overrides={}, create=createGraphLoader) => create({mountScope:{...h.scope},accessJob:new AbortController(),
  firstAccess:Promise.resolve(null),seq:()=>h.seq,alive:()=>h.alive,...overrides});
const reads = () => h.calls.filter(c=>c[0]==='graphStage');
const until = async ok => { for (let i=0;i<500&&!ok();i++) await new Promise(r=>setTimeout(r,2)); assert.ok(ok()); };

test('the compass loader makes exactly one read, the plain /graph read, and nothing else',async()=>{
  const r=await make().fetchGraph({});
  assert.deepEqual(r,raw);
  assert.deepEqual(h.calls.map(c=>c[0]),['graphStage'],'one call: no stored layout, no staged preview');
  const [, owner, options, stage, snapshot, scope, signal] = h.calls[0];
  assert.equal(owner,'owner');
  assert.deepEqual(options,{layout:'hub'});
  assert.equal(stage,undefined);assert.equal(snapshot,undefined);
  assert.deepEqual(scope,{team:'team-a'});
  assert.ok(signal instanceof AbortSignal);
});
test('a topic scope is still one read and carries the topics',async()=>{
  const loader=make();
  await loader.fetchGraph({domains:['vinc/design','agent']});
  assert.deepEqual(h.calls.map(c=>[c[0],c[2]]),[['graphStage',{domains:['vinc/design','agent'],layout:'hub'}]]);
  assert.equal(loader.scopeKey,JSON.stringify({domains:['agent','vinc/design'],layout:'hub',cone:1}));
});
test('each redraw reads once more and never more',async()=>{
  const loader=make();
  for (let i=0;i<3;i++) await loader.fetchGraph({});
  assert.equal(reads().length,3);assert.equal(h.calls.length,3);
});
for (const change of ['abort','space','sequence']) test(`${change} turns an obsolete read into a stale answer`,async()=>{
  const d=deferred();
  h.read=(_owner,_options,_stage,_snapshot,_scope,signal)=>new Promise((ok,fail)=>{
    signal.addEventListener('abort',()=>fail(new DOMException('aborted','AbortError')),{once:true});d.promise.then(ok);});
  const loader=make(),fetching=loader.fetchGraph({});
  await Promise.resolve();await Promise.resolve();
  if(change==='abort')loader.abort();else if(change==='space')h.scope={...h.scope,key:'scope-b'};else h.seq++;
  d.resolve(structuredClone(raw));
  assert.deepEqual(await fetching,{executed:false,stale:true});assert.equal(reads().length,1);
});
test('a failed read is thrown to the caller',async()=>{
  h.read=async()=>{throw Object.assign(new Error('down'),{status:503});};
  const errors=[],error=console.error;console.error=m=>errors.push(m);
  try { await assert.rejects(make().fetchGraph({}),/down/); } finally { console.error=error; }
  assert.match(errors[0],/View graph read failed .*"status":503/);
});
// 웹: 표식이 같으면 같은 탭의 다음 마운트는 보관본을 쓰고 읽지 않는다. 표식이 바뀌면 다시 한 번 읽는다.
test('a returning mount in the same tab reads nothing, and a changed marker reads once',async()=>{
  const first=make({firstAccess:h.access()},createWebLoader);
  assert.deepEqual(await first.fetchGraph({}),raw);
  await until(()=>viewSessionCache.count===1);
  assert.deepEqual(first.retained,{session:'s',revision:'epoch.1',key:first.scopeKey});
  const again=make({firstAccess:h.access()},createWebLoader);
  assert.deepEqual(await again.fetchGraph({}),raw);
  assert.equal(reads().length,1,'the retained graph is used without a read');
  h.revision='epoch.2';
  await make({firstAccess:h.access()},createWebLoader).fetchGraph({});
  assert.equal(reads().length,2);
  assert.deepEqual(h.calls.map(c=>c[0]),['graphStage','graphStage']);
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
  const team=await make().fetchGraph({});
  assert.equal(team.nodes.length,4,'a team space keeps every node');
  h.scope={...h.scope,team:'community:01a1367b-e924-72a0-9b24-3e299e2c70a7',key:'scope-c'};
  const community=await make().fetchGraph({});
  assert.deepEqual(community.nodes.map(n=>n.id),['a','b']);
});
