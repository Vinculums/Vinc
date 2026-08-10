---
doc_id: vinc.spec.product-overview
layer: spec
domain: vinc/product
authority: spec
title: Vinc, product overview and corpus index
supersedes: d2b6633ee1151a272
updated: 2026-08-06
inherits_from: [vinc.canon.terminology]
tags: [spec, product, overview, index, readme]
---

# Vinc, product overview and corpus index

> The entry point. What the product is in one page, and where every other document in this corpus
> lives. It owns no contract: everything below is a pointer, and where a pointer and a claim
> disagree, the pointer wins.
>
> This replaces the repository readme, which had been ingested under a generated identifier and was
> the only document in the corpus outside the naming scheme. Section 8 lists what it asserted that
> had stopped being true, with the evidence for each correction.

## 1. What Vinc is

A knowledge graph you build from your own material. Source text is kept as the canonical record, so
every concept and every relation can point back at the sentence it came from and at the offset in
the original where that sentence sits. Nothing in the product asks you to trust an extraction you
cannot check.

**The browser and the desktop are two surfaces of one tool, not two products.** They compile from
the same frontend source against the same graph contract. The web build authenticates with an
account session and works on a hosted graph; the desktop build talks to a local process and works on
this machine's graph. Same screens, same operations, same payloads.

The desktop side is offline-first in the strict sense: ingesting text, structuring it into a graph,
exploring, searching, extracting patterns and exporting a pack all work immediately after
installation, with no external service, no API key and no server. Signing in is optional and adds
three things: synchronizing selected topics with an account, working from the browser or another
machine, and publishing a pack to the public gallery.

### 1.1 The name, and one narrative constraint

The product name comes from the Latin *vinculum*, a bond or connecting link, which is also the
origin of the organization name `Vinculums`.

Vinc is a data mining tool and **does not use a memory or learning narrative** anywhere a user can
see. That is not stylistic preference: the vocabulary is enforced by a gate in continuous
integration, and [[vinc.canon.terminology]] is the list it reads.

## 2. How it is put together

Two axes. The native desktop, which is the default and works alone, and the hosted surface, which is
opt-in.

**Native desktop.** A shell process owns the window, the local session and the file dialogs, and
supervises a separate process that owns the graph. Control travels over inter-process calls, data
travels over loopback HTTP with a session token, and the graph is an embedded store behind one
interface so the driver is swappable at one file. Everything lives under a single data directory, so
a backup is a folder copy (`adr-engineering-043`). The full map is [[vinc.spec.architecture]]; the
store, schema and synchronization are [[vinc.spec.dataplane]]; the invariants that hold both
together are [[vinc.canon.principles]].

**Hosted surface.** A static site carries the marketing pages, the account screens, the browser
build and the gallery. An account backend sits behind it on the same origin. A second container
holds the hosted graph and the remote MCP server. The topology is
[[vinc.spec.azure-infrastructure]], the application behavior is [[vinc.spec.web-system]], credentials
and mail are [[vinc.spec.email-subsystem]], and the tier and team policy is
[[vinc.canon.account-policy]].

**The boundary between them is the single most misread thing in this system.** The prohibition on
billing, external authentication and learning narrative, and the promise of no external round trip,
are properties of the **native** binary. The native app never calls the account API. The web build
sits outside that boundary on purpose and authenticates against an account. A claim about offline
behavior is meaningless until you know which build it is about.

## 3. The second front door

Vinc exposes the graph, and since document canon shipped the source text itself, through an MCP
server, so a host can read and write directly from a conversation (`adr-engineering-011`).

**Nineteen tools: thirteen read and six write**, with `vinc_mcp/tools.py` as the registry that
decides. The write surface is deliberately more than one tool, because merging, deleting a node,
deleting an edge, folding two nodes together and writing a document are different acts with
different consequences.

Vocabulary management is **not** among them and stays on HTTP only. An MCP host may propose a type
and have it recorded as proposed; ratifying one is a human act on a different surface. The model is
[[vinc.canon.vocabulary-model]].

