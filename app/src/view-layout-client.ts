import type { GraphNode, GraphEdge } from "./api";
import type { LayoutGraph } from "./view-layout";

/** 진척이 이만큼 없으면 멈춘 것으로 본다. 워커는 ELK 호출 사이에 5초마다 진척을 알리고,
 *  가장 긴 동기 라우팅 한 번은 큰 그래프에서 20초대였다. */
const LAYOUT_IDLE_MS = 60_000;
/** 진척이 있어도 이보다 길면 접는다. 화면은 그동안 중심에서 배치를 보여 주고 있다. */
const LAYOUT_CAP_MS = 300_000;

/** A layout job owns its worker. All exits terminate it, including removal of
 * View, a newer scope, worker faults, a stalled worker and the overall cap.
 * The deadline follows progress: a large graph that keeps reporting is not cut
 * at a fixed time. 근거: [[vinc.research.time-branches-layout-bottleneck-2026-10-12]] */
export function layoutInWorker(nodes: GraphNode[], edges: GraphEdge[], basis: "recorded" | "content",
  signal: AbortSignal,
  factory = () => new Worker(new URL("./view-layout.worker.ts", import.meta.url), { type: "module" }),
  aspectRatio = 1.6, limits: { idleMs?: number; capMs?: number } = {}): Promise<LayoutGraph> {
  if (signal.aborted) return Promise.reject(new DOMException("Aborted", "AbortError"));
  if (!nodes.length) return Promise.resolve({ nodes: [], edges: [], topicEdges: [] });
  return new Promise((resolve, reject) => {
    const worker = factory();
    let settled = false;
    let lastProgress: { phase: string; callsDone: number; activeNodes: number; activeEdges: number; elapsedMs: number } | undefined;
    let loggedPhase = "", loggedWindow = -1;
    const finish = (error?: Error, graph?: LayoutGraph) => {
      if (settled) return;
      settled = true;
      clearTimeout(idle); clearTimeout(cap); signal.removeEventListener("abort", abort);
      worker.terminate();
      if (error) {
        // Keep worker failures observable without logging graph content. The
        // View falls back to the by-centre geometry it already received.
        if (error.name !== "AbortError") console.error("View layout failed:", error.message);
        reject(error);
      } else resolve(graph!);
    };
    const abort = () => finish(new DOMException("Aborted", "AbortError"));
    const timedOut = (why: string) => finish(new Error(`View layout timed out: ${why}${lastProgress
      ? ` (${JSON.stringify(lastProgress)})` : ""}`));
    const idleMs = limits.idleMs ?? LAYOUT_IDLE_MS;
    let idle = setTimeout(() => timedOut("no progress"), idleMs);
    const cap = setTimeout(() => timedOut("over the cap"), limits.capMs ?? LAYOUT_CAP_MS);
    const alive = () => { clearTimeout(idle); idle = setTimeout(() => timedOut("no progress"), idleMs); };
    signal.addEventListener("abort", abort, { once: true });
    worker.onmessage = (event: MessageEvent<{ graph?: LayoutGraph; error?: string; timings?: { elkMs: number; routingMs: number; compositionMs?: number }; progress?: typeof lastProgress }>) => {
      const progress = event.data.progress;
      if (progress) alive();
      if (progress && ["elk", "routing"].includes(progress.phase) &&
          [progress.callsDone, progress.activeNodes, progress.activeEdges, progress.elapsedMs].every(Number.isFinite)) {
        lastProgress = progress;
        const window = Math.floor(progress.elapsedMs / 10000);
        if (progress.phase !== loggedPhase || window !== loggedWindow) {
          console.info(`View layout progress ${JSON.stringify(progress)}`);
          loggedPhase = progress.phase; loggedWindow = window;
        }
        return;
      }
      const { graph, error } = event.data;
      if (event.data.timings) console.info(`View layout stages ${JSON.stringify(event.data.timings)}`);
      if (error) finish(new Error(error));
      else if (graph && Array.isArray(graph.nodes) && Array.isArray(graph.edges) && Array.isArray(graph.topicEdges)) finish(undefined, graph);
      else finish(new Error("Invalid View layout worker response"));
    };
    worker.onerror = (event) => finish(new Error(event.message || "View layout worker failed"));
    worker.onmessageerror = () => finish(new Error("Invalid View layout worker response"));
    try { worker.postMessage({ nodes, edges, basis, aspectRatio }); } catch (error) {
      finish(error instanceof Error ? error : new Error(String(error)));
    }
  });
}
