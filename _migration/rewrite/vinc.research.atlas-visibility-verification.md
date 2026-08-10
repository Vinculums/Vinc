---
doc_id: vinc.research.atlas-visibility-verification
layer: research
domain: vinc/design
authority: research
title: "The visibility diagnosis verified: two contrast measures and four numbers replaced"
supersedes: vinc.d.atlas-visibility-verification
updated: 2026-08-07
inherits_from: [vinc.research.atlas-visibility, vinc.spec.atlas-visual-language]
tags: [research, design, atlas, contrast, apca, accessibility, verification]
---

# The visibility diagnosis verified: two contrast measures and four numbers replaced

> Research input, dated 2026-08-02. A verification pass over [[vinc.research.atlas-visibility]],
> which it does not replace: the diagnosis there stands, and this document layers a second
> measurement standard, four replacement numbers, and four additional proposals on top of it.
>
> **What it could and could not check, stated first.** The full text of the original was read
> directly, so its color values and contrast claims are quoted from the source rather than
> paraphrased. The verification arm could reach neither the repository nor the store, so **the code
> anchors are once again unverified**: the fog constants, the recede literal, the ring thickness,
> the label threshold, and the renderer capability claims all remain self-reported. Values derived
> from color are self-contained and were independently recomputed. One arithmetic claim was checkable
> and checked: the default camera position gives a distance to the origin of about 2.54 scene units
> against a fog onset at 2.6, so the original's claim that the default framing sits **just before**
> the fog band is exactly right.

## 1. What this pass overturned

The diagnosis survived. The measurement standard and four arbitrary numbers did not.

| # | Verdict | Finding |
|---|---|---|
| V1 | Confirmed | All thirteen contrast figures in the original agree with an independent recomputation, within rounding |
| V2 | **Overturned** | The problem is the threshold, not the values. The ratio-based standard **overestimates contrast at low luminance in a dark theme**. Under a perceptual measure, even the one channel the original treated as the sole passing case falls short of the minimum for a solid mark |
| V3 | **Overturned** | The non-text criterion does not require 3:1 of every mark. It applies to graphics needed to understand the content, and inactive elements are exempt. Raising every distant node is not a normative requirement, and it conflicts with the rule against adding ink |
| V4 | **No source** | The floor proposed for the receded channel has no basis in any literature. The sourced replacement is the perceptual measure's invisibility floor |
| V5 | **No source** | The fog coefficients are invented. The principle of tying fog to camera distance is right; the near coefficient as proposed would put the target itself inside the fog |
| V6 | Partly sourced | The shape demotion threshold is an engineering value with a safety margin rather than an experimental constant. The literature's floor is roughly half of it |
| V7 | Partly sourced | The label treatment is the right thickness but the wrong construction: it specifies an **outline**, where the cartographic preference is a **halo** |
| V8 | New | The largest candidate the original missed: **render pipeline conformance**, meaning color space and tone mapping. If the discrepancy originates there, retuning the palette is wasted work |

## 2. The dual measurement table

Against the dark theme background. The ratio column is the standard the project gates on. The
perceptual column is a hand computation of the lightness contrast measure, checked against its
published reference pair, with an error of roughly a third of a unit. Signs are dropped and
magnitudes shown, since a dark background with a light foreground yields a negative polarity.

| Color role | Ratio | Perceptual | Against 15 | Against 30 | Against 45 |
|---|---|---|---|---|---|
| Far tier fill | 1.15 | about 0 | fails | fails | fails |
| Middle tier fill, and the 3D cross-domain link | 1.98 | 9.6 | fails | fails | fails |
| Near tier, and directed edges | 6.96 | 47.9 | passes | passes | passes |
| Focus | 10.79 | 69.0 | passes | passes | passes |
| Far stroke, and muted text | 4.05 | **28.2** | passes | **fails** | fails |
| Body ink | 15.80 | 92.5 | passes | passes | passes |
| Planar undirected edge, effective | 1.20 | about 0 | fails | fails | fails |
| Planar cross-domain edge, effective | 1.23 | about 2 | fails | fails | fails |
| 3D receded link | 1.37 | about 0 | fails | fails | fails |
| Hover-receded node, effective | 1.15 | about 0 | fails | fails | fails |
| Light theme receded link | 1.20 | 9.9 | fails | fails | fails |
| Proposed cross-domain replacement | 2.96 | **19.2** | borderline | **fails** | fails |
| Proposed far tier replacement | 3.35 | **22.6** | passes | **fails** | fails |

