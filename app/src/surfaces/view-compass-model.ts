/** 나침반이 읽는 데이터의 모델: 그래프 읽기를 나침반의 노드 · 관계 · 주제 사슬로 바꾸고, 그것만 읽는 순수한 함수를 둔다.
 *  DOM 을 만지지 않는다. 마운트(view-compass.ts)는 모델을 한 번 짓고 읽기만 한다. 렌즈의 상태는 view-compass-state.ts 에 있다. */
import { rootTopicOf } from "../topic-tint-table";
import type { GraphNode } from "../api";

/** 나침반이 읽는 그래프 노드(compassDataFrom 의 모양). k: 종류('doc' 은 문서 미러), t: 이름, d: 주제 경로(빈 값이면 뿌리), ts · ct · vf · vu: 날짜들,
 *  g: 이음 수, m: 뜻의 관계 수 */
export interface CompassNode { id: string; k: string; t: string; d: string; ts: string; ct?: string; vf?: string; vu?: string; g: number; m: number }
export interface CompassData { nodes: CompassNode[]; edges: [string, string, string][] }
/** compassDataFrom 이 읽는 그래프 읽기의 칸(GraphNode · GraphEdge 와, 읽기에 따라 함께 오는 title · updated_at · 객체 끝). */
type GraphNodeIn = Pick<GraphNode, "id" | "kind" | "label" | "domain" | "degree" | "ts" | "content_ts" | "valid_from" | "valid_until" | "props"> & { title?: string; updated_at?: string };
type GraphEnd = string | { id: string };
type GraphEdgeIn = { source: GraphEnd; target: GraphEnd; type: string; presentation?: boolean };
/** 나침반 안의 노드: 그래프 노드(DNode)와 domain 사슬로 지은 주제(TNode, k 는 'topic'). 한쪽에만 있는 칸은 다른 쪽에서 비어 있다. */
export interface DNode extends CompassNode { path?: undefined; direct?: undefined; sub?: undefined; count?: undefined }
export interface TNode { id: string; k: "topic"; path: string; direct: DNode[]; sub: Set<string>; count: number; ts?: string; t?: string;
  d?: undefined; ct?: undefined; vf?: undefined; vu?: undefined; g?: undefined; m?: undefined }
export type LNode = DNode | TNode;
/** 두 그래프 노드 사이의 관계 하나(a → b, 종류 t). */
export interface Edge { a: DNode; b: DNode; t: string }

/** 그래프 탭의 읽기(GraphNode·GraphEdge)를 나침반이 읽는 모양으로. 주제(domain) 노드는 노드의 domain 사슬에서 다시 만든다. */
const idDate = (id: string) => { const m = /-(20\d\d-\d\d-\d\d)$/.exec(String(id)); return m ? m[1] : ""; };
export function compassDataFrom(nodes: readonly GraphNodeIn[], edges: readonly GraphEdgeIn[]): CompassData {
  const out: CompassNode[] = nodes.filter((n) => n.kind !== "domain" && n.kind !== "area").map((n) => ({
    id: n.id, k: String(n.id).startsWith("concept:doc.") ? "doc" : n.kind as string, t: n.label || n.title || n.id, d: n.domain || "",   // 주제 없는 노드는 빈 값 그대로 둔다: 어느 뿌리에 설지는 compassModel 이 정한다
    // 날짜: 사건은 그래프 읽기의 ts 가 곧 그 일이 있었던 날이다(updated_at 은 마지막으로 고친 때라 뒤에 둔다).
    // 결정은 내용 시간 칸이 없어 props.valid_from(결정한 날)을, 그것도 없으면 id 끝의 날짜(…-2026-09-20, 이 그래프의 이름 관례)를 쓴다.
    ts: String(n.ts || n.updated_at || "").slice(0, 10), ct: String(n.content_ts || "").slice(0, 10) || idDate(n.id) || undefined,
    vf: String(n.valid_from || n.props?.valid_from || "").slice(0, 10) || undefined, vu: n.valid_until || undefined, g: n.degree || 0, m: 0 }));
  const byId = new Map(out.map((n) => [n.id, n])), M = new Set(["RELATED_TO", "PREREQUISITE_OF", "DERIVES_FROM", "INHERITS_FROM", "RETURNS_TO", "CONNECTS"]);
  const es: [string, string, string][] = [];
  for (const e of edges) { const a = typeof e.source === "object" ? e.source.id : e.source, b = typeof e.target === "object" ? e.target.id : e.target;
    if (!byId.has(a) || !byId.has(b) || e.presentation) continue; es.push([a, b, e.type]); if (M.has(e.type)) { byId.get(a)!.m++; byId.get(b)!.m++; } }
  return { nodes: out, edges: es };
}

