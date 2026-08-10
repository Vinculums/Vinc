---
doc_id: vinc.research.atlas-mark-verdict
layer: research
domain: vinc/design
authority: research
title: The mark language verdict, and where Vinc stands against comparable tools
supersedes: vinc.d.atlas-mark-verdict
updated: 2026-08-07
inherits_from: [vinc.research.atlas-mark, vinc.research.atlas-visibility-verification]
tags: [research, design, atlas, marks, competitive, benchmark, merge-semantics]
---

# The mark language verdict, and where Vinc stands against comparable tools

> Research input, dated 2026-08-03. It adjudicates the five proposals in [[vinc.research.atlas-mark]],
> analyzes a separate governance decision about how the edge resolver behaves, reconciles a direct
> contradiction between two earlier studies using live data, and compares the product against eleven
> others on eight axes.
>
> **This document is the origin of `adr-design-077` through `adr-design-080`.** It is also where the
> decision numbering fork was found, which is why the migration that produced this corpus had a
> numbering anomaly to resolve.

## 1. What was measured, and what was not

Four grades of evidence, kept separate, because three of the four verdicts turn on which grade a
claim has.

| Grade | What it covers |
|---|---|
| **Store measurement** | The live node type distribution, and which decision nodes exist. Queried directly during the session |
| **Web confirmation** | That the wide-line type is an official addon of an already-installed package, and the published limits on categorical color counts |
| **Session observation** | Search began returning pagination fields partway through the session that it had not returned earlier, which is consistent with `adr-governance-014` having deployed in between |
| **Self-reported, unverified** | Every code anchor inherited from the two source studies. The repository was not reachable |

## 2. The verdicts

| Candidate | Verdict | Weight | Cost | Invariants |
|---|---|---|---|---|
| `adr-design-077`, retire the node glass and clear the dead channels | **Adopt** | high | low | no breach |
| `adr-design-078`, type maps to five closed colors | **Reject**, replaced by fill against outline | high | medium | **breaches monochrome** |
| `adr-design-079`, restore line width | **Adopt** | medium | medium | no breach, the type is built in |
| `adr-design-080`, local graph as the default entry | **Adopt** | high | medium to high | no breach |
| `adr-governance-015`, the edge resolver queries the store | **Adopt, restricted** | medium | medium | none, under the restriction |

### 2.1 The single fact the whole adjudication rests on

**The live data contains three node kinds, not five.** Concepts, records and episodes. Bridges and
entities are at zero.

That measurement does two things at once. It removes the basis for opening color, because the
problem color would solve, telling five kinds apart, does not occur in the data. And it removes the
basis for the opposite prescription in [[vinc.research.atlas-visibility-verification]], which
computed an encoding overload from the same count of five.

Two studies reached opposite conclusions from one premise, and the premise was wrong.

## 3. Each verdict

### 3.1 Retire the glass and clear the dead channels

The channel budget analysis holds. Hue, material and line width empty while shape is overloaded is
consistent with the perception literature: shape is a weaker nominal channel than hue, size or
lightness, and it degrades first under zoom.

If the self-reported reading is right, the glass is not rendered at all in the default theme, in
which case two material systems are being maintained for zero benefit. The literature offers no
support for the other half either: follow-up experiments to the decoration studies do not find an
accuracy gain from three dimensional materiality or glow. Deleting a code path with no rendering
benefit is pure cleanup.

**Conditions attached.** The code anchors are unverified, so confirm against the working tree first:
if the glass does render in dark, the premise is wrong and the decision reopens. And run the render
pipeline audit before this, because if the color space or tone mapping is misconfigured, that is the
cheaper explanation and it invalidates palette work done ahead of it.

**Keeping the floating chrome glass is correct.** On chrome, materiality is an interface affordance
rather than a competitor to a data encoding, so the opportunity-cost argument does not reach it.

### 3.2 Reject color for type, and write the reopening threshold

This was the heaviest item, and the argument for color is sound on its own terms: the structural
advantage over comparable tools is type, the screen does not use it, and color for type is the
convention across ontology and graph platform tooling. The objections were each answered
convincingly.

