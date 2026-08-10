---
doc_id: vinc.plan.p3-execution
layer: plan
domain: vinc/design
authority: record
title: P3, the execution plan that turned into a decision log
supersedes: vinc.c.p3-execution
updated: 2026-08-07
inherits_from: [vinc.spec.atlas-visual-language, vinc.research.desktop-shell, vinc.canon.share-pack]
tags: [plan, design, record, execution, atlas, packs, figma, gates]
---

# P3, the execution plan that turned into a decision log

> **History. Complete.** Six phases were planned, all six landed, and the plan was closed in
> mid-July 2026. What makes it worth keeping is not the phase list. It is that **ten more decisions
> arrived while the plan was running**, one after another, each triggered by the owner looking at
> what had just shipped, and this document is where several of them are recorded at all.

## 1. Two rules the plan was built on

**Scope was cut to one project.** A neighboring repository had its own backlog, and the decision was
that none of it would be executed here. Where that repository's data quality was a problem, it would
be defended **at the ingest boundary** rather than fixed at the source. This is the ordinary
discipline of not letting an execution plan become a program.

**Code first, the design file last.** By this point the code had already overtaken the design
document, so the design file was not going to be a proposal. It would be an **as-built mirror**, and
mirroring only works when the thing being mirrored has stopped moving. Every phase carried the same
gate: the forbidden-vocabulary check across both repositories, the test suite, the application build,
and a real-browser measurement rather than a screenshot of intent.

## 2. The six phases

| Phase | What it produced |
|---|---|
| A | The Atlas mark specification finished, and the renderer switched from a two-dimensional library to a three-dimensional one. Edge direction, primitive shape by node kind, monochrome discipline, and a sidecar that emits a third coordinate deterministically |
| B | Region selection by dragging, and the share sheet the three separate share paths converge on: scope summary, per-node exclusion, an explicit notice of what is **not** included, a preview, and save. The import preview that answers it, showing overlap before anything is written |
| C | Small multiples, one canvas per domain with linked highlighting, and a domain-by-domain coupling matrix |
| D | The parts of the desktop shell that a web shell could already do: dark mode, live search, and a bundled sample pack that a first-run user can import and remove in one click |
| E | Sidecar additions: a read-only promotion-candidate query, an inbox folder scan as a queued job, and search filters pushed down into the sidecar instead of being applied in the client |
| F | The design file mirror of what had shipped, with the superseded first-draft frames marked with their lineage rather than deleted |

One phase-level ruling deserves preserving because it is a design principle rather than a task:
**promotion candidates are read-only.** Computing who deserves promotion is a query; performing the
promotion goes through the existing single write surface. Adding a second write path for
convenience is how a system acquires two truths.

Phase F also recorded a limit honestly: the bitmap mirror could not be uploaded through the proxy, so
the mirror was built as vector frames instead. The plan says so rather than claiming the intended
result.

## 3. The ten follow-ups, which is the real content

Each of these arrived **the same week**, from the owner looking at a build and asking one question.
The pattern is the useful artifact: a plan that gates on a real measurement generates decisions,
because seeing the thing is what produces the objection.

