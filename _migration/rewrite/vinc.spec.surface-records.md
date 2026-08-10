---
doc_id: vinc.spec.surface-records
layer: spec
domain: vinc/design
authority: spec
title: Records, an absorbed surface and the document browsing contract that outlived it
supersedes: vinc.d.records
updated: 2026-08-06
inherits_from: [vinc.spec.surface-tree]
tags: [spec, design, records, documents, browsing, absorbed-surface]
---

# Records, an absorbed surface and the document browsing contract that outlived it

> **Records is not a surface any more.** Measured in `Vinc/app/src/main.ts` on 2026-08-06, the hash
> `records` is rewritten to `#search`. The list itself was not deleted: it became a component that
> mounts in two places, and it is the same code in both.
>
> The layout in the superseded document is obsolete, and so is its data contract. **The browsing
> contract is not**, and it changed in a way worth writing down: the unit stopped being a record and
> became a document.

## 1. Where the list lives now

| Mount point | Opened by | Closes |
|---|---|---|
| the empty state of [[vinc.spec.surface-search]] | clearing the query, or arriving with none | not applicable, it is the surface |
| the `All documents` sheet | the top bar search dropdown, or pressing Enter with no results | itself, when a click lands on the map |

**Browsing is a sheet, not a destination.** Swapping the whole screen to look at a list loses the map
and the scope you were holding, and getting back means walking the path again. A sheet returns you to
exactly where you were when it closes. That reasoning is what removed the surface: once the list is
something you open over your work, a route to it is a route to somewhere you did not want to go.

The other mount is the reverse case. An empty query is a request to see everything, so the surface
that answers queries answers that one with the list.

## 2. Documents, not records

The superseded design listed records ranked by preference and offered `Pin` and `Tag` on each row.
Measured on 2026-08-06, none of that is wired: the client declares a recall call and a pin call that
**no surface invokes**, and tag and touch are not in the client at all. The list reads the document
listing instead.

This is not a regression to report as a bug. It followed the document canon: once whole source text
became the thing the product stores and shows, a list of derived records was listing the wrong
objects. What has not been rebuilt is the preference signal itself, which still exists in the store
and now has no interface. Recorded in section 7.

## 3. Two levels, because everything else in the app has two

The list is topic, then the documents in that topic. It was flat, and a flat list of ninety one rows
sat inside an app where the top level map, the totals view and the left tree all read "topic first,
then the grain". The same data appearing with a different structure on every screen is a cost the
user pays, not a variation.

### 3.1 Topic is derived, and absence is stated

**A document has no topic column.** Document metadata carries identity and change detection only.
The topic is derived by following the concepts a document mentions and taking the most frequent
topic among them.

A document that has no links yet has no topic, and it is placed in a `No topic yet` group rather
than assigned to one. Groups sort by size, then by name, and that group is **always last**: it is a
remainder, not a topic.

The conventions file is excluded from this listing entirely (`adr-governance-007`), because a file
about how to write the graph is not the user's material. See [[vinc.canon.graph-conventions]].

## 4. Rows say the least that identifies a document

| Shown | Withheld | Why |
|---|---|---|
| title | | the only field a person chose |
| edited date | the timestamp | a machine readable instant is not a date a person reads |
| | origin | see below |

**Origin is never displayed.** It is where the text came from, it is not a join key, and for a
document that arrived inside a pack it is a path on the sender's machine. Displaying it suggests the
original file must be present for the document to open, when the document was in fact restored
without one.

The filter field lives inside the list and narrows **both levels**. Searching from the topic level
has to work, because not knowing which topic holds the document is the ordinary reason for opening
the list at all.

## 5. Clicking a document goes to the map

`adr-design-070` made the row click a navigation to the node, and removed `Show in Atlas` as a
separate hover action. Before that, choosing a document from the list told you nothing about where
it sits on the map, which was the one thing the list could not answer on its own.

The rule has an exception it states rather than hides. A document with no landing node, meaning it
mentions no concept, has no place on the map, so it opens in the editor. The list is told which node
to land on by the listing itself, so no second lookup is needed to know which case applies.

When the map is live the click flies there without remounting the surface. When it is not, the click
is demoted to a hash landing. Same rule as everywhere in the shell: a visible row must do something.
See [[vinc.spec.surface-tree]] section 4.1.

**The sheet closes itself on a map landing.** Otherwise it stands in front of the place it just took
you to. It stays open for the editor case, because nothing moved behind it.

## 6. Four states

Copy is owned by the string constants in code; the inventory is [[vinc.spec.ui-copy]].

| State | What renders |
|---|---|
| `idle` | "No documents yet." with `Import files`, which opens the file picker in place rather than navigating |
| `loading` | one line |
| `success` | the topic level, or one topic's documents, with a count in the header |
| `error` | "Documents couldn't load. Retry anytime. Nothing was changed." |

**`New document` is deliberately absent.** Creating belongs with the other doors in the Tools modal
(`adr-design-071`). A list is a place to see what exists, and mixing creation into it means the same
action has two homes that will drift.

The filter redraws the list on every keystroke, so focus and caret position are restored explicitly
after each redraw. Losing the caret mid word is the failure that makes a live filter unusable.

## 7. Known gaps

| Gap | Detail |
|---|---|
| Preference signals have no interface | Pin and tag exist in the store and in the pack projection, and no screen writes them. The recall and pin calls are declared in the client and never invoked. Measured 2026-08-06 |
| Dead copy | The row action labels for `Show in Atlas`, `Pin` and `Tag` remain in the deck with nothing rendering them, as does a document count header string |
| No decision node covers the fold, or the two levels | Both are visible in code and neither is an ADR. See [[vinc.canon.decisions]] |

## Provenance

| Source | Relation |
|---|---|
| `Vinc/app/src/surfaces/records.ts`, `topbar.ts`, `surfaces/search.ts` | as-built authority |
| `Vinc/api/docstore.py` | the listing, and the topic derivation stated in section 3.1 |
| [[vinc.canon.chunk-ssot]] | why the unit is a document |
| [[vinc.spec.surface-search]] | the surface this list is mounted in |
