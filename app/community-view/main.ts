/** 커뮤니티 그래프 보기: 공개 커뮤니티 하나를 데스크톱 그래프와 같은 렌더러로
 *  읽기 전용으로 그린다. 관점·배치·선 갈래는 고정이고 손잡이와 범례 편집은 없다.
 *
 *  데스크톱의 3D 시간순 가지 경로를 그대로 밟는다: 받은 그래프 → 워커 배치
 *  (`layoutInWorker`) → `toN3`·`toL3` 와 같은 변환 → `mount3d`. 다른 점은 데이터가
 *  사이드카가 아니라 공개 커뮤니티 API 에서 온다는 것 하나다.
 *
 *  `app/src` 밖에 두는 이유: 이 화면은 사이드카가 아니라 사이트의 공개 커뮤니티 API 를
 *  부른다. `app/src` 의 호출은 사이드카 계약과 대조된다(`dev/sidecar-contract-check.ts`).
 *
 *  ⚠ 데스크톱의 저장된 설정(localStorage)을 읽지도 쓰지도 않는다. `mount3d` 의 prefs
 *  기본 인자가 `loadPrefs()` 라서 아래 `PREFS` 를 반드시 명시로 넘긴다. */
import { mount3d, type N3, type L3 } from "../src/surfaces/view3d";
import { layoutInWorker } from "../src/view-layout-client";
import { defaultPrefs, edgeKindOf, EDGE_KINDS, type EdgeKind, type ViewPrefs } from "../src/view-prefs";
import { palette, hexStr, type ThemeMode } from "../src/theme";
import type { GraphEdge, GraphNode } from "../src/api";

/** 그리는 선 갈래. 무방향·주제 교차·방향 관계를 켜고 다리 갈래만 끈다. */
export const COMMUNITY_EDGE_KINDS: readonly EdgeKind[] = ["related", "cross", "directed"];

/** 고정 설정. 나머지(크기·유리·라벨·밀도)는 데스크톱 기본값 그대로다. 시간축은 그 일이
 *  일어난 때(`when.start`)다. 페이지를 만든 때가 아니다. */
const PREFS: ViewPrefs = {
  ...defaultPrefs(),
  view: "3d", layout: "temporalRoots", timeBasis: "content", edges: "always", when: 1,
  unlinked: "show", labels: "hover",
  edgeOff: EDGE_KINDS.filter((k) => !COMMUNITY_EDGE_KINDS.includes(k)),
};

type Page = {
  slug: string; title?: string; isRules?: boolean; status?: string; topic?: string | null;
  kind?: string | null; createdAt?: string; updatedAt?: string;
  links?: { slug: string; type: string }[];
  /** 그 일이 일어난 때. 각 값은 `YYYY`·`YYYY-MM`·`YYYY-MM-DD` 이고 기원전은 `-0300` 이다. */
  when?: { start?: string; end?: string } | null;
};

const COPY = {
  en: { loading: "Loading the graph…", failed: "Could not load this community.", empty: "This community has no pages yet." },
  ko: { loading: "그래프를 불러오는 중입니다…", failed: "커뮤니티를 불러오지 못했습니다.", empty: "아직 페이지가 없습니다." },
};

/** 페이지 종류 → 렌더러 종류. 사건은 에피소드, 결과는 기록, 사람·작품은 개체로 그려
 *  데스크톱과 같은 모양을 받는다. 모르는 종류와 빈 값은 개념이다. */
const RENDER_KIND: Record<string, string> = {
  concept: "concept", event: "episode", result: "record", decision: "decision",
  person: "entity", work: "entity",
};
const kindOf = (k?: string | null): string => RENDER_KIND[k || ""] ?? "concept";

/** 배치가 읽는 시간 열쇠. 배치는 날짜 문자열을 사전순으로 비교하고 `YYYY` 꼴만 받아서
 *  기원전 연도를 그대로 넘기면 순서가 뒤집히거나 버려진다. 그래서 연도에 4000 을 더한
 *  네 자리로 옮긴다(-0300 → 3700, 2015 → 6015). 400 의 배수라 윤일이 그대로 유효하다.
 *  순서만 쓰이고 화면에는 나오지 않는다. */
const YEAR_SHIFT = 4000;
export function timeKey(value?: string | null): string {
  const m = /^(-?)(\d{4})(-\d{2}(?:-\d{2})?)?$/.exec((value || "").trim());
  if (!m) return "";
  const year = (m[1] ? -1 : 1) * Number(m[2]) + YEAR_SHIFT;
  if (year < 0 || year > 9999) return "";
  return String(year).padStart(4, "0") + (m[3] || "");
}

