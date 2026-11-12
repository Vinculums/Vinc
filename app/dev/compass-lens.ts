/** 나침반 렌즈(view-compass.ts)를 백엔드 없이 정해진 데이터로 띄우는 손 확인 판. CI 는 돌리지 않는다.
 *
 *      npm run dev  →  /dev/compass-lens.html?seed=7&theme=dark&lang=en&skin=desktop
 *
 *  데이터는 씨앗(seed)으로 정해지는 합성 그래프다: 뿌리 하나 · 영역 다섯 · 세 단 깊이의 주제 41개, 종류가 섞인 노드 약 사백(날짜 ·
 *  유효 기간 포함), 주제 안과 주제 사이의 관계, 다리, "더 보기"가 서는 큰 주제 하나. 바깥에 내는 것은 나침반의 공개 손잡이뿐이다
 *  (mountCompass · compassDataFrom · 핸들). 그래서 이 판은 손대기 전 main 과 고친 뒤 가지에서 같은 그림을 낸다
 *  (비교 하네스 compass-compare.mjs 가 둘을 나란히 돌린다).
 *
 *  쿼리: seed · theme(dark|light) · lang(en|ko) · skin(desktop|web) · start(주제 경로, 예: lab/ledger) · store(고정 열쇠) ·
 *  restore(JSON, 다시 그릴 때 받는 자리) · side(closed 면 사이드바를 접고 연다).
 *  창 손잡이: __h(핸들) · __mount(o) · __destroy() · __remount(restore) · __ids · __retheme(mode) · __side(open). */
import "../src/app.css";
import { mountCompass, compassDataFrom } from "../src/surfaces/view-compass";

type GNode = { id: string; kind: string; title: string; label: string; domain: string; ts: string; content_ts?: string; valid_from?: string; valid_until?: string; degree: number };
type GEdge = { source: string; target: string; type: string };

const q = new URLSearchParams(location.search);
const SEED = Number(q.get("seed") || 7);

