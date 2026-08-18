/** 빈 상태는 **오류가 아니라 초대**다.
 *
 *  여태 그래프가 비어 있으면 화면은 문구 한 줄과 "파일 가져오기" 버튼 하나였다. 그
 *  버튼은 파일 선택창만 열었으므로, 첫 사용자가 할 수 있는 일은 **파일을 고르는 것
 *  하나뿐**이었다 — 끌어다 놓을 자리도 없고, 파일이 아직 없는 사람(그냥 개념 하나를
 *  적어 보려는 사람)이 시작할 자리는 아예 없었다.
 *
 *  여기서 세 갈래를 한자리에 편다:
 *    ① 끌어다 놓기 — 이 블록 자체가 드롭 대상이다
 *    ② 파일 고르기 — 기존 `pickAndImport` 그대로(문서·팩 판별은 그쪽 규칙 한 벌)
 *    ③ **노드 직접 만들기** — 파일 없이 개념·기록·결정 하나를 손으로 적는다
 *
 *  ⚠ 오류 상태와 섞지 말 것. 조회가 실패한 것(`executed:false`)은 여전히 오류 문구와
 *  재시도이고, 이 블록은 **조회가 성공했는데 결과가 0** 인 자리에만 쓴다. 둘을 같은
 *  모양으로 그리면 "저장소가 죽었다"와 "아직 아무것도 없다"가 구분되지 않는다.
 */
import { api, cfg } from "./api";
import { COPY } from "./copy";
import { openSheet } from "./components";

const OWNER = "tenant:local";   // 본인 데이터 공간(intake 와 같은 값 — 샘플과 분리)

/** 여러 개를 놓았을 때 가져오기 표면으로 넘길 파일. 한 번만 꺼내진다. */
let handoff: File[] | null = null;
export function takeDroppedFiles(): File[] | null {
  const f = handoff; handoff = null; return f;
}

