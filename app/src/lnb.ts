import { api, cfg } from "./api";
import type { GraphEdge, NavGroup, NavNode, NavTreeRes } from "./api";
import { COPY } from "./copy";
import { icon } from "./icons";
import type { ThemeMode } from "./theme";
import { tintMap } from "./view-tint";
import { loadPrefs } from "./view-prefs";
import { mark3dSvg, markSvg } from "./surfaces/view-planar";

/** 좌측 문서 트리 (DEC D69) — 셸의 두 번째 크롬.
 *
 *  여기 사는 것: 표면 내비(홈·검색·팩) · 주제 트리(주제 → 그 주제의 문서 또는 노드)
 *  · 접기.
 *  D56 이 상단 바 하나로 몰아넣었던 것을 되돌린다. 되돌리는 이유는 그때와 전제가
 *  다르기 때문이다 — 이 사이드바가 싣는 것은 "표면 5개로 가는 내비"(D26 이 아이콘
 *  레일로 충분하다고 판정한 것)가 아니라 **문서 탐색기**다. Obsidian 으로 치면
 *  리본이 아니라 파일 목록 쪽이고, 그건 아이콘 레일로 대체되지 않는다.
 *
 *  **문서 정렬은 개념 계층 깊이순**이다(`/nav-tree`). 얕은 개념(선행·원본)의 문서가
 *  위, 깊은 개념(파생)의 문서가 아래로 들여쓰기된다. 최근 수정순이 아닌 이유: 목록의
 *  일이 "무엇이 새로운가"가 아니라 "이 주제가 어떻게 짜여 있는가"이기 때문이다.
 *
 *  **표면과의 계약은 훅 하나뿐이다**(상단 바와 같은 태도). View 가 마운트되면
 *  자기 `focusNode` 를 넘기고(setLnbNavigator), 떠나면 지운다. 훅이 없으면 문서
 *  클릭은 해시 착지로 강등된다 — 눌러도 아무 일 없는 줄을 만들지 않는다. */

/** 트리는 그 문서가 **어느 주제 밑에 있는지 이미 안다** — 그 정보를 함께 넘긴다.
 *  받는 쪽(합계 뷰)은 개념 마크가 없어서 그래프 노드로는 주제를 되짚을 수 없다. */
type Navigator = (nodeId: string, domain?: string, docId?: string) => void;
/** 주제를 고른다. `add` 면 지금 고른 것에 **더한다**(Ctrl·⌘ 클릭).
 *  화면이 즉시 따라간다 — 고르는 것과 보는 것이 어긋나는 중간 상태를 두지 않는다. */
type TopicPicker = (path: string, add: boolean, set?: boolean) => void;
/** `set` 이 참이면 토글하지 않고 그 주제 하나로 **건다**(이미 그것뿐이면 그대로 둔다).
 *  Agent 레일이 쓴다 — 토글이면 이미 걸린 스코프를 Agent 를 고르는 손이 벗긴다. */

const KEY_OPEN = "vinc.shell.lnb";        // 열림 상태(접기는 사용자 소유)
const KEY_EXPANDED = "vinc.shell.lnb.groups";   // 펼친 주제 목록
const KEY_KIND = "vinc.shell.lnb.kind";   // 종 레일이 고른 것

let navigate: Navigator | null = null;
let pickTopic: TopicPicker | null = null;
let tree: NavTreeRes | null = null;
let loading = false;
let failed = false;
let active: { domain?: string; nodeId?: string } = {};
/** 지금 스코프가 걸고 있는 주제들 — 트리 줄의 선택 표시가 이걸 읽는다. */
let picked = new Set<string>();
let expanded = new Set<string>();

/** 주제 색(열린 층) — 지도와 **같은 토글**(`ViewPrefs.domainTint`)을 읽고 같은
 *  `tintMap` 으로 만든다. paint 마다 한 번 만들어 줄마다 조회한다.
 *
 *  ⚠ **색을 배정하는 배열은 이 트리의 것이다**(지도의 것을 받아 오지 않는다).
 *  `tintMap` 은 넘긴 주제 집합을 정렬해 순위로 색을 주므로, 지도의 배열을 따라가면
 *  (1) 스코프를 옮길 때마다 왼쪽 트리의 색이 함께 흔들리고 (2) 지금 지도에 없는
 *  주제의 줄은 색을 잃으며 (3) 지도가 없는 표면에서는 색이 통째로 사라진다. 트리는
 *  자기 주제 전량을 늘 갖고 있어 집합이 안정적이라 이쪽을 정본으로 뒀다.
 *  대가는 **같은 주제가 지도와 다른 색으로 보일 수 있다**는 것이고, 받아들이는
 *  근거는 이 색이 뜻을 나르지 않는다는 성질이다(꺼도 아무 정보가 사라지지 않는다:
 *  [[decision:topic-tint-ships-on]]). */
let tints: Map<string, string> | null = null;
const themeMode = (): ThemeMode =>
  document.documentElement.dataset.theme === "dark" ? "dark" : "light";
/** 줄의 style 에 얹을 조각. ⚠ 깊이 잉크는 CSS 가 **부분문자열**로 읽는다
 *  (app.css 의 `[style*="--ink:0"]`) — `--ink:N` 리터럴을 깨지 않도록 **뒤에** 붙인다. */
const tintStyle = (domain: string): string => {
  const c = tints?.get(domain.trim());
  return c ? `;--tint:${c}` : "";
};

/** 종 레일 한 칸. `dest` 는 종이 아니라 목적지(Agent). `offTopic` 은 주제 트리에
 *  서지 않는 종(Entity·Bridge)이라 조회를 보내지 않고 지도 렌즈만 건다.
 *  `group` 은 같은 표식을 쓰는 종을 자리로 묶는다(구/정사면체). */
type RailItem = {
  key: string; kind?: string; domain?: string; mark: string;
  dest?: boolean; offTopic?: boolean; group?: "sphere" | "tetra"; icon?: string;
};

/** 종 레일이 고를 수 있는 것. 2026-09-21 scope-back: 홈과 Agent 가 구분선 **위**
 *  (같은 급의 목적지), 선 아래는 종 묶음이다. Agent 를 선 아래에 두던 2026-09-09
 *  배치는 이 지시가 덮는다.
 *
 *  Entity·Bridge 는 `/nav-tree` 모집단이 아니다(`IN_DOMAIN` 이 넷에만 걸리고
 *  Bridge 는 `CROSSES`). 레일에 세우는 이유는 지도 렌즈이지 빈 주제를 만들기
 *  위함이 아니다. 정본은 `api/query.py` 의 `_TREE_PK`. */
const RAIL: readonly RailItem[] = [
  { key: "agent", kind: "concept", domain: "vinc/agent/roles", mark: "concept",
    dest: true, icon: "domains" },
  { key: "concept", kind: "concept", mark: "concept", group: "sphere" },
  { key: "decision", kind: "decision", mark: "decision", group: "sphere" },
  { key: "record", kind: "record", mark: "record", group: "tetra" },
  { key: "episode", kind: "episode", mark: "episode", group: "tetra" },
  // Entity 는 기록과 같은 정사면체 표식. 묶음이 같은 모양의 형제를 가른다.
  { key: "entity", kind: "entity", mark: "record", group: "tetra", offTopic: true },
  { key: "bridge", kind: "bridge", mark: "bridge", offTopic: true },
];
const RAIL_KEYS = RAIL.map((r) => r.key);
/** 레일의 현재 선택. **빈 문자열이 뜻을 가진 상태다: "아무것도 안 고름" = 전체**
 *  (발주자 지시 2026-09-09: "홈 누르면 아무것도 선택 안 한 것으로 보여준다.
 *  아무것도 선택 안 함은 모든 노드 전체라는 즉, 기본 화면을 말한다").
 *  그래서 기본값도 빈 값이다 — 앱을 처음 열면 한 종에 갇혀 있는 것이 아니라
 *  전체를 보고 있어야 한다(여태 기본이 `concept` 이라 개념만 보였다). */
