// CodeMirror 6 인라인 Live Preview 편집기(D45-E 2단계). Obsidian 모델:
// **EditorState.doc 가 곧 정본**(순수 마크다운) — 렌더는 view-only 데코레이션일
// 뿐이라 저장/복원이 바이트 동일(verified 무손실 구조 보장). 커서가 놓인 줄은
// 원본 소스를 그대로 보이고(활성 줄은 절대 변형 안 함 → 타이핑 중 "튐" 회피),
// 커서가 없는 줄만 마크다운 기호를 숨기고 서식으로 렌더한다.
//
// 의존성은 npm 번들 인라인(CSP 정합, CDN 없음). 스타일은 CSS/데코레이션 클래스로
// 완전 제어(모노크롬 Ink & Paper — 색조 없이 굵기·크기·명도만).
import {
  EditorState, Compartment, Facet, RangeSetBuilder, StateEffect, StateField,
} from "@codemirror/state";
import {
  EditorView, keymap, Decoration, ViewPlugin, WidgetType,
  type DecorationSet, type ViewUpdate,
} from "@codemirror/view";
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { markdown, markdownLanguage } from "@codemirror/lang-markdown";
import { syntaxTree } from "@codemirror/language";

/** 슬래시 커맨드 항목 — 결정론 블록 삽입(오프라인·무LLM). insert = 삽입할 마크다운
 *  템플릿, cursorBack = 삽입 후 커서를 끝에서 몇 칸 되돌릴지(코드펜스 안쪽 등). */
export type SlashItem = { label: string; insert: string; cursorBack?: number };

export type CmHandle = {
  getValue: () => string;
  setValue: (text: string) => void;
  focus: () => void;
  destroy: () => void;
  /** 읽기/편집 전환 — **같은 렌더를 그대로 두고** 편집 가능 여부만 바꾼다.
   *  Reader(문서 보기)와 편집기가 한 표면인 이유: 보던 글자가 그 자리에서
   *  고쳐진다(모드 점프도, 원문 마크다운 노출도 없다). */
  setEditable: (on: boolean) => void;
  /** 개념 언급 위치(밑줄) 갱신 + 첫 위치로 스크롤. char 오프셋은 백엔드 계산값. */
  setHighlights: (ranges: { char_start: number; char_end: number }[]) => void;
  scrollToHighlight: () => void;
  /** 임의 오프셋으로 스크롤 — 목차 클릭과 검색의 청크 히트 착지가 쓴다.
   *  범위를 넘는 값은 문서 끝으로 잘린다(재청킹으로 오프셋이 어긋나도 튀지 않는다). */
  scrollToOffset: (pos: number) => void;
};

// 인라인 문법 토큰 — 짝 기호를 숨기고(replace) 안쪽을 서식(mark). 순서 주의:
// 코드(백틱)를 먼저 잡아 그 안의 별표를 강조로 오인하지 않게 한다.
type Tok = { re: RegExp; cls: string };
const INLINE: Tok[] = [
  { re: /`([^`]+)`/g, cls: "cm-r-code" },
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
const HR = /^(-{3,}|\*{3,})\s*$/;

// 표(GFM) — 줄 단위로 판별한다. 표는 다중행 블록이라 lineDecos 의 한 줄 계약으로는
// "격자"를 그릴 수 없다. 대신 **정렬을 살리는 데 필요한 것만** 한다: 등폭 + 줄바꿈
// 금지(가로 스크롤). 소스의 공백 정렬이 그대로 열로 보인다.
//   TABLE_ROW  : `|` 로 시작하는 줄
//   TABLE_DELIM: `|---|---:|` 구분행. 기호뿐이라 통째로 숨긴다.
//     `--` 연속 2개 이상을 요구해 `| - | - |`(대시가 셀 내용인 정상 행)와 가른다.
const TABLE_ROW = /^\s*\|/;
const TABLE_DELIM = /^[\s|:-]+$/;

const escHtml = (s: string) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  c === "&" ? "&amp;" : c === "<" ? "&lt;" : c === ">" ? "&gt;" : "&quot;");

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
      `<span class="cm-r-fm-cap">${this.open ? "▾" : "▸"} ${escHtml(this.label)}</span>`
      + (this.open ? "" : `<span class="cm-r-fm-sum">${escHtml(this.summary)}</span>`);
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
// 커서가 표 안에 있으면 위젯을 걷고 원문을 보인다(활성 줄 = 소스 규율 그대로).

/** `|` 로 셀을 가른다. 이스케이프된 `\|` 는 셀 내용이지 경계가 아니다. */
function splitCells(line: string): string[] {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|")) s = s.slice(0, -1);
  return s.split(/(?<!\\)\|/).map((c) => c.replace(/\\\|/g, "|").trim());
}

/** 셀 안 인라인. **이스케이프가 먼저**다 — 아래 치환이 innerHTML 로 들어간다. */
function cellHtml(raw: string): string {
  return escHtml(raw)
    .replace(/`([^`]+)`/g, '<code class="cm-r-code">$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, "<em>$1</em>")
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, "<u>$1</u>");
}

