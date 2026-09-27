// 쓰는 중에 노드를 잇고 만드는 두 손잡이(흐름 C · D). 편집기(`codemirror-editor.ts`)가
// 마운트 때 세우고 해제 때 거둔다. 슬래시 메뉴와 같은 규율이다: 카피와 쓰기는 부르는 쪽이
// 주고, 여기는 자리와 키만 안다.
//
//   C — `[[` 뒤에 글자를 치면 기존 노드 목록. 고르면 `[[…` 를 그 노드의 **제목**으로 바꾼다
//       (`[[ ]]` 는 이 앱에서 문서 링크 문법이라 남기지 않는다 — 멘션은 저장 때 링커가
//       제목을 찾아 잇는다). 마지막 줄은 "개념 만들기: <글자>".
//   D — 글을 고르면 그 위에 작은 줄(개념 · 기록 · 결정). 누르면 부르는 쪽이 컴포저를 연다.
import type { EditorView } from "@codemirror/view";

export type MentionHit = { id: string; title: string; domain: string };
export type MentionOpts = {
  /** 찾은 노드와, 의미 팔이 빠진 검색이면 그 사실(`note`). */
  search: (q: string) => Promise<{ hits: MentionHit[]; note: string }>;
  /** 새 개념을 만들고 본문에 넣을 제목을 돌려준다. null 이면 넣지 않는다. */
  create: (q: string) => Promise<string | null>;
  createLabel: (q: string) => string;
  noneLabel: string;
};
export type SelectionOpts = {
  label: string;
  actions: { key: string; label: string }[];
  run: (key: string, text: string) => void;
};

const escHtml = (s: string) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  c === "&" ? "&amp;" : c === "<" ? "&lt;" : c === ">" ? "&gt;" : "&quot;");

