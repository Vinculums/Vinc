import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';
const source=await readFile(new URL('../src/view-session-cache.ts',import.meta.url),'utf8');
const js=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const {ViewSessionCache}=await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
const graph={executed:true,nodes:[{id:'concept:a',x:12,props:{note:'합성'}}],edges:[{source:'concept:a',target:'concept:a'}],topicEdges:[]};
const put=(c,s='owner/space/identity',k='scope',r='epoch.1',g=graph,l)=>{const epoch=c.activate(s,r);return c.put(s,k,r,g,epoch,l);};
test('retains immutable full data and coordinates; renderer mutations cannot poison later mounts',()=>{
  const c=new ViewSessionCache();put(c,undefined,undefined,undefined,graph,{key:'temporal/recorded/1.6',graph});
  const raw=c.get('owner/space/identity','scope','epoch.1');raw.nodes[0].x=999;raw.edges[0].source={id:'bad'};
  const layout=c.getLayout('owner/space/identity','scope','epoch.1','temporal/recorded/1.6');layout.nodes[0].x=777;
  assert.deepEqual(c.get('owner/space/identity','scope','epoch.1'),graph);
  assert.deepEqual(c.getLayout('owner/space/identity','scope','epoch.1','temporal/recorded/1.6'),graph);
});
test('never stores partial, failed, token-bearing or pending responses',()=>{
  for(const bad of [{partial:true},{executed:false},{snapshot:{}},{graphSnapshotPending:true},{nodes:null}]){
    const c=new ViewSessionCache();assert.equal(put(c,undefined,undefined,undefined,{...graph,...bad}),false);assert.equal(c.count,0);
  }
});
test('owner Space account path identity revision and layout mismatch cannot reuse data',()=>{
  const c=new ViewSessionCache();put(c);
  assert.equal(c.get('other-user','scope','epoch.1'),undefined);
  assert.equal(c.get('owner/space/identity','other-filter','epoch.1'),undefined);
  assert.equal(c.get('owner/space/identity','scope','epoch.2'),undefined);
  c.activate('owner/space/identity','epoch.2');assert.equal(c.count,0);
  put(c);c.activate('other-user','epoch.1');assert.equal(c.count,0);
});
test('generation prevents late results after scope change logout or local write',()=>{
  const c=new ViewSessionCache(),epoch=c.activate('s','r');c.clear();c.activate('s','r');
  assert.equal(c.put('s','k','r',graph,epoch),false);assert.equal(c.count,0);
});
test('bounded LRU entry count, payload size, replacement camera state and TTL',()=>{
  let now=0;const c=new ViewSessionCache(6000,2,100,()=>now);
  put(c,'s','a','r');put(c,'s','b','r');c.get('s','a','r');put(c,'s','c','r');
  assert.equal(c.get('s','b','r'),undefined);assert.equal(c.count,2);
  assert.equal(put(c,'s','huge','r',{...graph,nodes:[{id:'x',props:{note:'x'.repeat(9000)}}]}),false);
  c.remember('s','a','r',{prefs:'filter',camera2:{x:1,ratio:.3}},['domain/a']);
  put(c,'s','a','r');assert.ok(c.bytes<=6000);assert.equal(c.getState('s','a','r','filter').camera2.x,1);
  now=101;assert.equal(c.count,0);assert.equal(c.last('s'),undefined);
});
test('camera and filter bookmark restored only in the verified session and same preferences',()=>{
  const c=new ViewSessionCache();put(c,'s','k','r');
  c.remember('s','k','r',{prefs:'view/filter/layout/aspect',camera3:{pos:{x:1,y:2,z:3}}},['domain/a']);
  assert.deepEqual(c.last('s'),{domains:['domain/a']});
  assert.equal(c.last('other'),undefined);assert.equal(c.getState('s','k','r','changed-filter'),undefined);
  c.activate('s','new-revision');assert.deepEqual(c.last('s'),{domains:['domain/a']});
  assert.equal(c.getState('s','k','new-revision','view/filter/layout/aspect'),undefined);
  c.clear();assert.equal(c.last('s'),undefined);
});

test('a recently left domain bookmark outlives an expired graph and still requires the same verified session',()=>{
  let now=0;const c=new ViewSessionCache(6000,2,100,()=>now);put(c,'s','k','r');
  now=80;c.remember('s','k','r',{prefs:'p'},['topic/a']);now=120;
  assert.equal(c.count,0);assert.equal(c.hasBookmark,true);assert.deepEqual(c.last('s'),{domains:['topic/a']});
  assert.equal(c.last('different-session'),undefined);now=181;assert.equal(c.hasBookmark,false);
});
