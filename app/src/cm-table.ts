// GFM 표 원문을 셀 단위로 읽고 고친다. 편집기의 표 위젯이 칸 안에서 받은 글자를
// 원문으로 되돌릴 때 쓴다. 화면도 CodeMirror 도 모르는 순수 함수라서 테스트가 바로 부른다.
//
// 고칠 때는 **그 칸이 든 줄 하나만** 다시 쓴다. 다른 줄(구분 줄의 정렬 · 손으로 맞춘
// 열 폭)은 바이트 그대로 남아서, 칸 하나를 고친 저장이 표 전체를 다시 쓴 diff 가 되지 않는다.

/** 표 안의 행. `line` 은 원문 줄 번호(0부터), 구분 줄(`|---|`)은 행이 아니다. */
export type TableRow = { line: number; cells: string[] };

/** 구분 줄인가: `|`, `:`, `-`, 공백만 있고 `--` 가 든 줄. */
export const isDelimLine = (s: string): boolean => /^[\s|:-]+$/.test(s) && s.includes("--");

/** `|` 로 셀을 가른다. 이스케이프된 `\|` 는 셀 내용이지 경계가 아니다. */
export function splitCells(line: string): string[] {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
  return s.split(/(?<!\\)\|/).map((c) => c.replace(/\\\|/g, "|").trim());
}

/** 칸에 넣을 글자를 원문으로. 줄바꿈은 표 한 줄을 깨므로 공백이 되고, `|` 는 이스케이프한다. */
export const cellSource = (text: string): string =>
  text.replace(/\r?\n/g, " ").replace(/\|/g, "\\|").trim();

/** 원문의 행들. 빈 줄과 구분 줄은 건너뛴다(첫 행이 머리 행이다). */
export function tableRows(src: string): TableRow[] {
  const out: TableRow[] = [];
  src.split("\n").forEach((text, line) => {
    if (!text.trim() || (out.length === 1 && isDelimLine(text))) return;
    out.push({ line, cells: splitCells(text) });
  });
  return out;
}

/** 열 수 = 머리 행의 칸 수. */
export const columnCount = (src: string): number => tableRows(src)[0]?.cells.length ?? 0;

/** 원래 줄의 모양(앞 들여쓰기, 양끝 `|` 유무)을 따라 한 행을 다시 쓴다. */
function joinRow(cells: string[], like: string): string {
  const indent = /^\s*/.exec(like)?.[0] ?? "";
  const t = like.trim();
  const lead = t.startsWith("|"), tail = t.endsWith("|") && !t.endsWith("\\|");
  return `${indent}${lead ? "| " : ""}${cells.map(cellSource).join(" | ")}${tail ? " |" : ""}`;
}

/** `row` 번째 행(0 = 머리)의 `col` 번째 칸을 `text` 로 바꾼 원문. 칸이 모자라면 빈 칸을 채운다. */
export function setCell(src: string, row: number, col: number, text: string): string {
  const rows = tableRows(src);
  const r = rows[row];
  if (!r || col < 0) return src;
  const cells = r.cells.slice();
  while (cells.length <= col) cells.push("");
  cells[col] = text;
  const lines = src.split("\n");
  lines[r.line] = joinRow(cells, lines[r.line]);
  return lines.join("\n");
}

/** 마지막 행 뒤에 빈 행 하나를 더한 원문. 칸 수는 머리 행을 따른다. */
export function addRow(src: string): string {
  const rows = tableRows(src);
  const last = rows[rows.length - 1];
  if (!last) return src;
  const lines = src.split("\n");
  const row = joinRow(Array.from({ length: rows[0].cells.length }, () => ""), lines[last.line]);
  lines.splice(last.line + 1, 0, row);
  return lines.join("\n");
}
