import { api, cfg, isCommunitySpace, remote, spaceKey } from "./api";
import type { GraphEdge, GraphNode, NavGroup, NavTreeRes } from "./api";
import { COPY } from "./copy";
import { docTitle } from "./doc-title";
import { icon } from "./icons";
import { applyDefaults, bridgeKey, foldsOwnRows, ownKey } from "./lnb-tree";
import type { ThemeMode } from "./theme";
import { topicRingIcon } from "./view-tint";
import { addTopics, topicTint, TOPIC_TINTS_EVENT } from "./topic-tint-table";
import { esc } from "./components";
import { loaderRows } from "./vinc-loaders.js";

/** 좌측 문서 트리 (DEC D69) — 셸의 LNB 한 장.
 *
 *  여기 사는 것: 맨 위 줄(홈 · Agent) · 주제 트리(주제 → 그 주제의 문서) · 바닥의
 *  설정. 층이 둘이던 시절의 종 레일(개념·결정·기록…)은 2026-10-22 에 걷었다 — 문서를
 *  다루는 자리에서 노드 종을 고르는 일은 그래프의 범례가 맡는다.
 *  근거: [[decision:the-desktop-shell-is-documents-first]]
 *  D56 이 상단 바 하나로 몰아넣었던 것을 되돌린다. 되돌리는 이유는 그때와 전제가
 *  다르기 때문이다 — 이 사이드바가 싣는 것은 "표면 5개로 가는 내비"(D26 이 아이콘
 *  레일로 충분하다고 판정한 것)가 아니라 **문서 탐색기**다. Obsidian 으로 치면
 *  리본이 아니라 파일 목록 쪽이고, 그건 아이콘 레일로 대체되지 않는다.
 *
 *  **문서 정렬은 개념 계층 깊이순**이다(`/nav-tree`). 얕은 개념(선행·원본)의 문서가
 *  위, 깊은 개념(파생)의 문서가 아래로 들여쓰기된다. 최근 수정순이 아닌 이유: 목록의
 *  일이 "무엇이 새로운가"가 아니라 "이 주제가 어떻게 짜여 있는가"이기 때문이다.
 *  "무엇이 새로운가"는 문서 홈의 목록이 맡는다(surfaces/home.ts).
 *
 *  **표면과의 계약은 훅뿐이다**(상단 바와 같은 태도). 문서 홈은 문서 열기를
 *  (setLnbDocOpener), 그래프는 노드 착지를(setLnbNavigator) 넘기고, 둘 다 주제 고르기를
 *  넘긴다(setLnbTopicPicker). 떠나면 지운다. 훅이 없으면 문서 클릭은 해시 착지로
 *  강등된다 — 눌러도 아무 일 없는 줄을 만들지 않는다. */

/** 트리는 그 문서가 **어느 주제 밑에 있는지 이미 안다** — 그 정보를 함께 넘긴다.
 *  받는 쪽(합계 뷰)은 개념 마크가 없어서 그래프 노드로는 주제를 되짚을 수 없다. */
type Navigator = (nodeId: string, domain?: string, docId?: string) => void;
/** 주제를 고른다. `add` 면 지금 고른 것에 **더한다**(Ctrl·⌘ 클릭).
 *  화면이 즉시 따라간다 — 고르는 것과 보는 것이 어긋나는 중간 상태를 두지 않는다. */
/** `set` 일 때만 `path` 가 여럿일 수 있다(Agent: 역할 주제 둘). */
type TopicPicker = (path: string | string[], add: boolean, set?: boolean) => void;
/** `set` 이 참이면 토글하지 않고 그 주제 하나로 **건다**(이미 그것뿐이면 그대로 둔다).
 *  Agent 레일이 쓴다 — 토글이면 이미 걸린 스코프를 Agent 를 고르는 손이 벗긴다. */

const KEY_OPEN = "vinc.shell.lnb";        // 열림 상태(접기는 사용자 소유)
// 펼친 주제 목록을 담던 옛 키. 이제 저장하지 않으므로 켤 때 한 번 지우기만 한다.
// 제거 조건: 0.9.8 태그 뒤. 이 키를 쓴 마지막 판은 0.9.3 이고, 남은 값은 아무도 읽지 않아 해가 없다.
const KEY_EXPANDED = "vinc.shell.lnb.groups";
const KEY_KIND = "vinc.shell.lnb.kind";   // 맨 위 줄이 고른 것(홈 = 빈 값 · Agent)

let navigate: Navigator | null = null;
/** 문서 홈이 넘기는 문서 열기. 있으면 문서 줄은 착지 노드와 무관하게 이것으로 연다. */
let openDocHere: ((docId: string, domain?: string) => void) | null = null;
let pickTopic: TopicPicker | null = null;
let tree: NavTreeRes | null = null;
let loading = false;
let failed = false;
let active: { domain?: string; nodeId?: string; docId?: string } = {};
/** 지금 스코프가 걸고 있는 주제들 — 트리 줄의 선택 표시가 이걸 읽는다. */
let picked = new Set<string>();
/** 펼친 주제(와 `own:` 접두의 항목 묶음). **저장하지 않는다**: 앱을 켤 때마다 기본
 *  펼침(`lnb-tree.ts`)에서 시작한다. `seenDefault` 는 기본을 이미 한 번 받은 경로다 —
 *  세션 안에서 사용자가 접은 주제를 다시 그릴 때마다 되살리지 않으려는 것이다. */
let expanded = new Set<string>();
const seenDefault = new Set<string>();

/** 주제 색(보석색, 늘 켜짐)은 앱 전체의 표 하나(`topic-tint-table`)에서 읽는다. 트리는 자기가 그리는 주제를 그 표에 더하고,
 *  표가 바뀌면(다른 표면이 주제를 더함) 다시 그린다. 그래서 같은 주제가 트리 · 홈 목록 · 그래프에서 같은 색이다. */
const themeMode = (): ThemeMode =>
  document.documentElement.dataset.theme === "dark" ? "dark" : "light";
/** 트리가 표에 주제를 더하거나 알림을 내는 동안에는 그 알림으로 다시 paint 하지 않는다(트리는 곧 새 표로 그린다). */
let painting = false;
/** 지금 가진 트리 · 로스터를 읽어 온 스페이스. 다른 스페이스에서 읽은 주제는 지금 스페이스의 표에 넣지 않는다. */
let dataSpace = "";
/** 트리의 주제를 표에 더한다(지금 스페이스에서 읽은 것만). */
function feedTints(domains: string[]): void {
  if (dataSpace !== spaceKey()) return;
  painting = true;
  try { addTopics(dataSpace, domains); } finally { painting = false; }
}
/** 줄의 style 에 얹을 조각. ⚠ 깊이 잉크는 CSS 가 **부분문자열**로 읽는다
 *  (app.css 의 `[style*="--ink:0"]`) — `--ink:N` 리터럴을 깨지 않도록 **뒤에** 붙인다. */
const tintStyle = (domain: string): string => {
  const c = topicTint(domain, themeMode());
  return c ? `;--tint:${c}` : "";
};
/** 사이드바가 지금 칠하는 주제 색(그 주제 줄의 고리 · 점과 같은 값, 뿌리와 표에 없는 주제는 ""). 홈 문서 목록이 읽는다.
 *  표가 바뀌거나 트리를 다시 그리면(테마 전환 포함) `vinc:lnb-tints` 가 난다. */
export function lnbTopicTint(domain: string): string { return topicTint(domain, themeMode()); }
// 다른 표면이 표에 주제를 더하면 트리를 다시 칠한다(그 주제들 때문에 순위가 옮겨 간 색이 있을 수 있다).
window.addEventListener(TOPIC_TINTS_EVENT, () => { if (!painting) paint(); });

/** 새 역할의 기본 주제와 기존 호환 주제. 목록 발견은 이 주제로 제한하지 않는다. */
export const AGENT_TOPICS = ["agent", "vinc/agent"] as const;

/** 맨 위 줄의 선택. **빈 문자열 = 홈**(아무것도 안 고름 = 전체, 발주자 지시 2026-09-09).
 *  다른 값은 `agent` 하나다. 종 레일이 쓰던 저장값(`concept` 등)은 홈으로 읽는다. */
