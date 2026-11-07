// CodeMirror 6 인라인 Live Preview 편집기(D45-E 2단계). Obsidian 모델:
// **EditorState.doc 가 곧 정본**(순수 마크다운) — 렌더는 view-only 데코레이션일
// 뿐이라 저장/복원이 바이트 동일(verified 무손실 구조 보장). 커서가 놓인 줄은
// 원본 소스를 그대로 보이고(활성 줄은 절대 변형 안 함 → 타이핑 중 "튐" 회피),
// 커서가 없는 줄만 마크다운 기호를 숨기고 서식으로 렌더한다.
//
// 의존성은 npm 번들 인라인(CSP 정합, CDN 없음). 스타일은 CSS/데코레이션 클래스로
// 완전 제어(모노크롬 Ink & Paper — 색조 없이 굵기·크기·명도만).
import {
  EditorState, Facet, RangeSetBuilder, StateEffect, StateField,
} from "@codemirror/state";
import {
  EditorView, keymap, Decoration, ViewPlugin, WidgetType,
  type DecorationSet, type ViewUpdate,
} from "@codemirror/view";
import { defaultKeymap, history, historyKeymap, isolateHistory, redo, undo } from "@codemirror/commands";
import { fillMath, fillMermaid } from "./rich-blocks";
import { markdown, markdownLanguage } from "@codemirror/lang-markdown";
import { syntaxTree } from "@codemirror/language";
import { MentionMenu, SelectionBar, MENTION_RE,
         type MentionOpts, type SelectionOpts } from "./cm-assist";
import { esc } from "./components";
import { InsertMenu, applyFormat, filterBlocks, insertBlock,
         type InsertBlock, type InsertOpts } from "./cm-insert";
import { addRow, isDelimLine, setCell, splitCells, tableRows } from "./cm-table";

export type CmHandle = {
  getValue: () => string;
  setValue: (text: string) => void;
  focus: () => void;
  destroy: () => void;
  /** 개념 언급 위치(밑줄) 갱신 + 첫 위치로 스크롤. char 오프셋은 백엔드 계산값. */
  setHighlights: (ranges: { char_start: number; char_end: number }[]) => void;
  scrollToHighlight: () => void;
  /** 임의 오프셋으로 스크롤 — 목차 클릭과 검색의 청크 히트 착지가 쓴다.
   *  범위를 넘는 값은 문서 끝으로 잘린다(재청킹으로 오프셋이 어긋나도 튀지 않는다). */
  scrollToOffset: (pos: number, align?: "center" | "start") => void;
  /** 오프셋이 놓인 줄의 화면 y(뷰포트 기준). 옆 칸 목차가 지금 절을 고를 때 쓴다. */
  lineTop: (pos: number) => number;
  /** 삽입 메뉴를 화면 좌표에 연다(머리 줄의 + 버튼). 편집 불가면 아무것도 안 한다. */
  openInsert: (at: { x: number; y: number }) => void;
};

// 인라인 문법 토큰 — 짝 기호를 숨기고(replace) 안쪽을 서식(mark). 순서 주의:
// 코드(백틱)를 먼저 잡아 그 안의 별표를 강조로 오인하지 않게 한다.
type Tok = { re: RegExp; cls: string };
const INLINE: Tok[] = [
  { re: /`([^`]+)`/g, cls: "cm-r-code" },
  { re: /~~([^~\n]+)~~/g, cls: "cm-r-strike" },
  { re: /\*\*([^*\n]+)\*\*/g, cls: "cm-r-strong" },
  { re: /(?<![*])\*([^*\n]+)\*(?![*])/g, cls: "cm-r-em" },
  // 문서 상호참조. 코퍼스가 이미 이 글자로 서로를 가리키고 있었는데 밑줄만 그어진
  // 죽은 글자였다 — 이 앱이 가장 많이 쓰는 링크가 유일하게 안 열리는 링크였다.
  // 마크다운 링크보다 **먼저** 잡는다(`[[a]]` 는 `[a](…)` 가 아니라 그 자체다).
  { re: /\[\[([^\]\n]+)\]\]/g, cls: "cm-r-doclink" },
  { re: /\[([^\]\n]+)\]\(([^)\n]+)\)/g, cls: "cm-r-link" },
];

const WIKI_RE = /\[\[([^\]\n]+)\]\]/g;
const MDLINK_RE = /\[([^\]\n]+)\]\(([^)\n]+)\)/g;

const HEADING = /^(#{1,6})\s+/;
const QUOTE = /^>\s?/;
const BULLET = /^(\s*)([-*])\s+/;
// 체크리스트 `- [ ] ` · `- [x] `. 불릿보다 먼저 본다(같은 줄이 둘 다에 걸린다).
const TASK = /^(\s*)([-*+])\s+\[([ xX])\]\s/;
const HR = /^(-{3,}|\*{3,})\s*$/;

// 표(GFM) — 줄 단위로 판별한다. 표는 다중행 블록이라 lineDecos 의 한 줄 계약으로는
// "격자"를 그릴 수 없다. 대신 **정렬을 살리는 데 필요한 것만** 한다: 등폭 + 줄바꿈
// 금지(가로 스크롤). 소스의 공백 정렬이 그대로 열로 보인다.
//   TABLE_ROW  : `|` 로 시작하는 줄
//   TABLE_DELIM: `|---|---:|` 구분행. 기호뿐이라 통째로 숨긴다.
//     `--` 연속 2개 이상을 요구해 `| - | - |`(대시가 셀 내용인 정상 행)와 가른다.
const TABLE_ROW = /^\s*\|/;
const TABLE_DELIM = /^[\s|:-]+$/;

// ── 프론트매터(YAML) — 문서가 **가진 값**이지 문서의 산문이 아니다 ────────────
// 정본에는 그대로 두고 화면에서만 접는다(소스=정본 계약: 저장은 여전히 바이트
// 동일). 접히면 요약 한 줄, 펼치면 원문 YAML 이 그 자리에 나와 바로 고쳐진다.
//
// ⚠ 접든 펼치든 이 구간에는 lineDecos 를 태우지 않는다. 태우면 여는 `---` 가 HR
// 규칙에 걸려 가로줄이 되고, 값 속의 `*` 가 이탤릭 짝으로 잡혀 글자가 사라진다.
const FM_MAX_LINES = 100;   // 1행이 `---` 인데 닫는 줄이 없는 문서에서 전수 스캔 방지

/** 접기 라벨. 편집기는 카피를 직접 들지 않는다(슬래시 항목과 같은 규율) — 호출부가 준다. */
const fmLabel = Facet.define<string, string>({ combine: (v) => v[0] ?? "Properties" });

const fmToggle = StateEffect.define<boolean>();
const fmOpen = StateField.define<boolean>({
  create: () => false,
  update(v, tr) {
    for (const e of tr.effects) if (e.is(fmToggle)) return e.value;
    return v;
  },
});

/** 첫 줄이 `---` 이고 뒤에 닫는 줄이 있으면 그 범위. 아니면 null. */
function frontmatterRange(state: EditorState): { from: number; to: number; body: string } | null {
  if (state.doc.lines < 2 || state.doc.line(1).text.trim() !== "---") return null;
  const first = state.doc.line(1);
  const last = Math.min(state.doc.lines, FM_MAX_LINES);
  for (let n = 2; n <= last; n++) {
    const line = state.doc.line(n);
    const t = line.text.trim();
    if (t === "---" || t === "...")
      return { from: first.from, to: line.to,
               body: state.doc.sliceString(first.to + 1, line.from) };
  }
  return null;
}

/** 접힌 줄 요약 — `키: 값` 을 가운뎃점으로 잇는다. 넘치는 폭은 CSS 가 자른다. */
function fmSummary(body: string): string {
  const out: string[] = [];
  for (const line of body.split("\n")) {
    const m = /^([A-Za-z0-9_-]+)\s*:\s*(.*)$/.exec(line);
    if (m && m[2].trim()) out.push(`${m[1]}: ${m[2].trim()}`);
  }
  return out.join(" · ");
}

class FmWidget extends WidgetType {
  constructor(readonly label: string, readonly summary: string, readonly open: boolean) { super(); }
  eq(o: FmWidget) {
    return o.label === this.label && o.summary === this.summary && o.open === this.open;
  }
  toDOM(view: EditorView) {
    const el = document.createElement("div");
    el.className = `cm-r-fm${this.open ? " cm-r-fm-open" : ""}`;
    el.setAttribute("role", "button");
    el.setAttribute("aria-expanded", this.open ? "true" : "false");
    el.tabIndex = 0;
    el.innerHTML =
      `<span class="cm-r-fm-cap">${this.open ? "▾" : "▸"} ${esc(this.label)}</span>`
      + (this.open ? "" : `<span class="cm-r-fm-sum">${esc(this.summary)}</span>`);
    const toggle = (e: Event) => {
      e.preventDefault();
      view.dispatch({ effects: fmToggle.of(!this.open) });
    };
    el.addEventListener("mousedown", toggle);
    el.addEventListener("keydown", (e) => {
      const k = (e as KeyboardEvent).key;
      if (k === "Enter" || k === " ") toggle(e);
    });
    return el;
  }
  ignoreEvent() { return false; }
}

// ── 표(GFM) — 진짜 격자로 그린다 ──────────────────────────────────────────────
// 전에는 lineDecos 의 한 줄 계약 때문에 격자를 못 그리고 등폭 정렬로 흉내 냈다
// (파이프가 그대로 보여 "표가 아니라 그림"이었다). GFM 파서가 이미 `Table` 노드를
// 만들어 두므로 그 범위를 통째로 블록 위젯으로 바꾼다. 셀 단위로 렌더하니 셀
// 경계를 넘는 인라인 오매칭(lineDecos 의 표 early return 이 존재하는 이유)이
// 구조적으로 불가능해지고, 그래서 셀 안 **강조**·`코드` 가 비로소 산다.
// 편집 중에는 **칸 안에서 고친다**: 칸을 누르면 그 칸만 원문(`**굵게**` 같은 기호째)으로
// 바뀌고, 입력은 그 칸이 든 줄 하나로 되돌아간다(cm-table.ts). 표 전체를 원문으로 보려면
// 칸에서 Esc 를 누르거나 키보드로 표 안에 들어온다(이 경우 위젯이 걷히고 원문이 보인다).

/** 셀 안 인라인. **이스케이프가 먼저**다 — 아래 치환이 innerHTML 로 들어간다. */
function cellHtml(raw: string): string {
  return esc(raw)
    .replace(/`([^`]+)`/g, '<code class="cm-r-code">$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, "<em>$1</em>")
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, "<u>$1</u>");
}

