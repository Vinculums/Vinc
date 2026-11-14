import type { GraphNode, GraphEdge } from "./api";

export type GraphRead = { executed: boolean; nodes?: GraphNode[]; edges?: GraphEdge[];
  topicEdges?: GraphEdge[]; kind?: string; partial?: boolean;
  snapshot?: { protocol?: string; token?: string }; graphSnapshotPending?: boolean };

/** One continuation belongs to one View generation and one captured account scope.
 * Legacy servers may ignore stage and return the full graph in the first request.
 */
export async function readGraphProgressively(
  fetchGraph: (stage?: "preview" | "complete" | "cancel", token?: string) => Promise<GraphRead>,
  preview: (graph: GraphRead) => void, signal: AbortSignal,
): Promise<GraphRead> {
  const active = () => { if (signal.aborted) throw new DOMException("Aborted", "AbortError"); };
  let first: GraphRead;
  try { first = await fetchGraph("preview"); active(); }
  catch (error) { active(); return fetchGraph(); }
  if (!first.partial && !first.snapshot) return first;
  const token = first.snapshot?.token;
  if (!first.executed || first.kind !== "graph_preview" || first.snapshot?.protocol !== "graph-preview-v1"
      || !token || !/^[A-Za-z0-9_-]{32,64}$/.test(token) || !first.nodes?.length
      || first.nodes.length > 160 || (first.edges?.length || 0) > 512
      || first.nodes.some(n => !n.id || !Number.isFinite(n.x2 ?? n.x) || !Number.isFinite(n.y2 ?? n.y))) {
    return fetchGraph();
  }
  let settled = false;
  const cancel = () => { if (!settled) void fetchGraph("cancel", token).catch(() => {}); };
  signal.addEventListener("abort", cancel, { once: true });
  try {
    preview(first);
    const deadline = performance.now() + 45000;
    let full = await fetchGraph("complete", token); active();
    while (full.graphSnapshotPending && performance.now() < deadline) {
      // The server normally long-polls. Also bound a fast pending/erroring gateway.
      await new Promise<void>((resolve, reject) => {
        const aborted = () => { clearTimeout(timer); reject(new DOMException("Aborted", "AbortError")); };
        const timer = setTimeout(() => { signal.removeEventListener("abort", aborted); resolve(); }, 250);
        signal.addEventListener("abort", aborted, { once: true });
      }); active();
      full = await fetchGraph("complete", token); active();
    }
    if (!full.executed || full.partial || full.snapshot || full.graphSnapshotPending) throw new Error("Graph continuation failed");
    const ids = new Set(full.nodes?.map(n => n.id));
    const edges = new Set(full.edges?.map(e => JSON.stringify([e.source, e.target, e.type])));
    if (first.nodes.some(n => n.kind !== "domain" && !ids.has(n.id))
        || first.edges?.some(e => !edges.has(JSON.stringify([e.source, e.target, e.type])))) {
      throw new Error("Graph continuation does not match its preview");
    }
    settled = true; return full;
  } catch (error) {
    active();
    cancel(); settled = true;
    // Expiry, restart, rolling-version mismatch and partial failure use the old contract.
    return fetchGraph();
  } finally {
    signal.removeEventListener("abort", cancel);
  }
}
