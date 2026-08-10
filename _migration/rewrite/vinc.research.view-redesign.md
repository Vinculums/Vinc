---
doc_id: vinc.research.view-redesign
layer: research
domain: vinc/design
authority: research
title: The planar view and the totals chart, reconsidered against a reference
supersedes: vinc.d.view-redesign-research
updated: 2026-08-07
inherits_from: [vinc.spec.atlas-views, vinc.spec.atlas-visual-language, vinc.research.motion, vinc.spec.glass-language]
tags: [research, design, atlas, planar, sankey, renderer, reference-analysis]
---

# The planar view and the totals chart, reconsidered against a reference

> Research input, dated 2026-07-25, answering two owner questions. First: the current planar view is
> just the 3D one flattened, so can it be redesigned into something structured like the reference
> film. Second: can the totals chart be built the way an external chart library documents it.
>
> **Both answers were yes, and for a reason neither question anticipated: both were specification
> compliance repairs rather than new features.** The specification already required a three-column
> chart and the implementation had shrunk to a stacked bar. Everything in stages one through four of
> section 9 shipped the same day. What is built now is in [[vinc.spec.atlas-views]] and
> [[vinc.spec.surface-atlas]].

## 1. The three-line answer

1. **The planar redesign is possible, and the diagnosis matches the code exactly.** The current
   planar view is not a design, it is a projection produced by one renderer flag. Worse, its vertical
   position is a residue of the 3D depth axis, so it encodes nothing. The reference's skeleton is
   transplantable; its color and glow are not.
2. **The totals redesign is possible, and it is a return rather than a departure.** What ships is a
   stacked bar, not a Sankey. The specification already required three columns and the design mockup
   had already passed the conservation check.
3. **The reference is a concept film, not a shipping product.** Structure and interaction grammar may
   be borrowed; **performance and data-scale claims may not.**

## 2. Why the reference is a concept film

Twenty-one seconds, 1600 by 1200, thirty frames per second. One frame per second extracted and read
in full.

Three pieces of evidence: a design studio's watermark appears partway through; the data repeats, with
the same list item appearing twelve times in one frame; and the headline metrics jump between frames
in a way no real dataset does, from single digits to sixty-something to nearly a hundred percent.

That determines what may be taken from it. **Everything about arrangement and interaction is fair.
Nothing about it is evidence that a given approach performs at scale.**

## 3. What the reference actually shows

| Screen | Content |
|---|---|
| A, the planar map | A vertical **year axis** down the left, spanning about eight years, with the active year as a pill and dotted tick ribbons. A central hub radiating outward, about ten topic nodes, a legend at lower left, four metric bars along the bottom, and a question input |
| B, single node focus | The rest of the graph cleared. One node with a large title, an equalizer ring around it, a gap-count badge, and a sentiment bar |
| C, the fan drilldown | Node, then five bezier lines fanning out to five category cards carrying counts, then a selected card re-fanning into twenty or more leaves, with a glass detail panel on the right |

Measured grammar: nodes are rendered spheres with five type colors and size for importance. There
are two edge kinds, a thick glowing beam with bloom for primary relations and a thin dotted line for
secondary. Badges carry percentages under labels and integer counts on satellites. During the fan,
light particles travel along the beziers, which is the same family of gesture as a link growing along
its own length.

**The year axis is probably decorative.** Node vertical positions do not reliably align with the tick
marks between frames. That single observation is what makes the recommendation in section 7 possible.

## 4. The structures already correspond

| Reference | This product | Assessment |
|---|---|---|
| Screen A, the planar map | The outer and topic scope levels | corresponds |
| Screen B, node focus | The node scope level | corresponds |
| Screen C, category fan into leaves | The detail panel and its drilldown | **only the presentation differs** |
| The left year axis | The timeline viewpoint | separated onto the viewpoint axis |
| The bottom metric bar | nothing | an opening |

**What the reference layers onto one screen, this product separates onto a viewpoint axis.** That
difference is the pivot of the whole judgment: features the reference gets by stacking are already
available here by switching.

