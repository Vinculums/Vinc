---
doc_id: vinc.research.ux-restructure
layer: research
domain: vinc/design
authority: research
title: "Restructuring the interface: the surface audit that produced the Atlas"
supersedes: vinc.d.ux-research
updated: 2026-08-07
inherits_from: [vinc.spec.surface-tree, vinc.canon.terminology]
tags: [research, design, atlas, surfaces, copy, semantic-zoom]
---

# Restructuring the interface: the surface audit that produced the Atlas

> Research input, dated 2026-07-10. This is where the Atlas came from, where the surface tree came
> from, and where the copy voice came from. Five of its proposals were approved the same day and are
> now `adr-design-007`, `adr-design-015`, `adr-design-016`, `adr-design-017` and `adr-design-018`.
>
> **It is an input, not a specification.** What is built today is in [[vinc.spec.surface-tree]],
> [[vinc.spec.surface-atlas]] and [[vinc.spec.ui-copy]], and in several places what is built is no
> longer what this study proposed. Section 6 records those divergences rather than quietly editing
> them out, because the reasoning that was overturned is the part a later reader needs.

## 1. What prompted it

Four owner observations, 2026-07-10:

1. There are surfaces in the current eight-screen tree that cost more than they return.
2. The graph is hard to read. It is difficult to get an insight out of it.
3. There is no way to move fluidly between domains, or to follow a coupling from one into another.
4. The interface should be English, and English written as copy rather than as exposed identifiers.

The starting point of the whole study was one asymmetry. The common criticism of the best known
markdown graph view is that it has neither node types nor edge types, so it is a topology picture
rather than a working tool: past a few hundred notes it becomes sightseeing. **Vinc already had
typed nodes, typed edges and a cross-domain flag in its data plane.** It had the asset that answers
the criticism, and the screen was not using it.

That sentence turned out to have a long life. It is restated almost verbatim in
[[vinc.research.atlas-mark]] two months later, still unresolved, and the verdict in
[[vinc.research.atlas-mark-verdict]] calls it the project's most expensive unused advantage.

## 2. The surface audit

Eight screens, judged by one question: what is lost if this is removed.

| Screen | Verdict | Reason |
|---|---|---|
| Login | Demote to a Settings opt-in | A single-user offline local application opened on account creation. First-run friction, at its most expensive point |
| Onboarding | Absorb into the first-run empty state | The shared convention already forces empty copy to carry a real command. A separate onboarding screen duplicates it |
| Intake | Keep | The entry surface |
| Records | Candidate for merging | Browsing was split across three screens with no path from a search result to the graph, or from a distribution to a subgraph. The structural cause of observation 3 |
| Dataset distribution | Absorb into the map's top zoom level | Domain distribution is not a chart screen. Its natural habitat is the outermost level of the graph, and the domain census is its data source |
| Graph mining | Redesign. This is the study's core | A single flat view with a filter form on the left, no zoom levels, no domain lens, no path between couplings |
| Search | Keep, and add a handoff to the map | A list result has its own value. Every row gets an action that opens the map at that node |
| Packs | Keep | The output surface |

The proposed tree was five surfaces plus a Settings popover. **Eight to five with nothing removed**:
login, onboarding and distribution do not disappear, they move somewhere cheaper. This contradicted
a standing declaration that the eight-screen model was fixed, which is why it was raised as a
decision rather than applied.

## 3. What the external reading contributed

Surveyed 2026-07, from information visualization sources and graph interface practice.

- **Overview first, zoom and filter, details on demand.** Not every node on one plane. Start from a
  summary, narrow by zoom and filter, open detail when asked. Large network visualization research
  agrees on one thing: a community-level or cluster-level overview has to guide the reader to a
  region of interest.
- **The legibility ceiling is real.** Past a few hundred nodes, undifferentiated rendering stops
  being navigation. The answer is not to render more, it is to **change the level of aggregation**.
- **Only a graph that uses its types becomes a working tool.** Without node and edge types, state,
  and faceted filters, a graph view is a topology picture.
- **A WebGL renderer is good at scale, and force-directed layout is expensive and nondeterministic.**
  Interaction grammar comes free with the renderer, so the interface problem is not rendering. It is
  precomputing the layout and designing the zoom levels.

That last point is the origin of `adr-design-017`, which is the one clause from this study that
survived every subsequent layout revision untouched.

## 4. The Atlas proposal

The name is a metaphor with a claim in it: an atlas is several maps at different scales, which is
exactly what a semantic zoom is.

### 4.1 Three levels

| Level | Nodes | Edges | Question answered |
|---|---|---|---|
| Domain atlas | domains as supernodes, sized by record count | couplings between domains, weighted by crossing count | how is my corpus divided, and where is it joined |
| Domain lens | the concepts and episodes of one domain | relations inside it, plus **edge gateways**: nodes that couple outward, drawn at the boundary, translucent | what is inside this partition and where does it leak |
| Node focus | one node's neighborhood | its adjacent edges | what is this bound to |

The outermost level is always summarized to the number of domains, which is a small number
regardless of corpus size, so the legibility ceiling is avoided structurally rather than fought.

**The gateway is the invention.** Inside domain A's lens, a node coupled to B sits at the edge of
the lens, oriented toward B, and one click passes through into B's lens. Walking between domains
without changing screens is the answer to observation 3.

### 4.2 The filter redesign

The existing left panel was a form: domain dropdown, tag field, depth stepper. That is the graph
version of observation 1. The replacement moved each control onto the map itself:

