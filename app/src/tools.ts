import { api, canWriteHere, cfg, teamsApi } from "./api";
import { COPY } from "./copy";
import { esc, sheetActions, sheetProgress, type SheetHandle } from "./components";
import { renderIntake } from "./surfaces/intake";
import { viewHooks } from "./topbar";

/** Tools (DEC D71) — 설정의 가져오기 · 공유 두 분류가 이 모듈을 심는다.
 *
 *  분류 안에 탭을 두지 않는다(중첩 금지): 레일에서 고르고, 본문은 그 갈래의 일만 한다.
 *  시트 규격(D63)은 그대로다 — 머리·몸·발, 그리고 Esc·✕·배경으로 닫힘.
 *
 *  새 문서 쓰기는 여기 없다. 홈의 `새 문서` 가 같은 편집기를 넓은 자리에서 연다.
 *
 *  `#packs` 표면이 하던 일(가져오기 · 공유 · 전체 백업 · 다른 스페이스에서 불러오기)은
 *  전부 이 두 탭에 산다. 커뮤니티 게시는 공유가 목적지를 묻는 자리
 *  (`sheets.chooseDestination`)에 있다. */

type Tab = "import" | "share";

/** 사이드카 쓰기 큐는 단일 워커라 앞선 잡이 오래 걸리면 뒤가 함께 선다. 화면이
 *  "처리 중"에 하염없이 머무르면 사용자는 무엇이 됐고 무엇이 안 됐는지 알 길이
 *  없으므로, 기다림에 한계를 두고 **실패로 정직하게 내려** 재시도 손잡이를 준다.
 *
 *  (이 가드는 원래 "문서 저장 직후 쓰기 큐가 영구히 잠기는" 결함을 덮으려고 넣었다.
 *  그 원인은 서버에서 고쳤다 — 잡이 던진 SystemExit 이 워커 스레드를 죽이고 있었고,
 *  이제 워커는 어떤 잡으로도 죽지 않으며 run_sync 자체가 유한 상한을 갖는다. 그래도
 *  이 클라이언트 상한은 남긴다: 정말 오래 걸리는 잡에서도 화면이 굳지 않게 한다.) */
const OP_TIMEOUT = 12_000;
function withTimeout<T>(p: Promise<T>, ms = OP_TIMEOUT): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const t = window.setTimeout(() => reject(new Error("timeout")), ms);
    p.then((v) => { window.clearTimeout(t); resolve(v); },
           (e) => { window.clearTimeout(t); reject(e); });
  });
}

export function openTools(tab: Tab = "import") {
  void import("./surfaces/settings").then((m) => m.openSettings("data", tab));
}

/** 설정 시트의 가져오기·공유 분류 안에 그린다. 시트를 새로 열지 않는다.
 *
 *  두 갈래는 설정의 분류 둘이다(탭 줄이 없다). 설명 줄은 분류 머리가 진다. 발(`host.foot`)은
 *  설정이 넘긴 본문 칸의 발이라, 공유의 보내기·취소가 분류 레일 밑으로 번지지 않는다. */
export function mountTools(host: SheetHandle, tab: Tab = "import"): () => void {
  const pane = host.body;
  host.foot.innerHTML = "";
  if (tab === "share") { void renderShare(pane, host); return () => {}; }
  // 가져오기는 쓰기다. 읽기만 되는 공간(커뮤니티, 팀 열람자)에서는 드롭존을 열지 않고
  // 안내만 보인다. `canWriteHere` 가 실패하면 연다: 막는 것은 서버다.
  void canWriteHere().catch(() => true).then((ok) => {
    if (!pane.isConnected) return;
    if (!ok) { pane.innerHTML = `<p class="hint">${esc(COPY.ui.settingsUi.readOnlySpace)} ${esc(COPY.ui.settingsUi.readOnlyShareStill)}</p>`; return; }
    renderImport(pane, host);
  });
  return () => {};
}