const esc = (s: string) => s.replace(/[&<>"]/g, (c) =>
  c === "&" ? "&amp;" : c === "<" ? "&lt;" : c === ">" ? "&gt;" : "&quot;");

/** 빈 상태 블록의 HTML. 문구는 호출부가 정한다 — 지도가 비었을 때와 문서 목록이
 *  비었을 때는 같은 초대를 하지만 첫 줄이 다르다. */
export function emptyInviteHtml(lead: string): string {
  const c = COPY.emptyInvite;
  return `
    <div class="empty empty-invite" data-invite>
      <p class="ei-lead">${lead}</p>
      <p class="hint ei-drop">${c.dropHint}</p>
      <div class="ei-actions">
        <button class="btn btn-primary" data-ei-pick>${c.pickCta}</button>
        <button class="btn" data-ei-new>${c.newNodeCta}</button>
      </div>
    </div>`;
}

/** 블록에 손을 붙인다(HTML 을 넣은 뒤 호출). `onDone` 은 적재·생성이 실제로 끝난
 *  뒤에만 불린다 — 화면을 다시 그리는 것은 호출부 몫이다. */
export function wireEmptyInvite(root: ParentNode, onDone?: (nodeId?: string) => void) {
  const box = root.querySelector<HTMLElement>("[data-invite]");
  if (!box) return;

  // 문서는 있는데 그래프가 비었으면 **다른 말을 해야 한다.** 방금 파일을 넣은 사람에게
  // "파일을 추가하세요"라고 답하는 화면은 자기가 무슨 일이 있었는지 모르는 화면이다.
  // (문서 적재와 개념 파생은 갈라져 있다 — 개념은 추출을 반영해야 생긴다.)
  void api.docList(cfg.owner).then((r) => {
    const n = r.count ?? (r.docs || []).length;
    if (!n) return;
    const lead = box.querySelector(".ei-lead");
    if (lead) lead.textContent = COPY.emptyInvite.leadDocsNoNodes;
    box.querySelector(".ei-actions")?.insertAdjacentHTML("beforeend",
      `<a class="btn" href="#records">${COPY.emptyInvite.seeDocsCta}</a>`);
  }).catch(() => {});

  box.querySelector("[data-ei-pick]")?.addEventListener("click", async () => {
    const { pickAndImport } = await import("./sheets");
    pickAndImport(() => onDone?.());
  });
  box.querySelector("[data-ei-new]")?.addEventListener("click", () => {
    void openNewNodeSheet((id) => onDone?.(id));
  });

  // 끌어다 놓기. 셸은 dragDropEnabled:false 라 웹뷰의 HTML5 이벤트로 받는다.
  box.addEventListener("dragover", (e) => { e.preventDefault(); box.classList.add("drag"); });
  box.addEventListener("dragleave", () => box.classList.remove("drag"));
  box.addEventListener("drop", async (e) => {
    e.preventDefault();
    box.classList.remove("drag");
    const files = Array.from(e.dataTransfer?.files || []);
    if (!files.length) return;
    // 하나면 여기서 끝낸다(파일 고르기와 **같은 시트**). 여럿이면 배치 진행률과
    // 부분 실패 보고가 있는 가져오기 표면으로 넘긴다 — 여기서 순차로 시트를 여러 번
    // 열면 "5개를 놓았는데 1개만 들어갔다"를 사용자가 뒤늦게 알게 된다.
    if (files.length === 1) {
      const { isDocFile } = await import("./doc-import");
      const { openDocImportSheet, openImportSheet } = await import("./sheets");
      if (isDocFile(files[0])) { void openDocImportSheet(files[0], () => onDone?.()); return; }
      try {
        const { readPackFile } = await import("./packfile");
        openImportSheet(await readPackFile(files[0]), () => onDone?.());
      } catch {
        const sheet = openSheet({ title: COPY.ui.sheet.importTitle });
        sheet.body.innerHTML = `<div class="error-banner">${COPY.packs.error}</div>`;
      }
      return;
    }
    handoff = files;
    location.hash = "#ingest";
  });
}

/** 제목 → id 조각. 그래프 id 는 소문자 슬러그라 한글은 그대로 두고 공백·기호만 접는다
 *  (`docstore._SLUG_RE` 와 같은 문자 계열). 빈 결과는 호출부가 막는다. */
function slug(title: string): string {
  return title.toLowerCase().replace(/[^a-z0-9가-힣]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60);
}

/** 노드 하나를 손으로 만든다 — 파일이 없어도 시작할 수 있게.
 *
 *  타입은 셋만 낸다(개념·기록·결정). 스키마는 여섯을 알지만 나머지 셋(주제·다리·사건)은
 *  손으로 지을 것이 아니다: 주제는 `domain` 문자열에서 파생되고, 다리와 사건은 각각
 *  구조와 시간이 붙어야 뜻이 선다.
 *
 *  ⚠ 한 줄 설명은 `one_liner` 로 보낸다(개념일 때). 그 자리가 답변 재료로 쓰이는 열이라
 *  props 에 넣으면 화면에는 남지만 답변에는 안 실린다. */
export async function openNewNodeSheet(onCreated?: (id: string) => void) {
  const c = COPY.emptyInvite;
  const sheet = openSheet({ title: c.newSheetTitle, subtitle: c.newSheetSub });
  const domains: string[] = await api.domains(cfg.owner)
    .then((r: any) => (r?.rows || r?.domains || []).map((d: any) => d.domain || d.name || d)
      .filter((s: any) => typeof s === "string"))
    .catch(() => []);

  sheet.body.innerHTML = `
    <label class="ei-field"><span>${c.fTitle}</span>
      <input id="ei-title" type="text" autocomplete="off" /></label>
    <label class="ei-field"><span>${c.fKind}</span>
      <select id="ei-kind">
        <option value="concept">${c.kConcept}</option>
        <option value="record">${c.kRecord}</option>
        <option value="decision">${c.kDecision}</option>
      </select></label>
    <label class="ei-field"><span>${c.fTopic}</span>
      <input id="ei-topic" type="text" list="ei-domains" autocomplete="off"
             placeholder="${esc(c.fTopicHint)}" />
      <datalist id="ei-domains">${domains.map((d) => `<option value="${esc(d)}"></option>`).join("")}</datalist>
    </label>
    <label class="ei-field"><span>${c.fOneLiner}</span>
      <textarea id="ei-note" rows="3"></textarea></label>
    <p class="hint" id="ei-msg"></p>`;
  sheet.foot.innerHTML = `<button class="btn btn-primary" id="ei-save">${c.newSaveCta}</button>`;

  const $ = <T extends HTMLElement>(s: string) => sheet.body.querySelector<T>(s)!;
  const titleEl = $<HTMLInputElement>("#ei-title");
  const msgEl = $<HTMLElement>("#ei-msg");
  titleEl.focus();

  sheet.foot.querySelector("#ei-save")!.addEventListener("click", async () => {
    const title = titleEl.value.trim();
    const kind = $<HTMLSelectElement>("#ei-kind").value;
    const topic = $<HTMLInputElement>("#ei-topic").value.trim();
    const note = $<HTMLTextAreaElement>("#ei-note").value.trim();
    const s = slug(title);
    if (!title || !s) { msgEl.textContent = c.needTitle; titleEl.focus(); return; }

    const id = `${kind}:${s}`;
    const node: Record<string, unknown> = { id, type: kind, title };
    if (topic) node.domain = topic;
    if (note) { node.one_liner = note; node.props = { note }; }

    msgEl.textContent = c.saving;
    try {
      const res: any = await api.ingestFragment({ nodes: [node], owner: OWNER });
      if (res?.executed === false) throw new Error(res.error || "ingest failed");
      // ⚠ `merged` 는 **실제로 들어간 것만** 센다. warnings 를 안 읽으면 반쯤 들어간
      // 쓰기를 성공으로 읽는다(partial 계약).
      const merged = (res?.merged?.nodes?.created || 0) + (res?.merged?.nodes?.matched || 0);
      if (!merged) throw new Error((res?.warnings || []).join(" · ") || "nothing merged");
      sheet.close();
      onCreated?.(id);
    } catch (err: any) {
      msgEl.innerHTML = `<span class="error-banner">${esc(c.saveFail)} ${esc(String(err?.message || ""))}</span>`;
    }
  });
}
