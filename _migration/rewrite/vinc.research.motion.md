---
doc_id: vinc.research.motion
layer: research
domain: vinc/design
authority: research
title: Motion, examined, and what the product built instead
supersedes: vinc.d.motion-research
updated: 2026-08-07
inherits_from: [vinc.spec.atlas-visual-language, vinc.spec.atlas-views, vinc.spec.glass-language, vinc.spec.surface-tree]
tags: [research, design, motion, animation, tooltip, sankey, accessibility]
---

# Motion, examined, and what the product built instead

> Research input, dated 2026-07-25. It asks what a motion system and a chart interaction library
> can contribute to a product that has no framework, no external network access at runtime, and a
> monochrome discipline. Four external sources plus a walkthrough recording of the interface as it
> then stood.
>
> **v0.7.4 shipped a motion system on 2026-08-06, and it is not the one this document proposes.**
> Section 9 sets the two side by side and states precisely which parts carried over. That section
> is the reason this stays a research document rather than being promoted to a specification: a
> promoted version would assert a spring model the code does not contain.

## 1. What was studied

A general-purpose animation library and its agent tooling, a marketing component set built on it,
and a chart library's Sankey and tooltip components. Read alongside a recording of the then-current
interface walking from the map through the planar views into a detail panel.

The conclusion was compact enough to state in three lines.

1. **The code cannot be taken and the numbers can.** All of the component work assumes a framework,
   a utility stylesheet and a component kit that this product does not have. What can be lifted
   without any of that: the spring parameters, the entrance choreography timings, the recede opacity
   values, and the tooltip anatomy.
2. **One runtime passes the constraint filter.** Installed from the package registry and inlined by
   the bundler, it does not breach the ban on external script sources. Its minimal build is small,
   and it can animate arbitrary object values, which means a 3D camera. Lighter still: a generated
   easing curve gives a spring shape with no runtime at all.
3. **The agent tooling is a development-loop tool, not a product dependency.** Documentation
   context, a render-cost audit, and curve generation belong on the development machine. Its output
   is a static string compiled into the product, which is compatible with the offline and
   determinism rules.

## 2. The constraint filter, kept as a record of what was refused

A proposal filtered out is more informative than one adopted, because it names a boundary.

| Refused | Rule it broke | Substitute |
|---|---|---|
| Loading the library from an external script host | The content security policy forbids external sources | Package install, inlined by the bundler |
| Installing the marketing component set | No framework, no utility stylesheet, no component kit, and the wrong register | Take the theme file's structure and individual choreography ideas only |
| Installing the chart components | Same dependencies | Take the numbers, the timeline and the anatomy |
| Gradient ribbons and a per-category node palette | Monochrome with one reserved focus color | One ink, hierarchy by opacity and thickness alone |
| Confetti, border beams, coverflow, page curtains | Decoration in excess of the product's register | A quiet crossfade and a small travel distance |
| A slot-machine digit animation on a date label | Visual noise in a data application | Rejected outright |
| Wiring the agent tooling into the product runtime | Offline and determinism | Development machine only. Only its artifacts reach the product |

## 3. The proposed token set

The rule worth importing was not any particular number. It was that **no component hardcodes a
transition**: every one resolves it from a single theme file. That is the same discipline the
product already applies to screen strings, applied to motion.

The proposal reduced the source's five marketing presets to four, named for what they move: a small
one for badges and snapping, a general one for panels and tooltips, a slower one for width and
height morphs, and a slowest one for the 3D camera. Alongside them: two stagger intervals, three
travel distances at roughly half the source's values, the three recede opacities, and a reduced
motion mode.

Two rules rode with the tokens:

- **Chrome is a generated easing curve, with no runtime**: one curve per preset, generated at build
  time and embedded as a custom property. Deterministic because it is a fixed string, offline
  because it is a string, and free because it is a string.
- **Content and graph layers are the runtime's job**: the camera, staggers, dash offsets, and
  values fed into a canvas draw loop.

## 4. The per-view proposals

| Surface | Proposal |
|---|---|
| The 3D map | Drive the camera between scope levels on the slowest preset, or leave the library's own tween. **Never both.** Move the hover recede between token values instead of jumping |
| The retired flow view | Give the relation panel an entrance and widen the clicked edge by one step, in thickness rather than color |
| Timeline | Stagger the points in by lane, once per data load and never on revisit. Borrow the vertical crosshair with faded ends, following the cursor on a spring |
| Totals | **The most direct transplant.** Nodes fade and scale up, then links grow from source to target, on the source's easing curve. Compress the source's 1.1 seconds to 700 to 800 milliseconds, because that pacing was written for marketing. Hover keeps the link and its two endpoints, and recedes the rest to the token values |
| Tooltips, every view | **The highest priority item.** The native tooltip was already judged non-conformant against the hover and focus criterion, satisfying none of its three conditions. Reimplement the box, the off-screen flip, and the translucent blurred material |
| Reader and editor | Standardize the four states: a skeleton that hands off to real content, a quiet chip for save state, a small pop for the graph feedback badge, and a width morph for panel promotion |

