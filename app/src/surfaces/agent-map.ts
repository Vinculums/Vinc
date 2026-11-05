/** Agent 관계 지도 — Agents 보드의 기본 면(`#home/agents`), Agent 에 절차가 있는 스페이스에서.
 *
 *  Agent(Maker·Checker)는 가운데 큰 점, 절차는 자기 Agent 둘레의 점, 정책은 위쪽의 알약이다. 점선은 정책이
 *  다스리는 절차(`about` 엣지), 아래 곡선은 Checker 의 실패가 Maker 로 돌아가는 길이다. 점을 누르면 옆 칸이
 *  그 항목을 보여 주고, 지도 아래에는 같은 항목을 목록으로 둔다(지도에서 누르기 어려울 때, 그리고 키보드로).
 *  절차는 `#home/agents/procedure/<id>` 에서 고친다. 정책 붙이기와 빼기는 이 면에서 한다(`about` 엣지 쓰기).
 *  배치와 합친 목록은 `agent-ontology.ts` 의 순수 함수가 정한다. 근거: [[vinc.canon.terminology §3.2]] */
import { api, cfg, isCommunitySpace } from "../api";
import { COPY } from "../copy";
import { esc } from "../components";
import { icon } from "../icons";
import { compose, layoutMap, mapHeight, policySlug, type MapAgent, type MapPolicy, type MapProcedure, type PolicyRule,
         type Props } from "../agent-ontology";
import type { AgentRole } from "../lnb";
import { announceRolesChanged } from "../role-composer";

export interface MapData {
  agents: (MapAgent & { mission: string; props: Props; domain: string })[];
  procedures: (MapProcedure & { one_liner: string; props: Props; domain: string })[];
  policies: (MapPolicy & { rule: Props; domain: string })[];
  legacy: AgentRole[];
}

/** 로스터의 역할마다 계약을 읽어, 절차가 있는 것을 Agent 로 본다. 절차는 노드를 하나씩 읽는다(제목·설명·props).
 *  Agent 가 하나도 없으면 null: 그 스페이스는 직함 역할 보드를 그대로 쓴다. */
export async function loadAgentMap(roles: AgentRole[]): Promise<MapData | null> {
  const reads = await Promise.all(roles.map((r) => api.role(cfg.owner, r.id).catch(() => null)));
  const agents: MapData["agents"] = [];
  const procIndex = new Map<string, { agent: string; policies: string[] }>();
  const policies = new Map<string, MapData["policies"][number]>();
  reads.forEach((got, i) => {
    const procs = got?.executed && Array.isArray(got.procedures) ? got.procedures : [];
    if (!procs.length) return;
    const node = got.node || {};
    const props = (node.props || {}) as Props;
    agents.push({ id: roles[i].id, title: node.title || roles[i].title, tier: String(props.tier || ""),
                  mission: String(props.mission || node.one_liner || ""), props, domain: String(node.domain || roles[i].domain || "") });
    for (const p of procs) procIndex.set(p.id, { agent: roles[i].id, policies: Array.isArray(p.policies) ? p.policies : [] });
    for (const pol of Array.isArray(got.policies) ? got.policies : [])
      policies.set(pol.id, { id: pol.id, title: pol.title || policySlug(pol.id), rule: pol.rule || {}, domain: "" });
  });
  if (!agents.length) return null;
  // 역할 읽기는 절차에 붙은 정책만 돌려준다. 붙은 절차가 하나도 없는 정책도 지도에 있어야 다시 붙일 수 있으므로
  // 결정 목록에서 `decision:policy-` 를 따로 모은다. 목록을 못 읽으면 붙은 정책만으로 그린다.
  try {
    const tree: any = await api.navTree(cfg.owner, { nodes: true, kind: "decision" });
    const loose = ([] as any[]).concat(...((tree?.domains || []).map((d: any) => d.nodes || [])), tree?.unfiled || [])
      .map((n: any) => String(n?.id || "")).filter((id: string) => id.startsWith("decision:policy-"));
    const got = await Promise.all(loose.map((id: string) => api.nodeContext(cfg.owner, id, 1).catch(() => null)));
    got.forEach((n: any, i: number) => {
      if (!n || n.executed === false) return;
      const had = policies.get(loose[i]);
      policies.set(loose[i], { id: loose[i], title: had?.title || String(n.title || policySlug(loose[i])),
        rule: had?.rule || ((n.props || {}) as Props).rule as Props || {}, domain: String(n.domain || "") });
    });
  } catch { /* 붙은 정책만으로 그린다 */ }
  const ids = [...procIndex.keys()];
  const nodes = await Promise.all(ids.map((id) => api.nodeContext(cfg.owner, id, 1).catch(() => null)));
  const procedures: MapData["procedures"] = [];
  nodes.forEach((node: any, i) => {
    // node-context 는 평평한 꼴이다: `node` 칸은 id 문자열이고 title·one_liner·props 는 바깥에 있다.
    if (!node || node.executed === false) return;
    const meta = procIndex.get(ids[i])!;
    procedures.push({ id: ids[i], agent: meta.agent, policies: meta.policies,
      title: String(node.title || ids[i]).replace(/ \((Maker|Checker) procedure\)$/, ""),
      one_liner: String(node.one_liner || ""), props: (node.props || {}) as Props, domain: String(node.domain || "") });
  });
  const agentIds = new Set(agents.map((a) => a.id));
  return { agents, procedures, policies: [...policies.values()], legacy: roles.filter((r) => !agentIds.has(r.id)) };
}