/** 날짜가 없는 페이지의 자리. 관계를 따라 가장 가까운 날짜 있는 페이지들 중 가장 이른
 *  날짜를 받고, 이어진 날짜가 없으면 같은 주제의 가장 이른 날짜에 선다. 주제에도 날짜가
 *  없으면 비워 둔다(가지의 시작이다). 이 값은 배치용이고 페이지에 날짜를 지어 붙이지 않는다. */
export function placeUndated(ids: string[], own: Map<string, string>, links: [string, string][],
  topic: Map<string, string>): Map<string, string> {
  const adj = new Map(ids.map((id) => [id, [] as string[]]));
  for (const [a, b] of links) { adj.get(a)?.push(b); adj.get(b)?.push(a); }
  const topicStart = new Map<string, string>();
  for (const id of ids) {
    const t = own.get(id), d = topic.get(id) || "";
    if (t && (!topicStart.has(d) || t < topicStart.get(d)!)) topicStart.set(d, t);
  }
  const out = new Map(own);
  for (const id of ids) {
    if (own.get(id)) continue;
    let ring = [id], seen = new Set(ring), found = "";
    while (ring.length && !found) {
      const next: string[] = [];
      for (const at of ring) for (const n of adj.get(at) || []) if (!seen.has(n)) { seen.add(n); next.push(n); }
      for (const n of next) { const t = own.get(n); if (t && (!found || t < found)) found = t; }
      ring = next;
    }
    out.set(id, found || topicStart.get(topic.get(id) || "") || "");
  }
  return out;
}

/** 페이지 목록 → 데스크톱 `/graph` 와 같은 모양의 노드·관계. 시작 페이지와 규칙 페이지는
 *  지도에 없는 안내 문서라 뺀다. */
export function graphOf(pages: Page[]): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const shown = pages.filter((p) => p.slug && (p.status ?? "active") === "active" && !p.isRules
    && p.slug !== "start-here");
  const topic = new Map(shown.map((p) => [p.slug, (p.topic || "").trim()]));
  const edges: GraphEdge[] = [];
  const seen = new Set<string>();
  for (const p of shown) for (const l of p.links || []) {
    if (!topic.has(l.slug) || l.slug === p.slug) continue;
    // 위키 관계 이름 → 그래프 rel 이름. relates_to 만 이름이 다르다(RELATED_TO).
    const raw = String(l.type || "relates_to").toLowerCase();
    const type = raw === "relates_to" ? "RELATED_TO" : raw.toUpperCase();
    // 무방향 관계는 양쪽 페이지가 서로를 적어도 선 하나다. 방향 관계는 방향마다 따로다.
    const key = type === "RELATED_TO" ? [type, ...[p.slug, l.slug].sort()].join("\n")
      : [type, p.slug, l.slug].join("\n");
    if (seen.has(key)) continue;
    seen.add(key);
    edges.push({ source: p.slug, target: l.slug, type,
      cross_domain: topic.get(p.slug) !== topic.get(l.slug) });
  }
  const degree = new Map<string, number>();
  for (const e of edges) for (const id of [e.source, e.target]) degree.set(id, (degree.get(id) || 0) + 1);
  const when = placeUndated(shown.map((p) => p.slug),
    new Map(shown.map((p) => [p.slug, timeKey(p.when?.start)])),
    edges.map((e) => [e.source, e.target]), topic);
  const nodes = shown.map((p): GraphNode => ({
    id: p.slug, label: p.title || p.slug, domain: topic.get(p.slug) || "",
    degree: degree.get(p.slug) || 0, kind: kindOf(p.kind),
    ts: p.createdAt || p.updatedAt || "",
    content_ts: when.get(p.slug) || "",
  }));
  return { nodes, edges };
}

/** 데스크톱 `toN3`(렌즈 없음 · 초점 없음)과 같은 변환. */
const toN3 = (nodes: GraphNode[]): N3[] => nodes.map((nd) => ({
  id: nd.id, label: nd.label === nd.id ? nd.id.replace(/^[a-z]+:/, "") : nd.label,
  domain: nd.domain, degree: nd.degree, kind: nd.kind || "concept",
  gateway: false, focus: false, x: nd.x, y: nd.y, z: nd.z,
}));
/** 데스크톱 `toL3` 와 같은 변환. */
const toL3 = (edges: GraphEdge[]): L3[] => edges.map((e) => ({ source: e.source, target: e.target,
  type: e.type, cross: e.cross_domain, confidence: e.confidence, evidence: e.evidence,
  id: e.id, role: e.role, route: e.route, presentation: e.presentation }));

