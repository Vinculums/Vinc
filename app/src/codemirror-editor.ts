// CodeMirror 6 인라인 Live Preview 편집기(D45-E 2단계). Obsidian 모델:
// **EditorState.doc 가 곧 정본**(순수 마크다운) — 렌더는 view-only 데코레이션일
// 뿐이라 저장/복원이 바이트 동일(verified 무손실 구조 보장). 커서가 놓인 줄은
// 원본 소스를 그대로 보이고(활성 줄은 절대 변형 안 함 → 타이핑 중 "튐" 회피),
// 커서가 없는 줄만 마크다운 기호를 숨기고 서식으로 렌더한다.
//
// 의존성은 npm 번들 인라인(CSP 정합, CDN 없음). 스타일은 CSS/데코레이션 클래스로
// 완전 제어(모노크롬 Ink & Paper — 색조 없이 굵기·크기·명도만).
import { EditorState, Compartment, RangeSetBuilder, StateEffect, StateField } from "@codemirror/state";
import {
  EditorView, keymap, Decoration, ViewPlugin, WidgetType,
  type DecorationSet, type ViewUpdate,
} from "@codemirror/view";
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { markdown, markdownLanguage } from "@codemirror/lang-markdown";

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
};

// 인라인 문법 토큰 — 짝 기호를 숨기고(replace) 안쪽을 서식(mark). 순서 주의:
// 코드(백틱)를 먼저 잡아 그 안의 별표를 강조로 오인하지 않게 한다.
type Tok = { re: RegExp; cls: string };
const INLINE: Tok[] = [
  { re: /`([^`]+)`/g, cls: "cm-r-code" },
  { re: /\*\*([^*\n]+)\*\*/g, cls: "cm-r-strong" },
  { re: /(?<![*])\*([^*\n]+)\*(?![*])/g, cls: "cm-r-em" },
  { re: /\[([^\]\n]+)\]\(([^)\n]+)\)/g, cls: "cm-r-link" },
];

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
  const sel = view.state.selection;
  const activeLines = new Set<number>();
  // 읽기 모드에는 "활성 줄"이 없다 — 커서가 없으니 소스를 보일 이유도 없다.
  // (이게 없으면 문서를 열자마자 1행이 `# 제목` 처럼 원문으로 보였다 — 선택이
  //  기본 0 이라 1행이 활성으로 잡혔다.)
  const editable = view.state.facet(EditorView.editable);
  if (editable) for (const r of sel.ranges) {
    let l = view.state.doc.lineAt(r.from).number;
    const last = view.state.doc.lineAt(r.to).number;
    for (; l <= last; l++) activeLines.add(l);
  }
  for (const { from, to } of view.visibleRanges) {
    let pos = from;
    while (pos <= to) {
      const line = view.state.doc.lineAt(pos);
      if (!activeLines.has(line.number) && line.length > 0)
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
    if (u.docChanged || u.selectionSet || u.viewportChanged)
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
        livePreview,
        highlightField,
        editable.of(EditorView.editable.of(!opts.readOnly)),
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
      if (!first) return;
      const pos = Math.min(first.char_start, view.state.doc.length);
      view.dispatch({ effects: EditorView.scrollIntoView(pos, { y: "center" }) });
    },
  };
}