const cellAlign = (spec: string): string =>
  /^:-+:$/.test(spec) ? "center" : /^-+:$/.test(spec) ? "right"
    : /^:-+$/.test(spec) ? "left" : "";

function tableHtml(src: string): string {
  const rows = src.split("\n").filter((l) => l.trim());
  if (!rows.length) return "";
  let align: string[] = [];
  let bodyFrom = 1;
  if (rows[1] && TABLE_DELIM.test(rows[1]) && rows[1].includes("--")) {
    align = splitCells(rows[1]).map((c) => cellAlign(c.replace(/\s/g, "")));
    bodyFrom = 2;
  }
  const cell = (t: string, i: number, tag: "th" | "td") =>
    `<${tag}${align[i] ? ` style="text-align:${align[i]}"` : ""}>${cellHtml(t)}</${tag}>`;
  const head = splitCells(rows[0]).map((c, i) => cell(c, i, "th")).join("");
  const body = rows.slice(bodyFrom)
    .map((r) => `<tr>${splitCells(r).map((c, i) => cell(c, i, "td")).join("")}</tr>`)
    .join("");
  return `<table class="cm-r-tbl"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

class TableWidget extends WidgetType {
  constructor(readonly src: string) { super(); }
  eq(o: TableWidget) { return o.src === this.src; }
  toDOM() {
    const wrap = document.createElement("div");
    wrap.className = "cm-r-tablewrap";
    wrap.innerHTML = tableHtml(this.src);
    return wrap;
  }
  // 이벤트를 삼키지 않는다 — 편집 중에 표를 클릭하면 커서가 그 범위에 들어가고,
  // 그 순간 위젯이 걷히며 원문이 나온다(클릭 = 고치기). 읽기 모드에서는 활성 줄이
  // 없으므로 클릭해도 표인 채로 남는다.
  ignoreEvent() { return false; }
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
      widget: new TableWidget(state.doc.sliceString(t.from, t.to)), block: true }) });
    return false;
  } });

  parts.sort((a, b) => a.from - b.from || a.to - b.to);
  const builder = new RangeSetBuilder<Decoration>();
  for (const p of parts) builder.add(p.from, p.to, p.deco);
  return { deco: builder.finish(), ranges };
}

const blockLayer = StateField.define<Blocks>({
  create: (state) => buildBlocks(state),
  update(v, tr) {
    const editableChanged =
      tr.startState.facet(EditorView.editable) !== tr.state.facet(EditorView.editable);
    const selChanged = !tr.startState.selection.eq(tr.state.selection);
    const toggled = tr.effects.some((e) => e.is(fmToggle));
    if (!tr.docChanged && !selChanged && !toggled && !editableChanged) return v;
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
  // 읽기 모드에는 "활성 줄"이 없다 — 커서가 없으니 소스를 보일 이유도 없다.
  // (이게 없으면 문서를 열자마자 1행이 `# 제목` 처럼 원문으로 보였다 — 선택이
  //  기본 0 이라 1행이 활성으로 잡혔다.)
  const editable = state.facet(EditorView.editable);
  if (editable) for (const r of sel.ranges) {
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
    if (u.docChanged || u.selectionSet || u.viewportChanged || toggled)
      this.decorations = buildDecorations(u.view);
  }
}, { decorations: (v) => v.decorations });

