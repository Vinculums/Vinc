/** 주제 색 측정 — 주제 색 계획(`tintPlan`)이 낼 수 있는 모든 색을, 그 색이 앉는 모든 면 위에서 잰다.
 *
 *     cd app && node dev/topic-tint-contrast.mjs                 # 이 트리
 *     cd app && node dev/topic-tint-contrast.mjs --src <app/src>  # 다른 트리(전후 비교)
 *     ... --full                                                  # 색마다 한 줄씩(긴 표)
 *     ... --raw <app/src>                                         # 바닥 전 색을 그 트리의 계획에서 읽는다
 *                                                                   (기본: 이 트리의 floorKit, 없으면 --src 의 계획 그대로)
 *
 *  색: 영역 24개 × 영역 안 주제 돌림 7칸(0, ±4, ±8, ±12°)을 실제 `tintPlan` 으로 만든다. 돌림은 7칸마다
 *  되풀이되므로 영역이 24개 이하인 어느 집합이든 이 안의 색만 쓴다. Agent 줄의 색은 `agent-map.ts`
 *  와 같은 보석 순서(호박 · 에메랄드 · 루비 · 사파이어, 정책은 자수정)에, 그 모듈이 거치는 `markColour`
 *  가 있으면 그것을 거친 값이다.
 *  면: 토큰 CSS(데스크탑 · 웹)의 HSL 채널과 app.css 의 줄 칠(쉼 · 고름 알파, 판 · 서랍 매핑)을 읽어 만든다.
 *  어느 값이든 못 찾으면 멈춘다(조용히 옛 값을 쓰지 않는다). */
import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { rgbOfHex, hexOfRgb, hslToRgb, over, mixSrgb, ratio, dOk, de2000, simulate, CVD_KINDS, oklch, lab, de2000Lab } from "./colour-math.mjs";

const args = process.argv.slice(2);
const opt = (k) => { const i = args.indexOf(k); return i < 0 ? null : args[i + 1]; };
const SRC = resolve(opt("--src") || resolve(dirname(fileURLToPath(import.meta.url)), "../src"));
const FULL = args.includes("--full");

const bundle = async (entry, src = SRC) => {
  const r = await build({ entryPoints: [resolve(src, entry)], bundle: true, platform: "node", format: "esm", write: false, logLevel: "silent" });
  return import(`data:text/javascript;base64,${Buffer.from(r.outputFiles[0].text).toString("base64")}`);
};
const tint = await bundle("view-tint.ts");
const RAWSRC = opt("--raw");
const rawTint = RAWSRC ? await bundle("view-tint.ts", resolve(RAWSRC)) : null;
const { topicJewels } = await bundle("theme.ts");
const read = (f) => readFileSync(resolve(SRC, f), "utf8");
const must = (m, what) => { if (!m) throw new Error(`못 찾았다: ${what}`); return m; };