let railKey: string = RAIL_KEYS.includes(localStorage.getItem(KEY_KIND) || "")
  ? localStorage.getItem(KEY_KIND)! : "";
/** 고른 종의 정의. 아무것도 안 골랐으면 **없다** — 여기서 첫 항목으로 흘려보내면
 *  "전체"라는 상태가 조용히 "개념"이 된다(그 폴백이 이 기능을 막고 있었다). */
const rail = () => RAIL.find((r) => r.key === railKey);
let wired = false;

/** 종 표식 — 지도가 쓰는 어휘 그대로다. 여기서 **다시 그리지 않고** 그 함수를
 *  부른다: 갈라 두면 같은 노드가 화면마다 다른 모양이 되고, 그건 형태에 뜻을 실은
 *  것을 스스로 무너뜨리는 일이다(D35).
 *
 *  **레일은 3D, 줄은 평면이다.** 같은 어휘의 두 투영이고(범례가 관점에 따라 그렇게
 *  한다) 나누는 근거는 둘이다:
 *   ① 크기 — 레일은 18px 배지라 구의 상단 하이라이트와 정사면체의 두 면이 읽히지만,
 *     줄의 11px 에서는 그 음영이 잉크 얼룩이 된다.
 *   ② id — 3D 구는 하이라이트에 그라디언트 `<defs>` 가 필요하고 그 id 는 문서 안에서
 *     유일해야 한다. 레일은 다섯 줄이라 종마다 하나면 되는데, 트리는 같은 종이 수십
 *     줄이라 같은 id 가 그만큼 생긴다(브라우저는 첫 정의만 쓴다 — 지금은 값이 같아
 *     안 보이지만 하나를 고치는 날 나머지가 조용히 안 따라온다). */
function kindMark3d(kind: string, key: string, px: number): string {
  return mark3dSvg(kind, px, `rail-${key}`, "lnb-mk");
}
/** 트리 줄의 평면 표식. `markSvg` 는 좌표를 받으므로 20×20 뷰박스 중앙에 세운다. */
function kindMark(kind: string, px: number): string {
  const body = markSvg(kind, false, 10, 10, kind === "concept" || kind === "decision" ? 6 : 5.6,
                       "lnb-kmk");
  return `<svg class="lnb-mk" width="${px}" height="${px}" viewBox="0 0 20 20"
    aria-hidden="true">${body}</svg>`;
}

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T | null;
const esc = (s: string) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  c === "&" ? "&amp;" : c === "<" ? "&lt;" : c === ">" ? "&gt;" : "&quot;");

function isOpen(): boolean {
  return localStorage.getItem(KEY_OPEN) !== "closed";
}

function setOpen(open: boolean) {
  localStorage.setItem(KEY_OPEN, open ? "open" : "closed");
  document.getElementById("shell")?.setAttribute("data-lnb", open ? "open" : "closed");
  // 손잡이는 **상단 바 왼쪽 끝**에 하나다(발주자 지시 2026-09-09). 접는 대상이
  // 레일 + 트리 **둘 다**이므로, 접히는 것 안에 손잡이를 두면 함께 사라진다.
  // 자리가 레일 위라 접는 대상 위에 손잡이가 선다.
  const t = $("tb-shell-toggle");
  if (t) {
    t.setAttribute("aria-expanded", String(open));
    const label = open ? COPY.ui.lnb.close : COPY.ui.lnb.open;
    t.setAttribute("aria-label", label);
    t.setAttribute("data-tip", label);
    // 아이콘은 **문 모양**이고 방향으로 상태를 말하지 않는다(2026-09-09). 셰브론은
    // 방향만 말하고 무엇이 움직이는지를 말하지 않아서, 상단 바에 셰브론이 둘 있던
    // 동안 어느 것이 무엇인지 구별이 안 됐다. 상태는 `aria-expanded` 와 채움이 말한다.
    t.classList.toggle("on", open);
  }
}

function loadExpanded(): Set<string> {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY_EXPANDED) || "[]");
    return new Set(Array.isArray(raw) ? raw.filter((x) => typeof x === "string") : []);
  } catch { return new Set(); }
}

function saveExpanded() {
  try { localStorage.setItem(KEY_EXPANDED, JSON.stringify([...expanded])); } catch { /* 용량 초과 무시 */ }
}

