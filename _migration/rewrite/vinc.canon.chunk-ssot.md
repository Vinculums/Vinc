---
doc_id: vinc.canon.chunk-ssot
layer: canon
domain: vinc/engineering
authority: canon
title: Chunk is the source of truth for document text
supersedes: vinc.c.chunk-ssot
updated: 2026-08-06
inherits_from: [vinc.canon.principles]
tags: [canon, engineering, document, chunk, lossless, sync]
---

# Chunk is the source of truth for document text

> `adr-engineering-045`. A user's source text lives **inside the graph as chunks**, with no separate
> document blob and no dependency on an external file. Implementation: `api/docstore.py` for the
> canonical CRUD and derivation, `api/docsync.py` for document-level sync, and the Chunk, document
> metadata, and tombstone tables in the schema. Surfaces are the HTTP document routes, the CLI, the
> MCP document tools, and the app's editor and drop zone.
>
> This document is **as-built documentation.** The code shipped first and the design document was
> empty behind it. The changelog and a module docstring were already citing section numbers in this
> file before it existed.

The product claim behind this contract: every concept and relation can carry a supporting sentence
and an offset into the original text, because the original text never left.

## 1. The decisions

1. **The chunk is the source of truth.** Source text exists only as offset-joined chunk text.
   There is no document blob column and no reference to an original file. A file is an **input
   convenience**, and once loaded, the source of truth survives the file being deleted
2. **One door for reads and writes.** Four functions are the only entry: put, get, delete, list.
   HTTP, CLI, MCP, the app, pack import, and the batch pipeline all converge on them.
   **There is no file I/O inside the document store**
3. **A document joins on its id, not on a path.** The origin field was demoted to a **provenance
   label**. It is not a join key. The stored column keeps its old name for compatibility
4. **No ownerless canon.** A write without an owner is refused, and read, write, delete,
   extraction, and re-embedding all pass an ownership guard

### 1.1 The third path into the graph

Knowledge enters Vinc three ways: a fragment authored by an MCP host, a pack import, and **document
canon**. Only the third takes source text as it is and **derives** concepts and relations from it.
That derivation reads markdown headings mechanically. **There is no LLM, no network, and no external
API in it.**

### 1.2 Out of scope, deliberately

- **Document metadata holds no body text.** Copying the body there would create a second source of truth
- **Document metadata has no domain column.** A document's topic is *derived* from the domains of
  the concepts its chunks mention. A document with no links yet has an **empty** topic. It is not
  assigned one by force
- Fine-grained splitting for retrieval belongs to the embedding pipeline. Chunking here is the
  **unit of canonical storage**, which is why the default size is comparatively large

## 2. Lossless chunking

### 2.1 Losslessness is structural, not checked

Chunking is not cutting. It is **slicing a list of boundaries**: with `b0 = 0 < b1 < ... < bn = len(text)`,
each chunk is `text[b_i:b_i+1]`. Because of that construction,

```
"".join(chunks) == text
```

holds **by definition**. Overlap and dropout are impossible to express. If boundary selection goes
wrong the result is an ugly split, never a broken document.

### 2.2 Built-in boundary priority

Within a window of at most 2000 characters, in order: a paragraph boundary, the **largest** one
inside the window; otherwise a line boundary, again the largest; otherwise a hard cut at the window
edge, which guarantees termination. An empty document becomes a single empty chunk.

### 2.3 External boundaries

Offsets from an external chunker are normalized by clamping to range, sorting, de-duplicating, and
forcing a zero start. If an external chunker drops whitespace at a boundary, the slice construction
means that whitespace is absorbed into some chunk anyway, so losslessness is **repaired by
construction** rather than by validation.

### 2.4 Stored columns

A chunk carries its id, document id, owner, origin label, section title, index, layer, character
start and end, character count, a token count that is currently fixed at zero, the text, and
optionally an embedding. Section title is the first markdown heading inside that chunk, or empty.

> **Cypher trap.** `start` and `end` are reserved words and the parser rejects them as parameter
> names. Short aliases are used instead. This is measured, not theoretical.

### 2.5 Restoration and verification

Reading concatenates chunk text in index order and checks two things together: that offsets are
contiguous, start at zero, and never invert, and that the hash of the restored text matches the
recorded hash.

**A false result is a report, not a failure.** It means legacy data written before offsets were
recorded, or a partial write, and the response says it can be healed by writing the document again.
The app's losslessness badge shows this value directly.

> **Offset trap.** Reading offsets with a truthiness fallback demotes a legitimate offset of zero
> to missing. Only an actual null may be treated as absent.

### 2.6 The contract is pinned by tests

Eight boundary-condition samples, including an empty document, no newlines at all, a trailing
newline, runs of blank lines, 5000 characters without a break, a heading plus long text, leading
blank lines, and mixed line endings, are run across three window sizes. The tests pin lossless
joining, offset contiguity, paragraph preference, external boundary normalization, and a real-engine
round trip.

