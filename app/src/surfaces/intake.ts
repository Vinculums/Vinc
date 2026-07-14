import { COPY } from "../copy";
import { header, headerCta } from "../components";
import { icon } from "../icons";
import { api, cfg, sidecarDiag } from "../api";
import samplePack from "../sample-pack.json";

/** Surface 1 — Intake (Figma 5:2 grammar · spec vinc.d.ingest v0.2).
 *  실제 적재 = 지식 팩/프래그먼트 파일 import(.vincpack/.json → /ingest/pack·
 *  /ingest/fragment). 원문 문서→그래프 추출은 앱이 아닌 Claude/MCP 경로(§mcp-note).
 *  파일 읽기는 HTML File API(플러그인 불요); 셸 드롭은 dragDropEnabled:false 로 웹뷰
 *  수신(tauri.conf). 신규 유저는 공홈 제공 팩 또는 샘플로 시작. */
const USER_OWNER = "tenant:local";   // 본인 데이터 공간(샘플 sample:demo 와 분리)

export function renderIntake(el: HTMLElement) {
  el.innerHTML = `
    ${header("ingest", "Intake", COPY.ui.meta.intake,
             headerCta("choose", "upload", COPY.intake.idleCta))}
    <div class="empty" id="drop">
      ${icon("ingest", 30)}
      <h2>${COPY.intake.idleTitle}</h2>
      <p>${COPY.intake.idleBody}</p>
      <p class="hint">${COPY.sovereignty}</p>
      <p>
        <button class="btn btn-primary" id="pick">${COPY.intake.idleCta}</button>
        <button class="btn" id="try-sample">${COPY.ui.intakeUi.trySample}</button>
      </p>
    </div>
    <div class="mcp-note">
      <h3>${COPY.ui.intakeUi.mcpTitle}</h3>
      <p>${COPY.ui.intakeUi.mcpBody}</p>
    </div>
    <input type="file" id="file-input" accept=".vincpack,.json,application/json" multiple hidden />
    <div id="intake-status" role="status"></div>
  `;
  const drop = el.querySelector<HTMLElement>("#drop")!;
  const status = el.querySelector<HTMLElement>("#intake-status")!;
  const input = el.querySelector<HTMLInputElement>("#file-input")!;
  const ui = COPY.ui.intakeUi;

  const openPicker = () => input.click();
  el.querySelector("#choose")!.addEventListener("click", openPicker);
  el.querySelector("#pick")!.addEventListener("click", openPicker);
  drop.addEventListener("click", (e) => {
    if ((e.target as HTMLElement).closest("button")) return;   // 버튼은 자체 핸들러
    openPicker();
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

  // 파일 1개 적재 — 형식 감지(share pack: vinc_pack 래퍼 · bare fragment: nodes/edges).
  async function importOne(f: File): Promise<{ nodes: number; edges: number }> {
    const obj = JSON.parse(await f.text());
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

  async function importFiles(files: File[]) {
    status.innerHTML = `<p class="hint">${ui.importing}</p>`;
    let nodes = 0, edges = 0, ok = 0;
    const failed: string[] = [];
    let lastErr: any = null;
    for (const f of files) {
      try {
        const r = await importOne(f);
        nodes += r.nodes; edges += r.edges; ok++;
      } catch (e) {
        failed.push(f.name); lastErr = e;
      }
    }
    if (ok > 0) {
      cfg.owner = USER_OWNER;   // 본인 공간으로 전환해 방금 넣은 데이터를 본다
      status.innerHTML =
        `<p>${ui.imported(ok, nodes, edges)}</p>` +
        (failed.length ? `<p class="hint">${ui.someFailed(failed.join(", "))}</p>` : "") +
        `<a class="btn btn-primary" href="#atlas">${COPY.intake.successCta}</a>`;
    } else {
      status.innerHTML = `<div class="error-banner">${COPY.intake.error(failed.join(", ") || "the file")}</div>`
        + `<p class="hint" style="font-size:.72rem;word-break:break-all;margin-top:.4rem;opacity:.75">진단: ${(lastErr && lastErr.message) || lastErr} · ${sidecarDiag}</p>`;
    }
  }

  // D30 — 설치 직후 60초 가치: 동봉 샘플 팩을 sample:demo 스코프로 수입하고
  // 그 스코프로 전환해 Atlas 를 바로 체험. (Settings 의 Owner scope 로 본인 데이터 복귀.)
  el.querySelector("#try-sample")!.addEventListener("click", async () => {
    status.innerHTML = `<p class="hint">${ui.importingSample}</p>`;
    try {
      const res = await api.importPack(samplePack, "sample:demo");
      if (res.executed === false) throw new Error(res.error);
      cfg.owner = "sample:demo";
      status.innerHTML = `<p>${ui.sampleLoaded}</p>
        <a class="btn btn-primary" href="#atlas">${COPY.intake.successCta}</a>`;
    } catch (e: any) {
      status.innerHTML = `<div class="error-banner">${COPY.intake.error("the sample pack")}</div>`
        + `<p class="hint" style="font-size:.72rem;word-break:break-all;margin-top:.4rem;opacity:.75">진단: ${(e && e.message) || e} · ${sidecarDiag}</p>`;
    }
  });
}
