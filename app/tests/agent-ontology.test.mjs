import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

const src = await readFile(new URL('../src/agent-ontology.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(src, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { compose, repeats, repeatedInField, layoutMap, listsOf, mapHeight } = await import(
  `data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);

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
  assert.deepEqual(one.links.filter((l) => l.kind === 'governs'), [{ from: readOnly.id, to: 'concept:procedure-a', kind: 'governs' }]);
  assert.deepEqual(one.links.filter((l) => l.kind === 'returns'), [{ from: 'concept:agent-checker', to: 'concept:agent-maker', kind: 'returns' }]);
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
