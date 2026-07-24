// CodeMirror 6 인라인 Live Preview 편집기(D45-E 2단계). Obsidian 모델:
// **EditorState.doc 가 곧 정본**(순수 마크다운) — 렌더는 view-only 데코레이션일
// 뿐이라 저장/복원이 바이트 동일(verified 무손실 구조 보장). 커서가 놓인 줄은
// 원본 소스를 그대로 보이고(활성 줄은 절대 변형 안 함 → 타이핑 중 "튐" 회피),
// 커서가 없는 줄만 마크다운 기호를 숨기고 서식으로 렌더한다.
//
// 의존성은 npm 번들 인라인(CSP 정합, CDN 없음). 스타일은 CSS/데코레이션 클래스로
// 완전 제어(모노크롬 Ink & Paper — 색조 없이 굵기·크기·명도만).
import { EditorState, Compartment, RangeSetBuilder } from "@codemirror/state";
import {
  EditorView, keymap, Decoration, ViewPlugin, WidgetType,
  type DecorationSet, type ViewUpdate,
} from "@codemirror/view";
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { markdown } from "@codemirror/lang-markdown";

export type CmHandle = {
  getValue: () => string;
  setValue: (text: string) => void;
  focus: () => void;
  destroy: () => void;
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

// 한 줄에서 (from,to) 절대오프셋 기준 데코레이션 조각을 모은다. 활성(커서) 줄은
// 호출 자체를 건너뛰므로 여기선 항상 "렌더" 대상이다.
function lineDecos(text: string, base: number,
                   out: { from: number; to: number; deco: Decoration }[]): void {
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
  for (const r of sel.ranges) {
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

const livePreview = ViewPlugin.fromClass(class {
  decorations: DecorationSet;
  constructor(view: EditorView) { this.decorations = buildDecorations(view); }
  update(u: ViewUpdate) {
    if (u.docChanged || u.selectionSet || u.viewportChanged)
      this.decorations = buildDecorations(u.view);
  }
}, { decorations: (v) => v.decorations });

/** CM6 인라인 라이브 프리뷰 편집기를 el 에 마운트. 실패 시 예외를 던져 호출부가
 *  MVP textarea 로 폴백하게 한다(무손실은 어느 쪽이든 동일 — 소스가 정본). */
export function mountEditor(el: HTMLElement, opts: {
  initial: string; onChange: (text: string) => void; onSave: () => void;
}): CmHandle {
  const editable = new Compartment();
  const view = new EditorView({
    parent: el,
    state: EditorState.create({
      doc: opts.initial,
      extensions: [
        history(),
        keymap.of([
          { key: "Mod-s", preventDefault: true, run: () => { opts.onSave(); return true; } },
          ...defaultKeymap, ...historyKeymap,
        ]),
        markdown(),
        livePreview,
        editable.of(EditorView.editable.of(true)),
        EditorView.lineWrapping,
        EditorView.updateListener.of((u) => { if (u.docChanged) opts.onChange(view.state.doc.toString()); }),
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
        }),
      ],
    }),
  });
  return {
    getValue: () => view.state.doc.toString(),
    setValue: (text) => view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: text } }),
    focus: () => view.focus(),
    destroy: () => view.destroy(),
  };
}