/** 데이터의 값 하나를 처음 쓸 때 한 번 센다. */
const memo = <T,>(f: () => T): (() => T) => { let done = false, v: T; return () => { if (!done) { v = f(); done = true; } return v; }; };

// 노드 하나만 읽는 것: 주제 경로 · 뜻의 노드인가 · 범례의 종류 · 그 일이 있었던 날
export const topicOf = (n: LNode) => (n.k === 'topic' ? n.path : n.d);
export const isMean = (n: LNode) => ['concept', 'doc', 'bridge'].includes(n.k) || n.m! > 0;
export const kindKey = (n: LNode) => (n.k === 'decision' || n.k === 'record' || n.k === 'episode' || n.k === 'bridge' ? n.k : n.k === 'doc' ? 'doc' : 'concept');   // 범례의 종류 키(끄고 켜는 단위)
const fullDate = (s: string) => (!s ? '' : s.length === 4 ? s + '-01-01' : s.length === 7 ? s + '-01' : s.slice(0, 10));   // '2026' · '2026-03' 같은 덜 적힌 날짜는 그 첫날로
// 날짜 = 그 일이 있었던 날: 내용 시간 → 유효 시작(→ 사건은 그날). 마지막으로 고친 때만 있는 노드는 날짜가 없는 것으로 본다(막대에 넣지 않고 흐리지도 않는다).
export const when = (n: LNode) => fullDate(n.ct || n.vf || (n.k === 'episode' ? n.ts : '') || '');

/** 마운트 하나가 짓는 모델: 뿌리(ROOT · RD) · 노드와 관계 · 이웃 표(adj) · 주제 사슬(topics), 그리고 그것만 읽는 함수들 */
export interface CompassModel { ATTACH: Set<string>; ROOT: string; RD: number; nodes: DNode[]; byId: Map<string, DNode>; edges: Edge[]; adj: Map<string, Edge[]>;
  topics: Map<string, TNode>; fresh: (n: LNode) => boolean; bridgeEnds: (b: LNode) => DNode[]; byAny: (id: string) => LNode | null | undefined; chainTo: (n: LNode) => LNode[] }
