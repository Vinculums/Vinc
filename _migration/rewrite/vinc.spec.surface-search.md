---
doc_id: vinc.spec.surface-search
layer: spec
domain: vinc/design
authority: spec
title: Search, and the always-on bar above it
supersedes: vinc.d.search
updated: 2026-08-06
inherits_from: [vinc.spec.surface-tree]
tags: [spec, design, search, ask, hybrid, surface, 4-state]
---

# Search, and the always-on bar above it

> Two things answer queries, and they answer different questions. The **bar** in the top of the
> shell is present on every surface and answers "take me to the thing I can name". The **surface**
> is a place to work through results, group them and read snippets. `adr-design-057` created the
> first by declaring lookup, not traversal, the primary way into the graph.
>
> Both consume the same hybrid retrieval and differ in what they do with the answer. This document
> specifies both, because splitting them across two documents is how the two drift.
>
> Measured against `Vinc/app/src` on 2026-08-06.

## 1. Retrieval is hybrid and the fusion is outside the engine

A query runs a vector arm and a keyword arm and the two ranked lists are fused. The fusion is a pure
function with the query embedding injected, which is what keeps the search path model agnostic. That
contract belongs to [[vinc.canon.principles]] section 2.5 and is not restated.

The surface exposes **why** a row matched rather than how well: `≡` for the keyword arm, `≈` for the
semantic arm. The numeric score stays in the tooltip. A score is not a reason, and a list sorted by
a number the user cannot interpret is a list they have to trust rather than read.

## 2. The bar

### 2.1 Two modes, and one word that was refused

| Mode | Icon | Does |
|---|---|---|
| `Find` | the search mark | node hits in a dropdown, landing on the map |
| `Ask` | the decide mark | a conversation panel that quotes stored sentences |

`adr-ai-073` added the second mode. Two naming decisions carry the whole promise of this surface:

- **The mode is called `Ask`, not `Chat`.** Chat sets the expectation of a language model on the
  other side. Vinc does not generate sentences.
- **The icon is not a sparkle.** A sparkle implies generative AI, which contradicts the one property
  this surface is built on. The two existing marks are reused instead, and they face each other.

The bar's `data-mode` attribute is the single source for the icon, the label, the placeholder and
which panel may be open. Switching modes closes the other mode's panel in the same place it changes
the icon: a half completed switch, where the icon says one thing and an open panel says another, is
the most common failure in a two mode control.

### 2.2 Find

- Debounced live query at two characters or more, with a sequence guard so a late response cannot
  overwrite a newer one.
- **Node hits only.** A chunk hit has no node to land on, so it would produce a row that goes
  nowhere. Reading through document text is what the `All documents` sheet is for.
- Enter lands on the first hit. With no hits, it opens `All documents` rather than doing nothing.
- The `All documents` button is present in every state of the dropdown **including the error state**.
  If search fails, the path to your documents must not fail with it.
- The first query builds the index and takes a few seconds, and the empty result says so. Silence
  during a slow first run is indistinguishable from a broken feature.

Landing uses the Atlas hook when the map is mounted, so no remount happens and the detail panel opens
with it. Otherwise it sets a trace address and lets the router mount the map.

### 2.3 Ask

The condition of existence is that **no sentence is generated**. Answer bodies are quotations of
stored text, concept summaries and canonical excerpts, and the module's only job is choosing which
sentence deterministically. That is why it needs no key and why desktop and web return the same
answer.

Three behaviors follow from having no model to paper over ambiguity:

- **It asks back.** An exact title match resolves; one partial match resolves; several ask which one.
  Pretending to know is what a retrieval system cannot afford.
- **Follow ups arrive as chips carrying ids**, so multi turn works without pronoun resolution. The
  user never types "that one".
- **Truncation is stated.** Turn history, excerpt count and related node count all have ceilings, and
  each one says when it was reached.

The panel does not close on blur. Reading an answer requires taking the cursor out of the field, and
a panel that vanishes at that moment cannot be read. It closes only on an explicit action.

