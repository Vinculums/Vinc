# The Vinc logo

**A single ring with a sphere at its centre. Final 2026-08-24, replacing the two-stroke V of
2026-07-19.**

One rounded lozenge ring, wall thickness 22% of the ring's width, and a sphere in the middle
at about a third of the inner width. Drawn as a single even-odd path plus that circle, so the
hole is real geometry rather than a second shape laid on top. Nothing else, and no black
anywhere: the ramp fades horizontally, indigo `#4A4F8C → #32355C` on light, white
`#FFFFFF → #C9CDDD` on dark.

## Files

| File | Use |
|---|---|
| `vinc-ring-indigo.svg` | **default mark**, transparent, indigo ramp, for light grounds |
| `vinc-ring-white.svg` | transparent, white ramp, for dark grounds |
| `vinc-ring-mono.svg` | single colour, `currentColor`, for one-ink print, stamps, and UI that has to take the surrounding text colour |
| `vinc-ring.svg` | plate, 40px rounded square, **application icon** |
| `vinc-ring-circle.svg` | plate, circular, **favicon and avatar** |

The three mark variants use `viewBox="42 38 436 604"`, tight to the art. The two plates are
`viewBox="0 0 180 180"` and carry the entanglement ground (radial `#5C63B8 → #3A3F7A →
#2C3070`) with one screen-blend cone of light across the upper left.

## Specification

- **Size ladder**: 128 · 96 · 64 · 40 · 24 · 18 · 16. The mark holds its shape all the way
  down to 16px, which is what the single ring buys over a stacked figure.
- **Clear space**: at least 25% of the mark's width on every side.
- **Colour**: the five defined variants only. The ramps are the brand's; do not recolour and
  do not add a sixth variant without changing this file.
- **Forbidden**: rotation, distorting the aspect ratio, filling the ring's hole, detaching the
  centre sphere from the middle, outlining the ring.

⚠ `currentColor` does not cross an `<img>` boundary. `vinc-ring-mono.svg` loaded with
`<img src>` renders black, not the surrounding text colour. Where the mark has to take the
text colour, use it as a CSS `mask-image` with `background-color: currentColor`. That keeps
the file external, which is the point below.

## Wordmark

None. The mark stands alone and the logo motion ends on it. Add a lockup here only if that
changes.

## Source of truth, and how it reaches anything

**Canonical**: the Claude Design project `Vinc 홈페이지`
(`444928b4-67cb-4af6-91cd-acfb15595487`), file `Vinc Logo.dc.html` and the five SVGs under
`logo/`. These files are those vectors as they are. Change the shape there and re-extract
here. This replaces the Figma canon the two-stroke V used.

⚠ **The old logo had exactly this README and it still reached nobody.** `sync_out.py` carried
only `tokens.css`, so every consumer held its own inline copy: 22 across 19 site files, 14
email templates, 2 app files, and a base64 PNG plus an inline SVG string compiled into
`vinc_mcp/http_server.py`. Replacing the mark meant editing 44 places by hand, and the canon
comment in the MCP server still pointed at `Vinc-design`, decommissioned on 2026-08-08.

So the rule now is not "copy carefully". It is **do not inline the logo**. Consumers reference
the asset by URL, `sync_out.py` copies the files, and `--check` fails when a copy drifts. The
paths are in that script's `MAP`. Anything that inlines the mark again puts itself outside the
only mechanism that keeps it current.

Rationale: `record:a-logo-ssot-that-reaches-nobody`.
