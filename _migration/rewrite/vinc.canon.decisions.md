---
doc_id: vinc.canon.decisions
layer: canon
domain: vinc/governance
authority: canon
title: Decision registry
updated: 2026-08-06
inherits_from: [vinc.canon.terminology]
tags: [canon, governance, adr, registry]
---

# Decision registry

> An index of the architecture decisions **that exist as nodes**, not a copy of their reasoning.
> Each row points at the ADR node and at the document where the reasoning lives. Adding a decision
> adds a row here and nowhere else.
>
> **It is not yet a complete register, and saying so is the point of this paragraph.** See section
> "What this register does not contain".

## How decisions are identified

`adr-{domain}-{NNN}`, zero padded to three digits. The number is the original series number, so the
thread back to older citations is unbroken: what was cited as D45 is `adr-engineering-045`.

The series letter became the domain, because that is what it always was. `d` meant design, `gm`
meant governance, `sec` meant security, `ap` meant account policy. Promoting it to the domain makes
the classification visible instead of hidden inside a string. Because the series split across
domains, preserving the numbers produces no collisions.

The human label stays on the node as `props.label`, so a person who remembers "D45" can still find it.

**Gaps are left as gaps.** `governance-006` and `security-002` have no decision. Pulling later
numbers down to close a gap would make historical citations point at the wrong decision.

### What a decision is, structurally

A decision is two things and the graph now separates them:

- the **rule** that is in force, as a Concept node, which is what citations point at
- the **event** of deciding, as an Episode carrying the date, linked by `about`

Where the date is unknown the Episode simply does not exist. **No date has been invented.** Of the
decisions below, roughly fifteen have a recorded date. The rest carry the rule only, and the missing
provenance is left visible so it can be filled in when a source turns up.

## Design

| ADR | Label | Decision |
|---|---|---|
| `adr-design-007` | D7 | Single passphrase-free profile by default. Login is a Settings opt-in, not a first screen |
| `adr-design-015` | D15 | Surface tree is five surfaces plus Settings. Later folded to three routed surfaces by D56 |
| `adr-design-016` | D16 | Atlas three-level semantic zoom: domain map, domain lens, node focus |
| `adr-design-017` | D17 | Graph layout is precomputed by the sidecar. The UI only consumes coordinates |
| `adr-design-018` | D18 | Verb-first English copy system. No memory or learning narrative |
| `adr-design-026` | D26 | Icon rail navigation |
| `adr-design-031` | D31 | Two layers of control over Atlas expression: the open layer belongs to the user, the closed layer to the spec |
| `adr-design-032` | D32 | Map-of-content structure discipline: hub promotion, orphan minimization, focus fade |
| `adr-design-035` | D35 | Primitive reassignment per node kind |
| `adr-design-037` | D37 | Glass confined to floating chrome. Content planes stay flat. **Still in force**, see the note under `adr-design-077` |
| `adr-design-038` | D38 | Dark by default, developer detail hidden from surface copy |
| `adr-design-042` | D42 | One build ships both English and Korean copy decks |
| `adr-design-043` | D43 | Atlas planar views. See the split note below |
| `adr-design-046` | D46 | Independent 2D renderer rather than a projection of the 3D one |
| `adr-design-047` | D47 | Three-column Sankey for the totals view |
| `adr-design-048` | D48-T | Own tooltip implementation, for WCAG 1.4.13 conformance |
| `adr-design-053` | D53 | Retire the flow view. Its only entry into the relation evidence panel moved to clicking a 2D line |
| `adr-design-054` | D54 | Ledger gains a source column, folding both document ingress paths into one cell |
| `adr-design-056` | D56 | Single top-bar shell. The left rail is gone |
| `adr-design-057` | D57 | Search is the primary entry point for retrieval |
| `adr-design-063` | D63 | Specification for anything that floats above the screen, the sheet half of a split. See the note below |
| `adr-design-084` | D63 | Path-preserving ribbon, the other half of that split, renumbered because both halves are design |
| `adr-design-068` | D68 | Barycenter lode placement for the alluvial totals view |
| `adr-design-069` | D69 | Document tree returns to the sidebar |
| `adr-design-070` | D70 | Focus shows a related-document card |
| `adr-design-071` | D71 | Tools modal and chrome rearrangement |
| `adr-design-072` | D72 | Two time axes |
| `adr-design-074` | D74 | Coordinates carry hierarchy, with explosion placement |
| `adr-design-075` | D75 | Stop Atlas marks vanishing at distance |
| `adr-design-076` | D76 | Dual contrast gate for Atlas |
| `adr-design-077` | D77 | Retire the **node** glass that had leaked into the content plane, and clear two dead channels. It **restores** `adr-design-037` rather than reversing it: chrome glass was explicitly kept. Reading it as retiring glass everywhere inverts what it decided |
| `adr-design-078` | D78 | Type maps to a closed set of five colors |
| `adr-design-079` | D79 | Restore line width rendering |
| `adr-design-080` | D80 | Local graph as the default entry |
| `adr-design-081` | D-vis-0 | Render pipeline audit |
| `adr-design-082` | D-vis-c | Label legibility and user control |
| `adr-design-083` | D-vis-d | Semantic zoom, shape, and aggregation |

