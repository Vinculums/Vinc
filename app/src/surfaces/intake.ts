import { COPY } from "../copy";
import { readPackFile } from "../packfile";
import { header } from "../components";
import { icon, stateDot } from "../icons";
import { api, cfg, sidecarDiag, WEB } from "../api";
import samplePack from "../sample-pack.json";

/** Surface 1 — Intake (Signal 재스타일 · Figma 42:3 Empty / 42:5 Ingesting /
 *  42:7 Success). 실제 적재 = 지식 팩/프래그먼트 파일 import(.vincpack/.json →
 *  /ingest/pack·/ingest/fragment). 원문 문서→그래프 추출은 앱이 아닌 Claude/MCP
 *  경로(§mcp-note). 파일 읽기는 HTML File API; 셸 드롭은 dragDropEnabled:false 로
 *  웹뷰 수신(tauri.conf). 카피는 정본(COPY) — 시안 더미 문구 미사용. */
const USER_OWNER = "tenant:local";   // 본인 데이터 공간(샘플 sample:demo 와 분리)

export function renderIntake(el: HTMLElement, opts?: { withHeader?: boolean }) {
  const ui = COPY.ui.intakeUi;
  // Packs '가져오기' 탭이 헤더리스로 재사용(패널 헤더+탭은 packs 가 제공).
  const chrome = opts?.withHeader === false ? "" : header("ingest", "Intake", COPY.ui.meta.intake);

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
            <button class="btn" id="new-doc">${COPY.ui.atlasx.edNew}</button>
            ${WEB ? "" : `<button class="btn" id="try-sample">${ui.trySample}</button>`}
          </div>
        </div>
        ${WEB ? "" : `
        <div class="mcp-note">
          <h3>${ui.mcpTitle}</h3>
          <p>${ui.mcpBody}</p>
        </div>`}
      </div>
      <input type="file" id="file-input" accept=".vincpack,.json,application/json" multiple hidden />
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

  // ── 파일 1개 적재 — 형식 감지(share pack 래퍼 · bare fragment). ──
  async function importOne(f: File): Promise<{ nodes: number; edges: number }> {
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

    let nodes = 0, edges = 0, ok = 0;
    const failed: string[] = [];
    let lastErr: any = null;

    for (let i = 0; i < files.length; i++) {
      setRow(i, "active");
      try {
        const r = await importOne(files[i]);
        nodes += r.nodes; edges += r.edges; ok++;
        setRow(i, "done");
      } catch (e) {
        failed.push(files[i].name); lastErr = e;
        setRow(i, "failed");
      }
      metaEl.textContent = ui.processing(i + 1, files.length);
    }

    if (ok > 0) {
      cfg.owner = USER_OWNER;   // 본인 공간으로 전환해 방금 넣은 데이터를 본다
      showSuccess({ files: ok, nodes, edges, failed });
    } else {
      showError(failed.join(", ") || "the file", lastErr);
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
  function showSuccess(r: { files: number; nodes: number; edges: number; failed: string[] }) {
    el.innerHTML = `
      ${chrome}
      <div class="intake-stage">
        <div class="success-stage">
          <span class="success-check">${icon("check", 30)}</span>
          <h2>${ui.imported(r.files, r.nodes, r.edges)}</h2>
          <p class="hint success-body">${ui.successBody}</p>
          ${r.failed.length ? `<p class="hint success-failed">${ui.someFailed(r.failed.join(", "))}</p>` : ""}
          <div class="concept-chips" id="concept-chips" hidden></div>
          <div class="success-actions">
            <a class="btn btn-primary" href="#atlas">${COPY.intake.successCta}</a>
            <button class="btn" id="add-more">${ui.addMore}</button>
          </div>
        </div>
      </div>
    `;
    el.querySelector("#add-more")!.addEventListener("click", showIdle);
    void loadConceptChips();
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
  function showError(src: string, err: any) {
    el.innerHTML = `
      ${chrome}
      <div class="intake-stage">
        <div class="success-stage">
          <div class="error-banner">${COPY.intake.error(src)}</div>
          <p class="hint diag">진단: ${esc(String((err && err.message) || err))} · ${esc(sidecarDiag)}</p>
          <div class="success-actions">
            <button class="btn btn-primary" id="retry">${COPY.intake.idleCta}</button>
          </div>
        </div>
      </div>
    `;
    el.querySelector("#retry")!.addEventListener("click", showIdle);
  }

  // D30 — 설치 직후 60초 가치: 동봉 샘플 팩을 sample:demo 스코프로 수입하고
  // 그 스코프로 전환해 Atlas 를 바로 체험. (Settings 의 Owner scope 로 본인 복귀.)
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
              <a class="btn btn-primary" href="#atlas">${COPY.intake.successCta}</a>
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
