---
doc_id: vinc.spec.atlas-visual-language
layer: spec
domain: vinc/design
authority: spec
title: Atlas visual language and interaction grammar
supersedes: vinc.d.atlas-visual-language
updated: 2026-08-06
inherits_from: [vinc.spec.surface-atlas, vinc.canon.terminology]
derives_from: [vinc.research.ux-restructure]
tags: [spec, design, atlas, marks, encoding, interaction, layout]
---

# Atlas visual language and interaction grammar

> How the map encodes meaning: what a shape means, what size means, what a line means, and which of
> those a user is allowed to change. As-built for v0.7.4. The views built on top of this language
> are in [[vinc.spec.atlas-views]]; the material of the chrome that floats over the map is in
> [[vinc.spec.glass-language]].

## 1. The one rule the rest follows from

**A channel that carries meaning belongs to the spec. A channel that carries emphasis belongs to the
user.** This is `adr-design-031`, and every other rule here is a consequence.

The reason is shareability. A pack travels between accounts ([[vinc.canon.share-pack]]), and if the
receiving user has remapped shape or color, they are not looking at the same graph the sender saw.
The map would still render, which is what makes the failure dangerous: nothing reports it. So the
encoding is frozen and only its intensity is adjustable.

| Layer | Contains | Owner |
|---|---|---|
| Closed, the encoding | type maps to shape, the point color means focus, monochrome ink, cross-domain treatment | Spec. Changing it takes a decision |
| Open, the intensity | view, size scale, label density, arrowheads, spread, orphan visibility, layout preset, rail width, edge-kind filters | User. Persisted per machine in local storage |

The open layer is enumerated in `app/src/atlas-prefs.ts` as `AtlasPrefs`. That file is the
as-built list, and it carries the same warning in code, which is deliberate: the discipline dies the
moment the two lists drift and nobody notices.

## 2. Marks

### 2.1 Shape is type

| Node kind | 3D primitive | Why |
|---|---|---|
| Concept | sphere | The most numerous kind gets the least ink. Its earlier hex prism read as bulky |
| Episode and Record | small regular hexagonal prism, all edges equal | The hexagon signature moved to the scarce kind, where it is a signal rather than noise |
| Bridge | octahedron | The solid form of a diamond: neutral about direction, which is what a bridge is |
| Domain, the L0 supernode | sphere with an equatorial ring | It says "a set of spheres" with its own geometry |
| Gateway | its own kind's shape, translucent | Present here, not a member here |

The reassignment is `adr-design-035`. Shape is doing more work than a nominal channel normally
should, which is the open question section 8 records.

### 2.2 Size is importance inside the current scope, and nothing else

Radius scales with the square root of the count, never linearly: linear scaling lets one hub eat the
frame. At L0 the count is the domain's records; below it, degree. Search score and pin are not
folded into size, because size already means one thing and a second meaning in the same channel is
unreadable. Pin gets its own weight in hub promotion instead.

### 2.3 State is a dot, not a color

A satellite dot at the node's upper right: filled for confirmed or pinned, half for in progress,
empty for candidate. Color is unavailable for this because color is reserved (section 2.5).

### 2.4 Depth comes from shading and atmosphere, never hue

Matte ink Lambert material in three ink steps, a paper background with fog so distance fades into
the background, one neutral ambient plus one directional light. No pure black shadow.

**The material was glass for one stretch and is not any more.** A decision extended the glass
language from the chrome into the node material, giving nodes a frosted physical material with
clearcoat and a baked environment map. `adr-design-077` reverted that on 2026-08-03, on a
measurement rather than a preference: in the default dark theme the constellation-glow branch of the
node builder returns early with a flat material, so the glass never rendered at all for most nodes.
The project was paying for a second material system, a physically based shader and a generated
environment map, and getting nothing on the default screen. The reflection source retired with it,
because scene environment maps are only read by physically based materials and were pure dead code
after the revert. Code anchor: `app/src/surfaces/atlas3d.ts`, the `material` helper.

Lambert rather than a flat unlit material, deliberately: the light-driven gradient across a face is
the depth cue in the light theme. The glass went; the cue stayed.

### 2.5 One point color, reserved