- The domain filter **is** the outer level. Click a domain to enter its lens instead of choosing it
  from a list.
- Tags become interactive legend chips along the bottom of the canvas.
- Depth only means anything at the innermost level, so it belongs in that level's detail rail.
- The left panel is then empty and is deleted. The canvas takes the full width.

### 4.3 What the sidecar had to provide first

| Item | Why |
|---|---|
| Precomputed layout | Force-directed layout is nondeterministic and expensive. The job queue computes coordinates on ingestion and the projection carries them. The UI only consumes |
| A domain census | Counts per domain, plus a matrix of couplings between domains, as the data source for the outer level |
| Seed and depth traversal | The same capability the assistant interface was missing. **One implementation resolves both**, which is why it was scheduled before the interface work rather than alongside it |

That last row is the study's most economical observation and it held: the traversal plan behind the
innermost Atlas level and the one behind the assistant's graph tool are the same plan. See
[[vinc.canon.mcp-contract]].

## 5. What became decisions

All five were approved by the owner on 2026-07-10.

| Now | Was | Decision |
|---|---|---|
| `adr-design-015` | D15 | Five surfaces plus Settings |
| `adr-design-016` | D16 | Three-level semantic zoom |
| `adr-design-017` | D17 | Layout precomputed in the sidecar |
| `adr-design-007` | D7 | Passphrase-free single profile by default, an already-open question that this study closed |
| `adr-design-018` | D18 | Verb-first English copy system |

## 6. What did not survive, and what it cost to find out

Recorded because a research document that only lists its wins is useless as an input.

| Proposal | What happened |
|---|---|
| Zoom gestures cross a threshold and change level | **Not built, and now explicitly rejected.** Measured 2026-08-06: every scope change is an explicit click, menu entry or address. An implicit transition changed what the map meant while the user was only trying to look closer |
| A breadcrumb prevents losing your place | Removed by `adr-design-069`. A permanently visible document tree already marks the active topic, and a breadcrumb repeating it spends the scarcest row on the screen twice. The cost is real: back became a key rather than a target, which is why first-run coach marks exist |
| Every result row gets a `Show in Atlas` action | Superseded by `adr-design-070`. The row click itself does it, so the action row was one control explaining another |
| Tag filtering as legend chips | Built, but for a different vocabulary. The legend that became a filter under `adr-design-074` filters **edge kinds**, not tags |
| Five surfaces are the resting shape | Folded to three routed surfaces. `adr-design-015` was right that fragmented navigation was the problem, and the fold continues it rather than reversing it: the reduction moved from merging screens to moving persistent work into the shell. See [[vinc.spec.surface-tree]] |
| Cross-domain edges are distinguished by a dashed stroke and a pair of domain icons | Not built as described. Cross-domain treatment is carried by ink density in the edge kind chain |

## 7. The copy system

This section is the origin of the voice, not the inventory. The strings that ship are canon and are
inventoried in [[vinc.spec.ui-copy]]; forbidden vocabulary is in [[vinc.canon.terminology]].

### 7.1 Voice, which does not vary

- **Verb first.** Every call to action starts with a verb and promises a result. Noun labels are
  banned.
- **Contractions.** Natural register, not stiff.
- **One sentence.** Empty and error states are exactly two clauses: what happened, and what to do.
- **No learning narrative.** The whole family of memory and study words is barred. The canonical
  verbs are mine, map, trace, surface, bind, connect, ingest, export.
- **Sovereignty is brand, not a feature.** The offline identity gets a reusable line rather than a
  bullet in a list.

### 7.2 Tone, which varies by state

| State | Tone | Rule |
|---|---|---|
| Empty | Invitation | Not an absence. Always carries an action |
| Loading | Plain | Numbers first. No anthropomorphism |
| Success | Brief, then onward | Summarize the result and offer the natural next screen |
| Error | Calm, remedy first | No blame. Cause if known, then recovery. Technical codes go in a fold |

### 7.3 Surface naming, and the rejected alternatives

| Surface | Name | Rejected, and why |
|---|---|---|
| Ingestion | Intake | `Ingest` is reserved as the verb. `Import` reads as a one-off |
| Records | Records | `Recall` is forbidden vocabulary |
| Graph | Atlas | `Graph` is an exposed identifier. `Map` invites a geographic reading |
| Search | Search | The one surface where convention is the right answer |
| Export | Packs | The artifact is the surface identity. `Export` is reserved as the verb |

Two of those names are now aliases rather than surfaces, and the aliases are permanent for the
reason given in [[vinc.spec.surface-tree]].

### 7.4 Taglines

Three candidates were proposed for marketing surfaces, recommending one as primary and one as the
Atlas feature line. Final selection was folded into `adr-design-018` rather than decided here.

## Provenance

| Source | Relation |
|---|---|
| Owner observations, 2026-07-10 | The four problems this study was opened to answer |
| External survey, 2026-07 | Graph interface practice, large network visualization research, the typed-graph critique of markdown graph views, WebGL renderer documentation, and interface writing style guides |
| [[vinc.spec.surface-tree]] | What the surface tree became |
| [[vinc.spec.surface-atlas]] | What the Atlas became, including the scope levels this study named |
| [[vinc.spec.ui-copy]] | The shipped copy inventory, which supersedes the deck sketched here |
| [[vinc.research.atlas-mark-verdict]] | Picks up the unused-type-advantage observation two months later |
| [[vinc.canon.decisions]] | Registry for the `adr-` identifiers used above |
