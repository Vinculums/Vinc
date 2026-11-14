import assert from 'node:assert/strict';
import { test } from 'node:test';
import { loadTs, tsUrl } from './ts-load.mjs';

const idsUrl = await tsUrl('agent-ids.ts');
const ids = await import(idsUrl);
const { compose, repeats, repeatedInField, layoutMap, listsOf, mapHeight, policySlug, idSlugOf } = await loadTs('agent-ontology.ts',
  { './agent-ids': idsUrl });

// 2026-11-05 소유자 그래프의 모양 그대로: Maker 의 불변 목록, 읽기 전용 정책 하나, 그 정책이 다스리는 절차.
const maker = { disallowed_tools: ['vinc_delete', 'vinc_merge_nodes'], capabilities: { deny: [] },
  person_only: [{ action: 'merge', reason: 'merge' }], never: ['widen scope'],
  gates: [{ id: 'person_only', check: { type: 'person_only' }, on_fail: 'interrupt' }] };
const research = { disallowed_tools: [], capabilities: { deny: [] }, never: ['decide for the owner'],
  person_only: [{ action: 'scope', reason: 'scope' }], gates: [] };
const readOnly = { id: 'decision:policy-read-only-procedures',
  rule: { disallowed_tools: ['Edit', 'Write', 'NotebookEdit'], capabilities: { deny: ['fs.write'] } } };

test('the composed list names where each item comes from: agent, procedure, policy', () => {
  const got = compose(maker, research, [readOnly]);
  const of = (kind, item) => got.find((x) => x.kind === kind && x.item === item)?.origin;
  assert.equal(of('tool', 'vinc_delete'), 'agent');
  assert.equal(of('never', 'decide for the owner'), 'procedure');
  assert.equal(of('person_only', 'scope'), 'procedure');
  assert.equal(of('tool', 'Edit'), 'decision:policy-read-only-procedures');
  assert.equal(of('capability', 'fs.write'), 'decision:policy-read-only-procedures');
  assert.equal(of('gate', 'person_only'), 'agent');
});

test('a policy never widens the agent and an item is counted once, at its first origin', () => {
  const widening = { id: 'decision:policy-x', rule: { disallowed_tools: ['vinc_delete'] } };
  const got = compose(maker, research, [widening]);
  assert.equal(got.filter((x) => x.item === 'vinc_delete').length, 1);
  assert.equal(got.find((x) => x.item === 'vinc_delete').origin, 'agent');
  // without the policy edge the wall is absent
  assert.equal(compose(maker, research, []).some((x) => x.item === 'Edit'), false);
  // with no procedure only the agent's own lists apply, policies included or not
  assert.deepEqual(compose(maker, null, [readOnly]).map((x) => x.origin).filter((o) => o !== 'agent'), []);
});

test('a procedure that repeats its agent is caught, field by field and while typing', () => {
  assert.deepEqual(repeats(maker, research), []);
  const copied = { ...research, disallowed_tools: ['vinc_delete', 'Bash'], person_only: [{ action: 'merge', reason: 'x' }] };
  assert.deepEqual(repeats(maker, copied), [{ kind: 'tool', item: 'vinc_delete' }, { kind: 'person_only', item: 'merge' }]);
  assert.deepEqual(repeatedInField(maker, 'tool', [' vinc_delete ', 'Bash', '']), ['vinc_delete']);
});

test('legacy shapes read without crashing: string person_only, missing lists', () => {
  assert.deepEqual(listsOf({ person_only: ['merge'], capabilities: 'bad' }).person_only, ['merge']);
  assert.deepEqual(listsOf({}).tool, []);
});

