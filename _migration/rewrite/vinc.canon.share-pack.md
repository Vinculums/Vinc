---
doc_id: vinc.canon.share-pack
layer: canon
domain: vinc/engineering
authority: canon
title: Share Pack, the account-to-account transfer contract
supersedes: vinc.c.sharepack
updated: 2026-08-06
inherits_from: [vinc.canon.mcp-contract]
tags: [canon, engineering, pack, export, import]
---

# Share Pack, the account-to-account transfer contract

> `adr-engineering-019`. How data loaded on one person's machine is packaged for someone else.
> Implementation: `api/share.py`, the `vinc share` and `vinc import` commands, and the sidecar
> routes `GET /share` and `POST /ingest/pack`.

## 1. Three decisions that shape the format

**The pack body is a fragment.** It is the same `{nodes, edges}` shape the ingestion tool accepts,
so importing is `api/fragment.merge_fragment` reused as is: exact-id MERGE, idempotent. There is no
separate import machine to keep in step. A pack built in the app can be read and re-authored by an
assistant upstream for the same reason.

**Scope is a first-class choice, not just "the whole domain."**

- `domain`: one dataset partition
- `tags`: any-overlap filter
- `seed` plus `depth` from one to four: the same ego subgraph the Atlas node focus uses, sharing its
  traversal plan. What you are looking at on screen is what gets packaged

**Preference signals are structurally absent.** `pinned`, `access_count`, and `last_access` belong
to the origin account. The sharing projection **does not select those columns**, so there is no
scrubbing step that could be forgotten. The recipient re-stamps everything with their own owner.

> This is the opposite policy from `adr-engineering-004`, which requires a pack to carry preference
> signals in full. The two serve different purposes: that one is lossless migration of your own data
> to yourself, this one is transfer to another person. The `kind` field tells them apart.

## 2. Format, version 1

```json
{
  "vinc_pack": 1,
  "kind": "share",
  "manifest": {
    "created": "2026-07-10T05:30:00Z",
    "scope": { "domain": null, "tags": [], "seed": "...", "depth": 1 },
    "counts": { "concepts": 9, "records": 0, "edges": 45, "documents": 3 },
    "note": "Preference signals are not included. They stay with the origin account."
  },
  "fragment": { "nodes": [], "edges": [] },
  "documents": [
    { "doc_id": "", "title": "", "origin": "",
      "content_hash": "sha256...", "updated_at": "", "text": "" }
  ]
}
```

**Nodes** are the fragment node kinds. An Episode carries its `date` and `summary` content fields,
mapped to columns on import. A Concept's `topic` and `one_liner` move into `props`.

**Edges** are `relates_to`, `prerequisite_of`, `derives_from`, and `inherits_from`. An edge that
would leave the scope boundary is cut: what remains inside the cut is the whole pack.

**`documents`** is optional and comes from `adr-engineering-045`. It carries the source text the
packed concepts refer to, so the pack is self-contained. Embeddings and chunk offsets are **not**
sent; the recipient re-chunks and regenerates. The build walks back from concept mentions to pick
only the documents that mention the packed concepts. Without this field the pack carries concepts
only, exactly as before that decision.

**Versioning.** `vinc_pack` is an integer and an unsupported version is refused with an explicit
error. `documents` did **not** raise it: it is an additive optional field, so an older client
ignores it and imports the concepts.

## 3. Import rules

`vinc import --pack <file> --owner user:me` injects the recipient owner explicitly and MERGEs.
Re-importing counts as `matched` and does not overwrite the owner of an existing node, per the
fragment contract.

Node ids are merged **as they are**, so receiving an updated pack from the same origin updates in
place.

**Documents import after concepts, not before.** Source text is restored with `put_doc` only once
the fragment MERGE has landed, so that mention re-linking attaches to the concepts that just
arrived.

**A document id is derived, never reused.** Unlike concept ids, reusing the author's `doc_id` across
owners fails: in a hosted single store it collides with that author's own document metadata, whose
owner does not match, and the write is refused. The id is derived from
`(recipient owner, content_hash, title)`, which avoids the collision and makes re-importing the same
content idempotent, since a `content_hash` match skips.

## 4. Measured round trip, 2026-07-10

Against a real store loaded with study notes.

| Step | Result |
|---|---|
| Author's store | 91 concepts across four domains, 645 intra-domain relations, 11 bridges |
| Pack A, whole domain | 11 concepts, 59 edges |
| Pack B, seed with depth 1 | **9 concepts, 45 edges**, the custom region |
| Recipient import | all 9 nodes re-stamped to the recipient owner, subgraph traversable |
| Re-import | created 0, matched 9, idempotent |
| Leak check | zero occurrences of the three preference columns. Author's store intact |

## 5. The file is a zip container

`adr-engineering-055`, 2026-07-26. Until then `.vincpack` was an extension with no container behind
it: no magic number, no compression, no integrity check, just pretty-printed JSON. Once packs began
carrying source text, three things hurt at once. Size, since a 74-concept pack was already 1.6 MB.
Whole-file parsing, since a single document could not be pulled out alone. And binaries, since an
image or PDF would inflate 33 percent through base64.

```
manifest.json          {vinc_pack, kind, manifest{}}
fragment.json          {nodes, edges}
documents/0001.json    one file per document
```

- **Both shapes are readable, only zip is written.** Old JSON packs are already published in the
  community gallery, so backward compatibility is the contract, not a courtesy. The first byte
  decides: `PK` is a zip, `{` is the old JSON
- **`vinc_pack` did not increment.** The container changed, the content contract did not
- **Deterministic.** Zip header timestamps are pinned to 1980-01-01 so the same pack produces the
  same bytes, which is what makes hash comparison and duplicate detection meaningful
- Implementation: `api/packfile.py` on the sidecar using the standard library, and
  `app/src/packfile.ts` in the app, which writes a minimal zip skeleton by hand and uses the native
  `CompressionStream("deflate-raw")`. The app has no zip library and its content security policy
  blocks a CDN, so **zero dependencies** is a constraint, not a preference
- Measured: 1.62 MB to 461 KB, 28 percent. Interoperation verified both directions, with Korean
  text, emoji, and a 5000-character document surviving intact

> **The gallery stores JSON, not zip.** Its column is text and it serves
> `Content-Type: application/json`. Unpacking a zip to JSON before publishing is the **normal path**,
> not a workaround. Seeding a zip directly into the gallery has been attempted and failed.

## 6. Out of scope

**Signing and encryption.** Trust in the delivery channel is the user's, since a pack is a local
file. The zip container closed part of this for free: CRC catches corruption in transit. Signing,
which would authenticate origin, is still absent, and the live community gallery makes it a
candidate.

**Embeddings never travel.** Regeneration at the receiving end is the contract, to avoid model
mismatch.

> **Known gap. As of 2026-08-06 that regeneration does not happen.** The shipped sidecar excludes the
> embedding stack from its bundle, and on the hub the embedding worker starts but has never loaded
> its model. Shared source text is therefore searchable by full text immediately and by vector
> never. No surface regenerates today. This is a live gap between the contract and the system, not
> a design choice. See [[vinc.plan.embedding-pipeline]].

**Billing and account integration** are excluded by the forbidden-vocabulary rule. See
[[vinc.canon.terminology]].
