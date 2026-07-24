// 재사용 문서 편집기(D45-E) — Atlas Reader 패널·Records·Intake 가 공유한다.
// 편집 모델: CM6 인라인 Live Preview(소스=정본, 렌더 view-only) + 실패 시 MVP
// textarea 폴백. 무손실 규율: 저장은 편집기 문자열을 어떤 변형도 없이 /doc/put
// 로 보낸다(verified 바이트 동일). 저장 = 디바운스 자동저장 + Ctrl/Cmd+S, 무음
// LWW 금지(원격 해시 비교 후 덮어쓰기/새 버전 선택), 저장 후 개념 후보 원클릭 반영.
import { api, cfg } from "./api";
import { COPY } from "./copy";

/** 결정론 미니 마크다운 렌더러(폴백 미리보기·Reader 공용). **표시 전용** — 소스는
 *  변형하지 않는다. 전부 이스케이프 후 최소 문법만. */
export function renderMarkdown(src: string): string {
  const esc = (s: string) => s.replace(/[&<>"]/g, (c) =>
    c === "&" ? "&amp;" : c === "<" ? "&lt;" : c === ">" ? "&gt;" : "&quot;");
  const inline = (s: string) => s
    .replace(/`([^`]+)`/g, (_m, c) => `<code>${c}</code>`)
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/\*([^*]+)\*/g, "<i>$1</i>")
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, "<u>$1</u>");
  const out: string[] = [];
  const lines = src.split("\n");
  let i = 0, list: "ul" | "ol" | null = null;
  const closeList = () => { if (list) { out.push(`</${list}>`); list = null; } };
  while (i < lines.length) {
    const raw = lines[i]; const t = raw.trim();
    if (t.startsWith("```")) {
      closeList(); const buf: string[] = []; i++;
      while (i < lines.length && !lines[i].trim().startsWith("```")) buf.push(lines[i++]);
      i++;
      out.push(`<pre style="overflow-x:auto"><code>${esc(buf.join("\n"))}</code></pre>`); continue;
    }
    const h = /^(#{1,6})\s+(.*)$/.exec(t);
    if (h) { closeList(); const lv = Math.min(h[1].length + 1, 6);
      out.push(`<h${lv}>${inline(esc(h[2]))}</h${lv}>`); i++; continue; }
    if (/^(-{3,}|\*{3,})$/.test(t)) { closeList(); out.push("<hr/>"); i++; continue; }
    if (t.startsWith("> ")) { closeList();
      out.push(`<blockquote class="hint">${inline(esc(t.slice(2)))}</blockquote>`); i++; continue; }
    const li = /^[-*]\s+(.*)$/.exec(t);
    if (li) { if (list !== "ul") { closeList(); out.push("<ul>"); list = "ul"; }
      out.push(`<li>${inline(esc(li[1]))}</li>`); i++; continue; }
    const oli = /^\d+\.\s+(.*)$/.exec(t);
    if (oli) { if (list !== "ol") { closeList(); out.push("<ol>"); list = "ol"; }
      out.push(`<li>${inline(esc(oli[1]))}</li>`); i++; continue; }
    closeList();
    if (t) out.push(`<p>${inline(esc(raw))}</p>`);
    i++;
  }
  closeList();
  return out.join("");
}

