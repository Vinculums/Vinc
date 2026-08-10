---
doc_id: vinc.research.atlas-mark
layer: research
domain: vinc/design
authority: research
title: "The mark language questioned: does glass and hairline suit a data view"
supersedes: vinc.d.atlas-mark-redesign
updated: 2026-08-07
inherits_from: [vinc.spec.atlas-visual-language, vinc.research.atlas-visibility]
tags: [research, design, atlas, marks, channels, monochrome, ontology]
---

# The mark language questioned: does glass and hairline suit a data view

> Research input, dated 2026-08-03. The owner asked whether the glassy spheres and thread-thin lines
> are actually right **for a data view**, why they were being kept, and to study how practitioners
> build ontologies in comparable tools and redesign whatever does not fit.
>
> **The question was fair and this document exists because of what it exposed.** The two preceding
> visibility studies took the closed layer as given, shape maps to type, glass, hairline, monochrome,
> and repaired contrast inside it. That premise had never been examined. This is the examination.
> Its verdict is not here: see [[vinc.research.atlas-mark-verdict]].

## 1. Three findings, stated before the argument

**1. The glass was not being rendered in the default theme.** The physically based material is
constructed only on the non-glow branch of the node builder. The dark glow branch returns a flat
unlit material before reaching it, and dark is the default theme. So the product's default screen had
no glass on it. What the owner had been looking at was a flat fill with an additive glow sprite. The
project was paying to maintain two material systems and using its signature one only in the
non-default theme.

**2. The hairline pinned the width channel to zero and then failed to use the channel it moved to.**
With line width at zero the renderer draws a hairline, and the ink density ramp that was supposed to
carry weight instead **branches on a field nothing populates**: the projection layer never sends it.
So a 3D edge carries neither width nor density. It carries two bits: directed or not, cross-domain or
not.

**3. Shape carries the most important information on the weakest channel.** Shape ranks below size
and lightness among nominal channels and collapses first under zoom. And the live type distribution
at the time of measurement was **concepts 148, records 53, episodes 44**, roughly 60, 22 and 18
percent, with bridges and entities at zero. Six marks in ten are the same shape, so there is little
for the channel to distinguish. The planar view has no shape channel at all.

**The common diagnosis: the render budget is spent on materiality, and the data channels are
empty.** The structural advantage this product has over comparable tools, typed nodes and typed
edges, is not on the screen.

## 2. What the code actually draws

Measured 2026-08-03. These readings were self-reported by the session and unverified against the
repository at the time, which the verdict document repeats as a condition on acting.

| Claim | Finding |
|---|---|
| Glass is used on the default dark screen | **False.** The physical material is constructed after the glow branch has already returned a flat one |
| The remaining glass call sites are reachable | One requires the light theme or a mount function with **no callers**. The other is for the domain supernode, which the graph projection does not emit. Both effectively dead |
| Edge width carries data | **False.** Width reflects only the user's scale preference and is otherwise zero |
| Edge density carries weight | **False, and dead.** The branch exists; the value it reads is never set |
| Five node kinds are separated by shape | **Partly.** Three kinds exist in the data. The planar view draws circles for all of them |

**A great deal of what was defended as a closed convention was already absent from the screen.**
What was being preserved was not the convention but the memory of it.

## 3. What the external reading contributed

### 3.1 The failure mode of an untyped graph view

The widely cited critique of the best known markdown graph view names three failures: no operational
metadata, so a draft and a finished note look identical; collapse at scale, where the layout works
below about fifty nodes and becomes an untraversable tangle past two hundred; and search dependence,
where a heavily linked index note cannot be told apart from an ordinary one without reading names,
which removes the reason to look at a picture. Two things were reported as working: **typed links
that establish hierarchy**, and the **local graph**, which was described as retaining its usefulness
at any scale.

### 3.2 That tool's structural limit is this product's advantage

The recurring complaint from personal knowledge graph practitioners is precise: there are no entity
types, so every node is just a note, and there are no typed links, so a relation cannot carry
metadata.

