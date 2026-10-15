import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';
const js = ts.transpileModule(await readFile(new URL('../src/topic-conversion-state.ts',import.meta.url),'utf8'),
  {compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const { TopicConversion } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
const check={ready:true,topic:'project',fingerprint:'a'.repeat(64),counts:{Concept:2},documents:1,blockers:[]};
const checkpoint={id:'00000000-0000-4000-8000-000000000099',name:'Team',topic:'project',fingerprint:check.fingerprint};
const run={...checkpoint,team:'fresh',state:'prepared'};
function setup(overrides={}) {
  const saved=[],calls=[];
  const client={preflight:async()=>check,reserve:async body=>{calls.push(body);return run;},status:async()=>run,
    resume:async()=>({...run,state:'active'}),cancel:async()=>({...run,state:'cancelled'}),...overrides};
  const flow=new TopicConversion(client,value=>saved.push(value),null,()=>checkpoint.id);
  flow.edit('project','Team');
  return {flow,saved,calls};
}
test('requires checked scope and explicit consent before reserving a fresh team',async()=>{
  const {flow,calls}=setup();await flow.reserve(true);assert.equal(calls.length,0);
  await flow.preflight();await flow.reserve(false);assert.equal(calls.length,0);
  await flow.reserve(true);assert.deepEqual(calls,[checkpoint]);assert.equal(flow.run.state,'prepared');
});
test('two clicks reserve once and preserve the run before an uncertain reply',async()=>{
  let reject;const pending=new Promise((_,r)=>reject=r);
  const {flow,saved,calls}=setup({reserve:body=>{calls.push(body);return pending;}});
  await flow.preflight();const first=flow.reserve(true);await flow.reserve(true);
  assert.equal(calls.length,1);assert.deepEqual(saved[0],checkpoint);
  reject({code:'timeout'});await first;assert.equal(flow.error,'timeout');assert.deepEqual(flow.checkpoint,checkpoint);
});
test('changing source selection invalidates old preflight responses',async()=>{
  let resolve;const {flow}=setup({preflight:()=>new Promise(r=>resolve=r)});
  const request=flow.preflight();flow.edit('other','Team');resolve(check);await request;
  assert.equal(flow.check,null);
});
test('an uncertain move is resumed using the same id and never declared complete early',async()=>{
  const ids=[];const {flow}=setup({resume:async id=>{ids.push(id);throw {code:'uncertain'};}});
  await flow.preflight();await flow.reserve(true);await flow.resume();
  assert.equal(flow.run.state,'prepared');assert.ok(flow.checkpoint);assert.deepEqual(ids,[checkpoint.id]);
});
test('cancellation clears recovery only after server confirmation',async()=>{
  let refused=true;const {flow,saved}=setup({cancel:async()=>{if(refused)throw {};return {...run,state:'cancelled'};}});
  await flow.preflight();await flow.reserve(true);await flow.cancel();assert.ok(flow.checkpoint);
  refused=false;await flow.cancel();assert.equal(flow.checkpoint,null);assert.equal(saved.at(-1),null);
});
test('reopening the sheet reads status without performing a move',async()=>{
  let moved=0;const flow=new TopicConversion({status:async()=>run,resume:async()=>{moved++;return run;}},()=>{},checkpoint);
  await flow.status();assert.equal(flow.run.state,'prepared');assert.equal(moved,0);
  flow.edit('other','other');assert.equal(flow.topic,'project');
});
test('blocked dependencies never prepare a team',async()=>{
  const {flow,calls}=setup({preflight:async()=>({...check,ready:false,blockers:['boundary_relation']})});
  await flow.preflight();await flow.reserve(true);assert.equal(calls.length,0);
});
test('an incomplete inventory never reserves a team or invents zero scope counts',async()=>{
  const unknown={ready:false,topic:'project',counts_known:false,counts:null,documents:null,blockers:['conversion_inventory_limit']};
  const {flow,calls}=setup({preflight:async()=>unknown});
  await flow.preflight();await flow.reserve(true);
  assert.deepEqual(flow.check,unknown);assert.equal(calls.length,0);
});

test('a known stale preflight rejection permits checking the scope again',async()=>{
  const {flow,saved}=setup({reserve:async()=>{throw {code:'preflight_changed'};}});
  await flow.preflight();await flow.reserve(true);
  assert.equal(flow.checkpoint,null);assert.equal(saved.at(-1),null);
  flow.edit('other','Team');assert.equal(flow.topic,'other');
});

test('a lost reservation response followed by a known stale rejection does not strand the sheet',async()=>{
  let count=0;const {flow}=setup({reserve:async()=>{throw {code:++count===1?'timeout':'preflight_changed'};}});
  await flow.preflight();await flow.reserve(true);assert.ok(flow.checkpoint);
  await flow.resume();assert.equal(flow.checkpoint,null);assert.equal(flow.error,'preflight_changed');
});

test('original conventions require a matching check and persist through an uncertain reservation',async()=>{
  const policies=[];
  const {flow,calls}=setup({preflight:async(topic,policy)=>{policies.push(policy);return {...check,conventions:{policy,documents:1,personal_defaults_after_move:policy==='move_original'}};},
    reserve:async body=>{calls.push(body);throw {code:'timeout'};}});
  flow.chooseConventions('move_original');await flow.preflight();await flow.reserve(true);
  assert.deepEqual(policies,['move_original']);assert.equal(calls[0].conventions_policy,'move_original');
  flow.chooseConventions('retain');assert.equal(flow.conventionsPolicy,'move_original');
  const reopened=new TopicConversion({},()=>{},flow.checkpoint);
  assert.equal(reopened.conventionsPolicy,'move_original');
});

test('changing conventions invalidates the checked scope and old servers cannot authorize original move',async()=>{
  const {flow,calls}=setup();await flow.preflight();flow.chooseConventions('move_original');
  assert.equal(flow.check,null);await flow.reserve(true);assert.equal(calls.length,0);
  await flow.preflight();assert.equal(flow.check,null);assert.equal(flow.error,'conversion_response_uncertain');
  await flow.reserve(true);assert.equal(calls.length,0);
});

test('status progress never repeats a write or announces account completion',async()=>{
  let writes=0;
  const flow=new TopicConversion({status:async()=>({...run,state:'committing',progress:{status:'graph_committed',batches:12}}),
    resume:async()=>{writes++;return run;}},()=>{},checkpoint);
  await flow.status();await flow.status();assert.equal(writes,0);
  assert.equal(flow.run.state,'committing');assert.equal(flow.run.progress.batches,12);
});
