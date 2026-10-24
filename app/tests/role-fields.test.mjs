import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

const src = await readFile(new URL('../src/role-fields.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(src, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { listLines, linesOf, fieldChanged, foldPersonOnly } = await import(
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
