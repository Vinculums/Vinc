/** Agent 관계 지도 — Agents 보드의 기본 면(`#home/agents`), Agent 에 절차가 있는 스페이스에서.
 *
 *  Agent(Maker·Checker)는 가운데 큰 점, 절차는 자기 Agent 둘레의 점, 정책은 위쪽의 알약이다. 점선은 정책이
 *  다스리는 절차(`about` 엣지), 아래 곡선은 Checker 의 실패가 Maker 로 돌아가는 길이다. 점을 누르면 옆 칸이
 *  그 항목을 보여 주고, 지도 아래에는 같은 항목을 목록으로 둔다(지도에서 누르기 어려울 때, 그리고 키보드로).
 *  절차는 `#home/agents/procedure/<id>` 에서 고친다. 정책 붙이기와 빼기는 이 면에서 한다(`about` 엣지 쓰기).
 *  배치와 합친 목록은 `agent-ontology.ts` 의 순수 함수가 정한다. 근거: [[vinc.canon.terminology §3.2]] */
import { api, cfg, isCommunitySpace, isWriteConflict } from "../api";
import { COPY } from "../copy";
import { esc } from "../components";
import { edgeKindOf } from "../view-prefs";
import { palette as signal, hexStr } from "../theme";
import { planColour } from "../view-tint";
import { idSlugOf, layoutMap, mapHeight, policySlug, type MapAgent, type MapPolicy, type MapProcedure, type Props } from "../agent-ontology";
import type { AgentRole } from "../agent-roster";
import { announceRolesChanged } from "../role-composer";

export interface MapData {
  agents: (MapAgent & { mission: string; props: Props; domain: string })[];
  procedures: (MapProcedure & { one_liner: string; props: Props; domain: string; rev: number })[];
  policies: (MapPolicy & { rule: Props; domain: string })[];
  legacy: AgentRole[];
}

/** 로스터의 역할마다 계약을 읽어, 절차가 있는 것을 Agent 로 본다. 절차는 노드를 하나씩 읽는다(제목·설명·props).
 *  Agent 가 하나도 없으면 null: 그 스페이스는 직함 역할 보드를 그대로 쓴다. */
let memo: { key: string; at: number; p: Promise<MapData | null> } | null = null;
/** 쓰기 뒤에 부른다: 다음 읽기가 새로 읽는다. */
export function invalidateAgentMap(): void { memo = null; }
/** Agent 화면의 색은 주제가 아니라 **Agent** 다: Agent 마다 보석색 하나, 그 Agent 의 절차는 같은 색. 보드와 LNB 가 함께 쓴다.
 *  순서는 Maker · Checker · 그 밖(이름순)이고, 주제 색(agent 주제 = 사파이어)과 겹치지 않게 호박 · 에메랄드부터 쓴다.
 *  색은 계획의 영역 색 그 자리(`planColour(보석, 0)`)라 마크 바닥을 거친다: 사이드바 Agent 점이 어느 줄 면에서도 비텍스트 바닥을 넘는다. */
