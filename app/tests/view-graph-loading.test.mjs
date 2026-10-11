import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';
const source=await readFile(new URL('../src/view-graph-loading.ts',import.meta.url),'utf8');
const js=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const {readGraphProgressively:read}=await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
const full={executed:true,nodes:[{id:'concept:a',kind:'concept',x:1,y:1,props:{unicode:'합성'}}],edges:[]};
const preview={executed:true,kind:'graph_preview',partial:true,nodes:[{id:'concept:a',kind:'concept',x:1,y:1}],edges:[],snapshot:{protocol:'graph-preview-v1',token:'x'.repeat(43)}};
test('legacy response performs one request and never publishes a preview',async()=>{
  const calls=[];assert.equal(await read(async stage=>{calls.push(stage);return full;},()=>assert.fail(),new AbortController().signal),full);
  assert.deepEqual(calls,['preview']);
});
test('publishes actual structure before requesting the full same-snapshot result',async()=>{
  const order=[];const result=await read(async stage=>{order.push(stage);return stage==='preview'?preview:full;},()=>order.push('display'),new AbortController().signal);
  assert.equal(result,full);assert.deepEqual(order,['preview','display','complete']);
});
test('partial failure and mismatched snapshot fall back exactly once',async()=>{
  for(const value of [Promise.reject(new Error('failed')),{...full,nodes:[]}]){
    const calls=[];
    const result=await read(async stage=>{calls.push(stage);return stage==='preview'?preview:stage==='complete'?value:full;},()=>{},new AbortController().signal);
    assert.equal(result,full);assert.deepEqual(calls,['preview','complete','cancel',undefined]);
  }
});
test('cancellation during completion cancels the owned continuation and ignores its late result',async()=>{
  const ctl=new AbortController(),calls=[];
  await assert.rejects(read(async stage=>{
    calls.push(stage);if(stage==='preview')return preview;
    if(stage==='complete'){ctl.abort();return full;}return {executed:true};
  },()=>{},ctl.signal),{name:'AbortError'});
  assert.deepEqual(calls,['preview','complete','cancel']);
});
test('fast pending replies are throttled and eventually preserve the full payload',async()=>{
  let count=0;const start=performance.now();
  const result=await read(async stage=>stage==='preview'?preview:++count===1?{executed:false,graphSnapshotPending:true}:full,()=>{},new AbortController().signal);
  assert.equal(result,full);assert.equal(count,2);assert.ok(performance.now()-start>=200);
});
test('cancellation does not fall back or publish a later response',async()=>{
  const ctl=new AbortController(),calls=[];
  await assert.rejects(read(async stage=>{calls.push(stage);ctl.abort();return preview;},()=>assert.fail(),ctl.signal),{name:'AbortError'});
  assert.deepEqual(calls,['preview']);
});
test('unsupported or invalid partial protocol never reaches the renderer',async()=>{
  const calls=[];assert.equal(await read(async stage=>{calls.push(stage);return stage?{...preview,snapshot:{protocol:'future',token:'x'}}:full;},()=>assert.fail(),new AbortController().signal),full);
  assert.deepEqual(calls,['preview',undefined]);
});
test('a failed partial renderer cancels its continuation and uses the full-response path',async()=>{
  const calls=[];const ctl=new AbortController();
  const result=await read(async stage=>{calls.push(stage);return stage==='preview'?preview:full;},()=>{throw new Error('synthetic renderer failure');},ctl.signal);
  assert.equal(result,full);assert.deepEqual(calls,['preview','cancel',undefined]);
  ctl.abort();assert.deepEqual(calls,['preview','cancel',undefined]);
});
