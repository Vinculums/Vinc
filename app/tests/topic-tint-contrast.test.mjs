// Topic marks are decorative: every colour the topic plan can give holds the non-text floor (3:1, WCAG 1.4.11)
// on every ground a tinted mark sits on in the sidebar (shell base
// and drawer card, at rest, under the hover fill and under the selected fill), in both themes and both skins.
// Marks without a topic colour (the muted ink fallback, the selected row's --ring) keep the text floor 4.5:1. The grounds are built here from the token CSS and the
// sidebar rules in app.css, not from view-tint's own constants, so a token, a fill or a plan change that
// drops a mark below the floor fails the build.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const SRC = new URL("../src/", import.meta.url);
const read = (f) => readFileSync(fileURLToPath(new URL(f, SRC)), "utf8");
const source = async (entry) => {
  const r = await build({ entryPoints: [fileURLToPath(new URL(entry, SRC))], bundle: true, platform: "node", format: "esm", write: false });
  return import(`data:text/javascript;base64,${Buffer.from(r.outputFiles[0].text).toString("base64")}`);
};
const { tintPlan, planColour, floorKit } = await source("view-tint.ts");
const { lab, de2000Lab, simulate } = await import("../dev/colour-math.mjs");
const { topicJewels } = await source("theme.ts");

// WCAG 2.x relative luminance and contrast, written out here so the test does not grade the code with itself.
const rgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
const lin = (v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
const lum = (c) => 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2]);
const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
function hsl(h, s, l) {
  s /= 100; l /= 100;
  const k = (n) => (n + h / 30) % 12, a = s * Math.min(l, 1 - l);
  return [0, 8, 4].map((n) => Math.round((l - a * Math.max(-1, Math.min(k(n) - 3, 9 - k(n), 1))) * 255) / 255);
}
const over = (fg, a, bg) => fg.map((v, i) => Math.round((v * a + bg[i] * (1 - a)) * 255) / 255);
const hex = (n) => `#${n.toString(16).padStart(6, "0")}`;
/** The floors, written here and not read from the plan: tinted marks 3:1, marks without a topic colour 4.5:1. */
const FLOOR = 3, TEXT_FLOOR = 4.5;

