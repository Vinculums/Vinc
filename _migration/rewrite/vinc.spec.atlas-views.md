---
doc_id: vinc.spec.atlas-views
layer: spec
domain: vinc/design
authority: spec
title: "Atlas views: perspective, timeline, and totals"
supersedes: vinc.d.graph-views
updated: 2026-08-06
inherits_from: [vinc.spec.surface-atlas, vinc.spec.atlas-visual-language]
tags: [spec, design, atlas, views, timeline, sankey, projection]
---

# Atlas views: perspective, timeline, and totals

> Atlas has one visual language ([[vinc.spec.atlas-visual-language]]) and four ways of projecting it.
> Two of those, timeline and totals, are planar and are drawn by a separate renderer. As-built for
> v0.7.4.

## 1. Two axes that cannot contradict each other

The switcher problem came first, before any view. A screen that lets you pick both "where you are"
and "how you are looking" can express states that do not exist, and a UI that claims a state the
renderer is not drawing is the worst kind of bug: everything looks fine.

So the two axes are owned separately and neither can move the other.

| Axis | Owner | Stored in | Shown as |
|---|---|---|---|
| Where, the scope | the trail | memory and the URL hash | the breadcrumb |
| How, the perspective | `AtlasPrefs.view` | local storage | a non-clickable tail chip, planar views only |

Changing perspective never pushes or truncates the trail. Only a change of scope moves the frame.
The planar views hold no scope state of their own; they read the end of the trail, so L0 means
everything, L1 means that topic, L2 means that node's neighborhood.

The consequence worth stating: **the row of options offered must be derived from what the current
renderer actually reads.** Deriving it from the perspective alone once produced controls that did
nothing, on a level whose renderer never looked at them.

## 2. The four perspectives

`3d`, `2d`, `timeline`, `totals`. Code authority is `AtlasView` in `app/src/atlas-prefs.ts`.

There were five. **Flow was retired by `adr-design-053` on 2026-07-26.** Scanning a graph node by
node was tiring, and the `rank` layout preset in 2D shows the same topological order with the
undirected relations still in the picture, which is more context for less effort. The renderer, its
depth control, its panel row and its copy strings all went with it.

One thing had to move rather than go. Flow was the only entry into the relation evidence panel, so
deleting it as it stood would have removed the only path to an edge's evidence. That entry is now a
click on a 2D line.

The frames that used to sit alongside the perspectives, a small-multiples grid, a domain-by-domain
matrix, and a two-domain compare, were all removed on 2026-08-02. Their deep links fold to L0 rather
than 404, so an old shared link lands on the map instead of on nothing. Their design rationale is
worth keeping and lives in [[vinc.research.view-redesign]]; restoring the screens would need a new
entry point designed first, because every old entry point is gone.

### 2.1 The legend filter is map-only, on purpose

`adr-design-074` made the edge legend double as a filter. Timeline is deliberately not wired to it.
The timeline screen has no legend control, so honoring the filter there would let a control that is
not on screen silently empty the screen. A filter the user cannot see is not a filter, it is a bug
report waiting to be written.

## 3. Timeline

**Question it answers.** When did this appear, and what else appeared around it.

### 3.1 As-built axis model

The original design put time on the vertical axis with an event spine and topic lanes running
across. That was replaced in v0.7.1 and the current model is different enough that the old
description would mislead:

| Element | As-built |
|---|---|
| Time | Vertical, past at the top. Scrolling is moving through time |
| Across | Branches. One topic, one branch |
| Metaphor | A root system growing downward and dividing. Entry animates the growth |
| Cause and effect | A lineage overlay, **off by default**. When on, directed relations are drawn over the time axis, and any link that runs backwards in time is dashed |

Drawing the lineage by default was rejected for the same reason event-to-concept links are hidden
until hover: shown always, they are spaghetti.

The backwards-in-time dash matters more than it looks. A derivation that points into the past is
either a data error or a genuinely interesting fact, and flattening it into the same line as every
other would hide both.

### 3.2 Two time bases

`adr-design-072` split the axis into recorded time and content time, because the system had been
conflating them and had neither cleanly.

- **Recorded** is when the row was written. This was already the only axis, computed by coalescing
  a creation stamp with a modification stamp, which meant editing a document moved its point on the
  timeline. A separate first-write stamp was added so that stops happening.
