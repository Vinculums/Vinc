import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';
const js = ts.transpileModule(await readFile(new URL('../src/topic-detail.ts', import.meta.url), 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { detailState, detailFailure, topicDetailHTML } = await import(
  `data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
const copy = new Proxy({ relsMore: n => `More ${n}` }, { get: (o, k) => o[k] ?? k });
const esc = s => s.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;');
const topic = { path: 'project/child', parent: {id:'domain:project', title:'project', domain:'project'},
  child_topics:1, direct_nodes:1, descendant_nodes:2, subtree_nodes:3,
  structure:{total:2, offset:0, truncated:false, items:[
    {rel:'PARENT_OF', id:'domain:project/child/leaf', title:'project/child/leaf',domain:'project/child'},
    {rel:'IN_DOMAIN',id:'record:n',title:'A <note>',domain:'project/child'}]}};

test('failures and access restrictions cannot become a successful empty result', () => {
  assert.equal(detailState({executed:false}), 'failed');
  assert.equal(detailState({executed:true,error:'offline'}), 'failed');
  assert.equal(detailState({executed:false,status:'query_failed'}), 'failed');
  assert.equal(detailState({executed:false,status:'unavailable'}), 'unavailable');
  assert.equal(detailState({executed:false,status:'unsupported_kind'}), 'unsupported');
  assert.equal(detailState({executed:true,links:{executed:true,total:0}}), 'ready');
  for (const status of [401,403,404]) assert.equal(detailFailure({status}), 'unavailable');
  for (const status of [0,500,502,503]) assert.equal(detailFailure({status}), 'failed');
});
test('topic rows link ancestors, children and direct members with distinct count labels', () => {
  const html = topicDetailHTML(topic,copy,esc);
  assert.match(html,/data-topic-go="domain:project"/);
  assert.match(html,/data-topic-go="domain:project\/child\/leaf" data-dom="project\/child\/leaf"/);
  assert.match(html,/data-topic-go="record:n"/);
  for (const label of ['topicDirect','topicDescendants','topicSubtree','topicRelations'])
    assert.ok(html.includes(label));
  assert.ok(html.includes('A &lt;note>'));
  for (const invented of ['node-basics','node-props','node-source','nd-connect','nd-del'])
    assert.ok(!html.includes(invented));
});
test('an empty direct list remains independent of descendants and a root has no invented parent', () => {
  const html = topicDetailHTML({...topic,parent:null,direct_nodes:0,structure:{...topic.structure,items:[]}},copy,esc);
  assert.ok(html.includes('topicRoot'));
  assert.match(html,/topicDirect<\/span><b>0/);
  assert.match(html,/topicSubtree<\/span><b>3/);
});
test('membership drift is visible and every continuation page has a way back', () => {
  const html = topicDetailHTML({...topic,consistency:{status:'membership_drift'},
    structure:{...topic.structure,total:60,offset:24,truncated:true,next_offset:48}},copy,esc);
  assert.ok(html.includes('topicDrift'));
  assert.ok(html.includes('data-topic-more="0"'));
  assert.ok(html.includes('data-topic-more="48"'));
});
