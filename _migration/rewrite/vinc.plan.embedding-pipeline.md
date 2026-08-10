---
doc_id: vinc.plan.embedding-pipeline
layer: plan
domain: vinc/ai
authority: spec
title: The embedding and retrieval pipeline
supersedes: vinc.plan.embedding-retrieval
updated: 2026-08-07
inherits_from: [vinc.canon.chunk-ssot, vinc.spec.dataplane]
tags: [plan, ai, embedding, retrieval, search, onnx, chunking, known-gap]
---

# The embedding and retrieval pipeline

> How text becomes a vector, which model does it, why the vector is not the storage unit, and what
> the retrieval path does when there is no vector to search. Every design choice below was settled by
> measurement rather than argument, and the measurements are kept because the conclusions are only
> as good as they are.
>
> **The one fact that governs the rest: the pipeline has never run.** The code is complete and
> deployed. On the hub the worker starts, has never loaded its model, and **not one document in the
> corpus has been embedded** (measured 2026-08-06). Every query today is answered by full-text
> search alone. Section 5 is the live work.

## 1. The index has a lifecycle, and it is the reason for everything else

The vector index is mutable, which is what the two design proposals disagreed about. It is also
**conditionally fatal**, which neither of them found.

| Operation against a live index | Result |
|---|---|
| Insert a new row that carries a vector | Works and is searchable immediately, then **crashes the process at roughly three hundred repetitions** |
| Update the value of a vector that already exists | Two thousand repetitions, no failure |
| Insert a row with no vector | Six hundred repetitions, no failure. The index is not touched |
| Set a vector where the column was empty | **Crashes between five and six hundred** |
| Delete a row | Works, and the index drops it |

The rule is that **putting a vector where there was none is an index insertion, and that is what
breaks.** Changing a value is safe.

The production flow is exactly the dangerous shape. Document write has no embedding callable on
either the hub or the desktop, so chunks land empty and a worker fills them later. And the failure
is a **process death, not an exception**: it cannot be caught, and on the hub the container
entrypoint ties the sidecar's life to the MCP endpoint, so a retrieval-quality background job could
kill the product's front door.

### 1.1 Drop the vector index, keep the text index

Three strategies were compared by replaying the real sequence, inserting empty chunks, filling them,
and searching in between.

| Strategy | Two thousand chunks | Text search during the fill |
|---|---|---|
| Drop nothing | **Died at the fiftieth document** | worked |
| Drop the vector index only | completed | **two hundred of two hundred succeeded** |
| Drop both, the behavior at the time | completed | **zero of sixty succeeded** |

Dropping nothing is not merely risky, it is **non-deterministic**: it survived one interleaving and
died in another, and a failure mode that depends on the pattern of traffic cannot be shipped.

The behavior that was already in production turned out to be the expensive one. While embedding ran,
keyword search failed **completely**, and in production that surfaces as a rebuild queued behind the
request with a wait of up to a minute. The only retrieval arm that worked at all was being switched
off periodically by a background job. The full-text index is mutable and is not on the crash path,
so there was never a reason to drop it. That narrowing landed first, on its own, because it is a
gain even with vector search switched off.

## 2. The model, and the quantization that nearly undid it

The corpus is English and Korean. The English-only model in use had **no Korean syllable tokens at
all**, so Korean text decomposed into letters and retrieval collapsed.

| nDCG at ten | English-only model | Multilingual model | Multilingual, vendor int8 |
|---|---|---|---|
| English | 0.669 | 0.636 | 0.574 |
| **Korean** | 0.347 | **0.670** | 0.472 |
| Combined | 0.460 | 0.658 | 0.508 |

Korean **almost doubles** and English loses five percent. That trade is the decision.

The vendor's quantized build then gave back most of the gain, and the cause is specific rather than
inherent: it is quantized without range reduction, which the runtime's own documentation warns will
saturate the intermediate accumulator on processors without the newer instruction extension.
Rebuilding the same model with range reduction restores Korean to **0.669, which is fp32 parity**.

⇒ The deployed artifact is a **self-built int8**, not the published one. Range reduction is harmless
on processors that do have the extension, so the choice does not depend on the hardware and the
hardware never had to be checked.

> **The first measurement round was invalid and was thrown away.** Documents labelled Korean were
> being queried with their titles and filenames, which are ASCII, so the "Korean" queries contained
> **zero Korean characters**. Korean retrieval had never been measured while its score was being
> read. Queries were rebuilt from body subheadings in the matching language. A measurement that
> silently measures the wrong thing is worse than none, because it produces confident numbers.