const esc = (s: string) => s.replace(/[&<>"]/g, (c) =>
  c === "&" ? "&amp;" : c === "<" ? "&lt;" : c === ">" ? "&gt;" : "&quot;");

export type DocEditorOpts = {
  owner: string;
  docId?: string;         // 없으면 신규(첫 저장 시 생성 → 반환 id 로 이후 편집)
  title?: string;
  initial?: string;       // 신규/이미 로드된 텍스트. docId 있고 미지정이면 fetch.
  contentHash?: string;   // 로드 시점 해시(충돌 감지 기준)
  onSaved?: (docId: string, title: string) => void;
  onClose?: () => void;
};

/** 편집기를 host 에 마운트. host 내부를 편집 UI 로 채운다(호출부가 컨테이너·닫기
 *  버튼 제공). 반환 = dispose. */
export async function mountDocEditor(host: HTMLElement, opts: DocEditorOpts): Promise<() => void> {
  let docId = opts.docId;
  let title = opts.title || "";
  let initial = opts.initial ?? "";
  let hash = opts.contentHash || "";

  if (docId && opts.initial === undefined) {
    host.innerHTML = `<p class="hint">${COPY.ui.atlasx.readerLoading}</p>`;
    const doc = await api.docGet(opts.owner, docId);
    if (!doc.executed || doc.text === undefined) {
      host.innerHTML = `<p class="hint">${COPY.ui.atlasx.readerFail}</p>`;
      return () => {};
    }
    initial = doc.text; title = doc.title || title; hash = doc.content_hash || "";
  }

  host.innerHTML = `
    <div class="doc-ed-bar" style="display:flex;gap:.5rem;align-items:center">
      <input id="ed-title" placeholder="${esc(COPY.ui.atlasx.edTitlePlaceholder)}"
        value="${esc(title)}" style="flex:1;background:transparent;color:inherit;
        border:none;border-bottom:1px solid hsl(var(--border));font:inherit;
        font-weight:700;font-size:1.15em;padding:.2rem 0"/>
      <span class="hint" id="ed-status">${COPY.ui.atlasx.edSaved}</span>
    </div>
    <textarea id="ed-src" spellcheck="false" style="width:100%;min-height:32vh;resize:vertical;
      background:transparent;color:inherit;border:1px solid hsl(var(--border));
      border-radius:6px;padding:.5rem;margin-top:.4rem;font:inherit;line-height:1.5"></textarea>
    <div id="ed-conflict"></div>
    <div id="ed-extract"></div>
    <div id="ed-prev" style="max-height:34vh;overflow:auto;border-top:1px solid hsl(var(--border));
      margin-top:.4rem;padding-top:.4rem;font-size:.9em;line-height:1.5"></div>`;

  const titleInput = host.querySelector<HTMLInputElement>("#ed-title")!;
  const src = host.querySelector<HTMLTextAreaElement>("#ed-src")!;
  const prev = host.querySelector<HTMLElement>("#ed-prev")!;
  const status = host.querySelector<HTMLElement>("#ed-status")!;
  const conflictBox = host.querySelector<HTMLElement>("#ed-conflict")!;
  const extractBox = host.querySelector<HTMLElement>("#ed-extract")!;

  let lastSaved = initial;
  let lastTitle = title;
  let timer: number | undefined;
  let saving = false;
  let cm: import("./codemirror-editor").CmHandle | null = null;

  const setStatus = (t: string) => { status.textContent = t; };
  const getText = () => (cm ? cm.getValue() : src.value);
  const setText = (t: string) => { if (cm) cm.setValue(t); else { src.value = t; prev.innerHTML = renderMarkdown(t); } };
  const onEdited = () => {
    setStatus(COPY.ui.atlasx.edDirty);
    if (!cm) prev.innerHTML = renderMarkdown(src.value);
    if (timer) window.clearTimeout(timer);
    timer = window.setTimeout(() => void save(), 2000);
  };

  // 슬래시 커맨드 항목 — 결정론 블록(오프라인·무LLM). cursorBack 은 코드펜스처럼
  // 커서를 안쪽에 두어야 할 때 끝에서 되돌릴 칸수.
  const s = COPY.ui.atlasx.slash;
  const slashItems = [
    { label: s.h1, insert: "# " }, { label: s.h2, insert: "## " },
    { label: s.bullet, insert: "- " }, { label: s.number, insert: "1. " },
    { label: s.quote, insert: "> " },
    { label: s.code, insert: "```\n\n```", cursorBack: 4 },
    { label: s.hr, insert: "---\n" },
  ];

  try {
    const mod = await import("./codemirror-editor");
    const cmHost = document.createElement("div");
    cmHost.style.cssText = "border:1px solid hsl(var(--border));border-radius:6px;"
      + "padding:.25rem;margin-top:.4rem;max-height:52vh;overflow:auto";
    src.replaceWith(cmHost);
    prev.remove();
    cm = mod.mountEditor(cmHost, { initial, onChange: onEdited, onSave: () => void save(),
      slash: slashItems });
  } catch {
    src.value = initial;
    prev.innerHTML = renderMarkdown(initial);
    src.addEventListener("input", onEdited);
    src.addEventListener("keydown", (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
        e.preventDefault(); if (timer) window.clearTimeout(timer); void save();
      }
    });
  }

  titleInput.addEventListener("input", () => {
    setStatus(COPY.ui.atlasx.edDirty);
    if (timer) window.clearTimeout(timer);
    timer = window.setTimeout(() => void save(), 2000);
  });

  async function save(force = false): Promise<void> {
    const text = getText();
    const t = titleInput.value;
    if (saving || (!force && text === lastSaved && t === lastTitle)) return;
    saving = true;
    setStatus(COPY.ui.atlasx.edSaving);
    try {
      // 무음 LWW 금지 — 기존 문서면 저장 직전 원격 해시 비교.
      if (!force && docId) {
        const cur = await api.docGet(opts.owner, docId);
        if (cur.executed && cur.content_hash && cur.content_hash !== hash) {
          saving = false; setStatus(COPY.ui.atlasx.edDirty);
          conflictBox.innerHTML = `
            <div class="error-banner" style="margin:.4rem 0">${COPY.ui.atlasx.edConflict}
              <div style="margin-top:.3rem">
                <button class="btn" id="ed-ow">${COPY.ui.atlasx.edOverwrite}</button>
                <button class="btn" id="ed-rl">${COPY.ui.atlasx.edReload}</button>
              </div></div>`;
          conflictBox.querySelector("#ed-ow")!.addEventListener("click", async () => {
            conflictBox.innerHTML = "";
            // 덮어쓰기 전 원격본을 "충돌본" 문서로 먼저 보존(작업 소실 0 — 순수
            // LWW 의 유일한 위험을 없앤다). 신규 doc 이라 id 없이 put.
            if (cur.text !== undefined && cur.text !== "") {
              try {
                await api.docPut(opts.owner, cur.text, undefined,
                                 COPY.ui.atlasx.edConflictCopy(cur.title || t));
              } catch { /* 스냅샷 실패해도 덮어쓰기는 진행 — 최선 노력 보존 */ }
            }
            void save(true);
          });
          conflictBox.querySelector("#ed-rl")!.addEventListener("click", () => {
            conflictBox.innerHTML = "";
            if (cur.text !== undefined) {
              setText(cur.text); lastSaved = cur.text; hash = cur.content_hash || "";
              titleInput.value = cur.title || ""; lastTitle = titleInput.value;
              setStatus(COPY.ui.atlasx.edSaved);
            }
          });
          return;
        }
      }
      const res = await api.docPut(opts.owner, text, docId, t);
      if (!res.executed) throw new Error(res.error || "put failed");
      if (res.doc_id) docId = res.doc_id;              // 신규 → 생성된 id 확보
      lastSaved = text; lastTitle = t; hash = res.content_hash || hash;
      setStatus(COPY.ui.atlasx.edSaved);
      opts.onSaved?.(docId || "", t);
      if (!res.skipped && docId) { void offerExtract(); void reindex(res.needs_embed); }
    } catch {
      setStatus(COPY.ui.atlasx.edSaveFail);
    } finally {
      saving = false;
    }
  }

  // 벡터 낡음(needs_embed) 처리 — 저장은 FTS 즉시, 벡터는 지연. 조용한 배지 +
  // 백그라운드 재임베딩 잡(임베딩 스택 없으면 서버가 no-op — FTS 로 서빙, 무해).
  async function reindex(needsEmbed?: boolean): Promise<void> {
    if (!needsEmbed || !docId) return;
    setStatus(COPY.ui.atlasx.edReindexing);
    try {
      await api.docReembed(opts.owner, docId);
      setStatus(COPY.ui.atlasx.edReindexed);
    } catch { setStatus(COPY.ui.atlasx.edSaved); }
  }

  async function offerExtract(): Promise<void> {
    if (!docId) return;
    try {
      const res = await api.docExtract(opts.owner, docId, false);
      const n = (res.candidates || []).length;
      if (!res.executed || n === 0) { extractBox.innerHTML = ""; return; }
      extractBox.innerHTML =
        `<button class="btn" style="margin-top:.3rem" id="ed-ap">${COPY.ui.atlasx.edExtractOffer(n)}</button>`;
      extractBox.querySelector("#ed-ap")!.addEventListener("click", async () => {
        extractBox.innerHTML = `<p class="hint">${COPY.ui.atlasx.edSaving}</p>`;
        try {
          const ap = await api.docExtract(opts.owner, docId!, true);
          extractBox.innerHTML = `<p class="hint">${COPY.ui.atlasx.edExtractDone(
            ap.applied?.created || 0, ap.mentions || 0)}</p>`;
        } catch { extractBox.innerHTML = ""; }
      });
    } catch { extractBox.innerHTML = ""; }
  }

  (cm ? { focus: () => cm!.focus() } : src).focus();
  return () => { if (timer) window.clearTimeout(timer); if (cm) cm.destroy(); };
}

