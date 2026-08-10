---
doc_id: vinc.spec.glass-language
layer: spec
domain: vinc/design
authority: spec
title: "Glass: what floats is translucent, what holds data is not"
supersedes: vinc.d.glass-language
updated: 2026-08-06
inherits_from: [vinc.spec.atlas-visual-language]
derives_from: [vinc.research.ux-restructure, vinc.research.desktop-shell]
tags: [spec, design, glass, material, chrome, accessibility]
---

# Glass: what floats is translucent, what holds data is not

> A material rule with one boundary in it. Glass belongs to chrome that floats over the content and
> to nothing else. The rule was set, then broken from inside, then restored by measurement. This
> document is the as-built after the restoration, verified against `app/src/app.css` and
> `app/src/surfaces/atlas3d.ts` on 2026-08-06.

## 1. The rule and the reason

`adr-design-037` accepted translucency into one layer only:

| Layer | Material | What sits there |
|---|---|---|
| Content plane | flat ink on paper, opaque | Atlas marks, lists, body text, resident navigation |
| Floating chrome | glass | Command palette, sheets, the view panel, the detail pane, the settings popover, tooltips, floating cards, the update toast |

The boundary is not a taste. **On chrome, material is an affordance. On content, material is a data
channel.** Atlas encodes weight and depth in ink density, so a translucent mark blends with whatever
is behind it and the encoding stops meaning anything. That is the readability complaint that
followed every glass design system, arriving through a channel collision rather than through
opacity as such.

Resident navigation is excluded even though it is chrome, because glass on something that never
moves drains the word "floating" of meaning.

There is a second, quieter reason glass suits this product specifically: a 3D canvas is the main
character, and a panel over it that lets the obscured graph show through preserves the sense of
space instead of punching a hole in it.

## 2. The rule was broken from inside, and then restored

The glass language was extended into the node material of the 3D canvas, which is the content plane
that the rule had just excluded. Nodes were given a frosted physically based material with clearcoat
and a baked environment map as a reflection source.

`adr-design-077` retired it on 2026-08-03. The argument was a measurement, not a preference:

- **In the default theme, the glass never rendered.** Dark is the default, and in dark the node
  builder takes a constellation-glow branch that returns a flat material before the physical one is
  ever constructed, for every node kind but the domain supernode.
- So the project was carrying two material systems, a physically based shader and a per-mount
  generated environment map, and getting nothing on the screen most users see.
- The reflection source retired with it, necessarily: scene environment maps are read only by
  physically based materials, so after the revert it was baking a texture nothing could look at.
- The wider literature offers no support for the other half of the case either. Experiments
  following the original decoration studies do not find a reading-accuracy gain from three
  dimensional material or glow.

**Dark did not change by a single pixel.** That is the point of the measurement: the cost was real
and the benefit was zero.

What went back was matte ink Lambert, which the renderer's own header had specified all along.
Lambert rather than an unlit flat material, because the light-driven gradient across a face is the
depth cue in the light theme. Opacity, color and shape were untouched. Only reflection and clearcoat
left.

The same decision cleared a second dead channel in the same pass, an edge density ramp that split on
a field nobody produced. See [[vinc.spec.atlas-visual-language]].

**The chrome glass of `adr-design-037` was explicitly kept.** The opportunity-cost argument does not
reach it, because there the material is not competing with a data encoding. Reading
`adr-design-077` as retiring glass everywhere inverts what it decided.

## 3. As-built parameters

### 3.1 The token set

Three custom properties define the material: a translucent card background, a blur combined with a
slight saturation lift, and an inset highlight along the top edge that implies thickness.

Two constraints ride with them:

- **An opacity floor.** Text on glass keeps its normal ink, so translucency is only allowed as far
  as the backdrop blur can still guarantee contrast.
- **The blur must exist for the translucency to be allowed.** See section 3.3.

### 3.2 Two implementations of the same material, and why

There are now two ways glass is applied, and the difference is not cosmetic.

**The token application.** A late block in `app.css`, inside a feature query for backdrop filter and
a media query excluding reduced transparency, sets the glass background, the blur and the edge
highlight directly on the element. It covers the palette card, the view panel, the detail pane, the
settings popover, tooltips and floating cards. The update toast applies the same tokens directly,
with an opaque fallback in the custom property.

