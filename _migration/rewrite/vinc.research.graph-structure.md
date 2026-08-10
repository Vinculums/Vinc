---
doc_id: vinc.research.graph-structure
layer: research
domain: vinc/design
authority: research
title: "Graph structure measured: marks, relations, and how much the model can express"
supersedes: vinc.d.graph-structure-analysis
updated: 2026-08-07
inherits_from: [vinc.spec.atlas-visual-language, vinc.spec.surface-atlas, vinc.spec.dataplane, vinc.canon.vocabulary-model, vinc.canon.terminology]
tags: [research, design, atlas, topology, schema, diversity, as-built-snapshot]
---

# Graph structure measured: marks, relations, and how much the model can express

> Research input. A traced reading of the live code on **2026-07-19**, answering four questions:
> what governs the geometry of a node, what shape the whole layout produces, what a relation
> physically is, and how many distinguishable kinds of connection the model can express.
>
> **It is a dated snapshot and parts of it are now false.** The visual sections describe a material
> and a layout preset set that have both since been retired. Section 8 consolidates what has gone
> stale so a reader does not have to detect it while reading. What is built today is in
> [[vinc.spec.atlas-visual-language]]; the data plane contract is in [[vinc.spec.dataplane]]; the
> type vocabulary model is in [[vinc.canon.vocabulary-model]].
>
> The durable contribution is sections 5 to 7: the three-layer connection architecture, the
> arithmetic of expressible variety, and the asymmetry between the two.

## 1. What made this measurable at all

The version measured was taken the same day nine defects were repaired, and the repairs are what
turned the arithmetic in section 6 from a hypothetical into a live ceiling.

| Repair | Effect |
|---|---|
| The user-authored open layer was reactivated | It had been dormant: schema, engine and tests existed, but the live ingestion path converged on a legacy route and discarded the theme identifier. It was **ported into the live write path** rather than revived as its own engine, so it inherits revision stamping, write-side owner isolation, and deduplication. Reviving the dormant engine directly would have broken synchronization, since it does not stamp revisions |
| The map stopped rendering a single axis | Attachment axes joined the projection, so bridges, episodes and records appear alongside concepts |
| Degree counts every relation type | A concept whose only edges are bridge arms stopped being classified as an orphan |
| Bridges entered the ownership scope | Schema version two gave them an owner column |
| Episodes gained a first-class relation to concepts | The bridge between the episodic and the semantic layer |

Before those repairs the live number of distinguishable connection kinds was about nine. After them
it is the number in section 6.

## 2. Marks

### 2.1 Shape is type, and the mapping is closed

The primitive per node kind, and the lineage of each assignment, is specified in
[[vinc.spec.atlas-visual-language]] and is not restated. Two properties measured here still hold:

- Shape is a **closed** channel. It is not exposed in the view panel and cannot be changed by
  preference. The open layer is the size scale, labels, arrowheads, the plane, and spread.
- In the planar view the prism kinds are laid toward the camera so the hexagonal face reads
  frontally, which preserves shape as a type cue without depth.

### 2.2 Size takes the square root of the count, and only one count

Radius scales with the square root of degree, with a ceiling so a hub cannot eat the frame, and the
domain supernode scales the same way against its record count. A pinned node is promoted by a fixed
multiplier. In the planar view every radius is reduced, because a plane has no depth separation to
absorb crowding.

The rule behind all of it is that the size channel carries exactly one variable. Double encoding is
banned, which is why search score is not folded in.

### 2.3 The material section is historical

At the time of measurement, nodes were a physically based frosted material with clearcoat and a
generated environment map, at two opacity steps: an ordinary node translucent, and a gateway node
much more so, since transparency was the channel saying "present here, not a member here".

**That material no longer exists.** `adr-design-077` retired it on 2026-08-03 and the reasoning is
in [[vinc.spec.glass-language]]. Two things about this snapshot are worth keeping anyway:

- The gateway's use of transparency as a membership statement is the part that outlived the
  material.
- The ordinary node's translucency was later identified as a **contributing cause of the contrast
  failure** analyzed in [[vinc.research.atlas-visibility]]: a translucent fill sits against whatever
  is behind it, so its measured contrast is not the contrast that was designed.

## 3. Layout

### 3.1 Coordinates are pure functions

Layout runs in the sidecar as a pure function with no randomness and no time input, so the same
graph always produces the same coordinates. The renderer pins them and runs no force simulation.
This is `adr-design-017`, and it is the single clause from the earliest interface research that has
survived every subsequent revision.