The three thresholds are: an invisibility floor, a minimum for solid or large non-text marks, and a
minimum for fine icons and thin outlines.

**The disagreement is concentrated and consequential.** The three colors nearest the ratio threshold
all fail the perceptual minimum for a solid mark, and two of those three are the **replacement
colors the original proposed**. In other words, the original's retuning target was not enough. The
near tier, by contrast, is robust under both.

## 3. Which standard governs

The ratio formula considers luminance alone: it ignores font size, weight and polarity, and it
overestimates contrast when one side is near black. That limitation is acknowledged by the standard
body itself, and the perceptual measure's own documentation states that a dark theme cannot be
designed against the ratio standard reliably.

Governance, however, is a separate question from accuracy. **The perceptual measure is not normative
as of 2026-08.** It was removed from a 2023-07 working draft of the successor guideline, whose
current text leaves the contrast algorithm undecided, and normative status is unlikely before the
next decade. Browser tooling support for it has been proposed for removal.

The recommendation, which costs nothing and breaks no invariant:

1. **The acceptance gate stays on the ratio standard.** It is the legal safe line and it is
   deterministic arithmetic, which suits a backend with no language model in it.
2. **Publish the perceptual value alongside it** in palette design and in regression tests, to catch
   the trap this pass found: passing the ratio and failing the perceptual minimum.
3. **In the dark low-luminance band, where the two disagree, follow the stricter one.** Solid fills
   and strokes in dark take the perceptual minimum, and prefer the higher one.
4. The perceptual constants can be inlined into the bundle, so this does not breach the content
   security policy.

## 4. How far the criterion actually reaches

The non-text criterion covers the parts of a graphic **required to understand the content**, with an
essential-content exception, and inactive components are explicitly exempt.

- **Requiring 3:1 of hundreds of marks is not a normative requirement.** In a knowledge graph the
  information is the pattern and the connectivity, not the color of an individual node. The minimum
  the criterion actually compels is (a) interactive targets, meaning hover, focus and selection, and
  (b) channels whose discrimination the task depends on. Raising every distant node is both excessive
  and in tension with the rule against adding ink.
- **Intentional recede channels are exempt.** A hovered non-neighbor and a faded edge are temporary
  de-emphasis and fall under the inactive-element logic. The original's floor for them is therefore
  an invented number solving a problem the criterion does not pose. The purpose of having a floor at
  all is to prevent total disappearance, and the sourced way to express that is the perceptual
  measure's invisibility floor, since a ratio expression of it is polarity-dependent.
- **The focus appearance criterion does apply** to a graph node's focus state. That is an
  emphasis direction rather than a recede, so it does not conflict with the ink rule and can be
  handled inside the single focus color.
- **Dark is not automatically the accessible choice.** Halation is reported at high polarity
  contrast, and astigmatism is common: a study of optical retail records covering 11,624 people found
  significant astigmatism in at least one eye in 47.4 percent of them. This product's near-black and
  near-white choices already mitigate it. **The frequently quoted 50 percent figure has no
  peer-reviewed source and should not be cited.**

## 5. How much a monochrome palette can encode

| Channel | Character | Reliable steps |
|---|---|---|
| Shape | nominal, selective, fragile under zoom | four or fewer |
| Lightness | ordered and quantitative | four to five in greyscale |
| Size | quantitative; poor as nominal | three to four |
| Texture and pattern | selective and associative, good as nominal | a usable channel on low-color displays |
| Orientation | nominal | about four |
| Dash pattern | nominal, edges | few |
| Outline present, fill present | binary | two each |

**The conclusion drawn was that five node kinds and six edge kinds cannot all be discriminated
permanently in monochrome**, and that the answer is to stop trying: carry a coarse classification on
the permanent screen and reveal detail on hover, selection or filter.