**The sibling-layer application.** The document pane and the view panel put the glass on an absolute
`::before` layer instead, at a higher opacity and a stronger blur, with the content raised above it.
This was done on 2026-08-05 for a measured reason: **a backdrop filter on an ancestor of a scroll
container is recomputed on every scroll frame**, and the panel scrolled in visible steps. The blur
had to become a sibling of the scrolling surface rather than its ancestor, so the two cannot
invalidate each other's paint. The accompanying fix moved padding onto the inner scrolling element,
because padding on the panel pulls the scrollbar inside the content.

The second approach is the current one for new surfaces. The first is what the older surfaces still
carry. Section 5 records the conflict this leaves.

### 3.3 Degradation is to opaque, never to bare translucency

Where backdrop filter is unavailable, or the user has asked for reduced transparency, every glass
surface falls back to the opaque card.

The rule is stated as a negative because the tempting middle option is the broken one:
**translucency without blur is the worst of the three states.** The text behind shows through
sharply and both layers become unreadable, where a solid panel would have cost nothing. Glass is
therefore a progressive enhancement, and the opaque card is the base case rather than the failure
case.

### 3.4 Ambient background: something for the glass to pick up

A flat background gives glass nothing to refract. The page carries three non-semantic washes, a
bright paper light, a whisper of the point color, and a sand tone, all as radial gradients over the
base background and all referencing tokens, so the dark theme follows automatically.

The point-color wash is the one that needed a rule. The point color is reserved to mean "the current
focus" ([[vinc.spec.atlas-visual-language]]), so an ambient tint in that hue is at risk of reading as
a focus mark. Its opacity is capped near the perceptual threshold, low enough that it cannot be
mistaken for the reserved signal and high enough that the glass has something to absorb.

The Atlas canvas is drawn with an alpha channel in both themes, so the ambient shows through behind
the graph and a glass panel refracts graph and ambient together. In dark this holds through the
inverted palette rather than through an exception carved out for the canvas.

## 4. Native window vibrancy is not this

Operating-system window vibrancy in the desktop shell is a separate mechanism from the web layer
described here. The web specification is the canonical one and comes first; the shell may add
window-level translucency on top of it, and the two must not be described as one thing, because a
web fallback and a native fallback are decided by different systems.

## 5. Known gaps

Three, all found by reading `app.css` on 2026-08-06. None is fixed here.

- **The token block re-applies an ancestor blur that the sibling-layer fix removed.** The view panel
  and the detail pane appear in the late token block's selector list, and that block sits after
  their own rules in the same stylesheet with equal specificity, so it wins. That reinstates a
  backdrop filter directly on the element whose blur was deliberately moved to a sibling, and it
  puts a second glass layer under a panel that already has one. This is a cascade reading, not a
  runtime measurement: the scroll behavior should be checked on the real build before anything is
  changed.
- **The detail pane's glass is now nested.** It lives inside the document pane, which already has a
  sibling glass layer, and it is still in the token block's list. Two blurs stacked in the same
  visual column was not anyone's intent.
- **Sheets are named as a glass target and are opaque as-built.** The sheet is styled with a solid
  card background and no blur. Either the target list is stale or the sheet lost its material, and
  the document should not assert which without a decision.

There is also a numeric divergence to resolve whenever the first two are: the two implementations
use different opacity and different blur radii for what the specification calls one material. One
material with two sets of numbers is a definition that has stopped being a definition.

## Provenance

| Source | Relation |
|---|---|
| `app/src/app.css` | Token definitions, the late floating-chrome block, the sibling-layer surfaces, the ambient background |
| `app/src/surfaces/atlas3d.ts` | Node material after the revert, and the retired environment map |
| [[vinc.research.atlas-mark-verdict]] | The verdict that produced `adr-design-077`, including its explicit finding that chrome glass should stay |
| [[vinc.spec.atlas-visual-language]] | The encoding that content-plane translucency would collide with |
| [[vinc.canon.decisions]] | Registry for the `adr-` identifiers used above |
