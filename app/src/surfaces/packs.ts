import { AccountError, api, cfg, community, teamsApi, WEB } from "../api";
import { COPY } from "../copy";
import { header } from "../components";
import { openShareSheet } from "../sheets";
import { renderIntake } from "./intake";

/** Surface — Packs. 토글 제거, 단일 페이지에 의도별 구분:
 *  · 가져오기(Import)  — 팩 파일을 내 그래프에 병합(intake 3-state 재사용)
 *  · 공유하기(Share)   — 주제 슬라이스를 타인에게: 주제 선택 → openShareSheet(미리보기·저장)
 *  · 커뮤니티 게시     — 주제를 공개 갤러리에(웹 전용, consent)
 *  · 전체 백업         — 내 전체 컨텍스트 팩(/activate, 이관용) — 하단 보조 링크
 *  세 액션은 목적이 다르다(병합 / 타인 전달 / 무손실 이관) → 섹션으로 분리. */
export function renderPacks(el: HTMLElement) {
  const U = COPY.ui.packsUi;

  el.innerHTML = `
    ${header("pack-export", "Packs", COPY.ui.meta.packs)}

    <section class="pack-card" id="pack-import">
      <h3 class="pack-h">${U.secImport}</h3>
      <p class="hint pack-desc">${U.secImportDesc}</p>
      <div id="pack-import-host"></div>
    </section>

    ${WEB && cfg.team ? `
    <section class="pack-card" id="pack-pull">
      <h3 class="pack-h">${U.secPull}</h3>
      <p class="hint pack-desc">${U.secPullDesc}</p>
      <div class="pack-row">
        <label class="pack-field">${U.topic}
          <select id="pull-topic"><option value="">${U.allTopics}</option></select>
        </label>
        <button class="btn" id="pull-go">${U.pullCta}</button>
      </div>
    </section>` : ""}

    <section class="pack-card" id="pack-share">
      <h3 class="pack-h">${U.secShare}</h3>
      <p class="hint pack-desc">${U.secShareDesc}</p>
      <div class="pack-row">
        <label class="pack-field">${U.topic}
          <select id="share-topic"><option value="">${U.allTopics}</option></select>
        </label>
        <button class="btn btn-primary" id="share-go">${U.previewSave}</button>
      </div>
      <p class="pack-note"><a class="pack-arealink" href="#view">${U.shareAreaHint}</a></p>
      <p class="hint pack-note">${COPY.packs.shareNote}</p>
    </section>

    ${WEB ? `
    <section class="pack-card" id="pack-community">
      <h3 class="pack-h">${U.secCommunity}</h3>
      <p class="hint pack-desc">${U.secCommunityDesc}</p>
      <div class="pack-row">
        <label class="pack-field">${U.topic}
          <select id="pub-topic"><option value="">${U.allTopics}</option></select>
        </label>
        <button class="btn" id="pub-open">${U.publishCta}</button>
      </div>
    </section>` : ""}

    <p class="pack-backup">
      <button class="pack-linkbtn" id="pack-activate">${U.backupLink}</button>
      <span class="hint">${U.backupDesc}</span>
    </p>
    <div id="packs-status" role="status"></div>`;

  // 가져오기 — intake 3-state 헤더리스 재사용.
  renderIntake(el.querySelector<HTMLElement>("#pack-import-host")!, { withHeader: false });

  const status = el.querySelector<HTMLElement>("#packs-status")!;

  // 주제 <select> 채우기 — main.ts loadRailDomains 와 같은 방어(배열/{domains}, 실패 시 '전체'만).
  async function topicOptions(scope?: Parameters<typeof api.domains>[1]) {
    const res: any = await api.domains(cfg.owner, scope);
    const rows: Array<{ domain: string }> = Array.isArray(res) ? res : (res?.domains || []);
    return rows.map((r) => {
      const d = String(r.domain ?? "");
      const safe = d.replace(/[<>&"]/g, (m) => (
        { "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;" }[m] as string));
      return `<option value="${safe}">${safe}</option>`;
    }).join("");
  }
  async function fillTopics() {
    try {
      const opts = await topicOptions();
      if (opts) {
        el.querySelectorAll<HTMLSelectElement>("#share-topic, #pub-topic").forEach((sel) =>
          sel.insertAdjacentHTML("beforeend", opts));
      }
    } catch { /* 사이드카 미가동 — '전체'만 유지 */ }
    // 불러오기 목록은 **개인** 주제다. 현재 스페이스가 팀이므로 명시적으로 개인을 읽는다.
    try {
      const sel = el.querySelector<HTMLSelectElement>("#pull-topic");
      if (!sel) return;
      const opts = await topicOptions({ team: null });
      if (opts) sel.insertAdjacentHTML("beforeend", opts);
    } catch { /* 개인 주제를 못 읽으면 '전체'만 유지 */ }
  }
  void fillTopics();

  // 스페이스 **전환**은 설정 → 계정에 있다(전역 상태라 표면 하나에 두지 않는다).
  // 여기서는 완료 문구에 쓸 팀 이름만 확인한다 — 못 읽어도 일반 명칭으로 진행한다.
  let teamName = "";
  if (cfg.team) {
    void teamsApi.list().then((rows) => {
      teamName = rows.find((t) => t.id === cfg.team)?.name || "";
    });
  }

  // 개인 → 팀 불러오기. 목적지가 정해져 있으므로 목적지 고르기를 건너뛴다.
  el.querySelector("#pull-go")?.addEventListener("click", () => {
    const domain = (el.querySelector("#pull-topic") as HTMLSelectElement).value || undefined;
    // 이름을 못 읽었으면 intoLabel 을 비워 시트가 일반 명칭("팀 스페이스")을 쓰게 둔다.
    void openShareSheet({ domain }, {
      from: { team: null }, into: { team: cfg.team }, intoLabel: teamName || undefined,
    });
  });

  // 공유하기 — 주제 → 미리보기·저장 시트(포함 노드 목록·개별 제외·저장).
  el.querySelector("#share-go")!.addEventListener("click", () => {
    const domain = (el.querySelector("#share-topic") as HTMLSelectElement).value || undefined;
    void openShareSheet({ domain });
  });

  // 커뮤니티 게시(웹) — 스코프 = 주제 select. 데이터가 공개되므로 consent.
  el.querySelector("#pub-open")?.addEventListener("click", async () => {
    const domain = (el.querySelector("#pub-topic") as HTMLSelectElement).value || undefined;
    status.innerHTML = `<p class="hint">${COPY.packs.loading(0)}</p>`;
    let pack: any;
    try {
      pack = await api.share(cfg.owner, { domain, depth: 2 });
    } catch { status.innerHTML = `<div class="error-banner">${COPY.packs.error}</div>`; return; }
    const c = pack.manifest?.counts || {};
    if (!(c.concepts > 0)) { status.innerHTML = `<p class="hint">${U.publishEmpty}</p>`; return; }
    status.innerHTML = `
      <div class="pack-card" style="max-width:26rem">
        <h3 class="pack-h" style="margin:.1rem 0 .3rem">${U.publishTitle}</h3>
        <p class="hint">${U.publishScope(c.concepts ?? 0, c.edges ?? 0)}</p>
        <input id="pub-title" placeholder="${U.publishName}" style="width:100%;margin:.3rem 0" />
        <textarea id="pub-desc" placeholder="${U.publishDesc}" rows="2" maxlength="500" style="width:100%;margin:.3rem 0"></textarea>
        <label class="hint" style="display:flex;gap:.4rem;align-items:flex-start;margin:.3rem 0">
          <input type="checkbox" id="pub-consent" /> <span>${U.publishConsent}</span></label>
        <p><button class="btn btn-primary" id="pub-go">${U.publishGo}</button></p>
        <div id="pub-msg" class="hint"></div>
      </div>`;
    // 제목 기본값은 JS 로 주입(HTML 속성 인젝션 회피 — 사용자 입력 도메인).
    const titleInput = el.querySelector<HTMLInputElement>("#pub-title")!;
    titleInput.value = domain || "";
    const msg = el.querySelector<HTMLElement>("#pub-msg")!;
    el.querySelector("#pub-go")!.addEventListener("click", async () => {
      if (!el.querySelector<HTMLInputElement>("#pub-consent")!.checked) { msg.textContent = U.publishNeedConsent; return; }
      const title = titleInput.value.trim();
      if (!title) { msg.textContent = U.publishNeedName; return; }
      const desc = el.querySelector<HTMLTextAreaElement>("#pub-desc")!.value.trim();
      msg.textContent = COPY.packs.loading(0);
      try {
        await community.publish(title, desc, pack);
        status.innerHTML = `<p>${U.publishDone}</p>
          <p><a class="btn btn-primary" href="/community" target="_blank" rel="noopener">${U.publishView}</a></p>`;
      } catch (e) {
        // 게시는 공개 핸들이 붙는 유일한 행위라 서버가 username 을 요구한다.
        // 이 경우만 갈라 안내한다 — 일반 오류로 뭉개면 "왜 안 되는지" 알 길이 없다.
        // 시트는 그대로 두어(status 미교체) 설정 후 바로 다시 누를 수 있게 한다.
        if (e instanceof AccountError && e.code === "username_required") {
          msg.innerHTML = `<span class="hint">${U.publishNeedHandle}</span> `
            + `<a href="/account/" target="_blank" rel="noopener">${U.publishSetHandle}</a>`;
          return;
        }
        msg.innerHTML = `<span class="error-banner">${COPY.packs.error}</span>`;
      }
    });
  });

  // 전체 백업 — 내 전체 컨텍스트 팩(/activate, 이관용).
  el.querySelector("#pack-activate")!.addEventListener("click", async () => {
    status.innerHTML = `<p class="hint">${COPY.packs.loading(0)}</p>`;
    try {
      const res = await api.activate(cfg.owner);
      status.innerHTML = `<p>${COPY.packs.success(res.episode_count ?? 0, res.out || "pack")}</p>`;
    } catch {
      status.innerHTML = `<div class="error-banner">${COPY.packs.error}</div>`;
    }
  });
}
