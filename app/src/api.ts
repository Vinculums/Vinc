import { viewSessionCache } from "./view-session-cache";
import type { LayoutAnswer } from "./view-layout-wire";
/** Sidecar client. Data plane is localhost HTTP + session token; in the shell the
 *  control plane (port, token, sidecar lifecycle) goes over Tauri IPC (`shellInvoke`).
 *  Graph/query logic never lives here. */
import { chunkPack, type StageCounts } from "./packchunks";
import { noteMe } from "./self-hosted";
import type { ApiError } from "./api-error";
// 데이터 형식은 `api-types.ts` 에 있다. 부르는 쪽이 전처럼 여기서 가져가도록 다시 내보낸다.
import type { DataMode, Scope, SearchOptions, StorageLocation, StorageConfig, FolderCheck, ConnectionCheck, DatabaseConnect, TimeBasis, GraphEdge, TopicDescription, NodeContext, NavTreeRes, PackProgress, DeleteGuard, ConventionsPolicy, ConversionCheck, ConversionRun } from "./api-types";
export type { DataMode, Scope, SearchOptions, StorageLocation, StorageConfig, FolderCheck, ConnectionCheck, DatabaseConnect, TimeBasis, GraphNode, GraphEdge, NodeLinks, TopicDescription, TopicDetail, NodeContext, NavNode, NavGroup, NavTreeRes, PackProgress, DeleteGuard, ConventionsPolicy, ConversionCheck, ConversionRun } from "./api-types";
export { ApiError } from "./api-error";
// 설정(실행 자리 · 데이터 모드 · `cfg` · 셸의 `invoke`)은 `api-config.ts` 에 있다. 부르는 쪽을 위해 다시 내보낸다.
import { cfg, WEB, dataMode, accountMode, shellInvoke, messageOf } from "./api-config";
export { cfg, IN_SHELL, WEB, dataMode, accountMode, remote, shellInvoke, messageOf } from "./api-config";
// 스페이스(쓰기 권한 · 팀 · 커뮤니티 · 주소의 스페이스)는 `api-space.ts` 에 있다.
import { isCommunitySpace } from "./api-space";
export { canWriteHere, teamsApi, communitiesApi, isCommunitySpace, spaceName, spaceKey, writeSpaceToAddress, adoptSpaceFromAddress } from "./api-space";
// 전송(사이드카 · 계정 통과로 · 웹 프록시 · 쿠키 라우트)은 `api-transport.ts` 에 있다.
import { CSRF_COOKIE, csrfToken, webFetch, sidecarOnly, accountCall, call, searchCall, localCall } from "./api-transport";
export { hasTeamCredential, bindTeamCredential, forgetMemberKey } from "./api-transport";

/** 연결 셋업 진단 문자열. 실패하면 화면에 노출해 원인을 바로 본다. 앱 버전과 셸이 준
 *  실제 포트(conn.port)를 박아, 어느 빌드가 어느 포트를 받았는지 리포트만으로 판별된다. */
declare const __APP_VERSION__: string;   // vite define 주입(빌드 상수)
declare const __BUILD_SHA__: string;     // 〃 — 짧은 커밋 SHA(웹은 태그와 무관하게
                                         // 배포돼 버전만으로는 빌드를 특정 못 한다)
const APP_V = typeof __APP_VERSION__ === "string" ? __APP_VERSION__ : "dev";
const BUILD_SHA = typeof __BUILD_SHA__ === "string" ? __BUILD_SHA__ : "";
/** 진단·리포트용 빌드 식별자 — `v0.6.7+a1b2c3d`. SHA 를 못 구하면 버전만. */
export const buildId = BUILD_SHA ? `${APP_V}+${BUILD_SHA}` : APP_V;
export let sidecarDiag = "(connectSidecar not run)";

/** 셸에서 사이드카 접속정보(포트·세션 토큰)를 받아 cfg 에 주입한다. main.rs 의
 *  `sidecar_conn` 커맨드를 invoke 해 win.eval 타이밍 의존 없이 토큰을 확보하고(정본),
 *  이어 /health 가 응답할 때까지 잠깐 대기해 첫 상호작용 실패(기동 지연)를 막는다.
 *  브라우저(dev)에는 셸이 없어 no-op — same-origin vite 프록시를 그대로 쓴다.
 *
 *  ⚠ 전역 `window.__TAURI__` 를 쓰지 않는다(Q2, 2026-08-07). `withGlobalTauri` 는
 *  **전체 API 를 페이지 전역에 노출**하는데, 이 앱은 사용자 문서를 렌더하고 웹 Vinc
 *  로도 같은 번들이 나가므로 표면을 열어 둘 이유가 없다. 대신 모듈에서 가져오되
 *  **셸일 때만 동적 import** 한다 — 정적 import 는 웹 번들에도 들어간다. */
export async function connectSidecar(): Promise<void> {
  const invoke = await shellInvoke();
  if (!invoke) {
    sidecarDiag = "not in shell (browser dev, web Vinc): sidecar connect skipped";
    return;
  }
  try {
    const c = await invoke("sidecar_conn") as { port?: number; token?: string };
    if (c?.port) cfg.base = `http://127.0.0.1:${c.port}`;
    if (c?.token) cfg.token = c.token;
    sidecarDiag = `v${buildId} conn.port=${c?.port ?? "?"} base=${cfg.base} `
      + `token=${c?.token ? c.token.length + "ch" : "MISSING"}`;
  } catch (e: any) {
    sidecarDiag = `invoke sidecar_conn threw: ${messageOf(e)}`;
  }
  // 사이드카 기동 대기 ≤ ~12s — PyInstaller 단일파일은 첫 실행에 압축 해제가 있어
  // (특히 macOS 콜드 스타트) 5s 로는 정상 기동도 오판할 수 있다.
  if (await waitForSidecar(48)) return;
  // 실패하면 "왜"를 반드시 싣는다 — 셸이 모아둔 사이드카 수명 이벤트(종료코드·
  // 시그널·stderr)를 붙여, health TIMEOUT 이 원인 불명으로 끝나지 않게 한다.
  let why = "";
  try { why = await invoke("sidecar_status") as string; } catch (e: any) { why = `sidecar_status failed: ${messageOf(e)}`; }
  sidecarDiag += ` · health TIMEOUT · sidecar[${why}]`;
}

/** 로그인 직전에 사이드카가 답하는지 본다. 셸이 "끝났다"고 알면 다시 띄운다.
 *
 *  로그인은 사이드카 루프백으로 키를 받는다. 기동 중에 죽은 사이드카(스토어 열기가 엔진을
 *  죽인 경우)를 두고는 단추를 몇 번 눌러도 같은 실패만 반복됐다. 아직 뜨는 중이면 죽이지
 *  않고 기다린다: 로그인 화면은 사이드카를 기다리지 않고 먼저 뜨므로 바로 누를 수 있다.
 *  근거: [[record:a-wal-that-crashes-the-engine-takes-the-desktop-sign-in-down-2026-09-27]] */
export async function ensureSidecar(): Promise<boolean> {
  const invoke = await shellInvoke();
  if (!invoke) return true;                       // 웹·브라우저 dev: 셸 사이드카가 없다
  if (await waitForSidecar(4)) return true;
  const alive = await invoke("sidecar_alive").then(Boolean).catch(() => true);
  if (!alive) {
    try { await invoke("sidecar_restart"); } catch { return false; }
  }
  // 남은 WAL 을 점검하면 자식 프로세스가 두 번 뜰 수 있다(store/wal_guard.py). 1분까지 본다.
  return waitForSidecar(240);
}