| Trigger | Outcome |
|---|---|
| "Can node composition be customized in the flat view too" | Two layers of control over Atlas expression: the open layer belongs to the user, the closed layer to the specification (`adr-design-031`) |
| Three principles about hubs, orphans and focus | Map-of-content discipline, with a tie-suggestion endpoint, an unlinked filter, binding through the existing merge path, hub ranking, and a deep link into a trace (`adr-design-032`) |
| "The connecting lines are fat translucent tubes" | The cause was that specifying a line width made the renderer build cylinder meshes, which had silently violated the hairline specification already written down. Edges became hairlines with slight curvature and weight carried by density. **A follow-up question about where an edge should attach was answered by keeping center attachment and recording why**, so it would not be relitigated |
| Two reference images of grid layouts | Five layout presets as pure functions, a per-card override, and a grid of miniature three-dimensional views rendered through one context with split viewports to avoid the browser's context ceiling. Three of the reference layouts were **rejected for having no semantics** to carry |
| "The hexagonal prism looks bulky" | Primitives reassigned per node kind (`adr-design-035`) |
| Remote access and floating chrome | The remote profile written down (`adr-infra-036`), and glass confined to floating chrome with content planes left flat (`adr-design-037`) |
| "New users should not have to configure anything" | Dark by default, and developer vocabulary removed from surface copy (`adr-design-038`) |
| "The background should be dark and the graph bright" | A theme-aware Atlas palette that inverts marks, fog, edges, labels and lighting for dark while leaving light unchanged, and a physically-shaded glass material for nodes with an environment reflection source |
| Control grammar and language | Platform-native segmented controls and button polish, then a single build carrying both English and Korean decks with sixty-one hardcoded surface strings moved into them (`adr-design-042`) |
| Packaging | Data home defaults to a dot-directory in the user's home, and the release pipeline: a frozen sidecar binary, an external-binary bundle, a file association, and a tagged three-platform build (`adr-engineering-043`) |

## 4. Decisions from this plan that were never registered

Nine of the rulings above have **no node and no identifier**. They exist only as prose, here and in
the design documents that inherited them. They are listed in the gap section of
[[vinc.canon.decisions]] and are described in section 3 rather than labelled, because inventing
identifiers for them now would be exactly the confident reconstruction this corpus is trying to stop.

The unregistered ones are the renderer switch, the import highlight, the file association, the sample
pack, the edge hairline ruling, the layout presets, the inverted dark palette, the glass node
material, and the native control grammar. Three of them were later **reversed**, which is the part
that makes the gap expensive: a reversal that cannot be cited leaves the original looking current.

## 5. What is still true, and what is not

**Still in force.** The tie-suggestion tool, the promotion-candidate query and the inbox scan are all
still in the sidecar. The sample pack still ships with the application. Dark by default, the two
copy decks in one build, and the data home and release pipeline are all unchanged. Lasso selection
and the multiples and matrix frames are still in the application source.

**Reversed since.** The icon rail this plan built on was removed when the shell collapsed into a
single top bar, and a document sidebar returned later in its place. Share and import stopped being
two top-level buttons and became one tools modal. The three-dimensional renderer stayed, but the flat
view stopped being a projection of it and became an independent renderer. **The glass material on
nodes was retired**, and that retirement restored rather than reversed the ruling that confined glass
to floating chrome, which is a distinction worth stating because reading it the other way inverts
what was decided. The design file mirror this plan produced is **not among the pages the rebuilt
design file now carries**: that file was rebuilt from scratch in August. See [[vinc.plan.figma-build]].

Current descriptions live in [[vinc.spec.atlas-visual-language]], [[vinc.spec.atlas-views]],
[[vinc.spec.surface-tree]], [[vinc.spec.glass-language]] and [[vinc.spec.surface-packs]].

## 6. The snapshot problem this document had

The original carried a "current position" section that had been true when written and was false
within a week, and someone had to bolt a warning onto it saying that the completion record above
superseded everything below.

That is a structural fault, not an editing slip: **a document that records both a plan and its
progress will always contain one stale half.** The correct shape is what this rewrite does, which is
to write the whole thing in the past tense once it is over. Progress belongs in a ledger with dates,
and a ledger is only readable when nothing around it is pretending to be current.

## Provenance

| Source | Relation |
|---|---|
| [[vinc.spec.atlas-visual-language]] | Where the mark and edge rulings live now |
| [[vinc.spec.atlas-views]] | The view work this plan started, as it stands now |
| [[vinc.spec.surface-packs]] | The share and import flows built in phase B |
| [[vinc.spec.glass-language]] | The glass ruling and its later restoration |
| [[vinc.canon.share-pack]] | The pack contract phase B extended |
| [[vinc.canon.decisions]] | The register, including the decisions from this plan that never entered it |
| [[vinc.plan.p5-remote-rollout]] | The successor plan, which executed what phase D deferred |
