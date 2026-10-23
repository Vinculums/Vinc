// 삽입 메뉴 — 우클릭 · 머리 줄의 + 버튼 · 슬래시(/)가 **같은 목록**을 쓴다. 렌더러가 그리는
// 블록(표·수식·다이어그램)은 전부 여기서 넣을 수 있어야 한다 — 문법을 외우게 하지 않는다.
//
// 넣는 것은 언제나 **마크다운 원문**이다. 편집기의 정본 규율(소스 = 정본, 렌더는 보기
// 전용)을 그대로 따르므로 메뉴는 글자를 넣을 뿐이고, 그리는 일은 라이브 프리뷰가 한다.
// 카피는 이 파일이 들지 않는다 — 호출부(doc-editor)가 항목과 라벨을 준다(슬래시와 같은 규율).
import { EditorView } from "@codemirror/view";
import { esc } from "./components";

export type InsertBlock = {
  key: string;
  label: string;
  /** 왼쪽 타일의 짧은 글리프(`H1`·`|`·`∑`). */
  glyph: string;
  /** 오른쪽의 마크다운 표기 — 메뉴를 쓰다 보면 문법이 손에 붙는다. */
  hint: string;
  /** 넣을 원문. 표는 크기를 받아 만든다. */
  text: string | ((rows: number, cols: number) => string);
  /** 넣은 뒤 커서 자리(원문 안의 오프셋). 없으면 원문 끝. 표는 함수로 준다. */
  caret?: number | ((rows: number, cols: number) => number);
  /** 줄 머리표(제목·목록·인용·체크) — 지금 줄을 **그 종류로 바꾼다**(글은 둔다). */
  prefix?: boolean;
  /** 여러 줄 블록. 앞뒤를 빈 줄로 띄운다 — 문단에 바로 붙으면 표는 문법이 안 서고
   *  `---` 는 윗줄을 제목으로 만든다(setext). */
  spaced?: boolean;
  /** 표 — 메뉴에서 크기 고르기 격자를 연다. */
  table?: boolean;
  /** 슬래시 뒤 글자로 거를 때 같이 보는 낱말(영·한). */
  aliases?: string;
};

export type FormatKey = "bold" | "italic" | "strike" | "code" | "link";
export type InsertLabels = {
  blocks: string; format: string;
  cut: string; copy: string; paste: string;
  formats: Record<FormatKey, string>;
  tableSize: (rows: number, cols: number) => string;
  linkText: string;
};
export type InsertOpts = { blocks: InsertBlock[]; labels: InsertLabels };

