---
doc_id: vinc.canon.terminology
layer: canon
domain: vinc/governance
authority: canon
title: Terminology, notation, and forbidden vocabulary
supersedes: vinc.a.terms
updated: 2026-08-06
tags: [canon, governance, naming, notation, forbidden-vocabulary]
---

# Terminology, notation, and forbidden vocabulary

> This document is the single source of truth for names and wording. A violation blocks the
> consistency gate. Sections 1 to 6 are the rule tables; sections 7 to 9 explain why each rule
> exists, because a rule whose reason is lost gets removed by the next person who finds it
> inconvenient.
>
> Two consumers read these tables: `Vinc/scripts/gate_forbidden.py` derives grep patterns from
> sections 4 to 6, and document review blocks any term not registered here.

## 0. Notation rules for this corpus

These apply to every document in the corpus and to every user-visible string in the product.

| Rule | Detail |
|---|---|
| Language | English. The corpus is written in English so that one reader can hold all of it. Forbidden tokens listed in sections 4 to 6 stay in their original script because they are the literal values being blocked, not prose |
| Em dash | Never. Not in documents, not in UI copy, not in design mockups. Use a colon, a comma, parentheses, or a middle dot. Owner instruction, 2026-07-22 |
| Emoji | Never in document bodies. Status glyphs belong in tables as words |
| Cross-reference | `[[doc_id]]` only, and the target must exist in this corpus. Section references use `§` in prose, never inside link brackets |

## 1. Product and system identifiers

| Item | Canonical | Forbidden | Note |
|---|---|---|---|
| Product name | **Vinc** | weg-memory, memory-standalone | From Latin *vinculum*, a bond or connecting link |
| Implementation repo | `Vinculums/Vinc` | any other name | Organization renamed from `Vinclums` on 2026-08-05 |
| Docs repo | `Vinculums/Vinc-docs` | | |
| Homepage repo | `Vinculums/Vinc-homepage` | | Site and account backend |
| Release repo | `Vinculums/Vincs-app` | | Public binaries only |
| CLI entry point | `vinc` | `memory`, `weg-memory` | Subcommands in section 2 |
| Data directory | `vinc/` | `weg-memory/` | Platform convention path |
| Python distribution | `vinc` | | Not published to PyPI, desktop bundle only |
| doc_id prefix | `vinc` | | |

## 2. CLI subcommands

| Subcommand | Meaning | Note |
|---|---|---|
| `vinc ingest` | Load a source corpus, parse and embed | Asynchronous job |
| `vinc recall` | Browse records | Read only. Do not extend into memory narrative in UI copy, see section 5 |
| `vinc decide` | Traverse relations and mine paths | |
| `vinc search` | Hybrid vector and BM25 search | Scope filter applies |
| `vinc domains` | Dataset distribution | Open set, never a hardcoded list |
| `vinc activate` | Export a pack | Includes preference and learner signals, [[vinc.canon.share-pack]] |
| `vinc backup` | Consistent snapshot of the data directory | Asynchronous job |

`recall` and `decide` are engine lineage identifiers. Using them in user-visible copy as
"recall your memories" or "learn your decisions" violates section 5.

## 3. Data model vocabulary

Field names are frozen because renaming them would break the MERGE keys, the schema, and every
migration across seven engines. The product meaning is redefined instead. See section 8.

| Field | Canonical meaning | Forbidden meaning |
|---|---|---|
| `owner_id` | Account scope: `user:{id}`, or `team:{uuid}` for a hosted team | "learner", "owner of personal memories" |
| `domain` | Dataset partition, free string | "field of study", "subject" |
| `tenant_id` | Constant `tenant:local`, never surfaced | multi-tenancy surface |
| `Episode` | A record, the unit of ingestion | "memory of a learning session" |
| `LearnerPattern` | Disabled by default | any UI surface |

`owner_id` holds an opaque string. That opacity is why team-shared graphs work with zero schema
change, see [[vinc.canon.account-policy]].

### 3.1 Theme and type vocabulary

| Term | Canonical meaning | Note |
|---|---|---|
| **Theme** | The interpretive frame a user builds over a subject: guiding questions, type vocabulary, nodes, edges. The unit of ownership, packing, sharing, and scope | Distinct from Subject. The world gives you a subject, you author a theme. See [[vinc.canon.vocabulary-model]] |
| **Type vocabulary** | What counts as a node or edge kind within one theme. The fingerprint of that theme. Metadata outside the graph | Not a fixed global enum |
| `authored` | A type the user has ratified | Travels in packs and exports |
| `proposed` | A provisional type, either a system draft or picked up during ingestion | Never travels in packs |
| provenance | `user`, `system_draft`, or `ingest_proposed` | Makes ownership legible |
| ratify | The act of confirming proposed into authored | The system proposes, only the user confirms |
| `Entity` / `TYPED` | Physical carrier node and relation for user-authored types outside the four legacy kinds | Stamped with `theme_id`, see [[vinc.spec.dataplane]] |

## 4. Forbidden: metering code (F1)

**Hard rule. Zero occurrences across the repository.** F1 blocks the import of usage-metering code
from the source project.

> **Scope precision.** F1 targets billing and metering **code identifiers**. The English words
> `billing` and `quota` are not themselves forbidden: `domain` is a free string, so a user who
> ingests billing documents legitimately has "billing" in their data. `plan.kind` is excluded too
> because it collides with the canonical `QueryPlan.kind`. A naive word grep produces false
> positives against the data plane's own vocabulary.
>
> **Revision, 2026-08-02.** Four payment copy tokens were released from the ban. The stated reason
> for banning them was that Vinc had no billing surface of its own, and [[vinc.canon.account-policy]]
> removed that premise by introducing paid plans. The execution gate had **already** dropped these
> four from its token list, so this revision also brings the document into line with what the gate
> actually does. The metering identifiers F1-a through F1-c remain permanently forbidden, and they
> are the substance of F1.