type Pick = { kind: "agent" | "procedure" | "policy"; id: string } | null;
let picked: Pick = null;
let legacyOpen = false;
/** 지난번에 그린 지도의 실제 폭(px). 다음 배치가 이 폭에 맞춘다. */
let lastWidth = 0;
const short = (s: string) => { const v = s.replace(/^Policy:\s*/, ""); return v.length > 26 ? v.slice(0, 25) + "…" : v; };

/** 지도 면을 그린다. `drawLegacy` 는 직함 역할이 남은 스페이스에서 옛 보드를 아래에 그린다. */
export function drawAgentMap(el: HTMLElement, data: MapData, drawLegacy: (host: HTMLElement) => void): void {
  const M = COPY.ui.agentMap;
  const readOnly = isCommunitySpace(cfg.team);
  // 그린 폭에 맞춰 배치한다: 고정 viewBox 를 줄여 그리면 글자도 같이 줄어 읽기 어렵다(11px 가 9px 로).
  const W = Math.max(560, Math.round(lastWidth || 760)), H = mapHeight(data.procedures, data.agents);
  const { nodes, links } = layoutMap(data.agents, data.procedures, data.policies, W, H);
  const at = new Map(nodes.map((n) => [n.id, n]));
  const proc = new Map(data.procedures.map((p) => [p.id, p]));
  const pol = new Map(data.policies.map((p) => [p.id, p]));
  const ag = new Map(data.agents.map((a) => [a.id, a]));
  if (picked && !at.has(picked.id)) picked = null;

  const line = (a: { x: number; y: number }, b: { x: number; y: number }, cls: string) =>
    `<line class="${cls}" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}"/>`;
  let svg = "";
  let lines = "";
  for (const l of links) {
    const a = at.get(l.from)!, b = at.get(l.to)!;
    if (l.kind === "has") lines += line(a, b, "am-has");
    else if (l.kind === "governs") lines += line({ x: a.x, y: a.y + 16 }, b, "am-gov");
    else lines += `<path class="am-ret" d="M${a.x - 26},${a.y + 30} C ${a.x - 110},${a.y + 170} ${b.x + 110},${b.y + 170} ${b.x + 26},${b.y + 30}"/>
      <text class="am-ret-t" x="${(a.x + b.x) / 2}" y="${a.y + 150}" text-anchor="middle">${esc(M.returns)}</text>`;
  }
  for (const n of nodes) {
    const sel = picked?.id === n.id ? " is-sel" : "";
    const label = `${n.kind === "agent" ? M.agent : n.kind === "policy" ? M.policy : M.procedure}: ${n.label}`;
    if (n.kind === "policy") {
      svg += `<g class="am-node am-pol${sel}" data-pick="policy" data-id="${esc(n.id)}" tabindex="0" role="button" aria-label="${esc(label)}">
        <circle cx="${n.x}" cy="${n.y}" r="${n.r}"/><title>${esc(n.label)}</title>
        <text x="${n.x}" y="${n.y + n.r + 14}" text-anchor="middle">${esc(short(n.label))}</text></g>`;
    } else {
      const dx = n.anchor === "end" ? -(n.r + 6) : n.anchor === "start" ? n.r + 6 : 0;
      const dy = n.kind === "agent" ? n.r + 18 : 4;
      svg += `<g class="am-node am-${n.kind}${sel}" data-pick="${n.kind}" data-id="${esc(n.id)}" tabindex="0" role="button" aria-label="${esc(label)}">
        <circle cx="${n.x}" cy="${n.y}" r="${n.r}"/>
        ${n.kind === "procedure" && (proc.get(n.id)?.policies.length || 0) ? `<circle class="am-mark" cx="${n.x}" cy="${n.y}" r="4"/>` : ""}
        <title>${esc(n.label)}</title><text x="${n.x + dx}" y="${n.y + dy}" text-anchor="${n.anchor}">${esc(short(n.label))}</text></g>`;
    }
  }

  const listRow = (kind: "agent" | "procedure" | "policy", id: string, title: string, sub: string) =>
    `<li><button type="button" class="am-row${picked?.id === id ? " is-sel" : ""}" data-pick="${kind}" data-id="${esc(id)}">
      <span class="am-row-t">${esc(title)}</span><span class="am-row-s">${esc(sub)}</span></button></li>`;
  const list = data.agents.map((a) => `<section class="am-group"><h3>${esc(a.title)}</h3><ul>${
      listRow("agent", a.id, a.title, M.agent)}${data.procedures.filter((p) => p.agent === a.id).map((p) =>
      listRow("procedure", p.id, p.title, p.policies.map((x) => pol.get(x)?.title || policySlug(x)).join(", ") || M.procedure)).join("")}</ul></section>`).join("")
    + (data.policies.length ? `<section class="am-group"><h3>${esc(M.policy)}</h3><ul>${data.policies.map((p) =>
      listRow("policy", p.id, p.title, M.governs(data.procedures.filter((x) => x.policies.includes(p.id)).length))).join("")}</ul></section>` : "");

  el.innerHTML = `<div class="home agents agents-board agent-map">
    <header class="home-head"><h1>${esc(M.title)} <span class="home-count">${esc(M.counts(data.agents.length, data.procedures.length, data.policies.length))}</span></h1></header>
    <p class="hint agents-lead">${esc(M.lead)}</p>
    <p class="agents-status" role="status"></p>
    <div class="am-stage">
      <div class="am-canvas-wrap"><svg class="am-lines" viewBox="0 0 ${W} ${H}" aria-hidden="true">${lines}</svg>
        <canvas class="am-orbs" aria-hidden="true"></canvas>
        <svg class="am-svg" viewBox="0 0 ${W} ${H}" role="group" aria-label="${esc(M.mapLabel)}">${svg}</svg></div>
      <aside class="am-side" aria-live="polite">${side()}</aside>
    </div>
    <h2 class="am-list-h">${esc(M.listTitle)}</h2>
    <div class="am-list">${list}</div>
    ${data.legacy.length ? `<div class="am-legacy"><button class="btn btn-quiet" type="button" data-act="legacy">${
      esc(legacyOpen ? M.legacyHide : M.legacyShow(data.legacy.length))}</button><div class="am-legacy-host"></div></div>` : ""}
  </div>`;
  if (legacyOpen && data.legacy.length) drawLegacy(el.querySelector<HTMLElement>(".am-legacy-host")!);
  const drawn = el.querySelector<SVGSVGElement>(".am-svg")?.getBoundingClientRect().width || 0;
  if (drawn && Math.abs(drawn - W) > 24) { lastWidth = drawn; drawAgentMap(el, data, drawLegacy); return; }
  // 노드 몸통은 그래프 보기와 같은 3D orb 다(WebGL). 못 그리면 SVG 의 점이 그대로 남는다.
  const canvas = el.querySelector<HTMLCanvasElement>(".am-orbs");
  if (canvas) {
    const domainOf = new Map<string, string>([...data.agents, ...data.procedures, ...data.policies].map((x) => [x.id, x.domain]));
    const sel = picked?.id ?? null;
    void import("./agent-orbs").then((m) => {
      if (!canvas.isConnected) return;
      const ok = m.mountOrbs(canvas, nodes.map((n) => ({ ...n, domain: domainOf.get(n.id) || "" })), W, H, sel);
      canvas.closest(".am-canvas-wrap")?.classList.toggle("am-has-orbs", ok);
    }).catch(() => { /* SVG 점으로 남는다 */ });
  }

  function side(): string {
    if (!picked) return `<p class="hint">${esc(M.pick)}</p>`;
    if (picked.kind === "agent") {
      const a = ag.get(picked.id)!;
      return `<p class="am-kind">${esc(M.agent)}</p><h2>${esc(a.title)}</h2><p>${esc(a.mission)}</p>
        <div class="am-acts">${readOnly ? "" : `<a class="btn" href="#home/agents/${encodeURIComponent(a.id)}">${esc(M.editAgent)}</a>`}
        <a class="btn btn-quiet" href="#view/node/${encodeURIComponent(a.id)}">${icon("graph", 14)}<span>${esc(M.showInGraph)}</span></a></div>`;
    }
    if (picked.kind === "procedure") {
      const p = proc.get(picked.id)!, a = ag.get(p.agent);
      const rules: PolicyRule[] = p.policies.map((id) => ({ id, rule: pol.get(id)?.rule || {} }));
      const eff = compose(a?.props || {}, p.props, rules);
      const blocked = eff.filter((x) => x.kind === "tool" || x.kind === "capability").length;
      const ask = eff.filter((x) => x.kind === "person_only").length;
      return `<p class="am-kind">${esc(M.procedure)} · ${esc(a?.title || "")}</p><h2>${esc(p.title)}</h2><p>${esc(p.one_liner)}</p>
        <h3>${esc(M.governedBy)}</h3>${p.policies.length ? `<p class="am-chips">${p.policies.map((id) =>
          `<button type="button" class="am-chip am-chip-pol" data-pick="policy" data-id="${esc(id)}">${esc(pol.get(id)?.title || policySlug(id))}</button>`).join("")}</p>`
          : `<p class="hint">${esc(M.noPolicy)}</p>`}
        <h3>${esc(M.effective)}</h3><p class="hint">${esc(M.effectiveHint)}</p>
        <ul class="am-facts"><li>${esc(M.blocked(blocked))}</li><li>${esc(M.askFirst(ask))}</li></ul>
        <div class="am-acts">${readOnly ? "" : `<a class="btn btn-primary" href="#home/agents/procedure/${encodeURIComponent(p.id)}">${esc(M.edit)}</a>`}
        <a class="btn btn-quiet" href="#view/node/${encodeURIComponent(p.id)}">${icon("graph", 14)}<span>${esc(M.showInGraph)}</span></a></div>`;
    }
    const q = pol.get(picked.id)!;
    const governed = data.procedures.filter((p) => p.policies.includes(q.id));
    const others = data.procedures.filter((p) => !p.policies.includes(q.id));
    const ruleItems = [...((q.rule as any).disallowed_tools || []), ...(((q.rule as any).capabilities || {}).deny || [])];
    return `<p class="am-kind">${esc(M.policy)}</p><h2>${esc(q.title)}</h2>
      <p class="am-chips">${ruleItems.map((x: string) => `<span class="am-chip">${esc(x)}</span>`).join("")}</p>
      <h3>${esc(M.governs(governed.length))}</h3>
      <ul class="am-gov-list">${governed.map((p) => `<li><button type="button" class="am-link" data-pick="procedure" data-id="${esc(p.id)}">${esc(p.title)}</button>
        ${readOnly ? "" : `<button type="button" class="btn btn-quiet am-detach" data-detach="${esc(p.id)}">${esc(M.detach)}</button>`}</li>`).join("")}</ul>
      ${readOnly || !others.length ? (readOnly ? `<p class="hint">${esc(M.readOnlyNote)}</p>` : "") : `<div class="am-attach">
        <select id="am-attach-pick" aria-label="${esc(M.attachPick)}"><option value="">${esc(M.attachPick)}</option>${others.map((p) =>
          `<option value="${esc(p.id)}">${esc(p.title)}</option>`).join("")}</select>
        <button type="button" class="btn" data-act="attach">${esc(M.attach)}</button></div>`}
      <div class="am-acts"><a class="btn btn-quiet" href="#view/node/${encodeURIComponent(q.id)}">${icon("graph", 14)}<span>${esc(M.showInGraph)}</span></a></div>`;
  }

  const board = el.querySelector<HTMLElement>(".agent-map")!;
  const status = (s: string) => { board.querySelector(".agents-status")!.textContent = s; };
  const choose = (t: HTMLElement) => {
    picked = { kind: t.dataset.pick as "agent" | "procedure" | "policy", id: t.dataset.id! };
    drawAgentMap(el, data, drawLegacy);
    el.querySelector<HTMLElement>(`.am-node[data-id="${CSS.escape(picked.id)}"]`)?.focus({ preventScroll: true });
  };
  const writeEdge = async (fn: () => Promise<any>) => {
    status(M.saving);
    let ok = false;
    try { const r = await fn(); ok = r?.executed !== false && !(r?.warnings || []).length; } catch { ok = false; }
    status(ok ? "" : M.saveFail);
    if (ok) announceRolesChanged();
  };
  board.addEventListener("click", (e) => {
    const t = e.target as HTMLElement;
    const pick = t.closest<HTMLElement>("[data-pick]");
    if (pick) { choose(pick); return; }
    const det = t.closest<HTMLElement>("[data-detach]");
    if (det && picked?.kind === "policy") {
      const from = picked.id, to = det.dataset.detach!;
      // 0.9.8 삭제 계약: 미리보기로 받은 묶음(digest·operation_id)을 실어 적용한다. 그 사이 바뀌었으면 서버가 거절한다.
      void writeEdge(async () => api.deleteEdge(cfg.owner, from, to, "about", await api.previewDeleteEdge(cfg.owner, from, to, "about")));
      return;
    }
    if (t.closest('[data-act="attach"]') && picked?.kind === "policy") {
      const to = (board.querySelector<HTMLSelectElement>("#am-attach-pick")?.value || "").trim();
      if (!to) return;
      const from = picked.id;
      void writeEdge(() => api.ingestFragment({ nodes: [], edges: [{ from, to, type: "about", evidence: M.attachEvidence }] }));
      return;
    }
    if (t.closest('[data-act="legacy"]')) { legacyOpen = !legacyOpen; drawAgentMap(el, data, drawLegacy); }
  });
  board.addEventListener("keydown", (e) => {
    const t = (e.target as HTMLElement).closest<HTMLElement>(".am-node");
    if (t && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); choose(t); }
  });
}
