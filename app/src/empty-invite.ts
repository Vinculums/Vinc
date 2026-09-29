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

/** 빈 상태 블록의 HTML. 문구는 호출부가 정한다 — 지도가 비었을 때와 문서 목록이
 *  비었을 때는 같은 초대를 하지만 첫 줄이 다르다.
 *
 *  `scoped` 는 **좁힌 범위**(주제 하나)가 빈 경우다. 스토어 전체가 빈 것과 다르므로
 *  첫 줄을 호출부가 준 그대로 두고(아래 `wireEmptyInvite` 가 바꾸지 않는다), 전체로
 *  나가는 버튼(`data-ei-all`)을 앞에 붙인다. 그 버튼의 동작은 층을 아는 호출부가 건다. */
export function emptyInviteHtml(lead: string, opts: { scoped?: boolean } = {}): string {
  const c = COPY.emptyInvite;
  return `
    <div class="empty empty-invite" data-invite${opts.scoped ? " data-scoped" : ""}>
      <p class="ei-lead">${lead}</p>
      <p class="hint ei-drop">${c.dropHint}</p>
      <div class="ei-actions">
        ${opts.scoped ? `<button class="btn btn-primary" data-ei-all>${c.seeAllCta}</button>` : ""}
        <button class="btn${opts.scoped ? "" : " btn-primary"}" data-ei-pick>${c.pickCta}</button>
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
  //
  // 그 말은 **스토어 전체**에 노드가 0 일 때만 참이다. 좁힌 범위가 빈 것(`data-scoped`)
  // 은 건드리지 않고, 범위를 안 좁혔어도 노드가 있으면(렌즈·시간 커서에 가려진 경우)
  // "개념이 없다"고 말하지 않는다. 노드 수는 nav-tree 의 종별 집계가 센다.
  if (!box.hasAttribute("data-scoped")) {
    void Promise.all([api.docList(cfg.owner), api.navTree(cfg.owner)]).then(([r, t]) => {
      const docs = r.count ?? (r.docs || []).length;
      const nodes = Object.values(t.counts?.kinds || {}).reduce((a, b) => a + b, 0);
      if (!docs || nodes) return;
      const lead = box.querySelector(".ei-lead");
      if (lead) lead.textContent = COPY.emptyInvite.leadDocsNoNodes;
      box.querySelector(".ei-actions")?.insertAdjacentHTML("beforeend",
        `<a class="btn" href="#records">${COPY.emptyInvite.seeDocsCta}</a>`);
    }).catch(() => {});
  }

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
    // 여럿이면 배치 진행률과 부분 실패 보고가 있는 가져오기 화면으로 **이 자리에서** 넘긴다.
    // 여태 없는 라우트(`#ingest`)로 보내 파일이 조용히 사라졌다.
    const { renderIntake } = await import("./surfaces/intake");
    const sheet = openSheet({ title: COPY.author.menuImport, size: "panel",
                              onClose: () => onDone?.() });
    renderIntake(sheet.body, { withHeader: false, withNewDoc: false, files });
  });
}

/** 노드 하나를 손으로 만든다 — 이제 컴포저(흐름 E)가 그 일을 한다. 여태 이 폼은 이미
 *  있는 id 에 조용히 합쳤고, 한글·영문 밖의 제목을 빈 id 로 만들었고, 소유자를 박아
 *  두었다(`node-composer.ts` 머리말). 만든 노드를 지도에 세우는 것도 컴포저가 한다. */
export async function openNewNodeSheet(onCreated?: (id: string) => void) {
  const { startNewNode } = await import("./authoring");
  const made = await startNewNode();
  if (made) onCreated?.(made.id);
}