Composition is two-stage: domain cluster centers are placed at equal intervals around a unit circle
in name order, and each cluster is then expanded internally by a preset. Ordering inside a cluster
is pinned first, then degree, then identifier, which is `adr-design-032` expressed as a sort key.

### 3.2 The preset section is historical

Five presets existed, and the default produced the shape the study is named for: a Fibonacci
sphere, a hub at the center with the remaining nodes distributed over shells of growing radius at
the golden angle. The clusters of those spheres, arranged around the unit circle, were the overall
form.

**The document already carried its own warning about this section**, and the situation has moved
further since: the default preset was retired, and `adr-design-074` reduced the set to two. The
current presets and the reasoning are in [[vinc.spec.atlas-visual-language]].

One detail from this section is worth rescuing because it explains a later decision. The planar fold
was **not** an orthographic projection: it mixed a fraction of the depth axis into the vertical one,
so that nodes separated only in depth would not collide on the plane. That mixing is exactly what
[[vinc.research.view-redesign]] later identified as the reason the planar view's vertical position
encoded nothing at all, and it is why `adr-design-046` replaced the projection with an independent
renderer.

## 4. Relations, physically

Nine relation tables. The engine fixes the source and target labels per table, and **that single
constraint is the physical cause of the design in section 5**.

| Relation | From, to | Properties | Direction | Written by |
|---|---|---|---|---|
| prerequisite of | concept to concept | | directed | concept ingestion, fragment writes |
| derives from | concept to concept | | directed | concept ingestion, fragment writes |
| related to | concept to concept | | symmetric | concept ingestion, fragment writes, the bind action |
| inherits from | concept to concept | | directed | fragment writes |
| crosses | bridge to domain | | structural | bridge ingestion, both ends |
| connects | bridge to concept | a role string | structural | bridge ingestion, fragment writes |
| attaches to | record to concept | a free type string | open | fragment writes |
| typed | entity to entity | a free type string | open | fragment writes |
| about | episode to concept | | structural | fragment writes |

Three consequences measured at the time:

- **The typed relation merges on the exact triple of source, type and target.** The type
  participates in the match key, so two differently typed edges can coexist between the same pair.
  That is the physical implementation of the rule that nothing is ever merged by similarity.
- Nine node tables exist but only five labels participate in relations. Chunks, skill executions
  and learner patterns are isolated layers used by search and statistics.
- **New edge types between the legacy node kinds are deliberately not representable.** Adding them
  would multiply relation tables combinatorially. A write that asks for one is reported through
  warnings rather than skipped, which is a different outcome and has to stay different. The
  exception is the episode-to-concept relation, promoted to first class because the value of a
  bridge between the episodic and semantic layers was judged to exceed the combinatorial cost.

## 5. The three-layer connection architecture

| Layer | Mechanism |
|---|---|
| Inside a domain | Front matter declarations produce the directed relations; wiki links produce the symmetric one, canonicalized so a pair yields one edge. **Directed arrows stand only on explicit authorship.** An earlier keyword classifier that inferred direction from ordinary prose was removed for false positives |
| Across domains | A first-class bridge node mediates. Measured on the hub data on 2026-07-10: **direct cross-domain edges between concepts numbered zero.** Every coupling ran through a bridge, connected to a concept on each side with a role, and crossing into both domains |
| User-authored | The open layer. Types are a per-theme authored vocabulary rather than a global enum, physically carried by an entity node and a typed relation |

Two properties of the open layer are load-bearing for section 6:

- **A type outside the vocabulary is not skipped.** It is admitted as proposed, with its provenance
  recorded, and reported back for ratification. Information loss is zero, and consequently **the
  system imposes no ceiling on vocabulary size**.
- The legacy node kinds route to their own tables and everything else routes to the entity carrier.
  The binding table is a routing table, not a validation gate.

## 6. How much variety the model can express

### 6.1 Distinguishable connection forms

Counting the distinct forms of "which kind, by what relation, to which kind":

```
T = 4     concept to concept
  + 1     bridge to domain
  + 1     episode to concept
  + D     bridge to concept, the role derived per domain
  + t     record to concept, one per attachment type
  + n²e   entity to entity, ordered pairs of node types by edge type
```

With five domains, a vocabulary of ten node types and eight edge types, and eight attachment types:
**819 forms, about ninety-one times the nine the fixed schema alone provides.** The dominant term is
the last one: at that vocabulary size the open layer is 800 of the 819.

### 6.2 The growth is compound

