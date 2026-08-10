---
doc_id: vinc.research.sankey-alluvial
layer: research
domain: vinc/design
authority: research
title: Alluvial layout for the totals view, and the crossing result that changed the objective
supersedes: vinc.d.alluvial-research
updated: 2026-08-07
inherits_from: [vinc.spec.atlas-views, vinc.spec.atlas-visual-language, vinc.research.view-redesign]
tags: [research, design, atlas, sankey, alluvial, layout, crossing-minimization]
---

# Alluvial layout for the totals view, and the crossing result that changed the objective

> Research input, dated 2026-07-28. An external report commissioned to answer why the totals view
> did not look like an alluvial diagram, plus an as-built comparison written alongside the
> implementation that followed the same day.
>
> **The report's central expectation was partially refuted by the implementation**, and that
> refutation is the most valuable thing in this document. Section 6 states it as a small theorem.
> `adr-design-068` is the decision it produced; the resulting behavior is specified in
> [[vinc.spec.atlas-views]].

## 1. What the report established about the form

The distinction between the two chart families is not stylistic, and the vocabulary matters for the
rest of the argument.

| | Origin | Structure |
|---|---|---|
| Sankey | An 1898 paper on the thermal efficiency of steam engines. The original was **black and white with a single kind of flow** | A flow network of nodes and links. Flows may merge or split at any stage, cycles are allowed, node placement is relatively free. It emphasizes direction and quantity |
| Alluvial | Popularized by a 2010 paper mapping change in large networks, which studied citation patterns among roughly seven thousand journals over a decade and found a subfield maturing into an independent discipline | Axes are fixed. On each axis, categories are blocks whose height is proportional to size. Flow runs one way between adjacent axes only, with no cycles |

The specialized vocabulary that goes with alluvial plots is worth adopting because it names the thing
this view already had: an **axis** is a categorical column, a **stratum** is one category's block, an
**alluvium** is a single flow that preserves its identity across every axis, a **lode** is the
subdivision of a stratum by the alluvia passing through it, and a **flow** is one segment between
adjacent strata.

**The path-preserving ribbon this product had already built is exactly an alluvium.** A related
family, parallel sets, differs in showing frequencies rather than identities and in stacking each
axis to a uniform whole. Under a widely used classification, an alluvial plot is a parallel sets plot
whose category order is consistent across axes and whose stacking has no gaps.

**So the view was already alluvial by definition:** three fixed axes, categorical regrouping, no
cycles, identity-preserving flows. The problem was never the definition. It was layout, ordering and
visual design.

## 2. The diagnosis

The report's checklist for why an alluvial plot reads as tangled curves rather than a river:

1. **Lode order is not locally optimized.** The sort keys at both gaps were globally fixed. The
   correct approach reorders lodes at each axis with respect to the adjacent one. A global key can
   make one gap clean only by forcing crossings in the other.
2. **No crossing minimization** was run at all.
3. **Every tuple is drawn as its own ribbon**, with no merging of flows sharing an endpoint pair, so
   band count explodes.
4. **Small flows are unhandled**, producing many hair-thin slivers.
5. **The strata are visually weak.** An alluvial plot needs distinct blocks on each axis or it reads
   as a bundle of curves. The original 2010 figures were themselves criticized for this.
6. **Steep ribbons produce zigzag noise.** Gentle curves increase the sense of continuity.

## 3. Ordering and layout

- **Barycenter sweep.** Place each node at the mean position of the neighbors it connects to in the
  adjacent layer, then sweep in the other direction, repeating until convergence or a cutoff.
  Crossing minimization is NP-hard, and this is its standard approximation, notably good on sparse
  graphs. A median variant performs comparably.
- **Lode guidance.** In an alluvial toolkit, a guidance function sets the priority with which the
  other axes contribute to ordering at an index axis: zigzag outward from the index, forward or
  backward, or out and back. With only three axes, holding the middle one as the index and trying
  forward or out-and-back was suggested.
- **Stratum order.** The 2010 original ordered clusters bottom to top by size. Semantic order is
  equally valid. **Whichever is chosen must be applied consistently across all axes.**
- **Curve type.** Cubic, quintic and sigmoid curves have zero slope at both ends, so flows enter a
  stratum perpendicular and appear to pour out of the block. This is the same principle as the
  standard cubic link generator in charting libraries.
- **Band straightening.** A steeply sloped ribbon becomes thinner and longer, which increases
  overlap, so **an ordering that lowers mean slope reduces clutter directly.**
