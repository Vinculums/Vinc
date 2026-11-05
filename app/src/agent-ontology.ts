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

/** 결정론적 배치. Agent 는 가로로 나란히(execute 가 왼쪽), 절차는 자기 Agent 둘레의 호, 정책은 위쪽 줄.
 *  절차 점의 반지름은 12: 지도에서 누르기 쉬워야 한다(시안의 7은 작았다). 같은 입력이면 같은 좌표다. */
export function layoutMap(agents: MapAgent[], procedures: MapProcedure[], policies: MapPolicy[],
                          width = 760, height = 540): { nodes: MapNode[]; links: MapLink[] } {
  const order = [...agents].sort((a, b) => (a.tier === "verify" ? 1 : 0) - (b.tier === "verify" ? 1 : 0) || a.id.localeCompare(b.id));
  const nodes: MapNode[] = [];
  const links: MapLink[] = [];
  const cy = height * 0.56;
  const centres = new Map<string, { x: number; y: number }>();
  order.forEach((a, i) => {
    // 이름표가 지도 밖으로 나가지 않을 만큼 안쪽에 둔다(이름표는 26자까지, 약 160px).
    const x = order.length === 1 ? width / 2 : width * (0.44 + 0.28 * i / Math.max(1, order.length - 1));
    centres.set(a.id, { x, y: cy });
    nodes.push({ id: a.id, kind: "agent", x, y: cy, r: 24, label: a.title, anchor: "middle" });
  });
  for (const a of order) {
    const mine = procedures.filter((p) => p.agent === a.id);
    const c = centres.get(a.id)!;
    const left = order.length > 1 && a.id === order[0].id;
    // 왼쪽 Agent 는 왼쪽 반원과 위·아래로, 오른쪽 Agent 는 오른쪽으로 편다. 하나뿐이면 둘레 전체.
    const [t0, t1] = order.length === 1 ? [0, 2 * Math.PI] : left ? [0.55 * Math.PI, 1.45 * Math.PI] : [-0.45 * Math.PI, 0.45 * Math.PI];
    const rx = left ? width * 0.15 : width * 0.1, ry = height * (mine.length > 8 ? 0.38 : 0.28);
    mine.forEach((p, i) => {
      const t = mine.length === 1 ? (t0 + t1) / 2 : t0 + (t1 - t0) * i / (mine.length - 1);
      const x = c.x + rx * Math.cos(t), y = c.y + ry * Math.sin(t);
      nodes.push({ id: p.id, kind: "procedure", x: round(x), y: round(y), r: 12, label: p.title,
                   anchor: x < c.x - 1 ? "end" : x > c.x + 1 ? "start" : "middle" });
      links.push({ from: a.id, to: p.id, kind: "has" });
      for (const pol of p.policies) links.push({ from: pol, to: p.id, kind: "governs" });
    });
  }
  policies.forEach((pol, i) => {
    const x = width * (policies.length === 1 ? 0.3 : 0.15 + 0.7 * i / Math.max(1, policies.length - 1));
    nodes.push({ id: pol.id, kind: "policy", x: round(x), y: 36, r: 16, label: pol.title, anchor: "middle" });
  });
  if (order.length > 1) links.push({ from: order[order.length - 1].id, to: order[0].id, kind: "returns" });
  const known = new Set(nodes.map((n) => n.id));
  return { nodes, links: links.filter((l) => known.has(l.from) && known.has(l.to)) };
}

const round = (v: number) => Math.round(v * 10) / 10;

/** 정책 id 를 화면용 짧은 이름으로(`decision:policy-read-only-procedures` → `read-only-procedures`). */
export function policySlug(id: string): string { return id.replace(/^decision:policy-/, ""); }
export function procedureSlug(id: string): string { return id.replace(/^concept:procedure-/, ""); }