## 5. The planar view as built: a flag, not a design

Traced through the 3D renderer module. The planar view is the 3D scene with one flag set: the depth
axis is dropped from position, rotation is locked while pan and zoom continue, fog is removed,
prismatic primitives are laid toward the camera and scaled down, and the camera is placed head on.

**And one line matters more than all the others: a fraction of the depth axis is mixed into the
vertical one.** So a node's height on the plane is not time, not hierarchy, and not importance. It is
a residue of depth, kept only so that nodes separated in depth would not land on the same point. The
reference uses vertical for the year; this does the exact opposite of encoding anything.

Coordinate ownership sits with the sidecar under `adr-design-017`. Its layout places domain clusters
on a unit circle and expands each cluster by a preset, and **that arrangement was designed for
viewing a sphere. There is no planar-specific rule in it at all.**

## 6. The totals view as built: a stacked bar, not a Sankey

Read in full from the planar renderer's totals mount.

| Fact |
|---|
| Each row is a domain, and each row is a track with three rectangles |
| **There are no paths, no ribbons and no node columns.** No Sankey path generation code exists in the file |
| Segments are distinguished by three steps of ink density |
| Only one grouping axis is available at a time: either the source axis or the connection-state axis |

The specification required three columns, source into topic into connection state, with every column
summing to the same total. The mockup's own verification table already recorded conservation as
passing. **So the mockup was a genuine Sankey and the application had been shortened to a stacked
bar.** With one axis visible at a time, following a path from origin through topic to state is not
merely absent, it is impossible in principle.

That is the second specification non-compliance found in the same week. The first was the outer scope
level of the planar views drawing a topic census where the specification said everything.

## 7. The external chart specification is isomorphic

Read from the library's own documentation: nodes carry a category among three, links reference nodes
by index with a value, and the layout parameters cover margins, node width and padding, corner
radius, aspect ratio, animation duration, faded opacity for nodes and links, and a source-to-target
gradient.

**Its three categories map exactly onto source, topic and connection state.** A ledger row already
carries the origin counts, the domain, and the state counts, so the column nodes and the links come
out of **one pure fold with no backend change at all.**

Two adjustments were required and both were small:

- **Reject the gradient.** One ink, with value-proportional stroke opacity.
- **Use the canonical faded value for links**, which is marginally different from the library's. The
  node value already matched exactly, which is itself a small confirmation that the canonical numbers
  were reasonable.

## 8. The verdicts

### 8.1 The planar view: the question is what the vertical axis means

| Option | Vertical | Problem |
|---|---|---|
| A, time anchored | a timestamp | **duplicates the timeline viewpoint**, which already puts time on the vertical |
| B, topology anchored | topological rank | **duplicated the flow viewpoint**, which already showed rank |
| C, no axis, transplant the grammar | hub radiation | gives up the reference's year axis |

**C was recommended**, on the ground that the year axis is not what makes the reference readable,
since its alignment could not even be confirmed. Four other things do the work, and all four operate
without an axis and touch no closed layer:

1. **Hub-centered radiation**, so satellites read as belonging to a hub. The hub promotion order is
   already specified.
2. **Numeric badges on the marks**, so quantity is attached to the node rather than inferred.
3. **Permanent labels**, where the default here was labels on hover.
4. **A bottom metric bar**, so totals are not lost while looking at the map.

Option A survives as a **sixth layout preset** rather than being discarded, because presets are open
layer: registering one more pure function costs nothing and keeps determinism intact.

> Option B's stated ground has since dissolved. `adr-design-053` retired the flow viewpoint, on the
> reasoning that the rank layout preset in the planar view shows the same topological order with more
> context. The conclusion is unchanged and its justification is now the opposite one.