/** 정책의 색(자수정). Agent 색과 겹치지 않는다. 보드 · LNB 가 함께 쓴다. */
export function policyColour(): string { const mode = document.documentElement.dataset.theme === "dark" ? "dark" : "light"; return planColour(4, 0, mode); }
export function agentColours(agents: { id: string; title: string }[]): Map<string, string> {
  const mode = document.documentElement.dataset.theme === "dark" ? "dark" : "light", pick = [3, 2, 0, 1];   // 자수정(4)은 정책 몫
  const rank = (a: { title: string }) => { const i = ["maker", "checker"].indexOf(a.title.toLowerCase()); return i < 0 ? 9 : i; };
  const order = [...agents].sort((x, y) => rank(x) - rank(y) || x.title.localeCompare(y.title));
  return new Map(order.map((a, i) => [a.id, planColour(pick[i % pick.length], 0, mode)]));
}
/** 같은 로스터로 잠깐 사이에 두 번 읽지 않는다(보드와 LNB 가 함께 부른다). 쓰기 뒤에는 로스터 판이 바뀌어 다시 읽힌다. */
export function loadAgentMap(roles: AgentRole[]): Promise<MapData | null> {
  const key = roles.map((r) => r.id).join("|");
  if (memo && memo.key === key && Date.now() - memo.at < 4000) return memo.p;
  const p = readAgentMap(roles); memo = { key, at: Date.now(), p }; p.catch(() => { memo = null; });
  return p;
}
async function readAgentMap(roles: AgentRole[]): Promise<MapData | null> {
  // 역할마다 한 번: `detail=procedures` 가 그 Agent 의 절차(제목·설명·주제·props·rev)와 정책을 한 쿼리로 싣는다.
  // 절차마다 노드를 따로 읽지 않는다(요청이 절차 수만큼 늘어난다).
  const reads = await Promise.all(roles.map((r) => api.role(cfg.owner, r.id, true).catch(() => null)));
  const agents: MapData["agents"] = [];
  const procedures: MapData["procedures"] = [];
  const policies = new Map<string, MapData["policies"][number]>();
  reads.forEach((got, i) => {
    const procs = got?.executed && Array.isArray(got.procedures) ? got.procedures : [];
    const node = got?.node || {};
    const props = (node.props || {}) as Props;
    if (!procs.length && !Array.isArray(props.procedures)) return;   // 절차 목록이 없는 역할(물러난 직함 역할 · 모델 없는 decision-gate)은 이 화면의 Agent 가 아니다
    agents.push({ id: roles[i].id, title: node.title || roles[i].title, tier: String(props.tier || ""),
                  mission: String(props.mission || node.one_liner || ""), props, domain: String(node.domain || roles[i].domain || "") });
    for (const p of procs) procedures.push({ id: p.id, agent: roles[i].id, policies: Array.isArray(p.policies) ? p.policies : [],
      title: String(p.title || p.id).replace(/ \((Maker|Checker) procedure\)$/, ""), one_liner: String(p.one_liner || ""),
      props: (p.props || {}) as Props, domain: String(p.domain || ""), rev: Number(p.rev) });
    for (const pol of Array.isArray(got.policies) ? got.policies : [])
      policies.set(pol.id, { id: pol.id, title: pol.title || policySlug(pol.id), rule: pol.rule || {}, domain: String(pol.domain || "") });
  });
  if (!agents.length) return null;
  // 역할 읽기는 절차에 붙은 정책만 싣는다. 아무 절차도 다스리지 않는 정책도 그래프에 있어야 다시 붙일 수 있으므로
  // 결정 목록에서 `decision:policy-` 중 아직 없는 것만 따로 읽는다(보통 0건). 목록을 못 읽으면 붙은 정책만으로 그린다.
  try {
    const tree: any = await api.navTree(cfg.owner, { nodes: true, kind: "decision" });
    const loose = ([] as any[]).concat(...((tree?.domains || []).map((d: any) => d.nodes || [])), tree?.unfiled || [])
      .map((n: any) => String(n?.id || "")).filter((id: string) => id.startsWith("decision:policy-") && !policies.has(id));
    const got = await Promise.all(loose.map((id: string) => api.nodeContext(cfg.owner, id, 1).catch(() => null)));
    got.forEach((n: any, i: number) => {
      if (n && n.executed !== false) policies.set(loose[i], { id: loose[i], title: String(n.title || policySlug(loose[i])),
        rule: ((n.props || {}) as Props).rule as Props || {}, domain: String(n.domain || "") });
    });
  } catch { /* 붙은 정책만으로 그린다 */ }
  const agentIds = new Set(agents.map((a) => a.id));
  return { agents, procedures, policies: [...policies.values()], legacy: roles.filter((r) => !agentIds.has(r.id)) };
}

/** 그래프에 보이는 모양: 노드·이름·주제·정책 연결. 이것이 같으면 다시 그리지 않는다. */
function shapeOf(d: MapData): string {
  return JSON.stringify([d.agents.map((a) => [a.id, a.title, a.domain]), d.procedures.map((p) => [p.id, p.title, p.agent, p.policies, p.domain]),
                         d.policies.map((p) => [p.id, p.title, p.domain, p.rule]), d.legacy.map((r) => r.id)]);
}
let shown: { el: HTMLElement; data: MapData; shape: string } | null = null;

/** 보드가 다시 읽은 자료를 들고 왔을 때. 모양이 같으면 화면은 그대로 두고 값(props·rev·설명)만 갈아 끼운다:
 *  자동 새로고침(`vinc:agents-changed`)마다 다시 그리지 않는다. 다시 그리면 WebGL 캔버스가 새로 생기고,
 *  옆 칸에서 고치던 칸도 날아간다. 그렸으면 true. */
export function updateAgentMap(el: HTMLElement, data: MapData, drawLegacy: (host: HTMLElement) => void): boolean {
  if (shown && shown.el === el && el.querySelector(".agent-map") && shown.shape === shapeOf(data)) {
    const fresh = new Map<string, any>([...data.agents, ...data.procedures].map((x) => [x.id, x]));
    for (const x of [...shown.data.agents, ...shown.data.procedures]) {
      const f = fresh.get(x.id);
      if (f) Object.assign(x, { props: f.props, ...(f.rev !== undefined ? { rev: f.rev, one_liner: f.one_liner } : { mission: f.mission }) });
    }
    return false;
  }
  drawAgentMap(el, data, drawLegacy);
  return true;
}

type Pick = { kind: "agent" | "procedure" | "policy"; id: string } | null;

