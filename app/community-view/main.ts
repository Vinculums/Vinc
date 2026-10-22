/** 커뮤니티 그래프 보기: 공개 커뮤니티 하나를 데스크톱 그래프와 같은 렌더러로
 *  읽기 전용으로 그린다. 손잡이와 범례 편집은 없다.
 *
 *  배치는 여기서 직접 한다. 모든 주제가 **한 시간축**을 나눠 쓴다: 축 방향이 그 일이
 *  일어난 때(`when.start`)이고 주제는 축을 가로지르는 띠(lane)다. 같은 해는 어느 띠에서나
 *  같은 자리에 선다. 데스크톱의 시간순 가지(ELK 워커·libavoid 경로)는 주제마다 따로 순서를
 *  매겨서 띠 사이의 해가 맞지 않았고, 꺾인 경로가 화면을 그물로 덮었다. 선은 모두 직선이다.
 *
 *  `app/src` 밖에 두는 이유: 이 화면은 사이드카가 아니라 사이트의 공개 커뮤니티 API 를
 *  부른다. `app/src` 의 호출은 사이드카 계약과 대조된다(`dev/sidecar-contract-check.ts`).
 *
 *  ⚠ 데스크톱의 저장된 설정(localStorage)을 읽지도 쓰지도 않는다. `mount3d` 의 prefs
 *  기본 인자가 `loadPrefs()` 라서 아래 `PREFS` 를 반드시 명시로 넘긴다. */
import { mount3d, type N3, type L3 } from "../src/surfaces/view3d";
import { defaultPrefs, type ViewPrefs } from "../src/view-prefs";
import { palette, hexStr, type ThemeMode } from "../src/theme";
import { tintMap } from "../src/view-tint";

/** 고정 설정. 노드는 데스크톱 기본(0.7)의 두 배라 종류별 모양이 읽힌다. */
const PREFS: ViewPrefs = {
  ...defaultPrefs(),
  view: "3d", edges: "always", when: 1, unlinked: "show", labels: "hover",
  nodeScale: 1.4, edgeOff: [],
};

type Page = {
  slug: string; title?: string; isRules?: boolean; status?: string; topic?: string | null;
  kind?: string | null; createdAt?: string; updatedAt?: string;
  links?: { slug: string; type: string }[];
  /** 그 일이 일어난 때. 각 값은 `YYYY`·`YYYY-MM`·`YYYY-MM-DD` 이고 기원전은 `-0300` 이다.
   *  `approx` 는 어림값이라는 표시인데 날짜 있는 페이지와 같이 그린다. */
  when?: { start?: string; end?: string; approx?: boolean } | null;
};

const COPY = {
  en: {
    loading: "Loading the graph…", failed: "Could not load this community.", empty: "This community has no pages yet.",
    concept: "Concept", event: "Event", result: "Result", decision: "Decision", personWork: "Person / work",
    estimated: "Date estimated from links",
    axisAcross: "Left to right: when it happened. Longer gaps between years get more room.",
    axisDown: "Top to bottom: when it happened. Longer gaps between years get more room.",
    noTopic: "No topic", year: (y: number) => y < 0 ? `${-y} BC` : String(y),
  },
  ko: {
    loading: "그래프를 불러오는 중입니다…", failed: "커뮤니티를 불러오지 못했습니다.", empty: "아직 페이지가 없습니다.",
    concept: "개념", event: "사건", result: "결과", decision: "결정", personWork: "인물·작품",
    estimated: "이어진 페이지로 짐작한 날짜",
    axisAcross: "왼쪽에서 오른쪽으로 일어난 때 순서입니다. 햇수가 멀리 떨어질수록 간격이 넓어집니다.",
    axisDown: "위에서 아래로 일어난 때 순서입니다. 햇수가 멀리 떨어질수록 간격이 넓어집니다.",
    noTopic: "주제 없음", year: (y: number) => y < 0 ? `기원전 ${-y}` : String(y),
  },
};

/** 페이지 종류 → 렌더러 종류. 사건은 에피소드, 결과는 기록, 사람·작품은 개체로 그려
 *  데스크톱과 같은 모양을 받는다. 모르는 종류와 빈 값은 개념이다. */
const RENDER_KIND: Record<string, string> = {
  concept: "concept", event: "episode", result: "record", decision: "decision",
  person: "entity", work: "entity",
};
const kindOf = (k?: string | null): string => RENDER_KIND[k || ""] ?? "concept";