// ── 슬래시 커맨드(D45-E 마감) — 빈 줄에서 "/" → 결정론 블록 메뉴 ──────────────
// 커서가 "/" 하나뿐인 줄에 있으면 뜨는 작은 메뉴. 선택 = "/" 를 블록 템플릿으로
// 교체(결정론·오프라인·무LLM). 상태는 View 옆의 DOM 하나로 관리한다.
class SlashMenu {
  dom: HTMLElement;
  items: SlashItem[] = [];
  sel = 0;
  open = false;
  from = 0;   // "/" 시작 오프셋(교체 범위)
  constructor(private view: EditorView, private list: SlashItem[]) {
    this.dom = document.createElement("div");
    this.dom.className = "cm-slash-menu palette-card";
    this.dom.style.cssText = "position:absolute;z-index:70;min-width:11rem;padding:.25rem;"
      + "display:none;font-size:.85em";
    document.body.appendChild(this.dom);
  }
  show(from: number) {
    this.items = this.list; this.sel = 0; this.from = from; this.open = true;
    const c = this.view.coordsAtPos(from);
    if (!c) { this.hide(); return; }
    this.dom.style.left = `${Math.round(c.left)}px`;
    this.dom.style.top = `${Math.round(c.bottom + 4)}px`;
    this.render();
    this.dom.style.display = "block";
  }
  render() {
    this.dom.innerHTML = this.items.map((it, i) =>
      `<div class="cm-slash-item list-row${i === this.sel ? " sel" : ""}" data-i="${i}"
        style="padding:.2rem .4rem;cursor:pointer;${i === this.sel
          ? "background:hsl(var(--muted))" : ""}">${it.label.replace(/[&<>"]/g, (c) =>
            c === "&" ? "&amp;" : c === "<" ? "&lt;" : c === ">" ? "&gt;" : "&quot;")}</div>`)
      .join("");
    this.dom.querySelectorAll<HTMLElement>(".cm-slash-item").forEach((el) => {
      el.addEventListener("mousedown", (e) => { e.preventDefault(); this.choose(Number(el.dataset.i)); });
    });
  }
  move(d: number) { if (!this.open) return; this.sel = (this.sel + d + this.items.length) % this.items.length; this.render(); }
  hide() { this.open = false; this.dom.style.display = "none"; }
  choose(i = this.sel) {
    if (!this.open) return;
    const it = this.items[i]; if (!it) return;
    const to = this.from + 1;   // "/" 한 글자
    const insert = it.insert;
    const cursor = this.from + insert.length - (it.cursorBack || 0);
    this.view.dispatch({
      changes: { from: this.from, to, insert },
      selection: { anchor: cursor },
    });
    this.hide(); this.view.focus();
  }
  destroy() { this.dom.remove(); }
}

/** CM6 인라인 라이브 프리뷰 편집기를 el 에 마운트. 실패 시 예외를 던져 호출부가
 *  MVP textarea 로 폴백하게 한다(무손실은 어느 쪽이든 동일 — 소스가 정본). */
export function mountEditor(el: HTMLElement, opts: {
  initial: string; onChange: (text: string) => void; onSave: () => void;
  slash?: SlashItem[];
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
}): CmHandle {
  const editable = new Compartment();
  let slash: SlashMenu | null = null;
  const view = new EditorView({
    parent: el,
    state: EditorState.create({
      doc: opts.initial,
      extensions: [
        history(),
        // 슬래시 메뉴가 열려 있으면 방향키·Enter·Esc 를 메뉴가 가로챈다(높은 우선순위).
        keymap.of([
          { key: "ArrowDown", run: () => { if (slash?.open) { slash.move(1); return true; } return false; } },
          { key: "ArrowUp", run: () => { if (slash?.open) { slash.move(-1); return true; } return false; } },
          { key: "Enter", run: () => { if (slash?.open) { slash.choose(); return true; } return false; } },
          { key: "Escape", run: () => { if (slash?.open) { slash.hide(); return true; } return false; } },
        ]),
        keymap.of([
          { key: "Mod-s", preventDefault: true, run: () => { opts.onSave(); return true; } },
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
        editable.of(EditorView.editable.of(!opts.readOnly)),
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
          mousedown: (e, v) => {
            if (e.button !== 0) return false;
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
          // 슬래시 트리거 — 커서가 "/" 하나뿐인 줄에 있으면 메뉴, 아니면 닫는다.
          if (slash && (u.docChanged || u.selectionSet)) {
            const sel = u.state.selection.main;
            const line = u.state.doc.lineAt(sel.head);
            if (sel.empty && line.text === "/" && sel.head === line.to) slash.show(line.from);
            else slash.hide();
          }
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
  if (opts.slash && opts.slash.length) slash = new SlashMenu(view, opts.slash);
  let hl: { char_start: number; char_end: number }[] = [];
  /** 오프셋 하나로 스크롤. 문서 길이로 자르는 이유: 청크 오프셋은 저장 시점의
   *  것이라 그 뒤 편집으로 짧아졌을 수 있다 — 넘는 값을 그대로 넣으면 던진다. */
  const scrollTo = (pos: number) => {
    const at = Math.max(0, Math.min(pos, view.state.doc.length));
    view.dispatch({ effects: EditorView.scrollIntoView(at, { y: "center" }) });
  };
  return {
    getValue: () => view.state.doc.toString(),
    setValue: (text) => view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: text } }),
    focus: () => view.focus(),
    destroy: () => { slash?.destroy(); view.destroy(); },
    setEditable: (on) => {
      view.dispatch({ effects: editable.reconfigure(EditorView.editable.of(on)) });
      if (on) view.focus();
    },
    setHighlights: (ranges) => {
      hl = ranges;
      view.dispatch({ effects: setHl.of(ranges.map((r) => ({ from: r.char_start, to: r.char_end }))) });
    },
    scrollToHighlight: () => {
      const first = hl.slice().sort((a, b) => a.char_start - b.char_start)[0];
      if (first) scrollTo(first.char_start);
    },
    scrollToOffset: scrollTo,
  };
}