Adding one vocabulary item does not add a constant. It adds an amount proportional to the vocabulary
already there: a new node type adds `(2n+1)e` forms, and a new edge type adds `n²`. At ten and eight
that is 168 and 100 respectively. Since admission is automatic, there is no theoretical ceiling.

### 6.3 Instance capacity

Counting distinct edge slots rather than forms, at a hundred concepts, twenty bridges, five domains,
thirty records, thirty episodes and fifty entities: **roughly ninety-six thousand** distinguishable
slots. The dominant term grows with the square of node count times type count. Folding the symmetric
relation reduces the concept-to-concept term somewhat; the figure above counts direction, because
storage does.

## 7. The asymmetry, which is the point

**Semantic variety is open and grows quadratically. Visual vocabulary is closed and constant.**

| Channel | Variety |
|---|---|
| Semantic connection forms | 819 and rising, with no ceiling |
| Node visual states | 32, from shape by gateway by focus by pin |
| Edge visual states | 10, from density steps by arrowhead |

That is a deliberate choice in favor of legibility, and the study states its cost honestly: as the
authored vocabulary grows, the number of edges that **differ in form and look identical** grows with
it. Type and role are exposed on the non-ink channels, in the detail panel and the response payload,
but the ink channels do not divide. Whether to open an ink channel to type was left as a candidate
for a later decision rather than proposed here.

It became one. `adr-design-078` proposed a closed five-color set two weeks later and was rejected,
with a factual reopening threshold rather than an aesthetic one. See
[[vinc.research.atlas-mark-verdict]].

## 8. What has gone stale, consolidated

| Section | Status |
|---|---|
| The node material | Retired by `adr-design-077`, 2026-08-03 |
| The five layout presets and the default | Reduced to two by `adr-design-074` |
| The 32 and 10 visual state counts | Arithmetic over channels that have since changed. Historical |
| The render configuration count | Derived from five presets and six spread steps. Historical |
| The level-axis count | Includes the comparison, grid and matrix frames, all removed 2026-08-02 |
| The planar fold | Replaced by an independent renderer, `adr-design-046` |
| The transitional predicate for unowned legacy bridges | Recorded as an open item then and still open. See [[vinc.ops.risk-audit-2026-08-05]] |

### 8.1 The one that is a lesson, not just a date

This document records a **five-step density ramp on edges, keyed to a weight value**, as as-built.

It was not. Nothing in the projection layer ever set that field, so every edge rendered at one
density while the branching code read as though it encoded something. The gap was found much later
and `adr-design-077` rewired the ramp onto relation confidence, which the backend does produce.

The reason it was missed here is worth stating, because it recurs: **this study read the code that
branches on a value and did not read the code that supplies it.** A channel wired to a field nobody
fills is worse than an absent channel, because it looks like information from both ends, including
from an audit.

## 9. Decisions cited here that have no identifier

Seven labels appear in the original and none of them is a decision node, so nothing can cite them
and nothing can find what depends on them. They are described here by effect and deliberately given
no `adr-` identifier.

| Label | What it decided |
|---|---|
| D21 | Adopting 3D for the map |
| D22 | The two-domain comparison frame, since removed |
| D33 | Edge weight moves from line width to ink density |
| D34 | The layout preset set |
| D39 | Palette inversion for the dark theme |
| D40 | The glass node material, since retired |
| D51 | Retirement of the default layout preset |

The general shape of this gap is recorded in [[vinc.canon.decisions]].

## Provenance

| Source | Relation |
|---|---|
| `Vinc/store/embedded_schema.py` | The relation tables of section 4 |
| `Vinc/engines/concepts_ingest.py`, `bridges_ingest.py`, `api/fragment.py` | The three connection layers of section 5 |
| `Vinc/api/query.py` | Layout as pure functions, the traversal plans, and the couplings |
| `Vinc/app/src/surfaces/atlas3d.ts`, `atlas.ts` | The mark and level assembly measured in sections 2 and 3 |
| Hub data measurement, 2026-07-10 | Zero direct cross-domain concept edges |
| [[vinc.spec.dataplane]] | The data plane contract this study reads rather than restates |
| [[vinc.canon.vocabulary-model]] | The theme and type vocabulary model behind the open layer |
| [[vinc.spec.atlas-visual-language]] | The current visual language, which supersedes sections 2 and 3 |
| [[vinc.canon.decisions]] | Registry for the `adr-` identifiers used above |

> The document was promoted into the account graph on 2026-08-02 and its repository copy frozen at
> that point, so edits to the file stopped reaching readers. That is the state this rewrite was made
> from.
