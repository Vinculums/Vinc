import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

const src = await readFile(new URL('../src/role-fields.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(src, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { listLines, linesOf, fieldChanged, foldPersonOnly, actLines, foldActPolicy, budgetFields, foldBudget,
        capabilityFields, foldCapabilities, samePolicy } = await import(
  `data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);

const structured = [
  { action: 'merge_or_deploy', reason: 'Main is production, so a person merges.' },
  { action: 'publish_release', reason: 'A person signs the release.' },
];
const legacy = ['publishing the release', 'approving a payment'];

test('structured items are shown as action: reason, never as [object Object]', () => {
  const lines = listLines(structured);
  assert.deepEqual(lines, ['merge_or_deploy: Main is production, so a person merges.',
                           'publish_release: A person signs the release.']);
  assert.ok(!lines.join('\n').includes('[object Object]'));
  assert.deepEqual(listLines(legacy), legacy);
  assert.deepEqual(listLines(undefined), []);
  assert.deepEqual(listLines([{ other: 1 }]), ['{"other":1}']);
});

test('an untouched field reads as unchanged, whitespace and blank lines included', () => {
  const text = listLines(structured).join('\n');
  assert.equal(fieldChanged(text, structured), false);
  assert.equal(fieldChanged(`  ${text}\n\n`, structured), false);
  assert.equal(fieldChanged(text + '\nextra_step: one more', structured), true);
  assert.equal(fieldChanged('', []), false);
  assert.equal(fieldChanged('', undefined), false);
});

test('structured lines fold back into the same objects', () => {
  const folded = foldPersonOnly(linesOf(listLines(structured).join('\n')), structured);
  assert.equal(folded.ok, true);
  assert.deepEqual(folded.value, structured);
});

test('a structured role refuses a line that is not action_key: reason, or a repeated key', () => {
  const bad = foldPersonOnly(['merge_or_deploy: a person merges', 'publishing the release'], structured);
  assert.deepEqual(bad, { ok: false, bad: ['publishing the release'] });
  const dup = foldPersonOnly(['merge_or_deploy: one', 'merge_or_deploy: two'], structured);
  assert.deepEqual(dup, { ok: false, bad: ['merge_or_deploy: two'] });
  const badKey = foldPersonOnly(['Merge-Or-Deploy: a person merges'], structured);
  assert.equal(badKey.ok, false);
});

test('a legacy or new list stays strings unless every line is structured, and never mixes', () => {
  assert.deepEqual(foldPersonOnly(legacy, legacy), { ok: true, value: legacy });
  assert.deepEqual(foldPersonOnly(['publish_release: a person signs it', 'approving a payment'], undefined),
                   { ok: true, value: ['publish_release: a person signs it', 'approving a payment'] });
  assert.deepEqual(foldPersonOnly(['publish_release: a person signs it'], undefined),
                   { ok: true, value: [{ action: 'publish_release', reason: 'a person signs it' }] });
  assert.deepEqual(foldPersonOnly([], structured), { ok: true, value: [] });
});

test('act policy lines round-trip and refuse loosening a person-only action', () => {
  const stored = { merge: 'ask_first', comment: 'act_and_report' };
  const lines = actLines(stored);
  assert.deepEqual(lines, ['merge: ask_first', 'comment: act_and_report']);
  const folded = foldActPolicy(lines, ['merge']);
  assert.ok(folded.ok && samePolicy(folded.value, stored));
  assert.deepEqual(foldActPolicy([], []), { ok: true, value: undefined });
  assert.equal(foldActPolicy(['merge: act'], ['merge']).ok, false);
  assert.equal(foldActPolicy(['merge: whenever'], []).ok, false);
  assert.equal(foldActPolicy(['Merge: act'], []).ok, false);
  assert.equal(foldActPolicy(['merge: never', 'merge: act'], []).ok, false);
  assert.deepEqual(actLines(['not', 'an object']), []);
});

test('budget fields take numbers of 0 or more and whole counts', () => {
  const stored = { tool_calls: 40, cost_usd: 1.5 };
  const fields = budgetFields(stored);
  assert.equal(fields.tool_calls, '40');
  assert.equal(fields.tokens, '');
  const folded = foldBudget(fields);
  assert.ok(folded.ok && samePolicy(folded.value, stored));
  assert.deepEqual(foldBudget({}), { ok: true, value: undefined });
  assert.deepEqual(foldBudget({ tool_calls: '2.5' }), { ok: false, bad: ['tool_calls'] });
  assert.deepEqual(foldBudget({ cost_usd: '-1' }), { ok: false, bad: ['cost_usd'] });
  assert.deepEqual(foldBudget({ wall_seconds: 'soon' }), { ok: false, bad: ['wall_seconds'] });
  assert.ok(foldBudget({ wall_seconds: '90.5' }).ok);
});

test('capability lists split on commas and keep each id in one list', () => {
  const stored = { allow: ['vinc.read', 'github.pr.read'], deny: ['shell.*'] };
  const fields = capabilityFields(stored);
  assert.equal(fields.allow, 'vinc.read, github.pr.read');
  assert.equal(fields.approve, '');
  const folded = foldCapabilities(fields);
  assert.ok(folded.ok && samePolicy(folded.value, stored));
  assert.deepEqual(foldCapabilities({ allow: ' , ' }), { ok: true, value: undefined });
  assert.equal(foldCapabilities({ allow: 'GitHub.Merge' }).ok, false);
  assert.equal(foldCapabilities({ allow: 'vinc.read', deny: 'vinc.read' }).ok, false);
});
