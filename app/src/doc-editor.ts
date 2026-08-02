// 재사용 문서 편집기(D45-E) — Atlas Reader 패널·Records·Intake 가 공유한다.
// 편집 모델: CM6 인라인 Live Preview(소스=정본, 렌더 view-only) + 실패 시 MVP
// textarea 폴백. 무손실 규율: 저장은 편집기 문자열을 어떤 변형도 없이 /doc/put
// 로 보낸다(verified 바이트 동일). 저장 = 디바운스 자동저장 + Ctrl/Cmd+S, 무음
// LWW 금지(원격 해시 비교 후 덮어쓰기/새 버전 선택), 저장 후 개념 후보 원클릭 반영.
import { api, cfg } from "./api";
import { COPY } from "./copy";
import { confirmDestructive } from "./components";

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

/** 마운트 핸들 — 읽기/편집 전환이 **같은 렌더 위에서** 일어난다(D45-E 개정).
 *  구조상 Reader 라는 별도 화면이 없어졌다: 문서는 언제나 렌더된 모습이고,
 *  편집을 켜면 그 자리에 커서가 생길 뿐이다. */
export type DocEditorHandle = {
  dispose: () => void;
  setEditable: (on: boolean) => void;
  isEditable: () => boolean;
};

export type DocEditorOpts = {
  owner: string;
  docId?: string;         // 없으면 신규(첫 저장 시 생성 → 반환 id 로 이후 편집)
  title?: string;
  initial?: string;       // 신규/이미 로드된 텍스트. docId 있고 미지정이면 fetch.
  contentHash?: string;   // 로드 시점 해시(충돌 감지 기준)
  onSaved?: (docId: string, title: string) => void;
  onClose?: () => void;
  /** 읽기로 시작(문서 보기). 편집은 호출부가 setEditable(true) 로 켠다. */
  readOnly?: boolean;
  /** 이 개념이 언급된 위치(백엔드 계산 char 오프셋) — 렌더 위에 밑줄. */
  highlights?: { char_start: number; char_end: number }[];
  /** 제목 줄을 숨긴다(레일처럼 바깥에 이미 제목이 있는 자리). */
  hideTitle?: boolean;
  /** D72 — 내용 시점 초기값. docId 만 주면 로드 시 서버 값으로 채운다. */
  contentTime?: string;
};

/** 편집기를 host 에 마운트. host 내부를 편집 UI 로 채운다(호출부가 컨테이너·닫기
 *  버튼 제공). 반환 = dispose. */
