import { cfg } from "../api";
import { COPY } from "../copy";

/** Settings popover (spec: vinc.d.settings v0.1) — exactly three items: Lock (D7
 *  opt-in), MCP observation, Data. Plus dev-scaffold sidecar fields; developer
 *  config (embedding model, driver) stays env-only by design. */
export function renderSettings(el: HTMLElement) {
  el.innerHTML = `
    <h2>Settings</h2>
    <label>Lock</label><p class="hint">${COPY.settings.lock}</p>
    <label>MCP</label><p class="hint">${COPY.settings.mcp}</p>
    <label>Data</label><p class="hint">${COPY.settings.data}</p>
    <label for="s-owner">Workspace</label>
    <p class="hint">Which space you're browsing. Switch back after trying the sample.</p>
    <input id="s-owner" value="${cfg.owner}" />
    <label>Theme</label>
    <p><span class="seg">
      ${["dark", "light", "auto"].map((t) => `
        <button class="chip${(localStorage.getItem("vinc.theme") || "dark") === t ? " active" : ""}"
                data-theme-opt="${t}">${t}</button>`).join("")}
    </span></p>
    <details style="margin-top:var(--space-3,.75rem)">
      <summary class="hint" style="cursor:pointer">Advanced</summary>
      <label for="s-base">Engine address</label>
      <input id="s-base" value="${cfg.base}" />
      <label for="s-token">Connection secret</label>
      <p class="hint">Filled automatically by the app. Only change this if you run the engine yourself.</p>
      <input id="s-token" value="${cfg.token}" />
    </details>
  `;
  el.querySelectorAll<HTMLElement>("[data-theme-opt]").forEach((b) =>
    b.addEventListener("click", async () => {
      localStorage.setItem("vinc.theme", b.dataset.themeOpt!);
      const { applyTheme } = await import("../main");
      applyTheme();
      renderSettings(el);
    }));
  el.querySelector<HTMLInputElement>("#s-base")!.addEventListener("change", (e) =>
    (cfg.base = (e.target as HTMLInputElement).value));
  el.querySelector<HTMLInputElement>("#s-token")!.addEventListener("change", (e) =>
    (cfg.token = (e.target as HTMLInputElement).value));
  el.querySelector<HTMLInputElement>("#s-owner")!.addEventListener("change", (e) =>
    (cfg.owner = (e.target as HTMLInputElement).value));
}
