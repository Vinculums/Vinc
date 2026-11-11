// Topic marks are decorative, yet every colour the topic plan can give reaches 4.5:1 on every ground a
// tinted mark sits on in the sidebar (shell base and drawer card, at rest, under the hover fill and under
// the selected fill), in both themes and both skins. The grounds are built here from the token CSS and the
// sidebar rules in app.css, not from view-tint's own constants, so a token, a fill or a plan change that
// drops a mark below the floor fails the build.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const SRC = new URL("../src/", import.meta.url);
const read = (f) => readFileSync(fileURLToPath(new URL(f, SRC)), "utf8");
const source = async (entry) => {
  const r = await build({ entryPoints: [fileURLToPath(new URL(entry, SRC))], bundle: true, platform: "node", format: "esm", write: false });
  return import(`data:text/javascript;base64,${Buffer.from(r.outputFiles[0].text).toString("base64")}`);
};
const { tintPlan, planColour, floorKit } = await source("view-tint.ts");
const { FLOOR_KEY } = await source("view-tint-floor.ts");
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

for (const skin of ["desktop", "web"]) for (const mode of ["light", "dark"]) {
  test(`${skin} ${mode}: every plan colour reaches 4.5:1 on every sidebar ground`, () => {
    const G = grounds(skin, mode), colours = [...planColours(mode), ...jewelMarks(mode)];
    assert.ok(colours.length > 100, "the plan should give well over a hundred colours for 24 areas");
    const bad = [];
    for (const c of colours) for (const [g, bg] of Object.entries(G)) { const r = ratio(rgb(c), bg); if (r < 4.5) bad.push(`${c} on ${g} ${r.toFixed(2)}`); }
    assert.deepEqual(bad, []);
  });
}

test("the floor table was built from the tokens in this tree", () => {
  // A token, row fill or floor change makes the app fall back to one-colour-at-a-time floors, which
  // collapse near hues: rerun `node dev/topic-tint-floor.mjs --write`.
  assert.equal(FLOOR_KEY, floorKit.floorInputKey());
});

// The floor must not erase the differences the plan relied on. Over every pair of plan colours (CIEDE2000):
// normal vision, a pair at least 5 apart before stays at least 5 apart and no pair falls below half its
// distance before; deuteranopia and protanopia (Machado 2009, full severity), a pair at least 5 apart before
// stays at least 3 apart. KNOWN lists the pairs the floor table cannot keep in sRGB with the distance each
// keeps now, so any further loss fails.
const KNOWN = {
  "dark deutan r1/t3 r17/t5": 2.93,   // before 8.84
  "dark deutan r17/t1 r17/t5": 2.88,   // before 6.09
  "dark normal r1/t4 r17/t5": 7.78,   // before 15.60
  "dark normal r1/t5 r6/t3": 4.85,   // before 5.79
  "dark normal r11/t0 r17/t5": 10.89,   // before 22.24
  "dark protan r14/t1 r17/t5": 2.88,   // before 7.75
  "light deutan r3/t1 r18/t1": 2.56,   // before 5.19
  "light normal r13/t2 r23/t3": 4.84,   // before 5.20
  "light normal r18/t0 r18/t6": 4.93,   // before 6.12
  "light normal r18/t2 r18/t3": 4.55,   // before 5.15
  "light normal r3/t0 r15/t1": 4.66,   // before 6.68
  "light normal r3/t1 r15/t3": 4.95,   // before 6.87
  "light normal r3/t3 r15/t5": 4.73,   // before 6.89
  "light normal r8/t1 r8/t2": 4.04,   // before 5.82
  "light protan r13/t1 r23/t0": 2.95,   // before 11.41
};
const VISION = { normal: (c) => c, deutan: (c) => simulate("deutan", c), protan: (c) => simulate("protan", c) };
for (const mode of ["light", "dark"]) {
  test(`${mode}: the floor keeps every pair of plan colours apart`, () => {
    const slots = planSlots(mode), seen = new Set(), uniq = slots.filter((s) => !seen.has(s.before) && seen.add(s.before));
    const broken = [];
    for (const [vis, f] of Object.entries(VISION)) {
      const L0 = uniq.map((s) => lab(f(rgb(s.before)))), L1 = uniq.map((s) => lab(f(rgb(s.after))));
      for (let i = 0; i < uniq.length; i++) for (let j = i + 1; j < uniq.length; j++) {
        const d0 = de2000Lab(L0[i], L0[j]), d1 = de2000Lab(L1[i], L1[j]), key = `${mode} ${vis} ${uniq[i].slot} ${uniq[j].slot}`;
        const need = d0 >= 5 ? (vis === "normal" ? 5 : 3) : 0, floor = Math.max(need, vis === "normal" ? d0 / 2 : 0);
        if (d1 + 1e-9 < floor && !(key in KNOWN && d1 + 0.005 >= KNOWN[key])) broken.push(`${key}: ${d0.toFixed(2)} -> ${d1.toFixed(2)}`);
      }
    }
    assert.deepEqual(broken, []);
  });
}

// OKLCH hue, to show the floor moves lightness and chroma and keeps each colour's hue.
function oklch(c) {
  const [r, g, b] = c.map(lin);
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  const A = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s, B = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s;
  return [Math.hypot(A, B), ((Math.atan2(B, A) * 180) / Math.PI + 360) % 360];
}
test("the floor keeps each plan colour's hue", () => {
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