Vinc has both, plus role, confidence and evidence on relations. **And it puts them on the weakest
channel under the least favorable conditions.** What that tool cannot show, this one can and does
not.

### 3.3 Color for type is the convention, not the exception

Ontology visualizers distinguish entities, properties and relations by node color and line style.
Graph platform guidance says the same: use color for category, cluster and hierarchy, and encode
meaning so it is read immediately.

Monochrome is the opposite choice. It had reasons, that a shared pack must read identically anywhere
and that a color wheel invites abuse. But **assigning fixed colors to a closed five-item type
vocabulary is not arbitrary coloring**: it is deterministic and shareable, and the standard remedy
for color vision deficiency, redundant encoding by color plus shape plus label, already exists.

### 3.4 Materiality is probably harmless and definitely not helpful

The minimal-ink principle has been criticized for resting on a single author's observation, and
follow-up experiments across three decades found that decoration is not always harmful and can aid
recall and engagement. **Those same experiments did not find an accuracy benefit from three
dimensional treatment.**

So there is no basis for calling glass a sin, and none for calling it useful. **The criterion is
therefore opportunity cost:** is the budget it consumes displacing a data channel. Here it was. The
translucency of the node fill is part of what pushed contrast below the floor in the preceding
study.

### 3.5 Edge width is a real channel, and the technical constraint was misread

Graph legibility experiments agree that **edge count is the principal driver of complexity**, and
that tangling appears at surprisingly small graphs. The remedies with support are bundling,
compression and highlighting. **Thinning the lines is not one of them:** it does not reduce crossings
and it does reduce legibility.

The technical fact behind the original decision is real: in this renderer the basic line's width
property is ignored on most platforms and fixed at one pixel. But that produced a false dichotomy of
fat tubes or hairlines. **There is a third option**: the addon line type with a screen-space width,
which is a thin quad strip rather than a tube.

## 4. The channel budget

A data view has a finite set of channels. This is how they were allocated.

| Channel | Carrying | Assessment |
|---|---|---|
| Position | domain hierarchy, through the explosion layout | **Good.** The strongest channel carries the most structural information |
| Size | degree, square rooted | **Good.** A quantitative channel carrying a quantity |
| Hue | nothing, plus one focus point | **Wasted.** In a typed graph this is the standard type channel and it is empty |
| Lightness | depth tier | Reasonable, but the tier is derived from degree, so it restates size |
| Shape | five types | **Overloaded.** A weak channel, three types in the data, absent in the plane, gone at distance |
| Material | nothing. Glass is materiality, not data | **Wasted**, and not even rendered by default |
| Line width | nothing, pinned to zero | **Wasted** |
| Line density | two bits. The weight branch is dead | Underused |

**Three channels empty and one overloaded.** That is the structural cause of "I cannot tell them
apart", and it survives any amount of contrast repair.

## 5. The proposals

Three principles first.

1. **Materiality that carries no data is removed**, since there is no accuracy benefit and there is
   an opportunity cost.
2. **Type moves to a stronger channel, and shape stays as redundant encoding**, for color vision
   deficiency and for print.
3. **Do not thin the lines, reduce their number.** The literature's remedies are bundling, filtering
   and highlighting. The edge legend filter is already in that position.

| # | Proposal | Content |
|---|---|---|
| M1 | **Retire glass, in the graph only** | Unify the node material as flat with an outline. Dark already is, so matching the light theme collapses two material systems into one. **The floating chrome glass of `adr-design-037` stays exactly as it is:** there, materiality is the purpose |
| M2 | **Type maps to color, five closed hues, with shape retained** | Deterministic, so a shared pack still reads the same, and readable without color because shape remains. Monochrome is **redefined as a ban on arbitrary coloring** rather than a ban on color, and the single focus color rule survives by moving focus onto lightness and glow |
| M2 prime | (conservative alternative) **Type maps to fill against outline** | Concept filled, record outline only, bridge doubly outlined. A binary channel, so it survives zoom, and monochrome is untouched |
| M3 | **Restore line width** | Use the addon line type with screen-space width, so direction, symmetry and cross-domain status are separated by width as well. The plane already has a width ladder |
| M4 | **Clear the dead channels** | Either populate the weight field and revive the density ramp or delete the branch. Same for the unreferenced mount function and the domain ring |
| M5 | **Answer tangling with interaction, not channels** | Of the literature's remedies, two already exist here: the legend filter, and the local graph at the innermost scope. **Promote the local graph to the default entry**, which was the one view reported to remain useful at any scale |

