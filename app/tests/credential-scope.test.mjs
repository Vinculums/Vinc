import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';
const js=ts.transpileModule(await readFile(new URL('../src/credential-scope.ts',import.meta.url),'utf8'),
 {compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const {ScopedCredentials}=await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
const A='user:00000000-0000-4000-8000-000000000001',B='user:00000000-0000-4000-8000-000000000002';
const T='00000000-0000-4000-8000-000000000003',U='00000000-0000-4000-8000-000000000004';
const identity=(subject,team=null,status='active')=>({subject,credential_team:team,key_scope:team||'personal',teams:team?[{id:team,status}]:[]});
function setup(){
 const state={personal:'personal-A',proof:{'personal-A':identity(A),'personal-B':identity(B),'team-A':identity(A,T),'other-team':identity(A,U),'other-user':identity(B,T)},stored:new Map(),saved:[],failSave:false};
 const vault=new ScopedCredentials({personal:async()=>state.personal,team:async(s,t)=>state.stored.get(s+t)||'',
 save:async(s,t,k)=>{if(state.failSave)throw Error('team_key_vault_unavailable');state.saved.push([s,t,k]);state.stored.set(s+t,k);},inspect:async k=>state.proof[k]});
 return {state,vault};
}
test('a team never falls back to the personal credential',async()=>{const {vault}=setup();assert.equal(await vault.personal(),'personal-A');assert.equal(await vault.forTeam(T),'');});
test('a separately issued key is bound to the confirmed account and team',async()=>{const {state,vault}=setup();await vault.bind(T,'team-A');assert.deepEqual(state.saved,[[A,T,'team-A']]);assert.equal(await vault.forTeam(T),'team-A');assert.equal(await vault.personal(),'personal-A');assert.equal(await vault.forTeam(U),'');});
test('personal, different team and different account keys cannot be bound',async()=>{for(const key of ['personal-A','other-team','other-user']){const {state,vault}=setup();await assert.rejects(vault.bind(T,key),/key_graph_scope_mismatch/);assert.deepEqual(state.saved,[]);}});
test('inactive teams and unavailable identity protocols fail closed',async()=>{const {state,vault}=setup();state.proof['team-A']=identity(A,T,'suspended');await assert.rejects(vault.bind(T,'team-A'));state.proof['personal-A']={teams:[]};vault.forget();await assert.rejects(vault.forTeam(T),/credential_identity_unavailable/);});
test('switching signed-in accounts cannot read the old account team key',async()=>{const {state,vault}=setup();await vault.bind(T,'team-A');state.personal='personal-B';vault.forget();assert.equal(await vault.forTeam(T),'');assert.equal(await vault.personal(),'personal-B');});
test('vault save failure leaves no usable cached credential',async()=>{const {state,vault}=setup();state.failSave=true;await assert.rejects(vault.bind(T,'team-A'),/vault_unavailable/);assert.equal(await vault.forTeam(T),'');});
test('an old identity response cannot bind a key after account change',async()=>{let finish;const saved=[];let first=true;const vault=new ScopedCredentials({personal:async()=>'personal',team:async()=>'',save:async(...args)=>saved.push(args),inspect:async()=>first?(first=false,identity(A)):new Promise(r=>finish=r)});const pending=vault.bind(T,'team');await new Promise(r=>setTimeout(r,0));vault.forget();finish(identity(A,T));await assert.rejects(pending,/credential_changed/);assert.deepEqual(saved,[]);});
test('stored revoked or rebound keys fail validation on the next session',async()=>{const {state,vault}=setup();await vault.bind(T,'team-A');vault.forget();state.proof['team-A']=identity(A,U);await assert.rejects(vault.forTeam(T),/key_graph_scope_mismatch/);});
