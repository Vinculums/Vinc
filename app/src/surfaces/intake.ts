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
    ${header("ingest", "Intake", COPY.ui.meta.intake,
             headerCta("choose", "upload", COPY.intake.idleCta))}
    <div class="empty" id="drop">
      ${icon("ingest", 30)}
      <h2>${COPY.intake.idleTitle}</h2>
      <p>${COPY.intake.idleBody}</p>
      <p class="hint">${COPY.sovereignty}</p>
      <p><button class="btn" id="try-sample">${COPY.ui.intakeUi.trySample}</button></p>
    </div>
    <div id="intake-status" role="status"></div>
  `;
  const drop = el.querySelector<HTMLElement>("#drop")!;
  const status = el.querySelector<HTMLElement>("#intake-status")!;
  drop.addEventListener("dragover", (e) => e.preventDefault());
  drop.addEventListener("drop", (e) => {
    e.preventDefault();
    status.innerHTML = `<p class="hint">${COPY.ui.intakeUi.dropLater}</p>`;
  });
  // D30 — 설치 직후 60초 가치: 동봉 샘플 팩을 sample:demo 스코프로 수입하고
  // 그 스코프로 전환해 Atlas 를 바로 체험. (스코프 삭제 op 는 P4 — Settings 의
  // Owner scope 를 되돌리면 본인 데이터로 복귀.)
  el.querySelector("#try-sample")!.addEventListener("click", async () => {
    status.innerHTML = `<p class="hint">${COPY.ui.intakeUi.importingSample}</p>`;
    try {
      const res = await api.importPack(samplePack, "sample:demo");
      if (res.executed === false) throw new Error(res.error);
      cfg.owner = "sample:demo";
      status.innerHTML = `<p>${COPY.ui.intakeUi.sampleLoaded}</p>
        <a class="btn btn-primary" href="#atlas">${COPY.intake.successCta}</a>`;
    } catch {
      status.innerHTML = `<div class="error-banner">${COPY.intake.error("the sample pack")}</div>`;
    }
  });
  el.querySelector("#choose")!.addEventListener("click", () => {
    status.innerHTML = `<p class="hint">${COPY.ui.intakeUi.chooseLater}</p>`;
  });
}