// ── 가져오기 ────────────────────────────────────────────────────────────────
// 구 `#packs` 의 가져오기 칸이 그대로 들어왔다(P15) — 그 칸은 이미 `renderIntake` 를
// 헤더리스로 품고 있었으므로 옮긴 것은 자리뿐이다.
//
// ⚠ 여기를 다시 `파일 고르기` 버튼 한 줄로 되돌리지 말 것. 그러면 **끌어다 놓기 ·
// 샘플 팩 · 여러 파일 일괄 적재**가 앱에서 통째로 사라진다: `renderIntake` 를 부르는
// 곳이 이제 여기 하나뿐이고, View 의 빈 상태 초대(empty-invite)는 그래프가 **빌 때만**
// 뜬다. 발은 비운다 — 드롭존이 자기 CTA 를 갖고 있다(`draw` 가 이미 비워 두었다).
function renderImport(pane: HTMLElement, _sheet: SheetHandle) {
  renderIntake(pane, { withHeader: false, withNewDoc: false });
}

// ── 공유 ────────────────────────────────────────────────────────────────────
// 스코프 피커가 새로 붙는다. 여태 공유는 View 훅에 매여 있어 "지금 보고 있는 곳"
// 하나뿐이었는데, Tools 는 어느 표면에서나 열리므로 범위를 물어야 한다.
//
// P15 로 **소스 스페이스**가 하나 더 붙었다. 와이어 계약은 이미 이것을 담고 있었다:
// `Scope` 의 `team` 은 임의의 팀 id 이고 `call` 이 그대로 실어 보낸다. 넓힌 것은 고르는
// 화면뿐이다(구 Packs 는 `from: {team: null}` 로 개인 하나만 못박아 두고 있었다).
//
// ⚠ 이 칸은 `teamsApi.list()` 가 비면 빠진다. 데스크탑 로컬 모드는 팀이 없어 비고, 계정 모드는
// `/spaces` 로 팀을 받고 `accountCall` 이 고른 팀을 싣는다. 팀 목록과 팀 호출은 같은 모드를
// 따라야 한다: 한쪽만 바뀌면 고를 수 있는데 안 먹는 칸이 된다.
//
// ⚠ 목록에 띄우는 것이 **접근을 주지 않는다.** 권한은 계정이 정하고, 활동 구성원이
// 아닌 스페이스는 존재하지 않는 것과 같은 답을 준다. 그래서 활동 중이 아닌 소속은
// 지우지 않고 **고를 수 없게** 두어, "안 된다"와 "안 보인다"가 구별되게 한다.
async function renderShare(pane: HTMLElement, sheet: SheetHandle, srcTeam?: string) {
  const T = COPY.ui.tools;
  const S = COPY.ui.settingsUi;
  const P = COPY.ui.packsUi;
  const here = viewHooks();
  // 안 고르면 지금 있는 스페이스에서 뜬다(기존 동작).
  const src = srcTeam ?? cfg.team;
  const sameSpace = src === cfg.team;
  // 이 대기는 **버튼이 하나도 없는 구간**을 만든다(팀 목록 · 주제 목록 두 요청).
  // 그래서 한 줄짜리 문구가 아니라 공유 동선의 한 벌을 쓴다. 불확정인 이유는
  // `sheetProgress` 주석 3번 — 셀 수 있는 단계가 둘뿐이라 세면 깜빡임이 된다.
  pane.innerHTML = sheetProgress(COPY.ui.common.loading);
  // ⚠ 이 둘은 **반드시 끝나야 한다.** 불확정 막대는 끝을 그리지 않으므로, 끝나지 않는
  // 실패를 `살아 있음`으로 그린다 — 여기서 던지면 호출부가 `void` 라 잡을 곳도 없고
  // (unhandled rejection) 막대만 영원히 돈다. 그래서 위의 `withTimeout` 을 씌우고 둘 다
  // 못 읽어도 화면은 세운다: 팀을 못 읽으면 소스 칸이 없고(개인에서 뜬다), 주제를 못
  // 읽으면 `주제로` 갈래가 꺼진다. 둘 다 화면이 이미 표현할 줄 아는 상태다.
  let teams: Awaited<ReturnType<typeof teamsApi.list>> = [];
  try { teams = await withTimeout(teamsApi.list()); } catch { /* 소스 칸 없이 간다 */ }
  let domains: string[] = [];
  try {
    // 주제 목록도 **고른 스페이스**의 것이어야 한다 — 여기가 두 번째 하드코딩 널이었다.
    const res: any = await withTimeout(api.domains(cfg.owner, { team: src || null }));
    domains = (res.rows || []).map((r: any) => r.domain).filter(Boolean);
  } catch { /* 주제를 못 읽어도 전체 공유는 된다 — 선택지만 줄어든다 */ }
  if (!pane.isConnected) return;
  // 고를 것이 하나뿐이면 묻지 않는다(`chooseDestination` 과 같은 규칙).
  const spaceRow = teams.length ? `
    <label class="list-row tools-opt">
      <span class="title">${esc(T.source)}</span>
      <select id="tl-space">
        <option value=""${src ? "" : " selected"}>${esc(S.spacePersonal)}</option>
        ${teams.map((t) => t.status === "active"
          ? `<option value="${esc(t.id)}"${t.id === src ? " selected" : ""}>${esc(t.name)}</option>`
          : `<option value="${esc(t.id)}" disabled>${esc(t.name)} (${esc(T.sourceOff)})</option>`
        ).join("")}
      </select></label>
    <p class="hint">${esc(T.sourceNote)}</p>` : "";
  // `지금 보고 있는 곳`은 열려 있는 지도의 스코프다 — 다른 스페이스에서 뜨는 팩에는
  // 대응하는 것이 없다. 꺼진 이유가 둘이라 문구도 갈린다.
  const hereOn = !!here && sameSpace;
  pane.innerHTML = `
    ${spaceRow}
    <p class="hint rail-sec">${esc(T.scope)}</p>
    <label class="list-row tools-opt"><input type="radio" name="tl-scope" value="all" checked />
      <span class="title">${esc(T.scopeAll)}</span></label>
    <label class="list-row tools-opt"${domains.length ? "" : ' style="opacity:.5"'}>
      <input type="radio" name="tl-scope" value="topic" ${domains.length ? "" : "disabled"} />
      <span class="title">${esc(T.scopeTopic)}</span>
      <select id="tl-topic" aria-label="${esc(T.scopeTopic)}" ${domains.length ? "" : "disabled"}>
        ${domains.map((d) => `<option value="${esc(d)}">${esc(d)}</option>`).join("")}
      </select></label>
    <label class="list-row tools-opt"${hereOn ? "" : ' style="opacity:.5"'}>
      <input type="radio" name="tl-scope" value="here" ${hereOn ? "" : "disabled"} />
      <span class="title">${esc(T.scopeHere)}</span>
      ${hereOn ? "" : `<span class="hint">${esc(here ? T.sourceOff : T.scopeHereOff)}</span>`}</label>
    <div class="tools-preview" id="tl-preview" aria-live="polite"></div>
    <p class="pack-backup">
      <button class="pack-linkbtn" id="tl-backup">${esc(P.backupLink)}</button>
      <span class="hint">${esc(P.backupDesc)}</span>
    </p>
    <div id="tl-backup-msg" role="status"></div>`;
  // 스페이스가 바뀌면 주제 목록도 `지금 보고 있는 곳`의 가부도 함께 바뀐다 — 조각조각
  // 고치는 대신 이 작은 판을 다시 그린다(부분 갱신은 한 군데를 빠뜨리기 좋은 자리다).
  pane.querySelector("#tl-space")?.addEventListener("change", (e) =>
    void renderShare(pane, sheet, (e.target as HTMLSelectElement).value));

  // 드롭다운에서 주제를 고르는 것이 곧 `주제로` 를 고르는 것이다.
  //
  // 라디오와 같은 `<label>` 안에 있어도 저절로 켜지지 않는다: 브라우저는 라벨 안의 **다른
  // 조작 요소**로 간 클릭을 라벨의 대상에 넘기지 않는다(실측 — select 를 클릭한 뒤
  // `input[name=tl-scope]:checked` 는 여전히 `all` 이었고, 라벨의 글자를 클릭했을 때만
  // `topic` 이 됐다). 그래서 주제를 골라 보낸 사람의 요청에는 `domain` 이 실리지 않았고,
  // 그래프 **전체**가 나갔다. 화면은 고른 것을 보여주고 있으므로 보내기 전에 알 길이 없다.
  //
  // click 과 change 를 **둘 다** 듣는다: change 는 이미 첫 항목이 골라져 있을 때 안 뜨고
  // (드롭다운을 열어 그 항목을 그대로 고르면 값이 안 바뀐다), click 은 키보드로 고를 때
  // 안 뜬다. 상한이 없는 쪽으로 틀리는 편이 낫다 — 잘못 켜지면 고른 주제가 나가고,
  // 안 켜지면 전부가 나간다.
  // 근거: [[record:choosing-a-topic-in-the-share-picker-did-not-arm-its-own-radio-2026-09-20]]
  const topicSel = pane.querySelector("#tl-topic");
  const armTopic = () => {
    const r = pane.querySelector<HTMLInputElement>('input[name=tl-scope][value=topic]');
    if (r) r.checked = true;
  };
  topicSel?.addEventListener("click", armTopic);
  topicSel?.addEventListener("change", armTopic);

  // 전체 백업 — 내 전체 컨텍스트 팩(/activate, 이관용). 구 Packs 하단의 보조 링크가
  // 그대로 왔다. 범위를 고르는 일이 아니라 통째로 내보내는 일이라, 발의 주 액션이
  // 아니라 본문 아래 링크로 둔다(그 화면에서도 같은 이유로 같은 자리였다).
  pane.querySelector("#tl-backup")!.addEventListener("click", async () => {
    const out = pane.querySelector<HTMLElement>("#tl-backup-msg")!;
    // 라벨은 "문서 0개를 팩으로 묶는 중"이었다. `api.activate` 는 무엇이 몇 개 담기는지
    // **미리 알려주지 않으므로** 그 0 은 언제나 거짓이었다. 세는 척을 걷고 시트의 팩
    // 준비와 같은 문구를 쓴다(같은 일이다). 세던 카피 키는 마지막 사용처가 여기여서
    // 두 덱에서 함께 지웠다 — 찾지 마라.
    out.innerHTML = sheetProgress(COPY.ui.sheet.preparing);
    try {
      const res: any = await api.activate(cfg.owner);
      // 완료 문구가 응답보다 많은 것을 말하고 있었다. `api/activate.py` `activate()` 가
      // 돌려주는 키는 넷이다 — `owner` · `episode_count` · `learner_pattern_count` ·
      // `executed`. 거기에 `out_dir` 을 주면 `written`, 안 주면 `pack{markdown,json,mcp}`
      // 이 붙는다. **`out` 이라는 키는 없다.** 앱은 `out` 을 실은 적이 없고 호스티드는
      // 그 인자를 아예 지우므로(`vinc_mcp/http_server.py` — 클라가 호스트 파일경로를
      // 정하지 못하게), `res.out || "pack"` 은 언제나 리터럴 `pack` 이었다. 크기 자리에
      // 그 네 글자가 찍혔다.
      //
      // **파일도 여기서 만든다.** `out_dir` 이 없으니 서버는 아무것도 쓰지 않는다 —
      // 안 만들면 `전체 백업 팩 만들기` 가 성공만 알리고 남기는 것이 없다. 담는 것은
      // `pack.json`(사이드카 `write_pack` 이 쓰는 것과 같은 내용 · 같은 들여쓰기): 갈 곳이
      // MCP 클라이언트라 사람이 읽는 `markdown` 이 아니라 이쪽이다.
      const body = JSON.stringify(res.pack?.json ?? res, null, 2);
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([body], { type: "application/json" }));
      a.download = "vinc-backup.json";
      a.click();
      URL.revokeObjectURL(a.href);
      // 세는 것은 **기록**이다. `episode_count` 는 recall 이 고른 행 수이고 문서 수가
      // 아니다(문서는 팩 공유 쪽 `_docs_for_concepts` 가 따로 센다).
      out.innerHTML = `<p>${esc(COPY.packs.success(res.episode_count ?? 0))}</p>`;
    } catch {
      out.innerHTML = `<div class="error-banner">${esc(COPY.packs.error)}</div>`;
    }
  });

  // 다른 스페이스에서 뜬 팩은 목적지를 묻지 않는다 — 지금 있는 곳으로 들어온다.
  // 구 Packs 의 `개인 → 팀 불러오기`가 이 갈래로 일반화됐다(방향은 양쪽 다 된다).
  // 이름을 못 읽었으면 intoLabel 을 비워 시트가 일반 명칭("팀 공간")을 쓰게 둔다.
  const shareOpts = sameSpace ? {} : {
    from: { team: src || null }, into: { team: cfg.team },
    intoLabel: cfg.team ? teams.find((t) => t.id === cfg.team)?.name : S.spacePersonal,
  };

  sheet.foot.innerHTML = sheetActions(
    { id: "tl-go", label: COPY.ui.sheet.save }, { id: "tl-cancel", label: COPY.ui.common.cancel });
  sheet.foot.querySelector("#tl-cancel")!.addEventListener("click", sheet.close);
  const go = sheet.foot.querySelector<HTMLButtonElement>("#tl-go")!;

  // 범위를 고르면 그 자리에서 나갈 것(수치·목록·개별 제외)을 보인다. 범위 고르기와 확인이 한 화면이라
  // 설정을 닫고 시트를 하나 더 거치지 않는다. 늦게 온 응답은 버린다(범위를 연달아 바꿀 때).
  const preview = pane.querySelector<HTMLElement>("#tl-preview")!;
  const { renderSharePreview, sendSharePick } = await import("./sheets");
  let collect: (() => ReturnType<Awaited<ReturnType<typeof renderSharePreview>>>) | null = null;
  let version = 0;
  const scopeNow = () => {
    const pick = pane.querySelector<HTMLInputElement>("input[name=tl-scope]:checked")?.value;
    const topic = pane.querySelector<HTMLSelectElement>("#tl-topic")?.value;
    return { pick, scope: pick === "topic" && topic ? { domain: topic } : {} };
  };
  const refresh = async () => {
    const mine = ++version;
    collect = null;
    const { pick, scope } = scopeNow();
    // `지금 보고 있는 곳`은 View 가 자기 스코프를 안다 — 미리보기는 그 시트가 한다.
    if (pick === "here") { preview.innerHTML = ""; go.disabled = false; return; }
    go.disabled = true;
    preview.innerHTML = sheetProgress(COPY.ui.sheet.preparing);
    try {
      const c = await renderSharePreview(preview, scope, shareOpts, (any) => { if (mine === version) go.disabled = !any; });
      if (mine === version && pane.isConnected) collect = c;
    } catch {
      if (mine === version) preview.innerHTML = `<div class="error-banner">${esc(COPY.packs.error)}</div>`;
    }
  };
  pane.querySelectorAll<HTMLInputElement>("input[name=tl-scope]").forEach((r) =>
    r.addEventListener("change", () => void refresh()));
  topicSel?.addEventListener("change", () => void refresh());
  void refresh();

  go.addEventListener("click", () => {
    const { pick } = scopeNow();
    if (pick === "here") { sheet.close(); here?.share(); return; }
    const chosen = collect?.();
    if (!chosen) return;
    sheet.close();
    sendSharePick(chosen, shareOpts);
  });
}
