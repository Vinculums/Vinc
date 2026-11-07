import { accountMode, bindTeamCredential, cfg, hasTeamCredential } from "./api";
import { openBrowser } from "./account-connect";
import { COPY } from "./copy";
import { esc, openSheet } from "./components";

let opened: { team: string; result: Promise<boolean> } | null = null;
/** An explicit team key is required before switching a desktop account graph. */
export function ensureTeamCredential(team: string, name = ""): Promise<boolean> {
  if (!team || !accountMode()) return Promise.resolve(true);
  if (opened) return opened.team === team ? opened.result : Promise.resolve(false);
  const result = (async () => {
    if (await hasTeamCredential(team).catch(() => false)) return true;
    const c = COPY.teamKey;
    return new Promise<boolean>(resolve => {
      let saved = false;
      const sheet = openSheet({ title: c.title, subtitle: name,
        onClose: () => resolve(saved) });
      sheet.body.innerHTML = `<p>${esc(c.hint)}</p><p><button class="btn" id="team-key-manage" type="button">${esc(c.manage)}</button></p>
        <label for="team-key-input">${esc(c.label)}</label>
        <input id="team-key-input" type="password" autocomplete="off" spellcheck="false">
        <p class="hint" role="status" id="team-key-status"></p>`;
      sheet.foot.innerHTML = `<button type="button" class="btn" id="team-key-cancel">${esc(c.cancel)}</button>
        <button type="button" class="btn primary" id="team-key-save">${esc(c.save)}</button>`;
      const input = sheet.body.querySelector<HTMLInputElement>("#team-key-input")!;
      const status = sheet.body.querySelector<HTMLElement>("#team-key-status")!;
      const save = sheet.foot.querySelector<HTMLButtonElement>("#team-key-save")!;
      sheet.body.querySelector("#team-key-manage")!.addEventListener("click", () => { void openBrowser("https://vincs.io/account/#keys"); });
      sheet.foot.querySelector("#team-key-cancel")!.addEventListener("click", sheet.close);
      save.addEventListener("click", async () => {
        if (save.disabled || !input.value.trim()) return;
        save.disabled = true; input.disabled = true; status.textContent = c.checking;
        try {
          await bindTeamCredential(team, input.value.trim());
          input.value = ""; saved = true; sheet.close();
        } catch (error: any) {
          if (sheet.closed) return;
          status.textContent = error?.message === "team_key_vault_unavailable" ? c.vaultUnavailable
            : error?.message === "credential_identity_unavailable" ? c.unavailable : c.rejected;
          save.disabled = false; input.disabled = false;
        }
      });
      input.focus();
    });
  })();
  opened = { team, result };
  void result.finally(() => { if (opened?.result === result) opened = null; });
  return result;
}

let restoring: Promise<void> | null = null;
export function restoreTeamCredential(): Promise<void> {
  if (restoring) return restoring;
  const promise = restore();
  restoring = promise;
  void promise.finally(() => { if (restoring === promise) restoring = null; });
  return promise;
}
async function restore(): Promise<void> {
  const team = cfg.team;
  if (!team) return;
  const ready = await ensureTeamCredential(team);
  if (cfg.team !== team) return;
  if (!ready) cfg.team = "";
  location.reload();
}
