/** 나침반 렌즈의 가운데 요약(LNB 칸 맨 위)과 엿보기 카드를 HTML 로 짓는다. 요약은 지금 가운데의 종류 · 주제 · 날짜 · 유효 기간과
 *  대체 · 관계 종류별 수 · 많이 이어진 이웃 · 고정까지의 길이고, 방향 있는 관계를 따라가는 사슬과 두 노드 사이 가장 짧은 길도
 *  여기서 센다. 모델과 받은 상태만 읽는다. 요약을 누른 뒤의 일(상태를 바꾸고 다시 그리기)은 마운트(view-compass.ts)가 받는다. */
import type { COPY } from "../copy";
import { esc } from "../components";
import { topicRing } from "../view-tint";
import * as LS from "./view-compass-state";
import type { LensState } from "./view-compass-state";
import { isMean, when, type CompassModel, type LNode } from "./view-compass-model";
import { shape } from "./view-compass-shapes";

/** 요약이 마운트에서 받는 손잡이: 문구 · 종류 이름, 이름표와 줄이기, 주제 색(그 순간의 테마), 열린 길, 자세히를 열 수 있는가 */
export interface SideLook { C: Pick<typeof COPY.ui.compass, 'sum' | 'rel'>; KIND: Record<string, string>; titleOf: (n: LNode) => string;
  short: (s: string, n: number) => string; colv: (n: LNode) => string; pathOf: (s: LensState) => LNode[]; ctx: { onOpen?: (id: string) => void } }