export async function mountDocEditor(host: HTMLElement, opts: DocEditorOpts): Promise<DocEditorHandle> {
  let docId = opts.docId;
  let title = opts.title || "";
  let initial = opts.initial ?? "";
  let hash = opts.contentHash || "";
  let contentTime = opts.contentTime || "";   // D72 — 내용 시점(빈 값 = 안 적음)

  if (docId && opts.initial === undefined) {
    host.innerHTML = `<p class="hint">${COPY.ui.atlasx.readerLoading}</p>`;
    const doc = await api.docGet(opts.owner, docId);
    if (!doc.executed || doc.text === undefined) {
      host.innerHTML = `<p class="hint">${COPY.ui.atlasx.readerFail}</p>`;
      return { dispose: () => {}, setEditable: () => {}, isEditable: () => false };
    }
    initial = doc.text; title = doc.title || title; hash = doc.content_hash || "";
    contentTime = doc.content_time || "";
  }

  // 렌더 타이포는 **마운트 지점 세 곳(#rdr-host·#ed-host·#tl-ed)에 모두** 걸려야
  // 한다. 여태 #rdr-host 에만 스코프돼 있어 같은 문서가 레일에서와 시트에서 다르게
  // 보였다. 여기서 공통 클래스를 붙여 CSS 가 마운트 지점을 몰라도 되게 한다.
  host.classList.add("doc-render");
  host.innerHTML = `
    <div class="doc-ed-bar" ${opts.hideTitle ? "hidden" : ""} style="display:flex;gap:.5rem;align-items:center">
      <input id="ed-title" placeholder="${esc(COPY.ui.atlasx.edTitlePlaceholder)}"
        value="${esc(title)}" style="flex:1;background:transparent;color:inherit;
        border:none;border-bottom:1px solid hsl(var(--border));font:inherit;
        font-weight:700;font-size:1.15em;padding:.2rem 0"/>
      <!-- D72 — 내용 시점. 제목 줄 옆에 두는 이유: 문서를 쓰는 순간이 "이게 언제
           이야기인가"를 가장 잘 아는 때다. 형식은 YYYY[-MM[-DD]] 이고, 어긋나면
           서버가 조용히 비운다(저장은 성공한다 — 축에서만 빠질 뿐이다). -->
      <input id="ed-ctime" placeholder="${esc(COPY.ui.atlasx.edContentTimePh)}"
        value="${esc(contentTime)}" inputmode="numeric" maxlength="10"
        data-tip="${esc(COPY.ui.atlasx.edContentTimeTip)}"
        style="width:7.5rem;background:transparent;color:inherit;
        border:none;border-bottom:1px solid hsl(var(--border));font:inherit;
        font-size:.85em;padding:.2rem 0"/>
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
  const ctimeInput = host.querySelector<HTMLInputElement>("#ed-ctime")!;
  const src = host.querySelector<HTMLTextAreaElement>("#ed-src")!;
  const prev = host.querySelector<HTMLElement>("#ed-prev")!;
  const status = host.querySelector<HTMLElement>("#ed-status")!;
  const conflictBox = host.querySelector<HTMLElement>("#ed-conflict")!;
  const extractBox = host.querySelector<HTMLElement>("#ed-extract")!;

  let lastSaved = initial;
  let lastTitle = title;
  let lastCtime = contentTime;
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
    cmHost.style.cssText = opts.hideTitle
      // 레일 문서 = 상자 없음. 스크롤은 레일이 진다(중첩 스크롤 금지).
      ? "margin-top:.2rem"
      : "border:1px solid hsl(var(--border));border-radius:6px;"
        + "padding:.25rem;margin-top:.4rem;max-height:52vh;overflow:auto";
    src.replaceWith(cmHost);
    prev.remove();
    cm = mod.mountEditor(cmHost, { initial, onChange: onEdited, onSave: () => void save(),
      slash: slashItems, readOnly: opts.readOnly });
    if (opts.highlights?.length) {
      cm.setHighlights(opts.highlights);
      // 첫 언급으로 스크롤 — 레이아웃이 앉은 뒤라야 좌표가 맞는다.
      requestAnimationFrame(() => cm?.scrollToHighlight());
    }
  } catch {
    src.value = initial;
    src.readOnly = !!opts.readOnly;
    prev.innerHTML = renderMarkdown(initial);
    src.addEventListener("input", onEdited);
    src.addEventListener("keydown", (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
        e.preventDefault(); if (timer) window.clearTimeout(timer); void save();
      }
    });
  }

  for (const el of [titleInput, ctimeInput]) {
    el.addEventListener("input", () => {
      setStatus(COPY.ui.atlasx.edDirty);
      if (timer) window.clearTimeout(timer);
      timer = window.setTimeout(() => void save(), 2000);
    });
  }

  async function save(force = false): Promise<void> {
    const text = getText();
    const t = titleInput.value;
    const ct = ctimeInput.value.trim();
    if (saving || (!force && text === lastSaved && t === lastTitle && ct === lastCtime)) return;
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
      const res = await api.docPut(opts.owner, text, docId, t, ct);
      if (!res.executed) throw new Error(res.error || "put failed");
      if (res.doc_id) docId = res.doc_id;              // 신규 → 생성된 id 확보
      lastSaved = text; lastTitle = t; lastCtime = ct; hash = res.content_hash || hash;
      // 서버가 형식 미달을 비웠으면 입력칸도 그 사실을 반영한다 — 화면에는 남아
      // 있는데 저장은 안 된 값이 보이면, 다음에 열었을 때 사라져 있어 놀란다.
      if (ct && res.content_time !== undefined && res.content_time !== ct) {
        ctimeInput.value = res.content_time || "";
        lastCtime = ctimeInput.value;
      }
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
  //
  // ⚠ **응답이 왔다고 색인이 갱신된 것은 아니다.** 여태 fetch 가 resolve 하기만 하면
  // "검색 색인 최신"을 띄웠는데, 서버는 임베딩 스택이 없으면 `executed:false` 로
  // 물러난다(그때 벡터 인덱스는 그대로 낡아 있다). 그 거짓 성공 배지가 2026-07-31
  // 쓰기 큐 사고를 **보이지 않게** 만든 장본인이다: 화면은 세 요청이 다 됐다고
  // 말하는데 실제로는 그 요청이 잡 워커를 죽인 뒤였다. 저장 자체는 성공했으므로
  // 강등 문구는 `저장됨` 이다(문서는 있고 FTS 로 검색된다 — 벡터만 낡았을 뿐이다).
  async function reindex(needsEmbed?: boolean): Promise<void> {
    if (!needsEmbed || !docId) return;
    setStatus(COPY.ui.atlasx.edReindexing);
    try {
      const res = await api.docReembed(opts.owner, docId);
      setStatus(res?.executed ? COPY.ui.atlasx.edReindexed : COPY.ui.atlasx.edSaved);
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

  // 읽기로 시작하면 포커스를 뺏지 않는다(레일이 열리자마자 커서가 깜빡이지 않게).
  if (!opts.readOnly) (cm ? { focus: () => cm!.focus() } : src).focus();
  let editable = !opts.readOnly;
  return {
    dispose: () => { if (timer) window.clearTimeout(timer); if (cm) cm.destroy(); },
    setEditable: (on: boolean) => {
      editable = on;
      const bar = host.querySelector<HTMLElement>(".doc-ed-bar");
      if (bar && !opts.hideTitle) bar.hidden = false;
      if (cm) cm.setEditable(on); else src.readOnly = !on;
      // 편집을 끄면 마지막 상태를 즉시 확정한다(디바운스를 기다리지 않는다).
      if (!on) void save();
    },
    isEditable: () => editable,
  };
}

/** 오버레이 시트로 편집기를 연다(Records/Intake 진입점). 저장은 편집기 내부에서
 *  자동. 닫기는 배경 클릭·닫기 버튼. onSaved 로 호출부가 목록 갱신. */
export async function openDocSheet(opts: {
  owner: string; docId?: string; onSaved?: (docId: string, title: string) => void;
}): Promise<void> {
  const { openSheet, sheetActions, confirmDestructive: confirmD } = await import("./components");
  let handle: DocEditorHandle | null = null;
  const sheet = openSheet({
    title: opts.docId ? COPY.ui.atlasx.readerTitle : COPY.ui.atlasx.edNew,
    bare: true, size: "write",
    onClose: () => handle?.dispose(),
  });
  sheet.body.innerHTML = `<div id="ed-host"></div>`;
  handle = await mountDocEditor(sheet.body.querySelector<HTMLElement>("#ed-host")!, {
    owner: opts.owner, docId: opts.docId, onSaved: opts.onSaved,
  });
  // 발: 삭제(부, 왼쪽) · 완료(주, 오른쪽). 시트 규격 그대로 — 주 액션은 늘 오른쪽 끝.
  sheet.foot.innerHTML = sheetActions(
    { id: "ed-close", label: COPY.ui.atlasx.edDone },
    undefined,
    opts.docId ? { id: "ed-del", label: COPY.ui.atlasx.del } : undefined);
  sheet.foot.querySelector("#ed-close")!.addEventListener("click", sheet.close);
  sheet.foot.querySelector("#ed-del")?.addEventListener("click", async () => {
    const title = sheet.body.querySelector<HTMLInputElement>("#ed-title")?.value
      || COPY.ui.atlasx.edTitlePlaceholder;
    if (!await confirmD(COPY.ui.atlasx.delDocAsk(title),
                        COPY.ui.atlasx.delConfirm, COPY.ui.atlasx.delCancel)) return;
    try {
      const res = await api.docDelete(opts.owner, opts.docId!);
      if (!res.executed) throw new Error(res.error || "delete failed");
      sheet.close();
      opts.onSaved?.(opts.docId!, "");
    } catch {
      const b = sheet.foot.querySelector<HTMLElement>("#ed-del");
      if (b) b.textContent = COPY.ui.atlasx.delFail;
    }
  });
}
