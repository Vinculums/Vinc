import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';
// view-graph-read imports view-prefs; load both as in-memory modules.
const load=async name=>ts.transpileModule(await readFile(new URL(`../src/${name}`,import.meta.url),'utf8'),
  {compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
globalThis.localStorage={getItem:()=>null,setItem(){}};
const prefsJs=await load('view-prefs.ts');
const prefsUrl=`data:text/javascript;base64,${Buffer.from(prefsJs).toString('base64')}`;
const readJs=(await load('view-graph-read.ts')).replace('"./view-prefs"',JSON.stringify(prefsUrl));
const {graphReadPlan,wantsStagedRead}=await import(`data:text/javascript;base64,${Buffer.from(readJs).toString('base64')}`);

test('hub plans carry the cone and the sorted scope key; roots ignore it',()=>{
  const hub=graphReadPlan({view:'3d',layout:'hub',cone:.8},['b','a']);
  assert.equal(hub.useRoots,false);assert.equal(hub.cone,.8);
  assert.equal(hub.scopeKey,JSON.stringify({domains:['a','b'],layout:'hub',cone:.8}));
  assert.deepEqual(hub.options,{domains:['b','a'],layout:'hub',cone:.8});
  const roots=graphReadPlan({view:'3d',layout:'temporalRoots',cone:.8},['a']);
  assert.equal(roots.useRoots,true);assert.equal(roots.cone,1);
  assert.deepEqual(roots.options,{domains:['a'],layout:'hub'});
});
test('the same request in any domain order shares one key and no scope sends no domains',()=>{
  const a=graphReadPlan({layout:'hub',cone:1},['x','y']),b=graphReadPlan({layout:'hub',cone:1},['y','x']);
  assert.equal(a.scopeKey,b.scopeKey);
  assert.deepEqual(graphReadPlan({layout:'hub',cone:1},[]).options,{layout:'hub'});
});
const base={view:'3d',when:1,unlinked:'show',edgeOff:[]},fresh={cached:false,mounted:false};
test('only a fresh, unfiltered map read is staged',()=>{
  assert.equal(wantsStagedRead(base,fresh),true);
  for(const state of [{cached:true},{mounted:true}])assert.equal(wantsStagedRead(base,{...fresh,...state}),false);
  for(const change of [{when:.5},{unlinked:'hide'},{edgeOff:['directed']}])
    assert.equal(wantsStagedRead({...base,...change},fresh),false);
});
