---
doc_id: vinc.canon.principles
layer: canon
domain: vinc/engineering
authority: canon
title: Architecture invariants and the embedded backend contract
supersedes: vinc.b.architecture
updated: 2026-08-06
inherits_from: [vinc.canon.terminology]
tags: [canon, engineering, invariants, embedded, concurrency, dialect]
---

# Architecture invariants and the embedded backend contract

> What holds regardless of which decision was made this week. Decisions themselves live in
> [[vinc.canon.decisions]] and as ADR nodes in the graph. This document deliberately does not
> restate them: an invariant document that churns every time a decision lands is not an invariant
> document.
>
> The live code in `Vinculums/Vinc` is the authority for behavior. Where this document states a
> measurement, the measurement date is given, because a number without a date cannot be audited.

## 1. Invariants

| # | Invariant | What it binds in Vinc |
|---|---|---|
| I1 | Scope first | The owner, domain, and tenant filters apply as a **pre-filter on the search path too**, not only on graph traversal. Zero out-of-scope rows is a gate condition, not a goal |
| I2 | Plan purity | When the store is unreachable or unprovisioned, return `executed=false` with a pure plan and do not crash. `verify.py` runs first regardless of backend and needs no database |
| I3 | Atomicity is not promised | `GraphStore` does not contract multi-statement atomicity. Callers defend with idempotent MERGE keys |
| I4 | Single writer | Writes serialize through one job-queue worker. See section 2.4: this is a **correctness** rule, not a performance preference |
| I5 | Offline first | Starts with no runtime download and no API key. Embedding defaults to a local model that needs no key |

## 2. Embedded backend contract

### 2.1 One place knows the driver name

`store.backend.open_store` and `store.embedded_backend._import_driver` are the only code coupled to
the driver name. That confinement is why replacing the engine cost one file when it happened.

Current state is a single unconditional import of `ladybug`. If it is not installed, the import
returns `(None, None)` and the caller degrades the store to unreachable rather than crashing, per
I2, with the reason exposed on `/health`.

> **Retired policy.** There used to be a multi-driver fallback rail built on the fact that two
> drivers of the same lineage cannot coexist in one process, because they re-register native types.
> When the second driver was removed entirely, that rail died along with the marker file
> (`<store>.driver`) auto-detection, the safe-migration path, and per-driver sidecar build variants.
> `store/migrate.py` survived: the migration framing was stripped and it remains a general logical
> export and import tool.
>
> The coexistence problem was real. It is simply not a binding rule while there is one candidate.
> It comes back the day a second lineage fork is considered again.

### 2.2 Schema is declared first

Node and relation tables are declared before use. This is not a schema-free store. The column
specification is settled by an exhaustive survey of what the engine actually SETs. Speculative
columns are forbidden.

> **"No speculative columns" does not mean "fixed type values."** The rule blocks speculative
> *columns*, not user-authored *type values*. A user-authored type is carried as a **value** inside
> the fixed columns of `Entity{id, type, title, owner, theme_id, tags}`, so the schema stays
> pre-declared and non-speculative. The type *vocabulary* lives outside the graph entirely, as
> metadata. See [[vinc.canon.vocabulary-model]].
>
> **The exception is bounded.** It applies to the `Entity` and `TYPED` tables only.
> First, those two tables do not grow speculative columns either: columns grow only up to the
> settable ceiling of a fragment node. Second, this exception does not justify speculative columns
> anywhere else. A new user-authored type is absorbed as a `type` **value**, not as another column,
> so "let us add just one more column to Entity" is a violation of this boundary, not a use of it.

### 2.3 Search index lifecycle

Vector (HNSW) and full-text (BM25) indexes make the indexed embedding column immutable while they
exist. So the embedding job drops before loading and builds after. The job owns that order.

### 2.4 Read and write concurrency (measured 2026-07-31, previously an assumption)

**MVCC holds.** With a writer running continuously, median query latency is 1.33 ms against
1.25 ms idle. The fallback that the code had announced for the case where MVCC did not hold,
serializing readers, is therefore unnecessary and **must not be introduced**: adding it would break
the concurrency gate this measurement was taken to close.

**But there is only one write transaction.** If a query connection issues even one write, an
ordinary MERGE from the job worker is refused with `Cannot start a new write transaction` for the
duration. This is why I4 is a correctness rule rather than a throughput choice.