> **This conclusion was refuted the next day, on the node side.** [[vinc.research.atlas-mark-verdict]]
> measured the live store and found only three node kinds present, with bridges and entities at zero.
> Three are separable by shape and fill alone, so the overload this section computed does not exist
> in the data. The edge side was never measured and the claim there remains untested. The general
> point about shape being the weakest of these channels stands and is supported by the perception
> literature, including the fact that shape's own ranking among nominal channels was revised upward
> between the two standard rankings and is still argued about.

## 6. The four numbers replaced

### 6.1 The fog coefficients

The principle is correct: tying the fog planes to camera distance makes the sharp proportion of the
scene invariant under zoom. The proposed near coefficient is not, because it puts the target itself
inside the fog.

- Replacement: a near coefficient below one, so the region in front of the target stays sharp, and a
  far coefficient near two, so fading happens only at the outskirts. The exact values need visual
  tuning per spread setting.
- **Keep linear fog.** The exponential form is more physical but cannot provide a clear near band,
  and a guaranteed sharp band around the target is the requirement here.
- **Do not remove fog.** Atmospheric perspective is a genuine depth cue. Occlusion and motion
  parallax compensate only partly. Better to keep it weakly and let tier lightness carry depth in
  parallel.
- **The direct cause of disappearance is that the fog color equals the background color.** That is
  the standard practice the renderer's own manual recommends, but a fog color very slightly lighter
  than the background leaves distant nodes as silhouettes in haze. This is a floor adjustment to an
  existing channel, not an addition of ink.
- Recompute on control-change events. Every frame is unnecessary.

### 6.2 The shape demotion threshold

The cartographic standard for a printed point symbol is about a third of a millimeter at a half-meter
viewing distance, and digital experiments report discrimination between circle, square and triangle
collapsing below about six tenths of a millimeter, which is roughly two to four pixels at standard
density. **The proposed six pixels is therefore an engineering value with a safety margin, and it is
defensible as it stands.** The precise objection is the unit: to account for display density and
viewing distance, a threshold of this kind should be expressed in visual angle or physical size, not
in layout pixels.

### 6.3 The label treatment

Cartographic practice prefers a halo to an outline, because an outline intrudes on the letter strokes
while a halo sits behind them. Recommended thickness is one to two pixels in the background color,
which the original's number satisfies; its construction does not. The improvement is to draw a thick
background-colored stroke first and lay the ink fill over it, producing a halo rather than an outline.

Two additions:

- The same treatment applied where an edge passes behind a node reduces clutter.
- The count of permanent labels should come from a **viewport pixel budget with collision
  avoidance**, which is the proven pattern in map renderers, rather than a constant.
- Screen-space labels need **no new dependency**: a canvas texture with an inverse scale correction
  is sufficient.

### 6.4 The floor for receded channels

Replaced by the perceptual invisibility floor, for the reason in section 4.

Separately confirmed: adding shapes to the planar renderer is possible with **already-published
packages for that renderer plus a custom program**, so it does not breach the rule against new
libraries.

## 7. Four proposals the original did not have

### R9. Check the render pipeline before touching the palette

The renderer changed its output encoding property and made color management default-on in a version
the project is past. Its tone mapping default did **not** change and remains off, so tone mapping can
only be a contributor if it was explicitly enabled.

But if an older encoding assumption or an explicit tone mapping is present, **the computed contrast
and the on-screen contrast diverge**, and at low luminance that divergence is decisive. So the
sequence matters: verify the output color space, the tone mapping setting, color management, and that
palette values are interpreted consistently as sRGB. Anti-aliasing settings also affect whether a
sub-pixel stroke survives at all.

**This is a gate, not a project.** It is cheap, and if everything is normal it closes immediately and
work proceeds to the recede repair.

### R10. Give the user contrast and polarity control

A contrast strength control and a theme polarity choice, stored locally. Personalization is the
accepted answer in accessibility practice, and it is compatible with determinism and offline
operation.

### R11. Bundle and aggregate along the domain branches

The explosion layout already encodes hierarchy in the coordinates, so hierarchical edge bundling
maps onto it directly. **With a caveat:** literacy research reports that general readers interpret
super-nodes and bundled edges poorly, while map-like representations perform well on both task and
recall. Bundling helps the overview and costs legibility, so a hover path back to the unbundled form
is mandatory rather than optional.

