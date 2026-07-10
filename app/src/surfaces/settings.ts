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
    <hr style="border:none;border-top:1px solid hsl(var(--border))" />
    <label for="s-base">Sidecar</label>
    <input id="s-base" value="${cfg.base}" />
    <label for="s-token">Token</label>
    <p class="hint">${COPY.settings.token}</p>
    <input id="s-token" value="${cfg.token}" />
    <label for="s-owner">Owner scope</label>
    <input id="s-owner" value="${cfg.owner}" />
    <label>Theme</label>
    <p>
      ${["auto", "light", "dark"].map((t) => `
        <button class="chip${(localStorage.getItem("vinc.theme") || "auto") === t ? " active" : ""}"
                data-theme-opt="${t}">${t}</button>`).join(" ")}
    </p>
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