// 지금 줄의 블록 머리표(들여쓰기 뒤). 종류를 바꿀 때 이것을 걷어 내고 새 것을 단다.
const LINE_PREFIX = /^(\s*)(#{1,6}\s+|>\s?|[-*+]\s+\[[ xX]\]\s+|[-*+]\s+|\d+[.)]\s+)?/;

/** 블록 하나를 넣는다. `replaceLine` 이면 지금 줄을 빈 줄로 보고 통째로 갈아 끼운다
 *  (슬래시: "/표" 를 친 줄이 블록이 된다). */
export function insertBlock(view: EditorView, b: InsertBlock,
                            size: [number, number] = [3, 3], replaceLine = false): void {
  const st = view.state;
  const line = st.doc.lineAt(st.selection.main.head);
  const empty = replaceLine || !line.text.trim();

  if (b.prefix && !empty) {
    // 종류 바꾸기 — 글은 두고 머리표만 바꾼다. 커서는 줄 끝.
    const m = LINE_PREFIX.exec(line.text)!;
    const from = line.from + m[1].length, to = from + (m[2]?.length ?? 0);
    const ins = String(typeof b.text === "string" ? b.text : "");
    view.dispatch({
      changes: { from, to, insert: ins },
      selection: { anchor: line.to - (to - from) + ins.length },
      scrollIntoView: true, userEvent: "input",
    });
    view.focus();
    return;
  }

  const body = typeof b.text === "function" ? b.text(...size) : b.text;
  const caretIn = typeof b.caret === "function" ? b.caret(...size) : b.caret ?? body.length;
  let from: number, to: number, pre = "", post = "";
  if (empty) { from = line.from; to = line.to; }
  else { from = to = line.to; pre = "\n"; }
  if (b.spaced) {
    // 앞: 바로 윗줄(빈 줄을 갈아 끼우면 그 위, 아니면 지금 줄)이 글이면 한 줄 띄운다.
    const above = empty ? (line.number > 1 ? st.doc.line(line.number - 1).text : "") : line.text;
    if (above.trim()) pre += "\n";
    // 뒤: 다음 줄이 글이면 띄우고, 문서 끝이면 빈 줄을 하나 남겨 블록 밖으로 나갈 자리를 둔다.
    const below = line.number < st.doc.lines ? st.doc.line(line.number + 1).text : null;
    if (below === null || below.trim()) post = "\n";
    if (below !== null && below.trim()) post += "\n";
  }
  view.dispatch({
    changes: { from, to, insert: pre + body + post },
    selection: { anchor: from + pre.length + caretIn },
    scrollIntoView: true, userEvent: "input",
  });
  view.focus();
}

const MARKS: Record<Exclude<FormatKey, "link">, string> = {
  bold: "**", italic: "*", strike: "~~", code: "`",
};

/** 고른 글에 서식을 씌운다. 이미 씌워져 있으면 벗긴다(같은 단축키로 켜고 끈다).
 *  고른 글이 없으면 짝 기호만 넣고 커서를 가운데 둔다. */
export function applyFormat(view: EditorView, key: FormatKey, linkText = "text"): void {
  const st = view.state;
  const { from, to } = st.selection.main;
  const sel = st.sliceDoc(from, to);
  if (key === "link") {
    const text = sel || linkText;
    const url = "https://";
    const insert = `[${text}](${url})`;
    // 고른 글이 있으면 주소 칸을, 없으면 글 칸을 골라 둔다 — 다음에 칠 것이 거기다.
    const a = sel ? from + text.length + 3 : from + 1;
    const b = sel ? a + url.length : a + text.length;
    view.dispatch({ changes: { from, to, insert }, selection: { anchor: a, head: b }, userEvent: "input" });
    view.focus();
    return;
  }
  const m = MARKS[key];
  const before = st.sliceDoc(Math.max(0, from - m.length), from);
  const after = st.sliceDoc(to, to + m.length);
  // 벗기기 — `**굵게**` 안을 고른 채 다시 누르면 기호를 걷는다. 기울임은 굵게의 별표
  // 한 짝과 헷갈리지 않게, 별표가 셋 이상 이어지면 벗기지 않는다.
  const wrapped = before === m && after === m
    && !(key === "italic" && (st.sliceDoc(from - 2, from) === "**" || st.sliceDoc(to, to + 2) === "**"));
  if (wrapped) {
    view.dispatch({
      changes: [{ from: from - m.length, to: from }, { from: to, to: to + m.length }],
      selection: { anchor: from - m.length, head: to - m.length }, userEvent: "input",
    });
  } else {
    view.dispatch({
      changes: { from, to, insert: m + sel + m },
      selection: { anchor: from + m.length, head: to + m.length }, userEvent: "input",
    });
  }
  view.focus();
}

/** 표 원문. 머리줄 · 구분줄 · 빈 칸 줄. 칸에 한 칸씩 띄어 두어 커서가 들어갈 자리가 있다. */
export function tableMarkdown(rows: number, cols: number, head: (i: number) => string): string {
  const r = (cells: string[]) => `| ${cells.join(" | ")} |`;
  const out = [r(Array.from({ length: cols }, (_, i) => head(i + 1))),
               r(Array.from({ length: cols }, () => "---"))];
  for (let i = 0; i < Math.max(1, rows - 1); i++) out.push(r(Array.from({ length: cols }, () => " ")));
  return out.join("\n");
}

/** 첫 본문 칸 안의 오프셋(머리줄 · 구분줄 다음 줄의 `| ` 뒤). */
export function tableCaret(rows: number, cols: number, head: (i: number) => string): number {
  const lines = tableMarkdown(rows, cols, head).split("\n");
  return lines[0].length + 1 + lines[1].length + 1 + 2;
}

const GRID_COLS = 8, GRID_ROWS = 8;

/** 떠 있는 메뉴 하나. 우클릭과 + 버튼이 같은 것을 연다. 키보드: 위·아래 · Enter · Esc. */
export class InsertMenu {
  private dom: HTMLElement;
  private rows: HTMLElement[] = [];
  private sel = 0;
  private grid: [number, number] = [3, 3];
  private onAway = (e: Event) => { if (!this.dom.contains(e.target as Node)) this.hide(); };
  // 페이지 스크롤 이벤트로 닫지 않는다 — 버튼을 화면에 들이는 스크롤만으로도 막 연 메뉴가
  // 닫힌다. 사람이 메뉴 밖에서 휠을 굴릴 때만 닫는다.
  private onWheel = (e: Event) => { if (!this.dom.contains(e.target as Node)) this.hide(); };
  open = false;

  constructor(private view: EditorView, private opts: InsertOpts) {
    this.dom = document.createElement("div");
    this.dom.className = "cm-insert";
    this.dom.setAttribute("role", "menu");
    this.dom.tabIndex = -1;
    this.dom.hidden = true;
    this.dom.addEventListener("keydown", (e) => this.key(e));
    // 메뉴를 누르는 동안 편집기 선택이 사라지지 않게 포커스를 뺏지 않는다.
    this.dom.addEventListener("mousedown", (e) => {
      if (!(e.target as HTMLElement).closest("input")) e.preventDefault();
    });
    document.body.appendChild(this.dom);
  }

  /** `at` 은 화면 좌표. `clipboard` 는 우클릭에서만 — 브라우저 메뉴를 대신하므로
   *  잘라내기 · 복사 · 붙여넣기를 잃으면 안 된다. */
  show(at: { x: number; y: number }, mode: { clipboard: boolean }) {
    const L = this.opts.labels;
    const { from, to } = this.view.state.selection.main;
    const hasSel = to > from;
    const fmt = (["bold", "italic", "strike", "code", "link"] as FormatKey[]);
    const glyph: Record<FormatKey, string> = { bold: "B", italic: "I", strike: "S", code: "</>", link: "↗" };
    const parts: string[] = [];
    if (hasSel) {
      parts.push(`<div class="cm-ins-sec">${esc(L.format)}</div><div class="cm-ins-fmt">${
        fmt.map((k) => `<button type="button" class="cm-ins-fbtn cm-ins-f-${k}" data-fmt="${k}"
          aria-label="${esc(L.formats[k])}" data-tip="${esc(L.formats[k])}">${esc(glyph[k])}</button>`).join("")}</div>`);
    }
    parts.push(`<div class="cm-ins-sec">${esc(L.blocks)}</div>`);
    parts.push(this.opts.blocks.map((b, i) => `<button type="button" class="cm-ins-row" role="menuitem"
        data-i="${i}"${b.table ? ' aria-haspopup="true"' : ""}>
        <span class="cm-ins-glyph" aria-hidden="true">${esc(b.glyph)}</span>
        <span class="cm-ins-label">${esc(b.label)}</span>
        <span class="cm-ins-hint" aria-hidden="true">${b.table ? "›" : esc(b.hint)}</span></button>`).join(""));
    if (mode.clipboard) {
      parts.push(`<div class="cm-ins-sep" role="separator"></div>`);
      const clip = [
        hasSel ? `<button type="button" class="cm-ins-row" role="menuitem" data-clip="cut"><span class="cm-ins-glyph" aria-hidden="true"></span><span class="cm-ins-label">${esc(L.cut)}</span><span class="cm-ins-hint">Ctrl+X</span></button>` : "",
        hasSel ? `<button type="button" class="cm-ins-row" role="menuitem" data-clip="copy"><span class="cm-ins-glyph" aria-hidden="true"></span><span class="cm-ins-label">${esc(L.copy)}</span><span class="cm-ins-hint">Ctrl+C</span></button>` : "",
        `<button type="button" class="cm-ins-row" role="menuitem" data-clip="paste"><span class="cm-ins-glyph" aria-hidden="true"></span><span class="cm-ins-label">${esc(L.paste)}</span><span class="cm-ins-hint">Ctrl+V</span></button>`,
      ];
      parts.push(clip.join(""));
    }
    parts.push(`<div class="cm-ins-grid" hidden>
      <div class="cm-ins-cells" style="--gc:${GRID_COLS}">${
        Array.from({ length: GRID_ROWS * GRID_COLS }, (_, i) =>
          `<span class="cm-ins-cell" data-r="${Math.floor(i / GRID_COLS) + 1}" data-c="${i % GRID_COLS + 1}"></span>`).join("")}</div>
      <div class="cm-ins-gridlabel"></div></div>`);
    this.dom.innerHTML = parts.join("");
    this.rows = [...this.dom.querySelectorAll<HTMLElement>(".cm-ins-row")];
    this.wire();
    this.dom.hidden = false;
    this.open = true;
    this.place(at);
    this.focusRow(0);
    document.addEventListener("mousedown", this.onAway, true);
    window.addEventListener("wheel", this.onWheel, { capture: true, passive: true });
    window.addEventListener("resize", this.hideNow);
  }

  private hideNow = () => this.hide();

  hide(refocus = false) {
    if (!this.open) return;
    this.open = false;
    this.dom.hidden = true;
    document.removeEventListener("mousedown", this.onAway, true);
    window.removeEventListener("wheel", this.onWheel, true);
    window.removeEventListener("resize", this.hideNow);
    if (refocus) this.view.focus();
  }

  destroy() { this.hide(); this.dom.remove(); }

  private place(at: { x: number; y: number }) {
    const pad = 8;
    const r = this.dom.getBoundingClientRect();
    const x = Math.min(at.x, window.innerWidth - r.width - pad);
    // 아래에 자리가 없으면 위로 연다.
    const y = at.y + r.height + pad > window.innerHeight ? Math.max(pad, at.y - r.height) : at.y;
    this.dom.style.left = `${Math.max(pad, Math.round(x))}px`;
    this.dom.style.top = `${Math.round(y)}px`;
  }

  private wire() {
    this.dom.querySelectorAll<HTMLElement>("[data-fmt]").forEach((b) => b.addEventListener("click", () => {
      this.hide();
      applyFormat(this.view, b.dataset.fmt as FormatKey, this.opts.labels.linkText);
    }));
    this.rows.forEach((row, n) => {
      row.addEventListener("mouseenter", () => this.focusRow(n));
      row.addEventListener("click", () => this.choose(n));
    });
    const grid = this.dom.querySelector<HTMLElement>(".cm-ins-grid")!;
    grid.querySelectorAll<HTMLElement>(".cm-ins-cell").forEach((c) => {
      c.addEventListener("mouseenter", () => this.setGrid(Number(c.dataset.r), Number(c.dataset.c)));
      c.addEventListener("click", () => this.chooseTable());
    });
  }

  private tableIndex() { return this.opts.blocks.findIndex((b) => b.table); }

  private focusRow(n: number) {
    this.sel = n;
    this.rows.forEach((r, i) => r.classList.toggle("on", i === n));
    this.rows[n]?.focus({ preventScroll: true });
    // 표 줄에 서 있으면 크기 격자를 그 줄 옆에 편다.
    const grid = this.dom.querySelector<HTMLElement>(".cm-ins-grid")!;
    const row = this.rows[n];
    const isTable = !!row && Number(row.dataset.i) === this.tableIndex();
    grid.hidden = !isTable;
    if (isTable) {
      this.setGrid(...this.grid);
      // 격자는 화면 좌표로 선다(메뉴가 자기 안에서 굴러도 잘리지 않게). 오른쪽에 자리가
      // 없으면 왼쪽에 편다.
      const m = this.dom.getBoundingClientRect();
      const rr = row.getBoundingClientRect();
      const gw = grid.offsetWidth, gh = grid.offsetHeight;
      const right = m.right + 6 + gw <= window.innerWidth - 8;
      grid.style.left = `${Math.round(right ? m.right + 6 : m.left - 6 - gw)}px`;
      grid.style.top = `${Math.round(Math.min(rr.top - 8, window.innerHeight - gh - 8))}px`;
    }
  }

  private setGrid(r: number, c: number) {
    this.grid = [r, c];
    this.dom.querySelectorAll<HTMLElement>(".cm-ins-cell").forEach((cell) => {
      cell.classList.toggle("on", Number(cell.dataset.r) <= r && Number(cell.dataset.c) <= c);
    });
    const lab = this.dom.querySelector<HTMLElement>(".cm-ins-gridlabel");
    if (lab) lab.textContent = this.opts.labels.tableSize(r, c);
  }

  private chooseTable() {
    const i = this.tableIndex();
    if (i < 0) return;
    this.hide();
    insertBlock(this.view, this.opts.blocks[i], this.grid);
  }

  private choose(n = this.sel) {
    const row = this.rows[n];
    if (!row) return;
    const clip = row.dataset.clip;
    if (clip) { this.hide(); void this.clipboard(clip); return; }
    const b = this.opts.blocks[Number(row.dataset.i)];
    if (!b) return;
    this.hide();
    insertBlock(this.view, b, b.table ? this.grid : undefined);
  }

  private async clipboard(kind: string) {
    const v = this.view;
    const { from, to } = v.state.selection.main;
    try {
      if (kind === "paste") {
        const t = await navigator.clipboard.readText();
        v.dispatch({ changes: { from, to, insert: t }, selection: { anchor: from + t.length }, userEvent: "input.paste" });
      } else {
        await navigator.clipboard.writeText(v.state.sliceDoc(from, to));
        if (kind === "cut") v.dispatch({ changes: { from, to }, userEvent: "delete.cut" });
      }
    } catch { /* 클립보드를 못 쓰는 자리다 — 메뉴의 단축키 표기가 다른 길을 말한다 */ }
    v.focus();
  }

  private key(e: KeyboardEvent) {
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); this.hide(true); return; }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const d = e.key === "ArrowDown" ? 1 : -1;
      this.focusRow((this.sel + d + this.rows.length) % this.rows.length);
      return;
    }
    // 표 줄에서 좌우 화살표는 격자 크기를 바꾼다(위·아래는 줄 이동이라 Shift 와 함께).
    const onTable = Number(this.rows[this.sel]?.dataset.i) === this.tableIndex();
    if (onTable && (e.key === "ArrowRight" || e.key === "ArrowLeft")) {
      e.preventDefault();
      const [r, c] = this.grid;
      this.setGrid(r, Math.min(GRID_COLS, Math.max(1, c + (e.key === "ArrowRight" ? 1 : -1))));
      return;
    }
    if (e.key === "Enter") { e.preventDefault(); this.choose(); return; }
    if (e.key === "Tab") { e.preventDefault(); this.hide(true); }
  }
}

/** 슬래시 뒤 글자로 블록을 거른다. 앞부분이 맞는 것이 속이 맞는 것보다 먼저 선다
 *  ("/ma" 는 Math 가 먼저다. "Small heading" 은 속에서 맞을 뿐이다). */
export function filterBlocks(blocks: InsertBlock[], q: string): InsertBlock[] {
  const t = q.trim().toLowerCase();
  if (!t) return blocks;
  const rank = (b: InsertBlock) => {
    const words = `${b.label} ${b.key} ${b.aliases ?? ""}`.toLowerCase();
    if (b.label.toLowerCase().startsWith(t) || b.key.startsWith(t)) return 0;
    if (words.split(/\s+/).some((w) => w.startsWith(t))) return 1;
    return words.includes(t) ? 2 : -1;
  };
  return blocks.map((b, i) => ({ b, r: rank(b), i })).filter((x) => x.r >= 0)
    .sort((x, y) => x.r - y.r || x.i - y.i).map((x) => x.b);
}