| # | Forbidden token (regex) | Reason | State |
|---|---|---|---|
| F1-a | `usage_record` | usage metering record | **forbidden, permanent** |
| F1-b | `token_margin` | token margin metering | **forbidden, permanent** |
| F1-c | `\bUsageRecord\b` | metering class | **forbidden, permanent** |
| F1-d | `과금` | (former) no billing surface | released, 2026-08-02 |
| F1-e | `결제` | (former) payment UI copy | released, 2026-08-02 |
| F1-f | `구독` | (former) subscription UI copy | released, 2026-08-02 |
| F1-g | `요금제` | (former) plan UI copy | released, 2026-08-02 |

> **Allowed, not a false positive:** using `billing` or `quota` as a domain, tag, or query value,
> which is user data, and accessing `QueryPlan.kind` or `plan.kind`, which is canonical code.
> If the metering identifiers never enter, the metering model has no path to follow them in.
>
> **What replaced the ban on the released four.** Payment copy is now a quality concern, not a gate:
> no em dash in user-visible strings, and price, refund, and cancellation terms must agree with
> [[vinc.canon.account-policy]]. Code review catches this, not grep.
>
> **Naming for new billing surfaces.** Do not name billing tables or classes in the `usage_record`
> family, which would violate F1-a through F1-c. The adopted names are `subscriptions`,
> `webhook_events`, and `charge_attempts`.

## 5. Forbidden: learning-framing copy (F2)

**Hard rule. Zero occurrences in UI strings.** The code identifier `learner_*` is allowed until
the corresponding decision is executed.

| # | Forbidden token | Canonical replacement |
|---|---|---|
| F2-a | `나의 기억` | "my records", "dataset" |
| F2-b | `학습을` (as in "build up your learning") | "structure your data" |
| F2-c | `배움` | "pattern extraction", "mining" |
| F2-d | any UI string framing memory, learning, or study | Vinc is a data mining tool |

## 6. Forbidden: legacy names (F7)

**Hard rule. Zero occurrences in user-visible identifiers:** UI strings, install paths, package
names, window titles, documents.

| # | Forbidden token | Canonical replacement | Exception |
|---|---|---|---|
| F7-a | `weg-memory` | `vinc` | none |
| F7-b | `weg-memory/` as data directory | `vinc/` | none |
| F7-c | `weg-9000-memory` | none, Vinc has no such surface | **allowed only in code provenance comments** |
| F7-d | `Vinclums` | `Vinculums` | none. The organization was renamed on 2026-08-05 and the old name now returns HTTP 301 from the GitHub API, which breaks any caller that does not follow redirects |

`weg-9000-memory` is the name of the repository this code was extracted from, so it appears in
lineage comments only. It must never reach a window title, a path, a package name, or a document body.

## 7. Why the naming bindings live in the canon layer

The rule that memory and learning narrative is banned across all user-visible identifiers only has
force if a deterministic gate can read a single list. Placing that list here means three gates read
the same source: `gate_forbidden.py` derives grep patterns from sections 4 to 6, document review
blocks unregistered terms so the rule propagates down to screen specs, and the rewrite check
verifies that canonical terms are actually used in prose.

> **A document and its gate can drift apart.** F1-d through F1-g were removed from the gate first
> and stayed in the document for a long time, discovered on 2026-08-02. "The gate consumes this
> table" is design intent, not automatic synchronization. Change one and you must change the other
> in the same piece of work. To compare: the rule count from `python Vinc/scripts/gate_forbidden.py --json`
> against the number of active rows in sections 4 to 6.

## 8. Why field names stay while their meaning changes

Renaming `owner_id` or `domain` would break MERGE keys, schema, and migrations across all seven
engines. Keeping the field names confines the engine diff to the driver acquisition lines.
So the identifiers are frozen and only the product meaning is redefined by the section 3 table.
The user sees "local account", not "learner", and "dataset partition", not "field of study".
The code does not change, the UI carries the mining narrative.

That choice paid a dividend in the team feature: because `owner_id` is an opaque string, a
team-shared graph works with zero schema change. See [[vinc.canon.account-policy]].

## 9. Why legacy names are a separate gate from F1 and F2

F1 and F2 block the import of a concept or a narrative. F7 blocks a name that survived. They need
different checks. F7 must distinguish `weg-9000-memory`, which is allowed as code provenance, from
`weg-memory`, which is a forbidden user surface, so it is a boundary judgment about visibility
rather than a substring match. The same applies to `Vinclums`: the string is correct inside a
historical commit message and wrong in anything a caller resolves today.

## Provenance

| Source | Relation |
|---|---|
| `weg-9000-docs` design SSoT | This document is the vocabulary form of that design's naming bindings and framing shift. The dependency was previously declared as `inherits_from: weg.c.memory-standalone`, which pointed outside this corpus and could never resolve. The declaration is dropped and the provenance is recorded here in prose instead |
| `weg-9000-docs/WORK-ORDERS/WO-MEM-SA-001.md` | Origin of the F1, F2, and F7 hard rules |
| `Vinculums/Vinc/scripts/gate_forbidden.py` | The implementation that consumes sections 4 to 6 |
| [[vinc.canon.account-policy]] | Basis for the 2026-08-02 revision to section 4 |
