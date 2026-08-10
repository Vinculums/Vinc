---
doc_id: vinc.spec.surface-tree
layer: spec
domain: vinc/design
authority: spec
title: The surface tree, as built
supersedes: vinc.d.index
updated: 2026-08-06
inherits_from: [vinc.canon.terminology]
tags: [spec, design, surfaces, shell, routing, 4-state]
---

# The surface tree, as built

> The index of every UI surface in the desktop shell and the web app, plus the conventions every
> surface obeys. `adr-design-015` established a tree of five surfaces and a Settings popover. That
> tree no longer exists. **Three surfaces route today**, and this document is the record of what
> replaced the other two and why.
>
> Measured against `Vinc/app/src` on 2026-08-06. Where this document and the code disagree, the
> code wins and this document is wrong. Colors and dimensions are never written here: design tokens
> and components are the only reference. Vocabulary and forbidden words live in
> [[vinc.canon.terminology]] and are not restated.

## 1. What routes

The router in `main.ts` knows three surfaces and nothing else.

| Hash | Screen name | Module | Keyboard |
|---|---|---|---|
| `#atlas` | `Home` | `surfaces/atlas.ts` | `1` |
| `#search` | `Search` | `surfaces/search.ts` | `2` |
| `#packs` | `Packs` | `surfaces/packs.ts` | `3` |

An unknown hash resolves to `atlas`, so the app always has a first screen. Settings is not in this
table on purpose: it is a modal over whatever surface you are on, and it survives navigation.
See [[vinc.spec.settings]].

## 2. Why five became three

The fold was not a simplification pass. Each surface lost its reason to exist separately, one at a
time, and the routing table caught up last.

| Decision | What it took away | What absorbed it |
|---|---|---|
| `adr-design-056` | the left icon rail that `adr-design-026` had introduced as surface navigation | one top bar |
| `adr-design-057` | the need to open a screen in order to look something up | an always-on search field in the bar |
| `adr-design-069` | the assumption that the left column is for surfaces | a document tree, which is a different thing |
| `adr-design-070` | the separate `Show in Atlas` row action | the row click itself |
| `adr-design-071` | three separate doors for create, import and share | one Tools modal |

Once ingestion, sharing and creation all lived in one modal reachable from any surface, the Intake
surface had nothing left that was only its own. Once the document list became something you open
over your current work rather than travel to, Records was the same. Both were folded into the
surface whose data they already shared: intake writes packs, so it went to Packs; the document list
is what an empty query returns, so it went to Search.

> **The count is not the point.** `adr-design-015` was right that fragmented navigation was the
> problem, and the fold continues that decision rather than reversing it. What changed is where the
> reduction happens: not by merging screens, but by moving persistent work into the shell so that
> fewer screens need to be travelled to at all.

## 3. Aliases, and why they are permanent

```
ALIAS = { intake: "packs", records: "search" }
```

An old hash is not resolved silently. The router **rewrites the address bar to the canonical hash
and re-enters**, so the active highlight, the deep link and the screen never disagree. Resolving
without rewriting would leave `#records` in the address bar while the Search surface renders, and
the next copied link would keep the dead name alive.

These entries stay indefinitely. They cost two map entries and they cover bookmarks, older copy
that still says `#intake`, and any pack or document that recorded a link. The alternative is a
dead link that produces the default surface with no explanation, which reads as a bug.

The retired Atlas deep link `#atlas/compare/a/b` is handled by the same principle inside the Atlas
surface: it is accepted and folded to the top level rather than dropped. See
[[vinc.spec.surface-atlas]].

## 4. The shell, and who owns what

The shell is a grid: top bar, left document tree, document column, surface. Nothing overlays
anything else, which is the whole point of the arrangement.

| Region | Owner | Holds |
|---|---|---|
| title bar | shell, desktop only | logo and window controls. The web build has none: the site navigation carries the logo |
| top bar left | shell | Settings, Tools, conventions. **App handles** |
| top bar center | shell | the search field and its mode selector. See [[vinc.spec.surface-search]] |
| top bar right | Atlas | lens and viewpoint controls. **Map handles** |
| left column | shell | the document tree, topic then document |
| document column | shell owns the column, the surface fills the body | the reader and the detail panel |
| surface | the routed module | everything else |

**Left is the app, right is the map.** While both groups sat on the right they read as one cluster,
so choosing how to view the map and operating the application looked like the same kind of act.

### 4.1 One hook, and the rule behind it

A surface hands the shell exactly one registration and takes it back when it leaves. Atlas
registers `share` and `openNode`; the router clears the registration before every render.

