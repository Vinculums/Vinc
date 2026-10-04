// 재사용 문서 편집기(D45-E) — View Reader 패널·Records·Intake 가 공유한다.
// 편집 모델: CM6 인라인 Live Preview(소스=정본, 렌더 view-only) + 실패 시 MVP
// textarea 폴백. 무손실 규율: 저장은 편집기 문자열을 어떤 변형도 없이 /doc/put
// 로 보낸다(verified 바이트 동일). 저장 = 디바운스 자동저장 + Ctrl/Cmd+S, 무음
// LWW 금지(원격 해시 비교 후 덮어쓰기/새 버전 선택), 저장 후 개념 후보 원클릭 반영.
import { api, cfg, docMidWrite, storeDownReason, shellInvoke } from "./api";
import { COPY } from "./copy";
import { confirmDestructive } from "./components";
import { hydrateRich } from "./rich-blocks";

/** 결정론 미니 마크다운 렌더러(폴백 미리보기·Reader 공용). **표시 전용** — 소스는
 *  변형하지 않는다. 전부 이스케이프 후 최소 문법만. */
export function renderMarkdown(src: string): string {
  const esc = (s: string) => s.replace(/[&<>"]/g, (c) =>
    c === "&" ? "&amp;" : c === "<" ? "&lt;" : c === ">" ? "&gt;" : "&quot;");
  const inline = (s: string) => s
    .replace(/`([^`]+)`/g, (_m, c) => `<code>${c}</code>`)
    // `[[문서]]` 가 먼저다 — 아래 링크 규칙이 `[..](..)` 를 먹기 전에 잡아야 대괄호
    // 두 겹이 한 겹으로 오해되지 않는다. CM 라이브 프리뷰는 이것을 doclink 로 그리는데
    // 여기만 원문 `[[doc:x]]` 를 그대로 내고 있었다(실측 2026-09-10) — 렌더러가 둘
    // 이라는 사실이 화면에 드러나면 안 된다는 이 함수의 규율이 깨져 있던 자리다.
    .replace(/\[\[([^\]\n]+)\]\]/g, (_m, c) => `<u class="md-doclink">${c}</u>`)
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/\*([^*]+)\*/g, "<i>$1</i>")
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, "<u>$1</u>");
  /** 파이프 표. **CM 쪽에는 이미 있고 여기만 없었다** — 같은 문서가 문서 열에서는
   *  표로, 발췌·폴백에서는 `| a | b |` 원문으로 보였다. 구분줄(`---`)이 있는 것만
   *  표로 본다: 그것이 마크다운의 계약이고, 없으면 그냥 파이프 문자가 든 문단이다. */
  const isDelim = (t: string) => /^\|?[\s:|-]+\|?$/.test(t) && t.includes("-");
  const cells = (t: string) => t.replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
  const out: string[] = [];
  const lines = src.split("\n");
  let i = 0, list: "ul" | "ol" | null = null;
  const closeList = () => { if (list) { out.push(`</${list}>`); list = null; } };
  while (i < lines.length) {
    const raw = lines[i]; const t = raw.trim();
    if (t.startsWith("```")) {
      closeList();
      const lang = t.slice(3).trim().toLowerCase();
      const buf: string[] = []; i++;
      while (i < lines.length && !lines[i].trim().startsWith("```")) buf.push(lines[i++]);
      i++;
      const body = buf.join("\n");
      // 다이어그램은 **자리만 만든다** — 그리는 손은 rich-blocks 의 hydrateRich 다.
      // 여기서 못 그리는 이유는 이 함수가 동기라서고, 동기로 두는 이유는 호출부가
      // 전부 `innerHTML =` 한 줄이기 때문이다(발췌·폴백·미리보기).
      if (lang === "mermaid") {
        out.push(`<div class="md-mermaid" data-rich="mermaid" data-src="${esc(body)}"></div>`);
        continue;
      }
      out.push(`<pre style="overflow-x:auto"><code>${esc(body)}</code></pre>`); continue;
    }
    // 블록 수식 `$$...$$`. 한 줄짜리와 여러 줄짜리를 함께 받는다.
    if (t.startsWith("$$")) {
      closeList();
      const one = /^[$][$](.+)[$][$]$/.exec(t);
      let tex: string;
      if (one) { tex = one[1]; i++; }
      else {
        const buf: string[] = []; i++;
        while (i < lines.length && !lines[i].trim().endsWith("$$")) buf.push(lines[i++]);
        if (i < lines.length) { buf.push(lines[i].trim().replace(/[$][$]$/, "")); i++; }
        tex = (t.slice(2) + "\n" + buf.join("\n")).trim();
      }
      out.push(`<div class="md-math" data-rich="math" data-src="${esc(tex)}"></div>`);
      continue;
    }
    // 표 — 머리줄 + 구분줄이 나란히 와야 성립한다.
    if (t.includes("|") && i + 1 < lines.length && isDelim(lines[i + 1].trim())) {
      closeList();
      const head = cells(t);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trim().includes("|")) rows.push(cells(lines[i++].trim()));
      out.push(`<table class="md-tbl"><thead><tr>${
        head.map((c) => `<th>${inline(esc(c))}</th>`).join("")}</tr></thead><tbody>${
        rows.map((r) => `<tr>${r.map((c) => `<td>${inline(esc(c))}</td>`).join("")}</tr>`).join("")
      }</tbody></table>`);
      continue;
    }
    const h = /^(#{1,6})\s+(.*)$/.exec(t);
    // 깊이는 소스 그대로다. 여태 한 단계 내려 찍어(`## X` → `<h3>`) 같은 문서가
    // 편집기(`cm-r-h2`)와 폴백에서 다른 크기로 보였다 — 렌더러가 둘이라는 사실이
    // 화면에 드러나면 안 된다.
    if (h) { closeList(); const lv = Math.min(h[1].length, 6);
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
  /** **나가면서 확정한다** — 대기 중이던 디바운스를 버리지 않고 저장한 뒤 해제한다.
   *  상시 편집 + 자동저장인 제품에서 "버린다"는 마지막 2초를 소리 없이 잃는 것이고,
   *  실제로 레일의 출구 넷(뒤로 · 전체폭 · ✕ · 표면 이동)이 결과 셋을 냈다
   *  (2026-09-07 실측). 반환 Promise 는 **같은 문서를 곧바로 다시 읽는 자리**만
   *  기다리면 된다(전체폭 승격) — 그 밖에는 무시해도 안전하다.
   *  두 번 불러도 안전하다(타이머를 비우고 cm 을 놓는다). */
  dispose: (discard?: boolean) => Promise<void>;
  /** 삭제 전에 늦은 자동저장이 삭제 결과를 뒤집지 않게 막고, 돌던 저장을 기다린다. */
  pauseSaving: (pause: boolean) => Promise<void>;
  /** 목차 클릭·청크 히트 착지가 쓰는 스크롤. 폴백(textarea)에는 오프셋 개념이
   *  없어 무동작이다 — 그 경로는 렌더 자체가 강등본이라 착지도 함께 강등한다. */
  scrollToOffset: (pos: number) => void;
  /** 지금 편집기가 들고 있는 원문. **복사 버튼이 쓴다**(발주자 지적 2026-09-10:
   *  "그냥 내용 드래그해서 복사하려는 사용자도 있을 것"). 화면에서 긁으면 렌더된
   *  모습이 딸려 오지만 문서의 정본은 마크다운이므로, 한 번 눌러 얻는 것은
   *  **소스 그대로**여야 붙여넣은 곳에서 같은 문서가 된다. */
  getText: () => string;
  /** 이 문서의 주제를 적는다(보통 저장과 같은 길 · 같은 충돌 확인). 아직 안 만든 문서면 false. */
  saveTopic?: (topic: string) => Promise<boolean>;
  /** 사람이 저장을 뜻한 것과 같다(Ctrl/Cmd+S). 새 문서의 주제를 고른 직후에 쓴다. */
  saveNow?: () => Promise<boolean>;
};

/** 살아 있는 편집기 — **teardown 이 핸들에 닿는 유일한 통로.**
 *  마운트 지점을 지우는 곳(라우터 · 레일 열기·닫기 · Tools 탭 전환)은 핸들을 들고
 *  있지 않다. 그래서 여태 그것들은 화면만 걷어 갔고 디바운스는 그대로 살아 남아
 *  화면 없는 곳에서 저장이 터졌다. 호스트로 찾을 수 있게 여기 등록해 둔다. */
const LIVE = new Map<HTMLElement, DocEditorHandle>();

/** `root`(자신 포함) 안에 마운트된 편집기를 전부 확정·해제한다.
 *  **마운트 지점을 비우기 전에** 부를 것 — dispose 가 대기 중 편집을 먼저 저장한다.
 *  기다리지 않는다: 저장할 글자는 dispose 가 동기적으로 이미 집어 갔다. */
export function disposeEditorsIn(root: HTMLElement): Promise<void> {
  const done: Promise<void>[] = [];
  for (const [host, h] of [...LIVE]) {
    if (root === host || root.contains(host)) done.push(h.dispose());
  }
  // 대부분의 호출부는 기다리지 않는다(위 주석). 새 문서 열기만 기다린다 — 닫히는 새 문서가
  // 초안을 넘긴 **뒤에** 다음 새 문서가 그 초안을 읽어야 한다(authoring.mountDocWriter).
  return Promise.all(done).then(() => undefined);
}

/** 문서 열이 떠 있고 그 안에 편집기가 있는가 — 표면 단축키가 쉬어야 하는 조건.
 *
 *  판정은 `keys.blocksSurfaceShortcuts` 가 하고 DOM 은 여기서만 읽는다: keys.ts 가
 *  DOM 을 안 쓰는 것이 `app/dev/keys-check.ts` 를 성립시키는 조건이다.
 *
 *  ⚠ 읽기 전용 편집기도 `contenteditable="false"` 인 `.cm-content` 다. 속성 **값**으로
 *  거르면 그 경우가 빠지고, 문서를 읽는 중에 숫자가 표면을 갈아엎는다(F-DOC-KB-01). */
export function docEditorOpen(): boolean {
  const pane = document.getElementById("docpane");
  return !!pane && !pane.hidden && !!pane.querySelector(".cm-content");
}

export type DocEditorOpts = {
  owner: string;
  docId?: string;         // 없으면 신규(첫 저장 시 생성 → 반환 id 로 이후 편집)
  title?: string;
  initial?: string;       // 신규/이미 로드된 텍스트. docId 있고 미지정이면 fetch.
  contentHash?: string;   // 로드 시점 해시(충돌 감지 기준)
  onSaved?: (docId: string, title: string) => void;
  onClose?: () => void;
  /** 읽기 전용으로 마운트(커서를 켜지 않는다). 마운트 뒤 전환하는 문은 없다 —
   *  문서 열은 상시 편집이라 쓸 곳이 없었고, 있던 토글은 사문이었다(2026-09-07 제거). */
  readOnly?: boolean;
  /** 이 개념이 언급된 위치(백엔드 계산 char 오프셋) — 렌더 위에 밑줄. */
  highlights?: { char_start: number; char_end: number }[];
  /** 제목 줄을 숨긴다(레일처럼 바깥에 이미 제목이 있는 자리). */
  hideTitle?: boolean;
  /** 마운트 직후 커서를 켜지 **않는다**(기본은 켠다). 문서 열처럼 지도와 나란히
   *  선 자리에서는 열자마자 키보드를 빼앗으면 지도 단축키가 먹지 않는다. */
  autoFocus?: boolean;
  /** 저장 상태를 바깥으로 내보낸다. `hideTitle` 이면 편집기의 상태 글자(`#ed-status`)
   *  가 제목 줄과 함께 숨으므로, 그 자리에서는 이 통로가 유일한 저장 신호다 —
   *  상시 편집에서 "저장됐나"를 알 수 없으면 사용자는 매번 Ctrl+S 를 누른다. */
  onStatus?: (text: string) => void;
  /** D72 — 내용 시점 초기값. docId 만 주면 로드 시 서버 값으로 채운다. */
  contentTime?: string;
  /** 본문의 `[[doc_id]]` 를 눌렀다. 주면 그 링크가 살아나고, 안 주면 종전대로
   *  밑줄만 그어진 글자로 남는다(시트·Tools 처럼 갈 곳이 없는 자리). */
  onDocLink?: (docId: string) => void;
  /** 새 문서(흐름 B) — 주제. 주면 **첫 저장**이 제목과 함께 보내 미러 노드가 생긴다.
   *  비어 있으면 첫 저장을 미루고 이유를 말한다(주제 없는 문서는 트리에 자리가 없다). */
  topic?: () => string;
  /** 새 문서 — 첫 저장 직전에 제목으로 id 를 선언한다. `skip` 은 쓰기에서 거절된 후보. */
  declareId?: (title: string, skip: string[]) => Promise<string>;
  /** 첫 저장으로 문서가 생겼다(선언한 id · 보낸 주제). */
  onCreated?: (docId: string, topic: string) => void;
  /** 새 문서가 **만들어지기 전에** 닫혔다(제목·주제가 없어 저장이 미뤄진 채). 쓴 것을 넘긴다. */
  onUnsaved?: (draft: { title: string; text: string; contentTime: string }) => void;
  /** 만들기 폼은 완료를 눌렀을 때만 저장한다. 닫기·취소는 제출이 아니다. */
  manualSave?: boolean;
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
    host.innerHTML = `<p class="hint">${COPY.ui.viewx.readerLoading}</p>`;
    // 이 await 는 오래 try 밖에 있었다. `call()` 은 비 2xx 를 전부 던지므로
    // (죽은 사이드카·500·그리고 2026-08-16 부터 스토어 미개방의 503) 예외가
    // mountDocEditor 밖으로 새어 나가고, 호출부는 "문서를 여는 중"에서 굳는다.
    // 여기서 받아 아래 실패 상자로 합류시킨다.
    let doc: any = null;
    let storeDown: string | null = null;
    try {
      doc = await api.docGet(opts.owner, docId);
    } catch (e) {
      storeDown = storeDownReason(e);      // 스토어 미개방이 아니면 null
    }
    if (!doc?.executed || doc.text === undefined) {
      // 스토어를 못 읽는 것은 **문서 하나가 상한 것과 다른 사건**이다. 기본 문구는
      // "잠시 후 다시 시도하세요"인데, 다른 Vinc 가 스토어를 물고 있는 동안에는
      // 몇 번을 다시 눌러도 같은 결과라 그 조언이 틀린 길로 보낸다.
      // 그리고 쓰기 창(P17)은 셋째 사건이다: 문서는 멀쩡히 있고 지금 이 순간만 반쪽이다.
      // ⚠ **이 이른 반환은 이 패키지가 만든 것이 아니다.** `!doc?.executed ||
      // doc.text === undefined` 로 무동작 stub 을 돌려주는 갈래는 P17b 이전부터 여기
      // 있었고, 편집기를 세우지 않는 보호는 거기서 온다. P17(#241)이 `doc_get` 을 이
      // 상태에서 `executed:false` 로 돌린 순간 쓰기 창은 이미 이 가드에 걸렸다.
      // P17b 가 더한 것은 **문구 갈래 + 사람이 누르는 재시도** 둘뿐이다.
      // (그 보호를 이 패키지의 공으로 적지 말 것 — 어디서 오는지 잘못 알게 된다.)
      // 그 보호가 무엇을 막는지는 그대로다: 편집기를 세우면 상시 편집 + 2초 디바운스라
      // 키 하나에 그 반쪽이 정본이 되고, 그 시점에 이전 본을 들고 있는 것이 없다.
      const busy = !!doc && docMidWrite(doc);
      host.innerHTML = `<p class="hint">${busy ? COPY.ui.viewx.readerMidWrite
        : storeDown === null ? COPY.ui.viewx.readerFail : COPY.view.storeDown}</p>`
        + (busy ? `<button class="btn" id="ed-retry">${esc(COPY.ui.viewx.readerRetry)}</button>` : "");
      // 자동 재시도는 없다 — 크래시 잔해면 영원히 돈다. 사람이 누를 때만 다시 읽는다.
      // 다시 성공하면 그때 만든 편집기를 이 stub 이 대신 들고 있어야 한다: 호출부는
      // 이미 stub 을 잡았으므로, 위임하지 않으면 시트를 닫아도 디바운스 타이머와
      // CM6 인스턴스가 남는다.
      let again: DocEditorHandle | null = null;
      // 왕복 **중에** 시트를 닫으면 `again` 이 아직 null 이라 dispose 가 위임할 데가
      // 없다. 그대로 두면 왕복이 끝난 뒤 이미 떨어져 나간 host 에 편집기가 서고,
      // 디바운스 타이머와 CM6 인스턴스가 아무도 안 잡은 채 남는다. 그래서 닫힌 사실을
      // 기억했다가 **도착하는 핸들을 즉시 버린다**(취소가 아니라 폐기 — 왕복은 fetch
      // 하나라 끊어 봐야 얻는 것이 없고, 폐기가 경합 창이 없다).
      let closed = false;
      host.querySelector("#ed-retry")?.addEventListener("click", () => {
        void mountDocEditor(host, opts).then((h) => { closed ? h.dispose() : (again = h); });
      });
      // stub 도 등록한다 — 호출부가 이것을 잡고 있어도, 마운트 지점을 지우는 쪽은
      // 핸들을 모르고 호스트만 안다. 재시도가 성공하면 그때 등록한 진짜 편집기가
      // 같은 호스트 자리를 덮고, 이 stub 의 dispose 는 거기로 흘러간다.
      // `closed` 를 세우는 것이 P17b 의 몫이다 — 왕복 중에 닫히면 도착하는 핸들을
      // 즉시 버려야 이미 떨어져 나간 host 에 편집기가 서지 않는다.
      const stub: DocEditorHandle = {
        dispose: async (discard) => { closed = true; LIVE.delete(host); await again?.dispose(discard); },
        pauseSaving: async (pause) => { await again?.pauseSaving(pause); },
        scrollToOffset: (pos) => again?.scrollToOffset(pos),
        // 아직 못 읽은 문서다 — 빈 문자열이 곧 "복사할 것이 없다"이고,
        // 호출부(복사 버튼)는 그 경우 아무 일도 하지 않는다.
        getText: () => again?.getText() ?? "",
      };
      LIVE.set(host, stub);
      return stub;
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
      <input id="ed-title" placeholder="${esc(COPY.ui.viewx.edTitlePlaceholder)}"
        value="${esc(title)}" style="flex:1;background:transparent;color:inherit;
        border:none;border-bottom:1px solid hsl(var(--border));font:inherit;
        font-weight:700;font-size:1.15em;padding:.2rem 0"/>
      <!-- D72 — 내용 시점. 제목 줄 옆에 두는 이유: 문서를 쓰는 순간이 "이게 언제
           이야기인가"를 가장 잘 아는 때다. 형식은 YYYY[-MM[-DD]] 이고, 어긋나면
           서버가 조용히 비운다(저장은 성공한다 — 축에서만 빠질 뿐이다). -->
      <input id="ed-ctime" placeholder="${esc(COPY.ui.viewx.edContentTimePh)}"
        value="${esc(contentTime)}" inputmode="numeric" maxlength="10"
        data-tip="${esc(COPY.ui.viewx.edContentTimeTip)}"
        style="width:7.5rem;background:transparent;color:inherit;
        border:none;border-bottom:1px solid hsl(var(--border));font:inherit;
        font-size:.85em;padding:.2rem 0"/>
      <span class="hint" id="ed-status">${COPY.ui.viewx.edSaved}</span>
    </div>
    <textarea id="ed-src" spellcheck="false" style="width:100%;min-height:12rem;resize:vertical;
      background:transparent;color:inherit;border:1px solid hsl(var(--border));
      border-radius:6px;padding:.5rem;margin-top:.4rem;font:inherit;line-height:1.5"></textarea>
    <p id="ed-validation" class="hint" role="alert" hidden></p>
    <div id="ed-conflict"></div>
    <div id="ed-extract"></div>
    <div id="ed-prev" style="border-top:1px solid hsl(var(--border));
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
  /** 돌고 있는 저장. 저장은 **한 번에 하나**이고, 도는 중에 온 저장은 그 뒤에 줄을 선다 —
   *  여태 `saving` 이면 그냥 돌아가서, 첫 저장 중에 친 글자·주제 적기가 조용히 빠졌다. */
  let inflight: Promise<boolean> | null = null;
  /** 닫히는 중이다 — 새 문서의 "제목을 치는 중에는 안 만든다" 가드를 풀고 확정한다. */
  let leaving = false;
  let savingPaused = false;
  let cm: import("./codemirror-editor").CmHandle | null = null;

  const setStatus = (t: string) => { status.textContent = t; opts.onStatus?.(t); };
  const getText = () => (cm ? cm.getValue() : src.value);
  const setText = (t: string) => { if (cm) cm.setValue(t); else { src.value = t; prev.innerHTML = renderMarkdown(t); } };
  /** 화면이 마지막 저장본과 다른가(본문 · 제목 · 시점). */
  const dirty = () => getText() !== lastSaved || titleInput.value !== lastTitle
    || ctimeInput.value.trim() !== lastCtime;
  /** 2초 뒤 저장(디바운스). 터지면 타이머를 비운다 — dispose 가 "대기 중인가"를 이것으로 본다. */
  const schedule = () => {
    if (opts.manualSave) return;
    if (savingPaused) return;
    if (timer) window.clearTimeout(timer);
    timer = window.setTimeout(() => { timer = undefined; void save(); }, 2000);
  };
  const onEdited = () => {
    setStatus(COPY.ui.viewx.edDirty);
    if (!cm) { prev.innerHTML = renderMarkdown(src.value); hydrateRich(prev); }
    schedule();
  };

  // 슬래시 커맨드 항목 — 결정론 블록(오프라인·무LLM). cursorBack 은 코드펜스처럼
  // 커서를 안쪽에 두어야 할 때 끝에서 되돌릴 칸수.
  const s = COPY.ui.viewx.slash;
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
      // 시트 문서도 같은 규율 — vh 상한을 걸면 문서가 패널이 아니라 창 기준으로
      // 잘리고(52vh 사고), 스크롤이 .sheet-body 와 이중이 된다. 시트 높이는
      // app.css `.sheet[data-size="write"]` 가 진다.
      : "border:1px solid hsl(var(--border));border-radius:6px;"
        + "padding:.25rem;margin-top:.4rem";
    src.replaceWith(cmHost);
    prev.remove();
    const A = COPY.author;
    cm = mod.mountEditor(cmHost, { initial, onChange: onEdited, onSave: () => void save(false, true),
      slash: slashItems, readOnly: opts.readOnly, fmLabel: COPY.ui.viewx.edFmLabel,
      onDocLink: opts.onDocLink,
      // 흐름 C — `[[` 로 기존 노드를 찾아 제목을 넣는다(멘션은 저장 때 링커가 잇는다).
      mention: {
        search: async (q) => (await import("./authoring")).findNodes(q),
        create: (q) => createMentionConcept(q),
        createLabel: A.mentionCreate, noneLabel: A.mentionNone,
      },
      // 흐름 D — 고른 글로 노드를 만든다(컴포저가 그 글을 들고 열린다).
      selection: {
        label: A.selLabel,
        actions: (["concept", "record", "decision"] as const)
          .map((k) => ({ key: k, label: A.kinds[k] })),
        run: (kind, text) => void composeFromText(kind, text),
      },
      // 바깥 링크는 셸이면 OS 브라우저로, 웹이면 새 탭으로 — 계정 연결이 이미
      // 쓰는 길과 같다(shell:allow-open 은 그 자리에서 이미 열려 있다).
      onUrl: (url) => void (async () => {
        const invoke = await shellInvoke();
        if (invoke) await invoke("plugin:shell|open", { path: url });
        else window.open(url, "_blank", "noopener");
      })() });
    if (opts.highlights?.length) {
      cm.setHighlights(opts.highlights);
      // 첫 언급으로 스크롤 — 레이아웃이 앉은 뒤라야 좌표가 맞는다.
      requestAnimationFrame(() => cm?.scrollToHighlight());
    }
  } catch {
    src.value = initial;
    src.readOnly = !!opts.readOnly;
    prev.innerHTML = renderMarkdown(initial); hydrateRich(prev);
    src.addEventListener("input", onEdited);
    src.addEventListener("keydown", (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
        e.preventDefault(); if (timer) window.clearTimeout(timer); void save(false, true);
      }
    });
  }

  for (const el of [titleInput, ctimeInput]) {
    el.addEventListener("input", () => {
      setStatus(COPY.ui.viewx.edDirty);
      schedule();
    });
  }
  // 새 문서(흐름 B): 제목에서 Enter = 본문으로 내려가며 만든다. 제목을 떠나는 것도 같다 —
  // 제목을 치는 **중**에는 만들지 않으므로(아래 save), 다 쳤다는 신호가 이 둘이다.
  if (opts.declareId) {
    titleInput.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" || e.isComposing) return;
      e.preventDefault();
      (cm ? cm : src).focus();
    });
    titleInput.addEventListener("blur", () => {
      if (!docId && titleInput.value.trim()) void save(false, true);
    });
  }

  /** `explicit` = 사람이 저장을 뜻했다(Ctrl/Cmd+S · 제목에서 Enter · 제목을 떠남).
   *  새 문서의 첫 저장은 이것(또는 닫힘)만 만든다 — 디바운스는 제목을 치는 중에 터질 수
   *  있고, 그러면 반쯤 친 제목이 문서 id 로 굳는다.
   *  `topicOnce` = 이번 저장에 주제도 적는다(본문 · 제목과 **같은 길**로, 같은 충돌 확인을 거쳐).
   *  돌려주는 값 = 이번 쓰기가 들어갔는가. */
  function save(force = false, explicit = false, topicOnce = ""): Promise<boolean> {
    if (savingPaused) return Promise.resolve(false);
    if (inflight) return inflight.then(() => save(force, explicit, topicOnce));
    const run = doSave(force, explicit, topicOnce).finally(() => { inflight = null; });
    inflight = run;
    return run;
  }

  async function doSave(force: boolean, explicit: boolean, topicOnce: string): Promise<boolean> {
    const text = getText();
    const t = docId && !opts.manualSave ? titleInput.value : titleInput.value.trim();
    const ct = ctimeInput.value.trim();
    // 새 문서 — 제목과 주제가 있어야 만든다. 없으면 **왜 안 저장됐는지** 말한다.
    const creating = !docId && !!opts.declareId;
    const validation = host.querySelector<HTMLElement>("#ed-validation")!;
    const needTitle = (!docId || opts.manualSave) && !t.trim();
    const needBody = (!docId || opts.manualSave) && !text.trim();
    titleInput.setAttribute("aria-invalid", String(needTitle));
    (host.querySelector<HTMLElement>(".cm-content") || src)
      .setAttribute("aria-invalid", String(needBody));
    validation.hidden = !needTitle && !needBody;
    validation.textContent = [needTitle ? COPY.ui.tools.needTitle : "",
      needBody ? COPY.ui.tools.needBody : ""].filter(Boolean).join(" ");
    if (needTitle || needBody) return false;
    if (!docId || opts.manualSave) titleInput.value = t;
    if (!creating && !force && !topicOnce && !dirty()) return false;
    const topic = topicOnce || opts.topic?.() || "";
    if (creating) {
      if (!t.trim()) { setStatus(COPY.author.docNeedTitle); return false; }
      if (opts.topic && !topic) { setStatus(COPY.author.docNeedTopic); return false; }
      // 본문이 비면 서버가 받지 않는다(`/doc/put` 의 text 는 필수다). 제목에서 Enter 로 본문에
      // 내려온 직후가 이 상태다 — 실패라고 말하지 않고 첫 줄을 쓰라고 말한다. 초안은 닫혀도 남는다.
      if (!text.trim()) { setStatus(COPY.author.docNeedBody); return false; }
      if (!explicit && !leaving && document.activeElement === titleInput) return false;
    }
    setStatus(COPY.ui.viewx.edSaving);
    try {
      // 무음 LWW 금지 — 기존 문서면 저장 직전 원격 해시 비교.
      if (!force && docId) {
        const cur = await api.docGet(opts.owner, docId);
        // ⚠ P18 이 여기다(P17b 범위 밖 — 일부러 안 고쳤다). 아래 조건은 `cur.executed`
        // 를 앞세우므로, 원격이 **쓰기 창(P17 의 `executed:false`)** 이면 비교가 통째로
        // 건너뛰어지고 저장이 그냥 진행된다: 충돌 배너도, 아래 "덮어쓰기 전 원격본을
        // 충돌본으로 보존"도 돌지 않는다. 즉 무음 LWW 금지 규율이 바로 그 상태에서만
        // 풀린다. 마운트는 이 상태를 막지만(위 stub), 이 경로는 **마운트 뒤에 원격이
        // 그 상태가 된 경우**라 stub 이 못 막는다.
        if (cur.executed && cur.content_hash && cur.content_hash !== hash) {
          setStatus(COPY.ui.viewx.edDirty);
          conflictBox.innerHTML = `
            <div class="error-banner" style="margin:.4rem 0">${COPY.ui.viewx.edConflict}
              <div style="margin-top:.3rem">
                <button class="btn" id="ed-ow">${COPY.ui.viewx.edOverwrite}</button>
                <button class="btn" id="ed-rl">${COPY.ui.viewx.edReload}</button>
              </div></div>`;
          conflictBox.querySelector("#ed-ow")!.addEventListener("click", async () => {
            conflictBox.innerHTML = "";
            // 덮어쓰기 전 원격본을 "충돌본" 문서로 먼저 보존(작업 소실 0 — 순수
            // LWW 의 유일한 위험을 없앤다). 신규 doc 이라 id 없이 put.
            if (cur.text !== undefined && cur.text !== "") {
              try {
                await api.docPut(opts.owner, cur.text, undefined,
                                 COPY.ui.viewx.edConflictCopy(cur.title || t));
              } catch { /* 스냅샷 실패해도 덮어쓰기는 진행 — 최선 노력 보존 */ }
            }
            void save(true, false, topicOnce);
          });
          conflictBox.querySelector("#ed-rl")!.addEventListener("click", () => {
            conflictBox.innerHTML = "";
            if (cur.text !== undefined) {
              setText(cur.text); lastSaved = cur.text; hash = cur.content_hash || "";
              titleInput.value = cur.title || ""; lastTitle = titleInput.value;
              setStatus(COPY.ui.viewx.edSaved);
            }
          });
          return false;
        }
      }
      let res: Awaited<ReturnType<typeof api.docPut>>;
      if (creating) {
        // 선언한 id 로 만든다. 남의 스페이스가 쥔 id 는 읽기로는 안 보여서 쓰기가 거절한다 —
        // 그때 다음 후보로 한 번 더(세 번까지). 거절된 id 에 조용히 합치는 길은 없다.
        const skip: string[] = [];
        let id = "";
        do {
          id = await opts.declareId!(t, skip);
          res = await api.docPut(opts.owner, text, id, t, ct, undefined, topic || undefined);
          // 다음 후보로 가는 것은 **그 id 를 못 쓴다**는 거절일 때뿐이다. 다른 실패에서 id 를
          // 바꾸면 멀쩡한 id 를 버리고 `-2` 를 지어낸다.
          if (res.executed || !/unavailable/i.test(String(res.error || ""))) break;
          skip.push(id);
        } while (skip.length < 3);
        if (!res.executed) throw new Error(res.error || "put failed");
        // 새로 만들었어야 한다. `created:false` 는 그 id 에 이미 내 문서가 있었다는 뜻이고
        // (읽기 확인과 쓰기 사이에 생겼다) — 성공이라고 말하지 않는다.
        if (res.created === false) throw new Error("doc id already in use");
        docId = res.doc_id || id;
      } else {
        res = await api.docPut(opts.owner, text, docId, t, ct, undefined, topicOnce || undefined);
        if (!res.executed) throw new Error(res.error || "put failed");
      }
      if (res.doc_id) docId = res.doc_id;              // 신규 → 생성된 id 확보
      lastSaved = text; lastTitle = t; lastCtime = ct; hash = res.content_hash || hash;
      if (topicOnce) knownTopic = topicOnce;
      // 서버가 형식 미달을 비웠으면 입력칸도 그 사실을 반영한다 — 화면에는 남아
      // 있는데 저장은 안 된 값이 보이면, 다음에 열었을 때 사라져 있어 놀란다.
      if (ct && res.content_time !== undefined && res.content_time !== ct) {
        ctimeInput.value = res.content_time || "";
        lastCtime = ctimeInput.value;
      }
      // 저장하는 동안 더 친 것이 있으면 "저장됨"이라고 말하지 않고 한 번 더 저장한다.
      if (dirty()) { setStatus(COPY.ui.viewx.edDirty); if (!timer) schedule(); }
      else setStatus(COPY.ui.viewx.edSaved);
      opts.onSaved?.(docId || "", t);
      if (creating && docId) opts.onCreated?.(docId, res.topic || topic);
      if (!res.skipped && docId) { void offerExtract(); void reindex(res.needs_embed); }
      return true;
    } catch {
      setStatus(COPY.ui.viewx.edSaveFail);
      return false;
    }
  }

  // 벡터 낡음(needs_embed) 처리 — 저장은 FTS 즉시, 벡터는 지연. 서버는 문서를 재임베딩
  // 큐에 되돌릴 뿐이고 벡터는 임베딩 워커가 나중에 채운다(워커가 없는 설치에서는 안 채워진다).
  //
  // ⚠ **응답이 왔다고 색인이 갱신된 것은 아니다.** 그래서 성공 문구는 "대기 중"이지 "갱신됨"
  // 이 아니다. 거짓 성공 배지가 2026-07-31 쓰기 큐 사고를 **보이지 않게** 만든 전례가 있다.
  // 요청이 실패해도 저장 자체는 성공했으므로 강등 문구는 `저장됨` 이다.
  async function reindex(needsEmbed?: boolean): Promise<void> {
    if (!needsEmbed || !docId) return;
    setStatus(COPY.ui.viewx.edReindexing);
    try {
      const res = await api.docReembed(opts.owner, docId);
      setStatus(res?.queued ? COPY.ui.viewx.edQueued : COPY.ui.viewx.edSaved);
    } catch { setStatus(COPY.ui.viewx.edSaved); }
  }

  /** 흐름 G — 저장 뒤 "이 문서에서 찾은 개념" 목록. 반영·건너뛴 것은 다시 권하지 않고,
   *  반영이 거절되면(주제 없음 등) 그 이유를 말한다. 목록은 `extract-list.ts` 가 그린다. */
  async function offerExtract(): Promise<void> {
    if (!docId) return;
    const { offerExtraction } = await import("./extract-list");
    await offerExtraction(extractBox, {
      owner: opts.owner, docId,
      setTopic: saveTopic,
    });
  }

  /** 주제를 적는다 — 보통 저장과 **같은 길**이다(도는 저장을 기다리고, 원격 해시를 확인하고,
   *  지금 화면의 본문·제목과 함께 보낸다). 본문이 그대로면 서버는 청크를 안 건드리고 주제만 적는다. */
  async function saveTopic(tp: string): Promise<boolean> {
    if (!docId || !tp) return false;
    return save(false, true, tp);
  }

  /** 이 문서의 주제 — 새 문서면 주제 칩, 아니면 미러 노드(`concept:doc.<id>`)의 주제. */
  let knownTopic = "";
  async function docTopic(): Promise<string> {
    const t = opts.topic?.() || knownTopic;
    if (t || !docId) return t;
    try {
      const r = await api.nodeContext(opts.owner, `concept:doc.${docId}`, 1) as { domain?: string };
      knownTopic = r?.domain || "";
      // 적힌 주제가 없으면 본문이 가장 많이 언급한 개념의 주제(트리가 이 문서를 거는 자리와 같은 규칙).
      if (!knownTopic) {
        const c = await api.docContext(opts.owner, docId);
        knownTopic = (c.concepts || []).find((x) => x.domain)?.domain || "";
      }
    } catch { /* 모르면 빈 값 — 부르는 쪽이 주제를 묻는다 */ }
    return knownTopic;
  }

  /** `[[` 목록의 마지막 줄 — 컴포저를 채워서 연다(종류 개념 · 주제 = 이 문서의 주제).
   *  만들어지면 그 제목이 본문에 들어간다. 닫으면 아무것도 안 넣는다. */
  async function createMentionConcept(q: string): Promise<string | null> {
    const title = q.trim();
    if (!title) return null;
    const { openComposer } = await import("./node-composer");
    const made = await openComposer({ kind: "concept", title, topic: await docTopic(),
                                      sourceDoc: docId });
    return made ? made.title : null;
  }

  /** 흐름 D — 고른 글이 짧으면 제목, 길면 한 줄 설명으로 컴포저를 연다. */
  async function composeFromText(kind: string, text: string) {
    const t = text.trim();
    if (!t) return;
    const short = t.length <= 80 && !t.includes("\n");
    const { openComposer } = await import("./node-composer");
    const k = kind === "record" || kind === "decision" ? kind : "concept";
    await openComposer({ kind: k, title: short ? t : "", oneLiner: short ? "" : t.slice(0, 400),
                         topic: await docTopic(), sourceDoc: docId });
  }

  // 포커스를 뺏지 않는 두 경우: 읽기로 시작할 때, 그리고 편집 가능하지만 자리가
   // 지도 옆일 때(autoFocus:false — 열자마자 커서가 깜빡이면 지도 단축키가 죽는다).
  if (!opts.readOnly && opts.autoFocus !== false) (cm ? { focus: () => cm!.focus() } : src).focus();
  const handle: DocEditorHandle = {
    pauseSaving: async (pause) => {
      savingPaused = pause;
      if (pause && timer) { window.clearTimeout(timer); timer = undefined; }
      if (pause && inflight) await inflight;
    },
    dispose: async (discard = false) => {
      LIVE.delete(host);
      // 디바운스를 **버리지 않고 확정한다.** 여기가 네 출구(뒤로 · 전체폭 · ✕ ·
      // 표면 이동)의 결과를 하나로 만드는 자리다 — 여태 앞의 둘은 버리고 뒤의 둘은
      // 몰래 저장했고, 어느 쪽인지는 누른 버튼이 정했다.
      // ⚠ `save()` 는 첫 줄에서 글자를 동기적으로 집는다. 그래서 아래 destroy 가
      //    뒤에 와야 한다 — 순서를 뒤집으면 확정할 본문이 이미 없다.
      leaving = true;
      if (timer) { window.clearTimeout(timer); timer = undefined; if (!discard) await save(); }
      else if (inflight) await inflight;
      // 아직 만들어지지 않은 새 문서(제목·주제가 없어 저장이 미뤄졌다) — 닫는다고 버리지
      // 않는다. 부르는 쪽이 초안으로 들고 있다가 다음 새 문서에서 되살린다.
      if (!docId && opts.declareId && (getText().trim() || titleInput.value.trim()))
        opts.onUnsaved?.({ title: titleInput.value, text: getText(), contentTime: ctimeInput.value });
      cm?.destroy();
      cm = null;
    },
    scrollToOffset: (pos: number) => cm?.scrollToOffset(pos),
    getText,
    saveTopic,
    saveNow: async () => {
      // 완료 클릭의 blur·compositionend 및 CM의 DOM 변경 처리가 끝난 뒤 원문을 읽는다.
      await new Promise<void>((resolve) => window.setTimeout(resolve, 0));
      if (timer) { window.clearTimeout(timer); timer = undefined; }
      await save(false, true);
      return !!docId && !dirty();
    },
  };
  LIVE.set(host, handle);
  // **처음부터 상태를 말한다.** 여태 `setStatus` 는 고치거나 저장할 때만 불려서,
  // 문서를 연 직후에는 상태 줄이 비어 있었다 — 상시 편집인 면이 저장 여부를 한 번도
  // 말하지 않은 셈이고, 그래서 "클릭해도 되나"가 남았다(발주자 지적 2026-09-10).
  // 방금 읽어 온 그대로이므로 지금은 저장된 상태다. 새 문서(흐름 B)는 아직 아무것도
  // 저장된 적이 없으므로 "저장됨"이라고 말하지 않는다.
  setStatus(!docId ? "" : COPY.ui.viewx.edSaved);
  return handle;
}