**Quantization moved to build time.** The plan proposed building the model once on first boot and
caching it. That is not possible in the deployed container: one embedding matrix alone is over seven
hundred megabytes and the peak exceeds a gigabyte and a half, against a container limit of one
gibibyte. The model is therefore **baked into the image** and the worker takes a path to it. If the
baked file is absent the worker builds it in place, which suits a development machine, and if that
fails it falls back to fp32, which is slow and heavy but **correct**. It never silently substitutes
the vendor build, because that one quietly costs quality.

## 3. Chunks stay large, vectors get small

A storage chunk is about two thousand characters. The model window is five hundred tokens. Those are
different units, so a single vector per chunk encoded the beginning and **silently discarded the
rest**, measured at 42.6 percent of Korean text and 12.3 percent of English.

Three ways out were measured against the same queries.

| nDCG at ten | Keep chunks, encode the first window | Keep chunks, average the pieces | Re-chunk to the model window |
|---|---|---|---|
| English | 0.526 | 0.511 | **0.589** |
| Korean | 0.641 | 0.549 | **0.697** |
| Combined | 0.601 | 0.535 | **0.659** |

**Averaging failed**, and it was the shortcut worth wanting: it would have made re-chunking,
migration and link loss all unnecessary. It scores below the baseline, because distinct content
inside one chunk smears together, and a truncated chunk with a sharp beginning beats a blurred whole.

⇒ The design is **parent and child**: the chunk stays at two thousand characters and carries
**several vectors**, one per piece of at most five hundred tokens, and a hit on a child returns the
parent. Retrieval quality equals re-chunking **exactly**, because the vector set is literally
identical, the same pieces encoded by the same model. The price is a schema change rather than a
data migration, and it buys back everything re-chunking would have cost: concept links, excerpts and
topic derivation all stay at baseline, and there is no forced re-chunk pass and no re-propagation
through synchronization.

> **Why link loss mattered enough to design around.** A concept is linked to a chunk when every
> significant token of the concept title appears **in that one chunk**. Shrinking chunks makes that
> conjunction monotonically less likely: measured at five hundred tokens, links fall 2.5 percent in
> English and 10 percent in Korean. The proposed mitigation, testing the condition across a window of
> adjacent chunks, was measured too and **it does not restore links, it inflates them**, by up to
> 137 percent. Over-linking points a concept at chunks that barely discuss it, which destroys the
> feature it was meant to protect. No window was added.

Two smaller findings from the same work, both of which change how the code has to be written:

- **Token counts are not additive.** Tokenizing two pieces separately does not equal tokenizing them
  joined, so a boundary chosen from offsets can still exceed the limit. A re-encode and repair loop
  is mandatory rather than defensive
- **A character limit cannot serve both languages.** At the same token budget, measured chunk sizes
  were 482 characters of Korean, 2,660 of English and 1,101 of mixed text. Tuning for one starves or
  truncates the other, and no value in between satisfies both. This is the reason a real tokenizer is
  required rather than an estimator

## 4. Where the model lives, and how a query gets a vector

The shipped sidecar **excludes the embedding stack from its bundle**, to avoid a multi-gigabyte
binary. So exactly one process holds a model: the worker.

| Path | Mechanism |
|---|---|
| Filling documents | The worker polls the sidecar over loopback for pending chunks, encodes, and posts vectors back. Source text never leaves the store |
| Encoding a query | The worker exposes a loopback encode endpoint, and the sidecar calls it **only when an address is configured**. On the desktop there is no worker, so it does not try, because waiting for a refused connection on every search is itself a performance defect |
| Switching model | Announcing a different model identifier makes the sidecar **erase every existing vector** and mark the corpus pending. Two models' vectors are different spaces, so mixing them is worse than having none |
| During a migration | The vector arm is **switched off entirely**. Half the corpus embedded biases ranking toward whichever documents were processed first, and answering with text search only is the honest degradation |

Degradation is never silent. The search response names which arms ran and, when an arm was attempted
and failed, why. A migration in progress is reported as a structured pending count, so the screen
can show progress instead of a lie.

## 5. Known gap: the worker has never loaded its model

Measured on the hub, 2026-08-06.

**Two plausible explanations were cleared first.** The worker is not switched off: the flag defaults
to on and the process announces itself in the logs with its interval. The model is not missing: it is
baked into the image, both files present at the expected size, and the container reaches the outside
network normally.

