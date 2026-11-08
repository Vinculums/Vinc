import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

// approvals.ts has no imports, so it loads as is.
const src = await readFile(new URL('../src/approvals.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(src, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const A = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);

const NOW = Date.parse('2026-11-08T10:00:00Z');
const row = (over = {}) => ({
  id: 'episode:run-a-1234abcd', run_id: 'a', role: 'concept:agent-maker', space: 'user:u-1',
  title: 'Merge the queue', task: 't', subject: '', host: 'claude-code', step: 2, ask: 3,
  started_at: '2026-11-08T09:00:00Z', asked_at: '2026-11-08T09:30:00Z', expires_at: '2026-11-08T10:30:00Z',
  expired: false,
  decision: { kind: 'person_only', question: 'May I merge?', actions: ['merge'],
    requests: [{ action: 'merge', reason: 'gh pr merge 1207 --squash\n  then deploy', kind: 'person_only' }],
    checks: [], asked_at: '2026-11-08T09:30:00Z', expires_at: '2026-11-08T10:30:00Z' },
  ...over,
});

test('the list keeps each run and its reasons exactly as the hub sent them', () => {
  const [r] = A.normalizeRuns({ kind: 'runs.waiting', runs: [row()] });
  assert.equal(r.id, 'episode:run-a-1234abcd');
  assert.equal(r.ask, 3);
  assert.equal(r.decision.requests[0].reason, 'gh pr merge 1207 --squash\n  then deploy');
  assert.equal(r.decision.question, 'May I merge?');
});

test('rows that cannot be answered are dropped, not guessed', () => {
  const runs = A.normalizeRuns({ runs: [row({ id: '' }), row({ ask: 0 }), row({ ask: 'x' }), null, 7, row()] });
  assert.equal(runs.length, 1);
  assert.deepEqual(A.normalizeRuns({}), []);
  assert.deepEqual(A.normalizeRuns(null), []);
});

test('the answer sends back the ask from the list unchanged', () => {
  const [r] = A.normalizeRuns({ runs: [row({ ask: 7 })] });
  assert.deepEqual(A.answerBody(r, 'approve', ''), { run: 'episode:run-a-1234abcd', ask: 7, answer: 'approve' });
  assert.deepEqual(A.answerBody(r, 'decline', '  not now  '), { run: 'episode:run-a-1234abcd', ask: 7, answer: 'decline', text: 'not now' });
});

test('a reply alone needs text and every answer stays under the hub limit', () => {
  assert.equal(A.canSend('reply', ''), false);
  assert.equal(A.canSend('reply', '   '), false);
  assert.equal(A.canSend('reply', 'wait'), true);
  assert.equal(A.canSend('approve', ''), true);
  assert.equal(A.canSend('approve', 'x'.repeat(A.ANSWER_TEXT_MAX + 1)), false);
});

test('expiry follows the hub flag and the clock', () => {
  const [open, flagged, late] = A.normalizeRuns({ runs: [row(), row({ id: 'b', expired: true }),
    row({ id: 'c', expires_at: '2026-11-08T09:59:00Z' })] });
  assert.equal(A.isExpired(open, NOW), false);
  assert.equal(A.isExpired(flagged, NOW), true);
  assert.equal(A.isExpired(late, NOW), true);
  assert.equal(A.openCount([open, flagged, late], NOW), 1);
  assert.equal(A.minutesLeft(open, NOW), 30);
  assert.equal(A.minutesLeft(late, NOW), null);
});

test('the path deep link becomes the hash route the app uses', () => {
  assert.deepEqual(A.deepLinkTarget('/app/approvals/episode%3Arun-a-1', '/app/'), { path: '/app/', hash: '#approvals/episode%3Arun-a-1' });
  assert.deepEqual(A.deepLinkTarget('/app/approvals/episode:run-a-1/', '/app/'), { path: '/app/', hash: '#approvals/episode%3Arun-a-1' });
  assert.deepEqual(A.deepLinkTarget('/app/approvals', '/app/'), { path: '/app/', hash: '#approvals' });
  assert.deepEqual(A.deepLinkTarget('/approvals/x', '/'), { path: '/', hash: '#approvals/x' });
  assert.equal(A.deepLinkTarget('/app/', '/app/'), null);
  assert.equal(A.deepLinkTarget('/app/approvalsx', '/app/'), null);
  assert.equal(A.runOfHash('#approvals/episode%3Arun-a-1'), 'episode:run-a-1');
  assert.equal(A.runOfHash('#approvals'), '');
  assert.equal(A.runOfHash('#home'), '');
  assert.equal(A.deepLinkPath('/app/', 'episode:run-a-1'), '/app/approvals/episode%3Arun-a-1');
  assert.equal(A.roleLabel('concept:agent-maker'), 'agent-maker');
});