The tool contract and the fragment schema are [[vinc.canon.mcp-contract]]. The server, its
transports and its lifecycle are [[vinc.spec.mcp-server]]. The opt-in remote profile, its
authorization and its hardening for directory submission are [[vinc.spec.remote-profile]].

## 4. Gates

Four checks stand between a change and a release, and all four run in continuous integration rather
than depending on someone remembering them.

| Check | What it refuses |
|---|---|
| `scripts/gate_forbidden.py` | any forbidden identifier reaching a user-visible surface. Zero tolerated |
| `scripts/version_sync.py --check` | a version number that disagrees across the files that carry it |
| `verify.py` | the test suite, including plan purity with no store present |
| `e2e_live.py --backend embedded` | an end-to-end failure against the real embedded backend |

A contrast gate for the map's palette runs beside them.

**The version check is a hard release gate**, wired into the release workflow to compare the tag
against the files. It exists because the failure it prevents is silent: the update manifest takes
its version from the tag while the client takes its own version from a configuration file, so
tagging without updating the file produces an installed build that still reports the old version,
and the update prompt then repeats forever.

## 5. Release state

**v0.7.4, released 2026-08-06.** Distribution is through the releases page and the site.
[[vinc.release.changelog]] is the developer-facing record and the authority for what shipped when;
this document does not mirror it, because a mirrored release list is the fastest-decaying thing a
readme can carry, and the version line in the predecessor of this document proved it by falling
seven releases behind.

The arc, for orientation only: bootstrap, then the port of the engine, then packaging, then the
hosted rollout, then the graph structure repairs, then document canon, then the shell consolidation
and the map's view work.

## 6. The corpus

Fifty-four documents. Each one is `vinc.{layer}.{name}`, and the layer says what kind of thing it is
rather than how finished it is.

| Layer | Meaning |
|---|---|
| `canon` | contracts and invariants. When one changes, things below it change |
| `spec` | as-built. Should be checkable against code |
| `research` | inputs and studies. Not to be cited as conclusions |
| `ops` | runbooks, incidents, audits |
| `plan` | execution plans |
| `release` | release records |

Beside the layer, each document declares an `authority`, one of four closed values: `canon`, `spec`,
`research`, `record`. It usually agrees with the layer and deliberately need not. A runbook sits in
`ops` and is as-built, so its authority is `spec`; a plan that was carried out and reversed sits in
`plan` and its authority is `record`, meaning it was true at the time.

**Authority is the only such axis.** The old `status` field was abolished: nobody updated it, so it
had stopped carrying signal, and a state that decays is worse than no field.

Domains are seven: `design`, `engineering`, `infra`, `product`, `governance`, `ai`, `security`.

### 6.1 Contracts

| Document | Domain | Holds |
|---|---|---|
| [[vinc.canon.terminology]] | governance | Names, notation, and the forbidden vocabulary the gate reads |
| [[vinc.canon.principles]] | engineering | Architecture invariants, the embedded backend contract, dialect conformance |
| [[vinc.canon.decisions]] | governance | The register of every architecture decision. An index, not the reasoning |
| [[vinc.canon.graph-conventions]] | governance | The writing contract an assistant reads before it writes to the graph |
| [[vinc.canon.vocabulary-model]] | governance | Where themes and user-authored types live, and why not in the graph |
| [[vinc.canon.chunk-ssot]] | engineering | Source text is canonical in the store, lossless and restorable |
| [[vinc.canon.share-pack]] | engineering | The account-to-account transfer format |
| [[vinc.canon.mcp-contract]] | engineering | The tool contract and fragment schema a host writes against |
| [[vinc.canon.account-policy]] | product | Tiers, the topic limit, the team model, billing |

### 6.2 As built