const cellAlign = (spec: string): string =>
  /^:-+:$/.test(spec) ? "center" : /^-+:$/.test(spec) ? "right"
    : /^:-+$/.test(spec) ? "left" : "";

function tableHtml(src: string, edit: boolean): string {
  const rows = tableRows(src);
  if (!rows.length) return "";
  const lines = src.split("\n");
  const delim = lines.find((l, i) => i > rows[0].line && l.trim() && isDelimLine(l));
  const align = delim ? splitCells(delim).map((c) => cellAlign(c.replace(/\s/g, ""))) : [];
  const cols = rows[0].cells.length;
  const cell = (t: string, r: number, i: number, tag: "th" | "td") =>
    `<${tag} data-r="${r}" data-c="${i}"${edit ? ' contenteditable="plaintext-only" spellcheck="false"' : ""}`
    + `${align[i] ? ` style="text-align:${align[i]}"` : ""}>${cellHtml(t)}</${tag}>`;
  const row = (cells: string[], r: number, tag: "th" | "td") =>
    Array.from({ length: Math.max(cols, cells.length) }, (_, i) => cell(cells[i] ?? "", r, i, tag)).join("");
  const head = row(rows[0].cells, 0, "th");
  const body = rows.slice(1).map((x, k) => `<tr>${row(x.cells, k + 1, "td")}</tr>`).join("");
  return `<table class="cm-r-tbl"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

/** 위젯 DOM 이 지금 그리고 있는 원문. 위젯 객체는 데코레이션이 바뀔 때마다 새로 생기지만
 *  DOM 은 `updateDOM` 으로 살아남으므로, 칸의 입력이 기준으로 삼을 원문은 DOM 에 붙인다. */
const tableSrc = new WeakMap<HTMLElement, string>();
/** 행을 더한 뒤 다시 그려진 표에서 초점을 받을 칸. 모양이 바뀌면 DOM 을 새로 짓기 때문이다. */
const pendingCell = new WeakMap<EditorView, { r: number; c: number }>();

const cellOf = (el: EventTarget | null): HTMLElement | null =>
  el instanceof Element ? el.closest<HTMLElement>("[data-r][data-c]") : null;

/** 칸의 원문(이스케이프를 푼 글자). */
const rawCell = (src: string, r: number, c: number): string => tableRows(src)[r]?.cells[c] ?? "";

function focusCell(wrap: HTMLElement, r: number, c: number): void {
  // 되돌리기로 그 행이 사라졌으면 남은 마지막 행의 같은 열로 간다.
  const rows = wrap.querySelectorAll("tr").length;
  const el = wrap.querySelector<HTMLElement>(`[data-r="${Math.min(r, rows - 1)}"][data-c="${c}"]`);
  if (!el) return;
  el.focus();
  const sel = el.ownerDocument.getSelection();
  if (sel) { sel.selectAllChildren(el); sel.collapseToEnd(); }
}

class TableWidget extends WidgetType {
  constructor(readonly src: string, readonly edit: boolean) { super(); }
  eq(o: TableWidget) { return o.src === this.src && o.edit === this.edit; }
  toDOM(view: EditorView) {
    const wrap = document.createElement("div");
    wrap.className = "cm-r-tablewrap";
    wrap.innerHTML = tableHtml(this.src, this.edit);
    tableSrc.set(wrap, this.src);
    if (this.edit) {
      wireTable(wrap, view);
      const want = pendingCell.get(view);
      if (want) {
        pendingCell.delete(view);
        requestAnimationFrame(() => focusCell(wrap, want.r, want.c));
      }
    }
    return wrap;
  }
  /** 칸에 글자를 칠 때마다 원문이 바뀌어 새 위젯이 온다. 모양(행·열 수)이 같으면 DOM 을
   *  그대로 두고 칸 글자만 갈아 끼운다 — 다시 지으면 입력 중인 칸이 초점을 잃는다.
   *  입력 중인 칸은 건드리지 않는다(되돌리기로 원문이 달라졌을 때만 고친다). */
  updateDOM(dom: HTMLElement, view: EditorView) {
    if (!this.edit || !dom.querySelector("[contenteditable]")) return false;
    const rows = tableRows(this.src);
    const trs = dom.querySelectorAll("tr");
    if (trs.length !== rows.length) return false;
    for (let r = 0; r < rows.length; r++) {
      if (trs[r].children.length !== Math.max(rows[0].cells.length, rows[r].cells.length)) return false;
    }
    tableSrc.set(dom, this.src);
    pendingCell.delete(view);
    const active = dom.ownerDocument.activeElement;
    dom.querySelectorAll<HTMLElement>("[data-r][data-c]").forEach((el) => {
      const raw = rawCell(this.src, +el.dataset.r!, +el.dataset.c!);
      if (el === active) { if (el.textContent !== raw) el.textContent = raw; }
      else el.innerHTML = cellHtml(raw);
    });
    return true;
  }
  // 칸 안의 이벤트는 위젯 것이다 — 편집기가 받으면 클릭이 커서를 표 안으로 옮겨 위젯이
  // 걷히고, 키는 편집기 단축키로 간다. 칸 밖(테두리 · 여백)을 누르면 예전처럼 원문이 나온다.
  // 읽기 모드에서는 칸이 편집 가능이 아니라서 클릭해도 표인 채로 남는다.
  ignoreEvent(e: Event) { return this.edit && !!cellOf(e.target); }
}

/** 칸 하나의 입력을 원문으로 되돌리는 배선. 위젯의 위치는 매번 DOM 에서 다시 잰다
 *  (앞쪽 글이 바뀌면 오프셋이 밀린다). */
function wireTable(wrap: HTMLElement, view: EditorView): void {
  const write = (next: string, userEvent: string) => {
    const cur = tableSrc.get(wrap) ?? "";
    if (next === cur) return;
    const from = view.posAtDOM(wrap);
    // 행 더하기는 따로 되돌린다 — 앞뒤 타이핑과 한 묶음이면 되돌릴 때 행과 글자가 함께 사라진다.
    view.dispatch({ changes: { from, to: from + cur.length, insert: next }, userEvent,
                    annotations: userEvent === "input" ? isolateHistory.of("full") : undefined });
  };
  wrap.addEventListener("focusin", (e) => {
    const el = cellOf(e.target);
    if (el) el.textContent = rawCell(tableSrc.get(wrap) ?? "", +el.dataset.r!, +el.dataset.c!);
  });
  wrap.addEventListener("focusout", (e) => {
    const el = cellOf(e.target);
    if (el) el.innerHTML = cellHtml(rawCell(tableSrc.get(wrap) ?? "", +el.dataset.r!, +el.dataset.c!));
  });
  wrap.addEventListener("input", (e) => {
    const el = cellOf(e.target);
    if (!el) return;
    write(setCell(tableSrc.get(wrap) ?? "", +el.dataset.r!, +el.dataset.c!, el.textContent ?? ""), "input.type");
  });
  wrap.addEventListener("keydown", (e) => {
    const el = cellOf(e.target);
    if (!el || e.isComposing) return;
    const r = +el.dataset.r!, c = +el.dataset.c!;
    const src = tableSrc.get(wrap) ?? "";
    const rows = tableRows(src).length, cols = tableRows(src)[0]?.cells.length ?? 0;
    const mod = e.metaKey || e.ctrlKey;
    // 되돌리기는 편집기의 기록으로 — 칸의 자체 되돌리기는 원문과 어긋난다. 되돌린 결과가
    // 행 수를 바꿔 표가 다시 지어지면 같은 칸으로 초점을 돌려준다.
    const hist = (cmd: typeof undo) => { e.preventDefault(); pendingCell.set(view, { r, c }); cmd(view); };
    if (mod && e.key.toLowerCase() === "z") { hist(e.shiftKey ? redo : undo); return; }
    if (mod && e.key.toLowerCase() === "y") { hist(redo); return; }
    // Tab · Shift+Tab 은 옆 칸, Enter 는 아래 칸. 마지막 칸에서 넘어가면 행을 하나 더한다.
    const go = (nr: number, nc: number) => {
      e.preventDefault();
      if (nr >= rows) {
        pendingCell.set(view, { r: nr, c: nc });
        write(addRow(src), "input");
        return;
      }
      if (nr >= 0) focusCell(wrap, nr, nc);
    };
    if (e.key === "Tab") {
      const i = r * cols + c + (e.shiftKey ? -1 : 1);
      go(Math.floor(i / cols), ((i % cols) + cols) % cols);
    } else if (e.key === "Enter") go(r + 1, c);
    else if (e.key === "Escape") {
      // 표 전체를 원문으로 — 커서를 표 앞에 두면 위젯이 걷힌다.
      e.preventDefault();
      view.focus();
      view.dispatch({ selection: { anchor: view.posAtDOM(wrap) } });
    }
  });
  // 서식째 붙여 넣지 않는다 — 칸은 한 줄 글자다(plaintext-only 를 모르는 엔진 대비).
  wrap.addEventListener("paste", (e) => {
    if (!cellOf(e.target)) return;
    e.preventDefault();
    const text = (e.clipboardData?.getData("text/plain") ?? "").replace(/\r?\n/g, " ");
    if (e.target instanceof Node) e.target.ownerDocument?.execCommand("insertText", false, text);
  });
}

/** 수식·다이어그램 블록. 커서가 그 범위에 들어오면 원문을 보이되(편집 가능일 때만,
 *  고칠 것은 그림이 아니라 소스다) 그림은 걷지 않고 원문 **바로 아래**에 남긴다 —
 *  치는 대로 다시 그려지므로 원문과 결과를 한 자리에서 본다(`preview`).
 *  그리는 일은 `rich-blocks` 가 늦게 실어 와서 한다. 위젯이 만들어질 때 DOM 만
 *  돌려주고 내용은 프라미스가 도착하면 채워진다 — CM 은 그 사이에도 자리를 잡는다. */
class RichWidget extends WidgetType {
  constructor(readonly kind: "math" | "mermaid", readonly src: string,
              readonly preview = false) { super(); }
  eq(o: RichWidget) { return o.kind === this.kind && o.src === this.src && o.preview === this.preview; }
  toDOM(view: EditorView) {
    const el = document.createElement("div");
    el.className = (this.kind === "math" ? "md-math" : "md-mermaid") + (this.preview ? " cm-r-preview" : "");
    // 그림은 늦게 채워져 위젯 높이가 그 뒤에 바뀐다. 편집기는 위젯 안의 변화를 보지 않으므로
    // 다 채워지면 다시 재게 한다 — 안 그러면 줄 높이 지도가 어긋나 Home · End · 화살표가
    // 엉뚱한 줄로 간다(원문 아래 미리보기에서 한두 줄씩 밀렸다).
    const filled = this.kind === "math" ? fillMath(el, this.src, true) : fillMermaid(el, this.src);
    void filled.then(() => view.requestMeasure());
    // 누르면 원문으로 — 커서를 블록 **안쪽** 첫 줄에 둔다. 편집기에 맡기면 블록 위젯의
    // 클릭이 블록 앞 줄 끝에 떨어져 원문이 열리지 않았다(화살표도 블록을 건너뛴다).
    if (!this.preview && view.state.facet(EditorView.editable)) {
      el.addEventListener("mousedown", (e) => {
        if (e.button !== 0) return;
        e.preventDefault();
        // 위젯의 DOM 위치는 블록 안 어딘가를 가리킬 수 있어서, 블록 층이 그린 구간에서 찾는다.
        const at = view.posAtDOM(el), st = view.state;
        const blk = st.field(blockLayer, false)?.ranges.find((r) => r.from <= at && at <= r.to);
        if (!blk) return;
        const line = st.doc.lineAt(blk.from);
        const one = line.to >= blk.to;               // `$$x$$` 한 줄짜리
        const anchor = one ? blk.from + line.text.indexOf("$$") + 2 : Math.min(line.to + 1, blk.to);
        view.focus();
        view.dispatch({ selection: { anchor }, scrollIntoView: true });
      });
    }
    return el;
  }
  ignoreEvent(e: Event) { return e.type === "mousedown" && !this.preview; }
}

/** `$$…$$` 블록의 구간. 마크다운 문법이 아니라 관례라 파서가 모르고, 그래서 줄을
 *  훑는다. 코드펜스 **안**은 건너뛴다 — 거기 든 달러는 수식이 아니라 글자다. */
function mathRanges(state: EditorState): { from: number; to: number }[] {
  const out: { from: number; to: number }[] = [];
  const doc = state.doc;
  let inFence = false;
  for (let n = 1, open = -1; n <= doc.lines; n++) {
    const line = doc.line(n);
    const t = line.text.trim();
    if (t.startsWith("```")) { inFence = !inFence; continue; }
    if (inFence) continue;
    if (open < 0) {
      if (!t.startsWith("$$")) continue;
      if (t.length > 4 && t.endsWith("$$")) { out.push({ from: line.from, to: line.to }); continue; }
      open = line.from;
    } else if (t.endsWith("$$")) { out.push({ from: open, to: line.to }); open = -1; }
  }
  return out;
}

