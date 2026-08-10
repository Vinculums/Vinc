# Application draft: Deep Signal (v1, 2026-07-19)

A draft of the new design direction for the desktop application, built on the owner's
reference of a museum display, a glowing data view centred on a dark screen. **Fully composed
on the Figma `App (real screens)` page**, with the palette and node styles on the
`App (Design system)` page in the `Foundations — App draft (Deep Signal)` frame.

## The concept

- **A dark observatory**: over a deepest-dark background with a faint blueprint grid, the
  knowledge graph floats like a **constellation**. Depth is expressed as glow intensity.
- **A coloured left navigation**: a 72px icon rail on the left, an indigo surface slightly
  lighter than the background (`bg-2`), with the active icon on an indigo chip
  (`accent-deep`) plus a periwinkle glow.
- **A panel that attaches beside it**: clicking a node or an item attaches a **frosted glass
  panel** on the right (translucent `bg-1` at 82% with BACKGROUND_BLUR 24 and a 2px glowing
  edge line on the left), joined to the focused node by a dotted connector.
- **Continuity with the logo**: the accent is a lightened relative of the logo's tip indigo,
  periwinkle. The application and the logo share a bloodline.

## Palette (draft, formalized as tenants/desktop tokens in phase 3)

| Proposed token | HEX | Use |
|---|---|---|
| bg-0 | `#0C0D13` | application background, the deepest |
| bg-1 | `#12141D` | surfaces: pills, cards, panel base |
| bg-2 | `#191C2A` | left navigation and raised surfaces |
| line | `#262A3D` | hairline |
| text | `#E8E7EF` | body, a cool ivory |
| text-soft | `#A7A9BF` | secondary |
| text-mute | `#6E7189` | captions, metadata, inactive icons |
| accent | `#8F96D9` | periwinkle indigo: glow, edges, points |
| accent-strong | `#B9BEF0` | glow core, active icons, CTA text |
| accent-deep | `#3A4070` | active surfaces, CTA background, segment thumb |

Glow is an `accent`-coloured DROP_SHADOW (offset 0, radius 6 to 30, intensity per tier).

> ⛔ **The glow rule, settled by the owner 2026-07-19: glow is for graph nodes only.**
> Every piece of UI chrome, chips, buttons, cards, input fields, panel edges, stays flat with
> no coloured DROP_SHADOW. A black depth shadow that raises a panel is not glow and is
> allowed.

## Graph node tiers (samples in Foundations)

| Tier | Size | Fill | Glow |
|---|---|---|---|
| focus | 26 | accent-strong | radius 30, 0.9 |
| near | 16 | accent | radius 16, 0.5 |
| mid | 13 | accent-deep with an accent ring | radius 10, 0.35 |
| far | 9 | bg-2 with a mute ring | radius 6, 0.18 |

Edges: accent at 1px, at 0.5 opacity next to the focus, 0.28 in general and 0.14 far away,
slightly curved.

## Screens composed (the App (real screens) page)

| Frame | Content |
|---|---|
| Atlas · Graph (42:15) | the representative screen: grid, constellation graph, legend, zoom, command palette, 2D and 3D segments |
| Atlas · Node detail (42:17) | **the key interaction**: click a node and a frosted panel appears on the right (concept information, a weight bar for strong ties, actions) with a dotted connector |
| Intake · Empty (42:3) | a glowing dashed drop zone with the logo mark and a sample CTA |
| Records · List (42:10) | filter chips, list rows keeping the filled, half and empty circle status marks, and a hover row at an 8% accent tint |
| Search · Results (42:22) | a glowing search field with operator highlighting, and results grouped by topic with exact and semantic marks |
| Packs · Idle (42:27) | pack cards with an accent line on the left and a glowing active card, plus a ghost new pack |
| Settings · Default (42:32) | a left menu, form rows, a theme segment, a Synced badge and toggles |

The empty-state variant frames (Intake Ingesting and Success, Records, Atlas and Search
Empty, Packs Share ready) stay skeletons and expand once the direction is settled.

## Structural patterns, settled by the owner (2026-07-19, option B throughout)

Chosen from the 3 by 3 exploration board (`App (Design system)` page,
`Patterns — restructuring exploration`), and **applied to every screen**:

