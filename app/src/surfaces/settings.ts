import { cfg } from "../api";
import { COPY } from "../copy";

/** Settings popover (spec: vinc.d.settings v0.1) — exactly three items: Lock (D7
 *  opt-in), MCP observation, Data. Plus dev-scaffold sidecar fields; developer
 *  config (embedding model, driver) stays env-only by design. */
export function renderSettings(el: HTMLElement) {
  const U = COPY.ui.settingsUi;
  el.innerHTML = `
    <h2>Settings</h2>
    <label>${U.lock}</label><p class="hint">${COPY.settings.lock}</p>
    <label>${U.mcp}</label><p class="hint">${COPY.settings.mcp}</p>
    <label>${U.data}</label><p class="hint">${COPY.settings.data}</p>
    <label for="s-owner">${U.workspace}</label>
    <p class="hint">${U.workspaceHint}</p>
    <input id="s-owner" value="${cfg.owner}" />
    <label>${U.theme}</label>
    <p><span class="seg">
      ${["dark", "light", "auto"].map((t) => `
        <button class="chip${(localStorage.getItem("vinc.theme") || "dark") === t ? " active" : ""}"
                data-theme-opt="${t}">${t}</button>`).join("")}
    </span></p>
    <label>${U.language}</label>
    <p><span class="seg">
      ${(["en", "ko"] as const).map((l) => `
        <button class="chip${cfg.lang === l ? " active" : ""}"
                data-lang-opt="${l}">${l === "en" ? "English" : "한국어"}</button>`).join("")}
    </span></p>
    <details style="margin-top:var(--space-3,.75rem)">
      <summary class="hint" style="cursor:pointer">${U.advanced}</summary>
      <label for="s-base">${U.engine}</label>
      <input id="s-base" value="${cfg.base}" />
      <label for="s-token">${U.secret}</label>
      <p class="hint">${U.secretHint}</p>
      <input id="s-token" value="${cfg.token}" />
    </details>
  `;
  // D42 — 언어 전환: 전 표면 재렌더가 필요하므로 reload(팔레트·타이틀 포함)
  el.querySelectorAll<HTMLElement>("[data-lang-opt]").forEach((b) =>
    b.addEventListener("click", () => {
      cfg.lang = b.dataset.langOpt as "en" | "ko";
      location.reload();
    }));
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