/** `/health?live=1` 를 최대 `tries * 250ms` 기다린다. 응답과 본문이 끝나면 true. */
async function waitForSidecar(tries: number): Promise<boolean> {
  // 요청·본문 읽기도 대기 상한 안에 둔다. 응답하지 않는 연결이 폴더 전환을 붙잡지 않는다.
  // 근거: [[record:sidecar-health-wait-deadline-restored-2026-10-09]]
  const deadline = performance.now() + tries * 250;
  while (performance.now() < deadline) {
    try {
      const signal = AbortSignal.timeout(Math.max(1, Math.ceil(deadline - performance.now())));
      const res = await fetch(new URL("/health?live=1", cfg.base), { signal });
      if (res.ok) {
        // 사이드카가 떴어도 스토어가 성치 않으면(구버전 잔존 스토어의 프로비저닝
        // 실패 등) status=degraded 로 온다 — "ok" 로 뭉개면 뒤따르는 전 조회가
        // 원인 불명으로 깨진다. 사유를 진단에 그대로 싣는다.
        const h = await res.json().catch(() => ({} as any));
        // 본문이 마감 신호로 끊겼으면 준비가 아니다. 시계만 보면 안 된다: 타이머는 시계가
        // 마감을 넘었다고 말하기 조금 전에 깰 수 있어서, 읽지도 못한 응답을 준비로 쳤다.
        if (signal.aborted || performance.now() >= deadline) return false;
        sidecarDiag += h?.status === "degraded"
          ? ` · health DEGRADED[${h.provision_error || h.error || "?"}]`
          : " · health ok";
        if (h?.migrating) startIndexWatch();
        return true;
      }
    } catch {}
    const remaining = deadline - performance.now();
    if (remaining > 0) await sleep(Math.min(250, remaining));
  }
  return false;
}

// 검색 색인 이관(VIN-272). 사이드카는 업데이트 뒤 첫 기동에서 색인을 옮기고, 그동안 `/health`
// 는 스토어를 안 읽고 `migrating` 을 싣는다. 살아 있는 것이니 기다림을 끝내고, 화면에는 한 줄을
// 건다(`index-signals.ts`). 끝날 때까지 2초마다 다시 묻는다.
let migrating = false;

/** 사이드카가 지금 검색 색인을 옮기고 있는가(신호 모듈이 늦게 걸릴 때 읽는다). */
export function indexMigrating(): boolean {
  return migrating;
}

function startIndexWatch(): void {
  if (migrating) return;
  migrating = true;
  dispatchEvent(new CustomEvent("vinc:index-migrating"));
  const tick = async () => {
    let still = true;
    try {
      const res = await fetch(new URL("/health", cfg.base));
      if (res.ok) still = Boolean((await res.json().catch(() => ({} as any)))?.migrating);
    } catch {}
    if (still) {
      setTimeout(tick, 2000);
      return;
    }
    migrating = false;
    dispatchEvent(new CustomEvent("vinc:index-ready"));
  };
  setTimeout(tick, 2000);
}


/** 로컬 모드가 **화면에 보여 줄** 데이터가 이 기기 스토어에 있는가. 모드를 정할 때 묻는다.
 *
 *  🔴 `call` 이 아니라 `localCall` 이다. 창에 "account" 가 남아 있으면 `call` 은 계정으로
 *  가서 **계정의** 그래프를 센다. 여기서 묻는 것은 이 기기다.
 *
 *  묻는 것은 "디스크에 뭔가 있나"가 아니라 "로컬로 두면 그 작업이 보이나"다. 로컬에 남기는
 *  이유가 그것뿐이기 때문이다. 그래서 둘을 세지 않는다:
 *  - `user:local` — 옛 로컬 MCP 의 소유자다. 사이드카가 기동하며 `tenant:local` 로 접으므로
 *    (`api/owner_fold.py`, VIN-131) 여기 올 때쯤에는 남아 있지 않다. 남아 있다면 접기가
 *    실패한 것이고, 그 행은 로컬 모드 화면에도 안 보인다.
 *  - 스토어를 못 여는 기기(503, WAL 격리 뒤 스토어 없이 뜬 사이드카) — 로컬 모드가 보여
 *    줄 것이 없다.
 *  어느 쪽도 지우지 않는다: 폴더는 그대로이고, 같은 폴더로 로컬 모드를 켜면 다시 열린다.
 *  샘플 스페이스(`sample:demo`)는 세지 않는다. 그 밖의 실패는 **있다고 답한다** — 모를 때
 *  계정으로 넘기면 보이던 작업이 빈 계정 화면 뒤로 가려진다. */
export async function localStoreHasData(): Promise<boolean> {
  const owner = "tenant:local";
  try {
    const d = await localCall("/domains", { owner });
    if ((d?.rows || []).length) return true;
    const docs = await localCall("/docs", { owner, limit: 1, detail: "summary" });
    return Number(docs?.total_count ?? (docs?.docs || []).length) > 0;
  } catch (e: any) {
    return !e?.storeDown;
  }
}

/** 이 셸이 볼 그래프를 정한다. **정본은 사이드카의 저장 설정**(`~/.vinc/config.json`,
 *  VIN-130)이고 localStorage 는 그 사본이다(`accountMode()` 가 동기로 읽어야 해서).
 *  정본을 파일에 두는 이유: 설정 화면이 모드와 폴더를 한 번에 바꾸고, 사이드카와 로컬
 *  MCP 가 같은 파일을 읽는다. 창 저장소는 그 둘이 못 본다.
 *
 *  **사람이 고른 모드(`mode_chosen`)만 그대로 따른다.** 고른 적이 없으면(새 설치, 그리고
 *  0.9.4·0.9.5 가 이 기기에 데이터가 있다고 말없이 로컬로 정한 설치) 이 기기에 보여 줄
 *  작업이 있는지 본다. 있으면 `"ask"` 를 돌려주고, 부르는 쪽이 로컬 모드로 계속 가며 그렇다고
 *  한 번 알린다(`device-graph.ts`, 옮기기는 설정에만 있다). 계정의 빈 화면이 디스크의 작업을
 *  말없이 가리면 그게 곧 유실로 읽힌다. 없으면 계정이다: 적되 고른 것으로는 적지 않는다
 *  (나중에 이 기기에 작업이 생기면 그때 알린다).
 *  근거: [[vinc.plan.desktop-account-mode §8]] ·
 *  [[decision:after-sign-in-a-device-with-a-graph-stays-local-and-moving-is-only-in-settings-2026-09-29]] */
export async function decideDataMode(): Promise<DataMode | "ask"> {
  const conf = await storage.config().catch(() => null);
  const saved = conf?.mode;
  if (conf?.mode_chosen && (saved === "account" || saved === "local")) {
    dataMode.set(saved);
    return saved;
  }
  if (!conf) {
    // 설정을 못 읽는다(옛 엔진). 물어도 답을 적을 곳이 없으니 종전 규칙대로 간다.
    const held = dataMode.get();
    const next: DataMode = held ?? ((await localStoreHasData()) ? "local" : "account");
    dataMode.set(next);
    return next;
  }
  if (await localStoreHasData()) return "ask";
  dataMode.set("account");
  if (saved !== "account") await storage.setConfig({ mode: "account" }).catch(() => {});
  return "account";
}

