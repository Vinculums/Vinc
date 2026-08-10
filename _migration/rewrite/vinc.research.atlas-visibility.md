---
doc_id: vinc.research.atlas-visibility
layer: research
domain: vinc/design
authority: research
title: "Why the map went dark: a visibility diagnosis"
supersedes: vinc.d.atlas-visibility-research
updated: 2026-08-07
inherits_from: [vinc.spec.atlas-visual-language, vinc.research.graph-structure]
tags: [research, design, atlas, contrast, dark-theme, accessibility, legibility]
---

# Why the map went dark: a visibility diagnosis

> Research input, dated 2026-08-02. The owner reported that zooming out turned the map uniformly
> dark, that background, nodes, hover and text were all hard to see, and that nodes could not be told
> apart. The instruction attached to it was to change the design as far as necessary to make it
> visible, without overdoing it.
>
> **This document separates two kinds of claim and the distinction matters.** The contrast figures
> are computed from color values and are reproducible by anyone with the same values. The code
> anchors were self-reported by the session that wrote it and were **not** verified against the
> repository, a limit the follow-up in [[vinc.research.atlas-visibility-verification]] states again
> and does not resolve. Section 7 records which claims were later confirmed by what happened.

## 1. The finding, in one line

The complaint was not about taste. **It was a measurable contrast shortfall.** In the default dark
theme every mark except focus and the nearest tier fell below the non-text contrast threshold, and
zooming out then applied a further blend toward the background on top of that.

## 2. Five causes

Four symptoms, five causes, each located in code.

| Symptom | Cause |
|---|---|
| Zooming out darkens everything in the dark theme, and whitens it in the light one | **The fog range does not adapt to the camera.** Fog is fixed to the scene's size, its color is the background color, and the default framing already sits at the near edge of the fog band. Zooming out therefore moves every node into the band and fades it to the background |
| Nodes are not visible | **The tier fill contrast has collapsed.** The far tier's fill is the same token as the card background, so it sits at 1.15 against the page. The middle tier reaches 1.98. Tier assignment falls back to degree, so on a real graph most nodes are far, and the first screen is mostly invisible by default |
| Lines are not visible | The planar renderer blends the line color toward the background, which lands between 1.20 and 1.26. In 3D the cross-domain line is 1.98 and the receded line is 1.37 |
| Hovering makes everything else disappear | **The 3D recede is a numeric literal at 0.15 of base**, where the canon is 0.40 for nodes. Effective contrast for a non-neighbor lands near 1.1. This breaks two rules at once: the canonical value, and the ban on numeric literals for it |
| Nodes cannot be told apart | Three things at once. The dark glow path uses an unlit flat material, so the silhouettes of a sphere, an octahedron and a hexagonal prism converge at small sizes. **The planar view has no shape channel at all**, since no custom node program is registered, so everything is a circle while the legend teaches four shapes. And labels vanish when zoomed out, because sprites are world-scaled and only a fixed few hubs plus the hovered node are labelled |

The third of those is worth naming precisely: **the screen contradicts its own legend.** The same
project rule that forbids teaching a line grammar that is not drawn was being broken in the shape
channel.

## 3. The contrast measurements

Computed 2026-08-02, by the standard relative-luminance ratio, against the dark theme background.
The threshold applied is 3:1, the non-text minimum. These values are self-contained: they follow
from the color values alone, and nothing in the code has to be trusted to reproduce them.

| Element | Ratio | Against 3:1 |
|---|---|---|
| Far tier fill | 1.15 | fails, effectively background |
| Middle tier fill | 1.98 | fails |
| Near tier | 6.96 | passes |
| Focus | 10.79 | passes |
| Far stroke, also the muted text color | 4.05 | passes, but the ring is a small fraction of the radius |
| Body ink | 15.80 | passes |
| Planar undirected edge, effective | 1.20 | fails |
| Planar cross-domain edge, effective | 1.23 | fails |
| 3D cross-domain link | 1.98 | fails |
| 3D receded link | 1.37 | intended recede, but with no floor |
| Hover-receded node, effective | 1.15 | fails. At the canonical 0.40 it would have been near 2.8 |
| Light theme receded link | 1.20 | the same shape of problem, mirrored |
| Light theme ring color | 2.49 | fails, which matters on any surface using the ring as a signal |