- **Content** is the time the document is *about*. A note on an event from a century ago has no
  representation on a recorded axis.

Content time is a free string validated by prefix against `YYYY`, then optional month, then optional
day. Only values that pass are stored, and a failing value produces an editor hint rather than a
silent drop. Every comparison and bucketing is **string lexicographic**, with no date parsing
anywhere. That is not laziness: mixed precision sorts correctly under string comparison, and
introducing a parser introduces a timezone and a locale into a field that has neither. Years before
the common era are unsupported, and that is stated rather than half-handled.

Bucket granularity adapts, month then year then decade, chosen from the count of distinct buckets.
A content axis can span centuries, and a monthly band would produce hundreds of bands. The band a
user clicks hands the drill-down the same string arithmetic that produced it.

**Nodes with no value are absent from the content axis, not zero and not today.** A caption states
how many were left out. Filling a missing timestamp with a plausible one is how a chart starts
lying.

### 3.3 The chart and the drill-down must count the same thing

Both the graph projection and the document range query take the basis as a parameter and **echo it
back in the response**. If the band you clicked is counted by one time column and the list it opens
is counted by another, the screen contradicts itself with no error. The echo makes the screen able
to state what it counted.

## 4. Totals

**Question it answers.** Where did this knowledge come from, where did it accumulate, and what
state is it in.

### 4.1 Three columns, and conservation as a self-test

`adr-design-047` restored the specified three-column Sankey after the implementation had quietly
shrunk to a single-axis stacked bar. Columns are source, then topic, then connection state.

Every column must sum to the same total. That is not decoration: **it is the classification logic
testing itself.** A column that does not balance means the classification is not a partition, and
that has actually happened, when a concept holding both an episode and a record was counted in both
source categories and inflated the total. The fix was a strict priority order so a concept lands in
exactly one bucket.

The orphan ribbon is an operational entry point rather than a statistic: it is the way into the
orphan-minimization path described in [[vinc.spec.atlas-visual-language]].

### 4.2 Scope changes the population, and L2 is an ego rollup

Totals counts a set, so the set has to be defined per scope.

| Scope | Population |
|---|---|
| L0 | Every concept |
| L1 | The concepts of that topic |
| L2 | The seed's neighborhood, using the **same traversal as the L2 subgraph** |

L2 was the problem. Focused on one node, the chart was rolling up that node's whole topic, so the
breadcrumb said one thing and the chart showed another, while the other perspectives were drawn from
the seed. Both now come from one shared traversal plan, so agreement is structural rather than
maintained.

Connection state stays global even under an ego population: an edge leaving the neighborhood still
counts, because "how far this reaches" is the value of the view and caging it inside the ego would
destroy it.

An older sidecar ignores the seed parameter, and the absence of a scope key in the response is how
the front end detects that and degrades to a topic rollup. The caption then says so, in place of
quietly showing a different number under the same label.

### 4.3 Ribbon placement, merging, and demotion

`adr-design-068` replaced a globally fixed sort of the ribbon strands. The important result was
negative and is worth stating precisely, because it prevents the same optimization being attempted
again:

**With stratum order fixed and band alignment holding, the total number of crossings is invariant
under reordering the strands.** A conflicting pair inside one topic, where the source order and the
state order disagree, crosses exactly once in one of the two gaps under any common ordering. The old
lexicographic order was already at that minimum; all it did wrong was pile every conflict into the
right-hand gap. Measured both before and after: identical crossing counts.

So the objective is not crossings. It is wander, the squared vertical displacement each strand
accumulates, minimized by a three-round barycenter sweep that keeps a round only if it strictly
improves. Strand coordinates are held in concept units rather than pixels, so a window resize does
not reshuffle the order. The topic band offset is stored once per tuple, which makes band alignment
a property of the data structure instead of an agreement between two sort functions that can drift.

The one lever that would actually reduce crossings, reordering the topic strata, was rejected: it
breaks the size-ordered convention readers rely on.

Three more rules keep the chart honest at scale:

- **A minimum strand thickness**, folded into the height floor rather than clamped at draw time, so
  proportion and conservation both survive. There is an absolute height cap; a cap relative to the
  container would make the demotion threshold depend on window size.
- **An others bucket** at the tail, for topics past a rank threshold or under a share threshold,
  with the top few protected, and only when at least two qualify. It merges whole, never partially,
  so the remaining topics keep exact values. It is not clickable, and marginal sums are preserved
  automatically because the merge is total.
