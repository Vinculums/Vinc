import { api, viewAccess, viewScope, WEB, type GraphNode, type GraphEdge } from "./api";
import { viewSessionCache } from "./view-session-cache";
import { readGraphProgressively, type GraphRead } from "./view-graph-loading";
import { graphReadPlan, wantsStagedRead } from "./view-graph-read";
import { layoutInWorker } from "./view-layout-client";
import { applyLayoutAnswer, placeOnAnswer, type LayoutAnswer } from "./view-layout-wire";
import type { ViewPrefs } from "./view-prefs";
import type { LayoutGraph } from "./view-layout";

/** 한 번의 View 마운트가 그래프를 읽고 필요하면 가지 배치까지 얹어 돌려주는 곳.
 *
 * `surfaces/view.ts` 의 마운트 클로저에 있던 `fetchGraph` 와 그 상태를 옮겼다. 마운트가
 * 주는 것은 `GraphLoaderContext` 로만 들어오고, 바깥이 만지는 것은 이 객체의 메서드뿐이다.
 *
 * 상태가 둘이다:
 *  - 마운트별(`raw`·`layout`): 같은 마운트 안에서 옵션만 바뀔 때 서버와 워커를 건너뛴다.
 *    렌더러가 좌표를 바꿔도 되는 같은 객체를 쓴다.
 *  - 탭별(`viewSessionCache`): 직렬화된 완료 DTO. 다른 마운트로 돌아올 때 쓴다.
 * 둘을 합치지 않은 것은 의도다. 앞의 것은 객체 동일성에 기대고, 뒤의 것은 복사본이어야
 * 렌더러의 변형이 다음 마운트에 번지지 않는다. */
/** `marker` 는 서버가 읽기 전에 잡은 변경 표식이다(요청에 `marker=1` 을 실었을 때만 온다). */
type RawGraph = { executed: boolean; nodes?: GraphNode[]; edges?: GraphEdge[]; topicEdges?: GraphEdge[];
  marker?: string };
/** `layout`: 시간순 가지를 요청했는데 그 배치가 아직 없을 때 무엇을 그렸는가.
 *  - `pending`: 배치가 오래 걸려 서버의 중심에서 좌표로 먼저 그렸다. 끝나면 `layoutReady` 가 불린다.
 *  - `failed`: 배치가 실패했거나 진척이 멈췄다. 중심에서 좌표를 그대로 쓴다.
 *  근거: [[vinc.plan.incremental-derived-view-results-2026-10-12]] §4 의 1 */
type FetchedGraph = RawGraph & { stale?: boolean; layout?: "pending" | "failed" };

/** 시간순 가지 배치를 이만큼 기다린 뒤에는 중심에서 좌표로 먼저 그린다. */
const LAYOUT_PATIENCE_MS = 15_000;
/** 큰 저장 배치의 전송도 기다리되, 화면은 그 전에 patience 기준으로 넘긴다. */
const ROOTS_READ_TIMEOUT_MS = 30_000;
/** 허브의 낡은 답으로 그린 뒤 새 답을 이 간격으로 묻는다. 답 본문은 바뀌었을 때만 온다(`have`). */
const FOLLOW_EVERY_MS = 3_000;
/** 그만 묻는 때. 허브가 배치 컨테이너를 기다리는 시간(300초)보다 길게 잡는다. */
const FOLLOW_FOR_MS = 6 * 60_000;

type GraphLoaderContext = {
  mountScope: ReturnType<typeof viewScope>;
  accessJob: AbortController;
  firstAccess: Promise<{ session: string; revision: string } | null>;
  restoreEntryCamera: boolean;
  /** 지금 화면 세대. 읽기가 시작될 때 잡아 두고 모든 await 뒤에 같은 값과 견준다. */
  seq(): number;
  /** 마운트가 아직 살아 있고 그 캔버스가 문서에 붙어 있는가. */
  alive(): boolean;
  canvasRatio(): number;
  mounted(): boolean;
  /** 카메라 복원에 쓰는 키: 설정과 캔버스 비율. */
  cameraPrefs(p: ViewPrefs): string;
  /** 부분 개요를 그리고 그것을 지우는 함수를 돌려준다. */
  showPreview(graph: GraphRead): () => void;
  /** 되돌린 카메라를 마운트에 넘긴다(다음 마운트에만 쓰인다). */
  restoreCameras(camera3: unknown): void;
  /** 먼저 그려 둔 중심에서 화면 뒤에서 시간순 가지 배치가 끝났다(같은 화면일 때만 불린다). */
  layoutReady(): void;
  /** 그 배치가 실패했다(같은 화면일 때만 불린다). */
  layoutFailed(): void;
  /** 시험용으로 기다리는 시간을 바꾼다. 없으면 `LAYOUT_PATIENCE_MS`. */
  patienceMs?: number;
  /** 시험용 저장 배치 읽기 제한. 화면 대기 시간은 patienceMs와 별개다. */
  rootsTimeoutMs?: number;
  /** 시험용 따라 묻기 간격과 기한. 없으면 `FOLLOW_EVERY_MS`·`FOLLOW_FOR_MS`. */
  followEveryMs?: number;
  followForMs?: number;
};