/** 오버레이 시트로 편집기를 연다(Records/Intake 진입점). 저장은 편집기 내부에서
 *  자동. 닫기는 배경 클릭·닫기 버튼. onSaved 로 호출부가 목록 갱신. */
export async function openDocSheet(opts: {
  owner: string; docId?: string; onSaved?: (docId: string, title: string) => void;
}): Promise<void> {
  const { openSheet, sheetActions, confirmDestructive: confirmD } = await import("./components");
  const { deleteDocument, checkDocumentDeletion } = await import("./doc-delete");
  let handle: DocEditorHandle | null = null;
  let pendingScope: { team: string | null } | null = null;
  let deleting = false;
  const sheet = openSheet({
    title: opts.docId ? COPY.ui.viewx.readerTitle : COPY.ui.viewx.edNew,
    bare: true, size: "write",
    onClose: () => {
      window.removeEventListener("vinc:account-reachable", retryOutcome);
      return handle?.dispose();
    },
  });
  sheet.body.innerHTML = `<div id="ed-host"></div>`;
  handle = await mountDocEditor(sheet.body.querySelector<HTMLElement>("#ed-host")!, {
    owner: opts.owner, docId: opts.docId, onSaved: opts.onSaved,
  });
  // 발: 삭제(부, 왼쪽) · 완료(주, 오른쪽). 시트 규격 그대로 — 주 액션은 늘 오른쪽 끝.
  sheet.foot.innerHTML = sheetActions(
    { id: "ed-close", label: COPY.ui.viewx.edDone },
    undefined,
    opts.docId ? { id: "ed-del", label: COPY.ui.viewx.del } : undefined);
  sheet.foot.querySelector("#ed-close")!.addEventListener("click", sheet.close);
  const reconcile = async (outcome: "deleted" | "present" | "unknown") => {
    // 부재가 확인되면 닫기가 대기 중 편집을 저장해 문서를 되살리지 않게 한다.
    if (outcome === "deleted") {
      pendingScope = null;
      await handle?.dispose(true);
      handle = null;
      sheet.close();
      opts.onSaved?.(opts.docId!, "");
    } else {
      const button = sheet.foot.querySelector<HTMLElement>("#ed-del");
      if (button) button.textContent = outcome === "present"
        ? COPY.ui.viewx.delDocPresent : COPY.ui.viewx.delDocUnknown;
      if (outcome === "present") {
        pendingScope = null;
        sheet.body.inert = false;
        await handle?.pauseSaving(false);
      }
    }
    const { refreshLnb } = await import("./lnb");
    await refreshLnb({ quiet: true });
  };
  async function retryOutcome() {
    if (!pendingScope || deleting) return;
    deleting = true;
    try { await reconcile(await checkDocumentDeletion(opts.owner, opts.docId!, pendingScope)); }
    finally { deleting = false; }
  }
  window.addEventListener("vinc:account-reachable", retryOutcome);
  sheet.foot.querySelector("#ed-del")?.addEventListener("click", async () => {
    if (deleting) return;
    deleting = true;
    const button = sheet.foot.querySelector<HTMLButtonElement>("#ed-del")!;
    const title = sheet.body.querySelector<HTMLInputElement>("#ed-title")?.value
      || COPY.ui.viewx.edTitlePlaceholder;
    try {
      if (!await confirmD(COPY.ui.viewx.delDocAsk(title),
                          COPY.ui.viewx.delConfirm, COPY.ui.viewx.delCancel)) return;
      button.disabled = true;
      sheet.body.inert = true;
      await handle?.pauseSaving(true);
      pendingScope = { team: cfg.team || null };
      await reconcile(await deleteDocument(opts.owner, opts.docId!, pendingScope));
    } finally { deleting = false; button.disabled = false; }
  });
}