- **Clutter metrics.** A 2021 paper defines eight measures of clutter for this chart family. Five
  depend on category order: overlap area, mean slope, orthogonality, number of crossings, and
  crossing angle, since ribbons crossing at a shallow angle are hard to follow. Three depend on the
  order of the dimensions themselves: ribbon count, ribbon width variance, since very broad ribbons
  hide smaller ones, and mutual information between adjacent axes, which argues for placing related
  dimensions next to each other. Practical guidance from the same paper: with few ribbons optimize
  overlap and slope, with many optimize orthogonality, crossings and crossing angle.

## 4. Monochrome under the constraint

With color unavailable as a category channel:

- **A lightness ramp** substitutes for categorical color, and suits ordered categories particularly
  well since lightness implies order. Its known limit is that similar lightness values are hard to
  separate when adjacent.
- **Hover focus and context**, keeping the selected element and receding the rest, is the standard
  compensation for static clutter. **This product had already built end-to-end path highlighting**,
  which is exactly that.
- **An others threshold.** The practitioner rule is blunt: if forty of fifty bands are hair-thin
  slivers they are visual noise, so filter or merge below a meaningful threshold. Related rules of
  thumb put readable Sankeys at around fifteen nodes and four or five levels. For this view the
  recommendation was an absolute minimum band thickness of two to three pixels, with tuples below it
  merged into an others lode at the middle axis only.
- **Strong strata, padding between them, and direct labels carrying values**, since a label with its
  number reduces the reader's work.

## 5. What was already built when the report arrived

Half of the report's recommendations described the existing implementation.

| Recommendation | State at filing |
|---|---|
| Cubic curve, flat at both ends | Already so. The band generator used a cubic curve with horizontal tangents at both ends |
| Node padding of six to ten pixels | Exceeded |
| Hover recede between 0.1 and 0.2 | Already so, at the canonical line value |
| Lightness ramp | Already so, interpreted as value-proportional opacity |
| End-to-end focus and context | Already so |
| Direct stratum labels with values | Already so |
| Per-gap lode reordering | **Missing.** Built as part one of the decision |
| Minimum band thickness | Only a one-pixel clamp. **Built as part two** |
| Others bucket | **Missing.** Built as part three, in a modified form |
| Per-gap aggregation with end-to-end hover on demotion | A fallback existed but neither automatic nor hover-joined. **Built as part four** |

## 6. The crossing invariance result

The report's immediate recommendation was that a barycenter sweep at each gap would remove most
crossings. **Under this product's constraints it does not, and the reason is structural rather than
implementational.** The constraints are that stratum order is fixed and that a topic node's incoming
order must equal its outgoing order, which is what band alignment means.

1. **Crossings between strands in different strata are fixed by stratum order alone.** No amount of
   lode reordering removes them.
2. **Among pairs sharing a stratum, the previous lexicographic arrangement had already driven the
   left gap to zero crossings**, because its sort key happened to agree with the opposite side's
   stratum order.
3. What remains are **conflicting pairs** inside one topic, where the source order and the state
   order disagree. Under any common band order, such a pair crosses exactly once, in the left gap or
   the right. **The total number of crossings is therefore an invariant of the arrangement.**

So the previous arrangement was already at the minimum. Its only fault was that it piled every
conflict into the right gap, which is precisely why the right half of the screen looked tangled while
the left looked clean.

**This changes the objective rather than defeating it.** What reordering actually delivers is the
distribution of crossings across both gaps, and a reduction in mean slope and wander, which is the
same direction as the report's own band-straightening argument and its slope metric. So the
implementation's target metric became **wander**, not crossing count.

The one lever that would genuinely reduce crossings is a barycenter reordering of the topic strata
themselves. That breaks the size-ordered convention the report also endorses, and it was rejected by
the owner on 2026-07-28, with a live visual check left as the condition for revisiting.

## 7. The metric correction, found by measuring

The first implementation defined wander as the sum over strands of count times the absolute vertical
displacement at each gap. **Measurement showed the number not moving at all** even when the sweep
demonstrably changed band order, so the keep-best rule saw no improvement and returned the initial
ordering.

The cause: when a band's center lies between its two endpoints, the two absolute displacements sum
to the distance between the endpoints, which is **independent of where the band sits**. Typical
arrangements all fall inside that flat region.