// ── 면 ──────────────────────────────────────────────────────────────────────
function tokenBlock(css, dark) {
  const blocks = css.split(/\n(?=[:\[][^{]*\{)/);
  return must(blocks.find((b) => (dark ? /data-theme="dark"\]\s*\{/.test(b.split("\n")[0]) : !/data-theme/.test(b.split("\n")[0]) && /^:root/.test(b))), `토큰 블록 ${dark ? "dark" : "light"}`);
}
function tokenHsl(block, name) {
  const m = must(block.match(new RegExp(`--${name}:\\s*([\\d.]+)\\s+([\\d.]+)%\\s+([\\d.]+)%`)), `--${name}`);
  return hslToRgb(+m[1], +m[2], +m[3]);
}
const APP = read("app.css");
const alphaOf = (sel) => +must(APP.match(new RegExp(`${sel.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\{[^}]*?hsl\\(var\\(--(\\w[\\w-]*)\\)\\s*/\\s*([\\d.]+)\\)`)), sel)[2];
const tokOf = (sel) => must(APP.match(new RegExp(`${sel.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\{[^}]*?hsl\\(var\\(--(\\w[\\w-]*)\\)\\s*/`)), sel)[1];
const HOVER = { tok: tokOf(".lnb-doc:hover"), a: alphaOf(".lnb-doc:hover") };
const PICK = { tok: tokOf(".lnb-group.on"), a: alphaOf(".lnb-group.on") };
for (const [s, want] of [[".lnb-group:hover", HOVER], [".lnb-fold:hover", HOVER]])
  if (tokOf(s) !== want.tok || alphaOf(s) !== want.a) throw new Error(`${s} 가 .lnb-doc:hover 와 다르다: 면을 따로 재야 한다`);
{ const m = must(APP.match(/\.lnb-doc\.on\s*\{[^}]*hsl\(var\(--(\w+)\)\s*\/\s*([\d.]+)\)/), ".lnb-doc.on");
  if (m[1] !== PICK.tok || +m[2] !== PICK.a) throw new Error(".lnb-doc.on 이 .lnb-group.on 과 다르다"); }
const S0 = { light: must(APP.match(/:root\s*\{[\s\S]*?--surface-0:\s*var\(--(\w+)\)/), "surface-0 light")[1],
  dark: must(APP.match(/\[data-theme="dark"\]\s*\{[^}]*--surface-0:\s*var\(--(\w+)\)/), "surface-0 dark")[1] };
const S2 = must(APP.match(/--surface-2:\s*var\(--(\w+)\)/), "surface-2")[1];
const DOCK = must(APP.match(/#cmp-dock \.pi:hover\s*\{\s*background:\s*color-mix\(in srgb, var\(--c-point\) (\d+)%, var\(--c-card\)\)/), "#cmp-dock .pi:hover");
const DOCK_P = +DOCK[1] / 100;

/** 테마 · 스킨마다 사이드바 면과 그래프 · 범례 면. */
function grounds(skin, theme) {
  const css = read(`tokens.${skin}.css`), blk = tokenBlock(css, theme === "dark");
  const T = (n) => tokenHsl(blk, n);
  const base = T(S0[theme]), drawer = T(S2), hov = T(HOVER.tok), pick = T(PICK.tok), card = T("card");
  return {
    side: {
      base, "base+hover": over(hov, HOVER.a, base), "base+selected": over(pick, PICK.a, base),
      drawer, "drawer+hover": over(hov, HOVER.a, drawer), "drawer+selected": over(pick, PICK.a, drawer),
      "dock pin hover": mixSrgb(T("point"), DOCK_P, card),
    },
    graph: T("background"), legend: card, ring: T("ring"),
  };
}

// ── 색 ──────────────────────────────────────────────────────────────────────
const AREAS = 24, TURNS = 7;
function planColours(mode) {
  const doms = [];
  for (let a = 0; a < AREAS; a++) for (let t = 0; t < TURNS; t++) doms.push(`r/a${String(a).padStart(2, "0")}/t${t}`);
  const plan = tint.tintPlan(doms, mode), out = [];
  for (let a = 0; a < AREAS; a++) for (let t = 0; t < TURNS; t++) {
    const d = `r/a${String(a).padStart(2, "0")}/t${t}`;
    out.push({ rank: a, turn: t, hex: plan.topics.get(d) });
  }
  // 영역 색(범례 견본 · 영역 부피)은 돌림 0 과 같아야 한다
  for (let a = 0; a < AREAS; a++) if (plan.areas.get(`a${String(a).padStart(2, "0")}`) !== out[a * TURNS].hex) throw new Error(`영역 ${a} 색이 첫 주제와 다르다`);
  return out;
}
const hex6 = (n) => `#${n.toString(16).padStart(6, "0")}`;
const markOf = (h, mode) => (tint.markColour ? tint.markColour(h, mode) : h);
const agentColours = (mode) => [3, 2, 0, 1].map((i) => ({ name: ["amber", "emerald", "ruby", "sapphire"][[3, 2, 0, 1].indexOf(i)], hex: markOf(hex6(topicJewels[mode][i]), mode) }));
const policyColour = (mode) => markOf(hex6(topicJewels[mode][4]), mode);

// ── 마크 × 상태 → 그 상태에서 마크가 입는 색과 면 ─────────────────────────────
// 선택된 문서 줄 · 그래프 노드 줄의 표식은 주제 색이 아니라 --ring 이다(app.css `.lnb-doc.on .lnb-dot`,
// `.lnb-doc.on .lnb-gmark`). 포커스는 윤곽선만 그리고 면을 바꾸지 않는다(`:focus-visible` outline).
const SIDE = [
  ["document dot (ink 0, 1, 2)", "topic", { rest: "base", hover: "base+hover", focus: "base", selected: "ring" }],
  ["topic ring", "topic", { rest: "base", hover: "base+hover", focus: "base", selected: "base+selected" }],
  ["graph mark", "topic", { rest: "base", hover: "base+hover", focus: "base", selected: "ring" }],
  ["bridge fold mark", "topic", { rest: "base", hover: "base+hover", focus: "base" }],
  ["policy dot (row)", "policy", { rest: "base", hover: "base+hover", focus: "base", selected: "base+selected" }],
  ["policy mark (group)", "policy", { rest: "base", hover: "base+hover", focus: "base" }],
  ["Agent dot", "agent", { rest: "base", hover: "base+hover", focus: "base", selected: "base+selected" }],
  ["pinned dock mark", "topic", { rest: "base", hover: "dock pin hover" }],
];

const rows = [], fmt = (x) => x.toFixed(2);
let globalMin = Infinity;
for (const skin of ["desktop", "web"]) for (const mode of ["light", "dark"]) {
  const G = grounds(skin, mode), cols = planColours(mode);
  const sets = { topic: cols.map((c) => ({ label: `rank ${c.rank} turn ${c.turn}`, hex: c.hex })),
    agent: agentColours(mode).map((c) => ({ label: c.name, hex: c.hex })), policy: [{ label: "amethyst", hex: policyColour(mode) }] };
  for (const [mark, set, states] of SIDE) for (const [state, gname] of Object.entries(states)) for (const where of ["sidebar", "drawer"]) {
    if (gname === "dock pin hover" && where === "drawer") continue;
    let ground, colours;
    if (gname === "ring") { ground = G.side[where === "drawer" ? "drawer+selected" : "base+selected"]; colours = [{ label: "--ring", hex: hexOfRgb(G.ring) }]; }
    else { ground = G.side[where === "drawer" ? gname.replace("base", "drawer") : gname] ?? G.side[gname]; colours = sets[set]; }
    let worst = null;
    for (const c of colours) { const r = ratio(rgbOfHex(c.hex), ground); if (!worst || r < worst.r) worst = { r, c }; if (FULL) console.log([skin, mode, mark, state, where, c.label, c.hex, hexOfRgb(ground), fmt(r)].join("\t")); }
    rows.push({ skin, mode, mark, state, where, ground: hexOfRgb(ground), worst: worst.r, colour: `${worst.c.label} ${worst.c.hex}`, ring: gname === "ring" });
    if (gname !== "ring") globalMin = Math.min(globalMin, worst.r);
  }
  for (const [name, g] of [["graph surface", G.graph], ["legend card", G.legend]]) {
    let worst = null;
    for (const c of sets.topic) { const r = ratio(rgbOfHex(c.hex), g); if (!worst || r < worst.r) worst = { r, c }; }
    rows.push({ skin, mode, mark: name, state: "full colour", where: "compass", ground: hexOfRgb(g), worst: worst.r, colour: `${worst.c.label} ${worst.c.hex}` });
  }
}
if (FULL) process.exit(0);

console.log(`# Topic tint contrast, ${SRC}\n`);
console.log(`Grounds from tokens.{desktop,web}.css and app.css: hover = --${HOVER.tok} / ${HOVER.a}, selected = --${PICK.tok} / ${PICK.a}, base = --surface-0 (light --${S0.light}, dark --${S0.dark}), drawer = --surface-2 (--${S2}), dock hover = --point ${DOCK_P * 100}% over --card.\n`);
console.log(`Colours: ${AREAS} area ranks x ${TURNS} topic turns per theme from tintPlan; Agent dots ${tint.markColour ? "through markColour" : "raw jewels"}.\n`);
console.log("| skin | theme | mark | state | where | ground | worst ratio | worst colour |\n|---|---|---|---|---|---|---|---|");
for (const r of rows) console.log(`| ${r.skin} | ${r.mode} | ${r.mark} | ${r.state} | ${r.where} | ${r.ground} | ${fmt(r.worst)}${r.ring ? " (ring, untinted)" : r.worst < 4.5 && r.where !== "compass" ? " FAIL" : ""} | ${r.colour} |`);
console.log(`\nMinimum over every tinted sidebar mark, state and ground: ${fmt(globalMin)}:1 (${globalMin >= 4.5 ? "meets" : "below"} 4.5:1)\n`);

// ── 첫 열두 순위가 갈리는가 ─────────────────────────────────────────────────
console.log("## Distinguishability\n\n| theme | vision | min dE2000, first 12 area ranks | pair | min OKLab dE, first 12 | min dE2000, 7 turns within an area | \n|---|---|---|---|---|---|");
for (const mode of ["light", "dark"]) {
  const cols = planColours(mode), ranks = cols.filter((c) => c.turn === 0).slice(0, 12);
  for (const vis of ["normal", ...CVD_KINDS]) {
    const f = (h) => (vis === "normal" ? rgbOfHex(h) : simulate(vis, rgbOfHex(h)));
    let m = { d: Infinity }, mo = Infinity;
    for (let i = 0; i < ranks.length; i++) for (let j = i + 1; j < ranks.length; j++) {
      const d = de2000(f(ranks[i].hex), f(ranks[j].hex)); if (d < m.d) m = { d, i, j };
      mo = Math.min(mo, dOk(f(ranks[i].hex), f(ranks[j].hex)));
    }
    let mt = Infinity;
    for (let a = 0; a < 12; a++) { const t = cols.filter((c) => c.rank === a);
      for (let i = 0; i < t.length; i++) for (let j = i + 1; j < t.length; j++) mt = Math.min(mt, de2000(f(t[i].hex), f(t[j].hex))); }
    console.log(`| ${mode} | ${vis} | ${fmt(m.d)} | rank ${m.i} ${ranks[m.i].hex} / rank ${m.j} ${ranks[m.j].hex} | ${mo.toFixed(4)} | ${fmt(mt)} |`);
  }
}
// ── 바닥이 쌍 사이 거리를 지우지 않는가: 계획의 색 전부, 모드 x 시각 ──────────────────────────────
/** 바닥 전 색(같은 차례). floorKit 이 있으면 그것, --raw 면 그 트리의 계획, 둘 다 없으면 이 트리의 계획(바닥 전 트리). */
function rawColours(mode) {
  if (rawTint) { const doms = []; for (let a = 0; a < AREAS; a++) for (let t = 0; t < TURNS; t++) doms.push(`r/a${String(a).padStart(2, "0")}/t${t}`);
    const p = rawTint.tintPlan(doms, mode); return doms.map((d) => p.topics.get(d)); }
  if (tint.floorKit) { const out = []; for (let a = 0; a < AREAS; a++) for (let t = 0; t < TURNS; t++) out.push(tint.floorKit.rawPlanColour(a, t, mode)); return out; }
  return planColours(mode).map((c) => c.hex);
}
const PAIR_RULES = [["normal", (c) => c, 5, 5, true], ["deutan", (c) => simulate("deutan", c), 5, 3, false], ["protan", (c) => simulate("protan", c), 5, 3, false]];
console.log("## Pairwise distance kept through the floor (all plan colours: 24 areas x 7 turns, CIEDE2000)\n");
console.log("Rule: a pair at least 5 apart before stays at least 5 apart (normal vision) or 3 apart (deuteranopia, protanopia, Machado 2009 full severity) after, and no pair (normal vision) falls below half its distance before.\n");
console.log("| theme | vision | pairs | pairs >= 5 before | below threshold after | below half after | worst pair (rank/turn: before -> after) |\n|---|---|---|---|---|---|---|");
const pairFails = [];
for (const mode of ["light", "dark"]) {
  const before = rawColours(mode), after = planColours(mode).map((c) => c.hex);
  const keys = [...new Set(before.map((h, i) => `${h}>${after[i]}`))].map((k) => { const i = before.findIndex((h, j) => `${h}>${after[j]}` === k); return i; });
  for (const [vis, f, need0, need1, half] of PAIR_RULES) {
    const L0 = keys.map((i) => lab(f(rgbOfHex(before[i])))), L1 = keys.map((i) => lab(f(rgbOfHex(after[i]))));
    let pairs = 0, big = 0, bad = 0, badHalf = 0, worst = null;
    for (let x = 0; x < keys.length; x++) for (let y = x + 1; y < keys.length; y++) {
      if (before[keys[x]] === before[keys[y]]) continue;
      pairs++; const d0 = de2000Lab(L0[x], L0[y]), d1 = de2000Lab(L1[x], L1[y]);
      const lbl = (i) => `r${Math.floor(i / TURNS)}/t${i % TURNS} ${before[i]}->${after[i]}`;
      if (d0 >= need0) { big++; if (d1 < need1) { bad++; pairFails.push({ mode, vis, a: lbl(keys[x]), b: lbl(keys[y]), d0, d1 }); if (!worst || d1 < worst.d1) worst = { d0, d1, a: lbl(keys[x]), b: lbl(keys[y]) }; } }
      if (half && d1 < d0 / 2) { badHalf++; pairFails.push({ mode, vis: "normal half", a: lbl(keys[x]), b: lbl(keys[y]), d0, d1 }); if (!worst || d1 / d0 < worst.d1 / worst.d0) worst = worst ?? { d0, d1, a: lbl(keys[x]), b: lbl(keys[y]) }; }
    }
    console.log(`| ${mode} | ${vis} | ${pairs} | ${big} | ${bad} | ${half ? badHalf : "n/a"} | ${worst ? `${worst.a} / ${worst.b}: ${fmt(worst.d0)} -> ${fmt(worst.d1)}` : "none"} |`);
  }
}
if (pairFails.length) {
  console.log("\nEvery pair that breaks the rule:\n\n| theme | rule | pair | before | after |\n|---|---|---|---|---|");
  for (const p of pairFails.sort((a, b) => a.d1 - b.d1)) console.log(`| ${p.mode} | ${p.vis} | ${p.a} / ${p.b} | ${fmt(p.d0)} | ${fmt(p.d1)} |`);
}
console.log("");

console.log("\n## Ranks 0 to 11 (turn 0) per theme, OKLCH L C h\n\n| theme | rank | colour | OKLCH |\n|---|---|---|---|");
for (const mode of ["light", "dark"]) for (const c of planColours(mode).filter((c) => c.turn === 0).slice(0, 12)) {
  const [L, C, h] = oklch(rgbOfHex(c.hex)); console.log(`| ${mode} | ${c.rank} | ${c.hex} | ${L.toFixed(3)} ${C.toFixed(3)} ${h.toFixed(1)} |`);
}
console.log("\n## Agent and policy colours\n\n| theme | colour |\n|---|---|");
for (const mode of ["light", "dark"]) console.log(`| ${mode} | ${agentColours(mode).map((c) => `${c.name} ${c.hex}`).join(", ")}, policy ${policyColour(mode)} |`);
