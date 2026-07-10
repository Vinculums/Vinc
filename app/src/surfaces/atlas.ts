import Graph from "graphology";
import Sigma from "sigma";
import { api, cfg, type GraphEdge, type GraphNode } from "../api";
import { COPY } from "../copy";
import { mount3d, type L3, type N3 } from "./atlas3d";

/** Surface 3 — Atlas (spec: vinc.d.graph v0.2 · vinc.d.atlas-visual-language).
 *
 *  Full-3D renderer (DEC D21 revised): L0 Domain Atlas / L1 Domain Lens /
 *  L2 Node Focus run on three.js (see atlas3d.ts — primitives = type,
 *  monochrome ink materials, paper fog). Compare keeps flat Sigma lenses —
 *  side-by-side comparison is deliberately planar.
 *
 *  Navigation: L0 click = open lens · L0 link click = Compare ·
 *  L1 click = gateway→follow / node→detail · right-click = L2 refocus. */

type Level = { kind: "l0" } | { kind: "l1"; domain: string } | { kind: "l2"; seed: string }
  | { kind: "compare"; a: string; b: string };

const INK = "#14110d";
const INK_MUTE = "#6a6252";
const PAPER_EDGE = "#ded8cc";
const POINT = "#3e4c63";

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

  let g3: any = null;                 // 3D 인스턴스 (L0/L1/L2)
  let extraSigmas: Sigma[] = [];      // Compare 2D 렌즈
  const trail: Level[] = [];

  // deep links: #atlas/<domain> · #atlas/compare/<a>/<b>
  const parts = location.hash.split("/").map(decodeURIComponent);
  const deep = parts[1] || "";

  function setMsg(html: string) { msg.innerHTML = html; msg.style.display = html ? "grid" : "none"; }

  function drawCrumb() {
    crumbEl.innerHTML = trail.map((lv, i) => {
      const name = lv.kind === "l0" ? "Atlas" : lv.kind === "l1" ? lv.domain
        : lv.kind === "compare" ? `${lv.a} ⇄ ${lv.b}` : lv.seed;
      return `<button data-i="${i}">${name}</button>${i < trail.length - 1 ? "›" : ""}`;
    }).join(" ");
    crumbEl.querySelectorAll("button").forEach((b) =>
      b.addEventListener("click", () => { trail.length = Number(b.dataset.i) + 1; show(trail[trail.length - 1], false); }));
  }

  function toN3(nodes: GraphNode[], lensDomain?: string, focusId?: string): N3[] {
    return nodes.map((nd) => ({
      id: nd.id,
      label: nd.label === nd.id ? nd.id.replace(/^[a-z]+:/, "") : nd.label,
      domain: nd.domain, degree: nd.degree,
      kind: (nd as any).kind || "concept",
      gateway: lensDomain !== undefined && nd.domain !== lensDomain,
      focus: focusId !== undefined && nd.id === focusId,
      x: nd.x, y: nd.y, z: (nd as any).z,
    }));
  }
  function toL3(edges: GraphEdge[]): L3[] {
    return edges.map((e) => ({ source: e.source, target: e.target, type: e.type, cross: e.cross_domain }));
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
    const n = Math.max(domains.length, 1);
    const nodes: N3[] = domains.map((d, i) => {
      const ang = (2 * Math.PI * i) / n;
      return { id: d.domain, label: d.domain, kind: "domain", records: d.episodes,
               x: Math.cos(ang), y: Math.sin(ang) * 0.25, z: Math.sin(ang) };
    });
    // Bridge 전용 도메인(레코드 0 — 예: 브릿지 매트릭스의 외부 축)은 L0 노드가
    // 없으므로 결합에서 제외 — 미지 노드 참조는 렌더러가 거부한다.
    const known = new Set(nodes.map((d) => d.id));
    const links: L3[] = ties
      .filter((t) => t.source !== t.target && known.has(t.source) && known.has(t.target))
      .map((t) => ({ source: t.source, target: t.target, cross: true, weight: t.ties }));
    g3 = mount3d(canvas, nodes, links, {
      onClick: (nd) => { trail.push({ kind: "l1", domain: nd.id }); show(trail[trail.length - 1]); },
      onLinkClick: (l) => {
        const a = typeof l.source === "object" ? l.source.id : l.source;
        const b = typeof l.target === "object" ? l.target.id : l.target;
        trail.push({ kind: "compare", a, b }); show(trail[trail.length - 1]);
      },
    });
  }

  function openDetail(nd: N3) {
    detail.hidden = false;
    detail.innerHTML = `
      <h2>${nd.label}</h2>
      <p><span class="badge">domain: ${nd.domain || "—"}</span></p>
      <p class="hint">${nd.degree || 0} connections${nd.gateway ? " · gateway" : ""}.</p>
      <button class="btn btn-primary" id="focus">Trace from here</button>`;
    detail.querySelector("#focus")!.addEventListener("click", () => {
      trail.push({ kind: "l2", seed: nd.id }); show(trail[trail.length - 1]);
    });
  }

  async function showL1(domain: string) {
    setMsg(`<p class="hint">${COPY.atlas.loading(1)}</p>`);
    const res = await api.graph(cfg.owner, { domain });
    if (!res.executed || (res.nodes || []).length === 0) {
      setMsg(`<div class="empty"><p>${COPY.atlas.idle}</p></div>`); return;
    }
    setMsg("");
    g3 = mount3d(canvas, toN3(res.nodes, domain), toL3(res.edges), {
      onClick: (nd) => {
        if (nd.gateway) { trail.push({ kind: "l1", domain: nd.domain! }); show(trail[trail.length - 1]); }
        else openDetail(nd);
      },
      onRightClick: (nd) => { trail.push({ kind: "l2", seed: nd.id }); show(trail[trail.length - 1]); },
    });
  }

  async function showL2(seed: string) {
    setMsg(`<p class="hint">${COPY.atlas.loading(1)}</p>`);
    const res = await api.graph(cfg.owner, { seed, depth: 2 });
    if (!res.executed) { setMsg(`<div class="error-banner">${COPY.atlas.error}</div>`); return; }
    setMsg("");
    g3 = mount3d(canvas, toN3(res.nodes, undefined, seed), toL3(res.edges), {
      onClick: (nd) => openDetail(nd),
      onRightClick: (nd) => { trail.push({ kind: "l2", seed: nd.id }); show(trail[trail.length - 1]); },
    });
  }

  /** Compare — 좌우 2D 렌즈(Sigma) + 중앙 개념 결합 레일 (D22). 비교 뷰는
   *  의도적으로 평면(나란히 대조가 목적 — 3D 회전은 좌우 대응을 깨뜨림). */
  function addShapedGraph(g: Graph, nodes: GraphNode[], edges: GraphEdge[]) {
    for (const nd of nodes) {
      const label = nd.label === nd.id ? nd.id.replace(/^[a-z]+:/, "") : nd.label;
      g.addNode(nd.id, { label, x: nd.x ?? 0, y: nd.y ?? 0,
        size: 4 + Math.sqrt(nd.degree || 1) * 2, color: INK, domain: nd.domain });
    }
    for (const e of edges) {
      if (g.hasNode(e.source) && g.hasNode(e.target) && !g.hasEdge(e.source, e.target))
        g.addEdge(e.source, e.target, { size: e.cross_domain ? 0.8 : 1.6,
          color: e.cross_domain ? INK_MUTE : PAPER_EDGE });
    }
  }

  async function showCompare(a: string, b: string) {
    setMsg("");
    canvas.innerHTML = `
      <div style="display:grid;grid-template-columns:1fr 22rem 1fr;height:100%">
        <div id="cmp-a"></div>
        <div id="cmp-rail" style="overflow:auto;border-left:1px solid ${PAPER_EDGE};
             border-right:1px solid ${PAPER_EDGE};padding:0 1rem">
          <h3 style="position:sticky;top:0;background:hsl(var(--background))">${a} ⇄ ${b}</h3>
          <div id="cmp-pairs" class="hint">Loading ties…</div>
        </div>
        <div id="cmp-b"></div>
      </div>`;
    const [ga, gb, cp] = await Promise.all([
      api.graph(cfg.owner, { domain: a }), api.graph(cfg.owner, { domain: b }),
      api.coupling(cfg.owner, a, b),
    ]);
    const mk = (elm: HTMLElement, res: any) => {
      const g = new Graph();
      addShapedGraph(g, res.nodes || [], res.edges || []);
      const s = new Sigma(g, elm, { allowInvalidContainer: true });
      extraSigmas.push(s);
      return { g, s };
    };
    const left = mk(canvas.querySelector("#cmp-a")!, ga);
    const right = mk(canvas.querySelector("#cmp-b")!, gb);
    const pairs: any[] = cp.pairs || [];
    const railEl = canvas.querySelector<HTMLElement>("#cmp-pairs")!;
    railEl.classList.remove("hint");
    railEl.innerHTML = pairs.length === 0
      ? `<p class="hint">No concept-level ties yet between these domains.</p>`
      : pairs.map((p, i) => `
        <div class="list-row" data-i="${i}">
          <span class="title" style="font-size:.9em">${p.a_title}
            <span class="hint">${p.kind === "direct" ? "⇢" : "◇"}</span> ${p.b_title}</span>
          ${p.via_title ? `<span class="meta">via ${p.via_title}</span>` : ""}
        </div>`).join("");
    const highlight = (p: any | null) => {
      for (const { s } of [left, right]) {
        s.setSetting("nodeReducer", (node, data) => {
          if (!p) return data;
          const hit = node === p.a_id || node === p.b_id;
          return hit ? { ...data, color: POINT, zIndex: 1, size: (data.size as number) * 1.4 }
                     : { ...data, color: PAPER_EDGE, label: "" };
        });
        s.refresh();
      }
    };
    railEl.querySelectorAll(".list-row").forEach((row) => {
      row.addEventListener("mouseenter", () => highlight(pairs[Number((row as HTMLElement).dataset.i)]));
      row.addEventListener("mouseleave", () => highlight(null));
    });
  }

  async function show(level: Level, push = true) {
    if (push && trail[trail.length - 1] !== level) { /* pushed by caller */ }
    detail.hidden = true;
    extraSigmas.forEach((s) => s.kill());
    extraSigmas = [];
    if (g3) { g3._destructor?.(); g3 = null; }
    canvas.innerHTML = "";
    drawCrumb();
    try {
      if (level.kind === "l0") await showL0();
      else if (level.kind === "l1") await showL1(level.domain);
      else if (level.kind === "compare") await showCompare(level.a, level.b);
      else await showL2(level.seed);
    } catch {
      setMsg(`<div class="error-banner">${COPY.atlas.error}</div>`);
    }
  }

  trail.push({ kind: "l0" });
  if (deep === "compare" && parts[2] && parts[3]) trail.push({ kind: "compare", a: parts[2], b: parts[3] });
  else if (deep) trail.push({ kind: "l1", domain: deep });
  await show(trail[trail.length - 1]);
}