const APP = read("app.css");
const fill = (sel) => {
  const m = APP.match(new RegExp(`${sel.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\{[^}]*?hsl\\(var\\(--([\\w-]+)\\)\\s*/\\s*([\\d.]+)\\)`));
  assert.ok(m, `no fill rule for ${sel} in app.css`);
  return { tok: m[1], a: +m[2] };
};
const FILLS = [".lnb-doc:hover", ".lnb-group:hover", ".lnb-fold:hover", ".lnb-doc.on", ".lnb-group.on"].map(fill);
const surface0 = {
  light: APP.match(/:root\s*\{[\s\S]*?--surface-0:\s*var\(--(\w+)\)/)[1],
  dark: APP.match(/\[data-theme="dark"\]\s*\{[^}]*--surface-0:\s*var\(--(\w+)\)/)[1],
};
const surface2 = APP.match(/--surface-2:\s*var\(--(\w+)\)/)[1];
const dock = +APP.match(/#cmp-dock \.pi:hover\s*\{\s*background:\s*color-mix\(in srgb, var\(--c-point\) (\d+)%, var\(--c-card\)\)/)[1] / 100;

function grounds(skin, mode) {
  const css = read(`tokens.${skin}.css`);
  const head = mode === "dark" ? /\[data-theme="dark"\]\s*\{([\s\S]*?)\n\}/ : /:root(?:\[data-skin="web"\])?\s*\{([\s\S]*?)\n\}/;
  const blk = css.match(head)[1];
  const tok = (n) => { const m = blk.match(new RegExp(`--${n}:\\s*([\\d.]+)\\s+([\\d.]+)%\\s+([\\d.]+)%`)); assert.ok(m, `--${n} in tokens.${skin}.css ${mode}`); return hsl(+m[1], +m[2], +m[3]); };
  const out = {};
  for (const [name, base] of [["base", tok(surface0[mode])], ["drawer", tok(surface2)]]) {
    out[name] = base;
    for (const f of FILLS) out[`${name} under ${f.tok}/${f.a}`] = over(tok(f.tok), f.a, base);
  }
  const card = tok("card"), point = tok("point");
  out["dock pin hover"] = card.map((v, i) => Math.round((point[i] * dock + v * (1 - dock)) * 255) / 255);
  return out;
}

/** Every colour the plan gives for up to 24 areas, in slot order (area x 7 + topic turn), with the colour before the floor. */
function planSlots(mode) {
  const doms = [];
  for (let a = 0; a < 24; a++) for (let t = 0; t < 7; t++) doms.push(`r/a${String(a).padStart(2, "0")}/t${t}`);
  const plan = tintPlan(doms, mode);
  return doms.map((d, i) => ({ slot: `r${Math.floor(i / 7)}/t${i % 7}`, before: floorKit.rawPlanColour(Math.floor(i / 7), i % 7, mode), after: plan.topics.get(d) }));
}
const planColours = (mode) => [...new Set(planSlots(mode).map((s) => s.after))];
/** The Agent board and the sidebar's Agent rows take the plan's whole-jewel slots. */
const jewelMarks = (mode) => [0, 1, 2, 3, 4].map((i) => planColour(i, 0, mode));

test("the tinted-mark floor is the non-text floor, not lower", () => {
  assert.equal(floorKit.floor, FLOOR);
});
for (const skin of ["desktop", "web"]) for (const mode of ["light", "dark"]) {
  test(`${skin} ${mode}: every plan colour holds the tinted-mark floor on every sidebar ground`, () => {
    const G = grounds(skin, mode), colours = [...planColours(mode), ...jewelMarks(mode)];
    assert.ok(colours.length > 100, "the plan should give well over a hundred colours for 24 areas");
    const bad = [];
    for (const c of colours) for (const [g, bg] of Object.entries(G)) { const r = ratio(rgb(c), bg); if (r < FLOOR) bad.push(`${c} on ${g} ${r.toFixed(2)}`); }
    assert.deepEqual(bad, []);
  });
  test(`${skin} ${mode}: marks without a topic colour keep 4.5:1 on every sidebar ground`, () => {
    const G = grounds(skin, mode), bad = [];
    const css = read(`tokens.${skin}.css`), blk = css.match(mode === "dark" ? /\[data-theme="dark"\]\s*\{([\s\S]*?)\n\}/ : /:root(?:\[data-skin="web"\])?\s*\{([\s\S]*?)\n\}/)[1];
    const tok = (n) => { const m = blk.match(new RegExp(`--${n}:\\s*([\\d.]+)\\s+([\\d.]+)%\\s+([\\d.]+)%`)); return hsl(+m[1], +m[2], +m[3]); };
    for (const [g, bg] of Object.entries(G)) { const r = ratio(tok("muted-foreground"), bg); if (r < TEXT_FLOOR) bad.push(`muted on ${g} ${r.toFixed(2)}`); }
    // the selected document and graph-node rows draw their mark in --ring on the selected fill
    for (const [g, bg] of Object.entries(G)) if (/under ring/.test(g)) { const r = ratio(tok("ring"), bg); if (r < TEXT_FLOOR) bad.push(`ring on ${g} ${r.toFixed(2)}`); }
    assert.deepEqual(bad, []);
  });
}
test("a plan colour that already holds the floor is drawn unchanged", () => {
  // "holds" is judged here, on the grounds this test builds from the token CSS and app.css, at 3:1 exactly
  const changed = [];
  for (const skin of ["desktop", "web"]) for (const mode of ["light", "dark"]) {
    const G = Object.values(grounds(skin, mode));
    for (const s of planSlots(mode)) if (Math.min(...G.map((g) => ratio(rgb(s.before), g))) >= FLOOR && s.after !== s.before) changed.push(`${skin} ${mode} ${s.slot} ${s.before} -> ${s.after}`);
  }
  assert.deepEqual([...new Set(changed)], []);
});
test("light area 15 turn 3 holds 3:1 on every drawn ground and is drawn as planned", () => {
  // the plan's ruby +40 turned +8: 3.007:1 on the light selected row as the CSS draws it
  const s = planSlots("light").find((x) => x.slot === "r15/t3");
  assert.equal(s.before, "#9b6c1b");
  assert.ok(Math.min(...Object.values(grounds("desktop", "light")).map((g) => ratio(rgb(s.before), g))) >= FLOOR);
  assert.equal(s.after, "#9b6c1b");
});

// Pair distances are measured limits of the jewel set under the floor, not a rule the colours meet: a colour that
// moves takes the nearest passing value of its own hue and nothing keeps it away from its neighbours, so some pairs
// that the plan kept apart come close (amber and its orange and olive neighbours in light, for red-green deficient
// readers most of all). The snapshot `topic-tint-pairs.json` records every pair that is close now with its distance
// (CIEDE2000; normal vision: under 5 having been 5 or more, or under half its distance before; deuteranopia and
// protanopia, Machado 2009 full severity: under 3 having been 5 or more). A later token or plan change fails this
// test if any recorded pair gets closer or a new pair becomes close. To accept a change on purpose, rewrite the
// snapshot with `TINT_PAIRS_WRITE=1 node --test tests/topic-tint-contrast.test.mjs` and say why in the commit.
const PAIRS = fileURLToPath(new URL("./topic-tint-pairs.json", import.meta.url));
const VISION = { normal: (c) => c, deutan: (c) => simulate("deutan", c), protan: (c) => simulate("protan", c) };
function closePairs() {
  const out = {};
  for (const mode of ["light", "dark"]) {
    const slots = planSlots(mode), seen = new Set(), uniq = slots.filter((s) => !seen.has(s.before) && seen.add(s.before));
    for (const [vis, f] of Object.entries(VISION)) {
      const L0 = uniq.map((s) => lab(f(rgb(s.before)))), L1 = uniq.map((s) => lab(f(rgb(s.after))));
      for (let i = 0; i < uniq.length; i++) for (let j = i + 1; j < uniq.length; j++) {
        const d0 = de2000Lab(L0[i], L0[j]), d1 = de2000Lab(L1[i], L1[j]), key = `${mode} ${vis} ${uniq[i].slot} ${uniq[j].slot}`;
        const close = vis === "normal" ? (d0 >= 5 && d1 < 5) || d1 < d0 / 2 : d0 >= 5 && d1 < 3;
        if (close) out[key] = +d1.toFixed(2);
      }
    }
  }
  return out;
}
test("pair distances get no worse than the recorded limits of the jewel set", () => {
  const now = closePairs();
  if (process.env.TINT_PAIRS_WRITE) writeFileSync(PAIRS, JSON.stringify(now, null, 1) + "\n");
  assert.ok(existsSync(PAIRS), "topic-tint-pairs.json is missing");
  const was = JSON.parse(readFileSync(PAIRS, "utf8")), worse = [];
  for (const [k, d] of Object.entries(now)) if (!(k in was)) worse.push(`${k}: newly close at ${d}`); else if (d + 0.005 < was[k]) worse.push(`${k}: ${was[k]} -> ${d}`);
  assert.deepEqual(worse, []);
});

// OKLCH hue, to show the floor moves lightness and keeps each colour's hue.
function oklch(c) {
  const [r, g, b] = c.map(lin);
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  const A = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s, B = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s;
  return [Math.hypot(A, B), ((Math.atan2(B, A) * 180) / Math.PI + 360) % 360];
}
test("a colour that moves keeps its hue", () => {
  for (const mode of ["light", "dark"]) for (const s of planSlots(mode)) {
    const [c1, h1] = oklch(rgb(s.after)), [, h0] = oklch(rgb(s.before));
    if (c1 < 0.04) continue;   // 8-bit rounding makes the hue of a near-grey meaningless
    const d = Math.abs(((h1 - h0 + 540) % 360) - 180);
    assert.ok(d < 3, `${mode} ${s.slot} ${s.before} -> ${s.after} turned ${d.toFixed(1)} degrees`);
  }
});

test("the Agent board and its sidebar rows take their jewels from the plan", () => {
  const map = read("surfaces/agent-map.ts");
  assert.match(map, /export function policyColour\(\)[^\n]*planColour\(4, 0, mode\)/);
  assert.match(map, /export function agentColours\([\s\S]*?planColour\(pick\[i % pick\.length\], 0, mode\)/);
});
