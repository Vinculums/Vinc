import ELK from "elkjs/lib/elk-api.js";
import elkWorkerUrl from "../node_modules/elkjs/lib/elk-worker.min.js?url";
import { AvoidLib } from "libavoid-js";
import wasmUrl from "../node_modules/libavoid-js/dist/libavoid.wasm?url";
import { temporalLayout } from "./view-layout";
import { routeAcrossTopics } from "./view-layout-avoid";
import type { GraphEdge, GraphNode } from "./api";
import type { LayoutOrdering } from "./view-layout";

self.onmessage = async (event: MessageEvent<{ nodes: GraphNode[]; edges: GraphEdge[]; basis: "recorded" | "content"; ordering?: LayoutOrdering }>) => {
  const elk = new ELK({ workerUrl: elkWorkerUrl });
  try {
    const { nodes, edges, basis, ordering } = event.data;
    const graph = await temporalLayout(nodes, edges, basis, elk, async (ns, es) => {
      if (!es.length) return [];
      await AvoidLib.load(wasmUrl);
      return routeAcrossTopics(AvoidLib.getInstance(), ns, es);
    }, ordering);
    self.postMessage({ graph });
  } catch (error) {
    self.postMessage({ error: error instanceof Error ? error.message : String(error) });
  } finally { elk.terminateWorker(); }
  // The owning View job terminates this worker too. Browser worker termination
  // also terminates descendants when a scope is superseded mid-layout.
};