**But the premise disagrees with the data.** Color is needed when five kinds must be told apart
permanently. Three kinds separate on fill and outline alone: filled, outline only, and either doubly
outlined or a different shape. **So the proposal pays an invariant to solve a problem the data does
not contain.**

The color-blind-safe evidence is corroborating rather than decisive. Validated categorical palettes
cap at about six colors, extending to eight or ten when combined with shape or pattern, and some
experimental work puts the limit for color alone below seven. Three would sit comfortably inside
that range. **Three simply do not need it.** Even the ontology visualization specification that uses
color for type states that the absence of color is not a decisive loss of information.

**The reopening threshold is written so the question returns without anyone remembering to ask it.**
If bridges and entities come into real use, more than three kinds are routinely on screen, and a
pair appears that shape and fill cannot separate, the decision reopens by itself. At that point
monochrome stops being an advantage and becomes a constraint. That threshold is factual rather than
aesthetic, and it is recorded in [[vinc.spec.atlas-visual-language]].

Opening color at all is a brand and invariant question, so it stays with the owner regardless of
this verdict.

### 3.3 Restore line width, and rewire its prerequisite

**Confirmed on the web:** the wide-line type and its material and geometry are official addons of
the renderer package already installed, distributed with it and imported from its addons path.
They are **not a new dependency**, so the rule against adding libraries is not engaged. The same
documentation states that the basic line material's width property is ignored on most platforms and
fixed at one pixel, and directs anyone needing thicker lines to these types. That confirms the
diagnosis that the original width-zero decision came from a false choice between fat tubes and
hairlines.

**The prerequisite is rewired.** The proposal made this depend on the color decision. With color
rejected, width has nothing to do with hue: it is an independent channel carrying directed against
undirected against cross-domain. It depends on the cleanup instead.

**Cautions.** The wide-line type needs its resolution updated on every resize, and at thousands of
edges its segment geometry costs more than a plain line. At scale it only pays off alongside
filtering and the local graph.

### 3.4 Make the local graph the default entry

**This is the highest-impact item of the four.** In the reported experience of the comparable tool,
the local graph was the one view that stayed useful at any scale, and the professional tools all
begin from a search or a node selection. **Whole graph first is the convention of personal knowledge
toys, not of specialist tools.**

The backend already exists: a one-hop neighbor query and a seed-with-depth traversal. Only the
default changes, which makes this a low-structure change with a high effect.

**The condition in the original proposal was right.** Do not remove the whole-graph survey. The 3D
explosion layout has its own justification in survey and spatial memory. Change the default entry
and keep the survey behind an explicit action.

## 4. The edge resolver, and what silent skipping costs

A separate governance question decided in the same pass: whether the write path should query the
store when resolving an edge's endpoints.

### 4.1 Decomposing the effect on merge semantics

Nodes match by exact identifier. Edges match on the exact triple of source, type and target, **after
both endpoints have matched**. The problem is that endpoint matching draws only from the nodes
carried in the same payload, so an edge to a node that exists in the store but is not in the payload
is skipped.

Adding a store lookup splits into two very different changes:

- **Confirm the endpoint's type only.** The edge's exact-match rule is unchanged and merge semantics
  are unaffected.
- **Treat what the lookup returns as an update target.** Now property and tag preservation, owner
  stamping and theme folding all become involved. **This is the dangerous branch.**

**A workaround already exists**, and noticing it is what makes the restricted verdict possible:
carrying the existing node in the payload in minimal form, identifier and type and title and domain,
attaches it as matched while preserving its properties and tags. **So the benefit of a store lookup
is convenience, not correctness.**

### 4.2 What other systems do

- The dominant property-graph query language's own knowledge base warns about exactly this trap: to
  use existing nodes and relations, match or merge the nodes first and merge the pattern using the
  bound variables. Merging a whole pattern matches it entirely or creates it entirely, and any
  imbalance produces duplicates. **Bind the endpoints first is the standard**, and this product's
  minimal-payload workaround is the local form of it.
- The triple-store insert statement is the opposite extreme: ground triples only, with no endpoint
  existence requirement, so dangling references are permitted. That model does not transfer to a
  labeled property graph.
- Across property-graph systems the standard is **confirm the endpoints, then either fail explicitly
  or create explicitly.** Never skip silently.

### 4.3 Silent skipping is a named antipattern

