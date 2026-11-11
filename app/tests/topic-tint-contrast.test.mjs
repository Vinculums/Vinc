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
const { tintPlan, markColour } = await source("view-tint.ts");
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

/** Every colour the plan gives for up to 24 areas: the topic turns repeat every seven topics in an area. */
function planColours(mode) {
  const doms = [];
  for (let a = 0; a < 24; a++) for (let t = 0; t < 7; t++) doms.push(`r/a${String(a).padStart(2, "0")}/t${t}`);
  const plan = tintPlan(doms, mode);
  return [...new Set([...plan.topics.values(), ...plan.areas.values()])];
}
/** The Agent board and the sidebar's Agent rows take whole jewels through the same floor. */
const jewelMarks = (mode) => topicJewels[mode].map((n) => markColour(hex(n), mode));

for (const skin of ["desktop", "web"]) for (const mode of ["light", "dark"]) {
  test(`${skin} ${mode}: every plan colour reaches 4.5:1 on every sidebar ground`, () => {
    const G = grounds(skin, mode), colours = [...planColours(mode), ...jewelMarks(mode)];
    assert.ok(colours.length > 100, "the plan should give well over a hundred colours for 24 areas");
    const bad = [];
    for (const c of colours) for (const [g, bg] of Object.entries(G)) { const r = ratio(rgb(c), bg); if (r < 4.5) bad.push(`${c} on ${g} ${r.toFixed(2)}`); }
    assert.deepEqual(bad, []);
  });
}

// OKLCH hue, to show the floor moves lightness and keeps the jewel's hue.
function hue(c) {
  const [r, g, b] = c.map(lin);
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  const A = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s, B = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s;
  return ((Math.atan2(B, A) * 180) / Math.PI + 360) % 360;
}
test("the floor keeps each jewel's hue and leaves a jewel that already passes untouched", () => {
  for (const mode of ["light", "dark"]) for (const n of topicJewels[mode]) {
    const before = hex(n), after = markColour(before, mode);
    const d = Math.abs(((hue(rgb(after)) - hue(rgb(before)) + 540) % 360) - 180);
    assert.ok(d < 2, `${mode} ${before} -> ${after} turned ${d.toFixed(1)} degrees`);
    if (Math.min(...Object.values(grounds("desktop", mode)).map((g) => ratio(rgb(before), g))) >= 4.56) assert.equal(after, before);
  }
});

test("the Agent board and its sidebar rows take their jewels through the floor", () => {
  const map = read("surfaces/agent-map.ts");
  assert.match(map, /export function policyColour\(\)[^\n]*markColour\(/);
  assert.match(map, /export function agentColours\([\s\S]*?markColour\(`#\$\{J\[pick/);
});