Reasoning for design decisions lives in [[vinc.spec.atlas-visual-language]],
[[vinc.spec.atlas-views]], [[vinc.spec.surface-tree]], and the research documents they cite.

## Governance

| ADR | Label | Decision |
|---|---|---|
| `adr-governance-001` | GM-1 | Adopt GRAPH.md as the assistant writing contract |
| `adr-governance-002` | GM-2 | Placement spans file, store, and read tools |
| `adr-governance-003` | GM-3 | Authority is declared per section, not per document |
| `adr-governance-004` | GM-4 | `graph-md build` and `check` commands |
| `adr-governance-005` | GM-5 | Docs site gets an independent four-page section |
| `adr-governance-007` | GM-7 | The conventions document is excluded from user surfaces |
| `adr-governance-008` | GM-8 | Control sheet |
| `adr-governance-009` | GM-9 | Child vector token budget |
| `adr-governance-010` | GM-10 | Frontmatter promoted to chrome |
| `adr-governance-011` | GM-11 | GRAPH.md becomes a theme |
| `adr-governance-012` | GM-12 | Domain separation at Atlas L0 |
| `adr-governance-013` | GM-13 | Re-embedding verification and a status badge |
| `adr-governance-014` | GM-14 | Cursor pagination for search |
| `adr-governance-015` | GM-15 | The edge resolver queries the store |

Reasoning lives in [[vinc.canon.graph-conventions]].

## Engineering

| ADR | Label | Decision |
|---|---|---|
| `adr-engineering-004` | D4 | A pack carries preference signals in full |
| `adr-engineering-006` | D6 | Pack export excludes the model by default |
| `adr-engineering-011` | D11 | MCP tool contract and the vocabulary graph |
| `adr-engineering-014` | D14 | A single embedded driver. Reversed once, see the note below |
| `adr-engineering-019` | D19 | Share Pack for account-to-account transfer |
| `adr-engineering-043` | D43 | Data home defaults to `~/.vinc`. See the split note below |
| `adr-engineering-045` | D45 | Chunk is the source of truth for document text |
| `adr-engineering-055` | D55 | The pack file becomes a zip container, readable in both shapes |

Reasoning lives in [[vinc.canon.principles]], [[vinc.canon.chunk-ssot]],
[[vinc.canon.share-pack]], and [[vinc.canon.mcp-contract]].

## Security

| ADR | Label | Decision |
|---|---|---|
| `adr-security-001` | SEC-1 | Owner isolation on reads |
| `adr-security-003` | SEC-3 | Owner isolation on writes |
| `adr-security-101` | B1 | Physical repository separation |
| `adr-security-102` | B2 | Pin the public key only |