Ask does not query on every keystroke either. A turn is a unit the user ends with Enter; a live query
would fill the conversation log with unfinished sentences.

What Ask does not do yet, and what it will never do, is stated in [[vinc.research.no-llm-chat]].
Summarizing and synthesizing are permanently out of scope. The on screen copy names only the question
forms that work, because inviting a question shape that fails makes that failure the verdict on the
whole surface.

## 3. The surface

### 3.1 One field with operators

```
domain:<topic>   tag:<tag>   "phrase"
```

Operators are parsed out of the raw string before the query is sent, and then applied as filters on
the result rows. There is no mode toggle: hybrid is the only behavior, so there is nothing to choose.

### 3.2 An empty field is a request to see everything

Clearing the field does not return to an instructional placeholder. It renders recent queries as
chips, then the full document list. That list is the browsing contract specified in
[[vinc.spec.surface-records]], mounted here as a component.

Recent queries are recorded **only on submit**, never on the live query, and are capped. A live query
records every prefix of what you typed, which produces a history of fragments.

### 3.3 Results

Two kinds of row are merged into one list.

| Row kind | Source | Landing |
|---|---|---|
| chunk hit | the chunk index | none today |
| node hit | concepts and records the backend always returns, even when the chunk index is empty | the node, by trace address |

**Node hits exist so that a graph with no documents still answers.** A user who only imported a pack
has concepts and no chunks. Before the surface read that field, their concepts were invisible to
search, and the product looked empty to the people with the least data to lose.

Rows are grouped by topic unless a topic filter is already applied, in which case grouping would be a
single header over everything. Each row carries a one line snippet, because a title alone is not
enough to choose between results.

### 3.4 The re-embedding badge

`adr-governance-013` requires the app to say when the semantic index is still being rebuilt. The
badge is a sub state of success, not a screen of its own.

**The trigger is the structured pending count, never the fact that results came from the keyword arm
alone.** Keyword only is the permanent, healthy state on a desktop install with no embedding worker,
so triggering on it would pin the badge on forever. Because the trigger is a field the sidecar stops
sending when the migration completes, the badge disappears with no code change.

The badge carries a glyph and a text label. Color alone is not a signal (`adr-design-038`).

## 4. Four states

Copy is owned by the string constants in code; the inventory is [[vinc.spec.ui-copy]].

| State | What renders |
|---|---|
| `idle` | recent chips and the document list. The bar's own idle is the placeholder, "Search your knowledge" |
| `loading` | "Searching" |
| `success`, none | "No results for '{q}'. Try a different term, or open the Atlas instead." with `Open Atlas` |
| `success` | a result count, then grouped rows, with the migration badge above when pending |
| `error` | "Search couldn't run. Retry anytime. The index is intact." |

**Every failure state names what was not damaged.** "The index is intact" is not reassurance for its
own sake: search failing is the moment a user suspects their data, and the sentence that prevents
that costs four words.

**No empty result is a dead end.** The zero result state offers the map, and the bar's zero result
state offers the document list. A search box that answers "nothing" and stops is where a session ends.

## 5. Known gaps

| Gap | Detail |
|---|---|
| Chunk hits cannot land | A chunk names no concept, so its row is inert in the results list while node rows are clickable. The two look alike. Measured 2026-08-06 |
| Dead copy | Row action labels for `Show in Atlas` and `Open` remain in the deck with nothing rendering them |
| The two entry points disagree by design | The bar returns node hits only and the surface returns both, so the same query yields different result counts in two places, with nothing on screen explaining why |

## Provenance

| Source | Relation |
|---|---|
| `Vinc/app/src/topbar.ts`, `ask.ts`, `surfaces/search.ts` | as-built authority |
| [[vinc.research.no-llm-chat]] | the feasibility study for Ask, including what stays out of scope |
| [[vinc.plan.embedding-pipeline]] | the retrieval and embedding pipeline behind both arms |
| [[vinc.spec.dataplane]] | the search route contract |