/** 그래프 보기의 선 색(theme.linkRamp)과 초점색을 CSS 변수로. 테마마다 값이 다르다. */
function linkVars(): string {
  const mode = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
  const p = signal[mode];
  return `--am-link-base:${p.linkRamp.base};--am-link-cross:${p.linkRamp.cross};--am-link-fade:${p.linkRamp.fade};--am-point:${hexStr(p.focus)}`;
}
let picked: Pick = null;
let chooseHook: ((kind: "agent" | "procedure" | "policy", id: string) => void) | null = null;
/** LNB 가 고른다. 보드가 떠 있으면 그 자리에서 고르고 true, 아니면 고른 것만 기억하고 false(보드를 열면 그것이 골라진 채 그려진다). */
export function pickInAgentMap(kind: "agent" | "procedure" | "policy", id: string): boolean {
  picked = { kind, id };
  if (chooseHook && shown?.el.isConnected) { chooseHook(kind, id); return true; }
  return false;
}
export const pickedInAgentMap = (): string => picked?.id || "";
/** 지난번에 그린 지도의 실제 폭(px). 다음 배치가 이 폭에 맞춘다. */
let lastWidth = 0;
const short = (s: string) => { const v = s.replace(/^Policy:\s*/, ""); return v.length > 20 ? v.slice(0, 19) + "…" : v; };