const params = new URLSearchParams(location.search);
const slug = params.get("c") || "";
const text = COPY[params.get("lang") === "ko" ? "ko" : "en"];
const root = document.documentElement;
if (params.get("lang") === "ko") root.lang = "ko";
const stage = document.getElementById("graph") as HTMLElement;
const msg = document.getElementById("msg") as HTMLElement;
let graph: ReturnType<typeof mount3d> | null = null;

/** 같은 출처의 부모 문서. 다른 출처면 접근이 막혀 null 이다. */
const parentRoot = (): HTMLElement | null => {
  try { return window.parent !== window ? window.parent.document.documentElement : null; } catch { return null; }
};
const askedTheme = params.get("theme");
const dark = window.matchMedia("(prefers-color-scheme: dark)");
const modeNow = (): ThemeMode => {
  if (askedTheme === "dark" || askedTheme === "light") return askedTheme;
  const t = parentRoot()?.dataset.theme;
  if (t === "dark" || t === "light") return t;
  return dark.matches ? "dark" : "light";
};
const applyTheme = () => {
  const mode = modeNow();
  if (root.dataset.theme === mode) return;
  root.dataset.theme = mode;
  // 렌더러의 안개가 배경색과 같아야 원경이 바탕에 녹는다.
  document.body.style.background = hexStr(palette[mode].background);
  document.body.style.color = hexStr(palette[mode].textMute);
  (graph as { __retheme?: () => void } | null)?.__retheme?.();
};
applyTheme();
if (askedTheme !== "dark" && askedTheme !== "light") {
  const parent = parentRoot();
  if (parent) new MutationObserver(applyTheme).observe(parent, { attributes: true, attributeFilter: ["data-theme"] });
  dark.addEventListener("change", applyTheme);
}

const say = (line: string) => { msg.textContent = line; msg.hidden = !line; };

const isPage = (n: { id: string | number; kind?: string }) => {
  const id = String(n.id);
  return n.kind !== "domain" && !id.startsWith("domain:") && !id.startsWith("@") && pageIds.has(id);
};
let pageIds = new Set<string>();
let pressed: { x: number; y: number } | null = null;
stage.addEventListener("pointerdown", (ev) => {
  const r = stage.getBoundingClientRect();
  pressed = { x: ev.clientX - r.left, y: ev.clientY - r.top };
});
/** 누른 자리에서 가장 가까운 문서 점. 주제 상자가 그 앞에 있어 클릭을 먼저 받으면 이것으로 넘긴다. */
function nearestPage(g: ReturnType<typeof mount3d>, at: { x: number; y: number }, within = 18): N3 | null {
  let best: N3 | null = null, bestD = within;
  for (const n of g.graphData().nodes as N3[]) {
    if (!isPage(n) || !Number.isFinite(n.x)) continue;
    const s = g.graph2ScreenCoords(n.x!, n.y || 0, n.z || 0);
    const d = Math.hypot(s.x - at.x, s.y - at.y);
    if (d < bestD) { best = n; bestD = d; }
  }
  return best;
}

/** 노드를 누르면 맨 위 창이 그 페이지로 간다. 주제 마크를 눌렀으면 그 자리의 문서 점으로 간다. */
const open = (n: N3) => {
  const page = isPage(n) ? n : graph && pressed ? nearestPage(graph, pressed) : null;
  if (!page) return;
  const url = `/community/${encodeURIComponent(slug)}/p/${encodeURIComponent(page.id)}`;
  // 프레임 안이면 주소를 부모(커뮤니티 화면)에 건네고 부모가 옮긴다. 프레임이 맨 위 창의 주소를
  // 직접 바꾸면 Chrome 이 그 이동을 취소했다(ERR_ABORTED, 같은 출처에서도).
  if (window.parent !== window) { window.parent.postMessage({ type: "vinc:community-open", url }, location.origin); return; }
  window.location.assign(url);
};

