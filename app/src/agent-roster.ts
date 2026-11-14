// Agent 역할 로스터: 역할 id 판별 · 표시 이름 · 팀 순서, `/graph` 의 역할 노드로 로스터를 짓는 일, LNB 가 마지막으로 읽은 로스터.
// LNB(Agent 레일) · Agents 보드 · 역할 편집 · 그래프 표면이 여기서 읽는다. 로스터를 다시 읽어 바꾸는 것은 LNB 하나다.
import type { GraphEdge, GraphNode } from "./api";
import { AGENT_ID_PREFIX } from "./agent-ids";

/** 새 역할의 기본 주제와 기존 호환 주제. 목록 발견은 이 주제로 제한하지 않는다. */
export const AGENT_TOPICS = ["agent", "vinc/agent"] as const;

/** 로스터 개념은 역할 id 접두(`AGENT_ID_PREFIX`)가 같아도
 *  역할이 아니다(`vinc_mcp.roles.is_role_id`). */
const AGENT_ROSTER_CONCEPT = "concept:agent-orchestration";
const isRoleId = (id: string): boolean =>
  id.startsWith(AGENT_ID_PREFIX) && id !== AGENT_ROSTER_CONCEPT;
/** 문서 열(View)도 역할을 알아본다 — 판별을 두 벌 두지 않으려고 내보낸다. */
export const isAgentRoleId = isRoleId;
/** 표시용 역할 이름. 옛 제목의 접두("Agent role:")를 뗀다 — 새로 쓰는 역할은 접두가
 *  없다([[vinc.canon.agent-roles §2]]). */
export const stripRolePrefix = (s: string): string => s.replace(/^Agent role:\s*/i, "");

/** 여섯 팀의 화면 순서. 이름은 `props.team` 정본이고, 순서는 로스터 v2 가
 *  비준한 그대로다. 여기 일곱 번째 팀을 만들지 않는다.
 *  근거: [[decision:roster-v2-ratified-2026-09-16]]
 *  [[decision:the-agent-rail-opens-an-agents-board-and-roles-edit-in-place-2026-10-24]] */
const AGENT_TEAM_ORDER = [
  "Program Office",
  "Product Engineering",
  "Design",
  "Content and Marketing",
  "Quality and Security",
  "Platform and Release",
] as const;

export type AgentRel = { from: string[]; then: string[]; beside: string[] };
export type AgentRole = {
  id: string; domain: string; title: string; team: string;
  teamRole: string; mission: string; rel: AgentRel;
};

/** `/graph` 가 노드에 싣는 props 까지 읽는 노드. 클라 GraphNode 타입은 이 열을 아직 안 적는다. */
type GraphRoleNode = GraphNode & { props?: Record<string, unknown> };

/** LNB 가 마지막으로 읽은 역할 로스터(Agent 레일일 때만). 안 읽었거나 문서 레일이거나 읽기에 실패했으면 null.
 *  이 파일만 바꾼다: 바꾸는 길은 `setAgentRoster` 하나이고, 다른 모듈은 읽기만 한다. */
export let agentRoster: AgentRole[] | null = null;
/** 로스터를 바꾼다. LNB 의 다시 읽기가 읽은 것(또는 null)을 넣는다. */
export function setAgentRoster(next: AgentRole[] | null): void { agentRoster = next; }

function strProp(props: Record<string, unknown> | undefined, key: string): string {
  const v = props?.[key];
  return typeof v === "string" ? v.trim() : "";
}

function roleTitle(n: GraphRoleNode): string {
  const job = strProp(n.props, "job_title");
  if (job) return job;
  return stripRolePrefix(String(n.label || n.id));
}

/** 역할 사이 엣지 → from / then / beside. 계산은 `vinc_mcp.roles.works_with` 와
 *  같다(prerequisite_of 방향이 뜻, relates_to 는 옆). 화면에 쓰는 이름은
 *  셸의 `slug (title)` 이 아니라 직함이다 — 목록 폭이 그 접두를 못 받는다. */
function roleRels(rid: string, byId: Map<string, GraphRoleNode>,
                  edges: GraphEdge[]): AgentRel {
  const from = new Set<string>();
  const then = new Set<string>();
  const beside = new Set<string>();
  const show = (other: string): string => {
    const n = byId.get(other);
    return n ? roleTitle(n) : other.replace(AGENT_ID_PREFIX, "");
  };
  for (const e of edges) {
    const src = e.source || "";
    const dst = e.target || "";
    if (rid !== src && rid !== dst) continue;
    if (src === dst || !byId.has(src) || !byId.has(dst)) continue;
    const other = src === rid ? dst : src;
    const kind = String(e.type || "").toUpperCase();
    if (kind === "PREREQUISITE_OF") {
      (src === rid ? then : from).add(show(other));
    } else if (kind === "RELATED_TO") {
      beside.add(show(other));
    }
  }
  const sort = (s: Set<string>) => [...s].sort((a, b) => a.localeCompare(b));
  return { from: sort(from), then: sort(then), beside: sort(beside) };
}

export function teamRank(team: string): number {
  const i = (AGENT_TEAM_ORDER as readonly string[]).indexOf(team);
  return i < 0 ? AGENT_TEAM_ORDER.length : i;
}
function teamRoleRank(role: string): number {
  if (role === "lead") return 0;
  if (role === "coordinator") return 1;
  return 2;
}

export function buildAgentRoster(g: { nodes?: GraphRoleNode[]; edges?: GraphEdge[] } | null
                          | undefined): AgentRole[] {
  const nodes = [...new Map((g?.nodes || []).filter((n) => isRoleId(n.id))
    .map((n) => [n.id, n])).values()];
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const edges = g?.edges || [];
  const rows: AgentRole[] = nodes.map((n) => ({
    id: n.id,
    domain: n.domain || AGENT_TOPICS[0],
    title: roleTitle(n),
    team: strProp(n.props, "team"),
    teamRole: strProp(n.props, "team_role"),
    mission: (typeof (n as { mission?: unknown }).mission === "string"
      ? String((n as { mission?: string }).mission) : strProp(n.props, "mission")).trim(),
    rel: roleRels(n.id, byId, edges),
  }));
  rows.sort((a, b) => {
    const td = teamRank(a.team) - teamRank(b.team);
    if (td) return td;
    if (a.team !== b.team) return a.team.localeCompare(b.team);
    const rd = teamRoleRank(a.teamRole) - teamRoleRank(b.teamRole);
    if (rd) return rd;
    return a.title.localeCompare(b.title);
  });
  return rows;
}

/** 읽어 온 Agent 역할 수. 아직 안 읽었으면 null. 홈의 빈 목록이 무엇으로 이을지 가른다. */
export const agentRoleCount = (): number | null => agentRoster ? agentRoster.length : null;
