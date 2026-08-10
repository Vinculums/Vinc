---
doc_id: vinc.spec.surface-atlas
layer: spec
domain: vinc/design
authority: spec
title: Atlas, the map surface
supersedes: vinc.d.graph
updated: 2026-08-06
inherits_from: [vinc.spec.surface-tree]
tags: [spec, design, atlas, graph, surface, 4-state]
---

# Atlas, the map surface

> The first screen. Called `Home` on screen and `atlas` in every identifier, for the reason given in
> [[vinc.spec.surface-tree]] section 6. It is the only surface that registers handles with the
> shell, and the only one that owns the map controls in the top bar.
>
> This document specifies the surface: its two axes, what each gesture does, and its four states.
> The mark language, edge treatment and monochrome discipline are specified in
> [[vinc.spec.atlas-visual-language]]. The planar viewpoints are specified in
> [[vinc.spec.atlas-views]]. Neither is restated here.
>
> Measured against `Vinc/app/src/surfaces/` on 2026-08-06.

## 1. Two axes that cannot contradict each other

`adr-design-043` split navigation into two independent questions, and the split is structural
rather than a naming convention.

| Axis | Question | Held by | Values |
|---|---|---|---|
| **Scope** | where am I | the trail stack | `l0` whole graph · `l1` one topic · `l2` one node |
| **Viewpoint** | how am I looking at it | stored preferences | `3d` · `2d` · `timeline` · `totals` |

The viewpoint never enters the trail. That is what makes the two structurally incapable of
disagreeing: no sequence of gestures can produce a scope that the current viewpoint cannot render,
because the viewpoint is applied to whatever scope the trail resolves to.

**Four viewpoints, not five.** `adr-design-053` retired the flow viewpoint: walking a graph node by
node was tiring, and the `rank` layout preset in the two dimensional viewpoint shows the same
topological order with more context around it.

### 1.1 The three scopes

| Scope | Nodes | Edges | Source |
|---|---|---|---|
| `l0` | one supernode per topic, sized by record count | topic couplings, weight by crossing count | the topic census |
| `l1` | the nodes of one topic | relations inside the topic, plus **edge gateways**: nodes that couple outward, drawn translucent at the boundary | the graph query filtered by domain |
| `l2` | the neighborhood of one node, depth two | adjacent edges | the graph query by seed and depth |

`adr-design-080` made the local graph the default landing for a node rather than the whole map,
because arriving at the top level after asking about one thing discards the question.

## 2. What the gestures do

| Gesture | Result |
|---|---|
| click a topic node at `l0` | open that topic lens |
| click a gateway at `l1` | follow into the neighboring topic lens |
| click any other node | open the detail panel in the document column |
| right click a node | context menu: open · trace here · share here · delete |
| `Escape` | pop one step off the trail |
| `f` | fit the current scope to the viewport |

**Right click is a menu, not an action.** It used to run the refocus immediately, so a hand that
expected a menu got a screen change with no warning. Refocus to `l2` now happens through the menu
entry or through the detail panel, which are both places where the user has already committed.

**There is no zoom threshold transition.** The earlier design moved between scopes when a continuous
zoom crossed a boundary. Measured on 2026-08-06, no such threshold exists: every scope change is an
explicit click, menu entry or address. Implicit transitions made the map change meaning while the
user was only trying to see something more closely.

### 2.1 The breadcrumb is gone

The trail is still a stack and `Escape` still walks back up it, but nothing draws it. `adr-design-069`
put a document tree in the left column, and that tree marks the active topic and document
continuously. A breadcrumb that repeats what a permanently visible tree already says is spending
the scarcest row on the screen to say it twice.

The consequence is that **back is a key, not a target**. That is a real reduction in
discoverability, and the first run coach marks exist because of it.

## 3. Renderers

| Viewpoint | Renderer | Why |
|---|---|---|
| `3d` | three.js through a force graph wrapper | depth carries structure that a plane has to encode as position alone |
| `2d` | an independent Sigma renderer (`adr-design-046`) | the plane is not a degraded 3D view, so it is not the 3D renderer with one axis removed |
| `timeline`, `totals` | a planar renderer of their own | see [[vinc.spec.atlas-views]] |

**Positions are computed by the sidecar** (`adr-design-017`) and pinned on arrival. The client runs
no force simulation. Two reasons hold this line: the same coordinates must be reproducible across a
reload and across devices, and a simulation that keeps running turns every screenshot and every
support question into a description of a different picture.

Layout presets are two, not six. One encodes **order** by rank, one encodes **centrality** by hub
distance. The rest were variations of those two with no separate question to answer. Retired preset
names stored in a browser are folded to `hub` on read, rather than reset, so an old profile does not
silently lose its choice.