/** `YYYY[-MM[-DD]]`(기원전은 앞에 `-`) → 소수 연도. 못 읽으면 null. */
export function yearOf(value?: string | null): number | null {
  const m = /^(-?)(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$/.exec((value || "").trim());
  if (!m) return null;
  const month = m[3] ? Math.min(Math.max(+m[3], 1), 12) : 1;
  const day = m[4] ? Math.min(Math.max(+m[4], 1), 31) : 1;
  return (m[1] ? -1 : 1) * Number(m[2]) + (month - 1) / 12 + (day - 1) / 372;
}

/** 날짜가 없는 페이지의 자리. 관계를 따라 가장 가까운 날짜 있는 페이지들 중 가장 이른
 *  때를 받고, 이어진 날짜가 없으면 같은 주제의 가장 이른 때에 선다. 주제에도 날짜가
 *  없으면 전체에서 가장 이른 때다. 이 값은 배치용이고 페이지에 날짜를 지어 붙이지 않는다. */
export function placeUndated(ids: string[], own: Map<string, number | null>, links: [string, string][],
  topic: Map<string, string>): Map<string, number> {
  const adj = new Map(ids.map((id) => [id, [] as string[]]));
  for (const [a, b] of links) { adj.get(a)?.push(b); adj.get(b)?.push(a); }
  const topicStart = new Map<string, number>();
  let first = Infinity;
  for (const id of ids) {
    const t = own.get(id), d = topic.get(id) || "";
    if (t == null) continue;
    first = Math.min(first, t);
    if (!topicStart.has(d) || t < topicStart.get(d)!) topicStart.set(d, t);
  }
  const out = new Map<string, number>();
  for (const id of ids) {
    const mine = own.get(id);
    if (mine != null) { out.set(id, mine); continue; }
    let ring = [id], found: number | null = null;
    const seen = new Set(ring);
    while (ring.length && found == null) {
      const next: string[] = [];
      for (const at of ring) for (const n of adj.get(at) || []) if (!seen.has(n)) { seen.add(n); next.push(n); }
      for (const n of next) { const t = own.get(n); if (t != null && (found == null || t < found)) found = t; }
      ring = next;
    }
    out.set(id, found ?? topicStart.get(topic.get(id) || "") ?? (Number.isFinite(first) ? first : 0));
  }
  return out;
}

type Item = { id: string; label: string; topic: string; kind: string; at: number; estimated: boolean; degree: number };
type Link = { source: string; target: string; type: string; cross: boolean };

/** 페이지 목록 → 그릴 항목과 관계. 시작 페이지와 규칙 페이지는 지도에 없는 안내 문서라 뺀다. */
export function modelOf(pages: Page[]): { items: Item[]; links: Link[] } {
  const shown = pages.filter((p) => p.slug && (p.status ?? "active") === "active" && !p.isRules
    && p.slug !== "start-here");
  const topic = new Map(shown.map((p) => [p.slug, (p.topic || "").trim()]));
  const links: Link[] = [];
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
    links.push({ source: p.slug, target: l.slug, type, cross: topic.get(p.slug) !== topic.get(l.slug) });
  }
  const degree = new Map<string, number>();
  for (const e of links) for (const id of [e.source, e.target]) degree.set(id, (degree.get(id) || 0) + 1);
  const own = new Map(shown.map((p) => [p.slug, yearOf(p.when?.start)]));
  const at = placeUndated(shown.map((p) => p.slug), own, links.map((e) => [e.source, e.target]), topic);
  const items = shown.map((p): Item => ({
    id: p.slug, label: p.title || p.slug, topic: topic.get(p.slug) || "", kind: kindOf(p.kind),
    at: at.get(p.slug)!, estimated: own.get(p.slug) == null, degree: degree.get(p.slug) || 0,
  }));
  return { items, links };
}

type P3 = { x: number; y: number; z: number };
type Laid = {
  pos: Map<string, P3>;
  /** 눈금을 달 해(정수), 그 축 위치, 그 해에 선 페이지 수. */
  years: { year: number; at: P3; count: number }[];
  lanes: { topic: string; at: P3 }[];
  axis: [P3, P3];
  /** 맞춤이 화면 안에 넣어야 하는 글자 자리(눈금 이름·주제 이름이 뻗는 쪽). */
  margins: P3[];
};