## 5. The performance discipline

Adoptable without buying the audit tool, and adopted.

1. **Animate composited properties only**: opacity, transform, filter. Nothing that forces layout.
2. **One owner per render loop.** The WebGL renderers own their frames. The motion layer owns
   overlay elements and supplies values. The same value is never driven by two systems.
3. **Entrance animations fire on data events only.** A Sankey that regrows every time a tab is
   switched is noise. Load and refresh, once. Switching gets a crossfade.
4. **Reduced motion is a single switch at the token level**, not a decision per surface.

## 6. The roadmap as proposed

| Stage | Content | Added runtime |
|---|---|---|
| 1 | Motion tokens and generated curves, own tooltip, recede values fixed | none |
| 2 | Adopt the runtime for panel and card staggers, four-state handoffs, small pops | a few kilobytes |
| 3 | View choreography: the totals entrance, the timeline crosshair, the edge panel | small |
| 4 | Camera spring, and resolving the double ownership | small |

The stated minimum viable outcome for stage 1: hover recede moves by the same numbers and the same
curve in every view, the tooltip closes on the escape key, and no transition is hardcoded on screen.

## 7. What the owner decided, 2026-07-25

- **Do not buy the agent tooling.** Generate the easing curves with a local script instead. The
  parallel row of the roadmap is dropped. Note that the runtime package itself is separately
  licensed and free, so refusing the subscription is not refusing stage 2.
- **The recede values are fixed**: node 0.40, line 0.12, text 0.35. Registered as an amendment to
  `adr-design-032`, under a label that never became an identifier of its own. It **revises** the
  earlier value: nodes recede less than before and lines recede more.
- Runtime scope, entrance policy, the reduced-motion default, and crosshair scope were left open.

## 8. The as-built check at filing time, 2026-07-25

The document's assumptions about the codebase were checked against the working tree the day it was
filed. Eight of eleven held. The three corrections matter more than the eight.

| Assumption | Finding |
|---|---|
| Hover recede and panel entrance are instantaneous | **Wrong.** Both were already animated, in the planar renderer through a stylesheet transition behind a reduced-motion gate, and in 3D through a per-frame interpolation. The study's real contribution was therefore never "add motion", it was **unifying the numbers and curves across views** |
| The earlier first and second research rounds it cites | **Not documents.** They exist only as code, under label series that never became decision nodes or files. A citation that resolves to nothing is worse than no citation, so they are described here by effect |
| Ink and paper as the token vocabulary | **Outdated.** The token canon had already moved, and the motion tokens should be named in the current family. No duration, easing or spring token existed anywhere yet |

The remaining eight confirmed the constraint filter: no framework, an enforced content security
policy that blocks external script sources while allowing inline style, native tooltips in four
places in the planar renderer, a real drift in the recede values across renderers, a built-in
camera tween that would collide with a second driver, and five hardcoded motion values in the
stylesheet.

## 9. What v0.7.4 built, and where it diverges

Measured against `Vinc/app` on 2026-08-07, against the release published 2026-08-06.

### 9.1 Carried over, and checkable

| Proposal | As built |
|---|---|
| Fix the recede values and read them from one place | `app.css` defines the three custom properties and `app/src/recede.ts` is the only runtime read point, because the two WebGL renderers cannot read the stylesheet. The fallback constants in that file must equal the stylesheet's, and a gate checks it |
| Replace the native tooltip | `app/src/tooltip.ts`, satisfying all three conditions of the hover and focus criterion, with the off-screen flip rule and the chrome glass material both taken from the studied component. Landed 2026-07-25 as `adr-design-048` |
| The totals entrance choreography | In `app.css`: nodes fade with a per-index stagger over roughly a third of a second, then ribbons grow horizontally from their source edge over about half a second, beginning a sixth of a second in, on the source's easing curve. Inside the recommended budget, and well under the source's own |
| Growth by stroke dash offset | Used, but on the **timeline** root lines rather than the Sankey links, because a ribbon is a filled area and dash offset is a line's grammar. The ribbon grows by horizontal scale from its source edge instead |
| Entrance on data events only | The entrance classes are applied on first mount and not on a resize redraw |
| Composited properties only | Opacity, transform, and stroke dash offset throughout |
| Reduced motion gating | Present on every animation |
| Duration and easing tokens | The stylesheet carries an easing token and three duration tokens, one of which encodes that closing is shorter than opening |