/** 오버레이 시트로 편집기를 연다(Records/Intake 진입점). 저장은 편집기 내부에서
 *  자동. 닫기는 배경 클릭·닫기 버튼. onSaved 로 호출부가 목록 갱신. */
export async function openDocSheet(opts: {
  owner: string; docId?: string; onSaved?: (docId: string, title: string) => void;
}): Promise<void> {
  let el = document.getElementById("doc-ed-overlay");
  if (!el) {
    el = document.createElement("div");
    el.id = "doc-ed-overlay";
    el.style.cssText = "position:fixed;inset:0;z-index:60;background:hsl(var(--foreground)/.18);"
      + "display:grid;place-items:start center;padding-top:6vh;overflow:auto";
    document.body.appendChild(el);
  }
  el.hidden = false;
  const card = document.createElement("div");
  card.className = "palette-card";
  card.style.cssText = "width:min(52rem,92vw)";
  card.innerHTML = `<div style="display:flex;justify-content:flex-end">
    <button class="btn" id="ed-close">${COPY.ui.atlasx.edDone}</button></div>
    <div id="ed-host"></div>`;
  el.innerHTML = "";
  el.appendChild(card);
  const overlayEl = el;
  const dispose = await mountDocEditor(card.querySelector<HTMLElement>("#ed-host")!, {
    owner: opts.owner, docId: opts.docId, onSaved: opts.onSaved,
  });
  const close = () => { dispose(); overlayEl.hidden = true; };
  card.querySelector("#ed-close")!.addEventListener("click", close);
  overlayEl.onclick = (e) => { if (e.target === overlayEl) close(); };
}