**A visible handle must do something.** With no registration the map controls are emptied rather
than disabled, because a control that is present and inert teaches the user that controls are
unreliable. The same rule demotes a document tree click to a hash landing when Atlas is not
mounted, instead of leaving a row that does nothing.

The router, not the surface, performs the teardown. A surface cannot detect its own removal: the
container outlives it, and any content the surface transplanted into the shell is already outside
its own subtree.

### 4.2 Control and data are separate paths

- **Control is Tauri IPC**: window, session, sidecar lifecycle.
- **Data is localhost HTTP** to the sidecar on the loopback interface with a session token. The web
  build has no local sidecar and goes through the account proxy instead. See
  [[vinc.spec.web-system]].

No surface holds graph or query logic. That separation is what lets the same bundle serve the
desktop shell and the web app, with a runtime skin attribute choosing the token set.

## 5. Conventions every surface obeys

| Item | Rule | What breaks without it |
|---|---|---|
| 4-State | every data surface declares `idle` (empty), `loading`, `success`, `error` | a surface ships with a blank screen for the case nobody demonstrated |
| Empty and error copy | must carry a real action and say what was **not** damaged | a dead end, and a user who assumes data was lost |
| Copy language | English is the source, Korean is a full deck (`adr-design-018`, `adr-design-042`) | two languages drift and the shorter one becomes a downgrade |
| Voice | verb first, contractions, one sentence, no narration | instructions read as marketing |
| Copy authority | the string constants in code are canon. The inventory is [[vinc.spec.ui-copy]] | documents claim strings the product does not ship |
| No em dash | on screen, use a middle dot, parentheses or a colon | a glyph that renders inconsistently across the shell and the site |
| Color and dimension | design tokens and components only, never written into a surface | the theme stops being a single switch |
| State notation | a glyph plus text weight, never color alone | the signal disappears in the other theme and for color vision deficiency |

**4-State is the value this layer adds.** It is not documentation of the happy path. Naming the
empty and error states forces someone to decide, before implementation, what the surface says when
there is nothing and when the sidecar is unreachable. Those two states are where an offline first
product is actually judged.

## 6. Screen name and code name are different names

The first surface is `Home` on screen and `atlas` everywhere else: hash, module, deep link, alias
map. The separation is deliberate and it is one directional. **The code name never reaches the
screen**, and the screen name never becomes an identifier, because renaming a screen would
otherwise invalidate every stored link.

The old rule that headings must read `Surface {N}. {name}` with N running 1 to 5 is retired with the
numbering it depended on.

## 7. Where each surface is specified

- [[vinc.spec.surface-atlas]], the map. Three scope levels and four viewpoints
- [[vinc.spec.atlas-views]], the planar viewpoints in detail
- [[vinc.spec.atlas-visual-language]], marks, edges and the manipulation grammar
- [[vinc.spec.surface-search]], the surface and the always-on bar above it
- [[vinc.spec.surface-packs]], where knowledge leaves and arrives
- [[vinc.spec.surface-intake]], the ingestion contract of an absorbed surface
- [[vinc.spec.surface-records]], the browsing contract of an absorbed surface
- [[vinc.spec.settings]], four tabs in a modal that is not a surface

Data contracts are not restated in any of them. The sidecar routes are specified in
[[vinc.spec.dataplane]], the document canon in [[vinc.canon.chunk-ssot]], and the assistant
interface in [[vinc.canon.mcp-contract]].

## 8. Addresses

```
#atlas                         top level
#atlas/<domain>                one topic lens
#atlas/trace/<node_id>         focus on a node
#atlas/.../as/<view>           viewpoint, orthogonal to scope
#search  #packs                the other two surfaces
#intake  #records              rewritten to #packs and #search
```

The viewpoint is a suffix rather than a path segment because scope and viewpoint are independent
axes. Folding them into one path is how an address ends up naming a screen that cannot be rendered.

## 9. Known gaps

| Gap | Detail |
|---|---|
| Packs has no visible entry point | The tree carries only `Home`. Packs is reachable by hash, by the command palette, by key `3`, and by one button in Settings under Data. Measured 2026-08-06 |
| The palette is not discoverable | `Cmd/Ctrl+Shift+K` after the bar search took the unmodified chord (`adr-design-057`). The shortcut sheet on `?` is the only place it is announced |
| Two decision series are unregistered | Several rules this layer depends on (the Home naming, the two column document list, the sheet size vocabulary) are cited in code by numbers that never became ADR nodes, so they cannot be cited from here. See [[vinc.canon.decisions]] |
