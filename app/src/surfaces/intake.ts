import { COPY } from "../copy";
import { readPackFile } from "../packfile";
import { header } from "../components";
import { icon, stateDot } from "../icons";
import { api, cfg, sidecarDiag, WEB } from "../api";
import {
  isDocFile, importDocFile, originIndex, previewExtract, applyExtract, docImportError,
} from "../doc-import";
import samplePack from "../sample-pack.json";

/** Surface 1 — Intake (Signal 재스타일 · Figma 42:3 Empty / 42:5 Ingesting /
 *  42:7 Success). 두 갈래를 받는다:
 *    · 지식 팩/프래그먼트(.vincpack/.json → /ingest/pack·/ingest/fragment)
 *    · **원문 문서**(.md/.txt/.rst → /doc/put, D45 정본) + 결정론 개념 추출
 *  더 깊은 추출(코드·Vinc 가 못 읽는 형식)만 Claude/MCP 경로가 맡는다(§mcp-note).
 *  파일 읽기는 HTML File API; 셸 드롭은 dragDropEnabled:false 로 웹뷰 수신
 *  (tauri.conf). 카피는 정본(COPY) — 시안 더미 문구 미사용. */
const USER_OWNER = "tenant:local";   // 본인 데이터 공간(샘플 sample:demo 와 분리)