// ── 저장 위치와 모드 전환(VIN-130) ──────────────────────────────────────────
// 전부 **이 기기의 사이드카**로 간다(`localCall`). 계정 모드에서 부르는 것들이라 `call` 로
// 보내면 계정으로 간다.
export const storage = {
  config: (): Promise<StorageConfig> => localCall("/config"),
  inspect: (path: string): Promise<FolderCheck> => localCall("/config/inspect", { path }),
  /** 비밀번호는 이 요청에만 실린다. 사이드카는 확인에만 쓰고 버린다. 주소가 틀리면 400 으로 던진다. */
  inspectDatabase: (body: DatabaseConnect): Promise<ConnectionCheck> =>
    localCall("/config/inspect-database", undefined, body),
  /** 그래프를 옮긴다(잡). 대상에 원본이 전부 왔을 때만 위치가 바뀐다(`result.switched`). 원본은 지우지
   *  않는다. 400·409 는 던지지 않고 그대로 준다(`error`·`check`·`inspect`). */
  move: async (body: { to: StorageLocation; password?: string; accept_risk?: boolean;
                       from?: "account"; token?: string; base?: string; owner?: string }): Promise<any> => {
    try { return await localCall("/config/move", undefined, body); }
    catch (e: any) {
      const m = /^(400|409) (.*)$/s.exec(messageOf(e, ""));
      if (m) { try { return { status: Number(m[1]), ...JSON.parse(m[2]!) }; } catch {} }
      throw e;
    }
  },
  /** 400·409 는 던지지 않고 그대로 준다: 화면이 사유(`error`·`inspect`·`check`)를 그려야 한다.
   *  `location` 은 예전 키(mode·data_path)와 섞지 않는다. 데이터베이스면 `password` 로 다시 확인하고 적는다. */
  setConfig: async (body: { mode?: DataMode; mode_chosen?: boolean;
                            data_path?: string | null; accept_risk?: boolean;
                            location?: StorageLocation; password?: string }): Promise<any> => {
    try { return await localCall("/config", undefined, body); }
    catch (e: any) {
      const m = /^(400|409) (.*)$/s.exec(messageOf(e, ""));
      if (m) { try { return { status: Number(m[1]), ...JSON.parse(m[2]!) }; } catch {} }
      throw e;
    }
  },
  /** 이 기기의 모든 주제를 계정으로(202 잡). 이 스토어에는 아무것도 안 쓴다. */
  pushAll: (token: string) => localCall("/sync/push-all", undefined, { token }),
  /** 계정 그래프 전부를 이 스토어로(202 잡). 앱의 소유자로 받는다. */
  pullAll: (token: string, owner: string) =>
    localCall("/sync/pull-all", undefined, { token, owner }),
  job: (id: string) => localCall(`/jobs/${id}`),
};

/** 사이드카를 다시 띄운다(셸 `sidecar_restart`). 폴더를 바꾼 뒤에 부른다. 포트·토큰은
 *  그대로라 접속 정보를 다시 받을 필요가 없고, `/health` 가 답할 때까지만 기다린다. */
export async function restartSidecar(): Promise<boolean> {
  const invoke = await shellInvoke();
  if (!invoke) return false;
  await invoke("sidecar_restart");
  return waitForSidecar(120);   // ≤ 30초: 큰 스토어를 여는 데 걸리는 시간
}

/** 이 실패가 "스토어를 못 읽는다"인가. 화면은 이 경우에만 다른 말을 해야 한다 —
 *  나머지 실패는 다시 시도하면 되지만, 이건 **빈 결과와 구분되지 않아서** 위험하다. */
export function storeDownReason(e: unknown): string | null {
  const err = e as Partial<ApiError> | null | undefined;
  return err?.storeDown ? (err.reason || "") : null;
}

/** `ms` 만큼 기다린다. */
export const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

/** 이 `doc_get` 거절이 **쓰기 창·중간에 끊긴 쓰기**인가(P17, `api/docstore.py` get_doc).
 *  그 갈래만 응답에 청크 수를 단다 — 모르는 id·소유자 불일치·죽은 사이드카에는 없다.
 *  `error`·`hint` 는 영어 서버 문자열이라 화면에 쓰지 않고, 판별에만 쓰는 이 열을 본다.
 *
 *  ⚠ 응답은 **"잠시 뒤 낫는다"와 "크래시가 남겨 며칠 간다"를 못 가른다.** 둘이 같은
 *  행 수 불일치로 오고, 서버도 재시도 전에는 어느 쪽인지 알 근거가 없다(쓰기 창은
 *  다음 순간 닫히고, 잔해는 다음 re-put 까지 그대로다 — 디스크 위 모습이 같다).
 *  그래서 문구가 두 처방(잠시 뒤 다시 · 계속 이러면 전문 재저장)을 함께 말한다. */
export const docMidWrite = (d: { executed?: boolean; chunk_count?: number }): boolean =>
  d.executed === false && d.chunk_count !== undefined;

/** 커뮤니티 스페이스의 트리에서 내린 페이지를 뺀다. 커뮤니티는 내린 페이지 문서에 유효기간 끝을 단다.
 *  개인·팀 트리는 거르지 않는다: 닫은 문서도 그 사람이 찾아 열 수 있어야 한다. */
export function withoutClosedDocs(res: NavTreeRes, today = new Date().toISOString().slice(0, 10)): NavTreeRes {
  const open = (d: { valid_until?: string }) => !d.valid_until || d.valid_until > today;
  const domains = res.domains?.map(g => ({ ...g, docs: g.docs.filter(open) }));
  const unfiled = res.unfiled?.filter(open);
  const docs = (domains ?? []).reduce((n, g) => n + g.docs.length, 0) + (unfiled?.length ?? 0);
  return { ...res, domains, unfiled, ...(res.counts ? { counts: { ...res.counts, docs } } : {}) };
}

/** 팩 적재 — 조각으로 나눠 **순서대로 하나씩** 보낸다.
 *
 *  나누는 이유는 허브의 본문 상한(4MB)이고, 순서와 단위는 `packchunks.ts` 가 정한다.
 *  병렬로 보내지 않는다: 허브 쓰기는 잡 큐 하나로 줄을 서므로(`api/server.py`
 *  `run_sync`) 빨라지지 않고, 기다리는 요청마다 계정 프록시의 45초 제한만 먼저 닳는다.
 *
 *  응답은 한 번에 보냈을 때와 **같은 모양**으로 합친다(`merged`·`id_conflicts`·
 *  `documents`) — 호출부가 조각을 몰라도 된다. 중간에 실패하면 던지되 `sent`(성공한
 *  조각 수)를 달아 둔다. 적재는 id 로 MERGE 하고 같은 문서는 건너뛰므로, 처음부터 다시
 *  보내면 들어간 것은 그대로 두고 나머지를 채운다. */
async function importPackInChunks(pack: any, owner: string, scope?: Scope,
                                  onStep?: (p: PackProgress) => void): Promise<any> {
  const { chunks, tooBig } = chunkPack(pack);
  const zero = (): StageCounts => ({ nodes: 0, edges: 0, documents: 0 });
  const total = zero(), done = zero();
  for (const c of chunks) total[c.stage] += c.count;
  const out: any = {
    executed: true, too_big: tooBig, id_conflicts: [] as string[],
    merged: { nodes: { created: 0, matched: 0 }, edges: { created: 0 } },
    documents: { applied: 0, skipped: 0, total: total.documents },
  };
  onStep?.({ total, done: { ...done } });
  let sent = 0;
  for (const c of chunks) {
    let r: any;
    try {
      r = await call("/ingest/pack", undefined, { pack: c.pack, owner }, scope);
    } catch (e) {
      if (e && typeof e === "object") (e as any).sent = sent;
      throw e;
    }
    const n = r?.merged?.nodes || {};
    out.merged.nodes.created += n.created || 0;
    out.merged.nodes.matched += n.matched || 0;
    out.merged.edges.created += r?.merged?.edges?.created || 0;
    out.id_conflicts.push(...(r?.id_conflicts || []));
    out.documents.applied += r?.documents?.applied || 0;
    out.documents.skipped += r?.documents?.skipped || 0;
    // 주제 상한 같은 거절은 오류가 아니라 응답으로 온다 — 그 뒤 조각은 보내지 않는다.
    if (r?.executed === false) return { ...out, executed: false, error: r.error, sent };
    sent++;
    done[c.stage] += c.count;
    onStep?.({ total, done: { ...done } });
  }
  return out;
}

