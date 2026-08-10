---
doc_id: vinc.spec.dataplane
layer: spec
domain: vinc/engineering
authority: spec
title: The dataplane as built, store to sync
supersedes: vinc.c.dataplane
updated: 2026-08-06
inherits_from: [vinc.canon.principles]
tags: [spec, engineering, store, schema, ingest, query, tenancy, sync, migration]
---

# The dataplane as built, store to sync

> The entry-point map for `store/`, `engines/` and `api/`: what opens the store, what the schema
> declares, which path writes, which path reads, how ownership is enforced, and how two machines
> converge. The backend contract itself (driver confinement, index lifecycle, dialect conformance,
> concurrency) is [[vinc.canon.principles]] and is referenced here rather than repeated.
>
> Counts in this document were taken from `store/embedded_schema.py` on 2026-08-06. The previous
> version stated nine node tables, nine relation tables and schema version two; all three had been
> overtaken by the code.

## 1. One place opens the store

Everything downstream sees a `GraphStore` protocol and never a driver. `store/backend.py`
`open_store` is the only opener, and `store/embedded_backend.py` is the only implementation.

The protocol is deliberately small: single-statement run, a session context manager that mimics the
shape the ingest engines were written against, provisioning, vector and full-text search, snapshot,
and close. A separate `reader_view` derives a read connection from the same database so that
queries stay responsive while the single write worker is busy.

When the store cannot be opened, whether the driver is missing, the file is locked or the database
is corrupt, `open_store` returns nothing and the caller degrades to `executed=false` with a pure
plan rather than crashing. That is invariant I2 in [[vinc.canon.principles]], and it is the reason
a health endpoint can report a broken store instead of the process dying with it.

`migrate(dim)` applies the base declaration idempotently and then the version-gated alterations, so
a fresh installation and an old one converge on the same final shape. `provision()` now delegates
to it, which means every existing caller gained migration without changing a line.

## 2. The schema is declared before use

This store is not schema-free. Tables are rendered from a single specification and applied
idempotently at boot. The column list is settled by surveying what the engines actually write:
speculative columns are forbidden, and the boundary of that rule is stated in
[[vinc.canon.principles]].

**Ten node tables.** Episode, Concept, Bridge, Domain, Record, SkillExecution, LearnerPattern,
Chunk, ChunkVec and Entity.

**Ten relation tables.** Three concept-to-concept relations plus inheritance, a bridge crossing a
domain, a bridge connecting a concept with a role, a record attaching to a concept, an episode
being about a concept, the general typed carrier between entities, and a chunk mentioning a
concept.

**Seven metadata tables**, all underscore-prefixed so they read as machinery rather than content:
schema version, sync cursors, the three-way merge base, node and edge tombstones, document
identity, and document tombstones.

Two of those tables are recent and were absent from the previous description:

- **`ChunkVec`**, added at schema version 8, carries **many vectors per chunk**. The storage chunk
  and the model input ceiling are different units, so a single vector per chunk embedded only the
  first window and silently discarded the rest, measured at 42.6 percent of Korean text and 12.3
  percent of English. Re-chunking to the model's unit would have meant rewriting the canonical text
  unit and would have cut concept links, so the chunk stays and the **vector** is split instead.
  Retrieval quality then equals re-chunking exactly, because the same pieces are encoded by the same
  model. Averaging the pieces into one vector was tried and scored **worse than the baseline**
- **`MENTIONS`** links a chunk to the concepts it names, which is what lets search return the
  neighbors of a matching chunk and not only the chunk. Its creation is deterministic, by origin
  match or title and stem token match, with the rule recorded on the edge

### 2.1 Tracked columns and derived scopes

Five labels carry synchronization tracking: Concept, Episode, Record, Bridge and Entity. Each gains
a hybrid logical clock string for tie-breaking, a revision counter for causal order, and a
per-owner monotonic cursor. Domain is a bare structural node, and Chunk, SkillExecution and
LearnerPattern are derived or log-like, so none of them is tracked.

