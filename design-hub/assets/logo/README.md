# The Vinc logo

**A single ring with a sphere at its centre. Final 2026-08-24, replacing the two-stroke V of
2026-07-19.**

One rounded lozenge ring, wall thickness 22% of the ring's width, and a sphere in the middle
at about a third of the inner width. Drawn as a single even-odd path plus that circle, so the
hole is real geometry rather than a second shape laid on top. Only solid black (#000000) on light surfaces and solid white (#FFFFFF) on dark surfaces.
The 2026-09-20 owner ruling retired coloured marks; the 2026-09-24 ruling retired the plates.
The mark never sits on a black circle or a rounded square: it is only the transparent ring,
black on light and white on dark.

**One exception (2026-09-26): the desktop application icon.** The icon the operating system
draws for the installed app (taskbar, Start menu, Alt+Tab, Dock, installer) is the white ring
on a black rounded square, because the transparent black ring disappears on a dark taskbar.
The favicon, the in-app title bar, emails and the MCP connector icon stay plate-less.
Decision: `decision:the-desktop-app-icon-keeps-a-black-plate-2026-09-26`.

## Files

| File | Use |
|---|---|
| `vinc-ring-black.svg` | **default mark**, transparent black, for light grounds |
| `vinc-ring-white.svg` | transparent, solid white, for dark grounds |
| `vinc-ring-mono.svg` | single colour, `currentColor`, for masks using the dedicated `--logo` token |
| `vinc-ring-favicon.svg` | **favicon**, black, switching itself to white under `prefers-color-scheme: dark` |

Every mark SVG uses `viewBox="42 38 436 604"`, tight to the art. The favicon is the exception:
it uses the square `viewBox="-42 38 604 604"` (the same art, centred, the box the rasters use),
because whoever rasterises a favicon fills a square icon cell without keeping the ratio, and the
tall box came out as a flattened ring with an oval sphere in Google results and connector cards
(`record:a-tall-favicon-viewbox-is-stretched-into-a-square-2026-10-21`). There is no plate file any more
(`vinc-ring.svg` and `vinc-ring-circle.svg` were deleted on 2026-09-24); the plated application
icon is composed inside `build_logo.mjs` from `vinc-ring-white.svg`. Rasters are generated,
never drawn: the favicon `.ico` (16, 32, 48), the 48px PNG and the MCP connector PNG are the
transparent black mark on a square canvas with no margin; the native Tauri icons are the white
mark on a black rounded square (180 units, corner radius 40, the mark at 65% of the height). The legacy filename
`vinc-ring-indigo.svg` is retained as a black compatibility asset and has no active sync mapping.

## Specification

- **Size ladder**: 128 · 96 · 64 · 40 · 24 · 18 · 16. The mark holds its shape all the way
  down to 16px, which is what the single ring buys over a stacked figure.
- **Clear space**: at least 25% of the mark's width on every side. Favicons drop the margin
  because a 16px tab icon cannot afford it; the application icon's plate is its margin.
- **Colour**: black and white only. Use `--logo` for theme-aware masks; pin white explicitly
  on a permanently dark surface. Functional UI accent tokens must not tint the logo.
- **Forbidden**: rotation, distorting the aspect ratio, filling the ring's hole, detaching the
  centre sphere from the middle, outlining the ring.

⚠ `currentColor` does not cross an `<img>` boundary. `vinc-ring-mono.svg` loaded with
`<img src>` renders black, not the surrounding text colour. Where the mark has to take the
theme colour, use it as a CSS `mask-image` with `background-color: hsl(var(--logo))`. That keeps
the file external, which is the point below.

## Wordmark

None. The mark stands alone and the logo motion ends on it. Add a lockup here only if that
changes.

## Source of truth, and how it reaches anything

**Canonical**: the Claude Design project `Vinc 홈페이지`
(`444928b4-67cb-4af6-91cd-acfb15595487`), file `Vinc Logo.dc.html` and the five SVGs under
`logo/`. These files are those vectors as they are (the favicon adds only the colour-scheme
switch). Change the shape there and re-extract
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

## Rebuild

From `design-hub`, after `npm ci` here and in `../app`:

```sh
npm run logo:build
python -X utf8 scripts/build_dist.py
python -X utf8 scripts/sync_out.py --apply
npm run logo:check
python -X utf8 scripts/sync_out.py --check
```

`build_logo.mjs` uses resvg for email/favicon rasters and the installed Tauri CLI for native
icons, then refreshes the MCP embedded constants. Its check mode compares regenerated bytes
and rejects coloured canonical SVGs. Native installed-artifact verification remains a release gate.
