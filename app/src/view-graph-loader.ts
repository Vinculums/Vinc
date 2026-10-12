import { api, cfg, viewAccess, viewScope, WEB, type GraphNode, type GraphEdge } from "./api";
import { viewSessionCache } from "./view-session-cache";
import { readGraphProgressively, type GraphRead } from "./view-graph-loading";
import { graphReadPlan, wantsStagedRead } from "./view-graph-read";
import { layoutInWorker } from "./view-layout-client";
import { rootsOrderingFor, type ViewPrefs } from "./view-prefs";
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
export type RawGraph = { executed: boolean; nodes?: GraphNode[]; edges?: GraphEdge[]; topicEdges?: GraphEdge[] };
export type FetchedGraph = RawGraph & { stale?: boolean };

export type GraphLoaderContext = {
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
  planar(): boolean;
  /** 카메라 복원에 쓰는 키: 설정과 캔버스 비율. */
  cameraPrefs(p: ViewPrefs): string;
  /** 부분 개요를 그리고 그것을 지우는 함수를 돌려준다. */
  showPreview(graph: GraphRead): () => void;
  /** 되돌린 카메라를 마운트에 넘긴다(다음 마운트에만 쓰인다). */
  restoreCameras(camera3: unknown, camera2: unknown): void;
};

export type RetainedRef = { session: string; revision: string; key: string; prefs: string };

export function createGraphLoader(ctx: GraphLoaderContext) {
  let rawGraph: RawGraph | undefined;
  let rawGraphScope: string | undefined;
  let layoutCache: { raw: RawGraph | undefined; basis: ViewPrefs["timeBasis"]; layout: ViewPrefs["layout"];
    aspectRatio: number; graph: LayoutGraph } | undefined;
  let layoutJob: AbortController | undefined;
  let graphReadJob: AbortController | undefined;
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
      const canRetain = WEB && ["2d", "3d"].includes(p.view);
      const access = canRetain
        ? (firstAccessUsed ? viewAccess(ctx.mountScope, ctx.accessJob.signal).catch(() => null) : ctx.firstAccess)
        : Promise.resolve(null);
      firstAccessUsed = true;
      // Confirm the marker before starting the graph snapshot; a late probe cannot label an older read.
      const grant = canRetain ? await access : null;
      if (!live()) return { executed: false, stale: true };
      if (canRetain && (!grant || loader.retained?.session !== grant.session
          || loader.retained?.revision !== grant.revision)) cached = false;
      if (grant) {
        viewSessionCache.activate(grant.session, grant.revision);
        cached ||= viewSessionCache.get(grant.session, scopeKey, grant.revision);
        if (cached) {
          loader.retained = { ...grant, key: scopeKey, prefs: ctx.cameraPrefs(p) };
          const state = viewSessionCache.getState(grant.session, scopeKey, grant.revision, ctx.cameraPrefs(p));
          if (ctx.restoreEntryCamera && !ctx.mounted() && state) ctx.restoreCameras(state.camera3, state.camera2);
        }
      } else if (canRetain && viewSessionCache.count) viewSessionCache.clear();
      const generation = viewSessionCache.generation;
      try {
        if (wantsStagedRead(p, { cached: !!cached, mounted: ctx.mounted(), planar: ctx.planar() })) {
          const job = graphReadJob = new AbortController();
          const capturedScope = { team: cfg.team || null };
          const owner = cfg.owner;
          res = await readGraphProgressively((stage, token) =>
            api.graphStage(owner, options, stage, token, capturedScope, job.signal), preview => {
              if (job.signal.aborted || !live()) return;
              clearPreview?.(); clearPreview = ctx.showPreview(preview);
            }, job.signal);
        } else if (cached) res = cached;
        else if (WEB) {
          const job = graphReadJob = new AbortController();
          res = await api.graphStage(ctx.mountScope.owner, options, undefined, undefined,
            { team: ctx.mountScope.team || null }, job.signal);
        } else res = await api.graph(cfg.owner, options);
      } catch (failure) {
        if (graphReadJob?.signal.aborted) return { executed: false, stale: true };
        console.error(`View graph read failed ${JSON.stringify({ elapsedMs: Math.round(performance.now() - start),
          status: Number((failure as { status?: number }).status) || 0 })}`);
        throw failure;
      }
      console.info(`View graph read ${JSON.stringify({ elapsedMs: Math.round(performance.now() - start), cached: !!cached,
        executed: !!res?.executed, nodes: res?.nodes?.length || 0, edges: res?.edges?.length || 0 })}`);
      if (!live()) return { executed: false, stale: true };
      rawGraph = res;
      rawGraphScope = scopeKey;
      if (!res) return { executed: false };
      const remember = (layout?: { key: string; graph: LayoutGraph }) => {
        if (!canRetain || (cached && !layout) || !res?.executed) return;
        // 두 표식이 일치할 때만 보관한다. 저장 검증은 첫 렌더를 기다리게 하지 않는다.
        void access.then(async before => {
          if (!before || ctx.accessJob.signal.aborted || generation !== viewSessionCache.generation) return;
          const after = await viewAccess(ctx.mountScope, ctx.accessJob.signal).catch(() => null);
          if (!after || before.session !== after.session || before.revision !== after.revision
              || !live() || generation !== viewSessionCache.generation) return;
          const ticket = viewSessionCache.activate(before.session, before.revision);
          if (viewSessionCache.put(before.session, scopeKey, before.revision, res!, ticket, layout))
            loader.retained = { ...before, key: scopeKey, prefs: ctx.cameraPrefs(p) };
        });
      };
      if (!useRoots || !res.executed) { remember(); return res; }
      const aspectRatio = ctx.canvasRatio();
      if (layoutCache && layoutCache.raw === res && layoutCache.basis === p.timeBasis
          && layoutCache.layout === p.layout && layoutCache.aspectRatio === aspectRatio)
        return { executed: true, ...layoutCache.graph };
      const layoutKey = JSON.stringify([p.timeBasis, p.layout, aspectRatio]);
      const retainedLayout = grant && cached && viewSessionCache.getLayout(grant.session, scopeKey, grant.revision, layoutKey);
      if (retainedLayout) {
        layoutCache = { raw: res, basis: p.timeBasis, layout: p.layout, aspectRatio, graph: retainedLayout };
        console.info("View layout reused");
        return { executed: true, ...retainedLayout };
      }
      layoutJob?.abort();
      const job = layoutJob = new AbortController();
      try {
        const graph = await layoutInWorker(res.nodes || [], res.edges || [], p.timeBasis, job.signal,
          rootsOrderingFor(p.layout), undefined, aspectRatio);
        if (!live()) return { executed: false, stale: true };
        layoutCache = { raw: res, basis: p.timeBasis, layout: p.layout, aspectRatio, graph };
        remember({ key: layoutKey, graph });
        return { executed: true, ...graph };
      } catch (error) {
        if (job.signal.aborted) return { executed: false, stale: true };
        throw error;
      } finally { if (layoutJob === job) layoutJob = undefined; }
    },
  };
  return loader;
}

export type GraphLoader = ReturnType<typeof createGraphLoader>;
