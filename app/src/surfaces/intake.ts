import { COPY } from "../copy";
import { header, headerCta } from "../components";
import { icon } from "../icons";

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
  el.querySelector("#choose")!.addEventListener("click", () => {
    status.innerHTML = `<p class="hint">File dialogs arrive with the Tauri shell (P4).
      Until then, ingest from the CLI: <code>python -m api.cli ingest</code>.</p>`;
  });
}