/** 노드 반지름(장면 단위). 렌더러의 `nodeRadius`(연결수 방식)와 같은 식이다. 겹침을 피해
 *  줄을 나눌 때만 쓴다. */
const radiusOf = (degree: number) => Math.min(3 + 1.4 * Math.sqrt(Math.max(degree, 1)), 12) * PREFS.nodeScale;

/** 한 시간축 배치(장면 단위). 이웃한 서로 다른 해 사이의 거리는 햇수 차의 로그로 자라서
 *  기원전 300년, 1600년대, 2020년대가 한 화면에서 다 읽힌다. 주제 띠는 가장 이른 항목 순으로
 *  쌓고, 같은 자리에 몰린 항목은 띠 안의 줄로 비켜 서며 줄마다 깊이를 조금 준다.
 *  `down` 이면(세로 화면) 시간이 위에서 아래로 흐르고 띠가 옆으로 늘어선다. */
export function axisLayout(items: Item[], down: boolean): Laid {
  const UNIT = 34, MIN_STEP = .3, ROW = 2.4 * radiusOf(4), LANE_GAP = 40, PAD = 6;
  const years = [...new Set(items.map((n) => Math.floor(n.at)))].sort((a, b) => a - b);
  const along = new Map<number, number>();
  years.forEach((y, i) => along.set(y, i ? along.get(years[i - 1])! + UNIT * (MIN_STEP + Math.log10(1 + y - years[i - 1])) : 0));
  const step = (y: number) => {
    const i = years.indexOf(y);
    return i < years.length - 1 ? along.get(years[i + 1])! - along.get(y)! : UNIT * MIN_STEP;
  };
  // 해 안의 달·날은 그 해 눈금 바로 뒤 좁은 폭에만 펼친다. 다음 해가 멀어도 눈금에서 떨어지지 않는다.
  const axisAt = (t: number) => { const y = Math.floor(t); return along.get(y)! + (t - y) * Math.min(step(y), UNIT * MIN_STEP) * .9; };
  const first = new Map<string, number>();
  for (const n of items) if (!first.has(n.topic) || n.at < first.get(n.topic)!) first.set(n.topic, n.at);
  const topics = [...first.keys()].sort((a, b) => first.get(a)! - first.get(b)! || a.localeCompare(b));
  const pos = new Map<string, P3>();
  const lanes: Laid["lanes"] = [];
  let cursor = 0;
  for (const topic of topics) {
    const members = items.filter((n) => n.topic === topic)
      .sort((a, b) => a.at - b.at || b.degree - a.degree || a.id.localeCompare(b.id));
    // 띠 안의 줄은 처음부터 셋을 돌아가며 쓴다. 한 줄에 늘어서면 같은 띠의 선이 한 직선 위에
    // 포개져 어느 노드를 잇는지 읽히지 않는다. 자리가 차 있으면 다음 빈 줄로 비킨다.
    const ends: number[] = [0, 0, 0].map(() => -Infinity);
    const placed: { id: string; a: number; row: number }[] = [];
    members.forEach((n, i) => {
      const a = axisAt(n.at), r = radiusOf(n.degree);
      const free = (row: number) => ends[row] + PAD <= a - r;
      let row = free(i % 3) ? i % 3 : ends.findIndex((_, k) => free(k));
      if (row < 0) { row = ends.length; ends.push(0); }
      ends[row] = a + r;
      placed.push({ id: n.id, a, row });
    });
    // 줄 0 이 띠 가운데, 그다음 줄은 위아래로 번갈아 선다.
    const offset = (row: number) => (row % 2 ? 1 : -1) * Math.ceil(row / 2) * ROW;
    const half = Math.max(...placed.map((p) => Math.abs(offset(p.row))), ROW) + ROW / 2;
    const centre = cursor + half;
    for (const p of placed) {
      const b = centre + offset(p.row), z = ((p.row % 3) - 1) * 6;
      pos.set(p.id, down ? { x: b, y: -p.a, z } : { x: p.a, y: -b, z });
    }
    // 주제 이름은 띠의 시작 위(가로) 또는 띠 머리(세로)에 선다.
    lanes.push({ topic, at: down ? { x: centre, y: 16, z: 0 } : { x: 0, y: -(centre - half - 12), z: 0 } });
    cursor = centre + half + LANE_GAP;
  }
  const end = along.get(years.at(-1)!)!;
  const edge = cursor - LANE_GAP + 18;   // 띠들 바깥, 눈금이 서는 줄
  const at = (a: number): P3 => down ? { x: -18, y: -a, z: 0 } : { x: a, y: -edge, z: 0 };
  const margins = down
    ? [{ x: -18 - 3 * UNIT, y: 0, z: 0 }, { x: -18 - 3 * UNIT, y: -end, z: 0 },
      ...lanes.map((l) => ({ ...l.at, y: 10 * UNIT })), { x: cursor + 3 * UNIT, y: 10 * UNIT, z: 0 }]
    : [{ x: 0, y: -edge - 1.3 * UNIT, z: 0 }, { x: end, y: -edge - 1.3 * UNIT, z: 0 }, { x: 0, y: 1.5 * UNIT, z: 0 }];
  const count = new Map<number, number>();
  for (const n of items) if (!n.estimated) count.set(Math.floor(n.at), (count.get(Math.floor(n.at)) || 0) + 1);
  return { pos, lanes, years: years.map((year) => ({ year, at: at(along.get(year)!), count: count.get(year) || 0 })),
    axis: [at(0), at(end)], margins };
}

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
let restyle = () => {};
const applyTheme = () => {
  const mode = modeNow();
  if (root.dataset.theme === mode) return;
  root.dataset.theme = mode;
  // 렌더러의 안개가 배경색과 같아야 원경이 바탕에 녹는다.
  document.body.style.background = hexStr(palette[mode].background);
  document.body.style.color = hexStr(palette[mode].textMute);
  (graph as { __retheme?: () => void } | null)?.__retheme?.();
  restyle();
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
/** 누른 자리에서 가장 가까운 문서 점. 다른 것이 클릭을 먼저 받으면 이것으로 넘긴다. */
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

/** 노드를 누르면 맨 위 창이 그 페이지로 간다. 페이지가 아닌 것을 눌렀으면 그 자리의 문서 점으로 간다. */
const open = (n: N3) => {
  const page = isPage(n) ? n : graph && pressed ? nearestPage(graph, pressed) : null;
  if (!page) return;
  const url = `/community/${encodeURIComponent(slug)}/p/${encodeURIComponent(page.id)}`;
  // 프레임 안이면 주소를 부모(커뮤니티 화면)에 건네고 부모가 옮긴다. 프레임이 맨 위 창의 주소를
  // 직접 바꾸면 Chrome 이 그 이동을 취소했다(ERR_ABORTED, 같은 출처에서도).
  if (window.parent !== window) { window.parent.postMessage({ type: "vinc:community-open", url }, location.origin); return; }
  window.location.assign(url);
};

const SVG = "http://www.w3.org/2000/svg";
const svgEl = <K extends keyof SVGElementTagNameMap>(tag: K, attrs: Record<string, string | number> = {}) => {
  const el = document.createElementNS(SVG, tag) as SVGElementTagNameMap[K];
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  return el;
};

/** 범례: 종류별 모양(렌더러와 같은 구·사면체)과 짐작한 날짜(윤곽선), 축의 뜻 한 줄.
 *  읽기 전용이고 누를 곳이 없다. */
function legend(down: boolean): HTMLElement {
  const box = document.createElement("aside");
  box.className = "legend";
  const shape = (kind: "sphere" | "tetra", hollow = false) => {
    const s = svgEl("svg", { width: 14, height: 14, viewBox: "0 0 14 14", "aria-hidden": "true" });
    s.append(kind === "sphere" ? svgEl("circle", { cx: 7, cy: 7, r: 5.2 }) : svgEl("path", { d: "M7 1.6 12.6 12H1.4Z" }));
    s.classList.toggle("hollow", hollow);
    return s;
  };
  const rows: [ReturnType<typeof shape>, string][] = [
    [shape("sphere"), text.concept], [shape("tetra"), text.event], [shape("tetra"), text.result],
    [shape("sphere"), text.decision], [shape("tetra"), text.personWork], [shape("sphere", true), text.estimated],
  ];
  const list = document.createElement("ul");
  for (const [icon, label] of rows) {
    const li = document.createElement("li");
    li.append(icon, document.createTextNode(label));
    list.append(li);
  }
  const axis = document.createElement("p");
  axis.textContent = down ? text.axisDown : text.axisAcross;
  box.append(list, axis);
  return box;
}

/** 해 눈금·주제 이름 겹층. 3D 좌표를 화면에 투영해 카메라가 움직일 때마다 다시 놓는다. */
function overlay(g: ReturnType<typeof mount3d>, laid: Laid, scale: number, down: boolean) {
  const svg = svgEl("svg", { class: "axis" });
  const line = svgEl("line");
  const ticks = laid.years.map(({ year }) => {
    const group = svgEl("g");
    const mark = svgEl("line"), label = svgEl("text");
    label.textContent = text.year(year);
    group.append(mark, label);
    return { group, mark, label };
  });
  const lanes = laid.lanes.map(({ topic }) => {
    const label = svgEl("text", { class: "lane" });
    // 세로 화면의 띠 이름은 비스듬히 서서 길면 화면 위로 넘친다. 그때만 줄인다.
    // 주제 경로는 주소 꼴이라(`relativity-and-cosmology`) 사이트 목록과 같이 띄우고 첫 글자만 올린다.
    const words = String(topic || "").split("/").pop()!.replace(/-/g, " ");
    const name = words ? words.charAt(0).toUpperCase() + words.slice(1) : text.noTopic;
    label.textContent = down && name.length > 16 ? name.slice(0, 15) + "…" : name;
    return label;
  });
  // 주제 이름은 그 주제 노드의 색(렌더러와 같은 표)이다. 테마가 바뀌면 다시 칠한다.
  const retint = () => {
    const tints = tintMap(laid.lanes.map((l) => l.topic), modeNow());
    laid.lanes.forEach(({ topic }, i) => { lanes[i].style.fill = tints.get(topic) || ""; });
  };
  retint();
  svg.append(line, ...ticks.map((t) => t.group), ...lanes);
  stage.append(svg);
  const screen = (p: P3) => g.graph2ScreenCoords(p.x * scale, p.y * scale, p.z * scale);
  const place = () => {
    const [a, b] = laid.axis.map(screen);
    Object.entries({ x1: a.x, y1: a.y, x2: b.x, y2: b.y }).forEach(([k, v]) => line.setAttribute(k, v.toFixed(1)));
    // 이름을 달 해는 페이지가 많이 선 해부터 고르고, 같으면 끝이 둥근 해를 먼저 고른다. 이미 고른
    // 눈금과 화면에서 너무 가까운 해는 건너뛴다. 확대하면 더 많은 해가 나온다.
    const gap = down ? 20 : 46;
    const round = (y: number) => y % 100 === 0 ? 3 : y % 50 === 0 ? 2 : y % 10 === 0 ? 1 : 0;
    const spots = laid.years.map(({ at }) => screen(at));
    const order = laid.years.map((_, i) => i).sort((a, b) =>
      (b === laid.years.length - 1 || b === 0 ? 1 : 0) - (a === laid.years.length - 1 || a === 0 ? 1 : 0)
      || laid.years[b].count - laid.years[a].count || round(laid.years[b].year) - round(laid.years[a].year) || a - b);
    const chosen: number[] = [];
    const shown = new Set<number>();
    for (const i of order) {
      const along = down ? spots[i].y : spots[i].x;
      if (chosen.some((c) => Math.abs(c - along) < gap)) continue;
      chosen.push(along); shown.add(i);
    }
    laid.years.forEach((_, i) => {
      const s = spots[i], t = ticks[i];
      t.group.style.display = shown.has(i) ? "" : "none";
      if (!shown.has(i)) return;
      const [dx, dy] = down ? [-6, 0] : [0, 6];
      Object.entries({ x1: s.x, y1: s.y, x2: s.x + dx, y2: s.y + dy }).forEach(([k, v]) => t.mark.setAttribute(k, v.toFixed(1)));
      t.label.setAttribute("x", (s.x + (down ? -9 : 0)).toFixed(1));
      t.label.setAttribute("y", (s.y + (down ? 4 : 19)).toFixed(1));
      t.label.setAttribute("text-anchor", down ? "end" : "middle");
    });
    // 세로 화면의 띠는 좁아서 이름을 비스듬히 세운다.
    laid.lanes.forEach(({ at }, i) => {
      const s = screen(at), x = s.x.toFixed(1), y = s.y.toFixed(1);
      lanes[i].setAttribute("x", x);
      lanes[i].setAttribute("y", y);
      lanes[i].setAttribute("text-anchor", "start");
      lanes[i].setAttribute("transform", down ? `rotate(-70 ${x} ${y})` : "");
    });
  };
  // 카메라가 움직이는 동안(끌기·맞춤 애니메이션)만 매 프레임 다시 놓는다.
  let until = 0, raf = 0;
  const tick = () => { place(); raf = performance.now() < until ? requestAnimationFrame(tick) : 0; };
  const follow = (ms = 120) => { until = Math.max(until, performance.now() + ms); if (!raf) raf = requestAnimationFrame(tick); };
  (g.controls() as { addEventListener?: (t: string, f: () => void) => void }).addEventListener?.("change", () => follow());
  return { follow, retint };
}

/** 겹층과 범례의 색. 테마가 바뀌면 다시 칠한다. */
function paintChrome() {
  const p = palette[modeNow()];
  let style = document.getElementById("chrome-style") as HTMLStyleElement | null;
  if (!style) { style = document.createElement("style"); style.id = "chrome-style"; document.head.append(style); }
  style.textContent = `
    svg.axis { position: fixed; inset: 0; width: 100%; height: 100%; pointer-events: none; overflow: visible; }
    svg.axis line { stroke: ${hexStr(p.textMute)}; stroke-width: 1; }
    svg.axis text { fill: ${hexStr(p.textSoft)}; font: 11px/1 system-ui, sans-serif; font-variant-numeric: tabular-nums; }
    svg.axis text.lane { font-weight: 600; font-size: 12px; }
    .legend { position: fixed; left: 12px; bottom: 12px; max-width: min(320px, calc(100vw - 24px)); padding: 8px 10px;
      border: 1px solid ${hexStr(p.line)}; border-radius: 8px; background: ${hexStr(p.card)}e6; color: ${hexStr(p.textSoft)};
      font: 12px/1.4 system-ui, sans-serif; pointer-events: none; box-sizing: border-box; word-break: keep-all; }
    .legend ul { display: flex; flex-wrap: wrap; gap: 2px 12px; margin: 0; padding: 0; list-style: none; }
    .legend li { display: flex; align-items: center; gap: 5px; }
    .legend svg * { fill: ${hexStr(p.text)}; }
    .legend svg.hollow * { fill: none; stroke: ${hexStr(p.textMute)}; stroke-width: 1.2; }
    .legend p { margin: 6px 0 0; color: ${hexStr(p.textMute)}; }`;
}

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
  const { items, links } = modelOf(pages);
  if (!items.length) { say(text.empty); return; }
  say("");
  pageIds = new Set(items.map((n) => n.id));
  // 세로가 긴 화면이면 시간이 위에서 아래로 흐른다. 띠가 화면 폭을 나눠 쓴다.
  const down = (stage.clientWidth || 1200) < (stage.clientHeight || 750);
  // 범례 자리는 무대에서 뺀다. 그래프와 눈금이 범례 밑에 깔리지 않는다.
  paintChrome();
  const key = legend(down);
  document.body.append(key);
  stage.style.bottom = `${key.offsetHeight + 20}px`;
  const laid = axisLayout(items, down);
  // 렌더러는 단위 좌표에 고정 배율을 곱한다. 장면 단위로 지은 배치를 그 단위로 옮긴다.
  const UNIT = 140;
  const raw = (p: P3) => ({ x: p.x / UNIT, y: p.y / UNIT, z: p.z / UNIT });
  const nodes: N3[] = items.map((n) => ({
    id: n.id, label: n.label, domain: n.topic, degree: n.degree, kind: n.kind,
    // 날짜를 짐작한 페이지는 윤곽선으로 그린다(렌더러의 gateway 모양).
    gateway: n.estimated, focus: false, ...raw(laid.pos.get(n.id)!),
  }));
  // 선은 모두 두 점짜리 직선 경로다. 주제를 건너는 선은 평소 옅고, 짚은 노드의 선만 밝아진다.
  const lines: L3[] = links.map((e) => ({ source: e.source, target: e.target, type: e.type,
    cross: e.cross, faint: e.cross,
    route: { engine: "spatial" as const, points: [raw(laid.pos.get(e.source)!), raw(laid.pos.get(e.target)!)] } }));
  const topics = laid.lanes.map((l) => l.topic);
  graph = mount3d(stage, nodes, lines, { onClick: open }, PREFS, null, topics);
  // 읽기 전용이다. 노드를 끌어 옮기는 손짓도 끈다.
  graph.enableNodeDrag(false);
  const placed = (graph.graphData().nodes as N3[]).find((n) => n.x && Math.abs(raw(laid.pos.get(n.id)!).x) > 1e-6);
  const scale = placed ? placed.x! / raw(laid.pos.get(placed.id)!).x : UNIT;
  const { follow, retint } = overlay(graph, laid, scale / UNIT, down);
  restyle = () => { paintChrome(); retint(); };
  // 맞춤은 눈금과 주제 이름까지 화면 안에 들게 한다.
  const extra = [...laid.years.map((y) => y.at), ...laid.lanes.map((l) => l.at), ...laid.axis, ...laid.margins]
    .map((p) => ({ x: p.x * scale / UNIT, y: p.y * scale / UNIT, z: p.z * scale / UNIT }));
  // 정면에서 본다. 위나 옆에서 보면 원근 때문에 위쪽 띠와 아래 눈금의 같은 해가 어긋난다
  // (가로 1440 에서 30px 남짓). 깊이는 줄마다 조금만 줘서 겹친 노드만 갈라 보이게 한다.
  const view = [0, 0, 1];
  whenDrawn(graph, () => { fit(graph!, 600, extra, view); follow(1100); });
  let resized = 0;
  window.addEventListener("resize", () => {
    clearTimeout(resized);
    resized = window.setTimeout(() => { if (graph) { fit(graph, 200, extra); follow(500); } }, 200);
  });
}