**Renderer choice.** Move the planar view onto the graph library already in the dependency list and
already in real use in the comparison frame. It is a graphics-processor renderer, so it holds
thousands of nodes, and badges and metric bars go into a document overlay above its canvas, managed
in the same layer as the replacement tooltip. **Then the planar view stops being the 3D view's shadow
and the vertical residue disappears with the projection.**

Cost: nothing in the sidecar. A new renderer module, a badge overlay and a metric bar in the
application, and **removing seven flat-view branches makes the 3D code simpler than before.** The
stated risk was that the comparison frame already used that library with its own styling, so the
theme and mark conventions had to be extracted and shared first.

### 8.2 The totals view: easy, because the data is already shaped

The ledger row already carries all three columns. Conservation was already verified in the mockup.
No layout library is needed, because with three fixed columns the arithmetic is shorter than the
adapter would be: thirds for the column positions, node height proportional to value, and a cubic
bezier with vertical tangents for the ribbon. A layout library's real value is ordering optimization
in many-column and cyclic graphs, and three columns have none of that. The drilldown reuses the
existing panel, since the click contract is the same.

**Three traps were predicted and all three were real.** Hiding unlinked nodes removes the orphan
column, so conservation has to be recomputed rather than asserted. The middle column's label crosses
the ribbons, so the label plate must be **measured rather than estimated**. And measuring inside a
hidden tab returns zero, so views must be built when they become visible.

**What it gains** is a sentence the stacked bar cannot express: how much knowledge that came from
episodes accumulated in a given topic, and how much of that ended up orphaned, read as a path in one
screen. That path is the entry point into the orphan-minimization flow.

## 9. What was rejected from the reference, explicitly

| Element | Verdict | Ground |
|---|---|---|
| Five-color type palette | **Reject** | Measured: in the dark theme an adjacent pair separates by 0.8, which is effectively one color under red-green deficiency, and 11.0 in normal vision against a threshold of 15. Dozens of crossing ribbons have no cushion for that |
| Glowing beams, bloom, particles | **Reject** | Against the visual register, and against the rule that only composited properties are animated |
| Equalizer rings around nodes | **Defer** | Decoration. If activity must be shown, border thickness is the honest channel, and an access count already exists in the data |
| Percentage badges on every mark | **Conditional** | Computable from the ledger, but a number on every mark is noise. Hub nodes only |
| Bottom metric bar | **Adopt** | One ledger call yields concept count, connection rate, orphan count and last update |
| Permanent labels | **Adopt** | Open layer. A default change, and the planar renderer already declutters |
| The category fan | **Candidate** | An alternative presentation of the detail panel, built from the same ribbon code as the totals work |
| The question input | **Separate** | A product direction question. The assistant interface is already that path |
| Top toolbar | **Reject** | Top tabs were already retired in favor of a single view panel. Not reopened |

## 10. The stage plan, and why the order was what it was

| Stage | Work | Depends on |
|---|---|---|
| 1 | Totals returns to three columns | nothing |
| 2 | Planar outer level draws the full population, and the timeline band height varies | nothing |
| 3 | Replace the native tooltip, and introduce motion tokens | best after 1 and 2, when the targets are known |
| 4 | Promote the planar view to an independent renderer with badges and a metric bar | 3, for the shared overlay layer |
| 5 | The time-anchored layout preset | 4 |
| 6 | The category fan drilldown | 1 |

Stages one and two are compliance repairs and therefore carry no judgment cost. Stage three builds
the overlay layer that four and six both consume. **Doing four before three would mean building the
tooltip and the badges twice.**

## 11. What was built, the same day

Stages one through four, after owner approval. **Backend change zero. New dependencies zero.**

| Stage | Verification |
|---|---|
| Three-column Sankey | Browser measurement: all three columns summing to the same total, each of ten nodes' height equal to the sum of the ribbon thicknesses attached to it within a tenth of a pixel, and label plate widths taken from a real measurement rather than a placeholder |
| Planar outer level and variable bands | A scenario with forty items in one month produced a viewport a thousand pixels tall, where the previous fixed formula would have compressed those forty into a little over half that. Beyond a cap, the excess is stated in a caption |
| Own tooltip | A new module, seventy-three call sites converted, and boot wiring. All three conditions of the hover and focus criterion measured as passing: closes on the escape key, survives the pointer moving onto it, and persists when left alone |
| Independent planar renderer | Eight hub badges tracking their coordinates, a metric bar, and hover keeping focus and neighbors at full ink while everything else recedes to the canonical values |