## 3. Identity

### 3.1 Two document id formulas

- **Content based**, the default. Derived from owner, title, origin, and content hash. Duplicate
  first loads of the same document deduplicate naturally, and an offline reload on another machine
  converges on the same identity. The content hash is in the basis, but **the id does not change
  when the document is edited**, because editing passes the existing id
- **Origin based**, used by the batch pipeline. Derived from owner and a stable external identifier,
  so the id survives editing without being passed

> **Client trap.** Because the default formula includes the content hash, **an edited document is a
> new document unless you supply the id.** The meaning of "dropping the same file again updates it"
> only exists if the client keeps an origin-to-id index and looks the id up. The app does.

### 3.2 Chunk ids are position stable

A chunk id depends on the document and the ordinal, not on content. Re-chunking after an edit
merges the chunk at the same position under the same id, and only a shortened tail is deleted.

### 3.3 Origin is not a join key

The MCP tool schema says so explicitly. The app **does not display** origin, because it may be a
path on someone else's machine.

### 3.4 Ownership

Chunk ownership arrived with this decision. An empty owner means a legacy chunk from before the
document store, derived from a file batch with no ownership source, and it is **not backfilled**.
Because owner-scoped labels are derived from the presence of the owner column, **account deletion
purge covers chunks and document metadata automatically**, and a test pins that.

## 4. Change detection and write order

### 4.1 Change detection is content based

A write is skipped **only when both the content hash and the title match**. A title-only change
still rewrites. The skip returns success, not failure. The batch path makes the same judgment
**before** chunking and embedding, to avoid paying for work it will discard.

### 4.2 The ownership guard

If a document exists and its owner differs from the caller, the write is refused immediately.
**You cannot seize someone else's canon by writing over it.** Read, delete, re-embed, and extract
carry the same guard, and a real-engine test pins three of those refusals.

### 4.3 Write order is the basis of self-healing

| # | Step | Why |
|---|---|---|
| 1 | drop search indexes, idempotent | indexed columns cannot be modified |
| 2 | detach delete old chunks | re-chunking to a smaller count leaves **zero orphans**, and attached mention edges go with them |
| 3 | merge new chunks | position-stable ids update in place |
| 4 | merge document metadata: hash, chunk count, timestamp, sync sequence, embedding flag | **the hash is written last** |
| 5 | clear any tombstone | recreation cancels a stale deletion |
| 6 | relink mentions | reusing the existing linker rules, never a second copy of them |

If the process dies partway, step 4 never runs, the hash is not updated, and **the next write does
not skip: it rewrites everything.** A single-process embedded store converges without needing a
multi-statement transaction.

### 4.4 Index lifecycle

Indexes are dropped before mutation and **not rebuilt here**. A lazy guarantee on the search path
creates them when they are missing. Rebuilding the whole vector index on every single-document edit
would burn seconds per keystroke.

When the search extension is unavailable, a failed drop is **swallowed**. That store simply has no
indexes, which is not a reason to block a canonical write. **The source of truth must hold without
search.**

### 4.5 The embedding flag

If no embedding callable is supplied, the document is marked as needing embedding. The app editor
and the sync receive path both land that way, so **full text search works immediately and vector
search lags.** Two paths fill it: a per-document re-embed, or a worker that pulls pending chunks and
applies vectors. The pending list is emitted **per document**, because splitting by a chunk cap
means a large document never finishes, and the flag clears **only when every chunk is filled**.

This is a loopback-internal surface for the self-hosted embedding worker. **Source text never leaves
the store.**

> **Known gap, 2026-08-06.** On the hub that worker starts and has never loaded its model, so
> nothing has been embedded at all. Every document in the corpus is still pending. This is a
> live operational failure, not a property of the design. See [[vinc.plan.embedding-pipeline]].

## 5. Synchronization is per document

Node sync is a three-way field merge over fragment nodes. Putting chunks on that rail fails twice:
re-chunking replaces the entire set of chunk ids, and embeddings would swamp the delta bundle size
limit. So **the document is the unit of synchronization**, on an independent channel.

| Axis | Rule |
|---|---|
| Send | document identity and **restored text only**. No embeddings, no chunk offsets |
| Receive | write locally, which **re-chunks locally**. Embeddings are regenerated per machine |
| Conflict | **last writer wins per document**, compared on a hybrid logical clock. No field merge, because a document is atomic |
| Delete | propagated by tombstone |
| Cursor | a separate sequence namespace from node sync |

Chunks are deliberately absent from the tracked labels: they have no tracking columns, and sync
happens only at document granularity.

### 5.1 Import is deterministic

