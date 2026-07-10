import { api, cfg } from "../api";
import { COPY } from "../copy";

/** Surface 5 — Packs (spec: vinc.d.packs v0.1). learner section follows D6 (off). */
export function renderPacks(el: HTMLElement) {
  el.innerHTML = `
    <h1>Packs</h1>
    <div class="empty" id="packs-empty">
      <p>${COPY.packs.idle}</p>
      <button class="btn btn-primary" id="create">${COPY.packs.idleCta}</button>
    </div>
    <div id="packs-status" role="status"></div>`;
  const status = el.querySelector<HTMLElement>("#packs-status")!;
  el.querySelector("#create")!.addEventListener("click", async () => {
    status.innerHTML = `<p class="hint">${COPY.packs.loading(0)}</p>`;
    try {
      const res = await api.activate(cfg.owner);
      status.innerHTML = `<p>${COPY.packs.success(res.episode_count ?? 0, res.out || "pack")}</p>`;
    } catch {
      status.innerHTML = `<div class="error-banner">${COPY.packs.error}</div>`;
    }
  });
}