type RetainedRef = { session: string; revision: string; key: string; prefs: string };

/** 커뮤니티 스페이스의 그래프에서 내린 페이지를 뺀다. 페이지 노드는 `props.status` 가 `retired` 이고,
 *  그 문서의 미러 노드(`concept:doc.<props.doc_id>`)도 함께 뺀다. 둘 다 관계가 없어 떠 있는 점으로만 남는다.
 *  개인·팀 그래프에는 쓰지 않는다: 그쪽에는 이 표식이 없고, 있어도 그 사람이 찾아볼 수 있어야 한다. */
export function withoutRetiredPages<T extends { nodes?: GraphNode[]; edges?: GraphEdge[] }>(graph: T): T {
  const gone = new Set<string>();
  for (const n of graph.nodes || []) {
    const props = (n as { props?: { status?: unknown; doc_id?: unknown } }).props;
    if (props?.status !== "retired") continue;
    gone.add(n.id);
    if (typeof props.doc_id === "string" && props.doc_id) gone.add(`concept:doc.${props.doc_id}`);
  }
  if (!gone.size) return graph;
  return { ...graph, nodes: graph.nodes?.filter(n => !gone.has(n.id)),
    edges: graph.edges?.filter(e => !gone.has(e.source) && !gone.has(e.target)) };
}

