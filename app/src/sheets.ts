/** Share / Import sheets (Phase B — spec vinc.d.atlas-visual-language §5).
 *  The share sheet is the final editor: it shows everything that will leave
 *  (with per-node exclusion) and everything that won't (preference notice). */
import { api, cfg } from "./api";
import { toBytes } from "./packfile";
import { COPY } from "./copy";

function overlay(): HTMLElement {
  let el = document.getElementById("sheet-overlay");
  if (!el) {
    el = document.createElement("div");
    el.id = "sheet-overlay";
    el.style.cssText = "position:fixed;inset:0;z-index:60;background:hsl(var(--foreground)/.18);display:grid;place-items:start center;padding-top:8vh;overflow:auto";
    document.body.appendChild(el);
  }
  el.hidden = false;
  el.onclick = (e) => { if (e.target === el) el!.hidden = true; };
  return el;
}

export async function openShareSheet(scope: { ids?: string[]; seed?: string; domain?: string; depth?: number }) {
  const el = overlay();
  el.innerHTML = `<div class="palette-card" style="width:42rem"><p class="hint">${COPY.ui.sheet.preparing}</p></div>`;
  try {
    const pack = await api.share(cfg.owner, {
      ids: scope.ids?.join(","), seed: scope.seed, domain: scope.domain, depth: scope.depth,
    });
    const nodes: any[] = pack.fragment?.nodes || [];
    const c = pack.manifest?.counts || {};
    el.innerHTML = `
      <div class="palette-card" style="width:42rem" role="dialog" aria-label="Share this slice">
        <h2 style="margin:.2rem 0">${COPY.ui.sheet.shareTitle}</h2>
        <p class="hint">${COPY.ui.sheet.counts(c.concepts ?? 0, c.edges ?? 0, c.records ?? 0)}</p>
        <div style="max-height:16rem;overflow:auto;border:1px solid hsl(var(--border));border-radius:var(--radius);padding:.25rem .75rem;margin:.5rem 0">
          ${nodes.map((n, i) => `
            <label class="list-row" style="cursor:pointer">
              <input type="checkbox" checked data-i="${i}" />
              <span class="title">${n.title}</span><span class="meta">${n.domain || n.type}</span>
            </label>`).join("") || `<p class="hint">${COPY.ui.sheet.nothing}</p>`}
        </div>
        <p class="hint">${COPY.ui.sheet.keepNote}</p>
        <p><button class="btn btn-primary" id="sheet-save">${COPY.ui.sheet.save}</button>
           <button class="btn" id="sheet-cancel">${COPY.ui.common.cancel}</button></p>
      </div>`;
    el.querySelector("#sheet-cancel")!.addEventListener("click", () => { el.hidden = true; });
    el.querySelector("#sheet-save")!.addEventListener("click", () => {
      const excluded = new Set([...el.querySelectorAll<HTMLInputElement>("input[type=checkbox]")]
        .filter((cb) => !cb.checked).map((cb) => nodes[Number(cb.dataset.i)].id));
      const keep = nodes.filter((n) => !excluded.has(n.id));
      const keepIds = new Set(keep.map((n) => n.id));
      const edges = (pack.fragment?.edges || []).filter((e: any) => keepIds.has(e.from) && keepIds.has(e.to));
      const out = { ...pack, fragment: { nodes: keep, edges },
        manifest: { ...pack.manifest, counts: { ...c, concepts: keep.filter((n) => n.type === "concept").length, edges: edges.length } } };
      delete (out as any).executed;
      // D55 — 쓰기는 zip 컨테이너로. 원문(documents)을 실은 팩이 통짜 JSON 이면
      // 수 MB 로 붓고 문서 하나만 꺼낼 수도 없다(실측 1.6MB → 461KB, 28%).
      void toBytes(out).then((bytes) => {
        const blob = new Blob([bytes as BlobPart], { type: "application/zip" });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = `vinc-share-${scope.domain || scope.seed || "slice"}.vincpack`;
        a.click(); URL.revokeObjectURL(a.href);
        el.hidden = true;
      });
    });
  } catch {
    el.innerHTML = `<div class="palette-card"><div class="error-banner">${COPY.packs.error}</div></div>`;
  }
}

export function openImportSheet(pack: any, onDone?: (res: any) => void) {
  const el = overlay();
  const m = pack?.manifest || {}; const c = m.counts || {};
  const n = (pack?.fragment?.nodes || []).length;
  el.innerHTML = `
    <div class="palette-card" style="width:36rem" role="dialog" aria-label="Import a pack">
      <h2 style="margin:.2rem 0">${COPY.ui.sheet.importTitle}</h2>
      <p class="hint">${COPY.ui.sheet.createdScope(m.created || "?", JSON.stringify(m.scope || {}))}</p>
      <p>${COPY.ui.sheet.updateNote(c.concepts ?? n, c.edges ?? 0, c.records ?? 0)}</p>
      <p><button class="btn btn-primary" id="sheet-import">${COPY.ui.sheet.importMine}</button>
         <button class="btn" id="sheet-cancel">${COPY.ui.common.cancel}</button></p>
      <div id="sheet-result" role="status"></div>
    </div>`;
  el.querySelector("#sheet-cancel")!.addEventListener("click", () => { el.hidden = true; });
  el.querySelector("#sheet-import")!.addEventListener("click", async () => {
    const out = el.querySelector<HTMLElement>("#sheet-result")!;
    out.innerHTML = `<p class="hint">${COPY.packs.importing}</p>`;
    try {
      const res = await api.importPack(pack, cfg.owner);
      if (res.executed === false) throw new Error(res.error);
      const mm = res.merged?.nodes || {};
      out.innerHTML = `<p>${COPY.packs.imported(mm.created ?? 0, mm.matched ?? 0)}</p>
        <a class="btn" href="#atlas" onclick="document.getElementById('sheet-overlay').hidden=true">${COPY.ui.sheet.viewAtlas}</a>`;
      onDone?.(res);
    } catch {
      out.innerHTML = `<div class="error-banner">${COPY.packs.error}</div>`;
    }
  });
}