/** 그래프 면을 그린다. `drawLegacy` 는 직함 역할이 남은 스페이스에서 옛 보드를 아래에 그린다. */
export function drawAgentMap(el: HTMLElement, data: MapData, drawLegacy: (host: HTMLElement) => void, refit = true): void {
  const M = COPY.ui.agentMap;
  shown = { el, data, shape: shapeOf(data) };
  const readOnly = isCommunitySpace(cfg.team);
  // 그린 폭에 맞춰 배치한다: 고정 viewBox 를 줄여 그리면 글자도 같이 줄어 읽기 어렵다(11px 가 9px 로).
  const W = Math.max(300, Math.round(lastWidth || 760)), H = mapHeight(data.procedures, data.agents);
  // 정책은 지도에 두지 않는다(아래 정책 칸이 맡는다). 절차의 작은 마름모가 "정책이 붙어 있다"를 말한다
  const laid = layoutMap(data.agents, data.procedures, data.policies, W, H);
  const nodes = laid.nodes.filter((n) => n.kind !== "policy"), links = laid.links.filter((l) => l.type !== "ABOUT");
  for (const n of nodes) n.r = n.kind === "agent" ? 18 : 6;   // 그리는 크기(빛살의 틈이 이 둘레에서 잰다)
  const at = new Map(nodes.map((n) => [n.id, n]));
  const proc = new Map(data.procedures.map((p) => [p.id, p]));
  const pol = new Map(data.policies.map((p) => [p.id, p]));
  const ag = new Map(data.agents.map((a) => [a.id, a]));
  if (picked && !at.has(picked.id) && !(picked.kind === "policy" && pol.has(picked.id))) picked = null;
  const domainOf = new Map<string, string>([...data.agents, ...data.procedures, ...data.policies].map((x) => [x.id, x.domain]));

  // 그래프 나침반과 같은 그림 문법: 개념은 원, Agent 는 나침반 허브처럼 고리를 두른 큰 원. 선은 틈을 두고 시작해 Agent 쪽이 굵은 빛살.
  // 색만 다르다: 이 화면은 Agent 단위로 칠한다(Agent 와 그 절차가 같은 색). 근거: [[vinc.manual.app §8]]
  const AC = agentColours(data.agents);
  const colOf = (id: string) => AC.get(id) || AC.get(proc.get(id)?.agent || "") || "hsl(var(--muted-foreground))";
  const GAP = 5;
  const ray = (a: typeof nodes[number], b: typeof nodes[number], i: number, w0: number, w1: number) => {
    const dx = b.x - a.x, dy = b.y - a.y, L = Math.hypot(dx, dy) || 1, ux = dx / L, uy = dy / L, nx = -uy, ny = ux;
    const r0 = a.r + GAP, r1 = b.r + GAP; if (L <= r0 + r1 + 2) return { def: "", d: "" };
    const ax = a.x + ux * r0, ay = a.y + uy * r0, bx = b.x - ux * r1, by = b.y - uy * r1, f = (v: number) => v.toFixed(1);
    const d = `M${f(ax + nx * w0)} ${f(ay + ny * w0)}L${f(bx + nx * w1)} ${f(by + ny * w1)}L${f(bx - nx * w1)} ${f(by - ny * w1)}L${f(ax - nx * w0)} ${f(ay - ny * w0)}Z`;
    const def = `<linearGradient id="amg${i}" gradientUnits="userSpaceOnUse" x1="${f(ax)}" y1="${f(ay)}" x2="${f(bx)}" y2="${f(by)}"><stop offset="0" stop-color="${colOf(a.id)}"/><stop offset="1" stop-color="${colOf(b.id)}" stop-opacity=".75"/></linearGradient>`;
    return { def, d }; };
  let svg = "";
  let lines = "", defs = "";
  links.forEach((l, i) => {
    let a = at.get(l.from)!, b = at.get(l.to)!;
    if (b.kind === "agent" && a.kind !== "agent") [a, b] = [b, a];   // 굵은 쪽이 Agent 쪽
    const cross = (domainOf.get(l.from) || "") !== (domainOf.get(l.to) || "");
    const kind = edgeKindOf({ type: l.type, cross_domain: cross });
    const both = a.kind === "agent" && b.kind === "agent", w0 = both ? 1.6 : a.kind === "agent" ? 2.6 : 1.3, w1 = both ? 1.6 : .5;
    const r = ray(a, b, i, w0, w1); if (!r.d) return; defs += r.def;
    const cls = ["am-l", kind === "cross" ? "am-cross" : ""].filter(Boolean).join(" ");
    lines += `<path class="${cls}" data-a="${esc(l.from)}" data-b="${esc(l.to)}" d="${r.d}" fill="url(#amg${i})"/>`;
  });
  const diamond = (x: number, y: number, r: number) => { const d = r * 1.25; return `M${x} ${y - d}L${x + d} ${y}L${x} ${y + d}L${x - d} ${y}Z`; };
  for (const n of nodes) {
    const sel = picked?.id === n.id ? " is-sel" : "", c = colOf(n.id);
    const label = `${n.kind === "agent" ? M.agent : n.kind === "policy" ? M.policy : M.procedure}: ${n.label}`;
    {
      const r = n.kind === "agent" ? 18 : 6, dx = n.anchor === "end" ? -(r + 7) : n.anchor === "start" ? r + 7 : 0;
      const dy = n.kind === "agent" ? r + 20 : 4;
      svg += `<g class="am-node am-${n.kind}${sel}" data-pick="${n.kind}" data-id="${esc(n.id)}" tabindex="0" role="button" aria-label="${esc(label)}" style="--c:${c}">
        ${n.kind === "agent" ? `<circle class="am-halo" cx="${n.x}" cy="${n.y}" r="${r + 6}"/>` : ""}<circle class="am-ring" cx="${n.x}" cy="${n.y}" r="${r + 4}"/><circle class="am-shape" cx="${n.x}" cy="${n.y}" r="${r}"/>
        ${n.kind === "procedure" && (proc.get(n.id)?.policies.length || 0) ? `<path class="am-mark" style="fill:${policyColour()}" d="${diamond(n.x + r + 3, n.y - r - 3, 2.6)}"/>` : ""}
        <title>${esc(n.label)}</title><text x="${n.x + dx}" y="${n.y + dy}" text-anchor="${n.anchor}">${esc(short(n.label))}</text></g>`;
    }
  }

  const y0 = Math.max(0, Math.min(...nodes.map((n) => n.y)) - 36);   // 위쪽 빈 띠(정책 줄 자리였다)를 잘라 낸다
  // 정책 칸: 정책마다 카드 하나. 제목 · 막는 도구 · 막는 권한을 고치고, 다스리는 절차를 붙이고 뗀다
  const polCard = (q: MapData["policies"][number]) => {
    const governed = data.procedures.filter((p) => p.policies.includes(q.id)), others = data.procedures.filter((p) => !p.policies.includes(q.id));
    const rule = q.rule as any, tools: string[] = rule.disallowed_tools || [], deny: string[] = (rule.capabilities || {}).deny || [], on = picked?.kind === "policy" && picked.id === q.id;
    const chip = (p: MapData["procedures"][number]) => `<span class="am-chip am-gov" style="--c:${colOf(p.id)}"><span class="am-gdot" aria-hidden="true"></span><button type="button" class="am-link" data-pick="procedure" data-id="${esc(p.id)}">${esc(p.title)}</button>${
      readOnly ? "" : `<button type="button" class="am-x" data-detach="${esc(p.id)}" aria-label="${esc(M.detach)}">✕</button>`}</span>`;
    return `<article class="am-pol-card${on ? " is-sel" : ""}" data-pol="${esc(q.id)}" style="--pc:${policyColour()}">
      <div class="pe-field"><label for="amp-t-${esc(q.id)}">${esc(M.polTitle)}</label><span class="am-pol-row"><span class="am-pol-mark" aria-hidden="true"></span><input id="amp-t-${esc(q.id)}" class="am-pol-title" value="${esc(q.title)}"${readOnly ? " readonly" : ""}></span></div>
      <div class="am-pol-grid">
        <div class="pe-field"><label for="amp-a-${esc(q.id)}">${esc(M.polTools)}</label><textarea id="amp-a-${esc(q.id)}" name="tools" rows="3"${readOnly ? " readonly" : ""}>${esc(tools.join("\n"))}</textarea></div>
        <div class="pe-field"><label for="amp-b-${esc(q.id)}">${esc(M.polDeny)}</label><textarea id="amp-b-${esc(q.id)}" name="deny" rows="3"${readOnly ? " readonly" : ""}>${esc(deny.join("\n"))}</textarea></div>
      </div>
      <p class="am-pol-sub">${esc(M.governs(governed.length))}</p><p class="am-chips">${governed.map(chip).join("") || `<span class="hint">${esc(M.polNone)}</span>`}</p>
      ${readOnly ? "" : `<div class="am-pol-acts">${others.length ? `<select class="am-attach-pick" aria-label="${esc(M.attachPick)}"><option value="">${esc(M.attachPick)}</option>${
        others.map((p) => `<option value="${esc(p.id)}">${esc(p.title)}</option>`).join("")}</select><button type="button" class="btn" data-act="attach">${esc(M.attach)}</button>` : ""}
        <span class="am-pol-status pe-status" role="status"></span><button type="button" class="btn btn-primary" data-act="pol-save">${esc(M.polSave)}</button></div>`}
    </article>`; };
  el.innerHTML = `<div class="home agents agents-board agent-map">
    <header class="home-head"><h1>${esc(M.title)}</h1></header>
    ${readOnly ? "" : `<div class="am-adds"><button type="button" class="btn" data-act="add-agent"><span aria-hidden="true">+</span><span>${esc(M.addAgent)}</span></button>
      <button type="button" class="btn" data-act="add-proc"${data.agents.length ? "" : " disabled"}><span aria-hidden="true">+</span><span>${esc(M.addProcedure)}</span></button>
      <form class="am-addform" hidden></form></div>`}
    <p class="agents-status" role="status"></p>
    <div class="am-stage"><div class="am-main">
      <div class="am-canvas-wrap" style="${linkVars()}"><svg class="am-svg" viewBox="0 ${y0} ${W} ${H - y0}" role="group" aria-label="${esc(M.mapLabel)}"><defs>${defs}</defs><g class="am-lines">${lines}</g>${svg}</svg></div>
      <section class="am-policies"><div class="am-pol-head"><h2>${esc(M.policiesTitle)}</h2>${readOnly ? "" :
        `<button type="button" class="rail-x am-pol-add" data-act="add-pol" aria-label="${esc(M.addPolicy)}" data-tip="${esc(M.addPolicy)}">+</button><form class="am-addform am-poladd" hidden></form>`}</div>
        <div class="am-pol-list">${data.policies.map(polCard).join("") || `<p class="hint">${esc(M.polEmpty)}</p>`}</div></section>
    </div>
      <aside class="am-side"></aside>
    </div>
  </div>`;
  const drawn = el.querySelector<SVGSVGElement>(".am-svg")?.getBoundingClientRect().width || 0;
  // 그린 폭에 맞춰 한 번만 다시 배치한다(좁은 창에서 최소 폭보다 좁으면 매번 어긋나 끝없이 다시 그렸다)
  if (refit && drawn && Math.abs(drawn - W) > 24) { lastWidth = drawn; drawAgentMap(el, data, drawLegacy, false); return; }
  function side(): string {
    if (!picked) return `<p class="hint">${esc(M.pick)}</p>`;
    if (picked.kind === "agent") {
      // Agent 도 절차와 같은 편집 칸으로 이 자리에서 고친다(fillSide 가 붙인다)
      const a = ag.get(picked.id)!;
      return `<p class="am-kind">${esc(M.agent)}</p><h2>${esc(a.title)}</h2><div class="am-proc-edit"></div>`;
    }
    if (picked.kind === "procedure") {
      const p = proc.get(picked.id)!, a = ag.get(p.agent);
      return `<p class="am-kind">${esc(M.procedure)} · ${esc(a?.title || "")}</p><h2>${esc(p.title)}</h2>
        <h3>${esc(M.governedBy)}</h3>${p.policies.length ? `<p class="am-chips">${p.policies.map((id) =>
          `<button type="button" class="am-chip am-chip-pol" data-pick="policy" data-id="${esc(id)}">${esc(pol.get(id)?.title || policySlug(id))}</button>`).join("")}</p>`
          : `<p class="hint">${esc(M.noPolicy)}</p>`}
        <div class="am-proc-edit"></div>`;
    }
    return `<p class="hint">${esc(M.pick)}</p>`;   // 정책은 아래 정책 칸에서 다룬다
  }

  const board = el.querySelector<HTMLElement>(".agent-map")!;
  /** 짚은 노드(없으면 고른 노드)에 닿은 선을 앞으로, 나머지는 뒤로. 끈은 닿을 때만 보인다. */
  let hoverId: string | null = null;
  const emphasize = () => {
    const id = hoverId ?? picked?.id ?? null;
    const wrap = board.querySelector(".am-canvas-wrap");
    wrap?.classList.toggle("has-focus", !!id);
    wrap?.querySelectorAll<SVGPathElement>(".am-l").forEach((ln) =>
      ln.classList.toggle("is-near", !!id && (ln.dataset.a === id || ln.dataset.b === id)));
  };
  board.addEventListener("mouseover", (e) => {
    const n = (e.target as Element).closest<HTMLElement>(".am-node");
    const id = n?.dataset.id ?? null;
    if (id !== hoverId) { hoverId = id; emphasize(); }
  });
  board.addEventListener("mouseleave", () => { hoverId = null; emphasize(); });
  /** 옆 칸을 채운다. 절차면 그 자리에 편집 칸을 붙인다(다른 면으로 가지 않는다). */
  const fillSide = () => {
    const box = board.querySelector<HTMLElement>(".am-side")!;
    box.innerHTML = side();
    const host = box.querySelector<HTMLElement>(".am-proc-edit");
    if (!host || !picked) return;
    if (picked.kind === "agent") {
      const a = ag.get(picked.id)!;
      // 역할 목록 읽기에는 판(rev)이 없다: 고칠 칸을 열 때 이 Agent 하나를 읽어 지금 판과 값을 쓴다
      void Promise.all([import("../procedure-panel"), api.nodeContext(cfg.owner, a.id, 1).catch(() => null)]).then(([{ mountProcedurePanel }, cur]: [any, any]) => {
        if (!host.isConnected) return;
        if (cur?.executed) { a.props = (cur.props || {}) as Props; a.mission = String(a.props.mission || ""); }
        mountProcedurePanel(host, { kind: "agent", id: a.id, title: a.title, oneLiner: a.mission, props: a.props, rev: Number(cur?.rev ?? 0),
          agentTitle: a.title, agentProps: a.props, policies: [], policyNames: new Map(), readOnly,
          onSaved: (fresh: { props: Props; title: string; rev: number }) => { const t0 = a.title; a.props = fresh.props; a.mission = String(fresh.props.mission || ""); (a as any).rev = fresh.rev; a.title = fresh.title;
            if (t0 !== fresh.title) { invalidateAgentMap(); announceRolesChanged(); } } });
      });
      return;
    }
    if (picked.kind !== "procedure") return;
    const p = proc.get(picked.id)!, a = ag.get(p.agent);
    void import("../procedure-panel").then(({ mountProcedurePanel }) => {
      if (!host.isConnected) return;
      mountProcedurePanel(host, {
        id: p.id, title: p.title, oneLiner: p.one_liner, props: p.props, rev: p.rev,
        agentTitle: a?.title || "", agentProps: a?.props || {},
        policies: p.policies.map((id) => ({ id, rule: pol.get(id)?.rule || {} })),
        policyNames: new Map(data.policies.map((x) => [x.id, x.title])), readOnly,
        onSaved: (fresh) => { const t0 = p.title; p.props = fresh.props; p.one_liner = fresh.oneLiner; p.rev = fresh.rev; p.title = fresh.title;
          if (t0 !== fresh.title) { invalidateAgentMap(); announceRolesChanged(); } },
      });
    });
  };
  fillSide();
  emphasize();
  const status = (s: string) => { board.querySelector(".agents-status")!.textContent = s; };
  // 고르기는 다시 그리지 않는다: 선택 표시와 옆 칸만 바꾸고, 3D 몸통은 같은 캔버스에서 상태만 바꾼다.
  // 전체를 다시 그리면 WebGL 캔버스가 새로 생겨 첫 장면까지 비어 보인다(누를 때마다 깜빡였다).
  // 정책 고르기: 아래 정책 칸의 그 카드를 켜고 보이게 하며, 지도에서는 그 정책이 다스리는 절차가 진해진다
  const focusPolicy = (id: string) => {
    board.querySelectorAll<HTMLElement>(".am-pol-card").forEach((c) => c.classList.toggle("is-sel", c.dataset.pol === id));
    const card = board.querySelector<HTMLElement>(`.am-pol-card[data-pol="${CSS.escape(id)}"]`); card?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    const gov = new Set(data.procedures.filter((p) => p.policies.includes(id)).map((p) => p.id));
    board.querySelectorAll<HTMLElement>(".am-node").forEach((x) => { x.classList.toggle("is-sel", false); x.classList.toggle("is-gov", gov.has(x.dataset.id || "")); });
    board.querySelector(".am-canvas-wrap")?.classList.toggle("has-gov", gov.size > 0);
  };
  const choose = (t: HTMLElement) => {
    picked = { kind: t.dataset.pick as "agent" | "procedure" | "policy", id: t.dataset.id! };
    window.dispatchEvent(new CustomEvent("vinc:agent-pick", { detail: picked }));   // LNB 가 그 줄을 켠다
    if (picked.kind === "policy") { focusPolicy(picked.id); return; }
    board.querySelectorAll<HTMLElement>(".am-pol-card.is-sel").forEach((c) => c.classList.remove("is-sel"));
    board.querySelectorAll<HTMLElement>(".am-node.is-gov").forEach((x) => x.classList.remove("is-gov")); board.querySelector(".am-canvas-wrap")?.classList.remove("has-gov");
    board.querySelectorAll<HTMLElement>(".am-node, .am-row").forEach((x) => x.classList.toggle("is-sel", x.dataset.id === picked!.id));
    fillSide();
    emphasize();
    if (t.closest(".am-node")) t.focus({ preventScroll: true });
  };
  const writeEdge = async (fn: () => Promise<any>) => {
    status(M.saving);
    let ok = false;
    try { const r = await fn(); ok = r?.executed !== false && !(r?.warnings || []).length; } catch { ok = false; }
    status(ok ? "" : M.saveFail);
    if (ok) { invalidateAgentMap(); announceRolesChanged(); }
  };
  // 만들기 쓰기는 노드가 실제로 들어갔는지로 판정한다(경고는 안내일 수 있다: 막 만든 정책은 아직 붙은 절차가 없어 'about 엣지 없음'을 알린다)
  const wrote = (r: any) => r?.executed !== false && ((r?.merged?.nodes?.created || 0) + (r?.merged?.nodes?.matched || 0)) > 0;
  const freshId = (prefix: string, title: string, taken: Set<string>) => { const base = idSlugOf(title) || Date.now().toString(36); let id = prefix + base, k = 2;
    while (taken.has(id)) id = `${prefix}${base}-${k++}`; return id; };
  const known = new Set([...data.agents, ...data.procedures, ...data.policies].map((x) => x.id));
  function openAdd(kind: string) {
    const f = board.querySelector<HTMLFormElement>(kind === "add-pol" ? ".am-poladd" : ".am-adds .am-addform"); if (!f) return;
    const def = picked?.kind === "agent" ? picked.id : picked?.kind === "procedure" ? proc.get(picked.id)?.agent || "" : "";
    f.dataset.kind = kind; f.hidden = false;
    f.innerHTML = `<input name="name" placeholder="${esc(M.newName)}" aria-label="${esc(M.newName)}" required>`
      + (kind === "add-proc" ? `<select name="agent" aria-label="${esc(M.forAgent)}">${data.agents.map((a) => `<option value="${esc(a.id)}"${a.id === def ? " selected" : ""}>${esc(a.title)}</option>`).join("")}</select>` : "")
      + `<button type="submit" class="btn btn-primary">${esc(M.create)}</button><button type="button" class="btn" data-act="add-cancel">${esc(M.cancel)}</button>`;
    f.querySelector<HTMLInputElement>("input")?.focus();
  }
  board.addEventListener("submit", (e) => {
    const f = (e.target as HTMLElement).closest<HTMLFormElement>(".am-addform"); if (!f) return; e.preventDefault();
    const name = (f.querySelector<HTMLInputElement>('[name="name"]')?.value || "").trim(); if (!name) return;
    const kind = f.dataset.kind;
    void (async () => {
      status(M.saving);
      try {
        if (kind === "add-agent") {
          // Agent = 절차 목록을 가진 역할(concept:agent-*). 처음엔 절차가 없다
          const id = freshId("concept:agent-", name, known), dom = data.agents[0]?.domain || "vinc/agent";
          // 새 Agent 는 곧바로 로스터에 든다: 실행 계약이 막지 않게 필수 검사 셋과 빈 목록을 처음부터 싣는다(이름 · 하는 일은 옆 칸에서 채운다)
          const seed = { procedures: [], mission: "", tier: "execute", never: [], person_only: [],
            gates: [{ id: "person_only", check: { type: "person_only" }, on_fail: "interrupt" }, { id: "side_effect_evidence", check: { type: "side_effect_evidence" }, on_fail: "block" },
              { id: "handoff_manifest", check: { type: "handoff_manifest" }, on_fail: "block" }] };
          const r: any = await api.ingestFragment({ owner: cfg.owner, create_only: true, nodes: [{ id, type: "concept", title: name, domain: dom, props: seed }], edges: [] });
          if (isWriteConflict(r)) throw new Error("conflict");
          if (!wrote(r)) throw new Error("write");
          picked = { kind: "agent", id };
        } else if (kind === "add-proc") {
          // 절차를 만들고 그 Agent 의 절차 목록 끝에 붙인다(목록은 통째로 보낸다: 배열은 합쳐지지 않는다)
          const agentId = f.querySelector<HTMLSelectElement>('[name="agent"]')?.value || data.agents[0].id, a = ag.get(agentId)!;
          const id = freshId("concept:procedure-", name, known);
          const r: any = await api.ingestFragment({ owner: cfg.owner, create_only: true, nodes: [{ id, type: "concept", title: name, domain: a.domain || "vinc/agent", props: { kind: "procedure", agent: agentId } }], edges: [] });
          if (isWriteConflict(r)) throw new Error("conflict");
          if (!wrote(r)) throw new Error("write");
          const fresh: any = await api.nodeContext(cfg.owner, agentId, 1);
          const list = Array.isArray(fresh?.props?.procedures) ? fresh.props.procedures : [];
          const r2: any = await api.editNode({ id: agentId, type: "concept", props_patch: { procedures: [...list, id] } }, Number(fresh.rev));
          if (!wrote(r2)) throw new Error("write");
          picked = { kind: "procedure", id };
        } else {
          // 정책 = decision:policy-*. 처음엔 막는 것도 다스리는 절차도 없다(카드에서 채운다)
          const id = freshId("decision:policy-", name, known), dom = data.policies[0]?.domain || data.agents[0]?.domain || "vinc/agent";
          const r: any = await api.ingestFragment({ owner: cfg.owner, create_only: true, nodes: [{ id, type: "decision", title: name, domain: dom, props: { rule: { disallowed_tools: [], capabilities: { deny: [] } } } }], edges: [] });
          if (isWriteConflict(r)) throw new Error("conflict");
          if (!wrote(r)) throw new Error("write");
          picked = { kind: "policy", id };
        }
        status(M.created); invalidateAgentMap(); announceRolesChanged();
      } catch (e) { status(/exists|conflict|409/i.test(String(e)) ? M.nameTaken : M.saveFail); }
    })();
  });
  const clearPick = () => { picked = null; window.dispatchEvent(new CustomEvent("vinc:agent-pick", { detail: null }));
    board.querySelectorAll<HTMLElement>(".is-sel").forEach((x) => x.classList.remove("is-sel")); board.querySelectorAll<HTMLElement>(".am-node.is-gov").forEach((x) => x.classList.remove("is-gov"));
    board.querySelector(".am-canvas-wrap")?.classList.remove("has-gov"); fillSide(); emphasize(); };
  board.addEventListener("click", (e) => {
    const t = e.target as HTMLElement;
    const pick = t.closest<HTMLElement>("[data-pick]");
    if (pick) { choose(pick); return; }
    // 지도의 빈 곳을 누르면 고른 것을 푼다(정책을 고를 때 생긴 흐림도 함께)
    if (t.closest(".am-canvas-wrap") && !t.closest(".am-node")) { clearPick(); return; }
    // 정책 카드의 빈 곳을 누르면 그 정책을 고른다(이미 골랐으면 푼다)
    const cardHit = t.closest<HTMLElement>(".am-pol-card");
    if (cardHit && !t.closest("input, textarea, select, button, label")) {
      if (picked?.kind === "policy" && picked.id === cardHit.dataset.pol) clearPick();
      else { picked = { kind: "policy", id: cardHit.dataset.pol! }; window.dispatchEvent(new CustomEvent("vinc:agent-pick", { detail: picked })); focusPolicy(picked.id); }
      return; }
    const card = t.closest<HTMLElement>("[data-pol]"), polId = card?.dataset.pol || "";
    const det = t.closest<HTMLElement>("[data-detach]");
    if (det && polId) {
      const to = det.dataset.detach!;
      // 0.9.8 삭제 계약: 미리보기로 받은 묶음(digest·operation_id)을 실어 적용한다. 그 사이 바뀌었으면 서버가 거절한다.
      void writeEdge(async () => api.deleteEdge(cfg.owner, polId, to, "about", await api.previewDeleteEdge(cfg.owner, polId, to, "about")));
      return;
    }
    if (t.closest('[data-act="attach"]') && card) {
      const to = (card.querySelector<HTMLSelectElement>(".am-attach-pick")?.value || "").trim();
      if (!to) return;
      void writeEdge(() => api.ingestFragment({ owner: cfg.owner, nodes: [], edges: [{ from: polId, to, type: "about", evidence: M.attachEvidence }] }));
      return;
    }
    // 추가(Agent · 절차 · 정책): 그 자리에 작은 입력 줄을 연다. 만들면 새 노드가 골라진 채 다시 그려진다
    const addAct = t.closest<HTMLElement>('[data-act="add-agent"], [data-act="add-proc"], [data-act="add-pol"]');
    if (addAct && !readOnly) { openAdd(addAct.dataset.act!); return; }
    if (t.closest('[data-act="add-cancel"]')) { board.querySelectorAll<HTMLFormElement>(".am-addform").forEach((f) => { f.hidden = true; f.innerHTML = ""; }); return; }
    // 정책 저장: 지금 판(rev)을 읽어 바뀐 것만 보낸다. rule 의 다른 키는 그대로 두고 막는 도구 · 막는 권한만 바꾼다
    if (t.closest('[data-act="pol-save"]') && card) {
      const st = card.querySelector<HTMLElement>(".am-pol-status")!, lines = (name: string) => (card.querySelector<HTMLTextAreaElement>(`textarea[name="${name}"]`)?.value || "")
        .split("\n").map((x) => x.trim()).filter(Boolean);
      const title = (card.querySelector<HTMLInputElement>(".am-pol-title")?.value || "").trim();
      void (async () => {
        st.textContent = M.saving;
        let fresh: any = null; try { fresh = await api.nodeContext(cfg.owner, polId, 1); } catch { fresh = null; }
        if (!fresh?.executed) { st.textContent = M.saveFail; return; }
        const rule0 = ((fresh.props || {}) as any).rule || {}, rule = { ...rule0, disallowed_tools: lines("tools"), capabilities: { ...(rule0.capabilities || {}), deny: lines("deny") } };
        const node: any = { id: polId, type: "decision", props_patch: { rule } };
        if (title && title !== String(fresh.title || "")) node.title = title;
        let res: any; try { res = await api.editNode(node, Number(fresh.rev)); } catch (e) { res = { executed: false, error: String(e) }; }
        if (/rev|revision|conflict/i.test(String(res?.error || ""))) { st.textContent = M.polStale; return; }
        if (res?.executed === false || (res?.warnings || []).length) { st.textContent = M.saveFail; return; }
        st.textContent = M.polSaved; invalidateAgentMap(); announceRolesChanged();
      })();
      return;
    }
  });
  chooseHook = (kind, id) => { if (kind === "policy") { picked = { kind, id }; focusPolicy(id); return; }
    const t = board.querySelector<HTMLElement>(`.am-node[data-pick="${kind}"][data-id="${CSS.escape(id)}"]`);
    if (t) choose(t); else { picked = { kind, id }; fillSide(); emphasize(); } };
  if (picked?.kind === "policy") focusPolicy(picked.id);
  board.addEventListener("keydown", (e) => {
    const t = (e.target as HTMLElement).closest<HTMLElement>(".am-node");
    if (t && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); choose(t); }
  });
}