### R12. Add the perceptual measure to the regression gate

Make the contrast contract test dual, so that "passes the ratio, fails the perceptual minimum" is
blocked automatically instead of being rediscovered by a person.

### A note on keeping 3D

There is no strong evidence that a 3D node-link view is better for surveying a knowledge graph, and
occlusion and depth ambiguity are real costs. 3D is justified when exploratory immersion and spatial
memory are stated product intents; for pure reading efficiency a plane is safer. Switching to a
planar or map-like representation at far zoom is worth considering on those grounds alone.

## 8. Reordered

**Render pipeline, then recede repair, then fog, then edges, then nodes, then labels, then shading,
then planar shapes, then level of detail.**

This mostly endorses the original order and inserts one gate at the top. If the color space or tone
mapping is misconfigured, every palette change made before checking is wasted.

The reconstituted decision candidates each carry a stop criterion, which is the part worth copying
into future decisions:

| Candidate | Content | Stop criterion |
|---|---|---|
| `adr-design-081` | The render pipeline audit | If the pipeline is normal and computed contrast matches the screen, close it and move on |
| `adr-design-075` | Recede conformance plus camera-adaptive fog, with replaced coefficients | If darkening on zoom-out persists after the fog change, fog was not the cause. Return to the pipeline audit and the node work |
| `adr-design-076` | Outline promotion, the edge ladder repositioned, and the dual gate | If total ink visibly increases, roll back the fill raise and keep only the outline |
| `adr-design-082` | Screen-space labels, halo, density budget, and user control | If the label budget exceeds the frame budget, lower the permanent label count |
| `adr-design-083` | Level of detail, minimum shading, planar shapes, and bundling | If bundled reading accuracy tests below the unbundled form, roll back only the bundling and keep the rest |

## 9. Limits, stated by the original

- **The code anchors are unverified.** They were unverified in the source document and remained so
  here. Confirm against the working tree before implementing.
- **The perceptual values are hand computed**, checked against the published reference pair, with an
  error near a third of a unit. No item sits close enough to a threshold for that error to flip a
  verdict, though the muted color is the closest.
- **The perceptual measure is not normative.** Using it alone as an acceptance criterion is a legal
  risk, which is why the recommendation keeps the gate on the ratio standard.
- **The 3D and bundling findings come from general graph experiments**, not from this product's data
  or its users.
- **Invariant check:** none of the twelve proposals breaks offline operation, determinism, the
  content security policy, the ban on new libraries, monochrome, the single focus color, the four
  states, the em dash ban, or shape as a closed type channel. The single point of tension is the
  fill raise against the total-ink rule, which is precisely why the outline was recommended instead.

## 10. What this document also did, unintentionally

It registered its decision candidates under a lettered sub-series while two of them **already
existed as integer-numbered decisions**. The duplication was discovered the following day by
[[vinc.research.atlas-mark-verdict]], which mapped the pairs but declined to assign numbers on the
grounds that numbering is a governance act rather than a research one.

The migration resolved it: the lettered duplicates were merged into the integers they duplicated,
and the three that had never had an integer received one. The resulting identifiers are used
throughout this document, and the anomaly is recorded in [[vinc.canon.decisions]].

## Provenance

| Source | Relation |
|---|---|
| [[vinc.research.atlas-visibility]] | The document verified here, which it supplements rather than replaces |
| Contrast recomputation, 2026-08-02 | Section 2, both measures |
| External survey, 2026-08-02 | The non-text and focus appearance criteria, the perceptual contrast measure and its thresholds, its standing in the successor guideline, renderer documentation on fog and color space, cartographic minimum symbol size and halo practice, nominal channel rankings and pre-attentive research, edge bundling literature, and an astigmatism prevalence study |
| [[vinc.research.atlas-mark-verdict]] | Refutes section 5 on the node side, using live measurement |
| [[vinc.spec.atlas-visual-language]] | Where the surviving values are specified as built |
| [[vinc.canon.decisions]] | Registry for the `adr-` identifiers used above, and for the numbering anomaly |