/** 캐시의 데이터 경로를 고정한다. 식별값은 진단 로그에 쓰지 않는다. */
export function viewScope() {
  const owner = cfg.owner, team = cfg.team;
  return { owner, team, key: JSON.stringify([owner, team, cfg.base, dataMode.get(),
    WEB, cfg.token, WEB ? csrfToken() : ""]) };
}

/** 웹 재사용은 현재 계정 identity와 현재 Space 권한을 매번 다시 확인한다. */
export async function viewAccess(scope: ReturnType<typeof viewScope>, signal?: AbortSignal) {
  const [me, changes] = await Promise.all([
    api.me({ cache: "no-store", signal }),
    localCall("/changes", { owner: scope.owner }, undefined, { team: scope.team || null }, signal),
  ]);
  if (!me.ok) { viewSessionCache.purge(); throw new Error("View access unavailable"); }
  const user = (await me.json()).user;
  const identity = typeof user?.email === "string" ? user.email : "";
  const marker = typeof changes?.marker === "string" ? changes.marker : "";
  if (!identity || !marker || scope.key !== viewScope().key || signal?.aborted) return null;
  return { session: JSON.stringify([scope.key, identity]), revision: marker };
}

type GraphRootsAnswer = {
  executed: boolean;
  state: "off" | "pending" | "stale" | "ready" | "failed";
  marker?: string;
  nodes?: number;
  /** 답을 만든 배치 코드의 판(`api.layout_remote`). 옛 허브는 싣지 않는다. */
  version?: string;
  /** 답 본문의 해시. 요청에 같은 값을 `have` 로 실으면 허브가 본문을 뺀다. */
  digest?: string;
  answer?: LayoutAnswer;
};


/** 미리보기(`dry_run`)를 받아 묶음을 만든다. 미리보기를 못 받으면(큰 노드라 미리보기가
 *  불완전함·종류 미지원·연결 실패) digest 없이 묶는다: 적용은 지금까지처럼 조건 없이 간다. */
async function previewDelete(path: string, body: Record<string, unknown>,
                             scope?: Scope): Promise<DeleteGuard> {
  const guard: DeleteGuard = { operation_id: crypto.randomUUID() };
  try {
    const p = await call(path, undefined, { ...body, dry_run: true }, scope);
    if (p?.executed && p.preview_complete && typeof p.preview_digest === "string")
      guard.preview_digest = p.preview_digest;
  } catch { /* 미리보기는 거들 뿐이다. 실패해도 삭제 자체는 막지 않는다. */ }
  return guard;
}

/** 묶음을 실어 적용한다. 응답을 잃은 실패(연결 끊김·502·504)만 같은 묶음으로 한 번 더
 *  보낸다: 같은 operation_id 라 서버가 첫 결과를 돌려줄 뿐 두 번 지우지 않는다. */
async function applyDelete(path: string, body: Record<string, unknown>,
                           guard?: DeleteGuard, scope?: Scope): Promise<any> {
  const send = () => call(path, undefined, { ...body, ...(guard || {}) }, scope);
  try { return await send(); }
  catch (failure: any) {
    const status = Number(failure?.status) || 0;
    if (guard && (status === 0 || status === 502 || status === 504)) return send();
    throw failure;
  }
}

/** 조건부 쓰기가 상태 불일치로 멈췄나(0.9.8 `MutationConflict`·미리보기 불일치).
 *  사이드카와 계정 통과로는 200 에 `status:"conflict"` 를 싣고, v1 꼴 오류면 409 로 온다. */
export function isWriteConflict(r: any): boolean {
  const code = r?.error ?? r?.code;
  return r?.status === "conflict" || r?.status === 409
    || code === "write_conflict" || code === "preview_conflict";
}