/** 줄 머리부터 커서까지에서 `[[질의` 를 찾는다. 닫는 `]]` 가 이미 있으면 링크를 쓰는 중이 아니다. */
export const MENTION_RE = /\[\[([^[\]\n]{0,60})$/;

export class MentionMenu {
  dom: HTMLElement;
  open = false;
  private from = 0;
  private q = "";
  private hits: MentionHit[] = [];
  private note = "";
  private sel = 0;
  private seq = 0;
  private timer = 0;
  constructor(private view: EditorView, private o: MentionOpts) {
    this.dom = document.createElement("div");
    this.dom.className = "cm-assist-menu";
    this.dom.setAttribute("role", "listbox");
    this.dom.hidden = true;
    document.body.appendChild(this.dom);
  }
  /** 커서 앞의 `[[질의` 가 바뀌었다. */
  update(from: number, q: string) {
    this.from = from;
    if (q === this.q && this.open) return;
    this.q = q;
    window.clearTimeout(this.timer);
    if (!q.trim()) { this.hits = []; this.show(); return; }
    this.timer = window.setTimeout(() => void this.search(q), 180);
    this.show();
  }
  private async search(q: string) {
    const mine = ++this.seq;
    const got = await this.o.search(q).catch(() => ({ hits: [], note: "" }));
    if (mine !== this.seq || !this.open) return;
    this.hits = got.hits;
    this.note = got.note;
    this.sel = 0;
    this.render();
  }
  /** 행 = 찾은 노드들 + "개념 만들기". 질의가 비었으면 만들 것도 없다. */
  private rows(): { label: string; sub: string; hit?: MentionHit }[] {
    const out: { label: string; sub: string; hit?: MentionHit }[] =
      this.hits.map((h) => ({ label: h.title, sub: h.domain, hit: h }));
    if (this.q.trim()) out.push({ label: this.o.createLabel(this.q.trim()), sub: "" });
    return out;
  }
  private show() {
    const c = this.view.coordsAtPos(this.from);
    if (!c) { this.hide(); return; }
    this.open = true;
    this.dom.style.left = `${Math.round(c.left)}px`;
    this.dom.style.top = `${Math.round(c.bottom + 4)}px`;
    this.render();
    this.dom.hidden = false;
  }
  private render() {
    const rows = this.rows();
    this.sel = Math.min(this.sel, Math.max(rows.length - 1, 0));
    const empty = (this.note ? `<p class="cm-assist-note">${escHtml(this.note)}</p>` : "")
      + (this.q.trim() && !this.hits.length
        ? `<p class="cm-assist-note">${escHtml(this.o.noneLabel)}</p>` : "");
    this.dom.innerHTML = empty + rows.map((r, i) =>
      `<div class="cm-assist-row${i === this.sel ? " sel" : ""}${r.hit ? "" : " cm-assist-create"}"
        role="option" aria-selected="${i === this.sel}" data-i="${i}">
        <span class="cm-assist-name">${escHtml(r.label)}</span>${
        r.sub ? `<span class="cm-assist-sub">${escHtml(r.sub)}</span>` : ""}</div>`).join("");
    this.dom.querySelectorAll<HTMLElement>(".cm-assist-row").forEach((el) =>
      el.addEventListener("mousedown", (e) => { e.preventDefault(); void this.choose(Number(el.dataset.i)); }));
  }
  move(d: number) {
    const n = this.rows().length;
    if (!this.open || !n) return;
    this.sel = (this.sel + d + n) % n;
    this.render();
  }
  hide() { this.open = false; this.q = ""; this.hits = []; this.note = ""; this.dom.hidden = true; }
  /** 고른 것을 넣는다. 만들기는 왕복이 있으므로, 돌아왔을 때 그 자리가 아직 `[[질의`
   *  그대로일 때만 바꾼다 — 그 사이 사람이 다른 곳을 고쳤으면 엉뚱한 글자를 덮지 않는다. */
  async choose(i = this.sel) {
    const row = this.rows()[i];
    if (!this.open || !row) return;
    const typed = `[[${this.q}`;
    const from = this.from;
    this.hide();
    const title = row.hit ? row.hit.title : await this.o.create(typed.slice(2).trim());
    if (!title) { this.view.focus(); return; }
    const doc = this.view.state.doc;
    if (doc.sliceString(from, from + typed.length) !== typed) return;
    this.view.dispatch({
      changes: { from, to: from + typed.length, insert: title },
      selection: { anchor: from + title.length },
    });
    this.view.focus();
  }
  destroy() { window.clearTimeout(this.timer); this.dom.remove(); }
}

export class SelectionBar {
  dom: HTMLElement;
  private text = "";
  constructor(private o: SelectionOpts) {
    this.dom = document.createElement("div");
    this.dom.className = "cm-selbar";
    this.dom.setAttribute("role", "toolbar");
    this.dom.setAttribute("aria-label", o.label);
    this.dom.hidden = true;
    this.dom.innerHTML = o.actions.map((a) =>
      `<button type="button" data-k="${escHtml(a.key)}">${escHtml(a.label)}</button>`).join("");
    // 누르는 동안 편집기의 선택이 풀리지 않게 한다(mousedown 에서 초점을 뺏지 않는다).
    this.dom.addEventListener("mousedown", (e) => e.preventDefault());
    this.dom.querySelectorAll<HTMLElement>("button").forEach((b) =>
      b.addEventListener("click", () => {
        const t = this.text;
        this.hide();
        this.o.run(b.dataset.k || "", t);
      }));
    document.body.appendChild(this.dom);
  }
  /** 선택 영역의 윗변 가운데 위에 선다. 화면 위로 넘치면 아래로 내린다. */
  showAt(text: string, rect: { left: number; right: number; top: number; bottom: number }) {
    this.text = text.trim();
    if (!this.text) { this.hide(); return; }
    this.dom.hidden = false;
    const w = this.dom.offsetWidth, h = this.dom.offsetHeight;
    const x = Math.max(8, Math.min((rect.left + rect.right) / 2 - w / 2, window.innerWidth - w - 8));
    const y = rect.top - h - 6 > 8 ? rect.top - h - 6 : rect.bottom + 6;
    this.dom.style.left = `${Math.round(x)}px`;
    this.dom.style.top = `${Math.round(y)}px`;
  }
  hide() { this.dom.hidden = true; this.text = ""; }
  destroy() { this.dom.remove(); }
}
