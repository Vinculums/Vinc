---
doc_id: vinc.canon.vocabulary-model
layer: canon
domain: vinc/governance
authority: canon
title: Themes and the user-authored type vocabulary
supersedes: vinc.b.theme
updated: 2026-08-06
inherits_from: [vinc.canon.principles, vinc.canon.terminology]
tags: [canon, governance, theme, type, vocabulary]
---

# Themes and the user-authored type vocabulary

> The contract behind `adr-engineering-011`: node and edge `type` is not a fixed global enum but a
> vocabulary the user authors, one per theme. This document states what that means for storage,
> ingestion, and ratification.

## 1. Why the type list is not ours to fix

A fixed global type enum forces someone else's taxonomy onto the user's material. The premise of
this product is the opposite: the world gives you a subject, and you author the theme over it.
The type vocabulary is the fingerprint of that authorship.

The obvious objection is the blank page. If the user must invent every type before they can record
anything, nothing gets recorded. So the system absorbs that cost by **proposing** a starting
vocabulary, and only the user **ratifies**. The system never quietly confirms its own proposal.

## 2. The model

```
Theme { theme_id, title, guiding_questions[], vocabulary: {node, edge} }
Type  { name, kind: node|edge, scope: theme|shared,
        status: authored|proposed, provenance: user|system_draft|ingest_proposed }
```

**The vocabulary lives outside the graph.** It sits in `<data_dir>.themes.json`, a sibling of the
database file, managed by `store/theme_store.py`. It is not a graph table, because a vocabulary is
the *frame* of an interpretation and not its *content*. Keeping them apart means editing a
vocabulary never touches graph data.

**State transitions.** `proposed` becomes `authored` by ratification, is removed by rejection, or
becomes a new `authored` type by rename. A `proposed` type exists and participates in MERGE; it is
simply not a confirmed structure.

**Guiding questions are optional.** Making them mandatory would reintroduce the blank page. The
system drafts questions, the user fills them in or skips them, and the tools only accept values.

## 3. Vocabulary and physical binding are different questions

The word `type` conflates two things: which type names exist, which is the vocabulary, and which
physical table a type binds to, which is the graph binding. They are separated.

| Concern | Where it lives | Character |
|---|---|---|
| Vocabulary | `store/theme_store.py`, `themes.json` | Per theme, user owned. Not a validation gate |
| Physical binding | `api/fragment.py` `_NODE_MAP` | Legacy routing table. No entry means the carrier |

The legacy node and edge types keep dedicated tables that the engine and query layer read and
write directly.

> **Correction, 2026-07-29.** This table used to point at `_PHYSICAL_*_BINDINGS` in
> `engines/fragment_ingest.py`. **That code has no callers.** It is lineage reference only, and a
> comment in `api/server.py` says so. The live path is `/ingest` and `/ingest/fragment` into
> `api/fragment.merge_fragment`, whose `_NODE_MAP` holds **five** kinds: the four legacy kinds plus
> `record`, with Record to Concept carried by `ATTACHES_TO`. The open-layer port lives on that same
> live path. Every contract below is stated against it.

Everything else the user authors goes to the general carrier `Entity{entity_id, type, title,
owner_id, theme_id, tags}` with `TYPED{type}` between entities. See [[vinc.spec.dataplane]].
The `theme_id` column stamps theme ownership into the schema. `domain` is not inherited.

This does not conflict with the no-speculative-columns rule: the carrier columns are fixed and only
their values are user authored, and the vocabulary is outside the graph entirely. The boundary of
that exception is stated in [[vinc.canon.principles]].

## 4. Ingestion contract

`vinc_ingest(fragment, theme_id)` treats `theme_id` as **optional** and resolves it lazily:
an explicit `fragment.theme_id` first, creating an unknown theme idempotently, then the active
theme, then a `default` theme created on first encounter with an open type.

> **Correction, 2026-07-29.** The original contract said `theme_id` was **required** and that
> ingestion should be rejected without it. That was dropped. The wording survives only in the
> docstring of the dormant path, which has no callers. The live path never refuses data for want of
> a vocabulary, which is what absorbing the blank-page cost actually requires. When no vocabulary
> store is passed, as in tests and the sync and share callers, vocabulary folding is a no-op and the
> `type` string stays on the row unchanged. Nothing is lost.

A `type` is looked up in the theme vocabulary. Inside it, the type is used as is. Outside it, the
type is **folded into the vocabulary** as `status: proposed, provenance: ingest_proposed` and
reported in the response as `proposed_types`. It is not skipped.

**MERGE stays exact-match only.** Nodes match on `id`, edges on `(from, type, to)`. Similarity
merging is permanently forbidden. What changed is only where `type` is interpreted, from a global
constant to a theme vocabulary.

Response shape: `{ merged: {nodes: {created, matched}, edges: {created}}, skipped: 0,
proposed_types: [...], warnings: [...] }`.

## 5. Ratification is an HTTP surface, deliberately

Graph writes go through `vinc_ingest`. **Vocabulary writes go through sidecar HTTP routes.**
The two writes touch different stores, the graph store and `themes.json`, so keeping the surfaces
apart keeps the audit boundary sharp. See [[vinc.canon.mcp-contract]].

- Themes: `GET /themes`, `POST /themes`, `POST /themes/activate`
- Types: `GET /types` to read, and `POST /types/ratify`, `/reject`, `/rename` to write

> **Surface correction, 2026-07-29.** An earlier version of this document listed seven MCP tools
> for themes and types. **Those tools were never implemented.** A grep of the tool registry returns
> nothing for them, and the shipped set contains no vocabulary tool at all. This turns out to be
> the right shape rather than an omission: ratification is a human act, so it does not belong on a
> surface an assistant can call on its own initiative. The intent of the contract, that the user
> confirms, is intact. Only the location is HTTP. Headless ratification calls the sidecar route
> directly.

## 6. Seeding, migration, and packs

**Seeding.** Creating a theme seeds the old global enum as `system_draft` drafts in `proposed`
state. The old enum is demoted, not deleted, and this is the only place it still lives.

**Migration.** `store/migrate_themes.py` seeds a `legacy` theme and ratifies only the types that
actually occur in the graph. It writes `themes.json` and does not modify the graph.

**Packs.** Only `authored` vocabulary travels. `proposed` types are provisional and are not
packed. See [[vinc.canon.share-pack]].

## 7. Cross-theme bridges

An edge type that spans two themes is proposed automatically with `scope: shared` and then
ratified by the user: the system spares the user a blank page at every junction, and the user keeps
the final say. The detailed policy is not yet decided.

## 8. Principles this implements

| Principle | How |
|---|---|
| Confirmation means ratification | Only `proposed` to `authored` confirms. The system proposes |
| Themes are not predetermined | Vocabulary is per theme. There is no global taxonomy |
| A type is authored work | The vocabulary is the fingerprint of a theme, owned by its author |
| Ownership travels | Vocabulary belongs to the theme and moves with the pack, `authored` only |
| Offline | `themes.json` is local. Zero external calls |
| The burden is shared | The system seeds the starting vocabulary, the user ratifies |