Tool design guidance for assistant interfaces names two antipatterns together: **suppressing errors
silently**, meaning returning an empty result as success, and **ending an entire workflow on a
single failure**. Both leave the coordinating model deciding blind. A partial success must state
what succeeded, and what failed and why.

The same guidance separates access failure from a legitimately empty result. "The target node was
not in the payload, so its type is unknown" and "the type genuinely does not match" are different
errors, and distinguishing them was already the right call.

### 4.4 The restricted adoption, and its stop criteria

**Query the store to confirm endpoint types. Do not create or update nodes.** An edge toward a node
confirmed by lookup attaches as matched, and that node's properties, tags, title and domain are left
untouched, since their absence from the payload means no update was intended. Merge semantics stay
unchanged and only the silent skip disappears.

Reporting is strengthened: count resolutions from the store in the response warnings, and leave
genuinely unresolved endpoints as explicit errors.

| Stop if | Action |
|---|---|
| A per-node lookup more than doubles batch latency on a large ingestion | Switch to a batched prefetch of the identifier set, or roll back |
| A lookup reaches a node outside the owner scope | Stop immediately. Owner isolation is the higher invariant |
| Tests detect any property update on a node that was only looked up | Stop. The boundary has been crossed |

## 5. Reconciling the two earlier studies

The verification study concluded that five node kinds and six edge kinds cannot be discriminated
permanently in monochrome, and prescribed giving up permanent discrimination in favor of on-demand
reveal. The redesign study concluded the opposite: redefine monochrome and open color to **recover**
discrimination.

Three rulings settle it.

1. **The five-kinds premise is wrong on the node side.** Three exist. The edge side was not measured
   and that half of the claim remains untested.
2. **Therefore the give-up prescription is an over-correction.** Three kinds separate on shape and
   fill, so nothing needs to be surrendered.
3. **And color is also an over-correction**, for the same reason.

The two documents merge rather than compete:

- **Permanent screen:** three node kinds by shape and fill, size by degree, no color.
- **Edges:** direction by arrowhead and taper, and kind by width. Dashes reserved for cross-domain.
- **On demand:** bridges, entities, detailed types, evidence and confidence appear on hover, filter
  or legend toggle. That is the part of the verification study that was right.

## 6. The numbering fork, found here

Two visibility decisions existed as integer-numbered nodes in the store. The verification study,
written the previous day, registered the same two decisions again under a lettered sub-series, and
added three more that had no integer at all. The mark work had meanwhile returned to the integer
sequence. One subject, three conventions.

| Decision | Integer node | Lettered duplicate |
|---|---|---|
| Fog adaptation and recede conformance | existed | added |
| Mark and edge ladder plus the contrast gate | existed | added |
| Label legibility and user control | none | added |
| Semantic zoom, shape, aggregation | none | added |
| Render pipeline audit | none | added |

**The recommendation was to unify on integers**, mark the lettered duplicates as superseded, and
**not** assign the three missing numbers here, on the explicit ground that numbering is a governance
act rather than a research one. It was raised for the owner instead.

The migration resolved it: the duplicates merged into the integers they duplicated, and the three
without integers received `adr-design-081`, `adr-design-082` and `adr-design-083` from the top of
the design series. See [[vinc.canon.decisions]].

## 7. The comparison

Eight axes: type encoding, lines and dimensionality, response to tangling, entry model, operational
metadata, accessibility, offline and ownership, and editing inside the visualization.