/** 메시가 다 생기고 렌더러의 첫 맞춤(400ms 애니메이션)이 끝난 뒤에 부른다. */
function whenDrawn(g: ReturnType<typeof mount3d>, then: () => void, tries = 0) {
  const ns = g.graphData().nodes as { __threeObj?: unknown }[];
  if (ns.some((n) => !n.__threeObj) && tries < 120) { requestAnimationFrame(() => whenDrawn(g, then, tries + 1)); return; }
  setTimeout(then, 700);
}

/** 화면 비율까지 따져 그래프가 프레임을 채우게 한다. 렌더러의 `zoomToFit` 은 세로 시야각
 *  하나로 재서 옆으로 긴 시간축이 넓은 화면 가운데 작게 남는다. `view` 를 주면 그 방향에서
 *  보고, 주지 않으면 보던 방향을 그대로 두고 중심과 거리만 다시 잡는다. `extra` 는 노드 말고도
 *  화면에 들어야 하는 점(눈금·주제 이름)이다. */
function fit(g: ReturnType<typeof mount3d>, ms = 500, extra: P3[] = [], view?: number[]) {
  const cam = g.camera() as unknown as { fov: number; aspect: number; position: { x: number; y: number; z: number } };
  const ns = [...(g.graphData().nodes as { x?: number; y?: number; z?: number }[]).filter((n) => Number.isFinite(n.x)), ...extra];
  if (!ns.length) return;
  const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  for (const n of ns) [n.x!, n.y || 0, n.z || 0].forEach((v, i) => { lo[i] = Math.min(lo[i], v); hi[i] = Math.max(hi[i], v); });
  const c = lo.map((v, i) => (v + hi[i]) / 2);
  const norm = (v: number[]) => { const l = Math.hypot(...v) || 1; return v.map((x) => x / l); };
  const cross = (a: number[], b: number[]) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  const dot = (a: number[], b: number[]) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const dir = norm(view ?? [cam.position.x - c[0], cam.position.y - c[1], cam.position.z - c[2]]);
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
  d *= 1.1;  // 가장자리 여백(범례 자리 포함)
  const pos = { x: centre[0] + dir[0] * d, y: centre[1] + dir[1] * d, z: centre[2] + dir[2] * d };
  g.cameraPosition(pos, { x: centre[0], y: centre[1], z: centre[2] }, ms);
  // 렌더러의 안개는 컨트롤의 change 로 카메라 거리에 맞춘다. 코드로 옮긴 카메라도 알린다.
  setTimeout(() => (g.controls() as { dispatchEvent?: (e: { type: string }) => void }).dispatchEvent?.({ type: "change" }), ms + 20);
}

void main();
