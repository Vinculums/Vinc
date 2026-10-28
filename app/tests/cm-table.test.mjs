// 표 칸 편집: 칸 하나를 고치면 그 칸이 든 줄만 다시 써지고, 나머지 원문은 바이트 그대로다.
// 편집기의 표 위젯이 칸 입력을 원문으로 되돌릴 때 이 함수들을 쓴다(codemirror-editor.ts TableWidget).
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { loadTs } from './ts-load.mjs';

const t = await loadTs('cm-table.ts');

const SRC = [
  '| Name | Value |',
  '|:-----|------:|',
  '| one  | **1** |',
  '| two  | 2     |',
].join('\n');

test('rows skip the delimiter line and keep the header first', () => {
  assert.deepEqual(t.tableRows(SRC).map((r) => [r.line, r.cells]), [
    [0, ['Name', 'Value']], [2, ['one', '**1**']], [3, ['two', '2']],
  ]);
  assert.equal(t.columnCount(SRC), 2);
});

test('setting a cell rewrites only the line that holds it', () => {
  const out = t.setCell(SRC, 1, 1, '**10**').split('\n');
  const before = SRC.split('\n');
  assert.equal(out[2], '| one | **10** |');
  assert.deepEqual([out[0], out[1], out[3]], [before[0], before[1], before[3]]);
});

test('a pipe typed into a cell is escaped and a newline becomes a space', () => {
  const out = t.setCell(SRC, 2, 0, 'a | b\nc');
  assert.equal(out.split('\n')[3], '| a \\| b c | 2 |');
  assert.deepEqual(t.tableRows(out)[2].cells, ['a | b c', '2']);
});

test('a short row grows to the column it is written in', () => {
  const src = '| a | b | c |\n|---|---|---|\n| 1 |';
  assert.equal(t.setCell(src, 1, 2, 'x').split('\n')[2], '| 1 |  | x |');
});

test('rows without outer pipes keep that style', () => {
  const src = 'a | b\n--|--\n1 | 2';
  assert.equal(t.setCell(src, 1, 0, '9'), 'a | b\n--|--\n9 | 2');
});

test('adding a row appends empty cells for every header column', () => {
  const out = t.addRow(SRC).split('\n');
  assert.equal(out.length, 5);
  assert.equal(out[4], '|  |  |');
  assert.equal(t.tableRows(out.join('\n')).length, 4);
});

test('out-of-range edits leave the source alone', () => {
  assert.equal(t.setCell(SRC, 9, 0, 'x'), SRC);
  assert.equal(t.setCell(SRC, 0, -1, 'x'), SRC);
  assert.equal(t.addRow(''), '');
});