/** 요약과 엿보기를 짓는 함수들을 돌려준다. 요약(drawSum)은 받은 칸(sum)에 쓰고, 나머지는 값만 돌려준다 */
export function compassSide(M: CompassModel, sum: Element | null, look: SideLook) {
  const { ATTACH, ROOT, nodes, adj, topics, byAny, chainTo } = M, { C, KIND, titleOf, short, colv, pathOf, ctx } = look;
  // ── 가운데 요약: 사이드바 맨 위에 지금 가운데의 종류 · 주제 · 날짜 · 유효 기간과 대체 · 관계 종류별 수 · 많이 이어진 이웃.
  // 방향 있는 관계(선행 · 파생 · 상속 · 대체 · 되돌림)는 줄을 누르면 그 관계를 따라 사슬을 곧게 연다(갈래가 갈리면 그 자리에서 멈춘다)
  const SC = () => C.sum;
  const RELT: [string, 'pre' | 'der' | 'inh' | 'sup' | 'ret'][] = [['PREREQUISITE_OF', 'pre'], ['DERIVES_FROM', 'der'], ['INHERITS_FROM', 'inh'], ['SUPERSEDES', 'sup'], ['RETURNS_TO', 'ret']];
  const relOf = (n: LNode) => { const out = new Map<string, LNode[]>(), rel: LNode[] = [], att: Record<string, number> = { decision: 0, record: 0, episode: 0, doc: 0 };
    for (const e of adj.get(n.id) || []) { const o = e.a === n ? e.b : e.a; if (!o || o === n) continue;
      if (ATTACH.has(e.t)) { if (e.b === n && att[o.k] != null) att[o.k]++; else if (e.a === n) rel.push(o); continue; }
      const r = RELT.find(([t]) => t === e.t); if (!r) { if (e.t === 'RELATED_TO' || e.t === 'CONNECTS') rel.push(o); continue; }
      const k = `${r[1]}:${e.a === n ? 'out' : 'in'}`; if (!out.has(k)) out.set(k, []); if (!out.get(k)!.includes(o)) out.get(k)!.push(o); }
    return { out, rel: [...new Set(rel)], att }; };
  // 따라가기: 같은 종류 · 같은 방향의 이웃이 하나뿐인 동안 잇는다. 갈래(둘 이상) · 되돌아옴 · 12칸이면 멈춘다
  function chainFrom(n: LNode, type: string, dir: string, first: LNode | null | undefined) { const out: LNode[] = [], seen = new Set([n.id]); let cur: LNode | null | undefined = first;
    while (cur && !seen.has(cur.id) && out.length < 12) { out.push(cur); seen.add(cur.id);
      const nx = (adj.get(cur.id) || []).filter((e) => e.t === type && (dir === 'out' ? e.a === cur : e.b === cur)).map((e) => (dir === 'out' ? e.b : e.a));
      cur = nx.length === 1 ? nx[0] : null; }
    return out; }
  // 두 노드 사이 가장 짧은 길(관계의 방향은 보지 않는다). 주제는 이음에 없으므로 노드끼리만 걷는다. 10칸 넘게 걸리면 없는 것으로 본다
  function shortest(a: LNode, b: LNode, max = 10) { const prev = new Map<string, LNode | null>([[a.id, null]]), dist = new Map([[a.id, 0]]), q: LNode[] = [a];
    while (q.length) { const x = q.shift()!; if (x === b) break; if (dist.get(x.id)! >= max) continue;
      for (const e of adj.get(x.id) || []) { const y = e.a === x ? e.b : e.a; if (!y || prev.has(y.id)) continue; prev.set(y.id, x); dist.set(y.id, dist.get(x.id)! + 1); q.push(y); } }
    if (!prev.has(b.id)) return null; const out: LNode[] = []; for (let x: LNode | null | undefined = b; x; x = prev.get(x.id)) out.unshift(x); return out; }
  // 엿보기 카드: 관계 종류별 수(나침반 선의 표) · 관련 · 붙은 것 · 많이 이어진 이웃 넷. 주제면 종류별 수와 하위 주제 수
  function peekHTML(n: LNode | null | undefined) { if (!n) return ''; const S = SC(); let h = '<div class="peek">';
    if (n.k === 'topic') { const c: Record<string, number> = {}; for (const m of nodes) if (m.d === n.path || (n.path && m.d.startsWith(n.path + '/'))) c[m.k] = (c[m.k] || 0) + 1;
      h += `<div class="pr">${Object.entries(c).map(([k, v]) => `${esc(KIND[k] || k)} ${v}`).join(' · ')}${n.sub && n.sub.size ? ' · ' + esc(S.subtopics(n.sub.size)) : ''}</div>`; }
    else { const R = relOf(n), G = { pre: '›', der: '∘›', inh: '|›', sup: '⇢', ret: '↩' }, parts: string[] = [];
      for (const [, key] of RELT) { const k = (R.out.get(`${key}:in`) || []).length + (R.out.get(`${key}:out`) || []).length; if (k) parts.push(`${G[key]} ${k}`); }
      if (R.rel.length) parts.push(S.related(R.rel.length)); const at = Object.values(R.att).reduce((x, y) => x + y, 0); if (at) parts.push(`${S.attached} ${at}`);
      if (parts.length) h += `<div class="pr">${parts.map(esc).join(' · ')}</div>`;
      const nb4 = [...new Set([...(adj.get(n.id) || [])].map((e) => (e.a === n ? e.b : e.a)).filter((o) => o && isMean(o)))].sort((a, b) => (b.g || 0) - (a.g || 0)).slice(0, 4);
      for (const o of nb4) h += `<span class="pn">${esc(short(titleOf(o), 40))}</span>`; }
    return h + '</div>'; }
  const ico = (n: LNode) => `<svg viewBox="-7 -7 14 14">${n.k === 'topic' ? topicRing(5, colv(n), 1.8) : shape(n.k, 0, 0, 5, colv(n), !!n.vu)}</svg>`;
  const nb = (o: LNode, extra = '') => `<button class="sn" data-go="${esc(o.id)}" title="${esc(o.t || o.path || o.id)}" ${extra}>${ico(o)}<span>${esc(short(titleOf(o), 34))}</span></button>`;
  // 요약의 가운데: 층 보기는 지금 가운데, 전체 보기는 고른 노드(없으면 뿌리). 요약에서 이어 여는 길은 전체 보기면 그 노드의 주제 사슬이다
  const sumNode = (s: LensState) => (s.whole.on ? byAny(s.whole.sel) || topics.get(ROOT)! : byAny(LS.centreOf(s)));
  const sumPath = (s: LensState) => (s.whole.on ? chainTo(sumNode(s)!) : pathOf(s));
  function drawSum(s: LensState) {
    const n = sumNode(s); if (!sum || !n) return; const S = SC(); let h = '';
    h += `<div class="sh">${ico(n)}<b>${esc(short(titleOf(n), 60))}</b></div>`;
    if (n.k === 'topic') {
      const ns = nodes.filter((m) => m.d === n.path || (n.path && m.d.startsWith(n.path + '/')) || (!n.path)), c: Record<string, number> = {}; for (const m of ns) c[m.k] = (c[m.k] || 0) + 1;
      const last = ns.map(when).filter(Boolean).sort().pop();
      h += `<div class="sk">${esc(C.rel.topic)}${n.path ? ' · ' + esc(n.path) : ''}${last ? ' · ' + esc(S.latest(last)) : ''}</div>`;
      h += `<div class="sr">${Object.entries(c).map(([k, v]) => `${esc(KIND[k] || k)} ${v}`).join(' · ') || esc(S.empty)}${n.sub && n.sub.size ? ` · ${esc(S.subtopics(n.sub.size))}` : ''}</div>`;
      const top = ns.filter(isMean).sort((a, b) => (b.g || 0) - (a.g || 0)).slice(0, 5);
      if (top.length) h += `<div class="sl">${esc(S.mostLinked)}</div>${top.map((o) => nb(o)).join('')}`;
    } else {
      const d = when(n);
      h += `<div class="sk">${esc(KIND[n.k] || n.k)}${n.d ? ' · ' + esc(n.d) : ''}${d ? ' · ' + esc(d) : ''}</div>`;
      const R = relOf(n), sup = R.out.get('sup:in') || [];
      if (n.vf || n.vu) h += `<div class="sv${n.vu ? ' closed' : ''}">${esc(n.vu ? S.held(n.vf || '', String(n.vu).slice(0, 10)) : S.holds(n.vf))}</div>`;
      if (sup.length) h += `<div class="sv closed">${esc(S.replacedBy)} ${sup.map((o) => nb(o)).join('')}</div>`;
      for (const [type, key] of RELT) for (const dir of ['in', 'out'] as const) { const l = R.out.get(`${key}:${dir}`); if (!l || !l.length || (type === 'SUPERSEDES' && dir === 'in')) continue;
        const id = `${key}:${dir}`, open = s.sumOpen.includes(id);
        h += `<div class="sf"><button class="sfr" data-follow="${type}|${dir}" title="${esc(S.followTip)}"><i class="sg sg-${key}"></i><span>${esc(S.rel[key][dir])}</span><b>${l.length}</b><em>›</em></button>`;
        if (l.length > 1) h += `<button class="sfx" data-sx="${id}" aria-expanded="${open}">${open ? '▾' : '▸'}</button>`;
        h += `</div>`;
        if (open || l.length === 1) h += `<div class="sfl">${l.slice(0, 12).map((o) => nb(o, `data-chain="${type}|${dir}"`)).join('')}</div>`; }
      if (R.rel.length) h += `<div class="sl">${esc(S.related(R.rel.length))}</div>${R.rel.sort((a, b) => (b.g || 0) - (a.g || 0)).slice(0, 5).map((o) => nb(o)).join('')}`;
      const at = Object.entries(R.att).filter(([, v]) => v); if (at.length) h += `<div class="sr">${esc(S.attached)} ${at.map(([k, v]) => `${esc(KIND[k] || k)} ${v}`).join(' · ')}</div>`;
    }
    const pinsTo = (s.pins.filter((id) => id !== n.id).map(byAny).filter(Boolean) as LNode[]).slice(0, 3);
    if (pinsTo.length) { h += `<div class="sl">${esc(S.pathTo)}</div>` + pinsTo.map((o) => `<button class="sn" data-path="${esc(o.id)}">${ico(o)}<span>${esc(short(titleOf(o), 34))}</span></button>`).join('');
      if (s.noPath === n.id) h += `<div class="sr">${esc(S.noPath)}</div>`; }
    if (ctx.onOpen) h += `<button class="btn btn-sm sdet" data-sopen="1">${esc(S.details)}</button>`;
    sum.innerHTML = h; }
  return { RELT, chainFrom, shortest, peekHTML, ico, sumNode, sumPath, drawSum };
}