| Tool | Type encoding | Lines, dimension | Tangle response | Entry model | Ownership |
|---|---|---|---|---|---|
| **Vinc** | shape, three kinds live, no color | hairline at width zero, 3D default plus a plane | legend filter, local graph not yet default | **whole graph first** | fully offline, local, user-owned |
| Neo4j Bloom and Explore | color by label, plus size, icons, rules | curves, 2D | search first, perspectives, rules | search first | cloud, or local desktop, paid at scale |
| WebVOWL | color plus shape plus line style by ontology element | solid and dashed, 2D force | datatype filters | load everything | web application, runs locally |
| Obsidian | color by folder or tag, manual, no shape | uniform lines, 2D | **weak**, tangles around two hundred nodes | whole graph first | local, markdown, user-owned |
| Logseq | color by page | uniform lines, 2D | weak | whole plus local | local, open source |
| Tana | supertag structure, **no graph view** | not applicable | avoided through views | search and views | cloud-centric |
| GraphXR | color, icon, size, label, multi-channel | curves and bundling, 2D and 3D | filter, aggregate, community, paths | load then query | browser or desktop, air-gapped possible, paid |
| Cosmograph | color and size, on the graphics processor | thin lines, 2D | **scales to a million nodes** | load data | local processing, data not transmitted |
| Gephi | color by category or community, plus size and shape | **width and density carry weight**, 2D | filter, layout, bundling | load everything | desktop, local, open source |
| Cytoscape | color, shape, opacity, label, by style mapping | **line style carries relation kind, opacity carries weight** | opacity, bundling, filter | load everything | desktop, local, open source |
| InfraNodus | color by community, automatic | density by co-occurrence | community and gap analysis | text to graph | web, open source, assistant-connected |
| Kumu | color, size, decoration by rule | rule-driven lines, 2D | filter, focus, cluster | load a map | cloud |

One name in the original brief resolves ambiguously and the disambiguation is worth keeping: a
declarative agent workflow engine shares a name with the family of knowledge-graph-plus-language-model
products. Only the latter is a comparison target. Those products encode entities as circles, size as
degree, and color as community, and generate community summaries with a language model. **That is
the exact inverse of a deterministic backend with no runtime language model**, and the contrast is
itself a statement of what this product is.

### 7.1 Three places Vinc is behind

1. **The entry model is the old convention.** Every specialist tool starts from a search or a node.
   Whole graph first is the specific thing the comparable consumer tool is criticized for.
   `adr-design-080` addresses it and is still unbuilt.
2. **Line width carries nothing.** Two of the open-source desktop tools have carried weight and
   relation kind on width, density and line style for many years. `adr-design-079` addresses it.
3. **Operational metadata is not on screen.** Evidence and confidence exist **in the data** and are
   not drawn. A rules-based styling engine in one competitor maps properties onto color, size and
   caption. **Holding a better data model and displaying it at the weaker tool's level is the
   expensive part of this list.**

### 7.2 Two problems being re-solved

4. **Tangling has a settled answer**: local graph, filter, search entry. This project was debating
   whether to solve it with channels or with interaction while the field had already settled on
   interaction.
5. **Color for type also has an answer**, redundant encoding. But this product's specifics, pack
   determinism and a monochrome brand, mean the answer cannot be copied unmodified, which is why the
   fill-against-outline variant fits here.

## 8. What is actually an advantage, judged coldly

**Real, and to be protected first.**

- **Fully offline, no cloud, user-owned data, with a deterministic backend.** Competitors are cloud,
  hosted, or paid. Several are locally runnable, but very few satisfy deterministic backend plus
  local embedding plus zero external keys **together**. The temptation during any redesign to slip in
  an external font, a content delivery network, a graphics library or a runtime language model must
  be refused explicitly. The line-width verdict is only safe because that rule forced the check that
  found the type is already bundled.
- **The chunk as a lossless source of truth**, where the summarizing competitors discard the original.
  Evidence edges attach to the chunk, so tracing back to source text loses nothing.
- **Spatial memory from the 3D explosion layout.** 3D has no confirmed accuracy advantage, but survey
  and spatial memory are legitimate stated intents. **Making the local graph the default must not
  remove the survey.**

**Potential, not yet an advantage.**

- **Typed edges with evidence and confidence.** A structural advantage over the note-tool family, and
  invisible on screen. Until it is drawn it is a data model advantage, not a product advantage.

**Conditional.**

- **Monochrome, and independence from color.** Genuinely good for color vision deficiency, for print,
  and for pack determinism. It stops being an advantage the moment it prevents discrimination. Three
  kinds are fine. Four permanent kinds is the threshold.

**Not an advantage.**

- **Owner scope.** Good security design that every competitor also has. Keep it as an invariant,
  do not present it as a differentiator.
- **The conventions channel.** Injecting writing conventions into the assistant is clever, but it is
  an application of ordinary tool-description practice, and the resolver trap above shows that
  following the conventions alone still walks into a hole. Not mature enough to claim.