Chunk is the interesting exclusion. It is the canonical text under [[vinc.canon.chunk-ssot]], yet it
is not on the node sync rail, because **documents synchronize on their own channel** at document
granularity (section 6.2).

The set of owner-scoped labels is **derived from the schema**, by asking which node tables declare
an owner column, rather than being maintained as a second list. That is why giving Bridge an owner
in version 2 and Chunk an owner in version 4 extended purge coverage automatically. A hand-kept
list would have been the thing that got out of date.

### 2.2 The free-property column is display and round-trip only

Free-form properties are stored as a JSON string in a `props` column. The contract is that they can
be shaped back into an object on read and re-ingested unchanged, and that is all: **there is no
property-conditional query and no index over them**. Promoting frequently used keys into real
columns is recorded as debt and deliberately not started at the current data scale.

### 2.3 The vocabulary is not in the graph

Which theme exists and which type names exist live in a JSON file beside the database file, not in
a table. A type is a frame for interpretation, not content, so editing the vocabulary must not touch
graph data. `Entity.type` holds a **value** of that vocabulary and is not its home. The model is
[[vinc.canon.vocabulary-model]].

## 3. Write paths

The batch engines under `engines/` merge their source products through a session, idempotently: an
episode ingester, a concept ingester, a bridge ingester, a skill tracker, a learner ingester, the
embedding pipeline, the mention linker, and the pack overlay.

**The live fragment path is `api/fragment.py` `merge_fragment`**, and the `/ingest/fragment` route
converges on it. It merges on **exact id match only** and makes no similarity judgment, which is the
server half of the covenant in [[vinc.canon.mcp-contract]].

The dormant engine module `engines/fragment_ingest.py` still exists as lineage. It must not be
called back into service as it stands, because it does not stamp revisions and a write through it
would break synchronization silently. The open-layer capability it once held was **ported into the
live path** precisely so that revision stamping, the ownership guard, de-duplication and partial
reporting are inherited from one write surface instead of two.

**Type routing is a table, not a gate.** The legacy kinds go to their dedicated tables; every other
user-authored type merges into the general `Entity` carrier, and an edge between two open-typed
nodes becomes a `TYPED` edge whose original type is preserved as a property. Nothing is skipped for
being unknown. Skips are reserved for shape errors, a missing id or title, and unsupported endpoint
pairs among the legacy kinds, and each one is reported as a warning with `partial` set.

**Stamping happens only on real change.** A tracked node is stamped with a new clock, an incremented
revision and the next owner sequence **only when it is new or a tracked field actually differs**.
An idempotent re-ingest stamps nothing, which is what stops re-ingestion from manufacturing
synchronization conflicts. Nodes arriving through the apply path keep the remote stamp instead.

## 4. Read surface and the degradation contract

`api/*` builds a pure plan and an executor runs it. If the store is unreachable, unprovisioned or
the dialect is unsupported, the plan comes back with `executed=false` and no exception. Scope
filters apply **on the search path as a pre-filter too**, not only on traversal, which is invariant
I1.

| Surface | Site | Character |
|---|---|---|
| recall, domains, learner, graph, decide | `api/query.py` | plan builder, then execute, then shape |
| search | `api/query.py` | full-text plus, when a query vector is injected, vector search, fused by a pure function outside the backend |
| preference: pin, tag, touch | `api/preference.py` | write signals; tag unions through a list function because list comprehension is unsupported |
| preference ranking | `api/query.py` | a pure function outside the backend. No scoring expression inside the query language |
| activate | `api/activate.py` | export owned data as a portable pack |
| delete, delete edge, merge nodes | `api/query.py` | irreversible cleanup writes, serialized on the job queue, owner scoped |
| document canon | `api/docstore.py` | put, get, delete, list, extract, re-embed, evidence backfill |