// ── 블록 층 — **StateField 여야 한다** ────────────────────────────────────────
// CM6 은 블록 데코레이션을 ViewPlugin 에서 받지 않는다("Block decorations may not
// be specified via plugins"): 블록은 줄 높이를 바꾸므로 뷰포트를 계산하기 **전에**
// 알아야 하고, 플러그인은 그 뒤에 돈다. 그래서 프론트매터·표(블록)는 여기 필드에
// 살고, 인라인·줄 데코는 그대로 livePreview 플러그인에 남는다. 두 세트는 CM6 가
// 병합한다. 이 구분을 지우면 편집기가 마운트에서 즉사한다(실측).
//
// ponytail: 문서가 바뀔 때마다 트리를 **전수** 훑는다(필드에는 뷰포트가 없다).
// 표 탐색은 이미 만들어진 구문 트리를 읽을 뿐이라 실측 문제 없으면 그대로 둔다 —
// 아주 긴 문서에서 타이핑이 무거워지면 변경 구간 주변만 다시 세는 쪽으로 좁힌다.
type Blocks = { deco: DecorationSet; ranges: { from: number; to: number }[] };

function buildBlocks(state: EditorState): Blocks {
  const parts: { from: number; to: number; deco: Decoration }[] = [];
  const ranges: { from: number; to: number }[] = [];
  const editable = state.facet(EditorView.editable);
  const sel = state.selection;

  const fm = frontmatterRange(state);
  if (fm) {
    ranges.push({ from: fm.from, to: fm.to });
    const open = state.field(fmOpen, false) ?? false;
    const w = new FmWidget(state.facet(fmLabel), fmSummary(fm.body), open);
    // 펼침은 머리만 얹고 원문 YAML 을 그대로 보인다(그 자리에서 고친다).
    // 접힘은 구간을 통째로 요약 한 줄로 대체한다.
    parts.push(open
      ? { from: fm.from, to: fm.from,
          deco: Decoration.widget({ widget: w, side: -1, block: true }) }
      : { from: fm.from, to: fm.to,
          deco: Decoration.replace({ widget: w, block: true }) });
  }

  // GFM `Table` 노드. 파서가 판정하므로 코드펜스 안의 파이프 줄을 표로 오인하지
  // 않는다(줄 정규식 판별에 남아 있던 구멍).
  syntaxTree(state).iterate({ enter: (n) => {
    if (n.name !== "Table") return undefined;
    const t = { from: n.from, to: n.to };
    if (fm && t.from < fm.to) return false;                 // 프론트매터 안이면 표가 아니다
    // 커서가 표 안에 있으면 위젯을 걷고 원문을 보인다(활성 줄 = 소스 규율 그대로).
    if (editable && sel.ranges.some((r) => r.from <= t.to && t.from <= r.to)) return false;
    ranges.push(t);
    parts.push({ from: t.from, to: t.to, deco: Decoration.replace({
      widget: new TableWidget(state.doc.sliceString(t.from, t.to), editable), block: true }) });
    return false;
  } });

  // 다이어그램 — 파서가 준 펜스 노드를 쓴다(줄 정규식으로 세면 중첩 펜스에서 어긋난다).
  syntaxTree(state).iterate({ enter: (n) => {
    if (n.name !== "FencedCode") return undefined;
    const head = state.doc.lineAt(n.from).text.trim();
    if (!/^```+\s*mermaid\s*$/i.test(head)) return false;
    const body = state.doc.sliceString(state.doc.lineAt(n.from).to + 1,
                                       state.doc.lineAt(n.to).from).replace(/\n$/, "");
    if (editable && sel.ranges.some((r) => r.from <= n.to && n.from <= r.to)) {
      parts.push({ from: n.to, to: n.to, deco: Decoration.widget({
        widget: new RichWidget("mermaid", body, true), block: true, side: 1 }) });
      return false;
    }
    ranges.push({ from: n.from, to: n.to });
    parts.push({ from: n.from, to: n.to, deco: Decoration.replace({
      widget: new RichWidget("mermaid", body), block: true }) });
    return false;
  } });
  // 수식
  for (const m of mathRanges(state)) {
    if (ranges.some((r) => r.from <= m.from && m.to <= r.to)) continue;   // 표·프론트매터 안이면 건너뛴다
    const tex = state.doc.sliceString(m.from, m.to)
      .replace(/^\s*[$][$]/, "").replace(/[$][$]\s*$/, "").trim();
    if (editable && sel.ranges.some((r) => r.from <= m.to && m.from <= r.to)) {
      parts.push({ from: m.to, to: m.to, deco: Decoration.widget({
        widget: new RichWidget("math", tex, true), block: true, side: 1 }) });
      continue;
    }
    ranges.push(m);
    parts.push({ from: m.from, to: m.to, deco: Decoration.replace({
      widget: new RichWidget("math", tex), block: true }) });
  }

  parts.sort((a, b) => a.from - b.from || a.to - b.to);
  const builder = new RangeSetBuilder<Decoration>();
  for (const p of parts) builder.add(p.from, p.to, p.deco);
  return { deco: builder.finish(), ranges };
}

const blockLayer = StateField.define<Blocks>({
  create: (state) => buildBlocks(state),
  update(v, tr) {
    const selChanged = !tr.startState.selection.eq(tr.state.selection);
    const toggled = tr.effects.some((e) => e.is(fmToggle));
    // 편집 가능 여부는 마운트 이후 안 바뀌므로(컴파트먼트 없음) 무효화 조건이 아니다.
    if (!tr.docChanged && !selChanged && !toggled) return v;
    return buildBlocks(tr.state);
  },
  provide: (f) => EditorView.decorations.from(f, (v) => v.deco),
});

// 한 줄에서 (from,to) 절대오프셋 기준 데코레이션 조각을 모은다. 활성(커서) 줄은
// 호출 자체를 건너뛰므로 여기선 항상 "렌더" 대상이다.
function lineDecos(text: string, base: number,
                   out: { from: number; to: number; deco: Decoration }[]): void {
  // 표 줄은 **여기서 끝낸다** — 아래 인라인 루프를 타면 안 된다. 인라인 규칙은 줄
  // 단위라 셀 경계를 넘어 매칭된다: `| 3*4 | 5*6 |` 에서 두 별표가 이탤릭 짝으로
  // 잡혀 Decoration.replace 로 **화면에서 글자가 사라지고** 가운데 파이프가 기울었다.
  if (TABLE_ROW.test(text)) {
    if (TABLE_DELIM.test(text) && text.includes("--")) {
      // 구분행은 기호뿐 — 통째로 숨긴다(줄 자체는 남아 행 간격을 유지).
      out.push({ from: base, to: base + text.length, deco: Decoration.replace({}) });
      out.push({ from: base, to: base, deco: Decoration.line({ class: "cm-r-table cm-r-table-delim" }) });
    } else {
      out.push({ from: base, to: base, deco: Decoration.line({ class: "cm-r-table" }) });
    }
    return;
  }
  // 블록 프리픽스 — 기호를 replace 로 숨기고 줄에 스타일 클래스.
  const h = HEADING.exec(text);
  if (h) {
    out.push({ from: base, to: base + h[0].length, deco: Decoration.replace({}) });
    out.push({ from: base, to: base,
      deco: Decoration.line({ class: `cm-r-h cm-r-h${h[1].length}` }) });
  } else if (QUOTE.test(text)) {
    const q = QUOTE.exec(text)!;
    out.push({ from: base, to: base + q[0].length, deco: Decoration.replace({}) });
    out.push({ from: base, to: base, deco: Decoration.line({ class: "cm-r-quote" }) });
  } else if (HR.test(text)) {
    out.push({ from: base, to: base + text.length,
      deco: Decoration.replace({ widget: new HrWidget() }) });
    return;
  } else if (TASK.test(text)) {
    // 머리표와 상자 기호를 통째로 체크 상자 하나로 바꾼다. 누르면 원문의 칸 한 글자를
    // 뒤집는다(편집기 mousedown 이 맡는다 — 위젯은 자기 자리를 모른다).
    const t = TASK.exec(text)!;
    const markFrom = base + t[1].length;
    const done = t[3] !== " ";
    out.push({ from: markFrom, to: base + t[0].length,
      deco: Decoration.replace({ widget: new TaskWidget(done) }) });
    if (done) out.push({ from: base, to: base, deco: Decoration.line({ class: "cm-r-done" }) });
  } else {
    const b = BULLET.exec(text);
    if (b) {
      // 불릿 기호(-,*)만 가운뎃점으로 치환, 들여쓰기 공백은 보존.
      const markFrom = base + b[1].length;
      out.push({ from: markFrom, to: markFrom + b[2].length + 1,
        deco: Decoration.replace({ widget: new BulletWidget() }) });
    }
  }
  // 인라인 — 짝 기호 숨김 + 안쪽 강조. **겹침 금지**(RangeSetBuilder 는 겹치는
  // 범위에 예외를 던진다): 이미 소비된 구간(occupied)과 겹치는 매치는 건너뛴다
  // (예: 링크 텍스트 안의 별표). 이 규율이 라이브 프리뷰 안정성의 핵심이다.
  const occupied: Array<[number, number]> = [];
  const overlaps = (s: number, e: number) =>
    occupied.some(([a, b]) => s < b && a < e);
  for (const { re, cls } of INLINE) {
    re.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = re.exec(text))) {
      const s = base + m.index, e = s + m[0].length;
      if (overlaps(s, e)) continue;
      const innerStart = s + m[0].indexOf(m[1]);
      const innerEnd = innerStart + m[1].length;
      out.push({ from: s, to: innerStart, deco: Decoration.replace({}) });   // 여는 기호
      out.push({ from: innerStart, to: innerEnd, deco: Decoration.mark({ class: cls }) });
      out.push({ from: innerEnd, to: e, deco: Decoration.replace({}) });     // 닫는 기호(+링크 URL)
      occupied.push([s, e]);
    }
  }
}

class HrWidget extends WidgetType {
  toDOM() { const el = document.createElement("span"); el.className = "cm-r-hr"; return el; }
}
class TaskWidget extends WidgetType {
  constructor(readonly done: boolean) { super(); }
  eq(o: TaskWidget) { return o.done === this.done; }
  toDOM() {
    const el = document.createElement("span");
    el.className = `cm-r-task${this.done ? " on" : ""}`;
    el.setAttribute("role", "checkbox");
    el.setAttribute("aria-checked", String(this.done));
    return el;
  }
  ignoreEvent() { return false; }
}
class BulletWidget extends WidgetType {
  toDOM() { const el = document.createElement("span"); el.className = "cm-r-bullet"; el.textContent = "· "; return el; }
}

// 커서가 없는 줄만 렌더 데코레이션을 만든다(활성 줄 = 원본 소스, 변형 0).
// **안전망**: 예기치 못한 겹침/정렬로 RangeSetBuilder 가 던지면 빈 데코를 돌려줘
// 편집기가 죽지 않게 한다(그 순간 라이브 프리뷰만 사라지고 소스=정본은 무손상).
function buildDecorations(view: EditorView): DecorationSet {
  try {
    return buildDecorationsUnsafe(view);
  } catch {
    return Decoration.none;
  }
}

function buildDecorationsUnsafe(view: EditorView): DecorationSet {
  const parts: { from: number; to: number; deco: Decoration }[] = [];
  const state = view.state;
  const sel = state.selection;
  const activeLines = new Set<number>();
  // 커서가 없으면 "활성 줄"도 없다 — 읽기 모드와, 편집할 수 있어도 아직 포커스가 없는 편집기.
  // (이게 없으면 문서를 열자마자 1행이 `# 제목` 처럼 원문으로 보였다 — 선택이
  //  기본 0 이라 1행이 활성으로 잡혔다. 편집 모드로 열린 문서도 같았다.)
  const editable = state.facet(EditorView.editable);
  if (editable && view.hasFocus) for (const r of sel.ranges) {
    let l = state.doc.lineAt(r.from).number;
    const last = state.doc.lineAt(r.to).number;
    for (; l <= last; l++) activeLines.add(l);
  }

  // 블록 층(프론트매터·표)이 이미 대체한 구간에는 **줄 데코를 태우지 않는다** —
  // 겹치면 RangeSetBuilder 가 던지고, 그러면 안전망이 라이브 프리뷰를 통째로 끈다.
  // 범위는 필드가 만들면서 함께 돌려준 것을 읽는다(같은 계산을 두 번 하지 않는다).
  const blocks = state.field(blockLayer, false)?.ranges ?? [];
  const covered = (from: number, to: number) =>
    blocks.some((b) => from < b.to && b.from < to);

  for (const { from, to } of view.visibleRanges) {
    let pos = from;
    while (pos <= to) {
      const line = state.doc.lineAt(pos);
      if (!activeLines.has(line.number) && line.length > 0 && !covered(line.from, line.to))
        lineDecos(line.text, line.from, parts);
      pos = line.to + 1;
    }
  }
  // CM6 계약: 데코레이션은 from 오름차순 + 같은 from 이면 startSide 순. line 데코를
  // 먼저(폭 0, 위치 앞) 두도록 정렬 안정화한 뒤 RangeSetBuilder 로 싣는다.
  parts.sort((a, b) => a.from - b.from || a.to - b.to);
  const builder = new RangeSetBuilder<Decoration>();
  for (const p of parts) builder.add(p.from, p.to, p.deco);
  return builder.finish();
}

// ── 개념 언급 밑줄(D45-V2) — 렌더 위에 얹는 별도 데코 층 ────────────────────
// 라이브 프리뷰와 **다른 세트**라 겹쳐도 안전하다(CM6 가 세트끼리 병합한다).
// 문서가 편집되면 CM6 의 매핑이 범위를 따라 옮겨 주므로, 타이핑 중에도 밑줄이
// 글자를 놓치지 않는다(구 Reader 의 정적 문자열 삽입은 편집이 불가능했다).
const setHl = StateEffect.define<{ from: number; to: number }[]>();
const hlMark = Decoration.mark({ class: "cm-r-hl" });
const highlightField = StateField.define<DecorationSet>({
  create: () => Decoration.none,
  update(deco, tr) {
    deco = deco.map(tr.changes);
    for (const e of tr.effects) {
      if (!e.is(setHl)) continue;
      const b = new RangeSetBuilder<Decoration>();
      const len = tr.state.doc.length;
      for (const r of e.value.slice().sort((x, y) => x.from - y.from)) {
        const from = Math.max(0, Math.min(r.from, len));
        const to = Math.max(from, Math.min(r.to, len));
        if (to > from) b.add(from, to, hlMark);
      }
      deco = b.finish();
    }
    return deco;
  },
  provide: (f) => EditorView.decorations.from(f),
});

const livePreview = ViewPlugin.fromClass(class {
  decorations: DecorationSet;
  constructor(view: EditorView) { this.decorations = buildDecorations(view); }
  update(u: ViewUpdate) {
    // 프론트매터 접기/펼치기는 문서도 선택도 뷰포트도 바꾸지 않는다 — 이 항이
    // 없으면 눌러도 화면이 그대로다(다음 타이핑에서야 뒤늦게 반영된다).
    const toggled = u.transactions.some((t) => t.effects.some((e) => e.is(fmToggle)));
    // 포커스가 들고 나면 활성 줄이 생기고 사라진다(위 `buildDecorationsUnsafe`).
    if (u.docChanged || u.selectionSet || u.viewportChanged || u.focusChanged || toggled)
      this.decorations = buildDecorations(u.view);
  }
}, { decorations: (v) => v.decorations });

// ── 슬래시 커맨드(D45-E 마감) — 줄 머리의 "/" → 블록 메뉴 ──────────────────────
// 줄이 "/" 로 시작하고 커서가 줄 끝이면 뜬다. "/" 뒤 글자로 거른다("/표" · "/tab").
// 목록은 삽입 메뉴와 같은 블록이다(cm-insert) — 문이 셋이어도 넣는 것은 한 벌이다.
class SlashMenu {
  dom: HTMLElement;
  items: InsertBlock[] = [];
  sel = 0;
  open = false;
  constructor(private view: EditorView, private list: InsertBlock[]) {
    this.dom = document.createElement("div");
    this.dom.className = "cm-slash-menu cm-insert";
    this.dom.hidden = true;
    document.body.appendChild(this.dom);
  }
  show(lineFrom: number, query: string) {
    const was = this.open;
    this.items = filterBlocks(this.list, query);
    if (!this.items.length) { this.hide(); return; }
    this.sel = was ? Math.min(this.sel, this.items.length - 1) : 0;
    const c = this.view.coordsAtPos(lineFrom);
    if (!c) { this.hide(); return; }
    this.open = true;
    this.render();
    this.dom.hidden = false;
    const h = this.dom.offsetHeight;
    const below = c.bottom + 4 + h < window.innerHeight;
    this.dom.style.left = `${Math.round(c.left)}px`;
    this.dom.style.top = `${Math.round(below ? c.bottom + 4 : c.top - h - 4)}px`;
  }
  render() {
    this.dom.innerHTML = this.items.map((b, i) => `<div class="cm-ins-row${i === this.sel ? " on" : ""}" data-i="${i}">
        <span class="cm-ins-glyph" aria-hidden="true">${esc(b.glyph)}</span>
        <span class="cm-ins-label">${esc(b.label)}</span>
        <span class="cm-ins-hint" aria-hidden="true">${esc(b.hint)}</span></div>`).join("");
    this.dom.querySelectorAll<HTMLElement>(".cm-ins-row").forEach((el) => {
      el.addEventListener("mousedown", (e) => { e.preventDefault(); this.choose(Number(el.dataset.i)); });
    });
    this.dom.querySelector(".cm-ins-row.on")?.scrollIntoView({ block: "nearest" });
  }
  move(d: number) { if (!this.open) return; this.sel = (this.sel + d + this.items.length) % this.items.length; this.render(); }
  hide() { this.open = false; this.dom.hidden = true; }
  choose(i = this.sel) {
    if (!this.open) return;
    const it = this.items[i]; if (!it) return;
    this.hide();
    // "/질의" 줄을 빈 줄로 보고 그 자리를 블록으로 갈아 끼운다.
    insertBlock(this.view, it, [3, 3], true);
  }
  destroy() { this.dom.remove(); }
}

/** CM6 인라인 라이브 프리뷰 편집기를 el 에 마운트. 실패 시 예외를 던져 호출부가
 *  MVP textarea 로 폴백하게 한다(무손실은 어느 쪽이든 동일 — 소스가 정본). */
export function mountEditor(el: HTMLElement, opts: {
  initial: string; onChange: (text: string) => void; onSave: () => void;
  /** 삽입 목록(슬래시 · 우클릭 · + 버튼 공용). 카피는 호출부가 준다. */
  insert?: InsertOpts;
  /** 시작 상태. Reader 진입은 읽기 전용이지만 **렌더는 완전히 같다** — 편집을 켜도
   *  화면이 다시 그려지지 않고 커서만 생긴다. */
  readOnly?: boolean;
  /** 접힌 프론트매터 줄의 라벨(카피는 호출부가 준다 — slash 항목과 같은 규율). */
  fmLabel?: string;
  /** `[[doc_id]]` 를 눌렀다. 편집기는 문서를 여는 법을 모른다(View 를 임포트하지
   *  않는다 — slash·fmLabel 과 같은 규율) — 호출부가 그 문을 준다. */
  onDocLink?: (docId: string) => void;
  /** `[text](url)` 를 Ctrl/Cmd+클릭했다. 여는 방법은 셸이냐 웹이냐로 갈리므로
   *  역시 호출부가 안다. */
  onUrl?: (url: string) => void;
  /** `[[` 노드 찾기(흐름 C). 편집 가능할 때만 뜬다. */
  mention?: MentionOpts;
  /** 고른 글 위의 작은 줄(흐름 D). 읽기 모드에서도 뜬다 — 읽다가 만드는 손도 있다. */
  selection?: SelectionOpts;
}): CmHandle {
  let slash: SlashMenu | null = null;
  let inserter: InsertMenu | null = null;
  /** 사람이 이 편집기에 한 번이라도 커서를 둔 적이 있는가. 없으면 + 버튼은 문서 끝에 넣는다
   *  (첫 마운트의 선택은 0 이라 그대로 넣으면 문서 맨 앞, 프론트매터 위에 들어간다). */
  let touched = false;
  let mention: MentionMenu | null = null;
  let selbar: SelectionBar | null = null;
  const view = new EditorView({
    parent: el,
    state: EditorState.create({
      doc: opts.initial,
      extensions: [
        history(),
        // 슬래시 메뉴가 열려 있으면 방향키·Enter·Esc 를 메뉴가 가로챈다(높은 우선순위).
        // `[[` 목록도 같은 방식으로 키를 가로챈다. 둘이 동시에 뜨는 일은 없다(슬래시는
        // "/" 한 글자 줄에서만, `[[` 는 그 뒤에 글자가 있을 때만).
        // `stopPropagation` — 목록이 받은 키는 문서 밖으로 새지 않는다. 안 막으면 목록을 닫는
        // Esc 가 View 까지 올라가 문서 열을 통째로 닫는다(목록이 떠 있을 때만 막힌다: run 이
        // false 면 CM 은 이 바인딩을 처리하지 않은 것으로 본다).
        keymap.of([
          { key: "ArrowDown", stopPropagation: true, run: () => { if (mention?.open) { mention.move(1); return true; } return false; } },
          { key: "ArrowUp", stopPropagation: true, run: () => { if (mention?.open) { mention.move(-1); return true; } return false; } },
          { key: "Enter", stopPropagation: true, run: () => { if (mention?.open) { void mention.choose(); return true; } return false; } },
          { key: "Tab", stopPropagation: true, run: () => { if (mention?.open) { void mention.choose(); return true; } return false; } },
          { key: "Escape", stopPropagation: true, run: () => { if (mention?.open) { mention.hide(); return true; } return false; } },
        ]),
        keymap.of([
          { key: "ArrowDown", run: () => { if (slash?.open) { slash.move(1); return true; } return false; } },
          { key: "ArrowUp", run: () => { if (slash?.open) { slash.move(-1); return true; } return false; } },
          { key: "Enter", run: () => { if (slash?.open) { slash.choose(); return true; } return false; } },
          { key: "Escape", run: () => { if (slash?.open) { slash.hide(); return true; } return false; } },
        ]),
        keymap.of([
          { key: "Mod-s", preventDefault: true, run: () => { opts.onSave(); return true; } },
          // 서식 단축키 — 고른 글을 씌우고, 이미 씌워져 있으면 벗긴다.
          { key: "Mod-b", preventDefault: true, run: (v) => { applyFormat(v, "bold"); return true; } },
          { key: "Mod-i", preventDefault: true, run: (v) => { applyFormat(v, "italic"); return true; } },
          { key: "Mod-k", preventDefault: true, run: (v) => {
            applyFormat(v, "link", opts.insert?.labels.linkText); return true; } },
          { key: "Mod-Shift-x", preventDefault: true, run: (v) => { applyFormat(v, "strike"); return true; } },
          ...defaultKeymap, ...historyKeymap,
        ]),
        // GFM 기반으로 파싱한다(표·취소선·태스크리스트·자동링크). 인자 없이 부르면
        // CommonMark 라 **표가 문법으로 인식조차 되지 않는다**. GFM 확장은 lang-markdown
        // 이 최상단에서 무조건 import 하므로 이미 번들에 들어 있다 — 번들 증가 0.
        markdown({ base: markdownLanguage }),
        // 순서가 곧 초기화 순서다: blockLayer 가 fmOpen·fmLabel 을 읽으므로 뒤에 온다.
        fmOpen,
        fmLabel.of(opts.fmLabel ?? "Properties"),
        blockLayer,
        livePreview,
        highlightField,
        // 마운트 때 한 번 정해진다 — 뒤집는 문이 없다(레일은 상시 편집이다).
        EditorView.editable.of(!opts.readOnly),
        // 링크를 따라간다. 자리를 데코레이션이 아니라 **본문에서 다시 찾는** 이유:
        // 소스가 정본이라 offset 하나면 무엇을 눌렀는지 알 수 있고, 데코레이션에
        // 속성을 달면 그 속성이 정본과 어긋날 자리를 하나 더 만든다.
        //
        // 평클릭의 임자는 **커서**다. 그래서:
        //  · `[text](url)` 는 Ctrl/Cmd+클릭에서만 연다(그리고 http(s) 만 — 로컬
        //    파일 스킴을 문서가 열게 두지 않는다).
        //  · `[[doc_id]]` 는 렌더된 줄에서만 평클릭으로 연다. 커서가 놓인 줄은 원본
        //    소스를 보이는 자리이고(라이브 프리뷰의 규율), 거기서까지 클릭을 뺏으면
        //    그 링크는 **손으로 고칠 수 없는 글자**가 된다.
        EditorView.domEventHandlers({
          // 우클릭 = 삽입 메뉴. 브라우저 메뉴는 Shift+우클릭으로 그대로 남는다.
          contextmenu: (e, v) => {
            if (!inserter || e.shiftKey) return false;
            e.preventDefault();
            // 고른 글 밖을 눌렀으면 커서를 그 자리로 옮긴다(넣을 자리가 누른 곳이 되게).
            const pos = v.posAtCoords({ x: e.clientX, y: e.clientY });
            const s = v.state.selection.main;
            if (pos != null && (s.empty || pos < s.from || pos > s.to))
              v.dispatch({ selection: { anchor: pos } });
            touched = true;
            inserter.show({ x: e.clientX, y: e.clientY }, { clipboard: true });
            return true;
          },
          focus: () => { touched = true; return false; },
          mousedown: (e, v) => {
            if (e.button !== 0) return false;
            // 체크 상자 — 원문의 칸 한 글자를 뒤집는다. 편집 불가면 건드리지 않는다.
            const box = (e.target as HTMLElement).closest?.(".cm-r-task");
            if (box) {
              if (!v.state.facet(EditorView.editable)) return false;
              e.preventDefault();
              const line = v.state.doc.lineAt(v.posAtDOM(box));
              const t = TASK.exec(line.text);
              if (!t) return true;
              const at = line.from + t[0].indexOf("[") + 1;
              v.dispatch({ changes: { from: at, to: at + 1, insert: t[3] === " " ? "x" : " " },
                           userEvent: "input" });
              return true;
            }
            const pos = v.posAtCoords({ x: e.clientX, y: e.clientY });
            if (pos == null) return false;
            const line = v.state.doc.lineAt(pos);
            const at = pos - line.from;
            const mod = e.metaKey || e.ctrlKey;
            const onCursorLine =
              v.state.doc.lineAt(v.state.selection.main.head).number === line.number;
            const hit = (re: RegExp) => {
              re.lastIndex = 0;
              for (let m = re.exec(line.text); m; m = re.exec(line.text))
                if (at >= m.index && at <= m.index + m[0].length) return m;
              return null;
            };
            const wiki = opts.onDocLink ? hit(WIKI_RE) : null;
            if (wiki && (mod || !onCursorLine)) {
              e.preventDefault();
              opts.onDocLink!(wiki[1].trim());
              return true;
            }
            if (!mod || !opts.onUrl) return false;
            const md = hit(MDLINK_RE);
            const url = md?.[2].trim() || "";
            if (!/^https?:\/\//i.test(url)) return false;
            e.preventDefault();
            opts.onUrl(url);
            return true;
          },
        }),
        EditorView.lineWrapping,
        EditorView.updateListener.of((u) => {
          if (u.docChanged) opts.onChange(view.state.doc.toString());
          // 슬래시 트리거 — 줄이 "/낱말" 하나이고 커서가 그 끝이면 메뉴, 아니면 닫는다.
          if (slash && (u.docChanged || u.selectionSet)) {
            const sel = u.state.selection.main;
            const line = u.state.doc.lineAt(sel.head);
            const m = /^\/([^\s/]{0,24})$/.exec(line.text);
            if (sel.empty && m && sel.head === line.to && (u.docChanged || slash.open)) slash.show(line.from, m[1]);
            else slash.hide();
          }
          // `[[질의` — 커서 바로 앞에 있을 때만. 선택 영역이 있거나 커서가 떠나면 닫는다.
          if (mention && (u.docChanged || u.selectionSet)) {
            const sel = u.state.selection.main;
            const line = u.state.doc.lineAt(sel.head);
            const m = sel.empty ? MENTION_RE.exec(line.text.slice(0, sel.head - line.from)) : null;
            if (m && (u.docChanged || mention.open)) mention.update(line.from + m.index, m[1]);
            else mention.hide();
          }
          // 키보드로 고른 글(Shift+화살표)도 작은 줄을 부른다. 마우스는 아래 mouseup 이 맡는다.
          if (selbar && u.selectionSet && !u.docChanged) {
            const sel = u.state.selection.main;
            if (sel.empty) selbar.hide();
            else if (u.view.hasFocus) {
              const a = u.view.coordsAtPos(sel.from), b = u.view.coordsAtPos(sel.to);
              if (a && b) selbar.showAt(u.state.sliceDoc(sel.from, sel.to),
                { left: Math.min(a.left, b.left), right: Math.max(a.right, b.right),
                  top: Math.min(a.top, b.top), bottom: Math.max(a.bottom, b.bottom) });
            }
          } else if (selbar && u.docChanged) selbar.hide();
        }),
        EditorView.theme({
          "&": { backgroundColor: "transparent", fontSize: "0.92em" },
          ".cm-content": { fontFamily: "inherit", padding: "4px 2px", caretColor: "hsl(var(--ring))" },
          "&.cm-focused": { outline: "none" },
          ".cm-r-h": { fontWeight: "700" },
          ".cm-r-h1": { fontSize: "1.5em" }, ".cm-r-h2": { fontSize: "1.3em" },
          ".cm-r-h3": { fontSize: "1.15em" }, ".cm-r-h4": { fontSize: "1.05em" },
          ".cm-r-strong": { fontWeight: "700" },
          ".cm-r-em": { fontStyle: "italic" },
          ".cm-r-strike": { textDecoration: "line-through" },
          ".cm-r-code": { fontFamily: "ui-monospace, monospace", opacity: "0.85" },
          ".cm-r-link": { textDecoration: "underline" },
          // 문서 상호참조 — 따라갈 수 있는 글자라는 것만 밑줄로 말한다(색은 안 쓴다:
          // 초점색은 화면당 한 의미 예약이고, 여기 색을 주면 지도의 초점과 겨룬다).
          ".cm-r-doclink": { textDecoration: "underline", cursor: "pointer" },
          ".cm-r-quote": { opacity: "0.72", borderLeft: "2px solid hsl(var(--border))", paddingLeft: "8px" },
          ".cm-r-bullet": { opacity: "0.6" },
          ".cm-r-hr": { display: "inline-block", width: "100%", borderTop: "1px solid hsl(var(--border))", verticalAlign: "middle" },
          // 밑줄은 색이 아니라 선으로(모노크롬) — 초점색은 화면당 1의미 예약이다.
          ".cm-r-hl": { textDecoration: "underline", textUnderlineOffset: "2px", textDecorationThickness: "1px" },
          // 읽기 모드에서도 글자는 고를 수 있어야 한다(인용·복사).
          "&:not(.cm-focused) .cm-cursor": { display: "none" },
        }),
      ],
    }),
  });
  if (opts.insert?.blocks.length && !opts.readOnly) {
    slash = new SlashMenu(view, opts.insert.blocks);
    inserter = new InsertMenu(view, opts.insert);
  }
  if (opts.mention && !opts.readOnly) mention = new MentionMenu(view, opts.mention);
  if (opts.selection) {
    const bar = selbar = new SelectionBar(opts.selection);
    // 마우스로 고른 글 — 읽기 모드에서는 편집기가 선택을 상태로 옮기지 않으므로 창의
    // 선택에서 읽는다. 편집기 밖으로 끌고 나간 선택은 받지 않는다.
    view.dom.addEventListener("mouseup", () => window.setTimeout(() => {
      const s = window.getSelection();
      if (!s || s.isCollapsed || !s.rangeCount || !view.dom.contains(s.anchorNode)) { bar.hide(); return; }
      const r = s.getRangeAt(0).getBoundingClientRect();
      bar.showAt(s.toString(), r);
    }, 0));
    view.dom.addEventListener("focusout", () => window.setTimeout(() => {
      if (!view.dom.contains(document.activeElement)) bar.hide();
    }, 150));
    view.scrollDOM.addEventListener("scroll", () => bar.hide(), { passive: true });
  }
  let hl: { char_start: number; char_end: number }[] = [];
  /** 오프셋 하나로 스크롤. 문서 길이로 자르는 이유: 청크 오프셋은 저장 시점의
   *  것이라 그 뒤 편집으로 짧아졌을 수 있다 — 넘는 값을 그대로 넣으면 던진다. */
  const scrollTo = (pos: number, align: "center" | "start" = "center") => {
    const at = Math.max(0, Math.min(pos, view.state.doc.length));
    view.dispatch({ effects: EditorView.scrollIntoView(at, { y: align, yMargin: 72 }) });
  };
  return {
    getValue: () => view.state.doc.toString(),
    setValue: (text) => view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: text } }),
    focus: () => view.focus(),
    destroy: () => { slash?.destroy(); inserter?.destroy(); mention?.destroy(); selbar?.destroy(); view.destroy(); },
    setHighlights: (ranges) => {
      hl = ranges;
      view.dispatch({ effects: setHl.of(ranges.map((r) => ({ from: r.char_start, to: r.char_end }))) });
    },
    scrollToHighlight: () => {
      const first = hl.slice().sort((a, b) => a.char_start - b.char_start)[0];
      if (first) scrollTo(first.char_start);
    },
    scrollToOffset: scrollTo,
    lineTop: (pos) => {
      const at = Math.max(0, Math.min(pos, view.state.doc.length));
      return view.lineBlockAt(at).top + view.documentTop;
    },
    openInsert: (at) => {
      if (!inserter) return;
      if (!touched) {
        view.dispatch({ selection: { anchor: view.state.doc.length } });
        touched = true;
      }
      inserter.show(at, { clipboard: false });
    },
  };
}
