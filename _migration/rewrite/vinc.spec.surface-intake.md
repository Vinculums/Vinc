---
doc_id: vinc.spec.surface-intake
layer: spec
domain: vinc/design
authority: spec
title: Intake, an absorbed surface and the ingestion contract that outlived it
supersedes: vinc.d.ingest
updated: 2026-08-06
inherits_from: [vinc.spec.surface-tree]
tags: [spec, design, intake, ingest, documents, absorbed-surface]
---

# Intake, an absorbed surface and the ingestion contract that outlived it

> **Intake is not a surface any more.** Measured in `Vinc/app/src/main.ts` on 2026-08-06, the router
> knows three surfaces and `intake` is not one of them: the hash is rewritten to `#packs`. What was
> the Intake surface now renders as a headerless component inside the Import section of
> [[vinc.spec.surface-packs]], and the same drop path is reachable from the Tools modal in the top
> bar and from the file picker that any empty document list offers.
>
> The screen layout in the superseded document is therefore obsolete. **The ingestion contract is
> not.** It is the most load bearing thing this surface ever owned, it is still exactly what runs,
> and it is written down here because nothing else specifies it at the surface level. The wire level
> canon is [[vinc.canon.chunk-ssot]] and is not restated.

## 1. Why it folded

Intake was one of three doors that all did the same kind of work: bring knowledge in, send knowledge
out, make knowledge here. `adr-design-071` put those three behind one Tools button, because three
doors in three places means choosing a place before doing the work. Once the drop zone was reachable
from the shell on any surface, a surface whose only job was to hold the drop zone had nothing left.

The component was kept whole rather than rewritten. Its three states are the whole contract, and
splitting it across the modal and the Packs section would have produced two ingestion behaviors that
drift.

## 2. The app reads documents itself

This is the correction that matters most, and the superseded document already carried it in part.

Vinc is offline and uses no language model on this path. That was read for a long time as "the app
does not read your files", which was false. Extraction is **deterministic**: markdown headings become
concept candidates through a parser. There is no model, so there is no need for one, and that is a
different statement from having no parser.

The on screen copy was reversed accordingly. The old line claimed Vinc never reads your files. The
current line says Vinc reads plain documents on its own and keeps them whole, and positions an
assistant connection as the path for **richer extraction**: code, or a format Vinc cannot read.
The assistant is complementary, not primary.

> Copy that overstates a privacy property is not a harmless simplification. It taught users that the
> product could not do something it had always done, and the drop zone rejecting `.md` files made
> that lesson look confirmed.

## 3. Three ways knowledge enters

| Path | Entry | What arrives |
|---|---|---|
| assistant | the ingest tool over MCP | a fragment the host authored, merged by exact id match |
| pack | a pack file, dropped or picked | concepts and connections. See [[vinc.canon.share-pack]] |
| **document canon** | a source file, dropped or picked, or written in the app | the whole text, stored intact, with concept candidates derived |

The third path is `adr-engineering-045`. Every writer converges on one store function: the HTTP
route, the assistant tool, the command line, and the in-app editor. Convergence is the reason a
document written by an assistant and a document dropped by a person are the same object afterwards.

## 4. The branch happens before the pack parser

**One regular expression decides**, in `doc-import.ts`, and it is the single source for both entry
points. When the rule lived in two places, which files opened depended on which door you used.

```
document extensions : .md .markdown .txt .text .rst .pdf
```

A file judged to be a document **never touches the pack parser**. This is not an optimization. When
a `.md` file went through the pack path, the JSON parse failed first, and the user was shown a raw
syntax error. Every problem downstream of that parse, including size and encoding, was reported as a
format error, which is the wrong diagnosis for all of them.

### 4.1 Text or bytes

- Text extensions go as a string, with the file name recorded as the origin.
- PDFs go as bytes, base64 inside JSON.
- **Text whose decode was damaged also goes as bytes.** A browser reads files as UTF-8 only, so a
  file saved in a legacy code page comes back with replacement characters. Their presence is the
  signal, and the original bytes are handed to the server extractor instead.

Base64 inside JSON rather than multipart, because the account proxy requires a JSON content type on
state changing requests. The encoding is a consequence of the proxy contract, not a preference.

## 5. Dropping the same file again is an update

Document identity includes a content hash, so an edited file with no id supplied is **always a new
document**. Update semantics only exist if the client finds the id first.

Before importing, the client builds an index from origin, meaning the file name, to document id, and
routes a repeat drop into an update of the existing canon. Three rules bound it:

- The index is fetched **only when at least one document is in the batch**. A pack only flow does
  not pay for a round trip it cannot use.
- If the index lookup fails, everything continues as new. One listing call must not be able to block
  ingestion.
- **Origin is never displayed.** It participates in the identity calculation and is not a join key,
  and for a document that arrived in a pack it is a path on someone else's machine. Showing it
  invites the belief that the original file must still exist for the document to open, when in fact
  it was restored without one.

## 6. Extraction is preview then approve

After a document lands, the client asks for a count of concept candidates without applying them,
and offers a button carrying that number. Approval applies them through the same route.

It is not automatic, for two reasons that compound. An arbitrary document's headings becoming
concepts pollutes the graph, and the concepts that arrive are proposals, so cleaning up is a person's
work either way. Automatic application converts a moment of user choice into a backlog.

## 7. The web size limit is a client preflight

The hub caps request bodies. Over the cap it returns **an empty body rather than a status code**, so
the sidecar sees a missing field and answers with a validation error. **A size problem arrives
disguised as a format problem.**

The client therefore refuses oversized files before sending, with a message that names the file and
the limit, and a lower threshold for the base64 path to account for its expansion. The desktop shell
talks to its sidecar directly with no proxy in between and has no such limit.

## 8. Extraction failures come back as codes, not as five hundreds

A document the server cannot extract is not a server error. It answers with a not executed result and
a stable code, and the client picks a sentence from a table:

| Code | Reads as |
|---|---|
| `pdf_encrypted` | password protected, so the text cannot be read |
| `pdf_no_text` | no readable text, scanned pages need OCR first |
| `pdf_unreadable` | could not be opened as a PDF, the file may be damaged |
| `pdf_support_missing` | this build cannot read PDFs yet |
| `unsupported_format` | documents and PDFs work, this does not yet |

An unknown code is carried through with the server's own message rather than swallowed. Silence is
the one response that leaves a user with nothing to act on.

## 9. Three states, and the error case

| State | What renders |
|---|---|
| `idle` | a drop zone that is also the file picker, with `Choose a file` as the primary action, `New document` beside it, and a sample pack on desktop. On desktop, a note explaining what an assistant connection adds |
| `loading` | **one row per file**, each with a state glyph, a progress bar and a label. Files are processed one at a time, so a single failure does not stop the rest |
| `success` | packs and documents are **counted separately**, and a concept candidate offer appears when any document arrived |
| `error` | shown **only when everything failed**. A determined cause displays that sentence alone |

**Packs and documents are counted in separate sentences** because they produce different things. A
combined sentence reports zero concepts and zero connections when only documents were imported,
which reads as a failure of a successful import.

**A determined cause suppresses the diagnostic line.** The diagnostic invites the user to suspect
the sidecar, and for an oversized file or an encrypted PDF the sidecar is healthy. Pointing at the
wrong component is worse than pointing at nothing.

## 10. The app writes documents too

`adr-engineering-045` extended into an in-app editor, so the claim that the product had no path to
author content inside itself is retired. Three entry points share one editor: a document row, the
reader panel on the map, and `New document` in the intake component and the Tools modal.

Two rules hold the editor to the canon:

- **Lossless.** The editor's string goes to the store with no transformation, so verified bytes stay
  identical.
- **No silent last write wins.** The remote hash is compared, and a person chooses between
  overwriting and creating a version.

Saving is debounced with an explicit save chord as well.

## 11. Known gaps

| Gap | Detail |
|---|---|
| The error state has no retry label | The primary action reuses the idle label. A retry string exists in the copy deck and is never rendered. Measured 2026-08-06 |
| The diagnostic line is not fully translated | The fallback diagnostic in the error state is assembled with a hardcoded Korean prefix rather than a deck entry, so the English build shows it too. Measured 2026-08-06 |
| No decision node covers the fold | That this component lost its surface is visible in the router and in the Packs section, and no ADR states it. Recorded here instead |

## Provenance

| Source | Relation |
|---|---|
| `Vinc/app/src/surfaces/intake.ts`, `doc-import.ts`, `sheets.ts` | as-built authority |
| [[vinc.canon.chunk-ssot]] | the document canon contract, including chunking and identity |
| [[vinc.canon.share-pack]] | the pack contract for the other input path |
| [[vinc.spec.dataplane]] | the ingestion and document routes on the sidecar |
| [[vinc.spec.surface-packs]] | where this component renders today |