Switching to squared displacement makes the midpoint, which is the barycenter position, the unique
minimum, so improvement becomes visible. It also penalizes slope variance, which matches perception:
two moderate slopes read better than one flat and one steep. Hand check on one swap: zero under the
first form, a clear improvement under the second.

**The general lesson is worth more than the fix.** An objective function that is flat over the region
you actually operate in will report that nothing is happening while everything is, and a keep-best
rule then dutifully preserves the worse arrangement.

## 8. Measurements, 2026-07-28

Synthetic data run outside the browser, plus a harness inside it.

| Case | Result |
|---|---|
| Thirteen tuples, three sources by four topics by three states | Crossings **41 before and 41 after**, redistributed from 16 and 25 across the two gaps to 18 and 23. Wander improved 8.6 percent. This is the direct demonstration of section 6 |
| Sixty-eight synthetic tuples, three by eight by three | Crossings **124 before and 124 after**, redistributed. Wander improved slightly |
| Browser harness, twenty-seven checks, all passing | Column sums preserved to within a fifth of a pixel. **Band alignment error of exactly zero** across thirteen tuple pairs, which is the structural guarantee showing up as a measurement. Two mounts of the same data producing identical markup, which is determinism. Minimum thickness comfortably above the floor. End-to-end hover across both modes, non-decreasing flow ordering at concept grain, and in the aggregated mode a gap key on every ribbon, joined hover, a demotion caption, and a non-clickable others stratum |

## 9. What was changed or rejected from the report

| Report | What was built | Why |
|---|---|---|
| Others as a partial merge, grouping only at the middle axis | **Whole-topic merge** | Partial merging leaves the remaining topics' node values disagreeing with the ledger. Topic integrity outranks identity preservation here |
| Demote when the mean band falls below two pixels | Demote on **strand count**, or when required height exceeds an absolute cap | Folding the minimum thickness into the height makes the mean structurally at least two pixels, so the criterion contradicts itself. And a pixel criterion makes the mode flip on window resize. Replaced by a criterion over the data alone |
| A and B testing the guidance functions | A single barycenter sweep | With three axes and few tuples, barycenter reaches the same effect deterministically. **And with crossings invariant, the test has no discriminating power** |
| Brute-force optimum search | Rejected | Same reason. The objective moved to wander, which barycenter with keep-best already minimizes well enough |
| A lightness ramp for the ordered state axis | Rejected, current treatment kept | Value-proportional opacity already occupies the density channel. A categorical ramp on top would put two meanings in one channel, which the closed-layer rule forbids |

## 10. The report's own caveats, preserved

The report qualified its own strongest numbers, and preserving that is part of what makes it usable.

- The headline clutter reduction figure is a **maximum for one metric on one dataset**, not a mean.
  Some metrics showed no improvement at all on already-optimized originals, and clutter was defined
  operationally as overlap area.
- **The metrics are descriptive.** They diagnose and do not supply a reordering algorithm; the optima
  in the paper were found by brute force, which does not scale. Several evaluation datasets were
  reconstructed by hand from published figures.
- Barycenter is a heuristic on an NP-hard problem and guarantees no global optimum.
- The names in this chart family have been **conflated in the literature for decades**, which is why
  the report adopted the working definition of fixed axes, preserved identity and no cycles.
- Lightness encoding struggles between similar values, so the number of categories per axis should
  stay small or hover must compensate.
- The readability rules of thumb come from vendor and practitioner writing, not from user studies.

## 11. Open question

**Whether the topic strata may be reordered by barycenter.** It is the only lever that actually
reduces crossings, and it costs the size-ordered convention that readers rely on. **Rejected by the
owner on 2026-07-28**, with revisiting permitted after a visual check against live data.

## Provenance

| Source | Relation |
|---|---|
| Commissioned external report, 2026-07-28 | Sections 1 to 4 and 10, preserved as the input they are |
| `app/src/surfaces/atlas-planar.ts`, `atlas-lodes.ts` | The as-built comparison of section 5 and the implementation the corrections came from |
| Measurement run, 2026-07-28 | Section 8, synthetic data outside the browser plus a harness inside it |
| [[vinc.spec.atlas-views]] | Where the totals view and the invariance result are specified as built |
| [[vinc.research.view-redesign]] | The study that restored the three columns this one then arranged |
| [[vinc.research.motion]] | The entrance choreography for this view, from a parallel study |
| [[vinc.canon.decisions]] | Registry for the `adr-` identifiers used above |