- **Gap aggregation as a demotion**, when strands still exceed a count threshold, folding the
  ribbons into per-gap sums. End-to-end identity is restored on hover through a tuple join. The
  change of representation is announced in a one-line caption. **Silent change of representation is
  banned**, on the same principle as the truncation notice in search.

## 5. Why there is no topic coloring

The site already has a categorical palette, and reusing it here is the obvious idea. It was measured
and failed:

| Theme | Contrast | Adjacent-pair separation under color vision deficiency | Verdict |
|---|---|---|---|
| Light | passes | far below the threshold | fail |
| Dark | passes | effectively zero, the pair is one color | fail |

In the dark theme an adjacent pair is indistinguishable to a red-green deficient viewer. On the
site's topic distribution bar this is cushioned by direct labels and few items. A Sankey with dozens
of crossing ribbons has no such cushion.

So the planar views inherit monochrome ink with one focus color, and separation is carried by
position, thickness, shape, and direct labels. If coloring becomes necessary the palette gets
redesigned and re-measured first, as its own decision.

## 6. What the sidecar had to project first

The planar views needed data the graph projection was not sending. All of it was additive, so an old
front end kept working.

| Need | Why it was missing | Result |
|---|---|---|
| Edge confidence and evidence | present in the store, absent from the shaping function | thickness and tooltip evidence |
| A normalized node timestamp | three kinds carry different columns | the timeline axis |
| An aggregation rollup | did not exist | totals |
| Content time and a document first-write stamp | new schema columns | the second time basis |

**The trap in this work is worth recording, because it does not fail where you are working.** The
projection may only be widened on the specific query plans that actually select the column. Adding a
relation column to a plan whose relation table lacks it produces a binder error, and the failure is
not in the new view: **every level of the graph endpoint dies.** The column's location is per label,
so a node column can be added only to the plans that read that label and the others need a constant
placeholder. Assuming a rule that held for one column holds for another is the exact shape of this
mistake.

The basis swap is done in Python after shaping rather than by branching the query, for the same
reason: each new plan widens the surface of that trap. Nodes carry both timestamps down, so the axis
switch needs no refetch.

## 7. Screen copy discipline

The rules live in [[vinc.spec.ui-copy]] and [[vinc.canon.terminology]]. Two apply specifically here:

- Internal names and screen names are separate. The perspective values in code are lowercase
  identifiers; the screen shows its own words in both language decks. Chart-genre words are barred
  from the screen with one exception, `Sankey`, which was released because once the three-column
  chart actually existed, a name describing the aggregate was less accurate about what the reader
  is looking at than the name of the chart.
- An empty table cell uses a middle dot, never a dash.

## 8. Failures found in implementation, kept as warnings

These were invisible at design time and each is a shape that recurs on the next surface.

| Failure | Why it mattered |
|---|---|
| A graph plan did not scope the far end of an optional match by owner | Its sibling plan already did. Projecting evidence through it put another tenant's evidence sentences into a tooltip. An owner isolation regression, now pinned by a two-owner test on the real engine |
| Source classification was not a partition | Concepts counted twice, totals inflated |
| Axis plans did not project the target concept's timestamp | Entering L2 through an episode made every concept undated, so a timeline full of dates drew an empty state |
| A degraded second query reported success | Everything then aggregates as an orphan while the response says it worked. For a ledger, being quietly wrong is the worst outcome |
| A hidden element measured as zero width | Building all views eagerly gave a label plate the width of its padding alone. Views are built when visible |
| Re-rendering a panel broke outside-click detection | The clicked chip has already left the DOM by the time the event bubbles, so its own click reads as outside. Detection moved to the capture phase |

## Provenance

| Source | Relation |
|---|---|
| `app/src/atlas-prefs.ts` | `AtlasView`, the perspective list, the planar test |
| `app/src/surfaces/atlas-planar.ts` | Timeline and totals renderers |
| `app/src/surfaces/atlas-lodes.ts` | Strand placement, kept pure and free of copy |
| [[vinc.research.sankey-alluvial]] | Research input behind `adr-design-068`, including the crossing invariance argument |
| [[vinc.research.view-redesign]] | Research input behind the independent 2D renderer and the three-column restoration |
| [[vinc.canon.decisions]] | Registry for the `adr-` identifiers used above |