**"Write" is not only SET.** The test is whether the statement requires a write transaction. DML
and DDL obviously do, and so does `LOAD EXTENSION`, including re-loading an extension that is
already loaded. Reading that narrowly let three things leak out quietly:

- search reloaded extensions on every call, which failed 49 percent of searches and 26 percent of saves
- deferred index builds
- structure diagnostics, which had been pulled out of the query path in reaction and were therefore
  permanently degraded

**The rule.** A write that is genuinely unavoidable during a query goes onto the job queue through
the injected write point on `_ReaderView` (`writer=(label, fn, timeout)`). Exactly two things
qualify today: deferred index build, and graph algorithms. Before adding a third, doubt that it
really must be written during a query.

Extension loading is Database-scoped, so once per process is enough. Connections created after the
load inherit it. Load once at startup and cache.

### 2.5 Hybrid search

RRF fusion is a pure function outside the backend (`rrf_fuse`, k=60), and the query embedding is
injected, so the search code is model-agnostic.

## 3. Dialect conformance

These were measured against the previous engine of the same lineage and have **not been re-measured**
on the current one. Read them as lineage-level statements, not as settled values for the current
driver. The conformance code for each item passes the real-engine test suite
(`tests/test_embedded_backend.py`, `test_migrate_driver.py`). If lineage divergence surfaces on any
item, re-measure that item alone.

| Item | Finding and conformance | Code site |
|---|---|---|
| M4 | `type(r)` absent, use `label(r)` | `api/query.py` graph_plan |
| M3 | Native variable length `*1..d` walks and re-uses edges, so cycles explode. Guard with `* TRAIL 1..d` before the keyword range | decide_plan |
| M7 | A label-less MATCH raises a binder error when it references a property absent from some table. Match on identifiers that exist (`concept_id`, `bridge_id`, `title`) and drop `n.id` | decide_plan |
| M8 | Dates are stored as STRING because the engine writes ISO strings | schema and engine |
| P-param | This lineage rejects unused parameters, so do not pass parameters the Cypher does not use | `api/query.py`, `engines/pack_ingest.py` |
| P-listcomp | List comprehension is unsupported. Use `list_distinct(list_cat(...))` for tag union | `api/preference.py` tag_plan |

## 4. Schema type rulings

Porting away from a schema-free store surfaced DDL and SET type contradictions that the previous
store had hidden. The current DDL in `store/embedded_schema.py` is the code authority.

| Column | Settled type | Ruling |
|---|---|---|
| `Episode.completed_items` | STRING[] | The original `completed:BOOL` had neither a source nor a consumer. It was an audit error, renamed and retyped to a list of completed items. No speculative BOOL was added |
| `Episode.patterns` | STRING[] | List of discovered pattern candidates |
| `LearnerPattern.observations` | INT64 | A **count** of observations, not a list |
| (parser) | h2 and h3 both captured | Real data and templates use h3 only, so a parser that split on h2 alone blanked entire sections. Fixed with the retype |

> A full SET-against-DDL sweep across every node type was completed once. No mismatch beyond the
> three columns above.

## 5. Migration overlay

`engines/pack_ingest.py` imports a server-side pack into the embedded store. Idempotent MERGE on
`episode_id` overlays preference signals (pinned, tags, access count, last access) while structural
fields prefer re-collected values via `coalesce`. The learner overlay is out of scope because the
pack projection carries no pattern identifier.

## 6. Where decisions live

This document holds what does not change. Individual decisions live as ADR nodes in the graph and
are indexed in [[vinc.canon.decisions]]. Their reasoning lives in the specification or research
document for the subject they touch, not here.

The previous version of this document carried a table of forty-two decisions inline. That table was
a second copy of nodes that already existed in the graph, and it forced this document to be revised
every time an unrelated decision landed. Splitting them is the point of the change.

## Provenance

| Source | Relation |
|---|---|
| `weg-9000-docs` design SSoT | This document is the Vinc-instance binding of that design's invariants. The dependency was declared as `inherits_from: weg.c.memory-standalone`, which points outside this corpus and can never resolve, so it is recorded here as prose instead |
| `Vinculums/Vinc` live code | The implementation of every contract stated here: `store/`, `engines/`, `api/` |
| [[vinc.canon.terminology]] | Naming bindings and forbidden vocabulary |
| `weg-9000-memory` | Code lineage only. The name never appears on a user-visible surface |