let railKey: string = localStorage.getItem(KEY_KIND) === "agent" ? "agent" : "";
const isAgent = () => railKey === "agent";
let wired = false;

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T | null;

/** 저장된 **넓은 창의** 선호 — 트리를 열로 펴 둘지. */
function isOpen(): boolean {
  return localStorage.getItem(KEY_OPEN) !== "closed";
}

/** 좁은 창(≤900, app.css 와 같은 경계)에서 트리는 열이 아니라 지도 위에 뜨는 **서랍**이다.
 *  서랍은 저장된 선호를 따르지 않는다: 열린 채로 새로 열리면 폰에서 첫 화면이 지도가
 *  아니라 목록으로 덮이고(실측 2026-09-26, 창 390: 서랍이 지도 폭 전부를 덮고 떴다),
 *  넓은 창을 좁히는 순간 열이 서랍으로 바뀌며 지도를 가린다. 그래서 좁은 창에서는
 *  **닫힌 채 시작하고**, 여닫기는 저장하지 않는다(저장값은 넓은 창의 것이다).
 *  근거: [[concept:app-narrow-window-contract]] */
const COMPACT = "(max-width: 900px)";
const compact = () => window.matchMedia(COMPACT).matches;

function setOpen(open: boolean, persist = true) {
  if (persist) localStorage.setItem(KEY_OPEN, open ? "open" : "closed");
  document.getElementById("shell")?.setAttribute("data-lnb", open ? "open" : "closed");
  // 손잡이는 LNB 맨 위 줄의 오른쪽 끝에 하나다(2026-10-22). 접히면 LNB 가 좁은 띠가
  // 되고 손잡이는 그 띠의 맨 위에 남는다 — 접는 것이 다시 펼 손잡이를 지우지 않는다.
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


/** View 가 마운트/언마운트하며 착지 동작을 넘긴다. null 이면 해시 착지로 강등. */
export function setLnbTopicPicker(fn: TopicPicker | null) {
  pickTopic = fn;
  // 맨 위 줄(홈 · Agent)은 **무엇을** 볼지, 머리의 문서 | 그래프는 **어떻게** 볼지다. 그래서 Agent 가
  // 골라져 있으면 표면이 첫 자리로 뜰 때마다 Agent 의 첫 자리로 맞춘다: 그래프는 역할 주제로 좁히고,
  // 문서 홈은 Agents 보드로 간다. 앱을 켤 때만이 아니라 문서 | 그래프를 오갈 때마다다. 딥링크(첫 자리가
  // 아닌 주소)는 덮지 않는다. View 의 첫 show 뒤에 도는 것은 안전하다 — show 는 세대 번호로 옛 것을 버린다.
  if (fn && isAgent() && atSurfaceRoot()) queueMicrotask(() => {
    if (pickTopic !== fn || !isAgent() || !atSurfaceRoot()) return;
    if (onGraph()) scopeAgentGraph();
    else location.hash = AGENTS_BOARD;
  });
}
/** 읽어 온 Agent 역할 수. 아직 안 읽었으면 null. 홈의 빈 목록이 무엇으로 이을지 가른다. */
export const agentRoleCount = (): number | null => agentRoster ? agentRoster.length : null;

/** Agents 보드의 주소. 문서 홈 표면 안의 한 면이다(surfaces/home.ts). */
export const AGENTS_BOARD = "#home/agents";

/** 지금 표면이 자기 첫 자리에 있는가(`#` · `#home` · `#view`) — 딥링크 착지를 덮지 않으려는 검사. */
const atSurfaceRoot = (): boolean => ["", "home", "view"].includes(location.hash.replace(/^#/, ""));
/** 지금 표면이 그래프인가. 아니면 문서 홈이다(검색 표면은 둘 다 아니지만 홈의 동선을 따른다). */
const onGraph = (): boolean => /^#view(\/|$)/.test(location.hash);
/** 문서 쪽의 첫 자리 — Agent 가 골라져 있으면 Agents 보드, 아니면 문서 홈. 머리의 `문서` 전환이 쓴다. */
export const documentsHome = (): string => isAgent() ? AGENTS_BOARD : "#home";

/** 그래프가 캔버스에서 고른 주제를 풀어 준다(주소는 그대로 `#view` 라 해시로는 못 푼다).
 *  풀었으면 true. 없거나 풀 것이 없으면 해시 착지로 간다. */
let clearScope: (() => boolean) | null = null;
export function setLnbScopeClearer(fn: (() => boolean) | null) {
  clearScope = fn;
}

export function setLnbNavigator(fn: Navigator | null) {
  navigate = fn;
}

/** 문서 홈이 마운트하며 문서 열기를 넘긴다. null 이면 종전 동선(그래프 착지)으로 간다. */
export function setLnbDocOpener(fn: ((docId: string, domain?: string) => void) | null) {
  openDocHere = fn;
}

/** 지금 어디를 보고 있는지 트리에 표시한다(크럼이 하던 일 — D71 로 여기 왔다). */

let lastActiveKey = "";
export function setLnbActive(sel: { domain?: string; domains?: string[]; nodeId?: string;
                                    docId?: string; reveal?: string }) {
  // 주제는 이제 **여럿**일 수 있다(스코프가 목록이 됐다). 첫 번째를 대표로 두고
  // 나머지는 선택 표시로만 쓴다 — 조상 펼치기는 대표 하나면 충분하다.
  // `reveal` 은 고르지 않고 **펼치기만** 할 주제다(문서 홈에서 연 문서의 자리).
  const doms = sel.domains?.length ? sel.domains : sel.domain ? [sel.domain] : [];
  active = { ...sel, domain: doms[0] };
  picked = new Set(doms);
  // 뷰 머리가 그래프의 범위를 이 한 자리에서 받는다(topbar.ts `paintGraphLead`).
  dispatchEvent(new CustomEvent("vinc:lnb-active", { detail: { domain: doms[0] || "" } }));
  const open = sel.reveal || doms[0];
  // 같은 자리를 다시 알리면(목록을 다시 그릴 때마다 온다) 펴지 않는다: 사용자가 방금 접은 주제가 되살아나지 않게
  const key = `${sel.reveal ? "r:" : ""}${open || ""}`;
  const fresh = key !== lastActiveKey;
  lastActiveKey = key;
  if (open && (fresh || sel.reveal)) {
    // 지금 있는 주제는 접혀 있으면 안 된다 — 그리고 중첩이 생겼으므로 **조상까지**
    // 함께 편다. 자기만 펴면 부모가 닫힌 채라 화면에 나타나지 않는다. 하위 주제가 있는
    // 줄은 자기 문서를 한 줄로 접어 두므로(`ownKey`) 그 묶음도 편다.
    const segs = open.split("/").filter(Boolean);
    segs.forEach((_s, i) => expanded.add(segs.slice(0, i + 1).join("/")));
    if (sel.reveal) expanded.add(ownKey(sel.reveal));
  }
  paint();
}

/** 역할 id 접두. 로스터 개념 `concept:agent-orchestration` 은 접두가 같아
 *  역할이 아니다(`vinc_mcp.roles.is_role_id`). */
const AGENT_ID_PREFIX = "concept:agent-";
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

let agentRoster: AgentRole[] | null = null;

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

/** Agent 레일의 트리. 보드(agent-map)와 같은 구조다: Agent(Maker · Checker) → 그 Agent 가 읽는 절차, 그리고 정책 묶음.
 *  물러난 직함 역할은 싣지 않는다. Agent 가 없는 스페이스(절차가 붙은 역할이 없다)는 역할을 한 줄씩 나열한다.
 *  줄을 누르면 보드가 그것을 고른다(절차는 보드 옆 칸에서 바로 고친다). 근거: [[vinc.canon.terminology §3.2]] */
type AgentMapLite = { agents: { id: string; title: string; domain: string }[];
  procedures: { id: string; title: string; agent: string; domain: string }[]; policies: { id: string; title: string; domain: string }[] };
let agentMap: AgentMapLite | null = null;
let lastDomains: string[] = [];   // 문서 트리가 마지막으로 받은 주제들(Agent 트리의 색을 같은 표로 매긴다)
let agentMapLoading = false;
let agentPick = "";
let agentCol = new Map<string, string>(), polCol = "";   // Agent 화면의 색 = Agent 단위(보드와 같은 표, agent-map.agentColours)
/** Agent · 정책 색을 **지금 모드로** 다시 매긴다. 색은 모드마다 다른 HEX 라 paint 마다 부른다(테마를 뒤집고 다시 그릴 때 반대 모드 색이 남지 않게). */
let colourAgents: (() => void) | null = null;
const AGENT_POL = "agent-policies";
function paintAgentTree(): string {
  const L = COPY.ui.lnb;
  if (!agentMap) {
    const roles = agentRoster || [];
    if (!roles.length) return `<div class="lnb-agent-empty"><p class="lnb-none hint">${esc(L.agentsEmpty)}</p>
      <button class="btn btn-primary lnb-new-role" type="button">${esc(COPY.author.role.title)}</button></div>`;
    return `<div class="lnb-tree" role="tree">${roles.map((r) => { const on = active.nodeId === r.id;
      return `<button class="lnb-doc lnb-node lnb-agent-role${on ? " on" : ""}" role="treeitem" aria-level="1" data-nodeid="${esc(r.id)}" data-dom="${esc(r.domain)}"
        style="--lvl:0;--ink:0${tintStyle(r.domain)}"><span class="lnb-dot" aria-hidden="true"></span><span class="lnb-dtitle">${esc(r.title)}</span></button>`; }).join("")}</div>`;
  }
  const tintA = (agentId: string) => { const c = agentId === AGENT_POL ? polCol : agentCol.get(agentId); return c ? `;--tint:${c}` : ""; };
  const row = (kind: string, id: string, title: string, agentId: string) => { const on = agentPick === id;
    return `<button class="lnb-doc lnb-arow${on ? " on" : ""}" role="treeitem" aria-level="2" data-apick="${kind}" data-id="${esc(id)}" title="${esc(title)}"
      style="--lvl:1;--ink:0${tintA(agentId)}"><span class="${kind === "policy" ? "lnb-pdot" : "lnb-dot"}" aria-hidden="true"></span><span class="lnb-dtitle">${esc(title)}</span></button>`; };
  let h = `<div class="lnb-tree" role="tree">`;
  // 만드는 쪽이 먼저(보드도 Maker 가 왼쪽이다). 그 밖의 Agent 는 이름순
  const rank = (a: { title: string }) => { const i = ["maker", "checker"].indexOf(a.title.toLowerCase()); return i < 0 ? 9 : i; };
  for (const a of [...agentMap.agents].sort((x, y) => rank(x) - rank(y) || x.title.localeCompare(y.title))) {
    const key = `agent:${a.id}`, open = expanded.has(key), on = agentPick === a.id, procs = agentMap.procedures.filter((p) => p.agent === a.id);
    h += `<div class="lnb-grow${on ? " on" : ""}"><button class="lnb-group${on ? " on" : ""}" role="treeitem" aria-level="1" data-group="${esc(key)}" data-apick="agent" data-id="${esc(a.id)}"
        aria-expanded="${open}" style="${tintA(a.id).slice(1)}"><span class="lnb-caret${open ? " open" : ""}" aria-hidden="true">›</span>
        <span class="lnb-adot" aria-hidden="true"></span><span class="lnb-gname">${esc(a.title)}</span><span class="lnb-gcount">${procs.length}</span></button></div>`;
    if (open) h += `<div class="lnb-docs" role="group">${procs.map((p) => row("procedure", p.id, p.title, a.id)).join("")}</div>`;
  }
  if (agentMap.policies.length) {
    const open = expanded.has(ownKey(AGENT_POL));
    h += `<div class="lnb-grow"><button class="lnb-group" type="button" role="treeitem" aria-level="1" data-fold="${AGENT_POL}" aria-expanded="${open}" style="${tintA(AGENT_POL).slice(1)}">
        <span class="lnb-caret${open ? " open" : ""}" aria-hidden="true">›</span><span class="lnb-pdot lnb-pmark" aria-hidden="true"></span><span class="lnb-gname">${esc(L.agentPolicies)}</span><span class="lnb-gcount">${agentMap.policies.length}</span></button></div>`
      + (open ? `<div class="lnb-docs" role="group">${agentMap.policies.map((q) => row("policy", q.id, q.title.replace(/^Policy:\s*/, ""), AGENT_POL)).join("")}</div>` : "");
  }
  return h + `</div>`;
}
/** 보드에서 고르면 LNB 의 그 줄을 켠다. */
window.addEventListener("vinc:agent-pick", (e) => {
  const d = (e as CustomEvent).detail as { kind?: string; id?: string } | null; agentPick = String(d?.id || "");
  // 보드에서 고른 것이 LNB 에서 보이게: 절차면 그 Agent 를, 정책이면 정책 묶음을 편다
  if (d?.kind === "procedure") { const p = agentMap?.procedures.find((q) => q.id === d.id); if (p) expanded.add(`agent:${p.agent}`); }
  else if (d?.kind === "agent") expanded.add(`agent:${d.id}`);
  else if (d?.kind === "policy") expanded.add(ownKey(AGENT_POL));
  if (isAgent() && !gsrc) { paint(); document.querySelector<HTMLElement>("#lnb-body .lnb-doc.on, #lnb-body .lnb-group.on")?.scrollIntoView({ block: "nearest" }); }
});

/** 다시 읽기의 세대 번호. 종을 빠르게 갈아 끼우면 앞 종의 응답이 늦게 와서 뒤 종의
 *  트리를 덮는다 — 가장 최근 호출의 응답만 상태에 쓴다. */
let refreshSeq = 0;

/** 문서가 늘거나 주제가 바뀐 뒤 다시 읽는다(가져오기·추출·이름 변경 후).
 *
 *  `quiet` 는 저절로 새로워지는 갱신(`live.ts`)이 쓴다. 로딩 표시를 안 띄우고, 실패하면
 *  있던 트리를 그대로 둔다: 사람이 누르지 않은 갱신이 트리를 깜빡이거나 "못 불러왔다"로
 *  바꾸면 그것이 곧 고장으로 읽힌다. */
export async function refreshLnb(opts: { quiet?: boolean } = {}): Promise<void> {
  const seq = ++refreshSeq, space = spaceKey();
  const stale = () => seq !== refreshSeq;
  if (!opts.quiet) {
    loading = true; failed = false;
    paint();
  }
  try {
    if (isAgent()) {
      // 주제 트리로 강등하지 않는다. 빈 로스터와 조회 실패를 구별한다.
      const roster = await api.roleRoster(cfg.owner);
      if (stale()) return;
      tree = { executed: true, domains: [], unfiled: [] };
      dataSpace = space;
      agentRoster = buildAgentRoster(roster);
      failed = false;
      window.dispatchEvent(new CustomEvent("vinc:agents-changed"));
      // Agent(절차가 붙은 역할)와 그 절차·정책. 보드가 같은 것을 읽으므로 잠깐 사이의 두 번째 읽기는 앞의 것을 쓴다
      agentMapLoading = true;
      try { const m = await import("./surfaces/agent-map"); const d = await m.loadAgentMap(agentRoster);
        if (stale()) return; agentMap = d ? { agents: d.agents, procedures: d.procedures, policies: d.policies } : null;
        colourAgents = () => { agentCol = d ? m.agentColours(d.agents) : new Map(); polCol = m.policyColour(); };
        if (!agentPick) agentPick = m.pickedInAgentMap(); }
      catch { agentMap = null; }
      agentMapLoading = false;
    } else {
      // 주제 → 문서만 받는다(노드 목록은 싣지 않는다). 노드는 그래프가 그리고, 이 열은
      // 문서를 다룬다. 응답은 지역에 받고 세대가 그대로일 때만 상태에 쓴다.
      const next = await api.navTree(cfg.owner);
      if (stale()) return;
      tree = next;
      dataSpace = space;
      // 문서의 주제는 받는 대로 앱 전체의 색 표에 더한다(그래프에 있어 문서 트리를 안 그려도 그래프가 같은 표로 칠한다)
      feedTints((tree?.domains || []).map((g) => g.domain));
      failed = !tree?.executed;
      if (tree?.domains?.length) lastDomains = tree.domains.map((g) => g.domain);
      agentRoster = null;
    }
  } catch {
    if (stale()) return;
    if (opts.quiet) return;          // 있던 트리를 지우지 않는다
    tree = null; failed = true; agentRoster = null;
  }
  loading = false;
  paint();
}

/** 역할들의 실제 주제로 지금 표면을 좁힌다(그래프는 지도를, 문서 홈은 목록을).
 *  빈 목록에는 기존 역할 주제를 쓴다. */
let agentGraphTopics: string[] = [...AGENT_TOPICS];
function scopeAgentGraph(): void {
  const here = [...new Set((agentRoster || []).map((r) => r.domain).filter(Boolean))];
  agentGraphTopics = here.length ? here : [...AGENT_TOPICS];
  pickTopic?.(agentGraphTopics, false, true);
}

/** Agent 를 떠나면 그 주제 스코프를 벗긴다. pickTopic 은 해시를 안 바꾸므로
 *  표면의 첫 자리가 이미 주소여도 hashchange 를 한 번 보내 전체로 되돌린다. */
function unscopeAgentGraph(): void {
  const agentScope = [...picked].some((d) => agentGraphTopics.includes(d));
  if (!agentScope) return;
  goSurfaceRoot();
}

/** 지금 표면(그래프 또는 문서 홈)의 첫 자리로 간다 = 전체. 이미 거기면 다시 그리게 깨운다. */
function goSurfaceRoot(): void {
  const root = onGraph() ? "#view" : "#home";
  if (location.hash === root) window.dispatchEvent(new HashChangeEvent("hashchange"));
  else location.hash = root;
}

/** 홈 ↔ Agent 전환 — **늘 다시 읽는다.** 두 목록은 다른 응답이다(주제 트리 · 역할 로스터). */
function setRailKey(next: string): void {
  if (next !== "" && next !== "agent") return;
  if (railKey === next) {
    // 다시 누르면 지금 표면에서 그 줄의 첫 자리로 돌아간다. 홈은 전체(좁혀 둔 것을 푸는 길),
    // Agent 는 그래프면 역할 주제로 좁힌 지도, 문서면 Agents 보드다.
    if (next === "") goSurfaceRoot();
    else if (onGraph()) scopeAgentGraph();
    else location.hash = AGENTS_BOARD;
    return;
  }
  const prev = railKey;
  railKey = next;
  localStorage.setItem(KEY_KIND, next);
  // Agent 목록은 역할을 고르기 전엔 줄이 켜지지 않는다. 직전 표면의 노드
  // 초점이 그대로 남으면 다른 것이 선택된 것처럼 보인다.
  if (next === "agent") active = { domain: AGENT_TOPICS[0] };
  paintRail();
  // 표면은 그대로 둔다. 문서에서 Agent 를 고르면 Agents 보드(팀 · 역할 카드)로, 그래프에서 고르면
  // 그래프인 채로 역할 주제로 좁힌다(로스터를 읽은 뒤 — 역할의 실제 주제로 좁힌다).
  if (next === "agent" && !onGraph()) location.hash = AGENTS_BOARD;
  else if (prev === "agent" && location.hash.startsWith(AGENTS_BOARD)) location.hash = "#home";
  void refreshLnb().then(() => {
    // 그 사이 다른 쪽을 골랐으면 이 전환의 뒷일은 없다.
    if (railKey !== next) return;
    if (next === "" && prev === "agent") unscopeAgentGraph();
    if (next === "agent" && onGraph()) scopeAgentGraph();
  });
}

/** 보드 주소로 바로 들어오면(주소창 · 새로고침) 레일도 Agent 로 맞춘다. 주소는 바꾸지 않는다. */
export function showAgentRail(): void {
  if (railKey === "agent") return;
  railKey = "agent";
  localStorage.setItem(KEY_KIND, "agent");
  active = { domain: AGENT_TOPICS[0] };
  paintRail();
  void refreshLnb();
}

/** 맨 위 줄 — 홈 · Agent. 트리와 **다른 요소**라 트리를 다시 그려도 여기는 그대로다
 *  (상주 크롬이고 목록은 조회 결과다). 접기 손잡이는 같은 줄의 형제(`#tb-shell-toggle`)다. */
function paintRail(): void {
  const el = $("rail-kinds");
  if (!el) return;
  const L = COPY.ui.lnb;
  const btn = (key: string, name: string, tip: string, ico: string) => {
    const on = railKey === key;
    return `<button class="rail-btn lnb-navbtn" type="button" data-rail="${esc(key)}"
      aria-pressed="${on}" aria-label="${esc(name)}" data-tip="${esc(tip)}">
      <span class="lnb-mk" aria-hidden="true">${icon(ico, 16)}</span>
      <span class="rail-name">${esc(name)}</span>
    </button>`;
  };
  el.innerHTML = btn("", COPY.ui.home.nav, COPY.ui.home.navTip, "doc")
    + btn("agent", L.kinds["agent.short"], L.kinds.agent, "domains");
  // 설정 버튼은 LNB 바닥(`#lnb-foot`)에 산다. 뱃지만 여기서 다시 묻는다 — 제안 적체가
  // 바닥에 남아 있어야 한다.
  void import("./conventions").then((m) => m.refreshBadge());
}
/* 이 열은 주제와 문서만 든다. 홈 · Agent 는 맨 위 줄에 있다. */

function docRow(d: { doc_id: string; title: string; node_id?: string; depth?: number },
                domain = ""): string {
  const depth = Math.min(Math.max(Number(d.depth ?? 0), 0), 8);   // 들여쓰기 상한 — 8단 넘으면 글자가 사라진다
  const on = active.docId ? active.docId === d.doc_id : !!d.node_id && active.nodeId === d.node_id;
  // 깊이 툴팁은 착지 개념이 있을 때만 뜻이 있다(개념 계층 안의 자리다).
  const tip = d.node_id ? COPY.ui.lnb.depthTip(Number(d.depth ?? 0)) : "";
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
    <span class="lnb-dot" aria-hidden="true"></span><span class="lnb-dtitle">${esc(docTitle(d.title))}</span>
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
  let loose: DomNode | null = null;
  for (const g of groups) {
    const segs = g.domain.split("/").filter(Boolean);
    // 그래프: 주제 없는 노드는 이름 없는 가상 뿌리에 직접 선다(최상위 주제가 여럿일 때). 줄은 "주제 없음" 하나로 맨 끝에 낸다.
    // 경로가 빈 문자열이라 누르면 접고 펴기만 한다(문서 트리의 미분류 줄과 같다). 문서 트리는 예전 그대로 이 묶음을 내지 않는다.
    if (!segs.length) { if (gsrc) loose = { seg: COPY.ui.lnb.unfiled, path: "", group: g, kids: [] }; continue; }
    let cur = root;
    segs.forEach((seg, i) => {
      const path = segs.slice(0, i + 1).join("/");
      let next = cur.kids.find((k) => k.path === path);
      if (!next) { next = { seg, path, kids: [] }; cur.kids.push(next); }
      cur = next;
    });
    cur.group = g;
  }
  if (loose) root.kids.push(loose);
  return root.kids;
}
const subDocs = (n: DomNode): number =>
  (n.group?.docs.length || 0) + n.kids.reduce((s, k) => s + subDocs(k), 0);
/** 주제 줄 끝의 숫자 = 그 아래 펼쳐질 **노드** 줄 수. 서버가 `count` 로 준다
 *  (`kind` 로 물었을 때만 오는 열이고, 그것이 곧 그 갈래의 줄 수라는 계약이다).
 *  문서는 노드 아래 매달리므로 이 숫자에 안 들어간다 — 세는 것과 그리는 것이
 *  같아야 한다는 규율은 **노드 줄에 대한 것**이다. */

function groupRows(n: DomNode, level = 1): string {
  const open = exp().has(n.path);
  const on = gsrc ? gActive === `topic:${n.path}` : picked.has(n.path);
  // 주제 행도 treeitem 이다. 자식 group 은 **항목의 형제가 아니라 안쪽**에 와야 한다 —
  // 형제로 두면 보조기술이 계층으로 읽지 않는다.
  // 줄은 **접고 펴기만** 한다. 지금 표면을 이 주제로 좁히는 것은 줄 끝의 버튼이다 — 줄
  // 클릭이 화면까지 바꾸면 "안을 보려고" 누른 사람의 화면이 통째로 바뀐다.
  // 버튼은 줄의 형제다(버튼 안에 버튼을 둘 수 없다). 골라져 있음(aria-pressed)도 버튼이 든다.
  const docsHere = subDocs(n);
  const head = `<div class="lnb-grow${on ? " on" : ""}">
    <button class="lnb-group${on ? " on" : ""}" role="treeitem" aria-level="${level}" style="${tintStyle(n.path).slice(1)}"
      ${on ? 'aria-selected="true" ' : ""}data-group="${esc(n.path)}"
      aria-expanded="${open}"${gsrc ? "" : ` data-tip="${esc(COPY.ui.home.count(docsHere))}"`}>
      <span class="lnb-caret${open ? " open" : ""}" aria-hidden="true">›</span>
      <span class="lnb-gring" aria-hidden="true">${topicRingIcon(11, 2)}</span><span class="lnb-gname">${esc(n.seg)}</span>
      <span class="lnb-gcount">${docsHere || "·"}</span>
    </button>
  </div>`;
  if (!open) return head;
  // 자식 가지가 먼저, 그다음 이 주제 자신의 문서. 순서를 뒤집으면 문서 목록 끝에
  // 가지가 붙어 "문서인 줄 알았던 것"이 사실 폴더인 상황이 된다.
  const kids = n.kids.map((k) => groupRows(k, level + 1)).join("");
  // 이 주제의 문서. 들여쓰기는 착지 개념의 계층 깊이다(얕은 개념의 문서가 위).
  const all = n.group?.docs || [];
  // 그래프: 다리(bridge)는 하위 주제들을 잇는 것이라 그 주제의 **맨 위**(하위 주제보다 앞)에 늘 펼쳐 둔다.
  // 줄은 묶음 단위로 가른다: depth 0 줄과 그 뒤에 붙은 depth 1 줄들이 한 묶음이다(다리에 붙은 것도 다리와 함께 간다).
  const kindOf = (d: unknown) => (d as { kind?: string }).kind || "";
  const bridgeRows: typeof all = [], docs: typeof all = [];
  if (gsrc) { let into = docs; for (const d of all) { if (Number(d.depth ?? 0) === 0) into = kindOf(d) === "bridge" ? bridgeRows : docs; into.push(d); } }
  else docs.push(...all);
  const gRow = (d: (typeof all)[number]) => gnodeRow({ id: d.doc_id, title: d.title, kind: kindOf(d), depth: Number(d.depth ?? 0) }, n.path);
  // 다리는 한 묶음("다리 N개")으로 묶어 맨 위에 둔다. 처음엔 접혀 있고 누르면 펼친다.
  const nBridges = bridgeRows.filter((d) => Number(d.depth ?? 0) === 0).length;
  let bridgesHtml = "";
  if (nBridges) {
    const bOpen = exp().has(bridgeKey(n.path));
    bridgesHtml = `<button class="lnb-fold lnb-bfold" type="button" role="treeitem" aria-level="${level + 1}"
        data-bfold="${esc(n.path)}" aria-expanded="${bOpen}">
        <span class="lnb-caret${bOpen ? " open" : ""}" aria-hidden="true">›</span>
        <span class="lnb-gmark" aria-hidden="true" style="${tintStyle(n.path).slice(1)}">${gsrc ? gsrc.mark("bridge", n.path) : ""}</span>
        <span class="lnb-fname">${esc(COPY.ui.lnb.bridgeItems(nBridges))}</span></button>`
      + (bOpen ? `<div class="lnb-docs" role="group">${bridgeRows.map((d) => gRow({ ...d, depth: Number(d.depth ?? 0) + 1 })).join("")}</div>` : "");
  }
  const own = docs.map((d) => gsrc ? gRow(d) : docRow(d, n.path)).join("")
    // 문서가 없는 주제 — 그렇다고 목록에서 지우면 이름을 바꾸거나 문서를 붙일
    // 진입점이 사라진다. 빈 채로 두되 빈 이유를 말한다.
    // (자식 가지가 있으면 비어 보이지 않으므로 그 문구를 내지 않는다.)
    || (n.kids.length || bridgeRows.length ? "" : `<p class="lnb-none hint">${esc(COPY.ui.home.topicEmpty)}</p>`);
  // 하위 주제가 있는 줄은 자기 문서를 **한 줄로 접어 둔다**: 기본 펼침이 주제를
  // 여는데, 그 줄들의 문서까지 쏟으면 주제의 짜임이 문서 더미에 묻힌다. 하위 주제가 없는
  // 줄은 펴면 곧장 문서다.
  let ownHtml = own;
  if (foldsOwnRows(n) && docs.length) {
    const ownOpen = exp().has(ownKey(n.path));
    ownHtml = `<button class="lnb-fold" type="button" role="treeitem" aria-level="${level + 1}"
        data-fold="${esc(n.path)}" aria-expanded="${ownOpen}">
        <span class="lnb-caret${ownOpen ? " open" : ""}" aria-hidden="true">›</span>
        <span class="lnb-fname">${esc(COPY.ui.lnb.ownItems(docs.length))}</span></button>`
      + (ownOpen ? `<div class="lnb-docs" role="group">${own}</div>` : "");
  }
  return head + `<div class="lnb-docs" role="group">${bridgesHtml}${kids}${ownHtml}</div>`;
}

/** 스크롤 밖의 머리 — 지금 몇 주제를 보고 있는지. 인자가 없으면 비운다(아직
 *  못 세었거나 셀 것이 없는 상태에서 `0` 을 내면 "주제가 없다"로 읽힌다). */
/** 머리 + 의 이름. Agent 레일에서는 새 역할 양식을 연다. */
const addTip = (): string => (railKey === "agent" ? COPY.author.role.newTip : COPY.author.addTip);
function paintHead(n?: number, label?: string): void {
  const h = $("lnb-head");
  if (!h) return;
  // `+` — 트리 머리의 만들기(문서 쓰기 · 노드 만들기 · 파일 가져오기). 지금 고른 주제가
  // 있으면 그 주제로 채운다(authoring.openCreateMenu).
  h.innerHTML = n === undefined ? ""
    : `<div class="lnb-sec"><span class="lnb-sec-label">${esc(label || COPY.ui.lnb.documents)}</span>`
      + `<span class="lnb-sec-end${isCommunitySpace(cfg.team) || railKey === "agent" ? " no-add" : ""}"><span class="lnb-gcount">${n || "·"}</span>`
      // 커뮤니티 스페이스는 읽기 전용이라 만들기 `+` 를 두지 않는다.
      + (isCommunitySpace(cfg.team) || railKey === "agent" ? "" : `<button class="rail-x lnb-add" type="button" aria-haspopup="${railKey === "agent" ? "dialog" : "menu"}"
           aria-label="${esc(addTip())}" data-tip="${esc(addTip())}">+</button>`) + `</span></div>`;
}

/** 주제 스코프 칩 — 고른 주제가 없으면 빈 문자열. */
function scopeChip(): string {
  if (!picked.size) return "";
  return `<div class="lnb-scope">
      <span>${esc(COPY.ui.lnb.scoped(picked.size))}</span>
      <button class="lnb-scope-clear" type="button">${esc(COPY.ui.lnb.scopedClear)}</button>
    </div>`;
}

/** 그래프 탭의 트리 원천. 문서 트리와 **같은 그림**(주제 마디 · 접기 규칙 · 줄 모양)으로 그래프의 노드를 나열한다:
 *  주제 아래에 그 주제의 개념·문서·결정(뜻의 층, depth 0)과 거기 붙은 기록·사건·결정(depth 1).
 *  거르개(LNB 검색 · 시간 · 범례의 종류)는 나침반이 쥐고, 바뀌면 `repaintLnbGraph` 로 다시 그리게 한다.
 *  펼침 상태는 문서 트리와 따로 둔다(한쪽에서 접은 것이 다른 쪽을 접지 않게). 그래프를 떠나면 null. */
export type LnbGraphRow = { id: string; title: string; kind: string; depth: number };
export type LnbGraphSource = {
  groups: () => { domain: string; rows: LnbGraphRow[] }[];
  open: (id: string) => void;
  mark: (kind: string, domain: string) => string;
  dock?: HTMLElement;
};
let gsrc: LnbGraphSource | null = null;
let gActive = "";
const gExpanded = new Set<string>();
const gSeen = new Set<string>();
export function setLnbGraphSource(src: LnbGraphSource | null) { gsrc = src; paint(); }
/** 거르개가 바뀌었을 때. `reveal` 이 있으면 그 주제 경로(와 조상)를 편다: 찾은 것이 접힌 줄 밑에 숨지 않게.
 *  `reset` 이면 펼침을 기본(영역까지)으로 되돌린다(검색을 비웠을 때). */
export function repaintLnbGraph(opts: { reveal?: string[]; reset?: boolean } = {}) {
  if (opts.reset) { gExpanded.clear(); gSeen.clear(); }
  for (const d of opts.reveal || []) {
    const segs = d.split("/");
    segs.forEach((_, i) => gExpanded.add(segs.slice(0, i + 1).join("/")));
    gExpanded.add(ownKey(d));
    gExpanded.add(bridgeKey(d));
  }
  if (gsrc) paint();
}
/** 나침반의 가운데가 바뀔 때. 다시 그리지 않고 켜진 줄만 옮긴다(트리가 크면 다시 그리기가 비싸다). */
export function setLnbGraphActive(id: string) {
  gActive = id;
  const body = $("lnb-body");
  if (!body || !gsrc) return;
  body.querySelectorAll(".lnb-gnode.on, .lnb-group.on").forEach((b) => b.classList.remove("on"));
  if (id.startsWith("topic:")) body.querySelector(`.lnb-group[data-group="${CSS.escape(id.slice(6))}"]`)?.classList.add("on");
  else body.querySelector(`.lnb-gnode[data-gid="${CSS.escape(id)}"]`)?.classList.add("on");
}
/** 지금 트리가 쓰는 펼침 집합 — 그래프면 그래프 것, 아니면 문서 것. */
const exp = (): Set<string> => (gsrc ? gExpanded : expanded);

function gnodeRow(d: LnbGraphRow, domain: string): string {
  const depth = Math.min(Math.max(d.depth, 0), 8), ink = Math.min(depth, 2), on = gActive === d.id;
  return `<button class="lnb-doc lnb-gnode${on ? " on" : ""}" role="treeitem" aria-level="${depth + 2}"
    data-gid="${esc(d.id)}" data-dom="${esc(domain)}" title="${esc(d.title)}"
    style="--lvl:${depth};--ink:${ink}${tintStyle(domain)}">
    <span class="lnb-gmark" aria-hidden="true">${gsrc ? gsrc.mark(d.kind, domain) : ""}</span><span class="lnb-dtitle">${esc(d.title)}</span>
  </button>`;
}

function paint() {
  const body = $("lnb-body");
  if (!body) return;
  if (gsrc) { paintGraphTree(body); return; }   // 그래프에서는 어느 레일이든 그래프 노드 트리가 선다
  if (loading && !tree) {
    paintHead();
    body.innerHTML = loaderRows(COPY.ui.lnb.loading, { count: 5, compact: true });
    return;
  }
  if (failed) {
    paintHead();
    body.innerHTML = `<p class="lnb-none hint">${esc(COPY.ui.lnb.failed)}</p>`
      + `<button class="btn btn-quiet lnb-retry" id="lnb-retry">${esc(COPY.ui.lnb.retry)}</button>`;
    return;
  }
  // Agent 목적지는 주제 트리가 아니라 로스터 목록이다. 팀을 섹션으로 두고
  // 각 줄에 이미 있는 관계를 붙인다. 그래프 스코프 칩은 목적지가 이미
  // 역할 주제라 내지 않는다.
  if (isAgent() && agentRoster) {
    if (agentMapLoading) { paintHead(); body.innerHTML = loaderRows(COPY.ui.lnb.loading, { count: 3, compact: true }); return; }
    const doms = lastDomains.length ? lastDomains : agentMap ? [...agentMap.agents, ...agentMap.procedures, ...agentMap.policies].map((x) => x.domain) : agentRoster.map((r) => r.domain);
    feedTints(doms);   // 역할 주제도 앱 전체의 색 표에 든다
    colourAgents?.();
    paintHead(agentMap ? agentMap.agents.length : agentRoster.length, COPY.ui.lnb.agents);
    body.innerHTML = paintAgentTree();
    return;
  }
  const groups = tree?.domains || [];
  const unfiled = tree?.unfiled || [];
  // 트리를 다시 그렸다는 알림(홈 목록이 점을 다시 칠한다). 테마 전환도 이 길로 온다.
  painting = true;
  try { window.dispatchEvent(new Event(TOPIC_TINTS_EVENT)); } finally { painting = false; }
  paintHead((tree?.domains || []).length);
  let html = "";
  // ③ 고른 것이 있으면 **말한다**. 켜진 배경만으로는 몇 개인지도, 어떻게 푸는지도
  //    안 보인다 — 홈 행이 전체로 돌아가는 길이지만 그렇게 읽히지 않는다.
  html += scopeChip();
  if (!groups.length && !unfiled.length) {
    html += `<p class="lnb-none hint">${esc(COPY.ui.lnb.empty)}</p>`
      + `<button class="btn btn-quiet lnb-retry" id="lnb-add">${esc(COPY.ui.lnb.emptyCta)}</button>`;
  } else {
    const tops = nestGroups(groups);
    // 기본 펼침(맨 위 주제 아래 영역까지). 처음 보는 경로에만 걸어서, 세션 안에서 접은 주제를
    // 다시 그릴 때마다 되살리지 않는다.
    applyDefaults(tops, expanded, seenDefault);
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

/** 그래프 노드 트리. 문서 트리의 `nestGroups` · `groupRows` 를 그대로 쓴다(그래서 생김새와 접기 규칙이 같다).
 *  줄만 다르다: 문서 줄 대신 노드 줄(`gnodeRow`, 도트 자리에 종류 모양). 고정(핀) 판이 있으면 트리 위에 세운다. */
function paintGraphTree(body: HTMLElement): void {
  const src = gsrc!;
  const gs = src.groups();
  const groups: NavGroup[] = gs.map((g) => ({ domain: g.domain,
    docs: g.rows.map((r) => ({ doc_id: r.id, title: r.title, node_id: r.id, depth: r.depth, kind: r.kind })) } as unknown as NavGroup));
  paintHead(groups.length, COPY.ui.lnb.documents);
  const tops = nestGroups(groups);
  applyDefaults(tops, gExpanded, gSeen);   // 문서 트리와 같은 기본: 맨 위 주제 아래 영역(가장 상위 도메인)까지만 보인다
  const tree = document.createElement("div");
  tree.className = "lnb-tree";
  tree.setAttribute("role", "tree");
  tree.innerHTML = tops.length ? tops.map((n) => groupRows(n)).join("") : `<p class="lnb-none hint">${esc(COPY.ui.lnb.empty)}</p>`;
  const kids: HTMLElement[] = [];
  if (src.dock) kids.push(src.dock);
  kids.push(tree);
  body.replaceChildren(...kids);
}

/** 캐럿을 **실제로 돌린다.** paint() 가 트리를 통째로 다시 그리므로 새 캐럿은 최종 각도로
 *  태어나고 전이는 시작점이 없다 — `.lnb-caret` 의 transition 이 여태 한 번도 돌지 않은
 *  이유. 방금 그린 캐럿을 이전 각도에 세워 한 프레임 확정한 뒤 놓아 준다(FLIP). */
function spinCaret(row: HTMLElement, open: boolean): void {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const caret = row.querySelector<HTMLElement>(":scope > .lnb-caret");
  if (!caret) return;
  caret.style.transition = "none";
  caret.style.transform = open ? "rotate(0deg)" : "rotate(90deg)";
  void caret.offsetWidth;                  // 시작 상태 확정 — 없으면 한 프레임에 건너뛴다
  caret.style.transition = "";
  caret.style.transform = "";
}

/** 펼침 키에 해당하는 줄(주제 줄 또는 접은 항목 줄). */
function rowFor(key: string): HTMLElement | null {
  const body = $("lnb-body");
  if (!body) return null;
  if (key.startsWith("bridge:")) return body.querySelector<HTMLElement>(`.lnb-bfold[data-bfold="${CSS.escape(key.slice(7))}"]`);
  return key.startsWith("own:")
    ? body.querySelector<HTMLElement>(`[data-fold="${CSS.escape(key.slice(4))}"]`)
    : body.querySelector<HTMLElement>(`.lnb-group[data-group="${CSS.escape(key)}"]`);
}

/** 접고 펴기 한 번. 트리를 통째로 다시 그리므로, 초점이 트리 안에 있었으면 새로 그린
 *  같은 줄로 옮긴다(키보드로 펼친 뒤 초점이 사라지면 다음 화살표가 갈 곳이 없다). */
function toggleRow(key: string): void {
  const hadFocus = !!document.activeElement?.closest("#lnb-body");
  const set = exp();
  if (set.has(key)) set.delete(key); else set.add(key);
  paint();
  const row = rowFor(key);
  if (!row) return;
  spinCaret(row, set.has(key));
  if (hadFocus) row.focus();
}

/** 문서 클릭 — **지도로 가고 문서를 연다**(둘 다, 한 클릭에). 여태 노드로만
 *  날아가서, 목록에서 문서를 골라도 본문은 두 번째 클릭이 있어야 나왔고 그 두 번째
 *  클릭조차 "그 노드의 발췌가 마침 이 문서일 때"만 성립했다. 착지 노드가 없는
 *  문서는 지도에 자리가 없으므로 본문만 연다. */
async function openDoc(docId: string, nodeId: string, domain?: string) {
  // 문서 홈이 떠 있으면 그 자리에서 연다 — 착지 개념이 있든 없든 문서는 문서로 읽는다.
  if (openDocHere) { openDocHere(docId, domain); return; }
  if (nodeId) {
    if (navigate) navigate(nodeId, domain, docId);
    // View 밖에서는 해시로 착지한다. `read` 는 trace 와 같되 넓은 문서 열로 연다
    // (LNB 에서 고른 것은 그렇게 연다 — view.ts openFromNav).
    else location.hash = `#view/read/${encodeURIComponent(nodeId)}/${encodeURIComponent(docId)}`;
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
 *  부르는 데는 **테마 전환**(`applyTheme`) 하나다 — `tintStyle` 이 싣는 것은 CSS 변수가 아니라 그때
 *  모드로 계산한 HEX 라, 다시 그리지 않으면 뒤집힌 화면에 반대 모드 색이 남는다. */
export function repaintLnb(): void { paint(); }

export function mountLnb(opts: { deferLoad?: boolean } = {}) {
  if (wired) return;
  wired = true;
  const root = $("lnb");
  if (!root) return;
  expanded = new Set();
  try { localStorage.removeItem(KEY_EXPANDED); } catch { /* 저장소가 없으면 지울 것도 없다 */ }

  // ⚠ **덮지 않고 잇는다.** 이 카드의 맨 위 줄(홈 · Agent)과 바닥(설정 · 접기)은
  // `index.html` 에 있다 — 대입으로 쓰면 그 둘이 지워진다. 머리와 트리는 그 사이에 선다.
  // 머리는 **스크롤 밖**이다(발주자 지적 2026-09-10: 스크롤 시작 지점이 더 아래여야
  // 한다) — 지금 무엇을 보고 있는지 말하는 줄이 스크롤과 함께 없어지면 그 줄의 일이
  // 성립하지 않는다. 렌즈 패널·시트와 같은 배치다(머리 고정 · 몸만 스크롤).
  const slot = $("lnb-foot");
  const parts = `<div class="lnb-head" id="lnb-head"></div><div class="lnb-body" id="lnb-body"></div>`;
  if (slot) slot.insertAdjacentHTML("beforebegin", parts);
  else root.insertAdjacentHTML("beforeend", parts);
  $("lnb-head")?.addEventListener("click", (e) => {
    const b = (e.target as HTMLElement).closest<HTMLElement>(".lnb-add");
    if (!b) return;
    // Agent 레일의 + 는 역할을 만든다 — 일반 노드로 만들면 id·계약을 사람이 맞춰야 한다.
    if (railKey === "agent") {
      void import("./role-composer").then((m) => m.openRoleComposer());
      return;
    }
    const r = b.getBoundingClientRect();
    // 고른 주제가 하나면 그 주제로, 아니면 지금 보고 있는 자리(View 의 맥락)로 채운다.
    const topic = picked.size === 1 ? [...picked][0] : undefined;
    void import("./authoring").then((m) => m.openCreateMenu(r.left, r.bottom + 4, topic));
  });
  const shellToggle = $("tb-shell-toggle");
  if (shellToggle) {
    shellToggle.innerHTML = icon("sidebar", 18);
    // 지금 화면의 상태를 뒤집는다(저장값이 아니라). 좁은 창의 서랍 여닫기는 저장하지
    // 않는다 — 위 COMPACT 주석.
    shellToggle.addEventListener("click", () =>
      setOpen($("shell")?.dataset.lnb !== "open", !compact()));
  }
  setOpen(isOpen() && !compact(), false);
  // 경계를 넘으면 다시 정한다: 좁아지면 서랍을 닫고, 넓어지면 저장된 선호로 돌아간다.
  window.matchMedia(COMPACT).addEventListener("change", (e) =>
    setOpen(!e.matches && isOpen(), false));


  // 맨 위 줄과 바닥은 자기 배선을 갖는다 — 트리 본문과 다른 요소이므로.
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
  settingsBtn?.addEventListener("click", () =>
    void import("./surfaces/settings").then((m) => m.openSettings("general", undefined, true)));
  // 승인함. 계정 그래프(웹 · 셸의 계정 모드)에만 실행의 대기가 있다. 숫자는 열린 대기 수다.
  const approvalsBtn = $("rail-approvals");
  if (approvalsBtn && remote()) {
    const A = COPY.approvals;
    approvalsBtn.hidden = false;
    approvalsBtn.setAttribute("aria-label", A.nav);
    approvalsBtn.setAttribute("data-tip", A.navTip);
    approvalsBtn.insertAdjacentHTML("afterbegin",
      `${icon("decide", 18)}<span class="rail-name">${esc(A.nav)}</span>`);
    approvalsBtn.addEventListener("click", () => {
      if (!/^#approvals$/.test(location.hash)) location.hash = "#approvals";
      if (compact()) setOpen(false, false);
    });
    const pressed = () => approvalsBtn.setAttribute("aria-pressed", String(/^#approvals(\/|$)/.test(location.hash)));
    pressed();
    window.addEventListener("hashchange", pressed);
  }
  $("lnb-top")?.addEventListener("click", (e) => {
    const b = (e.target as HTMLElement).closest<HTMLElement>(".rail-btn");
    // ⚠ `if (b?.dataset.rail)` 이면 홈의 빈 값이 falsy 라 걸러진다 — 있는지를 묻는다.
    if (b?.dataset.rail !== undefined) {
      setRailKey(b.dataset.rail);
      // 좁은 창에서는 서랍이 닫힌 채 시작하므로 Agent 를 고르면 그 목록이 보여야 한다.
      // 홈(빈 값)은 목록이 아니라 표면의 첫 자리로 가는 것이라 열지 않는다.
      if (compact() && b.dataset.rail) setOpen(true, false);
    }
  });

  const body = $("lnb-body")!;
  body.addEventListener("click", (e) => {
    const t = e.target as HTMLElement;

    // Agent 트리: Agent 줄은 따라가며 펴고(이미 고른 Agent 면 접고 편다), 절차·정책 줄은 보드에서 고른다
    const ap = t.closest<HTMLElement>("[data-apick]");
    if (ap && !gsrc) {
      const kind = ap.dataset.apick as "agent" | "procedure" | "policy", id = ap.dataset.id || "";
      if (kind === "agent") { const key = `agent:${id}`; if (agentPick === id) { toggleRow(key); return; } if (!expanded.has(key)) expanded.add(key); }
      agentPick = id; paint();
      void import("./surfaces/agent-map").then((m) => { if (!m.pickInAgentMap(kind, id) && location.hash !== "#home/agents") location.hash = "#home/agents"; });
      if (compact()) setOpen(false, false);
      return;
    }
    // 그래프 트리의 노드 줄 — 나침반이 그 노드로 간다(주제 사슬을 따라 펼친다).
    const gn = t.closest<HTMLElement>(".lnb-gnode");
    if (gn && gsrc) {
      gsrc.open(gn.dataset.gid || "");
      setLnbGraphActive(gn.dataset.gid || "");
      if (compact()) setOpen(false, false);
      return;
    }
    // 노드 줄이 문서 줄의 class 를 함께 쓰므로(같은 생김새) **먼저** 가른다.
    const nd = t.closest<HTMLElement>(".lnb-node");
    if (nd) {
      const id = nd.dataset.nodeid || "";
      if (!id) return;
      if (nd.classList.contains("lnb-agent-role") && !(onGraph() && navigate)) {
        // 문서 쪽에서는 역할을 Agents 보드 안의 역할 편집 면으로 연다. 그래프에서는 다른 노드처럼
        // 지도 위에서 연다(아래 `navigate`) — 고른다고 그래프를 떠나지 않는다.
        active = { domain: nd.dataset.dom || "", nodeId: id };
        paint();
        void import("./role-composer").then((m) => m.openRoleComposer({ id }));
        if (compact()) setOpen(false, false);
        return;
      }
      if (navigate) navigate(id, nd.dataset.dom || "");
      else location.hash = `#view/read/${encodeURIComponent(id)}`;
      active = { domain: nd.dataset.dom || "", nodeId: id };
      paint();
      // 좁은 창에서는 고른 것이 서랍 **뒤**에 열린다 — 닫아서 보여 준다.
      if (compact()) setOpen(false, false);
      return;
    }
    if (t.closest(".lnb-new-role")) {
      void import("./role-composer").then((m) => m.openRoleComposer());
      return;
    }
    const doc = t.closest<HTMLElement>(".lnb-doc");
    if (doc) {
      void openDoc(doc.dataset.doc || "", doc.dataset.node || "", doc.dataset.dom || "");
      if (compact()) setOpen(false, false);
      return;
    }
    const fold = t.closest<HTMLElement>(".lnb-fold");
    // 주제 줄을 누르면 지금 표면이 그 주제를 따라간다: 그래프는 나침반 가운데가, 문서는 문서 목록이 그 주제로 좁혀진다.
    // 아직 그 주제가 아니면 따라가며 펴고(접지 않는다), 이미 그 주제면 접고 편다. Ctrl·Cmd 클릭(문서)은 주제를 더한다.
    const here = (path: string) => gsrc ? gActive === `topic:${path}` : picked.size === 1 && picked.has(path);
    const follow = (path: string) => {
      if (!path) return;
      if (gsrc) { gsrc.open(`topic:${path}`); setLnbGraphActive(`topic:${path}`); }
      else if (pickTopic && !here(path)) pickTopic(path, false);
    };
    if (fold?.dataset.bfold !== undefined) { toggleRow(bridgeKey(fold.dataset.bfold ?? "")); return; }
    if (fold?.dataset.fold === AGENT_POL) { toggleRow(ownKey(AGENT_POL)); return; }   // Agent 트리의 정책 묶음: 접고 펴기만
    if (t.closest<HTMLElement>(`.lnb-group[data-fold="${AGENT_POL}"]`)) { toggleRow(ownKey(AGENT_POL)); return; }
    if (fold) { toggleRow(ownKey(fold.dataset.fold ?? "")); follow(fold.dataset.fold ?? ""); return; }
    const group = t.closest<HTMLElement>(".lnb-group");
    if (group) {
      const path = group.dataset.group ?? "";
      if (path && !gsrc && pickTopic && (e.ctrlKey || e.metaKey)) { pickTopic(path, true); return; }
      if (path && (gsrc || pickTopic) && !here(path)) { if (!exp().has(path)) toggleRow(path); follow(path); return; }
      toggleRow(path); return;
    }
    if (t.closest(".lnb-scope-clear")) {
      // 고른 것을 전부 뺀다 = 전체(L0). `pickTopic` 에 같은 경로를 다시 넘기면
      // 토글이라 하나씩 꺼야 하므로, 첫 번째를 비-add 로 두 번 눌러 비우는 대신
      // 홈 해시로 간다 — 그것이 이 앱에서 "전체"의 정본 주소다.
      if (clearScope?.()) return;
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
  // 트리 키보드(treeitem 에 맞춘 최소한): ↑↓ 이동 · Home/End · → 펼치기(펴져 있으면 첫
  // 자식으로) · ← 접기(접혀 있거나 항목 줄이면 부모로). Enter·Space 는 버튼 기본 동작이
  // 곧 클릭이라 따로 잡지 않는다.
  body.addEventListener("keydown", (e) => {
    const cur = (e.target as HTMLElement).closest<HTMLElement>(".lnb-group, .lnb-fold, .lnb-doc");
    if (!cur) return;
    const rows = [...body.querySelectorAll<HTMLElement>(".lnb-group, .lnb-fold, .lnb-doc")];
    const i = rows.indexOf(cur);
    const key = cur.dataset.bfold !== undefined ? bridgeKey(cur.dataset.bfold)
      : cur.classList.contains("lnb-fold") ? ownKey(cur.dataset.fold ?? "")
      : cur.classList.contains("lnb-group") ? (cur.dataset.group ?? "") : null;
    const open = key !== null && exp().has(key);
    const go = (el: HTMLElement | null | undefined) => { e.preventDefault(); el?.focus(); };
    switch (e.key) {
      case "ArrowDown": go(rows[i + 1]); break;
      case "ArrowUp": go(rows[i - 1]); break;
      case "Home": go(rows[0]); break;
      case "End": go(rows[rows.length - 1]); break;
      case "ArrowRight":
        if (key === null) break;
        e.preventDefault();
        if (open) rows[i + 1]?.focus(); else toggleRow(key);
        break;
      case "ArrowLeft": {
        e.preventDefault();
        if (key !== null && open) { toggleRow(key); break; }
        // 부모 = 이 줄을 담은 묶음(.lnb-docs) 바로 앞의 줄. 주제 줄은 .lnb-grow 로 싸여 있다.
        const prev = cur.closest(".lnb-docs")?.previousElementSibling as HTMLElement | null;
        (prev?.classList.contains("lnb-grow") ? prev.querySelector<HTMLElement>(".lnb-group") : prev)?.focus();
        break;
      }
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
  if (opts.deferLoad) {
    loading = true;
    failed = false;
    paint();
  } else {
    void refreshLnb();
  }
}

/** 주제 이름 변경 / 미분류로 내리기(S2). 폐지된 L0 주제 표의 `⋯` 가 하던 일 그대로 —
 *  전역 Domain 노드는 건드리지 않고 내 노드의 domain 문자열만 바뀐다. */
async function domainMenu(domain: string, ev: MouseEvent) {
  const { openContextMenu, promptText } = await import("./components");
  const authoring = await import("./authoring");
  openContextMenu(ev.clientX, ev.clientY, [
    // 여기에 새로 만들기(흐름 A) — 주제는 누른 주제다.
    { label: COPY.author.hereDoc, run: () => authoring.startNewDoc(domain) },
    { label: COPY.author.hereNode, run: () => void authoring.startNewNode({ topic: domain }) },
    {
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
