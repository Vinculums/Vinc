import { COPY } from "../copy";

/** Surface 1 — Intake (spec: vinc.d.ingest v0.2).
 *  First-run setup is this surface's empty state; no separate onboarding (D15).
 *  File pick will move to Tauri IPC dialog when the shell lands (P4). */
export function renderIntake(el: HTMLElement) {
  el.innerHTML = `
    <h1>Intake</h1>
    <div class="empty" id="drop">
      <h2>${COPY.intake.idleTitle}</h2>
      <p>${COPY.intake.idleBody}</p>
      <p class="hint">${COPY.sovereignty}</p>
      <button class="btn btn-primary" id="choose">${COPY.intake.idleCta}</button>
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