// ── 합성 그래프: 같은 씨앗이면 늘 같은 노드 · 관계 · 날짜 ───────────────────────────────
function graph(seed: number): { nodes: GNode[]; edges: GEdge[] } {
  let s = seed >>> 0; const rnd = () => (s = (Math.imul(s, 1664525) + 1013904223) >>> 0) / 4294967296;
  const pick = <T,>(a: T[]): T => a[Math.floor(rnd() * a.length)];
  const words = ["ledger", "review", "gate", "policy", "harness", "routing", "quota", "cache", "replica", "token", "audit", "session",
    "schema", "drift", "anchor", "budget", "window", "merge", "signal", "lattice"];
  const day = (lo: number, hi: number) => { const d = new Date(Date.UTC(2026, 6, 1) + Math.floor(lo + rnd() * (hi - lo)) * 864e5); return d.toISOString().slice(0, 10); };
  const areas = ["ledger", "review", "gate", "policy", "harness"], subs = ["core", "edge", "notes"], deep = ["alpha", "beta"];
  const topics: string[] = ["lab"];
  for (const a of areas) { topics.push(`lab/${a}`); subs.forEach((t, i) => { topics.push(`lab/${a}/${t}`); if (i < 2) for (const u of deep) topics.push(`lab/${a}/${t}/${u}`); }); }
  const nodes: GNode[] = [], edges: GEdge[] = [], byTopic = new Map<string, string[]>();
  const add = (kind: string, domain: string, i: number, extra: Partial<GNode> = {}) => {
    const n = 1 + Math.floor(rnd() * 4), w = Array.from({ length: n }, () => pick(words)).join(" ");
    const pre = kind === "doc" ? "concept:doc." : `${kind}:`, id = `${pre}${domain.replace(/\//g, "-")}-${kind}-${i}`;
    const title = kind === "concept" && rnd() < .2 ? `${w}: a longer explanation of the ${pick(words)} rule` : kind === "decision" ? `Decide the ${w} ${i}` : `${w[0].toUpperCase()}${w.slice(1)} ${i}`;
    const node: GNode = { id, kind: kind === "doc" ? "concept" : kind, title, label: title, domain, ts: day(0, 130), degree: 0, ...extra };
    if (rnd() < .35) node.content_ts = day(0, 130);
    if (kind === "decision") { node.valid_from = day(10, 120); if (rnd() < .25) node.valid_until = day(120, 135); }
    nodes.push(node); if (!byTopic.has(domain)) byTopic.set(domain, []); byTopic.get(domain)!.push(id); return id; };
  for (const t of topics) {
    const big = t === "lab/ledger/core", depth = t.split("/").length;
    const nc = big ? 6 : depth === 1 ? 2 : 2 + Math.floor(rnd() * 5);
    for (let i = 0; i < nc; i++) add("concept", t, i);
    for (let i = 0; i < (depth > 2 && rnd() < .6 ? 1 + Math.floor(rnd() * 2) : 0); i++) add("doc", t, i);
    const nr = big ? 30 : Math.floor(rnd() * 4), nd = big ? 22 : Math.floor(rnd() * 3), ne = big ? 8 : Math.floor(rnd() * 2);
    for (let i = 0; i < nr; i++) add("record", t, i);
    for (let i = 0; i < nd; i++) add("decision", t, i);
    for (let i = 0; i < ne; i++) add("episode", t, i);
  }
  // 주제 없는 노드 둘(뿌리에 선다)
  add("concept", "", 0); add("record", "", 1);
  const con = nodes.filter((n) => n.kind === "concept").map((n) => n.id), mean = (t: string) => (byTopic.get(t) || []).filter((id) => id.startsWith("concept:"));
  const rel = (a: string, b: string, type: string) => { if (a && b && a !== b) edges.push({ source: a, target: b, type }); };
  for (const t of topics) { const m = mean(t); for (let i = 0; i + 1 < m.length; i++) if (rnd() < .7) rel(m[i], m[i + 1], "RELATED_TO"); }
  for (let i = 0; i < 90; i++) rel(pick(con), pick(con), "RELATED_TO");
  for (let i = 0; i < 26; i++) rel(pick(con), pick(con), "PREREQUISITE_OF");
  for (let i = 0; i < 18; i++) rel(pick(con), pick(con), "DERIVES_FROM");
  for (let i = 0; i < 12; i++) rel(pick(con), pick(con), "INHERITS_FROM");
  for (let i = 0; i < 4; i++) rel(pick(con), pick(con), "RETURNS_TO");
  // 한 줄로 이어지는 선행 사슬(따라가기)
  { const m = mean("lab/review/core"); for (let i = 0; i + 1 < m.length; i++) rel(m[i], m[i + 1], "PREREQUISITE_OF"); }
  const decs = nodes.filter((n) => n.kind === "decision").map((n) => n.id);
  for (let i = 0; i < 10; i++) rel(pick(decs), pick(decs), "SUPERSEDES");
  for (const n of nodes) {
    if (n.kind === "record" || n.kind === "episode" || n.kind === "decision") { const m = mean(n.domain); const host = m.length && rnd() < .8 ? pick(m) : pick(con); rel(n.id, host, rnd() < .7 ? "ABOUT" : "ATTACHES_TO"); }
    if (n.id.startsWith("concept:doc.")) rel(n.id, pick(mean(n.domain).filter((x) => !x.startsWith("concept:doc.")).concat(con.slice(0, 3))), "MENTIONS");
  }
  // 다리: 같은 영역 안 둘, 영역을 건너는 넷
  const ends = [["lab/ledger/core", "lab/ledger/edge"], ["lab/gate/core/alpha", "lab/gate/edge"], ["lab/ledger/core", "lab/review/core"],
    ["lab/policy/edge", "lab/harness/core", "lab/gate/notes"], ["lab/review/edge/beta", "lab/harness/notes"], ["lab/policy/core", "lab/ledger/notes"]];
  ends.forEach((ts, b) => { const id = `bridge:junction-${b}`; nodes.push({ id, kind: "bridge", title: `${pick(words)} ${pick(words)} junction`, label: `link ${b}`, domain: "", ts: day(40, 130), degree: 0 });
    for (const t of ts) rel(id, pick(mean(t)), "CONNECTS"); });
  const deg = new Map<string, number>(); for (const e of edges) { deg.set(e.source, (deg.get(e.source) || 0) + 1); deg.set(e.target, (deg.get(e.target) || 0) + 1); }
  for (const n of nodes) n.degree = deg.get(n.id) || 0;
  return { nodes, edges };
}