test('the map lays the execute agent left, the verify agent right, and is the same every time', () => {
  const agents = [{ id: 'concept:agent-checker', title: 'Checker', tier: 'verify' }, { id: 'concept:agent-maker', title: 'Maker', tier: 'execute' }];
  const procs = [
    { id: 'concept:procedure-a', title: 'A', agent: 'concept:agent-maker', policies: [readOnly.id] },
    { id: 'concept:procedure-b', title: 'B', agent: 'concept:agent-maker', policies: [] },
    { id: 'concept:procedure-c', title: 'C', agent: 'concept:agent-checker', policies: [] }];
  const pols = [{ id: readOnly.id, title: 'Read-only' }];
  const one = layoutMap(agents, procs, pols), two = layoutMap(agents, procs, pols);
  assert.deepEqual(one, two);
  const node = (id) => one.nodes.find((n) => n.id === id);
  assert.ok(node('concept:agent-maker').x < node('concept:agent-checker').x);
  assert.ok(one.nodes.filter((n) => n.kind === 'procedure').every((n) => n.r >= 12), 'procedure dots are big enough to press');
  // 선은 저장된 관계 그대로다: 정책은 ABOUT(끈), 절차와 Agent·Checker 와 Maker 는 RELATED_TO.
  assert.deepEqual(one.links.filter((l) => l.type === 'ABOUT'), [{ from: readOnly.id, to: 'concept:procedure-a', type: 'ABOUT' }]);
  assert.ok(one.links.some((l) => l.from === 'concept:agent-checker' && l.to === 'concept:agent-maker' && l.type === 'RELATED_TO'));
  assert.ok(one.links.some((l) => l.from === 'concept:procedure-b' && l.to === 'concept:agent-maker' && l.type === 'RELATED_TO'));
  // a link to a policy the map does not hold is dropped, not drawn to nowhere
  const orphan = layoutMap(agents, [{ ...procs[0], policies: ['decision:policy-gone'] }], []);
  assert.equal(orphan.links.some((l) => l.from === 'decision:policy-gone'), false);
});

test('fifteen procedures on one side never put two labels closer than 26px, and stay inside the graph', () => {
  // 2026-11-05 소유자 그래프: Maker 15, Checker 5. 각도로 고르게 놓던 첫 배치는 위아래 이름표를 겹쳤다.
  const agents = [{ id: 'concept:agent-maker', title: 'Maker', tier: 'execute' }, { id: 'concept:agent-checker', title: 'Checker', tier: 'verify' }];
  const procs = [...Array(15)].map((_, i) => ({ id: `concept:procedure-m${i}`, title: `Maker procedure ${i}`, agent: 'concept:agent-maker', policies: [] }))
    .concat([...Array(5)].map((_, i) => ({ id: `concept:procedure-c${i}`, title: `Checker procedure ${i}`, agent: 'concept:agent-checker', policies: [] })));
  for (const width of [560, 640, 760, 960]) {
    const height = mapHeight(procs, agents);
    const { nodes } = layoutMap(agents, procs, [], width, height);
    for (const side of ['end', 'start']) {
      const ys = nodes.filter((n) => n.kind === 'procedure' && n.anchor === side).map((n) => n.y).sort((a, b) => a - b);
      ys.slice(1).forEach((y, i) => assert.ok(y - ys[i] >= 26, `${width}px ${side}: ${ys[i]} and ${y}`));
    }
    for (const n of nodes.filter((n) => n.kind === 'procedure')) {
      if (n.anchor === 'end') assert.ok(n.x - 18 - 140 >= 0, `${width}px: ${n.id} label leaves on the left (${n.x})`);
      if (n.anchor === 'start') assert.ok(n.x + 18 + 140 <= width, `${width}px: ${n.id} label leaves on the right (${n.x})`);
      assert.ok(n.y >= 60 && n.y <= height - 20, `${n.id} y ${n.y}`);
    }
    // 옆 칸이 편집기라 그래프가 좁아도 두 Agent 는 떨어져 있다(560px 에서 Maker 와 Checker 가 겹쳤다).
    const [m, c] = ['concept:agent-maker', 'concept:agent-checker'].map((id) => nodes.find((n) => n.id === id));
    assert.ok(c.x - m.x >= 130, `${width}px: agents ${m.x} and ${c.x}`);
  }
});

