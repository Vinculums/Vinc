import { api, cfg } from "../api";
import { COPY } from "../copy";
import { header, headerCta } from "../components";
import { icon } from "../icons";
import { openImportSheet } from "../sheets";

/** Surface 5 — Packs (spec: vinc.d.packs v0.2, DEC D19).
 *  Context packs (/activate) + share packs (/share, custom scope) + import
 *  (/ingest/pack — idempotent, stamped with YOUR owner). */
export function renderPacks(el: HTMLElement) {
  el.innerHTML = `
    ${header("pack-export", "Packs", COPY.ui.meta.packs,
             headerCta("create", "pack-export", COPY.packs.idleCta))}
    <div style="display:grid;grid-template-columns:1fr 20rem;gap:var(--space-5,1.25rem)">
      <div>
        <div class="empty" id="packs-empty">
          ${icon("pack-export", 30)}
          <p>${COPY.packs.idle}</p>
          <p>
            <button class="btn" id="share">${COPY.packs.shareCta}</button>
            <label class="btn" for="import-file" style="cursor:pointer">${COPY.packs.importCta}</label>
            <input id="import-file" type="file" accept="application/json" hidden />
          </p>
          <p class="hint">
            <label>${COPY.ui.packsUi.share} · ${COPY.ui.packsUi.domain} <input id="share-domain" placeholder="all" style="width:8rem" /></label>
            <label> ${COPY.ui.packsUi.startFrom} <input id="share-seed" placeholder="(anywhere)" style="width:12rem" /></label>
            <label> ${COPY.ui.packsUi.reach} <input id="share-depth" type="number" min="1" max="4" value="2" style="width:3rem" /></label>
          </p>
        </div>
      </div>
      <aside id="packs-preview" style="background:hsl(var(--card));border:1px solid hsl(var(--border));
             border-radius:var(--radius);padding:var(--space-4,1rem)">
        <p class="hint" style="margin-top:0">${COPY.ui.packsUi.preview}</p>
        <p class="hint">${COPY.ui.packsUi.previewHint}</p>
      </aside>
    </div>
    <div id="packs-status" role="status"></div>`;
  const status = el.querySelector<HTMLElement>("#packs-status")!;
  const val = (id: string) => (el.querySelector(`#${id}`) as HTMLInputElement).value.trim();

  el.querySelector("#create")!.addEventListener("click", async () => {
    status.innerHTML = `<p class="hint">${COPY.packs.loading(0)}</p>`;
    try {
      const res = await api.activate(cfg.owner);
      status.innerHTML = `<p>${COPY.packs.success(res.episode_count ?? 0, res.out || "pack")}</p>`;
    } catch {
      status.innerHTML = `<div class="error-banner">${COPY.packs.error}</div>`;
    }
  });

  el.querySelector("#share")!.addEventListener("click", async () => {
    status.innerHTML = `<p class="hint">${COPY.packs.loading(0)}</p>`;
    try {
      const pack = await api.share(cfg.owner, {
        domain: val("share-domain") || undefined,
        seed: val("share-seed") || undefined,
        depth: Number(val("share-depth")) || 2,
      });
      const c = pack.manifest?.counts || {};
      // 팩을 파일로 저장(브라우저 다운로드 — Tauri 셸에서는 저장 다이얼로그로 대체)
      const blob = new Blob([JSON.stringify(pack, null, 2)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `vinc-share-${val("share-domain") || val("share-seed") || "all"}.json`;
      a.click();
      URL.revokeObjectURL(a.href);
      status.innerHTML = `<p>${COPY.packs.shareReady(c.concepts ?? 0, c.edges ?? 0)}</p>
        <p class="hint">${COPY.packs.shareNote}</p>`;
      const pv = el.querySelector<HTMLElement>("#packs-preview")!;
      pv.innerHTML = `<p class="hint" style="margin-top:0">${COPY.ui.packsUi.preview}</p>
        <h3 style="margin:.2rem 0">${a.download}</h3>
        <p class="hint">${c.concepts ?? 0} concepts · ${c.edges ?? 0} connections · ${c.records ?? 0} records</p>
        <p class="hint">${COPY.ui.packsUi.offline} ${COPY.packs.shareNote}</p>`;
    } catch {
      status.innerHTML = `<div class="error-banner">${COPY.packs.error}</div>`;
    }
  });

  el.querySelector<HTMLInputElement>("#import-file")!.addEventListener("change", async (e) => {
    const file = (e.target as HTMLInputElement).files?.[0];
    if (!file) return;
    try {
      openImportSheet(JSON.parse(await file.text()));   // B3 — 미리보기 시트 경유
    } catch {
      status.innerHTML = `<div class="error-banner">${COPY.packs.error}</div>`;
    }
  });
}
