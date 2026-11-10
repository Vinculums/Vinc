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

/** A bounded SVG shows actual labels, topic positions and relationships while
 * the full read is pending. It never replaces the population used by View state.
 * No controls or camera are transferred from this provisional overview.
 */
export function mountGraphPreview(el: HTMLElement, graph: GraphRead,
  options: { edges: boolean; label: string }): () => void {
  const ns = "http://www.w3.org/2000/svg", nodes = graph.nodes || [];
  const svg = document.createElementNS(ns, "svg");
  svg.dataset.graphPreview = "true"; svg.setAttribute("aria-hidden", "true");
  svg.style.cssText = "position:absolute;inset:0;width:100%;height:100%;pointer-events:none";
  const positions = new Map(nodes.map(n => [n.id, { x: n.x2 ?? n.x ?? 0, y: n.y2 ?? n.y ?? 0 }]));
  const xs = [...positions.values()].map(p => p.x), ys = [...positions.values()].map(p => p.y);
  const xmin = Math.min(...xs), ymin = Math.min(...ys), w = Math.max(1, Math.max(...xs)-xmin), h = Math.max(1, Math.max(...ys)-ymin);
  const pad = Math.max(w,h)*.1;
  svg.setAttribute("viewBox", `${xmin-pad} ${ymin-pad} ${w+2*pad} ${h+2*pad}`);
  const unit = Math.max(w,h)/700;
  for (const edge of options.edges ? graph.edges || [] : []) {
    const a=positions.get(edge.source), b=positions.get(edge.target); if (!a || !b) continue;
    const line=document.createElementNS(ns,"line");
    for (const [key,value] of Object.entries({x1:a.x,y1:a.y,x2:b.x,y2:b.y})) line.setAttribute(key,String(value));
    line.setAttribute("stroke","hsl(var(--muted-foreground))"); line.setAttribute("stroke-opacity",".22");
    line.setAttribute("stroke-width",String(unit)); svg.append(line);
  }
  for (const [index,node] of nodes.entries()) {
    const p=positions.get(node.id)!;
    const mark=document.createElementNS(ns,"circle");mark.dataset.nodeId=node.id;
    mark.setAttribute("cx",String(p.x));mark.setAttribute("cy",String(p.y));
    mark.setAttribute("r",String(unit*(node.kind==="domain"?6:3)));
    mark.setAttribute("fill","hsl(var(--foreground))");svg.append(mark);
    if(node.kind==="domain" || index<16) {
      const label=document.createElementNS(ns,"text");label.textContent=node.label;
      label.setAttribute("x",String(p.x+unit*8));label.setAttribute("y",String(p.y+unit*4));
      label.setAttribute("font-size",String(unit*12));label.setAttribute("fill","hsl(var(--foreground))");svg.append(label);
    }
  }
  const status = document.createElement("div"); status.textContent = options.label;
  status.setAttribute("role", "status");
  status.style.cssText = "position:absolute;bottom:12px;left:12px;font-size:var(--fs-xs);color:hsl(var(--muted-foreground));pointer-events:none";
  el.append(svg, status);
  requestAnimationFrame(()=>requestAnimationFrame(()=>{
    if(!svg.isConnected) return;
    const bounds=el.getBoundingClientRect();
    const visible=[...svg.querySelectorAll("circle")].filter(mark=>{
      const r=mark.getBoundingClientRect();return r.width>0&&r.right>bounds.left&&r.left<bounds.right&&r.bottom>bounds.top&&r.top<bounds.bottom;
    }).length;
    if(visible) console.info(`View preview visible ${JSON.stringify({nodes:nodes.length,edges:graph.edges?.length||0,visibleNodes:visible})}`);
  }));
  return () => { svg.remove(); status.remove(); };
}