export function createGraphLoader(ctx: GraphLoaderContext) {
  let rawGraph: RawGraph | undefined;
  let rawGraphScope: string | undefined;
  let layoutCache: { raw: RawGraph | undefined; basis: ViewPrefs["timeBasis"]; layout: ViewPrefs["layout"];
    aspectRatio: number; graph: LayoutGraph } | undefined;
  let layoutJob: AbortController | undefined;
  let graphReadJob: AbortController | undefined;
  /** 허브가 새 배치를 낼 때까지 따라 묻는 일. `abort()`(매 다시 그리기)는 이것을 접지 않는다. */
  let followJob: AbortController | undefined;
  let clearPreview: (() => void) | undefined;
  let firstAccessUsed = false;

  const loader = {
    /** 다음 읽기가 서버를 건너뛸 수 있는가(좌표·데이터는 그대로이고 거르는 것만 바뀐 경우). */
    reuseGraph: false,
    /** 마지막으로 보관소와 맞춘 항목. dispose 가 카메라 상태를 이어 붙인다. */
    retained: undefined as RetainedRef | undefined,
    get scopeKey() { return rawGraphScope; },

    removePreview() { clearPreview?.(); clearPreview = undefined; },
    /** 진행 중인 읽기와 배치를 접는다. */
    abort() { graphReadJob?.abort(); layoutJob?.abort(); },
    /** 쓰기·로그아웃 뒤: 이 마운트가 들고 있던 것을 전부 버린다. */
    reset() {
      viewSessionCache.clear(); rawGraph = undefined; layoutCache = undefined; loader.reuseGraph = false;
      followJob?.abort();
    },

    async fetchGraph(p: ViewPrefs, scope: { domains?: string[] }): Promise<FetchedGraph> {
      const { useRoots, scopeKey, options } = graphReadPlan(p, scope.domains || []);
      let cached = loader.reuseGraph && rawGraph?.executed && rawGraphScope === scopeKey && rawGraph;
      loader.reuseGraph = false;
      const mySeq = ctx.seq();
      // 이 읽기를 낳은 마운트·세대·스코프가 아직 화면의 주인인가. 모든 await 뒤에 같은 식으로 묻는다.
      const live = () => mySeq === ctx.seq() && ctx.alive() && ctx.mountScope.key === viewScope().key;
      layoutJob?.abort();
      const start = performance.now();
      let res: RawGraph | undefined;
      const canRetain = WEB;
      const access = canRetain
        ? (firstAccessUsed ? viewAccess(ctx.mountScope, ctx.accessJob.signal).catch(() => null) : ctx.firstAccess)
        : Promise.resolve(null);
      firstAccessUsed = true;
      // 재사용할 후보가 하나도 없으면 표식 확인을 기다리지 않고 읽기를 바로 시작한다. 이때
      // 읽기의 라벨은 확인 결과가 아니라 **응답이 실어 온 표식**(서버가 읽기 전에 잡은 값)이고,
      // 보관은 읽기가 끝난 뒤 같은 표식이 다시 확인될 때만 한다. 후보가 있으면 그것을 쓸 수
      // 있는지 표식으로 먼저 가려야 하므로 예전 순서(확인 → 읽기)를 그대로 쓴다.
      const early = canRetain && !cached && !viewSessionCache.count && !viewSessionCache.hasBookmark;
      // Confirm the marker before starting the graph snapshot; a late probe cannot label an older read.
      const grant = canRetain && !early ? await access : null;
      if (!live()) return { executed: false, stale: true };
      if (canRetain && !early && (!grant || loader.retained?.session !== grant.session
          || loader.retained?.revision !== grant.revision)) cached = false;
      if (grant) {
        viewSessionCache.activate(grant.session, grant.revision);
        cached ||= viewSessionCache.get(grant.session, scopeKey, grant.revision);
        if (cached) {
          loader.retained = { ...grant, key: scopeKey, prefs: ctx.cameraPrefs(p) };
          const state = viewSessionCache.getState(grant.session, scopeKey, grant.revision, ctx.cameraPrefs(p));
          if (ctx.restoreEntryCamera && !ctx.mounted() && state) ctx.restoreCameras(state.camera3);
        }
      } else if (canRetain && !early && viewSessionCache.count) viewSessionCache.clear();
      const generation = viewSessionCache.generation;
      graphReadJob?.abort();
      const readJob = graphReadJob = new AbortController();
      const capturedScope = { team: ctx.mountScope.team || null };
      // 저장 배치는 전체 주제·원뿔 1 읽기에만 맞는다. 준비 여부를 기다리다 그래프나
      // 15초 대체 배치가 막히지 않도록 답을 기다리는 시간도 아래 배치 작업에 포함한다.
      const roots = useRoots && !options.domains?.length && (options.cone ?? 1) === 1
        ? api.graphRoots(ctx.mountScope.owner, p.timeBasis,
          AbortSignal.any([readJob.signal, AbortSignal.timeout(ctx.rootsTimeoutMs ?? ROOTS_READ_TIMEOUT_MS)]),
          capturedScope).catch(() => null)
        : Promise.resolve(null);
      try {
        if (wantsStagedRead(p, { cached: !!cached, mounted: ctx.mounted() })) {
          const job = readJob;
          const owner = ctx.mountScope.owner;
          res = await readGraphProgressively((stage, token) =>
            api.graphStage(owner, options, stage, token, capturedScope, job.signal), preview => {
              if (job.signal.aborted || !live()) return;
              clearPreview?.(); clearPreview = ctx.showPreview(preview);
            }, job.signal);
        } else if (cached) res = cached;
        else {
          const job = readJob;
          res = await api.graphStage(ctx.mountScope.owner, options, undefined, undefined,
            capturedScope, job.signal);
        }
      } catch (failure) {
        const cancelled = readJob.signal.aborted;
        readJob.abort();
        if (cancelled || !live()) return { executed: false, stale: true };
        console.error(`View graph read failed ${JSON.stringify({ elapsedMs: Math.round(performance.now() - start),
          status: Number((failure as { status?: number }).status) || 0 })}`);
        throw failure;
      }
      console.info(`View graph read ${JSON.stringify({ elapsedMs: Math.round(performance.now() - start), cached: !!cached,
        executed: !!res?.executed, nodes: res?.nodes?.length || 0, edges: res?.edges?.length || 0 })}`);
      if (!live()) return { executed: false, stale: true };
      // 커뮤니티 스페이스(`community:<uuid>`)는 내린 페이지를 그리지 않는다. 걸러진 것을 보관하므로 두 번 걸러도 같다.
      if (res?.executed && String(ctx.mountScope.team || "").startsWith("community:")) res = withoutRetiredPages(res);
      rawGraph = res;
      rawGraphScope = scopeKey;
      if (!res) return { executed: false };
      const remember = (layout?: { key: string; graph: LayoutGraph }): Promise<void> | undefined => {
        if (!canRetain || (cached && !layout) || !res?.executed) return;
        // 두 표식이 일치할 때만 보관한다. 저장 검증은 첫 렌더를 기다리게 하지 않는다.
        return access.then(async before => {
          if (!before || ctx.accessJob.signal.aborted || generation !== viewSessionCache.generation) return;
          // 바로 시작한 읽기는 응답의 표식으로, 확인 뒤에 시작한 읽기는 그 확인의 표식으로 라벨한다.
          // 서버가 표식을 안 실어 주면(옛 허브) 라벨이 없으므로 보관하지 않는다.
          const label = early ? res!.marker : before.revision;
          if (typeof label !== "string" || !label) return;
          const after = await viewAccess(ctx.mountScope, ctx.accessJob.signal).catch(() => null);
          if (!after || before.session !== after.session || after.revision !== label
              || !live() || generation !== viewSessionCache.generation) return;
          const ticket = viewSessionCache.activate(before.session, label);
          if (viewSessionCache.put(before.session, scopeKey, label, res!, ticket, layout))
            loader.retained = { session: before.session, revision: label, key: scopeKey, prefs: ctx.cameraPrefs(p) };
        });
      };
      if (!useRoots || !res.executed) { followJob?.abort(); remember(); return res; }
      const aspectRatio = ctx.canvasRatio();
      // 같은 배치를 다시 그리는 길만 허브 따라 묻기를 잇는다. 나머지 길은 다른 배치를 그린다.
      if (layoutCache && layoutCache.raw === res && layoutCache.basis === p.timeBasis
          && layoutCache.layout === p.layout && layoutCache.aspectRatio === aspectRatio)
        return { executed: true, ...layoutCache.graph };
      followJob?.abort();
      const layoutKey = JSON.stringify([p.timeBasis, p.layout, aspectRatio]);
      const retainedLayout = grant && cached && viewSessionCache.getLayout(grant.session, scopeKey, grant.revision, layoutKey);
      if (retainedLayout) {
        layoutCache = { raw: res, basis: p.timeBasis, layout: p.layout, aspectRatio, graph: retainedLayout };
        console.info("View layout reused");
        return { executed: true, ...retainedLayout };
      }
      layoutJob?.abort();
      const job = layoutJob = new AbortController();
      const held = res;
      /** `retain`: 보관소에도 둔다. 허브의 낡은 답으로 그린 대역은 이 마운트에만 둔다. */
      const keep = (graph: LayoutGraph, retain = true) => {
        layoutCache = { raw: held, basis: p.timeBasis, layout: p.layout, aspectRatio, graph };
        return retain ? remember({ key: layoutKey, graph }) : undefined;
      };
      /** 허브의 답을 이 읽기에 얹는다. 모르는 노드가 있으면 아는 자리 옆에 세운다(배치 엔진 없음). */
      const onAnswer = (answer: LayoutAnswer): { graph: LayoutGraph; whole: boolean } | null => {
        try {
          const whole = applyLayoutAnswer(held.nodes || [], held.edges || [], answer);
          if (whole) return { graph: whole, whole: true };
          return { graph: placeOnAnswer(held.nodes || [], held.edges || [], answer), whole: false };
        } catch { return null; }
      };
      // 허브가 쓴 답의 성격. `exact` 이면 예전처럼 그대로 쓰고 보관한다. 아니면 낡은 답으로 먼저
      // 그리고, 허브가 새 답을 만들 때까지 따라 물어 갈아 끼운다(쓰기·재시작·배치 배포 뒤).
      // 근거: [[vinc.reference.view-time-branches-layout]] §4
      const fromHub: { exact?: boolean; follow?: boolean; digest?: string; used?: boolean } = {};
      const follow = (applied: string | undefined) => {
        followJob?.abort();
        const fj = followJob = new AbortController();
        const owner = ctx.mountScope.owner, scope = capturedScope;
        // 화면 세대(`live`)는 보지 않는다: 갈아 끼우기 자체가 같은 그래프로 다시 그리기(세대 +1)이고,
        // 옵션만 바꾼 다시 그리기도 이 배치를 그대로 쓴다. 다른 배치로 넘어가면 `followJob` 이 접힌다.
        const followLive = () => !fj.signal.aborted && ctx.alive() && ctx.mountScope.key === viewScope().key;
        void (async () => {
          let have = applied, next: LayoutAnswer | undefined, nextMarker: string | undefined;
          const until = performance.now() + (ctx.followForMs ?? FOLLOW_FOR_MS);
          while (performance.now() < until) {
            await new Promise(done => setTimeout(done, ctx.followEveryMs ?? FOLLOW_EVERY_MS));
            if (!followLive()) return;
            const r = await api.graphRoots(owner, p.timeBasis,
              AbortSignal.any([fj.signal, AbortSignal.timeout(ctx.rootsTimeoutMs ?? ROOTS_READ_TIMEOUT_MS)]),
              scope, have).catch(() => null);
            if (!followLive()) return;
            if (!r) continue;
            if (!r.executed || r.state === "off") return;
            if (r.answer && (!r.digest || r.digest !== have)) { next = r.answer; have = r.digest; nextMarker = r.marker; }
            if (r.state === "ready" || r.state === "failed") break;
          }
          if (!next || !followLive()) return;
          const swapped = onAnswer(next);
          if (!swapped) return;
          await keep(swapped.graph, swapped.whole && !!held.marker && nextMarker === held.marker);
          if (!followLive()) return;
          console.info("View layout swapped to the hub's fresh answer");
          ctx.layoutReady();
        })();
      };
      // 결과가 오기 전에 화면을 넘겨줄 수 있도록 배치 작업과 기다림을 나눈다. 큰 그래프에서
      // 배치가 기다림보다 길어도 작업은 계속 돌고, 끝나면 같은 화면에 갈아 끼운다.
      let outcome: { graph?: LayoutGraph; error?: unknown } | undefined;
      const work = (async () => {
        const stored = await roots;
        if (readJob.signal.aborted || job.signal.aborted || !live())
          throw new DOMException("Layout read was canceled", "AbortError");
        // 허브가 답을 하나라도 들고 있으면 브라우저는 전체 배치를 하지 않는다. 답이 아예 없을
        // 때(그 공간을 처음 열 때)만 워커로 간다.
        const usable = stored?.executed && stored.state !== "off" && stored.answer ? onAnswer(stored.answer) : null;
        if (stored && usable) {
          const exact = usable.whole && stored.state === "ready" && typeof held.marker === "string"
            && !!held.marker && stored.marker === held.marker;
          // 허브가 배치에 실패한 상태면 10분 동안 다시 하지 않으므로 따라 묻지 않는다.
          Object.assign(fromHub, { used: true, exact, follow: !exact && stored.state !== "failed", digest: stored.digest });
          console.info(`View layout loaded from hub ${JSON.stringify({ state: stored.state, exact, whole: usable.whole })}`);
          return usable.graph;
        }
        return layoutInWorker(held.nodes || [], held.edges || [], p.timeBasis, job.signal,
          undefined, aspectRatio);
      })()
        .then(graph => { outcome = { graph }; return graph; }, error => { outcome = { error }; throw error; })
        .finally(() => { if (layoutJob === job) layoutJob = undefined; });
      let patience: ReturnType<typeof setTimeout> | undefined;
      await Promise.race([work.catch(() => undefined),
        new Promise<void>(done => { patience = setTimeout(done, ctx.patienceMs ?? LAYOUT_PATIENCE_MS); })]);
      clearTimeout(patience);
      if (job.signal.aborted || !live()) return { executed: false, stale: true };
      // 허브의 낡은 답으로 그린 대역은 보관하지 않고, 허브의 새 답을 따라 묻는다.
      const exactOrLocal = () => !fromHub.used || !!fromHub.exact;
      const followHub = () => { if (fromHub.follow) follow(fromHub.digest); };
      if (outcome?.graph) {
        keep(outcome.graph, exactOrLocal()); followHub();
        return { executed: true, ...outcome.graph };
      }
      // 실패하면 오류 상자가 아니라 서버가 이미 준 중심에서 좌표로 그린다.
      if (outcome) return { ...held, executed: true, layout: "failed" };
      void work.then(async graph => {
        if (job.signal.aborted || !live()) return;
        // 보관이 끝난 뒤에 갈아 끼운다. 먼저 다시 그리면 재사용 검증이 보관 전 상태를 보고
        // 그래프를 다시 읽어 배치를 처음부터 시작한다.
        await keep(graph, exactOrLocal());
        if (job.signal.aborted || !live()) return;
        ctx.layoutReady();
        followHub();
      }, () => { if (!job.signal.aborted && live()) ctx.layoutFailed(); });
      return { ...held, executed: true, layout: "pending" };
    },
  };
  return loader;
}