### 9.2 Not carried over

| Proposal | What happened |
|---|---|
| Adopt the animation runtime | **Not a dependency.** Stage 2 was never taken. The shipped module uses the browser's own animation interface directly, for a reason this study never raised: **closing has to be awaitable**, so the element can be removed after the animation finishes rather than during it. Doing that through stylesheet classes repeats the same event wiring at every call site |
| The spring preset table | **Does not exist.** `app/src/motion.ts` uses two easing curves and fixed durations. There is no stiffness or damping anywhere in the product |
| Generated spring curves as custom properties | **Not generated.** Zero occurrences across the stylesheets. Both halves of the plan, buying the generator and writing one, went unused |
| Reduced motion keeps crossfades | **Stricter than proposed.** The shipped functions return before animating at all |
| Camera spring | Not taken. The 3D camera still uses the library's built-in tween, so the warning against double ownership remains unexercised rather than resolved |
| Crosshair, data dots, date label, cursor following | Not taken. `tooltip.ts` positions against the anchor rectangle and states why it does not follow the cursor: the marks are small, so a following box jitters |
| The audit tool | Not bought, per the 2026-07-25 decision. Its criteria were adopted by hand |

### 9.3 The divergence that is worth more than the list

**This study organized motion around a token file. The product organized it around an origin
point.**

What shipped: a floating panel grows from the control that opened it and collapses back into that
control, and the control itself bounces as though pressed. The starting scale is derived from the
panel's share of the viewport, because one ratio reads as "it came from that point" on a small
popover and as "the screen is zooming" on a sheet covering two thirds of the window. Where there is
no trigger, because the panel was opened by a keyboard shortcut, the origin is a neutral point
rather than a fabricated one.

None of that is in this document. The reason it is not is the useful part.

Section 8 established that entrance was **already** animated and concluded that the contribution
would be tokenizing values rather than adding motion. That was true and incomplete. **It audited
the values that existed and never asked which states had no value at all.** Closing had no animation
anywhere in the product, and nothing anywhere carried a spatial origin, so no constant existed to
find. Both gaps were found by watching the product on 2026-08-05, not by reading its constants.

The reverse also holds, and keeps this document useful: the values that shipped are the ones this
study argued for. Three opacities, one easing curve, one time budget, and one rule about when an
entrance may play, each with a reason attached. A specification states that they are the values. This
document is where the reason lives.

### 9.4 One addition from outside this study

The segmented control gained a sliding thumb in v0.7.4, so a choice moves rather than one cell
switching off and another switching on. It survives a container redraw by remembering its last
position and starting the new element there before moving it, because six separate call sites redraw
their containers wholesale. That is a first-then-last measurement, and this study did not propose it.

## 10. A separate question raised in the same round

Not a motion question, but raised in the same decision round on 2026-07-25, so the facts are kept
together.

**"Instead of clicking into a node to enter a view, can everything be shown at once."**

The finding was that the backend already returns everything. The graph plan takes the domain as an
optional argument and applies no limit when it is absent, so one call with neither domain nor seed
returns the whole graph with coordinates attached. What blocked it was the client calling a topic
census at the outermost level, plus a fixed month-band height in the timeline renderer that would
have compressed forty items in a month into a gap smaller than a mark.

The important part of that answer is what it made the request into: **not a feature proposal but a
specification compliance repair.** The specification already said the outermost level is everything,
and the totals view already obeyed it while the others did not. Three options were compared, and the
one adopted made the planar views load the full population while leaving the 3D map's domain-level
summary alone, since the semantic zoom is what produces the coupling matrix.

That work shipped on 2026-07-25 alongside the planar view rework. See [[vinc.research.view-redesign]]
and [[vinc.spec.atlas-views]].

## Provenance

| Source | Relation |
|---|---|
| Four external sources, read 2026-07-25 | An animation library and its agent tooling, a marketing component set, and a chart library's Sankey and tooltip |
| A walkthrough recording of the interface, 2026-07-25 | The state of the product the study was written against |
| `Vinc/app/src/motion.ts`, `app.css`, `tooltip.ts`, `recede.ts`, `package.json` | The 2026-08-07 as-built check in section 9 |
| [[vinc.release.changelog]] | The v0.7.4 entry, which states the shipped motion in the product's own words |
| [[vinc.spec.atlas-visual-language]] | Where the recede values are specified as built |
| [[vinc.spec.glass-language]] | The chrome material the tooltip inherits |
| [[vinc.spec.atlas-views]] | The totals view the entrance choreography belongs to |
| [[vinc.canon.decisions]] | Registry for the `adr-` identifiers used above |
