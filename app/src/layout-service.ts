import { temporalLayout, type BoxPlacement, type CrossRouter, type LayoutEngine, type TopicPiece } from "./view-layout";
import type { RoutePoint } from "./view-route";
import type { GraphEdge, GraphNode } from "./api";
import { edgeKeys, type LayoutAnswer, type SlimEdge, type SlimNode } from "./view-layout-wire";

/** The time-branches layout as a service (the `vinc-layout` container).
 *
 * It keeps, per Space and time basis, the topic pieces, the routes and the last placement, so a
 * request after a write lays out only the topics whose inputs changed. It sees ids, kinds,
 * domains, dates and relation ends, never labels, evidence or properties. Requests run one at a
 * time: the container is small and the work is CPU bound.
 * 은퇴 1단계: 허브가 더는 부르지 않고, 이 코드는 2단계에서 지운다.
 * 근거: [[decision:vinc-layout-is-retired-in-two-steps-2026-11-07]] */
export type LayoutRequest = { space: string; basis: "recorded" | "content"; nodes: SlimNode[]; edges: SlimEdge[] };
export type LayoutStats = { ms: number; branchesLaidOut: number; corridorsRouted: number; reusedPlacement: boolean };

type State = { pieces: Map<string, TopicPiece>; routes: Map<string, RoutePoint[][]>;
  placement?: Map<string, BoxPlacement>; bytes: number; used: number };

const LIMITS = { spaceKey: 200, nodes: 60_000, edges: 200_000, id: 512 };

export function validRequest(body: unknown): body is LayoutRequest {
  const b = body as LayoutRequest;
  const text = (v: unknown, max = LIMITS.id) => typeof v === "string" && v.length <= max;
  return !!b && text(b.space, LIMITS.spaceKey) && b.space.length > 0 && (b.basis === "recorded" || b.basis === "content")
    && Array.isArray(b.nodes) && b.nodes.length <= LIMITS.nodes && Array.isArray(b.edges) && b.edges.length <= LIMITS.edges
    && b.nodes.every(n => n && text(n.id) && text(n.kind) && (n.domain === undefined || text(n.domain))
      && (n.ts === undefined || text(n.ts, 64)) && (n.content_ts === undefined || text(n.content_ts, 64)))
    && b.edges.every(e => e && text(e.source) && text(e.target) && text(e.type, 64));
}

export function createLayoutService(engine: LayoutEngine, router: CrossRouter,
  options: { maxBytes?: number; clock?: () => number } = {}) {
  const maxBytes = options.maxBytes ?? 800 * 1024 * 1024, clock = options.clock ?? (() => Date.now());
  const states = new Map<string, State>();
  let queue: Promise<unknown> = Promise.resolve();
  const total = () => [...states.values()].reduce((s, x) => s + x.bytes, 0);

  // Every stored value is counted once when it is first written. A Space whose store grows past a
  // quarter of the budget is reset: stale pieces of old topic versions would otherwise pile up.
  const counted = <V>(state: State, map: Map<string, V>) => ({
    get: (key: string) => map.get(key),
    set: (key: string, value: V) => {
      if (!map.has(key)) state.bytes += key.length * 2 + JSON.stringify(value).length * 2;
      map.set(key, value);
    },
  });

  async function run(request: LayoutRequest): Promise<{ answer: LayoutAnswer; stats: LayoutStats }> {
    const key = `${request.space}\u0000${request.basis}`;
    let state = states.get(key);
    if (!state || state.bytes > maxBytes / 4) state = { pieces: new Map(), routes: new Map(), bytes: 0, used: 0 };
    states.delete(key); states.set(key, state); state.used = clock();
    while (total() > maxBytes && states.size > 1) states.delete(states.keys().next().value!);
    let branchesLaidOut = 0, corridorsRouted = 0;
    const countedEngine: LayoutEngine = { layout: input => { if (String(input.id).startsWith("topic:")) branchesLaidOut++; return engine.layout(input); } };
    const countedRouter: CrossRouter = (ns, es, batching) => { if (batching === "connector") corridorsRouted += es.length; return router(ns, es, batching); };
    const started = clock(), next = new Map<string, BoxPlacement>(), reusedPlacement = !!state.placement;
    const nodes = request.nodes.map(n => ({ ...n, label: "", degree: 0 }) as GraphNode);
    const edges = request.edges.map(e => ({ ...e }) as GraphEdge);
    const graph = await temporalLayout(nodes, edges, request.basis, countedEngine, countedRouter, 1.6, {
      pieces: counted(state, state.pieces), routes: counted(state, state.routes),
      placement: { previous: state.placement, next } });
    state.placement = next;
    const sent = new Set(request.nodes.map(n => n.id)), keys = edgeKeys(request.edges);
    const answer: LayoutAnswer = {
      nodes: graph.nodes.filter(n => sent.has(n.id)).map(n => [n.id, n.x!, n.y!, n.z!, n.x2!, n.y2!]),
      topics: graph.nodes.filter(n => !sent.has(n.id)),
      edges: graph.edges.map((e, i) => [keys[i], e.route ?? null]),
      topicEdges: graph.topicEdges,
    };
    return { answer, stats: { ms: clock() - started, branchesLaidOut, corridorsRouted, reusedPlacement } };
  }

  return {
    layout(request: LayoutRequest) {
      const job = queue.then(() => run(request));
      queue = job.catch(() => undefined);
      return job;
    },
    health: () => ({ spaces: states.size, bytes: total() }),
  };
}