### 5.1 Why color was recommended, and the honest objections

The recommendation was M2. The argument: the only structural advantage over the comparable tool is
type, so not showing it contradicts the product's own logic; and the convention in typed graph tools
is color.

| Objection | Answer offered |
|---|---|
| Monochrome is the brand | Five closed hues can be selected from inside the brand palette. That is not arbitrary coloring |
| Color vision deficiency | Redundant encoding, shape retained plus labels, which is the standard remedy. A separation check can be added to the gate |
| A shared pack would look different | Only if the **user** picks colors. Colors fixed to types make packs read **more** consistently, not less |

### 5.2 What not to do

- **Do not open a color wheel by domain.** Domains grow to twenty, and that is not coloring, it is a
  rainbow. Position already expresses domain.
- **Do not remove 3D.** Out of scope, and it has its own justification in survey and spatial memory.
- **Do not strip glass from the chrome.** `adr-design-037` was never about the content plane. It
  applies to floating chrome, where materiality is the point rather than a competitor to a data
  encoding.

That last line is the one most often misread afterwards. This study proposes retiring glass **from
the map**, and explicitly preserves it on chrome. See [[vinc.spec.glass-language]].

## 6. What became of the proposals

The candidates raised here were adjudicated the same day.

| Proposal | Outcome |
|---|---|
| M1 and M4 | Adopted as `adr-design-077`, and shipped in v0.7.3 on 2026-08-03. The dark screen did not change by a pixel, which is the measurement's whole point: the cost was real and the benefit was zero. The dead density ramp was rewired onto relation confidence, which the backend does carry |
| M2 | **Rejected**, in favor of M2 prime, on a ground this document could not have known: only three node kinds exist in the live data, and three separate on shape and fill alone. A reopening threshold was written so the question returns by itself if that changes |
| M3 | Adopted as `adr-design-079` and **not yet built**. Its prerequisite was rewired: it depends on the glass retirement, not on the color decision, because width is a channel independent of hue |
| M5 | Adopted as `adr-design-080`, **not yet built**. Judged in the verdict to be the highest-impact item of the four |

The verdict, the competitive comparison behind it, and the reopening threshold are in
[[vinc.research.atlas-mark-verdict]].

## 7. Labels used here that have no identifier

| Label | What it names |
|---|---|
| D20 | The monochrome discipline |
| D33 | Edge weight carried by ink density rather than line width |
| D39 | Palette inversion for the dark theme |
| D40 | The glass node material, retired by `adr-design-077` |

They are described by effect and given no `adr-` identifier deliberately. See
[[vinc.canon.decisions]].

## Provenance

| Source | Relation |
|---|---|
| Owner question, 2026-08-03 | Whether the mark language suits a data view |
| `app/src/surfaces/atlas3d.ts`, `atlas2d.ts`, `atlas.ts`, `api/query.py` | The section 2 readings, self-reported and unverified at the time |
| Store measurement, 2026-08-03 | The live type distribution |
| External survey, 2026-08-03 | The critique of an untyped graph view, personal knowledge graph practitioner accounts, ontology visualization and graph platform styling guidance, the decoration and minimal-ink experiments, graph complexity surveys, and renderer documentation on line width |
| [[vinc.research.atlas-mark-verdict]] | The adjudication of every proposal here |
| [[vinc.research.atlas-visibility]] | The contrast work this study questions the premise of |
| [[vinc.spec.glass-language]] | Where the chrome and content boundary is specified as built |
| [[vinc.spec.atlas-visual-language]] | Where the surviving mark language is specified as built |
| [[vinc.canon.decisions]] | Registry for the `adr-` identifiers used above |
