// 읽기 설정: 저장된 값을 읽어 `<html>` 의 `data-doc-*` 로 얹는다. 모르는 값은 그 칸만 기본값이고,
// 기본값인 칸은 속성을 지워 CSS 의 기본이 그대로 살게 한다(doc-options.ts).
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { loadTs, COPY_STUB, COMPONENTS_STUB } from './ts-load.mjs';

const t = await loadTs('doc-options.ts', {
  './copy': COPY_STUB,
  './icons': 'export const icon = () => "";',
  './motion': 'export const popIn = () => null; export const popOut = async () => {};',
  './components': COMPONENTS_STUB + '\nexport const wireOutsideDismiss = () => () => {};',
  './float-panel': 'export const floatCeiling = () => 0;',
});

const fakeRoot = () => {
  const a = new Map();
  return { a, setAttribute: (k, v) => a.set(k, v), removeAttribute: (k) => a.delete(k) };
};

test('nothing stored reads as the defaults', () => {
  assert.deepEqual(t.readDocPrefs(null), t.DOC_DEFAULTS);
  assert.deepEqual(t.readDocPrefs('not json'), t.DOC_DEFAULTS);
});

test('an unknown value falls back for that field only', () => {
  const p = t.readDocPrefs(JSON.stringify({ text: 'huge', width: 'wide', lines: 'relaxed' }));
  assert.deepEqual(p, { text: 'medium', width: 'wide', lines: 'relaxed' });
});

test('defaults clear the attributes and choices set them', () => {
  const r = fakeRoot();
  t.applyDocPrefs({ text: 'large', width: 'standard', lines: 'compact' }, r);
  assert.deepEqual([...r.a], [['data-doc-text', 'large'], ['data-doc-lines', 'compact']]);
  t.applyDocPrefs(t.DOC_DEFAULTS, r);
  assert.equal(r.a.size, 0);
});