/** 데이터에서 모델을 짓는다. `C.rootTopic` 은 이름 없는 가상 뿌리의 이름표다. 들어온 데이터는 바꾸지 않는다. */
export function compassModel(W0: CompassData, C: { rootTopic: string }): CompassModel {
  const ATTACH = new Set(['ABOUT', 'ATTACHES_TO', 'MENTIONS']);
  // 뿌리: 이 그래프의 최상위 주제가 하나뿐이면 그 주제가 뿌리다('vinc' 만 쓰는 그래프는 예전과 같다). 둘 이상이거나 하나도 없으면
  // 이름 없는 가상 뿌리('')가 모든 최상위 주제를 품는다. 주제 없는 노드는 뿌리에 직접 서고, 이름을 지어 붙이지 않는다.
  // RD 는 뿌리가 경로에서 차지하는 칸 수다(영역 = 뿌리 다음 칸을 자를 때 쓴다). 규칙은 색 표와 같은 `rootTopicOf` 하나다.
  const ROOT = rootTopicOf(W0.nodes.map((n) => n.d)), RD = ROOT ? 1 : 0;
  const nodes: DNode[] = W0.nodes.map((n) => (n.d ? n : { ...n, d: ROOT })), byId = new Map(nodes.map((n) => [n.id, n]));
  const edges = W0.edges.map(([a, b, t]) => ({ a: byId.get(a), b: byId.get(b), t })).filter((e) => e.a && e.b) as Edge[];
  const adj = new Map<string, Edge[]>(nodes.map((n) => [n.id, []])); for (const e of edges) { adj.get(e.a.id)!.push(e); adj.get(e.b.id)!.push(e); }
  // 다리는 주제를 갖지 않는다(여러 주제 사이에 있다). 그래서 다리를 끝들이 만나는 주제, 곧 끝 노드들 주제의 가장 가까운 공통 조상에 둔다.
  // 같은 영역 안의 끝들을 잇는 다리는 그 영역 층에 서고, 영역을 넘는 다리만 뿌리에 남는다. 끝마다의 주제(BENDS)는 끝 층에서 다리를 보일 때 쓴다
  const BENDS = new Map<string, string[]>();
  for (const b of nodes) { if (b.k !== 'bridge') continue; const raw = W0.nodes.find((n) => n.id === b.id);
    const ds = [...new Set(adj.get(b.id)!.filter((e) => e.t === 'CONNECTS').map((e) => (e.a === b ? e.b : e.a).d).filter((d) => d && d !== ROOT))];
    BENDS.set(b.id, ds); if (raw && raw.d || !ds.length) continue;
    const seg = ds.map((d) => d.split('/')); let k = 0; while (seg.every((x) => k < x.length && x[k] === seg[0][k])) k++;
    if (k > RD) b.d = seg[0].slice(0, k).join('/'); }
  const fresh = (n: LNode) => { const w = when(n); return !!w && (Date.parse(newestW()) - Date.parse(w)) / 864e5 <= 7; };   // '최근' = 내용 시간으로 7일 안
  const newestW = memo(() => nodes.map(when).filter(Boolean).sort().pop() || '');
  // 주제: 노드의 domain 사슬에서 만든다. 'vinc/agent/roles' → vinc, vinc/agent, vinc/agent/roles (가상 뿌리면 '' 가 맨 앞에 붙는다)
  const topics = new Map<string, TNode>();
  const tget = (p: string): TNode => { if (!topics.has(p)) topics.set(p, { id: `topic:${p}`, path: p, k: 'topic', direct: [], sub: new Set(), count: 0 }); return topics.get(p)!; };
  const chainOf = (d: string | undefined) => { const o: string[] = ROOT ? [] : [''], s = d ? d.split('/') : []; for (let i = 1; i <= s.length; i++) o.push(s.slice(0, i).join('/')); return o; };
  const rootTopic = tget(ROOT); if (!ROOT) rootTopic.t = C.rootTopic;   // 뿌리는 노드가 없어도 늘 있다. 가상 뿌리만 이름표(t)를 갖는다
  for (const n of nodes) { const ch = chainOf(n.d);
    ch.forEach((p, i) => { const t = tget(p); t.count++; if ((n.ts || '') > (t.ts || '')) t.ts = n.ts; if (i < ch.length - 1) t.sub.add(ch[i + 1]); });
    tget(ch[ch.length - 1]).direct.push(n); }
  const bridgeEnds = (b: LNode) => adj.get(b.id)!.filter((e) => e.t === 'CONNECTS').map((e) => (e.a === b ? e.b : e.a));
  const byAny = (id: string): LNode | null | undefined => byId.get(id) || byId.get(String(id).split('@@')[0]) || (id.startsWith('topic:') ? topics.get(id.slice(6)) : null);   // 'id@@…' 별칭은 앞의 id 로 찾는다
  // 찾기: 주제 사슬을 따라 펼친 다음 그 노드를 편다
  const chainTo = (n: LNode): LNode[] => { const out: LNode[] = chainOf(topicOf(n)).map((p) => topics.get(p)!); if (n.k !== 'topic') out.push(n); return out; };
  return { ATTACH, ROOT, RD, nodes, byId, edges, adj, topics, fresh, bridgeEnds, byAny, chainTo };
}
