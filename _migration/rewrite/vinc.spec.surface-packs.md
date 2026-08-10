---
doc_id: vinc.spec.surface-packs
layer: spec
domain: vinc/design
authority: spec
title: Packs, the surface where knowledge leaves and arrives
supersedes: vinc.d.packs
updated: 2026-08-06
inherits_from: [vinc.spec.surface-tree]
tags: [spec, design, packs, share, import, community, surface]
---

# Packs, the surface where knowledge leaves and arrives

> One of the three surfaces that still route, and the one that absorbed Intake. It holds four
> sections separated by **intent**, not by direction: merging something in, copying your own data
> across spaces, sending a slice to a person, and making a topic public. A full backup sits below
> them as a link.
>
> The pack format, its manifest and what a pack may carry are specified in
> [[vinc.canon.share-pack]] and are not restated here. This document specifies the surface.
>
> Measured against `Vinc/app/src/surfaces/packs.ts` and `sheets.ts` on 2026-08-06.

## 1. Sections, not tabs

The surface used to be a toggle between composing and importing. It is now a single page with four
sections, because the three outward paths are **not variations of one action**:

| Section | Intent | Availability |
|---|---|---|
| `Import` | merge a pack file into this graph | everywhere |
| `Bring in your own data` | copy a topic from your personal graph into this team | web, and only inside a team space |
| `Share` | send a slice of a topic to a person | everywhere |
| `Publish to community` | make a topic public in the gallery | web |
| full backup | export everything, for moving to a new device or an assistant | everywhere, as a link below |

Merging, handing to a person and migrating losslessly are three different judgments about who ends
up holding the data. A toggle between two of them hid that, and a user choosing "export" had no way
to tell which of the three they were getting.

The backup path is a link rather than a button because it is the rarest and the least reversible in
consequence: it produces everything you have in one file.

## 2. Import is the Intake component

The Import section mounts the ingestion component headerless. Same drop zone, same file rules, same
three states, same document and pack branch. See [[vinc.spec.surface-intake]].

Mounting the component rather than reimplementing it is the reason a `.md` file dropped here behaves
exactly as one dropped into the Tools modal. Two implementations of the same drop would have produced
two answers to which files are accepted.

## 3. Sharing shows everything before it leaves

`adr-engineering-019` established the pack as a transfer between accounts, and the surface obeys one
rule from it: **nothing leaves without being shown first.**

The share sheet carries, in order: a scope summary, the counts, the format, the list of included
nodes each with its own exclusion checkbox, and a note about what is not included. Saving filters
edges down to the surviving node set, so an excluded node cannot leave a dangling edge behind.

Scope comes from one of three shapes, and the sheet names which one is in effect:

| Shape | Comes from |
|---|---|
| everything | the surface with no topic selected |
| one topic | the topic selector on this surface |
| this document and n hops out | the map, through the Atlas share hook |

**The area scope is not composed here.** Selecting a region visually belongs to the map, so the
surface carries a link into it rather than a second, worse region picker. See
[[vinc.spec.surface-atlas]].

### 3.1 Preference signals are structurally absent

"Your pins, tags and usage stay with you. The pack carries document content only."

This is not a filter applied at export time. The projection does not carry preference signals at all,
which is why the sentence can be stated without qualification. A filter can be forgotten; an absent
field cannot.

### 3.2 Bringing your own data in uses the same sheet

Copying a topic from a personal graph into a team reuses the share sheet with the source and
destination fixed in advance, so the destination question is skipped. The preview and per item
exclusion are identical.

They are identical on purpose: **choosing what leaves and choosing what arrives is the same judgment
to the person making it.** Two different screens for it would teach two different mental models of
the same operation.

Space switching itself is not on this surface. Which graph you are working in is global state that
every surface follows, so it lives in [[vinc.spec.settings]].

## 4. Publishing is the only act with a public handle

Publishing to the community gallery differs from every other path here in one respect: the result
carries the author's public name. Three consequences shape the flow.

- **Consent is explicit.** A checkbox states that the pack becomes public and that any signed in
  member can bring it into their own graph. Neither half is implied by the button label.
- **A name is required**, and defaults to the topic. It is injected as a value rather than
  interpolated into markup, because the topic is user authored text.
- **A missing username is handled separately from failure.** The server refuses with a distinct code,
  and the sheet stays open with a link to set the handle, so the user can return and press publish
  again. Collapsing it into a generic error would leave no way to learn what was actually missing.

An empty scope is refused before the sheet opens, with a sentence telling the user to pick a topic
that has concepts in it.

## 5. The container reads as bytes

`adr-engineering-055` made the pack a zip container. The reader accepts both the container and the
older plain JSON, and it opens the file **as bytes rather than as text**, because reading a zip
through a text decoder corrupts it before any format detection can run. Detection and extraction are
one module's job, so both entry points make the same judgment.

## 6. States

This surface has no single four state cycle, because it is a composition rather than one view of one
dataset. Each part declares its own, and one shared status area carries the rest.

| Part | idle | loading | success | error |
|---|---|---|---|---|
| Import | the drop zone | per file rows | counts, split between packs and documents | only when everything failed |
| Share | the topic selector | "Preparing the pack" inside the sheet | the pack is saved | the sheet's own banner |
| Publish | the topic selector | a working line | published, with a link to the gallery | in place, with the missing handle case split out |
| Backup | the link | a working line | the pack path and a count | the shared status banner |

The topic selectors degrade rather than fail: if the topic census cannot be read, the selector keeps
`All topics` and everything still works. A pack surface that becomes unusable because a dropdown
could not be populated has confused a convenience with a requirement.

## 7. Known gaps

| Gap | Detail |
|---|---|
| No visible entry point | Nothing in the top bar or the left tree leads here. It is reachable by hash, by the command palette, by key `3`, and by one button in Settings under Data. Measured 2026-08-06 |
| No pack list | The superseded design listed existing packs with size and creation date. There is no such list, and no record of previously created packs anywhere in the app |
| Dead copy | The old two tab labels and a preview panel string remain in the deck with nothing rendering them |

## Provenance

| Source | Relation |
|---|---|
| `Vinc/app/src/surfaces/packs.ts`, `sheets.ts`, `packfile.ts` | as-built authority |
| [[vinc.canon.share-pack]] | the pack contract, manifest and what may be carried |
| [[vinc.spec.surface-intake]] | the component mounted in the Import section |
| [[vinc.spec.web-system]] | the community gallery and the account proxy behind the web only sections |
| [[vinc.canon.account-policy]] | personal and team spaces, which the pull section depends on |
