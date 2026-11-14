import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';
const transpile = async name => ts.transpileModule(await readFile(new URL(`../src/${name}`, import.meta.url), 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const dataUrl = code => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
const js = (await transpile('topic-detail.ts'))
  .replace('"./node-topic"', JSON.stringify(dataUrl(await transpile('node-topic.ts'))));
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

// 데이터 호출의 실패 모양(api-error.ts): 오류 봉투를 읽는 곳 하나와, 사이드카 · 웹 경로가 실패 응답에서 짓는 ApiError.
// 레일의 상세 상태(detailFailure)는 그 실패의 status 만 읽는다.
const E = await import(`data:text/javascript;base64,${Buffer.from(await transpile('api-error.ts')).toString('base64')}`);

test('the envelope parser reads the account and the sidecar shapes and nothing else', () => {
  assert.deepEqual(E.parseEnvelope({ error: { code: 'not_authenticated', message: 'Key rejected', extra: { reason: 'invalid_key' }, reason: 'later' } }),
    { code: 'not_authenticated', detail: 'Key rejected', reason: 'invalid_key' });
  assert.deepEqual(E.parseEnvelope({ error: { code: 'forbidden', reason: 'team_role_required' } }),
    { code: 'forbidden', detail: 'forbidden', reason: 'team_role_required' });
  assert.deepEqual(E.parseEnvelope({ error: 'write_conflict' }), { code: 'write_conflict', detail: 'write_conflict', reason: '' });
  for (const b of [{}, null, undefined, 'text', 7, { error: null }, { error: '' }])
    assert.deepEqual(E.parseEnvelope(b), { code: '', detail: '', reason: '' }, JSON.stringify(b));
});

test('a store that cannot open (503) says so with its reason, apart from every other failure', () => {
  const e = E.localFailure(503, JSON.stringify({ executed: false, error: 'no reachable store', open_error: 'lock held by pid 42' }));
  assert.ok(e instanceof E.ApiError && e instanceof Error);
  assert.equal(e.message, 'lock held by pid 42');
  assert.deepEqual([e.status, e.storeDown, e.reason, e.code, e.body], [503, true, 'lock held by pid 42', 'no reachable store', undefined]);
  const bare = E.localFailure(503, 'Service Unavailable');
  assert.deepEqual([bare.message, bare.storeDown, bare.reason, bare.code], ['no reachable store', true, '', undefined]);
  const env = E.localFailure(503, JSON.stringify({ error: { code: 'store_closed' } }));
  assert.deepEqual([env.message, env.code], ['[object Object]', 'store_closed']);
  assert.equal(E.localFailure(500, '{}').storeDown, undefined);
});

test('other failures keep the status and the raw body in the message, and the code and body when it is JSON', () => {
  const conflict = E.localFailure(409, '{"error":"write_conflict"}');
  assert.deepEqual([conflict.message, conflict.status, conflict.code, conflict.body],
    ['409 {"error":"write_conflict"}', 409, 'write_conflict', { error: 'write_conflict' }]);
  const notFound = E.localFailure(404, '{"error":{"code":"not_found","reason":"gone"}}');
  assert.deepEqual([notFound.code, notFound.body.error.reason], ['not_found', 'gone']);
  const html = E.localFailure(502, '<html><style>p { margin: 0 }</style>Bad gateway</html>');
  assert.deepEqual([html.message.slice(0, 10), html.status, html.code, html.body], ['502 <html>', 502, undefined, undefined]);
  const big = E.localFailure(413, 'Payload Too Large');
  assert.deepEqual([big.message, big.code, big.account, big.storeDown], ['413 Payload Too Large', undefined, undefined, undefined]);
  for (const status of [401, 403, 404]) assert.equal(detailFailure(E.localFailure(status, '{"error":"x"}')), 'unavailable');
  for (const status of [409, 413, 500, 502, 503, 504]) assert.equal(detailFailure(E.localFailure(status, 'x')), 'failed');
});

test('an account failure carries its kind, and the body only when the envelope had no code or message', () => {
  const e = new E.ApiError('401 Key rejected', { status: 401, code: 'not_authenticated', account: 'signIn' });
  assert.deepEqual([e.status, e.code, e.account, e.body, e.storeDown], [401, 'not_authenticated', 'signIn', undefined, undefined]);
  const bare = new E.ApiError('500 {}', { status: 500, code: '', account: 'other', body: {} });
  assert.deepEqual(bare.body, {});
});
