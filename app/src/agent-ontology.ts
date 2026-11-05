/** Agent·절차·정책의 순수 계산 — 합친 목록, 되풀이 검사, 관계 지도의 배치.
 *
 *  Agent 는 맥락의 경계(Maker·Checker), 절차(procedure)는 Agent 가 과제에 맞춰 주소로 읽는 일의 방법,
 *  정책(policy)은 여러 절차가 함께 지키는 강제 제한이다(Decision, 다스리는 절차마다 `about` 엣지).
 *  실행이 강제하는 목록은 셋의 합집합이고, 금지는 늘 이기며, 절차와 정책은 좁히기만 한다.
 *  vincos `agents.compose` 와 같은 규칙이다. 근거: [[vinc.canon.terminology §3.2]]
 *
 *  화면에서 읽는 것은 이 파일의 결과뿐이다. DOM·네트워크를 모르므로 테스트가 그대로 부른다. */

export type Props = Record<string, unknown>;
export type Kind = "tool" | "capability" | "person_only" | "never" | "gate";
/** 항목이 온 곳: Agent 자신, 절차, 또는 정책 id. */
export type Origin = "agent" | "procedure" | string;
export interface Item { kind: Kind; item: string; origin: Origin }
export interface PolicyRule { id: string; rule: Props }

const strs = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);
const nested = (v: unknown, key: string): string[] =>
  (v && typeof v === "object" && !Array.isArray(v) ? strs((v as Record<string, unknown>)[key]) : []);
const actions = (v: unknown): string[] => (Array.isArray(v) ? v.map((x) =>
  (x && typeof x === "object" ? String((x as Record<string, unknown>).action ?? "") : String(x ?? ""))).filter(Boolean) : []);
const gateIds = (v: unknown): string[] => (Array.isArray(v) ? v.map((g) =>
  (g && typeof g === "object" ? String((g as Record<string, unknown>).id ?? "") : "")).filter(Boolean) : []);

/** 한 노드의 props 에서 다섯 목록을 읽는다(Agent·절차·정책 rule 이 같은 키를 쓴다). */
export function listsOf(p: Props): Record<Kind, string[]> {
  return { tool: strs(p.disallowed_tools), capability: nested(p.capabilities, "deny"), person_only: actions(p.person_only),
           never: strs(p.never), gate: gateIds(p.gates) };
}

const KINDS: Kind[] = ["tool", "capability", "person_only", "never", "gate"];

/** 실행이 강제하는 목록. 같은 항목은 처음 온 곳(Agent → 절차 → 정책 순)으로 한 번만 센다. */
export function compose(agent: Props, procedure: Props | null, policies: PolicyRule[] = []): Item[] {
  const out: Item[] = [];
  const seen = new Set<string>();
  const add = (src: Record<Kind, string[]>, origin: Origin) => {
    for (const kind of KINDS) for (const item of src[kind]) {
      const k = `${kind}\u0000${item}`;
      if (!seen.has(k)) { seen.add(k); out.push({ kind, item, origin }); }
    }
  };
  add(listsOf(agent), "agent");
  if (procedure) {
    add(listsOf(procedure), "procedure");
    for (const p of policies) add(listsOf(p.rule || {}), p.id);
  }
  return out;
}

/** 절차가 자기 Agent 에 이미 있는 항목을 되풀이하는가. 되풀이는 고칠 곳을 둘로 만든다. */
export function repeats(agent: Props, procedure: Props): { kind: Kind; item: string }[] {
  const a = listsOf(agent), p = listsOf(procedure);
  return KINDS.flatMap((kind) => p[kind].filter((item) => a[kind].includes(item)).map((item) => ({ kind, item })));
}

/** 한 칸에 새로 넣으려는 값 가운데 Agent 에 이미 있는 것. 편집 중 바로 알리는 데 쓴다. */
export function repeatedInField(agent: Props, kind: Kind, values: string[]): string[] {
  const have = listsOf(agent)[kind];
  return values.map((v) => v.trim()).filter((v) => v && have.includes(v));
}

// ── 관계 지도 ─────────────────────────────────────────────────────────────────────────────
export interface MapAgent { id: string; title: string; tier?: string }
export interface MapProcedure { id: string; title: string; agent: string; policies: string[] }
export interface MapPolicy { id: string; title: string }
export interface MapNode { id: string; kind: "agent" | "procedure" | "policy"; x: number; y: number; r: number;
                           label: string; anchor: "start" | "end" | "middle" }
export interface MapLink { from: string; to: string; kind: "has" | "governs" | "returns" }

