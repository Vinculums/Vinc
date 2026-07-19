import Graph from "graphology";
import Sigma from "sigma";
import { api, cfg, type GraphEdge, type GraphNode } from "../api";
import { COPY } from "../copy";
import { enableLasso, mount3d, mountMinis3d, type L3, type N3 } from "./atlas3d";
import { openShareSheet } from "../sheets";
import { LAYOUTS, loadPrefs, savePrefs, type AtlasLayout, type AtlasPrefs } from "../atlas-prefs";

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

// Signal 팔레트 — theme.ts(허브 생성물)에서 런타임 모드에 따라 읽는다(하드코딩 금지).
import { palette as signal, hexStr } from "../theme";
function curPal() {
  return signal[document.documentElement.dataset.theme === "dark" ? "dark" : "light"];
}
const THEME = {
  get ink() { return hexStr(curPal().text); },
  get inkMute() { return hexStr(curPal().textMute); },
  get edge() { return hexStr(curPal().line); },
  get point() {
    const dark = document.documentElement.dataset.theme === "dark";
    return hexStr(dark ? curPal().ring : curPal().primary);
  },
};

export async function renderAtlas(el: HTMLElement) {
  el.innerHTML = `
    <div id="atlas-crumb"></div>
    <div id="atlas-canvas"></div>
    <div id="atlas-legend"></div>
    <div id="atlas-detail" hidden></div>
    <div id="atlas-msg" style="position:absolute;inset:0;display:grid;place-items:center;z-index:1"></div>
    <div id="atlas-tools" style="position:absolute;top:var(--space-4,1rem);right:var(--space-6,1.5rem);z-index:2;display:flex;gap:.5rem"></div>
    <div id="atlas-float" style="position:absolute;left:50%;bottom:var(--space-6,1.5rem);transform:translateX(-50%);z-index:3" hidden></div>
    <div id="atlas-hint" hidden style="position:absolute;left:50%;bottom:var(--space-6,1.5rem);transform:translateX(-50%);z-index:2;pointer-events:none;white-space:nowrap;font-size:.72rem;color:hsl(var(--caption-foreground));background:hsl(var(--card)/.72);border:1px solid hsl(var(--border));border-radius:999px;padding:.25rem .7rem"></div>
    <div id="atlas-view-panel" hidden
         style="position:absolute;top:3.2rem;right:var(--space-6,1.5rem);z-index:4;width:17rem"></div>
  `;
  const canvas = el.querySelector<HTMLElement>("#atlas-canvas")!;
  const crumbEl = el.querySelector<HTMLElement>("#atlas-crumb")!;
  const detail = el.querySelector<HTMLElement>("#atlas-detail")!;
  const msg = el.querySelector<HTMLElement>("#atlas-msg")!;
  const tools = el.querySelector<HTMLElement>("#atlas-tools")!;
  const float = el.querySelector<HTMLElement>("#atlas-float")!;
  const lassoHint = el.querySelector<HTMLElement>("#atlas-hint")!;
  lassoHint.textContent = COPY.ui.atlasx.lassoHint;
  const viewPanel = el.querySelector<HTMLElement>("#atlas-view-panel")!;

  // lasso(영역 선택→공유) 배선 + 발견성 힌트. L1/L2 그래프에서만 활성.
  function wireLasso() {
    lassoHint.hidden = false;   // "Shift+드래그로 영역 선택" 안내(발견성)
    enableLasso(g3, canvas, (ids) => {
      lassoHint.hidden = true;
      float.hidden = false;
      float.innerHTML = `<button class="btn btn-primary">${COPY.ui.atlasx.shareSlice(ids.length)}</button>`;
      float.querySelector("button")!.addEventListener("click", () => {
        float.hidden = true; openShareSheet({ ids });
      });
      setTimeout(() => { float.hidden = true; lassoHint.hidden = false; }, 8000);
    });
  }

  let g3: any = null;                 // 3D 인스턴스 (L0/L1/L2)
  let minis3d: { dispose(): void } | null = null;   // Grid 미니(단일 렌더러, D34)
  let extraSigmas: Sigma[] = [];      // Compare 2D 렌즈
  let curEdges: GraphEdge[] = [];     // 현재 레벨 엣지 — 상세 레일 in/out 표기용
  const cardLayout = new Map<string, AtlasLayout>();  // Grid 카드별 프리셋 오버라이드(D34)
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
      `<button class="chip" id="t-view" aria-haspopup="true">${COPY.ui.panel.viewChip(p.view === "2d" ? "2D" : "3D")}</button>`);
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
      <p class="hint" style="margin-top:0">${COPY.ui.panel.view}</p>
      <p><span class="seg">${chip("view", "3d", "3D", p.view === "3d")}${chip("view", "2d", "2D", p.view === "2d")}</span></p>
      <p class="hint">${COPY.ui.panel.nodeSize}</p>
      <p><span class="seg">${chip("size", "links", COPY.ui.panel.byConnections, p.size === "links")}${chip("size", "uniform", COPY.ui.panel.uniform, p.size === "uniform")}</span></p>
      <p class="hint">${COPY.ui.panel.labels}</p>
      <p><span class="seg">${chip("labels", "hover", COPY.ui.panel.onHover, p.labels === "hover")}${chip("labels", "none", COPY.ui.panel.off, p.labels === "none")}</span></p>
      <p class="hint">${COPY.ui.panel.arrows}</p>
      <p><span class="seg">${chip("arrows", "on", COPY.ui.panel.on, p.arrows)}${chip("arrows", "off", COPY.ui.panel.off, !p.arrows)}</span></p>
      <p class="hint">${COPY.ui.panel.unlinkedNodes}</p>
      <p><span class="seg">${chip("unlinked", "show", COPY.ui.panel.show, p.unlinked === "show")}${chip("unlinked", "hide", COPY.ui.panel.hide, p.unlinked === "hide")}</span></p>
      <p class="hint">${COPY.ui.panel.layout}</p>
      <p><span class="seg">${LAYOUTS.map((l) => chip("layout", l, l, p.layout === l)).join("")}</span></p>
      <label class="hint" style="display:block">${COPY.ui.panel.spread}
        <input id="pref-spread" type="range" min="0.6" max="1.6" step="0.2" value="${p.spread}" style="width:100%">
      </label>
      <p class="hint" style="margin-bottom:0">${COPY.ui.panel.fixedNote}</p>`;
    viewPanel.querySelectorAll<HTMLElement>("[data-pref]").forEach((b) =>
      b.addEventListener("click", () => {
        const k = b.dataset.pref as keyof AtlasPrefs;
        applyPrefs(k === "arrows" ? { arrows: b.dataset.val === "on" }
                                  : ({ [k]: b.dataset.val } as Partial<AtlasPrefs>));
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
      kind: nd.kind || "concept",
      // 브릿지는 gateway 판정 면제(F2) — 단일 domain 이 없다(domain=source_domain 은
      // 보고 편의). 반투명+클릭 시 렌즈 이동은 브릿지에 무의미하다.
      gateway: lensDomain !== undefined && nd.domain !== lensDomain
        && (nd.kind || "concept") !== "bridge",
      focus: focusId !== undefined && nd.id === focusId,
      x: nd.x, y: nd.y, z: nd.z,
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
        <a class="btn btn-primary" href="#packs">${COPY.atlas.idleCta}</a></div>`);
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
      `<button class="chip" id="t-grid">${COPY.ui.atlasx.grid}</button><button class="chip" id="t-matrix">${COPY.ui.atlasx.matrix}</button>`);
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
    // 방향성 관계(화살촉 3종)만 in/out 분리 표기 — 대칭(relates_to)은 방향이 무의미
    const DIRECTED = ["PREREQUISITE_OF", "DERIVES_FROM", "INHERITS_FROM"];
    const out = curEdges.filter((e) => e.source === nd.id && DIRECTED.includes(e.type)).length;
    const inn = curEdges.filter((e) => e.target === nd.id && DIRECTED.includes(e.type)).length;
    // 부착(F2) — 브릿지·에피소드·레코드 팔은 화살촉 없는 구조 연결이라 별도 집계
    const ATTACH = ["CONNECTS", "ABOUT", "ATTACHES_TO"];
    const att = curEdges.filter((e) =>
      (e.source === nd.id || e.target === nd.id) && ATTACH.includes(e.type)).length;
    detail.hidden = false;
    detail.innerHTML = `
      <h2>${nd.label}</h2>
      <p><span class="badge">${COPY.ui.atlasx.domain(nd.domain || COPY.ui.common.none)}</span></p>
      <p class="hint">${COPY.ui.atlasx.connections(nd.degree || 0)}${out + inn > 0
        ? COPY.ui.atlasx.outIn(out, inn) : ""}${att > 0
        ? COPY.ui.atlasx.attachments(att) : ""}${nd.gateway ? COPY.ui.atlasx.gateway : ""}.</p>
      <button class="btn btn-primary" id="focus">${COPY.ui.atlasx.traceCta}</button>
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
    box.innerHTML = `<p class="hint">${COPY.ui.atlasx.suggestLooking}</p>`;
    try {
      const res = await api.suggestTies(cfg.owner, nd.id);
      const sug: { id: string; title: string; domain: string; shared_tags: string[] }[] =
        res.suggestions || [];
      if (!res.executed || sug.length === 0) {
        box.innerHTML = `<p class="hint">${COPY.ui.atlasx.suggestNone}</p>`;
        return;
      }
      box.innerHTML = `<p class="hint" style="margin-bottom:.3rem">${COPY.ui.atlasx.suggestHeader}</p>` +
        sug.map((s, i) => `
          <div class="list-row">
            <span class="title" style="font-size:.9em">${s.title}
              ${s.shared_tags.length ? `<span class="hint">· ${s.shared_tags.join(", ")}</span>` : ""}</span>
            <span class="actions" style="visibility:visible"><button data-bind="${i}">${COPY.ui.atlasx.bind}</button></span>
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
              <p>${COPY.ui.atlasx.bound(s.title)}</p>`;
          } catch {
            b.textContent = "Bind";
            box.insertAdjacentHTML("beforeend",
              `<div class="error-banner">${COPY.ui.atlasx.bindFail}</div>`);
          }
        }));
    } catch {
      box.innerHTML = `<p class="hint">${COPY.ui.atlasx.unavailable}</p>`;
    }
  }

  async function showL1(domain: string) {
    setMsg(`<p class="hint">${COPY.atlas.loading(1)}</p>`);
    const res = await api.graph(cfg.owner, { domain, layout: loadPrefs().layout });
    if (!res.executed || (res.nodes || []).length === 0) {
      setMsg(`<div class="empty"><p>${COPY.atlas.idle}</p></div>`); return;
    }
    setMsg("");
    curEdges = res.edges || [];
    g3 = mount3d(canvas, filterUnlinked(toN3(res.nodes, domain)), toL3(res.edges), {
      onClick: (nd) => {
        if (nd.gateway) { trail.push({ kind: "l1", domain: nd.domain! }); show(trail[trail.length - 1]); }
        else openDetail(nd);
      },
      onRightClick: (nd) => { trail.push({ kind: "l2", seed: nd.id }); show(trail[trail.length - 1]); },
    });
    wireLasso();
  }

  async function showL2(seed: string) {
    setMsg(`<p class="hint">${COPY.atlas.loading(1)}</p>`);
    const res = await api.graph(cfg.owner, { seed, depth: 2, layout: loadPrefs().layout });
    if (!res.executed) { setMsg(`<div class="error-banner">${COPY.atlas.error}</div>`); return; }
    setMsg("");
    curEdges = res.edges || [];
    g3 = mount3d(canvas, filterUnlinked(toN3(res.nodes, undefined, seed)), toL3(res.edges), {
      onClick: (nd) => openDetail(nd),
      onRightClick: (nd) => { trail.push({ kind: "l2", seed: nd.id }); show(trail[trail.length - 1]); },
    });
    wireLasso();
  }

  /** Compare — 좌우 2D 렌즈(Sigma) + 중앙 개념 결합 레일 (D22). 비교 뷰는
   *  의도적으로 평면(나란히 대조가 목적 — 3D 회전은 좌우 대응을 깨뜨림). */
  function addShapedGraph(g: Graph, nodes: GraphNode[], edges: GraphEdge[]) {
    for (const nd of nodes) {
      const label = nd.label === nd.id ? nd.id.replace(/^[a-z]+:/, "") : nd.label;
      g.addNode(nd.id, { label, x: nd.x ?? 0, y: nd.y ?? 0,
        size: 4 + Math.sqrt(nd.degree || 1) * 2, color: THEME.ink, domain: nd.domain });
    }
    for (const e of edges) {
      if (g.hasNode(e.source) && g.hasNode(e.target) && !g.hasEdge(e.source, e.target))
        g.addEdge(e.source, e.target, { size: e.cross_domain ? 0.8 : 1.6,
          color: e.cross_domain ? THEME.inkMute : THEME.edge });
    }
  }

  async function showCompare(a: string, b: string) {
    setMsg("");
    canvas.innerHTML = `
      <div style="display:grid;grid-template-columns:1fr 22rem 1fr;height:100%">
        <div id="cmp-a"></div>
        <div id="cmp-rail" style="overflow:auto;border-left:1px solid ${THEME.edge};
             border-right:1px solid ${THEME.edge};padding:0 1rem">
          <h3 style="position:sticky;top:0;background:hsl(var(--background))">${a} ⇄ ${b}</h3>
          <div id="cmp-pairs" class="hint">${COPY.ui.atlasx.tiesLoading}</div>
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
      ? `<p class="hint">${COPY.ui.atlasx.noTies}</p>`
      : pairs.map((p, i) => `
        <div class="list-row" data-i="${i}">
          <span class="title" style="font-size:.9em">${p.a_title}
            <span class="hint">${p.kind === "direct" ? "⇢" : "◇"}</span> ${p.b_title}</span>
          ${p.via_title ? `<span class="meta">${COPY.ui.atlasx.via(p.via_title)}</span>` : ""}
        </div>`).join("");
    const highlight = (p: any | null) => {
      for (const { s } of [left, right]) {
        s.setSetting("nodeReducer", (node, data) => {
          if (!p) return data;
          const hit = node === p.a_id || node === p.b_id;
          return hit ? { ...data, color: THEME.point, zIndex: 1, size: (data.size as number) * 1.4 }
                     : { ...data, color: THEME.edge, label: "" };
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
    const lay = (d: string) => cardLayout.get(d) || loadPrefs().layout;
    canvas.innerHTML = `<div style="display:grid;grid-template-columns:repeat(${Math.min(domains.length, 3)},1fr);
      gap:.75rem;height:100%;padding:3rem 0 .5rem">${domains.map((d) => `
      <div style="display:flex;flex-direction:column;border:1px solid ${THEME.edge};border-radius:var(--radius);overflow:hidden">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <button class="chip" data-open="${d}" style="border:none;border-radius:0;text-align:left;padding:.35rem .75rem">${d}</button>
          <button class="chip" data-cycle="${d}" title="Cycle layout (D34)"
                  style="border:none;border-radius:0;padding:.35rem .75rem">${lay(d)} ↻</button>
        </div>
        <div data-mini="${d}" style="flex:1;min-height:0"></div>
      </div>`).join("")}</div>`;
    // 카드별 레이아웃 사이클 — 프리셋만 바꿔 그리드 재조회(좌표는 사이드카 정본)
    canvas.querySelectorAll<HTMLElement>("[data-cycle]").forEach((b) =>
      b.addEventListener("click", () => {
        const d = b.dataset.cycle!;
        cardLayout.set(d, LAYOUTS[(LAYOUTS.indexOf(lay(d)) + 1) % LAYOUTS.length]);
        show(trail[trail.length - 1], false);
      }));
    // 미니 = 3D(단일 WebGL 렌더러 뷰포트 분할 — 컨텍스트 상한 회피, D34).
    // hover 시 같은 id(크로스도메인 고스트)가 전 카드에서 포인트색 동시 점등.
    const cells: { el: HTMLElement; nodes: N3[]; links: L3[] }[] = [];
    for (const d of domains) {
      const res = await api.graph(cfg.owner, { domain: d, layout: lay(d) });
      cells.push({
        el: canvas.querySelector(`[data-mini="${CSS.escape(d)}"]`) as HTMLElement,
        nodes: filterUnlinked(toN3(res.nodes || [])),
        links: toL3(res.edges || []),
      });
    }
    minis3d = mountMinis3d(canvas.firstElementChild as HTMLElement, cells);
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
              border:1px solid ${THEME.edge};background:${shade(v)};cursor:${v ? "pointer" : "default"};
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
    lassoHint.hidden = true;   // 기본 숨김 — L1/L2 의 wireLasso 가 다시 켠다
    viewPanel.hidden = true;
    extraSigmas.forEach((s) => s.kill());
    extraSigmas = [];
    if (minis3d) { minis3d.dispose(); minis3d = null; }
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