Three things were cleaned up alongside. The totals grouping preference was retired, because three
columns show both axes at once and there is nothing left to choose. Three copy strings that only
existed to serve that choice were deleted. And the recede values were given custom properties as
their single definition point, which the new planar renderer reads in order to blend ink toward the
background, since a graphics-processor renderer cannot inherit a stylesheet.

## 12. Two defects found during implementation, kept as warnings

Both are shapes that recur, which is why they are preserved rather than summarized.

1. **An attribute was appended to a tag that had already closed.** During a bulk replacement of the
   native tooltip markup, the new attribute landed after the closing bracket, so it rendered as a
   **text node** instead of an attribute. The only symptom was that the tooltip silently did not
   appear. In a renderer that assembles strings, confirm the fragment you are editing is inside the
   opening tag before adding to it.
2. **Passing an undefined option is not the same as omitting it.** Handing the graph library an
   explicit undefined for its label-drawing option does not fall back to the default: the key's
   presence makes it call undefined, and the render dies. The option must not be passed at all.

## 13. The decisions

| Now | Was | Decision | State |
|---|---|---|---|
| `adr-design-046` | D46 | Promote the planar view to an independent renderer, ending the flattened projection | built |
| `adr-design-047` | D47 | Totals returns to three columns. The stacked bar is retired | built |
| `adr-design-048` | D48-T | An own tooltip implementation, replacing the native one | built |

Two more were recorded in the original and are **not** decision nodes: the planar outer level drawing
the full population with variable timeline bands, which was built, and the confirmation that the
reference's coloring and glow are not adopted, which the owner agreed to on 2026-07-25.

> **A collision worth watching.** The tooltip decision carried a one-off suffix, which the migration
> dropped so that it is now the plain number. The original also uses that same plain number for a
> **different, unregistered** decision: adding a time-anchored layout preset, which was never
> started. Anyone reading the source text should not assume the two are the same decision. See
> [[vinc.canon.decisions]].

The other label used here with no identifier is the one naming the edge density ladder.

## 14. What the owner confirmed, 2026-07-25

1. **Option C.** No year axis in the planar view. Time is the timeline viewpoint's job and rank was
   the flow viewpoint's. The time-anchored preset can be added as open layer when it is wanted, so it
   was not built now.
2. **The rejection of color and glow is agreed.** Most of the reference's impression comes from
   exactly there, and there is no basis for overturning the measured separation figure. Making the
   product look like the film would require redesigning the palette and putting it back through the
   verifier, as **its own track**, and that track was not opened.

## Provenance

| Source | Relation |
|---|---|
| Owner questions, 2026-07-25 | The two questions this study answers |
| Reference film, twenty-one frames measured 2026-07-25 | Sections 2 and 3. A concept film with a studio watermark, not a shipping product |
| External chart library documentation, fetched 2026-07-25 | The layout parameters and the choreography timings of section 7 |
| `app/src/surfaces/atlas3d.ts`, `atlas-planar.ts`, `atlas.ts`, `api/query.py` | The as-built readings of sections 5 and 6 |
| Browser measurement, 2026-07-25 | Section 11 |
| [[vinc.spec.atlas-views]] | Where the totals view and the timeline are specified as built |
| [[vinc.spec.surface-atlas]] | Where the renderer split is specified as built |
| [[vinc.research.sankey-alluvial]] | The follow-up that arranged the ribbons this study restored |
| [[vinc.research.motion]] | The parallel study covering the tooltip and the entrance choreography |
| [[vinc.canon.decisions]] | Registry for the `adr-` identifiers used above |