The single point color means "the current focus" and nothing else: the L2 seed, the node a search
landed on, the highlight right after an import. It is the same meaning the primary button carries,
which is where the eye should go. It is never used on edges and never used for a type.

In the dark theme the whole ink axis inverts, marks become light ink on a dark field and the focus
color lightens with it. That is a palette inversion, not a change of grammar.

## 3. Edges

### 3.1 Form carries the relation, weight carries confidence

Every edge is a hairline. Width was tried and abandoned: in a 3D renderer, line width builds a
cylindrical tube mesh, and a weighted graph becomes a tangle of fat translucent pipes. Weight moved
to ink density in three steps, with unweighted cross-domain edges at the lightest step.

That density ramp then sat unreachable for a long time. It split on a `weight` field that the
projection layer never sent and the sidecar never produced, so every edge rendered at one density
while the code looked like it encoded something. `adr-design-077` swapped the ramp onto relation
`confidence`, which the backend does carry and which reaches the app. **A channel that is wired to a
field nobody fills is worse than an absent channel**, because it looks like information.

Direction is an arrowhead on the three directed relation types only, and it sits just outside the
target node's surface, sized from that node's radius. A fixed percentage along the line buries the
arrowhead inside a large hub's mesh.

Base curvature is slight rather than zero. In a dense 3D graph, straight lines pass through node
bodies and pile onto each other; a gentle curve separates them.

### 3.2 Edges are emitted from node centers, and ports were rejected

Port notation, where lines leave from fixed sockets, works when the layout is 2D, the semantics are
flow, and degree is low. Atlas is an orbiting 3D camera, most relations are symmetric, and hubs have
dozens of edges. An opaque mesh already hides the interior segment, so a center-emitted line reads
as surface-emitted anyway. The two things ports would have given were absorbed instead: the
arrowhead sits at the target surface, and the detail panel splits direction as an outbound and
inbound count, which is the port's information delivered at the moment of inspection.

### 3.3 Edge kinds are the legend, and the legend is the filter

`adr-design-074` named the priority chain the renderer already used, rather than inventing a
taxonomy: directed, then bridge, then cross-topic, then the rest. Each edge belongs to exactly one
kind, so the legend row, the filter switch, and the drawn line share one vocabulary. The judgment
lives in a single function, `edgeKindOf` in `atlas-prefs.ts`, because a new kind has to move three
things at once and splitting the decision guarantees they drift.

The stored value is the list of kinds that are **off**, not the list that is on. A future kind
therefore defaults to visible instead of being silently hidden by an old saved preference.

## 4. Focus and recede

Hover does not highlight, it recedes everything else. The values are canon across every view and
every surface, and a view using its own number is treated as drift:

| Target | Opacity floor |
|---|---|
| Non-neighbor nodes | 0.40 |
| Non-adjacent lines | 0.12 |
| Attached text, labels and figures | 0.35 |

Nodes stop at 0.40 so the surrounding map still reads as context. Lines drop further because they
are the main source of visual noise. Text sits just under the node value because glyphs leave a
strong afterimage.

Three properties make this hold:

- The numbers are **tokens**, read through `app/src/recede.ts`. Numeric literals in view code are
  banned for the same reason hardcoded colors are. This rule exists because 3D once hardcoded its
  own value at nearly four times the canonical one, and nothing caught it.
- Focus and its neighbors take no attenuation at all.
- Under reduced motion the values are unchanged. What disappears is the easing, not the effect:
  vestibular safety is not a reason to stop distinguishing focus.

## 5. Structure discipline

`adr-design-032` fixed three rules about the shape of the map itself.

- **Hubs are promoted by pin first, then degree.** A node the user has pinned outranks a node with
  more connections, because a pin is an explicit statement about what the map is organized around.
- **Orphans get two exits, not a filter alone.** They can be hidden through the open layer, and they
  can be joined: the detail panel offers link candidates for a zero-degree node from a deterministic
  ranking, tag intersection above shared topic, already-linked pairs excluded. Confirming a
  candidate goes through the normal fragment write path rather than a special one, so there stays a
  single write surface.
- **Focus is a fade, not a hide.** See section 4.

## 6. Layout

### 6.1 Coordinates come from the sidecar and are deterministic

`adr-design-017` puts layout computation in the sidecar as pure functions. The client pins the
positions it is given and runs no force simulation. This is the one clause that survived every
subsequent layout revision untouched, and it is why two people opening the same pack see the same
picture.

