import Graph from "graphology";
import Sigma from "sigma";
import { api, cfg, type GraphEdge, type GraphNode } from "../api";
import { COPY } from "../copy";

/** Surface 3 — Atlas (spec: vinc.d.graph v0.2, DEC D16·D17).
 *
 *  Three-level semantic zoom:
 *    L0 Domain Atlas — domains as supernodes (size = record count), ties as
 *      weighted edges. Data: GET /atlas.
 *    L1 Domain Lens — one domain's nodes + edge-of-lens gateways (ghost nodes
 *      that cross into another domain; click to follow). Data: GET /graph?domain=.
 *    L2 Node Focus — seed+depth ego. Data: GET /graph?seed=&depth=.
 *
 *  Layout comes precomputed from the sidecar (D17) — the UI only consumes x/y.
 *  Monochrome contract: cross-domain edges are dashed-equivalent (lighter +
 *  thinner), highlight is the single point color; no per-domain colors. */

type Level = { kind: "l0" } | { kind: "l1"; domain: string } | { kind: "l2"; seed: string };

const INK = "#14110d";
const INK_MUTE = "#6a6252";
const PAPER_EDGE = "#ded8cc";
const POINT = "#3e4c63";   // --ring / --primary (single point color)

export async function renderAtlas(el: HTMLElement) {
  el.innerHTML = `
    <div id="atlas-crumb"></div>
    <div id="atlas-canvas"></div>
    <div id="atlas-legend"></div>
    <div id="atlas-detail" hidden></div>
    <div id="atlas-msg" style="position:absolute;inset:0;display:grid;place-items:center;z-index:1"></div>
  `;
  const canvas = el.querySelector<HTMLElement>("#atlas-canvas")!;
  const crumbEl = el.querySelector<HTMLElement>("#atlas-crumb")!;
  const detail = el.querySelector<HTMLElement>("#atlas-detail")!;
  const msg = el.querySelector<HTMLElement>("#atlas-msg")!;

  let sigma: Sigma | null = null;
  const trail: Level[] = [];

  // deep link: #atlas/<domain>
  const deep = decodeURIComponent(location.hash.split("/")[1] || "");

  function setMsg(html: string) { msg.innerHTML = html; msg.style.display = html ? "grid" : "none"; }

  function drawCrumb() {
    crumbEl.innerHTML = trail.map((lv, i) => {
      const name = lv.kind === "l0" ? "Atlas" : lv.kind === "l1" ? lv.domain : lv.seed;
      return `<button data-i="${i}">${name}</button>${i < trail.length - 1 ? "›" : ""}`;
    }).join(" ");
    crumbEl.querySelectorAll("button").forEach((b) =>
      b.addEventListener("click", () => { trail.length = Number(b.dataset.i) + 1; show(trail[trail.length - 1], false); }));
  }

  function mount(graph: Graph, handlers: {
    onClick?: (node: string) => void; onDouble?: (node: string) => void;
  }) {
    sigma?.kill();
    sigma = new Sigma(graph, canvas, { allowInvalidContainer: true });
    // hover reducer — neighbors stay, the rest fade (D16 interaction grammar)
    let hovered: string | null = null;
    sigma.setSetting("nodeReducer", (node, data) => {
      if (!hovered) return data;
      if (node === hovered || graph.areNeighbors(node, hovered)) return { ...data, zIndex: 1 };
      return { ...data, color: PAPER_EDGE, label: "" };
    });
    sigma.setSetting("edgeReducer", (edge, data) => {
      if (!hovered) return data;
      return graph.hasExtremity(edge, hovered) ? data : { ...data, color: "#efeae1" };
    });
    sigma.on("enterNode", ({ node }) => { hovered = node; sigma!.refresh(); });
    sigma.on("leaveNode", () => { hovered = null; sigma!.refresh(); });
    if (handlers.onClick) sigma.on("clickNode", ({ node }) => handlers.onClick!(node));
    if (handlers.onDouble) sigma.on("doubleClickNode", ({ node }) => handlers.onDouble!(node));
  }

  async function showL0() {
    setMsg(`<p class="hint">${COPY.atlas.loading(0)}</p>`);
    const res = await api.atlasL0(cfg.owner);
    const domains: { domain: string; episodes: number }[] = res.domains || [];
    const ties: { source: string; target: string; ties: number }[] = res.ties || [];
    if (!res.executed || domains.length === 0) {
      setMsg(`<div class="empty"><p>${COPY.atlas.idle}</p>
        <a class="btn btn-primary" href="#intake">${COPY.atlas.idleCta}</a></div>`);
      return;
    }
    setMsg("");
    const g = new Graph();
    const n = Math.max(domains.length, 1);
    domains.forEach((d, i) => {
      const ang = (2 * Math.PI * i) / n;
      g.addNode(d.domain, {
        label: d.domain, x: Math.cos(ang), y: Math.sin(ang),
        size: 8 + 3 * Math.sqrt(d.episodes), color: INK,
      });
    });
    for (const t of ties) {
      if (g.hasNode(t.source) && g.hasNode(t.target) && !g.hasEdge(t.source, t.target))
        g.addEdge(t.source, t.target, { size: 1 + Math.log1p(t.ties), color: INK_MUTE });
    }
    mount(g, { onClick: (node) => { trail.push({ kind: "l1", domain: node }); show(trail[trail.length - 1]); } });
  }

  function addShapedGraph(g: Graph, nodes: GraphNode[], edges: GraphEdge[], lensDomain?: string) {
    for (const nd of nodes) {
      const gateway = lensDomain !== undefined && nd.domain !== lensDomain;
      // ghost nodes arrive with label=id — strip the type prefix for display
      const label = nd.label === nd.id ? nd.id.replace(/^[a-z]+:/, "") : nd.label;
      g.addNode(nd.id, {
        label, x: nd.x ?? 0, y: nd.y ?? 0,
        size: 4 + Math.sqrt(nd.degree || 1) * 2,
        color: gateway ? INK_MUTE : INK,           // gateway = ghost weight, not color hue
        gateway, domain: nd.domain,
      });
    }
    for (const e of edges) {
      if (g.hasNode(e.source) && g.hasNode(e.target) && !g.hasEdge(e.source, e.target))
        g.addEdge(e.source, e.target, {
          size: e.cross_domain ? 0.8 : 1.6,        // dashed-equivalent under monochrome
          color: e.cross_domain ? INK_MUTE : PAPER_EDGE,
        });
    }
  }

  function openDetail(node: string, g: Graph) {
    const a = g.getNodeAttributes(node);
    detail.hidden = false;
    detail.innerHTML = `
      <h2>${a.label}</h2>
      <p class="meta hint">${a.domain || ""}</p>
      <p class="hint">${g.degree(node)} connections.</p>
      <button class="btn btn-primary" id="focus">Trace from here</button>`;
    detail.querySelector("#focus")!.addEventListener("click", () => {
      trail.push({ kind: "l2", seed: node }); show(trail[trail.length - 1]);
    });
  }

  async function showL1(domain: string) {
    setMsg(`<p class="hint">${COPY.atlas.loading(1)}</p>`);
    const res = await api.graph(cfg.owner, { domain });
    if (!res.executed || (res.nodes || []).length === 0) {
      setMsg(`<div class="empty"><p>${COPY.atlas.idle}</p></div>`); return;
    }
    setMsg("");
    const g = new Graph();
    addShapedGraph(g, res.nodes, res.edges, domain);
    mount(g, {
      onClick: (node) => {
        if (g.getNodeAttribute(node, "gateway")) {
          const next = g.getNodeAttribute(node, "domain");
          trail.push({ kind: "l1", domain: next }); show(trail[trail.length - 1]);
        } else openDetail(node, g);
      },
      onDouble: (node) => { trail.push({ kind: "l2", seed: node }); show(trail[trail.length - 1]); },
    });
  }

  async function showL2(seed: string) {
    setMsg(`<p class="hint">${COPY.atlas.loading(1)}</p>`);
    const res = await api.graph(cfg.owner, { seed, depth: 2 });
    if (!res.executed) { setMsg(`<div class="error-banner">${COPY.atlas.error}</div>`); return; }
    setMsg("");
    const g = new Graph();
    addShapedGraph(g, res.nodes, res.edges);
    if (g.hasNode(seed)) g.setNodeAttribute(seed, "color", POINT);  // the single point color
    mount(g, { onClick: (node) => openDetail(node, g), onDouble: (node) => {
      trail.push({ kind: "l2", seed: node }); show(trail[trail.length - 1]);
    }});
  }

  async function show(level: Level, push = true) {
    if (push && trail[trail.length - 1] !== level) { /* pushed by caller */ }
    detail.hidden = true;
    drawCrumb();
    try {
      if (level.kind === "l0") await showL0();
      else if (level.kind === "l1") await showL1(level.domain);
      else await showL2(level.seed);
    } catch {
      setMsg(`<div class="error-banner">${COPY.atlas.error}</div>`);
    }
  }

  trail.push({ kind: "l0" });
  if (deep) trail.push({ kind: "l1", domain: deep });
  await show(trail[trail.length - 1]);
}