The last row is the one that reaches outside this document: the map's focus in the light theme uses
a different color, so the map is unaffected, but other surfaces that use the ring color as a signal
need their own check.

## 4. What the external reading contributed

Surveyed 2026-08-02.

- **The non-text contrast criterion.** Graphic objects needed to understand content require 3:1
  against their adjacent background, and so do focus indicators. A dark theme must have every value
  recomputed rather than inherited from a light one. Vinc's text is excellent and its **marks fail
  systematically**, which is an unusual and diagnostic combination.
- **Dark-theme data visualization convention.** Avoid pure black and pure white, which this product
  already does. Build a two-step hierarchy of primary and secondary information. The failure here is
  not a violation of that convention: **it is that the secondary tier was pushed below the floor.**
  A hierarchy can be built entirely above 3:1.
- **Perception research on retinal variables.** Size and lightness are reliable pre-attentive
  channels; **shape is a step weaker.** Two consequences: a mark that is a few pixels across cannot
  carry type on shape alone, so shape is a near-distance channel and distance should be served by
  size and lightness; and the number of shapes should stay at three or four, never grow.
- **Tool precedent.** World-scaled labels pixelating on zoom-out is a known and long-complained-about
  failure in comparable graph views. The planar renderer's own library scales node and edge size by
  the square root of the zoom ratio to hold an apparent size across the zoom range, so **the
  zoom-adaptive answer already exists in one of the two renderers** and is missing from the other.
  Fog is documented everywhere as an atmospheric device, not a legibility device; using it for
  legibility requires tying it to the camera.

The direction all of that supports is narrow: **the shapes are not the problem.** The floor, the
zoom adaptation, and the division of labor between channels are.

## 5. The eight proposals

The closed layer stays closed: shape maps to type, one focus color, monochrome. What changes is the
scale, the adaptation, and compliance. "Without overdoing it" was given an operational definition:
**do not increase the total ink.** No glow, no new colors. Fix the floors and the scaling of the
channels that already exist.

| # | Proposal | Character |
|---|---|---|
| R1 | **Tie fog to the camera.** Set the near and far planes from the camera's distance to the scene center, so the proportion of the scene that is sharp stays constant across zoom. The alternative considered was removing fog and carrying depth on tier lightness alone | Close to a bug fix |
| R2 | **Give the tier fill a floor**, or better, **promote the stroke to a permanent outline** with a screen-space minimum thickness. The outline is recommended: it preserves the impression of depth while satisfying the criterion through edge contrast | A decision, since the palette is closed |
| R3 | **Move the edge ladder above the floor.** Directed edges at the highest step, undirected below, cross-domain at the bottom of the visible range with dashes alongside. The receded step may fall below 3:1 but must have a stated floor. Replace the blend toward background with one that **stops when it would break the floor** | A revision of the density ladder |
| R4 | **Obey the canonical recede.** Replace the 3D literal with the token. Effective contrast for a non-neighbor goes from about 1.15 to about 2.8 | Not a decision. Repair of a rule violation |
| R5 | **Screen-space labels with a halo.** Inverse-scale the sprite by camera distance, bake an outline in the background color so a label reads over any mark, and set the number of permanent labels from a **viewport density budget** rather than a fixed count | Extension of the open layer |
| R6 | **Two levels of detail on zoom.** Near, the current shapes. Far, once a mark is smaller than a few pixels, demote it to a point carrying size and lightness and label the branch instead. The explosion layout already places branch structure in the coordinates, so a branch label can be derived deterministically from the coordinate mean | New decision |
| R7 | **Restore one step of shading in the dark theme**, by a baked gradient or a low-intensity lit material, so a silhouette has interior modeling and shape discrimination survives at close range. Total ink unchanged | Reinforcement |
| R8 | **Three shapes in the planar view**, through custom node programs. It resolves the screen contradicting its legend | Reinforcement |