const G = graph(SEED);
const doc = document;
doc.body.innerHTML = `<style>
  html, body { margin: 0; height: 100%; }
  #surface { position: fixed; inset: 0; display: grid; grid-template-columns: auto 1fr; grid-template-rows: 40px 1fr; background: hsl(var(--background)); color: hsl(var(--foreground)); font-family: var(--font-ui); }
  #hd { grid-column: 1 / 3; display: flex; align-items: center; gap: 8px; padding: 0 12px; border-bottom: 1px solid hsl(var(--border)); }
  #side { width: 280px; overflow: auto; border-right: 1px solid hsl(var(--border)); font-size: 12px; }
  body[data-side="closed"] #side { width: 0; border: 0; }
  @media (max-width: 900px) { #side { position: absolute; top: 40px; bottom: 0; left: 0; z-index: 3; background: hsl(var(--card)); } body:not([data-side="open"]) #side { width: 0; border: 0; } }
  #side { grid-row: 2; grid-column: 1; }
  #host { position: relative; min-width: 0; grid-row: 2; grid-column: 2; }
  #tree button { display: block; width: 100%; text-align: left; border: 0; background: none; color: inherit; font: inherit; padding: 2px 8px; cursor: pointer; }
  #tree .g { font-weight: 600; margin-top: 6px; }
  #log { font-size: 11px; color: hsl(var(--muted-foreground)); }
</style>
<div id="surface" aria-live="polite"><header id="hd"><b>Graph</b><span id="tools"></span><input id="q" placeholder="filter" autocomplete="off"><span id="log"></span></header>
<aside id="side"><div id="dock"></div><div id="tree"></div></aside><div id="host"></div></div>`;
if (q.get("side") === "closed") doc.body.dataset.side = "closed";

type Handle = ReturnType<typeof mountCompass>;
let h: Handle | null = null;
const host = doc.getElementById("host")!, tree = doc.getElementById("tree")!, log = doc.getElementById("log")!;
const paintTree = (reveal: string[] = []) => {
  if (!h) { tree.innerHTML = ""; return; }
  tree.innerHTML = h.tree().map((g) => `<button class="g" data-open="topic:${g.domain}">${g.domain || "-"} ${g.rows.length}</button>`
    + (reveal.includes(g.domain) ? g.rows.map((r) => `<button data-open="${r.id}" style="padding-left:${16 + r.depth * 12}px">${r.title}</button>`).join("") : "")).join(""); };
function mount(o: { restore?: unknown; start?: string | null; store?: string | null } = {}) {
  const start = o.start ?? q.get("start");
  const startPath = start ? start.split("/").map((_, i, a) => `topic:${a.slice(0, i + 1).join("/")}`) : undefined;
  let restore = o.restore as never; if (restore === undefined && q.get("restore")) restore = JSON.parse(q.get("restore")!) as never;
  h = mountCompass(host, compassDataFrom(G.nodes, G.edges), { startPath, restore, store: o.store ?? q.get("store") ?? "lab",
    onOpen: (id) => { log.textContent = `open ${id}`; }, onChange: () => { log.dataset.centre = h ? h.centre() : ""; },
    onTree: (t) => paintTree(t.reveal), onDestroy: () => { doc.getElementById("dock")!.innerHTML = ""; doc.getElementById("tools")!.innerHTML = ""; } });
  doc.getElementById("dock")!.append(h.side); doc.getElementById("tools")!.append(h.tools); paintTree();
  log.dataset.centre = h.centre();
}
const W = window as unknown as Record<string, unknown>;
W.__ids = { nodes: G.nodes.map((n) => n.id), topics: [...new Set(G.nodes.map((n) => n.domain))] };
W.__mount = mount;
W.__destroy = () => { h?.destroy(); h = null; paintTree(); };
W.__remount = (restore: unknown) => { h?.destroy(); h = null; mount({ restore }); };
W.__snap = () => (h ? JSON.parse(JSON.stringify(h.snapshot())) : null);
W.__retheme = (mode: "dark" | "light") => { doc.documentElement.dataset.theme = mode; h?.retheme(); };
W.__side = (open: boolean) => { doc.body.dataset.side = open ? "open" : "closed"; };
Object.defineProperty(W, "__h", { get: () => h });
doc.getElementById("q")!.addEventListener("input", (e) => h?.filter((e.target as HTMLInputElement).value));
tree.addEventListener("click", (e) => { const b = (e.target as HTMLElement).closest<HTMLElement>("[data-open]"); if (b && h) h.open(b.dataset.open!); });
mount();
