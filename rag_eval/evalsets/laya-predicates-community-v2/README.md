# laya-predicates-community-v2

A second measurement set for the three predicates, built on 2026-10-10 from the document bodies of the art and
economics starter packs. The first one, `laya-predicates-community-v1` (140 pairs), has been read by two checkpoints
(the retired v3 and the fresh lineage of VIN-431), so it can no longer serve as an unseen test and becomes a
development set. This set takes its place. Method: `vinc.plan.laya-test-method-2026-10-07`.

**Status: written, not yet reviewed.** The pairs were written and labelled by one AI. No second reader has seen
them and no person read them pair by pair. The next step is a blind relabel by a model from another vendor, under the
procedure of `vinc.plan.community-sets-cross-vendor-relabel-2026-10-10`; only pairs on which both agree are to be
used. No model was run and no measurement is authorized by this directory.

**Read it once.** A checkpoint is measured on this set one time, after its training recipe and calibration are
frozen. Error analysis and recipe choices use the development set, not this one.

## 300 pairs from 57 documents

| language | supports | contradicts | same_as | none | total |
|---|---|---|---|---|---|
| en | 24 | 24 | 24 | 48 | 120 |
| ko | 18 | 18 | 18 | 36 | 90 |
| mixed | 18 | 18 | 18 | 36 | 90 |
| all | 60 | 60 | 60 | 120 | 300 |

120 pairs come from the 15 economics documents (eight each) and 180 from the 42 art documents (four or five each).
`gold.split.json` marks every pair `eval`. Row shape is the same as the other sets.

Each pair holds at least one statement grounded in a pack document: one sentence that restates a fact the document
makes. The other side is a second grounded statement or a statement written against it. `authoring-spec.md` is the
rule the author followed, with the kinds of pair in each class.

## What differs from the 140-pair set

- Twice the positives: 60 per predicate against 28.
- Statements are single clean sentences taken from the body of a document. In the 140-pair set one side is usually
  the pack node's one-liner as shipped, with its markup. Numbers from the two sets are therefore not directly
  comparable.
- supports and contradicts are spread over three kinds each, because those are the predicates the measured
  checkpoints get wrong.

## Kept apart from the other community sets

| set | role | pack nodes |
|---|---|---|
| this set | measurement | art, economics (57 nodes) |
| `laya-predicates-community-v1` | development (was measurement) | the same 57 nodes, through their one-liners |
| `laya-predicates-community-calib-v1`, `laya-predicates-community-calib-v2` | calibration | 26 physics and mathematics nodes |
| `laya-predicates-community-train-v1` | training | the other 35 physics and mathematics nodes |

No statement of this set appears in any other community set, and no pack node of this set appears in a training or
calibration set. `verify.py` checks both. A near-duplicate scan against the older sets was run when the set was built.

## Length does not give the class away

As first written, the summed length of the two statements predicted the class: restatement pairs were long, none and
contradicts pairs short. Scoring on length alone reached an AUROC of 0.69 for `same_as` over the whole set and above
0.8 inside each language. Before any reader or model saw the set, the short none and contradicts pairs and a few
supports pairs were lengthened; `authoring-spec.md` says how. What length alone predicts now is in `summary.json`:

| pairs | supports | contradicts | same_as |
|---|---|---|---|
| all 300 | 0.49 | 0.48 | 0.55 |
| en 120 | 0.39 | 0.44 | 0.63 |
| ko 90 | 0.58 | 0.39 | 0.73 |
| mixed 90 | 0.43 | 0.45 | 0.67 |

0.5 means length says nothing. Inside one language a restatement pair is still somewhat longer than the rest, so a
`same_as` score by language should be read against the last column. The older community sets carry cues of the same
size (for example 0.74 for English `same_as` in the first calibration set).

## Limits

- One author, one model family, no second reader yet. The labels are a first reading.
- The same 57 nodes back the development set. The pairs are new and the statements are new, but the domain is the
  one the earlier checkpoints were measured on.
- The kinds of pair follow the same scheme as the training set (for example a recommendation in A for the
  `reversed_direction` kind, or a changed year for `value_conflict`). A model tuned on that set may find these pairs
  easier than text written by someone else.
- Every Korean statement was written by the author. The packs hold no Korean text.
- Grounded statements restate the documents; they were not checked against outside sources. A written
  counter-statement may be false on purpose.
- Language rows are small: 18 positives per predicate in Korean and in mixed pairs.
- Many none pairs set two dated facts side by side. They are easy to tell apart from a restatement, so the hard
  part of the none class is the `reversed_direction` kind.

Run `python verify.py` from any directory. It needs no model.