Recommended order: R4, then R1, then R3, then R2, then the rest. **R1 and R4 alone remove both
disappearances**, the one on zoom and the one on hover; R2 and R3 then establish the floor; R5
through R8 are improvements in discrimination.

## 6. The gates proposed

The point of these is that the table in section 3 stops being a document and becomes a test.

1. **A contrast contract test** in the design token generator: every mark fill, stroke and ladder
   step above the floor in each theme, with the receded step above its own stated floor. Changing a
   color re-runs the arithmetic.
2. **A single-source test for the recede values**: the three values come from one place, and there
   are zero numeric literals for them in view code.
3. **Screenshot instrumentation**: both themes at the default framing and two zoom-out steps, with a
   histogram of mark pixels against background contrast.
4. **Regression on the existing discipline**: monochrome intact, one focus color, layout determinism
   unchanged.

Gate two shipped. `app/src/recede.ts` is the single read point, and it exists specifically because
both WebGL renderers are outside the stylesheet and would otherwise each decide for themselves,
which is exactly how the divergence in R4 happened.

## 7. What happened to these proposals

| Proposal | Outcome |
|---|---|
| R4 | **Done, and the premise was confirmed.** The single read seam records the violation it was built to end |
| R1, R3, R2 | Became `adr-design-075` and `adr-design-076`. The follow-up study replaced the specific numbers in R1 and R3 before implementation, see below |
| R5 | Became `adr-design-082`, with the outline reworked into a halo |
| R6, R7, R8 | Became `adr-design-083` |
| The fog coefficients, the receded-edge floor, the six-pixel threshold, the label outline | **All four replaced.** [[vinc.research.atlas-visibility-verification]] found them to be numbers without sources and substituted sourced ones |
| The premise of the shape diagnosis | Weakened later. [[vinc.research.atlas-mark-verdict]] measured the live data and found only three node kinds present, so the difficulty of carrying five types on shape is a smaller problem than stated here |

One proposal in this document reads differently in hindsight than it did at the time. R2 recommends
promoting the **outline** over raising the fill, on the grounds that it preserves depth and adds no
ink. The follow-up study, working from a second contrast measure, arrived at the same recommendation
by an independent route and strengthened it. Two methods agreeing on the mechanism while disagreeing
on the numbers is the most useful outcome this pair of documents produced.

## 8. Labels used here that have no identifier

| Label | What it names |
|---|---|
| D20 | The monochrome discipline |
| D33 | Edge weight carried by ink density rather than line width |
| D39 | Palette inversion for the dark theme |

The recede values are cited under a revision label attached to `adr-design-032`, which likewise
never became an identifier of its own. All four are described here by effect and given no `adr-`
identifier deliberately. See [[vinc.canon.decisions]].

## Provenance

| Source | Relation |
|---|---|
| Owner observation, 2026-08-02 | The four symptoms |
| `app/src/surfaces/atlas3d.ts`, `atlas2d.ts`, `atlas.ts`, `theme.ts` | The code anchors, self-reported and unverified at the time of writing |
| Contrast arithmetic, 2026-08-02 | Section 3, reproducible from the color values alone |
| External survey, 2026-08-02 | The non-text contrast criterion, dark-theme data visualization practice, retinal variable research, and renderer documentation on zoom-adaptive sizing and fog |
| [[vinc.research.atlas-visibility-verification]] | The follow-up that replaced four of these numbers |
| [[vinc.research.atlas-mark]] | The study that questioned the closed layer this one held constant |
| [[vinc.spec.atlas-visual-language]] | Where the recede values and the edge treatment are specified as built |
| [[vinc.canon.decisions]] | Registry for the `adr-` identifiers used above |