export const api = {
  /** 웹 계정 세션(`/api/me`). 쿠키 세션이라 웹·같은 출처에서만 뜻이 있다.
   *  응답을 그대로 돌려준다 — 상태 확인과 본문 해석은 부르는 쪽 몫이다. */
  me: (init: { cache?: RequestCache; signal?: AbortSignal } = {}): Promise<Response> =>
    webFetch("/api/me", init),
  /** 커뮤니티 공유 행선지(웹 전용 같은 출처). 응답을 그대로 돌려준다. */
  communityDestinations: (signal?: AbortSignal): Promise<Response> =>
    webFetch("/api/community/agents/destinations", { cache: "no-store", signal }),
  roleRoster: async (owner: string) => {
    const scope = { team: cfg.team || null };
    const nodes = new Map<string, any>();
    const edges: GraphEdge[] = [];
    let offset = 0;
    for (;;) {
      const page = await call("/roles", { owner, roster: 1, limit: 100, offset }, undefined, scope);
      if (!page?.executed || !Array.isArray(page.roles) || !Array.isArray(page.relations))
        throw new Error(page?.error || "role roster unavailable");
      for (const r of page.roles) nodes.set(r.id, { ...r, label: r.title, kind: "concept" });
      for (const e of page.relations) edges.push({ ...e, source: e.from, target: e.to });
      if (page.next_offset == null) break;
      if (!Number.isInteger(page.next_offset) || page.next_offset <= offset)
        throw new Error("invalid role roster pagination");
      offset = page.next_offset;
    }
    return { nodes: [...nodes.values()], edges };
  },
  /** 역할 하나의 계약과 그 절차·정책(`procedures` [{id, policies}], `policies` [{id, title, rule}]).
   *  로스터는 계약 전문 없이 오므로 지도와 편집 면은 이것으로 읽는다. 근거: [[vinc.canon.terminology §3.2]] */
  role: (owner: string, role: string, detail = false): Promise<any> =>
    call("/roles", { owner, role, ...(detail ? { detail: "procedures" } : {}) }, undefined, { team: cfg.team || null }),
  /** 역할에 붙은 평가 카드(조회 전용, 최신순). `role` 은 짧은 이름도 받는다. */
  evalCards: (owner: string, role: string, limit = 20) =>
    call("/eval-cards", { owner, role, limit }, undefined, { team: cfg.team || null }),
  health: () => call("/health"),
  /** 변경 표식(VIN-129). 값은 비교만 한다: 같으면 그대로, 다르면 다시 읽는다(`live.ts`). */
  changes: (owner: string): Promise<{ marker?: string }> => call("/changes", { owner }),
  /** 사람의 답을 기다리는 실행(`api/runs.py` `waiting_runs`). 조회 전용이라 키로도 읽힌다. */
  runsWaiting: (limit = 100) => call("/runs/waiting", { limit }),
  /** 사람의 답. 계정의 전용 라우트(`POST /api/graph/runs/answer`)는 쿠키 세션만 받는다: 웹에서만 들어가고,
   *  셸의 키 통과로로 보내면 계정이 `person_session_required` 로 거절한다. `ask` 는 목록이 준 값 그대로다. */
  runAnswer: (body: { run: string; ask: number; answer: "approve" | "decline" | "reply"; text?: string }) =>
    call("/runs/answer", undefined, body),
  /** 이 기기의 알림(계정 `/api/push/*`). 웹의 쿠키 세션 전용이라 같은 출처에서만 뜻이 있다.
   *  키는 기기를 구독시키지 못한다(계정이 Authorization 을 거절한다). 응답을 그대로 돌려준다. */
  pushDevices: (): Promise<Response> =>
    webFetch("/api/push/devices", { cache: "no-store" }),
  // 경로와 메서드는 호출마다 글자 그대로 쓴다: 계약 검사(app/dev/sidecar-contract-check.ts)는 변수로 만든
  // 주소나 밖에서 받은 옵션을 읽지 못한다.
  pushPost: (op: "subscribe" | "unsubscribe" | "test", body: unknown): Promise<Response> => {
    if (op === "subscribe") return webFetch("/api/push/subscribe", { method: "POST", cache: "no-store", json: body });
    if (op === "unsubscribe") return webFetch("/api/push/unsubscribe", { method: "POST", cache: "no-store", json: body });
    return webFetch("/api/push/test", { method: "POST", cache: "no-store", json: body });
  },
  viewL0: (owner: string) => call("/view", { owner }),
  //: `domains` 는 **주제 여럿**(쉼표 결합). 스코프는 하위 트리 포함이다 —
  //: `domains=vinc` 하나면 `vinc/design` 까지 함께 온다(api/query.normalize_domains).
  graph: (owner: string, opts: { domain?: string; domains?: string[]; seed?: string;
                                 depth?: number; layout?: string;
                                 //: 원뿔 반각 배율(hub 3D). 1 이 기본이라 그때는 안 싣는다 —
                                 //: 안 싣는 요청이 여태와 같은 URL 이어야 캐시가 갈리지 않는다.
                                 cone?: number } = {}) => {
    const { domains, ...rest } = opts;
    return call("/graph", { owner, ...rest,
                            ...(domains?.length ? { domains: domains.join(",") } : {}) });
  },
  // Initial View read in stages. Capture Space and cancellation for both stages and fallback.
  // 웹은 계정 프록시(`/api/graph`)로, 데스크톱 계정 모드는 사이드카 통과로로, 로컬 모드는
  // 이 기기 사이드카로 간다. 세 길 모두 같은 `/graph` 핸들러에 `stage`·`snapshot` 이 닿는다.
  graphStage: (owner: string, opts: { domains?: string[]; layout?: string; cone?: number },
      stage: "preview" | "complete" | "cancel" | undefined, snapshot: string | undefined,
      scope: Scope, signal: AbortSignal) => {
    const { domains, ...rest } = opts;
    // `marker`: 응답이 읽기 전에 잡은 변경 표식을 싣게 한다(`api/server.py _query_graph`).
    const params = { owner, ...rest, stage, snapshot, marker: 1,
      ...(domains?.length ? { domains: domains.join(",") } : {}) };
    // 취소 요청은 접힌 신호에 묶지 않는다: 그 신호가 접혔기 때문에 보내는 것이다.
    const live = stage === "cancel" ? undefined : signal;
    return accountMode() && !sidecarOnly("/graph")
      ? accountCall("/graph", params, undefined, scope, live)
      : localCall("/graph", params, undefined, scope, live);
  },
  graphRoots: (owner: string, basis: "recorded" | "content", signal: AbortSignal,
      scope: Scope = { team: cfg.team || null }, have?: string): Promise<GraphRootsAnswer> =>
    accountMode() && !sidecarOnly("/graph/roots")
      ? accountCall("/graph/roots", { owner, basis, ...(have ? { have } : {}) }, undefined, scope, signal)
      : localCall("/graph/roots", { owner, basis, ...(have ? { have } : {}) }, undefined, scope, signal),
  recall: (owner: string, limit = 50) => call("/recall", { owner, limit, rank: "preference" }),
  search: (query: string, top_k = 20, options: SearchOptions = {}) => searchCall(query, top_k, options),
  // 정확한 제목은 저장된 메타에서 찾는다. 본문 색인·그래프 노드 유무와 무관하다.
  docTitleMatches: (title: string): Promise<{ docs?: { doc_id: string; title: string; temporal?: any }[] }> =>
    call("/docs", { owner: cfg.owner, title, detail: "summary", limit: 8 }),
  coupling: (owner: string, a: string, b: string) => call("/coupling", { owner, a, b }),
  // scope 를 주면 그 스페이스의 주제를 읽는다 — 개인 → 팀 불러오기 화면이
  // **개인** 주제 목록을 보여야 해서(팀 것을 보여주면 고를 것이 없다).
  domains: (owner: string, scope?: Scope) => call("/domains", { owner }, undefined, scope),
  // 관례(GRAPH.md) — 읽기는 웹에서도 되지만(허브 _PROXY_OPS 등재), **어휘 재가는
  // 셸 전용**이다: 주제 어휘에는 owner 차원이 없어 계정 프록시가 의도적으로 막는다.
  conventions: (owner: string) => call("/conventions", { owner }),
  conventionsBuild: (owner: string, lang?: string) =>
    call("/conventions/build", undefined, { owner, lang }),
  themes: () => call("/themes"),
  types: (theme_id: string, status?: string) => call("/types", { theme_id, status }),
  typeRatify: (theme_id: string, name: string) =>
    call("/types/ratify", undefined, { theme_id, name }),
  typeReject: (theme_id: string, name: string) =>
    call("/types/reject", undefined, { theme_id, name }),
  typeRename: (theme_id: string, from_name: string, to_name: string) =>
    call("/types/rename", undefined, { theme_id, from_name, to_name }),
  activate: (owner: string, opts: Record<string, unknown> = {}) =>
    call("/activate", undefined, { owner, ...opts }),
  // scope 를 주면 그 스페이스에서 팩을 만든다(생략 = 현재 스페이스).
  // 팀→개인 공유는 scope 없이(=팀에서) 만들고, 개인→팀 불러오기는 {team:null} 로 만든다.
  share: (owner: string, opts: { domain?: string; seed?: string; depth?: number; ids?: string } = {},
          scope?: Scope) =>
    call("/share", { owner, ...opts }, undefined, scope),
  promoteCandidates: (owner: string) => call("/promote-candidates", { owner }),
  suggestTies: (owner: string, node: string) => call("/suggest-ties", { owner, node }),
  // D45-V — 노드 클릭 상세: 노드 설명(one_liner/summary) + 정본 근거 발췌.
  // arm: 'mentions'(언급 링크) | 'fts'(제목 일치 폴백) | 'none'(발췌 없음 — 무해).
  // 관계 수는 이 응답의 조회 성공 값만 쓴다. 선 표시 설정·화면 안내선으로 대신 세지 않는다.
  // props(D73): 개념의 자유 속성. `one_liner` 는 쓰기 경로가 없어 MCP 적재분이
  // 전량 빈 값이고 사람이 쓴 요약은 실제로 props 에 들어 있다 — 호출부는
  // one_liner → props 순으로 본문을 고른다. 구 사이드카엔 키가 없다(undefined).
  nodeContext: (owner: string, node: string, limit = 4, scope?: Scope,
                linksOffset = 0, evidence = false): Promise<NodeContext> =>
    call("/node-context", { owner, node, limit, links_offset: linksOffset,
      ...(evidence ? { evidence: 1 } : {}) }, undefined, scope),
  topicDescription: (owner: string, node: string, text: string, expected_content_hash: string,
                     scope?: Scope): Promise<TopicDescription> =>
    call("/topic-description", undefined, { owner, node, text, expected_content_hash }, scope),
  // D45-V2 2단계 — Reader 서브뷰: 정본 전문(무손실 복원 + 해시 검증) + 밑줄 범위.
  docGet: (owner: string, id: string, scope?: Scope): Promise<{
    executed: boolean; text?: string; title?: string; verified?: boolean;
    content_hash?: string; error?: string;
    // 쓰기 창·중단된 쓰기의 거절 표식(P17). 이 갈래에만 실린다 — `docMidWrite` 참조.
    chunk_count?: number;
    // D72 — 최초 기록 시각 · 내용 시점. 구 사이드카에는 키 자체가 없다.
    created_at?: string; content_time?: string;
  }> => call("/doc", { owner, id }, undefined, scope),
  docHighlights: (owner: string, doc_id: string, concept_id: string): Promise<{
    executed: boolean; ranges?: { char_start: number; char_end: number }[];
  }> => call("/doc/highlights", { owner, doc_id, concept_id }),
  // 리더 머리 — 이 문서가 **무엇에 걸려 있고**(개념 칩: 문서→노드, 여태 없던 방향)
  // **어떤 모양인가**(목차), 그리고 검색의 청크 히트가 착지할 오프셋. 세 필드가 전부
  // 옵셔널인 것은 구 사이드카 강등 규약 — 없으면 그 줄을 안 그린다.
  docContext: (owner: string, doc_id: string): Promise<{
    executed: boolean;
    concepts?: { id: string; title: string; domain: string; count: number }[];
    sections?: { title: string; level: number; char_start: number }[];
    chunks?: { index: number; char_start: number }[];
  }> => call("/doc/context", { owner, doc_id }),
  // D45-E — 앱 내 SSoT 편집(MVP): 저장(전체 재청킹, 재임베딩은 지연) + 개념 추출.
  // origin 은 출처 표시(파일 import 시 파일명) — 서버의 doc 정체성 산식에 참여하고
  // 재드롭 식별의 근거가 된다. 조인 키가 아니며 UI 에도 노출하지 않는다(D45).
  // contentTime(D72): **키가 없을 때와 빈 값일 때가 다르다.** undefined 면 보내지
  // 않아 서버가 기존 값을 보존하고, "" 는 사용자가 지운 것이다(그래서 falsy 를
  // 통째로 걸러내는 spread 관용구를 쓰지 않는다).
  // topic: 문서의 주제를 **적는다**(미러 노드 `concept:doc.<id>` 의 domain). 주면 첫 저장에
  // 미러가 생겨 트리와 지도에 곧바로 선다(`docstore.put_doc`). 빈 값은 싣지 않는다 —
  // 서버는 빈 주제를 "지운다"가 아니라 "안 건드린다"로 읽는다.
  docPut: (owner: string, text: string, id?: string, title?: string,
           contentTime?: string, origin?: string, topic?: string,
           expectedContentHash?: string): Promise<{
    executed: boolean; skipped?: boolean; doc_id?: string; content_hash?: string;
    chunks?: number; needs_embed?: boolean; content_time?: string; error?: string;
    created?: boolean; topic?: string;
  }> => call("/doc/put", undefined, { owner, text, ...(id ? { id } : {}),
                                      ...(title === undefined ? {} : { title: title.trim() }),
                                      ...(contentTime === undefined ? {} : { content_time: contentTime }),
                                      ...(origin ? { origin } : {}),
                                      ...(topic ? { topic } : {}),
                                      // 읽은 판의 해시. 다르면 서버가 쓰지 않고 `write_conflict` 를 준다.
                                      ...(expectedContentHash ? { expected_content_hash: expectedContentHash } : {}) }),
  // 파일 바이트 입구(D45) — 텍스트로 못 읽는 것(PDF)과 브라우저 UTF-8 디코드가
  // 망친 것(cp949 등)을 원본 바이트로 넘겨 서버 추출기에 맡긴다. multipart 가
  // 아니라 base64-in-JSON 인 이유는 계정 프록시가 JSON 만 통과시키기 때문.
  docPutFile: (owner: string, filename: string, data_b64: string,
               id?: string, title?: string): Promise<{
    executed: boolean; skipped?: boolean; doc_id?: string; source_format?: string;
    chunks?: number; needs_embed?: boolean; error?: string; note?: string;
  }> => call("/doc/put-file", undefined, { owner, filename, data_b64,
                                           ...(id ? { id } : {}),
                                           ...(title ? { title } : {}) }),
  // 재임베딩 큐에 되돌릴 뿐이다(벡터는 워커가 채운다). queued = 되돌린 문서 수.
  docReembed: (owner: string, id: string): Promise<{ executed: boolean; queued?: number }> =>
    call("/doc/reembed", undefined, { owner, id }),
  // U2 — 관계 근거 저작(정본 청크에서 결정론 추출). 반복 실행 안전(있는 근거는 보존).
  evidenceBackfill: (owner: string, docId?: string): Promise<{
    executed: boolean; filled?: number; scanned?: number;
  }> => call("/evidence/backfill", undefined, { owner, ...(docId ? { doc_id: docId } : {}) }),
  // candidates: 반영할 목록을 고른다(사람이 체크한 것만). 없으면 서버가 결정론 추출 전량.
  // id 는 서버가 제목 slug 로 다시 짓는다 — 여기서 id 를 실어 보내지 않는다.
  docExtract: (owner: string, id: string, apply = false,
               candidates?: { title: string; one_liner?: string; source?: string;
                 candidate_id?: string; doc_id?: string; source_hash?: string;
                 spans?: { char_start: number; char_end: number; text: string }[];
                 extractor?: { name: string; version: string; runtime: string }; label?: string }[],
               approvedCandidateIds?: string[]): Promise<{
    executed: boolean; candidates?: { title: string; one_liner?: string; slug?: string;
      source?: string; candidate_id?: string; doc_id?: string; source_hash?: string;
      spans?: { char_start: number; char_end: number; text: string }[];
      extractor?: { name: string; version: string; runtime: string }; label?: string;
      evidence?: string; offset_unit?: string }[];
    applied?: { created: number; matched: number }; mentions?: number;
    domain?: string; error?: string;
  }> => call("/doc/extract", undefined, { owner, id, apply,
                                          ...(candidates ? { candidates } : {}),
                                          ...(approvedCandidateIds === undefined ? {} :
                                            { approved_candidate_ids: approvedCandidateIds }) }),
  // 문서 목록. `topic`/`topics` 는 언급 개념에서 유도된 주제(문서에 주제 열은 없다),
  // `primary_concept_id` 는 **그 문서를 눌렀을 때 착지할 노드**(D69) — 대표 주제 안의
  // 최다 언급 개념이다. 언급이 없으면 빈 문자열이고 착지 대상이 없다(편집기로만 열린다).
  docList: (owner: string): Promise<{
    executed: boolean; count?: number;
    docs?: { doc_id: string; title: string; origin: string; chunks: number;
             updated_at: string; topic?: string; topics?: string[];
             primary_concept_id?: string; primary_concept_title?: string; valid_until?: string }[];
  }> => bootRead(`docs|${owner}`, () => call("/docs", { owner })).then(res => {
    // 커뮤니티 스페이스에서는 내린 페이지를 목록에서 뺀다(`withoutClosedDocs` 와 같은 규칙).
    if (!isCommunitySpace(cfg.team) || !res.docs) return res;
    const today = new Date().toISOString().slice(0, 10);
    const docs = res.docs.filter((d: { valid_until?: string }) => !d.valid_until || d.valid_until > today);
    return { ...res, docs, count: docs.length };
  }),
  // D69 — 좌측 문서 트리: 주제 → 그 주제의 문서(개념 계층 깊이순) + 미분류.
  //: `kind` 는 모집단을 그 종 **하나**로 좁힌다(종 레일). 목록을 종별로 가져가는
  //: 것이 계약이다 - 전부를 한 번에 싣는 값은 없고, 레일의 개수는 응답의
  //: `counts.kinds` 가 따로 준다(제목을 싣지 않는 집계).
  //: `domain` 은 그 주제의 **하위 트리**로 좁힌다. 이 값이 없던 동안 레일의 Agent
  //: 갈래는 주제를 선언만 하고 실어 보낼 자리가 없어 **모든 개념**을 세웠다.
  navTree: (owner: string, opts?: { nodes?: boolean; kind?: string; domain?: string;
                                     domains?: string[] },
           ): Promise<NavTreeRes> =>
    // 옵션 없는 기본 트리만 부팅 때 미리 읽어 둔 것을 꺼내 쓴다(`bootRead`).
    (opts ? (run: () => Promise<any>) => run() : (run: () => Promise<any>) => bootRead(`nav|${owner}`, run))(
    () => call("/nav-tree", { owner, ...(opts?.nodes ? { nodes: 1 } : {}),
                        ...(opts?.kind ? { kind: opts.kind } : {}),
                        ...(opts?.domain ? { domain: opts.domain } : {}),
                        // 하위 트리 여럿의 합(Agent 레일: 역할 주제 둘). `/graph` 와 같은 쉼표 꼴.
                        ...(opts?.domains?.length ? { domains: opts.domains.join(",") } : {}) }))
      .then((res: NavTreeRes) => isCommunitySpace(cfg.team) ? withoutClosedDocs(res) : res),
  // D45-V2 4단계 — 타임라인 기간(월-밴드) 클릭: [start, end) 의 날짜 있는 항목.
  // ⚠ basis 는 차트가 그리는 축과 **글자 그대로 같아야** 한다(D72) — 밴드를 눌러
  // 여는 목록이 다른 시간 열로 세면 화면이 스스로를 반박한다.
  docsInRange: (owner: string, start: string, end: string, domain?: string,
                basis?: TimeBasis): Promise<{
    executed: boolean; total?: number; basis?: string;
    items?: { id: string; kind: string; title: string; ts: string; domain: string }[];
  }> => call("/docs-in-range", { owner, start, end, ...(domain ? { domain } : {}),
                                 ...(basis ? { basis } : {}) }),
  // D45-V2 3단계 — 합계 구간 클릭: 그 (도메인, mark) 의 개념 목록(원장 수치 정합).
  ledgerDrilldown: (owner: string, domain: string, mark: string, seed?: string): Promise<{
    executed: boolean; total?: number;
    items?: { concept_id: string; title: string; one_liner: string }[];
  }> => call("/ledger-drilldown", { owner, domain, mark, ...(seed ? { seed } : {}) }),
  // 에이전트 쓰기 검토(A5) — 최근 쓰기를 출처별로 묶고 사람이 볼 검사를 단다(조회 전용).
  agentWrites: (owner: string, days = 14): Promise<import("./agent-writes").AgentWrites> =>
    call("/agent-writes", { owner, days }),
  // D45-V 1단계 — 흐름 선 클릭: 관계의 evidence·confidence + 양끝 노드 메타/발췌.
  edgeContext: (owner: string, src: string, dst: string, type: string): Promise<{
    executed: boolean; evidence?: string; confidence?: number;
    src?: { id: string; title?: string; one_liner?: string;
            excerpt?: { doc_id: string; excerpt: string } | null };
    dst?: { id: string; title?: string; one_liner?: string;
            excerpt?: { doc_id: string; excerpt: string } | null };
  }> => call("/edge-context", { owner, src, dst, type }),
  // fragment MERGE(D11 §5) — Bind 확정도 이 단일 쓰기 표면을 그대로 쓴다(D32)
  ingestFragment: (fragment: unknown) => call("/ingest/fragment", undefined, fragment),
  // scope 를 주면 그 스페이스로 적재한다(생략 = 현재 스페이스). 스페이스 간 이동은
  // "한쪽에서 share → 다른 쪽으로 importPack" 이라는 한 가지 동작으로 통일돼 있다.
  importPack: importPackInChunks,
  backup: (out: string) => call("/backup", undefined, { out }),
  pin: (episode: string) => call("/pin", undefined, { episode }),

  // S2 — 도메인 일괄 변경. new='' 는 미분류로 내림(노드는 남는다). 전역 Domain
  // 노드는 서버가 안 건드린다 — 내 노드의 domain 문자열만 바뀐다.
  domainRename: (owner: string, old: string, next: string): Promise<{
    executed: boolean; moved?: number; error?: string;
  }> => call("/domain/rename", undefined, { owner, old, new: next }),

  // ── 삭제(S1) — 앱 UI 배선. 여태 백엔드·MCP 에만 있고 UI 가 없어 "추가는 원클릭,
  // 제거는 불가"였다. owner 는 웹모드에선 허브가 세션 sub 로 강제(바디 owner 무시),
  // 로컬 셸에선 여기서 넘긴 값이 스코프가 된다. 되돌릴 수 없으므로 호출부가 확인을 받는다.
  //
  // 0.9.8 계약: 확인 창을 열 때 `preview*` 로 미리보기를 받아 두고(digest 와 operation_id),
  // 확인하면 그 묶음을 실어 적용한다. 그 사이 대상이 바뀌었으면 서버가 지우지 않고
  // `preview_conflict` 로 답한다(`isWriteConflict`).
  previewDeleteNode: (owner: string, node: string): Promise<DeleteGuard> =>
    previewDelete("/delete", { owner, node }),
  deleteNode: (owner: string, node: string, guard?: DeleteGuard): Promise<{
    executed: boolean; found?: boolean;
    deleted?: { node: number; edges: number }; note?: string; error?: string; status?: string;
  }> => applyDelete("/delete", { owner, node }, guard),
  // `deleted` 는 수가 아니라 `{edges: n}` 이다(`graph_edit.delete_edge`). 지울 수 없는 관계
  // 종류(부착 등)는 executed:true 에 `note` 만 싣고 `deleted` 가 없다.
  previewDeleteEdge: (owner: string, from: string, to: string, type: string): Promise<DeleteGuard> =>
    previewDelete("/delete-edge", { owner, from, to, type }),
  deleteEdge: (owner: string, from: string, to: string, type: string, guard?: DeleteGuard): Promise<{
    executed: boolean; found?: boolean; deleted?: { edges: number }; note?: string; error?: string;
    status?: string;
  }> => applyDelete("/delete-edge", { owner, from, to, type }, guard),
  previewDocDelete: (owner: string, id: string, scope?: Scope): Promise<DeleteGuard> =>
    previewDelete("/doc/delete", { owner, id }, scope),
  docDelete: (owner: string, id: string, scope?: Scope, guard?: DeleteGuard): Promise<{
    executed: boolean; deleted_meta?: number; deleted_chunks?: number; error?: string; status?: string;
  }> => applyDelete("/doc/delete", { owner, id }, guard, scope),

  // 0.9.8 조건부 노드 쓰기. 서버가 지금 판(rev)을 `rev` 와 대조하고 다르면 쓰지 않고
  // `status:"conflict"` 로 답한다(`api/mutation_contract.py`). props 일부만 바꿀 때는
  // `props_patch`(JSON Merge Patch)를 실어 서버가 저장된 props 에 합치게 한다.
  // owner 를 싣는 이유: 판 확인은 owner 스코프로 읽는다. 빼면 로컬 기본 owner 로 읽어
  // 견본 스페이스의 노드가 "없음"으로 거절된다. 계정·웹은 허브가 세션 owner 로 바꾼다.
  // 근거 기록은 tests/app/test_node_props_edit.py 머리말이 가리킨다.
  editNode: (node: { id: string; type: string } & Record<string, unknown>, rev: number) =>
    call("/ingest/fragment", undefined, { owner: cfg.owner, nodes: [{ ...node, expected_rev: rev }] }),

  // 계정 연결(디바이스 플로우) — start 로 state 발급, 브라우저 승인 후 status 폴링으로
  // 키를 1회 수령한다(사이드카 메일박스). 셸 전용 UI(settings)에서만 쓴다.
  connectStart: () => call("/connect/start", undefined, {}),
  connectStatus: (state: string) => call("/connect/status", { state }),
  jobStatus: (jobId: string) => call(`/jobs/${jobId}`),
};

