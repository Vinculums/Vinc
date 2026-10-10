# laya-predicates-community-calib-v1

Calibration pairs for the three predicates, built on 2026-10-10 from the physics and mathematics starter packs in
`starter-packs/`. It exists so that a judge can be calibrated and measured on community text alone: calibration here,
measurement on `laya-predicates-community-v1` (art and economics). The two sets share no pack node and no text
(`verify.py` checks it). Method: `vinc.plan.laya-test-method-2026-10-07`; the run it serves is in the graph document
`vinc.plan.predicate-judge-comparison-community-run-2026-10-10`.

**Status: made at the owner's direction on 2026-10-10** (community text only for the judge comparison). The labels were
written by an AI and no person read them pair by pair. No model was run by this directory.

## 117 calibration pairs

| language | supports | contradicts | same_as | none | total |
|---|---|---|---|---|---|
| en | 7 | 8 | 8 | 16 | 39 |
| mixed | 8 | 8 | 8 | 16 | 40 |
| ko | 7 | 8 | 8 | 15 | 38 |

Same row shape as the other sets. `gold.split.json` marks every pair `calib`. Records per calibration bucket: supports
ko 38 and en file 79, contradicts and same_as ko 76 and en file 158, all above `min_bucket_n` 30.

One side of every pair is a pack node's one-liner as shipped (English) or the author's Korean rendering of it. The other
side is another pack one-liner or a statement written against it. The packs hold no Korean text, so every Korean side is
the author's.

## Not for Laya checkpoints

`laya-predicates-community-v1/train-negatives.jsonl` holds text of these same physics and mathematics nodes, and the v3
checkpoint was trained on a sample of it. Use this set to calibrate judges that were not trained on that file.

## Review

A second AI reader relabelled all 120 authored pairs blind. Pair agreement was 117/120; the 3 pairs with a
disagreement were removed to `disputed.jsonl` with both readings and no reference label was changed. Both readers are
the same vendor, so this is a consistency filter and not a validation.

Run `python verify.py` from any directory. It needs no model.