**What remains.** The cache directory the loader creates does not exist and the ready line has never
been logged. The loader creates that directory as its **first statement**, immediately after four
imports. Work was available on the first round, so the lazy load was entered. Therefore the failure
is in those four imports: the array library, the inference runtime, the model hub client, or the
tokenizer.

Two of the four were confirmed installed. The other two returned nothing, but the query tool itself
was unreliable at the time, so **absence cannot be concluded**, and it has not been.

> **One structural observation from reading the loader.** The model hub client is imported
> unconditionally at the top of the load, even though the baked model path makes it unnecessary at
> runtime and the tokenizer is read from the baked directory when present. A packaging gap in a
> dependency the code no longer needs is therefore sufficient to stop everything. Moving those two
> imports to the branches that use them would shrink the failure surface regardless of what the
> actual cause turns out to be.

**Why it was not settled.** Log retrieval returned only a fraction of the requested lines and
in-container execution was rate limited.

**The safe next step**, in order:

1. Add exception logging around the loader so the next round names the module that failed
2. Redeploy the hub and read one round
3. Fix, then verify against the **final** corpus rather than the old one

**What not to do.** Do not run the worker in the foreground inside the container to reproduce it. Two
copies of the model in one gibibyte is an out-of-memory kill, and the container's entrypoint takes
the MCP endpoint down with it. The image's own documentation warns about this failure mode.

The consequences are recorded where they are felt rather than only here:
[[vinc.canon.chunk-ssot]] for document canon, [[vinc.canon.share-pack]] for the claim that a
receiver regenerates embeddings, [[vinc.spec.surface-search]] for what the search screen can
actually offer, and [[vinc.spec.remote-profile]] for the hosted path that was supposed to be where
embedding finally happened.

## 6. A second inconsistency, recorded and not fixed here

Two model registries disagree. The store configuration maps the default model identifier to the
**English-only** model at 768 dimensions. The hub worker resolves the **multilingual** model, also at
768. Because the dimensions match, provisioning succeeds and nothing fails loudly.

Today this is harmless, since no vectors exist at all. It stops being harmless the moment embedding
works and someone reads the configuration to learn which model produced the vectors. The
configuration label should be renamed to match, and that rename belongs to whoever fixes section 5,
in the same change, so that the two never disagree again.

## 7. What is left, in order

1. **Make the worker load.** Section 5. Nothing downstream is measurable until this is done
2. **Let the corpus fill.** Sixteen documents every five minutes. The final corpus is small enough
   that this is a matter of hours, not days
3. **Confirm the arms flip back on their own.** The search response should move from text-only to
   text and vector without intervention, which is the design's own self-check
4. **Rename the model identifier in the store configuration.** Section 6
5. **Compare quality on the production path**, using the evaluation script in the repository, which
   measures text, vector and fused ranking through the real routes rather than by exact cosine

## 8. Open and unmeasured

- **The crash threshold is imprecise.** Somewhere between three and six hundred insertions,
  varying with the pattern. It was not pursued further, because **non-determinism alone** is a
  sufficient reason to avoid the strategy that triggers it
- **All index measurements used tiny vectors.** At full dimension the threshold will differ. Nothing
  suggests the **direction** reverses: insertion is dangerous, value replacement is safe
- **The evaluation set is small and biased conservatively.** Fifty documents, sixty queries, and
  queries drawn from subheadings favor lexical matching, which understates the vector arm
- **Whether the desktop should get semantic search at all.** It would mean bundling a small model and
  a large tokenizer file. Left open deliberately: it costs application size and buys nothing until
  section 5 is closed

## Provenance

| Source | Relation |
|---|---|
| `Vinc/packaging/embed_worker.py` | The worker: model resolution, pooling, prefixes, the loopback encode service |
| `Vinc/api/docstore.py` | Index lifecycle, model switching, the pending and apply routes |
| `Vinc/scripts/eval_search.py` | Quality comparison through the production retrieval path |
| [[vinc.spec.dataplane]] | The per-chunk vector table and where search sits in the read surface |
| [[vinc.canon.chunk-ssot]] | Why the chunk is the canonical unit and must not be reshaped for the model |
| [[vinc.spec.surface-search]] | What the user sees while this is degraded |
| [[vinc.spec.azure-infrastructure]] | The container the worker runs in, and its memory ceiling |
