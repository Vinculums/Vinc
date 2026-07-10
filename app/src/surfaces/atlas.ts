import Graph from "graphology";
import Sigma from "sigma";
import { api, cfg, type GraphEdge, type GraphNode } from "../api";
import { COPY } from "../copy";
import { enableLasso, mount3d, type L3, type N3 } from "./atlas3d";
import { openShareSheet } from "../sheets";
import { loadPrefs, savePrefs, type AtlasPrefs } from "../atlas-prefs";

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
  | { kind: "compare"; a: string; b: string }
  | { kind: "grid" } | { kind: "matrix" };

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
    <div id="atlas-tools" style="position:absolute;top:var(--space-4,1rem);right:var(--space-6,1.5rem);z-index:2;display:flex;gap:.5rem"></div>
    <div id="atlas-float" style="position:absolute;left:50%;bottom:var(--space-6,1.5rem);transform:translateX(-50%);z-index:3" hidden></div>
    <div id="atlas-view-panel" hidden style="position:absolute;top:3.2rem;right:var(--space-6,1.5rem);z-index:4;
         width:17rem;background:hsl(var(--card));border:1px solid hsl(var(--border));
         border-radius:var(--radius);box-shadow:var(--shadow-elev-2,none);padding:var(--space-4,1rem)"></div>
  `;
  const canvas = el.querySelector<HTMLElement>("#atlas-canvas")!;
  const crumbEl = el.querySelector<HTMLElement>("#atlas-crumb")!;
  const detail = el.querySelector<HTMLElement>("#atlas-detail")!;
  const msg = el.querySelector<HTMLElement>("#atlas-msg")!;
  const tools = el.querySelector<HTMLElement>("#atlas-tools")!;
  const float = el.querySelector<HTMLElement>("#atlas-float")!;
  const viewPanel = el.querySelector<HTMLElement>("#atlas-view-panel")!;

  let g3: any = null;                 // 3D 인스턴스 (L0/L1/L2)
  let extraSigmas: Sigma[] = [];      // Compare 2D 렌즈
  const trail: Level[] = [];

  // deep links: #atlas/<domain> · #atlas/compare/<a>/<b> · #atlas/trace/<id>(L2)
  const parts = location.hash.split("/").map(decodeURIComponent);
  const deep = parts[1] || "";

  function setMsg(html: string) { msg.innerHTML = html; msg.style.display = html ? "grid" : "none"; }

  /** D31 — 인캔버스 View 컨트롤(열린 층만: 2D/3D·크기·라벨·화살촉·산개).
   *  닫힌 층(형태=타입·포인트색=초점·모노크롬)은 노출하지 않는다. */
  function viewControl() {
    const p = loadPrefs();
    tools.insertAdjacentHTML("afterbegin",
      `<button class="chip" id="t-view" aria-haspopup="true">View · ${p.view === "2d" ? "2D" : "3D"}</button>`);
    tools.querySelector("#t-view")!.addEventListener("click", () => {
      viewPanel.hidden = !viewPanel.hidden;
      if (!viewPanel.hidden) drawViewPanel();
    });
  }

  function drawViewPanel() {
    const p = loadPrefs();
    const chip = (pref: string, val: string, label: string, active: boolean) =>
      `<button class="chip${active ? " active" : ""}" data-pref="${pref}" data-val="${val}">${label}</button>`;
    viewPanel.innerHTML = `
      <p class="hint" style="margin-top:0">View</p>
      <p>${chip("view", "3d", "3D", p.view === "3d")} ${chip("view", "2d", "2D — flat", p.view === "2d")}</p>
      <p class="hint">Node size</p>
      <p>${chip("size", "links", "by connections", p.size === "links")} ${chip("size", "uniform", "uniform", p.size === "uniform")}</p>
      <p class="hint">Labels</p>
      <p>${chip("labels", "hover", "on hover", p.labels === "hover")} ${chip("labels", "none", "off", p.labels === "none")}</p>
      <p>${chip("arrows", "toggle", "Direction arrows", p.arrows)}</p>
      <p class="hint">Unlinked nodes</p>
      <p>${chip("unlinked", "show", "show", p.unlinked === "show")} ${chip("unlinked", "hide", "hide", p.unlinked === "hide")}</p>
      <label class="hint" style="display:block">Spread
        <input id="pref-spread" type="range" min="0.6" max="1.6" step="0.2" value="${p.spread}" style="width:100%">
      </label>
      <p class="hint" style="margin-bottom:0">Shapes and the point color stay fixed —
        they carry meaning, so a shared pack reads the same everywhere.</p>`;
    viewPanel.querySelectorAll<HTMLElement>("[data-pref]").forEach((b) =>
      b.addEventListener("click", () => {
        const k = b.dataset.pref as keyof AtlasPrefs;
        const cur = loadPrefs();
        applyPrefs(k === "arrows" ? { arrows: !cur.arrows } : ({ [k]: b.dataset.val } as Partial<AtlasPrefs>));
      }));
    viewPanel.querySelector<HTMLInputElement>("#pref-spread")!.addEventListener("change", (e) =>
      applyPrefs({ spread: Number((e.target as HTMLInputElement).value) }));
  }

  async function applyPrefs(mut: Partial<AtlasPrefs>) {
    savePrefs({ ...loadPrefs(), ...mut });
    const lv = trail[trail.length - 1];
    if (lv.kind === "l0" || lv.kind === "l1" || lv.kind === "l2") {
      await show(lv, false);              // 좌표는 그대로 — 렌더 프리퍼런스만 재적용
      viewPanel.hidden = false;           // 패널 유지(연속 조정)
      drawViewPanel();
    } else {
      drawViewPanel();
    }
  }

  function drawCrumb() {
    crumbEl.innerHTML = trail.map((lv, i) => {
      const name = lv.kind === "l0" ? "Atlas" : lv.kind === "l1" ? lv.domain
        : lv.kind === "compare" ? `${lv.a} ⇄ ${lv.b}`
        : lv.kind === "grid" ? "Grid" : lv.kind === "matrix" ? "Matrix" : lv.seed;
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
  /** D32 — Unlinked 필터(열린 층): 고아(연결 0)를 숨긴다. 초점(seed)은 항상 유지. */
  function filterUnlinked(ns: N3[]): N3[] {
    if (loadPrefs().unlinked !== "hide") return ns;
    return ns.filter((n) => (n.degree || 0) > 0 || n.focus);
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
    const flat = loadPrefs().view === "2d";   // 2D 에선 원환을 화면 평면(x/y)에 그대로
    const nodes: N3[] = domains.map((d, i) => {
      const ang = (2 * Math.PI * i) / n;
      return { id: d.domain, label: d.domain, kind: "domain", records: d.episodes,
               x: Math.cos(ang), y: Math.sin(ang) * (flat ? 1 : 0.25), z: flat ? 0 : Math.sin(ang) };
    });
    // Bridge 전용 도메인(레코드 0 — 예: 브릿지 매트릭스의 외부 축)은 L0 노드가
    // 없으므로 결합에서 제외 — 미지 노드 참조는 렌더러가 거부한다.
    const known = new Set(nodes.map((d) => d.id));
    const links: L3[] = ties
      .filter((t) => t.source !== t.target && known.has(t.source) && known.has(t.target))
      .map((t) => ({ source: t.source, target: t.target, cross: true, weight: t.ties }));
    tools.insertAdjacentHTML("beforeend",
      `<button class="chip" id="t-grid">Grid</button><button class="chip" id="t-matrix">Matrix</button>`);
    tools.querySelector("#t-grid")!.addEventListener("click", () => { trail.push({ kind: "grid" }); show(trail[trail.length - 1]); });
    tools.querySelector("#t-matrix")!.addEventListener("click", () => { trail.push({ kind: "matrix" }); show(trail[trail.length - 1]); });
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
      <button class="btn btn-primary" id="focus">Trace from here</button>
      <div id="ties-suggest"></div>`;
    detail.querySelector("#focus")!.addEventListener("click", () => {
      trail.push({ kind: "l2", seed: nd.id }); show(trail[trail.length - 1]);
    });
    // D32 고아 최소화 — 연결 0 이면 잇기 후보(태그·도메인 근사, D25 전 단계)를
    // 제안하고, Bind 는 fragment MERGE(단일 쓰기 표면)로 확정한다.
    if ((nd.degree || 0) === 0) void suggestTiesInto(nd);
  }

  async function suggestTiesInto(nd: N3) {
    const box = detail.querySelector<HTMLElement>("#ties-suggest");
    if (!box) return;
    box.innerHTML = `<p class="hint">Looking for possible ties…</p>`;
    try {
      const res = await api.suggestTies(cfg.owner, nd.id);
      const sug: { id: string; title: string; domain: string; shared_tags: string[] }[] =
        res.suggestions || [];
      if (!res.executed || sug.length === 0) {
        box.innerHTML = `<p class="hint">Unlinked — no tie candidates yet.
          Tag it to surface matches.</p>`;
        return;
      }
      box.innerHTML = `<p class="hint" style="margin-bottom:.3rem">Unlinked — possible ties</p>` +
        sug.map((s, i) => `
          <div class="list-row">
            <span class="title" style="font-size:.9em">${s.title}
              ${s.shared_tags.length ? `<span class="hint">· ${s.shared_tags.join(", ")}</span>` : ""}</span>
            <span class="actions" style="visibility:visible"><button data-bind="${i}">Bind</button></span>
          </div>`).join("");
      box.querySelectorAll<HTMLElement>("[data-bind]").forEach((b) =>
        b.addEventListener("click", async () => {
          const s = sug[Number(b.dataset.bind)];
          b.textContent = "…";
          try {
            // 양 노드 모두 기존 노드(matched) — title 만 동봉해 클로버 없이 MERGE
            await api.ingestFragment({
              nodes: [{ type: "concept", id: nd.id, title: nd.label },
                      { type: "concept", id: s.id, title: s.title }],
              edges: [{ type: "relates_to", from: nd.id, to: s.id }],
            });
            await show(trail[trail.length - 1], false);   // 재렌더(연결 반영)
            detail.hidden = false;                        // show() 가 닫으므로 확인 문구 재표시
            detail.innerHTML = `<h2>${nd.label}</h2>
              <p>Bound to <b>${s.title}</b> — 1 connection added.</p>`;
          } catch {
            b.textContent = "Bind";
            box.insertAdjacentHTML("beforeend",
              `<div class="error-banner">Couldn't bind — sidecar unreachable.</div>`);
          }
        }));
    } catch {
      box.innerHTML = `<p class="hint">Tie suggestions unavailable.</p>`;
    }
  }

  async function showL1(domain: string) {
    setMsg(`<p class="hint">${COPY.atlas.loading(1)}</p>`);
    const res = await api.graph(cfg.owner, { domain });
    if (!res.executed || (res.nodes || []).length === 0) {
      setMsg(`<div class="empty"><p>${COPY.atlas.idle}</p></div>`); return;
    }
    setMsg("");
    g3 = mount3d(canvas, filterUnlinked(toN3(res.nodes, domain)), toL3(res.edges), {
      onClick: (nd) => {
        if (nd.gateway) { trail.push({ kind: "l1", domain: nd.domain! }); show(trail[trail.length - 1]); }
        else openDetail(nd);
      },
      onRightClick: (nd) => { trail.push({ kind: "l2", seed: nd.id }); show(trail[trail.length - 1]); },
    });
    enableLasso(g3, canvas, (ids) => {
      float.hidden = false;
      float.innerHTML = `<button class="btn btn-primary">Share this slice (${ids.length})</button>`;
      float.querySelector("button")!.addEventListener("click", () => {
        float.hidden = true; openShareSheet({ ids });
      });
      setTimeout(() => { float.hidden = true; }, 8000);
    });
  }

  async function showL2(seed: string) {
    setMsg(`<p class="hint">${COPY.atlas.loading(1)}</p>`);
    const res = await api.graph(cfg.owner, { seed, depth: 2 });
    if (!res.executed) { setMsg(`<div class="error-banner">${COPY.atlas.error}</div>`); return; }
    setMsg("");
    g3 = mount3d(canvas, filterUnlinked(toN3(res.nodes, undefined, seed)), toL3(res.edges), {
      onClick: (nd) => openDetail(nd),
      onRightClick: (nd) => { trail.push({ kind: "l2", seed: nd.id }); show(trail[trail.length - 1]); },
    });
    enableLasso(g3, canvas, (ids) => {
      float.hidden = false;
      float.innerHTML = `<button class="btn btn-primary">Share this slice (${ids.length})</button>`;
      float.querySelector("button")!.addEventListener("click", () => {
        float.hidden = true; openShareSheet({ ids });
      });
      setTimeout(() => { float.hidden = true; }, 8000);
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


  /** Multiples 그리드(Phase C1) — 도메인당 평면 미니 렌즈(좌표 재사용), hover 시
   *  같은 id(크로스도메인 고스트)가 다른 카드에서 동시 점등(brushing&linking). */
  async function showGrid() {
    setMsg(`<p class="hint">${COPY.atlas.loading(0)}</p>`);
    const l0 = await api.atlasL0(cfg.owner);
    const domains: string[] = (l0.domains || []).slice(0, 9).map((d: any) => d.domain);
    if (domains.length === 0) { setMsg(`<div class="empty"><p>${COPY.atlas.idle}</p></div>`); return; }
    setMsg("");
    canvas.innerHTML = `<div style="display:grid;grid-template-columns:repeat(${Math.min(domains.length, 3)},1fr);
      gap:.75rem;height:100%;padding:3rem 0 .5rem">${domains.map((d) => `
      <div style="display:flex;flex-direction:column;border:1px solid ${PAPER_EDGE};border-radius:var(--radius);overflow:hidden">
        <button class="chip" data-open="${d}" style="border:none;border-radius:0;text-align:left;padding:.35rem .75rem">${d}</button>
        <div data-mini="${d}" style="flex:1;min-height:0"></div>
      </div>`).join("")}</div>`;
    const cards: { d: string; g: Graph; s: Sigma }[] = [];
    for (const d of domains) {
      const res = await api.graph(cfg.owner, { domain: d });
      const g = new Graph();
      addShapedGraph(g, res.nodes || [], res.edges || []);
      const s = new Sigma(g, canvas.querySelector(`[data-mini="${CSS.escape(d)}"]`) as HTMLElement,
                          { allowInvalidContainer: true, renderLabels: false });
      extraSigmas.push(s);
      cards.push({ d, g, s });
    }
    for (const card of cards) {
      card.s.on("enterNode", ({ node }) => {
        for (const other of cards) {
          other.s.setSetting("nodeReducer", (n, data) =>
            n === node ? { ...data, color: POINT, size: (data.size as number) * 1.5, zIndex: 1 }
                       : other === card ? data : { ...data, color: PAPER_EDGE });
          other.s.refresh();
        }
      });
      card.s.on("leaveNode", () => {
        for (const other of cards) { other.s.setSetting("nodeReducer", null as any); other.s.refresh(); }
      });
    }
    canvas.querySelectorAll("[data-open]").forEach((b) =>
      b.addEventListener("click", () => {
        trail.push({ kind: "l1", domain: (b as HTMLElement).dataset.open! }); show(trail[trail.length - 1]);
      }));
  }

  /** Coupling Matrix(Phase C2) — 도메인×도메인 히트맵(잉크 농도 3단), 셀 → Compare. */
  async function showMatrix() {
    setMsg(`<p class="hint">${COPY.atlas.loading(0)}</p>`);
    const l0 = await api.atlasL0(cfg.owner);
    const domains: string[] = (l0.domains || []).map((d: any) => d.domain);
    const ties = new Map<string, number>();
    for (const t of l0.ties || []) {
      ties.set(`${t.source}|${t.target}`, (ties.get(`${t.source}|${t.target}`) || 0) + t.ties);
      ties.set(`${t.target}|${t.source}`, (ties.get(`${t.target}|${t.source}`) || 0) + t.ties);
    }
    setMsg("");
    const max = Math.max(1, ...ties.values());
    const shade = (v: number) => v === 0 ? "transparent"
      : v < max / 3 ? "hsl(var(--muted))" : v < (2 * max) / 3 ? "hsl(var(--muted-foreground)/.45)" : "hsl(var(--foreground)/.8)";
    canvas.innerHTML = `<div style="padding:3.5rem 1rem 1rem;overflow:auto;height:100%">
      <table style="border-collapse:collapse">
        <tr><th></th>${domains.map((d) => `<th style="padding:.3rem .6rem;font-weight:400" class="hint">${d}</th>`).join("")}</tr>
        ${domains.map((a) => `<tr><th style="padding:.3rem .6rem;text-align:right;font-weight:400" class="hint">${a}</th>
          ${domains.map((b) => {
            const v = a === b ? 0 : (ties.get(`${a}|${b}`) || 0);
            return `<td data-a="${a}" data-b="${b}" title="${v} ties" style="width:3.2rem;height:2.4rem;
              border:1px solid ${PAPER_EDGE};background:${shade(v)};cursor:${v ? "pointer" : "default"};
              text-align:center;color:${v >= (2 * max) / 3 && v > 0 ? "hsl(var(--background))" : "inherit"}">${v || ""}</td>`;
          }).join("")}</tr>`).join("")}
      </table></div>`;
    canvas.querySelectorAll("td[data-a]").forEach((td) =>
      td.addEventListener("click", () => {
        const a = (td as HTMLElement).dataset.a!, b = (td as HTMLElement).dataset.b!;
        if (a !== b && (td as HTMLElement).title !== "0 ties") {
          trail.push({ kind: "compare", a, b }); show(trail[trail.length - 1]);
        }
      }));
  }

  async function show(level: Level, push = true) {
    if (push && trail[trail.length - 1] !== level) { /* pushed by caller */ }
    detail.hidden = true;
    tools.innerHTML = "";
    float.hidden = true;
    viewPanel.hidden = true;
    extraSigmas.forEach((s) => s.kill());
    extraSigmas = [];
    if (g3) { g3._destructor?.(); g3 = null; }
    canvas.innerHTML = "";
    drawCrumb();
    // View 컨트롤은 3D 레벨에만 — Compare/Grid/Matrix 는 의도적 평면(D22)
    if (level.kind === "l0" || level.kind === "l1" || level.kind === "l2") viewControl();
    try {
      if (level.kind === "l0") await showL0();
      else if (level.kind === "l1") await showL1(level.domain);
      else if (level.kind === "compare") await showCompare(level.a, level.b);
      else if (level.kind === "grid") await showGrid();
      else if (level.kind === "matrix") await showMatrix();
      else await showL2(level.seed);
    } catch {
      setMsg(`<div class="error-banner">${COPY.atlas.error}</div>`);
    }
  }

  trail.push({ kind: "l0" });
  if (deep === "compare" && parts[2] && parts[3]) trail.push({ kind: "compare", a: parts[2], b: parts[3] });
  else if (deep === "trace" && parts[2]) trail.push({ kind: "l2", seed: parts[2] });
  else if (deep) trail.push({ kind: "l1", domain: deep });
  await show(trail[trail.length - 1]);
}