For each inbound document: create it if absent, stamping the remote clock. **Skip on owner
mismatch**, never trusting the owner in the payload. Skip if the content hash matches. Skip unless
the remote is strictly newer, so **a tie or an older version keeps the local copy**. After applying,
advance the local clock past the remote one so later local writes cannot land in the past.

The newer-than test is a plain string comparison, because the clock format sorts monotonically and
an empty value is the oldest possible.

### 5.2 Deletion propagation

A hard delete alone lets the peer's copy **resurrect** on the next delta. So a delete records a
tombstone, and the receiving side **ignores a deletion when its local copy is newer than the
deletion**. Recreation beats deletion. Applying a synced deletion does not itself write a tombstone,
which would otherwise ping-pong forever.

### 5.3 Time source

A monotonic hybrid logical clock inside the store, so last-writer-wins holds under wall-clock skew.
On a lightweight store without it, this degrades to wall-clock time, and the sync sequence degrades
to zero.

### 5.4 Surfaces

The two sync routes are sidecar internal and **deliberately absent** from the hub proxy allowlist:
sync is desktop-to-account machinery, not a target for the web graph proxy.

## 6. Deriving concepts from canon

### 6.1 Extraction is a pure function

No LLM, no network, no store. The same input yields the same candidates anywhere.

- Only second and third level headings are considered. A top-level heading is treated as the
  document title and excluded
- The slug is a normalized, truncated form of the heading. **A repeated slug counts only its first
  occurrence**, which makes the function idempotent
- The one-line summary is the first non-empty line after the heading, capped, and stops at the next
  heading
- At most twenty-four candidates

A document with no headings yields nothing. That is a real limit, not a bug.

### 6.2 Applying is a separate step

By default extraction **returns candidates only**. Only an explicit apply writes.

- Concepts are merged with a slug-derived id and a proposed status, with **no domain**, since
  domain assignment belongs to later editing
- **Existing concepts are never overwritten.** Yours are counted as matched and skipped, which
  protects a title or summary you refined. **Someone else's are not touched at all**
- Then mentions are relinked so a new concept immediately joins the view and search graph, and
  evidence is backfilled
- An externally refined candidate list may be supplied, but **the server re-derives the id from the
  slug**. Injecting an external id is refused

Re-applying reports zero created and n matched. It is idempotent.

### 6.3 Mention relinking

Relinking targets **only this document's chunks** and **reuses the existing linker rules
unchanged**. Forking the rules would create a second source of truth for linking. Old links were
already removed by the detach delete in the write order. A relink failure does not invalidate the
write, because linking is augmentation.

### 6.4 Highlight positions are computed at read time

Mention edges store a method and **no offsets**, so positions are computed when read, by token
matching with the same tokenizer the linker uses. Ranges are cut at token boundaries so the
highlighted span is exactly a word. **Adjacent ranges are not merged**: each word is its own
underline rather than painting a whole sentence.

### 6.5 Relation evidence

The schema had evidence and confidence columns and **nothing filled them**, so the relation panel
always said "no evidence". Measured: zero of eighty directed edges carried any. This fills that gap
from canon.

- The rule: a chunk mentions two concepts, and the first sentence where both titles occur **in the
  same sentence** becomes the evidence. **Merely appearing in the same document is not evidence**
- Sentence splitting is a regular expression. There is no NLP
- Confidence falls with the distance between the two titles and is **capped at 0.9**, so the number
  itself says this was not human-confirmed
- Evidence text is capped at 300 characters
- **An edge that already has evidence is left alone**, so repeated runs are safe

### 6.6 The app asks before applying

Drop a file, it is written, then extraction previews **without applying**, and one click applies.
The reason automatic application is refused is in the code: if arbitrary headings became concepts,
the graph would be polluted, and since applied concepts land in a proposed state, the cleanup would
be a person's work.

## 7. Surfaces

HTTP routes cover read, list, highlights, write, delete, extract, re-embed, and evidence backfill.
A write or an extract without an owner returns a client error rather than defaulting. The embedding
pending and apply routes are **loopback internal** and not proxied.

The CLI and the MCP document tools map onto the same four functions. The app accepts source files
directly in its drop zone and top bar, keeps an origin index so re-dropping a file updates rather
than duplicating, and caps web request bodies **below the hub limit** so the app reports an
oversized file itself instead of the hub closing the connection.

Packs carry documents in an optional field, described in [[vinc.canon.share-pack]].

## 8. Limits and follow-ups

**Accepted by design:** documents without headings derive nothing, mention offsets are recomputed
rather than stored, and evidence confidence is capped because it is machine-derived.

**Follow-ups:** whether chunks should ever gain tracking columns, which the schema comments
deliberately left open, and the text extractor scope, which has since widened to include PDF.

> The original of this document noted that the decision register had stalled and this decision was
> not yet registered in it. That is now resolved: the register is [[vinc.canon.decisions]] and this
> decision is `adr-engineering-045`.