/** Account-owned control plane, shared by web sessions and desktop member keys. */
export const conversionApi = {
  capabilities: (): Promise<{ enabled: boolean; subject: string; pending?: { id: string; name: string; topic: string; fingerprint: string; conventionsPolicy?: ConventionsPolicy } | null }> =>
    call("/conversion/capabilities", undefined, undefined, { team: null }),
  preflight: async (topic: string, conventions_policy: ConventionsPolicy = "retain"): Promise<ConversionCheck> => {
    const { waitForInventory } = await import("./conversion-inventory");
    return waitForInventory(topic,
      () => call("/conversion/preflight", undefined, { topic, conventions_policy }, { team: null }),
      ticket => call(`/conversion/inventory/${encodeURIComponent(ticket)}`, undefined, undefined, { team: null }));
  },
  reserve: (body: { id: string; name: string; topic: string; fingerprint: string; conventions_policy?: ConventionsPolicy }): Promise<ConversionRun> =>
    call("/conversion/runs", undefined, body, { team: null }),
  status: (id: string): Promise<ConversionRun> => call(`/conversion/runs/${encodeURIComponent(id)}`, undefined, undefined, { team: null }),
  resume: (id: string): Promise<ConversionRun> => call(`/conversion/runs/${encodeURIComponent(id)}/resume`, undefined, {}, { team: null }),
  cancel: (id: string): Promise<ConversionRun> => call(`/conversion/runs/${encodeURIComponent(id)}/cancel`, undefined, {}, { team: null }),
};