The sidecar exposes all of it over loopback HTTP with a session token, reads through the reader
view and writes through a single worker queue. The route table runs to several dozen paths, so an
inventory here would be a copy that rots; `api/server.py` is the list. What matters structurally is
that the same route table serves the desktop sidecar and the hub, and that the web build sees only
the subset the proxy allowlist names, per [[vinc.spec.architecture]] section 1.2.

The document routes are listed here only as existing. Their contract, including losslessness,
identity, change detection and derivation, is [[vinc.canon.chunk-ssot]].

## 5. Ownership isolation and the deletion cascade

Scope pre-filtering is enforced on **both** read and write paths, because the hosted and web
deployments run this same dataplane for many owners at once.

- **Read.** Every concept, episode and record match carries an owner predicate
  (`adr-security-001`)
- **Write.** Node deletion and node merging execute only on an owner match, and if an owner is
  forced but the label has no owner column, the operation is **refused** rather than run without a
  scope. Unscoped deletion is cross-tenant deletion, so this fails closed. The local app sends no
  owner and stays unscoped by design (`adr-security-003`)
- **Fragment write.** When the owner is explicit, matching is owner-scoped and a node already owned
  by someone else is **skipped rather than merged**, because merging would restamp ownership and
  overwrite. Edges likewise match only owner-scoped endpoints, so injecting an edge into another
  tenant's subgraph becomes a no-operation
- **Transport.** The MCP layer overwrites any client-supplied owner with the session subject. See
  [[vinc.spec.architecture]] section 1.1

**Account deletion cascades into the graph.** A purge routine detach-deletes every owner-scoped
label plus the tombstone table for that owner, exposed as a sidecar route and reachable remotely
only behind a signed request from the account, and it is attempted **before** the account hard
delete so that a failure stops the deletion rather than orphaning a graph.

> **Bridge ownership is a transitional state, not a finished one.** Schema version 2 gave Bridge an
> owner column with an empty default, and the legacy rows were **not backfilled** because there is
> no trustworthy ownership source for them on a multi-tenant store. Queries therefore widen to
> "owner or empty" through one predicate helper. That is a strict improvement on the previous
> behavior, which exposed every tenant's bridges, and it becomes strict by deleting one clause once
> backfill is universal. The remaining ownerless labels are Domain and the derived structures.

## 6. Synchronization

### 6.1 Nodes: a three-way field merge

Local and account graphs converge bidirectionally through export, apply and an orchestrator, with a
clock and cursor module underneath. A pack carries a whole snapshot; this carries **only what
changed since last time**, and the per-owner monotonic sequence is that cursor.

The synchronized field set is deliberately narrow: for a concept, title, domain, tags and
properties; for an episode, title, date, domain, summary and tags. The rule is **only fields a
fragment can round-trip**. Fields owned by the engines or by preference are not synchronized,
because a machine that cannot author a field must not be able to erase it. Only concepts and
episodes are in scope today; entities became round-trippable when the open layer was ported and
their inclusion is deferred work rather than a defect, while bridges still lack fragment
representation for their structural fields.

| Step | Rule |
|---|---|
| export | owner plus selected domains, everything past the cursor. An empty selection means **nothing leaves the machine**, which is what makes synchronization opt-in |
| apply | per node, a three-way merge against the stored base. Edits to **different** fields are both kept; only a same-field conflict is resolved by clock, then revision, then id |
| guard | an inbound revision older than the stored one is ignored, so a late-arriving old edit cannot overwrite a newer local one |
| deletion | propagated by tombstone, with a resurrection guard both ways: a newer local edit beats an older deletion, and a newer deletion beats an older edit |
| collection | tombstones are collected after ninety days, a conservative horizon assuming every device has pulled |
| chunking | bundles are split so each piece is self-contained, with edges carrying both endpoints, and stay under the remote body limit |

The orchestrator **pulls before it pushes**, to shrink the lost-update window, and it runs on the
job worker so local writes stay serialized behind the single writer. The two cursors live in
separate sequence spaces and advance only after a push succeeds or an apply is durable, so an
interruption re-sends rather than skips.