async function main() {
  if (!/^[a-z0-9][a-z0-9-]*$/.test(slug)) { say(text.failed); return; }
  say(text.loading);
  let pages: Page[];
  try {
    const res = await fetch(`/api/community/c/${encodeURIComponent(slug)}/pages`, { headers: { accept: "application/json" } });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    pages = (await res.json()).pages;
    if (!Array.isArray(pages)) throw new Error("no pages");
  } catch (error) {
    console.error("Community graph read failed:", error instanceof Error ? error.message : String(error));
    say(text.failed); return;
  }
  const { nodes, edges } = graphOf(pages);
  if (!nodes.length) { say(text.empty); return; }
  pageIds = new Set(nodes.map((n) => n.id));
  // 화면 비율은 데스크톱과 같이 소수 첫째 자리로 접는다(배치가 그 값으로 열 높이를 정한다).
  const aspect = Math.round((stage.clientWidth || 1200) / (stage.clientHeight || 750) * 10) / 10;
  let laid;
  try {
    laid = await layoutInWorker(nodes, edges, PREFS.timeBasis, new AbortController().signal, undefined, aspect);
  } catch {
    say(text.failed); return;
  }
  say("");
  const off = new Set(PREFS.edgeOff);
  const lines = [...laid.edges.filter((e) => !off.has(edgeKindOf(e))), ...laid.topicEdges];
  graph = mount3d(stage, toN3(laid.nodes), toL3(lines), { onClick: open }, PREFS, null,
    laid.nodes.map((n) => n.domain));
  // 읽기 전용이다. 노드를 끌어 옮기는 손짓도 끈다.
  graph.enableNodeDrag(false);
  whenDrawn(graph, () => fit(graph!));
  let resized = 0;
  window.addEventListener("resize", () => { clearTimeout(resized); resized = window.setTimeout(() => graph && fit(graph), 200); });
}

/** 메시가 다 생기고 렌더러의 첫 맞춤(400ms 애니메이션)이 끝난 뒤에 부른다. */
function whenDrawn(g: ReturnType<typeof mount3d>, then: () => void, tries = 0) {
  const ns = g.graphData().nodes as { __threeObj?: unknown }[];
  if (ns.some((n) => !n.__threeObj) && tries < 120) { requestAnimationFrame(() => whenDrawn(g, then, tries + 1)); return; }
  setTimeout(then, 700);
}

/** 화면 비율까지 따져 그래프가 프레임을 채우게 한다. 렌더러의 `zoomToFit` 은 세로 시야각
 *  하나로 재서, 가지가 옆으로 늘어선 시간순 배치는 넓은 화면 가운데 작게 남는다. 보던 방향은
 *  그대로 두고 중심과 거리만 다시 잡는다. */
function fit(g: ReturnType<typeof mount3d>, ms = 500) {
  const cam = g.camera() as unknown as { fov: number; aspect: number; position: { x: number; y: number; z: number } };
  const ns = (g.graphData().nodes as { x?: number; y?: number; z?: number }[]).filter((n) => Number.isFinite(n.x));
  if (!ns.length) return;
  const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  for (const n of ns) [n.x!, n.y || 0, n.z || 0].forEach((v, i) => { lo[i] = Math.min(lo[i], v); hi[i] = Math.max(hi[i], v); });
  const c = lo.map((v, i) => (v + hi[i]) / 2);
  const norm = (v: number[]) => { const l = Math.hypot(...v) || 1; return v.map((x) => x / l); };
  const cross = (a: number[], b: number[]) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  const dot = (a: number[], b: number[]) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const dir = norm([cam.position.x - c[0], cam.position.y - c[1], cam.position.z - c[2]]);
  const right = norm(cross([0, 1, 0], dir)), up = cross(dir, right);
  const tanV = Math.tan((cam.fov * Math.PI) / 360), tanH = tanV * cam.aspect;
  // 화면 평면에서의 상자 가운데로 중심을 옮긴 뒤, 모든 점이 시야 안에 드는 가장 가까운 거리.
  const rs = ns.map((n) => [n.x! - c[0], (n.y || 0) - c[1], (n.z || 0) - c[2]]);
  const r0 = (Math.min(...rs.map((p) => dot(p, right))) + Math.max(...rs.map((p) => dot(p, right)))) / 2;
  const u0 = (Math.min(...rs.map((p) => dot(p, up))) + Math.max(...rs.map((p) => dot(p, up)))) / 2;
  const centre = [0, 1, 2].map((i) => c[i] + right[i] * r0 + up[i] * u0);
  let d = 0;
  for (const p of rs) {
    const q = [0, 1, 2].map((i) => p[i] - right[i] * r0 - up[i] * u0);
    const depth = dot(q, dir);
    d = Math.max(d, depth + Math.abs(dot(q, right)) / tanH, depth + Math.abs(dot(q, up)) / tanV);
  }
  d *= 1.08;  // 가장자리 여백
  const pos = { x: centre[0] + dir[0] * d, y: centre[1] + dir[1] * d, z: centre[2] + dir[2] * d };
  g.cameraPosition(pos, { x: centre[0], y: centre[1], z: centre[2] }, ms);
  // 렌더러의 안개는 컨트롤의 change 로 카메라 거리에 맞춘다. 코드로 옮긴 카메라도 알린다.
  setTimeout(() => (g.controls() as { dispatchEvent?: (e: { type: string }) => void }).dispatchEvent?.({ type: "change" }), ms + 20);
}

void main();