// 웹 첫 화면의 두 읽기(사이드바 트리·문서 목록)를 세션 확인과 **나란히** 띄운다. 차례로
// 기다리면 /api/me 왕복 뒤에야 첫 데이터 요청이 나가서 빈 화면이 그만큼 길어진다.
// 한 번만 쓰이고 몇 초 뒤에는 버린다: 늦게 꺼내 쓰면 그 사이의 쓰기가 안 보인다.
// 스페이스는 키에 들어간다. 주소가 다른 스페이스를 들고 오면 아예 미리 읽지 않는다.
const BOOT_READ_MS = 10_000;
const bootReads = new Map<string, { at: number; p: Promise<any> }>();
function bootRead<T>(key: string, run: () => Promise<T>): Promise<T> {
  const k = `${key}|${cfg.team}`;
  const hit = bootReads.get(k);
  if (!hit) return run();
  bootReads.delete(k);
  if (Date.now() - hit.at > BOOT_READ_MS) return run();
  return hit.p.catch(() => run());
}
function primeBootReads(): void {
  if (new URLSearchParams(location.search).get("space")) return;
  const owner = cfg.owner;
  const at = Date.now();
  const docs = api.docList(owner);
  const nav = api.navTree(owner);
  // 아무도 안 꺼내 가도 거절이 처리되지 않은 채 남지 않게 한다.
  docs.catch(() => {}); nav.catch(() => {});
  bootReads.set(`docs|${owner}|${cfg.team}`, { at, p: docs });
  bootReads.set(`nav|${owner}|${cfg.team}`, { at, p: nav });
}

