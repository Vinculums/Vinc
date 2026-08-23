# The Vinc logo

**Hourglass, stacked slabs. Final 2026-08-23, replacing the two-stroke V of 2026-07-19.**

Ten slabs, five narrowing down and five narrowing up, mirrored around a sphere at the waist.
Each slab is a disc with thickness: a trapezoid side, an elliptical top face and a rim
crescent underneath. The rim gradient alternates direction slab by slab, which is what gives
the stack its turn. No black anywhere, only the brand ramp.

## Files

| File | Use |
|---|---|
| `vinc-hourglass-slab-indigo.svg` | **default mark**, transparent, indigo ramp `#4A4F8C → #32355C` with periwinkle rims |
| `vinc-hourglass-slab-white.svg` | transparent, white faces `#FFFFFF → #C9CDDD` with indigo rims, for dark grounds |
| `vinc-hourglass-slab.svg` | plate, 40px rounded square, **application icon** |
| `vinc-hourglass-slab-circle.svg` | plate, circular, **favicon and avatar** |

The two mark variants use `viewBox="114 34 508 1008"`, tight to the art. The two plates are
`viewBox="0 0 180 180"` and carry the indigo ground, its radial ramp and a single screen-blend
sheen across the upper left corner.

## Specification

- **Size ladder**: 160 · 96 · 64 · 40 · 24 · 16 for the mark. Plates read as squares at 96
  and 64, and as circles at 40 and 24.
- **Clear space**: at least 25% of the mark's width on every side.
- **Small sizes**: below 24px the slab separations close up. Use a plate, not the bare mark.
- **Colour**: the four defined variants only. The ramps are the brand's; do not recolour, do
  not add a fifth variant without changing this file.
- **Forbidden**: rotation, distorting the aspect ratio, changing the slab count, removing the
  waist sphere, flattening the rims away.

## Wordmark

None. The 2026-08-23 decision is mark only. The logo motion ends on the mark, not on a
lockup. Add a lockup here only if that decision changes.

## Source of truth, and how it reaches anything

**Canonical**: the Claude Design project `Vinc 홈페이지`
(`444928b4-67cb-4af6-91cd-acfb15595487`), file `Vinc Logo.dc.html` and the four SVGs under
`logo/`. These files are those vectors as they are. Change the shape there and re-extract
here. This replaces the Figma canon the two-stroke V used.

⚠ **The old logo had exactly this README and it still reached nobody.** `sync_out.py` carried
only `tokens.css`, so every consumer held its own inline copy: 22 across 19 site files, 14
email templates, 2 app files, and a base64 PNG plus an inline SVG string compiled into
`vinc_mcp/http_server.py`. Replacing the mark meant editing 44 places by hand, and the
canon comment in the MCP server still pointed at `Vinc-design`, decommissioned on 2026-08-08.

So the rule now is not "copy carefully". It is **do not inline the logo**. Consumers reference
the asset by URL, `sync_out.py` copies the files, and `--check` fails when a copy drifts.
The paths are in that script's `MAP`. Anything that inlines the mark again puts itself outside
the only mechanism that keeps it current.

Rationale: `record:a-logo-ssot-that-reaches-nobody`.