The B series was renumbered from 101 because its original numbers collided with the SEC series once
both moved into the same domain. The label preserves the original.

> There is no canon document describing the security model. Four decisions exist with nothing
> stating the model they belong to. [[vinc.ops.risk-audit-2026-08-05]] is the closest thing and it
> is an audit, not a model. This gap is recorded rather than hidden.

## Infrastructure

| ADR | Label | Decision |
|---|---|---|
| `adr-infra-036` | D36 | Opt-in remote MCP profile |
| `adr-infra-044` | D44 | Zero-cost code signing path. **Superseded**, see [[vinc.plan.code-signing]] |

## Product

| ADR | Label | Decision |
|---|---|---|
| `adr-product-001` | AP-1 | Account policy: personal and team tiers, with a paid plan |

Reasoning lives in [[vinc.canon.account-policy]].

## AI

| ADR | Label | Decision |
|---|---|---|
| `adr-ai-073` | D73 | Two-mode search bar, adding Ask without an LLM |

Reasoning lives in [[vinc.research.no-llm-chat]].

## What this register does not contain

Measured 2026-08-06, during the migration that produced this document.

The corpus cites **74 distinct decisions in the D series**. The graph holds nodes for **42** of them.
**Thirty-one exist only as prose inside a document**, with no node, no id, and therefore no way to
find what depends on them:

```
D1 D2 D3 D5 D8 D10 D20 D21 D22 D23 D24 D25 D27 D28 D29 D30
D33 D34 D39 D40 D41 D49 D50 D51 D52 D58 D62 D64 D65 D66 D67
```

**How the gap opened.** The register fell behind and the project kept deciding. One of the source
documents says so plainly: it notes that the register had stopped at D44 and that registering D45
was "a follow-up task." It stayed a follow-up. Later decisions, especially revisions to earlier ones,
were written as sub-entries inside whichever document they affected, using a notation for "revision
of D34" rather than a decision of their own. Those revisions are often the operative ruling today,
because a revision is what happens when the first answer turned out wrong.

**Why this matters more than a bookkeeping complaint.** A decision with no node cannot be cited by a
node, cannot appear in a traversal, and cannot be found by asking the graph what a change would
touch. Half the project's design rulings were, in that sense, invisible to the tool built to make
rulings visible.

**Not closed here, deliberately.** Creating thirty-one nodes from prose would mean inventing titles,
dates, and scope for decisions nobody has re-read. That is exactly the kind of confident
reconstruction this migration exists to stop. The gap is recorded so it can be closed by reading,
one decision at a time, by someone who was there.

> `D48` appears absent from the list above but is present in the register under the label `D48-T`,
> which carried a one-off suffix. It is `adr-design-048`.

## Anomalies carried over from the old numbering

The old scheme drifted in four ways. The graph had noticed: a node recorded the numbering fork
before this migration began.

| Anomaly | Resolution |
|---|---|
| **Double registration.** Two visibility decisions were filed twice, once as integers and once in a lettered sub-series | Merged. The lettered ids are gone, the integers survive as `adr-design-075` and `adr-design-076` |
| **No integer number.** Three decisions in the lettered sub-series never got integers | Numbered from the top of the design series: 081, 082, 083 |
| **Number used twice.** D43 and D63 each name two unrelated decisions | Split. D43 divides cleanly because its two halves belong to different domains, so both keep the number. D63 remains open: both halves look like design, and the second needs a fresh number |
| **A registry filed as a decision.** `dec-registry` was the decision log itself | Moved out of the ADR namespace to `concept:decision-registry`, which this document describes |

### D14, the one reversal

D14 first chose one embedded driver and later chose a different one from the same lineage, after the
replacement was proven on three fronts. Both halves are the same decision id because it is one
question, answered twice. The relevant fact for architecture is not which driver won but that the
swap cost one file, because only one place is coupled to the driver name. See
[[vinc.canon.principles]].