/** 웹모드 부팅 인증 게이트 — /api/me 로 세션을 **먼저** 확인한다. 미인증이면 서피스를
 *  렌더하기 전에 /login 으로 보내고 false 를 돌려준다(main.ts 가 렌더를 중단). 이게 없으면
 *  앱이 화면을 먼저 그린 뒤 데이터 호출이 401 을 받고서야 리다이렉트돼 "화면 깜빡임 → 로그인"
 *  이 된다. 셸/브라우저 dev(WEB=false)에서는 호출되지 않는다(세션 개념 없음). */
export async function ensureWebAuth(): Promise<boolean> {
  // 빠른 경로(왕복 0): 세션이 서면 __Host-csrf(JS-readable)가 함께 세팅된다. 그 쿠키가
  // 아예 없으면 로그인한 적 없음 → 서버 확인 없이 즉시 /login (never-logged-in = 흔한
  // 경우, 완전 즉시 리다이렉트). 쿠키가 있으면 만료됐을 수 있어 /api/me 로 검증한다.
  if (!CSRF_COOKIE.test(document.cookie)) {
    viewSessionCache.purge();       // 로그아웃된 브라우저에 보관본을 남기지 않는다
    location.assign("/login");
    return false;
  }
  primeBootReads();
  try {
    const res = await api.me();
    if (res.ok) {
      // 셀프호스트 허브는 여기서 자기를 밝힌다(`self-hosted.ts`). 본문이 JSON 이 아니어도 세션은 섰다.
      noteMe(await res.clone().json().catch(() => null));
      return true;
    }
  } catch { /* 네트워크 실패 → 아래 로그인으로(안전측) */ }
  viewSessionCache.purge();
  location.assign("/login");
  return false;
}

/** 셀프호스트 허브에서 로그아웃한다(`POST /logout`, CSRF 더블서브밋). 끝나면 로그인 화면으로 간다.
 *  호스티드 웹은 계정 사이트가 로그아웃을 맡으므로 부르지 않는다. */
export async function selfHostedSignOut(): Promise<void> {
  try {
    await webFetch("/logout", { method: "POST" });
  } catch { /* 세션이 남아도 아래에서 로그인 화면으로 간다 */ }
  viewSessionCache.purge();
  location.assign("/login");
}

/** 셀프호스트 허브의 좌석 상태(`GET /api/selfhost/seats`). 못 읽으면 null 이고 화면은 그 줄을 비운다. */
export async function selfHostedSeats(): Promise<any | null> {
  try {
    const r = await webFetch("/api/selfhost/seats");
    return r.ok ? await r.json() : null;
  } catch { return null; }
}

/** 잡 완료까지 폴링. jobs.py 계약: 상태 queued|running|done|error, 키는 job_id. */
export async function pollJob(jobId: string, onTick?: (s: any) => void,
                             { intervalMs = 900, timeoutMs = 300000,
                               status = api.jobStatus } = {}): Promise<any> {
  const t0 = Date.now();
  for (;;) {
    const s = await status(jobId);
    onTick?.(s);
    if (s.status === "done") return s;
    if (s.status === "error") throw new Error(s.error || "sync job failed");
    if (Date.now() - t0 > timeoutMs) throw new Error("sync timed out");
    await sleep(intervalMs);
  }
}