| Document | Domain | Holds |
|---|---|---|
| [[vinc.spec.architecture]] | engineering | One contract, two builds. Which process holds what, and which channel carries it |
| [[vinc.spec.dataplane]] | engineering | Store, schema, write and read paths, ownership isolation, synchronization |
| [[vinc.spec.mcp-server]] | engineering | The server implementation, its transports and its lifecycle |
| [[vinc.spec.web-system]] | product | Site, account backend, browser build, community gallery, the graph proxy |
| [[vinc.spec.email-subsystem]] | product | Credentials, sessions, mail delivery, mail identity, privacy discipline |
| [[vinc.spec.product-overview]] | product | This document |
| [[vinc.spec.azure-infrastructure]] | infra | What runs in the cloud, and the constraints that shape imposes |
| [[vinc.spec.remote-profile]] | infra | The opt-in remote connector profile |
| [[vinc.spec.surface-tree]] | design | The surfaces, the shell, and the state convention every view follows |
| [[vinc.spec.surface-atlas]] | design | The map surface |
| [[vinc.spec.surface-intake]] | design | Ingestion, and the contract that outlived the surface it was on |
| [[vinc.spec.surface-records]] | design | Document browsing, likewise absorbed |
| [[vinc.spec.surface-search]] | design | Search, and the always-on bar above it |
| [[vinc.spec.surface-packs]] | design | Where knowledge leaves and arrives |
| [[vinc.spec.settings]] | design | The settings modal |
| [[vinc.spec.atlas-visual-language]] | design | Marks, color, and the interaction grammar of the map |
| [[vinc.spec.atlas-views]] | design | The planar, timeline and totals views |
| [[vinc.spec.glass-language]] | design | Which planes are translucent, and which must never be |
| [[vinc.spec.ui-copy]] | design | Deck structure, voice, notation, and what a reviewer checks |

### 6.3 Inputs

Studies, to be read as evidence rather than as conclusions.

| Document | Domain | Holds |
|---|---|---|
| [[vinc.research.ux-restructure]] | design | Restructuring study for the interface |
| [[vinc.research.desktop-shell]] | design | The installed shell, examined |
| [[vinc.research.motion]] | design | Motion analysis. Implemented since, so a candidate for promotion to as-built |
| [[vinc.research.graph-structure]] | design | Quantitative analysis of the map's graph structure |
| [[vinc.research.atlas-visibility]] | design | Visibility redesign study |
| [[vinc.research.atlas-visibility-verification]] | design | Contrast verification against accessibility measures |
| [[vinc.research.atlas-mark]] | design | The mark language reconsidered |
| [[vinc.research.atlas-mark-verdict]] | design | The verdict, and the comparison behind it |
| [[vinc.research.sankey-alluvial]] | design | Alluvial layout applied to the totals view |
| [[vinc.research.view-redesign]] | design | Feasibility of the planar views and the Sankey rework |
| [[vinc.research.no-llm-chat]] | ai | Whether answering without a language model is achievable |
| [[vinc.research.deploy-pipeline]] | infra | Deployment pipeline options, examined and not decided |
| [[vinc.research.methodology]] | governance | A proposal for restructuring the method |

### 6.4 Operations

| Document | Domain | Holds |
|---|---|---|
| [[vinc.ops.deployment]] | infra | The runbook: surfaces, pipelines, ordering constraints, and what cannot be rolled back |
| [[vinc.ops.cost-management]] | infra | What the cloud costs, and where a bill can run away |
| [[vinc.ops.risk-audit-2026-08-05]] | security | The full risk audit, including the findings still open |
| [[vinc.ops.incident-2026-08-04-easyauth]] | infra | The platform front door that silently broke every connector key |
| [[vinc.ops.incident-2026-07-16-macos-sidecar]] | infra | The sidecar that would not start on one platform |

### 6.5 Plans and releases

