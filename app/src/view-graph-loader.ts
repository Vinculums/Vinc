import { api, viewAccess, viewScope, WEB, type GraphNode, type GraphEdge } from "./api";
import { viewSessionCache } from "./view-session-cache";
import { graphReadPlan } from "./view-graph-read";

/** 한 번의 View 마운트가 그래프를 읽어 돌려주는 곳. 읽기는 `/graph` 한 번이고, 같은 탭에서
 * 돌아온 마운트는 보관소(`viewSessionCache`)의 직렬화된 완료 DTO 를 쓴다(요청 없음). 보관본은
 * 복사본이라 렌더러의 변형이 다음 마운트에 번지지 않는다.
 * 마운트가 주는 것은 `GraphLoaderContext` 로만 들어오고, 바깥이 만지는 것은 이 객체의 메서드뿐이다. */
/** `marker` 는 서버가 읽기 전에 잡은 변경 표식이다(요청에 `marker=1` 을 실었을 때만 온다). */
type RawGraph = { executed: boolean; nodes?: GraphNode[]; edges?: GraphEdge[]; topicEdges?: GraphEdge[];
  marker?: string };
type FetchedGraph = RawGraph & { stale?: boolean };

type GraphLoaderContext = {
  mountScope: ReturnType<typeof viewScope>;
  accessJob: AbortController;
  firstAccess: Promise<{ session: string; revision: string } | null>;
  /** 지금 화면 세대. 읽기가 시작될 때 잡아 두고 모든 await 뒤에 같은 값과 견준다. */
  seq(): number;
  /** 마운트가 아직 살아 있고 그 캔버스가 문서에 붙어 있는가. */
  alive(): boolean;
};

type RetainedRef = { session: string; revision: string; key: string };

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
  let readScopeKey: string | undefined;
  let graphReadJob: AbortController | undefined;
  let firstAccessUsed = false;

  const loader = {
    /** 마지막으로 보관소와 맞춘 항목. dispose 가 그 범위를 책갈피로 남긴다. */
    retained: undefined as RetainedRef | undefined,
    get scopeKey() { return readScopeKey; },

    /** 진행 중인 읽기를 접는다. */
    abort() { graphReadJob?.abort(); },
    /** 쓰기·로그아웃 뒤: 탭 보관소를 비워 다음 읽기가 서버로 가게 한다. */
    reset() { viewSessionCache.clear(); },

    async fetchGraph(scope: { domains?: string[] }): Promise<FetchedGraph> {
      const { scopeKey, options } = graphReadPlan(scope.domains || []);
      const mySeq = ctx.seq();
      // 이 읽기를 낳은 마운트·세대·스코프가 아직 화면의 주인인가. 모든 await 뒤에 같은 식으로 묻는다.
      const live = () => mySeq === ctx.seq() && ctx.alive() && ctx.mountScope.key === viewScope().key;
      const start = performance.now();
      let res: RawGraph | undefined;
      let cached: RawGraph | undefined;
      const canRetain = WEB;
      const access = canRetain
        ? (firstAccessUsed ? viewAccess(ctx.mountScope, ctx.accessJob.signal).catch(() => null) : ctx.firstAccess)
        : Promise.resolve(null);
      firstAccessUsed = true;
      // 재사용할 후보가 하나도 없으면 표식 확인을 기다리지 않고 읽기를 바로 시작한다. 이때
      // 읽기의 라벨은 확인 결과가 아니라 **응답이 실어 온 표식**(서버가 읽기 전에 잡은 값)이고,
      // 보관은 읽기가 끝난 뒤 같은 표식이 다시 확인될 때만 한다. 후보가 있으면 그것을 쓸 수
      // 있는지 표식으로 먼저 가려야 하므로 확인 → 읽기 순서로 간다.
      const early = canRetain && !viewSessionCache.count && !viewSessionCache.hasBookmark;
      // Confirm the marker before starting the graph snapshot; a late probe cannot label an older read.
      const grant = canRetain && !early ? await access : null;
      if (!live()) return { executed: false, stale: true };
      if (grant) {
        viewSessionCache.activate(grant.session, grant.revision);
        cached = viewSessionCache.get(grant.session, scopeKey, grant.revision);
        if (cached) loader.retained = { ...grant, key: scopeKey };
      } else if (canRetain && !early && viewSessionCache.count) viewSessionCache.clear();
      const generation = viewSessionCache.generation;
      graphReadJob?.abort();
      const readJob = graphReadJob = new AbortController();
      const capturedScope = { team: ctx.mountScope.team || null };
      try {
        res = cached || await api.graphStage(ctx.mountScope.owner, options, undefined, undefined,
          capturedScope, readJob.signal);
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
      readScopeKey = scopeKey;
      if (!res) return { executed: false };
      const held = res;
      // 두 표식이 일치할 때만 보관한다. 저장 검증은 첫 렌더를 기다리게 하지 않는다.
      if (canRetain && !cached && held.executed) void access.then(async before => {
        if (!before || ctx.accessJob.signal.aborted || generation !== viewSessionCache.generation) return;
        // 바로 시작한 읽기는 응답의 표식으로, 확인 뒤에 시작한 읽기는 그 확인의 표식으로 라벨한다.
        // 서버가 표식을 안 실어 주면 라벨이 없으므로 보관하지 않는다.
        const label = early ? held.marker : before.revision;
        if (typeof label !== "string" || !label) return;
        const after = await viewAccess(ctx.mountScope, ctx.accessJob.signal).catch(() => null);
        if (!after || before.session !== after.session || after.revision !== label
            || !live() || generation !== viewSessionCache.generation) return;
        const ticket = viewSessionCache.activate(before.session, label);
        if (viewSessionCache.put(before.session, scopeKey, label, held, ticket))
          loader.retained = { session: before.session, revision: label, key: scopeKey };
      });
      return held;
    },
  };
  return loader;
}
