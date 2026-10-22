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

/** 그리는 선 갈래. 데스크톱 범례의 "Related (no direction)" 하나만 켠다. 방향·주제 교차·
 *  다리 갈래를 켜려면 이 목록에 더한다. */
export const COMMUNITY_EDGE_KINDS: readonly EdgeKind[] = ["related"];

/** 고정 설정. 나머지(크기·유리·라벨·밀도)는 데스크톱 기본값 그대로다. */
const PREFS: ViewPrefs = {
  ...defaultPrefs(),
  view: "3d", layout: "temporalRoots", timeBasis: "recorded", edges: "always", when: 1,
  unlinked: "show", labels: "hover",
  edgeOff: EDGE_KINDS.filter((k) => !COMMUNITY_EDGE_KINDS.includes(k)),
};

type Page = {
  slug: string; title?: string; isRules?: boolean; status?: string; topic?: string | null;
  kind?: string | null; createdAt?: string; updatedAt?: string;
  links?: { slug: string; type: string }[];
};

const COPY = {
  en: { loading: "Loading the graph…", failed: "Could not load this community.", empty: "This community has no pages yet." },
  ko: { loading: "그래프를 불러오는 중입니다…", failed: "커뮤니티를 불러오지 못했습니다.", empty: "아직 페이지가 없습니다." },
};

/** 렌더러가 아는 종류만 넘긴다. 사람·작품 같은 개체는 데스크톱이 개체 노드를 그리는
 *  모양(사면체)으로 간다. 종류가 없으면 개념이다. */
const RENDER_KINDS = new Set(["concept", "decision", "episode", "record", "bridge"]);
const kindOf = (k?: string | null): string => !k ? "concept" : RENDER_KINDS.has(k) ? k : "entity";

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
    const type = String(l.type || "relates_to").toUpperCase();
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
  const nodes = shown.map((p): GraphNode => ({
    id: p.slug, label: p.title || p.slug, domain: topic.get(p.slug) || "",
    degree: degree.get(p.slug) || 0, kind: kindOf(p.kind),
    // 기록 시각 축. 만든 시각이 아직 안 실려 오면 고친 시각으로 대신한다.
    ts: p.createdAt || p.updatedAt || "",
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

/** 노드를 누르면 맨 위 창이 그 페이지로 간다. 주제 마크는 페이지가 없으므로 아무 일도 없다. */
const open = (n: N3) => {
  if (n.kind === "domain" || n.id.startsWith("domain:")) return;
  const url = `/community/${encodeURIComponent(slug)}/p/${encodeURIComponent(n.id)}`;
  try { (window.top ?? window).location.assign(url); } catch { window.location.assign(url); }
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
}

void main();