> **Edge deletion propagates too.** An edge has no timestamp, so a hard delete alone lets the peer's
> copy resurrect on the next delta. Deleting an edge writes an edge tombstone keyed by source,
> relation and target, and the bundle carries them. Symmetrically, creating an edge stamps **both
> endpoint nodes**, because otherwise an edge added between two unchanged nodes would not appear in
> an incremental delta at all and would wait for a full synchronization. Apply does not restamp, to
> stop the echo.

### 6.2 Documents: a separate channel

Documents do not ride the node rail. Re-chunking replaces every chunk id, and vectors would consume
the bundle size limit, so **the document is the unit** and it has its own export and apply routes
and its own cursor namespace. Only identity and restored text travel; the receiving side re-chunks
and re-embeds locally. Conflicts are last-writer-wins per document, because a document is atomic.
The rules are in [[vinc.canon.chunk-ssot]] section 5; this section records only that the channel
exists and is live, which it was not when the previous version of this document was written.

## 7. Migration and the pack overlay

`migrate(dim)` runs in three steps: apply the base declaration idempotently, apply the alterations
recorded past the stored version, then record the new version. **Schema version is 8.**

| Version | Change |
|---|---|
| 1 | synchronization tracking columns on the five tracked labels |
| 2 | Bridge gains an owner column, legacy rows left empty on purpose |
| 3 | evidence and confidence on the four concept relations and the episode relation |
| 4 | Chunk gains owner and character offsets, the offsets being what makes lossless restoration checkable |
| 5 | document metadata gains a synchronization cursor |
| 6 | document metadata gains the pending-embedding flag |
| 7 | a second time axis: content time on concepts, records and documents, and a creation time on documents that editing preserves (`adr-design-072`) |
| 8 | the per-chunk vector table. **No alteration step**: a new node table is created by the idempotent base declaration at every boot |

Two properties make this survivable. Additive column addition means a rollback leaves old code
simply ignoring the new column, so reverting does not damage data. And version 7 wrote **empty**
rather than today's date into old rows: inventing a time nobody knows would pile the entire timeline
onto the migration day, which is the same illness a pack re-merge once caused.

**The pack overlay** imports a server-side pack into the embedded store, merging idempotently on
episode identity so preference signals overlay while structural fields prefer freshly collected
values. The learner overlay is out of scope because the pack projection carries no pattern
identifier.

## 8. First-run bring-up, kept as a record

Ingestion in the packaged app failed to reproduce across the first four patch releases and was
resolved in the fifth. Four causes stacked: the store was never provisioned at startup, the shell
fetched against the WebView origin instead of the sidecar, the session token was passed by script
evaluation unreliably, and, decisively, **the sidecar had no cross-origin headers** while the WebView
and the sidecar are different origins. Development had a same-origin proxy, so the last one could
not appear until packaging.

The diagnostic that ended it is worth keeping: surfacing base, token and health together in the
error made it visible that the token was fine and only the connection was blocked, which is what
identified cross-origin policy rather than authentication. A failure that reports only "failed to
fetch" costs days; the same failure with three adjacent facts costs minutes.

## Provenance

| Source | Relation |
|---|---|
| `Vinculums/Vinc` live code | The implementation this document maps: `store/`, `engines/`, `api/` |
| [[vinc.canon.principles]] | Invariants, backend contract, dialect conformance, schema type rulings |
| [[vinc.canon.chunk-ssot]] | Document canon, lossless chunking, document synchronization |
| [[vinc.canon.mcp-contract]] | The fragment schema this dataplane implements |
| [[vinc.canon.vocabulary-model]] | Where the type vocabulary lives and why it is not in the graph |
| [[vinc.spec.web-system]] | The account side of the proxy and of the deletion cascade |
| `weg-9000-docs` design source | The design this dataplane instantiates. The dependency used to be declared as an inheritance pointing outside this corpus, which could never resolve, so it is recorded here as prose |
