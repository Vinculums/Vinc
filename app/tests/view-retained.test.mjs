import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';
const js=async name=>ts.transpileModule(await readFile(new URL(`../src/${name}`,import.meta.url),'utf8'),
  {compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const url=code=>`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
globalThis.localStorage={_m:new Map(),getItem(k){return this._m.get(k)??null},setItem(k,v){this._m.set(k,String(v))},removeItem(k){this._m.delete(k)}};
const cacheUrl=url(await js('view-session-cache.ts'));
const retainedJs=(await js('view-retained.ts')).replace('"./view-session-cache"',JSON.stringify(cacheUrl));
const {RetainedViews,storageMark,TTL_MS}=await import(url(retainedJs));
const {ViewSessionCache}=await import(cacheUrl);

const memory=()=>{const m=new Map();return {m,
  get:async id=>m.get(id),put:async r=>{m.set(r.id,structuredClone(r));},delete:async id=>{m.delete(id);},
  all:async()=>[...m.values()].map(r=>structuredClone(r)),clear:async()=>{m.clear();}};};
const fakeHash=async s=>`h:${s}`;
const graph={executed:true,nodes:[{id:'concept:a',x:1,y:1}],edges:[]};
const sess=(scope,who='a@x')=>JSON.stringify([scope,who]);
const filled=(session=sess('s1'),revision='r1',clock=()=>1000)=>{
  const c=new ViewSessionCache(1<<20,4,300000,clock);
  c.activate(session,revision);
  assert.equal(c.put(session,'{"k":1}',revision,graph,c.generation),true);
  return c;
};
const make=(backend,clock=()=>1000)=>new RetainedViews(backend,clock,TTL_MS,4,1<<20,fakeHash,storageMark);

test('a snapshot is restored only for the same session and marker, then consumed',async()=>{
  const backend=memory(),views=make(backend);
  assert.equal(await views.save(filled().snapshot()),true);
  assert.equal(views.has(),true);
  assert.equal(await views.take(sess('s2'),'r1'),null,'another session gets nothing');
  assert.equal(backend.m.size,1,'a foreign probe does not consume the record');
  const taken=await views.take(sess('s1'),'r1');
  assert.equal(taken.entries.length,1);
  assert.equal(backend.m.size,0,'a restored record is deleted');
  assert.equal(await views.take(sess('s1'),'r1'),null);
});

test('a changed marker or an expired record is dropped without being returned',async()=>{
  const backend=memory();let now=1000;const views=make(backend,()=>now);
  await views.save(filled().snapshot());
  assert.equal(await views.take(sess('s1'),'r2'),null);
  assert.equal(backend.m.size,0);
  await views.save(filled().snapshot());
  now+=TTL_MS+1;
  assert.equal(views.has(),false,'the marker expires with the record');
  assert.equal(await views.take(sess('s1'),'r1'),null);
});

test('restored entries land in the cache only after the caller confirmed session and marker',async()=>{
  const views=make(memory());
  await views.save(filled().snapshot());
  const taken=await views.take(sess('s1'),'r1');
  const fresh=new ViewSessionCache(1<<20,4,300000,()=>5000);
  assert.equal(fresh.restore(sess('s1'),'r1',taken.entries,taken.expires),1);
  assert.deepEqual(fresh.get(sess('s1'),'{"k":1}','r1'),graph);
  assert.equal(fresh.restore(sess('s1'),'r9',taken.entries,taken.expires),0,'a different marker restores nothing');
});

test('only a bounded number of records and bytes are kept, newest first',async()=>{
  const backend=memory();let now=1000;const views=make(backend,()=>now);
  for(const session of ['a','b','c','d','e']){now+=10;await views.save(filled(sess(session)).snapshot());}
  assert.ok(backend.m.size<=4);
  assert.equal([...backend.m.values()].some(r=>r.entries.length&&r.id===`h:${sess('a')}`),false,'the oldest record was evicted');
  const big=filled(sess('big'));
  const snap=big.snapshot();snap.entries[0][1].size=(1<<20)+1;
  assert.equal(await views.save(snap),false);
});

test('purge empties the store and the marker',async()=>{
  const backend=memory(),views=make(backend);
  await views.save(filled().snapshot());
  await views.clear();
  assert.equal(backend.m.size,0);
  assert.equal(views.has(),false);
});

test('without a backend nothing is stored and nothing breaks',async()=>{
  const views=make(null);
  assert.equal(await views.save(filled().snapshot()),false);
  assert.equal(views.has(),false);
  assert.equal(await views.take(sess('s1'),'r1'),null);
  await views.clear();
});

test('session cache purge notifies listeners; clear does not',()=>{
  const c=new ViewSessionCache();let purged=0;c.onPurge(()=>purged++);
  c.clear();assert.equal(purged,0);
  c.purge();assert.equal(purged,1);
});

test('another identity on the same browser deletes records that are not theirs',async()=>{
  const backend=memory(),views=make(backend);
  await views.save(filled(sess('s1','a@x')).snapshot());
  assert.equal(await views.take(sess('s1','b@x'),'r1'),null);
  assert.equal(backend.m.size,0,'a record of another identity is removed, not left at rest');
});

test('a session without a readable identity is never stored',async()=>{
  const views=make(memory());
  assert.equal(await views.save(filled('not-json').snapshot()),false);
});
