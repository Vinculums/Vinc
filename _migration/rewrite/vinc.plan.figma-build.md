---
doc_id: vinc.plan.figma-build
layer: plan
domain: vinc/design
authority: spec
title: The Figma build
supersedes: vinc.plan.figma-design-resume
updated: 2026-08-07
inherits_from: [vinc.spec.surface-tree, vinc.spec.atlas-visual-language]
tags: [plan, design, figma, variables, components, mirror, tokens]
---

# The Figma build

> One Figma file mirrors every surface the product ships: the marketing site, the desktop
> application, the documentation pages, the transactional mail, and the graph views. It is a mirror
> rather than a proposal. The live surfaces are the authority for what a screen looks like, and this
> file exists so that the design can be **seen, varied and handed over** without reading code.
>
> The file is `WUXLeDDNBqih2KTZOz9wCX`. The node map, which is the only reliable index of what is
> inside it, is `Vinc-design/concepts/figma-2026-08-rebuild.md`.

## 1. The plan this replaces, and why it changed shape

The predecessor was written while the design tool was unreachable: the connector was not authorized,
so **no output of any kind was possible**, and the document was mostly a schedule waiting on a single
human step. It assumed the existing file would be **resumed** page by page, against a fixed
five-page layout, with the marketing frames mirrored one to one from the live site.

Authorization arrived and the assumption did not survive contact.

- **The file was rebuilt rather than resumed.** The old variable collections were deleted, not
  extended, because they encoded a palette the product no longer uses
- **The fixed-page rule is retired.** There are six pages now, and the count follows the surfaces
  rather than a convention. A reader who remembers the old rule will look for pages that are gone
- **Mirroring the site was right, and had to be done twice.** The first pass built the marketing
  frames from the source markup. A second pass, measured against the running site with a browser on
  2026-08-06, found them wrong enough that the landing, download, pricing and community frames were
  **deleted and rebuilt** from the observed screens

## 2. Rules, all of which survived the rebuild

These are the constraints that make the file maintainable rather than a pile of rectangles. They are
inherited from the product's own copy and theme rules, so breaking one here means the file starts
disagreeing with the build.

| # | Rule | Why |
|---|---|---|
| 1 | **Bind variables. Never type a color literal** | A literal cannot follow a theme switch, and the design gate rejects it |
| 2 | **The application window palette is its own collection and is always dark** | The application does not participate in the light theme (`adr-design-038`). Binding it to the marketing collection makes the app window go pale in light mode |
| 3 | **Place with numeric coordinates** | Inferred automatic layout drifts away from the measured spacing of the live page |
| 4 | **Read one page at a time** | Whole-file reads exhaust the context before anything is drawn |
| 5 | **English is the copy source, and no em dash** | [[vinc.canon.terminology]] and `adr-design-018`. The Korean deck is a port, not a translation, per `adr-design-042` |
| 6 | **The map is drawn as a static two-dimensional projection** | The real Atlas is a three-dimensional renderer. Figma owns layout, chrome and state; it does not own realism |

## 3. What the file contains

Six pages, measured 2026-08-06.

| Page | Holds |
|---|---|
| Design System, homepage | Logo canon, icons, marketing component set, page background |
| Design System, application | Application component set, fixed to the dark semantic mode |
| Homepage | Marketing, account, authentication and state frames, plus the documentation pages |
| Application, desktop | The shipped surfaces at 1280 by 800, plus overlays |
| Node | Five graph examples: three-dimensional in both themes, two-dimensional, timeline, and the totals view |
| Email | Mail design system and fourteen templates |

**Variables.** Seven collections carry design values: primitives, semantic roles in light and dark,
graph colors, domain colors, the always-dark window palette, mail colors, and the numeric scale. An
eighth carries **copy**: seventy string variables in English and Korean, which is how a single frame
switches language. Three older collections were deleted rather than migrated.

**Components.** Forty-five icons plus three added for the navigation bar, each bound to a color
variable rather than filled. Marketing, application and mail component sets, each with variant
properties for the states the code actually has.

**Screens.** The homepage page is laid out in labelled rows: marketing, account, authentication and
service states, documentation, and the light and mobile variants. The application page follows the
same discipline by surface.

## 4. What is not finished

| Item | State |
|---|---|
| **The brand typeface is not installed** | The design tool does not list it, so every text style was created with a substitute. A swap procedure exists and is blocked on installing the font and restarting the application. Until then the file is right in structure and wrong in letterforms |
| **The node map is not in the graph** | The file was built while the store was unreachable, so the record was kept as a markdown file for bulk ingest afterwards. That ingest has not happened, which means the graph cannot answer questions about the design file |
| **Copy variables cover the landing page and the main navigation only** | Seventy strings, verified by switching one frame to Korean and screenshotting it. The rest of the file is English |
| **Labels held as component text properties cannot be bound at all** | A platform limit, not an omission. Call-to-action labels inside component instances stay English. Binding them required detaching instances on the landing page, and detaching is a cost that should not be paid everywhere |
| **Light and mobile coverage is partial** | A handful of light frames and one mobile frame. Light is cheap, since it is a mode switch on a selected frame rather than a duplicate, and the earlier duplicated light frames were deleted for exactly that reason |
| **The film grain layer is absent** | The plugin runtime rejected the noise effect and does not support pattern paints in this file. The rest of the page background, the aurora blobs and the halftone dot field, is reproduced from the code values |

## 5. Traps measured while building

- **Do not trust a metadata call to enumerate pages.** Walk the document's children. A page that the
  summary omits is a page that gets silently rebuilt
- **Rebuild visuals from the code's own coordinates**, scaled once. The orbit graphic and the
  application mockup were both redone this way, and the second attempt matched where the first did
  not
- **Deleting a stale variable collection is safer than editing it.** The old palette had entries with
  the same names as the new semantic roles, which makes a partial migration produce a file that binds
  correctly and renders wrong

## 6. What to do next

1. **Install the typeface and run the swap.** Everything else in the file is downstream of it
2. **Ingest the node map**, so the design file becomes answerable from the graph like everything else
3. **Extend copy variables** past the landing page, accepting that component text labels stay English
4. **Fill in light and mobile** where a surface actually ships in that form, which is marketing and
   the account pages, and not the application

## Provenance

| Source | Relation |
|---|---|
| `Vinc-design/concepts/figma-2026-08-rebuild.md` | The node map. Authority for what exists in the file |
| `Vinc-design/HUB-NOTES.md` | The token hub that feeds the same values into code |
| `Vinc-homepage/site/` | The live marketing surfaces the mirror is measured against |
| [[vinc.spec.ui-copy]] | The copy inventory the frames must not contradict |
| [[vinc.spec.atlas-visual-language]] | The mark language the graph examples reproduce |
| [[vinc.spec.surface-tree]] | Which application surfaces exist and therefore need frames |
| [[vinc.canon.terminology]] | Copy rules, including the forbidden glyphs |