| Document | Domain | Holds |
|---|---|---|
| [[vinc.plan.code-signing]] | infra | A signing plan later reversed. Kept as history |
| [[vinc.plan.embedding-pipeline]] | ai | Retrieval and embedding, against a worker that is not currently running |
| [[vinc.plan.figma-build]] | design | Resuming the design-tool build |
| [[vinc.plan.graph-conventions]] | governance | How the conventions contract was built. Complete |
| [[vinc.plan.p3-execution]] | design | An execution plan. Complete |
| [[vinc.plan.p5-remote-rollout]] | infra | The remote rollout. Complete and live |
| [[vinc.release.changelog]] | product | The developer-facing release record |
| [[vinc.release.0-7-2]] | product | One release bundle in detail |

### 6.6 What is not in the corpus

- **A security model document.** Four security decisions exist with nothing describing the model
  they belong to. [[vinc.ops.risk-audit-2026-08-05]] is the nearest thing and it is an audit, not a
  model. The gap is recorded in [[vinc.canon.decisions]] rather than hidden
- **The external design source** this system was derived from. It lives in a different project and
  was previously declared as an inheritance that could never resolve. The declaration was dropped and
  the provenance is recorded as prose in [[vinc.canon.principles]]
- **A handover note** from the earliest phases, dropped because those phases ended long ago and its
  content was entirely stale. History is held by version control and by the decision episodes

## 7. Where a repository file is the authority instead

Two documents mirror a file that ships with code, and for those the file wins: the deployment runbook
and the changelog. Anything else in this corpus is authoritative in the corpus, and the code is the
authority only for the measurements a `spec` document cites.

## 8. Corrections to the previous version

Every one of these was true when written.

| Claim | Correction | Evidence |
|---|---|---|
| the live release is v0.6.7 | v0.7.4, 2026-08-06. The line had fallen seven releases behind, which is why section 5 now points instead of listing | `Vinc/CHANGELOG.md` |
| eighteen tools, twelve of them read | nineteen, thirteen read and six write | the registry in `vinc_mcp/tools.py`, counted 2026-08-06 |
| the previous domain is kept in parallel so existing installations keep working | it is not. That zone has no address record and its host does not answer; only outbound mail records remain. Links must use the canonical domain | the mailer's own note in `account/src/services/mailer.ts`, measured 2026-07-29 |
| the desktop ships a local embedding model | the packaged desktop process excludes the embedding stack entirely, so desktop search is full-text only. See [[vinc.spec.architecture]] section 6 | the packaging specification |
| host registration is done with a `vinc` shell command | there is no such command. The package declares no console entry point. The functionality exists in the command module and behind a button in Settings | `pyproject.toml` has no script table, checked 2026-08-06 |
| the source-of-truth table pointing at file paths, two of them outside this project | replaced by section 6, which indexes the corpus by layer and domain. The external design source is recorded as prose, per section 6.6 | the migration ledger |

Two decision labels the previous version cited, one for the default embedding model and one for the
first screen, **have no entry in the decision register**. They were dropped rather than converted,
because inventing an identifier for a decision that does not exist would put a broken citation into
every document that copies it. Both facts survive without the label.

## 9. Known gaps

1. **The embedding worker has never loaded its model**, so retrieval across the whole hosted corpus
   runs on full-text search alone. See [[vinc.plan.embedding-pipeline]]
2. **The decision register has fifteen dates for sixty-eight decisions.** No date was invented; the
   rest carry the rule without the event. See [[vinc.canon.decisions]]
3. **No security model document**, section 6.6
4. **Open findings from the 2026-08-05 audit remain unfixed**, including one in the credential path
   described in [[vinc.spec.email-subsystem]] section 2.4

## Provenance

| Source | Relation |
|---|---|
| `Vinc/README.md` | the document this replaces, ingested under a generated identifier |
| `Vinc/CHANGELOG.md`, `vinc_mcp/tools.py`, `pyproject.toml`, `scripts/` | the measurements cited above |
| the migration ledger | the corpus list in section 6 |
| [[vinc.spec.architecture]] | the architecture this page summarizes |
| [[vinc.canon.terminology]] | the vocabulary rule in section 1.1 |
