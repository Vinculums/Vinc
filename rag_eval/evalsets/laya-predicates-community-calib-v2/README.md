# laya-predicates-community-calib-v2

More calibration pairs for the three predicates, built on 2026-10-10 from the document bodies of the physics and
mathematics starter packs. It extends `laya-predicates-community-calib-v1` and is meant to be used together with it.
Method: `vinc.plan.laya-test-method-2026-10-07`.

Why: in the fresh run of VIN-431 the Korean supports bucket held 37 calibration records with 6 positives, and no
safe abstention cut could be fitted for supports in either language. Half of this set is Korean.

**Status: written, not yet reviewed.** The pairs were written and labelled by one AI. No second reader has seen
them and no person read them pair by pair. The next step is a blind relabel by a model from another vendor, under the
procedure of `vinc.plan.community-sets-cross-vendor-relabel-2026-10-10`; only pairs on which both agree are to be
used. No model was run and no calibration is authorized by this directory.

## 120 pairs from 26 documents

| language | supports | contradicts | same_as | none | total |
|---|---|---|---|---|---|
| ko | 12 | 12 | 12 | 24 | 60 |
| en | 6 | 6 | 6 | 12 | 30 |
| mixed | 6 | 6 | 6 | 12 | 30 |
| all | 24 | 24 | 24 | 48 | 120 |

`gold.split.json` marks every pair `calib`. Row shape is the same as the other sets. The class shares are the same
as in the measurement set (one fifth each for the three predicates), so that the two do not differ in base rate.

Together with the 115 agreed pairs of the first calibration set, and before any pair of this one is dropped by
review, the calibration buckets would hold:

| predicate | ko records | en records (en and mixed pairs) |
|---|---|---|
| supports | 37 + 60 = 97 | 78 + 60 = 138 |
| contradicts, same_as (two directions each) | 74 + 120 = 194 | 156 + 120 = 276 |

Each pair holds at least one statement grounded in a pack document. `authoring-spec.md` is the rule the author
followed.

## Kept apart from the other community sets

The pairs use only the 26 pack nodes that the first calibration set uses, through the bodies of their documents
instead of their one-liners. No statement appears in another community set, and no pack node of this set appears in
the training set or in a measurement set. `verify.py` checks all three.

## Limits

- One author, one model family, no second reader yet. The labels are a first reading.
- The kinds of pair follow the same scheme as the training set, so a tuned model may be more confident here than on
  text written by someone else. Thresholds fitted here can then be too loose elsewhere.
- Calibration text is physics and mathematics; measurement text is art and economics. That gap is the design, and it
  means a fitted temperature may not carry over.
- Statement length was not balanced here as it was in the measurement set. Length alone gives an AUROC of 0.54 for
  supports, 0.44 for contradicts and 0.60 for `same_as` over the 120 pairs, and more inside the 30 English pairs
  (0.77 for supports). `summary.json` has the numbers.
- Every Korean statement was written by the author. The packs hold no Korean text.
- Grounded statements restate the documents; they were not checked against outside sources. A written
  counter-statement may be false on purpose.

Run `python verify.py` from any directory. It needs no model.