> **Node material is matte, not glass.** `adr-design-037` introduced a layered glass treatment and
> `adr-design-077` retired it on graph nodes after measurement showed the default dark theme took an
> early return into a plain material, so the glass path never rendered at all outside domain nodes.
> The cost of maintaining two material systems bought nothing on the default screen. See
> [[vinc.spec.glass-language]].

## 4. Chrome, and where it lives

| Region | Contents |
|---|---|
| top bar right | the lens button, which opens one panel holding both view options and the legend, plus the viewpoint segmented control |
| bottom bar | metrics only. What the current screen is counting |
| document column | the detail panel, and the reader below it |

The bottom bar is a status strip, not a floating pill. A floating pill reads as a banner and gets
ignored, which is exactly the outcome a metric must not have.

The detail panel used to float over the canvas, which forced a choice: see the map, or see the
detail. It is now a grid column of the shell, so the map narrows instead of being covered. The
surface still creates the markup, because content belongs to the surface, but the shell supplies
the position and the router empties the column when the surface leaves.

### 4.1 The legend is the filter

`adr-design-074` collapsed three vocabularies into one. Edges fall into exactly four kinds by a
single classifier: directed, bridge, cross topic, and related. The legend entry, the filter switch
and the drawn line all name the same kind, so there is no way for the legend to describe something
other than what is on screen.

Two consequences follow from the classifier being a single function: adding a kind or reordering
the priority chain moves all three places at once, and **a kind with zero edges in the current
scope does not appear**. There is nothing to switch off.

Filter state is stored as the list of kinds that are **off**, not the list that are on. A later
release that adds a kind therefore turns it on by default for existing users, instead of hiding it
because a stored array predates it.

## 5. Preferences are the open layer only

`adr-design-031` divided expression into two layers, and only one of them is the user's.

- **Open**: viewpoint, node size scale, edge scale, label density, arrowheads, spread, layout
  preset, unlinked node visibility, timeline options, document column width.
- **Closed**: shape by type, the single focus color, monochrome ink. Owned by
  [[vinc.spec.atlas-visual-language]] and deliberately not configurable.

Preferences persist per machine in browser storage and involve no sidecar contract, which keeps the
canonical coordinates untouched by a display choice.

> **Edge scale is a multiplier, not an encoding.** Weight is carried by ink density. A global
> thickness multiplier that a user drags does not restore thickness as the weight channel, and
> conflating the two would make the density rule meaningless.

## 6. Four states

Copy is owned by the string constants in code; the inventory is [[vinc.spec.ui-copy]]. If a string
here differs from the code, the code is right.

| State | What renders |
|---|---|
| `idle` | the empty box: "No knowledge map yet. Add files or run a search, and the map builds itself from your documents." with an import action that **opens the Tools sheet in place** rather than navigating |
| `loading` | one line, "Arranging {n} topics" |
| `success` | the scope rendered in the current viewpoint. Topic tooltip gives document count and cross topic link count. Gateway tooltip names the topic it crosses into |
| `error` | the **same box as empty**, carrying "The map couldn't load. Retry anytime. Your documents are untouched." |

**Unreachable renders as the empty box, not a red banner.** Owner instruction, 2026-08-05. A red
banner over a blank canvas reads as damage, and the sentence that matters is the one saying nothing
was lost. Using the same container as empty also means a narrow scope with no data and an
unreachable sidecar do not look like two different classes of failure to the layout.

The idle action opening a sheet rather than a surface is the same rule: a first time user who
presses `Import files` should not land somewhere that asks them to press `Import files` again.

## 7. Retired paths that are still accepted

`#atlas/compare/a/b` is a dead address. The frames (Grid, Matrix and Compare) were removed on
2026-08-02, and Compare went with them because a Matrix cell click was its only entry point.
The router accepts the old address and folds it to the top level rather than returning nothing,
for the reason given in [[vinc.spec.surface-tree]] section 3.

## 8. Known gaps

| Gap | Detail |
|---|---|
| The error state has no retry control | A retry label exists in the copy deck and is never rendered. The sentence promises retry and the screen offers no button. Measured 2026-08-06 |
| Compare code outlives its entry point | The two lens Sigma comparison and its supporting state are still in the surface module with nothing that reaches them |
| Several rules cannot be cited | The renderer adoption, the primitive set revision, the frame retirement and the context menu are recorded in code by decision numbers that never became ADR nodes. See [[vinc.canon.decisions]] |

## Provenance

| Source | Relation |
|---|---|
| `Vinc/app/src/surfaces/atlas.ts` and its renderer modules | as-built authority for everything above |
| [[vinc.research.ux-restructure]] | the restructuring study behind the semantic zoom |
| [[vinc.research.graph-structure]] | the quantitative structure analysis the scope levels were sized against |
| [[vinc.spec.dataplane]] | the graph and topic census contracts this surface consumes |