| Axis | Chosen | Specification |
|---|---|---|
| **Left navigation** | **B, an expanded 232px sidebar** | logo and wordmark, five icon-and-label navigation rows (active is a 45% accent-deep row background plus weight, **with no colour band on the left**, revised 2026-08-02), a DOMAINS tree (dot, name, count, with depth as indentation plus a guide line plus two ink levels plus differentiated dots), and Settings at the bottom with a "Synced · vincs.app" footer. The canvas starts at x=232 |
| **Settings** | **B, a right slide-over** | a dim over the working screen (black at 42%, excluding the sidebar) plus a 380px panel on the right (blur 24 plus a black depth shadow). Section chip tabs and form rows, the same glass grammar as the floating card |
| **Panel** | **B, a floating glass card** | click a node and a 300px glass card appears beside it (blur 20, a 2px accent-strong bar on the left) with a dotted connector and a dotted selection ring around the node. Compact information: the concept, three ties, a Focus lens, and a link to more. "More" expands to the full panel |

Note: **`resize()` on an instance does not scale its interior.** Fixed by setting SCALE
constraints on the Logo/Mark glyph and vectors, so it does not recur.

> **Revised 2026-08-02, the left colour band retired (left navigation only).** The "2px
> accent-strong bar on the left" in the table above is dropped. The reason is an owner remark,
> first applied to the documentation site on 2026-07-31 and extended to the application:
> "when a colour bar stands beside every line, the decoration reads before the content." In
> the application there was also a defect where the home row is always active on the Atlas
> surface, so choosing a topic made **two bands stand at once**. Active state is unified as a
> **filled pill plus weight** for home, topics and documents alike.
> The "2px accent-strong bar on the left" on the floating panel (row three above) **stays**,
> because that is a single card rather than a list, so the objection (a bar standing beside
> every line) does not apply.

## The shared component library (2026-07-19, the `App (Design system)` page)

On the owner's instruction, the split between the **design system page (components and
variables)** and the **real screens (instances)** is complete. Real screens use component
instances only.

**Figma variables**: the collection `App · Deep Signal (draft)`, ten palette colours, with
scope and a WEB code syntax of `var(--app-*)`. Every component fill, stroke and text is bound
to a variable, with zero hardcoded values.

| Component | Variants and properties | Where used |
|---|---|---|
| `App / NavItem` | State = Default or Active, label TEXT, icon INSTANCE_SWAP | sidebar navigation rows |
| `App / DomainRow` | State = Default or Active, name and count TEXT | the sidebar topic tree |
| `App / Sidebar` | five NavItems plus three DomainRows, with the active tab set by a nested variant override per screen | **an instance on all thirteen screens** |
| `App / Button` | Kind = Primary or Ghost, label TEXT | CTAs throughout |
| `App / Chip` | State = Default or Active, label TEXT | filter and concept chips |
| `App / ListRow` | State = Default or Hover, mark, title and meta TEXT | the Records list |
| `App / GraphNode` | Tier = Focus, Near, Mid or Far (glow lives only here) | the Atlas constellation |
| `App / PackCard` | State = Default or Highlight, name, counts and meta TEXT | Packs |
| `App / ProgressBar` | progress through a fill width override | Intake Ingesting |
| `App / EmptyState` | title and desc TEXT with a nested Button | the three empty states |

**Six new branch screens**: Intake Ingesting (a processing panel with a ProgressBar and the
circle notation), Intake Success (a check, concept Chips and two Buttons), Records, Atlas and
Search Empty (EmptyState instances, with a grid background for Atlas), and Packs Share ready
(a highlighted PackCard, a share link and a community toggle). That completes **all thirteen
screens.**

⚠ Figma component property keys carry an id suffix in the form `name#<id>`. Look the
`componentProperties` keys up dynamically on the instance before calling setProperties, and
never guess the suffix.

## Implementation notes (connecting to phases 5 and 6)

- Of the old Ink and Paper discipline of "state through notation", **the circle and the exact
  or semantic marks are kept**, now combined with colour.
- Glow is CSS `box-shadow` or `filter: drop-shadow`; the graph uses three.js emissive plus
  bloom, or a sprite texture.
- The frosted panel is `backdrop-filter: blur(24px)`, which the web Vinc CSP allows and which
  has nothing to do with unsafe-inline styles.
- The draft uses Inter provisionally; the brand font is not settled.
- ⚠ WCAG: text-mute `#6E7189` on bg-0 is forbidden for body text and is caption only. The
  pair is verified at the phase 3 gate.