// `api/role_contract._SLUG` 와 같은 식: 소문자·숫자 덩어리를 하이픈 하나로 잇는다.
const ROLE_SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

test('a title that is cut on a hyphen gives an id tail that does not end in one', () => {
  // 앞 47자 뒤에 구분자가 오면 48자 자르기가 하이픈에서 끝난다.
  const cases = [
    ['Release engineering and deployments coordinator for the platform', 'release-engineering-and-deployments-coordinator'],
    ['x'.repeat(47) + ' y', 'x'.repeat(47)],
    ['a'.repeat(47) + ' !!! b', 'a'.repeat(47)],
    ['a'.repeat(47) + '  / - bcd', 'a'.repeat(47)],
  ];
  for (const [title, want] of cases) {
    const got = idSlugOf(title);
    assert.equal(got, want, title);
    assert.ok(ROLE_SLUG.test(`${got}`), `${title} -> ${got}`);
    assert.ok(got.length <= 48);
  }
});

test('ordinary titles keep their id tail', () => {
  const same = [
    ['Maker', 'maker'],
    ['Release Manager', 'release-manager'],
    ['  Backend   Engineer, Core  ', 'backend-engineer-core'],
    ['QA / Test (Web)', 'qa-test-web'],
    ['Café Manager', 'cafe-manager'],
    ['v2 reviewer', 'v2-reviewer'],
    ['a'.repeat(48), 'a'.repeat(48)],
    ['a'.repeat(60), 'a'.repeat(48)],
    ['a'.repeat(46) + ' b', 'a'.repeat(46) + '-b'],
    ['지식 그래프', ''],
    ['!!! ???', ''],
  ];
  for (const [title, want] of same) assert.equal(idSlugOf(title), want, title);
});

test('over many titles the tail is valid, and equals the plain 48-character cut unless that cut ends in a hyphen', () => {
  // 자르기까지만 한 값. 하이픈으로 끝나지 않으면 idSlugOf 와 한 글자도 다르지 않아야 한다.
  const cut = (t) => t.toLowerCase().normalize('NFKD').replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 48);
  const words = ['a', 'ab', 'release', 'engineering', 'and', 'deployments', 'coordinator', 'Café', 'QA', 'v2', 'platform', 'customer', 'support'];
  const seps = [' ', '  ', '-', ' / ', ', ', '!'];
  let seed = 20261114, onHyphen = 0, total = 0;
  const next = (n) => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed % n; };
  for (let i = 0; i < 4000; i++) {
    let title = '';
    for (let w = 2 + next(9); w > 0; w--) title += words[next(words.length)] + seps[next(seps.length)];
    const plain = cut(title), got = idSlugOf(title);
    total++;
    if (plain.endsWith('-')) { onHyphen++; assert.equal(got, plain.replace(/-+$/, ''), title); }
    else assert.equal(got, plain, title);
    assert.ok(ROLE_SLUG.test(got), `${title} -> ${got}`);
  }
  assert.ok(onHyphen > 50 && onHyphen < total, `the sweep must hit both branches (${onHyphen}/${total})`);
});

test('id prefixes are the hub values and a slug takes only a leading prefix off', () => {
  assert.equal(ids.AGENT_ID_PREFIX, 'concept:agent-');
  assert.equal(ids.PROCEDURE_ID_PREFIX, 'concept:procedure-');
  assert.equal(ids.POLICY_ID_PREFIX, 'decision:policy-');
  assert.equal(policySlug('decision:policy-read-only-procedures'), 'read-only-procedures');
  assert.equal(policySlug('x-decision:policy-y'), 'x-decision:policy-y');
  assert.equal(ids.withoutPrefix('concept:agent-dev-core', ids.AGENT_ID_PREFIX), 'dev-core');
  assert.equal(ids.withoutPrefix('concept:procedure-a', ids.AGENT_ID_PREFIX), 'concept:procedure-a');
});