### 6.2 Two presets, and why the other six went

`adr-design-074` reduced six presets to two, `hub` and `rank`:

| Preset | What the coordinate says |
|---|---|
| `hub`, the default | Where the center is. The root sits at the origin, topics fan out in every direction, and the concepts under a topic explode further along that topic's own direction. Centrality is not discarded, it moved inside the branch: within one topic, the better connected a node is, the closer to its axis |
| `rank` | What comes before what. The layer axis is global topological order. Topics do not form clusters, because lineage crosses topics, but order within a layer is banded by topic |

The other six were variations on those two, so there was no basis on which to choose one. Saved
preferences naming a retired preset fold to `hub` through an alias map, so old deep links keep
working instead of failing.

The interesting part of `hub` is that it reads the topic string **as a path**. Nothing in the
codebase split that string before, so a hierarchy that was already written into every node's topic
was invisible to the layout.

### 6.3 Two coordinate sets, because flattening loses nodes

The sidecar emits a 3D set and a separate 2D set. The 2D layout is not the 3D one with the depth
axis dropped. Under `hub`, two nodes separated only in depth collapse onto the same point when
flattened, and one of them is simply gone from the picture with no error anywhere. The 2D set
re-lays the same tree in a planar grammar so overlap cannot arise in principle. An older sidecar
without the second set degrades to the 3D pair.

## 7. Interaction grammar

| Gesture | Effect |
|---|---|
| Node drag | Move and pin, with an anchor mark. Double tap the anchor to release. The pin is a session-local override; sidecar coordinates remain canonical |
| Canvas drag, wheel | Pan, zoom. Crossing a zoom threshold changes level |
| Shift and drag | Region select, producing a selection set with actions including sharing that slice |
| Right click a node | Open, trace from here, pin, share a slice from here |
| `f` | Fit the camera to the selection |
| `1` `2` `3` | Jump to a level, keeping the current focus |
| `Esc` | Step back one layer at a time: clear the selection, then close the panel, then rise a level |

Escape is a stack, not a close button. Every surface that adds a floating layer inserts itself into
that order rather than grabbing the key.

Selection is scope. The same set that is highlighted is the set that a share sheet will carry, which
is why region select and search overlay both end at the same export sheet: sharing is the exit from
a scope, not a feature of one screen.

## 8. Deliberately unresolved

- **Whether type should get color.** `adr-design-078` proposed a closed five-color set and was
  rejected, for a reason worth keeping: the live corpus only ever shows three node kinds at once, and
  three are separable by fill and outline alone. The recorded reopening threshold is factual rather
  than aesthetic. If Bridge and Entity come into real use and more than three kinds are routinely on
  screen with a pair that shape cannot separate, monochrome stops being an advantage and the decision
  reopens by itself.
- **Line width.** `adr-design-079` restores real width using the three.js line addon, which is
  already in the dependency and so does not breach the no-new-library rule. Not landed. It needs a
  resolution update on every resize and costs more geometry than a plain line at thousands of edges.
- **Local graph as the default entry.** `adr-design-080`. Starting from the whole graph is a
  personal-knowledge-tool convention, not a professional-tool one; every comparable specialist tool
  starts from a search or a selection.
- **Decisions cited here that are not in the registry.** The mark language, the 3D adoption, the
  edge and material revision, the dark palette inversion, the node glass, and several layout
  revisions are named in the original by label only and were never filed as decision nodes. The
  original recorded this backlog itself. They are described here by their effect rather than cited by
  identifier, because citing an identifier that does not resolve is worse than not citing.

## Provenance

| Source | Relation |
|---|---|
| `app/src/atlas-prefs.ts` | The open layer as-built, plus `edgeKindOf` |
| `app/src/surfaces/atlas3d.ts` | Materials, primitives, edge rendering |
| `app/src/recede.ts` | The single read point for the recede values |
| [[vinc.canon.decisions]] | Registry for every `adr-` identifier used above |
| [[vinc.research.atlas-mark-verdict]] | The verdict that produced `adr-design-077` through `adr-design-080` |
| [[vinc.research.atlas-visibility-verification]] | Contrast measurement behind the dark palette work |