export function renderIntake(el: HTMLElement,
                             opts?: { withHeader?: boolean; withNewDoc?: boolean }) {
  const ui = COPY.ui.intakeUi;
  // Tools 의 '가져오기' 탭이 헤더리스로 재사용(시트의 머리·탭줄이 크롬을 이미 준다).
  const chrome = opts?.withHeader === false ? "" : header("ingest", "Intake", COPY.ui.meta.intake);
  // `새 문서`는 Tools 안에서는 **옆 탭이 이미 갖고 있다**(만들기). 같은 시트 안에서
  // 같은 일을 두 자리에 두면 하나가 다른 하나 위에 시트를 또 여는 꼴이 된다.
  // 화면 재렌더(showIdle 재호출)를 타므로 붙였다 떼는 대신 여기서 끈다.
  const newDoc = opts?.withNewDoc === false ? "" :
    `<button class="btn" id="new-doc">${COPY.ui.viewx.edNew}</button>`;

  // ── 상태 1 — Empty (42:3): 대시 드롭존 + 로고 마크 + CTA. mcp-note 유지. ──
  function showIdle() {
    el.innerHTML = `
      ${chrome}
      <div class="intake-stage">
        <div class="dropzone" id="drop" role="button" tabindex="0">
          <span class="dz-mark">${icon("recall", 34)}</span>
          <h2>${COPY.intake.idleTitle}</h2>
          <p class="dz-body">${COPY.intake.idleBody}</p>
          <p class="hint dz-sov">${WEB ? COPY.sovereigntyWeb : COPY.sovereignty}</p>
          <div class="dz-actions">
            <button class="btn btn-primary" id="pick">${COPY.intake.idleCta}</button>
            ${newDoc}
            ${WEB ? "" : `<button class="btn" id="try-sample">${ui.trySample}</button>`}
          </div>
        </div>
        ${WEB ? "" : `
        <div class="mcp-note">
          <h3>${ui.mcpTitle}</h3>
          <p>${ui.mcpBody}</p>
        </div>`}
      </div>
      <input type="file" id="file-input" multiple hidden
             accept=".vincpack,.json,.md,.markdown,.txt,.rst,.pdf,application/json,text/markdown,text/plain,application/pdf" />
    `;
    const drop = el.querySelector<HTMLElement>("#drop")!;
    const input = el.querySelector<HTMLInputElement>("#file-input")!;
    const openPicker = () => input.click();

    el.querySelector("#pick")!.addEventListener("click", openPicker);
    // D45-E — 빈 문서로 시작(파일 없이 앱 안에서 바로 작성 → 첫 저장 시 신규 정본 생성).
    el.querySelector("#new-doc")?.addEventListener("click", async () => {
      const { openDocSheet } = await import("../doc-editor");
      await openDocSheet({ owner: USER_OWNER });
    });
    drop.addEventListener("click", (e) => {
      if ((e.target as HTMLElement).closest("button")) return;   // 버튼은 자체 핸들러
      openPicker();
    });
    drop.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openPicker(); }
    });
    input.addEventListener("change", () => {
      if (input.files && input.files.length) importFiles(Array.from(input.files));
      input.value = "";   // 같은 파일 재선택 허용
    });

    // 드래그&드롭(웹뷰 HTML5 — 셸은 dragDropEnabled:false 로 수신)
    drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("drag"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("drag"));
    drop.addEventListener("drop", (e) => {
      e.preventDefault();
      drop.classList.remove("drag");
      const files = e.dataTransfer?.files;
      if (files && files.length) importFiles(Array.from(files));
    });

    el.querySelector("#try-sample")?.addEventListener("click", trySample);
  }

  // ── 파일 1개 적재 — 형식 감지(원문 문서 · share pack 래퍼 · bare fragment). ──
  async function importOne(
    f: File, index?: Map<string, string>,
  ): Promise<{ nodes: number; edges: number; conflicts: number; docId?: string }> {
    // 원문 문서는 팩 파서에 넘기지 않는다 — 넘기면 packfile 의 JSON.parse 가 먼저
    // 죽어서 크기·인코딩 문제까지 전부 "형식 오류"로 둔갑한다(D45 정본 경로로 직행).
    if (isDocFile(f)) {
      const r = await importDocFile(f, USER_OWNER, index);
      return { nodes: 0, edges: 0, conflicts: 0, docId: r.docId };
    }
    // D55 — zip 컨테이너·구 JSON 양쪽을 받는다. `f.text()` 로 읽으면 zip 이
    // 깨지므로 바이트로 연다(판별·해제는 packfile 이 한다).
    const obj: any = await readPackFile(f);
    let res: any;
    if (obj && obj.vinc_pack !== undefined) {
      res = await api.importPack(obj, USER_OWNER);
    } else if (obj && obj.fragment && (obj.fragment.nodes || obj.fragment.edges)) {
      res = await api.ingestFragment({ ...obj.fragment, owner: USER_OWNER });
    } else if (obj && (obj.nodes || obj.edges)) {
      res = await api.ingestFragment({ ...obj, owner: USER_OWNER });
    } else {
      throw new Error("unrecognized pack format");
    }
    if (res && res.executed === false) throw new Error(res.error || "import failed");
    const m = (res && res.merged) || {};
    return {
      nodes: (m.nodes?.created || 0) + (m.nodes?.matched || 0),
      edges: m.edges?.created || 0,
      // 남이 이미 가진 id 는 건너뛴다(호스티드는 노드 PK 가 전역이라 남의 팩을
      // 가져오면 개념이 통째로 이 사유로 빠진다). 개수만 세고 사유는 아래에서
      // 한 줄로 말한다 — 안 말하면 "개념 0개"가 원인 없는 실패로 읽힌다.
      conflicts: (res?.id_conflicts || []).length,
    };
  }

  // ── 상태 2 — Ingesting (42:5): 파일별 행(●◐○ + 진행 바 + 상태 라벨). ──
  async function importFiles(files: File[]) {
    el.innerHTML = `
      ${chrome}
      <div class="intake-stage">
        <section class="proc-panel">
          <div class="proc-head">
            <h2>${ui.importing}</h2>
            <p class="hint proc-meta" id="proc-meta">${ui.processing(0, files.length)}</p>
          </div>
          <div class="proc-rows" id="proc-rows">
            ${files.map((f, i) => procRow(f.name, i)).join("")}
          </div>
        </section>
      </div>
    `;
    const metaEl = el.querySelector<HTMLElement>("#proc-meta")!;

    let nodes = 0, edges = 0, ok = 0, conflicts = 0;
    const docIds: string[] = [];
    const failed: string[] = [];
    let lastErr: any = null;
    let lastMsg: string | null = null;

    // 같은 파일을 고쳐 다시 넣으면 새 문서가 아니라 갱신이 되게 하는 색인.
    // 문서가 하나라도 있을 때만 조회한다 — 팩만 넣는 흐름에 왕복을 더하지 않는다.
    const index = files.some(isDocFile) ? await originIndex(USER_OWNER) : undefined;

    for (let i = 0; i < files.length; i++) {
      setRow(i, "active");
      try {
        const r = await importOne(files[i], index);
        nodes += r.nodes; edges += r.edges; conflicts += r.conflicts; ok++;
        if (r.docId) docIds.push(r.docId);
        setRow(i, "done");
      } catch (e) {
        failed.push(files[i].name); lastErr = e;
        lastMsg = docImportError(e, files[i].name);
        setRow(i, "failed");
      }
      metaEl.textContent = ui.processing(i + 1, files.length);
    }

    if (ok > 0) {
      cfg.owner = USER_OWNER;   // 본인 공간으로 전환해 방금 넣은 데이터를 본다
      showSuccess({ files: ok, nodes, edges, conflicts,
                    docs: docIds.length, docIds, failed });
    } else {
      showError(failed.join(", ") || "the file", lastErr, lastMsg);
    }
  }

  function procRow(name: string, i: number): string {
    const safe = esc(name);
    return `
      <div class="proc-row" data-row="${i}" data-state="pending">
        <span class="proc-mark">${stateDot("empty", 16)}</span>
        <span class="proc-name" data-tip="${safe}">${safe}</span>
        <span class="proc-bar"><i></i></span>
        <span class="proc-status">${ui.stPending}</span>
      </div>`;
  }

  function setRow(i: number, state: "active" | "done" | "failed") {
    const row = el.querySelector<HTMLElement>(`.proc-row[data-row="${i}"]`);
    if (!row) return;
    row.dataset.state = state;
    const mark = row.querySelector<HTMLElement>(".proc-mark")!;
    const status = row.querySelector<HTMLElement>(".proc-status")!;
    if (state === "active") { mark.innerHTML = stateDot("half", 16); status.textContent = ui.stActive; }
    else if (state === "done") { mark.innerHTML = stateDot("filled", 16); status.textContent = ui.stDone; }
    else { mark.innerHTML = icon("alert", 16); status.textContent = ui.stFailed; }
  }

  // ── 상태 3 — Success (42:7): check + 요약 + 개념 칩(실 도메인) + 버튼 2종. ──
  function showSuccess(r: { files: number; nodes: number; edges: number; conflicts: number;
                            docs: number; docIds: string[]; failed: string[] }) {
    // 팩과 문서는 산출물이 다르다(팩=개념·연결 / 문서=원문 보관). 한 문장에 섞으면
    // 문서만 넣었을 때 "개념 0개, 연결 0개"로 읽혀 실패처럼 보인다 — 갈라서 센다.
    const packs = r.files - r.docs;
    const summary = [
      packs > 0 ? ui.imported(packs, r.nodes, r.edges) : "",
      r.docs > 0 ? ui.importedDocs(r.docs) : "",
    ].filter(Boolean).join(" ");
    el.innerHTML = `
      ${chrome}
      <div class="intake-stage">
        <div class="success-stage">
          <span class="success-check">${icon("check", 30)}</span>
          <h2>${summary}</h2>
          <p class="hint success-body">${ui.successBody}</p>
          ${r.failed.length ? `<p class="hint success-failed">${esc(ui.someFailed(r.failed.join(", ")))}</p>` : ""}
          ${r.conflicts ? `<p class="hint success-failed">${esc(COPY.ui.sheet.destSkippedIds(r.conflicts))}</p>` : ""}
          <div class="concept-chips" id="concept-chips" hidden></div>
          <div class="success-actions">
            <a class="btn btn-primary" href="#view">${COPY.intake.successCta}</a>
            <span id="extract-offer"></span>
            <button class="btn" id="add-more">${ui.addMore}</button>
          </div>
        </div>
      </div>
    `;
    el.querySelector("#add-more")!.addEventListener("click", showIdle);
    void loadConceptChips();
    if (r.docIds.length) void offerExtract(r.docIds);
  }

  // 정본 적재 후 개념 추출은 **미리보기 → 승인** 2단계다(편집기 offerExtract 와 같은
  // 규칙). 자동 반영하면 임의 문서의 헤딩이 그대로 개념이 되어 그래프를 오염시키고,
  // 들어간 개념은 status='proposed' 라 정리는 결국 사람 일이 된다.
  async function offerExtract(docIds: string[]) {
    const box = el.querySelector<HTMLElement>("#extract-offer");
    if (!box) return;
    const n = await previewExtract(USER_OWNER, docIds);
    // 미리보기 중에 '계속 추가'로 화면이 갈렸을 수 있다 — 죽은 노드에 쓰지 않는다.
    if (!n || !el.contains(box)) return;
    box.innerHTML = `<button class="btn" id="do-extract">${COPY.ui.viewx.edExtractOffer(n)}</button>`;
    box.querySelector("#do-extract")!.addEventListener("click", async () => {
      box.innerHTML = `<span class="hint">${ui.importing}</span>`;
      const ap = await applyExtract(USER_OWNER, docIds);
      if (!el.contains(box)) return;
      box.innerHTML = `<span class="hint">${COPY.ui.viewx.edExtractDone(ap.created, ap.mentions)}</span>`;
      void loadConceptChips();   // 새 개념이 칩에 반영되도록 다시 읽는다
    });
  }

  // 개념 칩 = 본인 공간의 실 도메인(상위 3개). 사이드카 미가동/빈 그래프면 조용히 숨김.
  async function loadConceptChips() {
    const box = el.querySelector<HTMLElement>("#concept-chips");
    if (!box) return;
    try {
      const res: any = await api.domains(USER_OWNER);
      const rows: Array<{ domain: string; count?: number; n?: number }> =
        Array.isArray(res) ? res : (res?.domains || []);
      if (!rows.length) return;
      box.innerHTML = rows.slice(0, 3).map((d) => {
        const name = esc(String(d.domain ?? ""));
        const c = d.count ?? d.n;
        return `<span class="chip chip-static">${name}${c != null ? ` · ${c}` : ""}</span>`;
      }).join("");
      box.hidden = false;
    } catch { /* 조용히 숨김 유지 */ }
  }

  // ── 오류 — 전량 실패 시. 이미 넣은 것은 안전(카피 정본). ──
  // msg 가 있으면(크기 초과처럼 원인이 확정된 실패) 그 문장만 보인다: 진단 줄은
  // 사이드카를 의심하게 만드는데 이 경우 사이드카는 멀쩡하다.
  function showError(src: string, err: any, msg?: string | null) {
    el.innerHTML = `
      ${chrome}
      <div class="intake-stage">
        <div class="success-stage">
          <div class="error-banner">${esc(msg || COPY.intake.error(src))}</div>
          ${msg ? "" : `<p class="hint diag">${ui.diag}: ${esc(String((err && err.message) || err))} · ${esc(sidecarDiag)}</p>`}
          <div class="success-actions">
            <button class="btn btn-primary" id="retry">${COPY.intake.idleCta}</button>
          </div>
        </div>
      </div>
    `;
    el.querySelector("#retry")!.addEventListener("click", showIdle);
  }

  // D30 — 설치 직후 60초 가치: 동봉 샘플 팩을 sample:demo 스코프로 수입하고
  // 그 스코프로 전환해 View 를 바로 체험. (Settings 의 Owner scope 로 본인 복귀.)
  async function trySample() {
    el.innerHTML = `
      ${chrome}
      <div class="intake-stage">
        <section class="proc-panel proc-slim">
          <div class="proc-head"><h2>${ui.importingSample}</h2></div>
        </section>
      </div>`;
    try {
      const res: any = await api.importPack(samplePack, "sample:demo");
      if (res.executed === false) throw new Error(res.error);
      cfg.owner = "sample:demo";
      el.innerHTML = `
        ${chrome}
        <div class="intake-stage">
          <div class="success-stage">
            <span class="success-check">${icon("check", 30)}</span>
            <h2>${ui.sampleLoaded}</h2>
            <div class="success-actions">
              <a class="btn btn-primary" href="#view">${COPY.intake.successCta}</a>
            </div>
          </div>
        </div>`;
    } catch (e: any) {
      showError("the sample pack", e);
    }
  }

  showIdle();
}

function esc(s: string): string {
  return s.replace(/[<>&"]/g, (m) => (
    { "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;" }[m] as string));
}
