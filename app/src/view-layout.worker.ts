import ELK from "elkjs/lib/elk-api.js";
import elkWorkerUrl from "../node_modules/elkjs/lib/elk-worker.min.js?url";
import { AvoidLib } from "libavoid-js";
import wasmUrl from "../node_modules/libavoid-js/dist/libavoid.wasm?url";
import { temporalLayout } from "./view-layout";
import { routeAcrossTopics } from "./view-layout-avoid";
import type { GraphEdge, GraphNode } from "./api";
import type { LayoutOrdering } from "./view-layout";

self.onmessage = async (event: MessageEvent<{ nodes: GraphNode[]; edges: GraphEdge[]; basis: "recorded" | "content"; ordering?: LayoutOrdering; aspectRatio?: number }>) => {
  const elk = new ELK({ workerUrl: elkWorkerUrl });
  let elkMs = 0, routingMs = 0;
  const started = performance.now();
  let phase: "elk" | "routing" = "elk", callsDone = 0, activeNodes = 0, activeEdges = 0;
  const progress = () => self.postMessage({ progress: { phase, callsDone, activeNodes, activeEdges,
    elapsedMs: Math.round(performance.now() - started), elkMs: Math.round(elkMs), routingMs: Math.round(routingMs) } });
  const heartbeat = setInterval(progress, 5000);
  try {
    const { nodes, edges, basis, ordering, aspectRatio } = event.data;
    const graph = await temporalLayout(nodes, edges, basis, { layout: async input => {
      phase = "elk"; activeNodes = input.children?.length || 0; activeEdges = input.edges?.length || 0;
      if (!callsDone) progress();
      const start = performance.now();
      try { return await elk.layout(input); } finally { elkMs += performance.now() - start; callsDone++; }
    } }, async (ns, es, batching) => {
      if (!es.length) return [];
      phase = "routing"; activeNodes = ns.length; activeEdges = es.length; progress();
      const start = performance.now();
      await AvoidLib.load(wasmUrl);
      try { return routeAcrossTopics(AvoidLib.getInstance(), ns, es, batching); }
      finally { routingMs += performance.now() - start; }
    }, ordering, aspectRatio);
    self.postMessage({ graph, timings: { elkMs: Math.round(elkMs), routingMs: Math.round(routingMs),
      compositionMs: Math.round(Math.max(0, performance.now() - started - elkMs - routingMs)) } });
  } catch (error) {
    self.postMessage({ error: error instanceof Error ? error.message : String(error) });
  } finally { clearInterval(heartbeat); elk.terminateWorker(); }
  // The owning View job terminates this worker too. Browser worker termination
  // also terminates descendants when a scope is superseded mid-layout.
};
