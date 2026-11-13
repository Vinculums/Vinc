import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';
// 그래프 읽기가 무엇으로 만들어지는가: 읽기 계획(view-graph-read)과 기기별 보기 설정(view-prefs).
const load=async name=>{
  const js=ts.transpileModule(await readFile(new URL(`../src/${name}`,import.meta.url),'utf8'),
    {compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
};
const {graphReadPlan}=await load('view-graph-read.ts');
const {loadPrefs,savePrefs}=await load('view-prefs.ts');

test('the plan asks for the hub layout with the sorted scope key the retained entries use',()=>{
  const plan=graphReadPlan(['b','a']);
  // 보관소와 스페이스 보관본이 이 문자열로 항목을 찾는다: 모양이 바뀌면 보관본을 못 찾는다.
  assert.equal(plan.scopeKey,JSON.stringify({domains:['a','b'],layout:'hub',cone:1}));
  assert.deepEqual(plan.options,{domains:['b','a'],layout:'hub'});
});
test('the same request in any domain order shares one key and no scope sends no domains',()=>{
  assert.equal(graphReadPlan(['x','y']).scopeKey,graphReadPlan(['y','x']).scopeKey);
  assert.deepEqual(graphReadPlan([]).options,{layout:'hub'});
  assert.equal(graphReadPlan([]).scopeKey,'{"domains":[],"layout":"hub","cone":1}');
});

const withStorage=(body)=>{
  const saved=new Map();
  globalThis.localStorage={getItem:k=>saved.get(k)??null,setItem:(k,v)=>saved.set(k,String(v))};
  try { body(saved); } finally { globalThis.localStorage=undefined; }
};
const KEY='vinc.view.prefs';
test('no stored value and an unreadable one give the defaults',()=>withStorage(saved=>{
  assert.deepEqual(loadPrefs(),{view:'compass',railW:20});
  for (const bad of ['{not json','null','5','[]']) {
    saved.set(KEY,bad);
    assert.deepEqual(loadPrefs(),{view:'compass',railW:20},bad);
  }
}));
// 이전 판(판 없음 · 2 · 3 · 4)의 저장값은 보기 설정 열일곱 칸을 들고 있다. 어느 판이든 읽히고,
// 남은 두 칸만 나온다: 보기는 무엇이든 나침반, 문서 패널 폭은 그 사람이 끌어 둔 값(범위 안으로).
test('a stored value of every earlier version still loads',()=>withStorage(saved=>{
  const v4={view:'3d',size:'links',nodeScale:0.7,cone:0.7,edgeScale:1,labels:'hover',arrows:true,edges:'always',
    spread:1,unlinked:'show',layout:'temporalRoots',railW:33,timeBasis:'recorded',when:1,edgeOff:[],pv:4};
  for (const [stored,want] of [
    [v4,{view:'compass',railW:33}],
    [{...v4,view:'compass',railW:31.5,pv:3,layout:'hub'},{view:'compass',railW:31.5}],
    [{view:'2d',layout:'hub',edges:'focus',edgeOff:['directed'],railW:500,pv:2},{view:'compass',railW:120}],
    [{view:'3d',nodeScale:1,cone:1,edges:'bring',layout:'rank',railW:27,timelineBasis:'content'},{view:'compass',railW:27}],
    [{view:'timeline',railW:4},{view:'compass',railW:16}],
    [{view:'bundle',railW:'wide'},{view:'compass',railW:20}],
    [{railW:0,pv:4},{view:'compass',railW:20}],
  ]) {
    saved.set(KEY,JSON.stringify(stored));
    assert.deepEqual(loadPrefs(),want,JSON.stringify(stored));
  }
}));
test('a saved value carries its version and only the two fields, and loads back unchanged',()=>withStorage(saved=>{
  saved.set(KEY,JSON.stringify({view:'3d',railW:33,edges:'always',edgeOff:[],nodeScale:0.7,pv:4}));
  savePrefs({...loadPrefs(),railW:42});
  assert.deepEqual(JSON.parse(saved.get(KEY)),{view:'compass',railW:42,pv:5});
  assert.deepEqual(loadPrefs(),{view:'compass',railW:42});
}));