/** 지도 높이: 한쪽에 절차가 많으면 그만큼 키운다(이름표 한 줄에 30px). */
export function mapHeight(procedures: MapProcedure[], agents: MapAgent[]): number {
  const most = Math.max(0, ...agents.map((a) => procedures.filter((p) => p.agent === a.id).length));
  return Math.max(540, most * 30 + 140);
}

/** 결정론적 배치. Agent 는 가로로 나란히(execute 가 왼쪽), 절차는 자기 Agent 바깥쪽 타원 위에 **세로로 고르게**
 *  놓는다: 같은 쪽 이름표가 한 줄씩 겹치지 않게(최소 28px). 왼쪽 Agent 의 이름표는 왼쪽으로, 오른쪽 Agent 의
 *  이름표는 오른쪽으로 편다. 정책은 맨 위 줄. 이름표 폭(22자, 약 140px)만큼 가장자리를 비운다.
 *  절차 점의 반지름은 12: 누르기 쉬워야 한다. 같은 입력이면 같은 좌표다. */
export function layoutMap(agents: MapAgent[], procedures: MapProcedure[], policies: MapPolicy[],
                          width = 760, height = mapHeight(procedures, agents)): { nodes: MapNode[]; links: MapLink[] } {
  const order = [...agents].sort((a, b) => (a.tier === "verify" ? 1 : 0) - (b.tier === "verify" ? 1 : 0) || a.id.localeCompare(b.id));
  const nodes: MapNode[] = [];
  const links: MapLink[] = [];
  // 이름표 폭 예산(22자, 약 140px). 옆 칸이 편집기라 그래프가 좁을 때(560px)도 두 Agent 가 겹치지 않아야 한다.
  const LABEL = 140, TOP = 84, BOTTOM = 40;
  const cy = TOP + (height - TOP - BOTTOM) / 2;
  const centres = new Map<string, { x: number; y: number }>();
  const xs = order.length === 1 ? [width / 2]
    : order.map((_, i) => i === 0 ? Math.max(LABEL + 80, width * 0.38) : Math.min(width - LABEL - 62, width * 0.8));
  order.forEach((a, i) => {
    centres.set(a.id, { x: round(xs[i]), y: round(cy) });
    nodes.push({ id: a.id, kind: "agent", x: round(xs[i]), y: round(cy), r: 24, label: a.title, anchor: "middle" });
  });
  order.forEach((a, i) => {
    const mine = procedures.filter((p) => p.agent === a.id);
    const c = centres.get(a.id)!;
    const right = order.length > 1 && i === order.length - 1;
    // 반지름은 이름표 예산에서 나온다: 점(12) + 틈(6) + 이름표(LABEL)가 가장자리 안에 들어오게.
    const rx = right ? Math.max(40, Math.min(90, width - c.x - LABEL - 20)) : Math.max(40, Math.min(c.x - LABEL - 20, width * 0.24));
    const span = Math.max(1, mine.length - 1) * 30;
    const top = cy - span / 2;
    mine.forEach((p, k) => {
      const y = mine.length === 1 ? cy : top + k * 30;
      const ry = Math.max(span / 2 + 30, 60);
      const dx = rx * Math.sqrt(Math.max(0, 1 - ((y - cy) / ry) ** 2));
      const x = right ? c.x + dx : c.x - dx;
      nodes.push({ id: p.id, kind: "procedure", x: round(x), y: round(y), r: 12, label: p.title, anchor: right ? "start" : "end" });
      links.push({ from: a.id, to: p.id, kind: "has" });
      for (const pol of p.policies) links.push({ from: pol, to: p.id, kind: "governs" });
    });
  });
  policies.forEach((pol, i) => {
    const slot = width / (policies.length + 1);
    nodes.push({ id: pol.id, kind: "policy", x: round(Math.max(110, Math.min(width - 110, slot * (i + 1) * (policies.length === 1 ? 0.75 : 1)))),
                 y: 30, r: 16, label: pol.title, anchor: "middle" });
  });
  if (order.length > 1) links.push({ from: order[order.length - 1].id, to: order[0].id, kind: "returns" });
  const known = new Set(nodes.map((n) => n.id));
  return { nodes, links: links.filter((l) => known.has(l.from) && known.has(l.to)) };
}

const round = (v: number) => Math.round(v * 10) / 10;

/** 정책 id 를 화면용 짧은 이름으로(`decision:policy-read-only-procedures` → `read-only-procedures`). */
export function policySlug(id: string): string { return id.replace(/^decision:policy-/, ""); }
export function procedureSlug(id: string): string { return id.replace(/^concept:procedure-/, ""); }
