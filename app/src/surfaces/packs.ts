import { api, cfg, community, WEB } from "../api";
import { COPY } from "../copy";
import { header } from "../components";
import { icon } from "../icons";
import { openImportSheet } from "../sheets";
import { renderIntake } from "./intake";

/** Surface — Packs (LNB 통합: Intake + Packs). 세그먼트 탭 두 개:
 *  · 가져오기 = Intake 드롭존 3-state(헤더리스 재사용, tenant:local 적재)
 *  · 공유·내보내기 = context 팩(/activate) + share 팩(/share) + 커뮤니티 게시 + import(sheet).
 *  탭 상태 = 해시 꼬리(#packs/import | #packs/share, 기본 import). 탭 클릭은 해시만
 *  바꾸고 route()가 재렌더 → 딥링크·새로고침 시 탭 복원. */
export function renderPacks(el: HTMLElement) {
  const U = COPY.ui.packsUi;
  const tail = location.hash.split("/")[1];
  const tab: "import" | "share" = tail === "share" ? "share" : "import";

  el.innerHTML = `
    ${header("pack-export", "Packs", COPY.ui.meta.packs)}
    <span class="seg" id="packs-tabs" role="tablist">
      <button class="chip${tab === "import" ? " active" : ""}" data-tab="import" role="tab">${U.importTab}</button>
      <button class="chip${tab === "share" ? " active" : ""}" data-tab="share" role="tab">${U.shareTab}</button>
    </span>
    <div id="packs-tab-body" style="margin-top:var(--space-4,1rem)"></div>`;

  el.querySelectorAll<HTMLElement>("#packs-tabs [data-tab]").forEach((b) =>
    b.addEventListener("click", () => {
      const t = b.dataset.tab as "import" | "share";
      if (t === tab) return;
      location.hash = `#packs/${t}`;   // hashchange → route() → 재렌더(해시 꼬리로 탭 결정)
    }));

  const tabBody = el.querySelector<HTMLElement>("#packs-tab-body")!;
  if (tab === "import") renderIntake(tabBody, { withHeader: false });
  else renderShare(tabBody);
}

/** 공유·내보내기 탭 — 구 Packs 본문(create/share/publish/import) 그대로. */
function renderShare(host: HTMLElement) {
  host.innerHTML = `
    <div style="display:grid;grid-template-columns:1fr 20rem;gap:var(--space-5,1.25rem)">
      <div>
        <div class="empty" id="packs-empty">
          ${icon("pack-export", 30)}
          <p>${COPY.packs.idle}</p>
          <p>
            <button class="btn btn-primary" id="create">${COPY.packs.idleCta}</button>
            <button class="btn" id="share">${COPY.packs.shareCta}</button>
            ${WEB ? `<button class="btn" id="publish">${COPY.ui.packsUi.publishCta}</button>` : ""}
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
  const status = host.querySelector<HTMLElement>("#packs-status")!;
  const val = (id: string) => (host.querySelector(`#${id}`) as HTMLInputElement).value.trim();

  host.querySelector("#create")!.addEventListener("click", async () => {
    status.innerHTML = `<p class="hint">${COPY.packs.loading(0)}</p>`;
    try {
      const res = await api.activate(cfg.owner);
      status.innerHTML = `<p>${COPY.packs.success(res.episode_count ?? 0, res.out || "pack")}</p>`;
    } catch {
      status.innerHTML = `<div class="error-banner">${COPY.packs.error}</div>`;
    }
  });

  host.querySelector("#share")!.addEventListener("click", async () => {
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
      const pv = host.querySelector<HTMLElement>("#packs-preview")!;
      pv.innerHTML = `<p class="hint" style="margin-top:0">${COPY.ui.packsUi.preview}</p>
        <h3 style="margin:.2rem 0">${a.download}</h3>
        <p class="hint">${c.concepts ?? 0} concepts · ${c.edges ?? 0} connections · ${c.records ?? 0} records</p>
        <p class="hint">${COPY.ui.packsUi.offline} ${COPY.packs.shareNote}</p>`;
    } catch {
      status.innerHTML = `<div class="error-banner">${COPY.packs.error}</div>`;
    }
  });

  // 웹 전용 — 도메인 슬라이스를 공개 커뮤니티 갤러리에 게시. 데이터가 공개되므로
  // 명시 동의를 받는다. owner 는 세션이 강제하니 cfg.owner 는 무시된다(허브가 sub 로).
  host.querySelector("#publish")?.addEventListener("click", async () => {
    const P = COPY.ui.packsUi;
    status.innerHTML = `<p class="hint">${COPY.packs.loading(0)}</p>`;
    let pack: any;
    try {
      pack = await api.share(cfg.owner, {
        domain: val("share-domain") || undefined,
        seed: val("share-seed") || undefined,
        depth: Number(val("share-depth")) || 2,
      });
    } catch { status.innerHTML = `<div class="error-banner">${COPY.packs.error}</div>`; return; }
    const c = pack.manifest?.counts || {};
    if (!(c.concepts > 0)) { status.innerHTML = `<p class="hint">${P.publishEmpty}</p>`; return; }
    status.innerHTML = `
      <div style="max-width:26rem;border:1px solid hsl(var(--border));border-radius:var(--radius);
                  padding:var(--space-4,1rem);background:hsl(var(--card))">
        <h3 style="margin:.1rem 0 .3rem">${P.publishTitle}</h3>
        <p class="hint">${P.publishScope(c.concepts ?? 0, c.edges ?? 0)}</p>
        <input id="pub-title" placeholder="${P.publishName}" style="width:100%;margin:.3rem 0" />
        <textarea id="pub-desc" placeholder="${P.publishDesc}" rows="2" style="width:100%;margin:.3rem 0"></textarea>
        <label class="hint" style="display:flex;gap:.4rem;align-items:flex-start;margin:.3rem 0">
          <input type="checkbox" id="pub-consent" /> <span>${P.publishConsent}</span></label>
        <p><button class="btn btn-primary" id="pub-go">${P.publishGo}</button></p>
        <div id="pub-msg" class="hint"></div>
      </div>`;
    // 제목 기본값은 JS 로 주입(HTML 속성 인젝션 회피 — 사용자 입력 도메인).
    const titleInput = host.querySelector<HTMLInputElement>("#pub-title")!;
    titleInput.value = val("share-domain") || "";
    const msg = host.querySelector<HTMLElement>("#pub-msg")!;
    host.querySelector("#pub-go")!.addEventListener("click", async () => {
      if (!host.querySelector<HTMLInputElement>("#pub-consent")!.checked) { msg.textContent = P.publishNeedConsent; return; }
      const title = titleInput.value.trim();
      if (!title) { msg.textContent = P.publishNeedName; return; }
      const desc = host.querySelector<HTMLTextAreaElement>("#pub-desc")!.value.trim();
      msg.textContent = COPY.packs.loading(0);
      try {
        await community.publish(title, desc, pack);
        status.innerHTML = `<p>${P.publishDone}</p>
          <p><a class="btn btn-primary" href="/community" target="_blank" rel="noopener">${P.publishView}</a></p>`;
      } catch {
        msg.innerHTML = `<span class="error-banner">${COPY.packs.error}</span>`;
      }
    });
  });

  host.querySelector<HTMLInputElement>("#import-file")!.addEventListener("change", async (e) => {
    const file = (e.target as HTMLInputElement).files?.[0];
    if (!file) return;
    try {
      openImportSheet(JSON.parse(await file.text()));   // B3 — 미리보기 시트 경유
    } catch {
      status.innerHTML = `<div class="error-banner">${COPY.packs.error}</div>`;
    }
  });
}