/** View 가 마운트/언마운트하며 착지 동작을 넘긴다. null 이면 해시 착지로 강등. */
export function setLnbTopicPicker(fn: TopicPicker | null) {
  pickTopic = fn;
  // 저장된 레일이 Agent 인 채로 새로 열면 setRailKey 를 안 거치므로 스코프가 안 걸린다.
  // View 가 피커를 처음 걸 때 한 번만 건다(딥링크로 들어왔으면 그 착지를 덮지 않는다).
  // View 의 첫 show 뒤에 도는 것은 안전하다 — show 는 세대 번호로 옛 것을 버린다.
  if (fn && !launchScoped && railKey === "agent") {
    launchScoped = true;
    const here = location.hash.replace(/^#/, "");
    if (here === "" || here === "view") queueMicrotask(scopeAgentGraph);
  }
}
/** 첫 실행 스코프를 이미 걸었는가(세션에 한 번). */
let launchScoped = false;

/** 종 렌즈 — 레일에서 고른 종을 **지도에도** 건다. 훅이 없으면(표면이 없거나 떠났으면)
 *  레일은 트리만 바꾼다: 눌러도 아무 일 없는 손잡이를 만들지 않는다는 규율의 반대편
 *  이다. 여기서는 "지도가 없으면 지도 일은 안 한다"가 그 규율이다.
 *
 *  넘기는 것은 **종 이름 하나**다(노드 id 목록이 아니라). 지금 지도에 무엇이 그려져
 *  있는지는 표면만 알고, 그 집합을 트리가 다시 계산하면 두 벌이 어긋난다. */
type KindLens = (kind: string | null) => void;
let kindLens: KindLens | null = null;
export function setLnbKindLens(fn: KindLens | null) {
  kindLens = fn;
  if (fn) fn(lnbKind());
}

/** 지금 레일이 고른 종. Agent 는 주제 필터라 **종으로는 개념**이다 — 지도 강조도
 *  개념 전체가 되면 뜻이 흐려지므로 그때는 종 렌즈를 걸지 않는다(null). */
export function lnbKind(): string | null {
  const r = rail();
  if (!r) return null;                 // 아무것도 안 고름 = 렌즈 없음 = 지도 전체
  return r.domain ? null : (r.kind || null);
}

export function setLnbNavigator(fn: Navigator | null) {
  navigate = fn;
}

/** 지금 어디를 보고 있는지 트리에 표시한다(크럼이 하던 일 — D71 로 여기 왔다). */
/** 뿌리 자동 펼침을 한 번만 하기 위한 표시(paint 안 주석 참조). */
let autoExpanded = false;

export function setLnbActive(sel: { domain?: string; domains?: string[]; nodeId?: string }) {
  // 주제는 이제 **여럿**일 수 있다(스코프가 목록이 됐다). 첫 번째를 대표로 두고
  // 나머지는 선택 표시로만 쓴다 — 조상 펼치기는 대표 하나면 충분하다.
  const doms = sel.domains?.length ? sel.domains : sel.domain ? [sel.domain] : [];
  active = { ...sel, domain: doms[0] };
  picked = new Set(doms);
  if (doms[0]) {
    // 지금 있는 주제는 접혀 있으면 안 된다 — 그리고 중첩이 생겼으므로 **조상까지**
    // 함께 편다. 자기만 펴면 부모가 닫힌 채라 화면에 나타나지 않는다.
    const segs = doms[0].split("/").filter(Boolean);
    let grew = false;
    segs.forEach((_s, i) => {
      const path = segs.slice(0, i + 1).join("/");
      if (!expanded.has(path)) { expanded.add(path); grew = true; }
    });
    if (grew) saveExpanded();
  }
  paint();
}

/** 이 응답이 노드 갈래를 그릴 수 있는가. 서버는 `nodes=1` 로 물었을 때만 싣는다. */
const hasNodes = (t: NavTreeRes | null): boolean =>
  !!t?.domains?.length && t.domains.some((g) => Array.isArray(g.nodes));

/** 레일이 그리는 개수 — 응답의 `counts.kinds` 하나에서 온다(제목을 싣지 않는 집계).
 *  종을 갈아 끼워도 이 값은 안 바뀌므로 마지막으로 받은 것을 들고 있는다. */
let kindCounts: Record<string, number> = {};

/** Agent 레일은 주제 필터다. 역할은 `vinc/agent/roles` 아래이고, Default Agents
 *  넷은 뿌리에도 선다. 둘 다 남겨야 한다 — 역할 id 접두 `concept:agent-` 가
 *  뿌리를 구한다. */
function isAgentRow(domain: string, nodes?: { id: string }[]): boolean {
  if (/(^|\/)agent(\/|$)/.test(domain)) return true;
  return (nodes || []).some((n) => n.id.startsWith("concept:agent-"));
}
function filterAgentTree(t: NavTreeRes): NavTreeRes {
  const domains = (t.domains || []).filter((g) => isAgentRow(g.domain, g.nodes));
  return { ...t, domains };
}

/** 역할 id 접두. 로스터 개념 `concept:agent-orchestration` 은 접두가 같아
 *  역할이 아니다(`vinc_mcp.roles.is_role_id`). */
const AGENT_ID_PREFIX = "concept:agent-";
const AGENT_ROSTER_CONCEPT = "concept:agent-orchestration";
const isRoleId = (id: string): boolean =>
  id.startsWith(AGENT_ID_PREFIX) && id !== AGENT_ROSTER_CONCEPT;

/** 여섯 팀의 화면 순서. 이름은 `props.team` 정본이고, 순서는 로스터 v2 가
 *  비준한 그대로다. 여기 일곱 번째 팀을 만들지 않는다.
 *  근거: [[decision:roster-v2-ratified-2026-09-16]]
 *  [[decision:vin88-agent-click-opens-roster-list-graph-scopes-agent-domains-2026-09-22]] */
const AGENT_TEAM_ORDER = [
  "Program Office",
  "Product Engineering",
  "Design",
  "Content and Marketing",
  "Quality and Security",
  "Platform and Release",
] as const;

type AgentRel = { from: string[]; then: string[]; beside: string[] };
type AgentRole = {
  id: string; domain: string; title: string; team: string;
  teamRole: string; rel: AgentRel;
};

/** `/graph` 가 노드에 싣는 props. 클라 GraphNode 타입은 이 열을 아직 안 적는다. */
type GraphRoleNode = {
  id: string; label?: string; domain?: string; kind?: string;
  props?: Record<string, unknown>;
};

let agentRoster: AgentRole[] | null = null;

function strProp(props: Record<string, unknown> | undefined, key: string): string {
  const v = props?.[key];
  return typeof v === "string" ? v.trim() : "";
}

function roleTitle(n: GraphRoleNode): string {
  const job = strProp(n.props, "job_title");
  if (job) return job;
  return String(n.label || n.id).replace(/^Agent role:\s*/i, "");
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
    if (src === dst || !isRoleId(src) || !isRoleId(dst)) continue;
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

function teamRank(team: string): number {
  const i = (AGENT_TEAM_ORDER as readonly string[]).indexOf(team);
  return i < 0 ? AGENT_TEAM_ORDER.length : i;
}
function teamRoleRank(role: string): number {
  if (role === "lead") return 0;
  if (role === "coordinator") return 1;
  return 2;
}

function buildAgentRoster(g: { nodes?: GraphRoleNode[]; edges?: GraphEdge[] } | null
                          | undefined): AgentRole[] {
  const nodes = (g?.nodes || []).filter((n) => isRoleId(n.id));
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const edges = g?.edges || [];
  const rows: AgentRole[] = nodes.map((n) => ({
    id: n.id,
    domain: n.domain || "vinc/agent/roles",
    title: roleTitle(n),
    team: strProp(n.props, "team"),
    teamRole: strProp(n.props, "team_role"),
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

function relLine(label: string, names: string[]): string {
  if (!names.length) return "";
  return `<span class="lnb-agent-rel">${esc(label)} · ${esc(names.join(" · "))}</span>`;
}

function paintAgentList(roles: AgentRole[]): string {
  if (!roles.length) {
    return `<p class="lnb-none hint">${esc(COPY.ui.lnb.noNodes)}</p>`;
  }
  const L = COPY.ui.lnb;
  const parts: string[] = [`<div class="lnb-tree lnb-agent-list" role="tree">`];
  let team = "\0";
  for (const r of roles) {
    const t = r.team || L.agentNoTeam;
    if (t !== team) {
      team = t;
      parts.push(`<p class="lnb-agent-team">${esc(t)}</p>`);
    }
    const on = active.nodeId === r.id;
    const lead = r.teamRole === "lead" ? ` · ${esc(L.agentLead)}` : "";
    parts.push(`<button class="lnb-doc lnb-node lnb-agent-role${on ? " on" : ""}"
      role="treeitem" aria-level="2"${on ? ' aria-selected="true"' : ""}
      data-nodeid="${esc(r.id)}" data-dom="vinc/agent"
      data-tip="${esc(L.nodeTip)}">
      <span class="lnb-dtitle">${esc(r.title)}${lead}</span>
      ${relLine(L.agentFrom, r.rel.from)}
      ${relLine(L.agentThen, r.rel.then)}
      ${relLine(L.agentBeside, r.rel.beside)}
    </button>`);
  }
  parts.push(`</div>`);
  return parts.join("");
}

/** 다시 읽기의 세대 번호. 종을 빠르게 갈아 끼우면 앞 종의 응답이 늦게 와서 뒤 종의
 *  트리를 덮는다 — 가장 최근 호출의 응답만 상태에 쓴다. */
let refreshSeq = 0;

/** 문서가 늘거나 주제가 바뀐 뒤 다시 읽는다(가져오기·추출·이름 변경 후). */
export async function refreshLnb(): Promise<void> {
  const seq = ++refreshSeq;
  const stale = () => seq !== refreshSeq;
  loading = true; failed = false;
  paint();
  try {
    const r = rail();
    if (r?.offTopic) {
      // Entity·Bridge 는 주제 트리 계약 밖이다. 없는 종을 물으면 서버가
      // unknown kind 로 실패하고, 그때 레일이 "종이 없어졌다"로 읽힌다.
      tree = { executed: true, domains: [], unfiled: [] };
      agentRoster = null;
      failed = false;
      // 레일 개수는 nav-tree 응답에만 실려 온다. 이 종을 고른 채 새로 열면 그 조회를
      // 한 번도 안 하므로, 제목 없이 개수만 받는 호출로 채운다(노드 목록을 안 실어 가볍다).
      // 못 받으면 레일은 지난 값 그대로 둔다.
      if (!Object.keys(kindCounts).length) {
        try {
          const c = await api.navTree(cfg.owner);
          if (stale()) return;
          if (c?.counts?.kinds) kindCounts = c.counts.kinds;
        } catch { /* 개수만 못 받았다 */ }
      }
      paintRail();
    } else {
      // 늘 노드까지 받는다 — 층 2 가 문서를 **노드 아래** 세우므로 노드 목록이
      // 없으면 그릴 것이 없다. 대신 **한 종만** 받는다: 넷을 한꺼번에 실으면 응답이
      // 그래프 전량이 된다(기록이 가장 큰 종이고 웹모드에서는 계정 프록시를 건넌다).
      // 종을 안 골랐으면 `kind` 를 안 싣는다 — 서버는 그때 네 종을 모두 준다.
      // 응답은 지역에 받고, 세대가 그대로일 때만 상태에 쓴다(늦게 온 앞 종의 응답이
      // 뒤 종의 트리를 덮지 않게).
      let next = await api.navTree(cfg.owner, { nodes: true, kind: r?.kind });
      if (stale()) return;
      if (next?.executed && r?.domain) next = filterAgentTree(next);
      // Agent 목록의 팀·관계는 `/graph` 의 역할 노드와 그 사이 엣지에서 접는다.
      // nav-tree 는 제목·주제만 주고 props.team 과 works_with 를 안 싣는다.
      // 그래프를 못 받으면 주제 트리로 강등한다(빈 목록을 "역할이 없다"로 읽히게 두지 않는다).
      let roster: AgentRole[] | null = null;
      if (r?.domain && next?.executed) {
        try {
          const rows = buildAgentRoster(
            await api.graph(cfg.owner, { domains: ["vinc/agent"] }));
          // 접은 역할이 없으면 주제 트리로 강등한다. 빈 배열을 그리면
          // 트리에 있던 역할이 "없다"로 읽힌다.
          roster = rows.length ? rows : null;
        } catch { roster = null; }
        if (stale()) return;
      }
      tree = next;
      failed = !tree?.executed;
      agentRoster = roster;
    }
    // 개수는 이 응답에 함께 온다. 못 받았으면 지난 값을 지우지 않는다 — 레일의
    // 숫자가 조회 실패마다 사라지면 그것이 곧 "종이 없어졌다"로 읽힌다.
    if (tree?.counts?.kinds) kindCounts = tree.counts.kinds;
    // 개수가 방금 왔으므로 레일을 다시 칠한다. 첫 마운트 때 레일이 먼저 그려지고
    // 조회가 뒤따르므로, 이 한 줄이 없으면 레일의 숫자가 **첫 화면에서 늘 0** 이다
    // (실측 2026-09-08: 마운트 직후 툴팁이 "노드 0개"였다).
    paintRail();
  } catch {
    if (stale()) return;
    tree = null; failed = true; agentRoster = null;
  }
  loading = false;
  paint();
}

/** Agent 를 고르면 지도는 `vinc/agent` 하위 트리다(역할 도메인 전량).
 *  상세는 목록에서 역할을 고른 뒤에만 연다. 근거: 소유자 지시 2026-09-22.
 *  **토글이 아니라 건다**(`set`) — 이미 vinc/agent 로 좁혀져 있을 때 Agent 를 다시
 *  고르면 토글 피커는 스코프를 벗겨 버린다. */
function scopeAgentGraph(): void {
  pickTopic?.("vinc/agent", false, true);
}

/** Agent 를 떠나면 그 주제 스코프를 벗긴다. pickTopic 은 해시를 안 바꾸므로
 *  `#view` 가 이미 자리여도 hashchange 를 한 번 보내 L0 로 되돌린다. */
function unscopeAgentGraph(): void {
  const agentScope = [...picked].some((d) => d === "vinc/agent" || d.startsWith("vinc/agent/"));
  if (!agentScope) return;
  const here = location.hash.replace(/^#/, "");
  if (here === "view" || here.startsWith("view/")) {
    if (here !== "view") location.hash = "#view";
    else window.dispatchEvent(new HashChangeEvent("hashchange"));
  }
}

/** 종 전환 — **늘 다시 읽는다.** 응답이 한 종만 싣기 때문이고, 캐시를 두지 않는
 *  것이 그 계약을 지키는 방법이다(종마다 트리를 들고 있으면 결국 전량을 메모리에
 *  갖는 것이고, 그러면 응답을 좁힌 이유가 사라진다). */
function setRailKey(next: string): void {
  // 빈 값(= 전체)도 유효한 목적지다. 그것을 거르면 홈이 아무 일도 못 한다.
  if (railKey === next || (next !== "" && !RAIL_KEYS.includes(next))) return;
  const prev = railKey;
  railKey = next;
  localStorage.setItem(KEY_KIND, next);
  // Agent 목록은 역할을 고르기 전엔 줄이 켜지지 않는다. 직전 표면의 노드
  // 초점이 그대로 남으면 다른 종이 선택된 것처럼 보인다.
  if (next === "agent") active = { domain: "vinc/agent" };
  paintRail();
  // 지도에도 같은 종을 건다 — 왼쪽에서 고른 것이 오른쪽에서 안 보이면 두 화면이
  // 다른 것을 보고 있는 셈이다(발주자 지적 2026-09-09).
  kindLens?.(lnbKind());
  void refreshLnb().then(() => {
    // 그 사이 다른 종을 골랐으면 이 전환의 뒷일은 없다. 늦게 끝난 Agent 전환이
    // 뒤에 고른 종 위에 Agent 스코프를 걸면 안 된다.
    if (railKey !== next) return;
    if (next === "agent") scopeAgentGraph();
    else if (prev === "agent") unscopeAgentGraph();
  });
}

/** 층 1 — 종 레일. 트리와 **다른 요소**라 트리를 다시 그려도 여기는 그대로다
 *  (레일은 상주 크롬이고 목록은 조회 결과다). */
function paintRail(): void {
  // ⚠ 레일 **전체**가 아니라 가운데 칸만 칠한다(2026-09-09). 레일에는 손잡이와 앱
  // 버튼 넷이 함께 사는데, 여기서 `#lnb-rail` 을 덮으면 그 둘이 종을 다시 그릴
  // 때마다 사라진다(그리고 그때 붙인 리스너도 함께 죽는다).
  const el = $("rail-kinds");
  if (!el) return;
  const names = COPY.ui.lnb.kinds;
  const here = location.hash.replace(/^#/, "").split("/")[0] || "view";
  // **홈은 레일에 산다**(발주자 지시 2026-09-09). 트리에 있던 동안 그 목록은
  // "문서 목록 + 내비 한 줄"이라 두 성질이 섞여 있었다 — 트리는 이제 문서만 든다.
  // 자리가 맨 위인 이유: 지도 전체로 돌아가는 것은 종을 고르는 일보다 상위다.
  // 홈이 켜진 상태 = **지도 위에 있으면서 아무 종도 안 고른 것**이다. 여태
  // 표면만 봤기 때문에 종을 고른 뒤에도 홈이 함께 켜져 있었다 — 레일에 켜진 것이
  // 둘이면 무엇이 지금 보이는 것인지 화면이 말하지 못한다.
  const atAll = here === "view" && !railKey;
  const home = `<a class="rail-btn rail-home${atAll ? " on" : ""}"
      href="#view" data-rail="" aria-current="${atAll ? "page" : "false"}"
      data-tip="${esc(COPY.ui.topbar.home)}">
      <span class="lnb-mk" aria-hidden="true">${icon("graph", 18)}</span>
      <span class="rail-name">${esc(COPY.ui.topbar.home)}</span>
    </a>`;
  const railBtn = (r: RailItem): string => {
    const on = r.key === railKey;
    const name = names[r.key] || r.key;
    // 개수는 그 **종**의 수다. Agent 는 주제라, Entity·Bridge 는 주제 집계 밖이라
    // 숫자를 내지 않는다 — 셀 수 없는 것에 그럴싸한 숫자를 붙이지 않는다.
    const cnt = (r.dest || r.offTopic) ? 0 : (kindCounts[r.key] || 0);
    const mark = r.icon
      ? `<span class="lnb-mk" aria-hidden="true">${icon(r.icon, 18)}</span>`
      : kindMark3d(r.mark, r.key, 20);
    const tip = (r.dest || r.offTopic) ? name : COPY.ui.lnb.kindTip(name, cnt);
    return `<button class="rail-btn${r.dest ? " rail-dest" : ""}" type="button"
      data-rail="${esc(r.key)}" aria-pressed="${on}" aria-label="${esc(name)}"
      data-tip="${esc(tip)}">
      ${mark}<span class="rail-name">${esc(names[r.key + ".short"] || name)}${
      cnt ? `<span class="rail-n">${cnt}</span>` : ""}</span>
    </button>`;
  };
  // 구분선은 홈·Agent(목적지)와 종 묶음 사이다. Agent 를 선 아래로 두던 배치는
  // 2026-09-21 scope-back 이 덮는다.
  const destHtml = RAIL.filter((r) => r.dest).map(railBtn).join("");
  const kindItems = RAIL.filter((r) => !r.dest);
  let kindHtml = "";
  for (let i = 0; i < kindItems.length; ) {
    const g = kindItems[i].group;
    if (!g) { kindHtml += railBtn(kindItems[i]); i += 1; continue; }
    const chunk: RailItem[] = [];
    while (i < kindItems.length && kindItems[i].group === g) {
      chunk.push(kindItems[i]); i += 1;
    }
    kindHtml += `<div class="rail-group" data-group="${g}">${chunk.map(railBtn).join("")}</div>`;
  }
  el.innerHTML = home + destHtml + `<hr class="rail-sep">` + kindHtml
    + `<span class="rail-fill"></span>`;
  // 설정 버튼은 `#rail-kinds` **밖**(레일 바닥)에 산다. 종을 다시 그려도 그 자리는
  // 그대로다. 뱃지만 여기서 다시 묻는다 — 제안 적체가 레일 바닥에 남아 있어야 한다.
  void import("./conventions").then((m) => m.refreshBadge());
  // ⚠ 레일에 접기 버튼을 두었다가 뺐다(2026-09-09). 접는 대상이 레일까지이므로
  // 손잡이가 그 안에 있으면 함께 사라진다 — 상단 바 왼쪽 끝으로 갔다.
}

/* ⚠ `navRows()` 와 `surfaceHash()` 는 **사라졌다**(2026-09-09). 홈 한 줄이
   레일로 갔으므로 이 열은 문서만 든다 — 내비를 그릴 것이 없다. */

function docRow(d: { doc_id: string; title: string; node_id?: string; depth?: number },
                domain = ""): string {
  const depth = Math.min(Math.max(Number(d.depth ?? 0), 0), 8);   // 들여쓰기 상한 — 8단 넘으면 글자가 사라진다
  const on = d.node_id && active.nodeId === d.node_id;
  const tip = d.node_id ? COPY.ui.lnb.depthTip(Number(d.depth ?? 0)) : COPY.ui.lnb.editOnly;
  // 깊이는 세 갈래로 말한다. 여태 들여쓰기 하나뿐이라 1단이 10.88px 밖에 안 벌어져
  // 종속이 거의 안 보였고, 보조기술에는 8단 트리가 평평한 목록으로 들렸다.
  //   --lvl  : 들여쓰기(폭이 232px 라 단차를 무한정 키울 수 없다)
  //   --ink  : 잉크 3단(0=본문 · 1=흐림 · 2+=더 흐림). 색은 2단까지만 — 계속 흐리면
  //            AA 하한에 먼저 닿는다. 그 아래는 들여쓰기와 도트가 받는다.
  //   aria-level : 시각 밖 통로. 주제가 1단이므로 문서는 depth+2 다.
  // 그 위에 --tint 가 하나 더 얹히는데 그것은 깊이가 아니라 **주제**를 말하고, 글자가
  // 아니라 도트에만 앉는다 — 잉크 3단과 자리를 다투지 않게 하려는 것이다.
  const ink = Math.min(depth, 2);
  return `<button class="lnb-doc${on ? " on" : ""}" role="treeitem"
    aria-level="${depth + 2}"${on ? ' aria-selected="true"' : ""} data-doc="${esc(d.doc_id)}"
    data-node="${esc(d.node_id || "")}" data-dom="${esc(domain)}"
    style="--lvl:${depth};--ink:${ink}${tintStyle(domain)}" data-tip="${esc(tip)}">
    <span class="lnb-dot" aria-hidden="true"></span><span class="lnb-dtitle">${esc(d.title || d.doc_id)}</span>
  </button>`;
}

/** 노드 한 줄. 문서 줄과 **같은 깊이 표현**을 쓴다(들여쓰기 · 잉크 · aria-level) —
 *  두 갈래가 같은 계층을 그리는데 다르게 생기면 같은 트리로 안 읽힌다. 다른 것은
 *  하나뿐이다: 열 문서가 없으므로 누르면 지도에서 그 노드로 간다. */
function nodeRow(nd: NavNode, domain = ""): string {
  const depth = Math.min(Math.max(Number(nd.depth ?? 0), 0), 8);
  const on = active.nodeId === nd.id;
  const ink = Math.min(depth, 2);
  // 표식이 **종**을 말한다(도트가 아니라). 도트는 문서의 것으로 남는다 — 그래서
  // "이 줄이 노드인가 문서인가"를 색이나 위치가 아니라 모양이 먼저 말한다.
  return `<button class="lnb-doc lnb-node${on ? " on" : ""}" role="treeitem"
    aria-level="${depth + 2}"${on ? ' aria-selected="true"' : ""} data-nodeid="${esc(nd.id)}"
    data-dom="${esc(domain)}" style="--lvl:${depth};--ink:${ink}${tintStyle(domain)}"
    data-tip="${esc(COPY.ui.lnb.nodeTip)}">
    ${kindMark(nd.kind || "concept", 11)}<span class="lnb-dtitle">${esc(nd.title || nd.id)}</span>
  </button>`;
}

/** 주제 트리의 한 마디. `path` 가 `/nav-tree` 의 주제 문자열 통짜와 같은 값이라
 *  선택·활성 키는 그대로다 — 중첩은 **화면의 일**이고 계약은 안 바뀐다. */
type DomNode = { seg: string; path: string; group?: NavGroup; kids: DomNode[] };

/** 슬래시가 곧 계층이다(GRAPH.md: `vinc/design` 은 `vinc` 아래에 선다). 저장소는
 *  그 계층을 이미 노드로 갖고 있는데(`domain:vinc` -PARENT_OF-> `domain:vinc/design`)
 *  좌측 트리만 문자열을 통짜로 묶어 평평하게 보여 주고 있었다. 서버가 주는 것은
 *  그대로 두고 여기서 쪼갠다 — 계약을 안 건드리는 것이 이 방식의 값이다.
 *
 *  중간 마디(`vinc`)는 그 자체로 주제일 수도 있고 아닐 수도 있다. 아니면 `group`
 *  이 없어 문서를 안 갖고 자식만 갖는다 — 그래도 행은 낸다(그 이름이 계층이다). */
function nestGroups(groups: NavGroup[]): DomNode[] {
  const root: DomNode = { seg: "", path: "", kids: [] };
  for (const g of groups) {
    const segs = g.domain.split("/").filter(Boolean);
    let cur = root;
    segs.forEach((seg, i) => {
      const path = segs.slice(0, i + 1).join("/");
      let next = cur.kids.find((k) => k.path === path);
      if (!next) { next = { seg, path, kids: [] }; cur.kids.push(next); }
      cur = next;
    });
    cur.group = g;
  }
  return root.kids;
}
const subDocs = (n: DomNode): number =>
  (n.group?.docs.length || 0) + n.kids.reduce((s, k) => s + subDocs(k), 0);
/** 주제 줄 끝의 숫자 = 그 아래 펼쳐질 **노드** 줄 수. 서버가 `count` 로 준다
 *  (`kind` 로 물었을 때만 오는 열이고, 그것이 곧 그 갈래의 줄 수라는 계약이다).
 *  문서는 노드 아래 매달리므로 이 숫자에 안 들어간다 — 세는 것과 그리는 것이
 *  같아야 한다는 규율은 **노드 줄에 대한 것**이다. */
const subCount = (n: DomNode): number =>
  (n.group?.count ?? n.group?.concepts ?? 0) + n.kids.reduce((s, k) => s + subCount(k), 0);
const subConcepts = (n: DomNode): number =>
  (n.group?.concepts || 0) + n.kids.reduce((s, k) => s + subConcepts(k), 0);

function groupRows(n: DomNode, level = 1): string {
  const open = expanded.has(n.path);
  const on = picked.has(n.path);
  // 주제 행도 treeitem 이다(여태 role 이 없어 트리에서 빠져 있었다). 그리고 자식
  // group 은 **항목의 형제가 아니라 안쪽**에 와야 한다 — 형제로 두면 보조기술이
  // 계층으로 읽지 않는다.
  const head = `<button class="lnb-group${on ? " on" : ""}" role="treeitem" aria-level="${level}"
      ${on ? 'aria-selected="true" ' : ""}data-group="${esc(n.path)}"
      aria-expanded="${open}" aria-pressed="${on}"
      data-tip="${esc(COPY.ui.lnb.conceptCount(subConcepts(n)))} · ${esc(COPY.ui.lnb.topicTip)}">
      <span class="lnb-caret${open ? " open" : ""}" aria-hidden="true">›</span>
      <span class="lnb-gname">${esc(n.seg)}</span>
      <span class="lnb-gcount">${subCount(n) || "·"}</span>
    </button>`;
  if (!open) return head;
  // 자식 가지가 먼저, 그다음 이 주제 자신의 문서. 순서를 뒤집으면 문서 목록 끝에
  // 가지가 붙어 "문서인 줄 알았던 것"이 사실 폴더인 상황이 된다.
  const kids = n.kids.map((k) => groupRows(k, level + 1)).join("");
  // **구조를 보여 준다**: 노드 한 줄, 그 아래 그 노드의 문서. 여태는 둘 중 하나만
  // 보여 줬고(칩이 배타였다), 그래서 "이 문서가 어느 개념의 것인가"가 화면에
  // 없었다. 조인 열은 응답에 이미 있다(`NavDoc.node_id` ↔ `NavNode.id`).
  const docs = n.group?.docs || [];
  const byNode = new Map<string, typeof docs>();
  for (const d of docs) {
    const k = d.node_id || "";
    const cur = byNode.get(k);
    if (cur) cur.push(d); else byNode.set(k, [d]);
  }
  const nodes = n.group?.nodes || [];
  const seen = new Set<string>();
  const rows = nodes.map((nd) => {
    seen.add(nd.id);
    const mine = byNode.get(nd.id) || [];
    // 문서의 들여쓰기는 **자기 노드보다 한 단**이다. 여태는 착지 개념의 깊이를
    // 그대로 물려받아, 문서 줄의 들여쓰기가 그 문서의 것이 아닌 값을 말했다.
    return nodeRow(nd, n.path)
      + mine.map((d) => docRow({ ...d, depth: (nd.depth || 0) + 1 }, n.path)).join("");
  }).join("");
  // 착지 노드가 이 종에 없는 문서 — **숨기지 않는다.** 종을 고른 것이 문서를 지우는
  // 일이 되면 목록을 신뢰할 수 없다. 주제 자리에 그대로 세우고 왜 거기 있는지 말한다.
  const orphans = docs.filter((d) => !d.node_id || !seen.has(d.node_id));
  const rest = orphans.length
    ? `<p class="lnb-none hint">${esc(COPY.ui.lnb.docsElsewhere)}</p>`
      + orphans.map((d) => docRow({ ...d, depth: 0 }, n.path)).join("")
    : "";
  const own = (rows + rest)
    // 이 종이 없는 주제 — 그렇다고 목록에서 지우면 이름을 바꾸거나 문서를 붙일
    // 진입점이 사라진다. 빈 채로 두되 빈 이유를 말한다.
    // (자식 가지가 있으면 비어 보이지 않으므로 그 문구를 내지 않는다.)
    || (n.kids.length ? "" : `<p class="lnb-none hint">${esc(COPY.ui.lnb.noNodes)}</p>`);
  return head + `<div class="lnb-docs" role="group">${kids}${own}</div>`;
}

/** 스크롤 밖의 머리 — 지금 몇 주제를 보고 있는지. 인자가 없으면 비운다(아직
 *  못 세었거나 셀 것이 없는 상태에서 `0` 을 내면 "주제가 없다"로 읽힌다). */
function paintHead(n?: number, label?: string): void {
  const h = $("lnb-head");
  if (!h) return;
  h.innerHTML = n === undefined ? ""
    : `<div class="lnb-sec"><span class="lnb-sec-label">${esc(label || COPY.ui.lnb.documents)}</span>`
      + `<span class="hint">${n || "·"}</span></div>`;
}

/** 주제 스코프 칩 — 고른 주제가 없으면 빈 문자열. */
function scopeChip(): string {
  if (!picked.size) return "";
  return `<div class="lnb-scope">
      <span>${esc(COPY.ui.lnb.scoped(picked.size))}</span>
      <button class="lnb-scope-clear" type="button">${esc(COPY.ui.lnb.scopedClear)}</button>
    </div>`;
}

function paint() {
  const body = $("lnb-body");
  if (!body) return;
  if (loading && !tree) {
    paintHead();
    body.innerHTML = `<p class="lnb-none hint">${esc(COPY.ui.lnb.loading)}</p>`;
    return;
  }
  if (failed) {
    paintHead();
    body.innerHTML = `<p class="lnb-none hint">${esc(COPY.ui.lnb.failed)}</p>`
      + `<button class="btn btn-quiet lnb-retry" id="lnb-retry">${esc(COPY.ui.lnb.retry)}</button>`;
    return;
  }
  if (rail()?.offTopic) {
    paintHead();
    // 트리는 없어도 주제 스코프는 살아 있을 수 있다. 칩을 빼면 걸린 스코프를
    // 볼 길도 풀 길도 없다.
    body.innerHTML = scopeChip()
      + `<p class="lnb-none hint">${esc(COPY.ui.lnb.offTopicKind)}</p>`;
    return;
  }
  // Agent 목적지는 주제 트리가 아니라 로스터 목록이다. 팀을 섹션으로 두고
  // 각 줄에 이미 있는 관계를 붙인다. 그래프 스코프 칩은 목적지가 이미
  // `vinc/agent` 이라 내지 않는다.
  if (rail()?.dest && agentRoster) {
    paintHead(agentRoster.length, COPY.ui.lnb.agents);
    body.innerHTML = paintAgentList(agentRoster);
    return;
  }
  const groups = tree?.domains || [];
  const unfiled = tree?.unfiled || [];
  // 색은 paint 마다 한 번 만든다(줄마다 만들면 같은 답을 n 번 낸다). 토글이 꺼져
  // 있으면 null 이고, 그때 줄에는 --tint 가 아예 안 실려 CSS 의 잉크가 그대로 쓰인다.
  tints = loadPrefs().domainTint
    ? tintMap(groups.map((g) => g.domain), themeMode()) : null;
  paintHead((tree?.domains || []).length);
  let html = "";
  // ③ 고른 것이 있으면 **말한다**. 켜진 배경만으로는 몇 개인지도, 어떻게 푸는지도
  //    안 보인다 — 홈 행이 전체로 돌아가는 길이지만 그렇게 읽히지 않는다.
  html += scopeChip();
  // 노드 갈래를 골랐는데 응답이 노드를 안 실어 왔다. 데스크탑에서는 사이드카가 앱과
  // 한 벌이라 날 수 없지만 **웹모드는 다르다** — 사이트가 배포한 앱과 허브의 사이드카가
  // 따로 올라가므로 앱이 먼저 새것일 수 있다. 그때 빈 트리를 그리면 "내 개념이
  // 사라졌다"로 읽힌다. 갈래는 사용자가 고른 대로 두고, 못 받았다고 말한다.
  // ⚠ 주제가 하나도 없을 때는 여기 오면 안 된다 — 그건 빈 그래프이지 못 받은 것이
  // 아니다. 아래 빈 상태 CTA 가 그 경우를 맡는다.
  if (groups.length > 0 && !hasNodes(tree)) {
    body.innerHTML = html
      + `<p class="lnb-none hint">${esc(COPY.ui.lnb.failed)}</p>`
      + `<button class="btn btn-quiet lnb-retry" id="lnb-retry">${esc(COPY.ui.lnb.retry)}</button>`;
    return;
  }
  if (!groups.length && !unfiled.length) {
    html += `<p class="lnb-none hint">${esc(
      rail()?.domain ? COPY.ui.lnb.noNodes : COPY.ui.lnb.empty)}</p>`;
    if (!rail()?.domain) {
      html += `<button class="btn btn-quiet lnb-retry" id="lnb-add">${esc(COPY.ui.lnb.emptyCta)}</button>`;
    }
  } else {
    const tops = nestGroups(groups);
    // 뿌리가 하나뿐이면(지금은 전부 `vinc/*` 다) 한 번은 펼쳐 둔다 — 안 그러면
    // 중첩이 정보를 더하는 대신 모든 것을 클릭 한 번 뒤로 밀기만 한다. 최초 1회만
    // 이라 접어 둔 사용자의 선택과 싸우지 않는다.
    if (!autoExpanded && tops.length === 1) { autoExpanded = true; expanded.add(tops[0].path); }
    html += `<div class="lnb-tree" role="tree">${tops.map((n) => groupRows(n)).join("")}`;
    if (unfiled.length) {
      const open = expanded.has("");
      html += `<button class="lnb-group" data-group="" aria-expanded="${open}"
          data-tip="${esc(COPY.ui.lnb.unfiledTip)}">
          <span class="lnb-caret${open ? " open" : ""}" aria-hidden="true">›</span>
          <span class="lnb-gname lnb-unfiled">${esc(COPY.ui.lnb.unfiled)}</span>
          <span class="lnb-gcount">${unfiled.length}</span></button>`;
      // 미분류는 주제가 없다 — 빈 문자열을 명시해 map 의 index 가 두 번째 인자로
      // 새어 들어가지 않게 한다(주제 자리에 0·1·2 가 박히는 고전적 함정).
      if (open) html += `<div class="lnb-docs" role="group">${
        unfiled.map((d) => docRow(d, "")).join("")}</div>`;
    }
    html += `</div>`;
  }
  body.innerHTML = html;
}

/** 캐럿을 **실제로 돌린다.** paint() 가 트리를 통째로 다시 그리므로 새 캐럿은 최종 각도로
 *  태어나고 전이는 시작점이 없다 — `.lnb-caret` 의 transition 이 여태 한 번도 돌지 않은
 *  이유. 방금 그린 캐럿을 이전 각도에 세워 한 프레임 확정한 뒤 놓아 준다(FLIP). */
function spinCaret(key: string, open: boolean): void {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const caret = $("lnb-body")?.querySelector<HTMLElement>(`.lnb-group[data-group="${CSS.escape(key)}"] > .lnb-caret`);
  if (!caret) return;
  caret.style.transition = "none";
  caret.style.transform = open ? "rotate(0deg)" : "rotate(90deg)";
  void caret.offsetWidth;                  // 시작 상태 확정 — 없으면 한 프레임에 건너뛴다
  caret.style.transition = "";
  caret.style.transform = "";
}

/** 문서 클릭 — **지도로 가고 문서를 연다**(둘 다, 한 클릭에). 여태 노드로만
 *  날아가서, 목록에서 문서를 골라도 본문은 두 번째 클릭이 있어야 나왔고 그 두 번째
 *  클릭조차 "그 노드의 발췌가 마침 이 문서일 때"만 성립했다. 착지 노드가 없는
 *  문서는 지도에 자리가 없으므로 본문만 연다. */
async function openDoc(docId: string, nodeId: string, domain?: string) {
  if (nodeId) {
    if (navigate) navigate(nodeId, domain, docId);
    else location.hash = `#view/trace/${encodeURIComponent(nodeId)}`;
    return;
  }
  // 개념과 아직 이어지지 않은 문서는 지도에 자리가 없다 — 없는 자리를 지어내는 대신
  // 원문을 연다(거기서 개념을 뽑으면 다음부터 트리의 제자리로 들어온다).
  const { openDocSheet } = await import("./doc-editor");
  openDocSheet({ owner: cfg.owner, docId, onSaved: () => void refreshLnb() });
}

/** 밖에서 색을 다시 굳히라고 부른다. 값은 안 받는다: 정본은 localStorage
 *  (`vinc.view.prefs`)와 `data-theme` 이고 paint 가 둘 다 다시 읽는다.
 *
 *  부르는 데 둘. ① 주제 색 토글(지도와 **같은 토글**이라 왼쪽 트리도 함께 따라간다)
 *  ② **테마 전환**(`applyTheme`) — `tintStyle` 이 싣는 것은 CSS 변수가 아니라 그때
 *  모드로 계산한 HEX 라, 다시 그리지 않으면 뒤집힌 화면에 반대 모드 색이 남는다. */
export function repaintLnb(): void { paint(); }

export function mountLnb() {
  if (wired) return;
  wired = true;
  const root = $("lnb");
  if (!root) return;
  expanded = loadExpanded();

  // **머리가 없다.** 여태 이 카드의 첫 52px 는 셰브론 하나를 담은 머리였고, 그만큼
  // 첫 줄(홈)을 아래로 밀었다(발주자 지적 2026-09-09, `수정항목/lnb.JPG`).
  // 접기 손잡이는 상단 바로 갔으므로 여기 담을 것이 없다.
  // ⚠ **덮지 않고 잇는다**(2026-09-09). 이 카드의 첫 칸은 관점 세그이고 그것은
  // `index.html` 에 있다 — 대입으로 쓰면 세그가 지워진다.
  // 머리는 **스크롤 밖**이다(발주자 지적 2026-09-10: 스크롤 시작 지점이 더 아래여야
  // 한다). 여태 `문서 N` 줄이 목록과 한 상자에 있어 굴리면 함께 사라졌다 — 지금 무엇을
  // 보고 있는지 말하는 줄이 스크롤과 함께 없어지면 그 줄의 일이 성립하지 않는다.
  // 렌즈 패널·시트와 같은 배치다(머리 고정 · 몸만 스크롤).
  root.insertAdjacentHTML("beforeend",
    `<div class="lnb-head" id="lnb-head"></div><div class="lnb-body" id="lnb-body"></div>`);
  const shellToggle = $("tb-shell-toggle");
  if (shellToggle) {
    shellToggle.innerHTML = icon("sidebar", 18);
    shellToggle.addEventListener("click", () => setOpen(!isOpen()));
  }
  setOpen(isOpen());


  // 레일은 자기 배선을 갖는다 — 트리 본문과 다른 요소이므로.
  paintRail();
  const settingsBtn = $("rail-settings");
  if (settingsBtn) {
    settingsBtn.setAttribute("aria-label", COPY.ui.topbar.settings);
    settingsBtn.setAttribute("data-tip", COPY.ui.topbar.settings);
    if (!settingsBtn.querySelector(".icon")) {
      settingsBtn.insertAdjacentHTML("afterbegin", icon("settings", 18));
    }
    if (!settingsBtn.querySelector(".rail-name")) {
      settingsBtn.insertAdjacentHTML("beforeend",
        `<span class="rail-name">${esc(COPY.ui.topbar.settings)}</span>`);
    }
  }
  $("lnb-rail")?.addEventListener("click", (e) => {
    const b = (e.target as HTMLElement).closest<HTMLElement>(".rail-btn");
    if (b?.id === "rail-settings") {
      void import("./surfaces/settings").then((m) => m.openSettings("general", undefined, true));
      return;
    }
    // ⚠ `if (b?.dataset.rail)` 이면 홈의 빈 값이 falsy 라 걸러진다 — 있는지를 묻는다.
    if (b?.dataset.rail !== undefined) setRailKey(b.dataset.rail);
  });

  const body = $("lnb-body")!;
  body.addEventListener("click", (e) => {
    const t = e.target as HTMLElement;

    // 노드 줄이 문서 줄의 class 를 함께 쓰므로(같은 생김새) **먼저** 가른다.
    const nd = t.closest<HTMLElement>(".lnb-node");
    if (nd) {
      const id = nd.dataset.nodeid || "";
      if (!id) return;
      if (navigate) navigate(id, nd.dataset.dom || "");
      else location.hash = `#view/trace/${encodeURIComponent(id)}`;
      active = { domain: nd.dataset.dom || "", nodeId: id };
      paint();
      return;
    }
    const doc = t.closest<HTMLElement>(".lnb-doc");
    if (doc) {
      void openDoc(doc.dataset.doc || "", doc.dataset.node || "", doc.dataset.dom || "");
      return;
    }
    const group = t.closest<HTMLElement>(".lnb-group");
    if (group) {
      const key = group.dataset.group ?? "";
      // 캐럿은 **접고 펴기**, 이름은 **주제 고르기**다. 여태 어디를 눌러도 접고
      // 펴기뿐이라, 주제 트리가 지도를 좁힐 것처럼 생겨서는 아무것도 안 했다.
      // 미분류 묶음(빈 key)은 실재하는 주제가 아니라 고를 수 없다 — 접고 펴기만.
      const onCaret = !!t.closest(".lnb-caret");
      if (!onCaret && key && pickTopic) {
        pickTopic(key, e.ctrlKey || e.metaKey);
        return;
      }
      if (expanded.has(key)) expanded.delete(key); else expanded.add(key);
      saveExpanded();
      paint();
      spinCaret(key, expanded.has(key));
      return;
    }
    if (t.closest(".lnb-scope-clear")) {
      // 고른 것을 전부 뺀다 = 전체(L0). `pickTopic` 에 같은 경로를 다시 넘기면
      // 토글이라 하나씩 꺼야 하므로, 첫 번째를 비-add 로 두 번 눌러 비우는 대신
      // 홈 해시로 간다 — 그것이 이 앱에서 "전체"의 정본 주소다.
      location.hash = "#view";
      return;
    }
    if (t.closest("#lnb-retry")) { void refreshLnb(); return; }
    // 트리가 비었을 때의 CTA — 쓸지 가져올지 아직 모르므로 Tools 를 연다(D71).
    if (t.closest("#lnb-add")) {
      void import("./surfaces/settings").then((m) => m.openSettings("data"));
      return;
    }
  });
  // 주제 우클릭 = 이름 변경(S2). L0 주제 표의 `⋯` 가 하던 일이 여기로 왔다 —
  // 표가 사라졌으므로 이 경로가 없으면 이름을 바꿀 길이 없어진다.
  body.addEventListener("contextmenu", (e) => {
    const group = (e.target as HTMLElement).closest<HTMLElement>(".lnb-group");
    const domain = group?.dataset.group;
    if (!domain) return;      // 미분류 묶음은 실재하는 주제가 아니라 이름이 없다
    e.preventDefault();
    void domainMenu(domain, e as MouseEvent);
  });

  // 표면이 바뀌면 내비 활성 줄도 따라간다(트리는 그대로 — 목록은 표면과 무관하다).
  window.addEventListener("hashchange", paint);
  void refreshLnb();
}

/** 주제 이름 변경 / 미분류로 내리기(S2). 폐지된 L0 주제 표의 `⋯` 가 하던 일 그대로 —
 *  전역 Domain 노드는 건드리지 않고 내 노드의 domain 문자열만 바뀐다. */
async function domainMenu(domain: string, ev: MouseEvent) {
  const { openContextMenu, promptText } = await import("./components");
  openContextMenu(ev.clientX, ev.clientY, [{
    label: COPY.ui.domains.rename,
    run: () => void (async () => {
      const next = await promptText(COPY.ui.domains.renameAsk(domain), domain,
        COPY.ui.domains.rename, COPY.ui.viewx.delCancel, COPY.ui.domains.unassign);
      if (next === null || next === domain) return;
      try {
        const r: any = await api.domainRename(cfg.owner, domain, next);
        if (!r.executed) throw new Error(r.error || "rename failed");
      } catch { return; }     // 실패는 조용히 — 목록은 그대로다(바뀐 것 없음)
      if (active.domain === domain) active.domain = next || undefined;
      await refreshLnb();
      // 열려 있는 지도의 주제 라벨도 같이 갈아야 한다. 표면에게 다시 그리라고
      // 말할 훅이 없으므로(내비게이터는 착지 전용) 해시 이벤트로 깨운다.
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    })(),
  }]);
}
