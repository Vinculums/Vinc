import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';
const js=ts.transpileModule(await readFile(new URL('../src/conversion-inventory.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const {waitForInventory}=await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
const ticket='00000000-0000-4000-8000-000000000099';
const pending={ready:false,topic:'vinc',counts:null,documents:null,blockers:['conversion_inventory_pending'],inventory_state:'pending',inventory_ticket:ticket};
const done={ready:false,topic:'vinc',counts_known:true,counts:{Concept:5001},documents:540,blockers:['conversion_bounded_executor_required'],inventory_state:'done',inventory_ticket:ticket};
test('polls opaque status until complete without reserving or moving',async()=>{
 const calls=[];let n=0;
 const result=await waitForInventory('vinc',async()=>pending,async id=>{calls.push(id);return ++n===1?pending:done;},async()=>{});
 assert.deepEqual(calls,[ticket,ticket]);assert.deepEqual(result,done);assert.equal(result.ready,false);
});
test('timeout and malformed tickets never become empty success',async()=>{
 let time=0;
 await assert.rejects(waitForInventory('vinc',async()=>pending,async()=>pending,async()=>{time+=10;},()=>time,15),e=>e.code==='conversion_inventory_pending');
 await assert.rejects(waitForInventory('vinc',async()=>({...pending,inventory_ticket:'wrong'}),async()=>done),e=>e.code==='conversion_inventory_invalid');
});
test('a different topic response cannot replace this selection',async()=>{
 await assert.rejects(waitForInventory('vinc',async()=>({...done,topic:'other'}),async()=>done),e=>e.code==='conversion_inventory_invalid');
});
test('legacy completed responses and actual empty counts stay distinct from failures',async()=>{
 const empty={ready:false,topic:'vinc',counts:{},documents:0,blockers:['empty_topic']};
 assert.equal((await waitForInventory('vinc',async()=>empty,async()=>done)).documents,0);
 const failed={...pending,inventory_state:'failed',blockers:['conversion_inventory_failed']};
 assert.equal((await waitForInventory('vinc',async()=>failed,async()=>done)).counts,null);
});
