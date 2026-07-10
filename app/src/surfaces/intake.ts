import { COPY } from "../copy";
import { header, headerCta } from "../components";
import { icon } from "../icons";
import { api, cfg } from "../api";
import samplePack from "../sample-pack.json";

/** Surface 1 — Intake (Figma 5:2 grammar · spec vinc.d.ingest v0.2).
 *  First-run setup lives in this surface's empty state (D15); the 설치 셋업
 *  frame (4:29)'s stepper survives as the inline preparing indicator. */
export function renderIntake(el: HTMLElement) {
  el.innerHTML = `
    ${header("ingest", "Intake", "Sources become records · /ingest/{engine}",
             headerCta("choose", "upload", "Choose sources"))}
    <div class="empty" id="drop">
      ${icon("ingest", 30)}
      <h2>${COPY.intake.idleTitle}</h2>
      <p>${COPY.intake.idleBody}</p>
      <p class="hint">${COPY.sovereignty}</p>
      <p><button class="btn" id="try-sample">Try a sample pack</button></p>
    </div>
    <div id="intake-status" role="status"></div>
  `;
  const drop = el.querySelector<HTMLElement>("#drop")!;
  const status = el.querySelector<HTMLElement>("#intake-status")!;
  drop.addEventListener("dragover", (e) => e.preventDefault());
  drop.addEventListener("drop", (e) => {
    e.preventDefault();
    status.innerHTML = `<p class="hint">Local ingest runs through the sidecar job queue —
      wire this drop to <code>POST /ingest/{engine}</code> when the Tauri file scope lands (P4).</p>`;
  });
  // D30 — 설치 직후 60초 가치: 동봉 샘플 팩을 sample:demo 스코프로 수입하고
  // 그 스코프로 전환해 Atlas 를 바로 체험. (스코프 삭제 op 는 P4 — Settings 의
  // Owner scope 를 되돌리면 본인 데이터로 복귀.)
  el.querySelector("#try-sample")!.addEventListener("click", async () => {
    status.innerHTML = `<p class="hint">Importing the sample…</p>`;
    try {
      const res = await api.importPack(samplePack, "sample:demo");
      if (res.executed === false) throw new Error(res.error);
      cfg.owner = "sample:demo";
      status.innerHTML = `<p>Sample in — 6 concepts across 2 domains. Owner scope switched to
        <code>sample:demo</code> (switch back in Settings).</p>
        <a class="btn btn-primary" href="#atlas">Open in Atlas</a>`;
    } catch {
      status.innerHTML = `<div class="error-banner">${COPY.intake.error("the sample pack")}</div>`;
    }
  });
  el.querySelector("#choose")!.addEventListener("click", () => {
    status.innerHTML = `<p class="hint">File dialogs arrive with the Tauri shell (P4).
      Until then, ingest from the CLI: <code>python -m api.cli ingest</code>.</p>`;
  });
}
