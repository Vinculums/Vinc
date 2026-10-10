import type { GraphNode, GraphEdge } from "./api";
import type { LayoutGraph, LayoutOrdering } from "./view-layout";

/** A layout job owns its worker. All exits terminate it, including removal of
 * View, a newer scope, worker faults, and the bounded execution deadline. */
export function layoutInWorker(nodes: GraphNode[], edges: GraphEdge[], basis: "recorded" | "content",
  signal: AbortSignal, ordering: LayoutOrdering = "temporal",
  factory = () => new Worker(new URL("./view-layout.worker.ts", import.meta.url), { type: "module" }),
  aspectRatio = 1.6): Promise<LayoutGraph> {
  if (signal.aborted) return Promise.reject(new DOMException("Aborted", "AbortError"));
  if (!nodes.length) return Promise.resolve({ nodes: [], edges: [], topicEdges: [] });
  return new Promise((resolve, reject) => {
    const worker = factory();
    let settled = false;
    const finish = (error?: Error, graph?: LayoutGraph) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer); signal.removeEventListener("abort", abort);
      worker.terminate();
      if (error) {
        // Keep worker failures observable without logging graph content. The
        // View retains its existing user-facing error state.
        if (error.name !== "AbortError") console.error("View layout failed:", error.message);
        reject(error);
      } else resolve(graph!);
    };
    const abort = () => finish(new DOMException("Aborted", "AbortError"));
    const timer = setTimeout(() => finish(new Error("View layout timed out")), 60000);
    signal.addEventListener("abort", abort, { once: true });
    worker.onmessage = (event: MessageEvent<{ graph?: LayoutGraph; error?: string; timings?: { elkMs: number; routingMs: number } }>) => {
      const { graph, error } = event.data;
      if (event.data.timings) console.info("View layout stages", event.data.timings);
      if (error) finish(new Error(error));
      else if (graph && Array.isArray(graph.nodes) && Array.isArray(graph.edges) && Array.isArray(graph.topicEdges)) finish(undefined, graph);
      else finish(new Error("Invalid View layout worker response"));
    };
    worker.onerror = (event) => finish(new Error(event.message || "View layout worker failed"));
    worker.onmessageerror = () => finish(new Error("Invalid View layout worker response"));
    try { worker.postMessage({ nodes, edges, basis, ordering, aspectRatio }); } catch (error) {
      finish(error instanceof Error ? error : new Error(String(error)));
    }
  });
}