## 9. Points worth borrowing

| From | What | Applied here | Relation |
|---|---|---|---|
| Rule-based styling | Properties mapped onto visual channels by rule | Evidence and confidence onto edge density and node outline | Deterministic, so no invariant breach |
| Search-first entry | Start from a node or a query | `adr-design-080` | |
| Line style as relation kind | Dash, solid, arrow, width together | `adr-design-079` | |
| Redundant encoding | Color plus shape plus label at once | Shape plus fill plus label, without the color | The fill-against-outline variant |
| Graphics-processor layout | Very large graph handling | Reference only. Keep the current renderers | **New dependency risk** |
| Community visualization | Cluster structure drawn | The community diagnostic already exists and could be drawn, **without summarizing it** | Clusters are deterministic, so the ban on runtime language models is not engaged |

## 10. The execution order proposed

**Immediate, invariant-safe.** Run the render pipeline audit, then retire the glass and clear the
dead channels, after confirming the code anchors. Stop if the glass turns out to render in dark.
Map the decision numbering, and stop short of assigning numbers.

**Structural, after a usability check.** The local graph as the default entry; stop if the node count
on entry does not drop below a constant above two hundred nodes. Then line width, stop if the frame
budget is exceeded at thousands of edges without filtering alongside.

**Integration.** Fill-against-outline for the three permanent kinds; stop if the three fail to
separate under color vision deficiency simulation or below the cartographic size floor. Then draw
evidence and confidence.

**Restricted and owner-gated.** The edge resolver change under its stop criteria. Color returns only
if the reopening threshold is reached.

## 11. What was left to the owner

1. **Whether to open color**, since it redefines the monochrome discipline. The verdict is that it is
   unnecessary now, and the reopening threshold makes the question return automatically.
2. **The standing of the 3D whole-graph survey** once local entry becomes the default: product
   identity, or a survey aid.
3. **Whether to promote the un-numbered decisions**, and to which numbers.

The third was answered by the migration. The first two remain open.

## 12. Limits

- **No code access.** Every line and constant anchor is inherited self-report, and both source
  documents admit as much. Confirm against the working tree before implementing.
- **The edge distribution was not measured.** The node distribution was. The claim that six edge
  kinds overload the channel is therefore not as firm as the node finding.
- **Local store only.** The account graph's distribution may differ.
- **One competitor's product status is uncertain**, since a rename and console consolidation are
  confirmed but a formal discontinuation of the standalone tool is not, and version numbering
  disagrees between its own documents.
- **One open-source visualizer is effectively dormant**, with no public commits to the original
  repository since 2023-07 and active development only in third-party forks.
- **The 3D and bundling findings come from general graph experiments**, not from this product's data
  or its users.

## 13. What happened next

- **The cleanup shipped the same day.** v0.7.3, 2026-08-03: the node glass retired with the dark
  screen unchanged by a pixel, and the dead density ramp rewired onto relation confidence. That last
  change is the first of the borrowed points landing: a stored property reached a visual channel.
- **The chrome glass was kept**, as this verdict directed. See [[vinc.spec.glass-language]].
- **Line width and local entry are still unbuilt**, and are recorded as open in
  [[vinc.spec.atlas-visual-language]].
- **The numbering fork became a migration task** rather than a decision, and is resolved in
  [[vinc.canon.decisions]].

## Provenance

| Source | Relation |
|---|---|
| [[vinc.research.atlas-mark]] | The proposals adjudicated here |
| [[vinc.research.atlas-visibility-verification]] | The study reconciled in section 5, and the source of the numbering fork |
| Store measurement, 2026-08-03 | The live node type distribution, and which decision nodes existed |
| External survey, 2026-08-03 | Renderer addon documentation and the fixed line width, property-graph merge guidance, assistant tool design guidance, ontology visualization specification, categorical color limits, and the product documentation of eleven comparison tools |
| [[vinc.release.changelog]] | The v0.7.3 entry, where the adopted cleanup shipped |
| [[vinc.spec.atlas-visual-language]] | Where the surviving mark language and the open items are specified |
| [[vinc.canon.mcp-contract]] | The tool contract the resolver decision modifies |
| [[vinc.canon.decisions]] | Registry for the `adr-` identifiers used above |
